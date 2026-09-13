"""Driving an analysis run across the Oasis boundary.

Milestone 2 of section 19 is the target: CASS publishes the OED, runs Oasis
file generation, then submits and monitors the loss job, with artifacts,
errors, reconciliation and lineage visible -- and without anyone opening the
native Oasis interface. This module is the orchestration half of that. The
protocol half is the adapter; nothing here speaks HTTP.

Seven of the eleven stages in the analysis pipeline are performed here:

``publish_oed``
    Create the Oasis portfolio and upload the frozen OED artifacts.

``keys``
    Map the published exposure to the model through the CASS keys service.
    Section 5 gives CASS keys this job; OasisLMF builds the kernel files from
    what it produces.

``reconcile_keys``
    The section 8 gate. Value that does not add up fails the run; value that
    adds up but could not be mapped holds it for an approval.

``generate_inputs``
    Create the analysis, post its settings, and let the pinned OasisLMF build
    the kernel and financial files.

``validate_inputs``
    Read back what Oasis's own lookup did and check every published location is
    accounted for, comparing it with the CASS keys result.

``losses``
    Run the requested perspectives.

``collect``
    Pull the ORD outputs back into the artifact store and complete the run
    manifest.

The remaining four belong to workstreams that are not built yet:
``validate_exposure`` and ``enrich`` to the exposure services, and ``smoke``
and ``review`` to the operational gates. They are recorded in the manifest as
not performed rather than skipped silently, because a manifest that omits them
reads as though they passed.

Two rules shape everything below. A run that stops must say why in terms an
analyst can act on, and must never leave a published partial result: every
engine failure is caught, recorded against the stage that raised it with the
engine's own traceback in the detail, and the run is failed rather than left
holding a queue slot. And a run held at a gate is not a run that failed --
blocking has its own state, its own fields and its own resume path, so a
waiting analysis is never reported as broken.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from django.db import transaction

from apps.artifacts.models import Artifact, ArtifactLink, ArtifactState
from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.engines import oasis_adapter, oasis_model_triple
from apps.common.storage import bucket, get_store
from apps.exposure import services as exposure_services
from apps.modelregistry.assets import ModelAssetError, load_grid, load_vulnerability
from cass_adapters.base import AdapterError, EngineState
from cass_adapters.oasis import OasisPhase, PortfolioFileKind
from cass_core.artifacts import AccessPolicy, RetentionClass
from cass_core.runs import RunState
from cass_keys.lookup import lookup as keys_lookup
from cass_oed import ord as ord_results
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
    "keys",
    "reconcile_keys",
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
    "smoke": "The reduced-event pre-loss check is not yet implemented.",
    "review": "Operational and scientific result review is a separate governance step.",
}


class AnalysisExecutionError(Exception):
    """Raised when a run cannot be executed in its current shape."""


class RunBlocked(Exception):
    """Raised when a governance gate stops a run pending an approval.

    Deliberately not an ``AnalysisExecutionError``. A blocked run has not
    failed: nothing is wrong with it, a person simply has to decide something
    before it goes on. Sharing a base class with failure is how the two end up
    being handled together and a waiting run gets reported as broken.
    """

    def __init__(self, summary: str, *, detail: str = "") -> None:
        super().__init__(summary)
        self.summary = summary
        self.detail = detail


# -- settings ---------------------------------------------------------------

#: The ORD outputs CASS asks for by default.
#:
#: Deliberately not every output Oasis can produce. Section 11 records that the
#: PiWind run reached roughly 21.6 GB at peak under an all-output configuration
#: and requires compute profiles to be declared rather than inherited from
#: maximum parallelism. These four answer the questions a portfolio result has
#: to answer -- what each event costs, what the exceedance curves look like, and
#: what the average annual loss is -- and a run that needs more says so
#: explicitly in its own settings document.
DEFAULT_ORD_OUTPUT: dict[str, bool] = {
    "elt_moment": True,
    "alt_period": True,
    "ept_mean_sample_aep": True,
    "ept_mean_sample_oep": True,
    "return_period_file": True,
}

#: Which settings keys carry each perspective.
_PERSPECTIVE_KEYS: dict[str, tuple[str, str]] = {
    str(Perspective.GROUND_UP): ("gul_output", "gul_summaries"),
    str(Perspective.INSURED): ("il_output", "il_summaries"),
    str(Perspective.REINSURANCE): ("ri_output", "ri_summaries"),
}


def build_analysis_settings(analysis_run: AnalysisRun, model=None) -> dict:
    """The analysis settings document Oasis is given.

    An explicitly configured document wins. Otherwise one is derived from the
    requested perspectives, because section 8 forbids generating a placeholder
    financial file to imply a perspective the source data does not support: if
    insured loss was not requested, the settings must not ask for it.

    Each requested perspective needs both its output flag and a summary block.
    A flag on its own is accepted by Oasis and produces nothing, which is worse
    than a refusal: the run succeeds and the result package is empty.

    ``model_supplier_id``, ``model_name_id`` and ``model_settings`` are required
    by the server's settings schema. The first two name the resolved Oasis
    model, so the document cannot drift from the model the analysis is bound
    to. ``model_settings`` is left empty on purpose: event set and occurrence
    choices belong to the model's own defaults, and filling them in here would
    put a CASS guess inside a model's configuration.
    """
    if analysis_run.analysis_settings:
        return dict(analysis_run.analysis_settings)

    requested = {str(item) for item in (analysis_run.perspectives or [])}
    if not requested:
        requested = {str(Perspective.GROUND_UP)}
    if str(Perspective.REINSURANCE) in requested:
        # Reinsurance is computed from the insured position: the engine applies
        # the contracts to the insured stream and needs it calculated and
        # summarised to write the reinsurance summary files at all. Asked for
        # reinsurance alone, it builds the reinsurance structures and then stops
        # on a missing insured summary index. So the insured stream is asked for
        # too, and its result is published beside the ceded one -- which is the
        # comparison a reinsurance analyst wants in front of them anyway.
        requested.add(str(Perspective.INSURED))

    supplier, model_name, _ = oasis_model_triple()
    settings_document: dict = {
        "version": "3",
        "analysis_tag": str(analysis_run.run_id),
        "model_version_id": str(analysis_run.model_version_id),
        "model_supplier_id": model.supplier_id if model is not None else supplier,
        "model_name_id": model.model_id if model is not None else model_name,
        "model_settings": {},
    }
    for perspective, (flag, summaries) in _PERSPECTIVE_KEYS.items():
        wanted = perspective in requested
        settings_document[flag] = wanted
        if wanted:
            settings_document[summaries] = [
                {"id": 1, "ord_output": dict(DEFAULT_ORD_OUTPUT)}
            ]
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
    document = build_analysis_settings(analysis_run, model)
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


def _keys(analysis_run, actor) -> dict:
    """Map the published exposure to the model through the CASS keys service.

    Section 5 gives CASS keys the mapping: CASS owns the business records and
    the immutable OED, CASS keys maps exposure to the model, and OasisLMF
    builds the kernel files from that. Running our own lookup before submission
    is also what makes ``validate_inputs`` meaningful later -- two independent
    lookups over the same exposure ought to agree, and a disagreement is worth
    knowing about.
    """
    run = analysis_run.run
    model_version = analysis_run.model_version

    grid = load_grid(model_version.grid)
    # What can answer a function is what the attached hazard set carries. A
    # function demanding a measure this hazard did not compute is reported as
    # unsupported, with the value behind it, rather than answered from a
    # measure it was not built for.
    hazard_set = model_version.hazard_set
    vulnerability = load_vulnerability(
        model_version.vulnerability_set,
        supported_imts=frozenset(hazard_set.imts) if hazard_set else None,
    )

    files = exposure_services.load_files(analysis_run.exposure_version)
    # The raw text, not the coerced values: section 5 keeps reported exposure
    # immutable, and the lookup does its own decimal conversion.
    locations = [dict(row.raw) for row in files.location.rows]

    result = keys_lookup(locations, grid=grid, vulnerability=vulnerability)
    report = result.report.as_dict()

    keys_csv = _keys_csv(result.records)
    errors_csv = _keys_csv(result.failures)
    stored = {
        "keys": _store_keys_file(run, "cass_keys", keys_csv, actor),
        "errors": _store_keys_file(run, "cass_keys_errors", errors_csv, actor),
    }

    summary = {
        "source": "cass_keys",
        "grid": result.grid_reference,
        "vulnerability": result.vulnerability_reference,
        "locations": len(locations),
        # Distinct locations, not records: a location produces one record per
        # coverage and sub-peril, so a record count answers a different
        # question and is the wrong thing to compare Oasis against.
        "mapped_locations": len({item.location_id for item in result.successes}),
        **report,
    }
    analysis_run.keys_summary = summary
    analysis_run.keys_reconciled = result.report.reconciled
    analysis_run.save(update_fields=["keys_summary", "keys_reconciled", "updated_at"])

    run.advance(
        "keys",
        actor=actor,
        message=(
            f"CASS keys mapped {report['mapped_tiv']} of {report['source_tiv']} TIV "
            f"against {result.grid_reference}."
        ),
        metrics={
            "grid": result.grid_reference,
            "vulnerability": result.vulnerability_reference,
            "record_count": len(result.records),
            "failure_count": len(result.failures),
        },
    )
    return {**summary, "artifacts": stored}


def _reconcile_keys(analysis_run, actor) -> dict:
    """The section 8 gate: no run proceeds on exposure nobody has accounted for.

    Two different things can be wrong here and they are not the same, so they
    do not get the same answer.

    Value that does not add up -- successful plus not-at-risk plus failed TIV
    not equal to the published source -- means the lookup lost some. Every
    combination is supposed to produce exactly one record, so that is a defect
    in CASS rather than a fact about the portfolio, and the run fails.

    Value that adds up but could not be mapped is a fact about the portfolio,
    and section 8 requires a person to decide whether the analysis is still
    worth running. The run blocks for an approval rather than failing, which is
    what ``RunState.BLOCKED`` exists for, and proceeds once a ``run_exception``
    approval is attached and granted.
    """
    run = analysis_run.run
    summary = analysis_run.keys_summary or {}
    source_tiv = Decimal(summary.get("source_tiv", "0"))
    failed_tiv = analysis_run.unmapped_tiv
    not_at_risk_tiv = Decimal(summary.get("not_at_risk_tiv", "0"))

    if not analysis_run.keys_reconciled:
        raise AnalysisExecutionError(
            "Keys TIV does not reconcile to the published exposure: "
            f"{summary.get('accounted_tiv')} accounted against {summary.get('source_tiv')} "
            f"published, a difference of {summary.get('difference')}. Every location, "
            "coverage and sub-peril is supposed to produce exactly one response, so "
            "value that has gone missing is a defect rather than a portfolio fact."
        )

    approval = analysis_run.exception_approval
    approved = approval is not None and approval.is_cleared

    # One definition of the gate, shared with the API through the model, so a
    # screen cannot show a run as clear while the service holds it.
    if not analysis_run.may_proceed_past_keys:
        raise RunBlocked(
            f"{failed_tiv} of {source_tiv} TIV could not be mapped to the model.",
            detail=_unmapped_detail(summary),
        )

    run.advance(
        "reconcile_keys",
        actor=actor,
        message=(
            f"Keys reconcile: {summary.get('mapped_tiv')} mapped, "
            f"{not_at_risk_tiv} not at risk, {failed_tiv} failed."
            + (" Proceeding under an approved run exception." if approved else "")
        ),
        metrics={
            "reconciled": True,
            "failed_tiv": str(failed_tiv),
            "approved_exception": str(approval.id) if approved else "",
        },
    )
    return {
        "reconciled": True,
        "failed_tiv": str(failed_tiv),
        "not_at_risk_tiv": str(not_at_risk_tiv),
        "approved_exception": str(approval.id) if approved else None,
    }


def _unmapped_detail(summary: Mapping[str, Any]) -> str:
    """Why the exposure could not be mapped, in the analyst's terms."""
    reasons = summary.get("tiv_by_reason") or {}
    if not reasons:
        return ""
    lines = [f"  {value} TIV: {reason}" for reason, value in sorted(reasons.items())]
    return "Unmapped value by reason:\n" + "\n".join(lines)


def _validate_inputs(analysis_run, engine, actor) -> dict:
    """Check what Oasis's own lookup did against the published exposure.

    Oasis runs its own lookup while generating inputs, so by this point two
    independent mappings of the same exposure exist: the CASS keys result from
    the ``keys`` stage, and this one. Both are checked here.

    Every published location must be accounted for -- mapped or reported as
    failed. One that is neither has been lost between the OED and the model,
    which is a defect rather than a business exception, so it fails rather than
    blocking. The approval gate belongs to ``reconcile_keys``, which runs
    before anything is submitted.

    Where the two lookups disagree about which locations map, the difference is
    recorded rather than raised. They are different implementations over
    different model data -- CASS keys against the CASS grid, Oasis against the
    model package's own keys server -- and a disagreement is a finding for the
    model owner, not necessarily a reason to stop.
    """
    run = analysis_run.run
    analysis_id = int(analysis_run.oasis_analysis_id)
    report = engine.keys_report(analysis_id)

    mapped = _keys_locations(report["success"], "successful")
    failed = _keys_locations(report["errors"], "failed")
    accounted = mapped | failed
    located = _location_count(analysis_run)

    summary = {
        "source": "oasis_lookup",
        "mapped_locations": len(mapped),
        "failed_locations": len(failed),
        "accounted_locations": len(accounted),
        "published_locations": located,
        # Row counts are one per location per peril per coverage type. They are
        # kept as evidence of how much the model matched, but they are not the
        # reconciliation: comparing them to a location count is what a keys
        # file that maps two perils per site would fail for no reason.
        "key_rows": _csv_rows(report["success"]),
        "error_rows": _csv_rows(report["errors"]),
        "validation_rows": _csv_rows(report["validation"]),
        "cass_keys_comparison": _cass_keys_comparison(analysis_run, mapped),
    }
    reconciled = located == 0 or len(accounted) == located

    if not reconciled:
        raise AnalysisExecutionError(
            "Oasis's lookup accounted for "
            f"{len(accounted)} of {located} published locations. "
            "A location that is neither mapped nor reported as failed has been lost "
            "between the published OED and the model, so the run cannot continue."
        )

    run.advance(
        "validate_inputs",
        actor=actor,
        message=(
            f"Oasis mapped {len(mapped)} locations and reported "
            f"{len(failed)} as unmappable."
        ),
        metrics=summary,
    )
    return summary


def _cass_keys_comparison(analysis_run, oasis_mapped: set[str]) -> dict:
    """How Oasis's lookup compares with the CASS keys result.

    Recorded rather than enforced. The two are different implementations over
    different model data -- CASS keys against the CASS grid, Oasis against the
    model package's own keys server -- so a disagreement is a finding for the
    model owner rather than automatically a reason to stop. It is stated
    explicitly when there was nothing to compare, because a silently absent
    comparison reads like one that passed.
    """
    summary = analysis_run.keys_summary or {}
    if summary.get("source") != "cass_keys":
        return {"compared": False, "reason": "The CASS keys stage did not run."}
    cass_mapped = int(summary.get("mapped_locations", 0))
    return {
        "compared": True,
        "cass_mapped_locations": cass_mapped,
        "oasis_mapped_locations": len(oasis_mapped),
        "agree": cass_mapped == len(oasis_mapped),
    }


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

    ingested = _ingest_results(analysis_run, payload, actor)

    run.advance(
        "collect",
        actor=actor,
        message=(
            "Collected the Oasis output package and published "
            f"{len(ingested['published'])} result set(s)."
        ),
        metrics={"uri": ref.uri, "size_bytes": ref.size_bytes, "checksum": ref.checksum},
    )
    # Returned rather than written to the run here: ``execute`` assembles the
    # manifest from what each stage returns and saves it at the end, so a write
    # made directly to ``run.manifest`` at this point would be overwritten.
    return {
        "uri": ref.uri,
        "checksum": ref.checksum,
        "size_bytes": ref.size_bytes,
        **ingested,
    }


def _ingest_results(analysis_run, payload: bytes, actor) -> dict:
    """Read the ORD tables and publish one result set per perspective.

    Without this the analysis stops at a tarball: the run succeeds, the
    artifact is registered, and the results workspace stays empty forever
    because nothing ever turned the package into numbers.

    A result arrives as a draft, never approved. Section 9 requires approved
    decision output to be operationally distinct from a research run, and a
    pipeline that published its own numbers as decision-grade would make the
    reviewer role a formality. A research-prototype model version produces a
    result marked research, which approval cannot lift.

    Failure to read the package is deliberately not failure of the run. The
    calculation happened, the output is stored and checksummed, and discarding
    hours of engine time because a table was not where CASS expected it would
    be the wrong trade -- so the reason is recorded on the run and the artifact
    stays available for somebody to read by hand.
    """
    from apps.results.models import ResultSet, ResultState

    run = analysis_run.run
    model_version = analysis_run.model_version

    try:
        package = ord_results.open_package(payload)
    except ord_results.OrdError as exc:
        return {"published": [], "results_not_published": str(exc)}

    published: list[dict] = []
    requested = [str(item) for item in (analysis_run.perspectives or [])]

    for perspective in requested:
        try:
            metrics = ord_results.metrics_for(package, perspective=perspective)
        except ord_results.OrdError as exc:
            published.append({"perspective": perspective, "not_published": str(exc)})
            continue

        label = (
            f"{analysis_run.exposure_version.name} "
            f"v{analysis_run.exposure_version.version} — "
            f"{perspective.replace('_', '-')}"
        )
        result, _ = ResultSet.objects.update_or_create(
            run=run,
            perspective=perspective,
            defaults={
                "project": run.project,
                "label": label,
                # Research output stays research whatever a reviewer does: a
                # prototype's number must not become a decision number by
                # approval alone.
                "state": (
                    ResultState.RESEARCH
                    if model_version.is_research_prototype
                    else ResultState.DRAFT
                ),
                "average_annual_loss": metrics.average_annual_loss,
                "standard_deviation": metrics.standard_deviation,
                "currency": analysis_run.run_currency
                or analysis_run.exposure_version.run_currency,
                "return_period_losses": metrics.return_period_losses,
                "model_version_reference": model_version.reference,
                "assumption_set_reference": (
                    analysis_run.enrichment_run.assumption_set.reference
                    if analysis_run.enrichment_run_id
                    and analysis_run.enrichment_run.assumption_set_id
                    else ""
                ),
                "valuation_date": analysis_run.exposure_version.valuation_date,
                "exposure_quality": _exposure_quality(analysis_run),
                "peril_scope": model_version.peril_scope,
                "material_exclusions": _material_exclusions(analysis_run, model_version),
                # The basis travels with the number, because a package carries
                # several and an analyst has to know which one they hold.
                "uncertainty_attribution": {"ord_basis": metrics.basis},
                "created_by": actor,
                "updated_by": actor,
            },
        )
        published.append(
            {
                "perspective": perspective,
                "result_set": str(result.id),
                "basis": metrics.basis,
            }
        )

        audit.record(
            action=AuditAction.CREATE,
            subject_type="result_set",
            subject_id=result.id,
            actor=actor,
            project=run.project,
            subject_label=str(result),
            after={"state": result.state, "perspective": perspective},
        )

    return {"published": published}


def _exposure_quality(analysis_run) -> dict:
    """What the keys lookup said about how much of the book was modelled."""
    summary = analysis_run.keys_summary or {}
    return {
        "location_count": analysis_run.exposure_version.location_count,
        "source_tiv": str(analysis_run.exposure_version.total_tiv or ""),
        "successful_tiv": summary.get("successful_tiv"),
        "not_at_risk_tiv": summary.get("not_at_risk_tiv"),
        "unmapped_tiv": summary.get("failed_tiv"),
        "keys_reconciled": analysis_run.keys_reconciled,
    }


def _material_exclusions(analysis_run, model_version) -> list[str]:
    """What this number does not include, from the exposure and the model.

    Two sources, and both matter. A sub-peril the portfolio covers but the
    release does not model is missing loss; a sub-peril the model version
    declares excluded is missing loss as well. Reporting only one of them would
    understate what the number leaves out.
    """
    exclusions: list[str] = list(analysis_run.exposure_version.unmodelled_subperils or [])
    for peril, treatment in (model_version.peril_scope or {}).items():
        if not isinstance(treatment, Mapping):
            continue
        if str(treatment.get("treatment", "")).lower() == "excluded":
            exclusions.append(peril)
    return sorted(set(exclusions))


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

    #: Where a resumed run picks up. A run blocked at a gate has already done
    #: everything before it, and redoing that would republish the OED and leave
    #: an orphan portfolio on the engine -- and ``advance`` would refuse the
    #: backwards move anyway.
    resume_at = run.stage if run.run_state is RunState.BLOCKED else ""

    if run.run_state is RunState.DRAFT:
        run.transition(RunState.QUEUED, actor=actor)
    if run.run_state is RunState.BLOCKED:
        run.transition(RunState.QUEUED, actor=actor, save=False)
        # The gate no longer applies. Its history stays in the stage events and
        # the audit trail; leaving it on the run would show a resumed analysis
        # as though it were still waiting.
        run.gate_summary = ""
        run.gate_detail = ""
        run.save()
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
        ("keys", "keys", lambda: _keys(analysis_run, actor)),
        ("reconcile_keys", "reconciliation", lambda: _reconcile_keys(analysis_run, actor)),
        (
            "generate_inputs",
            "inputs",
            lambda: _generate_inputs(
                analysis_run, engine, actor, poll_interval=poll_interval, timeout=timeout
            ),
        ),
        (
            "validate_inputs",
            "oasis_keys",
            lambda: _validate_inputs(analysis_run, engine, actor),
        ),
        (
            "losses",
            "losses",
            lambda: _losses(
                analysis_run, engine, actor, poll_interval=poll_interval, timeout=timeout
            ),
        ),
        ("collect", "output", lambda: _collect(analysis_run, engine, actor)),
    )

    pipeline = run.pipeline
    start = pipeline.index_of(resume_at) if resume_at else -1

    stage = ENGINE_STAGES[0]
    try:
        # Before anything is submitted: section 18 refuses an untested engine
        # rather than producing a result nobody can defend.
        engine_version = engine.check_compatible()
        manifest["engine"] = engine_version.as_dict()

        for next_stage, key, step in steps:
            if pipeline.index_of(next_stage) < start:
                continue
            # Carried out of the loop so the failure handler can name the stage
            # that raised rather than the last one that finished.
            stage = next_stage
            manifest[key] = step()

        manifest["exposure_version"] = str(analysis_run.exposure_version_id)
        manifest["model_version"] = str(analysis_run.model_version_id)
        manifest["settings_hash"] = run.settings_hash
    except RunBlocked as exc:
        _block(run, exc, stage=stage, actor=actor)
        raise
    except (AdapterError, AnalysisExecutionError, ModelAssetError) as exc:
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


def _block(run, exc: RunBlocked, *, stage: str, actor) -> None:
    """Hold the run at a gate and record what a person has to decide.

    A blocked run keeps its progress fraction and its evidence. It has not
    failed and it is not retried: it resumes from this stage once the approval
    exists, which is why nothing before this point is undone.
    """
    progress = run.progress
    with transaction.atomic():
        run.transition(
            RunState.BLOCKED,
            actor=actor,
            stage=stage,
            # Carried into the stage event as the message; the gate fields
            # below are what the run record itself keeps.
            failure_summary=exc.summary,
            save=False,
        )
        run.progress = progress
        run.gate_summary = exc.summary[:500]
        run.gate_detail = exc.detail
        run.save()

    audit.record(
        action=AuditAction.UPDATE,
        subject_type="analysis_run",
        subject_id=run.id,
        actor=actor,
        project=run.project,
        subject_label=str(run),
        after={"state": run.state, "stage": stage},
        detail=exc.summary,
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


def _keys_csv(records) -> bytes:
    """The keys file, in the shape section 8 asks for."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "LocID",
            "AccNumber",
            "LocNumber",
            "PerilID",
            "CoverageTypeID",
            "AreaPerilID",
            "VulnerabilityID",
            # A class spanning intensity measures produces one row per measure,
            # each carrying its share. Every other row carries 1, so the column
            # is meaningful without having to know which case a row is.
            "ChannelWeight",
            "Status",
            "Message",
        ],
        lineterminator="\n",
    )
    writer.writeheader()
    for record in records:
        writer.writerow(record.as_row())
    return buffer.getvalue().encode("utf-8")


def _store_keys_file(run, role: str, payload: bytes, actor) -> str:
    """Register one keys output against the run and return its URI.

    Kept as an artifact rather than a column because section 8 requires the
    keys and error files themselves to be retrievable, not merely counted: an
    analyst asking which locations failed needs the rows, not a total.
    """
    store = get_store()
    ref = store.put_bytes(
        bucket("portfolio"),
        f"analysis/{run.id}/{role}.csv",
        payload,
        content_type="text/csv",
        retention=RetentionClass.DIAGNOSTIC,
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
            "role": role,
            "original_filename": f"{role}.csv",
            "created_by": actor,
            "updated_by": actor,
        },
    )
    ArtifactLink.objects.update_or_create(
        artifact=artifact,
        subject_type="analysis_run",
        subject_id=run.id,
        role=role,
        direction="output",
        defaults={"created_by": actor},
    )
    return ref.uri


def _csv_rows(text: str) -> int:
    """Count data rows in a CSV payload, ignoring the header and blank lines."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return max(0, len(lines) - 1) if lines else 0


#: Columns an Oasis keys file may use for the location identity, best first.
#: ``LocNumber`` is the OED identifier and the one the published exposure
#: shares; ``loc_id`` is Oasis's own sequential index and only a fallback.
#:
#: A location number is qualified by its account wherever the file carries one.
#: OED makes the number unique within an account rather than within a
#: portfolio, so a book where several businesses each schedule a location 1
#: would otherwise reconcile three real locations down to one and report a
#: complete mapping as a shortfall.
_LOCATION_COLUMNS = ("locnumber", "loc_id", "locid")
_ACCOUNT_COLUMN = "accnumber"


def _keys_locations(text: str, what: str) -> set[str]:
    """The distinct locations a keys file accounts for.

    Oasis writes one row per location per peril per coverage type, so ten
    locations covering two perils produce twenty rows. Counting rows and
    comparing them to a location count fails a perfectly good run, which is
    exactly what the first live PiWind run did.

    A file whose header carries no recognisable location column cannot be
    reconciled at all, and section 8 does not allow proceeding on an
    unreconciled mapping, so that is raised rather than treated as empty.
    """
    if not (text or "").strip():
        return set()

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return set()

    columns = {name.strip().lower(): position for position, name in enumerate(header)}
    for candidate in _LOCATION_COLUMNS:
        if candidate in columns:
            index = columns[candidate]
            break
    else:
        raise AnalysisExecutionError(
            f"Oasis's {what} keys output has no location identifier column, so the "
            "mapping cannot be reconciled against the published exposure. "
            f"Columns present: {', '.join(header)}."
        )

    # Only when the identifier is the OED number. Oasis's own ``loc_id`` is
    # already unique across the portfolio, and qualifying it would invent a
    # distinction the file does not make.
    account = columns.get(_ACCOUNT_COLUMN) if candidate == "locnumber" else None

    identities: set[str] = set()
    for row in reader:
        if len(row) <= index or not row[index].strip():
            continue
        number = row[index].strip()
        if account is not None and len(row) > account and row[account].strip():
            identities.add(f"{row[account].strip()}/{number}")
        else:
            identities.add(number)
    return identities


def _location_count(analysis_run) -> int:
    """How many locations the published exposure version holds.

    Taken from the count validation recorded on the version, which is the same
    number the exposure workspace showed the analyst, so the two cannot
    disagree about what was published.
    """
    return int(analysis_run.exposure_version.location_count or 0)
