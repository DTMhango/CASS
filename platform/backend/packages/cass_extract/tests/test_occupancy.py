"""Occupancy as a chosen, reproducible assumption.

The source reports no vulnerability attributes, so whatever a location gets is
an assumption. What these tests hold to is that the assumption is visible, that
it is identical on every run, and that real evidence displaces it.
"""

from __future__ import annotations

import pytest

import cass_extract as extract
from cass_extract.allocation import AllocationError, AllocationEvidence

KEYS = [("B-1", "1"), ("B-1", "2"), ("B-2", "1"), ("B-3", "1"), ("B-3", "2"), ("B-4", "1")]


# -- the assumption itself -----------------------------------------------------------

def test_a_uniform_assumption_gives_every_location_the_same_class():
    codes = {extract.COMMERCIAL_GENERAL.apply(key)["OccupancyCode"] for key in KEYS}
    assert codes == {"1100"}


def test_a_spread_assumption_uses_more_than_one_class():
    codes = {extract.MIXED_COMMERCIAL.apply(key)["OccupancyCode"] for key in KEYS}
    assert len(codes) > 1


def test_the_same_location_always_lands_in_the_same_class():
    """Not a draw. A loss that moves between runs moved for a reason."""
    first = [extract.MIXED_COMMERCIAL.apply(key) for key in KEYS]
    second = [extract.MIXED_COMMERCIAL.apply(key) for key in KEYS]
    assert first == second


def test_the_assignment_does_not_depend_on_the_process():
    """``hash()`` is salted per process; a digest is not.

    The expected codes are written out rather than recomputed, so a change to
    the selection rule fails here instead of quietly renumbering a portfolio
    somebody already ran.
    """
    assert [extract.MIXED_COMMERCIAL.apply(key)["OccupancyCode"] for key in KEYS] == [
        "1050",
        "1100",
        "1050",
        "1050",
        "1100",
        "1050",
    ]


def test_two_assumptions_do_not_assign_alike():
    """The assumption name is in the digest, so the mix is a property of it."""
    other = extract.OccupancyAssumption(
        name="other_test_v1",
        description="Same classes, different name.",
        classes=extract.MIXED_COMMERCIAL.classes,
    )
    assert [other.apply(key) for key in KEYS] != [
        extract.MIXED_COMMERCIAL.apply(key) for key in KEYS
    ]


def test_weights_are_respected_across_a_portfolio():
    keys = [("B", str(number)) for number in range(2000)]
    counts = extract.MIXED_COMMERCIAL.distribution(keys)
    # 5:3:2 of 2000 is 1000/600/400, and a digest lands near it rather than on
    # it. Six keys can skew badly; two thousand cannot.
    assert 900 < counts["Commercial, reinforced concrete"] < 1100
    assert 500 < counts["Industrial, steel"] < 700
    assert 300 < counts["Residential, masonry"] < 500
    assert sum(counts.values()) == 2000


def test_the_distribution_names_every_class_even_an_unassigned_one():
    counts = extract.MIXED_COMMERCIAL.distribution([("B-1", "1")])
    assert set(counts) == {item.label for item in extract.MIXED_COMMERCIAL.classes}
    assert sum(counts.values()) == 1


# -- the refusals --------------------------------------------------------------------

def test_an_assumption_with_no_class_is_refused():
    with pytest.raises(AllocationError, match="names no classes"):
        extract.OccupancyAssumption(name="empty_v1", description="", classes=())


def test_a_class_with_no_occupancy_code_is_refused():
    with pytest.raises(AllocationError, match="names no occupancy code"):
        extract.TaxonomyClass("", "5000", "Nothing")


def test_a_class_that_can_never_be_assigned_is_refused():
    with pytest.raises(AllocationError, match="weight 0"):
        extract.TaxonomyClass("1100", "5000", "Never", weight=0)


def test_repeated_class_labels_are_refused():
    """Labels identify a class in the distribution report."""
    with pytest.raises(AllocationError, match="repeats a class label"):
        extract.OccupancyAssumption(
            name="dupe_v1",
            description="",
            classes=(
                extract.TaxonomyClass("1100", "5000", "Same"),
                extract.TaxonomyClass("1150", "5000", "Same"),
            ),
        )


def test_an_unknown_preset_lists_what_exists():
    with pytest.raises(AllocationError, match="not a known occupancy assumption"):
        extract.occupancy_preset("nope")


# -- what each preset claims ---------------------------------------------------------

def test_the_default_is_uniform_rather_than_spread():
    """A spread default would put three priors into every version that never asked."""
    assert extract.DEFAULT_OCCUPANCY.is_uniform
    assert extract.DEFAULT_OCCUPANCY is extract.COMMERCIAL_GENERAL


def test_only_the_assumption_that_asserts_nothing_is_approved():
    approved = {
        name for name, item in extract.OCCUPANCY_PRESETS.items() if item.approved
    }
    assert approved == {"not_reported_v1"}


def test_the_unknown_assumption_writes_oed_unknown():
    codes = extract.NOT_REPORTED.apply(("B-1", "1"))
    assert codes["OccupancyCode"] == extract.UNKNOWN_OCCUPANCY
    assert codes["ConstructionCode"] == extract.UNKNOWN_CONSTRUCTION
    assert extract.NOT_REPORTED.evidence is AllocationEvidence.REPORTED


def test_an_analyst_supplied_assumption_needs_no_release():
    supplied = extract.uniform("warehouse_test_v1", "1150", "5200")
    assert supplied.apply(("B-1", "1")) == {
        "OccupancyCode": "1150",
        "ConstructionCode": "5200",
    }
    assert supplied.approved is False


# -- evidence outranks assumption ------------------------------------------------------

def test_a_stated_occupancy_displaces_the_assumption():
    resolved = extract.assign_taxonomy(
        KEYS,
        extract.COMMERCIAL_GENERAL,
        reported={("B-2", "1"): {"OccupancyCode": "1050", "ConstructionCode": "5100"}},
    )
    assert resolved[("B-2", "1")]["OccupancyCode"] == "1050"
    assert resolved[("B-1", "1")]["OccupancyCode"] == "1100"


def test_a_partly_completed_schedule_is_not_an_all_or_nothing_choice():
    resolved = extract.assign_taxonomy(
        KEYS,
        extract.COMMERCIAL_GENERAL,
        reported={("B-2", "1"): {"OccupancyCode": "1050"}},
    )
    record = extract.taxonomy_record(
        extract.COMMERCIAL_GENERAL,
        resolved,
        reported={("B-2", "1"): {"OccupancyCode": "1050"}},
    )
    assert record["source"] == "mixed_reported_and_assumed"
    assert record["reported_locations"] == 1
    assert record["assumed_locations"] == len(KEYS) - 1


def test_a_fully_stated_schedule_is_recorded_as_reported():
    stated = {key: {"OccupancyCode": "1150", "ConstructionCode": "5200"} for key in KEYS}
    resolved = extract.assign_taxonomy(KEYS, extract.COMMERCIAL_GENERAL, reported=stated)
    record = extract.taxonomy_record(
        extract.COMMERCIAL_GENERAL, resolved, reported=stated
    )
    assert record["source"] == "reported_location_taxonomy"
    assert record["decision_note"] == "reported vulnerability attributes"
    assert set(record["occupancy_counts"]) == {"1150"}


def test_a_blank_stated_code_falls_through_rather_than_blanking_the_row():
    """An empty cell is not an assertion that the occupancy is empty."""
    resolved = extract.assign_taxonomy(
        [("B-1", "1")],
        extract.COMMERCIAL_GENERAL,
        reported={("B-1", "1"): {"OccupancyCode": "", "ConstructionCode": ""}},
    )
    assert resolved[("B-1", "1")]["OccupancyCode"] == "1100"


def test_the_record_says_the_assumption_is_unapproved():
    resolved = extract.assign_taxonomy(KEYS, extract.COMMERCIAL_GENERAL)
    record = extract.taxonomy_record(extract.COMMERCIAL_GENERAL, resolved)
    assert record["source"] == "assumed"
    assert "not an approved prior" in record["basis"]
    assert "commercial_general_v1" in record["decision_note"]


# -- the template carries them too -------------------------------------------------------

def test_the_template_offers_the_taxonomy_columns():
    assert "OccupancyCode" in extract.TEMPLATE_COLUMNS
    assert "ConstructionCode" in extract.TEMPLATE_COLUMNS


def test_a_stated_taxonomy_is_read_back_from_a_completed_template():
    supplied = extract.read_reported_components(
        b"AccNumber,LocNumber,BuildingTIV,OccupancyCode,ConstructionCode\n"
        b"B-1,1,100.00,1150,5200\n"
        b"B-1,2,200.00,,\n"
    )
    assert dict(supplied.taxonomy) == {
        ("B-1", "1"): {"OccupancyCode": "1150", "ConstructionCode": "5200"}
    }


def test_a_construction_with_no_occupancy_is_refused():
    """OED requires an occupancy, and construction alone reaches no function."""
    with pytest.raises(AllocationError, match="construction code with no occupancy"):
        extract.read_reported_components(
            b"AccNumber,LocNumber,BuildingTIV,OccupancyCode,ConstructionCode\n"
            b"B-1,1,100.00,,5200\n"
        )


def test_a_file_without_the_taxonomy_columns_is_still_valid():
    supplied = extract.read_reported_components(
        b"AccNumber,LocNumber,BuildingTIV\nB-1,1,100.00\n"
    )
    assert supplied.taxonomy == {}
