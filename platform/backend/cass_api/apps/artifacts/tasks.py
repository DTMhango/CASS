"""Background work for the artifact store."""

from __future__ import annotations

import logging

from celery import shared_task

from . import retention

logger = logging.getLogger(__name__)


@shared_task(name="cass.artifacts.expire_due")
def expire_due(limit: int | None = None, dry_run: bool = False) -> dict:
    """Expire artifacts whose retention class has run out.

    Run on a schedule. It takes no actor: nobody presses this, the retention
    class does, and the audit trail records the class that decided rather than
    a person who did not.
    """
    report = retention.sweep(limit=limit, dry_run=dry_run)
    logger.info(
        "retention sweep expired %s artifact(s), kept %s, released %s bytes",
        report["expired_count"],
        report["kept_count"],
        report["bytes_released"],
    )
    return report
