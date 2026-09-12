"""The model assets the keys service reads.

The registry records what a grid and a vulnerability set *are* -- resolution,
refinement rule, provenance, licence, counts. It does not hold the cells or the
taxonomy rows themselves, because section 4 keeps large arrays out of Django
and section 5 puts them in the artifact store behind a reference.

This module is that reference resolved: it registers the two assets the keys
service needs against their registry records, and reads them back as the
``cass_keys`` structures. Nothing else in the control plane should parse a
grid file.

Both assets are immutable model data rather than project data. They carry the
``MODEL_ASSET`` retention class and the ``MODEL`` access policy, so entitlement
follows the model version rather than a project membership, and they outlive
any single run that used them.

The formats are deliberately plain CSV. A grid is a governed artifact that a
reviewer has to be able to read, diff and sign off without a tool, and section
7 makes the mapping from a coordinate to an area peril something the model
owner is accountable for.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation

from apps.artifacts.models import Artifact, ArtifactLink, ArtifactState
from apps.common.storage import bucket, get_store
from cass_core.artifacts import AccessPolicy, RetentionClass
from cass_keys.lookup import (
    AreaPerilGrid as KeysGrid,
)
from cass_keys.lookup import (
    GridCell,
    VulnerabilityEntry,
    VulnerabilityMapping,
)

#: Artifact roles. One asset of each kind per registry record.
GRID_CELLS_ROLE = "area_peril_grid_cells"
VULNERABILITY_MAPPING_ROLE = "vulnerability_mapping"

#: Separator for multi-valued taxonomy columns. A comma would collide with the
#: CSV itself and quoting a list inside a cell is how a reviewer misreads one.
CODE_SEPARATOR = "|"

GRID_COLUMNS = (
    "AreaPerilID",
    "MinLatitude",
    "MaxLatitude",
    "MinLongitude",
    "MaxLongitude",
)

VULNERABILITY_COLUMNS = ("VulnerabilityID", "CoverageTypeID", "RequiredIMT")


class ModelAssetError(Exception):
    """Raised when a model asset is missing or cannot be read.

    A run that cannot load its grid must stop with this rather than proceed on
    an empty one: an empty grid maps nothing, and section 15 names silently
    omitted exposure as the failure to prevent.
    """


# -- registering -------------------------------------------------------------

def _attach(subject, subject_type: str, role: str, payload: bytes, filename: str, actor):
    """Store one model asset and link it to its registry record."""
    store = get_store()
    ref = store.put_bytes(
        bucket("model"),
        f"{subject_type}/{subject.id}/{role}.csv",
        payload,
        content_type="text/csv",
        retention=RetentionClass.MODEL_ASSET,
        access=AccessPolicy.MODEL,
    )
    artifact, _ = Artifact.objects.update_or_create(
        uri=ref.uri,
        defaults={
            "checksum": ref.checksum,
            "size_bytes": ref.size_bytes,
            "content_type": ref.content_type,
            "retention": str(ref.retention),
            "access": str(ref.access),
            "state": ArtifactState.REGISTERED,
            "project": None,
            "role": role,
            "original_filename": filename[:255],
            "created_by": actor,
            "updated_by": actor,
        },
    )
    ArtifactLink.objects.update_or_create(
        artifact=artifact,
        subject_type=subject_type,
        subject_id=subject.id,
        role=role,
        direction="input",
        defaults={"created_by": actor},
    )
    return artifact


def attach_grid_cells(grid, payload: bytes, *, filename: str = "cells.csv", actor=None):
    """Register the cell definitions for a grid.

    Parsed before it is stored. An unreadable grid must be refused at the point
    someone publishes it, not discovered by the first run that needs it.
    """
    cells = tuple(_read_cells(payload.decode("utf-8-sig")))
    if not cells:
        raise ModelAssetError("The grid file defines no cells.")
    artifact = _attach(grid, "area_peril_grid", GRID_CELLS_ROLE, payload, filename, actor)
    if grid.cell_count != len(cells):
        grid.cell_count = len(cells)
        grid.save(update_fields=["cell_count", "updated_at"])
    return artifact


def attach_vulnerability_mapping(
    vulnerability_set, payload: bytes, *, filename: str = "mapping.csv", actor=None
):
    """Register the taxonomy mapping for a vulnerability set."""
    entries = tuple(_read_vulnerability(payload.decode("utf-8-sig")))
    if not entries:
        raise ModelAssetError("The vulnerability mapping defines no functions.")
    return _attach(
        vulnerability_set,
        "vulnerability_set",
        VULNERABILITY_MAPPING_ROLE,
        payload,
        filename,
        actor,
    )


# -- loading -----------------------------------------------------------------

def _asset_text(subject, subject_type: str, role: str, description: str) -> str:
    link = (
        ArtifactLink.objects.filter(
            subject_type=subject_type, subject_id=subject.id, role=role
        )
        .select_related("artifact")
        .order_by("-created_at")
        .first()
    )
    if link is None or not link.artifact.is_readable:
        raise ModelAssetError(
            f"{description} has no registered {role.replace('_', ' ')}. "
            "Attach it to the model version before running an analysis."
        )
    with get_store().open(link.artifact.uri) as handle:
        return handle.read().decode("utf-8-sig")


def load_grid(grid) -> KeysGrid:
    """Read a registry grid as the keys service's grid."""
    text = _asset_text(grid, "area_peril_grid", GRID_CELLS_ROLE, str(grid))
    cells = tuple(_read_cells(text))
    if not cells:
        raise ModelAssetError(f"{grid} has a cell file that defines no cells.")
    return KeysGrid(
        country_code=grid.country_code,
        version=grid.version,
        cells=cells,
        tolerance_km=Decimal(str(grid.mapping_tolerance_km or 0)),
    )


def load_vulnerability(vulnerability_set) -> VulnerabilityMapping:
    """Read a registry vulnerability set as the keys service's mapping.

    ``supported_imts`` is left at the release default. Which intensity measures
    the converter can actually produce is a property of the converter, not of
    the vulnerability set, and section 6 requires a function demanding anything
    else to be reported rather than rerouted.
    """
    text = _asset_text(
        vulnerability_set, "vulnerability_set", VULNERABILITY_MAPPING_ROLE,
        str(vulnerability_set),
    )
    entries = tuple(_read_vulnerability(text))
    if not entries:
        raise ModelAssetError(
            f"{vulnerability_set} has a mapping file that defines no functions."
        )
    return VulnerabilityMapping(
        country_code=vulnerability_set.country_code,
        version=vulnerability_set.version,
        entries=entries,
    )


# -- parsing ------------------------------------------------------------------

def _rows(text: str, required: tuple[str, ...], what: str) -> Iterator[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    columns = {name.strip() for name in (reader.fieldnames or [])}
    missing = [name for name in required if name not in columns]
    if missing:
        raise ModelAssetError(
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
        raise ModelAssetError(
            f"The {what} file has an unreadable {column} on line {row.get('__line__', '?')}: "
            f"{row.get(column, '')!r}."
        ) from exc


def _read_cells(text: str) -> Iterator[GridCell]:
    for row in _rows(text, GRID_COLUMNS, "grid cell"):
        try:
            area_peril_id = int(row["AreaPerilID"])
        except (KeyError, ValueError) as exc:
            raise ModelAssetError(
                "The grid cell file has an unreadable AreaPerilID on line "
                f"{row.get('__line__', '?')}: {row.get('AreaPerilID', '')!r}."
            ) from exc

        minimum_latitude = _decimal(row, "MinLatitude", "grid cell")
        maximum_latitude = _decimal(row, "MaxLatitude", "grid cell")
        minimum_longitude = _decimal(row, "MinLongitude", "grid cell")
        maximum_longitude = _decimal(row, "MaxLongitude", "grid cell")
        if minimum_latitude >= maximum_latitude or minimum_longitude >= maximum_longitude:
            raise ModelAssetError(
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


def _read_vulnerability(text: str) -> Iterator[VulnerabilityEntry]:
    for row in _rows(text, VULNERABILITY_COLUMNS, "vulnerability mapping"):
        try:
            vulnerability_id = int(row["VulnerabilityID"])
            coverage_type = int(row["CoverageTypeID"])
        except (KeyError, ValueError) as exc:
            raise ModelAssetError(
                "The vulnerability mapping has an unreadable identifier on line "
                f"{row.get('__line__', '?')}."
            ) from exc

        required_imt = row.get("RequiredIMT", "")
        if not required_imt:
            raise ModelAssetError(
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
        )


def _codes(value: str) -> frozenset[str]:
    return frozenset(
        item.strip() for item in value.split(CODE_SEPARATOR) if item.strip()
    )


def _flag(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "y", "on")
