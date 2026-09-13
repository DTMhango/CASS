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

And the datastore comes back whole. OpenQuake can export a ground-motion field
as CSV, which is smaller and easier to read; the HDF5 datastore is the engine's
complete record of the calculation, checksummed once, from which every later
question -- events, sites, realisations, the GMF itself -- can be answered
without asking a server that may by then have been rebuilt.
"""

from __future__ import annotations

import io
import pathlib
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

    while True:
        last = engine.status(calculation_id)
        if last.state.is_terminal:
            break
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


def _export(hazard_run: HazardRun, engine, actor) -> dict[str, Any]:
    """Bring the datastore back and register it as an artifact."""
    run = hazard_run.run
    calculation_id = int(hazard_run.openquake_calculation_id)

    sink = io.BytesIO()
    engine.download_datastore(calculation_id, sink)
    payload = sink.getvalue()
    if not payload:
        raise HazardExecutionError(
            "OpenQuake reported the calculation complete but returned an empty "
            "datastore."
        )

    store = get_store()
    ref = store.put_bytes(
        bucket("hazard"),
        f"hazard/{run.id}/datastore.hdf5",
        payload,
        content_type="application/x-hdf5",
        # The class the retention policy already names for this exact object:
        # an OpenQuake GMF that expires after footprint acceptance unless
        # something governs it for longer.
        retention=RetentionClass.HAZARD_INTERMEDIATE,
        # Model scope, not project. A hazard run belongs to a model version
        # rather than a project, so PROJECT access on a run with no project
        # would make the datastore readable by nobody at all.
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

    # The datastore is the engine's complete record, but the converter reads
    # the CSV exports -- ground motion, events, ruptures for the timing, and the
    # realisation count that says whether one event set may stand alone.
    exports: dict[str, bytes] = {}
    for output_type in ("gmf_data", "events", "ruptures"):
        exports.update(engine.export(calculation_id, output_type))
    # A calculation on a single logic-tree branch has no logic tree to
    # describe, and OpenQuake publishes no realizations output for it. Its
    # absence is the ordinary case here, not a failure: the converter counts
    # the realisations the events themselves name.
    try:
        exports.update(engine.export(calculation_id, "realizations"))
    except AdapterError:
        pass

    hazard_set, converted = _register_hazard_set(hazard_run, exports, actor)

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
            f"{hazard_set.cell_count} cells."
        ),
        actor=actor,
        metrics={"checksum": ref.checksum, "hazard_set": registered},
    )
    return {
        "uri": ref.uri,
        "checksum": ref.checksum,
        "size_bytes": ref.size_bytes,
        "exports": sorted(exports),
        "hazard_set": registered,
    }


def _register_hazard_set(hazard_run: HazardRun, exports: dict[str, bytes], actor):
    """Turn the exported calculation into a hazard set a model version can use.

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
    with tempfile.TemporaryDirectory() as directory:
        for name, payload in exports.items():
            (pathlib.Path(directory) / name).write_bytes(payload)
        try:
            return hazard_registry.register(
                directory,
                country_code=hazard_run.grid.country_code,
                version=f"{model.version}-{str(hazard_run.run_id)[:8]}"[:32],
                source=source,
                grid=hazard_run.grid,
                label=f"{model.label}, {spec.name}",
                # Kept so a later job can be chained onto the same ground-motion
                # fields, which is what makes a reference comparison a comparison.
                calculation_id=hazard_run.openquake_calculation_id,
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
