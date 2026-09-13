"""The Oasis Platform adapter.

Section 4 of the build plan requires every engine boundary to be crossed
through a versioned adapter over a supported API, and section 5 gives the
ownership split this module implements: CASS creates the business records and
the immutable OED, CASS keys maps exposure to the model, and the pinned
OasisLMF implementation inside the platform builds the kernel and financial
files. Nothing here reimplements Oasis logic. It drives the documented REST
API of an Oasis Platform server and translates what comes back into terms the
run monitor can display.

Section 5's analysis pipeline crosses this boundary four times:

``generate_inputs``
    Oasis runs its own keys and file generation and reaches ``READY``.

``losses``
    Oasis runs ground-up and the supported financial perspectives.

``collect``
    CASS pulls the ORD outputs back and registers them as artifacts.

``cancel``
    A cancellation must reach the engine, not merely the CASS record, or the
    worker keeps burning the resource envelope section 11 allocated.

The three rules the base class states are honoured as follows. Compatibility is
declared in :attr:`OasisAdapter.supported_versions` and enforced before any
call that changes engine state. Nothing writes to the Oasis database or its
shared filesystem: files go up as multipart uploads and come back as streamed
downloads, so no CASS or Windows host path is ever handed to the engine. And
every failure is raised as an :class:`~cass_adapters.base.AdapterError`
carrying a sentence an analyst can act on, with the engine's own text kept in
``detail`` for the support bundle.

The adapter takes its HTTP session by injection. That is what lets the contract
tests in ``adapters/tests`` run the whole conversation -- authentication, token
refresh, upload, poll, cancel, download -- without an Oasis server, and it is
why this module adds no dependency beyond the ``requests`` already pinned for
the control plane.
"""

from __future__ import annotations

import dataclasses
import enum
import io
import json
import time
from collections.abc import Callable, Mapping
from typing import Any, BinaryIO

from .base import (
    AdapterError,
    EngineAdapter,
    EngineJob,
    EngineRejected,
    EngineState,
    EngineUnavailable,
    EngineVersion,
)
from .http import (
    HttpResponse,
    HttpSession,
    Transport,
)
from .http import (
    body_text as _body_text,
)

__all__ = [
    "AnalysisStatus",
    "OasisAdapter",
    "OasisModel",
    "OasisPhase",
    "PortfolioFileKind",
    "analysis_state",
]


# -- the engine's vocabulary ------------------------------------------------

class AnalysisStatus(enum.StrEnum):
    """The statuses an Oasis 2.5.x analysis reports.

    One field carries two phases: input generation and the loss run. That is
    why :func:`analysis_state` needs to know which phase is being watched --
    ``READY`` means finished to an input-generation watcher and not-yet-started
    to a loss watcher, and a poller that confuses the two either stops early or
    never stops at all.
    """

    NEW = "NEW"
    INPUTS_GENERATION_QUEUED = "INPUTS_GENERATION_QUEUED"
    INPUTS_GENERATION_STARTED = "INPUTS_GENERATION_STARTED"
    INPUTS_GENERATION_CANCELLED = "INPUTS_GENERATION_CANCELLED"
    INPUTS_GENERATION_ERROR = "INPUTS_GENERATION_ERROR"
    INPUTS_GENERATION_NO_KEYS = "INPUTS_GENERATION_NO_KEYS"
    READY = "READY"
    RUN_QUEUED = "RUN_QUEUED"
    RUN_STARTED = "RUN_STARTED"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_CANCELLED = "RUN_CANCELLED"
    RUN_ERROR = "RUN_ERROR"


class OasisPhase(enum.StrEnum):
    """Which half of the analysis lifecycle a caller is watching."""

    INPUTS = "inputs"
    """Keys and Oasis file generation, ending at ``READY``."""

    LOSSES = "losses"
    """The loss calculation, ending at ``RUN_COMPLETED``."""


class PortfolioFileKind(enum.StrEnum):
    """The OED files a portfolio accepts, named as the API names them."""

    LOCATION = "location_file"
    ACCOUNTS = "accounts_file"
    REINSURANCE_INFO = "reinsurance_info_file"
    REINSURANCE_SCOPE = "reinsurance_scope_file"


#: Input generation, from the watcher's point of view. Every status at or past
#: ``READY`` means the inputs exist, including the loss statuses: an analysis
#: that is running losses has necessarily finished generating its inputs.
_INPUT_STATES: Mapping[AnalysisStatus, EngineState] = {
    AnalysisStatus.NEW: EngineState.PENDING,
    AnalysisStatus.INPUTS_GENERATION_QUEUED: EngineState.PENDING,
    AnalysisStatus.INPUTS_GENERATION_STARTED: EngineState.RUNNING,
    AnalysisStatus.INPUTS_GENERATION_CANCELLED: EngineState.CANCELLED,
    AnalysisStatus.INPUTS_GENERATION_ERROR: EngineState.FAILED,
    AnalysisStatus.INPUTS_GENERATION_NO_KEYS: EngineState.FAILED,
    AnalysisStatus.READY: EngineState.SUCCEEDED,
    AnalysisStatus.RUN_QUEUED: EngineState.SUCCEEDED,
    AnalysisStatus.RUN_STARTED: EngineState.SUCCEEDED,
    AnalysisStatus.RUN_COMPLETED: EngineState.SUCCEEDED,
    AnalysisStatus.RUN_CANCELLED: EngineState.SUCCEEDED,
    AnalysisStatus.RUN_ERROR: EngineState.SUCCEEDED,
}

#: The loss run. An input-generation failure is reported as a loss-phase
#: failure too, because a run waiting behind failed inputs will never start and
#: a monitor that shows it as pending forever is the unintelligible state
#: section 12 forbids.
_LOSS_STATES: Mapping[AnalysisStatus, EngineState] = {
    AnalysisStatus.NEW: EngineState.PENDING,
    AnalysisStatus.INPUTS_GENERATION_QUEUED: EngineState.PENDING,
    AnalysisStatus.INPUTS_GENERATION_STARTED: EngineState.PENDING,
    AnalysisStatus.INPUTS_GENERATION_CANCELLED: EngineState.CANCELLED,
    AnalysisStatus.INPUTS_GENERATION_ERROR: EngineState.FAILED,
    AnalysisStatus.INPUTS_GENERATION_NO_KEYS: EngineState.FAILED,
    AnalysisStatus.READY: EngineState.PENDING,
    AnalysisStatus.RUN_QUEUED: EngineState.PENDING,
    AnalysisStatus.RUN_STARTED: EngineState.RUNNING,
    AnalysisStatus.RUN_COMPLETED: EngineState.SUCCEEDED,
    AnalysisStatus.RUN_CANCELLED: EngineState.CANCELLED,
    AnalysisStatus.RUN_ERROR: EngineState.FAILED,
}

#: What to tell the analyst. The run monitor shows these verbatim, so they say
#: what is happening rather than restating the enumeration name.
_MESSAGES: Mapping[AnalysisStatus, str] = {
    AnalysisStatus.NEW: "The analysis exists but nothing has been submitted yet.",
    AnalysisStatus.INPUTS_GENERATION_QUEUED: "Waiting for an Oasis worker to begin generating inputs.",
    AnalysisStatus.INPUTS_GENERATION_STARTED: "Oasis is running keys and generating the kernel files.",
    AnalysisStatus.INPUTS_GENERATION_CANCELLED: "Input generation was cancelled before it finished.",
    AnalysisStatus.INPUTS_GENERATION_ERROR: "Oasis could not generate the input files.",
    AnalysisStatus.INPUTS_GENERATION_NO_KEYS: (
        "The model returned no keys for any location in this portfolio, so there is "
        "nothing to calculate a loss on. Its keys-errors file says why each one was "
        "refused."
    ),
    AnalysisStatus.READY: "The input files are generated and the analysis is ready to run.",
    AnalysisStatus.RUN_QUEUED: "Waiting for an Oasis worker to begin the loss calculation.",
    AnalysisStatus.RUN_STARTED: "Oasis is calculating losses.",
    AnalysisStatus.RUN_COMPLETED: "The loss calculation finished and the outputs are available.",
    AnalysisStatus.RUN_CANCELLED: "The loss calculation was cancelled before it finished.",
    AnalysisStatus.RUN_ERROR: "The loss calculation failed.",
}


def analysis_state(status: str, phase: OasisPhase) -> EngineState:
    """Normalise one Oasis status against the phase being watched.

    An unrecognised status is ``UNKNOWN`` rather than an exception: a new
    status in a future patch release should show as unknown in the monitor, not
    crash the poller that is holding a run's only progress record.
    """
    known = _status_or_none(status)
    if known is None:
        return EngineState.UNKNOWN
    table = _INPUT_STATES if phase is OasisPhase.INPUTS else _LOSS_STATES
    return table[known]


# -- the transport ----------------------------------------------------------

@dataclasses.dataclass(frozen=True, slots=True)
class OasisModel:
    """A model as the Oasis server has it registered."""

    id: int
    supplier_id: str
    model_id: str
    version_id: str

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


#: Statuses that mean the server is briefly unavailable rather than refusing.
class OasisAdapter(EngineAdapter):
    """Drive an Oasis Platform server over its documented REST API."""

    engine_name = "Oasis Platform"

    #: The Oasis Platform release the PiWind baseline and the contract tests in
    #: this package run against. Section 18 permits promotion only after those
    #: suites pass, so this list is a record of what has actually been tested
    #: rather than a statement of what might work.
    supported_versions: tuple[str, ...] = ("2.5.7",)

    def __init__(
        self,
        base_url: str,
        *,
        username: str = "",
        password: str = "",
        session: HttpSession | None = None,
        api_version: str = "v2",
        timeout: float = 25.0,
        retries: int = 4,
        retry_delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if session is None:  # pragma: no cover - exercised against a live server
            import requests

            session = requests.Session()
        self._session = session
        self._base = base_url.rstrip("/") + "/"
        self._api = self._base + api_version.strip("/") + "/"
        self._username = username
        self._password = password
        self._transport = Transport(
            session,
            engine_name=self.engine_name,
            base_url=self._base,
            timeout=timeout,
            retries=retries,
            retry_delay=retry_delay,
            sleep=sleep,
        )

        self._access_token = ""
        self._refresh_token = ""
        self._auth_type: str | None = None

    # -- URLs --------------------------------------------------------------
    def _root(self, path: str) -> str:
        """A path on the server root, outside the versioned API."""
        return self._base + path.lstrip("/")

    def _url(self, path: str) -> str:
        """A path inside the versioned API."""
        return self._api + path.lstrip("/")

    def _analysis_url(self, analysis_id: int, resource: str = "") -> str:
        return self._url("analyses/" + str(analysis_id) + "/" + resource)

    def _portfolio_url(self, portfolio_id: int, resource: str = "") -> str:
        return self._url("portfolios/" + str(portfolio_id) + "/" + resource)

    # -- authentication ----------------------------------------------------
    def _server_auth_type(self) -> str:
        """Ask the server how it wants to be authenticated.

        A server with authentication disabled is a legitimate local
        configuration, and asking rather than assuming means the same adapter
        serves a developer workstation and a deployment where section 10's
        access control is switched on.
        """
        if self._auth_type is not None:
            return self._auth_type
        try:
            response = self._send("GET", self._root("server_info/"), authenticate=False)
            config = _json_or_empty(response).get("config") or {}
            self._auth_type = str(config.get("API_AUTH_TYPE") or "simple")
        except AdapterError:
            self._auth_type = "simple"
        return self._auth_type

    def authenticate(self) -> None:
        """Obtain an access token, unless the server has authentication off."""
        if self._server_auth_type() == "disabled":
            self._access_token = ""
            self._refresh_token = ""
            return
        if not (self._username and self._password):
            raise EngineRejected(
                "No Oasis credentials are configured.",
                detail=(
                    "Set the Oasis username and password in the deployment "
                    "configuration; the server requires authentication."
                ),
            )
        response = self._send(
            "POST",
            self._root("access_token/"),
            authenticate=False,
            json={"username": self._username, "password": self._password},
        )
        body = _json_or_empty(response)
        self._access_token = str(body.get("access_token") or "")
        self._refresh_token = str(body.get("refresh_token") or "")
        if not self._access_token:
            raise EngineRejected(
                "Oasis accepted the sign-in request but returned no access token.",
                detail=json.dumps(dict(body))[:2000],
            )

    def _refresh(self) -> bool:
        """Exchange the refresh token for a new access token."""
        if not self._refresh_token:
            return False
        try:
            response = self._send(
                "POST",
                self._root("refresh_token/"),
                authenticate=False,
                headers={"authorization": "Bearer " + self._refresh_token},
            )
        except AdapterError:
            return False
        body = _json_or_empty(response)
        token = str(body.get("access_token") or "")
        if not token:
            return False
        self._access_token = token
        if body.get("refresh_token"):
            self._refresh_token = str(body["refresh_token"])
        return True

    def _reauthenticate(self) -> bool:
        """Sign in again from scratch, for when the refresh token has gone too."""
        try:
            self.authenticate()
        except AdapterError:
            return False
        return bool(self._access_token) or self._auth_type == "disabled"

    # -- the request loop --------------------------------------------------
    def _send(
        self,
        method: str,
        url: str,
        *,
        authenticate: bool = True,
        headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> HttpResponse:
        """Make one API call through the shared transport.

        What is Oasis-specific is the pair of callbacks: a bearer token in the
        authorization header, and one attempt to refresh it -- falling back to
        signing in again -- when the server says it has expired. The retry
        rules themselves are the same for every engine and live in
        :mod:`cass_adapters.http`.
        """

        def auth_headers() -> Mapping[str, str]:
            if authenticate and self._access_token:
                return {"authorization": "Bearer " + self._access_token}
            return {}

        def recover() -> bool:
            if not authenticate:
                return False
            return bool(self._refresh() or self._reauthenticate())

        return self._transport.send(
            method,
            url,
            headers=headers,
            auth_headers=auth_headers,
            recover_auth=recover if authenticate else None,
            refusal_summary=_refusal_summary,
            **kwargs,
        )

    # -- the base contract -------------------------------------------------
    def version(self) -> EngineVersion:
        """Ask the server what it is.

        ``server_info`` needs credentials on a real 2.5.x deployment, unlike
        ``healthcheck``. The request therefore goes through the authenticated
        path, which signs in first if no token is held yet: a compatibility
        check that reported "engine unreachable" because nobody had signed in
        would send an operator hunting for a network fault that is not there.
        """
        body = _json_or_empty(self._send("GET", self._root("server_info/")))
        config = body.get("config") or {}
        reported = (
            body.get("version")
            or body.get("api_version")
            or (config.get("VERSION") if isinstance(config, Mapping) else "")
            or ""
        )
        if not reported:
            raise EngineRejected(
                self.engine_name + " did not report a version.",
                detail=json.dumps(dict(body))[:2000],
            )
        return EngineVersion(
            name=self.engine_name,
            version=str(reported),
            image_digest=str(body.get("image_digest") or ""),
        )

    def healthy(self) -> bool:
        """Whether the server answers its health check."""
        try:
            self._send("GET", self._root("healthcheck/"), authenticate=False)
        except AdapterError:
            return False
        return True

    # -- models ------------------------------------------------------------
    def models(self) -> list[OasisModel]:
        """List the models this server can run."""
        response = self._send("GET", self._url("models/"))
        return [_model(item) for item in _results(_json_body(response))]

    def find_model(self, supplier_id: str, model_id: str, version_id: str) -> OasisModel:
        """Resolve the registered model triple to the id the API works in.

        Section 12 requires a result to be traceable to an immutable model
        version. Looking the triple up each time, rather than storing a bare
        numeric id, means a rebuilt server cannot silently point a CASS run at
        a different model that happened to inherit the same primary key.
        """
        for model in self.models():
            if (
                model.supplier_id == str(supplier_id)
                and model.model_id == str(model_id)
                and model.version_id == str(version_id)
            ):
                return model
        raise EngineRejected(
            self.engine_name
            + " has no model registered as "
            + "/".join([str(supplier_id), str(model_id), str(version_id)])
            + ".",
            detail=(
                "Register the model package with the Oasis server, or correct the "
                "supplier, model and version identifiers on the CASS model version."
            ),
        )

    # -- portfolios --------------------------------------------------------
    def create_portfolio(self, name: str) -> int:
        """Create an empty portfolio and return its id."""
        response = self._send("POST", self._url("portfolios/"), json={"name": name})
        return _identifier(response, "portfolio")

    def upload_portfolio_file(
        self,
        portfolio_id: int,
        kind: PortfolioFileKind,
        filename: str,
        content: bytes | BinaryIO,
    ) -> Mapping[str, Any]:
        """Upload one OED file to a portfolio.

        Content is passed as bytes or an open binary stream, never as a path.
        That is the base class's second rule made concrete: the artifact store
        opens the object, the adapter posts the bytes, and no CASS or Windows
        host path is ever visible to the engine.
        """
        stream: Any = io.BytesIO(content) if isinstance(content, bytes) else content
        response = self._send(
            "POST",
            self._portfolio_url(portfolio_id, PortfolioFileKind(kind).value + "/"),
            files={"file": (filename, stream, "text/csv")},
        )
        return _json_or_empty(response)

    def portfolio(self, portfolio_id: int) -> Mapping[str, Any]:
        """Fetch a portfolio record."""
        return _json_or_empty(self._send("GET", self._portfolio_url(portfolio_id)))

    # -- analyses ----------------------------------------------------------
    def create_analysis(self, name: str, portfolio_id: int, model_id: int) -> int:
        """Create an analysis over a portfolio and a model, returning its id."""
        response = self._send(
            "POST",
            self._url("analyses/"),
            json={
                "name": name,
                "portfolio": int(portfolio_id),
                "model": int(model_id),
                "complex_model_data_files": [],
            },
        )
        return _identifier(response, "analysis")

    def upload_settings(self, analysis_id: int, settings: Mapping[str, Any]) -> None:
        """Post the analysis settings document."""
        self._send("POST", self._analysis_url(analysis_id, "settings/"), json=dict(settings))

    def analysis(self, analysis_id: int) -> Mapping[str, Any]:
        """Fetch the analysis record."""
        return _json_or_empty(self._send("GET", self._analysis_url(analysis_id)))

    def generate_inputs(self, analysis_id: int) -> EngineJob:
        """Ask Oasis to run keys and build the kernel files."""
        self.check_compatible()
        self._send("POST", self._analysis_url(analysis_id, "generate_inputs/"), json={})
        return self.job(analysis_id, OasisPhase.INPUTS)

    def run(self, analysis_id: int) -> EngineJob:
        """Ask Oasis to calculate losses from inputs that are already generated."""
        self.check_compatible()
        self._send("POST", self._analysis_url(analysis_id, "run/"), json={})
        return self.job(analysis_id, OasisPhase.LOSSES)

    def job(self, analysis_id: int, phase: OasisPhase) -> EngineJob:
        """Report the analysis as a normalised job for the run monitor."""
        return self._job_from(self.analysis(analysis_id), analysis_id, phase)

    def _job_from(
        self, record: Mapping[str, Any], analysis_id: int, phase: OasisPhase
    ) -> EngineJob:
        raw = str(record.get("status") or "")
        state = analysis_state(raw, phase)
        known = _status_or_none(raw)
        message = (
            _MESSAGES[known]
            if known is not None
            else self.engine_name
            + " reported an unrecognised status: "
            + (raw or "none")
            + "."
        )
        return EngineJob(
            engine_job_id=str(analysis_id),
            state=state,
            progress=_progress(record, state),
            message=message,
            raw_state=raw,
        )

    def cancel(self, analysis_id: int, phase: OasisPhase) -> EngineJob:
        """Cancel the running phase on the engine itself.

        Section 11 requires cancellation to free the resource envelope. Marking
        the CASS record cancelled while an Oasis worker keeps running would
        leave the queue accounting wrong and the compute still spent, so the
        cancellation is sent to the engine and the engine's answer is returned.
        """
        resource = (
            "cancel_generate_inputs/"
            if phase is OasisPhase.INPUTS
            else "cancel_analysis_run/"
        )
        self._send("POST", self._analysis_url(analysis_id, resource), json={})
        return self.job(analysis_id, phase)

    def poll(
        self,
        analysis_id: int,
        phase: OasisPhase,
        *,
        interval: float = 5.0,
        timeout: float | None = None,
        on_update: Callable[[EngineJob], None] | None = None,
    ) -> EngineJob:
        """Poll one phase to a terminal state.

        ``on_update`` receives every observation, which is how the run monitor
        gets a stage message and a progress fraction while the engine works
        rather than only once it finishes.
        """
        waited = 0.0
        last = self.job(analysis_id, phase)
        while True:
            if on_update:
                on_update(last)
            if last.state.is_terminal:
                return last
            if timeout is not None and waited >= timeout:
                raise EngineUnavailable(
                    self.engine_name + " did not finish within the allowed time.",
                    detail=(
                        "Analysis "
                        + str(analysis_id)
                        + " was still "
                        + (last.raw_state or "unreported")
                        + " after "
                        + format(waited, ".0f")
                        + " seconds of the "
                        + phase.value
                        + " phase."
                    ),
                )
            self._transport.sleep(interval)
            waited += interval
            last = self.job(analysis_id, phase)

    # -- files coming back -------------------------------------------------
    def download(self, analysis_id: int, resource: str, sink: BinaryIO) -> int:
        """Stream one analysis file into a caller-provided binary sink.

        The adapter deliberately does not know about the artifact store. It
        writes bytes where it is told and the caller decides what URI those
        bytes become, which keeps the dependency pointing one way.
        """
        response = self._send(
            "GET", self._analysis_url(analysis_id, resource), stream=True
        )
        written = 0
        for chunk in response.iter_content(chunk_size=512 * 1024):
            if chunk:
                sink.write(chunk)
                written += len(chunk)
        return written

    def download_outputs(self, analysis_id: int, sink: BinaryIO) -> int:
        """Stream the ORD output archive."""
        return self.download(analysis_id, "output_file/", sink)

    def download_inputs(self, analysis_id: int, sink: BinaryIO) -> int:
        """Stream the generated kernel input archive."""
        return self.download(analysis_id, "input_file/", sink)

    def keys_report(self, analysis_id: int) -> dict[str, str]:
        """Fetch the keys success, error and validation files as text.

        Section 5 requires keys reconciliation before the loss stage is
        admitted. These three files are what Oasis's own lookup produced, and
        CASS reconciles them against its keys result rather than trusting
        either side alone.
        """
        return {
            "success": self._text_or_blank(analysis_id, "lookup_success_file/"),
            "errors": self._text_or_blank(analysis_id, "lookup_errors_file/"),
            "validation": self._text_or_blank(analysis_id, "lookup_validation_file/"),
        }

    def failure_log(self, analysis_id: int, phase: OasisPhase) -> str:
        """Fetch the traceback for a failed phase, for the failure detail."""
        resource = (
            "input_generation_traceback_file/"
            if phase is OasisPhase.INPUTS
            else "run_traceback_file/"
        )
        return self._text_or_blank(analysis_id, resource)

    def _text_or_blank(self, analysis_id: int, resource: str) -> str:
        """Fetch a text file, treating absence as empty rather than as an error.

        Oasis returns 404 for a file a given run never produced -- a successful
        run has no traceback, a run with no failures has no lookup error file.
        Absence is information, not a fault, so it must not turn a diagnostic
        fetch into a run failure.
        """
        try:
            response = self._send("GET", self._analysis_url(analysis_id, resource))
        except EngineRejected:
            return ""
        return _body_text(response)


# -- helpers ----------------------------------------------------------------

def _status_or_none(status: str) -> AnalysisStatus | None:
    try:
        return AnalysisStatus(status)
    except ValueError:
        return None


def _progress(record: Mapping[str, Any], state: EngineState) -> float | None:
    """Derive a progress fraction from the sub-task counts, when there are any.

    Oasis's V2 run mode reports a ``status_count`` breakdown of sub-tasks. It is
    the only honest progress signal the API offers; without it the monitor
    shows a stage rather than a bar, which is better than inventing a number.
    """
    if state is EngineState.SUCCEEDED:
        return 1.0
    counts = record.get("status_count")
    if not isinstance(counts, Mapping):
        return None
    total = counts.get("TOTAL")
    completed = counts.get("COMPLETED")
    if not isinstance(total, int) or not isinstance(completed, int) or total <= 0:
        return None
    return max(0.0, min(1.0, completed / total))


def _results(payload: Any) -> list[Mapping[str, Any]]:
    """Unwrap a list response, which may or may not be paginated."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping) and isinstance(payload.get("results"), list):
        return [item for item in payload["results"] if isinstance(item, Mapping)]
    return []


def _model(item: Mapping[str, Any]) -> OasisModel:
    return OasisModel(
        id=int(item["id"]),
        supplier_id=str(item.get("supplier_id", "")),
        model_id=str(item.get("model_id", "")),
        version_id=str(item.get("version_id", "")),
    )


def _json_body(response: HttpResponse) -> Any:
    try:
        return response.json()
    except Exception:
        return None


def _json_or_empty(response: HttpResponse) -> Mapping[str, Any]:
    body = _json_body(response)
    return body if isinstance(body, Mapping) else {}


def _identifier(response: HttpResponse, what: str) -> int:
    """Read the id out of a creation response, or say what was missing.

    A creation that returns 2xx without a usable id would otherwise fail later
    with a confusing 404 against ``None``, which is exactly the unintelligible
    state the base class exists to prevent.
    """
    body = _json_or_empty(response)
    try:
        return int(body["id"])
    except (KeyError, TypeError, ValueError):
        raise EngineRejected(
            "Oasis Platform did not return an identifier for the new " + what + ".",
            detail=json.dumps(dict(body))[:2000],
        ) from None


def _refusal_summary(method: str, status: int) -> str:
    """Say what the engine refused, in terms the run monitor can display."""
    if status == 404:
        return "Oasis Platform has no record of the requested analysis or file."
    if status == 400:
        return "Oasis Platform rejected the request as invalid."
    if status == 409:
        return "Oasis Platform refused the request because the analysis is in the wrong state."
    return (
        "Oasis Platform refused the "
        + method
        + " request with HTTP "
        + str(status)
        + "."
    )
