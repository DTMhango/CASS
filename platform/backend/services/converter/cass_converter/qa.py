"""The measurements a conversion has to pass before anybody relies on it.

Section 7 makes scientific QA a gate with recorded tolerances, and the gate had
nothing behind it: a conversion reported how many rows it wrote and stopped.
This produces the numbers a reviewer needs -- and, where somebody has approved
tolerances, compares them.

What it measures is the set of ways a conversion can be wrong without failing.

A footprint whose bin probabilities do not sum to one at a cell has either lost
probability or double-counted it, and either way the loss at that cell is wrong
by that share. Ground motion above the top bin is discarded, which understates
exactly the strongest shaking. An event in the occurrence table with no
footprint row contributes a period with no loss, which is usually correct and
occasionally a truncated read. And annual frequency has to survive the
conversion exactly, because everything downstream is a rate.

Nothing here decides acceptance. A measurement with no approved tolerance is
reported as undecided, because a gate that passes itself is not a gate.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from typing import Any

#: What an approved tolerance set may state, and what each one guards.
TOLERANCE_KEYS: Mapping[str, str] = {
    "probability_sum": (
        "How far the bin probabilities at one event, cell and measure may sum "
        "away from one."
    ),
    "clipped_share": (
        "The share of ground-motion samples that may fall above the top "
        "intensity bin and be discarded."
    ),
    "unfootprinted_event_share": (
        "The share of events in the occurrence table that may produce no "
        "footprint row, which is ordinary for a national set over a regional "
        "grid and suspicious for a national one."
    ),
}


class QaError(Exception):
    """Raised when a conversion cannot be measured."""


@dataclasses.dataclass(frozen=True, slots=True)
class Measurement:
    """One measured quantity, and what it is allowed to be."""

    key: str
    label: str
    value: float
    tolerance: float | None
    #: Where a measurement is a count rather than a share, this carries it, so
    #: a reviewer reads "3 of 27,313" rather than a bare proportion.
    detail: str = ""

    @property
    def within_tolerance(self) -> bool | None:
        if self.tolerance is None:
            return None
        return self.value <= self.tolerance

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.key,
            "label": self.label,
            "value": self.value,
            "tolerance": self.tolerance,
            "within_tolerance": self.within_tolerance,
            "detail": self.detail,
            "guards": TOLERANCE_KEYS.get(self.key, ""),
        }


def probability_sums(footprint: Iterable[Any]) -> tuple[float, str]:
    """The largest distance from one, over every event, cell and measure.

    Taken as a maximum rather than a mean: one cell whose probabilities sum to
    0.6 is a cell losing 40% of its loss, and an average over a national
    footprint would bury it.
    """
    totals: dict[tuple[int, int, str], float] = {}
    for row in footprint:
        key = (row.event_id, row.area_peril_id, row.imt)
        totals[key] = totals.get(key, 0.0) + float(row.probability)
    if not totals:
        return 0.0, "the footprint is empty"

    worst_key, worst = max(totals.items(), key=lambda item: abs(item[1] - 1.0))
    event, cell, imt = worst_key
    return (
        abs(worst - 1.0),
        f"event {event} at cell {cell} on {imt} sums to {worst:.6f}",
    )


def measure(
    hazard: Any, *, tolerances: Mapping[str, float] | None = None
) -> dict[str, Any]:
    """Measure one converted hazard set, and compare where a tolerance exists."""
    stated = dict(tolerances or {})
    unknown = sorted(set(stated) - set(TOLERANCE_KEYS))
    if unknown:
        raise QaError(
            f"The tolerance set states {', '.join(unknown)}, which this conversion "
            "does not measure. Known: " + ", ".join(sorted(TOLERANCE_KEYS)) + "."
        )

    metrics = hazard.metrics
    distance, detail = probability_sums(hazard.footprint)

    read = metrics.samples_read or 0
    clipped = metrics.samples_above_range or 0
    coverage = dict(hazard.coverage or {})
    expected = int(coverage.get("events_expected") or len(hazard.events) or 0)
    without = int(coverage.get("events_without_footprint") or 0)

    measurements = [
        Measurement(
            key="probability_sum",
            label="Furthest a cell's bin probabilities sum from one",
            value=distance,
            tolerance=stated.get("probability_sum"),
            detail=detail,
        ),
        Measurement(
            key="clipped_share",
            label="Ground motion discarded above the top intensity bin",
            value=(clipped / read) if read else 0.0,
            tolerance=stated.get("clipped_share"),
            detail=f"{clipped} of {read} samples",
        ),
        Measurement(
            key="unfootprinted_event_share",
            label="Events in the occurrence table with no footprint row",
            value=(without / expected) if expected else 0.0,
            tolerance=stated.get("unfootprinted_event_share"),
            detail=f"{without} of {expected} events",
        ),
    ]

    decided = [item for item in measurements if item.within_tolerance is not None]
    failed = [item for item in measurements if item.within_tolerance is False]

    return {
        "checks": [item.as_dict() for item in measurements],
        "measured": len(measurements),
        "decided": len(decided) == len(measurements) and bool(measurements),
        "failed": [item.key for item in failed],
        # The conversion's own hard refusals -- a footprint that does not
        # validate, frequency that was not preserved -- are separate from this
        # and have already stopped the build. What is here is the graded part.
        "problems": list(hazard.problems),
        "passed": (
            None
            if len(decided) != len(measurements) or not measurements
            else not failed and not hazard.problems
        ),
    }
