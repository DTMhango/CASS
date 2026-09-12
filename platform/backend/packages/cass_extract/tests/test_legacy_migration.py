"""Carrying the pilot portfolio from the retired two-sheet workbook.

A migration is only trustworthy if it can be checked, and the thing worth
checking is that it did not quietly decide anything. The conversion makes one
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
from cass_extract.reader import read_workbook


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


def test_confidential_names_do_not_cross(converted):
    """Insured, cedent and broker have no destination in the template."""
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
