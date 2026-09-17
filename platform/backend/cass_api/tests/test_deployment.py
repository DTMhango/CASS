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


# -- resource limits ----------------------------------------------------------
#
# CASS may use about 24 GB of a shared server. Nothing limited it: the one
# memory limit in the file sat on the converter, a container that imports its
# package and exits, and every engine started a process per CPU. These hold that
# the limits exist, fit, cannot be bypassed through swap or a core count, and
# can all be lifted at once for a machine CASS has to itself.

#: What the long-running containers' default limits may add up to. About 24 GB
#: is CASS's share of the company server; 22 GiB is 23.6 GB, leaving the rest.
SHARED_SERVER_BUDGET = 22 * 2**30

#: Containers that run for a moment and exit, and so share nothing over time.
SHORT_LIVED = ("minio-init", "keys", "converter")

_LIMIT = re.compile(r"^\$\{(CASS_[A-Z_]+):-\$\{CASS_RESOURCE_LIMITS:-([^}]+)\}\}$")
_SHORT_LIVED_LIMIT = re.compile(r"^\$\{CASS_RESOURCE_LIMITS:-([^}]+)\}$")


def _bytes(size: str) -> int:
    """A Docker size -- 768M, 3.5G -- in bytes. Docker's M and G are MiB and GiB."""
    number, unit = re.fullmatch(r"(\d+(?:\.\d+)?)([KMG])", size).groups()
    return int(float(number) * 1024 ** ("KMG".index(unit) + 1))


def _template(name: str) -> dict[str, str]:
    """Every setting a template names, set or left commented at its default."""
    settings: dict[str, str] = {}
    for line in (COMPOSE.parent / name).read_text(encoding="utf-8").splitlines():
        found = re.fullmatch(r"#?\s?([A-Z][A-Z0-9_]*)=(\S*)", line)
        if found:
            settings[found.group(1)] = found.group(2)
    return settings


def test_every_container_has_a_memory_limit_it_cannot_swap_past():
    """Docker otherwise lets a container use as much swap again as its limit.

    On a shared host that turns a limit into disk thrashing for every other
    application, so swap is held to the same figure from the same setting.
    """
    for name, service in services().items():
        limit = service.get("mem_limit")
        assert limit, f"{name} has no memory limit"
        assert service.get("memswap_limit") == limit, f"{name} may swap past its limit"
        pattern = _SHORT_LIVED_LIMIT if name in SHORT_LIVED else _LIMIT
        assert pattern.match(limit), f"{name}: {limit} is not a setting CASS_RESOURCE_LIMITS lifts"
        assert "deploy" not in service or "resources" not in service["deploy"], (
            f"{name} sets its limit twice"
        )

    text = COMPOSE.read_text(encoding="utf-8")
    assert "${CASS_CONVERTER_MEMORY" not in text, (
        "a limit on a container that does no work reads as a limit on conversions"
    )


def test_the_default_limits_fit_the_shared_server():
    """A sum over the budget is a server that can be run out of memory by CASS."""
    total = 0
    for name, service in services().items():
        if name in SHORT_LIVED:
            continue
        variable, default = _LIMIT.match(service["mem_limit"]).groups()
        assert variable == f"CASS_{name.replace('-', '_').upper()}_MEMORY", name
        total += _bytes(default)

    assert total <= SHARED_SERVER_BUDGET, (
        f"the defaults add up to {total / 2**30:.2f} GiB, over "
        f"{SHARED_SERVER_BUDGET / 2**30:.0f} GiB"
    )


def test_no_engine_starts_a_process_per_core_by_default():
    """Each engine process needs its own memory, so a count set by the core count
    is a limit the engine outgrows on a bigger server: OpenQuake asks for 2 GB a
    core, and started all twenty on a workstation.
    """
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    declared = compose["services"]

    openquake = declared["openquake"]
    assert openquake["environment"]["OQ_CONFIG_FILE"] == openquake["configs"][0]["target"]
    cores = re.search(
        r"num_cores = \$\{CASS_OPENQUAKE_CORES:-\$\{CASS_RESOURCE_LIMITS:-(\d+)\}\}",
        compose["configs"]["openquake"]["content"],
    )
    assert cores and int(cores.group(1)) > 0

    kernel = _LIMIT.match(declared["oasis-worker"]["environment"]["OASIS_KERNEL_NUM_PROCESSES"])
    assert kernel and kernel.group(1) == "CASS_OASIS_KERNEL_PROCESSES"
    assert int(kernel.group(2)) > 0

    api = declared["oasis-api"]
    workers = _LIMIT.match(api["environment"]["CASS_OASIS_API_WORKERS"])
    assert workers and int(workers.group(2)) > 0
    assert '--workers="$$workers"' in api["command"][-1], (
        "the image's own script would start a worker per CPU"
    )


def test_one_setting_lifts_every_limit_and_process_count():
    """0 has to mean what it meant before the limits: nothing held back."""
    text = COMPOSE.read_text(encoding="utf-8")
    for setting in (
        "num_cores",
        "OASIS_KERNEL_NUM_PROCESSES",
        "CASS_OASIS_API_WORKERS",
        "mem_limit",
        "memswap_limit",
    ):
        lines = [line for line in text.splitlines() if re.match(rf"\s*{setting}\s*[:=]", line)]
        assert lines, setting
        for line in lines:
            assert "${CASS_RESOURCE_LIMITS:-" in line, line.strip()


def test_nothing_is_found_relative_to_where_compose_happens_to_run():
    """Dokploy resolves compose's paths from the repository root.

    A build context of ".." from there is the folder above the repository, and a
    ./postgres-init is a folder that does not exist, which Docker creates empty.
    """
    for name, service in services().items():
        build = service.get("build")
        if build:
            assert build["context"] == "${CASS_PLATFORM_DIR:-..}", name
        for volume in service.get("volumes", []):
            # A source that starts with a setting is one a deployment can point
            # somewhere absolute; a literal relative path is not.
            source = volume if isinstance(volume, str) else volume.get("source", "")
            if not source.startswith("$"):
                assert not source.startswith("."), f"{name} mounts {source} relative to the run"


def test_the_dokploy_template_has_every_setting_and_what_dokploy_needs():
    """A setting added to one template and not the other is one a deployment lacks."""
    local = _template(".env.example")
    dokploy = _template("dokploy.env.example")

    assert set(local) <= set(dokploy), (
        f"missing from dokploy.env.example: {sorted(set(local) - set(dokploy))}"
    )
    assert dokploy["COMPOSE_PROFILES"] == "engines", "Dokploy passes no --profile"
    # With the "./": without it compose reads the init scripts' path as a volume name.
    assert dokploy["CASS_PLATFORM_DIR"] == "./platform"
    assert dokploy["CASS_MODELS_PATH"].startswith("/"), (
        "Dokploy re-clones the repository on every deploy"
    )


def test_the_templates_serve_the_packages_cass_builds():
    """A model root on disk replaces the oasis-model volume CASS deploys packages to.

    The template read ./model_data after the volume replaced it, so an
    installation set up from it served none of the packages it built.
    """
    for name in (".env.example", "dokploy.env.example"):
        assert _template(name)["CASS_MODEL_DATA_PATH"] == "", name
