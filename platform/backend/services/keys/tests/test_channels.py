"""Resolving a risk to a class, where a class may be several functions.

The contract this exercises is the one that changed. Resolution used to return
a single row and refuse any tie, which meant a class reaching structures that
respond at different spectral periods could not be expressed at all -- the
converter could build one and the runtime had no way to carry it.

Three properties matter, and they pull in different directions.

**A class is answered whole.** A risk reaching four intensity measures gets
four rows, not the first one in the table, because the other three are also
true of it.

**Height narrows, and only where it was stated.** A schedule that gives a
storey count reaches the band containing it. One that does not reaches the
band that means silence -- never a numbered band, because falling back to one
would invent a height, and never the reverse, because that would discard the
only field that makes the mixture narrower.

**Value is counted once.** A coverage answered through four channels is still
one coverage's worth of money. The reconciliation is the whole point of the
keys contract and channels must not be able to inflate it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cass_core.policy import IMTRepresentation
from cass_keys.lookup import (
    AreaPerilGrid,
    GridCell,
    KeyStatus,
    MappingError,
    VulnerabilityEntry,
    VulnerabilityMapping,
    lookup,
)

MEASURES = frozenset({"PGA", "SA(0.3)", "SA(0.6)", "SA(1.0)"})


def entry(vulnerability_id: int, **overrides) -> VulnerabilityEntry:
    settings = {
        "vulnerability_id": vulnerability_id,
        "coverage_type": 1,
        "required_imt": "SA(0.3)",
        "occupancy_codes": frozenset({"1100"}),
        "label": f"function {vulnerability_id}",
    }
    settings.update(overrides)
    return VulnerabilityEntry(**settings)


def mapping(*entries: VulnerabilityEntry, **overrides) -> VulnerabilityMapping:
    settings = {
        "country_code": "ID",
        "version": "t",
        "entries": entries,
        "supported_imts": MEASURES,
        "imt_representation": IMTRepresentation.CORRELATED_CHANNELS,
    }
    settings.update(overrides)
    return VulnerabilityMapping(**settings)


def band(name: str, low: int | None, high: int | None) -> dict[str, object]:
    return {"storey_band": name, "min_storeys": low, "max_storeys": high}


# -- grouping entries into classes ------------------------------------------------

def test_entries_sharing_a_taxonomy_and_band_are_channels_of_one_class():
    built = mapping(
        entry(1, required_imt="PGA", channel_weight=0.7),
        entry(2, required_imt="SA(1.0)", channel_weight=0.3),
    )
    assert len(built.classes) == 1
    assert built.classes[0].intensity_measures == ("PGA", "SA(1.0)")
    assert built.multi_channel_classes == built.classes


def test_entries_differing_in_taxonomy_are_separate_classes_not_channels():
    built = mapping(
        entry(1, occupancy_codes=frozenset({"1100"})),
        entry(2, occupancy_codes=frozenset({"1050"})),
    )
    assert len(built.classes) == 2
    assert built.multi_channel_classes == ()


def test_a_class_naming_the_same_measure_twice_is_refused():
    """Channels are told apart by their measure, so two of one is row order."""
    with pytest.raises(MappingError, match="the same intensity measure twice"):
        mapping(
            entry(1, required_imt="PGA", channel_weight=0.5),
            entry(2, required_imt="PGA", channel_weight=0.5),
        )


def test_channel_weights_that_do_not_account_for_the_class_are_refused():
    """The shortfall is damage that would vanish with nothing reporting it."""
    with pytest.raises(MappingError, match="sum to"):
        mapping(
            entry(1, required_imt="PGA", channel_weight=0.5),
            entry(2, required_imt="SA(1.0)", channel_weight=0.3),
        )


def test_a_single_function_class_needs_no_weight_stated():
    assert mapping(entry(1)).classes[0].channels[0].channel_weight == 1.0


# -- ambiguity, now between classes rather than rows -------------------------------

def test_two_classes_answering_the_same_risk_equally_well_are_refused():
    with pytest.raises(MappingError, match="depend on table order"):
        mapping(
            entry(1, occupancy_codes=frozenset({"1100"}), label="first"),
            entry(2, occupancy_codes=frozenset({"1100", "1150"}), label="second"),
        )


def test_a_more_specific_class_alongside_a_general_one_is_the_intended_shape():
    built = mapping(
        entry(1, label="general"),
        entry(2, construction_codes=frozenset({"5150"}), label="concrete"),
    )
    assert built.find_channels("1100", "5150", 1)[0].label == "concrete"
    assert built.find_channels("1100", "5000", 1)[0].label == "general"


def test_two_bands_that_cannot_hold_the_same_building_are_not_ambiguous():
    built = mapping(
        entry(1, **band("low", 1, 3)),
        entry(2, **band("mid", 4, 7)),
    )
    assert len(built.classes) == 2


def test_two_bands_that_overlap_are_refused():
    """A five-storey building would reach both, and order would decide."""
    with pytest.raises(MappingError, match="depend on table order"):
        mapping(
            entry(1, **band("low", 1, 6)),
            entry(2, **band("mid", 4, 7)),
        )


def test_the_unstated_band_never_collides_with_a_numbered_one():
    """One answers silence and the other answers a height. No risk is both."""
    built = mapping(
        entry(1, **band("unstated", None, None)),
        entry(2, **band("low", 1, 3)),
    )
    assert len(built.classes) == 2


# -- resolving against height --------------------------------------------------------

@pytest.fixture()
def banded():
    return mapping(
        entry(1, **band("unstated", None, None)),
        entry(2, **band("low", 1, 3)),
        entry(3, **band("mid", 4, 7)),
        entry(4, **band("high", 8, None)),
    )


@pytest.mark.parametrize(
    ("storeys", "expected"),
    [(None, 1), (1, 2), (3, 2), (4, 3), (7, 3), (8, 4), (40, 4)],
)
def test_a_risk_reaches_the_band_holding_its_height(banded, storeys, expected):
    channels = banded.find_channels("1100", "", 1, storeys)
    assert [item.vulnerability_id for item in channels] == [expected]


def test_a_stated_height_does_not_fall_back_to_the_unstated_band(banded):
    """Falling back would discard the field that makes the mixture narrower."""
    assert banded.find_channels("1100", "", 1, storeys=2)[0].vulnerability_id == 2


def test_a_risk_with_no_height_does_not_reach_a_numbered_band():
    """Reaching one would invent a height the schedule never gave."""
    numbered = mapping(entry(1, **band("low", 1, 3)))
    assert numbered.find_channels("1100", "", 1, storeys=None) == ()


def test_a_mapping_that_states_no_band_answers_whatever_the_risk_says():
    """A routing table with no reason to distinguish height should not have to."""
    agnostic = mapping(entry(1))
    assert agnostic.find_channels("1100", "", 1, storeys=None)
    assert agnostic.find_channels("1100", "", 1, storeys=12)


def test_a_height_outside_every_band_reaches_nothing():
    """Zero storeys is not a building this class set describes."""
    numbered = mapping(entry(1, **band("low", 1, 3)))
    assert numbered.find_channels("1100", "", 1, storeys=0) == ()


# -- through the lookup ---------------------------------------------------------------

GRID = AreaPerilGrid(
    country_code="ID",
    version="t",
    cells=(
        GridCell(1, Decimal("-7"), Decimal("-6"), Decimal("106"), Decimal("107"), "ID"),
    ),
)


def location(**overrides) -> dict[str, str]:
    row = {
        "AccNumber": "A1",
        "LocNumber": "1",
        "Latitude": "-6.2",
        "Longitude": "106.8",
        "OccupancyCode": "1100",
        "ConstructionCode": "5150",
        "LocPerilsCovered": "QEQ",
        "BuildingTIV": "1000000",
        "OtherTIV": "0",
        "ContentsTIV": "0",
        "BITIV": "0",
    }
    row.update(overrides)
    return row


def spanning(**overrides) -> VulnerabilityMapping:
    return mapping(
        entry(1, required_imt="PGA", channel_weight=0.6),
        entry(2, required_imt="SA(1.0)", channel_weight=0.4),
        **overrides,
    )


def test_a_multi_channel_class_produces_one_row_per_measure():
    result = lookup([location()], grid=GRID, vulnerability=spanning())
    building = [item for item in result.records if item.coverage_type == 1]
    assert len(building) == 2
    assert all(item.status is KeyStatus.SUCCESS for item in building)
    assert [item.imt for item in building] == ["PGA", "SA(1.0)"]
    assert [item.channel_weight for item in building] == [0.6, 0.4]
    assert [item.channel_index for item in building] == [1, 2]


def test_the_value_of_a_multi_channel_coverage_is_still_counted_once():
    """Four channels on one coverage is one coverage's worth of money."""
    result = lookup([location()], grid=GRID, vulnerability=spanning())
    assert result.report.reconciled
    assert result.report.mapped_tiv == Decimal("1000000")
    assert result.report.source_tiv == Decimal("1000000")


def test_a_multi_channel_class_is_refused_while_the_representation_is_undecided():
    """Choosing one silently is the failure section 15 names."""
    result = lookup(
        [location()],
        grid=GRID,
        vulnerability=spanning(imt_representation=IMTRepresentation.UNDECIDED),
    )
    building = [item for item in result.records if item.coverage_type == 1]
    assert len(building) == 1
    assert building[0].status is KeyStatus.FAIL_V
    assert "PGA, SA(1.0)" in building[0].message
    assert "no multi-IMT representation has been approved" in building[0].message


def test_the_refused_value_is_reported_rather_than_lost():
    """It is the number the decision is waiting on, so it has to be visible."""
    result = lookup(
        [location()],
        grid=GRID,
        vulnerability=spanning(imt_representation=IMTRepresentation.UNDECIDED),
    )
    assert result.report.reconciled
    assert result.report.failed_tiv == Decimal("1000000")
    assert any("cannot be one function" in reason for reason in result.report.by_reason)


def test_a_representation_that_resolves_the_class_elsewhere_still_refuses_here():
    """A custom GUL component does not make the keys contract able to answer."""
    result = lookup(
        [location()],
        grid=GRID,
        vulnerability=spanning(imt_representation=IMTRepresentation.CUSTOM_GUL),
    )
    building = next(item for item in result.records if item.coverage_type == 1)
    assert building.status is KeyStatus.FAIL_V
    assert "outside the keys contract" in building.message


def test_a_single_channel_class_is_answered_whatever_the_representation_is():
    """It needs no representation, so an undecided one must not block it."""
    result = lookup(
        [location()],
        grid=GRID,
        vulnerability=mapping(entry(1), imt_representation=IMTRepresentation.UNDECIDED),
    )
    building = next(item for item in result.records if item.coverage_type == 1)
    assert building.status is KeyStatus.SUCCESS
    assert building.channel_count == 1


def test_a_class_demanding_an_unsupported_measure_is_reported_not_rerouted():
    narrow = mapping(
        entry(1, required_imt="PGA"), supported_imts=frozenset({"SA(0.3)"})
    )
    result = lookup([location()], grid=GRID, vulnerability=narrow)
    building = next(item for item in result.records if item.coverage_type == 1)
    assert building.status is KeyStatus.FAIL_V
    assert "requires PGA" in building.message


def test_the_lookup_reads_the_storey_count_off_the_oed_row():
    banded = mapping(
        entry(1, **band("unstated", None, None)),
        entry(2, **band("mid", 4, 7)),
    )
    result = lookup(
        [location(NumberOfStoreys="5")], grid=GRID, vulnerability=banded
    )
    building = next(item for item in result.records if item.coverage_type == 1)
    assert building.vulnerability_id == 2


def test_a_storey_count_that_is_not_a_number_is_read_as_unstated():
    """The safe reading of an uninterpretable height is that it is unknown."""
    banded = mapping(
        entry(1, **band("unstated", None, None)),
        entry(2, **band("mid", 4, 7)),
    )
    result = lookup(
        [location(NumberOfStoreys="about six")], grid=GRID, vulnerability=banded
    )
    building = next(item for item in result.records if item.coverage_type == 1)
    assert building.vulnerability_id == 1


def test_a_storey_count_of_zero_is_read_as_unstated():
    """OED's default for the field, and what oasislmf writes where CASS left it blank.

    The engine's lookup reads the row after oasislmf has filled its defaults, so
    reading 0 as a height would fail every risk without one in the engine and
    answer it in CASS -- two lookups over one book disagreeing about every row.
    """
    banded = mapping(
        entry(1, **band("unstated", None, None)),
        entry(2, **band("low", 1, 3)),
    )
    for stated in ("0", 0):
        result = lookup(
            [location(NumberOfStoreys=stated)], grid=GRID, vulnerability=banded
        )
        building = next(item for item in result.records if item.coverage_type == 1)
        assert building.status is KeyStatus.SUCCESS
        assert building.vulnerability_id == 1


def test_the_result_records_which_representation_answered_it():
    """A keys file has to be readable without finding the registry record."""
    result = lookup([location()], grid=GRID, vulnerability=spanning())
    assert result.imt_representation == "correlated_channels"
    # One coverage carries value and the class answers it through two measures.
    assert result.as_dict()["multi_channel_success_count"] == 2
