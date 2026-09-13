"""Contract tests for the Oasis Platform adapter.

Section 17 asks for the successful PiWind command-line test to become an
automated integration test, and section 18 permits an engine version to be
promoted only after its contract suite passes. This file is that suite for the
Oasis boundary: it drives the whole conversation the analysis pipeline has with
an Oasis 2.5.x server -- authenticate, register a portfolio, upload OED,
create an analysis, generate inputs, run losses, poll, cancel, and pull the
outputs back -- against a scripted server rather than a live one.

Scripting the server is what makes the failure paths testable. A live Oasis
will not reliably produce an expired token, a 503 from a restarting gateway or
a missing traceback file on demand, and those are precisely the paths where an
unintelligible state would reach the run monitor.
"""

from __future__ import annotations

import io

import pytest

from cass_adapters.base import (
    EngineRejected,
    EngineState,
    EngineUnavailable,
    IncompatibleEngine,
)
from cass_adapters.oasis import (
    AnalysisStatus,
    OasisAdapter,
    OasisPhase,
    PortfolioFileKind,
    analysis_state,
)

BASE = "http://oasis-api:8000"


# -- a scripted Oasis server ------------------------------------------------

class FakeResponse:
    """Enough of a ``requests`` response for the adapter to work with."""

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
        if self._text is not None:
            return self._text
        return "" if self._body is None else str(self._body)

    def iter_content(self, chunk_size=1024):
        yield from self._chunks


class Boom(Exception):
    """A transport failure: the request never reached a server."""


class FakeSession:
    """Replies from a routing table and records everything it was asked.

    A route value may be a single response, a list of responses consumed in
    order (which is how a status that changes between polls is expressed), or
    an exception instance to raise.
    """

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []

    def route(self, method, path, response):
        self.routes[(method, path)] = response
        return self

    def request(self, method, url, **kwargs):
        assert url.startswith(BASE + "/"), url
        path = url[len(BASE) + 1 :]
        self.calls.append({"method": method, "path": path, **kwargs})

        try:
            entry = self.routes[(method, path)]
        except KeyError:
            return FakeResponse(404, text="no route for " + method + " " + path)

        if isinstance(entry, list):
            entry = entry.pop(0) if len(entry) > 1 else entry[0]
        if isinstance(entry, Exception):
            raise entry
        return entry

    # -- assertions the tests read ----------------------------------------
    def paths(self, method=None):
        return [
            call["path"] for call in self.calls if method is None or call["method"] == method
        ]

    def call_to(self, method, path):
        for call in self.calls:
            if call["method"] == method and call["path"] == path:
                return call
        raise AssertionError("no " + method + " to " + path + " in " + str(self.paths()))


def adapter(session, **kwargs):
    """An adapter wired to a scripted session, with the waiting taken out."""
    kwargs.setdefault("username", "admin")
    kwargs.setdefault("password", "secret")
    kwargs.setdefault("retries", 3)
    kwargs.setdefault("retry_delay", 0)
    return OasisAdapter(BASE, session=session, sleep=lambda _: None, **kwargs)


def server(status="READY", *, version="2.5.7", auth="simple", **routes):
    """A session answering the calls almost every test needs."""
    session = FakeSession(
        {
            ("GET", "healthcheck/"): FakeResponse(200, {"status": "OK"}),
            ("GET", "server_info/"): FakeResponse(
                200, {"version": version, "config": {"API_AUTH_TYPE": auth}}
            ),
            ("POST", "access_token/"): FakeResponse(
                200, {"access_token": "tkn-a", "refresh_token": "tkn-r"}
            ),
            ("GET", "v2/analyses/7/"): FakeResponse(200, {"id": 7, "status": status}),
        }
    )
    session.routes.update(routes)
    return session


# -- the status vocabulary --------------------------------------------------

@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (AnalysisStatus.NEW, EngineState.PENDING),
        (AnalysisStatus.INPUTS_GENERATION_QUEUED, EngineState.PENDING),
        (AnalysisStatus.INPUTS_GENERATION_STARTED, EngineState.RUNNING),
        (AnalysisStatus.INPUTS_GENERATION_CANCELLED, EngineState.CANCELLED),
        (AnalysisStatus.INPUTS_GENERATION_ERROR, EngineState.FAILED),
        (AnalysisStatus.READY, EngineState.SUCCEEDED),
        (AnalysisStatus.RUN_STARTED, EngineState.SUCCEEDED),
        (AnalysisStatus.RUN_COMPLETED, EngineState.SUCCEEDED),
    ],
)
def test_the_inputs_phase_reads_ready_and_everything_after_it_as_finished(status, expected):
    """Inputs exist once the analysis is READY, and still exist while it runs."""
    assert analysis_state(status, OasisPhase.INPUTS) is expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (AnalysisStatus.NEW, EngineState.PENDING),
        (AnalysisStatus.READY, EngineState.PENDING),
        (AnalysisStatus.RUN_QUEUED, EngineState.PENDING),
        (AnalysisStatus.RUN_STARTED, EngineState.RUNNING),
        (AnalysisStatus.RUN_COMPLETED, EngineState.SUCCEEDED),
        (AnalysisStatus.RUN_CANCELLED, EngineState.CANCELLED),
        (AnalysisStatus.RUN_ERROR, EngineState.FAILED),
    ],
)
def test_the_losses_phase_reads_ready_as_not_yet_started(status, expected):
    """READY means the run has not begun; a poller that stops here stops early."""
    assert analysis_state(status, OasisPhase.LOSSES) is expected


def test_a_portfolio_the_model_could_not_key_fails_with_a_reason():
    """The engine has a status of its own for it, and it is not an error status.

    Left unrecognised, a portfolio that keyed to nothing was reported to the
    analyst as "an unrecognised status", which says nothing about the model, the
    portfolio, or what to do next.
    """
    for phase in OasisPhase:
        assert (
            analysis_state(AnalysisStatus.INPUTS_GENERATION_NO_KEYS, phase)
            is EngineState.FAILED
        )
    job = adapter(server(status="INPUTS_GENERATION_NO_KEYS")).job(7, OasisPhase.INPUTS)

    assert job.state is EngineState.FAILED
    assert "no keys" in job.message


def test_a_failed_input_generation_fails_the_loss_phase_too():
    """A run waiting behind failed inputs will never start, so it must not show as pending."""
    assert (
        analysis_state(AnalysisStatus.INPUTS_GENERATION_ERROR, OasisPhase.LOSSES)
        is EngineState.FAILED
    )


@pytest.mark.parametrize("phase", list(OasisPhase))
def test_an_unrecognised_status_is_unknown_rather_than_an_exception(phase):
    """A status added by a future patch must not crash a running poller."""
    assert analysis_state("SOMETHING_NEW", phase) is EngineState.UNKNOWN


@pytest.mark.parametrize("phase", list(OasisPhase))
@pytest.mark.parametrize("status", list(AnalysisStatus))
def test_every_documented_status_maps_in_both_phases(status, phase):
    assert analysis_state(status, phase) is not EngineState.UNKNOWN


# -- version, health and the compatibility gate -----------------------------

def test_the_version_comes_from_the_server_itself():
    engine = adapter(server())
    assert engine.version().version == "2.5.7"


def test_the_tested_version_passes_the_gate():
    assert adapter(server()).check_compatible().version == "2.5.7"


def test_an_untested_minor_release_is_refused():
    engine = adapter(server(version="2.6.0"))
    with pytest.raises(IncompatibleEngine):
        engine.check_compatible()


def test_an_untested_engine_is_refused_before_anything_is_submitted():
    """The gate has to close before the state-changing call, not after it."""
    session = server(version="2.6.0")
    with pytest.raises(IncompatibleEngine):
        adapter(session).generate_inputs(7)
    assert "v2/analyses/7/generate_inputs/" not in session.paths("POST")


def test_a_server_that_reports_no_version_is_refused():
    session = server()
    session.route("GET", "server_info/", FakeResponse(200, {"config": {}}))
    with pytest.raises(EngineRejected, match="did not report a version"):
        adapter(session).version()


def test_health_is_true_when_the_check_answers():
    assert adapter(server()).healthy() is True


def test_health_is_false_when_the_server_cannot_be_reached():
    session = server()
    session.route("GET", "healthcheck/", Boom("connection refused"))
    assert adapter(session).healthy() is False


# -- authentication ---------------------------------------------------------

def test_signing_in_stores_the_token_and_sends_it_as_a_bearer_header():
    session = server()
    engine = adapter(session)
    engine.authenticate()
    engine.analysis(7)
    assert session.call_to("GET", "v2/analyses/7/")["headers"]["authorization"] == "Bearer tkn-a"


def test_a_server_with_authentication_disabled_is_not_asked_for_a_token():
    session = server(auth="disabled")
    engine = adapter(session)
    engine.authenticate()
    assert "access_token/" not in session.paths("POST")
    assert "authorization" not in session.call_to("GET", "server_info/")["headers"]


def test_missing_credentials_are_refused_with_an_actionable_message():
    engine = adapter(server(), username="", password="")
    with pytest.raises(EngineRejected, match="No Oasis credentials") as excinfo:
        engine.authenticate()
    assert "deployment" in excinfo.value.detail


def test_a_sign_in_that_returns_no_token_is_refused():
    session = server()
    session.route("POST", "access_token/", FakeResponse(200, {"detail": "nope"}))
    with pytest.raises(EngineRejected, match="no access token"):
        adapter(session).authenticate()


def test_an_expired_token_is_refreshed_and_the_original_call_retried():
    """The analyst should never see a run fail because a token aged out."""
    session = server()
    session.route(
        "GET",
        "v2/analyses/7/",
        [FakeResponse(401, text="expired"), FakeResponse(200, {"id": 7, "status": "READY"})],
    )
    session.route("POST", "refresh_token/", FakeResponse(200, {"access_token": "tkn-b"}))

    engine = adapter(session)
    engine.authenticate()
    assert engine.analysis(7)["status"] == "READY"
    assert "refresh_token/" in session.paths("POST")


def test_a_rejected_refresh_falls_back_to_signing_in_again():
    session = server()
    session.route(
        "GET",
        "v2/analyses/7/",
        [FakeResponse(401, text="expired"), FakeResponse(200, {"id": 7, "status": "READY"})],
    )
    session.route("POST", "refresh_token/", FakeResponse(401, text="refresh expired"))

    engine = adapter(session)
    engine.authenticate()
    assert engine.analysis(7)["status"] == "READY"
    assert session.paths("POST").count("access_token/") == 2


def test_credentials_the_server_will_not_accept_are_reported_as_a_refusal():
    session = server()
    session.route("GET", "v2/analyses/7/", FakeResponse(403, text="forbidden"))
    session.route("POST", "refresh_token/", FakeResponse(401, text="no"))
    session.route("POST", "access_token/", FakeResponse(401, text="bad password"))

    with pytest.raises(EngineRejected, match="rejected the CASS credentials"):
        adapter(session).analysis(7)


# -- transient failure versus refusal ---------------------------------------

def test_a_gateway_error_is_retried_and_then_succeeds():
    session = server()
    session.route(
        "GET",
        "v2/analyses/7/",
        [FakeResponse(503, text="restarting"), FakeResponse(200, {"id": 7, "status": "READY"})],
    )
    assert adapter(session).analysis(7)["status"] == "READY"


def test_a_gateway_error_that_persists_is_unavailable_rather_than_rejected():
    session = server()
    session.route("GET", "v2/analyses/7/", FakeResponse(503, text="still restarting"))
    with pytest.raises(EngineUnavailable) as excinfo:
        adapter(session).analysis(7)
    assert excinfo.value.retryable is True


def test_a_transport_failure_is_retried_and_then_reported_as_unavailable():
    session = server()
    session.route("GET", "v2/analyses/7/", Boom("connection reset"))
    with pytest.raises(EngineUnavailable) as excinfo:
        adapter(session).analysis(7)
    assert "connection reset" in excinfo.value.detail
    assert session.paths("GET").count("v2/analyses/7/") == 3


def test_a_bad_request_is_refused_immediately_without_burning_the_queue_slot():
    session = server()
    session.route("GET", "v2/analyses/7/", FakeResponse(400, text="invalid"))
    with pytest.raises(EngineRejected) as excinfo:
        adapter(session).analysis(7)
    assert excinfo.value.retryable is False
    assert session.paths("GET").count("v2/analyses/7/") == 1


def test_a_wrong_state_refusal_says_so_in_words_a_monitor_can_show():
    session = server()
    session.route("POST", "v2/analyses/7/run/", FakeResponse(409, text="not READY"))
    with pytest.raises(EngineRejected, match="wrong state"):
        adapter(session).run(7)


def test_the_engine_text_is_kept_in_the_detail_for_the_support_bundle():
    session = server()
    session.route("GET", "v2/analyses/7/", FakeResponse(400, text="location file is empty"))
    with pytest.raises(EngineRejected) as excinfo:
        adapter(session).analysis(7)
    assert "location file is empty" in excinfo.value.detail


# -- models -----------------------------------------------------------------

def test_the_model_triple_resolves_to_the_id_the_api_works_in():
    session = server()
    session.route(
        "GET",
        "v2/models/",
        FakeResponse(
            200,
            {
                "results": [
                    {"id": 1, "supplier_id": "OasisLMF", "model_id": "PiWind", "version_id": "1"},
                    {"id": 2, "supplier_id": "KRE", "model_id": "EQ", "version_id": "1"},
                ]
            },
        ),
    )
    assert adapter(session).find_model("KRE", "EQ", "1").id == 2


def test_an_unpaginated_model_list_is_read_too():
    session = server()
    session.route(
        "GET",
        "v2/models/",
        FakeResponse(200, [{"id": 3, "supplier_id": "KRE", "model_id": "EQ", "version_id": "2"}]),
    )
    assert adapter(session).find_model("KRE", "EQ", "2").id == 3


def test_a_model_the_server_does_not_have_names_the_triple_that_was_sought():
    session = server()
    session.route("GET", "v2/models/", FakeResponse(200, {"results": []}))
    with pytest.raises(EngineRejected, match="KRE/EQ/1"):
        adapter(session).find_model("KRE", "EQ", "1")


# -- portfolios and OED -----------------------------------------------------

def test_creating_a_portfolio_returns_the_new_id():
    session = server()
    session.route("POST", "v2/portfolios/", FakeResponse(201, {"id": 11, "name": "PiWind"}))
    assert adapter(session).create_portfolio("PiWind") == 11


def test_a_creation_without_an_id_is_refused_rather_than_failing_later():
    session = server()
    session.route("POST", "v2/portfolios/", FakeResponse(201, {"name": "PiWind"}))
    with pytest.raises(EngineRejected, match="did not return an identifier"):
        adapter(session).create_portfolio("PiWind")


@pytest.mark.parametrize(
    ("kind", "path"),
    [
        (PortfolioFileKind.LOCATION, "v2/portfolios/11/location_file/"),
        (PortfolioFileKind.ACCOUNTS, "v2/portfolios/11/accounts_file/"),
        (PortfolioFileKind.REINSURANCE_INFO, "v2/portfolios/11/reinsurance_info_file/"),
        (PortfolioFileKind.REINSURANCE_SCOPE, "v2/portfolios/11/reinsurance_scope_file/"),
    ],
)
def test_each_oed_file_goes_to_its_own_endpoint(kind, path):
    session = server()
    session.route("POST", path, FakeResponse(200, {"uri": path}))
    adapter(session).upload_portfolio_file(11, kind, "loc.csv", b"PortNumber\n1\n")
    assert path in session.paths("POST")


def test_an_upload_sends_the_bytes_and_never_a_host_path():
    """Section 5: no CASS or Windows host path may be visible to the engine."""
    session = server()
    session.route("POST", "v2/portfolios/11/location_file/", FakeResponse(200, {}))
    adapter(session).upload_portfolio_file(
        11, PortfolioFileKind.LOCATION, "oed_location.csv", b"PortNumber,AccNumber\n1,A\n"
    )
    filename, stream, content_type = session.call_to(
        "POST", "v2/portfolios/11/location_file/"
    )["files"]["file"]
    assert filename == "oed_location.csv"
    assert content_type == "text/csv"
    assert stream.read() == b"PortNumber,AccNumber\n1,A\n"


def test_an_open_stream_may_be_uploaded_without_being_read_into_memory():
    session = server()
    session.route("POST", "v2/portfolios/11/location_file/", FakeResponse(200, {}))
    handle = io.BytesIO(b"PortNumber\n1\n")
    adapter(session).upload_portfolio_file(
        11, PortfolioFileKind.LOCATION, "loc.csv", handle
    )
    assert session.call_to("POST", "v2/portfolios/11/location_file/")["files"]["file"][1] is handle


# -- analyses ---------------------------------------------------------------

def test_creating_an_analysis_binds_the_portfolio_and_the_model():
    session = server()
    session.route("POST", "v2/analyses/", FakeResponse(201, {"id": 7}))
    assert adapter(session).create_analysis("PiWind baseline", 11, 2) == 7
    body = session.call_to("POST", "v2/analyses/")["json"]
    assert body["portfolio"] == 11
    assert body["model"] == 2
    assert body["name"] == "PiWind baseline"


def test_settings_are_posted_to_the_analysis():
    session = server()
    session.route("POST", "v2/analyses/7/settings/", FakeResponse(200, {}))
    adapter(session).upload_settings(7, {"gul_output": True})
    assert session.call_to("POST", "v2/analyses/7/settings/")["json"] == {"gul_output": True}


def test_generating_inputs_reports_the_phase_the_caller_asked_about():
    session = server(status="INPUTS_GENERATION_STARTED")
    session.route("POST", "v2/analyses/7/generate_inputs/", FakeResponse(200, {}))
    job = adapter(session).generate_inputs(7)
    assert job.state is EngineState.RUNNING
    assert job.raw_state == "INPUTS_GENERATION_STARTED"
    assert "keys" in job.message


def test_running_losses_from_a_ready_analysis_reports_the_loss_phase():
    session = server(status="RUN_STARTED")
    session.route("POST", "v2/analyses/7/run/", FakeResponse(200, {}))
    job = adapter(session).run(7)
    assert job.state is EngineState.RUNNING
    assert job.message == "Oasis is calculating losses."


def test_an_unrecognised_status_still_produces_a_displayable_message():
    session = server(status="SOMETHING_NEW")
    job = adapter(session).job(7, OasisPhase.LOSSES)
    assert job.state is EngineState.UNKNOWN
    assert "SOMETHING_NEW" in job.message


# -- progress ---------------------------------------------------------------

def test_sub_task_counts_become_a_progress_fraction():
    session = server()
    session.route(
        "GET",
        "v2/analyses/7/",
        FakeResponse(
            200,
            {"id": 7, "status": "RUN_STARTED", "status_count": {"TOTAL": 8, "COMPLETED": 2}},
        ),
    )
    assert adapter(session).job(7, OasisPhase.LOSSES).progress == 0.25


def test_a_finished_phase_reports_full_progress():
    session = server(status="RUN_COMPLETED")
    assert adapter(session).job(7, OasisPhase.LOSSES).progress == 1.0


def test_no_sub_task_counts_means_no_invented_progress_number():
    session = server(status="RUN_STARTED")
    assert adapter(session).job(7, OasisPhase.LOSSES).progress is None


# -- polling ----------------------------------------------------------------

def test_polling_follows_the_analysis_to_a_terminal_state():
    session = server()
    session.route(
        "GET",
        "v2/analyses/7/",
        [
            FakeResponse(200, {"id": 7, "status": "RUN_QUEUED"}),
            FakeResponse(200, {"id": 7, "status": "RUN_STARTED"}),
            FakeResponse(200, {"id": 7, "status": "RUN_COMPLETED"}),
        ],
    )
    seen = []
    final = adapter(session).poll(
        7, OasisPhase.LOSSES, interval=0, on_update=seen.append
    )
    assert final.state is EngineState.SUCCEEDED
    assert [job.raw_state for job in seen] == ["RUN_QUEUED", "RUN_STARTED", "RUN_COMPLETED"]


def test_polling_reports_a_failure_rather_than_waiting_for_it_to_improve():
    session = server(status="RUN_ERROR")
    assert adapter(session).poll(7, OasisPhase.LOSSES, interval=0).state is EngineState.FAILED


def test_polling_gives_up_at_the_timeout_and_says_what_it_was_waiting_for():
    session = server(status="RUN_STARTED")
    with pytest.raises(EngineUnavailable) as excinfo:
        adapter(session).poll(7, OasisPhase.LOSSES, interval=1, timeout=2)
    assert "RUN_STARTED" in excinfo.value.detail
    assert "losses" in excinfo.value.detail


# -- cancellation -----------------------------------------------------------

def test_cancelling_input_generation_reaches_the_generation_endpoint():
    session = server(status="INPUTS_GENERATION_CANCELLED")
    session.route("POST", "v2/analyses/7/cancel_generate_inputs/", FakeResponse(200, {}))
    job = adapter(session).cancel(7, OasisPhase.INPUTS)
    assert job.state is EngineState.CANCELLED


def test_cancelling_a_loss_run_reaches_the_run_endpoint():
    """Section 11: the engine must stop, not merely the CASS record."""
    session = server(status="RUN_CANCELLED")
    session.route("POST", "v2/analyses/7/cancel_analysis_run/", FakeResponse(200, {}))
    job = adapter(session).cancel(7, OasisPhase.LOSSES)
    assert job.state is EngineState.CANCELLED
    assert "v2/analyses/7/cancel_analysis_run/" in session.paths("POST")


# -- files coming back ------------------------------------------------------

def test_outputs_stream_into_the_sink_the_caller_provides():
    session = server()
    session.route(
        "GET", "v2/analyses/7/output_file/", FakeResponse(200, chunks=[b"abc", b"defg"])
    )
    sink = io.BytesIO()
    written = adapter(session).download_outputs(7, sink)
    assert sink.getvalue() == b"abcdefg"
    assert written == 7


def test_generated_inputs_can_be_pulled_back_for_the_evidence_record():
    session = server()
    session.route("GET", "v2/analyses/7/input_file/", FakeResponse(200, chunks=[b"tar"]))
    sink = io.BytesIO()
    assert adapter(session).download_inputs(7, sink) == 3


def test_the_keys_report_collects_all_three_lookup_files():
    session = server()
    session.route("GET", "v2/analyses/7/lookup_success_file/", FakeResponse(200, text="ok"))
    session.route("GET", "v2/analyses/7/lookup_errors_file/", FakeResponse(200, text="bad"))
    session.route("GET", "v2/analyses/7/lookup_validation_file/", FakeResponse(200, text="v"))
    assert adapter(session).keys_report(7) == {
        "success": "ok",
        "errors": "bad",
        "validation": "v",
    }


def test_a_missing_diagnostic_file_is_blank_rather_than_a_run_failure():
    """A successful run has no traceback; absence is information, not a fault."""
    session = server()
    assert adapter(session).keys_report(7) == {"success": "", "errors": "", "validation": ""}


def test_the_failure_log_comes_from_the_endpoint_matching_the_phase():
    session = server()
    session.route(
        "GET",
        "v2/analyses/7/input_generation_traceback_file/",
        FakeResponse(200, text="KeyError: AreaPerilID"),
    )
    session.route(
        "GET", "v2/analyses/7/run_traceback_file/", FakeResponse(200, text="MemoryError")
    )
    engine = adapter(session)
    assert engine.failure_log(7, OasisPhase.INPUTS) == "KeyError: AreaPerilID"
    assert engine.failure_log(7, OasisPhase.LOSSES) == "MemoryError"


# -- the whole conversation -------------------------------------------------

def test_the_piwind_sequence_runs_end_to_end_against_a_scripted_server():
    """Milestone 2: publish OED, generate, run and collect without the native UI."""
    session = server()
    session.route(
        "GET",
        "v2/models/",
        FakeResponse(
            200,
            {"results": [{"id": 2, "supplier_id": "OasisLMF", "model_id": "PiWind",
                          "version_id": "1"}]},
        ),
    )
    session.route("POST", "v2/portfolios/", FakeResponse(201, {"id": 11}))
    session.route("POST", "v2/portfolios/11/location_file/", FakeResponse(200, {}))
    session.route("POST", "v2/portfolios/11/accounts_file/", FakeResponse(200, {}))
    session.route("POST", "v2/analyses/", FakeResponse(201, {"id": 7}))
    session.route("POST", "v2/analyses/7/settings/", FakeResponse(200, {}))
    session.route("POST", "v2/analyses/7/generate_inputs/", FakeResponse(200, {}))
    session.route("POST", "v2/analyses/7/run/", FakeResponse(200, {}))
    session.route("GET", "v2/analyses/7/output_file/", FakeResponse(200, chunks=[b"ORD"]))
    session.route(
        "GET",
        "v2/analyses/7/",
        [
            FakeResponse(200, {"id": 7, "status": "INPUTS_GENERATION_STARTED"}),
            FakeResponse(200, {"id": 7, "status": "READY"}),
            FakeResponse(200, {"id": 7, "status": "RUN_STARTED"}),
            FakeResponse(200, {"id": 7, "status": "RUN_COMPLETED"}),
        ],
    )

    engine = adapter(session)
    engine.authenticate()
    engine.check_compatible()

    model = engine.find_model("OasisLMF", "PiWind", "1")
    portfolio_id = engine.create_portfolio("PiWind baseline")
    engine.upload_portfolio_file(
        portfolio_id, PortfolioFileKind.LOCATION, "location.csv", b"PortNumber\n1\n"
    )
    engine.upload_portfolio_file(
        portfolio_id, PortfolioFileKind.ACCOUNTS, "account.csv", b"PortNumber\n1\n"
    )
    analysis_id = engine.create_analysis("PiWind baseline", portfolio_id, model.id)
    engine.upload_settings(analysis_id, {"gul_output": True})

    engine.generate_inputs(analysis_id)
    assert engine.poll(analysis_id, OasisPhase.INPUTS, interval=0).state is EngineState.SUCCEEDED

    engine.run(analysis_id)
    assert engine.poll(analysis_id, OasisPhase.LOSSES, interval=0).state is EngineState.SUCCEEDED

    sink = io.BytesIO()
    engine.download_outputs(analysis_id, sink)
    assert sink.getvalue() == b"ORD"


def test_describe_reports_the_engine_for_the_administration_screen():
    described = adapter(server()).describe()
    assert described["reachable"] is True
    assert described["compatible"] is True
    assert described["version"] == "2.5.7"


def test_describe_reports_an_unreachable_engine_without_raising():
    session = server()
    session.route("GET", "server_info/", Boom("no route to host"))
    described = adapter(session).describe()
    assert described["reachable"] is False
    assert described["compatible"] is False


# -- malformed and partial engine responses ---------------------------------

def test_a_model_record_serialises_for_the_run_manifest():
    """Section 12 traces a result to its model version, so the triple is kept."""
    session = server()
    session.route(
        "GET",
        "v2/models/",
        FakeResponse(200, [{"id": 2, "supplier_id": "KRE", "model_id": "EQ", "version_id": "1"}]),
    )
    assert adapter(session).find_model("KRE", "EQ", "1").as_dict() == {
        "id": 2,
        "supplier_id": "KRE",
        "model_id": "EQ",
        "version_id": "1",
    }


def test_a_portfolio_record_can_be_read_back_after_upload():
    session = server()
    session.route("GET", "v2/portfolios/11/", FakeResponse(200, {"id": 11, "name": "PiWind"}))
    assert adapter(session).portfolio(11)["name"] == "PiWind"


def test_a_server_that_will_not_say_how_to_authenticate_is_assumed_to_want_credentials():
    """Assuming authentication is off because the question failed would be the unsafe default."""
    session = server()
    session.route("GET", "server_info/", Boom("no route to host"))
    engine = adapter(session)
    engine.authenticate()
    assert "access_token/" in session.paths("POST")


def test_a_refresh_that_returns_no_token_falls_through_to_signing_in_again():
    session = server()
    session.route(
        "GET",
        "v2/analyses/7/",
        [FakeResponse(401, text="expired"), FakeResponse(200, {"id": 7, "status": "READY"})],
    )
    session.route("POST", "refresh_token/", FakeResponse(200, {"detail": "no token here"}))

    engine = adapter(session)
    engine.authenticate()
    assert engine.analysis(7)["status"] == "READY"
    assert session.paths("POST").count("access_token/") == 2


def test_a_rotated_refresh_token_is_the_one_used_next_time():
    """Oasis may issue a new refresh token; keeping the stale one breaks the next refresh."""
    session = server()
    session.route(
        "GET",
        "v2/analyses/7/",
        [
            FakeResponse(401, text="expired"),
            FakeResponse(200, {"id": 7, "status": "READY"}),
            FakeResponse(401, text="expired again"),
            FakeResponse(200, {"id": 7, "status": "RUN_COMPLETED"}),
        ],
    )
    session.route(
        "POST",
        "refresh_token/",
        FakeResponse(200, {"access_token": "tkn-b", "refresh_token": "tkn-r2"}),
    )

    engine = adapter(session)
    engine.authenticate()
    engine.analysis(7)
    engine.analysis(7)

    refreshes = [
        call["headers"]["authorization"]
        for call in session.calls
        if call["path"] == "refresh_token/"
    ]
    assert refreshes == ["Bearer tkn-r", "Bearer tkn-r2"]


def test_a_malformed_sub_task_count_produces_no_progress_rather_than_an_error():
    session = server()
    session.route(
        "GET",
        "v2/analyses/7/",
        FakeResponse(
            200,
            {"id": 7, "status": "RUN_STARTED", "status_count": {"TOTAL": "eight"}},
        ),
    )
    assert adapter(session).job(7, OasisPhase.LOSSES).progress is None


def test_a_model_list_in_an_unrecognised_shape_is_read_as_empty():
    session = server()
    session.route("GET", "v2/models/", FakeResponse(200, {"detail": "unexpected"}))
    assert adapter(session).models() == []


def test_a_response_that_is_not_json_does_not_crash_the_caller():
    """A proxy returning HTML must surface as a missing record, not a decode error."""
    session = server()
    session.route("GET", "v2/analyses/7/", FakeResponse(200, text="<html>gateway</html>"))
    assert adapter(session).analysis(7) == {}


def test_server_info_is_fetched_with_credentials_when_the_server_demands_them():
    """A real 2.5.x deployment refuses server_info to an anonymous caller.

    A compatibility check that reported "engine unreachable" because nobody had
    signed in would send an operator hunting a network fault that is not there.
    """
    session = server()
    session.route(
        "GET",
        "server_info/",
        [
            FakeResponse(403, text="Authentication credentials were not provided."),
            FakeResponse(
                200, {"version": "2.5.7", "config": {"API_AUTH_TYPE": "simple_jwt"}}
            ),
        ],
    )
    engine = adapter(session)
    assert engine.version().version == "2.5.7"
    assert "access_token/" in session.paths("POST")


def test_an_unfamiliar_authentication_type_still_signs_in():
    """2.5.7 reports simple_jwt; only 'disabled' means do not sign in."""
    session = server(auth="simple_jwt")
    engine = adapter(session)
    engine.authenticate()
    assert "access_token/" in session.paths("POST")
