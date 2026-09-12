"""Reading OpenQuake event-based output into the converter's own shapes.

The footprint and occurrence machinery has existed for a while and had nothing
to read. This is the missing half: it turns what the engine actually exports
into ``GroundMotionSample`` and ``OccurrenceRow``, so a real calculation can
become a real Oasis footprint.

Written against observed output from engine 3.23.4 rather than from memory,
because the details that matter are not the ones documentation emphasises:

* every exported CSV opens with a ``#`` comment line carrying the engine
  version, a checksum and -- on the ruptures file -- the investigation time and
  stochastic event set count. That line is provenance, not noise, and it is
  parsed rather than skipped;
* ground-motion rows arrive already grouped by event and in ascending order,
  which is the property that lets the footprint accumulator hold one event at a
  time. It is checked rather than assumed, because a future export that
  reordered them would otherwise produce a silently truncated footprint;
* a site is identified by ``custom_site_id`` when the job supplies one and by
  ``site_id`` otherwise, and the two need different joins.

The strictness here is deliberate and one-directional. Anything that would
quietly *lose* hazard -- a ground-motion row at a site with no area peril, an
intensity measure the job produced but the conversion does not carry, a
realisation count that would make the effective time wrong -- is refused.
Section 15 names silently omitted exposure as the failure to prevent; hazard
that never reaches a footprint is the same failure one step earlier.
"""

from __future__ import annotations

import csv
import dataclasses
import pathlib
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from decimal import Decimal
from typing import Any

from .footprint import GroundMotionSample
from .occurrence import OccurrenceRow

#: Prefix OpenQuake gives a ground-motion column: ``gmv_PGA``, ``gmv_SA(0.3)``.
GMV_PREFIX = "gmv_"

#: Fields parsed out of the ``#`` provenance line each export carries.
_HEADER_FIELD = re.compile(r"(\w+)=('[^']*'|[-\d.eE+]+)")


class OpenQuakeError(Exception):
    """Raised when OpenQuake output cannot be converted faithfully."""


@dataclasses.dataclass(frozen=True, slots=True)
class CalculationMetadata:
    """Provenance and timing, read from the export header.

    ``investigation_time`` and ``ses_per_logic_tree_path`` are the two numbers
    an annual frequency depends on, and getting either wrong scales every AAL
    computed from the result by exactly their ratio with nothing downstream
    noticing. They are read from the file rather than passed in for that
    reason: a caller retyping them is a caller who can mistype them.
    """

    engine_version: str = ""
    checksum: str = ""
    start_date: str = ""
    investigation_time: float | None = None
    ses_per_logic_tree_path: int | None = None
    realization_count: int = 1

    @property
    def effective_time(self) -> float:
        """Years the event set represents, and so the number of Oasis periods.

        One stochastic event set covers the investigation time; the job runs
        several per logic-tree path, and the engine numbers each event's year
        across the whole span. Multiplying by the realisation count as well
        would double-count: a sampled realisation is an alternative history of
        the same span, not more of it.
        """
        if self.investigation_time is None or self.ses_per_logic_tree_path is None:
            raise OpenQuakeError(
                "The export header carries no investigation time or stochastic "
                "event set count, so the effective time cannot be derived. The "
                "ruptures export is the one that states them."
            )
        if self.investigation_time <= 0 or self.ses_per_logic_tree_path <= 0:
            raise OpenQuakeError(
                f"Investigation time {self.investigation_time} and event set count "
                f"{self.ses_per_logic_tree_path} must both be positive."
            )
        return self.investigation_time * self.ses_per_logic_tree_path

    @property
    def period_count(self) -> int:
        effective = self.effective_time
        if effective != int(effective):
            raise OpenQuakeError(
                f"The effective time is {effective} years, which is not a whole "
                "number of Oasis periods. A period is a year and a fractional one "
                "has no meaning in an occurrence table."
            )
        return int(effective)

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine_version": self.engine_version,
            "checksum": self.checksum,
            "start_date": self.start_date,
            "investigation_time": self.investigation_time,
            "ses_per_logic_tree_path": self.ses_per_logic_tree_path,
            "realization_count": self.realization_count,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class Event:
    """One event of the stochastic set, as OpenQuake numbers it."""

    event_id: int
    rupture_id: int
    realization_id: int
    year: int
    ses_id: int


def _lines(path: pathlib.Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise OpenQuakeError(f"{path} could not be read: {exc}") from exc


def _rows(lines: Sequence[str]) -> Iterator[dict[str, str]]:
    """Every data row, with the provenance comment left out."""
    yield from csv.DictReader(line for line in lines if not line.startswith("#"))


def read_metadata(*paths: str | pathlib.Path) -> CalculationMetadata:
    """Merge the provenance lines of one or more exports.

    Several files are accepted because the fields are spread across them: the
    engine version is on all of them, the investigation time and event set
    count only on the ruptures export. Passing several and letting this join
    them is better than making the caller know which file holds what.
    """
    found: dict[str, str] = {}
    checksums: set[str] = set()
    for item in paths:
        path = pathlib.Path(item)
        lines = _lines(path)
        if not lines or not lines[0].startswith("#"):
            continue
        pairs = dict(_HEADER_FIELD.findall(lines[0]))
        if "checksum" in pairs:
            checksums.add(pairs["checksum"])
        found.update(pairs)

    if len(checksums) > 1:
        raise OpenQuakeError(
            f"These exports carry different checksums ({sorted(checksums)}), so "
            "they come from different calculations. Converting them together "
            "would attach one calculation's ground motion to another's events."
        )

    def text(key: str) -> str:
        return found.get(key, "").strip("'")

    def number(key: str) -> float | None:
        raw = found.get(key)
        return float(raw) if raw else None

    ses = number("ses_per_logic_tree_path")
    return CalculationMetadata(
        engine_version=text("generated_by"),
        checksum=text("checksum"),
        start_date=text("start_date"),
        investigation_time=number("investigation_time"),
        ses_per_logic_tree_path=int(ses) if ses is not None else None,
    )


def read_realization_count(path: str | pathlib.Path) -> int:
    """How many logic-tree realisations the calculation produced."""
    return sum(1 for _ in _rows(_lines(pathlib.Path(path))))


def read_sites(path: str | pathlib.Path) -> dict[str, tuple[float, float]]:
    """The site mesh: site key to longitude and latitude."""
    sites: dict[str, tuple[float, float]] = {}
    for row in _rows(_lines(pathlib.Path(path))):
        key = (row.get("custom_site_id") or row.get("site_id") or "").strip()
        if not key:
            continue
        sites[key] = (float(row["lon"]), float(row["lat"]))
    if not sites:
        raise OpenQuakeError(f"{path} defines no sites.")
    return sites


def read_events(path: str | pathlib.Path) -> tuple[Event, ...]:
    """The event set, in the order the engine wrote it."""
    events = tuple(
        Event(
            event_id=int(row["event_id"]),
            rupture_id=int(row["rup_id"]),
            realization_id=int(row.get("rlz_id", 0)),
            year=int(row["year"]),
            ses_id=int(row.get("ses_id", 0)),
        )
        for row in _rows(_lines(pathlib.Path(path)))
    )
    if not events:
        raise OpenQuakeError(f"{path} defines no events.")

    identifiers = [item.event_id for item in events]
    if len(set(identifiers)) != len(identifiers):
        raise OpenQuakeError(
            f"{path} repeats an event identifier. Every event must be distinct or "
            "an occurrence table built from it would count one twice."
        )
    return events


def measures(path: str | pathlib.Path) -> tuple[str, ...]:
    """The intensity measures a ground-motion export carries."""
    lines = _lines(pathlib.Path(path))
    header = next((line for line in lines if not line.startswith("#")), "")
    return tuple(
        column[len(GMV_PREFIX) :]
        for column in header.split(",")
        if column.startswith(GMV_PREFIX)
    )


def read_ground_motion(
    path: str | pathlib.Path,
    *,
    area_perils: Mapping[str, int],
    imts: Sequence[str] | None = None,
) -> Iterator[GroundMotionSample]:
    """Stream a ground-motion export as samples the accumulator can take.

    ``area_perils`` maps each site key -- ``custom_site_id`` where the job
    supplied one, ``site_id`` otherwise -- to the area peril the site stands
    for. A key the mapping does not carry is refused rather than skipped: a
    ground-motion value with nowhere to go is hazard being dropped, and a
    footprint short of a cell understates every loss at it without saying so.

    ``imts`` narrows the export to the measures the conversion carries. Asking
    for one the export does not have is refused; the reverse is not, because a
    calculation covering more measures than one conversion needs is ordinary.
    """
    path = pathlib.Path(path)
    available = measures(path)
    if not available:
        raise OpenQuakeError(
            f"{path} carries no {GMV_PREFIX}* columns, so it is not a ground-motion "
            "export."
        )

    wanted = tuple(imts) if imts is not None else available
    missing = sorted(set(wanted) - set(available))
    if missing:
        raise OpenQuakeError(
            f"{path} does not carry {', '.join(missing)}. It has "
            f"{', '.join(available)}. A vulnerability function built for a measure "
            "the hazard does not produce cannot be answered by rerouting it to one "
            "that was not built for it."
        )

    previous: int | None = None
    for row in _rows(_lines(path)):
        event_id = int(row["event_id"])
        if previous is not None and event_id < previous:
            raise OpenQuakeError(
                f"{path} is not grouped by event: {event_id} arrived after "
                f"{previous}. The footprint accumulator holds one event at a time "
                "and would emit two partial footprints for this one."
            )
        previous = event_id

        key = (row.get("custom_site_id") or row.get("site_id") or "").strip()
        try:
            area_peril_id = area_perils[key]
        except KeyError:
            raise OpenQuakeError(
                f"{path}: site {key!r} has no area peril. Every site in the "
                "calculation must map to a cell of the grid it was run on, or its "
                "ground motion is dropped."
            ) from None

        for imt in wanted:
            value = row.get(f"{GMV_PREFIX}{imt}", "")
            if not value:
                continue
            yield GroundMotionSample(
                event_id=event_id,
                area_peril_id=area_peril_id,
                imt=imt,
                value=Decimal(value),
            )


def occurrences(events: Iterable[Event]) -> tuple[OccurrenceRow, ...]:
    """The Oasis occurrence table for a stochastic event set.

    OpenQuake already assigns each event the year of the simulation it fell in,
    which is exactly what an Oasis period is. So this is a rename rather than a
    calculation, and that is worth keeping: any arithmetic here would be a
    second opinion about frequency, and the engine's is the one that matches
    the hazard.
    """
    return tuple(
        OccurrenceRow(event_id=item.event_id, period_no=item.year)
        for item in sorted(events, key=lambda item: (item.year, item.event_id))
    )


def require_single_realization(metadata: CalculationMetadata) -> None:
    """Refuse a sampled logic tree until the weighting is decided.

    Several realisations are alternative histories of the same span, each
    carrying a weight. Flattening them into one occurrence table without
    applying those weights would treat a low-weight branch as though it were as
    likely as the mean, which is a scientific choice and not a conversion
    detail. Refused here rather than approximated, for the same reason the
    multi-IMT representation is.
    """
    if metadata.realization_count > 1:
        raise OpenQuakeError(
            f"This calculation has {metadata.realization_count} logic-tree "
            "realisations. Combining them into one event set needs a weighting "
            "rule, which is a scientific decision this converter does not make. "
            "Run one realisation, or decide the rule first."
        )
