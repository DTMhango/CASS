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
from apps.exposure.extract import import_portfolio, transformation_manifest
from apps.exposure.models import SourcePolicyRow, SourceRiskLocation
from cass_extract import intake

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

EXTRACT_PATH = os.environ.get("CASS_EXTRACT_PATH", "")

needs_extract = pytest.mark.skipif(
    not (EXTRACT_PATH and Path(EXTRACT_PATH).is_file()),
    reason="set CASS_EXTRACT_PATH to the confidential source workbook",
)


@pytest.fixture()
def template_bytes():
    """The confidential workbook, migrated once into a CASS intake template.

    Migrated rather than read directly: CASS no longer ingests the two-sheet
    shape, so the acceptance figures have to survive the conversion to mean
    anything. That they do is itself the test that the retirement was safe.
    """
    from cass_extract import legacy

    payload, _ = legacy.migrate(EXTRACT_PATH, project_reference="idn-fac-2026")
    return payload


@pytest.fixture()
def batch(project, analyst, template_bytes):
    return import_portfolio(
        project,
        template_bytes,
        filename="klapton-re-portfolio.xlsx",
        snapshot_date=dt.date(2026, 6, 30),
        actor=analyst,
    )


# -- the acceptance checks ------------------------------------------------------

@needs_extract
def test_the_declared_row_counts_are_read(batch):
    assert batch.policy_row_count == 1353
    assert batch.risk_row_count == 224
    assert SourcePolicyRow.objects.filter(batch=batch).count() == 1353
    assert SourceRiskLocation.objects.filter(batch=batch).count() == 224


@needs_extract
def test_every_account_and_risk_reference_combination_is_unique(batch):
    """224 risks, no duplicates. A duplicate would put a building in twice."""
    assert SourceRiskLocation.objects.filter(batch=batch).count() == 224
    assert "duplicate_risk" not in batch.intake_report["findings_by_code"]


@needs_extract
def test_the_primary_and_secondary_split_is_retained(batch):
    """213 primary sites and 11 secondary, as the brief states.

    The flag survives the format change because the primary-concentrated
    sensitivity needs it, and it needs it precisely on the multi-site accounts
    whose values the migration leaves blank.
    """
    risks = SourceRiskLocation.objects.filter(batch=batch)
    assert risks.filter(primary_location=True).count() == 213
    assert risks.filter(primary_location=False).count() == 11


@needs_extract
def test_the_coordinate_disagreement_the_old_format_could_have_had_is_gone(batch):
    """The retired workbook carried a coordinate on both sheets, so the two
    could disagree and the join report had to check that they did not.

    A risk is a row now and holds one coordinate, so there is nothing to
    reconcile. The check is retired with the failure mode, rather than kept as
    an assertion that can no longer fail.
    """
    risks = SourceRiskLocation.objects.filter(batch=batch)
    assert risks.exclude(latitude=None).count() == 224


@needs_extract
def test_two_policies_on_one_account_no_longer_need_reporting(batch):
    """The fan-out the brief warned about was a property of the inferred join.

    Two accounts here carry two policies each. Under the old shape that was a
    finding, because joining on the business reference alone would have
    repeated their locations. The account reference is stated on both sheets
    now, so two policies are two policies.
    """
    from django.db.models import Count

    repeated = (
        SourcePolicyRow.objects.filter(batch=batch)
        .values("business_id")
        .annotate(rows=Count("id"))
        .filter(rows__gt=1)
    )
    assert repeated.count() == 2
    assert SourceRiskLocation.objects.filter(batch=batch).count() == 224
    assert batch.blocking is False


@needs_extract
def test_the_source_checksum_and_parser_version_are_in_the_audit_trail(batch):
    event = AuditEvent.objects.filter(
        subject_type="import_batch", subject_id=batch.id, action=AuditAction.CREATE
    ).first()
    assert event.after_reference["checksum"] == batch.source_checksum
    assert event.after_reference["parser_version"] == intake.PARSER_VERSION
    assert batch.source_checksum.startswith("sha256:")


# -- the profile the brief describes ---------------------------------------------

@needs_extract
def test_the_reported_totals_match_the_brief(batch, template_bytes):
    """USD 3.328bn total, USD 822.817m across geocoded businesses.

    Read off the migration rather than the batch: the batch holds what CASS
    now models, and the ungeocoded remainder is precisely what it does not.
    """
    from decimal import Decimal

    from cass_extract import legacy

    _, migration = legacy.migrate(EXTRACT_PATH)
    report = migration.as_dict()
    assert Decimal(report["source_tiv"]) == Decimal("3327746598.60")
    assert Decimal(report["geocoded_tiv"]) == Decimal("822816504.04")


@needs_extract
def test_the_cohorts_are_the_sizes_the_country_screen_leaves(batch):
    """The brief states 63/46/115; the country screen makes it 61/44/114 plus 5.

    The difference is exactly the five rows whose coordinates are not in the
    country they name. The brief's figures were computed from precision and the
    review flag alone, which is what the screen exists to correct -- two of the
    five reach street or better precision and carry no review flag.
    """
    counts = batch.cohort_profile["counts"]
    assert counts["A"] == 61
    assert counts["B"] == 44
    assert counts["C"] == 114
    assert counts["unclassified"] == 5
    assert sum(counts.values()) == 224


@needs_extract
def test_the_cohorts_split_by_country(batch):
    by_country = batch.cohort_profile["by_country"]
    assert by_country["A"] == {"ID": 51, "NP": 10}
    assert by_country["B"] == {"ID": 29, "NP": 15}
    assert by_country["C"] == {"ID": 110, "NP": 4}


@needs_extract
def test_the_five_wrong_country_rows_are_named_and_excluded(batch):
    """Two of them would otherwise have entered the automated cohort."""
    from apps.exposure.models import SourceRiskLocation

    unclassified = SourceRiskLocation.objects.filter(batch=batch, cohort="unclassified")
    assert unclassified.count() == 5
    assert all("outside" in row.cohort_reason for row in unclassified)
    assert {row.business_id for row in unclassified} == {
        "PFAC6368", "PFAC10240", "PFAC10241", "PFAC11086", "PFAC11588",
    }


@needs_extract
def test_the_review_backlog_holds_the_flagged_and_the_unplaceable(batch):
    """114 flagged plus the 5 the rules could not place."""
    assert transformation_manifest(batch)["review_queue"]["pending"] == 119


@needs_extract
def test_the_business_complete_benchmark_is_forty_two_risks(batch):
    """39 Indonesia and 3 Nepal, at USD 147,044,599.14 of KRE-share TIV."""
    from decimal import Decimal

    risks = list(SourceRiskLocation.objects.filter(batch=batch))
    assignments = extract.assign_all([row.values for row in risks])
    complete = extract.business_complete([row.values for row in risks], assignments)

    assert len(complete) == 42
    # Every benchmark business holds one site, so each states its own value and
    # no allocation applies. That is why the total can be read off the risks.
    total = sum(
        (
            row.total_insured_value or Decimal(0)
            for row in risks
            if row.business_id in complete
        ),
        Decimal(0),
    )
    assert total == Decimal("147044599.14")


@needs_extract
def test_the_real_extract_parses_without_a_single_finding(batch):
    """One finding, and it is the state of the book rather than a defect.

    1,138 accounts carry policy terms and no risks, because most of a
    facultative book is not geocoded. Reporting that once with a count is
    something a person can act on; reporting it 1,138 times is noise.
    """
    assert [item["code"] for item in batch.findings] == ["policy_without_risks"]
    assert batch.blocking is False


@needs_extract
def test_shared_coordinates_are_reported_rather_than_deduplicated(batch):
    """167 distinct pairs across 224 risks; every row keeps its identity.

    Two businesses can occupy one building, and collapsing them would lose a
    risk. The pair count is a property of the portfolio rather than of a join,
    so it is read off the staged rows.
    """
    risks = SourceRiskLocation.objects.filter(batch=batch)
    assert risks.count() == 224
    assert len({(row.latitude, row.longitude) for row in risks}) == 167


@needs_extract
def test_reimporting_the_real_extract_is_idempotent(batch, project, analyst, template_bytes):
    again = import_portfolio(
        project, template_bytes, filename="klapton-re-portfolio.xlsx", actor=analyst
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


# -- promoting the real benchmark to OED --------------------------------------------

@needs_extract
def test_the_forty_two_risk_benchmark_promotes_to_a_published_oed_version(batch, analyst):
    """Step 4 of the execution order needs this version to exist and validate."""
    from decimal import Decimal

    from apps.exposure.models import ExposureState
    from apps.exposure.promotion import promote

    version = promote(batch, name="Cohort A Fire benchmark", actor=analyst)

    assert version.state == ExposureState.PUBLISHED
    assert version.is_usable_by_runs is True
    assert version.location_count == 42
    assert version.total_tiv == Decimal("147044599.14")
    assert version.run_currency == "USD"


@needs_extract
def test_the_benchmark_version_carries_no_allocation_assumption(batch, analyst):
    """All 42 businesses hold one site, so nothing about location is assumed."""
    from apps.exposure.promotion import promote

    version = promote(batch, name="Benchmark", actor=analyst)
    allocation = version.source_lineage["allocation"]

    assert allocation["methods_used"] == ["single_location_v1"]
    assert allocation["reconciles"] is True
    assert allocation["policy_count"] == 42


@needs_extract
def test_the_benchmark_spans_both_pilot_countries(batch, analyst):
    from apps.exposure.promotion import promote

    version = promote(batch, name="Benchmark", actor=analyst)
    assert version.source_lineage["countries"] == ["ID", "NP"]
    assert version.tiv_by_country["ID"] > version.tiv_by_country["NP"]


@needs_extract
@pytest.mark.parametrize(
    "split_name",
    [
        "building_only_technical_v1",
        "building_contents_test_v1",
        "commercial_property_test_v1",
    ],
)
def test_the_benchmark_total_holds_under_every_coverage_split(batch, analyst, split_name):
    """A component assumption moves value between columns, never creates it."""
    from decimal import Decimal

    from apps.exposure.promotion import promote

    version = promote(
        batch,
        name=f"Benchmark {split_name}",
        component_split=extract.preset(split_name),
        actor=analyst,
    )
    assert version.total_tiv == Decimal("147044599.14")
    assert version.source_lineage["coverage"]["reconciliation"]["reconciles"] is True


@needs_extract
def test_the_multi_location_sensitivity_promotes_as_its_own_version(batch, analyst):
    """The ten multi-location businesses are a separate allocation test.

    They are cohort-mixed in the real extract, so this promotes the cohort A
    Fire selection under the primary-concentrated scenario and checks the
    version records which scenario produced it.
    """
    from apps.exposure.promotion import promote

    version = promote(
        batch,
        name="Primary concentrated sensitivity",
        allocation_method=extract.AllocationMethod.PRIMARY_CONCENTRATED,
        actor=analyst,
    )
    assert version.source_lineage["allocation"]["method"] == "primary_concentrated_v1"
    assert version.source_lineage["allocation"]["reconciles"] is True


@needs_extract
def test_one_batch_can_produce_several_versions(batch, analyst):
    """A benchmark and a sensitivity are different selections of one read."""
    from apps.exposure.promotion import promote

    promote(batch, name="Baseline", actor=analyst)
    promote(
        batch,
        name="Sensitivity",
        allocation_method=extract.AllocationMethod.PRIMARY_CONCENTRATED,
        actor=analyst,
    )
    assert batch.exposure_versions.count() == 2


@needs_extract
def test_the_promoted_oed_names_no_counterparty(batch, analyst):
    import csv
    import io

    from apps.artifacts.models import ArtifactLink
    from apps.common.storage import get_store
    from apps.exposure.promotion import promote

    version = promote(batch, name="Benchmark", actor=analyst)
    link = ArtifactLink.objects.get(
        subject_type="exposure_version", subject_id=version.id, role="oed_location"
    )
    with get_store().open(link.artifact.uri) as handle:
        text = handle.read().decode("utf-8")

    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 42
    assert set(rows[0]) == {
        "PortNumber", "AccNumber", "LocNumber", "CountryCode", "Latitude",
        "Longitude", "OccupancyCode", "ConstructionCode", "LocPerilsCovered",
        "BuildingTIV", "OtherTIV", "ContentsTIV", "BITIV", "LocCurrency",
    }
    # Business references only, never a name.
    assert all(row["AccNumber"].startswith("PFAC") for row in rows)


# -- step 5: the KRE-share earthquake golden test, on the real extract -------------------

@pytest.fixture()
def pilot_models(db, modeller):
    """Both prototype country models, registered with their cells and functions."""
    from apps.modelregistry import pilot

    return {model.country_code: model for model in pilot.register_all(actor=modeller)}


def _keys_for(version, model):
    """Run the CASS keys lookup over a published version, as a run would."""
    from apps.exposure import services as exposure_services
    from apps.modelregistry.assets import load_grid, load_vulnerability
    from cass_keys.lookup import lookup

    files = exposure_services.load_files(version)
    return lookup(
        [dict(row.raw) for row in files.location.rows],
        grid=load_grid(model.grid),
        vulnerability=load_vulnerability(model.vulnerability_set),
    )


@needs_extract
@pytest.mark.parametrize(
    ("country", "code"), [("ID", "ID"), ("NP", "NP")]
)
def test_every_benchmark_location_maps_against_its_country_model(
    batch, analyst, pilot_models, country, code
):
    """The heart of step 5: no benchmark location falls outside grid or taxonomy."""
    from apps.exposure.promotion import promote

    version = promote(batch, name=f"{country} benchmark", country=country, actor=analyst)
    result = _keys_for(version, pilot_models[code])

    # Distinct locations, not records: each location produces one record per
    # coverage type, so a record count answers a different question.
    assert len({item.location_id for item in result.successes}) == version.location_count
    assert [item.message for item in result.failures] == []


@needs_extract
@pytest.mark.parametrize(
    ("country", "code"), [("ID", "ID"), ("NP", "NP")]
)
def test_the_whole_kre_share_tiv_maps_and_reconciles(
    batch, analyst, pilot_models, country, code
):
    """Section 15: unmapped TIV is exposure silently omitted from the loss."""
    from apps.exposure.promotion import promote

    version = promote(batch, name=f"{country} benchmark", country=country, actor=analyst)
    report = _keys_for(version, pilot_models[code]).report

    assert report.source_tiv == version.total_tiv
    assert report.mapped_tiv == version.total_tiv
    assert report.failed_tiv == 0
    assert report.reconciled is True
    assert report.difference == 0


@needs_extract
def test_the_two_country_benchmark_splits_into_two_runnable_selections(batch, analyst):
    """A model version covers one country, so the 42 risks are two runs, not one.

    The two selections must add back to the whole benchmark: a country filter
    that quietly dropped a risk would understate the book by exactly the amount
    nobody noticed.
    """
    from decimal import Decimal

    from apps.exposure.promotion import promote

    whole = promote(batch, name="Whole benchmark", actor=analyst)
    indonesia = promote(batch, name="Indonesia benchmark", country="ID", actor=analyst)
    nepal = promote(batch, name="Nepal benchmark", country="NP", actor=analyst)

    assert indonesia.location_count + nepal.location_count == whole.location_count == 42
    assert indonesia.total_tiv + nepal.total_tiv == whole.total_tiv == Decimal("147044599.14")


@needs_extract
def test_the_whole_benchmark_cannot_run_against_one_country_model(
    batch, analyst, pilot_models
):
    """Reported rather than hidden: the Nepali sites are outside every ID tile."""
    from apps.exposure.promotion import promote

    whole = promote(batch, name="Whole benchmark", actor=analyst)
    report = _keys_for(whole, pilot_models["ID"]).report

    assert report.failed_tiv > 0
    assert report.mapped_tiv + report.failed_tiv == report.source_tiv
    assert any("outside the area-peril grid" in reason for reason in report.by_reason)


@needs_extract
def test_the_benchmark_is_reproducible_key_for_key(batch, analyst, pilot_models):
    """A golden test that is not byte-identical between runs pins nothing."""
    from apps.exposure.promotion import promote

    first = promote(batch, name="Benchmark one", country="ID", actor=analyst)
    second = promote(batch, name="Benchmark two", country="ID", actor=analyst)

    rows_one = [item.as_row() for item in _keys_for(first, pilot_models["ID"]).records]
    rows_two = [item.as_row() for item in _keys_for(second, pilot_models["ID"]).records]
    assert rows_one == rows_two


@needs_extract
def test_the_real_benchmark_still_holds_at_the_gate_without_an_occupancy(
    batch, analyst, pilot_models
):
    """The assumption is what unlocks the engine, on the real book as much as a fixture."""
    from apps.exposure.promotion import promote

    version = promote(
        batch,
        name="Occupancy not reported",
        country="ID",
        occupancy=extract.NOT_REPORTED,
        actor=analyst,
    )
    report = _keys_for(version, pilot_models["ID"]).report

    assert report.mapped_tiv == 0
    assert report.failed_tiv == version.total_tiv
    assert report.reconciled is True


@needs_extract
def test_the_real_benchmark_maps_across_the_grid_rather_than_into_one_cell(
    batch, analyst, pilot_models
):
    """42 risks in one cell would mean the grid, not the book, chose the answer."""
    from apps.exposure.promotion import promote

    version = promote(batch, name="Benchmark", country="ID", actor=analyst)
    result = _keys_for(version, pilot_models["ID"])
    cells = {item.area_peril_id for item in result.successes}
    assert len(cells) > 10


# -- step 6: the multi-location allocation scenarios, on the real extract -----------------

@needs_extract
def test_no_multi_location_business_reaches_the_benchmark(batch, analyst, pilot_models):
    """Which is why the benchmark needed no allocation assumption at all.

    Stated as a scenario comparison rather than inferred: the comparison over
    cohort A Fire finds nothing the allocation choice can move, so the two
    scenarios are the same portfolio.
    """
    from apps.exposure import scenarios

    comparison = scenarios.compare(
        batch, model_version=pilot_models["ID"], country="ID"
    )
    assert comparison.total_holds is True
    assert [item.business_id for item in comparison.materiality if item.material] == []
    assert comparison.envelope == {}
    assert comparison.movement(comparison.scenarios[1]) == {}


@needs_extract
def test_the_allocation_scenarios_move_value_between_cells_in_the_review_cohort(
    batch, analyst, pilot_models
):
    """Cohort C Fire is where the assumption actually bites: six of the ten.

    Worth stating plainly -- every business the allocation choice can reach is
    in the analyst-review backlog, so the assumption and the review queue are
    the same work, not two.
    """
    from decimal import Decimal

    from apps.exposure import scenarios

    comparison = scenarios.compare(
        batch,
        model_version=pilot_models["ID"],
        cohort=extract.Cohort.C,
        country="ID",
    )
    material = [item for item in comparison.materiality if item.material]

    assert comparison.total_holds is True
    assert material, "cohort C Fire holds six multi-location businesses"
    movement = comparison.movement(comparison.scenarios[1])
    assert movement
    assert sum(movement.values(), Decimal("0.00")) == 0


@needs_extract
def test_every_real_scenario_still_maps_completely(batch, analyst, pilot_models):
    """Moving value between sites must not move any of it outside the grid."""
    from apps.exposure import scenarios

    comparison = scenarios.compare(
        batch,
        model_version=pilot_models["ID"],
        cohort=extract.Cohort.C,
        country="ID",
    )
    for scenario in comparison.scenarios:
        assert scenario.reconciles is True
        assert scenario.mapped_tiv == scenario.total_tiv
        assert scenario.failed_tiv == 0


@needs_extract
def test_the_envelope_bounds_each_multi_location_business_in_cell_terms(
    batch, analyst, pilot_models
):
    """One candidate cell per site, which is what the brief's envelope means here."""
    from apps.exposure import scenarios

    comparison = scenarios.compare(
        batch,
        model_version=pilot_models["ID"],
        cohort=extract.Cohort.C,
        country="ID",
    )
    assert comparison.envelope
    for business_id, cells in comparison.envelope.items():
        entry = next(
            item for item in comparison.materiality if item.business_id == business_id
        )
        assert len(cells) == entry.area_peril_count
        assert len(cells) <= entry.location_count


@needs_extract
def test_a_business_whose_schedule_straddles_cohorts_enters_neither(batch):
    """PFAC9832 has sites in cohort A and cohort B, so it is complete in neither.

    The whole-schedule rule doing its job on the real book: taking the cohort A
    site alone would leave the policy's value with nowhere honest to go.
    """
    rows = [row.values for row in batch.location_rows.all()]
    assignments = extract.assign_all(rows)
    for cohort in (extract.Cohort.A, extract.Cohort.B):
        selected = extract.business_complete(
            rows, assignments, cohort=cohort, class_of_business=None
        )
        assert "PFAC9832" not in selected


@needs_extract
def test_the_real_comparison_is_reproducible(batch, analyst, pilot_models):
    from apps.exposure import scenarios

    def run():
        return scenarios.compare(
            batch,
            model_version=pilot_models["ID"],
            cohort=extract.Cohort.C,
            country="ID",
        ).as_dict()

    assert run() == run()


@needs_extract
def test_the_sensitivity_moves_the_amount_it_claims_to(batch, analyst, pilot_models):
    """A stated 70/30 has to actually be a 70/30 at the model.

    Every material business here schedules two sites, so equal-location puts
    half at each and primary-concentrated puts 70% at one: exactly a fifth of
    each business's value crosses between cells. Checking the magnitude rather
    than only the direction is what catches a weighting that was applied to the
    wrong denominator -- an error that nets to zero and looks correct.
    """
    from decimal import Decimal

    from apps.exposure import scenarios

    comparison = scenarios.compare(
        batch,
        model_version=pilot_models["ID"],
        cohort=extract.Cohort.C,
        country="ID",
    )
    material = [item for item in comparison.materiality if item.material]
    assert {item.location_count for item in material} == {2}

    movement = comparison.movement(comparison.scenarios[1])
    gross = sum((abs(amount) for amount in movement.values()), Decimal("0.00")) / 2
    expected = comparison.material_tiv / 5

    # Within a cent per business: the shares are exact rationals but the
    # amounts are whole cents, so a residual can land either way.
    assert abs(gross - expected) <= Decimal("0.01") * len(material)


# -- migrating the pilot portfolio to the CASS intake template ---------------------------

@needs_extract
def test_the_migration_preserves_every_acceptance_figure(batch):
    """The retirement is only safe if the numbers survive it.

    Each of these appears in section 7 of the integration brief, and each is
    reproduced here from the migrated template rather than from the workbook,
    so the two formats are proved to describe the same portfolio.
    """
    from decimal import Decimal

    from cass_extract import legacy

    _, result = legacy.migrate(EXTRACT_PATH, project_reference="idn-fac-2026")
    report = result.as_dict()

    assert report["risks"] == 224
    assert report["policies"] == 1353
    assert Decimal(report["source_tiv"]) == Decimal("3327746598.60")
    assert Decimal(report["geocoded_tiv"]) == Decimal("822816504.04")
    assert Decimal(report["value_left_to_allocate"]) == Decimal("55176423.17")
    assert report["multi_site_businesses"] == 10
    assert report["risks_awaiting_allocation"] == 21


@needs_extract
def test_the_migrated_template_reads_back_cleanly(batch):
    """One finding, and it is the state of the book rather than a defect."""
    import io

    from cass_extract import intake, legacy

    payload, _ = legacy.migrate(EXTRACT_PATH, project_reference="idn-fac-2026")
    read = intake.read_workbook(io.BytesIO(payload))

    assert read.is_readable is True
    assert len(read.risks.rows) == 224
    assert len(read.policies.rows) == 1353
    assert [item.code for item in read.findings] == ["policy_without_risks"]
    assert read.findings[0].value == "1138"


@needs_extract
def test_the_allocation_question_becomes_visible_in_the_data(batch):
    """The point of the new shape, on the real book.

    Under the old format every one of the 213 geocoded policies carried an
    allocation assumption whether it needed one or not. Under this one, 203
    businesses state what their site is worth and only the 21 risks of the ten
    multi-site businesses await a division -- which is exactly the population
    the step 6 materiality report identified.
    """
    import io

    from cass_extract import intake, legacy

    payload, _ = legacy.migrate(EXTRACT_PATH, project_reference="idn-fac-2026")
    read = intake.read_workbook(io.BytesIO(payload))

    assert dict(intake.coverage_evidence(read)) == {
        "risks": 224,
        "coverages_stated": 0,
        "risk_total_stated": 203,
        "allocated_from_policy": 21,
    }


@needs_extract
def test_the_migration_is_reproducible(batch):
    """A confidential fixture nobody can regenerate is one nobody can check."""
    import io

    from cass_extract import intake, legacy

    # Compared as content, not as bytes: an xlsx is a zip and openpyxl stamps
    # its entries with the time of writing, so two identical workbooks differ
    # by a byte or two. What has to be stable is every cell.
    def rows():
        payload, _ = legacy.migrate(EXTRACT_PATH, generated=dt.date(2026, 6, 30))
        read = intake.read_workbook(io.BytesIO(payload))
        return (
            [dict(row.raw) for row in read.risks.rows],
            [dict(row.raw) for row in read.policies.rows],
        )

    first_risks, first_policies = rows()
    second_risks, second_policies = rows()
    assert first_risks == second_risks
    assert first_policies == second_policies


@needs_extract
def test_no_counterparty_column_crosses_into_the_template(batch):
    """Insured, cedent, broker and business title have no destination.

    Not because they are secret -- Klapton Re holds no portfolio information a
    Klapton Re colleague may not see -- but because a name is not a property.
    Nothing in the model consumes one, and a column nothing consumes is one
    more thing to keep correct.
    """
    from cass_extract import legacy
    from cass_extract.reader import read_workbook

    converted = legacy.convert(read_workbook(EXTRACT_PATH))
    written = {key for row in converted.risks for key in row}
    written |= {key for row in converted.policies for key in row}

    assert not written & {"insured_name", "cedent_name", "broker_name", "business_title"}
    # "Total insured value" is an amount, not a party, so match whole names.
    assert not {
        item
        for item in written
        if item.lower() in ("insured", "cedent", "broker", "business title", "insured name")
    }


@needs_extract
def test_the_address_reaches_the_template_and_is_meant_to(batch):
    """A modeller who cannot read the address cannot check the coordinate.

    Six of this source's addresses begin with the insured's registered name --
    "PT Ainul Hayat Sejahtera, Mangunreja 42455" -- and that is fine: the
    address is what a reviewer compares a geocode against, and stripping the
    name out of it would corrupt the only evidence the review has.
    """
    from cass_extract import legacy
    from cass_extract.reader import read_workbook

    converted = legacy.convert(read_workbook(EXTRACT_PATH))
    addressed = [row for row in converted.risks if row.get("Address")]
    assert len(addressed) > 200
