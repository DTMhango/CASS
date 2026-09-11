"""Correlation IDs.

Section 4 requires a correlation ID to follow every run across all services.
The middleware accepts one from an upstream caller or mints one, stores it for
the life of the request, and echoes it on the response so a browser network log
and a worker log can be lined up.
"""

from __future__ import annotations

import contextvars
import uuid

HEADER = "HTTP_X_CORRELATION_ID"
RESPONSE_HEADER = "X-Correlation-ID"

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "cass_correlation_id", default=""
)


def current_correlation_id() -> str:
    return _correlation_id.get()


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


class CorrelationIDMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.META.get(HEADER, "").strip()
        correlation_id = incoming[:64] or new_correlation_id()
        token = _correlation_id.set(correlation_id)
        request.correlation_id = correlation_id
        try:
            response = self.get_response(request)
        finally:
            _correlation_id.reset(token)
        response[RESPONSE_HEADER] = correlation_id
        return response
