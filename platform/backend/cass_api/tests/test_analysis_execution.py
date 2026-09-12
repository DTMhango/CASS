"""Milestone M2: an analysis run driven through Oasis from CASS.

Section 19 defines M2 as generating the keys and kernel files, running end to
end from CASS, inspecting reconciliation and outputs, and completing the
workflow without opening the native Oasis interface. These tests drive that
through the real adapter against a scripted Oasis server, so the service and
the protocol boundary are exercised together rather than one being stubbed out
from under the other.

The failure paths matter as much as the happy one. Section 12 requires a
failure to produce an intelligible state with a safe retry or cancellation
path, and a live Oasis will not produce a lookup that loses locations, an
untested version or an undeliverable cancellation on demand.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.artifacts.models import ArtifactLink
from apps.exposure.models import ExposureVersion
from apps.modelregistry.models import AreaPerilGrid, ModelVersion, VulnerabilitySet
from apps.runs.models import AnalysisRun, Run, RunKind
from apps.runs.services import (
    DEFAULT_ORD_OUTPUT,
    UNPERFORMED_STAGES,
    AnalysisExecutionError,
    build_analysis_settings,
    cancel,
    execute,
)
from cass_adapters.base import EngineRejected, IncompatibleEngine
from cass_adapters.oasis import OasisAdapter
from cass_core.runs import IllegalStageSequence, RunState

from .conftest import API

pytestmark = pytest.mark.django_db

BASE = "http://oasis-api:8000"


# -- a scripted Oasis server ------------------------------------------------

class FakeResponse:
    def __init__(self, status_code=200, body=None, text=None, chunks=None):
        self.status_code = status_code
        self._body = body
        self._text = text
        self._chunks = chunks or []

    def json(self):
        if self._body is None:
            raise ValueError("no JSON body")
        return self._body

    @property
    def text(self):
        return self._text if self._text is not None else ""

    def iter_content(self, chunk_size=1024):
        yield from self._chunks


class FakeSession:
    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def route(self, method, path, response):
        self.routes[(method, path)] = response
        return self

    def request(self, method, url, **kwargs):
        path = url[len(BASE) + 1 :]
        self.calls.append({"method": method, "path": path, **kwargs})
        entry = self.routes.get((method, path))
        if entry is None:
            return FakeResponse(404, text=f"no route for {method} {path}")
        if isinstance(entry, list):
            entry = entry.pop(0) if len(entry) > 1 else entry[0]
        if isinstance(entry, Exception):
            raise entry
        return entry

    def paths(self, method=None):
        return [c["path"] for c in self.calls if method is None or c["method"] == method]


def oasis_server(
    *,
    version="2.5.7",
    inputs_status="READY",
    loss_status="RUN_COMPLETED",
    lookup_rows=3,
    lookup_failures=0,
    output=b"ORD-package",
):
    """A server that carries one analysis all the way through.

    The keys files carry the real column set and, like a real lookup, one row
    per location per peril per coverage type. ``lookup_rows`` counts locations,
    not rows.
    """
    success_csv = (
        "loc_id,PortNumber,AccNumber,LocNumber,peril_id,coverage_type_id,tiv\n"
        + "".join(
            f"{n},1,ACC-1,LOC-{n},{peril},1,100000.0\n"
            for n in range(1, lookup_rows + 1)
            for peril in ("QEQ", "QSL")
        )
    )
    errors_csv = "loc_id,PortNumber,AccNumber,LocNumber,peril_id,message\n" + "".join(
        f"{n},1,ACC-1,LOC-{n},QEQ,no area peril\n"
        for n in range(lookup_rows + 1, lookup_rows + lookup_failures + 1)
    )
    return FakeSession(
        {
            ("GET", "healthcheck/"): FakeResponse(200, {"status": "OK"}),
            ("GET", "server_info/"): FakeResponse(
                200, {"version": version, "config": {"API_AUTH_TYPE": "disabled"}}
            ),
            ("GET", "v2/models/"): FakeResponse(
                200,
                {
                    "results": [
                        {"id": 4, "supplier_id": "KRE", "model_id": "EQ", "version_id": "1"}
                    ]
                },
            ),
            ("POST", "v2/portfolios/"): FakeResponse(201, {"id": 11}),
            ("POST", "v2/portfolios/11/location_file/"): FakeResponse(200, {}),
            ("POST", "v2/portfolios/11/accounts_file/"): FakeResponse(200, {}),
            ("POST", "v2/analyses/"): FakeResponse(201, {"id": 7}),
            ("POST", "v2/analyses/7/settings/"): FakeResponse(200, {}),
            ("POST", "v2/analyses/7/generate_inputs/"): FakeResponse(200, {}),
            ("POST", "v2/analyses/7/run/"): FakeResponse(200, {}),
            ("POST", "v2/analyses/7/cancel_generate_inputs/"): FakeResponse(200, {}),
            ("POST", "v2/analyses/7/cancel_analysis_run/"): FakeResponse(200, {}),
            ("GET", "v2/analyses/7/lookup_success_file/"): FakeResponse(200, text=success_csv),
            ("GET", "v2/analyses/7/lookup_errors_file/"): FakeResponse(200, text=errors_csv),
            ("GET", "v2/analyses/7/lookup_validation_file/"): FakeResponse(200, text=""),
            ("GET", "v2/analyses/7/output_file/"): FakeResponse(200, chunks=[output]),
            ("GET", "v2/analyses/7/"): [
                FakeResponse(200, {"id": 7, "status": inputs_status}),
                FakeResponse(200, {"id": 7, "status": inputs_status}),
                FakeResponse(200, {"id": 7, "status": loss_status}),
            ],
        }
    )


def engine_for(session):
    return OasisAdapter(BASE, session=session, retries=1, retry_delay=0, sleep=lambda _: None)


def run_it(analysis_run, session, **kwargs):
    return execute(
        analysis_run, adapter=engine_for(session), poll_interval=0, **kwargs
    )


# -- fixtures ---------------------------------------------------------------

@pytest.fixture()
def published_exposure(api, project, earthquake_location_csv) -> ExposureVersion:
    """A validated, published three-location Indonesian portfolio."""
    created = api.post(
        f"{API}/exposure-versions/",
        {"project": str(project.id), "name": "M2 portfolio", "cedant": "Test Cedant"},
        format="json",
    )
    assert created.status_code == 201, created.data
    exposure_id = created.data["id"]

    uploaded = api.post(
        f"{API}/exposure-versions/{exposure_id}/files/",
        {
            "kind": "location",
            "file": SimpleUploadedFile("loc.csv", earthquake_location_csv, "text/csv"),
        },
        format="multipart",
    )
    assert uploaded.status_code == 201, uploaded.data
    assert api.post(f"{API}/exposure-versions/{exposure_id}/validate/").status_code == 200
    assert api.post(f"{API}/exposure-versions/{exposure_id}/publish/").status_code == 200
    return ExposureVersion.objects.get(id=exposure_id)


@pytest.fixture()
def model_version(db, modeller) -> ModelVersion:
    grid = AreaPerilGrid.objects.create(
        country_code="ID",
        version="0.1.0",
        label="Indonesia prototype grid",
        base_resolution_deg="0.100000",
        refined_resolution_deg="0.025000",
        cell_count=0,
        created_by=modeller,
    )
    vulnerability = VulnerabilitySet.objects.create(
        country_code="ID",
        version="2026.0.0",
        source="GEM",
        function_count=1,
        created_by=modeller,
    )
    return ModelVersion.objects.create(
        country_code="ID",
        version="0.1.0-sa",
        label="Indonesia earthquake, SA-only prototype",
        grid=grid,
        vulnerability_set=vulnerability,
        oasis_version="2.5.7",
        created_by=modeller,
    )


@pytest.fixture()
def analysis_run(db, project, published_exposure, model_version, analyst) -> AnalysisRun:
    run = Run.objects.create(
        kind=RunKind.ANALYSIS,
        project=project,
        label="M2 baseline",
        created_by=analyst,
    )
    return AnalysisRun.objects.create(
        run=run,
        exposure_version=published_exposure,
        model_version=model_version,
        perspectives=["ground_up"],
        created_by=analyst,
    )


# -- the milestone ----------------------------------------------------------

def test_an_analysis_runs_end_to_end_without_the_native_oasis_interface(analysis_run, analyst):
    session = oasis_server()
    run_it(analysis_run, session, actor=analyst)

    analysis_run.refresh_from_db()
    run = analysis_run.run
    run.refresh_from_db()

    assert run.state == RunState.SUCCEEDED
    assert run.progress == 1.0
    assert analysis_run.oasis_portfolio_id == "11"
    assert analysis_run.oasis_analysis_id == "7"


def test_every_engine_stage_is_recorded_in_order(analysis_run, analyst):
    session = oasis_server()
    run_it(analysis_run, session, actor=analyst)

    stages = [
        event.stage
        for event in analysis_run.run.events.order_by("created_at")
        if event.stage
    ]
    engine_stages = [s for s in stages if s in
                     ("publish_oed", "generate_inputs", "validate_inputs", "losses", "collect")]
    assert engine_stages == sorted(
        engine_stages,
        key=["publish_oed", "generate_inputs", "validate_inputs", "losses", "collect"].index,
    )
    assert set(engine_stages) == {
        "publish_oed", "generate_inputs", "validate_inputs", "losses", "collect"
    }


def test_the_manifest_traces_the_result_to_its_versions(analysis_run, analyst):
    """Section 12: a result is traceable to immutable exposure, model and engine."""
    session = oasis_server()
    run_it(analysis_run, session, actor=analyst)
    manifest = Run.objects.get(id=analysis_run.run_id).manifest

    assert manifest["engine"]["version"] == "2.5.7"
    assert manifest["exposure_version"] == str(analysis_run.exposure_version_id)
    assert manifest["model_version"] == str(analysis_run.model_version_id)
    assert manifest["inputs"]["oasis_model"]["model_id"] == "EQ"
    assert manifest["settings_hash"]


def test_the_manifest_says_which_stages_were_not_performed(analysis_run, analyst):
    """A manifest that omits an unbuilt stage reads as though it passed."""
    session = oasis_server()
    run_it(analysis_run, session, actor=analyst)
    manifest = Run.objects.get(id=analysis_run.run_id).manifest

    assert manifest["stages_not_performed"] == UNPERFORMED_STAGES
    assert "reconcile_keys" in manifest["stages_not_performed"]


def test_the_output_package_is_registered_as_a_linked_artifact(analysis_run, analyst):
    session = oasis_server(output=b"ORD-package")
    run_it(analysis_run, session, actor=analyst)

    link = ArtifactLink.objects.get(
        subject_type="analysis_run", subject_id=analysis_run.run_id, role="oasis_output"
    )
    assert link.direction == "output"
    assert link.artifact.size_bytes == len(b"ORD-package")
    assert link.artifact.checksum
    assert link.artifact.project_id == analysis_run.run.project_id


def test_the_oed_is_uploaded_to_the_portfolio_rather_than_a_path(analysis_run, analyst):
    session = oasis_server()
    run_it(analysis_run, session, actor=analyst)

    upload = next(
        call for call in session.calls if call["path"] == "v2/portfolios/11/location_file/"
    )
    _, stream, content_type = upload["files"]["file"]
    assert content_type == "text/csv"
    assert b"PortNumber" in stream.read()


def test_keys_reconciliation_is_recorded_against_the_published_count(analysis_run, analyst):
    session = oasis_server(lookup_rows=2, lookup_failures=1)
    run_it(analysis_run, session, actor=analyst)

    analysis_run.refresh_from_db()
    summary = analysis_run.keys_summary
    assert analysis_run.keys_reconciled is True
    assert summary["mapped_locations"] == 2
    assert summary["failed_locations"] == 1
    assert summary["accounted_locations"] == 3
    assert summary["published_locations"] == 3


def test_many_keys_rows_for_one_location_still_reconcile_to_one_location(
    analysis_run, analyst
):
    """Oasis writes a row per location per peril per coverage type.

    The first live PiWind run failed here: twenty rows for ten locations were
    read as twenty locations, and a perfectly good run was stopped.
    """
    session = oasis_server(lookup_rows=3, lookup_failures=0)
    run_it(analysis_run, session, actor=analyst)

    analysis_run.refresh_from_db()
    summary = analysis_run.keys_summary
    assert summary["key_rows"] == 6
    assert summary["mapped_locations"] == 3
    assert analysis_run.keys_reconciled is True


def test_a_keys_file_without_a_location_column_cannot_be_reconciled(
    analysis_run, analyst
):
    """Section 8 does not allow proceeding on a mapping nobody could check."""
    session = oasis_server()
    session.route(
        "GET",
        "v2/analyses/7/lookup_success_file/",
        FakeResponse(200, text="areaperil_id,vulnerability_id\n1,1\n"),
    )
    with pytest.raises(AnalysisExecutionError, match="no location identifier column"):
        run_it(analysis_run, session, actor=analyst)


# -- settings ---------------------------------------------------------------

def test_settings_ask_only_for_the_perspectives_that_were_requested(analysis_run):
    """Section 8 forbids implying a perspective the source data does not support."""
    document = build_analysis_settings(analysis_run)
    assert document["gul_output"] is True
    assert document["il_output"] is False
    assert document["ri_output"] is False
    assert "il_summaries" not in document
    assert "ri_summaries" not in document


def test_a_requested_perspective_gets_a_summary_block_and_not_just_a_flag(analysis_run):
    """A bare flag is accepted by Oasis and produces an empty result package."""
    document = build_analysis_settings(analysis_run)
    assert document["gul_summaries"] == [{"id": 1, "ord_output": DEFAULT_ORD_OUTPUT}]


def test_each_requested_perspective_is_carried_into_the_settings(analysis_run):
    analysis_run.perspectives = ["ground_up", "insured", "reinsurance"]
    document = build_analysis_settings(analysis_run)
    assert [document["gul_output"], document["il_output"], document["ri_output"]] == [
        True, True, True
    ]
    assert {"gul_summaries", "il_summaries", "ri_summaries"} <= set(document)


def test_the_default_output_set_is_not_every_output_oasis_can_produce(analysis_run):
    """Section 11: the PiWind all-output run peaked near 21.6 GB."""
    ord_output = build_analysis_settings(analysis_run)["gul_summaries"][0]["ord_output"]
    assert ord_output["elt_moment"] is True
    assert "plt_sample" not in ord_output
    assert "psept_aep" not in ord_output


def test_an_explicitly_configured_settings_document_is_used_as_given(analysis_run):
    analysis_run.analysis_settings = {"gul_output": True, "custom": "value"}
    assert build_analysis_settings(analysis_run) == {"gul_output": True, "custom": "value"}


def test_the_settings_hash_is_recorded_for_reproducibility(analysis_run, analyst):
    session = oasis_server()
    run_it(analysis_run, session, actor=analyst)
    assert Run.objects.get(id=analysis_run.run_id).settings_hash.startswith("sha256:")


# -- refusals before anything is submitted ----------------------------------

def test_an_untested_engine_is_refused_before_a_portfolio_is_created(analysis_run, analyst):
    """Section 18: no result should exist that nobody can defend."""
    session = oasis_server(version="2.6.0")
    with pytest.raises(IncompatibleEngine):
        run_it(analysis_run, session, actor=analyst)

    assert "v2/portfolios/" not in session.paths("POST")
    run = Run.objects.get(id=analysis_run.run_id)
    assert run.state == RunState.FAILED
    assert "2.6.0" in run.failure_summary


def test_an_unpublished_exposure_version_is_refused(analysis_run, analyst, project, api):
    draft = ExposureVersion.objects.create(
        project=project, name="Draft", version=99, created_by=analyst
    )
    analysis_run.exposure_version = draft
    analysis_run.save()

    session = oasis_server()
    with pytest.raises(AnalysisExecutionError, match="not published"):
        run_it(analysis_run, session, actor=analyst)
    assert "v2/portfolios/" not in session.paths("POST")


def test_a_run_that_has_already_finished_cannot_be_executed_again(analysis_run, analyst):
    session = oasis_server()
    run_it(analysis_run, session, actor=analyst)
    with pytest.raises(AnalysisExecutionError, match="cannot be executed"):
        run_it(analysis_run, oasis_server(), actor=analyst)


# -- failures during the run ------------------------------------------------

def test_a_failed_input_generation_fails_the_run_with_the_engine_log(analysis_run, analyst):
    session = oasis_server(inputs_status="INPUTS_GENERATION_ERROR")
    session.route(
        "GET",
        "v2/analyses/7/input_generation_traceback_file/",
        FakeResponse(200, text="KeyError: AreaPerilID"),
    )
    with pytest.raises(AnalysisExecutionError):
        run_it(analysis_run, session, actor=analyst)

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.state == RunState.FAILED
    assert run.failure_stage == "generate_inputs"
    assert "KeyError: AreaPerilID" in run.failure_detail
    assert "v2/analyses/7/run/" not in session.paths("POST")


def test_a_failed_loss_calculation_fails_the_run(analysis_run, analyst):
    session = oasis_server(loss_status="RUN_ERROR")
    session.route(
        "GET", "v2/analyses/7/run_traceback_file/", FakeResponse(200, text="MemoryError")
    )
    with pytest.raises(AnalysisExecutionError):
        run_it(analysis_run, session, actor=analyst)

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.state == RunState.FAILED
    assert run.failure_stage == "losses"
    assert "MemoryError" in run.failure_detail


def test_a_lookup_that_loses_locations_stops_the_run(analysis_run, analyst):
    """A location neither mapped nor reported failed has gone missing."""
    session = oasis_server(lookup_rows=1, lookup_failures=0)
    with pytest.raises(AnalysisExecutionError, match="1 of 3 published locations"):
        run_it(analysis_run, session, actor=analyst)

    analysis_run.refresh_from_db()
    assert analysis_run.keys_reconciled is False
    assert Run.objects.get(id=analysis_run.run_id).state == RunState.FAILED
    assert "v2/analyses/7/run/" not in session.paths("POST")


def test_a_completed_run_that_returns_no_output_is_a_failure(analysis_run, analyst):
    session = oasis_server(output=b"")
    with pytest.raises(AnalysisExecutionError, match="no output package"):
        run_it(analysis_run, session, actor=analyst)
    assert Run.objects.get(id=analysis_run.run_id).state == RunState.FAILED


def test_a_failed_run_publishes_no_result(analysis_run, analyst):
    """Section 11: cancellation and failure never leave a published partial result."""
    session = oasis_server(loss_status="RUN_ERROR")
    with pytest.raises(AnalysisExecutionError):
        run_it(analysis_run, session, actor=analyst)

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.may_publish_results is False
    assert not ArtifactLink.objects.filter(
        subject_type="analysis_run", subject_id=run.id, role="oasis_output"
    ).exists()


def test_a_failed_run_may_be_retried(analysis_run, analyst):
    session = oasis_server(loss_status="RUN_ERROR")
    with pytest.raises(AnalysisExecutionError):
        run_it(analysis_run, session, actor=analyst)
    assert Run.objects.get(id=analysis_run.run_id).may_retry is True


# -- cancellation -----------------------------------------------------------

def test_cancelling_reaches_the_engine_and_not_only_the_cass_record(analysis_run, analyst):
    """Section 11: the resource envelope is only freed if the worker stops."""
    session = oasis_server()
    engine = engine_for(session)
    analysis_run.oasis_analysis_id = "7"
    analysis_run.save()
    analysis_run.run.transition(RunState.QUEUED, actor=analyst)
    analysis_run.run.transition(RunState.RUNNING, actor=analyst)

    cancel(analysis_run, adapter=engine, actor=analyst)

    assert "v2/analyses/7/cancel_generate_inputs/" in session.paths("POST")
    assert Run.objects.get(id=analysis_run.run_id).state == RunState.CANCELLED


def test_cancelling_a_loss_run_uses_the_loss_endpoint(analysis_run, analyst):
    session = oasis_server()
    analysis_run.oasis_analysis_id = "7"
    analysis_run.save()
    run = analysis_run.run
    run.transition(RunState.QUEUED, actor=analyst)
    run.transition(RunState.RUNNING, actor=analyst)
    run.advance("losses", actor=analyst)

    cancel(analysis_run, adapter=engine_for(session), actor=analyst)
    assert "v2/analyses/7/cancel_analysis_run/" in session.paths("POST")


def test_a_cancellation_that_cannot_reach_the_engine_is_not_reported_as_clean(
    analysis_run, analyst
):
    session = oasis_server()
    session.route("POST", "v2/analyses/7/cancel_generate_inputs/", FakeResponse(500, text="boom"))
    analysis_run.oasis_analysis_id = "7"
    analysis_run.save()
    analysis_run.run.transition(RunState.QUEUED, actor=analyst)
    analysis_run.run.transition(RunState.RUNNING, actor=analyst)

    with pytest.raises(EngineRejected):
        cancel(analysis_run, adapter=engine_for(session), actor=analyst)

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.state == RunState.FAILED
    assert "Cancellation could not be delivered" in run.failure_summary


def test_a_run_that_never_reached_the_engine_cancels_without_calling_it(analysis_run, analyst):
    session = oasis_server()
    analysis_run.run.transition(RunState.QUEUED, actor=analyst)
    cancel(analysis_run, adapter=engine_for(session), actor=analyst)

    assert session.paths("POST") == []
    assert Run.objects.get(id=analysis_run.run_id).state == RunState.CANCELLED


# -- stage bookkeeping ------------------------------------------------------

def test_a_stage_may_be_skipped_forward_but_never_repeated_backwards(analysis_run, analyst):
    """Backwards is not a retry -- a retry is a new run -- it is lost lineage."""
    run = analysis_run.run
    run.transition(RunState.QUEUED, actor=analyst)
    run.transition(RunState.RUNNING, actor=analyst)

    run.advance("losses", actor=analyst)
    with pytest.raises(IllegalStageSequence):
        run.advance("publish_oed", actor=analyst)


def test_advancing_records_progress_without_changing_the_lifecycle_state(
    analysis_run, analyst
):
    run = analysis_run.run
    run.transition(RunState.QUEUED, actor=analyst)
    run.transition(RunState.RUNNING, actor=analyst)

    run.advance("publish_oed", actor=analyst, message="Published.")
    run.refresh_from_db()
    assert run.state == RunState.RUNNING
    assert run.stage == "publish_oed"
    assert 0 < run.progress < 1


def test_repeated_identical_engine_observations_do_not_bury_the_stage_history(
    analysis_run, analyst
):
    """Polling a long calculation must not write a hundred identical rows."""
    session = oasis_server()
    session.route(
        "GET",
        "v2/analyses/7/",
        [
            FakeResponse(200, {"id": 7, "status": "INPUTS_GENERATION_STARTED"}),
            FakeResponse(200, {"id": 7, "status": "INPUTS_GENERATION_STARTED"}),
            FakeResponse(200, {"id": 7, "status": "INPUTS_GENERATION_STARTED"}),
            FakeResponse(200, {"id": 7, "status": "READY"}),
            FakeResponse(200, {"id": 7, "status": "RUN_COMPLETED"}),
        ],
    )
    run_it(analysis_run, session, actor=analyst)

    generating = analysis_run.run.events.filter(stage="generate_inputs")
    raw_states = [e.metrics.get("raw_state") for e in generating if e.metrics]
    assert raw_states.count("INPUTS_GENERATION_STARTED") == 1


# -- submitting through the API ---------------------------------------------

@pytest.fixture()
def oasis_is(monkeypatch):
    """Point the service's engine factory at a scripted server."""

    def _install(session):
        monkeypatch.setattr(
            "apps.runs.services.oasis_adapter", lambda **kwargs: engine_for(session)
        )
        return session

    return _install


def test_submitting_queues_the_run_and_returns_the_monitor_record(
    api, analysis_run, oasis_is
):
    """The response is the queued run, not the result of a loss calculation."""
    oasis_is(oasis_server())
    response = api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")

    assert response.status_code == 202
    assert Run.objects.get(id=analysis_run.run_id).state == RunState.SUCCEEDED


def test_a_submitted_run_records_its_output_artifact(api, analysis_run, oasis_is):
    oasis_is(oasis_server())
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")

    artifacts = api.get(f"{API}/runs/{analysis_run.run_id}/artifacts/")
    assert artifacts.status_code == 200
    assert [item["role"] for item in artifacts.data] == ["oasis_output"]


def test_a_submitted_run_explains_itself_through_the_run_monitor(
    api, analysis_run, oasis_is
):
    oasis_is(oasis_server())
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")

    events = api.get(f"{API}/runs/{analysis_run.run_id}/events/")
    assert events.status_code == 200
    assert {event["stage"] for event in events.data} >= {
        "publish_oed", "generate_inputs", "validate_inputs", "losses", "collect"
    }


def test_a_run_that_is_not_a_draft_cannot_be_submitted(api, analysis_run, oasis_is):
    oasis_is(oasis_server())
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")
    again = api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")
    assert again.status_code == 409
    assert "draft" in again.data["hint"]


def test_an_unpublished_exposure_version_is_refused_before_the_queue(
    api, analysis_run, project, analyst, oasis_is
):
    session = oasis_is(oasis_server())
    draft = ExposureVersion.objects.create(
        project=project, name="Draft", version=99, created_by=analyst
    )
    analysis_run.exposure_version = draft
    analysis_run.save()

    response = api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")
    assert response.status_code == 409
    assert "not published" in response.data["detail"]
    assert session.calls == []
    assert Run.objects.get(id=analysis_run.run_id).state == RunState.DRAFT


def test_someone_without_write_access_cannot_submit(
    client_for, outsider, analysis_run, oasis_is
):
    oasis_is(oasis_server())
    response = client_for(outsider).post(f"{API}/analysis-runs/{analysis_run.id}/submit/")
    assert response.status_code in (403, 404)
    assert Run.objects.get(id=analysis_run.run_id).state == RunState.DRAFT


def test_a_domain_failure_is_recorded_on_the_run_rather_than_raised_at_the_broker(
    analysis_run, oasis_is
):
    """The monitor is where a person reads this, not a Celery traceback."""
    from apps.runs.tasks import execute_analysis

    oasis_is(oasis_server(loss_status="RUN_ERROR"))
    result = execute_analysis(str(analysis_run.id))

    assert result["state"] == RunState.FAILED
    assert result["stage"] == "losses"
    assert Run.objects.get(id=analysis_run.run_id).state == RunState.FAILED


def test_cancelling_through_the_api_stops_the_engine_and_completes_the_run(
    api, analysis_run, analyst, oasis_is
):
    """CANCELLING is a request; the run only reaches CANCELLED once Oasis stops."""
    session = oasis_is(oasis_server())
    analysis_run.oasis_analysis_id = "7"
    analysis_run.save()
    run = analysis_run.run
    run.transition(RunState.QUEUED, actor=analyst)
    run.transition(RunState.RUNNING, actor=analyst)

    response = api.post(f"{API}/runs/{run.id}/cancel/")
    assert response.status_code == 200
    assert "v2/analyses/7/cancel_generate_inputs/" in session.paths("POST")
    assert Run.objects.get(id=run.id).state == RunState.CANCELLED
