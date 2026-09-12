"""Promoting a selection of staged rows into an OED exposure version.

Section 4.4 of the integration brief gives the identity mapping and section 5.4
gives the value mapping. What this module does is apply both to one selection
of one import batch and hand the result to the ordinary exposure pipeline --
validated, published, and usable by a run like any other version.

Two decisions in here are assumptions rather than translations, and both are
recorded as such rather than absorbed.

**Occupancy is not reported, so it is chosen.** OED requires
``OccupancyCode`` and the extract has none. It gets the same treatment as the
coverage split: a named, versioned assumption selected per promotion and
recorded in the lineage. It is stated, never derived -- deriving one from
``class_of_business`` would produce something that looks like information while
resting on nothing, and section 6 puts that behind a controlled mapping and an
approved assumption set. ``not_reported_v1`` writes OED's own unknown code and
remains one parameter away, at the cost that no vulnerability function covers
it and the section 8 gate will hold the run. Where a schedule states occupancy
per row, that outranks the assumption for the rows it covers.

**The coverage split is a choice, not a constant.** Section 5.4 keeps it
independent of the location split, and the source reports no component
breakdown, so whatever is used is an assumption. It is therefore selected per
promotion from a named, versioned set, recorded in the version's lineage, and
reconciled to the cent. The default is the building-only smoke fixture the
brief permits: a default that spread value across coverages would put an
unapproved prior into every version that never asked for one.

The selection itself is business-complete: a business enters only when its
whole schedule qualifies. Taking part of a schedule would leave the excluded
sites' value to go somewhere, and there is nowhere honest for it to go.

Nothing promoted here is fit for decision use. Every version says so in its
lineage, and says why -- an unapproved coverage split and no reported
vulnerability attributes.
"""

from __future__ import annotations

import csv
import dataclasses
import io
from decimal import Decimal
from typing import Any

from django.db import transaction

import cass_extract as extract
from apps.audit import services as audit
from apps.audit.models import AuditAction
from cass_oed.schema import FileKind

from . import services
from .models import ExposureVersion, ImportBatch, SourcePolicyRow, SourceRiskLocation

#: OED's own code for an occupancy that is not known. Writing it states a fact;
#: deriving a real occupancy from class of business would be an assumption.
UNKNOWN_OCCUPANCY = extract.UNKNOWN_OCCUPANCY

#: The brief confirms earthquake coverage for every policy in this extract, and
#: USD for every monetary field.
COVERED_PERIL = "QEQ"
CURRENCY = "USD"

#: The OED columns a ground-up location file needs. Account and reinsurance
#: files are not written: section 8 of the build plan refuses to generate a
#: placeholder financial file to imply a perspective the source cannot support,
#: and this extract carries no deductible, limit or layer interpretation.
LOCATION_COLUMNS = (
    "PortNumber",
    "AccNumber",
    "LocNumber",
    "CountryCode",
    "Latitude",
    "Longitude",
    "OccupancyCode",
    "ConstructionCode",
    "LocPerilsCovered",
    *(str(coverage) for coverage in extract.COVERAGE_ORDER),
    "LocCurrency",
)


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
    reported_components: extract.ReportedComponents | None = None,
    accept_restated_total: bool = False,
    actor=None,
    request=None,
) -> ExposureVersion:
    """Turn one cohort selection of a batch into a published exposure version.

    Coverage values come from one of two places, and reported ones win.
    ``reported_components`` is a completed template: real numbers per location,
    in whatever proportions the schedule actually shows, which is how OED works
    and what the brief's evidence hierarchy ranks first. ``component_split`` is
    the fallback for when nobody knows the breakdown, and it defaults to the
    building-only smoke fixture rather than to something that spreads value.
    """
    prepared = prepare(
        batch,
        cohort=cohort,
        class_of_business=class_of_business,
        country=country,
        allocation_method=allocation_method,
        component_split=component_split,
        occupancy=occupancy,
        reported_components=reported_components,
        accept_restated_total=accept_restated_total,
    )
    selected_businesses = prepared.businesses
    selected_locations = prepared.locations
    allocation = prepared.allocation
    coverage_record = prepared.coverage_record
    taxonomy_record = prepared.taxonomy_record

    version = _create_version(
        batch,
        name=name,
        cohort=cohort,
        class_of_business=class_of_business,
        country=country,
        allocation=allocation,
        coverage_record=coverage_record,
        taxonomy_record=taxonomy_record,
        selected_businesses=selected_businesses,
        selected_locations=selected_locations,
        actor=actor,
    )

    payload = _location_csv(prepared.rows)
    services.attach_file(
        version,
        FileKind.LOCATION,
        payload,
        filename=f"{version.id}_oed_location.csv",
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
    reported_components: extract.ReportedComponents | None = None,
    accept_restated_total: bool = False,
) -> PreparedSelection:
    """Resolve a selection into OED rows without creating anything.

    Every assumption is applied here -- the cohort, the allocation, the
    coverage values and the taxonomy -- so a caller can see the result of a
    choice before committing to it. Nothing is stored and nothing is audited:
    a promotion is the act that does both.
    """
    component_split = component_split or extract.DEFAULT_SPLIT
    occupancy = occupancy or extract.DEFAULT_OCCUPANCY

    businesses, locations, allocation = _selection(
        batch,
        cohort=cohort,
        class_of_business=class_of_business,
        country=country,
        allocation_method=allocation_method,
    )

    totals = allocation.by_location()
    components, coverage_record = _coverage_values(
        totals,
        component_split=component_split,
        reported_components=reported_components,
        accept_restated_total=accept_restated_total,
    )

    stated_taxonomy = reported_components.taxonomy if reported_components else None
    taxonomy = extract.assign_taxonomy(totals, occupancy, reported=stated_taxonomy)
    taxonomy_record = extract.taxonomy_record(
        occupancy, taxonomy, reported=stated_taxonomy
    )

    return PreparedSelection(
        businesses=businesses,
        locations=locations,
        allocation=allocation,
        rows=_location_rows(batch, locations, components, taxonomy),
        coverage_record=coverage_record,
        taxonomy_record=taxonomy_record,
    )


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
    locations = list(batch.location_rows.all())
    if not locations:
        raise PromotionError("The batch staged no locations, so there is nothing to promote.")

    rows = [row.values for row in locations]
    assignments = extract.assign_all(rows)
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
            "with nowhere honest to go."
        )

    selected = [row for row in locations if row.business_id in businesses]
    policies = list(
        SourcePolicyRow.objects.filter(batch=batch, business_id__in=businesses)
    )
    allocation = extract.allocate(
        [policy.values for policy in policies],
        [row.values for row in selected],
        method=allocation_method,
    )
    if not allocation.reconciles:
        raise PromotionError(
            "The allocation does not reconcile to the reported policy values, so the "
            "exposure version would misstate the portfolio."
        )
    return businesses, selected, allocation


def template_rows(
    batch: ImportBatch,
    *,
    cohort: extract.Cohort = extract.Cohort.A,
    class_of_business: str | None = extract.cohorts.PHYSICAL_DAMAGE_CLASS,
    country: str | None = None,
    allocation_method: extract.AllocationMethod = extract.AllocationMethod.EQUAL_LOCATION,
) -> list[dict[str, Any]]:
    """The rows of a coverage template for one selection."""
    _, selected, allocation = _selection(
        batch,
        cohort=cohort,
        class_of_business=class_of_business,
        country=country,
        allocation_method=allocation_method,
    )
    totals = allocation.by_location()
    return [
        {
            "business_id": row.business_id,
            "location_number": row.location_number,
            "country_code": row.country_code,
            "label": row.precision,
            "allocated_tiv": totals.get((row.business_id, row.location_number)),
        }
        for row in sorted(selected, key=lambda item: (item.business_id, item.location_number))
    ]


def _coverage_values(
    totals,
    *,
    component_split: extract.ComponentSplit,
    reported_components: extract.ReportedComponents | None,
    accept_restated_total: bool,
):
    """Resolve the coverage values, preferring what somebody actually reported.

    A supplied schedule that disagrees with the allocated total is not silently
    rescaled and not silently accepted. The two numbers are different pieces of
    reported information -- the policy's TIV and the schedule's -- and which is
    right is a question for a person. So the difference is reported, and
    proceeding on the schedule's total is something the caller asks for
    explicitly.
    """
    if reported_components is None:
        components = extract.split_locations(totals, component_split)
        check = extract.reconciliation(totals, components)
        if not check["reconciles"]:
            raise PromotionError(
                "The coverage components do not add back to the location totals, so "
                "the exposure version would misstate the portfolio."
            )
        return components, {
            "source": "derived_split",
            "split": component_split.as_dict(),
            "reconciliation": check,
            "basis": (
                "No component split is reported in the source. Values were divided by "
                f"the {component_split.name} assumption, which is "
                + ("approved." if component_split.approved else "not an approved prior.")
            ),
            "decision_note": (
                "an unapproved coverage split"
                if not component_split.approved
                else "an approved coverage split"
            ),
        }

    check = extract.reconcile_reported(reported_components, totals)
    if check["missing_locations"]:
        raise PromotionError(
            f"{len(check['missing_locations'])} selected locations carry no supplied "
            "coverage values, so part of the portfolio would have no value at all. "
            "First missing: " + ", ".join(check["missing_locations"][:5]) + "."
        )
    if check["restates_total"] and not accept_restated_total:
        raise PromotionError(
            "The supplied coverage values total "
            f"{check['supplied_total']} against an allocated {check['allocated_total']}, "
            f"a difference of {check['difference']} across {check['difference_count']} "
            "locations. Reported location values outrank a derived split, so this may "
            "be the better number -- but restating the portfolio total is a decision, "
            "not a rounding. Correct the file, or promote again accepting the restated "
            "total."
        )

    components = {key: reported_components.apply(key) for key in totals}
    return components, {
        "source": "reported_location_values",
        "supplied": reported_components.as_dict(),
        "reconciliation": check,
        "restated_total_accepted": bool(check["restates_total"] and accept_restated_total),
        "basis": (
            "Coverage values were supplied per location rather than derived. "
            + (
                "They restate the portfolio total, which was accepted explicitly."
                if check["restates_total"]
                else "They reconcile to the allocated location totals."
            )
        ),
        "decision_note": "reported coverage values that no approval covers",
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
            "value_basis": (
                "gross_limit is reported TIV at KRE's share in USD. The share is not "
                "applied again, so a physical-damage result from this version is "
                "KRE-share gross damage, not 100%-of-risk ground-up loss."
            ),
            # Section 8: an assumed attribute must never look like a reported
            # one. These are the fields the source did not carry.
            "attributes_not_reported": {
                "OccupancyCode": taxonomy_record["basis"],
                "ConstructionCode": taxonomy_record["basis"],
                "YearBuilt": "Not in the source and not inferred.",
                "NumberOfStoreys": "Not in the source and not inferred.",
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


def _location_rows(
    batch: ImportBatch,
    locations: list[SourceRiskLocation],
    components: dict[tuple[str, int], dict[str, Decimal]],
    taxonomy: dict[tuple[str, int], dict[str, str]],
) -> list[dict[str, Any]]:
    """The OED location rows a selection produces.

    The identity mapping of section 4.4: the extract reference is the portfolio,
    the business reference is the account, and the source location number is the
    location. Names are never identifiers -- an insured name is confidential and
    is not stable enough to key on even where it is permitted.
    """
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
                "LocPerilsCovered": COVERED_PERIL,
                "LocCurrency": CURRENCY,
                **{
                    coverage: format(amounts[coverage], "f")
                    for coverage in _COVERAGE_COLUMNS
                },
            }
        )
    return rows


def _location_csv(rows: list[dict[str, Any]]) -> bytes:
    """The OED location file for a set of prepared rows."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(LOCATION_COLUMNS), lineterminator="\n")
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
        "attributes_not_reported": sorted(lineage.get("attributes_not_reported", {})),
        "decision_use": lineage.get("decision_use"),
    }
