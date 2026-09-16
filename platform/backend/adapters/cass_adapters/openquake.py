"""The OpenQuake engine adapter.

Section 4 requires every engine boundary to be crossed through a versioned
adapter over a supported API, and section 5 gives the hazard pipeline this
module serves: prepare a job, validate its settings, submit it, monitor it,
export the ground-motion fields, and benchmark them.

The boundary is the OpenQuake WebUI's documented REST API, not the engine's
Python package and not its datastore on disk. That distinction is the whole
point of the adapter. ``oq`` is importable, and calling it directly would be
shorter than this file; it would also mean CASS held a second, unversioned
copy of the engine's internals, which is exactly the accumulation of custom
forks section 15 names as the thing adapters exist to prevent.

Two things about OpenQuake shape what follows.

It answers with plain text where most APIs answer with JSON. ``engine_version``
returns ``3.23.0`` and nothing else, so :meth:`OpenQuakeAdapter.version` reads
text rather than a document, and a proxy that returns an HTML error page in its
place is reported as an unreadable version rather than as the string ``<!DOC``.

And a calculation that fails is still a calculation. ``status`` reports
``failed`` with a job record intact, and the reason lives in a separate
traceback resource. A monitor told only "failed" would send a modeller to the
server logs, so :meth:`OpenQuakeAdapter.failure_detail` fetches the traceback
and the tail of the log, and the hazard service records both against the run.
"""

from __future__ import annotations

import dataclasses
import enum
import io
import re
import time
import zipfile
from collections.abc import Callable, Mapping
from typing import Any, BinaryIO

from .base import (
    EngineAdapter,
    EngineJob,
    EngineRejected,
    EngineState,
    EngineVersion,
)
from .http import (
    HttpResponse,
    HttpSession,
    Transport,
    body_text,
    json_body,
    json_or_empty,
    truncated_json,
)

__all__ = [
    "CalculationStatus",
    "HazardResult",
    "OpenQuakeAdapter",
    "calculation_state",
]

#: How much of the log to keep when a calculation fails. The traceback says
#: what broke; the log says what it was doing, and the last few hundred lines
#: are where that is.
LOG_TAIL_LINES = 400


class CalculationStatus(enum.StrEnum):
    """The statuses an OpenQuake 3.x calculation reports."""

    CREATED = "created"
    SUBMITTED = "submitted"
    EXECUTING = "executing"
    COMPLETE = "complete"
    FAILED = "failed"
    ABORTED = "aborted"
    DELETED = "deleted"


#: OpenQuake's vocabulary mapped onto the normalised one. ``deleted`` is a
#: state CASS can reach by asking about a calculation somebody removed on the
#: server, and it is failure rather than cancellation: nothing was stopped on
#: purpose, the evidence simply is not there any more.
_STATES: Mapping[str, EngineState] = {
    CalculationStatus.CREATED: EngineState.PENDING,
    CalculationStatus.SUBMITTED: EngineState.PENDING,
    CalculationStatus.EXECUTING: EngineState.RUNNING,
    CalculationStatus.COMPLETE: EngineState.SUCCEEDED,
    CalculationStatus.FAILED: EngineState.FAILED,
    CalculationStatus.ABORTED: EngineState.CANCELLED,
    CalculationStatus.DELETED: EngineState.FAILED,
}


def calculation_state(status: str) -> EngineState:
    """Normalise one OpenQuake status."""
    return _STATES.get(str(status).strip().lower(), EngineState.UNKNOWN)


@dataclasses.dataclass(frozen=True, slots=True)
class HazardResult:
    """One exportable output of a calculation."""

    id: int
    name: str
    type: str
    outtypes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "outtypes": list(self.outtypes),
        }


class OpenQuakeAdapter(EngineAdapter):
    """Drive an OpenQuake engine server over its documented WebUI API."""

    engine_name = "OpenQuake"

    #: The engine series the hazard contract tests in this package run against.
    #: Section 18 permits promotion only after those suites pass, so this is a
    #: record of what has been tested rather than what might work.
    supported_versions: tuple[str, ...] = ("3.23.0",)

    def __init__(
        self,
        base_url: str,
        *,
        username: str = "",
        password: str = "",
        session: HttpSession | None = None,
        api_version: str = "v1",
        timeout: float = 25.0,
        transfer_timeout: float = 3600.0,
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
        self._signed_in = False
        # The engine builds an export in full before it sends a byte of it: the
        # ground motion of a regional run is tens of seconds of silence, and a
        # national one minutes. The conversation timeout would call that an
        # engine that did not respond.
        self._transfer_timeout = transfer_timeout
        self._transport = Transport(
            session,
            engine_name=self.engine_name,
            base_url=self._base,
            timeout=timeout,
            retries=retries,
            retry_delay=retry_delay,
            sleep=sleep,
        )

    # -- URLs --------------------------------------------------------------
    def _root(self, path: str) -> str:
        return self._base + path.lstrip("/")

    def _url(self, path: str) -> str:
        return self._api + path.lstrip("/")

    def _calc_url(self, calculation_id: int, resource: str = "") -> str:
        return self._url("calc/" + str(calculation_id) + "/" + resource)

    # -- authentication ----------------------------------------------------
    def sign_in(self) -> bool:
        """Sign in, where the server has lockdown switched on.

        An OpenQuake server with ``LOCKDOWN`` off is a legitimate and common
        local configuration, and it has no login endpoint at all. So a failure
        to sign in is not raised here: it is reported as "not signed in", and
        the call that actually needed credentials is the one that refuses. That
        way a deployment without lockdown never sees an error about an
        authentication it does not use.
        """
        if not self._username:
            return False
        try:
            self._transport.send(
                "POST",
                self._root("accounts/ajax_login/"),
                data={"username": self._username, "password": self._password},
            )
        except EngineRejected:
            self._signed_in = False
            return False
        self._signed_in = True
        return True

    def _recover_auth(self) -> bool:
        """One attempt to sign in again after a 401 or 403."""
        return self.sign_in()

    def _send(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
        """One API call, with signing-in offered only where it is possible.

        The recovery hook is withheld when no username is configured, and that
        is the whole difference between two very different messages. With
        credentials, a 403 means they were refused. Without any, it means the
        server wants a user and this installation has not been given one --
        and telling an operator their credentials were rejected would send
        them hunting for a password problem that does not exist.
        """
        return self._transport.send(
            method,
            url,
            recover_auth=self._recover_auth if self._username else None,
            refusal_summary=_refusal_summary,
            **kwargs,
        )

    # -- the base contract -------------------------------------------------
    def version(self) -> EngineVersion:
        """Ask the engine what it is.

        ``engine_version`` answers with a bare version string rather than a
        document, so this reads text. A body that does not look like a version
        is reported as such: an HTML error page from a proxy in front of the
        engine would otherwise be compared against the supported list and
        refused as an untested engine, sending an operator to the wrong place.
        """
        response = self._send("GET", self._url("engine_version"))
        reported = body_text(response).strip().strip('"')
        if not reported or not reported[0].isdigit():
            raise EngineRejected(
                "OpenQuake did not report a version.",
                detail=reported[:2000] or "The response body was empty.",
            )
        return EngineVersion(
            name=self.engine_name,
            version=reported,
            image_digest=self._image_digest(),
        )

    def _image_digest(self) -> str:
        """The container digest, where the deployment exposes one.

        Section 12 requires a result to be traceable to an immutable engine
        version, and a version string is not that on its own: two images can
        report 3.23.0. Absence is not an error, because a bare-metal install
        has no digest to report.
        """
        try:
            body = json_or_empty(self._send("GET", self._url("engine_info")))
        except Exception:
            # Many deployments do not serve this at all. Absence of a digest is
            # not a failure to report; it is simply one fewer thing recorded.
            return ""
        return str(body.get("image_digest") or body.get("digest") or "")

    def healthy(self) -> bool:
        """Whether the engine answers at all."""
        try:
            self._send("GET", self._url("engine_version"))
        except Exception:
            return False
        return True

    # -- running a calculation ---------------------------------------------
    def submit(
        self,
        files: Mapping[str, bytes],
        *,
        job_ini: str = "job.ini",
        hazard_job_id: int | None = None,
    ) -> int:
        """Start a calculation from an in-memory job.

        The files go up as one zip archive rather than being written to a path
        the engine can see. A shared volume would be faster and is what the
        command-line tool does; it would also mean CASS handing the engine a
        filesystem path, which the base class forbids because it makes the two
        deployments inseparable.

        An archive rather than one multipart part per file, and not for
        tidiness. A national source model is a directory -- its logic tree names
        ``ssm/crust/Indo_Faults_CH.xml`` -- and Django keeps only the base name
        of an uploaded file, so separate parts arrive flattened and the engine
        fails looking for sources that were sent. The engine extracts an
        archive with its paths intact.
        """
        if not files:
            raise EngineRejected(
                "A calculation needs at least a job configuration to run.",
                detail="No files were supplied to submit().",
            )
        if job_ini not in files:
            raise EngineRejected(
                f"The job has no {job_ini}, so the engine would not know what to run.",
                detail=f"Files supplied: {', '.join(sorted(files))[:2000]}",
            )

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for name, content in sorted(files.items()):
                bundle.writestr(name, content)

        data: dict[str, Any] = {"job_ini": job_ini}
        if hazard_job_id is not None:
            # Reusing a completed hazard calculation rather than recomputing it
            # is how OpenQuake chains a risk job onto one already run.
            data["hazard_job_id"] = str(hazard_job_id)

        response = self._send(
            "POST",
            self._url("calc/run"),
            files=[("archive", ("job.zip", archive.getvalue(), "application/zip"))],
            data=data,
        )
        body = json_or_empty(response)
        for key in ("job_id", "calc_id", "id"):
            if body.get(key) is not None:
                try:
                    return int(body[key])
                except (TypeError, ValueError):
                    break
        raise EngineRejected(
            "OpenQuake accepted the job but did not return a calculation id.",
            detail=truncated_json(body),
        )

    def status(self, calculation_id: int) -> EngineJob:
        """Where the calculation has got to."""
        body = json_or_empty(self._send("GET", self._calc_url(calculation_id, "status")))
        raw = str(body.get("status") or "")
        return EngineJob(
            engine_job_id=str(calculation_id),
            state=calculation_state(raw),
            progress=_progress(body),
            message=str(body.get("description") or ""),
            raw_state=raw,
        )

    def log_entries(
        self, calculation_id: int, *, start: int = 0, stop: int = 0
    ) -> list[list[str]]:
        """Calculation log entries in their fields: timestamp, level, process, message.

        Kept apart from the joined lines below because a reader of the log and a
        reader of one field of it want different things. The engine writes its
        progress in the message, and a caller looking for it should not have to
        find where the timestamp ended.

        ``stop`` of zero means "to the end", which is the engine's convention
        rather than this adapter's.
        """
        response = self._send(
            "GET", self._calc_url(calculation_id, f"log/{start}:{stop}")
        )
        body = json_body(response)
        if not isinstance(body, list):
            return []
        return [
            [str(part) for part in entry] if isinstance(entry, list) else [str(entry)]
            for entry in body
        ]

    def log(self, calculation_id: int, *, start: int = 0, stop: int = 0) -> list[str]:
        """Calculation log lines, assembled for reading."""
        return [
            " ".join(entry)
            for entry in self.log_entries(calculation_id, start=start, stop=stop)
        ]

    def traceback(self, calculation_id: int) -> list[str]:
        """Why the calculation failed, where the engine kept a traceback."""
        try:
            body = json_body(self._send("GET", self._calc_url(calculation_id, "traceback")))
        except EngineRejected:
            # A failure before the job started leaves no traceback file. That
            # is a fact about the failure, not a second failure to report.
            return []
        if isinstance(body, list):
            return [str(line) for line in body]
        return [str(body)] if body else []

    def failure_detail(self, calculation_id: int) -> str:
        """The traceback and the tail of the log, as one readable block.

        Assembled here rather than by the caller because it is the same two
        requests every time, and a monitor that showed one without the other
        would say either what broke or what it was doing, never both.
        """
        parts: list[str] = []
        trace = self.traceback(calculation_id)
        if trace:
            parts.append("Traceback reported by OpenQuake:\n" + "\n".join(trace))
        try:
            lines = self.log(calculation_id)
        except Exception:  # pragma: no cover - the traceback is the important half
            lines = []
        if lines:
            tail = lines[-LOG_TAIL_LINES:]
            parts.append(f"Last {len(tail)} log line(s):\n" + "\n".join(tail))
        return "\n\n".join(parts) or "OpenQuake recorded no traceback or log for this calculation."

    def results(self, calculation_id: int) -> list[HazardResult]:
        """Everything the calculation produced that can be exported."""
        body = json_body(self._send("GET", self._calc_url(calculation_id, "results")))
        if not isinstance(body, list):
            return []
        found: list[HazardResult] = []
        for entry in body:
            if not isinstance(entry, Mapping):
                continue
            try:
                identifier = int(entry.get("id"))
            except (TypeError, ValueError):
                continue
            outtypes = entry.get("outtypes") or []
            found.append(
                HazardResult(
                    id=identifier,
                    name=str(entry.get("name") or ""),
                    type=str(entry.get("type") or ""),
                    outtypes=tuple(str(item) for item in outtypes),
                )
            )
        return found

    def find_result(self, calculation_id: int, name: str) -> HazardResult | None:
        """The named output, or nothing.

        Matched on the engine's own ``type`` first and its display name second,
        because the display name carries the calculation id in some engine
        versions and is not a stable key.
        """
        wanted = name.strip().lower()
        available = self.results(calculation_id)
        for item in available:
            if item.type.strip().lower() == wanted:
                return item
        for item in available:
            if wanted in item.name.strip().lower():
                return item
        return None

    def download_result(
        self,
        result_id: int,
        sink: BinaryIO,
        *,
        export_type: str = "csv",
        chunk_size: int = 1024 * 1024,
    ) -> int:
        """Stream one exported result into ``sink``.

        Streamed rather than read whole because a national ground-motion field
        does not fit comfortably in memory, which is the same reason section 11
        requires the resource envelope to be declared.
        """
        response = self._send(
            "GET",
            self._url("calc/result/" + str(result_id)),
            params={"export_type": export_type},
            stream=True,
            timeout=self._transfer_timeout,
        )
        return _stream(response, sink, chunk_size)

    def download_datastore(
        self, calculation_id: int, sink: BinaryIO, *, chunk_size: int = 1024 * 1024
    ) -> int:
        """Stream the whole HDF5 datastore for a calculation.

        The hazard pipeline's ``export`` stage wants the GMF in HDF5, and the
        datastore is the engine's own complete record of the calculation: one
        file, checksummed once, from which every later question can be answered
        without asking the engine again.
        """
        response = self._send(
            "GET",
            self._calc_url(calculation_id, "datastore"),
            stream=True,
            timeout=self._transfer_timeout,
        )
        return _stream(response, sink, chunk_size)

    def export(
        self, calculation_id: int, output_type: str, *, export_type: str = "csv"
    ) -> dict[str, bytes]:
        """One output exported as files, keyed by the name the engine gave each.

        The converter reads CSV exports -- ``gmf-data_12.csv``,
        ``events_12.csv`` -- and finds them by those names, so the names matter
        as much as the bytes. The engine returns a single file as itself and
        several as a zip, and prefixes the download name with the result id;
        both are undone here so a caller gets exactly the files ``oq export``
        would have written to disk.
        """
        result = self.find_result(calculation_id, output_type)
        if result is None:
            raise EngineRejected(
                f"Calculation {calculation_id} has no {output_type} output to export.",
                detail="Outputs: "
                + ", ".join(item.type for item in self.results(calculation_id)),
            )
        if result.outtypes and export_type not in result.outtypes:
            raise EngineRejected(
                f"OpenQuake cannot export {output_type} as {export_type}.",
                detail=f"It offers: {', '.join(result.outtypes)}",
            )

        response = self._send(
            "GET",
            self._url("calc/result/" + str(result.id)),
            params={"export_type": export_type},
            stream=True,
            timeout=self._transfer_timeout,
        )
        sink = io.BytesIO()
        _stream(response, sink, 1024 * 1024)
        payload = sink.getvalue()
        name = _download_name(response, result.id, fallback=f"{output_type}.{export_type}")

        if payload[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
                return {
                    member.rsplit("/", 1)[-1]: bundle.read(member)
                    for member in bundle.namelist()
                    if not member.endswith("/")
                }
        return {name: payload}

    def abort(self, calculation_id: int) -> EngineJob:
        """Ask the engine to stop a running calculation.

        Section 11 counts the resource envelope as freed only once the engine
        has actually stopped, so this reports the state the engine gives back
        rather than assuming the request succeeded.
        """
        self._send("POST", self._calc_url(calculation_id, "abort"))
        return self.status(calculation_id)

    def remove(self, calculation_id: int) -> None:
        """Remove a calculation from the engine.

        Used for cleanup after the outputs are safely in the artifact store.
        Deliberately not part of cancellation: a cancelled run keeps its
        evidence, and section 12 requires a failed attempt to remain readable.
        """
        self._send("POST", self._calc_url(calculation_id, "remove"))


def _download_name(response: HttpResponse, result_id: int, *, fallback: str) -> str:
    """The name the engine gave a download, without its result-id prefix.

    The WebUI names every export ``output-<result id>-<file>``. The converter
    finds files by what ``oq export`` would have called them, so the prefix is
    removed rather than carried into a name nothing looks for.
    """
    headers = getattr(response, "headers", None) or {}
    disposition = str(
        headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    )
    match = re.search(r'filename="?([^";]+)"?', disposition)
    name = match.group(1).strip() if match else fallback
    prefix = f"output-{result_id}-"
    return name[len(prefix) :] if name.startswith(prefix) else name


def _stream(response: HttpResponse, sink: BinaryIO, chunk_size: int) -> int:
    written = 0
    for chunk in response.iter_content(chunk_size=chunk_size):
        if chunk:
            sink.write(chunk)
            written += len(chunk)
    return written


def _progress(body: Mapping[str, Any]) -> float | None:
    """A fraction complete, where the engine reports enough to compute one."""
    done = body.get("done") if isinstance(body, Mapping) else None
    total = body.get("total") if isinstance(body, Mapping) else None
    try:
        done_value = float(done)
        total_value = float(total)
    except (TypeError, ValueError):
        return None
    if total_value <= 0:
        return None
    return max(0.0, min(1.0, done_value / total_value))


def _refusal_summary(method: str, status: int) -> str:
    """Say what the engine refused, in terms the run monitor can display."""
    if status == 404:
        return "OpenQuake has no record of the requested calculation or result."
    if status == 400:
        return "OpenQuake rejected the job configuration as invalid."
    if status == 403:
        return "OpenQuake refused the request: this server requires a signed-in user."
    return "OpenQuake refused the " + method + " request with HTTP " + str(status) + "."
