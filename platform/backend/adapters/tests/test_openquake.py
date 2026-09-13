"""Contract tests for the OpenQuake adapter.

Section 18 permits an engine version to be promoted only after its contract
suite passes, and this is that suite for the hazard boundary: submit a job,
poll it, read the log, export the datastore, abort, and fail.

The server is scripted rather than live for the same reason the Oasis suite
scripts one. A real engine will not produce an HTML error page from a proxy, a
restarting gateway, or a failure with no traceback file on demand -- and those
are precisely the paths where an unintelligible state would reach the run
monitor instead of a sentence a modeller can act on.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from cass_adapters.base import (
    EngineRejected,
    EngineState,
    EngineUnavailable,
    IncompatibleEngine,
)
from cass_adapters.openquake import (
    CalculationStatus,
    OpenQuakeAdapter,
    calculation_state,
)

BASE = "http://openquake:8800"


# -- a scripted OpenQuake server --------------------------------------------

class FakeResponse:
    """Enough of a ``requests`` response for the adapter to work with."""

    def __init__(self, status_code=200, body=None, text=None, chunks=None):
        self.status_code = status_code
        self._body = body
        self._text = text
        self._chunks = chunks or []

    @property
    def text(self) -> str:
        if self._text is not None:
            return self._text
        return "" if self._body is None else str(self._body)

    def json(self):
        if self._body is None:
            raise ValueError("no JSON body")
        return self._body

    def iter_content(self, chunk_size: int = 1):
        yield from self._chunks


class FakeSession:
    """A scripted server: each rule matches a method and a URL fragment."""

    def __init__(self, rules):
        self.rules = list(rules)
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for rule in self.rules:
            if rule["method"] == method and rule["fragment"] in url:
                responses = rule["responses"]
                response = responses[0] if len(responses) == 1 else responses.pop(0)
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"no scripted response for {method} {url}")


def rule(method, fragment, *responses):
    return {"method": method, "fragment": fragment, "responses": list(responses)}


def adapter_for(session, **kwargs) -> OpenQuakeAdapter:
    return OpenQuakeAdapter(
        BASE, session=session, sleep=lambda _: None, **kwargs
    )


def version_rule(version="3.23.0"):
    return rule("GET", "engine_version", FakeResponse(200, text=version))


def digest_rule():
    return rule("GET", "engine_info", FakeResponse(404, text="not found"))


# -- the vocabulary ---------------------------------------------------------

@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("created", EngineState.PENDING),
        ("submitted", EngineState.PENDING),
        ("executing", EngineState.RUNNING),
        ("complete", EngineState.SUCCEEDED),
        ("failed", EngineState.FAILED),
        ("aborted", EngineState.CANCELLED),
        ("COMPLETE", EngineState.SUCCEEDED),
        ("something new", EngineState.UNKNOWN),
    ],
)
def test_the_engine_vocabulary_is_normalised(reported, expected):
    assert calculation_state(reported) is expected


def test_a_deleted_calculation_is_failure_not_cancellation():
    """Nothing was stopped on purpose; the evidence is simply gone."""
    assert calculation_state(CalculationStatus.DELETED) is EngineState.FAILED


# -- version and compatibility ----------------------------------------------

def test_the_version_is_read_from_a_plain_text_body():
    """OpenQuake answers with a bare version string, not a document."""
    session = FakeSession([version_rule("3.23.0"), digest_rule()])

    reported = adapter_for(session).version()

    assert reported.version == "3.23.0"
    assert reported.name == "OpenQuake"


def test_an_html_error_page_is_not_mistaken_for_a_version():
    """A proxy in front of the engine must not read as an untested engine.

    Reporting "version <!DOCTYPE html> is untested" would send an operator to
    the compatibility matrix for what is actually a routing fault.
    """
    session = FakeSession(
        [rule("GET", "engine_version", FakeResponse(200, text="<!DOCTYPE html>"))]
    )

    with pytest.raises(EngineRejected, match="did not report a version"):
        adapter_for(session).version()


def test_an_untested_engine_series_is_refused_before_anything_runs():
    session = FakeSession([version_rule("3.19.0"), digest_rule()])

    with pytest.raises(IncompatibleEngine, match="3.19.0"):
        adapter_for(session).check_compatible()


def test_a_patch_release_within_a_tested_series_is_accepted():
    """Patch releases do not move export formats; minor releases do."""
    session = FakeSession([version_rule("3.23.4"), digest_rule()])

    assert adapter_for(session).check_compatible().version == "3.23.4"


def test_health_is_false_rather_than_raising_when_the_engine_is_down():
    session = FakeSession([rule("GET", "engine_version", OSError("connection refused"))])

    assert adapter_for(session).healthy() is False


def test_describe_reports_an_unreachable_engine_for_the_support_bundle():
    session = FakeSession([rule("GET", "engine_version", OSError("no route to host"))])

    described = adapter_for(session).describe()

    assert described["reachable"] is False
    assert described["compatible"] is False
    assert described["engine"] == "OpenQuake"


# -- submitting -------------------------------------------------------------

def test_submitting_uploads_the_job_and_returns_the_calculation_id():
    session = FakeSession([rule("POST", "calc/run", FakeResponse(200, {"job_id": 42}))])

    calculation_id = adapter_for(session).submit(
        {"job.ini": b"[general]\n", "sites.csv": b"lon,lat\n"}
    )

    assert calculation_id == 42
    _, _, kwargs = session.calls[0]
    # One archive under the field the engine reads, and the job file named.
    field, (filename, payload, content_type) = kwargs["files"][0]
    assert (field, filename, content_type) == ("archive", "job.zip", "application/zip")
    assert kwargs["data"]["job_ini"] == "job.ini"
    with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
        assert sorted(bundle.namelist()) == ["job.ini", "sites.csv"]


def test_a_source_model_keeps_the_paths_its_logic_tree_references():
    """Django keeps only an upload's base name, so separate parts arrive flat.

    A national logic tree names ``ssm/crust/faults.xml``. Sent as a multipart
    part per file the engine receives ``faults.xml`` and fails looking for the
    source it was sent. An archive keeps the directory.
    """
    session = FakeSession([rule("POST", "calc/run", FakeResponse(200, {"job_id": 7}))])

    adapter_for(session).submit(
        {"job.ini": b"[general]\n", "ssm/crust/faults.xml": b"<nrml/>"}
    )

    _, _, kwargs = session.calls[0]
    _, (_, payload, _) = kwargs["files"][0]
    with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
        assert "ssm/crust/faults.xml" in bundle.namelist()


def test_a_job_with_no_job_file_is_refused_before_it_is_sent():
    session = FakeSession([])

    with pytest.raises(EngineRejected, match="has no job.ini"):
        adapter_for(session).submit({"source.xml": b"<nrml/>"})

    assert session.calls == []


# -- exporting --------------------------------------------------------------

class NamedResponse(FakeResponse):
    """A download that carries the engine's Content-Disposition header."""

    def __init__(self, *args, filename="", **kwargs):
        super().__init__(*args, **kwargs)
        self.headers = {"Content-Disposition": f"attachment; filename={filename}"}


def export_rules(result_type, response):
    return [
        rule(
            "GET",
            "calc/11/results",
            FakeResponse(
                200,
                [{"id": 5, "name": result_type, "type": result_type, "outtypes": ["csv"]}],
            ),
        ),
        rule("GET", "calc/result/5", response),
    ]


def test_a_single_file_export_keeps_the_name_oq_export_would_give_it():
    """The converter finds ``events_11.csv`` by that name, not by the download's."""
    session = FakeSession(
        export_rules(
            "events",
            NamedResponse(
                200, chunks=[b"event_id,rup_id\n"], filename="output-5-events_11.csv"
            ),
        )
    )

    exported = adapter_for(session).export(11, "events")

    assert exported == {"events_11.csv": b"event_id,rup_id\n"}


def test_a_multi_file_export_is_unwrapped_from_the_engine_zip():
    """Ground motion exports as the GMF and its site mesh together."""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("gmf-data_11.csv", "event_id,custom_site_id,gmv_PGA\n")
        bundle.writestr("sitemesh_11.csv", "custom_site_id,lon,lat\n")
    session = FakeSession(
        export_rules(
            "gmf_data",
            NamedResponse(200, chunks=[archive.getvalue()], filename="output-5-gmf_data-csv.zip"),
        )
    )

    exported = adapter_for(session).export(11, "gmf_data")

    assert sorted(exported) == ["gmf-data_11.csv", "sitemesh_11.csv"]


def test_an_output_the_calculation_did_not_produce_is_refused():
    session = FakeSession([rule("GET", "calc/11/results", FakeResponse(200, []))])

    with pytest.raises(EngineRejected, match="has no gmf_data output"):
        adapter_for(session).export(11, "gmf_data")


def test_nothing_is_sent_to_the_engine_without_a_job_configuration():
    session = FakeSession([])

    with pytest.raises(EngineRejected, match="needs at least a job configuration"):
        adapter_for(session).submit({})

    assert session.calls == []


def test_a_creation_that_returns_no_identifier_is_reported_as_such():
    """A 2xx with no id would otherwise fail later as a 404 against None."""
    session = FakeSession([rule("POST", "calc/run", FakeResponse(200, {"status": "ok"}))])

    with pytest.raises(EngineRejected, match="did not return a calculation id"):
        adapter_for(session).submit({"job.ini": b"x"})


def test_an_invalid_job_is_refused_in_words_a_modeller_can_act_on():
    session = FakeSession(
        [rule("POST", "calc/run", FakeResponse(400, text="Invalid job.ini"))]
    )

    with pytest.raises(EngineRejected, match="rejected the job configuration as invalid"):
        adapter_for(session).submit({"job.ini": b"broken"})


def test_a_refusal_is_not_retried():
    """Repeating a 400 wastes the queue slot without changing the answer."""
    session = FakeSession(
        [rule("POST", "calc/run", FakeResponse(400, text="Invalid job.ini"))]
    )

    with pytest.raises(EngineRejected):
        adapter_for(session).submit({"job.ini": b"broken"})

    assert len(session.calls) == 1


def test_a_restarting_gateway_is_retried_and_then_succeeds():
    session = FakeSession(
        [
            rule(
                "POST",
                "calc/run",
                FakeResponse(503, text="restarting"),
                FakeResponse(200, {"job_id": 7}),
            )
        ]
    )

    assert adapter_for(session).submit({"job.ini": b"x"}) == 7
    assert len(session.calls) == 2


def test_an_engine_that_never_responds_is_reported_as_unavailable():
    session = FakeSession([rule("POST", "calc/run", OSError("connection refused"))])

    with pytest.raises(EngineUnavailable, match="did not respond"):
        adapter_for(session).submit({"job.ini": b"x"})


# -- monitoring -------------------------------------------------------------

def test_status_reports_the_normalised_state_and_keeps_the_engine_word():
    session = FakeSession(
        [
            rule(
                "GET",
                "calc/11/status",
                FakeResponse(200, {"status": "executing", "description": "computing gmfs"}),
            )
        ]
    )

    job = adapter_for(session).status(11)

    assert job.state is EngineState.RUNNING
    assert job.raw_state == "executing"
    assert job.message == "computing gmfs"


def test_progress_is_reported_where_the_engine_gives_enough_to_compute_one():
    session = FakeSession(
        [
            rule(
                "GET",
                "calc/11/status",
                FakeResponse(200, {"status": "executing", "done": 3, "total": 12}),
            )
        ]
    )

    assert adapter_for(session).status(11).progress == pytest.approx(0.25)


def test_progress_is_absent_rather_than_invented():
    session = FakeSession(
        [rule("GET", "calc/11/status", FakeResponse(200, {"status": "executing"}))]
    )

    assert adapter_for(session).status(11).progress is None


def test_the_log_is_flattened_into_readable_lines():
    session = FakeSession(
        [
            rule(
                "GET",
                "calc/11/log/",
                FakeResponse(
                    200,
                    [
                        ["2026-09-12 10:00:00", "INFO", "job", "starting"],
                        ["2026-09-12 10:00:05", "INFO", "job", "computing"],
                    ],
                ),
            )
        ]
    )

    lines = adapter_for(session).log(11)

    assert lines == [
        "2026-09-12 10:00:00 INFO job starting",
        "2026-09-12 10:00:05 INFO job computing",
    ]


# -- failure ----------------------------------------------------------------

def test_a_failure_carries_the_traceback_and_the_log_together():
    """One says what broke, the other what it was doing. Both, or neither helps."""
    session = FakeSession(
        [
            rule(
                "GET",
                "calc/11/traceback",
                FakeResponse(200, ["Traceback:", "ValueError: no sites"]),
            ),
            rule(
                "GET",
                "calc/11/log/",
                FakeResponse(200, [["t", "INFO", "job", "reading site model"]]),
            ),
        ]
    )

    detail = adapter_for(session).failure_detail(11)

    assert "ValueError: no sites" in detail
    assert "reading site model" in detail


def test_a_failure_with_no_traceback_file_still_reports_something():
    """A job that failed before starting leaves no traceback. Say so."""
    session = FakeSession(
        [
            rule("GET", "calc/11/traceback", FakeResponse(404, text="not found")),
            rule("GET", "calc/11/log/", FakeResponse(200, [])),
        ]
    )

    detail = adapter_for(session).failure_detail(11)

    assert "no traceback or log" in detail


def test_a_missing_calculation_says_which_thing_is_missing():
    session = FakeSession([rule("GET", "calc/99/status", FakeResponse(404, text="gone"))])

    with pytest.raises(EngineRejected, match="no record of the requested calculation"):
        adapter_for(session).status(99)


# -- results and export -----------------------------------------------------

def test_results_are_listed_with_their_export_types():
    session = FakeSession(
        [
            rule(
                "GET",
                "calc/11/results",
                FakeResponse(
                    200,
                    [
                        {"id": 5, "name": "Ground Motion Fields", "type": "gmf_data",
                         "outtypes": ["csv", "hdf5"]},
                        {"id": 6, "name": "Events", "type": "events", "outtypes": ["csv"]},
                    ],
                ),
            )
        ]
    )

    found = adapter_for(session).results(11)

    assert [item.type for item in found] == ["gmf_data", "events"]
    assert found[0].outtypes == ("csv", "hdf5")


def test_a_result_is_found_by_the_engine_type_rather_than_its_display_name():
    """The display name carries the calculation id in some engine versions."""
    session = FakeSession(
        [
            rule(
                "GET",
                "calc/11/results",
                FakeResponse(
                    200,
                    [{"id": 5, "name": "Ground Motion Fields rlz-0", "type": "gmf_data",
                      "outtypes": ["hdf5"]}],
                ),
            )
        ]
    )

    found = adapter_for(session).find_result(11, "gmf_data")

    assert found is not None
    assert found.id == 5


def test_an_absent_result_is_nothing_rather_than_an_error():
    session = FakeSession([rule("GET", "calc/11/results", FakeResponse(200, []))])

    assert adapter_for(session).find_result(11, "gmf_data") is None


def test_the_datastore_is_streamed_rather_than_read_whole():
    """A national ground-motion field does not fit comfortably in memory."""
    session = FakeSession(
        [
            rule(
                "GET",
                "calc/11/datastore",
                FakeResponse(200, chunks=[b"HDF", b"5 payload"]),
            )
        ]
    )
    sink = io.BytesIO()

    written = adapter_for(session).download_datastore(11, sink)

    assert written == 12
    assert sink.getvalue() == b"HDF5 payload"
    assert session.calls[0][2]["stream"] is True


def test_exporting_one_result_asks_for_the_requested_type():
    session = FakeSession(
        [rule("GET", "calc/result/5", FakeResponse(200, chunks=[b"csv rows"]))]
    )
    sink = io.BytesIO()

    adapter_for(session).download_result(5, sink, export_type="csv")

    assert session.calls[0][2]["params"] == {"export_type": "csv"}
    assert sink.getvalue() == b"csv rows"


# -- cancellation -----------------------------------------------------------

def test_aborting_reports_the_state_the_engine_gives_back():
    """Section 11 frees the envelope when the engine stopped, not when asked."""
    session = FakeSession(
        [
            rule("POST", "calc/11/abort", FakeResponse(200, {"status": "aborted"})),
            rule("GET", "calc/11/status", FakeResponse(200, {"status": "aborted"})),
        ]
    )

    job = adapter_for(session).abort(11)

    assert job.state is EngineState.CANCELLED


# -- authentication ---------------------------------------------------------

def test_a_server_without_lockdown_needs_no_credentials():
    """Lockdown off is a legitimate local configuration with no login at all."""
    session = FakeSession([version_rule(), digest_rule()])

    assert adapter_for(session).version().version == "3.23.0"
    assert not any("ajax_login" in url for _, url, _ in session.calls)


def test_a_rejected_request_signs_in_once_and_retries():
    session = FakeSession(
        [
            rule(
                "GET",
                "calc/11/status",
                FakeResponse(403, text="forbidden"),
                FakeResponse(200, {"status": "complete"}),
            ),
            rule("POST", "ajax_login", FakeResponse(200, {"success": True})),
        ]
    )

    job = adapter_for(session, username="cass", password="secret").status(11)

    assert job.state is EngineState.SUCCEEDED
    assert any("ajax_login" in url for _, url, _ in session.calls)


def test_credentials_that_do_not_work_are_reported_as_such():
    session = FakeSession(
        [
            rule("GET", "calc/11/status", FakeResponse(403, text="forbidden")),
            rule("POST", "ajax_login", FakeResponse(403, text="bad credentials")),
        ]
    )

    with pytest.raises(EngineRejected, match="rejected the CASS credentials"):
        adapter_for(session, username="cass", password="wrong").status(11)


def test_a_locked_down_server_with_no_credentials_says_what_is_wrong():
    """Not "unreachable": the engine answered, and it wants a user."""
    session = FakeSession([rule("GET", "calc/11/status", FakeResponse(403, text="denied"))])

    with pytest.raises(EngineRejected, match="requires a signed-in user"):
        adapter_for(session).status(11)


# -- transfers ---------------------------------------------------------------

def test_an_export_is_given_the_transfer_timeout_not_the_conversation_one():
    """OpenQuake builds an export whole before it sends a byte of it.

    A region's ground motion takes the better part of a minute to write, and a
    country's several. Held to the conversation timeout, the engine that is
    working looks exactly like the engine that has died.
    """
    session = FakeSession(
        export_rules(
            "gmf_data",
            NamedResponse(200, chunks=[b"event_id"], filename="output-5-gmf-data_11.csv"),
        )
    )
    engine = adapter_for(session, timeout=5, transfer_timeout=900)

    engine.export(11, "gmf_data")

    listed, exported = session.calls
    assert listed[2]["timeout"] == 5
    assert exported[2]["timeout"] == 900


def test_the_datastore_download_is_given_the_transfer_timeout():
    session = FakeSession([rule("GET", "calc/11/datastore", FakeResponse(200, chunks=[b"HDF5"]))])

    adapter_for(session, transfer_timeout=900).download_datastore(11, io.BytesIO())

    assert session.calls[0][2]["timeout"] == 900
