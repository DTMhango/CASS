"""Promoting a selection of staged rows into an OED exposure version.

Section 4.4 of the integration brief gives the identity mapping and section 5.4
gives the value mapping. What this module does is apply both to one selection
of one import batch and hand the result to the ordinary exposure pipeline --
validated, published, and usable by a run like any other version.

Two decisions in here are assumptions rather than translations, and both are
recorded as such rather than absorbed.

**Occupancy is not reported.** OED requires ``OccupancyCode`` and the extract
has none. Writing OED's own unknown code says exactly that; deriving one from
``class_of_business`` would be an enrichment decision, and section 6 puts that
behind a controlled mapping and an approved assumption set, not here. So the
lineage records the field as not reported, and a keys lookup will say what an
unknown occupancy maps to rather than being handed a guess that hides it.

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
UNKNOWN_OCCUPANCY = "1000"

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
    "LocPerilsCovered",
    *(str(coverage) for coverage in extract.COVERAGE_ORDER),
    "LocCurrency",
)


class PromotionError(Exception):
    """Raised when a selection cannot be promoted."""


@transaction.atomic
def promote(
    batch: ImportBatch,
    *,
    name: str,
    cohort: extract.Cohort = extract.Cohort.A,
    class_of_business: str | None = extract.cohorts.PHYSICAL_DAMAGE_CLASS,
    allocation_method: extract.AllocationMethod = extract.AllocationMethod.EQUAL_LOCATION,
    component_split: extract.ComponentSplit | None = None,
    actor=None,
    request=None,
) -> ExposureVersion:
    """Turn one cohort selection of a batch into a published exposure version.

    ``component_split`` is the coverage assumption. It defaults to the
    building-only smoke fixture rather than to something that spreads value,
    because a default that quietly asserted a building/contents ratio would put
    an unapproved prior into every result that never asked for one.
    """
    component_split = component_split or extract.DEFAULT_SPLIT
    locations = list(batch.location_rows.all())
    if not locations:
        raise PromotionError("The batch staged no locations, so there is nothing to promote.")

    rows = [row.values for row in locations]
    assignments = extract.assign_all(rows)
    selected_businesses = extract.business_complete(
        rows, assignments, cohort=cohort, class_of_business=class_of_business
    )
    if not selected_businesses:
        label = f"cohort {cohort}" + (f" {class_of_business}" if class_of_business else "")
        raise PromotionError(
            f"No business has its whole schedule in {label}, so there is nothing to "
            "promote. Taking part of a schedule would leave the excluded sites' value "
            "with nowhere honest to go."
        )

    selected_locations = [
        row for row in locations if row.business_id in selected_businesses
    ]
    policies = list(
        SourcePolicyRow.objects.filter(
            batch=batch, business_id__in=selected_businesses
        )
    )

    allocation = extract.allocate(
        [policy.values for policy in policies],
        [row.values for row in selected_locations],
        method=allocation_method,
    )
    if not allocation.reconciles:
        raise PromotionError(
            "The allocation does not reconcile to the reported policy values, so the "
            "exposure version would misstate the portfolio."
        )

    totals = allocation.by_location()
    components = extract.split_locations(totals, component_split)
    component_check = extract.reconciliation(totals, components)
    if not component_check["reconciles"]:
        raise PromotionError(
            "The coverage components do not add back to the location totals, so the "
            "exposure version would misstate the portfolio."
        )

    version = _create_version(
        batch,
        name=name,
        cohort=cohort,
        class_of_business=class_of_business,
        allocation=allocation,
        component_split=component_split,
        component_check=component_check,
        selected_businesses=selected_businesses,
        selected_locations=selected_locations,
        actor=actor,
    )

    payload = _location_csv(batch, version, selected_locations, components)
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
            "coverage_split": component_split.name,
            "locations": len(selected_locations),
        },
        detail="Promoted a source-extract cohort to an OED exposure version.",
        request=request,
    )
    version.refresh_from_db()
    return version


def _create_version(
    batch: ImportBatch,
    *,
    name: str,
    cohort: extract.Cohort,
    class_of_business: str | None,
    allocation,
    component_split: extract.ComponentSplit,
    component_check: dict[str, Any],
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
            "coverage_split": {
                **component_split.as_dict(),
                "reconciliation": component_check,
            },
            "value_basis": (
                "gross_limit is reported TIV at KRE's share in USD. The share is not "
                "applied again, so a physical-damage result from this version is "
                "KRE-share gross damage, not 100%-of-risk ground-up loss."
            ),
            # Section 8: an assumed attribute must never look like a reported
            # one. These are the fields the source did not carry.
            "attributes_not_reported": {
                "OccupancyCode": (
                    f"Not in the source. Written as OED unknown ({UNKNOWN_OCCUPANCY}); "
                    "deriving one from class of business is enrichment work under an "
                    "approved assumption set, not a mapping decision."
                ),
                "ConstructionCode": "Not in the source and not inferred.",
                "YearBuilt": "Not in the source and not inferred.",
                "NumberOfStoreys": "Not in the source and not inferred.",
                "coverage_components": (
                    "No component split is reported in the source. Values were "
                    f"divided by the {component_split.name} assumption, which is "
                    + ("approved." if component_split.approved else "not an approved prior.")
                ),
            },
            "decision_use": (
                "Blocked. This version carries "
                + (
                    "an unapproved coverage split"
                    if not component_split.approved
                    else "an approved coverage split"
                )
                + " and no reported vulnerability attributes; it is a research and "
                "engine-test version."
            ),
        },
        created_by=actor,
        updated_by=actor,
    )


def _location_csv(
    batch: ImportBatch,
    version: ExposureVersion,
    locations: list[SourceRiskLocation],
    components: dict[tuple[str, int], dict[str, Decimal]],
) -> bytes:
    """Write the OED location file for a selection.

    The identity mapping of section 4.4: the extract reference is the portfolio,
    the business reference is the account, and the source location number is the
    location. Names are never identifiers -- an insured name is confidential and
    is not stable enough to key on even where it is permitted.
    """
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(LOCATION_COLUMNS), lineterminator="\n")
    writer.writeheader()

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
        writer.writerow(
            {
                "PortNumber": batch.project.reference,
                "AccNumber": row.business_id,
                "LocNumber": str(row.location_number),
                "CountryCode": row.country_code,
                "Latitude": _coordinate(row.latitude),
                "Longitude": _coordinate(row.longitude),
                "OccupancyCode": UNKNOWN_OCCUPANCY,
                "LocPerilsCovered": COVERED_PERIL,
                "LocCurrency": CURRENCY,
                **{
                    coverage: format(amounts[coverage], "f")
                    for coverage in (str(item) for item in extract.COVERAGE_ORDER)
                },
            }
        )
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
        "coverage_split": lineage.get("coverage_split"),
        "value_basis": lineage.get("value_basis"),
        "attributes_not_reported": sorted(lineage.get("attributes_not_reported", {})),
        "decision_use": lineage.get("decision_use"),
    }
