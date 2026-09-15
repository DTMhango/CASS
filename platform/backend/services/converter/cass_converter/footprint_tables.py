"""The footprint as files, written as the rows stream past.

The accumulator in :mod:`.footprint` streams: it holds one event's counts and
emits that event's rows as soon as the next begins. Until this module existed
every caller undid that immediately, collecting the rows it emitted into one
list and then copying that list into a tuple.

That is affordable for a region and not for a country. A footprint row is a
frozen slotted dataclass of eighty bytes including its pointer, and Indonesia's
grid is fifty-five times the Jakarta-Bandung domain the conversion had been run
on: four hundred to seven hundred and fifty million rows, thirty to sixty
gigabytes of interpreter objects, for a table whose only destination is a file.
Section 15 names that failure -- large files passing through in one piece --
and the reader had already been written to avoid it.

So the rows go straight to disk, one CSV per measure, and everything the
conversion reports about them is counted on the way past. Three properties make
that possible without a second pass:

* **The stream is already sorted.** The accumulator emits events in ascending
  order and, within an event, iterates ``sorted((area_peril_id, imt))`` and then
  ascending bins. Filtered to one measure that is exactly event, cell, bin --
  the order an Oasis footprint wants. Nothing is sorted here, and the writer
  refuses a row that arrives out of order rather than trusting the guarantee.
* **Every per-row check is per-group**, and a group -- one event, one cell, one
  measure -- is contiguous in that stream. Probabilities summing to one, and a
  bin appearing twice, are decided as each group closes.
* **Every aggregate is bounded by something other than the row count**: the set
  of events, the set of cells, a count per measure.

What is left in memory is a few megabytes whatever the footprint's size.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from .footprint import (
    PROBABILITY_TOLERANCE,
    FootprintError,
    FootprintRow,
    event_coverage,
)

#: The header every footprint CSV carries, and the column order Oasis reads.
FOOTPRINT_HEADER = "event_id,areaperil_id,intensity_bin_id,probability\n"

#: Decimal places a probability is written to. Matches what the table-building
#: code wrote before the footprint was streamed, so a file produced either way
#: is byte for byte the same.
PROBABILITY_FORMAT = ".8f"


def measure_stem(imt: str) -> str:
    """The filename-safe form of a measure, as the Oasis tables name it."""
    return imt.replace("(", "").replace(")", "").replace(".", "p")


class FootprintTableWriter:
    """Writes footprint rows to one CSV per measure, checking as they pass."""

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._handles: dict[str, Any] = {}
        self._rows_by_measure: dict[str, int] = {}

        # Bounded by the event set and the grid rather than by the footprint.
        self._events: set[int] = set()
        self._cells: set[int] = set()
        self._problems: list[str] = []

        # The group currently open: one event, cell and measure, closed and
        # judged as soon as a row belonging to a different one arrives.
        self._group: tuple[int, int, str] | None = None
        self._group_total = 0.0
        self._group_bins: set[int] = set()
        self._worst: tuple[float, str] = (0.0, "the footprint is empty")
        self._last: tuple[int, int, str, int] | None = None
        self._closed = False

    def add(self, row: FootprintRow) -> None:
        """Write one row, judging it against the group it belongs to."""
        if self._closed:
            raise FootprintError("this footprint has already been closed")

        order = (row.event_id, row.area_peril_id, row.imt, row.intensity_bin_id)
        if self._last is not None and order <= self._last:
            raise FootprintError(
                f"footprint rows arrived out of order: {order} after {self._last}. "
                "Rows are written straight to their file in the order the "
                "accumulator emits them, so an unsorted row would produce a table "
                "the engine reads as a different footprint."
            )
        self._last = order

        key = (row.event_id, row.area_peril_id, row.imt)
        if key != self._group:
            self._close_group()
            self._group = key
            self._group_total = 0.0
            self._group_bins = set()

        if not 0.0 <= row.probability <= 1.0:
            self._problems.append(
                f"event {row.event_id}, cell {row.area_peril_id}, {row.imt}: "
                f"probability {row.probability} is outside [0, 1]"
            )
        if row.intensity_bin_id in self._group_bins:
            self._problems.append(
                f"event {row.event_id}, cell {row.area_peril_id}, {row.imt}: an "
                "intensity bin appears more than once"
            )
        self._group_bins.add(row.intensity_bin_id)
        self._group_total += float(row.probability)

        self._events.add(row.event_id)
        self._cells.add(row.area_peril_id)
        self._rows_by_measure[row.imt] = self._rows_by_measure.get(row.imt, 0) + 1
        self._handle(row.imt).write(
            f"{row.event_id},{row.area_peril_id},{row.intensity_bin_id},"
            f"{row.probability:{PROBABILITY_FORMAT}}\n"
        )

    def extend(self, rows: Iterable[FootprintRow]) -> None:
        for row in rows:
            self.add(row)

    def _handle(self, imt: str):
        handle = self._handles.get(imt)
        if handle is None:
            path = self._directory / f"footprint_{measure_stem(imt)}.csv"
            handle = path.open("w", encoding="utf-8", newline="")
            handle.write(FOOTPRINT_HEADER)
            self._handles[imt] = handle
        return handle

    def _close_group(self) -> None:
        if self._group is None:
            return
        event_id, area_peril_id, imt = self._group
        distance = abs(self._group_total - 1.0)
        if distance > PROBABILITY_TOLERANCE:
            self._problems.append(
                f"event {event_id}, cell {area_peril_id}, {imt}: probabilities sum "
                f"to {self._group_total:.9f} rather than 1"
            )
        # ``>=`` so that a footprint whose every group is exact still names the
        # group it measured, rather than reporting an empty table.
        if distance >= self._worst[0]:
            self._worst = (
                distance,
                f"event {event_id} at cell {area_peril_id} on {imt} sums to "
                f"{self._group_total:.6f}",
            )
        self._group = None

    def close(
        self,
        *,
        expected_event_ids: Iterable[int] = (),
        workspace: Any = None,
    ) -> FootprintTables:
        """Finish every file and return the footprint with its summary."""
        self._close_group()
        for handle in self._handles.values():
            handle.close()
        self._handles = {}
        self._closed = True
        return FootprintTables(
            directory=self._directory,
            rows_by_measure=dict(self._rows_by_measure),
            cells=frozenset(self._cells),
            events=frozenset(self._events),
            problems=tuple(self._problems),
            probability_distance=self._worst,
            coverage=event_coverage(self._events, expected_event_ids),
            workspace=workspace,
        )


@dataclasses.dataclass(frozen=True, slots=True)
class FootprintTables:
    """A footprint held as one CSV per measure, with the summary of its writing.

    Every number here was counted as the rows went past, so nothing downstream
    reads the table back to learn how large it is, which events or cells it
    touched, or whether its probabilities are sound. Reading it back is still
    possible -- iterating yields the rows again -- and exists for small sets and
    for tests rather than for a national footprint.
    """

    directory: Path
    rows_by_measure: Mapping[str, int]
    cells: frozenset[int]
    events: frozenset[int]
    problems: tuple[str, ...]
    probability_distance: tuple[float, str]
    coverage: Mapping[str, Any]
    #: The temporary directory these files live in, where one was made for
    #: them. Held only so that it outlives the footprint that points into it:
    #: dropping the reference would clean the directory away while a hazard
    #: set still named its files.
    workspace: Any = None

    def __len__(self) -> int:
        return sum(self.rows_by_measure.values())

    @property
    def imts(self) -> tuple[str, ...]:
        return tuple(sorted(self.rows_by_measure))

    def path_for(self, imt: str) -> Path:
        path = self.directory / f"footprint_{measure_stem(imt)}.csv"
        if not path.is_file():
            raise FootprintError(
                f"This hazard set has no rows for {imt}. It carries "
                f"{', '.join(self.imts) or 'nothing'}."
            )
        return path

    def paths(self) -> dict[str, Path]:
        """Every footprint file, keyed by the name it is stored under."""
        return {
            f"footprint_{measure_stem(imt)}.csv": self.path_for(imt)
            for imt in self.imts
        }

    def csv_bytes(self, imt: str) -> bytes:
        """One measure's table, read whole.

        For small sets and for tests. A national footprint is gigabytes and is
        uploaded from its path instead.
        """
        return self.path_for(imt).read_bytes()

    def rows_for(self, imt: str) -> Iterator[FootprintRow]:
        """Read one measure's rows back from its file."""
        with self.path_for(imt).open(encoding="utf-8") as handle:
            next(handle, None)  # the header
            for line in handle:
                event, cell, bin_id, probability = line.rstrip("\n").split(",")
                yield FootprintRow(
                    event_id=int(event),
                    area_peril_id=int(cell),
                    imt=imt,
                    intensity_bin_id=int(bin_id),
                    probability=float(probability),
                )

    def __iter__(self) -> Iterator[FootprintRow]:
        for imt in self.imts:
            yield from self.rows_for(imt)
