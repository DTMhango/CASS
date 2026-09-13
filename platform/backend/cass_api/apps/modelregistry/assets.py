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
from cass_keys import assets as keys_assets
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

#: One table of functions per assumption set, under the same identifiers as the
#: baseline table above. The role names the set, so a package can find the table
#: each set's engine file is built from (ADR 14).
VULNERABILITY_VARIANT_ROLE_PREFIX = "vulnerability_functions:"

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


def asset_bytes(subject, subject_type: str, role: str, description: str) -> bytes:
    """One registered asset's bytes, refusing one that is missing or unreadable.

    Bytes rather than text for the tables a package is built from: a footprint is
    millions of rows and decoding it only to encode it again doubles the memory
    for nothing.
    """
    link = (
        ArtifactLink.objects.filter(subject_type=subject_type, subject_id=subject.id, role=role)
        .select_related("artifact")
        .order_by("-created_at")
        .first()
    )
    if link is None or not link.artifact.is_readable:
        raise ModelAssetError(
            f"{description} has no registered {role.replace('_', ' ')}, so it cannot "
            "be packaged."
        )
    with get_store().open(link.artifact.uri) as handle:
        return handle.read()


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


def load_vulnerability(
    vulnerability_set, *, supported_imts: frozenset[str] | None = None
) -> VulnerabilityMapping:
    """Read a registry vulnerability set as the keys service's mapping.

    ``supported_imts`` is what can actually answer a function, and that is a
    property of neither the converter nor the vulnerability set: it is the set
    of measures the hazard attached to the run carries. A caller that knows
    which hazard set is in play passes its measures; one that does not leaves
    the keys release default, which is the conservative answer. Section 6
    requires a function demanding anything else to be reported rather than
    rerouted, and either way it is.

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
    optional = (
        {"supported_imts": supported_imts} if supported_imts is not None else {}
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
            **optional,
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
    """Grid cells, read by the same parser the Oasis package's lookup ships."""
    try:
        yield from keys_assets.read_cells(text)
    except keys_assets.AssetFormatError as exc:
        raise ModelAssetError(str(exc)) from exc


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


def attach_vulnerability_variant(
    vulnerability_set, key: str, payload: bytes, *, filename: str = "", actor=None
):
    """Register one assumption set's table of functions for a vulnerability set.

    Checksummed and not parsed, for the same reason as the baseline table.
    """
    return _attach(
        vulnerability_set,
        "vulnerability_set",
        VULNERABILITY_VARIANT_ROLE_PREFIX + key,
        payload,
        filename or f"vulnerability_{key}.csv",
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


def hazard_model_file(model, path: str) -> bytes | None:
    """One file of an uploaded package by its path inside it, or ``None``.

    Read alone rather than through :func:`hazard_model_files`, because the
    configuration editor asks for the site model on every edit and a national
    package is tens of megabytes of sources it does not need.
    """
    link = (
        ArtifactLink.objects.filter(
            subject_type="hazard_model",
            subject_id=model.id,
            role=HAZARD_MODEL_ROLE_PREFIX + path,
        )
        .select_related("artifact")
        .order_by("-created_at")
        .first()
    )
    if link is None or not link.artifact.is_readable:
        return None
    with get_store().open(link.artifact.uri) as handle:
        return handle.read()


def hazard_model_files(model) -> dict[str, bytes]:
    """Every file of an uploaded package, keyed by its path inside it.

    A source model logic tree references its files by relative path, so the
    paths are what make the package assemble. Reading them back by path rather
    than by role is the same reason they were stored that way.
    """
    links = (
        ArtifactLink.objects.filter(
            subject_type="hazard_model", subject_id=model.id
        )
        .select_related("artifact")
        .order_by("created_at")
    )
    store = get_store()
    found: dict[str, bytes] = {}
    for link in links:
        if not link.role.startswith(HAZARD_MODEL_ROLE_PREFIX):
            continue
        if not link.artifact.is_readable:
            continue
        path = link.role[len(HAZARD_MODEL_ROLE_PREFIX) :]
        with store.open(link.artifact.uri) as handle:
            found[path] = handle.read()
    if not found:
        raise ModelAssetError(
            f"{model} has no registered package files. The archive was "
            "registered but its contents are not in the artifact store, so "
            "there is nothing to submit."
        )
    return found


def _read_vulnerability(text: str) -> Iterator[VulnerabilityEntry]:
    """Mapping rows, read by the same parser the Oasis package's lookup ships.

    One parser for both lookups is the point: CASS reconciles a portfolio
    before submitting it and the engine builds items from it afterwards, and
    two readers of one file could disagree about a storey band without either
    being wrong on its own terms.
    """
    try:
        yield from keys_assets.read_mapping(text)
    except keys_assets.AssetFormatError as exc:
        raise ModelAssetError(str(exc)) from exc


def _flag(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "y", "on")
