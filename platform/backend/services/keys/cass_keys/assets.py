"""Reading the two model assets the lookup resolves against.

A grid's cells and a vulnerability set's taxonomy mapping are plain CSV, because
a reviewer has to be able to read and diff them without a tool. Parsing them
lives here, beside the lookup that consumes them, rather than in the control
plane -- and the reason is that there are now two places the lookup runs.

CASS runs it before an analysis is submitted, to reconcile value against the
published source. An Oasis model package runs it again inside the engine, to
build the items the loss calculation reads. If each side parsed the files its
own way, the two lookups could disagree about a cell boundary or a storey band
and the reconciliation would be comparing two different models. So both read
through this module, and the package ships a copy of it.

Nothing here imports Django. A malformed file raises :class:`AssetFormatError`
with a sentence a modeller can act on; the control plane re-raises that as its
own error type.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation

from .lookup import GridCell, VulnerabilityEntry

__all__ = [
    "CODE_SEPARATOR",
    "GRID_COLUMNS",
    "MAPPING_COLUMNS",
    "AssetFormatError",
    "read_cells",
    "read_mapping",
]

GRID_COLUMNS = (
    "AreaPerilID",
    "MinLatitude",
    "MaxLatitude",
    "MinLongitude",
    "MaxLongitude",
)

#: The columns a mapping must carry. The rest -- codes, storey band, channel
#: weight, label -- are optional, because a mapping written before channels
#: existed has none of them and every class in it is a single function.
MAPPING_COLUMNS = ("VulnerabilityID", "CoverageTypeID", "RequiredIMT")

#: Separator for multi-valued taxonomy columns. A comma would collide with the
#: CSV itself and quoting a list inside a cell is how a reviewer misreads one.
CODE_SEPARATOR = "|"


class AssetFormatError(ValueError):
    """Raised when a grid or mapping file cannot be read as one."""


def read_cells(text: str) -> Iterator[GridCell]:
    """Every cell of a grid file, refusing one that covers nothing."""
    for row in _rows(text, GRID_COLUMNS, "grid cell"):
        try:
            area_peril_id = int(row["AreaPerilID"])
        except (KeyError, ValueError) as exc:
            raise AssetFormatError(
                "The grid cell file has an unreadable AreaPerilID on line "
                f"{row.get('__line__', '?')}: {row.get('AreaPerilID', '')!r}."
            ) from exc

        minimum_latitude = _decimal(row, "MinLatitude", "grid cell")
        maximum_latitude = _decimal(row, "MaxLatitude", "grid cell")
        minimum_longitude = _decimal(row, "MinLongitude", "grid cell")
        maximum_longitude = _decimal(row, "MaxLongitude", "grid cell")
        if minimum_latitude >= maximum_latitude or minimum_longitude >= maximum_longitude:
            raise AssetFormatError(
                f"Grid cell {area_peril_id} has an empty or inverted extent. A cell "
                "that covers nothing would silently map no location to it."
            )

        vs30 = row.get("Vs30", "")
        yield GridCell(
            area_peril_id=area_peril_id,
            min_latitude=minimum_latitude,
            max_latitude=maximum_latitude,
            min_longitude=minimum_longitude,
            max_longitude=maximum_longitude,
            country_code=row.get("CountryCode", ""),
            offshore=_flag(row.get("Offshore", "")),
            vs30=float(vs30) if vs30 else None,
        )


def read_mapping(text: str) -> Iterator[VulnerabilityEntry]:
    """Every row of a taxonomy mapping, one function per row."""
    for row in _rows(text, MAPPING_COLUMNS, "vulnerability mapping"):
        try:
            vulnerability_id = int(row["VulnerabilityID"])
            coverage_type = int(row["CoverageTypeID"])
        except (KeyError, ValueError) as exc:
            raise AssetFormatError(
                "The vulnerability mapping has an unreadable identifier on line "
                f"{row.get('__line__', '?')}."
            ) from exc

        required_imt = row.get("RequiredIMT", "")
        if not required_imt:
            raise AssetFormatError(
                f"Vulnerability {vulnerability_id} declares no required intensity "
                "measure, so it cannot be routed to a hazard channel."
            )

        yield VulnerabilityEntry(
            vulnerability_id=vulnerability_id,
            coverage_type=coverage_type,
            required_imt=required_imt,
            occupancy_codes=_codes(row.get("OccupancyCodes", "")),
            construction_codes=_codes(row.get("ConstructionCodes", "")),
            label=row.get("Label", ""),
            storey_band=row.get("StoreyBand", ""),
            min_storeys=_optional_int(row, "MinStoreys"),
            max_storeys=_optional_int(row, "MaxStoreys"),
            channel_weight=_channel_weight(row, vulnerability_id),
        )


# -- parsing ------------------------------------------------------------------

def _rows(text: str, required: tuple[str, ...], what: str) -> Iterator[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    columns = {name.strip() for name in (reader.fieldnames or [])}
    missing = [name for name in required if name not in columns]
    if missing:
        raise AssetFormatError(
            f"The {what} file is missing required columns: {', '.join(missing)}."
        )
    for position, row in enumerate(reader, start=2):
        yield {
            (key or "").strip(): (value or "").strip()
            for key, value in row.items()
            if key
        } | {"__line__": str(position)}


def _decimal(row: dict[str, str], column: str, what: str) -> Decimal:
    try:
        return Decimal(row[column])
    except (KeyError, InvalidOperation, TypeError) as exc:
        raise AssetFormatError(
            f"The {what} file has an unreadable {column} on line {row.get('__line__', '?')}: "
            f"{row.get(column, '')!r}."
        ) from exc


def _optional_int(row: dict[str, str], column: str) -> int | None:
    """A storey limit, or None where the band is open at that end."""
    value = row.get(column, "")
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise AssetFormatError(
            f"The vulnerability mapping has an unreadable {column} on line "
            f"{row.get('__line__', '?')}: {value!r}."
        ) from exc


def _channel_weight(row: dict[str, str], vulnerability_id: int) -> float:
    """This channel's share of its class; 1 where the column is absent.

    A mapping written before channels existed has no such column and every one
    of its classes is a single function, so the whole share belongs to the one
    row. A column that is present and unreadable is refused rather than
    defaulted -- silently reading a broken weight as 1 would turn a mixture
    into several full-value functions.
    """
    value = row.get("ChannelWeight", "")
    if not value:
        return 1.0
    try:
        weight = float(value)
    except ValueError as exc:
        raise AssetFormatError(
            f"Vulnerability {vulnerability_id} has an unreadable ChannelWeight "
            f"{value!r} on line {row.get('__line__', '?')}."
        ) from exc
    if not 0.0 < weight <= 1.0:
        raise AssetFormatError(
            f"Vulnerability {vulnerability_id} has a ChannelWeight of {weight}, "
            "which is not a share of its class."
        )
    return weight


def _codes(value: str) -> frozenset[str]:
    return frozenset(
        item.strip() for item in value.split(CODE_SEPARATOR) if item.strip()
    )


def _flag(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "y", "on")
