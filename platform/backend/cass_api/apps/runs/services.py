"""Driving an analysis run across the Oasis boundary.

Milestone 2 of section 19 is the target: CASS publishes the OED, runs Oasis
file generation, then submits and monitors the loss job, with artifacts,
errors, reconciliation and lineage visible -- and without anyone opening the
native Oasis interface. This module is the orchestration half of that. The
protocol half is the adapter; nothing here speaks HTTP.

Five of the eleven stages in the analysis pipeline cross the engine boundary,
and those are the five this service performs:

``publish_oed``
    Create the Oasis portfolio and upload the frozen OED artifacts.

``generate_inputs``
    Create the analysis, post its settings, and let the pinned OasisLMF build
    the kernel and financial files.

``validate_inputs``
    Read back what Oasis's own lookup did with the exposure and reconcile it
    against the published record count.

``losses``
    Run the requested perspectives.

``collect``
    Pull the ORD outputs back into the artifact store and complete the run
    manifest.

The other six belong to workstreams that are not built yet: ``validate_exposure``
and ``enrich`` to the exposure services, ``keys`` and ``reconcile_keys`` to the
CASS keys service, and ``smoke`` and ``review`` to the operational gates. They
are recorded in the manifest as not performed rather than skipped silently,
because a manifest that omits them reads as though they passed.

The rule that shapes the failure paths: a run that stops must say why in terms
an analyst can act on, and must never leave a published partial result. Every
engine failure is caught, recorded against the stage that raised it with the
engine's own traceback in the detail, and the run is failed rather than left
holding a queue slot.
"""

from __future__ import annotations

import io
import json

from django.db import transaction

from apps.artifacts.models import Artifact, ArtifactLink, ArtifactState
from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.engines import oasis_adapter, oasis_model_triple
from apps.common.storage import bucket, get_store
from cass_adapters.base import AdapterError, EngineState
from cass_adapters.oasis import OasisPhase, PortfolioFileKind
from cass_core.artifacts import AccessPolicy, RetentionClass
from cass_core.runs import RunState
from cass_oed.perspectives import Perspective

from .models import AnalysisRun

#: Which Oasis portfolio endpoint each CASS artifact role belongs to. The order
#: matters: Oasis validates a reinsurance scope against the contracts it
#: references, and contracts against the accounts they attach to.
OASIS_FILE_BY_ROLE: dict[str, PortfolioFileKind] = {
    "oed_location": PortfolioFileKind.LOCATION,
    "oed_account": PortfolioFileKind.ACCOUNTS,
    "oed_reins_info": PortfolioFileKind.REINSURANCE_INFO,
    "oed_reins_scope": PortfolioFileKind.REINSURANCE_SCOPE,
}

#: Stages this service performs, in pipeline order.
ENGINE_STAGES = (
    "publish_oed",
    "generate_inputs",
    "validate_inputs",
    "losses",
    "collect",
)

#: Stages the analysis pipeline declares that this service does not perform,
#: with the reason each is outstanding. Recorded in the manifest so a reader
#: can tell "not done" from "done and clean".
UNPERFORMED_STAGES: dict[str, str] = {
    "validate_exposure": "Performed by the exposure workspace before the run is submitted.",
    "enrich": "Assumption sets are applied by the enrichment service; not yet wired to a run.",
    "keys": "The CASS keys service is not yet part of the analysis pipeline.",
    "reconcile_keys": "Awaits the CASS keys service; Oasis's own lookup is reconciled at validate_inputs.",
    "smoke": "The reduced-event pre-loss check is not yet implemented.",
    "review": "Operational and scientific result review is a separate governance step.",
}


class AnalysisExecutionError(Exception):
    """Raised when a run cannot be executed in its current shape."""


# -- settings ---------------------------------------------------------------

def build_analysis_settings(analysis_run: AnalysisRun) -> dict:
    """The analysis settings document Oasis is given.

    An explicitly configured document wins. Otherwise one is derived from the
    requested perspectives, because section 8 forbids generating a placeholder
    financial file to imply a perspective the source data does not support: if
    insured loss was not requested, the settings must not ask for it.
    """
    if analysis_run.analysis_settings:
        return dict(analysis_run.analysis_settings)

    requested = {str(item) for item in (analysis_run.perspectives or [])}
    if not requested:
        requested = {str(Perspective.GROUND_UP)}

    settings_document: dict = {
        "analysis_tag": str(analysis_run.run_id),
        "model_version_id": str(analysis_run.model_version_id),
        "gul_output": str(Perspective.GROUND_UP) in requested,
        "il_output": str(Perspective.INSURED) in requested,
        "ri_output": str(Perspective.REINSURANCE) in requested,
    }
    if analysis_run.run_currency:
        settings_document["model_settings"] = {"currency": analysis_run.run_currency}
    return settings_document


def settings_digest(document: dict) -> str:
    """A stable digest of the settings, for the run's ``settings_hash``."""
    from cass_core.checksums import hash_bytes

    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hash_bytes(canonical)


# -- reading the published OED ----------------------------------------------

def oed_payloads(analysis_run: AnalysisRun) -> list[tuple[str, str, bytes]]:
    """The published OED files as ``(role, filename, bytes)``, in upload order.

    Read through the artifact interface rather than a path, so this behaves the
    same on a workstation and against S3, and so the engine is handed bytes
    rather than a location it could not reach anyway.
    """
    version = analysis_run.exposure_version
    if not version.is_frozen:
        raise AnalysisExecutionError(
            "The exposure version is not published. Publish it before running an analysis, "
            "so the run points at immutable input."
        )

    store = get_store()
    links = {
        link.role: link
        for link in ArtifactLink.objects.filter(
            subject_type="exposure_version", subject_id=version.id, direction="input"
        ).select_related("artifact")
    }

    payloads: list[tuple[str, str, bytes]] = []
    for role in OASIS_FILE_BY_ROLE:
        link = links.get(role)
        if link is None or not link.artifact.is_readable:
            continue
        with store.open(link.artifact.uri) as handle:
            payloads.append((role, link.artifact.original_filename or f"{role}.csv", handle.read()))

    if not any(role == "oed_location" for role, _, _ in payloads):
        raise AnalysisExecutionError(
            "The exposure version has no readable location file, so there is nothing to analyse."
        )
    return payloads


# -- the stages -------------------------------------------------------------

def _publish_oed(analysis_run, engine, actor) -> dict:
    """Create the Oasis portfolio and upload the OED."""
    run = analysis_run.run
    payloads = oed_payloads(analysis_run)

    portfolio_id = engine.create_portfolio(f"cass-{run.id}")
    uploaded = {}
    for role, filename, payload in payloads:
        engine.upload_portfolio_file(
            portfolio_id, OASIS_FILE_BY_ROLE[role], filename, payload
        )
        uploaded[role] = len(payload)

    analysis_run.oasis_portfolio_id = str(portfolio_id)
    analysis_run.save(update_fields=["oasis_portfolio_id", "updated_at"])

    run.advance(
        "publish_oed",
        actor=actor,
        message=f"Published {len(uploaded)} OED files to Oasis portfolio {portfolio_id}.",
        metrics={"oasis_portfolio_id": portfolio_id, "bytes_by_role": uploaded},
    )
    return {"oasis_portfolio_id": portfolio_id, "files": uploaded}


def _generate_inputs(analysis_run, engine, actor, *, poll_interval, timeout) -> dict:
    """Create the analysis, post settings and build the kernel files."""
    run = analysis_run.run
    model = engine.find_model(*oasis_model_triple())

    analysis_id = engine.create_analysis(
        f"cass-{run.id}", int(analysis_run.oasis_portfolio_id), model.id
    )
    document = build_analysis_settings(analysis_run)
    engine.upload_settings(analysis_id, document)

    analysis_run.oasis_analysis_id = str(analysis_id)
    analysis_run.save(update_fields=["oasis_analysis_id", "updated_at"])
    run.settings_hash = settings_digest(document)
    run.save(update_fields=["settings_hash", "updated_at"])

    engine.generate_inputs(analysis_id)
    job = engine.poll(
        analysis_id,
        OasisPhase.INPUTS,
        interval=poll_interval,
        timeout=timeout,
        on_update=lambda observed: _note(run, "generate_inputs", observed, actor),
    )
    _require_success(job, engine, analysis_id, OasisPhase.INPUTS, "generate the Oasis input files")

    run.advance(
        "generate_inputs",
        actor=actor,
        message="Oasis generated the kernel and financial files.",
        metrics={"oasis_analysis_id": analysis_id, "oasis_model": model.as_dict()},
    )
    return {"oasis_analysis_id": analysis_id, "oasis_model": model.as_dict()}


def _validate_inputs(analysis_run, engine, actor) -> dict:
    """Reconcile what Oasis's own lookup did against the published exposure.

    Two lookups disagreeing is a defect rather than a business exception, so
    this fails the run rather than blocking it for approval. The approval gate
    that section 8 requires belongs to ``reconcile_keys``, which reconciles the
    CASS keys result before anything is submitted.
    """
    run = analysis_run.run
    analysis_id = int(analysis_run.oasis_analysis_id)
    report = engine.keys_report(analysis_id)

    summary = {
        "source": "oasis_lookup",
        "successes": _csv_rows(report["success"]),
        "failures": _csv_rows(report["errors"]),
        "validation_rows": _csv_rows(report["validation"]),
    }
    located = _location_count(analysis_run)
    summary["published_locations"] = located
    accounted = summary["successes"] + summary["failures"]
    summary["accounted"] = accounted
    reconciled = located == 0 or accounted == located

    analysis_run.keys_summary = summary
    analysis_run.keys_reconciled = reconciled
    analysis_run.save(update_fields=["keys_summary", "keys_reconciled", "updated_at"])

    if not reconciled:
        raise AnalysisExecutionError(
            "Oasis's lookup accounted for "
            f"{accounted} of {located} published locations. "
            "A location that is neither mapped nor reported as failed has been lost "
            "between the published OED and the model, so the run cannot continue."
        )

    run.advance(
        "validate_inputs",
        actor=actor,
        message=(
            f"Oasis mapped {summary['successes']} locations and reported "
            f"{summary['failures']} as unmappable."
        ),
        metrics=summary,
    )
    return summary


def _losses(analysis_run, engine, actor, *, poll_interval, timeout) -> dict:
    """Run the loss calculation."""
    run = analysis_run.run
    analysis_id = int(analysis_run.oasis_analysis_id)

    engine.run(analysis_id)
    job = engine.poll(
        analysis_id,
        OasisPhase.LOSSES,
        interval=poll_interval,
        timeout=timeout,
        on_update=lambda observed: _note(run, "losses", observed, actor),
    )
    _require_success(job, engine, analysis_id, OasisPhase.LOSSES, "calculate losses")

    run.advance(
        "losses",
        actor=actor,
        message="Oasis completed the loss calculation.",
        metrics={"raw_state": job.raw_state},
    )
    return {"raw_state": job.raw_state}


def _collect(analysis_run, engine, actor) -> dict:
    """Bring the outputs back and register them as an artifact."""
    run = analysis_run.run
    analysis_id = int(analysis_run.oasis_analysis_id)

    sink = io.BytesIO()
    engine.download_outputs(analysis_id, sink)
    payload = sink.getvalue()
    if not payload:
        raise AnalysisExecutionError(
            "Oasis reported the run complete but returned no output package."
        )

    store = get_store()
    ref = store.put_bytes(
        bucket("result"),
        f"analysis/{run.id}/oasis_output.tar.gz",
        payload,
        content_type="application/gzip",
        retention=RetentionClass.RESULT,
        access=AccessPolicy.PROJECT,
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
            "role": "oasis_output",
            "original_filename": "oasis_output.tar.gz",
            "created_by": actor,
            "updated_by": actor,
        },
    )
    ArtifactLink.objects.update_or_create(
        artifact=artifact,
        subject_type="analysis_run",
        subject_id=run.id,
        role="oasis_output",
        direction="output",
        defaults={"created_by": actor},
    )

    run.advance(
        "collect",
        actor=actor,
        message="Collected the Oasis output package.",
        metrics={"uri": ref.uri, "size_bytes": ref.size_bytes, "checksum": ref.checksum},
    )
    return {"uri": ref.uri, "checksum": ref.checksum, "size_bytes": ref.size_bytes}


# -- the driver -------------------------------------------------------------

def execute(
    analysis_run: AnalysisRun,
    *,
    adapter=None,
    actor=None,
    poll_interval: float = 5.0,
    timeout: float | None = None,
) -> AnalysisRun:
    """Run one analysis through Oasis, from published OED to collected output.

    Returns the run with its manifest complete. Raises on failure, having first
    recorded the failure on the run so the monitor can explain it.
    """
    run = analysis_run.run
    engine = adapter if adapter is not None else oasis_adapter()

    if run.run_state is RunState.DRAFT:
        run.transition(RunState.QUEUED, actor=actor)
    if run.run_state is RunState.BLOCKED:
        run.transition(RunState.QUEUED, actor=actor)
    if run.run_state is not RunState.QUEUED:
        raise AnalysisExecutionError(
            f"A run in state {run.state} cannot be executed. "
            "Only a draft, queued or approved-and-resumed run may start."
        )
    run.transition(RunState.RUNNING, actor=actor)

    manifest = dict(run.manifest or {})
    manifest["stages_not_performed"] = UNPERFORMED_STAGES

    #: Each step is named by the stage it performs rather than by the stage the
    #: run last completed. A failure has to be located where it happened: a
    #: loss calculation that dies reported against ``validate_inputs``, because
    #: that was the last stage to finish, sends the analyst to the wrong place.
    steps = (
        ("publish_oed", "portfolio", lambda: _publish_oed(analysis_run, engine, actor)),
        (
            "generate_inputs",
            "inputs",
            lambda: _generate_inputs(
                analysis_run, engine, actor, poll_interval=poll_interval, timeout=timeout
            ),
        ),
        ("validate_inputs", "keys", lambda: _validate_inputs(analysis_run, engine, actor)),
        (
            "losses",
            "losses",
            lambda: _losses(
                analysis_run, engine, actor, poll_interval=poll_interval, timeout=timeout
            ),
        ),
        ("collect", "output", lambda: _collect(analysis_run, engine, actor)),
    )

    stage = ENGINE_STAGES[0]
    try:
        # Before anything is submitted: section 18 refuses an untested engine
        # rather than producing a result nobody can defend.
        engine_version = engine.check_compatible()
        manifest["engine"] = engine_version.as_dict()

        for next_stage, key, step in steps:
            # Carried out of the loop so the failure handler can name the stage
            # that raised rather than the last one that finished.
            stage = next_stage
            manifest[key] = step()

        manifest["exposure_version"] = str(analysis_run.exposure_version_id)
        manifest["model_version"] = str(analysis_run.model_version_id)
        manifest["settings_hash"] = run.settings_hash
    except (AdapterError, AnalysisExecutionError) as exc:
        _fail(run, exc, stage=stage, actor=actor)
        raise

    run.manifest = manifest
    run.save(update_fields=["manifest", "updated_at"])
    run.transition(RunState.SUCCEEDED, actor=actor, stage="collect")

    audit.record(
        action=AuditAction.SUBMIT,
        subject_type="analysis_run",
        subject_id=run.id,
        actor=actor,
        project=run.project,
        subject_label=str(run),
        after={
            "state": run.state,
            "oasis_analysis_id": analysis_run.oasis_analysis_id,
            "settings_hash": run.settings_hash,
        },
    )
    return analysis_run


def cancel(analysis_run: AnalysisRun, *, adapter=None, actor=None) -> AnalysisRun:
    """Stop the run on the engine as well as in CASS.

    Section 11 requires cancellation to free the resource envelope. Marking the
    CASS record cancelled while an Oasis worker keeps going would leave the
    queue accounting wrong and the compute still spent.
    """
    run = analysis_run.run
    if run.run_state is not RunState.CANCELLING:
        run.transition(RunState.CANCELLING, actor=actor)

    if analysis_run.oasis_analysis_id:
        engine = adapter if adapter is not None else oasis_adapter()
        phase = (
            OasisPhase.LOSSES
            if run.stage in ("losses", "collect")
            else OasisPhase.INPUTS
        )
        try:
            engine.cancel(int(analysis_run.oasis_analysis_id), phase)
        except AdapterError as exc:
            # The engine could not be told. Say so rather than reporting a
            # clean cancellation that did not reach the worker.
            run.transition(
                RunState.FAILED,
                actor=actor,
                failure_summary="Cancellation could not be delivered to Oasis.",
                failure_detail=exc.detail or exc.summary,
            )
            raise

    run.transition(RunState.CANCELLED, actor=actor)
    audit.record(
        action=AuditAction.CANCEL,
        subject_type="analysis_run",
        subject_id=run.id,
        actor=actor,
        project=run.project,
        subject_label=str(run),
        after={"state": run.state},
    )
    return analysis_run


# -- helpers ----------------------------------------------------------------

def _note(run, stage: str, job, actor) -> None:
    """Record one engine observation against the run, for the monitor.

    Only meaningful changes are written. Polling a ten-minute calculation every
    five seconds would otherwise bury the stage history in a hundred identical
    rows.
    """
    from .models import RunStageEvent

    latest = (
        RunStageEvent.objects.filter(run=run, stage=stage).order_by("-created_at").first()
    )
    if latest is not None and latest.metrics.get("raw_state") == job.raw_state:
        return
    RunStageEvent.objects.create(
        run=run,
        stage=stage,
        state=run.state,
        message=job.message[:500],
        metrics={"raw_state": job.raw_state, "progress": job.progress},
        created_by=actor,
    )


def _require_success(job, engine, analysis_id, phase, what: str) -> None:
    """Turn a non-successful engine job into an intelligible failure."""
    if job.state is EngineState.SUCCEEDED:
        return
    if job.state is EngineState.CANCELLED:
        raise AnalysisExecutionError(f"Oasis cancelled the request to {what}.")
    detail = engine.failure_log(analysis_id, phase)
    raise AnalysisExecutionError(
        f"Oasis could not {what}. {job.message}"
        + (f"\n\nEngine log:\n{detail}" if detail else "")
    )


def _fail(run, exc: Exception, *, stage: str, actor) -> None:
    """Record a failure against the stage that raised it.

    ``transition`` sets the progress fraction from the stage it is given, and
    a failed stage did not complete, so the fraction is restored afterwards:
    a run that died part-way through the losses must not read as though the
    losses finished.
    """
    summary = getattr(exc, "summary", None) or str(exc)
    detail = getattr(exc, "detail", "") or ""
    progress = run.progress
    with transaction.atomic():
        run.transition(
            RunState.FAILED,
            actor=actor,
            stage=stage,
            failure_summary=summary,
            failure_detail=detail or str(exc),
            save=False,
        )
        run.progress = progress
        run.save()


def _csv_rows(text: str) -> int:
    """Count data rows in a CSV payload, ignoring the header and blank lines."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return max(0, len(lines) - 1) if lines else 0


def _location_count(analysis_run) -> int:
    """How many locations the published exposure version holds.

    Taken from the count validation recorded on the version, which is the same
    number the exposure workspace showed the analyst, so the two cannot
    disagree about what was published.
    """
    return int(analysis_run.exposure_version.location_count or 0)
