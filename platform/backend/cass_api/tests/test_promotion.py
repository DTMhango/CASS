"""Promoting a staged selection into an OED exposure version.

The point of these tests is that the assumptions are choices and the choices
are visible. A promotion picks a cohort, an allocation scenario and a coverage
split; each is recorded on the version that results, each can be changed, and
whatever is chosen the value reconciles exactly.
"""

from __future__ import annotations

import csv
import io
import sys
from decimal import Decimal
from pathlib import Path

import pytest

import cass_extract as extract
from apps.audit.models import AuditAction, AuditEvent
from apps.common.storage import get_store
from apps.exposure.extract import import_portfolio, location_identity
from apps.exposure.models import ExposureState, ExposureVersion, SourceRiskLocation
from apps.exposure.promotion import (
    UNKNOWN_OCCUPANCY,
    PromotionError,
    promote,
    promotion_summary,
)

from .conftest import API

_FIXTURES = Path(__file__).resolve().parents[2] / "packages" / "cass_extract" / "tests"
if str(_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_FIXTURES))

from fixtures import as_template  # noqa: E402

pytestmark = pytest.mark.django_db


@pytest.fixture()
def batch(project, analyst):
    return import_portfolio(
        project, as_template(), filename="portfolio.xlsx", actor=analyst
    )


@pytest.fixture()
def version(batch, analyst) -> ExposureVersion:
    return promote(batch, name="Cohort A Fire benchmark", actor=analyst)


def oed_rows(version: ExposureVersion) -> list[dict[str, str]]:
    """Read back the OED location file the promotion wrote."""
    from apps.artifacts.models import ArtifactLink

    link = ArtifactLink.objects.get(
        subject_type="exposure_version", subject_id=version.id, role="oed_location"
    )
    with get_store().open(link.artifact.uri) as handle:
        text = handle.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


# -- what a promotion produces ----------------------------------------------------

def test_a_promotion_publishes_an_immutable_exposure_version(version):
    assert version.state == ExposureState.PUBLISHED
    assert version.is_frozen is True
    assert version.is_usable_by_runs is True


def test_the_selection_is_business_complete(version):
    """B-PARTIAL has a flagged site, so the whole business stays out."""
    accounts = {row["AccNumber"] for row in oed_rows(version)}
    assert "B-MULTI" in accounts
    assert "B-SINGLE" in accounts
    assert "B-PARTIAL" not in accounts
    assert "B-NEPAL" not in accounts      # Engineering
    assert "B-LIABILITY" not in accounts  # Liability


def test_the_identity_mapping_follows_section_four_point_four(version, batch):
    """Project reference is the portfolio; business is the account."""
    row = next(row for row in oed_rows(version) if row["AccNumber"] == "B-SINGLE")
    assert row["PortNumber"] == batch.project.reference
    assert row["LocNumber"] == "1"
    assert row["CountryCode"] == "ID"
    assert row["LocCurrency"] == "USD"
    assert row["LocPerilsCovered"] == "QEQ"


def test_no_counterparty_name_reaches_the_oed(version):
    """Names are confidential and are not stable enough to key on."""
    text = str(oed_rows(version))
    assert "Example Insured" not in text
    assert "Example Broker" not in text


def test_a_deterministic_location_identity_is_recorded(batch):
    """Section 4.4: the same location in the same source is the same id."""
    row = SourceRiskLocation.objects.filter(batch=batch).first()
    assert row.cass_location_id == location_identity(
        batch.source_checksum, row.business_id, row.location_number
    )


# -- occupancy is stated, never invented ---------------------------------------------

def test_the_default_occupancy_is_one_stated_class_for_every_row(version):
    """A single visible assumption, not a derivation dressed up as information."""
    assert {row["OccupancyCode"] for row in oed_rows(version)} == {"1100"}
    assert version.source_lineage["taxonomy"]["assumption"]["name"] == "commercial_general_v1"
    assert version.source_lineage["taxonomy"]["source"] == "assumed"


def test_the_honest_assumption_writes_oed_unknown_and_is_still_available(batch, analyst):
    """The source supports nothing else, and saying so must stay one parameter away."""
    result = promote(
        batch, name="Unknown", occupancy=extract.NOT_REPORTED, actor=analyst
    )
    assert {row["OccupancyCode"] for row in oed_rows(result)} == {UNKNOWN_OCCUPANCY}
    assert result.source_lineage["taxonomy"]["assumption"]["approved"] is True


def test_no_preset_but_the_honest_one_claims_approval():
    """An assumed occupancy is an assumption about the answer, not about an input."""
    approved = {
        name for name, item in extract.OCCUPANCY_PRESETS.items() if item.approved
    }
    assert approved == {"not_reported_v1"}


def test_a_spread_assumption_is_reproducible_rather_than_random(batch, analyst):
    """A loss that moved between runs must have moved for a reason."""
    first = promote(
        batch, name="Mixed one", occupancy=extract.MIXED_COMMERCIAL, actor=analyst
    )
    second = promote(
        batch, name="Mixed two", occupancy=extract.MIXED_COMMERCIAL, actor=analyst
    )
    codes = {
        (row["AccNumber"], row["LocNumber"]): row["OccupancyCode"]
        for row in oed_rows(first)
    }
    assert codes == {
        (row["AccNumber"], row["LocNumber"]): row["OccupancyCode"]
        for row in oed_rows(second)
    }
    assert len(set(codes.values())) > 1


def test_a_spread_assumption_reports_what_it_assigned(batch, analyst):
    result = promote(
        batch, name="Mixed", occupancy=extract.MIXED_COMMERCIAL, actor=analyst
    )
    record = result.source_lineage["taxonomy"]
    assert sum(record["class_distribution"].values()) == result.location_count
    assert sum(record["occupancy_counts"].values()) == result.location_count


def test_an_analyst_may_state_any_occupancy_without_a_release(batch, analyst):
    result = promote(
        batch,
        name="All industrial",
        occupancy=extract.uniform("industrial_test_v1", "1150", "5200"),
        actor=analyst,
    )
    rows = oed_rows(result)
    assert {row["OccupancyCode"] for row in rows} == {"1150"}
    assert {row["ConstructionCode"] for row in rows} == {"5200"}


def test_the_lineage_says_which_attributes_were_not_reported(version):
    absent = version.source_lineage["attributes_not_reported"]
    assert "not in the source" in absent["OccupancyCode"]
    assert "not inferred" in absent["YearBuilt"]
    assert (
        "an unapproved occupancy assumption (commercial_general_v1)"
        in version.source_lineage["decision_use"]
    )


# -- the coverage split is a choice ----------------------------------------------------

def test_the_default_split_is_the_conservative_one(version):
    """A default that spread value would assert an unapproved prior."""
    assert version.source_lineage["coverage"]["split"]["name"] == "building_only_technical_v1"
    row = next(row for row in oed_rows(version) if row["AccNumber"] == "B-SINGLE")
    assert row["BuildingTIV"] == "1000000.00"
    assert row["ContentsTIV"] == "0.00"


def test_a_different_split_moves_value_between_coverages(batch, analyst):
    result = promote(
        batch,
        name="Building and contents",
        component_split=extract.preset("building_contents_test_v1"),
        actor=analyst,
    )
    row = next(row for row in oed_rows(result) if row["AccNumber"] == "B-SINGLE")
    assert row["BuildingTIV"] == "800000.00"
    assert row["ContentsTIV"] == "200000.00"


def test_an_analyst_supplied_split_is_applied_as_given(batch, analyst):
    """The point of the assumption is that someone can change it."""
    result = promote(
        batch,
        name="My split",
        component_split=extract.custom(
            "my_split_v1", {"building": 55, "other": 10, "contents": 30, "bi": 5}
        ),
        actor=analyst,
    )
    row = next(row for row in oed_rows(result) if row["AccNumber"] == "B-SINGLE")
    assert row["BuildingTIV"] == "550000.00"
    assert row["OtherTIV"] == "100000.00"
    assert row["ContentsTIV"] == "300000.00"
    assert row["BITIV"] == "50000.00"


@pytest.mark.parametrize(
    "split_name",
    ["building_only_technical_v1", "building_contents_test_v1", "commercial_property_test_v1"],
)
def test_every_split_reconciles_to_the_location_total(batch, analyst, split_name):
    result = promote(
        batch,
        name=f"Split {split_name}",
        component_split=extract.preset(split_name),
        actor=analyst,
    )
    for row in oed_rows(result):
        components = sum(
            Decimal(row[str(coverage)]) for coverage in extract.COVERAGE_ORDER
        )
        assert components > 0
    assert result.source_lineage["coverage"]["reconciliation"]["reconciles"] is True


def test_the_total_is_the_same_whatever_the_split(batch, analyst):
    """A component assumption moves value between columns, never creates it."""
    conservative = promote(
        batch, name="A", component_split=extract.preset("building_only_technical_v1"),
        actor=analyst,
    )
    spread = promote(
        batch, name="B", component_split=extract.preset("commercial_property_test_v1"),
        actor=analyst,
    )
    assert conservative.total_tiv == spread.total_tiv


def test_a_split_that_does_not_add_to_one_hundred_is_refused():
    """Normalising would silently apply something other than what was asked."""
    with pytest.raises(extract.AllocationError, match="summing to"):
        extract.custom("bad_v1", {"building": 60, "contents": 30})


def test_a_split_naming_a_coverage_oed_does_not_have_is_refused():
    with pytest.raises(extract.AllocationError, match="not an OED coverage"):
        extract.custom("bad_v1", {"building": 50, "livestock": 50})


# -- the allocation scenario is a choice too ---------------------------------------------

def test_the_baseline_splits_a_multi_location_policy_equally(version):
    rows = {row["LocNumber"]: row for row in oed_rows(version) if row["AccNumber"] == "B-MULTI"}
    assert len(rows) == 3
    assert {row["BuildingTIV"] for row in rows.values()} == {"1000000.00"}


def test_the_primary_sensitivity_concentrates_on_the_primary_site(batch, analyst):
    result = promote(
        batch,
        name="Primary concentrated",
        allocation_method=extract.AllocationMethod.PRIMARY_CONCENTRATED,
        actor=analyst,
    )
    rows = {row["LocNumber"]: row for row in oed_rows(result) if row["AccNumber"] == "B-MULTI"}
    assert rows["1"]["BuildingTIV"] == "2100000.00"
    assert rows["2"]["BuildingTIV"] == "450000.00"
    assert rows["3"]["BuildingTIV"] == "450000.00"


def test_the_scenario_does_not_change_the_portfolio_total(batch, analyst):
    equal = promote(batch, name="Equal", actor=analyst)
    primary = promote(
        batch,
        name="Primary",
        allocation_method=extract.AllocationMethod.PRIMARY_CONCENTRATED,
        actor=analyst,
    )
    assert equal.total_tiv == primary.total_tiv


def test_the_lineage_records_the_allocation_scenario(version):
    allocation = version.source_lineage["allocation"]
    assert allocation["method"] == "equal_location_v1"
    assert allocation["reconciles"] is True
    assert allocation["rule_version"] == extract.ALLOCATION_RULE_VERSION


# -- what the version says about itself ----------------------------------------------------

def test_the_version_states_the_value_basis(version):
    basis = version.source_lineage["value_basis"]
    assert "KRE's share" in basis
    assert "not applied again" in basis


def test_the_version_blocks_decision_use_and_says_why(version):
    decision = version.source_lineage["decision_use"]
    assert decision.startswith("Blocked")
    assert "unapproved coverage split" in decision


def test_the_version_points_back_at_the_batch_that_produced_it(version, batch):
    assert version.source_lineage["import_batch"] == str(batch.id)
    assert version.source_lineage["source_checksum"] == batch.source_checksum
    assert batch.exposure_versions.filter(id=version.id).exists()


def test_the_summary_gathers_what_a_reader_needs(version):
    summary = promotion_summary(version)
    assert summary["cohort"] == "A"
    assert summary["coverage"]["split"]["name"] == "building_only_technical_v1"
    assert summary["decision_use"].startswith("Blocked")
    assert "OccupancyCode" in summary["attributes_not_reported"]


def test_the_promotion_is_audited(version, batch):
    # Publishing records its own event, so name the promotion's specifically.
    event = AuditEvent.objects.filter(
        subject_type="exposure_version",
        subject_id=version.id,
        action=AuditAction.PUBLISH,
        detail__startswith="Promoted",
    ).get()
    assert event.after_reference["import_batch"] == str(batch.id)
    assert event.after_reference["coverage_source"] == "derived_split"


# -- refusals -------------------------------------------------------------------------------

def test_a_selection_with_no_complete_business_is_refused(batch, analyst):
    with pytest.raises(PromotionError, match="nowhere honest to go"):
        promote(batch, name="Empty", cohort=extract.Cohort.C, actor=analyst)


def test_the_generated_oed_is_validated_before_it_is_published(version):
    """It goes through the ordinary exposure pipeline, not around it."""
    assert version.validation_report["publishable"] is True
    assert version.location_count == len(oed_rows(version))


def test_the_published_total_equals_the_allocated_total(version):
    rows = oed_rows(version)
    total = sum(
        Decimal(row[str(coverage)])
        for row in rows
        for coverage in extract.COVERAGE_ORDER
    )
    assert version.total_tiv == total
    assert Decimal(version.source_lineage["allocation"]["allocated_tiv"]) == total


# -- the API ---------------------------------------------------------------------------------

def test_the_assumption_catalogue_lists_what_the_platform_supports(api):
    response = api.get(f"{API}/assumptions/")
    assert response.status_code == 200

    names = {item["name"] for item in response.data["coverage_splits"]}
    assert "building_only_technical_v1" in names
    assert response.data["default_coverage_split"] == "building_only_technical_v1"
    assert response.data["custom_split_allowed"] is True
    assert {item["value"] for item in response.data["allocation_methods"]} == {
        "equal_location_v1",
        "primary_concentrated_v1",
    }


def test_the_catalogue_says_which_assumptions_are_approved(api):
    response = api.get(f"{API}/assumptions/")
    assert all(item["approved"] is False for item in response.data["coverage_splits"])


def test_an_analyst_can_promote_through_the_api(api, batch):
    response = api.post(
        f"{API}/portfolio-imports/{batch.id}/promote/",
        {"name": "Benchmark", "cohort": "A", "coverage_split": "building_contents_test_v1"},
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["coverage"]["split"]["name"] == "building_contents_test_v1"
    assert response.data["decision_use"].startswith("Blocked")


def test_custom_percentages_can_be_supplied_through_the_api(api, batch):
    response = api.post(
        f"{API}/portfolio-imports/{batch.id}/promote/",
        {
            "name": "Custom",
            "coverage_split": "analyst_split_v1",
            "coverage_percentages": {"building": 70, "contents": 30},
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["coverage"]["split"]["percentages"]["BuildingTIV"] == 70.0


def test_a_bad_custom_split_is_refused_with_a_reason(api, batch):
    response = api.post(
        f"{API}/portfolio-imports/{batch.id}/promote/",
        {
            "name": "Bad",
            "coverage_percentages": {"building": 70, "contents": 20},
        },
        format="json",
    )
    assert response.status_code == 409
    assert "summing to" in response.data["detail"]


def test_promoting_needs_a_name(api, batch):
    response = api.post(
        f"{API}/portfolio-imports/{batch.id}/promote/", {}, format="json"
    )
    assert response.status_code == 400
    assert "Name the exposure version" in response.data["detail"]


def test_someone_without_write_access_cannot_promote(client_for, outsider, batch):
    response = client_for(outsider).post(
        f"{API}/portfolio-imports/{batch.id}/promote/",
        {"name": "Nope"},
        format="json",
    )
    assert response.status_code in (403, 404)


# -- value reads in three tiers ------------------------------------------------------

def valued(**overrides):
    """The standard portfolio with one risk's values overridden."""
    from fixtures import policy_row, risk_row, structural_template

    risks, policies = structural_template()
    for row in risks:
        if row["Account reference"] == "B-SINGLE":
            row.update(overrides)
    return as_template(risks, policies), policies, policy_row, risk_row


def promoted(payload, project, analyst, **kwargs):
    batch = import_portfolio(project, payload, filename="p.xlsx", actor=analyst)
    return promote(batch, name="Tiered", actor=analyst, **kwargs)


def test_a_risk_that_states_its_coverages_keeps_them_exactly(project, analyst):
    """Nothing is assumed, so no split touches the row."""
    payload, *_ = valued(
        **{
            "Total insured value": "",
            "Building value": "600000.00",
            "Contents value": "400000.00",
        }
    )
    version = promoted(payload, project, analyst)
    row = next(item for item in oed_rows(version) if item["AccNumber"] == "B-SINGLE")

    assert row["BuildingTIV"] == "600000.00"
    assert row["ContentsTIV"] == "400000.00"


def test_a_stated_zero_is_a_statement_and_is_not_split(project, analyst):
    """A schedule with a building figure and no contents says there are none."""
    payload, *_ = valued(
        **{"Total insured value": "", "Building value": "1000000.00"}
    )
    version = promoted(payload, project, analyst)
    row = next(item for item in oed_rows(version) if item["AccNumber"] == "B-SINGLE")

    assert row["BuildingTIV"] == "1000000.00"
    assert row["ContentsTIV"] == "0.00"


def test_a_risk_total_is_divided_by_the_component_assumption(project, analyst):
    """The middle tier: the value is known, the breakdown is not."""
    version = promoted(
        as_template(), project, analyst,
        component_split=extract.preset("building_contents_test_v1"),
    )
    row = next(item for item in oed_rows(version) if item["AccNumber"] == "B-SINGLE")

    assert row["BuildingTIV"] == "800000.00"
    assert row["ContentsTIV"] == "200000.00"


def test_the_lineage_counts_the_risks_in_each_tier(project, analyst):
    """A reader cannot tell a stated number from an assumed one by looking."""
    version = promoted(as_template(), project, analyst)
    tiers = version.source_lineage["coverage"]["evidence_tiers"]

    # Three single-site accounts state what their site is worth; B-MULTI's
    # three sites state nothing and are divided from the policy total.
    assert tiers["stated_total"] == 3
    assert tiers["allocated_from_policy"] == 3
    assert tiers["stated_coverages"] == 0
    assert sum(tiers.values()) == version.location_count


def test_a_portfolio_that_states_everything_needs_no_split_at_all(project, analyst):
    """And the version says so rather than naming an assumption it never used."""
    from fixtures import structural_template

    risks, policies = structural_template()
    for row in risks:
        row["Total insured value"] = ""
        row["Building value"] = "1000000.00"

    version = promoted(as_template(risks, policies), project, analyst)
    coverage = version.source_lineage["coverage"]

    assert coverage["source"] == "stated_coverage_values"
    assert coverage["split"] is None
    assert coverage["decision_note"] == "stated coverage values"


def test_a_mixed_portfolio_says_how_much_rested_on_the_assumption(project, analyst):
    from fixtures import structural_template

    risks, policies = structural_template()
    for row in risks:
        if row["Account reference"] == "B-SHARED":
            row["Total insured value"] = ""
            row["Building value"] = "250000.00"

    version = promoted(as_template(risks, policies), project, analyst)
    coverage = version.source_lineage["coverage"]

    assert coverage["source"] == "mixed_stated_and_derived"
    assert "state no coverage breakdown" in coverage["basis"]


def test_a_stated_occupancy_survives_the_promotion(project, analyst):
    payload, *_ = valued(**{"Occupancy": "1150", "Construction": "5200"})
    version = promoted(payload, project, analyst)
    row = next(item for item in oed_rows(version) if item["AccNumber"] == "B-SINGLE")

    assert row["OccupancyCode"] == "1150"
    assert row["ConstructionCode"] == "5200"
    assert version.source_lineage["taxonomy"]["source"] == "mixed_reported_and_assumed"


def test_a_risk_with_no_value_and_no_policy_total_is_refused(project, analyst):
    """A risk worth nothing is not what an empty row means."""
    from fixtures import structural_template

    risks, policies = structural_template()
    for row in risks:
        row["Total insured value"] = ""
    policies = [item for item in policies if item["Account reference"] != "B-SINGLE"]

    batch = import_portfolio(
        project, as_template(risks, policies), filename="p.xlsx", actor=analyst
    )
    with pytest.raises(PromotionError, match="would enter the model worth nothing"):
        promote(batch, name="Unvalued", actor=analyst)


# -- through the API ------------------------------------------------------------------

def test_the_blank_template_downloads_as_a_workbook(api, project):
    response = api.get(f"{API}/portfolio-imports/template/?project={project.id}")
    assert response.status_code == 200
    assert "spreadsheetml" in response["Content-Type"]
    assert "cass-portfolio-intake.xlsx" in response["Content-Disposition"]


def test_the_column_mapping_is_published_for_a_loading_api(api):
    response = api.get(f"{API}/portfolio-imports/profile/")
    assert response.status_code == 200
    assert response.data["profile_version"]
    columns = {item["name"]: item for item in response.data["sheets"]["Risks"]}
    assert columns["Building value"]["oed_field"] == "BuildingTIV"
    assert columns["Geocode precision"]["oed_field"] is None
    assert columns["Geocode precision"]["purpose"]
