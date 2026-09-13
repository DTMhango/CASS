"""The conversion QA measurements.

What these hold to account is the shape of the gate rather than the numbers in
it. A measurement with no approved tolerance must read as undecided: a gate
that passes itself is not a gate, and section 7 makes acceptance a decision
somebody takes.

The one measurement worth arguing about is the probability sum, which is taken
as the worst case rather than the average. A national footprint averaging 0.999
can still hold one cell at 0.6, and that cell's loss is 40% short.
"""

from __future__ import annotations

import dataclasses

import pytest

from cass_converter.qa import QaError, measure, probability_sums


@dataclasses.dataclass(frozen=True)
class Row:
    event_id: int
    area_peril_id: int
    imt: str
    intensity_bin_id: int
    probability: float


@dataclasses.dataclass
class Metrics:
    samples_read: int = 1000
    samples_above_range: int = 0


@dataclasses.dataclass
class Hazard:
    footprint: tuple
    metrics: Metrics
    coverage: dict
    problems: tuple = ()
    events: tuple = ()


def hazard(**overrides):
    stated = {
        "footprint": (
            Row(1, 7, "PGA", 1, 0.6),
            Row(1, 7, "PGA", 2, 0.4),
            Row(1, 8, "PGA", 1, 1.0),
        ),
        "metrics": Metrics(),
        "coverage": {"events_expected": 100, "events_without_footprint": 2},
    }
    stated.update(overrides)
    return Hazard(**stated)


def test_the_worst_cell_is_what_the_probability_check_reports():
    """An average would bury one cell losing 40% of its loss."""
    distance, detail = probability_sums(
        (Row(1, 7, "PGA", 1, 1.0), Row(1, 8, "PGA", 1, 0.6))
    )

    assert distance == pytest.approx(0.4)
    assert "cell 8" in detail


def test_a_measurement_with_no_approved_tolerance_is_undecided():
    report = measure(hazard())

    assert report["decided"] is False
    assert report["passed"] is None
    assert all(check["within_tolerance"] is None for check in report["checks"])


def test_every_measurement_within_its_tolerance_passes():
    report = measure(
        hazard(),
        tolerances={
            "probability_sum": 0.001,
            "clipped_share": 0.01,
            "unfootprinted_event_share": 0.10,
        },
    )

    assert report["decided"] is True
    assert report["passed"] is True


def test_discarded_ground_motion_above_the_top_bin_fails_its_tolerance():
    report = measure(
        hazard(metrics=Metrics(samples_read=1000, samples_above_range=50)),
        tolerances={
            "probability_sum": 0.001,
            "clipped_share": 0.01,
            "unfootprinted_event_share": 0.10,
        },
    )

    assert report["passed"] is False
    assert report["failed"] == ["clipped_share"]
    clipped = next(item for item in report["checks"] if item["check"] == "clipped_share")
    assert clipped["value"] == pytest.approx(0.05)
    assert clipped["detail"] == "50 of 1000 samples"


def test_a_conversion_carrying_a_problem_does_not_pass_on_measurements_alone():
    report = measure(
        hazard(problems=("The footprint does not validate.",)),
        tolerances={
            "probability_sum": 0.001,
            "clipped_share": 0.01,
            "unfootprinted_event_share": 0.10,
        },
    )

    assert report["passed"] is False
    assert report["problems"] == ["The footprint does not validate."]


def test_a_tolerance_for_something_the_conversion_does_not_measure_is_refused():
    """An approved tolerance nobody checks is worse than none: it reads as covered."""
    with pytest.raises(QaError, match="does not measure"):
        measure(hazard(), tolerances={"something_else": 0.1})
