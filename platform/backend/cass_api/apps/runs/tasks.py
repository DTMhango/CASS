"""Background execution of runs.

Section 4 requires durable background execution and section 11 requires
cancellation and clean restart without leaving published partial results. The
Celery application already configures late acknowledgement, rejection on worker
loss and a prefetch of one, so a killed worker returns its task to the queue
rather than losing it. What these tasks add is the rule about which failures
are the task's business.

A run that fails for a reason the domain understands -- an engine refusal, an
untested version, a lookup that lost locations -- is not a task failure. The
service has already recorded it on the run, where the monitor reads it, and
raising again would mark the task failed in the broker without adding anything
a person could act on. Those are caught and returned as a result.

A failure the domain does not understand is a defect, and is allowed to
propagate so it appears as a task failure rather than being quietly absorbed.
"""

from __future__ import annotations

import logging

from celery import shared_task

from cass_adapters.base import AdapterError

from . import services
from .models import AnalysisRun

logger = logging.getLogger(__name__)


@shared_task(name="cass.runs.execute_analysis")
def execute_analysis(analysis_run_id: str) -> dict:
    """Run one analysis through Oasis, from published OED to collected output."""
    analysis_run = AnalysisRun.objects.select_related("run").get(id=analysis_run_id)
    try:
        services.execute(analysis_run, actor=analysis_run.created_by)
    except (AdapterError, services.AnalysisExecutionError) as exc:
        # Already recorded against the run by the service; the monitor is the
        # place a person reads this, not the broker.
        logger.warning("analysis run %s failed: %s", analysis_run_id, exc)
        analysis_run.run.refresh_from_db()
        return {
            "analysis_run": str(analysis_run_id),
            "state": analysis_run.run.state,
            "stage": analysis_run.run.failure_stage,
            "summary": analysis_run.run.failure_summary,
        }

    analysis_run.run.refresh_from_db()
    return {"analysis_run": str(analysis_run_id), "state": analysis_run.run.state}


@shared_task(name="cass.runs.cancel_analysis")
def cancel_analysis(analysis_run_id: str, actor_id: str | None = None) -> dict:
    """Stop an analysis on the engine as well as in CASS.

    Cancellation is a task rather than part of the request because it has to
    reach the engine, and an Oasis server that is slow to answer must not hold
    the analyst's browser open.
    """
    from apps.accounts.models import User

    analysis_run = AnalysisRun.objects.select_related("run").get(id=analysis_run_id)
    actor = User.objects.filter(id=actor_id).first() if actor_id else None
    try:
        services.cancel(analysis_run, actor=actor)
    except AdapterError as exc:
        logger.warning("cancellation of %s did not reach Oasis: %s", analysis_run_id, exc)
        analysis_run.run.refresh_from_db()
        return {
            "analysis_run": str(analysis_run_id),
            "state": analysis_run.run.state,
            "summary": analysis_run.run.failure_summary,
        }

    analysis_run.run.refresh_from_db()
    return {"analysis_run": str(analysis_run_id), "state": analysis_run.run.state}
