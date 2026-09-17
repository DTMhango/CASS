"""Registering an OpenQuake calculation as a hazard set the platform can run.

The last missing leg. Exposure has been real since the intake template, the
damage relationships since the GEM build, and this is the ground motion they
are read against -- the piece without which the platform could hold a portfolio
and never produce a number.

What gets stored is the whole hazard set, not a summary of it: one footprint
per intensity measure, the occurrence table, an intensity-bin dictionary per
measure, and the job that produced them. The job matters as much as the output.
A footprint is a large table of numbers that looks the same whatever it came
from, and the only way to know whether two runs are comparable is to compare
the calculations behind them -- so the job configuration, its checksum, the
engine version and the calculation's own checksum are all recorded.

Two refusals, and both are about a footprint that would look complete.

**Clipped hazard blocks publication rather than warning.** Ground motion above
the top intensity bin is discarded by the accumulator, and those values are the
strongest the calculation produced. A set that clips is not slightly wrong at
the mean; it is wrong exactly where a reinsurance loss lives.

**A hazard set is not attached to a model version unless it carries every
measure the vulnerability functions demand.** Half the measures produces a
model that answers half its own vulnerability set and reports zero for the
rest, which is indistinguishable from an event that did no damage.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
from typing import Any

from django.db import transaction

from apps.artifacts.models import Artifact, ArtifactLink, ArtifactState
from cass_converter import datastore, hazard_build, pilot_bins, qa
from cass_converter.hazard_build import HazardBuildError
from cass_converter.hazard_build import HazardSet as ConvertedHazard
from cass_converter.hazard_job import HazardJob

from . import quality
from .assets import HAZARD_ROLE_PREFIX, attach_hazard_asset, attach_hazard_asset_file
from .models import (
    INTERNAL_USE_LICENCE,
    AreaPerilGrid,
    HazardJobSpec,
    HazardSet,
    ModelVersion,
    PublicationState,
)


class HazardRegistrationError(Exception):
    """Raised when a calculation cannot be registered as a hazard set."""


@dataclasses.dataclass(frozen=True, slots=True)
class SourceStatement:
    """What the operator asserts about the seismic sources behind a calculation.

    The source model is the single largest determinant of the answer and it is
    not derivable from the export -- OpenQuake records that a source model was
    used, not whose it is or what may be done with it. So it is stated, with
    the same shape as the vulnerability licence: a clearance needs a reference,
    because an unevidenced one in a governance record is worse than an honest
    absence.
    """

    model: str
    licence: str = ""
    cleared: bool = True
    reference: str = INTERNAL_USE_LICENCE
    ground_motion_models: tuple[str, ...] = ()
    checksum: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise HazardRegistrationError(
                "A hazard set must name its seismic source model. It is the largest "
                "single determinant of the answer and nothing in the export records "
                "which one was used."
            )
        if self.cleared and not self.reference.strip():
            raise HazardRegistrationError(
                "A source licence clearance needs a reference -- the basis or "
                "approval that grants it."
            )

    def as_note(self) -> str:
        if not self.cleared:
            return (
                f"Use of {self.model} has not been cleared. This hazard set may be "
                "used for platform development until it is."
            ) + (f" {self.note}" if self.note else "")
        # The reference is the basis in full -- GEM's permission, or the
        # internal-use basis -- and reads as written.
        return self.reference + (f" {self.note}" if self.note else "")


def build(
    directory: str | pathlib.Path,
    *,
    country_code: str,
    label: str = "",
    job: HazardJob | None = None,
) -> ConvertedHazard:
    """Read an exported calculation into the converter's hazard set."""
    try:
        return hazard_build.build_hazard(
            directory,
            country_code=country_code,
            intensity_bins=pilot_bins.intensity_bins(),
            label=label,
            job=job,
        )
    except HazardBuildError as exc:
        raise HazardRegistrationError(str(exc)) from exc


def build_from_datastore(
    path: str | pathlib.Path,
    *,
    country_code: str,
    label: str = "",
    job: HazardJob | None = None,
) -> ConvertedHazard:
    """Read a calculation's own datastore into the converter's hazard set.

    The datastore rather than the CSV exports, because the exports are a second
    copy of the ground motion written as text, and at national scale a copy the
    worker cannot hold. The datastore is read a slice at a time under a row
    budget, so memory follows the budget rather than the size of the
    calculation.
    """
    try:
        return hazard_build.build_hazard_from_datastore(
            path,
            country_code=country_code,
            intensity_bins=pilot_bins.intensity_bins(),
            label=label,
            job=job,
        )
    except HazardBuildError as exc:
        raise HazardRegistrationError(str(exc)) from exc


def current_intensity_bins_checksum() -> str:
    """The fingerprint of the intensity bins a footprint built now would use."""
    return hazard_build.intensity_bins_checksum(pilot_bins.intensity_bins())


@transaction.atomic
def register(
    directory: str | pathlib.Path,
    *,
    country_code: str,
    version: str,
    source: SourceStatement,
    grid: AreaPerilGrid | None = None,
    label: str = "",
    job: HazardJob | None = None,
    calculation_id: str = "",
    actor=None,
) -> tuple[HazardSet, ConvertedHazard]:
    """Register one calculation as a versioned hazard set with its tables.

    Idempotent by version. Re-registering replaces the stored files and leaves
    the registry record in place, so running this twice does not produce two
    sets whose event identifiers mean different things.
    """
    converted = build(directory, country_code=country_code, label=label, job=job)
    hazard_set = record(
        converted,
        version=version,
        source=source,
        grid=grid,
        job=job,
        calculation_id=calculation_id,
        actor=actor,
    )
    return hazard_set, converted


def register_from_datastore(
    path: str | pathlib.Path,
    *,
    country_code: str,
    version: str,
    source: SourceStatement,
    grid: AreaPerilGrid | None = None,
    label: str = "",
    job: HazardJob | None = None,
    calculation_id: str = "",
    datastore_uri: str = "",
    rebuilt_from: HazardSet | None = None,
    job_spec: HazardJobSpec | None = None,
    job_checksum: str = "",
    actor=None,
) -> tuple[HazardSet, ConvertedHazard]:
    """Register a calculation from its datastore, which stays where it is stored.

    The conversion runs before anything is written, outside the transaction: a
    national footprint takes minutes to bin, and a database transaction held
    open for all of it would block everything else that touches the registry.
    Only the record and its tables are written atomically.

    ``datastore_uri`` is kept on the set, because it is what a later rebuild
    reads. ``rebuilt_from`` names the set this one replaces, where it was
    rebuilt rather than computed. ``job_spec`` and ``job_checksum`` name the
    saved configuration the calculation ran, which is what lets it be run again
    and shown to be the same calculation.
    """
    converted = build_from_datastore(
        path, country_code=country_code, label=label, job=job
    )
    try:
        digest = datastore.ground_motion_digest(path)
    except datastore.DatastoreError as exc:
        raise HazardRegistrationError(str(exc)) from exc
    with transaction.atomic():
        hazard_set = record(
            converted,
            version=version,
            source=source,
            grid=grid,
            job=job,
            calculation_id=calculation_id,
            datastore_uri=datastore_uri,
            rebuilt_from=rebuilt_from,
            job_spec=job_spec,
            job_checksum=job_checksum,
            ground_motion_digest=digest,
            actor=actor,
        )
    return hazard_set, converted


def record(
    converted: ConvertedHazard,
    *,
    version: str,
    source: SourceStatement,
    grid: AreaPerilGrid | None = None,
    job: HazardJob | None = None,
    calculation_id: str = "",
    datastore_uri: str = "",
    rebuilt_from: HazardSet | None = None,
    job_spec: HazardJobSpec | None = None,
    job_checksum: str = "",
    ground_motion_digest: str = "",
    actor=None,
) -> HazardSet:
    """Write one converted calculation into the registry with its tables."""
    code = converted.country_code

    if grid is None:
        grid = (
            AreaPerilGrid.objects.filter(country_code=code)
            .order_by("-created_at")
            .first()
        )
    if grid is None:
        raise HazardRegistrationError(
            f"No area-peril grid is registered for {code}, so the cells this "
            "footprint names would refer to nothing. Register the grid first."
        )

    # Counted as the footprint was written, not by reading it back: a
    # national footprint is hundreds of millions of rows and the cell set is
    # bounded by the grid.
    cells = converted.footprint.cells
    hazard_set, _ = HazardSet.objects.update_or_create(
        country_code=code,
        version=version,
        defaults={
            "label": converted.label,
            "source_model": source.model,
            "source_model_checksum": source.checksum[:64],
            "ground_motion_models": list(source.ground_motion_models),
            "licence": source.licence,
            "licence_cleared": source.cleared,
            "licence_note": source.as_note(),
            "grid": grid,
            "engine_version": converted.metadata.engine_version[:32],
            "calculation_checksum": converted.metadata.checksum[:64],
            "job_checksum": (
                hazard_build.hazard_job.job_checksum(job)
                if job is not None
                else job_checksum[:64]
            ),
            "job_spec": job_spec,
            "ground_motion_digest": ground_motion_digest,
            "openquake_calculation_removed": False,
            "openquake_calculation_id": str(calculation_id or "")[:32],
            "datastore_uri": datastore_uri,
            "intensity_bins_checksum": hazard_build.intensity_bins_checksum(
                converted.intensity_bins
            ),
            "rebuilt_from": rebuilt_from,
            "investigation_time": converted.metadata.investigation_time or 0.0,
            "stochastic_event_sets": converted.metadata.ses_per_logic_tree_path or 0,
            "logic_tree_paths": max(1, converted.metadata.realization_count),
            "event_count": len(converted.events),
            "cell_count": len(cells),
            "footprint_row_count": len(converted.footprint),
            "imts": list(converted.imts),
            "samples_above_range": converted.metrics.samples_above_range,
            "conversion_report": _report_with_qa(converted),
            "publication_state": PublicationState.DRAFT,
            "notes": _notes(converted, source),
            "updated_by": actor,
        },
    )

    for name, payload in hazard_build.tables(converted).items():
        attach_hazard_asset(hazard_set, name, payload, actor=actor)
    # The footprints go up from their files. They are the large tables, and
    # reading one back into memory to hand it along would undo the streaming
    # that produced it.
    for name, path in hazard_build.table_paths(converted).items():
        attach_hazard_asset_file(hazard_set, name, path, actor=actor)
    if job is not None:
        for name, payload in hazard_build.hazard_job.files(job).items():
            attach_hazard_asset(hazard_set, f"job_{name}", payload, actor=actor)
    attach_hazard_asset(
        hazard_set,
        "conversion_report.json",
        json.dumps(_report_with_qa(converted), indent=2, sort_keys=True).encode("utf-8"),
        actor=actor,
    )
    return hazard_set


@transaction.atomic
def attach(model_version: ModelVersion, hazard_set: HazardSet, *, actor=None) -> ModelVersion:
    """Point a model version at a hazard set, if the two can work together."""
    if model_version.country_code.upper() != hazard_set.country_code.upper():
        raise HazardRegistrationError(
            f"The hazard set is for {hazard_set.country_code} and the model version "
            f"for {model_version.country_code}. Attaching them would apply one "
            "country's ground motion to another's buildings."
        )
    if model_version.grid_id != hazard_set.grid_id:
        raise HazardRegistrationError(
            f"The hazard set was computed on {hazard_set.grid} and the model version "
            f"uses {model_version.grid}. Area-peril identifiers are only stable "
            "within a grid version, so the footprint would name the wrong cells."
        )

    demanded = set(model_version.vulnerability_set.imts_used)
    missing = sorted(demanded - set(hazard_set.imts))
    if missing:
        raise HazardRegistrationError(
            f"{hazard_set} carries {', '.join(hazard_set.imts) or 'no measures'}, "
            f"and this version's vulnerability functions demand {', '.join(missing)} "
            "as well. Attaching it would leave those functions answered by nothing "
            "and reporting zero, which is indistinguishable from no damage."
        )

    replaced = model_version.hazard_set
    model_version.hazard_set = hazard_set
    model_version.hazard_source_model = hazard_set.source_model
    model_version.hazard_source_licence = hazard_set.licence
    model_version.openquake_version = hazard_set.engine_version
    model_version.converter_version = hazard_build.HAZARD_BUILD_VERSION
    model_version.imts = sorted(set(model_version.imts) | set(hazard_set.imts))
    model_version.updated_by = actor
    model_version.save()
    # A set this one was rebuilt from may now be used by nothing, and its
    # footprint is then a second copy of this one's.
    if replaced is not None and replaced.pk != hazard_set.pk and hazard_set.rebuilt_from_id == replaced.pk:
        retire_footprints(replaced, actor=actor)
    return model_version


def _notes(converted: ConvertedHazard, source: SourceStatement) -> str:
    lines = [
        f"Event-based calculation on {converted.metadata.engine_version}, "
        f"{len(converted.events)} events over "
        f"{converted.metadata.effective_time:.0f} years "
        f"({len(converted.events) / converted.metadata.effective_time:.4f}/year).",
        f"Sources: {source.model}.",
    ]
    if converted.metrics.samples_below_range:
        lines.append(
            f"{converted.metrics.samples_below_range} ground-motion values fell "
            "below the lowest intensity bin and produce no footprint row. This is "
            "shaking too weak to damage anything and omitting it is what keeps the "
            "footprint a manageable size."
        )
    if converted.problems:
        lines.append("Problems found in conversion:")
        lines.extend(f"- {item}" for item in converted.problems)
    return "\n".join(lines)


# -- rebuilding a footprint from the stored calculation ---------------------------------

#: How a rebuilt set's version is marked: the set it came from, then ``-b`` and the
#: start of the fingerprint of the bins it was rebuilt against.
_REBUILT_SUFFIX = re.compile(r"-b[0-9a-f]{8}$")


def datastore_artifact(hazard_set: HazardSet) -> Artifact:
    """The stored datastore a set's footprint can be rebuilt from, or why there is none."""
    if not hazard_set.datastore_uri:
        raise HazardRegistrationError(
            f"{hazard_set.reference} was registered from exported tables rather than "
            "from a calculation run on this platform, so CASS holds no datastore to "
            "rebuild its footprint from. Run the hazard on the platform to get one."
        )
    artifact = Artifact.objects.filter(uri=hazard_set.datastore_uri).first()
    if artifact is None or not artifact.is_readable:
        state = artifact.state if artifact is not None else "missing"
        raise HazardRegistrationError(
            f"The calculation behind {hazard_set.reference} is no longer stored "
            f"({state}): stored calculations are kept 90 days, and this one's time has "
            "passed. Its footprint can only be rebuilt by running the hazard again."
        )
    return artifact


def rebuild_status(hazard_set: HazardSet) -> dict[str, Any]:
    """Whether a set's footprint matches the current intensity bins, and whether it can be rebuilt.

    Asked for every set the registry lists, so it reads the database and nothing
    else: the fingerprint of the current bins is a hash of a few hundred rows.
    """
    current = current_intensity_bins_checksum()
    recorded = hazard_set.intensity_bins_checksum
    try:
        artifact = datastore_artifact(hazard_set)
        available, reason, expires = True, "", artifact.expires_at
    except HazardRegistrationError as exc:
        available, reason, expires = False, str(exc), None
    rebuilt = hazard_set.rebuilds.filter(intensity_bins_checksum=current).first()
    return {
        "intensity_bins_current": None if not recorded else recorded == current,
        "datastore_available": available,
        "datastore_expires_at": expires.isoformat() if expires else None,
        "unavailable_reason": reason,
        "rebuilt_as": rebuilt.reference if rebuilt else None,
        "rebuilt_from": hazard_set.rebuilt_from.reference if hazard_set.rebuilt_from_id else None,
    }


def rebuilt_version(hazard_set: HazardSet) -> str:
    """The version a rebuild of this set against the current bins is registered under."""
    base = _REBUILT_SUFFIX.sub("", hazard_set.version)
    return f"{base[:22]}-b{current_intensity_bins_checksum()[:8]}"


def rebuild_refusal(hazard_set: HazardSet) -> str:
    """Why this set's footprint may not be rebuilt now, or an empty string where it may."""
    status = rebuild_status(hazard_set)
    if not status["datastore_available"]:
        return status["unavailable_reason"]
    if status["intensity_bins_current"]:
        return (
            f"{hazard_set.reference} was binned against the intensity bins CASS uses "
            "now, so rebuilding it would produce the same footprint."
        )
    if status["rebuilt_as"]:
        return (
            f"{hazard_set.reference} has already been rebuilt against the current "
            f"bins, as {status['rebuilt_as']}. Use that set."
        )
    return ""


def retire_footprints(hazard_set: HazardSet, *, actor=None) -> dict[str, Any]:
    """Remove a superseded set's footprints once nothing can use them.

    A rebuilt set carries its own footprint, counted into the current bins, and
    the set it replaced still holds the old one -- the largest table either
    stores, kept twice. The old one goes as soon as nothing needs it: the record
    stays, for lineage, and so does the calculation both were counted from, so
    the old footprint could be made again.

    Kept, and said so, while a model version still points at the old set or the
    set is published: a package built from it would otherwise have nothing to
    read.
    """
    from apps.artifacts import retention  # noqa: PLC0415

    if not hazard_set.rebuilds.exists():
        return {"retired": 0, "kept_because": "Nothing has replaced it."}
    if hazard_set.is_frozen:
        return {"retired": 0, "kept_because": "It is published, and published sets are kept whole."}
    users = list(
        ModelVersion.objects.filter(hazard_set=hazard_set).values_list("version", flat=True)
    )
    if users:
        return {
            "retired": 0,
            "kept_because": (
                f"Model version(s) {', '.join(users)} still use it. Attach the rebuilt "
                "set to them and the old footprint is removed."
            ),
        }
    links = ArtifactLink.objects.filter(
        subject_type="hazard_set",
        subject_id=hazard_set.id,
        role__startswith=f"{HAZARD_ROLE_PREFIX}footprint_",
        artifact__state=ArtifactState.REGISTERED,
    ).select_related("artifact")
    retired = 0
    for link in links:
        retention.expire(link.artifact, actor=actor)
        retired += 1
    return {"retired": retired, "kept_because": ""}


def report(hazard_set: HazardSet) -> dict[str, Any]:
    """What was registered, for the operator who ran it."""
    return {
        "reference": hazard_set.reference,
        "source_model": hazard_set.source_model,
        "engine_version": hazard_set.engine_version,
        "events": hazard_set.event_count,
        "effective_time": hazard_set.effective_time,
        "logic_tree_paths": hazard_set.logic_tree_paths,
        "annual_event_rate": hazard_set.annual_event_rate,
        "cells": hazard_set.cell_count,
        "footprint_rows": hazard_set.footprint_row_count,
        "imts": list(hazard_set.imts),
        "clips_the_hazard": hazard_set.clips_the_hazard,
        "publication_blockers": hazard_set.publication_blockers(),
    }


def _report_with_qa(converted) -> dict:
    """The conversion report, with the section 7 acceptance measurements in it.

    Measured here because this is where the conversion happens and the tables
    are in hand. Whether the numbers are acceptable is decided at the gate,
    against whatever tolerances are approved then -- which may be none, and
    then the report says so rather than passing itself.
    """
    report = dict(hazard_build.hazard_report(converted))
    report["qa"] = qa.measure(
        converted,
        tolerances=quality.tolerance_values(quality.approved_tolerances()),
    )
    return report
