"""Correlation through background work, and metrics from the records.

Section 4 requires a correlation ID to follow every run across all services.
The request middleware kept one in a context variable, and a context variable
does not cross a message broker, so a worker picked a task up and logged with
an empty ID -- the place a long run spends its time was the place its logs
could not be joined to the request that started it. These hold the carry: the
ID goes onto the task at publish, comes back in the worker, and is stamped on
the run where every kind of run passes.

The metrics are read from the records at scrape time, and the tests are mostly
about that choice: a count kept in process memory would reset on a worker
restart and disagree with the database it duplicates.
"""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from apps.audit import metrics
from apps.audit.middleware import _correlation_id, current_correlation_id
from cass import celery as cass_celery

pytestmark = pytest.mark.django_db


class _Request:
    def __init__(self, **values):
        self.__dict__.update(values)


class _Task:
    def __init__(self, **request_values):
        self.request = _Request(**request_values)


# -- correlation across the broker -------------------------------------------

def test_publishing_a_task_carries_the_requests_correlation_id():
    token = _correlation_id.set("req-123")
    try:
        headers: dict = {}
        cass_celery.carry_correlation_id(headers=headers)
    finally:
        _correlation_id.reset(token)

    assert headers[cass_celery.CORRELATION_HEADER] == "req-123"


def test_a_task_published_with_no_request_behind_it_still_gets_an_id():
    """A scheduled sweep's log lines should group with each other."""
    headers: dict = {}
    cass_celery.carry_correlation_id(headers=headers)

    assert len(headers[cass_celery.CORRELATION_HEADER]) == 32


def test_the_worker_restores_the_carried_id_for_the_life_of_the_task():
    assert current_correlation_id() == ""

    cass_celery.restore_correlation_id(
        task_id="t-1", task=_Task(**{cass_celery.CORRELATION_HEADER: "req-456"})
    )
    try:
        assert current_correlation_id() == "req-456"
    finally:
        cass_celery.release_correlation_id(task_id="t-1")

    assert current_correlation_id() == ""


def test_the_header_is_not_a_name_celery_already_uses():
    """Celery sets its own correlation_id property to the task id.

    A header with that name is shadowed by it, which is exactly what the live
    worker showed: every line carried the task id rather than the request's.
    The fake task below models that, so a regression cannot pass by accident.
    """
    assert cass_celery.CORRELATION_HEADER != "correlation_id"

    task = _Task(correlation_id="the-task-id", **{cass_celery.CORRELATION_HEADER: "req-000"})
    cass_celery.restore_correlation_id(task_id="t-0", task=task)
    try:
        assert current_correlation_id() == "req-000"
    finally:
        cass_celery.release_correlation_id(task_id="t-0")


def test_a_run_is_stamped_with_the_current_id_when_it_is_queued(project, analyst):
    from apps.runs.models import Run, RunKind
    from cass_core.runs import RunState

    run = Run.objects.create(kind=RunKind.ANALYSIS, project=project, created_by=analyst)
    token = _correlation_id.set("req-789")
    try:
        run.transition(RunState.QUEUED, actor=analyst)
    finally:
        _correlation_id.reset(token)

    run.refresh_from_db()
    assert run.correlation_id == "req-789"


def test_a_run_keeps_the_id_it_already_carries(project, analyst):
    from apps.runs.models import Run, RunKind
    from cass_core.runs import RunState

    run = Run.objects.create(
        kind=RunKind.ANALYSIS, project=project, correlation_id="original", created_by=analyst
    )
    token = _correlation_id.set("later")
    try:
        run.transition(RunState.QUEUED, actor=analyst)
    finally:
        _correlation_id.reset(token)

    assert run.correlation_id == "original"


# -- metrics ------------------------------------------------------------------

def test_the_metrics_are_counts_read_from_the_records(project, analyst):
    from apps.runs.models import Run, RunKind

    Run.objects.create(kind=RunKind.ANALYSIS, project=project, state="running", created_by=analyst)
    Run.objects.create(kind=RunKind.ANALYSIS, project=project, state="failed", created_by=analyst)

    document = metrics.collect()

    assert 'cass_runs{kind="analysis",state="running"} 1' in document
    assert 'cass_runs{kind="analysis",state="failed"} 1' in document
    assert "# TYPE cass_profile_capacity gauge" in document


def test_a_failure_in_the_last_day_is_counted_against_the_stage_it_failed_at(
    project, analyst
):
    from django.utils import timezone

    from apps.runs.models import Run, RunKind

    Run.objects.create(
        kind=RunKind.ANALYSIS,
        project=project,
        state="failed",
        failure_stage="losses",
        finished_at=timezone.now(),
        created_by=analyst,
    )

    document = metrics.collect()

    assert 'cass_runs_failed_last_24h{kind="analysis",stage="losses"} 1' in document


def test_a_label_value_cannot_break_the_exposition_format():
    assert metrics._sample("x", 1, {"stage": 'bad "quote"\nline'}) == (
        'x{stage="bad \\"quote\\"\\nline"} 1'
    )


def test_a_scraper_without_the_token_is_refused(settings):
    settings.CASS_METRICS_TOKEN = "scrape-secret"
    request = RequestFactory().get("/metrics/")

    refused = metrics.MetricsView.as_view()(request)

    assert refused.status_code == 403


def test_a_scraper_with_the_token_reads_the_metrics(settings):
    settings.CASS_METRICS_TOKEN = "scrape-secret"
    request = RequestFactory().get("/metrics/", HTTP_AUTHORIZATION="Bearer scrape-secret")

    allowed = metrics.MetricsView.as_view()(request)

    assert allowed.status_code == 200
    assert allowed["Content-Type"].startswith("text/plain; version=0.0.4")
    assert b"cass_build_info" in allowed.content


def test_without_a_token_configured_only_an_administrator_may_read(settings, admin, analyst):
    from rest_framework.test import force_authenticate

    settings.CASS_METRICS_TOKEN = ""
    view = metrics.MetricsView.as_view()

    as_analyst = RequestFactory().get("/metrics/")
    force_authenticate(as_analyst, user=analyst)
    as_admin = RequestFactory().get("/metrics/")
    force_authenticate(as_admin, user=admin)

    assert view(as_analyst).status_code == 403
    assert view(as_admin).status_code == 200


def test_the_metrics_endpoint_is_routed_where_a_scraper_looks(client, settings):
    """Beside the health probe, outside the versioned API a browser uses."""
    settings.CASS_METRICS_TOKEN = "scrape-secret"

    scraped = client.get("/metrics/", HTTP_AUTHORIZATION="Bearer scrape-secret")
    anonymous = client.get("/metrics/")

    assert scraped.status_code == 200
    assert b"# TYPE cass_runs gauge" in scraped.content
    assert anonymous.status_code == 403


def test_the_worker_logs_through_the_platforms_structured_formatter():
    """A correlation ID restored in the worker is only useful if a line writes it.

    Celery's default replaces the root logger's handlers with a plain format,
    which is exactly what the live worker was doing: the ID arrived and no log
    line carried it.
    """
    import json
    import logging

    from apps.audit.logging import StructuredFormatter

    cass_celery.use_the_platforms_logging()

    formatters = {type(handler.formatter) for handler in logging.getLogger().handlers}
    assert StructuredFormatter in formatters

    token = _correlation_id.set("worker-line")
    try:
        record = logging.LogRecord("apps.artifacts.tasks", logging.INFO, __file__, 1, "swept", (), None)
        line = json.loads(StructuredFormatter().format(record))
    finally:
        _correlation_id.reset(token)
    assert line["correlation_id"] == "worker-line"
