"""The Klapton Re geocoded extract: parsing, cohorts and the join report.

Section 8 of the integration brief lists what these tests have to prove:
deterministic import, no join fan-out, no coordinate-based deduplication, no
policy value duplicated across locations, and confidential columns staying out
of anything that gets logged. The fixtures are synthetic on purpose -- the real
workbook is confidential working data and must not be committed -- but they
carry every structural shape the real one has.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fixtures import (
    SHARED_LAT,
    SHARED_LON,
    as_workbook,
    location,
    sited,
    structural_extract,
)

from cass_extract import (
    COHORT_RULE_VERSION,
    LOCATION_SHEET,
    PARSER_VERSION,
    POLICY_SHEET,
    PROFILE_NAME,
    RESTRICTED_COLUMNS,
    SCHEMA_VERSION,
    Cohort,
    ExtractReadError,
    JoinSeverity,
    assign,
    assign_all,
    build_join_report,
    business_complete,
    cohort_profile,
    country_code,
    masked,
    read_workbook,
)
from cass_extract.schema import is_not_applicable


@pytest.fixture()
def extract():
    policies, locations = structural_extract()
    return read_workbook(as_workbook(policies, locations))


@pytest.fixture()
def canonical():
    """The same portfolio as the platform's rules read it.

    Cohorts and the business-complete rule are properties of a portfolio, not
    of a spreadsheet, so they are tested against canonical records rather than
    against the retired workbook's own column names.
    """
    import io

    from cass_extract import intake, legacy

    policies, locations = structural_extract()
    payload, _ = legacy.migrate(as_workbook(policies, locations))
    return intake.records(intake.read_workbook(io.BytesIO(payload)))


@pytest.fixture()
def rows(extract):
    return (
        [row.values for row in extract.policies],
        [row.values for row in extract.locations],
    )


# -- reading -----------------------------------------------------------------

def test_both_sheets_are_read(extract):
    assert len(extract.policies) == 10
    assert len(extract.locations) == 11
    assert extract.is_readable is True


def test_a_clean_extract_raises_no_findings(extract):
    """Findings are for problems. A clean read must be silent."""
    assert extract.findings == []


def test_money_is_read_as_decimal_not_float(rows):
    """The exact reconciliation the brief requires cannot survive a float."""
    policies, _ = rows
    value = policies[0]["gross_limit"]
    assert isinstance(value, Decimal)
    assert value == Decimal("1000000.00")


def test_the_source_text_is_kept_beside_the_typed_value(extract):
    """Section 5: no transformation silently replaces a reported field."""
    row = extract.policies.rows[0]
    assert row.raw["gross_limit"] == "1000000.00"
    assert row.values["gross_limit"] == Decimal("1000000.00")


def test_a_not_applicable_marker_is_absence_rather_than_a_bad_number(rows):
    """The renewal columns carry an em dash on every unrenewed policy.

    Reading those as unreadable numbers raised 4,020 findings against the real
    extract and buried everything that mattered.
    """
    policies, _ = rows
    unrenewed = next(p for p in policies if p["renewed"] == "N")
    assert unrenewed["renewal_premium"] is None
    assert unrenewed["premium_growth_pct"] is None


@pytest.mark.parametrize("marker", ["—", "–", "-", "N/A", "na", "none", " — "])
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
    import io as stdlib_io

    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.active.title = "Sheet1"
    buffer = stdlib_io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    with pytest.raises(ExtractReadError, match="Premium Policies"):
        read_workbook(buffer)


def test_something_that_is_not_a_workbook_is_refused():
    import io as stdlib_io

    with pytest.raises(ExtractReadError, match="could not be opened"):
        read_workbook(stdlib_io.BytesIO(b"policy_id,business_id\n1,2\n"))


def test_reading_the_same_extract_twice_gives_the_same_answer(rows):
    """Section 8: import is deterministic."""
    policies, locations = structural_extract()
    first = read_workbook(as_workbook(policies, locations))
    second = read_workbook(as_workbook(policies, locations))

    assert [r.values for r in first.locations] == [r.values for r in second.locations]
    assert (
        build_join_report(
            [r.values for r in first.policies], [r.values for r in first.locations]
        ).as_dict()
        == build_join_report(
            [r.values for r in second.policies], [r.values for r in second.locations]
        ).as_dict()
    )


def test_the_schema_and_parser_versions_are_stated():
    """Both are recorded with every import so a reread is distinguishable."""
    assert SCHEMA_VERSION.startswith("klapton-re-geocoded-policy-extract/")
    assert PROFILE_NAME == "Klapton Re geocoded policy extract"
    assert PARSER_VERSION


# -- what leaves the platform ---------------------------------------------------

def test_the_restricted_columns_are_the_ones_a_support_bundle_omits():
    """Not a permission. Every CASS user sees all of these in the platform.

    A support bundle is a file that leaves it, sometimes to a vendor, so it
    carries identifiers and codes rather than whole portfolio rows. Nothing
    here is withheld from a colleague, and an earlier draft that withheld the
    address from modellers made the geocoding review impossible to do.
    """
    assert RESTRICTED_COLUMNS == {
        "business_title",
        "insured_name",
        "cedent_name",
        "broker_name",
        "risk_location_address",
        "provider_address",
        "gross_premium",
        "net_premium",
        "gross_limit",
        "gross_paid",
        "gross_outstanding",
        "gross_incurred",
        "loss_ratio_pct",
        "prior_share_pct",
        "renewal_premium",
        "premium_growth_pct",
        "renewal_share_pct",
    }


def test_a_bundle_carries_identifiers_rather_than_portfolio_detail(rows):
    policies, _ = rows
    safe = masked(policies[0])

    assert "insured_name" not in safe
    assert "gross_limit" not in safe
    assert safe["policy_id"] == "P-1"
    assert safe["business_id"] == "B-SINGLE"


def test_a_bundle_can_be_asked_to_carry_everything(rows):
    policies, _ = rows
    full = masked(policies[0], include_confidential=True)
    assert full["insured_name"] == "Example Insured Ltd"
    assert full["gross_limit"] == Decimal("1000000.00")


def test_a_finding_quotes_the_cell_that_needs_correcting():
    """Blanking values would make a findings list unactionable.

    A finding names one cell on one row, which is what a person needs to fix
    it. That is a different thing from a bundle carrying the whole portfolio.
    """
    policies, locations = structural_extract()
    locations[0]["latitude"] = "not-a-number"
    read = read_workbook(as_workbook(policies, locations))

    finding = next(item for item in read.findings if item.field == "latitude")
    assert finding.value == "not-a-number"


# -- the join report ----------------------------------------------------------

def test_the_join_counts_both_sheets_without_joining_them(rows):
    report = build_join_report(*rows)
    assert report.policy_rows == 10
    assert report.location_rows == 11
    assert report.policy_businesses == 9
    assert report.located_businesses == 8


def test_primary_and_secondary_locations_are_both_retained(rows):
    """primary_location is an attribute, not a filter that deletes sites."""
    report = build_join_report(*rows)
    assert report.primary_locations == 8
    assert report.secondary_locations == 3
    assert report.primary_locations + report.secondary_locations == report.location_rows


def test_the_natural_key_is_business_and_location_number(rows):
    report = build_join_report(*rows)
    assert report.unique_location_keys == report.location_rows
    assert report.duplicate_location_keys == ()


def test_a_repeated_natural_key_is_an_error():
    policies, locations = structural_extract()
    locations.append(location("B-MULTI", 1, Decimal("-6.910000"), Decimal("107.600000")))
    read = read_workbook(as_workbook(policies, locations))
    report = build_join_report(
        [r.values for r in read.policies], [r.values for r in read.locations]
    )

    assert "B-MULTI/1" in report.duplicate_location_keys
    assert report.blocking is True


def test_a_business_on_two_policy_rows_is_reported(rows):
    """Joining on the business reference alone would repeat its locations."""
    report = build_join_report(*rows)
    assert report.businesses_with_many_policies == ("B-TWOPOL",)

    finding = next(f for f in report.findings if f.code == "many_policies_per_business")
    assert finding.severity == JoinSeverity.ERROR
    assert "B-TWOPOL" in finding.subjects
    assert "policy-selection or split rule" in finding.remediation


def test_a_repeated_business_with_no_locations_is_a_warning_not_an_error():
    """The fan-out risk is only real where the business has locations to repeat."""
    policies, locations = structural_extract()
    locations = [row for row in locations if row["business_id"] != "B-TWOPOL"]
    for row in policies:
        if row["business_id"] == "B-TWOPOL":
            row["risk_location_count"] = 0
            row["risk_latitude"] = None
            row["risk_longitude"] = None
    read = read_workbook(as_workbook(policies, locations))
    report = build_join_report(
        [r.values for r in read.policies], [r.values for r in read.locations]
    )

    finding = next(f for f in report.findings if f.code == "many_policies_per_business")
    assert finding.severity == JoinSeverity.WARNING
    assert report.blocking is False


def test_no_policy_value_is_duplicated_across_locations(rows):
    """Section 8: the total is the sum of the policy sheet, once."""
    policies, _ = rows
    report = build_join_report(*rows)
    assert report.total_tiv == sum(p["gross_limit"] for p in policies)
    assert report.total_tiv == Decimal("9425000.00")


def test_the_located_total_counts_only_businesses_that_have_locations(rows):
    report = build_join_report(*rows)
    # Everything except B-NOLOC, whose 800,000 has no site to sit at.
    assert report.located_tiv == Decimal("8625000.00")
    assert report.total_tiv - report.located_tiv == Decimal("800000.00")


def test_a_shared_coordinate_is_reported_and_not_deduplicated(rows):
    """They may be shared sites, group risks or geocoding centroids."""
    _, locations = rows
    report = build_join_report(*rows)

    finding = next(f for f in report.findings if f.code == "shared_coordinate")
    assert finding.severity == JoinSeverity.INFO
    assert "Do not deduplicate on coordinates" in finding.remediation

    # Both rows survive with their own identities.
    at_shared = [
        row
        for row in locations
        if row["latitude"] == SHARED_LAT and row["longitude"] == SHARED_LON
    ]
    assert {row["business_id"] for row in at_shared} == {"B-SINGLE", "B-SHARED"}
    assert report.location_rows == len(locations)


def test_a_policy_declaring_the_wrong_schedule_size_is_an_error():
    policies, locations = structural_extract()
    for row in policies:
        if row["policy_id"] == "P-2":
            row["risk_location_count"] = 5
    read = read_workbook(as_workbook(policies, locations))
    report = build_join_report(
        [r.values for r in read.policies], [r.values for r in read.locations]
    )

    finding = next(f for f in report.findings if f.code == "location_count_disagreement")
    assert finding.severity == JoinSeverity.ERROR
    assert any("P-2" in subject for subject in finding.subjects)
    assert report.blocking is True


def test_a_primary_coordinate_that_disagrees_between_sheets_is_an_error():
    policies, locations = structural_extract()
    locations[0]["latitude"] = Decimal("-6.500000")
    read = read_workbook(as_workbook(policies, locations))
    report = build_join_report(
        [r.values for r in read.policies], [r.values for r in read.locations]
    )

    assert "P-1" in report.primary_coordinate_mismatches
    assert report.blocking is True


def test_a_location_with_no_policy_row_is_reported_as_an_orphan():
    policies, locations = structural_extract()
    locations.append(location("B-GHOST", 1, Decimal("-6.4"), Decimal("106.4")))
    read = read_workbook(as_workbook(policies, locations))
    report = build_join_report(
        [r.values for r in read.policies], [r.values for r in read.locations]
    )

    assert report.orphan_locations == ("B-GHOST",)
    finding = next(f for f in report.findings if f.code == "orphan_location")
    assert finding.severity == JoinSeverity.WARNING


def test_a_clean_extract_produces_no_blocking_finding(rows):
    policies, locations = structural_extract()
    # B-TWOPOL is the only error in the structural fixture; without it the
    # report should be clean.
    policies = [p for p in policies if p["policy_id"] != "P-4b"]
    read = read_workbook(as_workbook(policies, locations))
    report = build_join_report(
        [r.values for r in read.policies], [r.values for r in read.locations]
    )
    assert report.blocking is False


# -- cohorts ------------------------------------------------------------------

def test_review_beats_precision(rows):
    """A reviewer's flag is a judgement about the row; precision is not."""
    assignment = assign(
        sited("B-X", 1, Decimal("-6.2"), Decimal("106.8"),
              precision="parcel", needs_review=True)
    )
    assert assignment.cohort is Cohort.C
    assert "flagged" in assignment.reason


@pytest.mark.parametrize("precision", ["parcel", "street", "embedded"])
def test_a_precise_no_review_location_is_cohort_a(precision):
    assignment = assign(
        sited("B-X", 1, Decimal("-6.2"), Decimal("106.8"), precision=precision)
    )
    assert assignment.cohort is Cohort.A


@pytest.mark.parametrize("precision", ["locality", "postcode", "admin"])
def test_a_coarse_no_review_location_is_cohort_b(precision):
    assignment = assign(
        sited("B-X", 1, Decimal("-6.2"), Decimal("106.8"), precision=precision)
    )
    assert assignment.cohort is Cohort.B


def test_an_unrecognised_precision_is_never_treated_as_eligible():
    assignment = assign(
        sited("B-X", 1, Decimal("-6.2"), Decimal("106.8"), precision="guessed")
    )
    assert assignment.cohort is Cohort.UNCLASSIFIED


def test_a_null_island_coordinate_is_a_failed_geocode_not_a_place():
    assignment = assign(sited("B-X", 1, Decimal("0"), Decimal("0")))
    assert assignment.cohort is Cohort.UNCLASSIFIED
    assert "failed geocode" in assignment.reason


def test_every_assignment_records_the_rule_version_that_made_it(canonical):
    locations, _ = canonical
    assignments = assign_all(locations)
    assert {a.rule_version for a in assignments} == {COHORT_RULE_VERSION}


def test_every_source_row_keeps_an_assignment(canonical):
    """Preserve every source row: none is dropped for being ineligible."""
    locations, _ = canonical
    assert len(assign_all(locations)) == len(locations)


def test_the_cohort_profile_counts_by_country_and_class(canonical):
    locations, _ = canonical
    summary = cohort_profile(locations, assign_all(locations))

    assert summary.counts[str(Cohort.A)] == 9
    assert summary.counts[str(Cohort.B)] == 1
    assert summary.counts[str(Cohort.C)] == 1
    assert summary.by_country[str(Cohort.A)]["NP"] == 1
    assert summary.by_class[str(Cohort.A)]["Liability"] == 1


# -- the business-complete benchmark rule --------------------------------------

def test_a_business_qualifies_only_when_every_one_of_its_sites_does(canonical):
    """Otherwise the excluded site's TIV silently moves or disappears."""
    locations, _ = canonical
    complete = business_complete(locations, assign_all(locations))

    assert "B-MULTI" in complete
    assert "B-SINGLE" in complete
    # One of B-PARTIAL's two sites needs review, so the whole business is out.
    assert "B-PARTIAL" not in complete


def test_the_excluded_classes_keep_a_business_out(canonical):
    locations, _ = canonical
    complete = business_complete(locations, assign_all(locations))

    assert "B-NEPAL" not in complete      # Engineering
    assert "B-LIABILITY" not in complete  # Liability
    assert "B-COARSE" not in complete     # cohort B


def test_the_class_filter_can_be_lifted_for_another_workstream(canonical):
    """Engineering is a separate workstream, not a permanent exclusion."""
    locations, _ = canonical
    complete = business_complete(locations, assign_all(locations), class_of_business=None)
    assert "B-NEPAL" in complete


def test_the_benchmark_tiv_is_the_sum_of_its_policies_once(canonical):
    locations, policies = canonical
    complete = business_complete(locations, assign_all(locations))
    total = sum(p["policy_tiv"] for p in policies if p["business_id"] in complete)

    # B-SINGLE 1,000,000 + B-MULTI 3,000,000 + B-SHARED 250,000
    # + B-TWOPOL 500,000 + 750,000 across its two policy rows.
    assert total == Decimal("5500000.00")


# -- country codes -------------------------------------------------------------

@pytest.mark.parametrize(("name", "code"), [("Indonesia", "ID"), ("nepal", "NP")])
def test_the_pilot_countries_map_to_iso_codes(name, code):
    assert country_code(name) == code


def test_an_unrecognised_country_is_not_guessed():
    """A wrong code would route a location to the wrong national grid."""
    assert country_code("Indoneesia") == ""
    assert country_code("") == ""


def test_the_sheet_names_are_the_ones_the_source_uses():
    assert POLICY_SHEET == "Premium Policies"
    assert LOCATION_SHEET == "Risk Locations"
