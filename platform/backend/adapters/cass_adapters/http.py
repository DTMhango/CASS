"""The HTTP conversation an engine adapter has, minus the engine.

Two adapters now cross an engine boundary over REST, and the part that is not
about the engine is identical in both: which statuses are worth retrying, how
many times, how to recover once from an expired credential, and how to turn a
transport failure into something a run monitor can display rather than a stack
trace.

That part lives here so it is decided once. What stays in each adapter is
everything that is actually about its engine -- the URLs, the vocabulary, the
version check, and how it authenticates -- because those are the things that
differ, and a shared layer that tried to absorb them would end up with a
parameter for each.

Authentication is a pair of callbacks rather than a base class. Oasis holds a
bearer token it can refresh; the OpenQuake WebUI holds a Django session cookie
and may have authentication switched off entirely. Those have nothing in common
except the moment they happen, which is what the callbacks name.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any, Protocol

from .base import EngineRejected, EngineUnavailable

__all__ = [
    "AUTH_STATUS",
    "HttpResponse",
    "HttpSession",
    "RETRYABLE_STATUS",
    "Transport",
    "body_text",
    "detail",
    "json_body",
    "json_or_empty",
]

#: Statuses worth another attempt: a restarting gateway rather than a refusal.
RETRYABLE_STATUS = frozenset({502, 503, 504})

#: Statuses that mean a credential has expired or is not accepted.
AUTH_STATUS = frozenset({401, 403})


class HttpResponse(Protocol):
    """The part of a ``requests`` response an adapter uses."""

    status_code: int
    text: str

    def json(self) -> Any: ...

    def iter_content(self, chunk_size: int = ...) -> Iterator[bytes]: ...


class HttpSession(Protocol):
    """The part of a ``requests.Session`` an adapter uses.

    Kept this narrow so a contract test can supply a fake in a dozen lines, and
    so no adapter reaches for session state a different transport would lack.
    """

    def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse: ...


class Transport:
    """One engine's HTTP conversation, with the retry rules applied."""

    def __init__(
        self,
        session: HttpSession,
        *,
        engine_name: str,
        base_url: str,
        timeout: float = 25.0,
        retries: int = 4,
        retry_delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session
        self.engine_name = engine_name
        self.base_url = base_url
        self.timeout = timeout
        self.retries = max(1, retries)
        self.retry_delay = retry_delay
        self.sleep = sleep

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        auth_headers: Callable[[], Mapping[str, str]] | None = None,
        recover_auth: Callable[[], bool] | None = None,
        refusal_summary: Callable[[str, int], str] | None = None,
        **kwargs: Any,
    ) -> HttpResponse:
        """Make one API call, retrying what is worth retrying.

        Three failures are distinguished, because the run monitor treats them
        differently. A connection failure or a gateway status is transient and
        the run may be retried. An expired credential is recovered once, in
        place. A 4xx refusal is the engine saying no, and repeating it only
        wastes the queue slot without changing the answer.
        """
        recovered = False
        last_error = ""
        # A call may wait longer than the conversation default: an export is
        # computed whole before its first byte is sent.
        timeout = kwargs.pop("timeout", self.timeout)

        for attempt in range(1, self.retries + 1):
            request_headers: dict[str, str] = {"accept": "application/json"}
            if auth_headers is not None:
                request_headers.update(auth_headers())
            if headers:
                request_headers.update(headers)

            try:
                response = self.session.request(
                    method,
                    url,
                    headers=request_headers,
                    timeout=timeout,
                    **kwargs,
                )
            except Exception as exc:  # transport failure: no response at all
                last_error = type(exc).__name__ + ": " + str(exc)
                if attempt < self.retries:
                    self.sleep(self.retry_delay * attempt)
                    continue
                raise EngineUnavailable(
                    self.engine_name + " at " + self.base_url + " did not respond.",
                    detail=last_error,
                ) from exc

            status = response.status_code
            if 200 <= status < 300:
                return response

            body = body_text(response)
            if status in AUTH_STATUS and recover_auth is not None and not recovered:
                recovered = True
                if recover_auth():
                    continue
                raise EngineRejected(
                    self.engine_name + " rejected the CASS credentials.",
                    detail=detail(status, url, body),
                )
            if status in RETRYABLE_STATUS and attempt < self.retries:
                last_error = detail(status, url, body)
                self.sleep(self.retry_delay * attempt)
                continue
            if status in RETRYABLE_STATUS:
                raise EngineUnavailable(
                    self.engine_name + " is not currently serving requests.",
                    detail=detail(status, url, body),
                )
            summary = (
                refusal_summary(method, status)
                if refusal_summary is not None
                else self.engine_name
                + " refused the "
                + method
                + " request with HTTP "
                + str(status)
                + "."
            )
            raise EngineRejected(summary, detail=detail(status, url, body))

        raise EngineUnavailable(  # pragma: no cover - the loop returns or raises
            self.engine_name + " at " + self.base_url + " did not respond.",
            detail=last_error,
        )


def body_text(response: HttpResponse) -> str:
    """Read a response body as text without letting the read itself fail."""
    try:
        return response.text or ""
    except Exception:  # pragma: no cover - defensive
        return ""


def detail(status: int, url: str, body: str) -> str:
    return "HTTP " + str(status) + " from " + url + ": " + body[:4000]


def json_body(response: HttpResponse) -> Any:
    """Parse a JSON body, or say that the engine did not send one.

    An engine that answers 200 with an HTML error page is a real deployment
    failure -- usually a proxy in front of it -- and reporting it as such is
    more use than a ``JSONDecodeError`` reaching the run monitor.
    """
    try:
        return response.json()
    except Exception as exc:
        raise EngineRejected(
            "The engine returned a response CASS could not read as JSON.",
            detail=body_text(response)[:2000],
        ) from exc


def json_or_empty(response: HttpResponse) -> Mapping[str, Any]:
    body = json_body(response)
    return body if isinstance(body, Mapping) else {}


def truncated_json(body: Mapping[str, Any], limit: int = 2000) -> str:
    """A mapping as text for a failure detail, without risking a huge payload."""
    try:
        return json.dumps(dict(body))[:limit]
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return str(body)[:limit]
