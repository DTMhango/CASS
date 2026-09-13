"""Closing runs whose worker died, so the monitor never shows phantom work."""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.runs.models import Run, RunKind
from cass_core.runs import RunState

pytestmark = pytest.mark.django_db


def running_run(modeller, *, minutes_ago: int) -> Run:
    run = Run.objects.create(
        kind=RunKind.ANALYSIS, label="Interrupted", created_by=modeller
    )
    run.transition(RunState.QUEUED, actor=modeller)
    run.transition(RunState.RUNNING, actor=modeller, stage="losses")
    Run.objects.filter(pk=run.pk).update(
        updated_at=timezone.now() - dt.timedelta(minutes=minutes_ago)
    )
    run.refresh_from_db()
    return run


def test_a_run_left_behind_by_a_dead_worker_is_closed(modeller):
    run = running_run(modeller, minutes_ago=180)

    call_command("abandon_stale_runs", "--older-than", "60")

    run.refresh_from_db()
    assert run.state == RunState.FAILED
    assert run.failure_stage == "losses"
    assert "did not survive" in run.failure_summary


def test_a_run_that_is_still_working_is_left_alone(modeller):
    run = running_run(modeller, minutes_ago=5)

    call_command("abandon_stale_runs", "--older-than", "60")

    run.refresh_from_db()
    assert run.state == RunState.RUNNING


def test_a_dry_run_changes_nothing(modeller):
    run = running_run(modeller, minutes_ago=180)

    call_command("abandon_stale_runs", "--older-than", "60", "--dry-run")

    run.refresh_from_db()
    assert run.state == RunState.RUNNING


def test_the_progress_it_reached_is_not_rewritten_by_the_failure(modeller):
    """A run that died in the losses must not read as though they finished."""
    run = running_run(modeller, minutes_ago=180)
    before = run.progress

    call_command("abandon_stale_runs", "--older-than", "60")

    run.refresh_from_db()
    assert run.progress == before
