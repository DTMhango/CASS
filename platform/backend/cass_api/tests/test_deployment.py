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
IMAGES = Path(__file__).resolve().parents[3] / "deploy" / "images"

#: Services that run the control plane's own code, and so must run the same build.
CONTROL_PLANE = ("api", "worker", "beat")

#: Images this repository builds. Their digest changes with every build, so a run
#: records the digest it ran on rather than the compose file pinning one; what
#: they are built from is pinned instead.
LOCAL_BUILDS = ("cass/",)


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


def test_every_upstream_image_is_pinned_by_digest():
    """A tag can be pushed again; a digest cannot.

    ``openquake/engine:3.23`` is a minor-version tag, so without its digest the
    same compose file could start a different engine next month, and a run
    repeated to check a result would be repeated on something else.
    """
    unpinned = {
        name: service["image"]
        for name, service in services().items()
        if service.get("image")
        and not service["image"].startswith(LOCAL_BUILDS)
        and "@sha256:" not in service["image"]
    }

    assert not unpinned, f"pin these by digest: {unpinned}"


def test_every_image_a_build_starts_from_is_pinned_by_digest():
    """The same for what this repository builds on, including a base named by an ARG."""
    unpinned = []
    for dockerfile in sorted(IMAGES.glob("Dockerfile.*")):
        arguments: dict[str, str] = {}
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("ARG ") and "=" in stripped:
                name, _, default = stripped[4:].partition("=")
                arguments[name.strip()] = default.strip()
            if not stripped.startswith("FROM "):
                continue
            reference = stripped.split()[1]
            if reference.startswith("${") and reference.endswith("}"):
                reference = arguments.get(reference[2:-1], reference)
            if "@sha256:" not in reference:
                unpinned.append(f"{dockerfile.name}: {reference}")

    assert not unpinned, f"pin these by digest: {unpinned}"
