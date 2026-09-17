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
    VULNERABILITY_VARIANT_ROLE_PREFIX,
    asset_bytes,
    asset_file,
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


def gather(
    model_version: ModelVersion, *, workspace: pathlib.Path | None = None
) -> oasis_package.PackageInputs:
    """Everything the package is built from, read from the registry.

    With a ``workspace``, each footprint is downloaded into it and handed over
    as a path, so the package is written from disk a row at a time. Without one
    the footprints are read into memory, which is for small sets and tests: a
    national footprint is gigabytes and the worker is not.
    """
    hazard_set = model_version.hazard_set
    if hazard_set is None:
        raise PackageBuildError(
            f"{model_version} has no hazard set attached, so a package would have no "
            "ground motion to read. Attach one from the model build screen."
        )
    grid = model_version.grid
    vulnerability = model_version.vulnerability_set

    # A footprint counted into bins the package no longer uses would be read
    # against the wrong intensities, every row of it, and nothing downstream
    # would notice. A set that records no fingerprint predates the check and is
    # packaged as it was.
    from . import hazard as hazard_registry  # noqa: PLC0415

    if hazard_registry.rebuild_status(hazard_set)["intensity_bins_current"] is False:
        raise PackageBuildError(
            f"{hazard_set.reference} was binned against intensity bins that have since "
            "changed, so its footprint would be read against the wrong intensities. "
            "Rebuild its footprint from the hazard set's page, then attach the rebuilt "
            "set."
        )
    current = hazard_registry.current_intensity_bins_checksum()
    if vulnerability.intensity_bins_checksum and vulnerability.intensity_bins_checksum != current:
        raise PackageBuildError(
            f"{vulnerability} was discretised against intensity bins that have since "
            "changed, so its damage would be read against the wrong intensities. Build "
            "the vulnerability set again from its GEM release, then build the package."
        )

    def footprint(imt: str):
        role = f"{HAZARD_ROLE_PREFIX}footprint_{measure_stem(imt)}"
        description = f"{hazard_set.reference} {imt} footprint"
        if workspace is None:
            return asset_bytes(hazard_set, "hazard_set", role, description)
        return asset_file(hazard_set, "hazard_set", role, description, workspace)

    footprints = {imt: footprint(imt) for imt in hazard_set.imts}
    # The hazard set's own effective time, not a second multiplication here: two
    # places computing the span of the same catalogue is two places to forget a
    # factor, and the factor this one forgot was the logic-tree paths (ADR 18).
    period_count = hazard_set.effective_time
    if period_count <= 0 or period_count != int(period_count):
        raise PackageBuildError(
            f"{hazard_set.reference} records {hazard_set.investigation_time} years over "
            f"{hazard_set.stochastic_event_sets} event sets and "
            f"{hazard_set.logic_tree_paths} logic-tree paths, which is not a whole "
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
        vulnerability_variants={
            key: asset_bytes(
                vulnerability,
                "vulnerability_set",
                VULNERABILITY_VARIANT_ROLE_PREFIX + key,
                f"{vulnerability} under the {key.replace('_', ' ')} assumption set",
            )
            for key in (vulnerability.assumption_variants or {})
        },
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
            "assumption_sets": dict(vulnerability.assumption_variants or {}),
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
