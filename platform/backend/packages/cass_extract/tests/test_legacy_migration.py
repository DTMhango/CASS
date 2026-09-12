"""Carrying the pilot portfolio from the retired two-sheet workbook.

Two things are proved here, and they belong together because neither is worth
much on its own.

The first is that the workbook can still be read. CASS does not accept the
two-sheet extract any more, but the book exists in that shape and the reader
that understands it has to keep working until the conversion has run -- so its
tests moved here with it rather than being deleted alongside the live path.

The second is that the conversion did not quietly decide anything. It makes one
judgement -- a one-site business's value sits at that site -- and these tests
hold it to that, particularly the part where it declines to make the second
judgement it could easily have made.
"""

from __future__ import annotations

import io
from decimal import Decimal

import pytest
from fixtures import as_workbook, structural_extract

from cass_extract import intake, legacy
from cass_extract.legacy_reader import read_workbook
from cass_extract.legacy_schema import (
    LEGACY_PARSER_VERSION,
    LEGACY_PROFILE_NAME,
    LEGACY_SCHEMA_VERSION,
    LOCATION_SHEET,
    POLICY_SHEET,
    country_code,
    is_not_applicable,
)
from cass_extract.records import ExtractReadError


@pytest.fixture()
def extract():
    policies, locations = structural_extract()
    return read_workbook(as_workbook(policies, locations))


@pytest.fixture()
def converted():
    policies, locations = structural_extract()
    payload = as_workbook(policies, locations).getvalue()
    return legacy.convert(read_workbook(io.BytesIO(payload)))


@pytest.fixture()
def migrated():
    policies, locations = structural_extract()
    payload = as_workbook(policies, locations).getvalue()
    template, result = legacy.migrate(io.BytesIO(payload), project_reference="demo")
    return intake.read_workbook(io.BytesIO(template)), result


# -- what the conversion decides, and what it refuses to ---------------------------

def test_a_one_site_business_carries_its_whole_value_at_that_site(converted):
    """Arithmetic, not an assumption, so it is written."""
    row = next(item for item in converted.risks if item["Account reference"] == "B-SINGLE")
    assert row["Total insured value"] == "1000000.00"


def test_a_multi_site_business_is_written_blank(converted):
    """Freezing an equal split into the file would make an assumption read as data."""
    rows = [item for item in converted.risks if item["Account reference"] == "B-MULTI"]
    assert len(rows) == 3
    assert {item["Total insured value"] for item in rows} == {""}


def test_a_policy_with_no_scheduled_site_produces_no_risk_row(converted):
    """B-NOLOC is most of the real book: value with nowhere to sit."""
    assert not [
        item for item in converted.risks if item["Account reference"] == "B-NOLOC"
    ]
    assert Decimal(converted.as_dict()["value_on_accounts_with_no_risks"]) == Decimal(
        "800000.00"
    )


def test_the_policy_total_carries_what_the_risks_do_not(converted):
    policies = [item for item in converted.policies if item["Account reference"] == "B-MULTI"]
    assert policies
    assert sum(Decimal(item["Total insured value"]) for item in policies) > 0


def test_several_policies_on_one_site_are_summed_at_it(converted):
    """B-TWOPOL holds two policies over a single location."""
    row = next(item for item in converted.risks if item["Account reference"] == "B-TWOPOL")
    assert Decimal(row["Total insured value"]) == Decimal("1250000.00")


def test_no_coverage_column_is_filled(converted):
    """The component split is a separate assumption and stays one."""
    for row in converted.risks:
        assert "Building value" not in row or not row.get("Building value")


def test_the_conversion_reports_what_it_left_for_the_allocation(converted):
    """B-MULTI holds three sites and B-PARTIAL two, so five rows defer."""
    report = converted.as_dict()
    assert report["multi_site_businesses"] == 2
    assert report["risks_awaiting_allocation"] == 5
    assert Decimal(report["value_left_to_allocate"]) == Decimal("5000000.00")


def test_value_awaiting_allocation_is_separate_from_value_with_no_risk(converted):
    """Adding them together would make the first look enormous."""
    report = converted.as_dict()
    total = (
        Decimal(report["value_stated_per_risk"])
        + Decimal(report["value_left_to_allocate"])
        + Decimal(report["value_on_accounts_with_no_risks"])
    )
    assert total == Decimal(report["source_tiv"])


def test_the_commercial_columns_have_no_destination(converted):
    """Premium, loss ratio and the counterparty names are not exposure.

    Not withheld -- nobody at Klapton Re is kept from them -- but the intake
    template describes what is at risk and where, and a column with no OED
    field behind it would be carried for no reader.
    """
    text = str(converted.risks) + str(converted.policies)
    assert "Example Insured" not in text
    assert "Example Broker" not in text


def test_an_unreadable_legacy_workbook_is_refused():
    """A conversion that guessed at a missing identifier would be worse than none."""
    policies, locations = structural_extract()
    for row in locations:
        row.pop("location_number")
    payload = as_workbook(policies, locations).getvalue()

    with pytest.raises(legacy.MigrationError, match="missing required columns"):
        legacy.convert(read_workbook(io.BytesIO(payload)))


# -- and the file it produces is a valid template ------------------------------------

def test_the_migrated_file_reads_as_a_cass_template(migrated):
    read, _ = migrated
    assert read.is_readable is True
    assert read.profile_version
    assert len(read.risks.rows) == 11


def test_the_migrated_file_carries_the_eligibility_fields(migrated):
    """Precision, review and class survive, or the cohorts stop working."""
    read, _ = migrated
    row = read.risks.rows[0]
    assert row.get("Geocode precision")
    assert row.get("Class of business")
    assert row.get("Needs review") in (True, False)


def test_the_migrated_multi_site_rows_defer_to_the_allocation(migrated):
    read, _ = migrated
    deferred = {row.get("Account reference") for row in read.deferred()}
    assert deferred == {"B-MULTI", "B-PARTIAL"}


def test_the_migrated_file_needs_no_allocation_it_cannot_perform(migrated):
    """Every deferred risk has a policy total to divide."""
    read, _ = migrated
    assert [item for item in read.findings if item.code == "no_value_to_allocate"] == []


def test_the_country_is_translated_to_its_iso_code(migrated):
    read, _ = migrated
    assert {row.get("Country") for row in read.risks.rows} <= {"ID", "NP"}


def test_the_primary_site_flag_survives_the_migration(converted):
    """Without it the primary-concentrated sensitivity silently stops working.

    It matters only for the multi-site accounts, which are exactly the rows the
    migration leaves blank -- so a conversion that dropped it would break the
    one scenario those rows exist to be tested under, and break it quietly.
    """
    rows = [item for item in converted.risks if item["Account reference"] == "B-MULTI"]
    assert [item["Primary site"] for item in rows] == ["Yes", "No", "No"]


# -- reading the retired workbook --------------------------------------------------

def test_both_sheets_are_read(extract):
    assert len(extract.policies) == 10
    assert len(extract.locations) == 11
    assert extract.is_readable is True


def test_a_clean_extract_raises_no_findings(extract):
    """Findings are for problems. A clean read must be silent."""
    assert extract.findings == []


def test_money_is_read_as_decimal_not_float(extract):
    """The exact reconciliation the brief requires cannot survive a float."""
    value = extract.policies.rows[0].values["gross_limit"]
    assert isinstance(value, Decimal)
    assert value == Decimal("1000000.00")


def test_the_source_text_is_kept_beside_the_typed_value(extract):
    """No transformation silently replaces a reported field."""
    row = extract.policies.rows[0]
    assert row.raw["gross_limit"] == "1000000.00"
    assert row.values["gross_limit"] == Decimal("1000000.00")


def test_a_not_applicable_marker_is_absence_rather_than_a_bad_number(extract):
    """The renewal columns carry an em dash on every unrenewed policy.

    Reading those as unreadable numbers raised 4,020 findings against the real
    extract and buried everything that mattered.
    """
    policies = [row.values for row in extract.policies]
    unrenewed = next(item for item in policies if item["renewed"] == "N")
    assert unrenewed["renewal_premium"] is None
    assert unrenewed["premium_growth_pct"] is None


@pytest.mark.parametrize(
    "marker", ["—", "–", "-", "N/A", "na", "none", " — "]
)
def test_the_recognised_not_applicable_markers(marker):
    assert is_not_applicable(marker) is True


@pytest.mark.parametrize("value", ["0", "medium", "12.5", "Yes"])
def test_a_real_value_is_not_mistaken_for_a_marker(value):
    assert is_not_applicable(value) is False


def test_a_required_field_marked_not_applicable_is_still_missing():
    """There, "not applicable" is itself the problem."""
    policies, locations = structural_extract()
    locations[0]["latitude"] = "—"
    read = read_workbook(as_workbook(policies, locations))
    codes = {(f.field, f.code) for f in read.locations.findings}
    assert ("latitude", "missing_value") in codes


def test_an_unreadable_coordinate_is_reported_against_its_spreadsheet_row():
    policies, locations = structural_extract()
    locations[1]["latitude"] = "north"
    read = read_workbook(as_workbook(policies, locations))

    finding = next(f for f in read.locations.findings if f.code == "unreadable_value")
    # Row 1 is the header, so the second location row is spreadsheet row 3.
    assert finding.row_number == 3
    assert finding.field == "latitude"


def test_a_coordinate_outside_its_range_is_refused():
    policies, locations = structural_extract()
    locations[0]["latitude"] = "120.0"
    read = read_workbook(as_workbook(policies, locations))
    assert any("outside the valid range" in f.message for f in read.locations.findings)


def test_a_missing_required_column_makes_the_extract_unreadable():
    policies, locations = structural_extract()
    for row in locations:
        row.pop("latitude")
    read = read_workbook(as_workbook(policies, locations))
    assert read.is_readable is False
    assert "latitude" in read.locations.missing_columns


def test_an_extra_column_is_noted_but_does_not_refuse_the_extract():
    """A source system adding a field it did not have before is normal."""
    policies, locations = structural_extract()
    for row in locations:
        row["new_provider_score"] = "0.9"
    read = read_workbook(as_workbook(policies, locations))
    assert read.is_readable is True
    assert "new_provider_score" in read.locations.unrecognised_columns


def test_a_workbook_without_the_expected_sheets_is_refused():
    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.active.title = "Sheet1"
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    with pytest.raises(ExtractReadError, match="Premium Policies"):
        read_workbook(buffer)


def test_something_that_is_not_a_workbook_is_refused():
    with pytest.raises(ExtractReadError, match="could not be opened"):
        read_workbook(io.BytesIO(b"policy_id,business_id\n1,2\n"))


def test_reading_the_same_extract_twice_gives_the_same_answer():
    """Import is deterministic, which is what makes a rerun evidence."""
    policies, locations = structural_extract()
    first = read_workbook(as_workbook(policies, locations))
    second = read_workbook(as_workbook(policies, locations))

    assert [r.values for r in first.locations] == [r.values for r in second.locations]
    assert [r.values for r in first.policies] == [r.values for r in second.policies]


def test_the_retired_format_states_its_own_versions():
    """Recorded with the migration, so a reread by a later build is distinguishable."""
    assert LEGACY_SCHEMA_VERSION.startswith("klapton-re-geocoded-policy-extract/")
    assert LEGACY_PROFILE_NAME == "Klapton Re geocoded policy extract"
    assert LEGACY_PARSER_VERSION


def test_the_sheet_names_are_the_ones_the_retired_source_used():
    assert POLICY_SHEET == "Premium Policies"
    assert LOCATION_SHEET == "Risk Locations"


# -- country names, which only the retired format needed translating ---------------

@pytest.mark.parametrize(("name", "code"), [("Indonesia", "ID"), ("nepal", "NP")])
def test_the_pilot_countries_map_to_iso_codes(name, code):
    """The intake template asks for the code. Only the old workbook wrote a name."""
    assert country_code(name) == code


def test_an_unrecognised_country_is_not_guessed():
    """A wrong code would route a location to the wrong national grid."""
    assert country_code("Indoneesia") == ""
    assert country_code("") == ""
