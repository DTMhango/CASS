"""An Oasis model package, assembled from a CASS model version.

The hazard set holds a footprint per intensity measure and an occurrence table;
the vulnerability set holds discretised damage functions and the taxonomy
mapping; the grid holds the cells. None of those is something an Oasis worker
can load. This module turns the three into the directory a worker mounts as its
model root: ``model_data`` in the ktools binary formats, ``keys_data`` with the
lookup that builds items, ``meta-data/model_settings.json``, and the
``oasislmf.json`` that says where each of them is.

Three decisions shape it, and each is recorded in the package manifest rather
than left implicit.

**Intensity measures become area-peril channels.** An Oasis footprint carries
no intensity measure: it is event, area peril and intensity bin, and a
vulnerability function reads whichever intensity its area peril names. GEM's
functions demand four different measures, and one cell cannot carry four
intensities under one area peril. So each measure is its own channel of the
cell -- ``area_peril = cell * 10 + channel`` -- and the lookup routes a risk to
the channel of the measure its function was built for. The event is shared, so
ground motion across measures stays correlated within an event exactly as the
engine computed it. This is the correlated-channel representation of section
6, applied to single-channel classes only: a class whose GEM taxonomies respond
at several measures still has no single function, and the lookup refuses it
rather than approximating it.

**The lookup Oasis runs is the lookup CASS runs.** The package ships
``cass_keys`` and the two modules it imports, and a thin class that adapts
oasislmf's location frame to it. Reimplementing the routing as an oasislmf
built-in would give two lookups that could disagree about a storey band or a
cell edge, and the reconciliation CASS performs before submission would be
comparing two different models without saying so.

**Binaries are written here, not by the engine's tools.** ``csvtobin`` lives in
the worker image, and a package built by shelling into the engine would be a
package the control plane cannot build or test on its own. The layouts are few
and fixed, and the tests hold every one of them byte-for-byte against the
binaries of the official PiWind model.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import itertools
import json
import pathlib
import struct
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any, BinaryIO

__all__ = [
    "CHANNEL_BASE",
    "DEFAULT_RETURN_PERIODS",
    "IMT_CHANNEL_CODES",
    "PACKAGE_VERSION",
    "ModelIdentity",
    "PackageError",
    "PackageInputs",
    "build",
    "channel_area_peril",
    "damage_bins_bin",
    "events_bin",
    "merged_footprint_events",
    "occurrence_bin",
    "return_periods_bin",
    "vulnerability_bin",
    "write_footprint",
]

#: Bumped when the files a package contains, or their layout, change.
PACKAGE_VERSION = "1.0.0"

#: The channel each intensity measure occupies within a cell. Fixed rather than
#: derived from whichever measures a hazard set happens to carry, so an area
#: peril means the same thing in every package built from the same grid.
IMT_CHANNEL_CODES: Mapping[str, int] = {
    "PGA": 1,
    "SA(0.3)": 2,
    "SA(0.6)": 3,
    "SA(1.0)": 4,
}

#: The multiplier that makes room for the channels. Ten rather than four so a
#: measure added later does not renumber every area peril already published.
CHANNEL_BASE = 10

#: Reporting return periods, in years. Oasis interpolates an EP curve at these,
#: so they are what a result's exceedance table is read at.
DEFAULT_RETURN_PERIODS: tuple[int, ...] = (1000, 500, 250, 200, 100, 50, 25, 10, 5, 2)

_EVENT_ROW = struct.Struct("<Iif")
_INDEX_ROW = struct.Struct("<iqq")
_VULNERABILITY_ROW = struct.Struct("<iiif")
_DAMAGE_ROW = struct.Struct("<ifffi")
_OCCURRENCE_ROW = struct.Struct("<iii")


class PackageError(Exception):
    """Raised when a package cannot be assembled faithfully."""


def channel_area_peril(cell_id: int, imt: str) -> int:
    """The area peril a cell's ground motion in one measure is carried under."""
    try:
        code = IMT_CHANNEL_CODES[imt]
    except KeyError:
        raise PackageError(
            f"{imt} has no channel in this package layout. Known measures: "
            f"{', '.join(IMT_CHANNEL_CODES)}. A measure with no channel cannot be "
            "carried, and dropping it would leave its functions answered by nothing."
        ) from None
    return int(cell_id) * CHANNEL_BASE + code


@dataclasses.dataclass(frozen=True, slots=True)
class ModelIdentity:
    """The model triple the Oasis worker registers under and the API resolves."""

    supplier_id: str = "KRE"
    model_id: str = "EQ"
    version_id: str = "1"

    def as_dict(self) -> dict[str, str]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True, slots=True)
class PackageInputs:
    """Everything a package is assembled from, as the registry stores it."""

    identity: ModelIdentity
    country_code: str
    grid_version: str
    grid_tolerance_km: str
    imt_representation: str
    grid_cells_csv: bytes
    mapping_csv: bytes
    vulnerability_csv: bytes
    damage_bins_csv: bytes
    #: One ``event_id,areaperil_id,intensity_bin_id,probability`` table per
    #: measure, with the grid's cell as the area peril.
    footprints: Mapping[str, bytes]
    occurrence_csv: bytes
    period_count: int
    #: Source files of the packages the lookup imports, keyed by their path
    #: under ``keys_data/vendor``.
    vendored: Mapping[str, bytes]
    intensity_bin_count: int
    provenance: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    return_periods: Sequence[int] = DEFAULT_RETURN_PERIODS
    sample_count: int = 10


# -- the binaries ---------------------------------------------------------------------

def write_footprint(
    events: Iterable[tuple[int, list[tuple[int, int, float]]]],
    footprint: BinaryIO,
    index: BinaryIO,
    *,
    intensity_bin_count: int | None = None,
    uncertainty: bool | None = None,
) -> dict[str, int]:
    """Write ``footprint.bin`` and ``footprint.idx`` from events in order.

    Each event's rows are sorted by area peril and then bin before writing,
    because the engine searches an event's rows by area peril. Events must
    arrive in ascending order: the index is read as a sorted table.

    The header's bin count is the largest bin written unless the dictionary's
    count is given, and its uncertainty flag is whether any area peril carries
    more than one bin unless stated -- PiWind's tools set it regardless.
    """
    footprint.write(b"\0" * 8)
    offset = 8
    largest_bin = 0
    uncertain = False
    previous_event: int | None = None
    event_count = 0
    row_count = 0

    for event_id, entries in events:
        if event_id < 1:
            raise PackageError(
                f"Footprint event {event_id} is not a usable engine identifier: "
                "ktools reserves zero and negative numbers as markers, so an event "
                "numbered there is read as the absence of an event."
            )
        if previous_event is not None and event_id <= previous_event:
            raise PackageError(
                f"Footprint event {event_id} arrived after {previous_event}. The "
                "index is read as a sorted table, so an event out of order would "
                "be unreachable."
            )
        previous_event = event_id
        entries.sort()
        payload = bytearray()
        previous_area_peril: int | None = None
        for area_peril, bin_id, probability in entries:
            payload += _EVENT_ROW.pack(area_peril, bin_id, probability)
            largest_bin = max(largest_bin, bin_id)
            if area_peril == previous_area_peril:
                uncertain = True
            previous_area_peril = area_peril
        footprint.write(payload)
        index.write(_INDEX_ROW.pack(event_id, offset, len(payload)))
        offset += len(payload)
        event_count += 1
        row_count += len(entries)

    end = footprint.tell()
    footprint.seek(0)
    footprint.write(
        struct.pack(
            "<ii",
            intensity_bin_count if intensity_bin_count is not None else largest_bin,
            int(uncertain if uncertainty is None else uncertainty),
        )
    )
    footprint.seek(end)
    return {"events": event_count, "rows": row_count, "largest_bin": largest_bin}


@dataclasses.dataclass(frozen=True, slots=True)
class FootprintIndexEntry:
    """One row of ``footprint.idx``: where an event's rows sit, and how many bytes."""

    event_id: int
    offset: int
    size: int


def read_footprint_index(payload: bytes) -> list[FootprintIndexEntry]:
    """Read ``footprint.idx`` back, one entry per event in the order written.

    The layout is :func:`write_footprint`'s, kept here so that nothing outside
    this module has to know it. The control plane reads it to choose the events
    a smoke check runs, and an index read with the wrong row size would choose
    events that do not exist.
    """
    if len(payload) % _INDEX_ROW.size:
        raise PackageError(
            f"A footprint index of {len(payload)} bytes is not a whole number of "
            f"{_INDEX_ROW.size}-byte rows, so it is not an index this package writes."
        )
    return [
        FootprintIndexEntry(*_INDEX_ROW.unpack_from(payload, offset))
        for offset in range(0, len(payload), _INDEX_ROW.size)
    ]


def merged_footprint_events(
    footprints: Mapping[str, bytes],
) -> Iterator[tuple[int, list[tuple[int, int, float]]]]:
    """Every event across the per-measure footprints, with channel area perils.

    Streams each table in step rather than loading them: a regional footprint is
    millions of rows per measure, and only one event's rows across the measures
    are ever held at once.
    """
    streams = []
    for imt, payload in sorted(footprints.items()):
        rows = _footprint_rows(payload, imt)
        streams.append(itertools.groupby(rows, key=lambda row: row[0]))

    current = [next(stream, None) for stream in streams]
    last_seen: list[int | None] = [None] * len(streams)
    while any(group is not None for group in current):
        event_id = min(group[0] for group in current if group is not None)
        entries: list[tuple[int, int, float]] = []
        for position, group in enumerate(current):
            if group is None or group[0] != event_id:
                continue
            if last_seen[position] is not None and event_id <= last_seen[position]:
                raise PackageError(
                    "A footprint is not grouped by ascending event, so its rows for "
                    f"event {event_id} would be split across the index."
                )
            last_seen[position] = event_id
            entries.extend((row[1], row[2], row[3]) for row in group[1])
            current[position] = next(streams[position], None)
        yield event_id, entries


def _footprint_rows(payload: bytes, imt: str) -> Iterator[tuple[int, int, int, float]]:
    reader = csv.reader(io.StringIO(payload.decode("utf-8")))
    header = next(reader, None) or []
    column = {name.strip(): position for position, name in enumerate(header)}
    missing = {"event_id", "areaperil_id", "intensity_bin_id", "probability"} - set(column)
    if missing:
        raise PackageError(
            f"The {imt} footprint is missing {', '.join(sorted(missing))}, so it is "
            "not an Oasis footprint table."
        )
    event, area, bin_, probability = (
        column["event_id"],
        column["areaperil_id"],
        column["intensity_bin_id"],
        column["probability"],
    )
    for row in reader:
        if not row:
            continue
        yield (
            int(row[event]),
            channel_area_peril(int(row[area]), imt),
            int(row[bin_]),
            float(row[probability]),
        )


def vulnerability_bin(payload: bytes, damage_bin_count: int) -> bytes:
    """``vulnerability.bin``: the damage-bin count, then every row in key order."""
    rows = sorted(
        (
            int(row["vulnerability_id"]),
            int(row["intensity_bin_id"]),
            int(row["damage_bin_id"]),
            float(row["probability"]),
        )
        for row in csv.DictReader(io.StringIO(payload.decode("utf-8")))
    )
    if not rows:
        raise PackageError("The vulnerability table has no rows.")
    largest = max(row[2] for row in rows)
    if largest > damage_bin_count:
        raise PackageError(
            f"The vulnerability table uses damage bin {largest} and the dictionary "
            f"has {damage_bin_count}. A damage ratio past the dictionary has no "
            "value to sample."
        )
    output = bytearray(struct.pack("<i", damage_bin_count))
    for row in rows:
        output += _VULNERABILITY_ROW.pack(*row)
    return bytes(output)


def damage_bins_bin(payload: bytes) -> tuple[bytes, bytes, int]:
    """``damage_bin_dict.bin`` and a CSV in the engine's own column names.

    CASS records the interval type on its dictionary; the engine's reader wants
    a damage type in that position, and every bin here is an ordinary damage
    ratio, which is type 0.
    """
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))
    if not rows:
        raise PackageError("The damage-bin dictionary has no bins.")
    binary = bytearray()
    text = io.StringIO(newline="")
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(["bin_index", "bin_from", "bin_to", "interpolation", "damage_type"])
    for position, row in enumerate(rows, start=1):
        index = int(row["bin_index"])
        if index != position:
            raise PackageError(
                f"Damage bins must be numbered from 1 without gaps; found {index} "
                f"at position {position}."
            )
        damage_type = int(row.get("damage_type") or 0)
        values = (float(row["bin_from"]), float(row["bin_to"]), float(row["interpolation"]))
        binary += _DAMAGE_ROW.pack(index, *values, damage_type)
        writer.writerow([index, row["bin_from"], row["bin_to"], row["interpolation"], damage_type])
    return bytes(binary), text.getvalue().encode("utf-8"), len(rows)


def events_bin(event_ids: Iterable[int]) -> bytes:
    """``events.bin``: every event identifier, ascending."""
    ordered = sorted({int(item) for item in event_ids})
    return struct.pack(f"<{len(ordered)}i", *ordered)


def date_id(year: int, month: int = 1, day: int = 1) -> int:
    """The engine's day number for a date, as its occurrence converter computes it."""
    shifted_month = (month + 9) % 12
    shifted_year = year - shifted_month // 10
    return (
        365 * shifted_year
        + shifted_year // 4
        - shifted_year // 100
        + shifted_year // 400
        + (shifted_month * 306 + 5) // 10
        + (day - 1)
    )


def event_id_offset(occurrence_csv: bytes) -> int:
    """How far the event identifiers must move to be usable by the engine.

    ktools reserves zero. It is the null node, the end-of-list marker in the
    financial module's compute queue and the terminator in its streams, and an
    event numbered zero is read as the absence of one: the financial module
    treats the following event as a continuation of it, pushes every item a
    second time, and writes past the end of a node's children. That is a
    segmentation fault in the middle of a loss calculation, with nothing said
    about why.

    OpenQuake numbers its events from zero, so this is the ordinary case rather
    than an exotic one. The whole set is shifted by the same amount, in the
    footprint, the event list and the occurrence table together, and the offset
    is recorded in the manifest so a loss can be traced back to the calculation
    event it came from.
    """
    lowest: int | None = None
    for row in csv.DictReader(io.StringIO(occurrence_csv.decode("utf-8"))):
        event_id = int(row["event_id"])
        lowest = event_id if lowest is None else min(lowest, event_id)
    if lowest is None:
        raise PackageError(
            "The occurrence table names no events, so the package would carry a "
            "hazard nothing can happen in."
        )
    return max(0, 1 - lowest)


def occurrence_bin(payload: bytes, period_count: int, offset: int = 0) -> tuple[bytes, list[int]]:
    """``occurrence.bin`` with dates, and the event identifiers it names.

    A period is a simulated year. Where the table carries no calendar date the
    occurrence is placed on the first of January of its period, because every
    output CASS asks for is annual and a date inside the year changes none of
    them.
    """
    output = bytearray(struct.pack("<ii", 1, period_count))
    event_ids: list[int] = []
    for row in csv.DictReader(io.StringIO(payload.decode("utf-8"))):
        event_id = int(row["event_id"]) + offset
        period = int(row["period_no"])
        if not 1 <= period <= period_count:
            raise PackageError(
                f"Event {event_id} falls in period {period}, outside the "
                f"{period_count} periods of the event set. Its frequency would be "
                "counted against a year that does not exist."
            )
        year = int(row.get("occ_year") or period)
        month = int(row.get("occ_month") or 1)
        day = int(row.get("occ_day") or 1)
        output += _OCCURRENCE_ROW.pack(event_id, period, date_id(year, month, day))
        event_ids.append(event_id)
    return bytes(output), event_ids


def return_periods_bin(periods: Sequence[int]) -> bytes:
    """``returnperiods.bin``: the reporting periods, longest first."""
    ordered = sorted({int(item) for item in periods}, reverse=True)
    return struct.pack(f"<{len(ordered)}i", *ordered)


# -- the package ----------------------------------------------------------------------

def build(inputs: PackageInputs, destination: pathlib.Path) -> dict[str, Any]:
    """Write a complete model package into ``destination`` and describe it."""
    base = pathlib.Path(destination)
    model_data = base / "model_data"
    keys_data = base / "keys_data"
    meta_data = base / "meta-data"
    for directory in (model_data, keys_data / "vendor", meta_data):
        directory.mkdir(parents=True, exist_ok=True)

    demanded = _demanded_measures(inputs.mapping_csv)
    missing = sorted(demanded - set(inputs.footprints))
    if missing:
        raise PackageError(
            f"The vulnerability functions demand {', '.join(missing)} and the hazard "
            "set carries no footprint for it. Those functions would be answered by "
            "nothing and report zero, which reads exactly like no damage."
        )

    offset = event_id_offset(inputs.occurrence_csv)
    with (model_data / "footprint.bin").open("wb") as footprint, (
        model_data / "footprint.idx"
    ).open("wb") as index:
        footprint_summary = write_footprint(
            (
                (event_id + offset, entries)
                for event_id, entries in merged_footprint_events(inputs.footprints)
            ),
            footprint,
            index,
            intensity_bin_count=inputs.intensity_bin_count,
        )

    damage_binary, damage_csv, damage_count = damage_bins_bin(inputs.damage_bins_csv)
    (model_data / "damage_bin_dict.bin").write_bytes(damage_binary)
    (model_data / "damage_bin_dict.csv").write_bytes(damage_csv)
    (model_data / "vulnerability.bin").write_bytes(
        vulnerability_bin(inputs.vulnerability_csv, damage_count)
    )

    occurrence, event_ids = occurrence_bin(
        inputs.occurrence_csv, inputs.period_count, offset
    )
    events = events_bin(event_ids)
    # Both the named and the unnamed file: the engine looks for the name the
    # model settings default to, or the plain name when analysis settings choose
    # no event set -- which is what CASS sends.
    for name in ("events.bin", "events_p.bin"):
        (model_data / name).write_bytes(events)
    for name in ("occurrence.bin", "occurrence_lt.bin"):
        (model_data / name).write_bytes(occurrence)
    (model_data / "returnperiods.bin").write_bytes(return_periods_bin(inputs.return_periods))

    (keys_data / "grid_cells.csv").write_bytes(inputs.grid_cells_csv)
    (keys_data / "vulnerability_mapping.csv").write_bytes(inputs.mapping_csv)
    (keys_data / "channels.json").write_text(
        json.dumps(
            {
                "country_code": inputs.country_code,
                "grid_version": inputs.grid_version,
                "tolerance_km": inputs.grid_tolerance_km,
                "imt_representation": inputs.imt_representation,
                "imt_channel_codes": dict(IMT_CHANNEL_CODES),
                "channel_base": CHANNEL_BASE,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (keys_data / "lookup.py").write_text(LOOKUP_SOURCE, encoding="utf-8")
    (keys_data / "lookup_config.json").write_text(
        json.dumps(
            {
                "model": {
                    "supplier_id": inputs.identity.supplier_id,
                    "model_id": inputs.identity.model_id,
                    "model_version": inputs.identity.version_id,
                },
                "keys_data_path": "./",
                "lookup_module_path": "lookup.py",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    for relative, source in sorted(inputs.vendored.items()):
        target = keys_data / "vendor" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source)

    unique_events = len(set(event_ids))
    (meta_data / "model_settings.json").write_text(
        json.dumps(
            _model_settings(inputs, unique_events), indent=2, sort_keys=True
        ),
        encoding="utf-8",
    )
    (base / "oasislmf.json").write_text(
        json.dumps(
            {
                "model_data_dir": "model_data",
                "lookup_config_json": "keys_data/lookup_config.json",
                "model_settings_json": "meta-data/model_settings.json",
                "gulmc": True,
                "gulpy": False,
                "modelpy": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    manifest = {
        "package_version": PACKAGE_VERSION,
        "identity": inputs.identity.as_dict(),
        "country_code": inputs.country_code,
        "imt_representation": inputs.imt_representation,
        "imt_channel_codes": dict(IMT_CHANNEL_CODES),
        "channel_base": CHANNEL_BASE,
        "measures": sorted(inputs.footprints),
        "measures_demanded": sorted(demanded),
        "events": unique_events,
        "occurrences": len(event_ids),
        # What was added to the hazard set's own event numbers to make them
        # usable by the engine, so a loss can be traced back to the calculation
        # event it came from.
        "event_id_offset": offset,
        "periods": inputs.period_count,
        "footprint": footprint_summary,
        "damage_bins": damage_count,
        "intensity_bins": inputs.intensity_bin_count,
        "return_periods": sorted(set(inputs.return_periods), reverse=True),
        "provenance": dict(inputs.provenance),
    }
    manifest["files"] = _checksums(base)
    (base / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest


def _demanded_measures(mapping_csv: bytes) -> set[str]:
    return {
        (row.get("RequiredIMT") or "").strip()
        for row in csv.DictReader(io.StringIO(mapping_csv.decode("utf-8-sig")))
        if (row.get("RequiredIMT") or "").strip()
    }


def _model_settings(inputs: PackageInputs, event_count: int) -> dict[str, Any]:
    return {
        "version": "3",
        "model_settings": {
            "event_set": {
                "name": "Event set",
                "desc": "Stochastic events from the attached OpenQuake hazard set",
                "default": "p",
                "options": [
                    {"id": "p", "desc": "Probabilistic", "number_of_events": event_count}
                ],
            },
            "event_occurrence_id": {
                "name": "Occurrence set",
                "desc": f"{inputs.period_count} simulated years",
                "default": "lt",
                "options": [{"id": "lt", "desc": "Long term"}],
            },
        },
        "lookup_settings": {
            "supported_perils": [{"id": "QEQ", "desc": "Earthquake shaking"}]
        },
        "data_settings": {
            "damage_group_fields": ["PortNumber", "AccNumber", "LocNumber"],
            "hazard_group_fields": ["PortNumber", "AccNumber", "LocNumber"],
        },
        "model_default_samples": inputs.sample_count,
    }


def _checksums(base: pathlib.Path) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.name == "MANIFEST.json":
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        found[path.relative_to(base).as_posix()] = {
            "sha256": digest.hexdigest(),
            "bytes": path.stat().st_size,
        }
    return found


#: The lookup oasislmf loads from the package, by the class name it expects:
#: ``<model_id>KeysLookup``. Written as source rather than imported, because it
#: runs inside the engine against the vendored copy of ``cass_keys`` beside it.
LOOKUP_SOURCE = '''"""The CASS keys lookup, as an Oasis worker runs it.

This package ships the same ``cass_keys`` CASS used to reconcile the portfolio
before submitting it. This class only adapts oasislmf's location frame to it and
turns each key into the channel area peril the package's footprint is written
under, so the two lookups cannot disagree about a cell edge or a storey band.
"""

import json
import math
import pathlib
import sys
from decimal import Decimal

import pandas as pd
from oasislmf.lookup.base import AbstractBasicKeyLookup, MultiprocLookupMixin

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "vendor"))

from cass_core.policy import IMTRepresentation  # noqa: E402
from cass_keys.assets import read_cells, read_mapping  # noqa: E402
from cass_keys.lookup import AreaPerilGrid, VulnerabilityMapping, lookup  # noqa: E402

#: oasislmf lower-cases the location columns; cass_keys reads OED's own names.
OED_NAMES = {
    "accnumber": "AccNumber",
    "locnumber": "LocNumber",
    "portnumber": "PortNumber",
    "latitude": "Latitude",
    "longitude": "Longitude",
    "occupancycode": "OccupancyCode",
    "constructioncode": "ConstructionCode",
    "numberofstoreys": "NumberOfStoreys",
    "locperilscovered": "LocPerilsCovered",
    "buildingtiv": "BuildingTIV",
    "othertiv": "OtherTIV",
    "contentstiv": "ContentsTIV",
    "bitiv": "BITIV",
}

#: Columns holding codes, which pandas reads as floats and OED writes as integers.
CODE_COLUMNS = {"OccupancyCode", "ConstructionCode", "NumberOfStoreys", "LocNumber", "AccNumber"}


def _text(name, value):
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if name in CODE_COLUMNS and value.is_integer():
            return str(int(value))
    return str(value).strip()


class EQKeysLookup(AbstractBasicKeyLookup, MultiprocLookupMixin):
    interface_version = "1"

    def __init__(self, config, config_dir=None, user_data_dir=None, output_dir=None):
        super().__init__(
            config, config_dir=config_dir, user_data_dir=user_data_dir, output_dir=output_dir
        )
        base = pathlib.Path(self.config["keys_data_path"])
        channels = json.loads((base / "channels.json").read_text(encoding="utf-8"))
        self.codes = channels["imt_channel_codes"]
        self.channel_base = int(channels["channel_base"])
        cells = tuple(read_cells((base / "grid_cells.csv").read_text(encoding="utf-8-sig")))
        self.grid = AreaPerilGrid(
            country_code=channels["country_code"],
            version=channels["grid_version"],
            cells=cells,
            tolerance_km=Decimal(str(channels["tolerance_km"])),
        )
        entries = tuple(
            read_mapping((base / "vulnerability_mapping.csv").read_text(encoding="utf-8-sig"))
        )
        self.mapping = VulnerabilityMapping(
            country_code=channels["country_code"],
            version=channels["grid_version"],
            entries=entries,
            # What this package can answer is exactly what it carries a
            # footprint for, which is what the channel codes enumerate. Left at
            # the keys default, the engine's own lookup would refuse classes
            # the package holds ground motion for, and disagree with the CASS
            # keys result over the same exposure.
            supported_imts=frozenset(self.codes),
            imt_representation=IMTRepresentation(channels["imt_representation"]),
        )

    def process_locations(self, locations):
        frame = locations if isinstance(locations, pd.DataFrame) else pd.DataFrame(locations)
        rows = []
        identities = {}
        for record in frame.to_dict("records"):
            # Column case is not ours to assume: one oasislmf path lower-cases
            # the location frame and another hands over OED's own spelling, and
            # reading only one of them loses every identifier -- which reads
            # downstream as a portfolio the model could not key at all.
            folded = {str(column).lower(): value for column, value in record.items()}
            row = {}
            for column, name in OED_NAMES.items():
                if column in folded:
                    row[name] = _text(name, folded[column])
            identities[(row.get("AccNumber", ""), row.get("LocNumber", ""))] = folded["loc_id"]
            rows.append(row)

        result = lookup(rows, grid=self.grid, vulnerability=self.mapping)
        keys = []
        for key in result.records:
            loc_id = identities.get((key.account_id, key.location_number))
            if loc_id is None:
                continue
            status = str(key.status)
            area_peril = 0
            vulnerability = 0
            message = key.message
            if status == "success":
                if key.channel_count > 1:
                    status = "fail_v"
                    message = (
                        "The class spans intensity measures and this package carries "
                        "single-channel classes only."
                    )
                elif key.imt not in self.codes:
                    status = "fail_v"
                    message = f"No footprint channel carries {key.imt}."
                else:
                    area_peril = int(key.area_peril_id) * self.channel_base + int(self.codes[key.imt])
                    vulnerability = int(key.vulnerability_id)
            keys.append(
                {
                    "loc_id": loc_id,
                    "peril_id": key.peril_id,
                    "coverage_type": int(key.coverage_type),
                    "area_peril_id": area_peril,
                    "vulnerability_id": vulnerability,
                    "status": status,
                    "message": message,
                }
            )
        return pd.DataFrame(keys)
'''
