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


# -- coverage values supplied per row, as OED actually works -----------------------

def template_for(batch, **kwargs) -> list[dict]:
    from apps.exposure.promotion import template_rows

    return template_rows(batch, **kwargs)


def completed(rows, values) -> bytes:
    """A completed template: identifiers from the platform, numbers from a user."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=["AccNumber", "LocNumber", "BuildingTIV", "OtherTIV", "ContentsTIV", "BITIV"],
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        key = (row["business_id"], row["location_number"])
        writer.writerow(
            {
                "AccNumber": row["business_id"],
                "LocNumber": row["location_number"],
                **values(key, row["allocated_tiv"]),
            }
        )
    return buffer.getvalue().encode("utf-8")


def test_the_template_lists_the_selection_with_its_allocated_totals(batch):
    rows = template_for(batch)
    payload = extract.component_template(rows)
    parsed = list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))

    assert {row["AccNumber"] for row in parsed} == {"B-SINGLE", "B-MULTI", "B-SHARED", "B-TWOPOL"}
    assert all(row["BuildingTIV"] == "" for row in parsed)
    single = next(row for row in parsed if row["AccNumber"] == "B-SINGLE")
    assert single["AllocatedTIV"] == "1000000.00"
    assert single["CountryCode"] == "ID"


def test_supplied_values_are_used_exactly_as_given(batch, analyst):
    """No single ratio describes a real schedule, so none is imposed."""
    rows = template_for(batch)

    def values(key, allocated):
        # Deliberately uneven, and different on every row.
        building = (allocated * Decimal("0.6")).quantize(Decimal("0.01"))
        contents = allocated - building
        return {"BuildingTIV": building, "OtherTIV": "", "ContentsTIV": contents, "BITIV": ""}

    supplied = extract.read_reported_components(completed(rows, values))
    version = promote(batch, name="Supplied", reported_components=supplied, actor=analyst)

    row = next(item for item in oed_rows(version) if item["AccNumber"] == "B-SINGLE")
    assert row["BuildingTIV"] == "600000.00"
    assert row["ContentsTIV"] == "400000.00"


def test_a_schedule_may_carry_a_different_shape_on_every_row(batch, analyst):
    """One site with contents and the next without is ordinary."""
    rows = template_for(batch)

    def values(key, allocated):
        if key[0] == "B-SINGLE":
            return {"BuildingTIV": allocated, "OtherTIV": "", "ContentsTIV": "", "BITIV": ""}
        half = (allocated / 2).quantize(Decimal("0.01"))
        return {
            "BuildingTIV": half,
            "OtherTIV": "",
            "ContentsTIV": allocated - half,
            "BITIV": "",
        }

    supplied = extract.read_reported_components(completed(rows, values))
    version = promote(batch, name="Mixed", reported_components=supplied, actor=analyst)

    single = next(item for item in oed_rows(version) if item["AccNumber"] == "B-SINGLE")
    other = next(item for item in oed_rows(version) if item["AccNumber"] == "B-SHARED")
    assert single["ContentsTIV"] == "0.00"
    assert Decimal(other["ContentsTIV"]) > 0


def test_supplied_values_are_recorded_as_reported_not_assumed(batch, analyst):
    rows = template_for(batch)
    supplied = extract.read_reported_components(
        completed(rows, lambda key, allocated: {"BuildingTIV": allocated})
    )
    version = promote(batch, name="Reported", reported_components=supplied, actor=analyst)

    coverage = version.source_lineage["coverage"]
    assert coverage["source"] == "reported_location_values"
    assert coverage["supplied"]["evidence"] == "reported"
    assert "supplied per location rather than derived" in coverage["basis"]


def test_a_supplied_total_that_disagrees_is_refused_with_both_numbers(batch, analyst):
    """Rescaling to fit would smooth away a real discrepancy."""
    rows = template_for(batch)
    supplied = extract.read_reported_components(
        completed(
            rows,
            lambda key, allocated: {"BuildingTIV": allocated + Decimal("1000.00")},
        )
    )
    with pytest.raises(PromotionError) as excinfo:
        promote(batch, name="Restated", reported_components=supplied, actor=analyst)

    message = str(excinfo.value)
    assert "restating the portfolio total is a decision" in message
    assert "5500000.00" in message  # what the allocation derived


def test_a_restated_total_can_be_accepted_deliberately(batch, analyst):
    """Reported location values outrank a derived split; a person decides."""
    rows = template_for(batch)
    supplied = extract.read_reported_components(
        completed(
            rows,
            lambda key, allocated: {"BuildingTIV": allocated + Decimal("1000.00")},
        )
    )
    version = promote(
        batch,
        name="Restated",
        reported_components=supplied,
        accept_restated_total=True,
        actor=analyst,
    )
    coverage = version.source_lineage["coverage"]
    assert coverage["restated_total_accepted"] is True
    assert coverage["reconciliation"]["restates_total"] is True
    # Six selected locations, each restated upward by 1,000.
    assert version.total_tiv == Decimal("5506000.00")


def test_a_missing_location_is_refused_rather_than_valued_at_nothing(batch, analyst):
    rows = template_for(batch)
    supplied = extract.read_reported_components(
        completed(rows[:-1], lambda key, allocated: {"BuildingTIV": allocated})
    )
    with pytest.raises(PromotionError, match="no supplied coverage values"):
        promote(batch, name="Incomplete", reported_components=supplied, actor=analyst)


def test_a_blank_cell_is_read_as_zero_and_counted(batch):
    payload = (
        b"AccNumber,LocNumber,BuildingTIV,ContentsTIV\n"
        b"B-SINGLE,1,1000.00,\n"
    )
    supplied = extract.read_reported_components(payload)
    assert supplied.blank_cells == 1
    assert supplied.apply(("B-SINGLE", 1))["ContentsTIV"] == Decimal("0.00")


def test_a_file_with_no_coverage_column_says_what_to_supply():
    with pytest.raises(extract.AllocationError, match="carries no coverage column"):
        extract.read_reported_components(b"AccNumber,LocNumber\nB-1,1\n")


def test_a_repeated_location_in_the_file_is_refused():
    payload = b"AccNumber,LocNumber,BuildingTIV\nB-1,1,100\nB-1,1,200\n"
    with pytest.raises(extract.AllocationError, match="repeats location"):
        extract.read_reported_components(payload)


def test_a_negative_coverage_value_is_refused():
    payload = b"AccNumber,LocNumber,BuildingTIV\nB-1,1,-100\n"
    with pytest.raises(extract.AllocationError, match="negative"):
        extract.read_reported_components(payload)


def test_thousands_separators_are_tolerated():
    payload = b"AccNumber,LocNumber,BuildingTIV\nB-1,1,\"1,234,567.89\"\n"
    supplied = extract.read_reported_components(payload)
    assert supplied.apply(("B-1", 1))["BuildingTIV"] == Decimal("1234567.89")


# -- through the API ------------------------------------------------------------------

def test_the_template_downloads_as_a_csv(api, batch):
    response = api.get(f"{API}/portfolio-imports/{batch.id}/coverage-template/")
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert "coverage-template" in response["Content-Disposition"]

    parsed = list(csv.DictReader(io.StringIO(response.content.decode("utf-8"))))
    assert len(parsed) == 6
    assert "BuildingTIV" in parsed[0]


def test_a_completed_template_can_be_posted_with_the_promotion(api, batch):
    from django.core.files.uploadedfile import SimpleUploadedFile

    rows = template_for(batch)
    payload = completed(rows, lambda key, allocated: {"BuildingTIV": allocated})

    response = api.post(
        f"{API}/portfolio-imports/{batch.id}/promote/",
        {
            "name": "From template",
            "coverage_file": SimpleUploadedFile("coverage.csv", payload, "text/csv"),
        },
        format="multipart",
    )
    assert response.status_code == 201, response.data
    assert response.data["coverage"]["source"] == "reported_location_values"


def test_a_bad_coverage_file_is_refused_with_a_reason(api, batch):
    from django.core.files.uploadedfile import SimpleUploadedFile

    response = api.post(
        f"{API}/portfolio-imports/{batch.id}/promote/",
        {
            "name": "Bad file",
            "coverage_file": SimpleUploadedFile(
                "coverage.csv", b"AccNumber,LocNumber\nB-1,1\n", "text/csv"
            ),
        },
        format="multipart",
    )
    assert response.status_code == 409
    assert "no coverage column" in response.data["detail"]
