"""Durable background execution.

Section 4 requires Celery-compatible durable background execution, and section
11 requires cancellation and clean restart without leaving published partial
results. Late acknowledgement and a prefetch of one mean a killed worker
returns its task to the queue rather than losing it.
"""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cass.settings.dev")

app = Celery("cass")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True, name="cass.ping")
def ping(self) -> str:
    """Liveness probe used by the administration screen and by CI."""
    return "pong"
