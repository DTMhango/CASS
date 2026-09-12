"""Footprint construction: ground motion to intensity-bin probabilities.

Section 7 requires the converter to chunk by event and site without loading the
complete dataset, to validate probabilities and identifier coverage, and to
report memory and throughput. Section 15 names the failure mode: large files
passing through in one piece, causing memory pressure and fragile workflows.

So the accumulator here is streaming. It takes ground-motion samples one at a
time, holds only the counts for the events currently open, and emits completed
footprint rows as soon as an event closes.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Iterator
from decimal import Decimal
from typing import Any

from .bins import IntensityBinSet

#: Probabilities are compared at this tolerance. They are ratios of sample
#: counts, so they are exact in principle but pass through float division.
PROBABILITY_TOLERANCE = 1e-9


class FootprintError(Exception):
    """Raised when a footprint would be invalid."""


@dataclasses.dataclass(frozen=True, slots=True)
class FootprintRow:
    """One footprint entry: an event, a cell, an IMT and a bin probability."""

    event_id: int
    area_peril_id: int
    imt: str
    intensity_bin_id: int
    probability: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "areaperil_id": self.area_peril_id,
            "imt": self.imt,
            "intensity_bin_id": self.intensity_bin_id,
            "probability": self.probability,
        }


@dataclasses.dataclass
class ConversionMetrics:
    """Throughput and coverage, for the conversion report of section 7."""

    samples_read: int = 0
    samples_binned: int = 0
    #: Motion below the lowest bin. Benign, and usually most of what is
    #: dropped: it is ground shaking too weak to damage anything, and omitting
    #: it is what keeps a footprint from carrying a row per site per event.
    samples_below_range: int = 0
    #: Motion above the highest bin. Never benign. It is the strongest shaking
    #: the calculation produced and it is being discarded, so every loss at
    #: that cell is understated -- and by exactly the events that drive the
    #: tail. Counted separately because a single number covering both would
    #: hide this behind the harmless case.
    samples_above_range: int = 0
    events_emitted: int = 0
    rows_emitted: int = 0
    cells_seen: set[int] = dataclasses.field(default_factory=set)
    imts_seen: set[str] = dataclasses.field(default_factory=set)
    peak_open_events: int = 0

    @property
    def samples_out_of_range(self) -> int:
        return self.samples_below_range + self.samples_above_range

    @property
    def out_of_range_share(self) -> float:
        if self.samples_read == 0:
            return 0.0
        return self.samples_out_of_range / self.samples_read

    @property
    def above_range_share(self) -> float:
        """The share that matters. Anything above zero is a finding."""
        if self.samples_read == 0:
            return 0.0
        return self.samples_above_range / self.samples_read

    @property
    def clips_the_hazard(self) -> bool:
        """Whether the intensity dictionary is too narrow for this hazard."""
        return self.samples_above_range > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "samples_read": self.samples_read,
            "samples_binned": self.samples_binned,
            "samples_below_range": self.samples_below_range,
            "samples_above_range": self.samples_above_range,
            "samples_out_of_range": self.samples_out_of_range,
            "out_of_range_share": self.out_of_range_share,
            "above_range_share": self.above_range_share,
            "clips_the_hazard": self.clips_the_hazard,
            "events_emitted": self.events_emitted,
            "rows_emitted": self.rows_emitted,
            "distinct_cells": len(self.cells_seen),
            "imts": sorted(self.imts_seen),
            "peak_open_events": self.peak_open_events,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class GroundMotionSample:
    """One ground-motion value at one site, for one event and IMT."""

    event_id: int
    area_peril_id: int
    imt: str
    value: Decimal


class FootprintAccumulator:
    """Turns ground-motion samples into intensity-bin probabilities.

    The probability held against a bin is the share of that event's samples at
    that cell and IMT which fell in the bin. Where an event has one sample per
    site, that is 1.0 in a single bin -- a deterministic footprint. Where it has
    many, the spread across bins is what carries the ground-motion variability
    into Oasis.

    Samples must arrive grouped by event. That is how OpenQuake exports them,
    and it is what lets this hold only one event at a time.
    """

    def __init__(
        self,
        bin_sets: dict[str, IntensityBinSet],
        *,
        drop_below: float = 0.0,
    ) -> None:
        if not bin_sets:
            raise FootprintError("no intensity bin sets were supplied")
        self._bin_sets = bin_sets
        # Bins holding a negligible share can be dropped to keep the footprint
        # small, but the dropped mass is redistributed rather than discarded,
        # so probabilities still sum to one.
        self._drop_below = drop_below
        self._current_event: int | None = None
        self._counts: dict[tuple[int, str], dict[int, int]] = {}
        self.metrics = ConversionMetrics()

    def _bin_set(self, imt: str) -> IntensityBinSet:
        try:
            return self._bin_sets[imt]
        except KeyError as exc:
            raise FootprintError(
                f"no intensity bin set is defined for {imt}; the conversion policy "
                "must declare every IMT it emits"
            ) from exc

    def add(self, sample: GroundMotionSample) -> Iterator[FootprintRow]:
        """Add one sample, emitting the previous event once it closes."""
        self.metrics.samples_read += 1

        if self._current_event is not None and sample.event_id != self._current_event:
            if sample.event_id < self._current_event:
                raise FootprintError(
                    f"samples are not grouped by event: {sample.event_id} arrived "
                    f"after {self._current_event}"
                )
            yield from self._flush()

        self._current_event = sample.event_id

        bin_set = self._bin_set(sample.imt)
        found = bin_set.find(sample.value)
        if found is None:
            # Out of range is counted and reported, never clamped: a value
            # above the dictionary means the dictionary is wrong for this
            # hazard, and silently pinning it to the top bin would hide that.
            #
            # Which side it fell off is recorded, because the two are opposite
            # findings. Below the floor is motion too weak to damage anything
            # and dropping it is how a footprint stays a manageable size.
            # Above the ceiling is the strongest shaking in the calculation
            # going missing.
            if sample.value < bin_set.bins[0].lower:
                self.metrics.samples_below_range += 1
            else:
                self.metrics.samples_above_range += 1
            return

        key = (sample.area_peril_id, sample.imt)
        self._counts.setdefault(key, {})
        self._counts[key][found.bin_index] = self._counts[key].get(found.bin_index, 0) + 1

        self.metrics.samples_binned += 1
        self.metrics.cells_seen.add(sample.area_peril_id)
        self.metrics.imts_seen.add(sample.imt)
        self.metrics.peak_open_events = max(self.metrics.peak_open_events, 1)

    def _flush(self) -> Iterator[FootprintRow]:
        """Emit the rows for the event currently held."""
        if self._current_event is None:
            return

        event_id = self._current_event
        for (area_peril_id, imt), counts in sorted(self._counts.items()):
            total = sum(counts.values())
            if total == 0:
                continue

            kept = {
                bin_index: count
                for bin_index, count in counts.items()
                if count / total >= self._drop_below
            }
            if not kept:
                kept = counts
            kept_total = sum(kept.values())

            for bin_index in sorted(kept):
                yield FootprintRow(
                    event_id=event_id,
                    area_peril_id=area_peril_id,
                    imt=imt,
                    intensity_bin_id=bin_index,
                    probability=kept[bin_index] / kept_total,
                )
                self.metrics.rows_emitted += 1

        self.metrics.events_emitted += 1
        self._counts = {}
        self._current_event = None

    def close(self) -> Iterator[FootprintRow]:
        """Emit the final event. Must be called, or its rows are lost."""
        yield from self._flush()


def build_footprint(
    samples: Iterable[GroundMotionSample],
    bin_sets: dict[str, IntensityBinSet],
    *,
    drop_below: float = 0.0,
) -> tuple[list[FootprintRow], ConversionMetrics]:
    """Convenience wrapper that materialises a small footprint.

    Production conversions stream through the accumulator instead; this exists
    for fixtures, tests and the golden vertical slice, where the whole
    footprint comfortably fits in memory.
    """
    accumulator = FootprintAccumulator(bin_sets, drop_below=drop_below)
    rows: list[FootprintRow] = []
    for sample in samples:
        rows.extend(accumulator.add(sample))
    rows.extend(accumulator.close())
    return rows, accumulator.metrics


def validate_footprint(rows: Iterable[FootprintRow]) -> list[str]:
    """Check the properties section 7 requires before a package is accepted.

    Returns the problems found rather than raising, so a conversion report can
    list all of them at once.
    """
    problems: list[str] = []
    groups: dict[tuple[int, int, str], list[FootprintRow]] = {}

    for row in rows:
        if not 0.0 <= row.probability <= 1.0:
            problems.append(
                f"event {row.event_id}, cell {row.area_peril_id}, {row.imt}: "
                f"probability {row.probability} is outside [0, 1]"
            )
        groups.setdefault((row.event_id, row.area_peril_id, row.imt), []).append(row)

    for (event_id, area_peril_id, imt), group in sorted(groups.items()):
        total = sum(item.probability for item in group)
        if abs(total - 1.0) > PROBABILITY_TOLERANCE:
            problems.append(
                f"event {event_id}, cell {area_peril_id}, {imt}: probabilities sum "
                f"to {total:.9f} rather than 1"
            )
        bins = [item.intensity_bin_id for item in group]
        if len(bins) != len(set(bins)):
            problems.append(
                f"event {event_id}, cell {area_peril_id}, {imt}: an intensity bin "
                "appears more than once"
            )

    return problems


def check_event_coverage(
    rows: Iterable[FootprintRow],
    expected_event_ids: Iterable[int],
) -> dict[str, Any]:
    """How the event set and the footprint line up, and which gaps matter.

    Two things can disagree here and only one of them is a defect.

    An event in the set with **no footprint rows** is usually not a defect at
    all. A national source model produces earthquakes across the whole country
    and a footprint covers the cells a portfolio sits in, so a rupture off
    Sulawesi does nothing in Jakarta and correctly contributes an occurrence
    with no loss. It is reported as a share rather than a problem, because the
    number is worth seeing -- if it is *most* of the set, the grid and the
    source model may not be describing the same place.

    A footprint row for an event **not in the set** is always a defect: it is
    loss with no occurrence behind it, so it contributes damage at no frequency
    and nothing downstream will notice.
    """
    present = {row.event_id for row in rows}
    expected = set(expected_event_ids)

    silent = sorted(expected - present)
    unknown = sorted(present - expected)

    problems: list[str] = []
    if unknown:
        problems.append(
            f"{len(unknown)} footprint event(s) are not in the event set, "
            f"starting with {unknown[:5]}. Loss with no occurrence behind it "
            "contributes damage at no frequency."
        )

    return {
        "events_expected": len(expected),
        "events_with_footprint": len(expected & present),
        "events_without_footprint": len(silent),
        "silent_share": len(silent) / len(expected) if expected else 0.0,
        "silent_examples": silent[:5],
        "unknown_events": len(unknown),
        "problems": problems,
        "note": (
            "An event with no footprint rows caused no ground motion above the "
            "minimum intensity at any cell in this domain. For a national source "
            "model over a regional grid that is the ordinary case and not a "
            "defect: the event occurred and did nothing here."
        ),
    }
