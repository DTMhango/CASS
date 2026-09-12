"""The work package 1 acceptance checks, against the real extract.

Section 7 of the integration brief lists these as numbers: 1,353 policy rows,
224 risk-location rows, 224 unique keys, 213 primary and 11 secondary, 213
primary coordinates agreeing, two repeated business identifiers reported
without duplicating a location, and the source checksum and parser version in
the audit trail.

They can only be checked against the real workbook, which is confidential KRE
working data and is deliberately not in this repository. So this suite is
marked ``integration`` and skips unless someone points it at a copy:

    CASS_EXTRACT_PATH=/path/to/premium_policies_2026-06-30_geocoded.xlsx \\
        pytest -m integration cass_api/tests/test_extract_acceptance.py

The structural behaviour these numbers depend on is covered by the synthetic
fixtures in ``test_portfolio_import.py``, which run everywhere. This file is
what proves the parser meets the brief on the actual data.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest

import cass_extract as extract
from apps.audit.models import AuditAction, AuditEvent
from apps.exposure.extract import import_extract, transformation_manifest
from apps.exposure.models import SourcePolicyRow, SourceRiskLocation

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

EXTRACT_PATH = os.environ.get("CASS_EXTRACT_PATH", "")

needs_extract = pytest.mark.skipif(
    not (EXTRACT_PATH and Path(EXTRACT_PATH).is_file()),
    reason="set CASS_EXTRACT_PATH to the confidential source workbook",
)


@pytest.fixture()
def batch(project, analyst):
    payload = Path(EXTRACT_PATH).read_bytes()
    return import_extract(
        project,
        payload,
        filename=Path(EXTRACT_PATH).name,
        snapshot_date=dt.date(2026, 6, 30),
        actor=analyst,
    )


# -- the acceptance checks ------------------------------------------------------

@needs_extract
def test_the_declared_row_counts_are_read(batch):
    assert batch.policy_row_count == 1353
    assert batch.location_row_count == 224
    assert SourcePolicyRow.objects.filter(batch=batch).count() == 1353
    assert SourceRiskLocation.objects.filter(batch=batch).count() == 224


@needs_extract
def test_every_business_and_location_number_combination_is_unique(batch):
    assert batch.join_report["unique_location_keys"] == 224


@needs_extract
def test_the_primary_and_secondary_split_is_retained(batch):
    assert batch.join_report["primary_locations"] == 213
    assert batch.join_report["secondary_locations"] == 11


@needs_extract
def test_every_primary_coordinate_agrees_between_the_two_sheets(batch):
    assert batch.join_report["primary_coordinate_mismatches"] == []


@needs_extract
def test_the_repeated_business_identifiers_are_reported_without_duplicating_a_location(
    batch,
):
    """The fan-out the brief warns about, and the reason the join is reported."""
    assert len(batch.join_report["businesses_with_many_policies"]) == 2
    assert SourceRiskLocation.objects.filter(batch=batch).count() == 224
    assert batch.join_report["duplicate_location_keys"] == []


@needs_extract
def test_the_source_checksum_and_parser_version_are_in_the_audit_trail(batch):
    event = AuditEvent.objects.filter(
        subject_type="import_batch", subject_id=batch.id, action=AuditAction.CREATE
    ).first()
    assert event.after_reference["checksum"] == batch.source_checksum
    assert event.after_reference["parser_version"] == extract.PARSER_VERSION
    assert batch.source_checksum.startswith("sha256:")


# -- the profile the brief describes ---------------------------------------------

@needs_extract
def test_the_reported_totals_match_the_brief(batch):
    """USD 3.328bn total, USD 822.817m across geocoded businesses."""
    assert batch.join_report["total_tiv"] == "3327746598.60"
    assert batch.join_report["located_tiv"] == "822816504.04"


@needs_extract
def test_the_cohorts_are_the_sizes_the_brief_states(batch):
    counts = batch.cohort_profile["counts"]
    assert counts["A"] == 63
    assert counts["B"] == 46
    assert counts["C"] == 115
    assert sum(counts.values()) == 224


@needs_extract
def test_the_cohorts_split_by_country_as_the_brief_states(batch):
    by_country = batch.cohort_profile["by_country"]
    assert by_country["A"] == {"Indonesia": 53, "Nepal": 10}
    assert by_country["B"] == {"Indonesia": 31, "Nepal": 15}
    assert by_country["C"] == {"Indonesia": 110, "Nepal": 5}


@needs_extract
def test_the_review_backlog_is_the_flagged_cohort(batch):
    assert transformation_manifest(batch)["review_queue"]["pending"] == 115


@needs_extract
def test_the_business_complete_benchmark_is_forty_two_risks(batch):
    """39 Indonesia and 3 Nepal, at USD 147,044,599.14 of KRE-share TIV."""
    from decimal import Decimal

    locations = [row.values for row in SourceRiskLocation.objects.filter(batch=batch)]
    assignments = extract.assign_all(locations)
    complete = extract.business_complete(locations, assignments)

    assert len(complete) == 42
    total = sum(
        (
            row.gross_limit or Decimal(0)
            for row in SourcePolicyRow.objects.filter(
                batch=batch, business_id__in=complete
            )
        ),
        Decimal(0),
    )
    assert total == Decimal("147044599.14")


@needs_extract
def test_the_real_extract_parses_without_a_single_finding(batch):
    """Geocode confidence is categorical and the em dash means not applicable.

    Both were learned from this file. Reading confidence as a number, or the
    em dash as a broken one, raised 4,457 findings and buried anything real.
    """
    assert batch.findings == []


@needs_extract
def test_shared_coordinates_are_reported_rather_than_deduplicated(batch):
    """167 distinct pairs across 224 locations; every row keeps its identity."""
    assert batch.join_report["distinct_coordinates"] == 167
    assert SourceRiskLocation.objects.filter(batch=batch).count() == 224


@needs_extract
def test_reimporting_the_real_extract_is_idempotent(batch, project, analyst):
    payload = Path(EXTRACT_PATH).read_bytes()
    again = import_extract(
        project, payload, filename=Path(EXTRACT_PATH).name, actor=analyst
    )
    assert again.id == batch.id
    assert SourceRiskLocation.objects.filter(batch=batch).count() == 224


# -- multi-location allocation, against the real portfolio -------------------------

@pytest.fixture()
def rows(batch):
    from apps.exposure.models import SourcePolicyRow, SourceRiskLocation

    return (
        [row.values for row in SourcePolicyRow.objects.filter(batch=batch)],
        [row.values for row in SourceRiskLocation.objects.filter(batch=batch)],
    )


@needs_extract
@pytest.mark.parametrize(
    "method",
    [
        extract.AllocationMethod.EQUAL_LOCATION,
        extract.AllocationMethod.PRIMARY_CONCENTRATED,
    ],
)
def test_every_geocoded_policy_reconciles_under_each_scenario(rows, method):
    """Section 5.3: sum(location_tiv) == gross_limit, for every policy."""
    from decimal import Decimal

    result = extract.allocate(*rows, method=method)

    assert len(result.allocations) == 213
    assert result.source_tiv == Decimal("822816504.04")
    assert result.allocated_tiv == Decimal("822816504.04")
    assert result.reconciles


@needs_extract
def test_the_scenarios_move_value_between_sites_without_changing_the_total(rows):
    """The whole point of a sensitivity: same money, different places."""
    equal = extract.allocate(*rows, method=extract.AllocationMethod.EQUAL_LOCATION)
    primary = extract.allocate(
        *rows, method=extract.AllocationMethod.PRIMARY_CONCENTRATED
    )

    assert equal.allocated_tiv == primary.allocated_tiv
    assert equal.by_location() != primary.by_location()


@needs_extract
def test_every_geocoded_location_receives_a_share(rows):
    result = extract.allocate(*rows)
    assert len(result.by_location()) == 224
    assert sum(result.by_location().values()) == result.allocated_tiv


@needs_extract
def test_the_policies_with_no_geocoded_site_are_excluded_not_dropped_silently(rows):
    policies, _ = rows
    result = extract.allocate(*rows)
    assert len(result.allocations) + len(result.excluded) == len(policies) == 1353
    assert all(reason for _, reason in result.excluded)


@needs_extract
def test_the_multi_location_subset_is_the_size_the_brief_states(rows):
    """USD 55,176,423.17 across ten businesses, and it reconciles."""
    from decimal import Decimal

    policies, locations = rows
    schedules: dict[str, int] = {}
    for row in locations:
        schedules[row["business_id"]] = schedules.get(row["business_id"], 0) + 1
    multi = {business for business, count in schedules.items() if count > 1}

    subset_policies = [p for p in policies if p["business_id"] in multi]
    subset_locations = [item for item in locations if item["business_id"] in multi]

    assert len(multi) == 10
    assert len(subset_locations) == 21

    result = extract.allocate(subset_policies, subset_locations)
    assert result.source_tiv == Decimal("55176423.17")
    assert result.allocated_tiv == Decimal("55176423.17")
    assert result.reconciles


@needs_extract
def test_the_concentration_envelope_bounds_each_multi_location_business(rows):
    """One variant per site, each holding the whole policy TIV."""
    policies, locations = rows
    schedules: dict[str, list] = {}
    for row in locations:
        schedules.setdefault(row["business_id"], []).append(row)

    checked = 0
    for policy in policies:
        schedule = schedules.get(policy["business_id"], [])
        if len(schedule) < 2:
            continue
        variants = extract.concentration_envelope(policy, schedule)
        assert len(variants) == len(schedule)
        for variant in variants:
            held = [share for share in variant.shares if share.amount > 0]
            assert len(held) == 1
            assert held[0].amount == variant.policy_tiv
            assert variant.reconciles
        checked += 1
    assert checked == 10


@needs_extract
def test_the_benchmark_needs_no_allocation_assumption_at_all(rows):
    """All 42 benchmark businesses hold exactly one site, so nothing is assumed."""
    from decimal import Decimal

    policies, locations = rows
    complete = extract.business_complete(locations, extract.assign_all(locations))
    subset_policies = [p for p in policies if p["business_id"] in complete]
    subset_locations = [item for item in locations if item["business_id"] in complete]

    result = extract.allocate(subset_policies, subset_locations)
    assert len(result.allocations) == 42
    assert all(
        item.method is extract.AllocationMethod.SINGLE_LOCATION
        for item in result.allocations
    )
    assert all(
        item.evidence is extract.AllocationEvidence.REPORTED
        for item in result.allocations
    )
    assert result.allocated_tiv == Decimal("147044599.14")
    assert result.reconciles


@needs_extract
def test_restricting_to_cohort_a_excludes_whole_policies_rather_than_redistributing(rows):
    """A partly eligible business leaves entirely, taking all its value."""
    policies, locations = rows
    assignments = extract.assign_all(locations)
    eligible = {
        (item["business_id"], int(item["location_number"]))
        for item, assignment in zip(locations, assignments, strict=True)
        if assignment.cohort is extract.Cohort.A
    }

    gated = extract.allocate(policies, locations, eligible=eligible)
    ungated = extract.allocate(policies, locations)

    assert gated.reconciles
    # No location gains value because another was excluded.
    ungated_totals = ungated.by_location()
    for key, amount in gated.by_location().items():
        assert ungated_totals[key] == amount
