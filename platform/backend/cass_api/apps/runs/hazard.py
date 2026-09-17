"""Running a hazard calculation on OpenQuake.

Section 5's hazard pipeline is six stages: prepare a job from a saved
specification, validate its settings, submit it, monitor it, export the ground
motion fields, and benchmark them against approved curves.

Five of those are implemented here. The sixth is not, and saying so is the
point: ``benchmark`` is a governance gate whose approved curves do not exist
yet, and a stage that silently passed would turn a gate into a formality. It is
recorded as unperformed, the run succeeds at ``export``, and the gate stands
open where a reviewer can see it.

Two decisions shape the rest.

A job is rebuilt in the worker from the specification it names, and the
checksum is compared against the one recorded when the run was created. The
files are not carried on the run: a national source model is hundreds of
megabytes of NRML, and a job configuration held in a JSON column would put the
whole package in the database to be read once. What travels is the
specification id and the checksum, which is what makes the rebuild checkable --
a grid republished between the launch and the worker picking it up changes the
checksum, and the run refuses rather than computing something nobody reviewed.

And the datastore comes back whole, to disk rather than to memory. OpenQuake
can export a ground-motion field as CSV, but that is a second copy of the
motion written as text, and at national scale a copy the worker cannot hold.
The HDF5 datastore is the engine's complete record of the calculation,
checksummed once, from which every later question -- events, sites,
realisations, the GMF itself -- can be answered without asking a server that
may by then have been rebuilt; and it is read a slice at a time. Once CASS
holds it, the engine's copy is removed, so a calculation is stored once.
"""

from __future__ import annotations

import pathlib
import re
import tempfile
import time
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.artifacts.models import Artifact, ArtifactLink, ArtifactState
from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.engines import openquake_adapter
from apps.common.storage import bucket, get_store
from cass_adapters.base import AdapterError, EngineState
from cass_core.artifacts import AccessPolicy, RetentionClass
from cass_core.runs import RunState

from .models import HazardRun

#: Stages the hazard pipeline declares that this service does not perform, with
#: the reason each is outstanding. Recorded in the manifest so a reader can
#: tell "not done" from "done and clean".
UNPERFORMED_STAGES: dict[str, str] = {
    "benchmark": (
        "Comparison against approved hazard curves is a governance gate. The "
        "benchmark set for this country has not been approved, so the gate "
        "stands open rather than being passed automatically."
    ),
}


class HazardExecutionError(Exception):
    """Raised when a hazard run cannot be executed in its current shape."""


def execute(
    hazard_run: HazardRun,
    *,
    adapter=None,
    actor=None,
    poll_interval: float = 10.0,
    timeout: float | None = None,
) -> HazardRun:
    """Run one hazard calculation, from a saved job specification to a datastore.

    Returns the run with its manifest complete. Raises on failure, having first
    recorded the failure on the run so the monitor can explain it.
    """
    run = hazard_run.run
    engine = adapter if adapter is not None else openquake_adapter()

    if run.run_state is RunState.DRAFT:
        run.transition(RunState.QUEUED, actor=actor)
    if run.run_state is not RunState.QUEUED:
        raise HazardExecutionError(
            f"A run in state {run.state} cannot be executed. "
            "Only a draft or queued run may start."
        )
    run.transition(RunState.RUNNING, actor=actor)

    manifest = dict(run.manifest or {})
    manifest["stages_not_performed"] = UNPERFORMED_STAGES

    stage = "prepare"
    try:
        # Before anything is submitted: section 18 refuses an untested engine
        # rather than producing hazard nobody can defend.
        engine_version = engine.check_compatible()
        manifest["engine"] = engine_version.as_dict()
        hazard_run.openquake_version = engine_version.version
        hazard_run.image_digest = engine_version.image_digest
        hazard_run.save(
            update_fields=["openquake_version", "image_digest", "updated_at"]
        )

        # The assembled files live here rather than on the run: ``prepare``
        # builds them and ``submit`` sends them, and nothing in between needs
        # them to have been written down.
        prepared: dict[str, bytes] = {}

        for next_stage, key, step in (
            ("prepare", "job", lambda: _prepare(hazard_run, prepared, actor)),
            ("validate_settings", "settings", lambda: _validate(hazard_run, actor)),
            ("submit", "submission", lambda: _submit(hazard_run, prepared, engine, actor)),
            (
                "monitor",
                "calculation",
                lambda: _monitor(
                    hazard_run,
                    engine,
                    actor,
                    poll_interval=poll_interval,
                    timeout=timeout,
                ),
            ),
            ("export", "hazard", lambda: _export(hazard_run, engine, actor)),
        ):
            stage = next_stage
            manifest[key] = step()

        manifest["grid"] = hazard_run.grid.reference
        manifest["job_checksum"] = hazard_run.job_settings.get("job_checksum", "")
    except (AdapterError, HazardExecutionError) as exc:
        _fail(run, exc, stage=stage, actor=actor)
        raise

    run.manifest = manifest
    run.save(update_fields=["manifest", "updated_at"])
    run.transition(RunState.SUCCEEDED, actor=actor, stage="export")

    audit.record(
        action=AuditAction.SUBMIT,
        subject_type="hazard_run",
        subject_id=run.id,
        actor=actor,
        subject_label=str(run),
        after={
            "state": run.state,
            "openquake_calculation_id": hazard_run.openquake_calculation_id,
            "event_count": hazard_run.event_count,
        },
    )
    return hazard_run


def rebuild(hazard_run: HazardRun, *, actor=None) -> HazardRun:
    """Bin a hazard set's stored calculation again, against the current intensity bins.

    A footprint is ground motion counted into intensity bins, so when the bins
    change every footprint counted into the old ones is out of date -- but the
    ground motion is not. CASS keeps the calculation's datastore, and this reads
    it again instead of asking OpenQuake for hours of new ground motion.

    What comes out is a new hazard set rather than a changed one: a set a model
    version was built on keeps meaning what it meant. The new set names the one
    it replaces and shares its datastore rather than copying it, and the old
    set's footprint is removed as soon as no model version uses it.

    Such a run skips submitting and monitoring, because the calculation already
    happened; it prepares, then exports.
    """
    from apps.modelregistry import hazard as hazard_registry

    run = hazard_run.run
    source = hazard_run.rebuild_of
    if source is None:
        raise HazardExecutionError("This run names no hazard set to rebuild.")
    if run.run_state is RunState.DRAFT:
        run.transition(RunState.QUEUED, actor=actor)
    if run.run_state is not RunState.QUEUED:
        raise HazardExecutionError(
            f"A run in state {run.state} cannot be executed. "
            "Only a draft or queued run may start."
        )
    run.transition(RunState.RUNNING, actor=actor)

    manifest = dict(run.manifest or {})
    manifest["stages_not_performed"] = {
        **UNPERFORMED_STAGES,
        "validate_settings": "A rebuild runs no calculation, so there are no settings to validate.",
        "submit": "A rebuild reads the stored calculation rather than submitting a new one.",
        "monitor": "A rebuild reads the stored calculation rather than submitting a new one.",
    }
    manifest["rebuild_of"] = source.reference
    stage = "prepare"
    try:
        refusal = hazard_registry.rebuild_refusal(source)
        if refusal:
            raise HazardExecutionError(refusal)
        artifact = hazard_registry.datastore_artifact(source)
        # Linked as this run's input, so the retention sweep will not expire the
        # datastore out from under a rebuild that is reading it.
        ArtifactLink.objects.update_or_create(
            subject_type="hazard_run",
            subject_id=run.id,
            role="openquake_datastore",
            defaults={"artifact": artifact, "direction": "input"},
        )
        run.advance(
            "prepare",
            actor=actor,
            message=(
                f"Rebuilding {source.reference}'s footprint from its stored calculation "
                f"({artifact.size_bytes} bytes), against the current intensity bins."
            ),
        )

        stage = "export"
        source_statement = hazard_registry.SourceStatement(
            model=source.source_model,
            licence=source.licence,
            cleared=source.licence_cleared,
            reference=source.licence_note if source.licence_cleared else "",
            ground_motion_models=tuple(source.ground_motion_models or ()),
            checksum=source.source_model_checksum,
        )
        with tempfile.TemporaryDirectory(prefix="cass-rebuild-") as workspace:
            path = get_store().download(artifact.uri, pathlib.Path(workspace) / "datastore.hdf5")
            hazard_set, converted = hazard_registry.register_from_datastore(
                path,
                country_code=source.country_code,
                version=hazard_registry.rebuilt_version(source),
                source=source_statement,
                grid=source.grid,
                label=source.label,
                calculation_id=source.openquake_calculation_id,
                datastore_uri=source.datastore_uri,
                rebuilt_from=source,
                job_spec=source.job_spec,
                job_checksum=source.job_checksum,
                actor=actor,
            )
        # The same calculation, so the same facts about it as the set it replaces.
        type(hazard_set).objects.filter(pk=hazard_set.pk).update(
            licence_note=source.licence_note,
            openquake_calculation_removed=source.openquake_calculation_removed,
        )
        retired = hazard_registry.retire_footprints(source, actor=actor)

        hazard_run.gmf_bytes = artifact.size_bytes
        hazard_run.event_count = len(converted.events)
        hazard_run.site_count = hazard_set.cell_count
        hazard_run.save(update_fields=["gmf_bytes", "event_count", "site_count", "updated_at"])

        kept = retired["kept_because"]
        run.advance(
            "export",
            actor=actor,
            message=(
                f"Rebuilt as {hazard_set.reference}: {hazard_set.footprint_row_count} "
                f"footprint rows over {hazard_set.cell_count} cells. "
                + (
                    f"{source.reference}'s footprint was removed ({retired['retired']} "
                    "table(s)); its record and the calculation remain."
                    if retired["retired"]
                    else f"{source.reference}'s footprint is kept: {kept}"
                )
            ),
        )
        manifest["hazard"] = {
            "hazard_set": {
                "id": str(hazard_set.id),
                "reference": hazard_set.reference,
                "events": hazard_set.event_count,
                "cells": hazard_set.cell_count,
                "footprint_rows": hazard_set.footprint_row_count,
                "imts": list(hazard_set.imts),
                "samples_above_range": hazard_set.samples_above_range,
                "problems": list(converted.problems),
            },
            "rebuilt_from": source.reference,
            "retired_footprints": retired,
        }
        manifest["grid"] = source.grid.reference
    except (AdapterError, HazardExecutionError, hazard_registry.HazardRegistrationError) as exc:
        _fail(run, exc, stage=stage, actor=actor)
        if isinstance(exc, hazard_registry.HazardRegistrationError):
            raise HazardExecutionError(str(exc)) from exc
        raise

    run.manifest = manifest
    run.save(update_fields=["manifest", "updated_at"])
    run.transition(RunState.SUCCEEDED, actor=actor, stage="export")
    audit.record(
        action=AuditAction.CREATE,
        subject_type="hazard_set",
        subject_id=manifest["hazard"]["hazard_set"]["id"],
        actor=actor,
        subject_label=manifest["hazard"]["hazard_set"]["reference"],
        after={"rebuilt_from": source.reference, "run": str(run.id)},
        detail=f"Rebuilt {source.reference}'s footprint against the current intensity bins.",
    )
    return hazard_run


def cancel(hazard_run: HazardRun, *, adapter=None, actor=None) -> HazardRun:
    """Stop the calculation on the engine as well as in CASS.

    Section 11 counts the resource envelope as freed only once the engine has
    stopped, and an OpenQuake calculation left running would hold a worker for
    hours after the run showed as cancelled in the interface.
    """
    run = hazard_run.run
    engine = adapter if adapter is not None else openquake_adapter()

    if run.run_state not in (RunState.CANCELLING, RunState.RUNNING, RunState.QUEUED):
        return hazard_run
    if run.run_state is not RunState.CANCELLING:
        run.transition(RunState.CANCELLING, actor=actor)

    if hazard_run.openquake_calculation_id:
        try:
            engine.abort(int(hazard_run.openquake_calculation_id))
        except AdapterError as exc:
            # The CASS record must not say cancelled while the engine is still
            # computing. The run stays in CANCELLING with the reason on it.
            run.failure_summary = (
                "Cancellation did not reach OpenQuake. The calculation may still "
                "be running and holding its resource envelope."
            )
            run.failure_detail = getattr(exc, "detail", "") or str(exc)
            run.save(update_fields=["failure_summary", "failure_detail", "updated_at"])
            raise

    run.transition(RunState.CANCELLED, actor=actor)
    audit.record(
        action=AuditAction.CANCEL,
        subject_type="hazard_run",
        subject_id=run.id,
        actor=actor,
        subject_label=str(run),
        after={"state": run.state},
    )
    return hazard_run


# -- the stages -------------------------------------------------------------

def _prepare(
    hazard_run: HazardRun, prepared: dict[str, bytes], actor
) -> dict[str, Any]:
    """Rebuild the job from the specification this run names.

    The checksum recorded when the run was launched is the contract. A grid
    republished since then produces a different job, and running it would mean
    a calculation whose configuration nobody reviewed -- so the difference is
    refused here, with both checksums, rather than discovered afterwards by
    somebody comparing two footprints.
    """
    from apps.modelregistry import hazard_models
    from apps.modelregistry.assets import ModelAssetError
    from apps.modelregistry.models import HazardJobSpec

    settings = hazard_run.job_settings or {}
    # Looked up defensively: Django raises a ValidationError rather than
    # returning nothing for a malformed primary key, and a run that crashed
    # with "badly formed hexadecimal UUID string" would be exactly the
    # unintelligible state section 12 exists to prevent.
    reference = str(settings.get("spec") or "")
    try:
        spec = HazardJobSpec.objects.filter(pk=reference).first() if reference else None
    except (ValidationError, ValueError):
        spec = None
    if spec is None:
        raise HazardExecutionError(
            "The saved configuration this run was created from no longer "
            "exists, so there is nothing to rebuild the job from."
        )

    try:
        assembled = hazard_models.job_files(spec)
    except (hazard_models.HazardModelError, ModelAssetError) as exc:
        raise HazardExecutionError(str(exc)) from exc

    expected = settings.get("job_checksum") or ""
    if expected and assembled["job_checksum"] != expected:
        raise HazardExecutionError(
            "The configuration no longer resolves to the job this run was "
            f"launched as. It was checked as {expected[:12]} and now resolves "
            f"to {assembled['job_checksum'][:12]}, usually because the grid was "
            "republished. Resolve and save the configuration again."
        )

    prepared.clear()
    prepared.update(assembled["files"])

    hazard_run.run.advance(
        "prepare",
        message=(
            f"Rebuilt {len(prepared)} file(s) for {hazard_run.grid.reference} "
            f"at checksum {assembled['job_checksum'][:12]}."
        ),
        actor=actor,
    )
    return {
        "grid": hazard_run.grid.reference,
        "file_names": sorted(prepared),
        "job_checksum": assembled["job_checksum"],
        "site_join": assembled["site_join"],
    }


def _validate(hazard_run: HazardRun, actor) -> dict[str, Any]:
    """Refuse a configuration the specification already said was not runnable.

    The problems were found when the configuration was resolved, which is where
    an operator could still act on them. Submitting anyway would spend hours to
    rediscover what is already written down.
    """
    spec = hazard_run.job_settings or {}
    problems = spec.get("problems") or []
    blocking = [
        item for item in problems if str(item.get("severity", "")).lower() == "error"
    ]
    if blocking:
        first = blocking[0]
        raise HazardExecutionError(
            "The saved configuration has "
            f"{len(blocking)} problem(s) that stop a run. First: "
            f"{first.get('parameter', 'a setting')} — {first.get('message', '')}"
        )

    hazard_run.run.advance(
        "validate_settings",
        message=(
            f"{len(problems)} advisory problem(s); none stop the run."
            if problems
            else "No problems recorded against this configuration."
        ),
        actor=actor,
    )
    return {"problems": problems, "blocking": []}


def _submit(
    hazard_run: HazardRun, prepared: dict[str, bytes], engine, actor
) -> dict[str, Any]:
    """Upload the job and start the calculation."""
    if not prepared:
        raise HazardExecutionError(
            "The job was not assembled, so there is nothing to submit."
        )

    calculation_id = engine.submit(prepared)
    hazard_run.openquake_calculation_id = str(calculation_id)
    hazard_run.save(update_fields=["openquake_calculation_id", "updated_at"])

    run = hazard_run.run
    run.correlation_id = run.correlation_id or f"oq-{calculation_id}"
    run.save(update_fields=["correlation_id", "updated_at"])

    run.advance(
        "submit",
        message=f"OpenQuake accepted the job as calculation {calculation_id}.",
        actor=actor,
        metrics={"openquake_calculation_id": calculation_id},
    )
    return {"openquake_calculation_id": calculation_id}


def _monitor(
    hazard_run: HazardRun,
    engine,
    actor,
    *,
    poll_interval: float,
    timeout: float | None,
) -> dict[str, Any]:
    """Follow the calculation to a terminal state.

    A failure is read back from the engine in full before it is raised. The
    alternative -- reporting "the calculation failed" and leaving the reason on
    the server -- is precisely the unintelligible state section 12 forbids.
    """
    calculation_id = int(hazard_run.openquake_calculation_id)
    waited = 0.0
    last = None
    read = 0

    while True:
        last = engine.status(calculation_id)
        if last.state.is_terminal:
            break
        read = _follow(hazard_run.run, engine, calculation_id, last, read)
        if timeout is not None and waited >= timeout:
            raise HazardExecutionError(
                f"OpenQuake calculation {calculation_id} was still "
                f"{last.raw_state or 'running'} after {waited:.0f} seconds."
            )
        time.sleep(poll_interval)
        waited += poll_interval

    if last.state is EngineState.FAILED:
        raise HazardExecutionError(
            f"OpenQuake calculation {calculation_id} failed: "
            f"{last.message or 'the engine did not say why'}."
        ) from _with_detail(engine, calculation_id)

    if last.state is EngineState.CANCELLED:
        raise HazardExecutionError(
            f"OpenQuake calculation {calculation_id} was aborted on the engine."
        )

    hazard_run.run.advance(
        "monitor",
        message=f"Calculation {calculation_id} completed.",
        actor=actor,
        metrics={"raw_state": last.raw_state},
    )
    return {"state": str(last.state), "raw_state": last.raw_state}


#: OpenQuake's own progress line, as ``baselib.parallel`` writes it:
#: ``classical  35% [128 submitted, 12 queued]``. Read from the start of the
#: message and anchored on the bracketed counts, so neither the phase picks up
#: the log's own timestamp nor a line that merely mentions a percentage is
#: mistaken for progress.
_PERCENT = re.compile(
    r"^\s*(?P<phase>\w[\w .-]*?)\s+(?P<percent>\d{1,3})%\s+\[\s*\d+\s+submitted"
)


def _follow(run, engine, calculation_id: int, job, read: int) -> int:
    """Record how far into the calculation the engine says it has got.

    The status endpoint answers with a state and a description and no counts,
    so the log is the only progress OpenQuake offers. Each poll asks for the
    entries it has not read yet -- the engine takes a line offset, so this stays
    one small request however long the calculation runs -- and keeps the last
    percentage in them.

    That percentage restarts for every phase, so the phase name is kept with
    it. A bar that fills three times is unreadable; "classical 35%", then
    "computing gmfs 8%", is the calculation as the engine describes it.

    Never allowed to disturb the run. Progress is a courtesy to whoever is
    watching, and a monitor that abandoned a ten-hour calculation because a log
    request timed out would have cost far more than it showed.
    """
    try:
        entries = engine.log_entries(calculation_id, start=read)
    except Exception:  # pragma: no cover - defended above, not diagnosed here
        return read

    read += len(entries)
    for entry in reversed(entries):
        found = _PERCENT.search(entry[-1] if entry else "")
        if found:
            run.record_stage_progress(
                int(found.group("percent")) / 100, found.group("phase").strip()
            )
            return read

    # No line of its own, but a build of the engine that counts tasks in its
    # status would still be reporting something.
    if job.progress is not None:
        run.record_stage_progress(job.progress, job.message or "")
    return read


def _export(hazard_run: HazardRun, engine, actor) -> dict[str, Any]:
    """Bring the datastore back to disk, store it, and build the hazard set from it.

    Nothing here holds the calculation in memory. The datastore is streamed to a
    file, uploaded from that file, and read a slice at a time to build the
    footprint -- because a national calculation is gigabytes, and the worker
    that holds it is not. The CSV exports this stage used to fetch were a second
    copy of the ground motion written as text, and they are no longer asked for.

    Once the hazard set is registered, the engine's own copy of the datastore
    is removed: CASS holds the calculation from then on, and keeping it on the
    engine as well would store every national calculation twice. It is kept
    where the deployment asks for that, and whenever anything before
    registration fails, so a failed run stays readable where it ran.
    """
    run = hazard_run.run
    calculation_id = int(hazard_run.openquake_calculation_id)

    with tempfile.TemporaryDirectory(prefix="cass-hazard-") as workspace:
        path = pathlib.Path(workspace) / "datastore.hdf5"
        with path.open("wb") as sink:
            engine.download_datastore(calculation_id, sink)
        size = path.stat().st_size
        if not size:
            raise HazardExecutionError(
                "OpenQuake reported the calculation complete but returned an empty "
                "datastore."
            )

        store = get_store()
        ref = store.put_file(
            bucket("hazard"),
            f"hazard/{run.id}/datastore.hdf5",
            path,
            content_type="application/x-hdf5",
            # The class the retention policy already names for this exact
            # object: an OpenQuake GMF that expires after footprint acceptance
            # unless something governs it for longer.
            retention=RetentionClass.HAZARD_INTERMEDIATE,
            # Model scope, not project. A hazard run belongs to a model version
            # rather than a project, so PROJECT access on a run with no project
            # would make the datastore readable by nobody at all.
            access=AccessPolicy.MODEL,
        )
        if ref.size_bytes != size:
            raise HazardExecutionError(
                f"The datastore was {size} bytes on disk and {ref.size_bytes} bytes "
                "once stored, so the stored copy is not the calculation. Nothing was "
                "registered, and OpenQuake keeps its copy."
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
                "project": run.project,
                "role": "openquake_datastore",
            },
        )
        ArtifactLink.objects.update_or_create(
            subject_type="hazard_run",
            subject_id=run.id,
            role="openquake_datastore",
            defaults={"artifact": artifact, "direction": "output"},
        )

        hazard_set, converted = _register_hazard_set(hazard_run, path, ref.uri, actor)

    engine_copy = release_engine_copy(hazard_set, engine)

    hazard_run.gmf_bytes = ref.size_bytes
    hazard_run.event_count = len(converted.events)
    hazard_run.site_count = hazard_set.cell_count
    hazard_run.save(
        update_fields=["gmf_bytes", "event_count", "site_count", "updated_at"]
    )

    registered = {
        "id": str(hazard_set.id),
        "reference": hazard_set.reference,
        "events": hazard_set.event_count,
        "cells": hazard_set.cell_count,
        "footprint_rows": hazard_set.footprint_row_count,
        "imts": list(hazard_set.imts),
        "samples_above_range": hazard_set.samples_above_range,
        "problems": list(converted.problems),
    }
    run.advance(
        "export",
        message=(
            f"Datastore registered ({ref.size_bytes} bytes) and converted to hazard "
            f"set {hazard_set.reference}: {hazard_set.event_count} events over "
            f"{hazard_set.cell_count} cells. {engine_copy['message']}"
        ),
        actor=actor,
        metrics={"checksum": ref.checksum, "hazard_set": registered},
    )
    return {
        "uri": ref.uri,
        "checksum": ref.checksum,
        "size_bytes": ref.size_bytes,
        "hazard_set": registered,
        "engine_copy": engine_copy,
    }


def release_engine_copy(hazard_set, engine) -> dict[str, Any]:
    """Remove OpenQuake's copy of a calculation CASS now holds, unless told not to.

    Never allowed to fail the run. The hazard set is registered by the time this
    is asked, so a removal the engine refuses costs disk rather than a result,
    and the run says so rather than pretending the copy is gone.

    The set records the removal. OpenQuake reuses calculation numbers once its
    own database is reset, so a number whose calculation is gone may later name
    a different one -- and a comparison chained onto that would read somebody
    else's ground motion while looking exactly like a comparison.
    """
    from django.conf import settings

    calculation_id = int(hazard_set.openquake_calculation_id)
    if getattr(settings, "CASS_OPENQUAKE_KEEP_CALCULATIONS", False):
        return {
            "removed": False,
            "message": (
                f"OpenQuake keeps calculation {calculation_id} as well, because this "
                "installation is set to keep calculations on the engine."
            ),
        }
    try:
        engine.remove(calculation_id)
    except AdapterError as exc:
        return {
            "removed": False,
            "message": (
                f"OpenQuake's copy of calculation {calculation_id} could not be "
                f"removed ({exc}), so the calculation is stored twice until it is."
            ),
        }
    type(hazard_set).objects.filter(pk=hazard_set.pk).update(
        openquake_calculation_removed=True
    )
    hazard_set.openquake_calculation_removed = True
    return {
        "removed": True,
        "message": (
            f"OpenQuake's copy of calculation {calculation_id} was removed, so CASS "
            "holds the only copy."
        ),
    }


def restore_calculation(
    hazard_set,
    engine,
    *,
    poll_interval: float = 10.0,
    timeout: float | None = None,
) -> int:
    """Run a calculation removed from OpenQuake again, and prove it is the same one.

    A chained job -- the OpenQuake reference comparison -- reads the ground
    motion from the engine's own copy of the hazard calculation, and that copy
    is removed once CASS holds the datastore. Rather than keep every national
    calculation twice for a comparison run now and then, the calculation is run
    again when one is asked for.

    It is not trusted to match. The engine writes the same ground motion for the
    same inputs -- measured on the Jakarta-Bandung calculation, which run twice
    gave the same rows to the bit -- so the new datastore's fingerprint is
    compared with the one recorded when the set was registered, and so is the
    engine's own checksum of its inputs. A calculation that does not reproduce
    is removed again and refused, because a comparison chained onto different
    ground motion would look exactly like a comparison.

    Returns the new calculation's number. The caller removes it when finished.
    """
    from apps.modelregistry import hazard_models
    from apps.modelregistry.assets import ModelAssetError
    from cass_converter import datastore

    spec = hazard_set.job_spec
    if spec is None or not hazard_set.job_checksum or not hazard_set.ground_motion_digest:
        raise HazardExecutionError(
            f"OpenQuake's copy of the calculation behind {hazard_set.reference} was "
            "removed, and the set does not record the configuration and ground-motion "
            "fingerprint needed to run it again and check it is the same. Run the "
            "hazard again to compare against it."
        )
    try:
        assembled = hazard_models.job_files(spec)
    except (hazard_models.HazardModelError, ModelAssetError) as exc:
        raise HazardExecutionError(str(exc)) from exc
    if assembled["job_checksum"] != hazard_set.job_checksum:
        raise HazardExecutionError(
            f"The configuration behind {hazard_set.reference} now resolves to a "
            f"different job ({assembled['job_checksum'][:12]}, not "
            f"{hazard_set.job_checksum[:12]}), usually because its grid or model was "
            "republished. Running it would compute different ground motion."
        )

    calculation = engine.submit(assembled["files"])
    waited = 0.0
    state = engine.status(calculation)
    while not state.state.is_terminal:
        if timeout is not None and waited >= timeout:
            engine.abort(calculation)
            raise HazardExecutionError(
                f"Running the calculation again was still {state.raw_state or 'running'} "
                f"after {waited:.0f} seconds."
            )
        time.sleep(poll_interval)
        waited += poll_interval
        state = engine.status(calculation)
    if state.state is not EngineState.SUCCEEDED:
        raise HazardExecutionError(
            f"Running the calculation again {state.raw_state or 'did not finish'}: "
            f"{state.message or 'the engine did not say why'}."
        )

    with tempfile.TemporaryDirectory(prefix="cass-restore-") as workspace:
        path = pathlib.Path(workspace) / "datastore.hdf5"
        with path.open("wb") as sink:
            engine.download_datastore(calculation, sink)
        try:
            stated = datastore.provenance(path)
            digest = datastore.ground_motion_digest(path)
        except datastore.DatastoreError as exc:
            _remove_quietly(engine, calculation)
            raise HazardExecutionError(str(exc)) from exc

    differs = []
    if digest != hazard_set.ground_motion_digest:
        differs.append("its ground motion")
    if hazard_set.calculation_checksum and stated["checksum"] != hazard_set.calculation_checksum:
        differs.append("the engine's checksum of its inputs")
    if differs:
        _remove_quietly(engine, calculation)
        raise HazardExecutionError(
            f"The calculation run again for {hazard_set.reference} differs from the "
            f"stored one in {' and '.join(differs)}, so nothing was chained onto it. "
            "The engine may have been upgraded since the set was computed."
        )
    return calculation


def _remove_quietly(engine, calculation: int) -> bool:
    """Remove a calculation, reporting rather than raising if the engine refuses."""
    try:
        engine.remove(calculation)
    except AdapterError:
        return False
    return True


def _register_hazard_set(hazard_run: HazardRun, datastore_path, datastore_uri: str, actor):
    """Turn the calculation's datastore into a hazard set a model version can use.

    Registered as the run's final act rather than left to a separate command,
    because a hazard run whose output nothing can consume is the gap that left
    footprints on a modeller's disk and never in the registry. The source
    statement is the uploaded model's own: its label, licence and the note that
    records what cleared it, since a hazard set that could not say where its
    earthquakes came from would be untraceable the first time a number moved.
    """
    from apps.modelregistry import hazard as hazard_registry
    from apps.modelregistry.models import HazardJobSpec

    settings = hazard_run.job_settings or {}
    spec = (
        HazardJobSpec.objects.select_related("model")
        .filter(pk=settings.get("spec") or None)
        .first()
    )
    if spec is None:
        raise HazardExecutionError(
            "The saved configuration behind this run no longer exists, so the "
            "hazard cannot be attributed to a source model."
        )
    model = spec.model

    source = hazard_registry.SourceStatement(
        model=f"{model.label} ({model.reference})",
        licence=model.licence,
        cleared=model.licence_cleared,
        reference=model.licence_note if model.licence_cleared else "",
        checksum=model.archive_checksum,
        note=f"Configuration {spec.name}, job checksum {settings.get('job_checksum', '')[:12]}.",
    )
    try:
        return hazard_registry.register_from_datastore(
            datastore_path,
            country_code=hazard_run.grid.country_code,
            version=f"{model.version}-{str(hazard_run.run_id)[:8]}"[:32],
            source=source,
            grid=hazard_run.grid,
            label=f"{model.label}, {spec.name}",
            # Kept so a later job can be chained onto the same ground-motion
            # fields, which is what makes a reference comparison a comparison.
            calculation_id=hazard_run.openquake_calculation_id,
            datastore_uri=datastore_uri,
            job_spec=spec,
            job_checksum=settings.get("job_checksum", ""),
            actor=actor,
        )
    except hazard_registry.HazardRegistrationError as exc:
        raise HazardExecutionError(str(exc)) from exc


def _with_detail(engine, calculation_id: int) -> Exception:
    """The engine's own account of a failure, as the cause of ours."""
    try:
        return HazardExecutionError(engine.failure_detail(calculation_id))
    except Exception:  # pragma: no cover - the summary is the important half
        return HazardExecutionError("OpenQuake did not return a traceback.")


def _fail(run, exc: Exception, *, stage: str, actor) -> None:
    """Record a failure against the stage that raised it."""
    summary = getattr(exc, "summary", None) or str(exc)
    cause = exc.__cause__
    detail = getattr(exc, "detail", "") or (str(cause) if cause else "") or str(exc)
    progress = run.progress
    with transaction.atomic():
        run.transition(
            RunState.FAILED,
            actor=actor,
            stage=stage,
            failure_summary=summary[:500],
            failure_detail=detail,
            save=False,
        )
        # A failed stage did not complete, so the fraction is restored: a run
        # that died part-way through the monitor must not read as though the
        # calculation finished.
        run.progress = progress
        run.save()
