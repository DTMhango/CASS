"""Coordinate eligibility, and the rule that a benchmark business is all or nothing.

Cohorts are a property of a portfolio rather than of a spreadsheet, so these run
against canonical records -- what any reader produces, whatever it read -- and
never against a column name. A cohort says how far a coordinate can be trusted:
A is precise enough to model at the site, B is coarse enough that only an
aggregate is honest, C is anything a reviewer has flagged, and unclassified is
everything the rules will not place.

The distinction that matters most is the last one. Nothing is dropped for being
ineligible; every source row keeps an assignment, and the assignment carries the
reason, so a location that is out is visibly out rather than absent.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fixtures import as_workbook, sited, structural_extract

from cass_extract import (
    COHORT_RULE_VERSION,
    Cohort,
    assign,
    assign_all,
    business_complete,
    cohort_profile,
)


@pytest.fixture()
def canonical():
    """The same portfolio as the platform's rules read it.

    Built through the migration so that one portfolio serves both suites: the
    shapes section 8 asks for are declared once, in the fixtures, and the rules
    see them as intake records however they arrived.
    """
    import io

    from cass_extract import intake, legacy

    policies, locations = structural_extract()
    payload, _ = legacy.migrate(as_workbook(policies, locations))
    return intake.records(intake.read_workbook(io.BytesIO(payload)))


# -- cohorts ------------------------------------------------------------------

def test_review_beats_precision():
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
