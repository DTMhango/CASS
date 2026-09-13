"""What the deployment files have to keep true.

These guard defects that no unit test of the code can see, because the code was
right and the installation was not running it.

The one that prompted this: the API, the worker and the scheduler are built
from one Dockerfile, and compose gave each its own image tag. Building the API
alone rebuilt the API's tag and left the worker on an image hours old, so the
scheduler sent a retention sweep the worker had never heard of and it was
refused as an unregistered task. Nothing failed loudly; a nightly policy simply
did not run.
"""

from __future__ import annotations

from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[3] / "deploy" / "docker-compose.yml"

#: Services that run the control plane's own code, and so must run the same build.
CONTROL_PLANE = ("api", "worker", "beat")


def services() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def test_the_api_worker_and_scheduler_run_one_image():
    """One build updates all three, so a worker cannot be left on stale code."""
    declared = services()
    tags = {name: declared[name].get("image") for name in CONTROL_PLANE}

    assert all(tags.values()), f"each of {CONTROL_PLANE} must name its image: {tags}"
    assert len(set(tags.values())) == 1, f"they must name the same image: {tags}"


def test_the_scheduler_runs_exactly_once():
    """Two schedulers would issue every scheduled task twice."""
    beat = services()["beat"]

    assert beat.get("deploy", {}).get("replicas", 1) == 1
    assert "beat" in beat["command"]


def test_every_setting_the_uploads_and_metrics_read_reaches_the_containers():
    """A setting documented in the template but not passed through is inert."""
    environment = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["x-django-env"]

    for name in (
        "CASS_MAX_UPLOAD_BYTES",
        "CASS_UPLOAD_SESSION_SECONDS",
        "CASS_CLAMD_HOST",
        "CASS_CLAMD_PORT",
        "CASS_METRICS_TOKEN",
    ):
        assert name in environment, f"{name} is not passed to the control plane"
