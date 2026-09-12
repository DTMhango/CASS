"""Splitting a location total into OED coverage components.

Two properties matter. The components add back to the location total exactly,
whatever the weights and however awkward the amount. And a split is a named,
inspectable thing rather than a constant, because an analyst has to be able to
see which assumption produced a result and change it.
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest

from cass_extract import (
    COMPONENT_RULE_VERSION,
    COVERAGE_ORDER,
    DEFAULT_SPLIT,
    PRESETS,
    AllocationError,
    AllocationEvidence,
    ComponentSplit,
    Coverage,
    custom,
    preset,
    reconciliation,
    split_locations,
)


def total_of(amounts) -> Decimal:
    return sum(amounts.values(), Decimal("0.00"))


# -- exactness ---------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(PRESETS))
@pytest.mark.parametrize(
    "amount", ["0.01", "0.03", "1.00", "33.33", "100000.00", "822816504.04"]
)
def test_every_preset_reconciles_on_every_amount(name, amount):
    total = Decimal(amount)
    assert total_of(preset(name).apply(total)) == total


def test_an_awkward_three_way_split_still_reconciles():
    """A third each of one cent cannot divide evenly, and must still balance."""
    split = custom("thirds_v1", {"building": "33.34", "other": "33.33", "contents": "33.33"})
    assert total_of(split.apply(Decimal("0.01"))) == Decimal("0.01")
    assert total_of(split.apply(Decimal("1.00"))) == Decimal("1.00")


def test_a_zero_total_splits_to_zeroes_rather_than_failing():
    amounts = preset("commercial_property_test_v1").apply(Decimal("0.00"))
    assert total_of(amounts) == Decimal("0.00")
    assert set(amounts.values()) == {Decimal("0.00")}


def test_every_coverage_appears_even_when_it_receives_nothing():
    """A blank contents column and a zero say different things."""
    amounts = preset("building_only_technical_v1").apply(Decimal("100.00"))
    assert set(amounts) == {str(coverage) for coverage in COVERAGE_ORDER}
    assert amounts["ContentsTIV"] == Decimal("0.00")


def test_the_same_amount_splits_the_same_way_every_time():
    split = preset("commercial_property_test_v1")
    assert split.apply(Decimal("12345.67")) == split.apply(Decimal("12345.67"))


# -- a split is a named thing ----------------------------------------------------------

def test_the_default_is_the_conservative_option():
    """A default that spread value would assert an unapproved prior."""
    assert DEFAULT_SPLIT.name == "building_only_technical_v1"
    assert DEFAULT_SPLIT.weights[str(Coverage.BUILDING)] == Fraction(1)


def test_no_preset_claims_to_be_approved():
    assert all(split.approved is False for split in PRESETS.values())
    assert all(
        split.evidence is AllocationEvidence.ASSUMED for split in PRESETS.values()
    )


def test_every_preset_says_what_it_is():
    for split in PRESETS.values():
        assert split.description
        assert split.rule_version == COMPONENT_RULE_VERSION


def test_a_split_serialises_its_weights_and_percentages():
    described = preset("building_contents_test_v1").as_dict()
    assert described["percentages"]["BuildingTIV"] == 80.0
    assert described["percentages"]["ContentsTIV"] == 20.0
    assert described["weights"]["BuildingTIV"] == "4/5"


def test_only_the_coverages_that_receive_a_share_are_listed():
    assert preset("building_only_technical_v1").coverages == ("BuildingTIV",)
    assert preset("commercial_property_test_v1").coverages == (
        "BuildingTIV",
        "OtherTIV",
        "ContentsTIV",
        "BITIV",
    )


def test_an_unknown_preset_names_what_is_available():
    with pytest.raises(AllocationError, match="building_only_technical_v1"):
        preset("something_else")


# -- analyst-supplied splits ------------------------------------------------------------

def test_percentages_are_accepted_because_that_is_how_the_assumption_is_discussed():
    split = custom("mine_v1", {"building": 62.5, "contents": 37.5})
    amounts = split.apply(Decimal("1000.00"))
    assert amounts["BuildingTIV"] == Decimal("625.00")
    assert amounts["ContentsTIV"] == Decimal("375.00")


def test_a_coverage_can_be_named_by_its_short_name_or_its_oed_column():
    by_short = custom("a_v1", {"building": 50, "contents": 50})
    by_column = custom("b_v1", {"BuildingTIV": 50, "ContentsTIV": 50})
    assert by_short.weights == by_column.weights


def test_a_split_that_does_not_add_to_one_hundred_is_refused_not_normalised():
    """Normalising would silently apply something other than what was asked."""
    with pytest.raises(AllocationError, match="summing to"):
        custom("bad_v1", {"building": 70, "contents": 20})


def test_a_coverage_oed_does_not_have_is_refused():
    with pytest.raises(AllocationError, match="not an OED coverage"):
        custom("bad_v1", {"building": 50, "goodwill": 50})


def test_a_split_built_directly_is_validated_too():
    with pytest.raises(AllocationError, match="summing to"):
        ComponentSplit(
            name="bad_v1",
            weights={str(Coverage.BUILDING): Fraction(1, 2)},
            description="Half of nothing.",
        )


# -- across a portfolio -------------------------------------------------------------------

def test_a_portfolio_of_locations_reconciles_location_by_location():
    totals = {
        ("B-1", 1): Decimal("1000000.00"),
        ("B-1", 2): Decimal("0.01"),
        ("B-2", 1): Decimal("333333.33"),
    }
    split = preset("commercial_property_test_v1")
    components = split_locations(totals, split)
    report = reconciliation(totals, components)

    assert report["reconciles"] is True
    assert report["mismatched_locations"] == []
    assert Decimal(report["component_total"]) == Decimal(report["expected_total"])
    assert report["location_count"] == 3


def test_the_reconciliation_names_a_location_that_does_not_balance():
    """A portfolio that balances while two locations are wrong is the risk."""
    totals = {("B-1", 1): Decimal("100.00")}
    tampered = {("B-1", 1): {"BuildingTIV": Decimal("99.00")}}
    report = reconciliation(totals, tampered)

    assert report["reconciles"] is False
    assert report["mismatched_locations"][0]["location"] == "B-1/1"
    assert report["difference"] == "-1.00"
