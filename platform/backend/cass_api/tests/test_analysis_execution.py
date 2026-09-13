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

from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.artifacts.models import ArtifactLink
from apps.audit.models import Approval
from apps.exposure.models import ExposureVersion
from apps.modelregistry.assets import (
    ModelAssetError,
    attach_grid_cells,
    attach_vulnerability_mapping,
)
from apps.modelregistry.models import (
    AreaPerilGrid,
    ModelVersion,
    PublicationState,
    VulnerabilitySet,
)
from apps.runs.models import AnalysisRun, Run, RunKind
from apps.runs.services import (
    DEFAULT_ORD_OUTPUT,
    ENGINE_STAGES,
    UNPERFORMED_STAGES,
    AnalysisExecutionError,
    RunBlocked,
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


def run_oasis(analysis_run, session, actor):
    """Run to completion and return the manifest."""
    run_it(analysis_run, session, actor=actor)
    return Run.objects.get(id=analysis_run.run_id).manifest


# -- fixtures ---------------------------------------------------------------

@pytest.fixture()
def published_exposure(api, project, earthquake_location_csv) -> ExposureVersion:
    """A validated, published three-location Indonesian portfolio."""
    created = api.post(
        f"{API}/exposure-versions/",
        {"project": str(project.id), "name": "M2 portfolio"},
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


#: A grid covering the three locations of ``earthquake_location_csv``:
#: Jakarta and Bandung in one cell, Surabaya in another.
GRID_CELLS = (
    b"AreaPerilID,MinLatitude,MaxLatitude,MinLongitude,MaxLongitude,CountryCode,Offshore\n"
    b"1,-7.0,-6.0,106.0,108.0,ID,false\n"
    b"2,-8.0,-7.0,112.0,113.0,ID,false\n"
)

#: A grid that covers Jakarta and Bandung but not Surabaya, so one location
#: falls outside the domain and its TIV cannot be mapped.
PARTIAL_GRID_CELLS = (
    b"AreaPerilID,MinLatitude,MaxLatitude,MinLongitude,MaxLongitude,CountryCode,Offshore\n"
    b"1,-7.0,-6.0,106.0,108.0,ID,false\n"
)

#: Building and contents functions with no taxonomy restriction, so the test
#: portfolio maps without needing occupancy codes to line up.
VULNERABILITY_MAPPING = (
    b"VulnerabilityID,CoverageTypeID,RequiredIMT,OccupancyCodes,ConstructionCodes,Label\n"
    b"1,1,SA(0.3),,,Generic building\n"
    b"3,3,SA(0.3),,,Generic contents\n"
)


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
    attach_grid_cells(grid, GRID_CELLS, actor=modeller)

    vulnerability = VulnerabilitySet.objects.create(
        country_code="ID",
        version="2026.0.0",
        source="GEM",
        function_count=2,
        created_by=modeller,
    )
    attach_vulnerability_mapping(vulnerability, VULNERABILITY_MAPPING, actor=modeller)

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
    engine_stages = [s for s in stages if s in ENGINE_STAGES]
    assert engine_stages == sorted(engine_stages, key=list(ENGINE_STAGES).index)
    assert set(engine_stages) == set(ENGINE_STAGES)


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

    not_performed = manifest["stages_not_performed"]
    assert set(UNPERFORMED_STAGES) <= set(not_performed)
    assert "enrich" in not_performed
    # No model package is readable from the test process, so the smoke check
    # could not choose any events, and it says so rather than passing.
    assert "footprint index" in not_performed["smoke"]
    # Stages that ran must not be listed.
    for performed in ("validate_exposure", "keys", "reconcile_keys", "review"):
        assert performed not in not_performed


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


def test_the_oasis_lookup_is_reconciled_against_the_published_count(analysis_run, analyst):
    session = oasis_server(lookup_rows=2, lookup_failures=1)
    manifest = run_oasis(analysis_run, session, analyst)

    summary = manifest["oasis_keys"]
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
    summary = run_oasis(analysis_run, session, analyst)["oasis_keys"]
    assert summary["key_rows"] == 6
    assert summary["mapped_locations"] == 3


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


def test_reinsurance_asks_for_the_insured_position_it_is_derived_from(analysis_run):
    """The engine applies contracts to the insured stream, not to ground-up loss.

    Asked for reinsurance alone it builds the reinsurance structures and then
    stops on a missing insured summary index, several minutes into a run.
    """
    analysis_run.perspectives = ["reinsurance"]

    document = build_analysis_settings(analysis_run)

    assert document["ri_output"] is True
    assert document["il_output"] is True
    assert "il_summaries" in document
    assert document["gul_output"] is False


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


def test_a_failure_longer_than_the_column_is_still_recorded(analysis_run, analyst):
    """An engine's answer is not ours to size.

    A kernel traceback came back longer than the failure column, the write was
    refused by the database, and the run stayed RUNNING for ever with its
    failure nowhere -- the unintelligible state the monitor exists to prevent.
    """
    run = Run.objects.get(id=analysis_run.run_id)
    run.transition(RunState.QUEUED, actor=analyst)
    run.transition(RunState.RUNNING, actor=analyst)

    run.transition(
        RunState.FAILED,
        actor=analyst,
        stage="losses",
        failure_summary="x" * 2000,
        failure_detail="y" * 20000,
    )

    run.refresh_from_db()
    assert run.state == RunState.FAILED
    assert len(run.failure_summary) == 500
    assert run.events.filter(state=RunState.FAILED).exists()


def test_an_oasis_lookup_that_loses_locations_stops_the_run(analysis_run, analyst):
    """A location neither mapped nor reported failed has gone missing.

    This is the Oasis-side check, so it fails rather than blocking: the CASS
    keys gate has already passed by this point, and two lookups losing track of
    a location between them is a defect, not a portfolio fact to approve.
    """
    session = oasis_server(lookup_rows=1, lookup_failures=0)
    with pytest.raises(AnalysisExecutionError, match="1 of 3 published locations"):
        run_it(analysis_run, session, actor=analyst)

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.state == RunState.FAILED
    assert run.failure_stage == "validate_inputs"
    assert "v2/analyses/7/run/" not in session.paths("POST")

    # The CASS keys result stands: it reconciled, and this failure is about
    # what Oasis did afterwards.
    analysis_run.refresh_from_db()
    assert analysis_run.keys_reconciled is True
    assert analysis_run.keys_summary["source"] == "cass_keys"


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
    # The keys files are outputs too: section 8 wants the rows retrievable, not
    # just a count of how many locations failed.
    assert {item["role"] for item in artifacts.data} == {
        "cass_keys",
        "cass_keys_errors",
        "oasis_output",
    }
    assert all(item["checksum"] for item in artifacts.data)


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


# -- the CASS keys stage and the section 8 gate ------------------------------

@pytest.fixture()
def partial_grid(model_version, modeller):
    """Replace the grid with one that does not reach Surabaya."""
    attach_grid_cells(model_version.grid, PARTIAL_GRID_CELLS, actor=modeller)
    return model_version


def approve_exception(analysis_run, requester, approver):
    """Attach a cleared run-exception approval to the run."""
    approval = Approval.objects.create(
        gate=Approval.Gate.RUN_EXCEPTION,
        decision=Approval.Decision.APPROVED,
        subject_type="analysis_run",
        subject_id=analysis_run.id,
        requested_by=requester,
        decided_by=approver,
        rationale="Surabaya is outside the pilot grid; proceed on the remainder.",
    )
    analysis_run.exception_approval = approval
    analysis_run.save(update_fields=["exception_approval", "updated_at"])
    return approval


def test_cass_keys_maps_the_published_exposure_before_anything_is_submitted(
    analysis_run, analyst
):
    """Section 5 gives CASS keys the mapping, not the engine."""
    session = oasis_server()
    run_it(analysis_run, session, actor=analyst)

    analysis_run.refresh_from_db()
    summary = analysis_run.keys_summary
    assert summary["source"] == "cass_keys"
    assert summary["grid"] == "id-grid-0.1.0"
    assert summary["locations"] == 3
    assert summary["mapped_locations"] == 3
    assert analysis_run.keys_reconciled is True


def test_the_keys_and_error_files_are_retrievable_not_merely_counted(
    analysis_run, analyst
):
    """An analyst asking which locations failed needs the rows, not a total."""
    session = oasis_server()
    run_it(analysis_run, session, actor=analyst)

    roles = set(
        ArtifactLink.objects.filter(
            subject_type="analysis_run", subject_id=analysis_run.run_id
        ).values_list("role", flat=True)
    )
    assert {"cass_keys", "cass_keys_errors"} <= roles


def test_every_unit_of_source_value_lands_in_exactly_one_bucket(analysis_run, analyst):
    """Section 8: successful, not-at-risk and failed TIV reconcile to source."""
    session = oasis_server()
    run_it(analysis_run, session, actor=analyst)

    summary = AnalysisRun.objects.get(id=analysis_run.id).keys_summary
    assert Decimal(summary["source_tiv"]) == Decimal("9900000")
    assert Decimal(summary["accounted_tiv"]) == Decimal(summary["source_tiv"])
    assert Decimal(summary["difference"]) == 0


def test_unmapped_value_holds_the_run_at_the_gate_rather_than_failing_it(
    analysis_run, analyst, partial_grid
):
    """A location outside the grid is a portfolio fact, not a defect."""
    session = oasis_server()
    with pytest.raises(RunBlocked):
        run_it(analysis_run, session, actor=analyst)

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.state == RunState.BLOCKED
    assert run.stage == "reconcile_keys"
    assert "could not be mapped" in run.gate_summary
    assert "outside the area-peril grid domain" in run.gate_detail
    assert run.failure_summary == ""


def test_a_blocked_run_has_not_failed_and_is_not_retried(
    analysis_run, analyst, partial_grid
):
    session = oasis_server()
    with pytest.raises(RunBlocked):
        run_it(analysis_run, session, actor=analyst)

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.may_retry is False
    assert run.may_publish_results is False
    assert 0 < run.progress < 1


def test_a_blocked_run_never_reaches_the_engine_loss_stage(
    analysis_run, analyst, partial_grid
):
    session = oasis_server()
    with pytest.raises(RunBlocked):
        run_it(analysis_run, session, actor=analyst)
    assert "v2/analyses/7/generate_inputs/" not in session.paths("POST")
    assert "v2/analyses/7/run/" not in session.paths("POST")


def test_an_approved_exception_releases_the_gate(
    analysis_run, analyst, reviewer, partial_grid
):
    session = oasis_server()
    with pytest.raises(RunBlocked):
        run_it(analysis_run, session, actor=analyst)

    approve_exception(analysis_run, analyst, reviewer)
    run_it(analysis_run, session, actor=analyst)

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.state == RunState.SUCCEEDED
    assert run.gate_summary == ""


def test_resuming_after_approval_does_not_republish_the_portfolio(
    analysis_run, analyst, reviewer, partial_grid
):
    """Redoing publish_oed would leave an orphan portfolio on the engine."""
    session = oasis_server()
    with pytest.raises(RunBlocked):
        run_it(analysis_run, session, actor=analyst)
    before = session.paths("POST").count("v2/portfolios/")

    approve_exception(analysis_run, analyst, reviewer)
    run_it(analysis_run, session, actor=analyst)

    assert before == 1
    assert session.paths("POST").count("v2/portfolios/") == 1


def test_the_manifest_records_the_approval_the_run_proceeded_under(
    analysis_run, analyst, reviewer, partial_grid
):
    session = oasis_server()
    with pytest.raises(RunBlocked):
        run_it(analysis_run, session, actor=analyst)
    approval = approve_exception(analysis_run, analyst, reviewer)
    run_it(analysis_run, session, actor=analyst)

    manifest = Run.objects.get(id=analysis_run.run_id).manifest
    assert manifest["reconciliation"]["approved_exception"] == str(approval.id)


def test_a_rejected_exception_does_not_release_the_gate(
    analysis_run, analyst, reviewer, partial_grid
):
    session = oasis_server()
    with pytest.raises(RunBlocked):
        run_it(analysis_run, session, actor=analyst)

    approval = approve_exception(analysis_run, analyst, reviewer)
    approval.decision = Approval.Decision.REJECTED
    approval.save(update_fields=["decision"])

    with pytest.raises(RunBlocked):
        run_it(analysis_run, session, actor=analyst)
    assert Run.objects.get(id=analysis_run.run_id).state == RunState.BLOCKED


def test_the_gate_is_one_definition_shared_with_the_api(
    api, analysis_run, analyst, partial_grid
):
    """A screen must not show a run as clear while the service holds it."""
    session = oasis_server()
    with pytest.raises(RunBlocked):
        run_it(analysis_run, session, actor=analyst)

    shown = api.get(f"{API}/analysis-runs/{analysis_run.id}/").data
    assert shown["may_proceed_past_keys"] is False
    assert shown["run_detail"]["state"] == RunState.BLOCKED


def test_a_model_version_with_no_grid_cells_cannot_run(
    analysis_run, analyst, model_version
):
    """An empty grid maps nothing, so this must stop rather than proceed."""
    ArtifactLink.objects.filter(
        subject_type="area_peril_grid", subject_id=model_version.grid_id
    ).delete()

    session = oasis_server()
    with pytest.raises(ModelAssetError, match="area peril grid cells"):
        run_it(analysis_run, session, actor=analyst)

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.state == RunState.FAILED
    assert run.failure_stage == "keys"


def test_a_blocked_run_reports_itself_as_blocked_rather_than_failed_to_the_task(
    analysis_run, partial_grid, oasis_is
):
    from apps.runs.tasks import execute_analysis

    oasis_is(oasis_server())
    result = execute_analysis(str(analysis_run.id))

    assert result["state"] == RunState.BLOCKED
    assert result["stage"] == "reconcile_keys"
    assert "could not be mapped" in result["summary"]


# -- configuring an analysis through the API --------------------------------
#
# The analysis builder of section 3 has to be able to make the thing it
# submits. Until it could, a run existed only where a test or a shell made one,
# which is the shape of a workflow that is finished everywhere except the end a
# person touches.

@pytest.fixture()
def published_model_version(model_version, modeller) -> ModelVersion:
    """The catalogue only offers published versions, so a builder only sees these."""
    model_version.publication_state = PublicationState.PUBLISHED
    model_version.save()
    return model_version


def configure(api, project, exposure, model_version, **extra):
    body = {
        "project": str(project.id),
        "exposure_version": str(exposure.id),
        "model_version": str(model_version.id),
        "perspectives": ["ground_up"],
    }
    body.update(extra)
    return api.post(f"{API}/analysis-runs/", body, format="json")


def test_configuring_an_analysis_creates_the_run_that_carries_it(
    api, project, published_exposure, published_model_version
):
    """A detail record and a lifecycle record, made as one act."""
    response = configure(api, project, published_exposure, published_model_version)

    assert response.status_code == 201, response.data
    analysis = AnalysisRun.objects.get(id=response.data["id"])
    assert analysis.run.kind == RunKind.ANALYSIS
    assert analysis.run.project_id == project.id
    assert analysis.run.state == RunState.DRAFT
    # The response carries the monitor record, so the builder can link
    # straight to the run it just made.
    assert response.data["run_detail"]["state"] == RunState.DRAFT


def test_a_configured_analysis_can_then_be_submitted(
    api, project, published_exposure, published_model_version, oasis_is
):
    """The whole path, from an empty builder to a completed run."""
    oasis_is(oasis_server())
    created = configure(api, project, published_exposure, published_model_version)

    submitted = api.post(f"{API}/analysis-runs/{created.data['id']}/submit/")

    assert submitted.status_code == 202
    analysis = AnalysisRun.objects.get(id=created.data["id"])
    assert analysis.run.state == RunState.SUCCEEDED


def test_an_unpublished_exposure_version_cannot_be_configured(
    api, project, published_model_version, analyst
):
    draft = ExposureVersion.objects.create(
        project=project, name="Draft", version=41, created_by=analyst
    )
    response = configure(api, project, draft, published_model_version)

    assert response.status_code == 400
    assert "not published" in str(response.data["exposure_version"])


def test_an_unpublished_model_version_cannot_be_configured(
    api, project, published_exposure, model_version
):
    """A draft model version is not in the catalogue and is not runnable."""
    response = configure(api, project, published_exposure, model_version)

    assert response.status_code == 400
    assert "rather than published" in str(response.data["model_version"])


def test_a_perspective_the_source_data_cannot_support_is_refused(
    api, project, published_exposure, published_model_version
):
    """Section 8: no empty financial file is invented to imply a perspective."""
    response = configure(
        api, project, published_exposure, published_model_version,
        perspectives=["reinsurance"],
    )

    assert response.status_code == 400
    assert "not supported by this portfolio" in str(response.data["perspectives"])


def test_an_execution_profile_this_installation_does_not_declare_is_refused(
    api, project, published_exposure, published_model_version
):
    response = configure(
        api, project, published_exposure, published_model_version,
        execution_profile="unlimited",
    )

    assert response.status_code == 400
    assert "not an execution profile" in str(response.data["execution_profile"])


def test_someone_without_write_access_cannot_configure_a_run(
    client_for, outsider, project, published_exposure, published_model_version
):
    response = configure(
        client_for(outsider), project, published_exposure, published_model_version
    )

    assert response.status_code == 400
    assert AnalysisRun.objects.filter(run__project=project).count() == 0


def test_a_retried_analysis_carries_the_configuration_that_makes_it_runnable(
    api, analysis_run, oasis_is
):
    """A retry the monitor offers has to produce a run that can actually run.

    Copying the lifecycle record alone made a row an analyst could see, submit
    and watch do nothing, because the detail that says which portfolio and
    which model version was left behind on the failed attempt.
    """
    oasis_is(oasis_server(loss_status="RUN_ERROR"))
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")
    assert Run.objects.get(id=analysis_run.run_id).state == RunState.FAILED

    retried = api.post(f"{API}/runs/{analysis_run.run_id}/retry/")
    assert retried.status_code == 201

    replacement = Run.objects.get(id=retried.data["id"])
    carried = replacement.analysis
    assert carried.exposure_version_id == analysis_run.exposure_version_id
    assert carried.model_version_id == analysis_run.model_version_id
    assert carried.perspectives == analysis_run.perspectives
    # The failed attempt's engine identifiers are not inherited: the retry is
    # a new analysis on the engine, not a second reading of the old one.
    assert carried.oasis_analysis_id == ""
    assert carried.keys_reconciled is None


def test_a_retried_analysis_can_be_submitted_and_succeed(api, analysis_run, oasis_is):
    oasis_is(oasis_server(loss_status="RUN_ERROR"))
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")
    retried = api.post(f"{API}/runs/{analysis_run.run_id}/retry/")

    oasis_is(oasis_server())
    replacement = Run.objects.get(id=retried.data["id"])
    submitted = api.post(f"{API}/analysis-runs/{replacement.analysis.id}/submit/")

    assert submitted.status_code == 202
    replacement.refresh_from_db()
    assert replacement.state == RunState.SUCCEEDED

# -- publishing the results -------------------------------------------------
#
# Until this landed the pipeline stopped at a tarball: the run succeeded, the
# artifact was registered, and the results workspace stayed empty forever
# because nothing turned the package into numbers. These hold the step that
# closes that gap, and the rules about what a published number may claim.

def ord_package(
    *,
    perspectives=("gul",),
    aal="232122.90",
    sd="623738.00",
    top_loss="9000000.000000",
    melt_rows=None,
) -> bytes:
    """An Oasis output package in the shape the engine actually serves one.

    ``melt_rows`` adds the moment event loss table a smoke run asks for, in the
    column order a real PiWind package writes it.
    """
    import io
    import tarfile

    ept_rows = [
        "SummaryId,EPCalc,EPType,ReturnPeriod,Loss",
        # The requested basis: mean sample (4), AEP (3).
        f"1,4,3,250.000000,{top_loss}",
        "1,4,3,100.000000,5000000.000000",
        "1,4,3,10.000000,633300.000000",
        # A different calculation and type in the same file, which must not be
        # mixed into the curve.
        "1,2,1,250.000000,1.000000",
        "1,4,1,100.000000,2.000000",
    ]
    palt_rows = [
        "SummaryId,SampleType,MeanLoss,SDLoss",
        "1,1,999999.00,111111.00",
        f"1,2,{aal},{sd}",
    ]

    tables = [("ept", ept_rows), ("palt", palt_rows)]
    if melt_rows is not None:
        tables.append(("melt", [MELT_HEADER, *melt_rows]))

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for prefix in perspectives:
            for table, rows in tables:
                payload = ("\n".join(rows) + "\n").encode("utf-8")
                info = tarfile.TarInfo(f"output/{prefix}_S1_{table}.csv")
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def test_a_completed_analysis_publishes_a_result_set(analysis_run, oasis_is, api):
    from apps.results.models import ResultSet

    oasis_is(oasis_server(output=ord_package()))
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")

    result = ResultSet.objects.get(run=analysis_run.run_id)
    assert result.perspective == "ground_up"
    assert result.average_annual_loss == Decimal("232122.90")
    assert result.standard_deviation == Decimal("623738.00")


def test_the_exceedance_curve_takes_only_the_requested_basis(
    analysis_run, oasis_is, api
):
    """An EPT holds several calculations; mixing them draws no real curve."""
    from apps.results.models import ResultSet

    oasis_is(oasis_server(output=ord_package()))
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")

    curve = ResultSet.objects.get(run=analysis_run.run_id).return_period_losses
    assert curve == {
        "10": "633300.000000",
        "100": "5000000.000000",
        "250": "9000000.000000",
    }


def test_the_basis_travels_with_the_number(analysis_run, oasis_is, api):
    """A package carries several numbers; the result says which one it is."""
    from apps.results.models import ResultSet

    oasis_is(oasis_server(output=ord_package()))
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")

    basis = ResultSet.objects.get(run=analysis_run.run_id).uncertainty_attribution
    assert basis["ord_basis"]["ep_type"] == "AEP"
    assert basis["ord_basis"]["average_loss"] == "sample"


def test_a_published_result_is_never_approved_by_the_pipeline(
    analysis_run, oasis_is, api, model_version
):
    """A pipeline that approved its own output makes the reviewer a formality."""
    from apps.results.models import ResultSet, ResultState

    model_version.is_research_prototype = False
    model_version.save()
    oasis_is(oasis_server(output=ord_package()))
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")

    result = ResultSet.objects.get(run=analysis_run.run_id)
    assert result.state == ResultState.DRAFT
    assert result.usable_for_decisions is False


def test_a_research_prototype_produces_research_output(analysis_run, oasis_is, api):
    """Section 9: research output stays distinct from a decision number."""
    from apps.results.models import ResultSet, ResultState

    assert analysis_run.model_version.is_research_prototype is True
    oasis_is(oasis_server(output=ord_package()))
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")

    assert ResultSet.objects.get(run=analysis_run.run_id).state == ResultState.RESEARCH


def test_the_result_carries_what_the_number_rests_on(analysis_run, oasis_is, api):
    """Section 9 requires the caveat block, so the fields behind it are filled."""
    from apps.results.models import ResultSet

    oasis_is(oasis_server(output=ord_package()))
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")

    result = ResultSet.objects.get(run=analysis_run.run_id)
    caveats = result.export_caveats()
    assert caveats["model_version"] == analysis_run.model_version.reference
    assert caveats["currency"]
    assert result.exposure_quality["keys_reconciled"] is True


def test_only_the_perspectives_the_run_asked_for_are_published(
    analysis_run, oasis_is, api
):
    """A ground-up run must not quietly publish an insured number as well."""
    from apps.results.models import ResultSet

    oasis_is(oasis_server(output=ord_package(perspectives=("gul", "il"))))
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")

    published = list(
        ResultSet.objects.filter(run=analysis_run.run_id).values_list(
            "perspective", flat=True
        )
    )
    assert published == ["ground_up"]


def test_an_unreadable_package_does_not_fail_a_completed_run(
    analysis_run, oasis_is, api
):
    """Hours of engine time are not discarded because a table moved.

    The calculation happened and the output is stored and checksummed. The run
    succeeds, the reason no results were published is recorded, and somebody
    can read the artifact by hand.
    """
    from apps.results.models import ResultSet

    oasis_is(oasis_server(output=b"not an archive at all"))
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.state == RunState.SUCCEEDED
    assert not ResultSet.objects.filter(run=run).exists()
    assert "results_not_published" in run.manifest["output"]


#: The moment event loss table's columns, as a real PiWind package writes them.
MELT_HEADER = (
    "EventId,SummaryId,SampleType,EventRate,ChanceOfLoss,MeanLoss,SDLoss,MaxLoss,"
    "FootprintExposure,MeanImpactedExposure,MaxImpactedExposure"
)


@pytest.fixture(autouse=True)
def no_served_package(settings, tmp_path):
    """Nothing is served unless a test says so.

    The deployment default is a volume path that may or may not exist on the
    machine running the tests, and a test whose outcome depended on that would
    pass on one laptop and fail on the next.
    """
    settings.CASS_OASIS_MODEL_ROOT = str(tmp_path / "no-package-served")


@pytest.fixture()
def package_root(settings, tmp_path):
    """A served model package the control plane can read."""
    root = tmp_path / "oasis-model"
    settings.CASS_OASIS_MODEL_ROOT = str(root)
    return root


def serve_package(root, *, model_version: str, footprint_rows: dict[int, int]) -> None:
    """Write the manifest and footprint index a CASS-built package carries.

    ``footprint_rows`` is event to the number of footprint rows it has, which is
    what the smoke check ranks events by.
    """
    import io
    import json

    from cass_converter.oasis_package import write_footprint

    (root / "model_data").mkdir(parents=True, exist_ok=True)
    footprint, index = io.BytesIO(), io.BytesIO()
    write_footprint(
        (
            (event_id, [(10 + row, 1, 1.0) for row in range(rows)])
            for event_id, rows in sorted(footprint_rows.items())
        ),
        footprint,
        index,
    )
    (root / "model_data" / "footprint.idx").write_bytes(index.getvalue())
    (root / "MANIFEST.json").write_text(
        json.dumps({"package_version": "1.0.0", "provenance": {"model_version": model_version}}),
        encoding="utf-8",
    )


@pytest.fixture()
def attached_hazard(model_version, modeller):
    """Give the model version a CASS hazard set, so its package is CASS-built."""
    from apps.modelregistry.models import HazardSet

    hazard_set = HazardSet.objects.create(
        country_code="ID",
        version="2024.0.0",
        label="Jakarta-Bandung test hazard",
        source_model="PuSGeN 2024",
        grid=model_version.grid,
        imts=["SA(0.3)"],
        investigation_time=50.0,
        stochastic_event_sets=20,
        created_by=modeller,
    )
    model_version.hazard_set = hazard_set
    model_version.save()
    return hazard_set


def settings_documents(session) -> list[dict]:
    return [call["json"] for call in session.calls if call["path"] == "v2/analyses/7/settings/"]


# -- validating the exposure inside the run ----------------------------------

def test_the_run_validates_the_published_exposure_before_the_engine(analysis_run, analyst):
    manifest = run_oasis(analysis_run, oasis_server(), analyst)

    validation = manifest["exposure_validation"]
    assert validation["locations"] == 3
    assert Decimal(validation["total_tiv"]) == Decimal("9900000")
    assert validation["currency"] == "IDR"
    assert set(validation["inputs"]) == {"oed_location"}


def test_files_that_no_longer_match_their_record_do_not_reach_the_engine(
    analysis_run, analyst
):
    ExposureVersion.objects.filter(id=analysis_run.exposure_version_id).update(
        total_tiv=Decimal("1.00")
    )
    reloaded = AnalysisRun.objects.get(id=analysis_run.id)
    session = oasis_server()

    with pytest.raises(AnalysisExecutionError, match="come apart"):
        run_it(reloaded, session, actor=analyst)

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.state == RunState.FAILED
    assert run.failure_stage == "validate_exposure"
    assert session.calls == []


def test_a_blocking_finding_found_at_run_time_stops_the_run(
    analysis_run, analyst, monkeypatch
):
    """A validator that learned something since publication is listened to."""
    from cass_oed import findings as fnd
    from cass_oed.validation import validate as real_validate

    def stricter(files):
        report = real_validate(files)
        report.findings.add(
            fnd.make(
                "unsupported_financial_term",
                "StepTriggerType carries a value.",
                file_kind="location",
                row_number=2,
                field="StepTriggerType",
            )
        )
        return report

    monkeypatch.setattr("apps.runs.services.validate_portfolio", stricter)

    with pytest.raises(AnalysisExecutionError, match="blocking validation finding"):
        run_it(analysis_run, oasis_server(), actor=analyst)
    assert Run.objects.get(id=analysis_run.run_id).failure_stage == "validate_exposure"


# -- the package the worker serves -------------------------------------------

def test_a_worker_serving_another_versions_package_is_refused(
    analysis_run, analyst, attached_hazard, package_root
):
    """ADR 9: one package at a time, and it has to be this version's."""
    serve_package(package_root, model_version="id-qeq-9.9.9", footprint_rows={1: 2})
    session = oasis_server()

    with pytest.raises(AnalysisExecutionError, match="serving the package built for id-qeq-9.9.9"):
        run_it(analysis_run, session, actor=analyst)

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.failure_stage == "publish_oed"
    assert "v2/portfolios/" not in session.paths("POST")


def test_the_served_package_is_confirmed_and_recorded(
    analysis_run, analyst, attached_hazard, package_root
):
    serve_package(
        package_root,
        model_version=analysis_run.model_version.reference,
        footprint_rows={1: 2},
    )
    manifest = run_oasis(analysis_run, oasis_server(), analyst)

    assert manifest["package"]["checked"] is True
    assert manifest["package"]["model_version"] == analysis_run.model_version.reference


def test_an_unreadable_package_is_recorded_as_unconfirmed_not_passed(
    analysis_run, analyst, attached_hazard
):
    manifest = run_oasis(analysis_run, oasis_server(), analyst)

    assert manifest["package"]["checked"] is False
    assert "could not be confirmed" in manifest["package"]["reason"]


# -- the smoke check ----------------------------------------------------------

def test_the_smoke_check_chooses_the_events_with_the_largest_footprints(package_root):
    from apps.runs.services import smoke_event_ids

    serve_package(package_root, model_version="any", footprint_rows={1: 1, 2: 5, 3: 3, 4: 5})

    # Largest first, ties to the lower identifier, returned in identifier order.
    assert smoke_event_ids(package_root, count=2) == [2, 4]
    assert smoke_event_ids(package_root, count=3) == [2, 3, 4]


def test_a_smoke_run_goes_before_the_full_event_set_and_restores_its_settings(
    analysis_run, analyst, package_root
):
    serve_package(package_root, model_version="any", footprint_rows={1: 1, 2: 5, 3: 3})
    session = oasis_server(
        output=ord_package(melt_rows=["2,1,1,nan,0.1,120000.0,0.0,3000000.0,3000000.0,3000000.0,3000000.0"])
    )

    run_it(analysis_run, session, actor=analyst)

    full, reduced, restored = settings_documents(session)
    assert "event_ids" not in full
    assert reduced["event_ids"] == [1, 2, 3]
    assert reduced["gul_summaries"][0]["ord_output"] == {"elt_moment": True}
    # The loss run uses exactly the document the settings hash was taken of.
    assert restored == full
    assert session.paths("POST").count("v2/analyses/7/run/") == 2

    manifest = Run.objects.get(id=analysis_run.run_id).manifest
    assert manifest["smoke"]["performed"] is True
    assert manifest["smoke"]["perspectives"]["ground_up"]["events_with_loss"] == 1
    assert "smoke" not in manifest["stages_not_performed"]


def test_an_engine_failure_in_the_smoke_run_stops_before_the_full_event_set(
    analysis_run, analyst, package_root
):
    serve_package(package_root, model_version="any", footprint_rows={1: 2})
    session = oasis_server(loss_status="RUN_ERROR")
    session.route(
        "GET",
        "v2/analyses/7/run_traceback_file/",
        FakeResponse(200, text="IndexError: index 12 is out of bounds"),
    )

    with pytest.raises(AnalysisExecutionError, match="smoke check"):
        run_it(analysis_run, session, actor=analyst)

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.failure_stage == "smoke"
    assert "IndexError" in run.failure_detail
    assert session.paths("POST").count("v2/analyses/7/run/") == 1


def test_a_smoke_loss_above_the_whole_portfolio_stops_the_run(
    analysis_run, analyst, package_root
):
    serve_package(package_root, model_version="any", footprint_rows={1: 2})
    session = oasis_server(
        output=ord_package(melt_rows=["1,1,1,nan,1.0,120000.0,0.0,50000000.0,1.0,1.0,1.0"])
    )

    with pytest.raises(AnalysisExecutionError, match="insured value of the whole portfolio"):
        run_it(analysis_run, session, actor=analyst)

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.failure_stage == "smoke"
    assert session.paths("POST").count("v2/analyses/7/run/") == 1


def test_a_perspective_the_smoke_run_wrote_nothing_for_stops_the_run(
    analysis_run, analyst, package_root
):
    """A readable package with no event losses is a perspective that produced nothing."""
    serve_package(package_root, model_version="any", footprint_rows={1: 2})
    session = oasis_server(output=ord_package())

    with pytest.raises(AnalysisExecutionError, match="wrote no ground-up loss event losses"):
        run_it(analysis_run, session, actor=analyst)
    assert Run.objects.get(id=analysis_run.run_id).failure_stage == "smoke"


# -- reviewing the results, and releasing a run held at a gate ----------------

def request_exception(api, analysis_run, rationale="Accepted for this research run; recorded."):
    return api.post(
        f"{API}/analysis-runs/{analysis_run.id}/request-exception/",
        {"rationale": rationale},
        format="json",
    )


def decide(client, approval_id, decision="approved"):
    return client.post(
        f"{API}/approvals/{approval_id}/decide/",
        {"decision": decision, "rationale": "Reviewed against the gate detail."},
        format="json",
    )


def test_results_that_pass_their_checks_complete_the_run_at_review(
    analysis_run, oasis_is, api
):
    oasis_is(oasis_server(output=ord_package()))
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.state == RunState.SUCCEEDED
    assert run.stage == "review"
    review = run.manifest["review"]
    assert review["failed"] == 0
    assert not any(item["passed"] is False for item in review["checks"])
    # The monitor reads the checks from the run itself.
    shown = api.get(f"{API}/runs/{run.id}/").data
    assert shown["manifest"]["review"]["checks"] == review["checks"]


def test_a_loss_above_the_insured_value_holds_the_results_at_review(
    analysis_run, oasis_is, api
):
    from apps.results.models import ResultSet

    oasis_is(oasis_server(output=ord_package(top_loss="50000000.000000")))
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.state == RunState.BLOCKED
    assert run.stage == "review"
    assert "result check" in run.gate_summary
    assert "insured value" in run.gate_detail
    assert run.may_publish_results is False
    # The result exists and is held, and the evidence before the gate is kept.
    assert ResultSet.objects.filter(run=run).exists()
    assert run.manifest["output"]["published"]


def test_a_held_result_is_released_by_an_exception_a_reviewer_decides(
    analysis_run, oasis_is, api, client_for, reviewer
):
    session = oasis_is(oasis_server(output=ord_package(top_loss="50000000.000000")))
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")

    asked = request_exception(api, analysis_run)
    assert asked.status_code == 201, asked.data
    assert asked.data["evidence"]["stage"] == "review"
    assert decide(client_for(reviewer), asked.data["id"]).status_code == 200

    resumed = api.post(f"{API}/analysis-runs/{analysis_run.id}/resume/")
    assert resumed.status_code == 202, resumed.data

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.state == RunState.SUCCEEDED
    assert run.manifest["review"]["approved_exception"] == asked.data["id"]
    # Resumed at the gate rather than rerun: the losses were calculated once,
    # and the evidence recorded before the gate survived the resume.
    assert session.paths("POST").count("v2/analyses/7/run/") == 1
    assert run.manifest["output"]["published"]


def test_resuming_before_the_gate_is_cleared_is_refused(analysis_run, oasis_is, api):
    oasis_is(oasis_server(output=ord_package(top_loss="50000000.000000")))
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")
    request_exception(api, analysis_run)

    refused = api.post(f"{API}/analysis-runs/{analysis_run.id}/resume/")

    assert refused.status_code == 409
    assert Run.objects.get(id=analysis_run.run_id).state == RunState.BLOCKED


def test_an_exception_needs_a_reason(analysis_run, oasis_is, api):
    oasis_is(oasis_server(output=ord_package(top_loss="50000000.000000")))
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")

    response = request_exception(api, analysis_run, rationale="ok")
    assert response.status_code == 400


def test_asking_twice_returns_the_request_that_stands(analysis_run, oasis_is, api):
    oasis_is(oasis_server(output=ord_package(top_loss="50000000.000000")))
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")

    first = request_exception(api, analysis_run)
    second = request_exception(api, analysis_run)

    assert second.status_code == 200
    assert second.data["id"] == first.data["id"]
    assert Approval.objects.filter(gate=Approval.Gate.RUN_EXCEPTION).count() == 1


def test_only_a_run_held_at_a_gate_can_ask_for_an_exception(analysis_run, api):
    response = request_exception(api, analysis_run)
    assert response.status_code == 409


def test_the_keys_gate_is_released_through_the_run_as_well(
    analysis_run, partial_grid, oasis_is, api, client_for, reviewer
):
    session = oasis_is(oasis_server())
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")
    assert Run.objects.get(id=analysis_run.run_id).stage == "reconcile_keys"

    asked = request_exception(api, analysis_run)
    assert asked.data["evidence"]["stage"] == "reconcile_keys"
    assert decide(client_for(reviewer), asked.data["id"]).status_code == 200
    assert api.post(f"{API}/analysis-runs/{analysis_run.id}/resume/").status_code == 202

    analysis_run.refresh_from_db()
    assert analysis_run.run.state == RunState.SUCCEEDED
    assert str(analysis_run.exception_approval_id) == asked.data["id"]
    assert session.paths("POST").count("v2/portfolios/") == 1


def test_an_exception_cleared_at_one_gate_does_not_release_another(
    analysis_run, partial_grid, oasis_is, api, client_for, reviewer
):
    """Accepting unmapped value says nothing about a loss curve."""
    oasis_is(oasis_server(output=ord_package(top_loss="50000000.000000")))
    api.post(f"{API}/analysis-runs/{analysis_run.id}/submit/")
    keys_request = request_exception(api, analysis_run)
    decide(client_for(reviewer), keys_request.data["id"])
    api.post(f"{API}/analysis-runs/{analysis_run.id}/resume/")

    run = Run.objects.get(id=analysis_run.run_id)
    assert run.state == RunState.BLOCKED
    assert run.stage == "review"
    refused = api.post(f"{API}/analysis-runs/{analysis_run.id}/resume/")
    assert refused.status_code == 409
