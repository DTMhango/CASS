"""Validation behaviour against the PiWind baseline and focused cases."""

from __future__ import annotations

from decimal import Decimal

import pytest

from cass_oed.perspectives import (
    Perspective,
    UnsupportedPerspective,
    available_perspectives,
    highest_available,
    require,
)
from cass_oed.reader import read_bytes
from cass_oed.schema import FileKind, expand_perils, unmodelled_subperils
from cass_oed.validation import PortfolioFiles, validate

# -- the official PiWind baseline ------------------------------------------

def test_piwind_portfolio_is_read_completely(piwind_files):
    assert len(piwind_files.location) == 10
    assert len(piwind_files.account) == 2
    assert len(piwind_files.reins_info) == 1
    assert len(piwind_files.reins_scope) == 2


def test_piwind_tiv_reconciles_to_the_source_file(piwind_files):
    report = validate(piwind_files)
    expected = sum(
        Decimal(row.get("BuildingTIV") or 0)
        + Decimal(row.get("OtherTIV") or 0)
        + Decimal(row.get("ContentsTIV") or 0)
        + Decimal(row.get("BITIV") or 0)
        for row in piwind_files.location.rows
    )
    assert report.total_tiv == expected
    assert report.tiv_by_currency == {"GBP": expected}
    assert report.tiv_by_country == {"GB": expected}


def test_piwind_has_no_blocking_structural_errors(piwind_files):
    """A wind portfolio is structurally valid; it simply models no earthquake."""
    report = validate(piwind_files)
    blocking = {finding.code for finding in report.findings.errors}
    assert blocking == set(), f"unexpected blocking findings: {blocking}"


def test_piwind_is_reported_as_carrying_no_modelled_earthquake_peril(piwind_files):
    report = validate(piwind_files)
    codes = report.findings.by_code()
    assert codes["no_modelled_peril"] == 10
    assert report.modelled_subperils == ()
    assert report.publishable is True


def test_piwind_supports_every_perspective(piwind_files):
    availability = {item.perspective: item.available for item in available_perspectives(piwind_files)}
    assert availability == {
        Perspective.GROUND_UP: True,
        Perspective.INSURED: True,
        Perspective.REINSURANCE: True,
    }
    assert highest_available(piwind_files) is Perspective.REINSURANCE


# -- field and record rules -------------------------------------------------

def test_required_value_is_reported_against_its_record(make_location):
    files = make_location("1,A1,L1,1,ID,,106.8,1050,CR,QEQ,100000,0,IDR")
    report = validate(files)
    finding = next(f for f in report.findings if f.code == "missing_value")
    assert finding.field == "Latitude"
    assert finding.row_number == 2
    assert "LocNumber=L1" in finding.record_key
    assert finding.remediation


def test_out_of_range_coordinate_is_blocking(make_location):
    files = make_location("1,A1,L1,1,ID,95.5,106.8,1050,CR,QEQ,100000,0,IDR")
    report = validate(files)
    finding = next(f for f in report.findings if f.code == "out_of_range")
    assert finding.field == "Latitude"
    assert report.publishable is False


def test_null_island_is_treated_as_a_failed_geocode(make_location):
    files = make_location("1,A1,L1,1,ID,0,0,1050,CR,QEQ,100000,0,IDR")
    report = validate(files)
    assert any(f.code == "zero_coordinates" for f in report.findings)


def test_location_without_value_cannot_produce_loss(make_location):
    files = make_location("1,A1,L1,1,ID,-6.2,106.8,1050,CR,QEQ,0,0,IDR")
    report = validate(files)
    assert any(f.code == "no_tiv" for f in report.findings)
    assert report.publishable is False


def test_negative_value_is_rejected(make_location):
    files = make_location("1,A1,L1,1,ID,-6.2,106.8,1050,CR,QEQ,-5,100,IDR")
    report = validate(files)
    assert any(f.code == "negative_tiv" for f in report.findings)


def test_non_numeric_value_is_reported_not_raised(make_location):
    files = make_location("1,A1,L1,1,ID,-6.2,106.8,1050,CR,QEQ,1 000 000,0,IDR")
    report = validate(files)
    finding = next(f for f in report.findings if f.code == "invalid_number")
    assert finding.field == "BuildingTIV"


def test_duplicate_building_at_one_location_is_rejected(make_location):
    files = make_location(
        "1,A1,L1,1,ID,-6.2,106.8,1050,CR,QEQ,100,0,IDR",
        "1,A1,L1,1,ID,-6.2,106.8,1050,CR,QEQ,200,0,IDR",
    )
    report = validate(files)
    assert any(f.code == "building_id_reused" for f in report.findings)


def test_distinct_buildings_at_one_location_are_allowed(make_location):
    files = make_location(
        "1,A1,L1,1,ID,-6.2,106.8,1050,CR,QEQ,100,0,IDR",
        "1,A1,L1,2,ID,-6.2,106.8,1050,CR,QEQ,200,0,IDR",
    )
    report = validate(files)
    assert not any(f.code == "building_id_reused" for f in report.findings)
    assert report.total_tiv == Decimal(300)


def test_unrecognised_column_is_surfaced_rather_than_dropped(make_location):
    header = (
        "PortNumber,AccNumber,LocNumber,BuildingID,CountryCode,Latitude,Longitude,"
        "OccupancyCode,ConstructionCode,LocPerilsCovered,BuildingTIV,ContentsTIV,"
        "LocCurrency,CedantRiskScore"
    )
    files = make_location("1,A1,L1,1,ID,-6.2,106.8,1050,CR,QEQ,100,0,IDR,7", header=header)
    report = validate(files)
    finding = next(f for f in report.findings if f.code == "unrecognised_column")
    assert finding.field == "CedantRiskScore"
    assert finding.severity == "warning"


def test_mixed_currency_is_flagged_before_oasis_generation(make_location):
    """Section 15: the Oasis FM does not calculate multi-currency terms."""
    files = make_location(
        "1,A1,L1,1,ID,-6.2,106.8,1050,CR,QEQ,100,0,IDR",
        "1,A1,L2,1,ID,-6.3,106.9,1050,CR,QEQ,100,0,USD",
    )
    report = validate(files)
    assert any(f.code == "mixed_currency" for f in report.findings)
    assert set(report.currencies) == {"IDR", "USD"}


def test_unsupported_financial_term_blocks_publication():
    header = (
        "PortNumber,AccNumber,LocNumber,BuildingID,CountryCode,Latitude,Longitude,"
        "OccupancyCode,LocPerilsCovered,BuildingTIV,LocCurrency,CondTag"
    )
    result = read_bytes(
        FileKind.LOCATION,
        (header + "\n1,A1,L1,1,ID,-6.2,106.8,1050,QEQ,100,IDR,SPECIAL\n").encode(),
    )
    report = validate(PortfolioFiles(location=result))
    finding = next(f for f in report.findings if f.code == "unsupported_financial_term")
    assert finding.field == "CondTag"
    assert report.publishable is False


# -- peril scope ------------------------------------------------------------

def test_peril_group_expands_to_subperils():
    """OED's own group codes: QQ1 for every earthquake peril, AA1 for every peril."""
    assert expand_perils("QQ1") == ("QEQ", "QFF", "QTS", "QSL", "QLS", "QLF")
    assert expand_perils("AA1") == ("QEQ", "QFF", "QTS", "QSL", "QLS", "QLF")
    assert expand_perils("QEQ;QTS") == ("QEQ", "QTS")
    assert expand_perils("") == ()


def test_a_code_oed_does_not_define_is_not_expanded():
    """CASS wrote QQ before this was checked, and OED has no such code.

    Read as a group it would quietly cover shake; left alone it reaches the
    finding that says no sub-peril on the location is modelled.
    """
    assert expand_perils("QQ") == ("QQ",)


def test_unmodelled_subperils_are_named():
    assert unmodelled_subperils(expand_perils("QQ1")) == ("QFF", "QTS", "QSL", "QLS", "QLF")
    assert unmodelled_subperils(expand_perils("QEQ")) == ()


def test_covered_but_unmodelled_subperil_is_disclosed(make_location):
    """Section 9: output may not be labelled earthquake loss silently."""
    files = make_location("1,A1,L1,1,ID,-6.2,106.8,1050,CR,QQ1,100,0,IDR")
    report = validate(files)
    assert any(f.code == "unmodelled_subperil" for f in report.findings)
    assert set(report.unmodelled_subperils) == {"QFF", "QTS", "QSL", "QLS", "QLF"}
    assert report.modelled_subperils == ("QEQ",)


def test_a_book_covered_for_the_whole_earthquake_group_is_covered_for_shake(make_location):
    """A schedule written the way OED writes it must not read as covering nothing."""
    files = make_location("1,A1,L1,1,ID,-6.2,106.8,1050,CR,QQ1,100,0,IDR")
    report = validate(files)
    assert not any(f.code == "no_modelled_peril" for f in report.findings)


# -- hierarchy and reinsurance ---------------------------------------------

def test_location_referencing_an_absent_account_is_an_orphan():
    location = read_bytes(
        FileKind.LOCATION,
        (
            b"PortNumber,AccNumber,LocNumber,BuildingID,CountryCode,Latitude,Longitude,"
            b"OccupancyCode,LocPerilsCovered,BuildingTIV,LocCurrency\n"
            b"1,A9,L1,1,ID,-6.2,106.8,1050,QEQ,100,IDR\n"
        ),
    )
    account = read_bytes(
        FileKind.ACCOUNT,
        (
            b"PortNumber,AccNumber,AccCurrency,PolNumber,PolPerilsCovered,LayerNumber\n"
            b"1,A1,IDR,P1,QEQ,1\n"
        ),
    )
    report = validate(PortfolioFiles(location=location, account=account))
    finding = next(f for f in report.findings if f.code == "orphan_reference")
    assert finding.value == "A9"


def test_layer_gap_is_reported_as_a_warning():
    location = read_bytes(
        FileKind.LOCATION,
        (
            b"PortNumber,AccNumber,LocNumber,BuildingID,CountryCode,Latitude,Longitude,"
            b"OccupancyCode,LocPerilsCovered,BuildingTIV,LocCurrency\n"
            b"1,A1,L1,1,ID,-6.2,106.8,1050,QEQ,100,IDR\n"
        ),
    )
    account = read_bytes(
        FileKind.ACCOUNT,
        (
            b"PortNumber,AccNumber,AccCurrency,PolNumber,PolPerilsCovered,LayerNumber\n"
            b"1,A1,IDR,P1,QEQ,1\n"
            b"1,A1,IDR,P1,QEQ,3\n"
        ),
    )
    report = validate(PortfolioFiles(location=location, account=account))
    assert any(f.code == "layer_gap" for f in report.findings)


TERM_HEADER = (
    "PortNumber,AccNumber,LocNumber,BuildingID,CountryCode,Latitude,Longitude,"
    "OccupancyCode,ConstructionCode,LocPerilsCovered,BuildingTIV,ContentsTIV,"
    "LocCurrency,LocDed6All,LocDedType6All,LocPeril"
)


def test_a_deductible_with_no_basis_is_reported_here_not_inside_the_engine(make_location):
    """OED requires the basis beside the amount, and the engine refuses without it.

    Left to the engine, a deductible with no basis stops the run several stages
    later, in a file reader's traceback, long after the person who could fix it
    has moved on.
    """
    files = make_location(
        "1,A1,L1,1,ID,-6.2,106.8,1050,5100,QEQ,100000,0,USD,5000,,",
        header=TERM_HEADER,
    )

    report = validate(files)

    missing = [f for f in report.findings if f.code == "missing_term_basis"]
    assert {finding.field for finding in missing} == {"LocDedType6All", "LocPeril"}


def test_a_zero_deductible_needs_no_basis(make_location):
    """Nothing is a term here, and demanding a basis would flag every row."""
    files = make_location(
        "1,A1,L1,1,ID,-6.2,106.8,1050,5100,QEQ,100000,0,USD,0,,",
        header=TERM_HEADER,
    )

    report = validate(files)

    assert not [f for f in report.findings if f.code == "missing_term_basis"]


def test_a_percentage_deductible_basis_is_refused_rather_than_treated_as_flat(
    make_location,
):
    files = make_location(
        "1,A1,L1,1,ID,-6.2,106.8,1050,5100,QEQ,100000,0,USD,5000,1,QEQ",
        header=TERM_HEADER,
    )

    report = validate(files)

    assert any(
        finding.code == "invalid_code" and finding.field == "LocDedType6All"
        for finding in report.findings
    )


def test_a_contract_reference_that_is_not_a_number_is_reported_here(piwind_files):
    """OED numbers contracts, and the engine parses that column as a number.

    A book whose contracts were called "RE001" validated clean and then failed
    inside the engine's own file reader, which is the kind of discovery this
    validator exists to make first.
    """
    piwind_files.reins_scope = read_bytes(
        FileKind.REINS_SCOPE,
        (
            b"ReinsNumber,PortNumber,AccNumber,CededPercent\n"
            b"RE001,1,A11111,0.1\n"
        ),
    )

    report = validate(piwind_files)

    assert any(
        finding.code == "invalid_number" and finding.field == "ReinsNumber"
        for finding in report.findings
    )


def test_reinsurance_scope_matching_nothing_is_blocking(piwind_files):
    piwind_files.reins_scope = read_bytes(
        FileKind.REINS_SCOPE,
        (
            b"ReinsNumber,PortNumber,AccNumber,PolNumber,LocGroup,LocNumber,CededPercent\n"
            b"1,1,A11111,,,NOT-A-LOCATION,0.1\n"
        ),
    )
    report = validate(piwind_files)
    assert any(f.code == "scope_matches_nothing" for f in report.findings)
    assert report.publishable is False


# -- perspectives -----------------------------------------------------------

def test_location_only_portfolio_supports_ground_up_alone(make_location):
    files = make_location("1,A1,L1,1,ID,-6.2,106.8,1050,CR,QEQ,100,0,IDR")
    availability = {item.perspective: item for item in available_perspectives(files)}
    assert availability[Perspective.GROUND_UP].available
    assert not availability[Perspective.INSURED].available
    assert "account file is required" in availability[Perspective.INSURED].reason
    assert highest_available(files) is Perspective.GROUND_UP


def test_requesting_an_unsupported_perspective_raises(make_location):
    files = make_location("1,A1,L1,1,ID,-6.2,106.8,1050,CR,QEQ,100,0,IDR")
    with pytest.raises(UnsupportedPerspective) as excinfo:
        require(files, [Perspective.GROUND_UP, Perspective.REINSURANCE])
    assert "Loss net of reinsurance" in str(excinfo.value)


def test_contracts_without_scope_do_not_enable_reinsurance(piwind_files):
    piwind_files.reins_scope = None
    availability = {item.perspective: item for item in available_perspectives(piwind_files)}
    assert not availability[Perspective.REINSURANCE].available
    assert "scope" in availability[Perspective.REINSURANCE].reason


def test_report_serialises_for_the_api(piwind_files):
    body = validate(piwind_files).as_dict()
    assert body["location_count"] == 10
    assert body["publishable"] is True
    assert "counts_by_code" in body["validation"]
    assert isinstance(body["total_tiv"], str)
