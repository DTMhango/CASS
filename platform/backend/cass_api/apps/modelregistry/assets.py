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
import pathlib
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation

from apps.artifacts.models import Artifact, ArtifactLink, ArtifactState
from apps.common.storage import bucket, get_store
from cass_core.artifacts import AccessPolicy, RetentionClass
from cass_core.policy import IMTRepresentation
from cass_keys.lookup import (
    AreaPerilGrid as KeysGrid,
)
from cass_keys.lookup import (
    GridCell,
    MappingError,
    VulnerabilityEntry,
    VulnerabilityMapping,
)

#: Artifact roles. One asset of each kind per registry record.
GRID_CELLS_ROLE = "area_peril_grid_cells"

#: The routing table: which function a taxonomy reaches. Small, and a reviewer
#: reads it directly.
VULNERABILITY_MAPPING_ROLE = "vulnerability_mapping"

#: The damage relationships themselves -- Oasis ``vulnerability.csv``, one row
#: per function, intensity bin and damage bin. Separate from the mapping
#: because they are separate things, and conflating them is how a routing table
#: with nothing behind it came to look like a model. Large: hundreds of
#: thousands of rows, which is exactly why section 4 keeps it out of Django.
VULNERABILITY_FUNCTIONS_ROLE = "vulnerability_functions"

#: The damage-bin dictionary the functions were discretised against. A loss
#: computed against one set of bins is not comparable with a loss computed
#: against another, so the dictionary travels with the set that used it.
DAMAGE_BINS_ROLE = "damage_bin_dictionary"

#: What each identifier means and which GEM taxonomies were blended to make it,
#: so a loss can be traced back to the buildings it was computed from.
VULNERABILITY_DICTIONARY_ROLE = "vulnerability_dictionary"

#: Hazard assets are role-per-file rather than one role per kind, because a set
#: carries a footprint per intensity measure and they are different files
#: answering different vulnerability functions. Naming them by filename keeps
#: that visible instead of hiding four tables behind one role.
HAZARD_ROLE_PREFIX = "hazard_"

#: One role per file of an uploaded PSHA package. A national model is a
#: directory of NRML naming its parts from the job configuration, so the
#: published path is the identity and flattening them under one role would
#: leave nothing able to find the file a logic tree references.
HAZARD_MODEL_ROLE_PREFIX = "hazard_model:"

#: Separator for multi-valued taxonomy columns. A comma would collide with the
#: CSV itself and quoting a list inside a cell is how a reviewer misreads one.
CODE_SEPARATOR = "|"

#: Media types for the file kinds a model asset can be. A source model is NRML,
#: which is XML, and storing it as text/csv would make it unreadable to
#: anything that trusts the content type.
_MEDIA_TYPES = {
    "json": "application/json",
    "xml": "application/xml",
    "ini": "text/plain",
    "md": "text/markdown",
    "txt": "text/plain",
    "hdf5": "application/x-hdf5",
    "csv": "text/csv",
}

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

def _attach(
    subject,
    subject_type: str,
    role: str,
    payload: bytes,
    filename: str,
    actor,
    key: str | None = None,
):
    """Store one model asset and link it to its registry record.

    ``key`` overrides the stored path. A hazard model package keeps the paths
    its own logic trees reference, and those are directories rather than a
    role name.
    """
    # Taken from the filename the caller chose rather than assumed: the
    # provenance dictionary is JSON, and storing it as text/csv would make it
    # unreadable to anything that trusts the content type.
    suffix = pathlib.PurePosixPath(filename).suffix.lstrip(".").lower() or "csv"
    store = get_store()
    ref = store.put_bytes(
        bucket("model"),
        key or f"{subject_type}/{subject.id}/{role}.{suffix}",
        payload,
        content_type=_MEDIA_TYPES.get(suffix, "text/csv"),
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

    The multi-IMT representation comes from the registry record rather than the
    file, because it is a governance fact about the set -- which approval it was
    built under -- and not a property of any row. A class spanning intensity
    measures is refused under an undecided one, so getting this from the record
    keeps the refusal auditable.
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
    try:
        return VulnerabilityMapping(
            country_code=vulnerability_set.country_code,
            version=vulnerability_set.version,
            entries=entries,
            imt_representation=IMTRepresentation(
                vulnerability_set.imt_representation
                or IMTRepresentation.UNDECIDED
            ),
        )
    except MappingError as exc:
        raise ModelAssetError(
            f"{vulnerability_set} has a mapping file that is not a usable routing "
            f"table: {exc}"
        ) from exc


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


def attach_vulnerability_functions(
    vulnerability_set, payload: bytes, *, filename: str = "vulnerability.csv", actor=None
):
    """Register the damage relationships for a vulnerability set.

    Not parsed on the way in. The mapping is checked because it is small and a
    broken one makes every key wrong; this file is hundreds of thousands of
    rows and reading it into the control plane to count them would be exactly
    the memory behaviour section 4 keeps arrays out of Django to avoid. It is
    checksummed, and the converter's own reconstruction gate is what stands
    behind its contents.
    """
    return _attach(
        vulnerability_set,
        "vulnerability_set",
        VULNERABILITY_FUNCTIONS_ROLE,
        payload,
        filename,
        actor,
    )


def attach_damage_bins(
    vulnerability_set, payload: bytes, *, filename: str = "damage_bin_dict.csv", actor=None
):
    """Register the damage-bin dictionary the functions were built against."""
    return _attach(
        vulnerability_set,
        "vulnerability_set",
        DAMAGE_BINS_ROLE,
        payload,
        filename,
        actor,
    )


def attach_vulnerability_dictionary(
    vulnerability_set, payload: bytes, *, filename: str = "dictionary.json", actor=None
):
    """Register the provenance dictionary for a vulnerability set."""
    return _attach(
        vulnerability_set,
        "vulnerability_set",
        VULNERABILITY_DICTIONARY_ROLE,
        payload,
        filename,
        actor,
    )


def attach_hazard_asset(hazard_set, filename: str, payload: bytes, *, actor=None):
    """Register one file of a hazard set: a footprint, the occurrence table,
    an intensity dictionary, or the job that produced them.

    Not parsed on the way in. A footprint is hundreds of thousands of rows and
    the converter has already validated it -- probabilities summing to one,
    every event covered, nothing clipped -- before it reaches here.
    """
    role = HAZARD_ROLE_PREFIX + pathlib.PurePosixPath(filename).stem
    return _attach(hazard_set, "hazard_set", role, payload, filename, actor)


def attach_hazard_model_file(model, path: str, payload: bytes, *, actor=None):
    """Register one file of an uploaded hazard model package.

    Stored under its path inside the package, because a source model logic tree
    references its files by relative path and a store that renamed them would
    hold a model nothing could assemble.
    """
    return _attach(
        model,
        "hazard_model",
        HAZARD_MODEL_ROLE_PREFIX + path,
        payload,
        pathlib.PurePosixPath(path).name,
        actor,
        key=f"hazard_model/{model.id}/files/{path}",
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

        band = row.get("StoreyBand", "")
        yield VulnerabilityEntry(
            vulnerability_id=vulnerability_id,
            coverage_type=coverage_type,
            required_imt=required_imt,
            occupancy_codes=_codes(row.get("OccupancyCodes", "")),
            construction_codes=_codes(row.get("ConstructionCodes", "")),
            label=row.get("Label", ""),
            storey_band=band,
            min_storeys=_optional_int(row, "MinStoreys"),
            max_storeys=_optional_int(row, "MaxStoreys"),
            channel_weight=_channel_weight(row, vulnerability_id),
        )


def _optional_int(row: dict[str, str], column: str) -> int | None:
    """A storey limit, or None where the band is open at that end."""
    value = row.get(column, "")
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ModelAssetError(
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
        raise ModelAssetError(
            f"Vulnerability {vulnerability_id} has an unreadable ChannelWeight "
            f"{value!r} on line {row.get('__line__', '?')}."
        ) from exc
    if not 0.0 < weight <= 1.0:
        raise ModelAssetError(
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
