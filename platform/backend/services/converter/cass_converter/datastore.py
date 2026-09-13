"""Reading an OpenQuake datastore, rather than the CSV it can export.

Section 7 asks the converter to read the HDF5 datastore in chunks. The CSV
exports were the way in while the pipeline was being built, and they have two
problems at national scale: the engine has to write a second copy of the ground
motion before CASS reads any of it, and a text file of hundreds of millions of
rows is slow to parse and easy to truncate without noticing. The datastore is
the engine's own record, already written, and it can be read a slice at a time.

Written against a real 3.23 datastore rather than from documentation, because
the details that decide whether a footprint is right are not the ones the
manual emphasises:

* ``gmf_data`` holds parallel columns -- ``eid``, ``sid`` and ``gmv_0`` upwards
  -- and the attribute ``imts`` names the measures in the order those ``gmv_``
  columns run. The mapping is positional, so it is read rather than assumed;
* the attributes also carry ``investigation_time``, ``effective_time`` and
  ``num_events``, which is where annual frequency comes from;
* ``events`` carries the year each event fell in, which is an Oasis period;
* sites are identified by ``custom_site_id`` where the job supplied one, and
  that is what CASS grids use as the area peril;
* and the rows are in neither event nor site order. They arrive as the workers
  wrote them, which is the fact that shapes everything below.

That last point matters because the footprint accumulator takes one complete
event at a time. Rather than sorting hundreds of millions of rows or holding
them all, this reads events in batches under a stated row budget: it counts the
rows per event once, groups events into batches that fit, and scans for each
batch. Memory stays bounded by the budget rather than by the size of the
calculation, and the cost is a scan per batch, which is what makes a national
run possible on a worker that has not got the whole GMF in memory.
"""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Iterator, Mapping, Sequence
from decimal import Decimal
from typing import Any

from .footprint import GroundMotionSample
from .occurrence import OccurrenceRow

#: How many ground-motion rows one batch may hold in memory at once. Two
#: million rows of (event, site, four measures) is on the order of fifty
#: megabytes, which a worker can carry while leaving room for the accumulator.
DEFAULT_ROW_BUDGET = 2_000_000

#: How many rows are read from a column in one slice. Independent of the batch
#: budget: this bounds the read, that bounds what is held.
DEFAULT_CHUNK = 500_000

#: Significant digits kept when a 32-bit ground-motion value becomes a decimal.
#: The engine stores single precision and its own CSV export writes about this
#: many; carrying the full binary expansion of a float32 would state a
#: precision the calculation never had.
VALUE_DIGITS = 6


class DatastoreError(Exception):
    """Raised when a datastore cannot be read faithfully."""


def _modules():
    """h5py and numpy, or a refusal that says how to get them.

    Imported here rather than at module scope so that installing the converter
    without its ``hdf5`` extra stays a working installation -- everything else
    in the package reads text -- and asking for a datastore without them is a
    clear message instead of an ImportError at the top of an unrelated stack.
    """
    try:
        import h5py
        import numpy
    except ImportError as exc:  # pragma: no cover - exercised by the extra
        raise DatastoreError(
            "Reading an OpenQuake datastore needs h5py and numpy. Install the "
            "converter with its hdf5 extra: pip install 'cass-converter[hdf5]'."
        ) from exc
    return h5py, numpy


@dataclasses.dataclass(frozen=True, slots=True)
class DatastoreMetadata:
    """What the calculation says about itself.

    ``investigation_time`` and ``ses_per_logic_tree_path`` decide annual
    frequency, and the datastore states the product directly as
    ``effective_time``. All three are carried: a reader that recomputed one from
    the others would be offering a second opinion about frequency, and the
    engine's is the one that matches the hazard.
    """

    imts: tuple[str, ...]
    investigation_time: float | None
    effective_time: float | None
    event_count: int
    site_count: int
    row_count: int

    @property
    def ses_per_logic_tree_path(self) -> int | None:
        """Simulations behind the effective time, where both are stated."""
        if not self.investigation_time or not self.effective_time:
            return None
        ratio = self.effective_time / self.investigation_time
        rounded = round(ratio)
        return rounded if abs(ratio - rounded) < 1e-6 else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "imts": list(self.imts),
            "investigation_time": self.investigation_time,
            "effective_time": self.effective_time,
            "ses_per_logic_tree_path": self.ses_per_logic_tree_path,
            "event_count": self.event_count,
            "site_count": self.site_count,
            "row_count": self.row_count,
        }


def _open(path: str | pathlib.Path):
    h5py, _ = _modules()
    location = pathlib.Path(path)
    if not location.is_file():
        raise DatastoreError(f"{location} is not a file CASS can read.")
    try:
        return h5py.File(location, "r")
    except OSError as exc:
        raise DatastoreError(
            f"{location} is not a readable HDF5 datastore: {exc}"
        ) from exc


def _gmf(store) -> Any:
    group = store.get("gmf_data")
    if group is None:
        raise DatastoreError(
            "This datastore holds no gmf_data, so it is not an event-based "
            "calculation with ground motion in it."
        )
    return group


def _imts(group) -> tuple[str, ...]:
    stated = group.attrs.get("imts")
    if isinstance(stated, bytes):
        stated = stated.decode("utf-8")
    names = tuple(str(stated).split()) if stated else ()
    if not names:
        raise DatastoreError(
            "gmf_data does not name its intensity measures, so the gmv_ columns "
            "cannot be matched to the measures they hold."
        )
    return names


def metadata(path: str | pathlib.Path) -> DatastoreMetadata:
    """What the calculation carries, read from its own attributes."""
    with _open(path) as store:
        group = _gmf(store)
        names = _imts(group)
        events = store.get("events")
        sites = store.get("sitecol/sids")

        def attribute(key: str) -> float | None:
            value = group.attrs.get(key)
            return float(value) if value is not None else None

        return DatastoreMetadata(
            imts=names,
            investigation_time=attribute("investigation_time"),
            effective_time=attribute("effective_time"),
            event_count=int(group.attrs.get("num_events", len(events) if events is not None else 0)),
            site_count=int(len(sites)) if sites is not None else 0,
            row_count=int(len(group["eid"])),
        )


def site_keys(path: str | pathlib.Path) -> dict[int, str]:
    """Site id to the key a grid maps, which is ``custom_site_id`` where present.

    A calculation run on a CASS grid carries the area peril identifier as the
    custom site id, which is what makes the join to the grid exact rather than
    a coordinate comparison.
    """
    with _open(path) as store:
        sites = store.get("sitecol")
        if sites is None:
            raise DatastoreError("This datastore holds no site collection.")
        ids = sites["sids"][:]
        custom = sites.get("custom_site_id")
        if custom is None:
            return {int(item): str(int(item)) for item in ids}
        values = custom[:]
        return {
            int(site): value.decode("utf-8") if isinstance(value, bytes) else str(value)
            for site, value in zip(ids, values, strict=True)
        }


def events(path: str | pathlib.Path) -> tuple[OccurrenceRow, ...]:
    """The occurrence table: each event in the year it fell in.

    OpenQuake assigns the year, which is exactly an Oasis period, so this is a
    rename rather than a calculation. Any arithmetic here would be a second
    opinion about frequency.
    """
    with _open(path) as store:
        table = store.get("events")
        if table is None:
            raise DatastoreError("This datastore holds no events table.")
        if "year" not in (table.dtype.names or ()):
            raise DatastoreError(
                "The events table carries no year, so no Oasis period can be "
                "derived from it without inventing one."
            )
        rows = table[:]
        return tuple(
            OccurrenceRow(event_id=int(row["id"]), period_no=int(row["year"]))
            for row in rows
        )


def _value(number: Any) -> Decimal:
    """One ground-motion value as a decimal of the precision it was stored at."""
    return Decimal(f"{float(number):.{VALUE_DIGITS}g}")


def read_ground_motion(
    path: str | pathlib.Path,
    *,
    area_perils: Mapping[str, int],
    imts: Sequence[str] | None = None,
    row_budget: int = DEFAULT_ROW_BUDGET,
    chunk: int = DEFAULT_CHUNK,
) -> Iterator[GroundMotionSample]:
    """Stream the ground motion, one complete event at a time.

    ``area_perils`` maps a site key to the area peril it stands for, as the CSV
    reader does. A key the mapping does not carry is refused rather than
    skipped: ground motion with nowhere to go is hazard being dropped, and a
    footprint short of a cell understates every loss at it silently.

    Events are emitted in ascending order, and every row of an event is emitted
    before the next event begins, which is what the footprint accumulator
    requires. Rows are read in slices and held only for the events of the batch
    in hand, so memory follows the budget rather than the calculation.
    """
    _, numpy = _modules()

    with _open(path) as store:
        group = _gmf(store)
        available = _imts(group)
        wanted = tuple(imts) if imts is not None else available
        missing = sorted(set(wanted) - set(available))
        if missing:
            raise DatastoreError(
                f"{pathlib.Path(path).name} does not carry {', '.join(missing)}. It "
                f"has {', '.join(available)}. A vulnerability function built for a "
                "measure the hazard did not produce cannot be answered by rerouting "
                "it to one that was not built for it."
            )
        columns = {name: f"gmv_{available.index(name)}" for name in wanted}

        sites = site_keys(path)
        peril_by_site: dict[int, int] = {}
        for site, key in sites.items():
            try:
                peril_by_site[site] = area_perils[key]
            except KeyError:
                raise DatastoreError(
                    f"site {key!r} has no area peril. Every site in the calculation "
                    "must map to a cell of the grid it was run on, or its ground "
                    "motion is dropped."
                ) from None

        eid = group["eid"]
        sid = group["sid"]
        rows = int(len(eid))

        # One pass to learn how many rows each event has, which is what lets a
        # batch be sized before anything is held.
        counts: dict[int, int] = {}
        for start in range(0, rows, chunk):
            values, totals = numpy.unique(eid[start : start + chunk], return_counts=True)
            for event, total in zip(values.tolist(), totals.tolist(), strict=True):
                counts[event] = counts.get(event, 0) + total

        for batch in _batches(sorted(counts), counts, row_budget):
            wanted_events = numpy.array(batch, dtype=numpy.int64)
            # Held as array slices rather than a Python object per row. A row
            # budget of two million dictionaries would be hundreds of megabytes
            # of interpreter overhead against forty of actual measurement, which
            # would make the budget describe something other than the memory.
            held: dict[int, list[tuple[Any, dict[str, Any]]]] = {
                event: [] for event in batch
            }

            for start in range(0, rows, chunk):
                stop = min(start + chunk, rows)
                events_here = eid[start:stop].astype(numpy.int64)
                mask = numpy.isin(events_here, wanted_events)
                if not mask.any():
                    continue
                selected_events = events_here[mask]
                # Grouped within the slice, so each event's rows are one
                # contiguous view rather than a scatter of indices.
                order = numpy.argsort(selected_events, kind="stable")
                ordered_events = selected_events[order]
                sites_here = sid[start:stop][mask][order]
                measured = {
                    name: group[column][start:stop][mask][order]
                    for name, column in columns.items()
                }
                found, first, totals = numpy.unique(
                    ordered_events, return_index=True, return_counts=True
                )
                for event, begin, total in zip(
                    found.tolist(), first.tolist(), totals.tolist(), strict=True
                ):
                    end = begin + total
                    held[event].append(
                        (
                            sites_here[begin:end],
                            {name: values[begin:end] for name, values in measured.items()},
                        )
                    )

            for event in batch:
                for sites_slice, measurements in held[event]:
                    for position in range(len(sites_slice)):
                        site = int(sites_slice[position])
                        for name in wanted:
                            value = measurements[name][position]
                            if value is None or float(value) <= 0:
                                continue
                            yield GroundMotionSample(
                                event_id=event,
                                area_peril_id=peril_by_site[site],
                                imt=name,
                                value=_value(value),
                            )


def _batches(
    order: Sequence[int], counts: Mapping[int, int], row_budget: int
) -> Iterator[list[int]]:
    """Group events into runs that fit the budget, keeping them in order.

    An event whose own rows exceed the budget still forms a batch of its own:
    the accumulator needs it whole, so the budget bends for one event rather
    than the event being split across two.
    """
    batch: list[int] = []
    held = 0
    for event in order:
        size = counts[event]
        if batch and held + size > row_budget:
            yield batch
            batch, held = [], 0
        batch.append(event)
        held += size
    if batch:
        yield batch
