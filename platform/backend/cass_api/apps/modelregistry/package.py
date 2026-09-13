"""Building and deploying the Oasis model package for a model version.

``cass_converter.oasis_package`` knows how a package is laid out; this module
knows where its ingredients are kept. It reads the grid, the vulnerability set
and the attached hazard set from the artifact store, hands them to the
converter, and puts the result where the Oasis worker reads its model from.

Deployment is a swap rather than an overwrite. The package is written into a
staging directory on the same volume and then each top-level entry is renamed
into place, so a worker that reads the model while a new one is being built
reads the previous package whole, not half of each.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
from typing import Any

from django.conf import settings

from apps.common.engines import oasis_model_triple
from cass_converter import oasis_package, pilot_bins

from .assets import (
    DAMAGE_BINS_ROLE,
    GRID_CELLS_ROLE,
    HAZARD_ROLE_PREFIX,
    VULNERABILITY_FUNCTIONS_ROLE,
    VULNERABILITY_MAPPING_ROLE,
    asset_bytes,
)
from .models import ModelVersion

#: The packages the engine-side lookup imports, and the files of each it needs.
#: ``None`` is every module. ``cass_core`` is limited to the policy vocabulary,
#: because the rest of it is the artifact store and has no business inside an
#: engine's model directory.
VENDORED_PACKAGES: dict[str, tuple[str, ...] | None] = {
    "cass_keys": None,
    "cass_oed": None,
    "cass_core": ("__init__.py", "policy.py"),
}


class PackageBuildError(Exception):
    """Raised when a model version cannot be packaged for Oasis."""


def model_root() -> pathlib.Path:
    """Where the Oasis worker mounts its model from."""
    return pathlib.Path(settings.CASS_OASIS_MODEL_ROOT)


def vendored_sources() -> dict[str, bytes]:
    """The source of the packages the lookup imports, as installed here."""
    found: dict[str, bytes] = {}
    for name, only in VENDORED_PACKAGES.items():
        spec = importlib.util.find_spec(name)
        if spec is None or not spec.submodule_search_locations:
            raise PackageBuildError(f"{name} is not installed, so it cannot be vendored.")
        directory = pathlib.Path(next(iter(spec.submodule_search_locations)))
        for path in sorted(directory.glob("*.py")):
            if only is not None and path.name not in only:
                continue
            found[f"{name}/{path.name}"] = path.read_bytes()
    return found


def measure_stem(imt: str) -> str:
    """The file stem a hazard set stores a measure's tables under."""
    return imt.replace("(", "").replace(")", "").replace(".", "p")


def gather(model_version: ModelVersion) -> oasis_package.PackageInputs:
    """Everything the package is built from, read from the registry."""
    hazard_set = model_version.hazard_set
    if hazard_set is None:
        raise PackageBuildError(
            f"{model_version} has no hazard set attached, so a package would have no "
            "ground motion to read. Attach one from the model build screen."
        )
    grid = model_version.grid
    vulnerability = model_version.vulnerability_set

    footprints = {
        imt: asset_bytes(
            hazard_set,
            "hazard_set",
            f"{HAZARD_ROLE_PREFIX}footprint_{measure_stem(imt)}",
            f"{hazard_set.reference} {imt} footprint",
        )
        for imt in hazard_set.imts
    }
    period_count = hazard_set.investigation_time * hazard_set.stochastic_event_sets
    if period_count <= 0 or period_count != int(period_count):
        raise PackageBuildError(
            f"{hazard_set.reference} records {hazard_set.investigation_time} years over "
            f"{hazard_set.stochastic_event_sets} event sets, which is not a whole "
            "number of periods. Annual frequency could not be preserved."
        )

    supplier, model, version = oasis_model_triple()
    return oasis_package.PackageInputs(
        identity=oasis_package.ModelIdentity(supplier, model, version),
        country_code=grid.country_code,
        grid_version=grid.version,
        grid_tolerance_km=str(grid.mapping_tolerance_km or 0),
        imt_representation=vulnerability.imt_representation or "undecided",
        grid_cells_csv=asset_bytes(grid, "area_peril_grid", GRID_CELLS_ROLE, str(grid)),
        mapping_csv=asset_bytes(
            vulnerability, "vulnerability_set", VULNERABILITY_MAPPING_ROLE, str(vulnerability)
        ),
        vulnerability_csv=asset_bytes(
            vulnerability, "vulnerability_set", VULNERABILITY_FUNCTIONS_ROLE, str(vulnerability)
        ),
        damage_bins_csv=asset_bytes(
            vulnerability, "vulnerability_set", DAMAGE_BINS_ROLE, str(vulnerability)
        ),
        footprints=footprints,
        occurrence_csv=asset_bytes(
            hazard_set,
            "hazard_set",
            f"{HAZARD_ROLE_PREFIX}occurrence",
            f"{hazard_set.reference} occurrence table",
        ),
        period_count=int(period_count),
        vendored=vendored_sources(),
        intensity_bin_count=pilot_bins.INTENSITY_BIN_COUNT,
        provenance={
            "model_version": model_version.reference,
            "grid": grid.reference,
            "vulnerability_set": str(vulnerability),
            "hazard_set": hazard_set.reference,
            "hazard_source_model": hazard_set.source_model,
            "hazard_licence_cleared": hazard_set.licence_cleared,
            "vulnerability_licence_cleared": vulnerability.licence_cleared,
            "intensity_bin_version": pilot_bins.PILOT_BIN_VERSION,
        },
    )


def deploy(
    inputs: oasis_package.PackageInputs, *, root: pathlib.Path, token: str
) -> dict[str, Any]:
    """Build into a staging directory, then swap it into the model root."""
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".staging-{token}"
    previous = root / f".previous-{token}"
    for leftover in (staging, previous):
        if leftover.exists():
            shutil.rmtree(leftover)

    try:
        manifest = oasis_package.build(inputs, staging)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    previous.mkdir()
    for entry in sorted(staging.iterdir()):
        target = root / entry.name
        if target.exists():
            os.replace(target, previous / entry.name)
        os.replace(entry, target)
    shutil.rmtree(previous, ignore_errors=True)
    staging.rmdir()
    return manifest
