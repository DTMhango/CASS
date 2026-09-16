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

import json
import re
from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[3] / "deploy" / "docker-compose.yml"
IMAGES = Path(__file__).resolve().parents[3] / "deploy" / "images"
WORKFLOWS = Path(__file__).resolve().parents[4] / ".github" / "workflows"
GEM_MANIFEST = (
    Path(__file__).resolve().parents[4] / "models" / "gem" / "v2026.0.0" / "MODEL_MANIFEST.md"
)

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


def test_the_api_migrates_before_it_serves_and_nothing_else_migrates():
    """A rebuilt stack must not run new code against the old schema.

    The API's image migrates and then starts gunicorn, and only if the migration
    succeeded. The worker and the scheduler run that image too, so each has to
    replace its command -- or it would migrate as well, racing the API over one
    schema -- and wait for the API to be healthy, which it cannot be until the
    migration has finished and gunicorn is serving.
    """
    dockerfile = (IMAGES / "Dockerfile.api").read_text(encoding="utf-8").replace("\\\n", "")
    command = json.loads(re.search(r"^CMD (\[.*\])\s*$", dockerfile, re.MULTILINE).group(1))
    declared = services()

    assert re.search(r"manage\.py migrate\b.*&&.*\bgunicorn\b", command[-1]), command
    assert "command" not in declared["api"], "the API's command would skip the migration"
    for name in CONTROL_PLANE:
        if name == "api":
            continue
        service = declared[name]
        assert service.get("command"), f"{name} would run the API's command and migrate"
        assert "migrate" not in " ".join(service["command"]), name
        assert service["depends_on"]["api"]["condition"] == "service_healthy", name


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


def test_ci_builds_every_image_the_repository_defines():
    """An image CI never builds is one whose Dockerfile can rot unnoticed.

    The patched Oasis worker is the case that matters: its patch asserts the
    source it replaces, so building it is how a raised upstream version that
    has moved past the defect gets caught.
    """
    workflow = yaml.safe_load((WORKFLOWS / "platform.yml").read_text(encoding="utf-8"))
    built = set(workflow["jobs"]["images"]["strategy"]["matrix"]["image"])
    defined = {path.name.removeprefix("Dockerfile.") for path in IMAGES.glob("Dockerfile.*")}

    assert defined <= built, f"CI does not build: {sorted(defined - built)}"


def test_the_integration_workflow_reads_the_gem_release_the_manifest_pins():
    """One place says which GEM commits CASS was validated against, and CI reads it."""
    workflow = (WORKFLOWS / "platform-integration.yml").read_text(encoding="utf-8")
    manifest = GEM_MANIFEST.read_text(encoding="utf-8")

    assert "MODEL_MANIFEST.md" in workflow
    assert "CASS_GEM_PATH" in workflow
    assert "-m integration" in workflow
    for repository in ("global_exposure_model", "global_vulnerability_model"):
        section = manifest.split(f"github.com/gem/{repository}.git", 1)[1][:200]
        assert re.search(r"\b[0-9a-f]{40}\b", section), f"no commit pinned for {repository}"


def test_no_workflow_names_the_confidential_portfolio():
    """The workbook is KRE's own book. CI is not somewhere it may be read."""
    for path in sorted(WORKFLOWS.glob("*.yml")):
        assert "CASS_EXTRACT_PATH" not in path.read_text(encoding="utf-8"), path.name
