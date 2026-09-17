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

from apps.modelregistry.assets import ModelAssetError
from cass_adapters.base import AdapterError

from . import conversion as conversion_service
from . import hazard as hazard_service
from . import services
from .models import AnalysisRun, ConversionRun, HazardRun

logger = logging.getLogger(__name__)


@shared_task(name="cass.runs.execute_analysis")
def execute_analysis(analysis_run_id: str) -> dict:
    """Run one analysis through Oasis, from published OED to collected output."""
    analysis_run = AnalysisRun.objects.select_related("run").get(id=analysis_run_id)
    try:
        services.execute(analysis_run, actor=analysis_run.created_by)
    except services.RunBlocked as exc:
        # Not a failure. The run is waiting for someone to decide something,
        # and the approval that releases it is a separate piece of work.
        logger.info("analysis run %s is held at a gate: %s", analysis_run_id, exc)
        analysis_run.run.refresh_from_db()
        return {
            "analysis_run": str(analysis_run_id),
            "state": analysis_run.run.state,
            "stage": analysis_run.run.stage,
            "summary": analysis_run.run.gate_summary,
        }
    except (AdapterError, services.AnalysisExecutionError, ModelAssetError) as exc:
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


@shared_task(name="cass.runs.execute_hazard")
def execute_hazard(hazard_run_id: str) -> dict:
    """Run one OpenQuake hazard calculation, from job files to a datastore.

    A national calculation runs for hours, which is the whole reason this is a
    task: section 3 requires that work continue whether or not a browser stays
    open, and a modeller follows it on the run monitor rather than holding a
    request.
    """
    hazard_run = HazardRun.objects.select_related("run").get(id=hazard_run_id)
    try:
        hazard_service.execute(hazard_run, actor=hazard_run.created_by)
    except (AdapterError, hazard_service.HazardExecutionError) as exc:
        # Already recorded against the run by the service. The monitor is where
        # a person reads this, not the broker.
        logger.warning("hazard run %s failed: %s", hazard_run_id, exc)
        hazard_run.run.refresh_from_db()
        return {
            "hazard_run": str(hazard_run_id),
            "state": hazard_run.run.state,
            "stage": hazard_run.run.failure_stage,
            "summary": hazard_run.run.failure_summary,
        }

    hazard_run.run.refresh_from_db()
    return {
        "hazard_run": str(hazard_run_id),
        "state": hazard_run.run.state,
        "calculation": hazard_run.openquake_calculation_id,
    }


@shared_task(name="cass.runs.rebuild_hazard")
def rebuild_hazard(hazard_run_id: str) -> dict:
    """Rebuild a hazard set's footprint from its stored calculation.

    A national footprint takes minutes to bin, so this runs in the background
    and is followed on the run monitor, as a hazard calculation is.
    """
    hazard_run = HazardRun.objects.select_related("run").get(id=hazard_run_id)
    try:
        hazard_service.rebuild(hazard_run, actor=hazard_run.created_by)
    except (AdapterError, hazard_service.HazardExecutionError) as exc:
        logger.warning("hazard rebuild %s failed: %s", hazard_run_id, exc)
        hazard_run.run.refresh_from_db()
        return {
            "hazard_run": str(hazard_run_id),
            "state": hazard_run.run.state,
            "stage": hazard_run.run.failure_stage,
            "summary": hazard_run.run.failure_summary,
        }
    hazard_run.run.refresh_from_db()
    return {"hazard_run": str(hazard_run_id), "state": hazard_run.run.state}


@shared_task(name="cass.runs.cancel_hazard")
def cancel_hazard(hazard_run_id: str, actor_id: str | None = None) -> dict:
    """Stop a calculation on OpenQuake as well as in CASS."""
    from apps.accounts.models import User

    hazard_run = HazardRun.objects.select_related("run").get(id=hazard_run_id)
    actor = User.objects.filter(id=actor_id).first() if actor_id else None
    try:
        hazard_service.cancel(hazard_run, actor=actor)
    except AdapterError as exc:
        logger.warning("cancellation of %s did not reach OpenQuake: %s", hazard_run_id, exc)
        hazard_run.run.refresh_from_db()
        return {
            "hazard_run": str(hazard_run_id),
            "state": hazard_run.run.state,
            "summary": hazard_run.run.failure_summary,
        }

    hazard_run.run.refresh_from_db()
    return {"hazard_run": str(hazard_run_id), "state": hazard_run.run.state}


@shared_task(name="cass.runs.execute_conversion")
def execute_conversion(conversion_run_id: str) -> dict:
    """Build and deploy an Oasis model package for a model version."""
    conversion_run = ConversionRun.objects.select_related(
        "run", "model_version", "model_version__hazard_set"
    ).get(id=conversion_run_id)
    try:
        conversion_service.execute(conversion_run, actor=conversion_run.created_by)
    except Exception as exc:  # recorded against the run by the service
        logger.warning("conversion run %s failed: %s", conversion_run_id, exc)
        conversion_run.run.refresh_from_db()
        return {
            "conversion_run": str(conversion_run_id),
            "state": conversion_run.run.state,
            "stage": conversion_run.run.failure_stage,
            "summary": conversion_run.run.failure_summary,
        }
    conversion_run.run.refresh_from_db()
    return {"conversion_run": str(conversion_run_id), "state": conversion_run.run.state}


@shared_task(name="cass.runs.abandon_stale")
def abandon_stale(older_than_minutes: int = 60) -> dict:
    """Close runs whose worker did not survive.

    Calls the management command rather than reimplementing it: what counts as
    stale, and what an abandoned run is left looking like, are decided in one
    place. A scheduled copy of that logic would drift from the one an operator
    runs by hand.
    """
    from django.core.management import call_command

    call_command("abandon_stale_runs", older_than=older_than_minutes)
    return {"older_than_minutes": older_than_minutes}
