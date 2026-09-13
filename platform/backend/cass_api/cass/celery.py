"""Durable background execution.

Section 4 requires Celery-compatible durable background execution, and section
11 requires cancellation and clean restart without leaving published partial
results. Late acknowledgement and a prefetch of one mean a killed worker
returns its task to the queue rather than losing it.

Section 4 also requires a correlation ID to follow every run across all
services. The request middleware keeps one in a context variable, and a context
variable does not cross a message broker: a task picked up in a worker used to
log with an empty correlation ID, so the one place a long run actually spends
its time was the one place its logs could not be joined to the request that
started it. The signals below carry the ID in the task's headers and restore it
in the worker for the life of the task.
"""

from __future__ import annotations

import os

from celery import Celery
from celery.signals import before_task_publish, setup_logging, task_postrun, task_prerun

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cass.settings.dev")

app = Celery("cass")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

#: The header a task carries its correlation ID in. Not ``correlation_id``:
#: Celery's own message protocol already has a property of that name, set to
#: the task id, and it shadows a header called the same thing -- which is how
#: the first live check found worker lines stamped with the task id instead of
#: the request's.
CORRELATION_HEADER = "cass_correlation_id"


@setup_logging.connect
def use_the_platforms_logging(**kwargs) -> None:
    """Log from the worker the way the API logs: structured, with the correlation ID.

    Left alone, Celery replaces the root logger's handlers with its own plain
    formatter. The correlation ID then travelled into the worker and was
    restored there faithfully, and not one worker log line carried it, because
    the formatter that writes it had been swapped out. Connecting this signal
    is how Celery is told the application configures logging itself.
    """
    from logging.config import dictConfig

    from django.conf import settings

    dictConfig(settings.LOGGING)

#: Context tokens by task id, so each task restores exactly what it replaced.
_tokens: dict[str, object] = {}


@before_task_publish.connect
def carry_correlation_id(headers=None, **kwargs) -> None:
    """Put the publishing request's correlation ID on the task.

    A task published with no request behind it -- a scheduled sweep -- gets one
    of its own, so its log lines can still be grouped with each other.
    """
    if headers is None or headers.get(CORRELATION_HEADER):
        return
    from apps.audit.middleware import current_correlation_id, new_correlation_id

    headers[CORRELATION_HEADER] = current_correlation_id() or new_correlation_id()


@task_prerun.connect
def restore_correlation_id(task_id=None, task=None, **kwargs) -> None:
    """Make the carried ID current in the worker for the life of the task."""
    from apps.audit.middleware import _correlation_id, new_correlation_id

    request = getattr(task, "request", None)
    carried = getattr(request, CORRELATION_HEADER, None) if request is not None else None
    if not carried and request is not None:
        carried = (getattr(request, "headers", None) or {}).get(CORRELATION_HEADER)
    if not carried:
        # Run eagerly inside a request, the ID is already current; anything
        # else gets a fresh one rather than logging with none.
        from apps.audit.middleware import current_correlation_id

        carried = current_correlation_id() or new_correlation_id()
    if task_id:
        _tokens[task_id] = _correlation_id.set(str(carried)[:64])


@task_postrun.connect
def release_correlation_id(task_id=None, **kwargs) -> None:
    from apps.audit.middleware import _correlation_id

    token = _tokens.pop(task_id, None) if task_id else None
    if token is not None:
        _correlation_id.reset(token)


@app.task(bind=True, name="cass.ping")
def ping(self) -> str:
    """Liveness probe used by the administration screen and by CI."""
    return "pong"
