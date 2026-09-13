"""Turning one OpenQuake calculation into an Oasis hazard set.

The counterpart of ``model_build``: that one produces the damage relationships,
this one produces the ground motion they are read against. Given an export
directory it reads the metadata, the events and the ground motion, bins the
motion into intensity-bin probabilities, and produces the Oasis tables.

One thing here is not a formatting detail and is worth stating plainly.

**An Oasis footprint carries no intensity measure.** It is a table of event,
cell and intensity bin, and the measure is implicit in the file. So a
calculation covering four measures does not become one footprint -- it becomes
four, one per measure, and how a risk that needs several of them consumes them
is the multi-IMT representation the build plan leaves open. Writing them
separately is the shape the data has under every candidate representation; it
does not choose one. Under correlated channels each becomes a channel; under a
custom ground-up-loss component they are read together; under a single common
measure three of them are discarded and that discarding has to be approved.

The alternative -- silently emitting only the first measure -- would produce a
complete-looking footprint that answered a quarter of the vulnerability set and
understated everything else. That is the failure this module is shaped to make
impossible.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import pathlib
from collections.abc import Mapping, Sequence
from typing import Any

from .bins import IntensityBinSet
from .footprint import (
    ConversionMetrics,
    FootprintAccumulator,
    FootprintRow,
    check_event_coverage,
    validate_footprint,
)
from .hazard_job import HazardJob
from .occurrence import OccurrenceRow, check_frequency
from .openquake import (
    CalculationMetadata,
    Event,
    OpenQuakeError,
    measures,
    occurrences,
    read_events,
    read_ground_motion,
    read_metadata,
    read_realization_count,
    require_single_realization,
)

#: Bumped when the tables this produces change shape.
HAZARD_BUILD_VERSION = "1.0.0"

#: The exports a conversion needs, and what each is for.
REQUIRED_EXPORTS: Mapping[str, str] = {
    "gmf-data": "the ground motion itself",
    "events": "which simulated year each event fell in",
    "ruptures": "the investigation time and stochastic event set count",
    "realizations": "how many logic-tree branches the calculation produced",
}


class HazardBuildError(Exception):
    """Raised when a calculation cannot become a faithful hazard set."""


@dataclasses.dataclass(frozen=True, slots=True)
class HazardSet:
    """One country's footprint, occurrence table and the provenance of both."""

    country_code: str
    label: str
    metadata: CalculationMetadata
    events: tuple[Event, ...]
    occurrences: tuple[OccurrenceRow, ...]
    footprint: tuple[FootprintRow, ...]
    intensity_bins: Mapping[str, IntensityBinSet]
    metrics: ConversionMetrics
    problems: tuple[str, ...]
    #: How the event set and the footprint line up. Held apart from
    #: ``problems`` because most of what it reports is expected: a national
    #: event set over a regional grid contains many events that do nothing here.
    coverage: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    job: HazardJob | None = None
    build_version: str = HAZARD_BUILD_VERSION

    @property
    def imts(self) -> tuple[str, ...]:
        return tuple(sorted({row.imt for row in self.footprint}))

    @property
    def is_valid(self) -> bool:
        return not self.problems

    def rows_for(self, imt: str) -> tuple[FootprintRow, ...]:
        return tuple(row for row in self.footprint if row.imt == imt)

    def as_dict(self) -> dict[str, Any]:
        return {
            "country_code": self.country_code,
            "label": self.label,
            "build_version": self.build_version,
            "calculation": self.metadata.as_dict(),
            "effective_time": self.metadata.effective_time,
            "period_count": self.metadata.period_count,
            "events": len(self.events),
            "occurrences": len(self.occurrences),
            "annual_rate": len(self.events) / self.metadata.effective_time,
            "footprint_rows": len(self.footprint),
            "intensity_measures": list(self.imts),
            "rows_by_measure": {
                imt: len(self.rows_for(imt)) for imt in self.imts
            },
            "conversion": self.metrics.as_dict(),
            "event_coverage": dict(self.coverage),
            "valid": self.is_valid,
            "problems": list(self.problems),
            "job": self.job.as_dict() if self.job is not None else None,
        }


def _export(directory: pathlib.Path, stem: str) -> pathlib.Path:
    """The one export of a kind, refusing a directory holding several.

    Two calculations exported into one directory would leave, say, two events
    files, and picking either would risk attaching one calculation's events to
    another's ground motion. The checksums would catch it -- but only after the
    tables had been built, and only if somebody read the report.
    """
    found = sorted(directory.glob(f"{stem}_*.csv"))
    if not found:
        raise HazardBuildError(
            f"{directory} holds no {stem} export, which carries "
            f"{REQUIRED_EXPORTS.get(stem, 'part of the calculation')}. Export the "
            "calculation with --exports csv."
        )
    if len(found) > 1:
        raise HazardBuildError(
            f"{directory} holds {len(found)} {stem} exports "
            f"({', '.join(item.name for item in found)}). Give each calculation "
            "its own directory: mixing two would attach one's ground motion to "
            "the other's events."
        )
    return found[0]


def _realization_count(base: pathlib.Path, events: Sequence[Event]) -> int:
    """How many logic-tree realisations produced this event set.

    The engine publishes a realizations output only where there is a logic tree
    to describe. A calculation on a single branch exposes none at all, and
    refusing that calculation would refuse the only kind this converter accepts.
    So the export is read where it exists, and otherwise the events are asked:
    each carries the realisation it belongs to, which is the same fact counted
    one row lower down.
    """
    found = sorted(base.glob("realizations_*.csv"))
    if len(found) > 1:
        raise HazardBuildError(
            f"{base} holds {len(found)} realizations exports "
            f"({', '.join(item.name for item in found)}). Give each calculation "
            "its own directory."
        )
    if found:
        return read_realization_count(found[0])
    return len({event.realization_id for event in events})


def build_hazard(
    directory: str | pathlib.Path,
    *,
    country_code: str,
    intensity_bins: Mapping[str, IntensityBinSet],
    label: str = "",
    area_perils: Mapping[str, int] | None = None,
    job: HazardJob | None = None,
    imts: Sequence[str] | None = None,
    drop_below: float = 0.0,
) -> HazardSet:
    """Read one exported calculation and produce its Oasis tables.

    ``area_perils`` maps the export's site keys to grid cells. It can be
    omitted where the job carried the area peril as ``custom_site_id``, which
    is what ``hazard_job`` does and why it does it -- then the export names the
    cell and there is nothing to join.
    """
    base = pathlib.Path(directory)
    if not base.is_dir():
        raise HazardBuildError(f"{base} is not a directory of OpenQuake exports.")

    gmf = _export(base, "gmf-data")
    events = read_events(_export(base, "events"))
    metadata = dataclasses.replace(
        read_metadata(_export(base, "ruptures"), _export(base, "events"), gmf),
        realization_count=_realization_count(base, events),
    )
    require_single_realization(metadata)

    table = occurrences(events)

    frequency = check_frequency(
        source_event_count=len(events),
        source_investigation_time=metadata.effective_time,
        occurrences=table,
        period_count=metadata.period_count,
    )
    frequency.require_preserved()

    if area_perils is None:
        if job is not None:
            area_perils = job.area_perils
        else:
            # The export names its own sites, so they can stand for themselves
            # where they are already area perils. Refused if they are not
            # numeric, rather than guessed at.
            area_perils = _identity_area_perils(gmf)

    wanted = tuple(imts) if imts is not None else measures(gmf)
    unbinned = sorted(set(wanted) - set(intensity_bins))
    if unbinned:
        raise HazardBuildError(
            f"No intensity-bin dictionary was supplied for {', '.join(unbinned)}. "
            "A measure with no bins cannot be binned, and leaving it out would "
            "produce a footprint that silently answers fewer vulnerability "
            "functions than the set contains."
        )

    accumulator = FootprintAccumulator(intensity_bins, drop_below=drop_below)
    rows: list[FootprintRow] = []
    try:
        for sample in read_ground_motion(gmf, area_perils=area_perils, imts=wanted):
            rows.extend(accumulator.add(sample))
    except OpenQuakeError as exc:
        raise HazardBuildError(str(exc)) from exc
    rows.extend(accumulator.close())

    problems = list(validate_footprint(rows))
    coverage = check_event_coverage(rows, [item.event_id for item in events])
    problems.extend(coverage["problems"])
    if accumulator.metrics.clips_the_hazard:
        problems.append(
            f"{accumulator.metrics.samples_above_range} ground-motion values "
            f"({accumulator.metrics.above_range_share:.2%}) are above the top "
            "intensity bin and were discarded. These are the strongest values in "
            "the calculation, so every loss at those cells is understated. Widen "
            "the intensity dictionary and convert again."
        )

    return HazardSet(
        country_code=country_code.upper(),
        label=label or f"{country_code.upper()} event-based hazard",
        metadata=metadata,
        events=events,
        occurrences=table,
        footprint=tuple(rows),
        intensity_bins=dict(intensity_bins),
        metrics=accumulator.metrics,
        problems=tuple(problems),
        coverage=coverage,
        job=job,
    )


def _identity_area_perils(gmf: pathlib.Path) -> dict[str, int]:
    """Site keys that are already area perils, taken at face value."""
    from .openquake import _lines, _rows  # noqa: PLC0415

    keys = {
        (row.get("custom_site_id") or row.get("site_id") or "").strip()
        for row in _rows(_lines(gmf))
    }
    keys.discard("")
    try:
        return {key: int(key) for key in keys}
    except ValueError:
        raise HazardBuildError(
            f"{gmf.name} identifies its sites by keys that are not area perils "
            f"(for example {sorted(keys)[0]!r}), and no mapping was supplied. "
            "Either run the job through hazard_job, which carries the area peril "
            "as the site identifier, or pass area_perils."
        ) from None


# -- the Oasis tables ------------------------------------------------------------------

def footprint_csv(hazard: HazardSet, imt: str) -> bytes:
    """One measure's Oasis ``footprint.csv``.

    The measure is not a column. An Oasis footprint is a table of event, cell
    and intensity bin for one hazard channel, and which channel it is has to be
    carried by the file's identity rather than inside it.
    """
    rows = hazard.rows_for(imt)
    if not rows:
        raise HazardBuildError(
            f"This hazard set has no rows for {imt}. It carries "
            f"{', '.join(hazard.imts) or 'nothing'}."
        )
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["event_id", "areaperil_id", "intensity_bin_id", "probability"])
    for row in sorted(
        rows, key=lambda item: (item.event_id, item.area_peril_id, item.intensity_bin_id)
    ):
        writer.writerow(
            [row.event_id, row.area_peril_id, row.intensity_bin_id, f"{row.probability:.8f}"]
        )
    return buffer.getvalue().encode("utf-8")


def occurrence_csv(hazard: HazardSet) -> bytes:
    """The Oasis ``occurrence.csv``: which period each event fell in."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["event_id", "period_no"])
    for row in hazard.occurrences:
        writer.writerow([row.event_id, row.period_no])
    return buffer.getvalue().encode("utf-8")


def intensity_bins_csv(bins: IntensityBinSet) -> bytes:
    """The Oasis ``intensity_bin_dict.csv`` for one measure."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        ["bin_index", "bin_from", "bin_to", "interpolation", "interval_type"]
    )
    for item in bins.bins:
        writer.writerow(
            [item.bin_index, item.lower, item.upper, item.interpolation, 1201]
        )
    return buffer.getvalue().encode("utf-8")


def tables(hazard: HazardSet) -> dict[str, bytes]:
    """Every file this hazard set contributes, keyed by filename.

    One footprint and one intensity dictionary per measure, with the measure in
    the filename, plus the single occurrence table -- events and their periods
    are shared across measures because they are the same events.
    """
    produced: dict[str, bytes] = {"occurrence.csv": occurrence_csv(hazard)}
    for imt in hazard.imts:
        safe = imt.replace("(", "").replace(")", "").replace(".", "p")
        produced[f"footprint_{safe}.csv"] = footprint_csv(hazard, imt)
        produced[f"intensity_bin_dict_{safe}.csv"] = intensity_bins_csv(
            hazard.intensity_bins[imt]
        )
    return produced


def hazard_report(hazard: HazardSet) -> dict[str, Any]:
    """What the conversion produced and what it could not answer for."""
    report = hazard.as_dict()
    report["note"] = (
        "One footprint per intensity measure. An Oasis footprint carries no "
        "measure of its own, so a vulnerability class demanding several needs "
        "the multi-IMT representation of section 6 to say how they combine. "
        "Producing them separately states the question; it does not answer it."
    )
    return report
