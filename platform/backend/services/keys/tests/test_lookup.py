"""The keys lookup contract.

These tests exist because section 15 names a specific failure: keys failures or
not-at-risk values being hidden, so exposure is silently omitted from loss. The
defence is arithmetic -- every unit of source value must land in exactly one
bucket -- so that is what is asserted here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cass_keys.lookup import (
    AreaPerilGrid,
    GridCell,
    KeyStatus,
    LookupError_,
    VulnerabilityEntry,
    VulnerabilityMapping,
    lookup,
)


def cell(area_peril_id: int, *, offshore: bool = False) -> GridCell:
    """A one-degree cell around Jakarta, offset by the identifier."""
    base_lat = Decimal("-7") + area_peril_id
    base_lon = Decimal("106") + area_peril_id
    return GridCell(
        area_peril_id=area_peril_id,
        min_latitude=base_lat,
        max_latitude=base_lat + 1,
        min_longitude=base_lon,
        max_longitude=base_lon + 1,
        country_code="ID",
        offshore=offshore,
    )


@pytest.fixture()
def grid() -> AreaPerilGrid:
    return AreaPerilGrid(
        country_code="ID",
        version="1.0.0",
        cells=(cell(0), cell(1), cell(2, offshore=True)),
    )


@pytest.fixture()
def vulnerability() -> VulnerabilityMapping:
    return VulnerabilityMapping(
        country_code="ID",
        version="2026.0.0",
        entries=(
            VulnerabilityEntry(101, coverage_type=1, required_imt="SA(0.3)",
                               occupancy_codes=frozenset({"1100"})),
            VulnerabilityEntry(102, coverage_type=3, required_imt="SA(0.3)",
                               occupancy_codes=frozenset({"1100"})),
            # A PGA-based function: present in the GEM set, not producible by
            # the SA-only converter prototype.
            VulnerabilityEntry(201, coverage_type=1, required_imt="PGA",
                               occupancy_codes=frozenset({"1200"})),
        ),
    )


def location(**overrides):
    row = {
        "LocNumber": "LOC-1",
        "Latitude": "-6.5",
        "Longitude": "106.5",
        "OccupancyCode": "1100",
        "ConstructionCode": "CR",
        "LocPerilsCovered": "QEQ",
        "BuildingTIV": "1000000",
        "OtherTIV": "0",
        "ContentsTIV": "200000",
        "BITIV": "0",
    }
    row.update(overrides)
    return row


# -- the completeness guarantee --------------------------------------------

def test_every_coverage_receives_a_response(grid, vulnerability):
    """Section 8: a missing response is an error, not an absence."""
    result = lookup([location()], grid=grid, vulnerability=vulnerability)
    # Four coverage types, one modelled sub-peril.
    assert len(result.records) == 4
    assert {record.coverage_type for record in result.records} == {1, 2, 3, 4}


def test_tiv_reconciles_to_the_source(grid, vulnerability):
    result = lookup([location()], grid=grid, vulnerability=vulnerability)
    report = result.report

    assert report.source_tiv == Decimal("1200000")
    assert report.accounted_tiv == report.source_tiv
    assert report.reconciled is True
    assert report.difference == 0


def test_mapped_not_at_risk_and_failed_are_reported_separately(grid, vulnerability):
    result = lookup([location()], grid=grid, vulnerability=vulnerability)
    report = result.report

    # Building and contents map; other structures and BI carry no value.
    assert report.mapped_tiv == Decimal("1200000")
    assert report.not_at_risk_tiv == Decimal(0)
    assert report.failed_tiv == Decimal(0)
    assert report.counts_by_status[str(KeyStatus.NOTATRISK)] == 2


def test_reconciliation_holds_when_everything_fails(grid, vulnerability):
    """A wholly unmappable portfolio still reconciles; it just maps nothing."""
    result = lookup(
        [location(Latitude="55.0", Longitude="-3.0")],
        grid=grid,
        vulnerability=vulnerability,
    )
    assert result.report.reconciled is True
    assert result.report.mapped_tiv == Decimal(0)
    assert result.report.failed_tiv == Decimal("1200000")
    assert result.report.mapped_share == 0.0


# -- statuses ---------------------------------------------------------------

def test_location_outside_the_grid_fails_on_area_peril(grid, vulnerability):
    result = lookup(
        [location(Latitude="55.0", Longitude="-3.0")],
        grid=grid,
        vulnerability=vulnerability,
    )
    record = next(r for r in result.records if r.coverage_type == 1)
    assert record.status is KeyStatus.FAIL_AP
    assert "outside the area-peril grid domain" in record.message


def test_offshore_cell_is_reported_rather_than_accepted(grid, vulnerability):
    """Section 6: offshore and border cases are reported, not snapped."""
    result = lookup(
        [location(Latitude="-4.5", Longitude="108.5")],
        grid=grid,
        vulnerability=vulnerability,
    )
    record = next(r for r in result.records if r.coverage_type == 1)
    assert record.status is KeyStatus.FAIL_AP
    assert "offshore" in record.message
    # The cell is still named, so an analyst can see where it landed.
    assert record.area_peril_id == 2


def test_missing_coordinates_fail_rather_than_default(grid, vulnerability):
    result = lookup(
        [location(Latitude="", Longitude="")], grid=grid, vulnerability=vulnerability
    )
    record = next(r for r in result.records if r.coverage_type == 1)
    assert record.status is KeyStatus.FAIL_AP
    assert "no usable coordinates" in record.message


def test_unmapped_taxonomy_fails_on_vulnerability(grid, vulnerability):
    result = lookup(
        [location(OccupancyCode="9999")], grid=grid, vulnerability=vulnerability
    )
    record = next(r for r in result.records if r.coverage_type == 1)
    assert record.status is KeyStatus.FAIL_V
    assert "No vulnerability function" in record.message


def test_a_pga_function_is_refused_by_the_sa_only_release(grid, vulnerability):
    """Section 6: the SA-only prototype must report the gap, not hide it."""
    result = lookup(
        [location(OccupancyCode="1200")], grid=grid, vulnerability=vulnerability
    )
    record = next(r for r in result.records if r.coverage_type == 1)
    assert record.status is KeyStatus.FAIL_V
    assert "requires PGA" in record.message
    assert record.vulnerability_id == 201
    assert record.imt == "PGA"


def test_uncovered_subperil_is_not_at_risk_rather_than_failed(grid, vulnerability):
    result = lookup(
        [location(LocPerilsCovered="WW1")], grid=grid, vulnerability=vulnerability
    )
    record = next(r for r in result.records if r.coverage_type == 1)
    assert record.status is KeyStatus.NOTATRISK
    assert "not covered for QEQ" in record.message


def test_zero_value_coverage_is_not_at_risk(grid, vulnerability):
    result = lookup([location()], grid=grid, vulnerability=vulnerability)
    record = next(r for r in result.records if r.coverage_type == 2)
    assert record.status is KeyStatus.NOTATRISK
    assert "no insured value" in record.message


def test_location_without_an_identifier_fails(grid, vulnerability):
    result = lookup(
        [location(LocNumber="")], grid=grid, vulnerability=vulnerability
    )
    assert all(record.status is KeyStatus.FAIL for record in result.records)


# -- outputs ----------------------------------------------------------------

def test_successful_record_names_its_route(grid, vulnerability):
    result = lookup([location()], grid=grid, vulnerability=vulnerability)
    record = next(r for r in result.records if r.coverage_type == 1)

    assert record.status is KeyStatus.SUCCESS
    assert record.area_peril_id == 0
    assert record.vulnerability_id == 101
    assert record.imt == "SA(0.3)"
    assert record.message == ""


def test_keys_row_matches_the_oasis_column_contract(grid, vulnerability):
    result = lookup([location()], grid=grid, vulnerability=vulnerability)
    row = next(r for r in result.records if r.coverage_type == 1).as_row()

    assert set(row) == {
        "LocID",
        "AccNumber",
        "LocNumber",
        "PerilID",
        "CoverageTypeID",
        "AreaPerilID",
        "VulnerabilityID",
        "ChannelWeight",
        "Status",
        "Message",
    }
    assert row["Status"] == "success"


def test_two_accounts_may_each_schedule_a_location_one(grid, vulnerability):
    """OED makes a location number unique within an account, not a portfolio.

    A promoted Klapton Re cohort is the case that proves it: four businesses,
    each with a location 1, at four different coordinates. Keyed on the number
    alone, three of them would receive another's area peril and the mapped
    count would report four locations as one.
    """
    result = lookup(
        [
            location(AccNumber="B-ONE", LocNumber="1"),
            # Same coordinates, which the real extract also carries: two
            # businesses can occupy one building, and the pair still has to
            # count as two locations.
            location(AccNumber="B-TWO", LocNumber="1"),
        ],
        grid=grid,
        vulnerability=vulnerability,
    )
    assert {record.location_id for record in result.successes} == {"B-ONE/1", "B-TWO/1"}


def test_the_keys_row_carries_the_account_and_number_it_was_built_from(grid, vulnerability):
    """The composite is for counting; the errors file still has to be readable."""
    result = lookup(
        [location(AccNumber="B-ONE", LocNumber="7")], grid=grid, vulnerability=vulnerability
    )
    row = next(r for r in result.records if r.coverage_type == 1).as_row()
    assert row["LocID"] == "B-ONE/7"
    assert row["AccNumber"] == "B-ONE"
    assert row["LocNumber"] == "7"


def test_a_location_with_no_account_keeps_its_bare_number(grid, vulnerability):
    """A single-account file needs no qualification and should not gain one."""
    result = lookup([location()], grid=grid, vulnerability=vulnerability)
    assert {record.location_id for record in result.successes} == {"LOC-1"}


def test_failures_are_separable_for_the_errors_file(grid, vulnerability):
    result = lookup(
        [location(), location(LocNumber="LOC-2", Latitude="55.0", Longitude="-3.0")],
        grid=grid,
        vulnerability=vulnerability,
    )
    assert {record.location_id for record in result.failures} == {"LOC-2"}
    assert {record.location_id for record in result.successes} == {"LOC-1"}


def test_report_groups_unmapped_value_by_reason(grid, vulnerability):
    result = lookup(
        [location(Latitude="55.0", Longitude="-3.0")],
        grid=grid,
        vulnerability=vulnerability,
    )
    reasons = result.report.as_dict()["tiv_by_reason"]
    assert any("outside the area-peril grid domain" in reason for reason in reasons)


def test_result_records_the_grid_and_vulnerability_versions(grid, vulnerability):
    """Lineage: a key is only meaningful against the version that produced it."""
    result = lookup([location()], grid=grid, vulnerability=vulnerability)
    assert result.grid_reference == "id-grid-1.0.0"
    assert result.vulnerability_reference == "id-vuln-2026.0.0"


def test_lookup_requires_at_least_one_modelled_subperil(grid, vulnerability):
    with pytest.raises(LookupError_):
        lookup([location()], grid=grid, vulnerability=vulnerability, modelled_subperils=[])


def test_more_specific_taxonomy_mapping_wins():
    """A construction-specific entry beats an occupancy-only one."""
    mapping = VulnerabilityMapping(
        country_code="ID",
        version="1",
        entries=(
            VulnerabilityEntry(1, coverage_type=1, required_imt="SA(0.3)",
                               occupancy_codes=frozenset({"1100"})),
            VulnerabilityEntry(2, coverage_type=1, required_imt="SA(0.3)",
                               occupancy_codes=frozenset({"1100"}),
                               construction_codes=frozenset({"CR"})),
        ),
    )
    assert mapping.find_channels("1100", "CR", 1)[0].vulnerability_id == 2
    assert mapping.find_channels("1100", "MUR", 1)[0].vulnerability_id == 1
