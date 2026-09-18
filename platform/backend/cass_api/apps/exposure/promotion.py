"""Promoting a selection of staged risks into an OED exposure version.

What this module does is apply the identity and value mappings to one selection
of one import batch and hand the result to the ordinary exposure pipeline --
validated, published, and usable by a run like any other version.

**Value reads in three tiers, and the tier is recorded.** A risk that states
its coverages has stated them; nothing is assumed and no split applies. A risk
that states a total but not its breakdown gets a named component assumption. A
risk that states neither gets the policy total divided across its schedule
under a named allocation scenario, and then the component assumption. The
version's lineage says which tier each risk sat in, because "the building value
was 1.5m" and "we assumed the building value was 1.5m" are different claims and
a reader cannot tell them apart from the number.

**A schedule is valued consistently or not at all.** A business whose risks are
partly valued is excluded rather than patched, for the same reason a partly
qualifying schedule is: the remainder of the policy would have to go somewhere,
and there is nowhere honest to put it.

**Occupancy is chosen, not derived.** OED requires ``OccupancyCode`` and most
schedules will not carry one. It gets the same treatment as the coverage split:
a named, versioned assumption selected per promotion and recorded in the
lineage. Where a risk states its own, that outranks the assumption for that
row.

**Policy terms and reinsurance are written as the workbook states them.**
The promotion writes the Policies sheet into an OED account file, the risks'
own deductibles and limits into the location file, and the reinsurance sheets
into the two reinsurance files. A term the workbook leaves blank is written
blank, and OED reads a blank layer limit as no limit and a blank attachment as
nothing: such a policy's insured loss is its ground-up loss, less any
deductible it does state. That is never hidden. Every Policy ID with no layer
or policy limit, or no stated attachment, or no policy row at all, is listed
on the version as possibly overstated, and the list travels with every insured
and net-of-reinsurance result made from it. Leaving an account out would
understate the book, which is the worse error for a PML. The caller may still
leave the terms out altogether, for a ground-up version.

Nothing promoted under an assumption is fit for decision use. Every version
says so in its lineage, and says which assumptions it rests on.
"""

from __future__ import annotations

import csv
import dataclasses
import enum
import io
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from django.db import transaction

import cass_extract as extract
from apps.audit import services as audit
from apps.audit.models import AuditAction
from cass_oed.schema import FLAT_TERM_BASIS, FileKind

from . import review, services, structure_builder
from .models import ExposureVersion, ImportBatch, SourcePolicyRow, SourceRiskLocation

#: OED's own code for an occupancy that is not known. Writing it states a fact;
#: deriving a real occupancy from class of business would be an assumption.
UNKNOWN_OCCUPANCY = extract.UNKNOWN_OCCUPANCY

#: The brief confirms earthquake coverage for every policy in this extract, and
#: USD for every monetary field.
COVERED_PERIL = "QEQ"
CURRENCY = "USD"

#: The OED columns a ground-up location file needs. Section 8 of the build plan
#: refuses to generate a placeholder financial file to imply a perspective the
#: source cannot support, so the financial columns and files below are written
#: only from terms the workbook states.
LOCATION_COLUMNS = (
    "PortNumber",
    "AccNumber",
    "LocNumber",
    "CountryCode",
    "Latitude",
    "Longitude",
    "OccupancyCode",
    "ConstructionCode",
    "NumberOfStoreys",
    "LocPerilsCovered",
    *(str(coverage) for coverage in extract.COVERAGE_ORDER),
    "LocCurrency",
)


#: A risk's own deductible and limit, written beside the location when the
#: version carries policy terms. OED requires the basis and the peril wherever
#: an amount is stated, and CASS calculates only a flat amount.
LOCATION_TERM_COLUMNS = (
    "LocDed6All",
    "LocDedType6All",
    "LocLimit6All",
    "LocLimitType6All",
    "LocPeril",
)

#: The account file, one row per policy layer. The same shape the Financial
#: structure screen writes, so a promoted policy and a built one read alike.
ACCOUNT_COLUMNS = (
    "PortNumber",
    "AccNumber",
    "AccCurrency",
    "PolNumber",
    "PolPerilsCovered",
    "PolInceptionDate",
    "PolExpiryDate",
    "LayerNumber",
    "LayerParticipation",
    "LayerLimit",
    "LayerAttachment",
    "PolDed6All",
    "PolDedType6All",
    "PolLimit6All",
    "PolLimitType6All",
    "PolPeril",
)

REINS_INFO_COLUMNS = (
    "ReinsNumber",
    "ReinsLayerNumber",
    "ReinsName",
    "ReinsPeril",
    "CededPercent",
    "PlacedPercent",
    "RiskLimit",
    "RiskAttachment",
    "OccLimit",
    "OccAttachment",
    "ReinsCurrency",
    "InuringPriority",
    "ReinsType",
    "RiskLevel",
    "Reinstatement",
    "ReinstatementCharge",
    "ReinsPremium",
)

REINS_SCOPE_COLUMNS = (
    "ReinsNumber",
    "PortNumber",
    "AccNumber",
    "PolNumber",
    "LocNumber",
    "CededPercent",
)


class PolicyTerms(enum.StrEnum):
    """Whether a promotion writes the workbook's policy terms and reinsurance."""

    APPLY = "apply"
    """Written for every account, as stated. An account without a limit keeps its
    ground-up loss as insured loss, and is listed as possibly overstated."""

    GROUND_UP = "ground_up"
    """Not written, whatever the workbook holds: a ground-up version."""

    @property
    def label(self) -> str:
        return {
            PolicyTerms.APPLY: "Apply them, flagging policies with no limit",
            PolicyTerms.GROUND_UP: "Leave them out: ground-up loss only",
        }[self]


#: The coverage columns of a written row, in OED order.
_COVERAGE_COLUMNS = tuple(str(item) for item in extract.COVERAGE_ORDER)


class PromotionError(Exception):
    """Raised when a selection cannot be promoted."""


@transaction.atomic
def promote(
    batch: ImportBatch,
    *,
    name: str,
    cohort: extract.Cohort = extract.Cohort.A,
    class_of_business: str | None = extract.cohorts.PHYSICAL_DAMAGE_CLASS,
    country: str | None = None,
    allocation_method: extract.AllocationMethod = extract.AllocationMethod.EQUAL_LOCATION,
    component_split: extract.ComponentSplit | None = None,
    occupancy: extract.OccupancyAssumption | None = None,
    policy_terms: PolicyTerms | str = PolicyTerms.APPLY,
    actor=None,
    request=None,
) -> ExposureVersion:
    """Turn one cohort selection of a batch into a published exposure version.

    The assumptions are the caller's: which cohort, which country, how a
    multi-location policy divides where it has to, how a risk total splits
    across coverages where it has to, and what occupancy to assume. Every one
    is recorded on the version that results.
    """
    prepared = prepare(
        batch,
        cohort=cohort,
        class_of_business=class_of_business,
        country=country,
        allocation_method=allocation_method,
        component_split=component_split,
        occupancy=occupancy,
        policy_terms=policy_terms,
    )
    selected_businesses = prepared.businesses
    selected_locations = prepared.locations
    allocation = prepared.allocation
    coverage_record = prepared.coverage_record
    taxonomy_record = prepared.taxonomy_record
    height_record = prepared.height_record

    version = _create_version(
        batch,
        name=name,
        cohort=cohort,
        class_of_business=class_of_business,
        country=country,
        allocation=allocation,
        coverage_record=coverage_record,
        taxonomy_record=taxonomy_record,
        height_record=height_record,
        financial_record=prepared.financial_record,
        selected_businesses=selected_businesses,
        selected_locations=selected_locations,
        actor=actor,
    )

    for kind, rows, columns in (
        (FileKind.LOCATION, prepared.rows, prepared.location_columns),
        (FileKind.ACCOUNT, prepared.account_rows, ACCOUNT_COLUMNS),
        (FileKind.REINS_INFO, prepared.reins_info_rows, REINS_INFO_COLUMNS),
        (FileKind.REINS_SCOPE, prepared.reins_scope_rows, REINS_SCOPE_COLUMNS),
    ):
        if not rows:
            # No placeholder file: an empty account file would claim an
            # insured perspective the workbook does not support.
            continue
        services.attach_file(
            version,
            kind,
            _csv(rows, columns),
            filename=f"{version.id}_oed_{kind}.csv",
            actor=actor,
            request=request,
        )
    services.run_validation(version, actor=actor, request=request)
    version.refresh_from_db()
    if not version.is_publishable:
        raise PromotionError(
            "The generated OED did not validate cleanly, so it was not published. "
            "The findings on the exposure version say what is wrong."
        )
    services.publish(version, actor=actor, request=request)

    batch.exposure_versions.add(version)
    audit.record(
        action=AuditAction.PUBLISH,
        subject_type="exposure_version",
        subject_id=version.id,
        actor=actor,
        project=batch.project,
        subject_label=str(version),
        after={
            "import_batch": str(batch.id),
            "cohort": str(cohort),
            "allocation_method": str(allocation.method),
            "coverage_source": coverage_record["source"],
            "occupancy_assumption": taxonomy_record["assumption"]["name"],
            "locations": len(selected_locations),
            "policy_terms": prepared.financial_record["policy_terms"],
            "terms_applied": prepared.financial_record["applied"],
            "contract_layers": prepared.financial_record["contract_layers_written"],
        },
        detail="Promoted a source-extract cohort to an OED exposure version.",
        request=request,
    )
    version.refresh_from_db()
    return version


@dataclasses.dataclass(frozen=True, slots=True)
class PreparedSelection:
    """Everything a promotion would write, before anything is written.

    Extracted so the allocation-scenario comparison runs the *same* rows a
    promotion would produce rather than its own approximation of them. A
    comparison that described a portfolio nobody could promote would be advice
    about a thing that does not exist.
    """

    businesses: set[str]
    locations: list[SourceRiskLocation]
    allocation: Any
    rows: list[dict[str, Any]]
    coverage_record: dict[str, Any]
    taxonomy_record: dict[str, Any]
    height_record: dict[str, Any]
    location_columns: tuple[str, ...] = LOCATION_COLUMNS
    account_rows: tuple[dict[str, str], ...] = ()
    reins_info_rows: tuple[dict[str, str], ...] = ()
    reins_scope_rows: tuple[dict[str, str], ...] = ()
    financial_record: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def total_tiv(self) -> Decimal:
        return sum(
            (
                sum(
                    (Decimal(row[coverage]) for coverage in _COVERAGE_COLUMNS),
                    Decimal("0.00"),
                )
                for row in self.rows
            ),
            Decimal("0.00"),
        )


def prepare(
    batch: ImportBatch,
    *,
    cohort: extract.Cohort = extract.Cohort.A,
    class_of_business: str | None = extract.cohorts.PHYSICAL_DAMAGE_CLASS,
    country: str | None = None,
    allocation_method: extract.AllocationMethod = extract.AllocationMethod.EQUAL_LOCATION,
    component_split: extract.ComponentSplit | None = None,
    occupancy: extract.OccupancyAssumption | None = None,
    policy_terms: PolicyTerms | str = PolicyTerms.APPLY,
) -> PreparedSelection:
    """Resolve a selection into OED rows without creating anything.

    Every assumption is applied here -- the cohort, the allocation, the
    coverage values and the taxonomy -- so a caller can see the result of a
    choice before committing to it. Nothing is stored and nothing is audited:
    a promotion is the act that does both.
    """
    component_split = component_split or extract.DEFAULT_SPLIT
    occupancy = occupancy or extract.DEFAULT_OCCUPANCY
    mode = PolicyTerms(policy_terms)

    businesses, locations, allocation = _selection(
        batch,
        cohort=cohort,
        class_of_business=class_of_business,
        country=country,
        allocation_method=allocation_method,
    )
    # Terms are written where the workbook has a Policies sheet for this
    # selection at all. A book without one states no terms, and an account file
    # of nothing but blanks would claim an insured perspective it never had.
    applies = mode is PolicyTerms.APPLY and batch.policy_rows.filter(
        business_id__in=businesses
    ).exists()

    totals, tiers = _location_totals(locations, allocation)
    components, coverage_record = _coverage_values(
        locations, totals, tiers, component_split=component_split
    )

    stated_taxonomy = _stated_taxonomy(locations)
    taxonomy = extract.assign_taxonomy(totals, occupancy, reported=stated_taxonomy)
    taxonomy_record = extract.taxonomy_record(
        occupancy, taxonomy, reported=stated_taxonomy
    )

    rows = _location_rows(batch, locations, components, taxonomy, terms=applies)
    account_rows = _account_rows(batch, businesses) if applies else []
    programme, scope_dropped = (
        _programme(batch, businesses, rows, account_rows) if applies else (None, 0)
    )
    contracts_in_workbook = sorted(
        {
            int(record["contract_number"])
            for record in (batch.reinsurance or {}).get("contracts", [])
            if record.get("contract_number") is not None
        }
    )

    return PreparedSelection(
        businesses=businesses,
        locations=locations,
        allocation=allocation,
        rows=rows,
        coverage_record=coverage_record,
        taxonomy_record=taxonomy_record,
        height_record=_height_record(locations),
        location_columns=(
            (*LOCATION_COLUMNS, *LOCATION_TERM_COLUMNS) if applies else LOCATION_COLUMNS
        ),
        account_rows=tuple(account_rows),
        reins_info_rows=programme.info if programme else (),
        reins_scope_rows=programme.scope if programme else (),
        financial_record=_financial_record(
            mode,
            applies=applies,
            businesses=businesses,
            account_rows=account_rows,
            location_rows=rows,
            programme=programme,
            contracts_in_workbook=contracts_in_workbook,
            scope_dropped=scope_dropped,
        ),
    )


def overstated_accounts(account_rows: Sequence[Mapping[str, str]]) -> dict[str, list[str]]:
    """Policy IDs whose insured loss may be overstated, by the reason why.

    ``uncapped``: a layer with neither a layer limit nor a policy limit, so the
    layer pays the whole loss above its attachment. ``attachment_read_as_zero``:
    a layer with no stated attachment, which OED reads as paying from the first
    loss. ``no_policy_row``: an account the Policies sheet does not mention,
    written with no terms at all.
    """
    reasons: dict[str, set[str]] = {
        "uncapped": set(),
        "attachment_read_as_zero": set(),
        "no_policy_row": set(),
    }
    for row in account_rows:
        account = row["AccNumber"]
        if row.get("_no_policy_row"):
            reasons["no_policy_row"].add(account)
            continue
        if not row.get("LayerLimit") and not row.get("PolLimit6All"):
            reasons["uncapped"].add(account)
        if not row.get("LayerAttachment"):
            reasons["attachment_read_as_zero"].add(account)
    return {key: sorted(values) for key, values in reasons.items()}


def _financial_record(
    mode: PolicyTerms,
    *,
    applies: bool,
    businesses: set[str],
    account_rows: Sequence[Mapping[str, str]],
    location_rows: Sequence[Mapping[str, Any]],
    programme,
    contracts_in_workbook: list[int],
    scope_dropped: int,
) -> dict[str, Any]:
    """What the version carries beyond its locations, and what it may overstate.

    Every "no" says why, because "insured loss is not available" reads as a
    platform limit when the reason is usually a column the workbook left empty.
    """
    flagged = overstated_accounts(account_rows)
    possibly_overstated = sorted({account for values in flagged.values() for account in values})
    value = {row["AccNumber"]: Decimal(0) for row in location_rows}
    for row in location_rows:
        value[row["AccNumber"]] += sum(
            (Decimal(str(row.get(column) or 0)) for column in _COVERAGE_COLUMNS), Decimal(0)
        )
    overstated_tiv = sum((value.get(account, Decimal(0)) for account in possibly_overstated), Decimal(0))

    if mode is PolicyTerms.GROUND_UP:
        reason = "Ground-up only was chosen at promotion, so no terms were written."
    elif not applies:
        reason = (
            "The workbook has no Policies rows for this selection, so there were no "
            "terms to write and the version is ground-up only."
        )
    else:
        reason = f"Written for all {len(businesses)} Policy IDs in the selection."
        if possibly_overstated:
            reason += (
                f" {len(possibly_overstated)} of them state no limit, no attachment or "
                "no policy row, so their insured loss is their ground-up loss (less any "
                "deductible they state) and may be overstated."
            )

    written = programme.written if programme else ()
    if contracts_in_workbook and not applies:
        reinsurance = (
            "Not written: reinsurance applies to insured loss, and this version "
            "carries no policy terms."
        )
    elif programme and programme.unscoped:
        reinsurance = (
            f"Contract(s) {', '.join(str(item) for item in programme.unscoped)} "
            "were left out: their scope names only accounts outside this version, "
            "so they would cede nothing."
        )
    else:
        reinsurance = ""

    signed = {
        row["LayerParticipation"] for row in account_rows if row.get("LayerParticipation")
    }
    return {
        "policy_terms": str(mode),
        "applied": applies,
        "reason": reason,
        "policy_ids_written": len({row["AccNumber"] for row in account_rows}),
        "possibly_overstated": {
            "policy_ids": possibly_overstated,
            "tiv": str(overstated_tiv),
            **flagged,
        },
        "policy_rows_written": len(account_rows),
        "locations_with_terms": sum(
            1 for row in location_rows if row.get("LocDed6All") or row.get("LocLimit6All")
        ),
        "signed_shares": sorted(signed),
        "contracts_in_workbook": contracts_in_workbook,
        "contracts_written": list(written),
        "contract_layers_written": len(programme.info) if programme else 0,
        "scope_rows_outside_version": scope_dropped,
        "reinsurance_note": reinsurance,
    }


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _positive(value: Any) -> str:
    """A stated amount above zero as OED text, or blank: a zero term is no term."""
    if value in (None, ""):
        return ""
    amount = Decimal(str(value))
    return format(amount, "f") if amount > 0 else ""


def _account_rows(batch: ImportBatch, businesses: set[str]) -> list[dict[str, str]]:
    """The OED account rows: one per policy layer, exactly as the workbook states.

    Nothing is completed on the way. A layer without a deductible is written
    without one, even where the layer above states one, because a term copied
    between layers is a term nobody wrote; a layer without a limit is written
    without one, which OED reads as no limit. An account the Policies sheet
    does not mention gets one row of no terms, under its own Policy ID, so it
    is modelled at ground-up rather than left out of the insured perspective.
    The ``_no_policy_row`` marker says so and is never written.
    """
    rows: list[dict[str, str]] = []
    policies = SourcePolicyRow.objects.filter(
        batch=batch, business_id__in=businesses
    ).order_by("business_id", "policy_id", "layer_number", "row_number")
    for policy in policies:
        values = policy.values or {}
        deductible = _positive(values.get("policy_deductible"))
        limit = _positive(values.get("policy_limit"))
        rows.append(
            {
                "PortNumber": batch.project.reference,
                "AccNumber": policy.business_id,
                "AccCurrency": CURRENCY,
                "PolNumber": policy.policy_id,
                "PolPerilsCovered": COVERED_PERIL,
                "PolInceptionDate": _text(values.get("inception_date")),
                "PolExpiryDate": _text(values.get("expiry_date")),
                "LayerNumber": _text(values.get("layer_number")) or "1",
                "LayerParticipation": _text(values.get("signed_share")) or "1",
                "LayerLimit": _text(values.get("layer_limit")),
                "LayerAttachment": _text(values.get("layer_attachment")),
                "PolDed6All": deductible,
                "PolDedType6All": FLAT_TERM_BASIS if deductible else "",
                "PolLimit6All": limit,
                "PolLimitType6All": FLAT_TERM_BASIS if limit else "",
                "PolPeril": COVERED_PERIL if (deductible or limit) else "",
            }
        )
    for account in sorted(businesses - {row["AccNumber"] for row in rows}):
        rows.append(
            {
                "PortNumber": batch.project.reference,
                "AccNumber": account,
                "AccCurrency": CURRENCY,
                "PolNumber": account,
                "PolPerilsCovered": COVERED_PERIL,
                "LayerNumber": "1",
                "LayerParticipation": "1",
                "_no_policy_row": "1",
            }
        )
    return rows


def _programme(
    batch: ImportBatch,
    businesses: set[str],
    location_rows: Sequence[Mapping[str, Any]],
    account_rows: Sequence[Mapping[str, str]],
):
    """The workbook's reinsurance, for the accounts this version holds.

    Scope rows naming an account outside the selection are dropped: the
    contract still covers what it names inside it. A contract left covering
    nothing is left out and named, rather than written to cede nothing. A
    contract that breaks a rule refuses the promotion, because a version whose
    reinsurance differed from the workbook's would be net of a programme
    nobody bought.
    """
    reinsurance = batch.reinsurance or {}
    contracts = reinsurance.get("contracts") or []
    if not contracts:
        return None, 0
    every_scope = reinsurance.get("scope") or []
    scope = [
        row
        for row in every_scope
        if not _text(row.get("business_id")) or _text(row.get("business_id")) in businesses
    ]
    programme = structure_builder.programme_rows(
        contracts,
        scope,
        locations=location_rows,
        policy_keys={(row["AccNumber"], row["PolNumber"]) for row in account_rows},
        perils=COVERED_PERIL,
        currency=CURRENCY,
    )
    if programme.refused:
        reasons = [
            f"Contract {record.get('contract_number')} layer "
            f"{record.get('layer_number') or 1}: {reason}"
            for record, reasons in programme.refused
            for reason in reasons
        ]
        raise PromotionError(
            "The reinsurance contracts cannot be written as the workbook states them, "
            "so nothing was promoted. "
            + " ".join(reasons[:5])
            + (f" ({len(reasons) - 5} more.)" if len(reasons) > 5 else "")
        )
    return programme, len(every_scope) - len(scope)


#: How one risk's value was arrived at, best evidence first.
STATED_COVERAGES = "stated_coverages"
STATED_TOTAL = "stated_total"
ALLOCATED = "allocated_from_policy"


def _key(row: SourceRiskLocation) -> tuple[str, str]:
    return (row.business_id, row.location_number)


def _tier(row: SourceRiskLocation) -> str:
    values = row.values or {}
    if any(values.get(column) is not None for column in _COVERAGE_COLUMNS):
        return STATED_COVERAGES
    if values.get("location_tiv") is not None:
        return STATED_TOTAL
    return ALLOCATED


def _location_totals(
    locations: list[SourceRiskLocation], allocation
) -> tuple[dict[tuple[str, str], Decimal], dict[tuple[str, str], str]]:
    """What each risk is worth, and where that number came from.

    The allocation is consulted only for the risks that need it. Reading it for
    a risk that stated its own value would replace evidence with an assumption
    and leave no trace that it had happened.
    """
    allocated = allocation.by_location()
    totals: dict[tuple[str, str], Decimal] = {}
    tiers: dict[tuple[str, str], str] = {}

    for row in locations:
        key = _key(row)
        tier = _tier(row)
        tiers[key] = tier
        if tier is STATED_COVERAGES or tier == STATED_COVERAGES:
            values = row.values or {}
            totals[key] = sum(
                (
                    Decimal(str(values[column]))
                    for column in _COVERAGE_COLUMNS
                    if values.get(column) is not None
                ),
                Decimal("0.00"),
            )
        elif tier == STATED_TOTAL:
            totals[key] = Decimal(str((row.values or {})["location_tiv"]))
        else:
            if key not in allocated:
                raise PromotionError(
                    f"Risk {key[0]}/{key[1]} states no value and its account's "
                    "policies supply none to divide, so it would enter the model "
                    "worth nothing. Supply the values on the risk, or the total "
                    "insured value on the policy."
                )
            totals[key] = allocated[key]

    return totals, tiers


def _stated_taxonomy(
    locations: list[SourceRiskLocation],
) -> dict[tuple[str, str], dict[str, str]]:
    """Occupancy and construction where a risk states its own."""
    stated: dict[tuple[str, str], dict[str, str]] = {}
    for row in locations:
        values = row.values or {}
        occupancy = str(values.get("occupancy_code") or "").strip()
        if not occupancy:
            continue
        stated[_key(row)] = {
            "OccupancyCode": occupancy,
            "ConstructionCode": str(values.get("construction_code") or "").strip(),
        }
    return stated


def _eligible(
    batch: ImportBatch,
    *,
    cohort: extract.Cohort,
    class_of_business: str | None,
    country: str | None = None,
) -> tuple[list[SourceRiskLocation], set[str]]:
    """Every staged location, and the businesses whose whole schedule qualifies.

    By the cohort each location stands in after review: the rules' assignment
    at import, then whatever a person decided with a rationale. The rules are
    not run again here, so the cohort rule version recorded on the batch is the
    one that applied, and the review screen's counts are what is promoted.
    """
    locations = list(batch.location_rows.all().prefetch_related("decisions"))
    if not locations:
        raise PromotionError("The batch staged no locations, so there is nothing to promote.")

    rows = [row.values for row in locations]
    assignments = review.standing_assignments(locations)
    businesses = extract.business_complete(
        rows,
        assignments,
        cohort=cohort,
        class_of_business=class_of_business,
        country=country,
    )
    if not businesses:
        label = (
            f"cohort {cohort}"
            + (f" {class_of_business}" if class_of_business else "")
            + (f" in {country}" if country else "")
        )
        raise PromotionError(
            f"No business has its whole schedule in {label}, so there is nothing to "
            "promote. Taking part of a schedule would leave the excluded sites' value "
            "with nowhere honest to go. The review queue lists every location left "
            "out and why."
        )
    return locations, businesses


def _selection(
    batch: ImportBatch,
    *,
    cohort: extract.Cohort,
    class_of_business: str | None,
    allocation_method: extract.AllocationMethod,
    country: str | None = None,
):
    """The locations a promotion would cover, and what each would be allocated.

    Shared by the promotion itself and the coverage template, so the template a
    person fills in is always exactly the set the promotion will read back.
    """
    locations, businesses = _eligible(
        batch, cohort=cohort, class_of_business=class_of_business, country=country
    )

    selected = [row for row in locations if row.business_id in businesses]
    policies = list(
        SourcePolicyRow.objects.filter(batch=batch, business_id__in=businesses)
    )
    allocation = extract.allocate(
        _one_row_per_policy([policy.values for policy in policies]),
        [row.values for row in selected],
        method=allocation_method,
    )
    if not allocation.reconciles:
        raise PromotionError(
            "The allocation does not reconcile to the reported policy values, so the "
            "exposure version would misstate the portfolio."
        )
    return businesses, selected, allocation


def _one_row_per_policy(policies: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """One record per policy, however many layers it has.

    A policy's total insured value is one number, and allocating it once per
    layer row would put it in the portfolio once per layer. A layered policy
    may carry it on its first row or on every row; two different figures are a
    contradiction nothing here can resolve, so they are refused.
    """
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for record in policies:
        key = (_text(record.get("business_id")), _text(record.get("policy_id")))
        grouped.setdefault(key, []).append(record)

    kept: list[Mapping[str, Any]] = []
    for (account, policy), records in grouped.items():
        stated = [record for record in records if record.get("policy_tiv") not in (None, "")]
        if len({Decimal(str(record["policy_tiv"])) for record in stated}) > 1:
            raise PromotionError(
                f"Policy {policy} of {account} states different total insured values "
                "on different layers. A policy has one value: write it on the first "
                "layer's row, or the same figure on every row."
            )
        kept.append(stated[0] if stated else records[0])
    return kept


def _coverage_values(
    locations: list[SourceRiskLocation],
    totals: dict[tuple[str, str], Decimal],
    tiers: dict[tuple[str, str], str],
    *,
    component_split: extract.ComponentSplit,
):
    """Resolve every risk's coverage values, preferring what was stated.

    A risk that states its coverages keeps them exactly, including the zeroes:
    a schedule with a building figure and no contents is saying there are no
    contents, and running that through a split would invent some.
    """
    components: dict[tuple[str, str], dict[str, Decimal]] = {}
    stated_rows = 0

    for row in locations:
        key = _key(row)
        if tiers[key] == STATED_COVERAGES:
            values = row.values or {}
            components[key] = {
                column: Decimal(str(values.get(column) or "0.00")).quantize(
                    Decimal("0.01")
                )
                for column in _COVERAGE_COLUMNS
            }
            stated_rows += 1
        else:
            components[key] = component_split.apply(totals[key])

    check = extract.reconciliation(totals, components)
    if not check["reconciles"]:
        raise PromotionError(
            "The coverage components do not add back to the risk totals, so the "
            "exposure version would misstate the portfolio."
        )

    split_rows = len(locations) - stated_rows
    counts = {tier: 0 for tier in (STATED_COVERAGES, STATED_TOTAL, ALLOCATED)}
    for tier in tiers.values():
        counts[tier] = counts.get(tier, 0) + 1

    return components, {
        "source": (
            "stated_coverage_values"
            if split_rows == 0
            else "derived_split"
            if stated_rows == 0
            else "mixed_stated_and_derived"
        ),
        "split": component_split.as_dict() if split_rows else None,
        "reconciliation": check,
        "evidence_tiers": counts,
        "basis": (
            "Every risk states its own coverage values."
            if split_rows == 0
            else (
                f"{split_rows} of {len(locations)} risks state no coverage "
                f"breakdown. Their value was divided by the {component_split.name} "
                "assumption, which is "
                + ("approved." if component_split.approved else "not an approved prior.")
            )
        ),
        "decision_note": (
            "stated coverage values"
            if split_rows == 0
            else "an unapproved coverage split"
            if not component_split.approved
            else "an approved coverage split"
        ),
    }


def _create_version(
    batch: ImportBatch,
    *,
    name: str,
    cohort: extract.Cohort,
    class_of_business: str | None,
    country: str | None,
    allocation,
    coverage_record: dict[str, Any],
    taxonomy_record: dict[str, Any],
    height_record: dict[str, Any],
    financial_record: dict[str, Any],
    selected_businesses: set[str],
    selected_locations: list[SourceRiskLocation],
    actor,
) -> ExposureVersion:
    countries = sorted({row.country_code for row in selected_locations if row.country_code})
    return ExposureVersion.objects.create(
        project=batch.project,
        name=name,
        version=services.next_version_number(batch.project, name),
        source_description=(
            f"Promoted from {batch.profile} {batch.source_filename or ''} "
            f"(checksum {batch.source_checksum}), cohort {cohort}"
            + (f" {class_of_business}" if class_of_business else "")
            + (f", {country} only" if country else "")
            + "."
        ).strip(),
        run_currency=CURRENCY,
        source_lineage={
            "import_batch": str(batch.id),
            "source_checksum": batch.source_checksum,
            "profile": batch.profile,
            "schema_version": batch.schema_version,
            "parser_version": batch.parser_version,
            "cohort": str(cohort),
            "cohort_rule_version": batch.cohort_rule_version,
            # Locations in the version because a person put them in its cohort,
            # rather than the rules: a coordinate outside the country's outline
            # confirmed as right, say. The review overlay is what applied it.
            "cohorts_decided_in_review": sum(
                1
                for row in selected_locations
                if review.overlay(row).cohort_is_reviewed
            ),
            "overlay_version": review.OVERLAY_VERSION,
            "class_of_business": class_of_business,
            "country_filter": country,
            "business_count": len(selected_businesses),
            "location_count": len(selected_locations),
            "countries": countries,
            "allocation": {
                "method": str(allocation.method),
                "rule_version": allocation.rule_version,
                "policy_count": len(allocation.allocations),
                "source_tiv": str(allocation.source_tiv),
                "allocated_tiv": str(allocation.allocated_tiv),
                "reconciles": allocation.reconciles,
                "methods_used": sorted(
                    {str(item.method) for item in allocation.allocations}
                ),
            },
            "coverage": coverage_record,
            "taxonomy": taxonomy_record,
            "financial_structure": financial_record,
            "value_basis": _value_basis(financial_record),
            # Section 8: an assumed attribute must never look like a reported
            # one. These are the fields the source did not carry.
            "attributes_not_reported": {
                "OccupancyCode": taxonomy_record["basis"],
                "ConstructionCode": taxonomy_record["basis"],
                "YearBuilt": "Not in the source and not inferred.",
                "NumberOfStoreys": height_record["basis"],
                "coverage_components": coverage_record["basis"],
            },
            "decision_use": (
                "Blocked. This version carries "
                + coverage_record["decision_note"]
                + " and "
                + taxonomy_record["decision_note"]
                + "; it is a research and engine-test version."
            ),
        },
        created_by=actor,
        updated_by=actor,
    )


def _value_basis(financial_record: Mapping[str, Any]) -> str:
    """What a loss from this version is a loss of, in one sentence.

    The workbook states values at KRE's share unless a signed share says
    otherwise, and a signed share below one is the statement that the values
    are at 100% of the risk.
    """
    shares = [Decimal(item) for item in financial_record.get("signed_shares", [])]
    if any(share < 1 for share in shares):
        return (
            "Values are at 100% of each risk, in USD, and the signed share on each "
            "layer applies in the insured perspective. Ground-up loss from this "
            "version is 100%-of-risk damage; insured loss is at KRE's share."
        )
    return (
        "Values are reported at KRE's share, in USD. The share is not applied again, "
        "so a physical-damage result from this version is KRE-share gross damage, "
        "not 100%-of-risk ground-up loss."
    )


def _height_record(locations: list[SourceRiskLocation]) -> dict[str, Any]:
    """Where each storey count came from, and what the gap costs.

    Section 8 forbids an assumed attribute looking like a reported one, and
    height is the case where that matters most: it decides which spectral
    period answers a risk, so a version that could not say whether a height was
    reported, established in review or absent would let a guess become a
    function.
    """
    coverage = review.storey_coverage(locations)
    stated = coverage["stated_in_source"] + coverage["established_in_review"]
    if stated == 0:
        basis = (
            "Not stated by any risk and never inferred. Every location reaches "
            "vulnerability candidates across several intensity measures."
        )
    elif coverage["unstated"] == 0:
        basis = (
            f"Stated for all {stated} locations "
            f"({coverage['established_in_review']} established in review)."
        )
    else:
        basis = (
            f"Stated for {stated} of {coverage['locations']} locations "
            f"({coverage['established_in_review']} established in review); the "
            f"remaining {coverage['unstated']} are written blank rather than "
            "defaulted, because a height band decides which function answers a "
            "risk and a guess is a different function rather than a small error."
        )
    return {"basis": basis, **coverage}


def _storeys(value: int | None) -> str:
    """The stated height, or blank. Never a default."""
    return "" if value is None else str(value)


def _heights(
    locations: list[SourceRiskLocation],
) -> dict[tuple[str, str], int | None]:
    """Each location's storey count after any review decision is applied.

    The overlay rather than the staged value, because establishing a height is
    exactly what work package 2's review is for and a promotion that ignored it
    would leave the reviewer's work out of the model.
    """
    applied = review.overlays(locations)
    return {_key(row): applied[row.id].storeys for row in locations}


def _location_rows(
    batch: ImportBatch,
    locations: list[SourceRiskLocation],
    components: dict[tuple[str, int], dict[str, Decimal]],
    taxonomy: dict[tuple[str, int], dict[str, str]],
    *,
    terms: bool = False,
) -> list[dict[str, Any]]:
    """The OED location rows a selection produces.

    The identity mapping of section 4.4: the extract reference is the portfolio,
    the business reference is the account, and the source location number is the
    location. Names are never identifiers -- an insured name is confidential and
    is not stable enough to key on even where it is permitted.
    """
    heights = _heights(locations)
    rows: list[dict[str, Any]] = []
    for row in sorted(locations, key=lambda item: (item.business_id, item.location_number)):
        amounts = components.get((row.business_id, row.location_number))
        if amounts is None:
            # A staged location no policy allocated to. Writing it with no value
            # would put a not-at-risk row in the model; leaving it out silently
            # would lose it. The selection rule should already have prevented
            # this, so it is a defect rather than a case to absorb.
            raise PromotionError(
                f"Location {row.business_id}/{row.location_number} was selected but "
                "received no allocation, so the selection and the allocation disagree."
            )
        codes = taxonomy[(row.business_id, row.location_number)]
        rows.append(
            {
                "PortNumber": batch.project.reference,
                "AccNumber": row.business_id,
                "LocNumber": str(row.location_number),
                "CountryCode": row.country_code,
                "Latitude": _coordinate(row.latitude),
                "Longitude": _coordinate(row.longitude),
                "OccupancyCode": codes["OccupancyCode"],
                "ConstructionCode": codes["ConstructionCode"],
                # Blank where nobody knows. Writing a default here would put
                # the risk in a height band the schedule never claimed, and the
                # band decides which spectral period answers it -- so a guess
                # is not a small approximation, it is a different function.
                "NumberOfStoreys": _storeys(heights.get(_key(row))),
                "LocPerilsCovered": COVERED_PERIL,
                "LocCurrency": CURRENCY,
                **{
                    coverage: format(amounts[coverage], "f")
                    for coverage in _COVERAGE_COLUMNS
                },
            }
        )
        if terms:
            values = row.values or {}
            deductible = _positive(values.get("location_deductible"))
            limit = _positive(values.get("location_limit"))
            rows[-1].update(
                {
                    "LocDed6All": deductible,
                    "LocDedType6All": FLAT_TERM_BASIS if deductible else "",
                    "LocLimit6All": limit,
                    "LocLimitType6All": FLAT_TERM_BASIS if limit else "",
                    "LocPeril": COVERED_PERIL if (deductible or limit) else "",
                }
            )
    return rows


def _csv(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> bytes:
    """One OED file for a set of prepared rows, blank where a row has no value."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=list(columns), restval="", extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _coordinate(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")


def promotion_summary(version: ExposureVersion) -> dict[str, Any]:
    """What a reader needs to interpret a promoted version, in one place."""
    lineage = version.source_lineage or {}
    return {
        "exposure_version": str(version.id),
        "name": version.name,
        "state": version.state,
        "location_count": version.location_count,
        "total_tiv": str(version.total_tiv),
        "cohort": lineage.get("cohort"),
        "allocation": lineage.get("allocation"),
        "coverage": lineage.get("coverage"),
        "taxonomy": lineage.get("taxonomy"),
        "value_basis": lineage.get("value_basis"),
        "financial_structure": lineage.get("financial_structure"),
        "attributes_not_reported": sorted(lineage.get("attributes_not_reported", {})),
        "decision_use": lineage.get("decision_use"),
    }
