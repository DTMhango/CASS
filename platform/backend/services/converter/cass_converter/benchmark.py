"""Comparing the hazard CASS converted against a published curve.

The hazard pipeline's last stage is a benchmark, and section 7 is specific
about what it compares: the hazard the platform will actually use against
curves somebody has approved. That is not the same as comparing two OpenQuake
runs. What reaches a loss is the footprint -- ground motion binned, joined to
an occurrence table -- so the curve derived here is derived from those two
tables and nothing else. A benchmark that read the engine's own hazard curves
would be checking the engine against itself and would pass a conversion that
had quietly lost half the shaking on the way into the bins.

The derivation is the standard one. For one cell and one measure, the annual
rate of exceeding an intensity is the sum over events of the event's annual
rate times its probability of exceeding that intensity, and the return period
is the reciprocal. Two things about it are worth stating because they are where
a comparison like this usually goes wrong:

* an event's annual rate is its number of occurrences over the effective time,
  not one over the number of events. A national set run for a thousand years
  contains events that occur more than once;
* an intensity bin is an interval, and a probability sitting in it is not a
  measurement at its lower edge. Exceedance is evaluated at bin boundaries,
  where the answer needs no assumption about the shape inside a bin, and the
  intensity at a requested return period is interpolated between two boundaries
  in log-rate space.

Nothing here decides whether a conversion is acceptable. It produces the
numbers and the ratios; the tolerance and the approval are somebody's decision,
and section 7 keeps them that way.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .bins import IntensityBinSet


class BenchmarkError(Exception):
    """Raised when a comparison cannot be made honestly."""


@dataclasses.dataclass(frozen=True, slots=True)
class CurvePoint:
    """One point of a hazard curve: an intensity and how often it is exceeded."""

    intensity: float
    annual_rate: float

    @property
    def return_period(self) -> float | None:
        """Years between exceedances, or nothing where it is never exceeded."""
        return 1.0 / self.annual_rate if self.annual_rate > 0 else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "intensity": self.intensity,
            "annual_rate": self.annual_rate,
            "return_period": self.return_period,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class BenchmarkPoint:
    """One published expectation: an intensity at a return period."""

    area_peril_id: int
    imt: str
    return_period: float
    intensity: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "areaperil_id": self.area_peril_id,
            "imt": self.imt,
            "return_period": self.return_period,
            "intensity": self.intensity,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class Comparison:
    """What the converted hazard says where a benchmark says something."""

    point: BenchmarkPoint
    converted_intensity: float | None
    ratio: float | None
    within_tolerance: bool | None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.point.as_dict(),
            "converted_intensity": self.converted_intensity,
            "ratio": self.ratio,
            "within_tolerance": self.within_tolerance,
            "note": self.note,
        }


def event_rates(
    occurrences: Iterable[Any], *, effective_time: float
) -> dict[int, float]:
    """Each event's annual rate: its occurrences over the effective time.

    Not one over the number of events. A set run for a thousand years contains
    events that occur in more than one period, and treating every event as
    equally frequent would flatten exactly the part of the hazard a benchmark
    is checking.
    """
    if effective_time <= 0:
        raise BenchmarkError(
            f"The effective time is {effective_time}, so no annual rate can be "
            "derived from this event set."
        )
    counted: dict[int, int] = {}
    for row in occurrences:
        counted[row.event_id] = counted.get(row.event_id, 0) + 1
    return {event: total / effective_time for event, total in counted.items()}


def hazard_curve(
    footprint: Iterable[Any],
    *,
    occurrences: Iterable[Any],
    effective_time: float,
    bins: IntensityBinSet,
    area_peril_id: int,
    imt: str,
) -> tuple[CurvePoint, ...]:
    """The exceedance curve at one cell, derived from the footprint itself.

    Evaluated at bin boundaries, where exceedance needs no assumption about how
    probability is distributed inside a bin.
    """
    rates = event_rates(occurrences, effective_time=effective_time)
    ordered = sorted(bins.bins, key=lambda item: item.bin_index)
    boundaries = [float(item.lower) for item in ordered]

    # Probability that each event exceeds each boundary, accumulated from the
    # top bin down so every bin is counted once.
    by_event: dict[int, dict[int, float]] = {}
    for row in footprint:
        if row.area_peril_id != area_peril_id or row.imt != imt:
            continue
        by_event.setdefault(row.event_id, {})[row.intensity_bin_id] = (
            by_event.setdefault(row.event_id, {}).get(row.intensity_bin_id, 0.0)
            + float(row.probability)
        )

    curve: list[CurvePoint] = []
    for position, boundary in enumerate(boundaries):
        rate = 0.0
        for event, distribution in by_event.items():
            exceeding = sum(
                probability
                for bin_id, probability in distribution.items()
                # A bin exceeds the boundary when the boundary is at or below
                # the bin's own lower edge: everything in it is above.
                if _lower_of(ordered, bin_id) >= boundary
            )
            if exceeding:
                rate += rates.get(event, 0.0) * exceeding
        curve.append(CurvePoint(intensity=boundary, annual_rate=rate))
        if position == len(boundaries) - 1:
            # The top boundary is the last one that can be exceeded; the upper
            # edge of the top bin is where the dictionary stops describing the
            # hazard rather than where the hazard stops.
            break
    return tuple(curve)


def _lower_of(ordered: Sequence[Any], bin_id: int) -> float:
    for item in ordered:
        if item.bin_index == bin_id:
            return float(item.lower)
    raise BenchmarkError(
        f"The footprint refers to intensity bin {bin_id}, which the dictionary it "
        "was built against does not define. One of the two is from another "
        "conversion."
    )


def intensity_at(curve: Sequence[CurvePoint], return_period: float) -> float | None:
    """The intensity exceeded once in ``return_period`` years.

    Interpolated in log-rate space between the two boundaries that bracket the
    rate, which is how a hazard curve is read. Returns nothing where the
    requested period lies outside what the event set can say: a thousand-year
    set has nothing to say about a ten-thousand-year return period, and an
    extrapolated answer would be a number the calculation never produced.
    """
    if return_period <= 0:
        raise BenchmarkError("A return period must be positive.")
    wanted = 1.0 / return_period

    usable = [point for point in curve if point.annual_rate > 0]
    if not usable:
        return None
    if wanted > usable[0].annual_rate:
        # More frequent than anything the curve reaches: the answer is below
        # the bottom of the intensity dictionary, which describes nothing.
        return None
    if wanted < usable[-1].annual_rate:
        # Rarer than the top boundary is still exceeded at. The answer lies
        # above where the dictionary stops describing the hazard, and an
        # extrapolated one would be a number the calculation never produced.
        return None

    # The intensity exceeded at least this often is the highest one that is,
    # which is not the first: an exceedance curve is flat wherever no event
    # falls between two boundaries, and reading the flat section from its left
    # edge understates the hazard by the width of the flat.
    last = max(
        position
        for position, point in enumerate(usable)
        if point.annual_rate >= wanted
    )
    if last == len(usable) - 1:
        return usable[last].intensity

    earlier, later = usable[last], usable[last + 1]
    if earlier.annual_rate == later.annual_rate:
        return later.intensity
    span = math.log(earlier.annual_rate) - math.log(later.annual_rate)
    share = (math.log(earlier.annual_rate) - math.log(wanted)) / span
    return earlier.intensity + share * (later.intensity - earlier.intensity)


def compare(
    footprint: Iterable[Any],
    *,
    occurrences: Iterable[Any],
    effective_time: float,
    intensity_bins: Mapping[str, IntensityBinSet],
    points: Sequence[BenchmarkPoint],
    tolerance: float | None = None,
) -> dict[str, Any]:
    """Compare the converted hazard against every published point.

    ``tolerance`` is the permitted proportional difference, and it is optional
    on purpose: where nobody has approved one, the comparison still produces
    every ratio and says that acceptance is undecided rather than passing
    itself.
    """
    rows = list(footprint)
    occurrence_rows = list(occurrences)
    comparisons: list[Comparison] = []

    for point in points:
        bins = intensity_bins.get(point.imt)
        if bins is None:
            comparisons.append(
                Comparison(
                    point=point,
                    converted_intensity=None,
                    ratio=None,
                    within_tolerance=None,
                    note=(
                        f"The conversion carries no intensity bins for {point.imt}, so "
                        "there is nothing to compare at this point."
                    ),
                )
            )
            continue

        curve = hazard_curve(
            rows,
            occurrences=occurrence_rows,
            effective_time=effective_time,
            bins=bins,
            area_peril_id=point.area_peril_id,
            imt=point.imt,
        )
        converted = intensity_at(curve, point.return_period)
        if converted is None:
            comparisons.append(
                Comparison(
                    point=point,
                    converted_intensity=None,
                    ratio=None,
                    within_tolerance=None,
                    note=(
                        f"The event set says nothing at a {point.return_period:g}-year "
                        "return period for this cell: it is outside the range the "
                        "effective time and the intensity dictionary can describe."
                    ),
                )
            )
            continue

        ratio = converted / point.intensity if point.intensity else None
        within = (
            None
            if tolerance is None or ratio is None
            else abs(ratio - 1.0) <= tolerance
        )
        comparisons.append(
            Comparison(
                point=point,
                converted_intensity=converted,
                ratio=ratio,
                within_tolerance=within,
            )
        )

    compared = [item for item in comparisons if item.ratio is not None]
    outside = [item for item in comparisons if item.within_tolerance is False]
    return {
        "points": [item.as_dict() for item in comparisons],
        "compared": len(compared),
        "not_comparable": len(comparisons) - len(compared),
        "outside_tolerance": len(outside),
        "tolerance": tolerance,
        # Acceptance is a decision, and a comparison with no approved tolerance
        # has not made it. Saying "passed" here would turn the gate section 7
        # requires into a formality.
        "decided": tolerance is not None,
        "passed": bool(compared) and not outside if tolerance is not None else None,
        "mean_ratio": (
            sum(item.ratio for item in compared) / len(compared) if compared else None
        ),
    }
