"""Deriving a hazard curve from the footprint, and comparing it.

The benchmark gate exists to catch a conversion that lost hazard on the way
into the bins, so the curve has to come from the tables that reach the engine
rather than from the engine that produced them. These tests are mostly
arithmetic, and deliberately so: every one of them is a number worked out by
hand, because a curve that is wrong in the same way as the code that made it
would agree with itself forever.

Two of them are the errors this kind of comparison usually makes. An event that
occurs twice in a thousand years is twice as frequent as one that occurs once,
and treating every event as equally likely flattens exactly the part of the
hazard being checked. And a return period the event set cannot describe gets no
answer rather than an extrapolated one.
"""

from __future__ import annotations

import dataclasses

import pytest

from cass_converter.benchmark import (
    BenchmarkError,
    BenchmarkPoint,
    compare,
    event_rates,
    hazard_curve,
    intensity_at,
)
from cass_converter.bins import IntensityBinSet, linear_bins


@dataclasses.dataclass(frozen=True)
class Row:
    event_id: int
    area_peril_id: int
    imt: str
    intensity_bin_id: int
    probability: float


@dataclasses.dataclass(frozen=True)
class Occurrence:
    event_id: int
    period_no: int


#: Four bins spanning 0 to 1: [0,0.25), [0.25,0.5), [0.5,0.75), [0.75,1].
BINS = IntensityBinSet(imt="PGA", version="1", bins=linear_bins("0", "1", 4))


def test_an_event_that_occurs_twice_is_twice_as_frequent():
    rates = event_rates(
        [Occurrence(1, 10), Occurrence(1, 400), Occurrence(2, 55)],
        effective_time=1000,
    )

    assert rates == {1: 0.002, 2: 0.001}


def test_an_event_set_with_no_effective_time_has_no_rates():
    with pytest.raises(BenchmarkError, match="no annual rate"):
        event_rates([Occurrence(1, 1)], effective_time=0)


def test_the_curve_is_the_rate_of_exceeding_each_bin_boundary():
    """One event a year at 0.5-0.75, and one every two years at 0.75-1."""
    footprint = [
        Row(1, 7, "PGA", 3, 1.0),
        Row(2, 7, "PGA", 4, 1.0),
    ]
    occurrences = [Occurrence(1, year) for year in range(1, 11)] + [
        Occurrence(2, year) for year in (2, 4, 6, 8, 10)
    ]

    curve = hazard_curve(
        footprint,
        occurrences=occurrences,
        effective_time=10,
        bins=BINS,
        area_peril_id=7,
        imt="PGA",
    )

    rates = {point.intensity: point.annual_rate for point in curve}
    # Above 0 and above 0.25: both events, so 1.0 + 0.5 a year.
    assert rates[0.0] == pytest.approx(1.5)
    assert rates[0.25] == pytest.approx(1.5)
    # Above 0.5: both, since the first event's bin starts there.
    assert rates[0.5] == pytest.approx(1.5)
    # Above 0.75: only the second event.
    assert rates[0.75] == pytest.approx(0.5)


def test_a_partial_probability_contributes_only_its_share():
    footprint = [
        Row(1, 7, "PGA", 2, 0.75),
        Row(1, 7, "PGA", 4, 0.25),
    ]

    curve = hazard_curve(
        footprint,
        occurrences=[Occurrence(1, 1)],
        effective_time=100,
        bins=BINS,
        area_peril_id=7,
        imt="PGA",
    )

    rates = {point.intensity: point.annual_rate for point in curve}
    assert rates[0.25] == pytest.approx(0.01)
    assert rates[0.75] == pytest.approx(0.0025)


def test_another_cell_does_not_reach_this_curve():
    footprint = [Row(1, 7, "PGA", 4, 1.0), Row(1, 8, "PGA", 4, 1.0)]

    curve = hazard_curve(
        footprint,
        occurrences=[Occurrence(1, 1)],
        effective_time=100,
        bins=BINS,
        area_peril_id=8,
        imt="PGA",
    )

    assert {point.intensity: point.annual_rate for point in curve}[0.75] == pytest.approx(
        0.01
    )


def test_the_intensity_at_a_return_period_is_interpolated_between_boundaries():
    footprint = [Row(event, 7, "PGA", 2 if event % 2 else 4, 1.0) for event in range(1, 5)]
    occurrences = [Occurrence(event, event) for event in range(1, 5)]

    curve = hazard_curve(
        footprint,
        occurrences=occurrences,
        effective_time=100,
        bins=BINS,
        area_peril_id=7,
        imt="PGA",
    )
    found = intensity_at(curve, 50)

    # Two of the four events exceed 0.75 (rate 0.02) and all four exceed 0.25
    # (rate 0.04); one in fifty years is 0.02, which is the 0.75 boundary.
    assert found == pytest.approx(0.75)


def test_a_return_period_the_event_set_cannot_describe_gets_no_answer():
    """A thousand-year set says nothing about ten thousand years."""
    footprint = [Row(1, 7, "PGA", 4, 1.0)]
    curve = hazard_curve(
        footprint,
        occurrences=[Occurrence(1, 1)],
        effective_time=1000,
        bins=BINS,
        area_peril_id=7,
        imt="PGA",
    )

    assert intensity_at(curve, 10_000) is None


def test_a_comparison_without_an_approved_tolerance_decides_nothing():
    """Section 7 keeps acceptance a decision, so the report must not pass itself."""
    footprint = [Row(1, 7, "PGA", 4, 1.0)]
    report = compare(
        footprint,
        occurrences=[Occurrence(1, 1)],
        effective_time=100,
        intensity_bins={"PGA": BINS},
        points=[BenchmarkPoint(7, "PGA", 100, 0.75)],
    )

    assert report["decided"] is False
    assert report["passed"] is None
    assert report["compared"] == 1
    assert report["points"][0]["ratio"] == pytest.approx(1.0)


def test_a_comparison_within_an_approved_tolerance_passes():
    footprint = [Row(1, 7, "PGA", 4, 1.0)]
    report = compare(
        footprint,
        occurrences=[Occurrence(1, 1)],
        effective_time=100,
        intensity_bins={"PGA": BINS},
        points=[BenchmarkPoint(7, "PGA", 100, 0.70)],
        tolerance=0.10,
    )

    assert report["decided"] is True
    assert report["passed"] is True
    assert report["points"][0]["within_tolerance"] is True


def test_a_comparison_outside_the_tolerance_fails_and_says_by_how_much():
    footprint = [Row(1, 7, "PGA", 4, 1.0)]
    report = compare(
        footprint,
        occurrences=[Occurrence(1, 1)],
        effective_time=100,
        intensity_bins={"PGA": BINS},
        points=[BenchmarkPoint(7, "PGA", 100, 0.40)],
        tolerance=0.10,
    )

    assert report["passed"] is False
    assert report["outside_tolerance"] == 1
    assert report["points"][0]["ratio"] == pytest.approx(0.75 / 0.40)


def test_a_measure_the_conversion_does_not_carry_is_reported_not_skipped():
    report = compare(
        [Row(1, 7, "PGA", 4, 1.0)],
        occurrences=[Occurrence(1, 1)],
        effective_time=100,
        intensity_bins={"PGA": BINS},
        points=[BenchmarkPoint(7, "SA(1.0)", 475, 0.3)],
        tolerance=0.10,
    )

    assert report["not_comparable"] == 1
    assert "no intensity bins for SA(1.0)" in report["points"][0]["note"]
    # Nothing was compared, so nothing passed.
    assert report["passed"] is False


def test_a_footprint_bin_the_dictionary_does_not_define_is_refused():
    """Two conversions mixed together would compare one against the other's bins."""
    with pytest.raises(BenchmarkError, match="does not define"):
        hazard_curve(
            [Row(1, 7, "PGA", 99, 1.0)],
            occurrences=[Occurrence(1, 1)],
            effective_time=100,
            bins=BINS,
            area_peril_id=7,
            imt="PGA",
        )
