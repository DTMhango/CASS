"""Dividing a policy's TIV across the locations it schedules.

The property that matters most is exact reconciliation: every policy, every
business, the whole portfolio, to the cent. A total that is nearly right is the
failure mode this module exists to prevent, so most of what follows is an
assertion about equality rather than closeness.

The other half is what must *not* influence a weight. Geocode confidence,
precision, provider and the primary flag are not measures of value, and neither
is modelled hazard; the tests pin that by changing those fields and requiring
the answer not to move.
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest
from fixtures import location, policy, structural_extract

from cass_extract import (
    ALLOCATION_RULE_VERSION,
    AllocationError,
    AllocationEvidence,
    AllocationMethod,
    allocate,
    allocate_policy,
    concentration_envelope,
    read_workbook,
)
from cass_extract.allocation import _apportion


def sites(business: str, count: int, **overrides):
    """A schedule of ``count`` locations for one business."""
    return [
        location(
            business,
            number,
            Decimal("-6.2") + Decimal(number) / 100,
            Decimal("106.8") + Decimal(number) / 100,
            primary="Yes" if number == 1 else "No",
            **overrides,
        )
        for number in range(1, count + 1)
    ]


# -- exact apportionment ---------------------------------------------------------

def test_an_even_split_of_an_even_amount_is_exact():
    amounts = _apportion(Decimal("100.00"), [Fraction(1, 2)] * 2)
    assert amounts == [Decimal("50.00"), Decimal("50.00")]


def test_a_third_of_a_dollar_still_reconciles_to_the_cent():
    """The classic case: 1.00 in three parts cannot divide evenly."""
    amounts = _apportion(Decimal("1.00"), [Fraction(1, 3)] * 3)
    assert sum(amounts) == Decimal("1.00")
    assert sorted(amounts) == [Decimal("0.33"), Decimal("0.33"), Decimal("0.34")]


def test_the_residual_cents_go_to_the_earliest_positions_on_a_tie():
    """Equal weights leave equal remainders, so order decides. Deterministically."""
    amounts = _apportion(Decimal("10.00"), [Fraction(1, 3)] * 3)
    assert amounts == [Decimal("3.34"), Decimal("3.33"), Decimal("3.33")]


def test_the_same_input_apportions_the_same_way_every_time(  ):
    first = _apportion(Decimal("1000000.01"), [Fraction(1, 7)] * 7)
    second = _apportion(Decimal("1000000.01"), [Fraction(1, 7)] * 7)
    assert first == second
    assert sum(first) == Decimal("1000000.01")


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6, 7, 9, 11, 13])
@pytest.mark.parametrize("amount", ["0.01", "0.07", "1.00", "999.99", "3327746598.60"])
def test_any_equal_split_of_any_amount_reconciles(count, amount):
    total = Decimal(amount)
    amounts = _apportion(total, [Fraction(1, count)] * count)
    assert sum(amounts) == total
    assert len(amounts) == count
    # No share is more than one cent from any other under an equal split.
    assert max(amounts) - min(amounts) <= Decimal("0.01")


def test_weights_that_do_not_sum_to_one_are_refused():
    """A policy's value must be fully accounted for."""
    with pytest.raises(AllocationError, match="sum to"):
        _apportion(Decimal("100.00"), [Fraction(1, 3)] * 2)


def test_an_amount_finer_than_a_cent_cannot_be_allocated_exactly():
    with pytest.raises(AllocationError, match="whole number of cents"):
        _apportion(Decimal("1.005"), [Fraction(1)])


# -- one policy --------------------------------------------------------------------

def test_a_single_location_policy_takes_the_whole_tiv():
    result = allocate_policy(
        policy("P-1", "B-1", "1000000.00"), sites("B-1", 1)
    )
    assert result.method is AllocationMethod.SINGLE_LOCATION
    assert result.shares[0].amount == Decimal("1000000.00")
    assert result.reconciles


def test_a_single_location_allocation_is_reported_not_assumed():
    """A whole allocation to the only site is a fact; an equal split is not."""
    result = allocate_policy(policy("P-1", "B-1", "1000000.00"), sites("B-1", 1))
    assert result.evidence is AllocationEvidence.REPORTED


def test_the_baseline_splits_equally_and_says_it_is_an_assumption():
    result = allocate_policy(policy("P-1", "B-1", "3000000.00"), sites("B-1", 3))
    assert result.method is AllocationMethod.EQUAL_LOCATION
    assert result.evidence is AllocationEvidence.ASSUMED
    assert [share.amount for share in result.shares] == [Decimal("1000000.00")] * 3


def test_nothing_is_repeated_at_every_location():
    """Writing the whole TIV at each site is the failure named first."""
    result = allocate_policy(policy("P-1", "B-1", "3000000.00"), sites("B-1", 3))
    assert result.allocated == Decimal("3000000.00")
    assert result.allocated != Decimal("9000000.00")


def test_every_share_records_the_weight_behind_it():
    result = allocate_policy(policy("P-1", "B-1", "1000.00"), sites("B-1", 3))
    assert {share.weight for share in result.shares} == {"1/3"}


def test_the_rule_version_is_recorded_on_every_allocation():
    result = allocate_policy(policy("P-1", "B-1", "1000.00"), sites("B-1", 2))
    assert result.rule_version == ALLOCATION_RULE_VERSION


def test_a_policy_with_no_scheduled_location_is_refused():
    """Its value would have nowhere to go."""
    with pytest.raises(AllocationError, match="nowhere to go"):
        allocate_policy(policy("P-1", "B-1", "1000.00"), [])


def test_the_residual_cent_is_flagged_on_the_share_that_received_it():
    result = allocate_policy(policy("P-1", "B-1", "1.00"), sites("B-1", 3))
    flagged = [share for share in result.shares if share.residual_cent]
    assert len(flagged) == 1
    assert flagged[0].amount == Decimal("0.34")


# -- the primary-concentrated sensitivity ---------------------------------------------

def test_the_primary_sensitivity_puts_seventy_percent_at_the_primary_site():
    result = allocate_policy(
        policy("P-1", "B-1", "1000000.00"),
        sites("B-1", 3),
        method=AllocationMethod.PRIMARY_CONCENTRATED,
    )
    by_number = {share.location_number: share.amount for share in result.shares}
    assert by_number[1] == Decimal("700000.00")
    assert by_number[2] == Decimal("150000.00")
    assert by_number[3] == Decimal("150000.00")
    assert result.reconciles


def test_the_primary_sensitivity_still_reconciles_on_an_awkward_amount():
    result = allocate_policy(
        policy("P-1", "B-1", "1000.01"),
        sites("B-1", 3),
        method=AllocationMethod.PRIMARY_CONCENTRATED,
    )
    assert result.allocated == Decimal("1000.01")


def test_the_primary_sensitivity_needs_exactly_one_primary_site():
    schedule = sites("B-1", 3)
    for row in schedule:
        row["primary_location"] = "Yes"
    with pytest.raises(AllocationError, match="marked primary"):
        allocate_policy(
            policy("P-1", "B-1", "1000.00"),
            schedule,
            method=AllocationMethod.PRIMARY_CONCENTRATED,
        )


def test_the_primary_sensitivity_is_an_assumption_not_evidence():
    """It tests whether the primary flag is material; it does not assert it."""
    result = allocate_policy(
        policy("P-1", "B-1", "1000.00"),
        sites("B-1", 2),
        method=AllocationMethod.PRIMARY_CONCENTRATED,
    )
    assert result.evidence is AllocationEvidence.ASSUMED


# -- the concentration envelope ---------------------------------------------------------

def test_the_envelope_produces_one_variant_per_scheduled_location():
    variants = concentration_envelope(
        policy("P-1", "B-1", "1000000.00"), sites("B-1", 3)
    )
    assert len(variants) == 3
    assert [item.concentrated_at for item in variants] == [1, 2, 3]


def test_each_envelope_variant_holds_the_whole_policy_tiv_at_one_site():
    variants = concentration_envelope(
        policy("P-1", "B-1", "1000000.00"), sites("B-1", 3)
    )
    for variant in variants:
        held = [share for share in variant.shares if share.amount > 0]
        assert len(held) == 1
        assert held[0].amount == Decimal("1000000.00")
        assert held[0].location_number == variant.concentrated_at
        assert variant.reconciles


def test_a_concentration_variant_must_name_its_site():
    with pytest.raises(AllocationError, match="must name the location"):
        allocate_policy(
            policy("P-1", "B-1", "1000.00"),
            sites("B-1", 2),
            method=AllocationMethod.CONCENTRATION,
        )


def test_a_concentration_variant_cannot_name_an_unscheduled_site():
    with pytest.raises(AllocationError, match="does not schedule location"):
        allocate_policy(
            policy("P-1", "B-1", "1000.00"),
            sites("B-1", 2),
            method=AllocationMethod.CONCENTRATION,
            concentrate_at=9,
        )


# -- what must never move a weight ----------------------------------------------------

@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("confidence", ["high", "low"]),
        ("precision", ["parcel", "admin"]),
        ("provider", ["example-geocoder", "another-geocoder"]),
        ("method", ["address", "centroid"]),
    ],
)
def test_geocoding_quality_never_changes_the_split(field, values):
    """A coordinate that was easy to find is not a building worth more."""
    baseline = sites("B-1", 2)
    skewed = sites("B-1", 2)
    skewed[0][field] = values[0]
    skewed[1][field] = values[1]

    first = allocate_policy(policy("P-1", "B-1", "1000.00"), baseline)
    second = allocate_policy(policy("P-1", "B-1", "1000.00"), skewed)
    assert [s.amount for s in first.shares] == [s.amount for s in second.shares]


def test_the_primary_flag_does_not_move_the_baseline_split():
    """It is an attribute, and the baseline must imply no ranking at all."""
    result = allocate_policy(policy("P-1", "B-1", "1000.00"), sites("B-1", 2))
    assert [share.amount for share in result.shares] == [
        Decimal("500.00"),
        Decimal("500.00"),
    ]


# -- a whole portfolio -------------------------------------------------------------------

@pytest.fixture()
def extract_rows():
    policies, locations = structural_extract()
    from fixtures import as_workbook

    read = read_workbook(as_workbook(policies, locations))
    return (
        [row.values for row in read.policies],
        [row.values for row in read.locations],
    )


def test_every_policy_in_the_portfolio_reconciles(extract_rows):
    result = allocate(*extract_rows)
    assert result.reconciles
    assert all(item.reconciles for item in result.allocations)


def test_the_portfolio_total_equals_the_sum_of_its_allocated_policies(extract_rows):
    result = allocate(*extract_rows)
    assert result.allocated_tiv == result.source_tiv


def test_each_policy_is_allocated_independently_even_on_a_shared_business(extract_rows):
    """B-TWOPOL carries two policies over one location schedule."""
    result = allocate(*extract_rows)
    twopol = [item for item in result.allocations if item.business_id == "B-TWOPOL"]
    assert len(twopol) == 2
    assert {item.policy_id for item in twopol} == {"P-4a", "P-4b"}

    # The location carries both policies' value, which is correct.
    assert result.by_location()[("B-TWOPOL", 1)] == Decimal("1250000.00")


def test_a_policy_whose_business_has_no_location_is_excluded_with_a_reason(extract_rows):
    result = allocate(*extract_rows)
    excluded = dict(result.excluded)
    assert "P-9" in excluded
    assert "no geocoded location" in excluded["P-9"]


def test_the_location_totals_sum_back_to_the_allocated_total(extract_rows):
    result = allocate(*extract_rows)
    assert sum(result.by_location().values()) == result.allocated_tiv


# -- the eligibility gate -------------------------------------------------------------------

def test_a_policy_with_a_site_outside_the_cohort_is_excluded_whole(extract_rows):
    """Redistributing its share would overstate the sites that remain."""
    policies, locations = extract_rows
    # Approve everything except B-MULTI's third site.
    eligible = {
        (row["business_id"], int(row["location_number"]))
        for row in locations
        if not (row["business_id"] == "B-MULTI" and int(row["location_number"]) == 3)
    }
    result = allocate(policies, locations, eligible=eligible)

    excluded = dict(result.excluded)
    assert "P-2" in excluded
    assert "outside the approved cohort" in excluded["P-2"]
    assert "rather than moving their share" in excluded["P-2"]
    assert all(item.business_id != "B-MULTI" for item in result.allocations)


def test_an_eligible_policy_is_unaffected_by_the_gate(extract_rows):
    policies, locations = extract_rows
    eligible = {
        (row["business_id"], int(row["location_number"])) for row in locations
    }
    result = allocate(policies, locations, eligible=eligible)
    assert result.reconciles
    assert not any(
        "outside the approved cohort" in reason for _, reason in result.excluded
    )


def test_the_gate_never_silently_moves_value(extract_rows):
    """The excluded policy's TIV is absent from the result, not spread over it."""
    policies, locations = extract_rows
    eligible = {
        (row["business_id"], int(row["location_number"]))
        for row in locations
        if row["business_id"] != "B-MULTI"
    }
    with_gate = allocate(policies, locations, eligible=eligible)
    without_gate = allocate(policies, locations)

    assert without_gate.source_tiv - with_gate.source_tiv == Decimal("3000000.00")
    for key, amount in with_gate.by_location().items():
        assert without_gate.by_location()[key] == amount


# -- when the schedule reports its own site values ------------------------------------

def _sited(business: str, number: int, value: str | None, **extra):
    row = {
        "business_id": business,
        "location_number": number,
        "primary_location": "Yes" if number == 1 else "No",
        "latitude": Decimal("-6.2"),
        "longitude": Decimal("106.8"),
    }
    if value is not None:
        row["location_tiv"] = Decimal(value)
    row.update(extra)
    return row


def test_a_schedule_that_reports_its_values_needs_no_assumption():
    """The case where the allocation question does not arise at all."""
    policy = {"policy_id": "P-1", "business_id": "B-1", "gross_limit": Decimal("1000.00")}
    schedule = [_sited("B-1", 1, "750.00"), _sited("B-1", 2, "250.00")]

    result = allocate_policy(policy, schedule, method=AllocationMethod.REPORTED)

    assert result.method is AllocationMethod.REPORTED
    assert result.evidence is AllocationEvidence.REPORTED
    assert [share.amount for share in result.shares] == [
        Decimal("750.00"),
        Decimal("250.00"),
    ]
    assert result.reconciles


def test_reported_values_are_never_silently_read_as_an_equal_split():
    """The defect this test exists for: the method used to fall through.

    An option the interface offers and the engine quietly substitutes is worse
    than one it refuses, because the lineage records the substitute as though
    somebody chose it.
    """
    policy = {"policy_id": "P-1", "business_id": "B-1", "gross_limit": Decimal("1000.00")}
    schedule = [_sited("B-1", 1, "900.00"), _sited("B-1", 2, "100.00")]

    reported = allocate_policy(policy, schedule, method=AllocationMethod.REPORTED)
    equal = allocate_policy(policy, schedule, method=AllocationMethod.EQUAL_LOCATION)

    assert [share.amount for share in reported.shares] != [
        share.amount for share in equal.shares
    ]
    assert equal.evidence is AllocationEvidence.ASSUMED


def test_several_policies_over_one_schedule_divide_in_proportion():
    """Site values cannot equal every policy's TIV at once, so they are weights."""
    schedule = [_sited("B-1", 1, "750.00"), _sited("B-1", 2, "250.00")]
    first = allocate_policy(
        {"policy_id": "P-1", "business_id": "B-1", "gross_limit": Decimal("1000.00")},
        schedule,
        method=AllocationMethod.REPORTED,
    )
    second = allocate_policy(
        {"policy_id": "P-2", "business_id": "B-1", "gross_limit": Decimal("400.00")},
        schedule,
        method=AllocationMethod.REPORTED,
    )
    assert [share.amount for share in second.shares] == [
        Decimal("300.00"),
        Decimal("100.00"),
    ]
    assert first.reconciles and second.reconciles


def test_a_partly_reported_schedule_is_refused_and_names_the_gaps():
    """Part of a division is not a division."""
    policy = {"policy_id": "P-1", "business_id": "B-1", "gross_limit": Decimal("1000.00")}
    schedule = [_sited("B-1", 1, "750.00"), _sited("B-1", 2, None)]

    with pytest.raises(AllocationError) as excinfo:
        allocate_policy(policy, schedule, method=AllocationMethod.REPORTED)
    message = str(excinfo.value)
    assert "1 of its 2 locations report no value" in message
    assert "location 2" in message


def test_a_schedule_of_zeroes_is_not_a_reported_allocation():
    policy = {"policy_id": "P-1", "business_id": "B-1", "gross_limit": Decimal("1000.00")}
    schedule = [_sited("B-1", 1, "0.00"), _sited("B-1", 2, "0.00")]

    with pytest.raises(AllocationError, match="cannot divide anything"):
        allocate_policy(policy, schedule, method=AllocationMethod.REPORTED)


def test_a_negative_reported_value_is_refused():
    policy = {"policy_id": "P-1", "business_id": "B-1", "gross_limit": Decimal("1000.00")}
    schedule = [_sited("B-1", 1, "-10.00"), _sited("B-1", 2, "100.00")]

    with pytest.raises(AllocationError, match="negative value"):
        allocate_policy(policy, schedule, method=AllocationMethod.REPORTED)


def test_reported_values_reconcile_exactly_when_they_do_not_divide_evenly():
    """Thirds of a policy total still land on the cent."""
    policy = {"policy_id": "P-1", "business_id": "B-1", "gross_limit": Decimal("100.00")}
    schedule = [
        _sited("B-1", 1, "1.00"),
        _sited("B-1", 2, "1.00"),
        _sited("B-1", 3, "1.00"),
    ]
    result = allocate_policy(policy, schedule, method=AllocationMethod.REPORTED)
    assert sum(share.amount for share in result.shares) == Decimal("100.00")
    assert result.reconciles


def test_one_reported_site_is_still_recorded_as_a_single_location():
    """A whole allocation to the only site is a fact whatever method was asked for."""
    policy = {"policy_id": "P-1", "business_id": "B-1", "gross_limit": Decimal("500.00")}
    result = allocate_policy(
        policy, [_sited("B-1", 1, "500.00")], method=AllocationMethod.REPORTED
    )
    assert result.method is AllocationMethod.SINGLE_LOCATION
