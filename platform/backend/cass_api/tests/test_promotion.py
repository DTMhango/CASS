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
from apps.exposure.extract import import_extract, location_identity
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

from fixtures import as_workbook, structural_extract  # noqa: E402

pytestmark = pytest.mark.django_db


@pytest.fixture()
def batch(project, analyst):
    policies, locations = structural_extract()
    payload = as_workbook(policies, locations).getvalue()
    return import_extract(project, payload, filename="extract.xlsx", actor=analyst)


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


# -- occupancy is not invented -------------------------------------------------------

def test_the_unreported_occupancy_is_written_as_oed_unknown(version):
    """Deriving one from class of business would be enrichment, not mapping."""
    assert {row["OccupancyCode"] for row in oed_rows(version)} == {UNKNOWN_OCCUPANCY}


def test_the_lineage_says_which_attributes_were_not_reported(version):
    absent = version.source_lineage["attributes_not_reported"]
    assert "OccupancyCode" in absent
    assert "ConstructionCode" in absent
    assert "not inferred" in absent["ConstructionCode"]


# -- the coverage split is a choice ----------------------------------------------------

def test_the_default_split_is_the_conservative_one(version):
    """A default that spread value would assert an unapproved prior."""
    assert version.source_lineage["coverage_split"]["name"] == "building_only_technical_v1"
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
    assert result.source_lineage["coverage_split"]["reconciliation"]["reconciles"] is True


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
    assert summary["coverage_split"]["name"] == "building_only_technical_v1"
    assert summary["decision_use"].startswith("Blocked")
    assert "OccupancyCode" in summary["attributes_not_reported"]


def test_the_promotion_is_audited(version, batch):
    event = AuditEvent.objects.filter(
        subject_type="exposure_version",
        subject_id=version.id,
        action=AuditAction.PUBLISH,
    ).first()
    assert event.after_reference["import_batch"] == str(batch.id)
    assert event.after_reference["coverage_split"] == "building_only_technical_v1"


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
    assert response.data["coverage_split"]["name"] == "building_contents_test_v1"
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
    assert response.data["coverage_split"]["percentages"]["BuildingTIV"] == 70.0


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
