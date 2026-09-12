"""Building an OpenQuake event-based job from an area-peril grid.

The grid decides where hazard is computed, so the job has to be generated from
it rather than written alongside it. A sites file that drifted from the grid it
was meant to match would produce ground motion at coordinates no area peril
covers -- and the join is by coordinate, so the failure would look like missing
hazard rather than like a mismatch.

Two choices here are worth stating because they are not obvious.

**The area peril is carried as ``custom_site_id``.** OpenQuake will invent a
site identifier if the job does not supply one, and in 3.23 it invents a
geohash -- which then has to be joined back through coordinates, comparing
floats that have been through two file formats. Supplying the area peril
directly removes the join entirely: the ground-motion export names the cell.

**Nothing here declares a source model.** A job needs one and this does not
supply it, because which seismic sources represent Indonesia is the single
largest open question in the hazard leg and not something a builder should
default. The caller names the files; ``JobFiles`` records which, so a footprint
can always be traced to the sources behind it.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any

#: OpenQuake truncates ``custom_site_id`` beyond this many characters, which
#: would silently merge two cells into one site.
MAX_SITE_ID_LENGTH = 8


class HazardJobError(Exception):
    """Raised when a job would not faithfully cover its grid."""


@dataclasses.dataclass(frozen=True, slots=True)
class Site:
    """One calculation point, standing for one grid cell."""

    area_peril_id: int
    longitude: Decimal
    latitude: Decimal

    @property
    def site_id(self) -> str:
        return str(self.area_peril_id)


@dataclasses.dataclass(frozen=True, slots=True)
class HazardJob:
    """Everything one event-based calculation needs, except its sources.

    ``investigation_time`` and ``ses_per_logic_tree_path`` multiply to the
    effective time, which becomes the number of Oasis periods. Both are stated
    rather than derived so the occurrence table's denominator is a decision
    somebody made.
    """

    country_code: str
    label: str
    sites: tuple[Site, ...]
    imts: tuple[str, ...]
    source_model_logic_tree: str
    gsim_logic_tree: str
    investigation_time: float = 50.0
    ses_per_logic_tree_path: int = 20
    truncation_level: float = 3.0
    maximum_distance: float = 300.0
    rupture_mesh_spacing: float = 5.0
    area_source_discretization: float = 10.0
    width_of_mfd_bin: float = 0.2
    #: Motion below this is not exported. Set to the floor of the intensity-bin
    #: dictionary, so the engine drops exactly what the converter would drop
    #: anyway -- doing it here keeps the export small rather than making the
    #: converter read and discard it.
    minimum_intensity: float = 0.005
    #: No site parameters are carried on the grid, so one reference value
    #: applies everywhere. This is a known limitation and not a modelling
    #: choice: it is stated on every pilot grid as an open question.
    reference_vs30: float = 400.0
    random_seed: int = 20260912
    #: Where the engine writes its CSV exports. A job that does not name one
    #: runs and exports nothing, which reads as a calculation that produced no
    #: hazard rather than as a missing setting.
    export_dir: str = "out"
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.sites:
            raise HazardJobError(
                f"The {self.country_code} job covers no sites, so it would compute "
                "hazard nowhere."
            )
        if not self.imts:
            raise HazardJobError(
                f"The {self.country_code} job declares no intensity measures."
            )
        identifiers = [item.site_id for item in self.sites]
        if len(set(identifiers)) != len(identifiers):
            raise HazardJobError(
                "Two sites share an area peril. Their ground motion would be "
                "merged and one cell's hazard would be lost."
            )
        too_long = sorted(item for item in identifiers if len(item) > MAX_SITE_ID_LENGTH)
        if too_long:
            raise HazardJobError(
                f"Area peril {too_long[0]} is longer than {MAX_SITE_ID_LENGTH} "
                "characters, which OpenQuake truncates -- silently merging cells."
            )
        if self.investigation_time <= 0 or self.ses_per_logic_tree_path <= 0:
            raise HazardJobError(
                "Investigation time and stochastic event set count must both be "
                "positive; their product is the number of Oasis periods."
            )

    @property
    def effective_time(self) -> float:
        return self.investigation_time * self.ses_per_logic_tree_path

    @property
    def area_perils(self) -> dict[str, int]:
        """The mapping a ground-motion export is read against."""
        return {item.site_id: item.area_peril_id for item in self.sites}

    def as_dict(self) -> dict[str, Any]:
        return {
            "country_code": self.country_code,
            "label": self.label,
            "sites": len(self.sites),
            "imts": list(self.imts),
            "investigation_time": self.investigation_time,
            "ses_per_logic_tree_path": self.ses_per_logic_tree_path,
            "effective_time": self.effective_time,
            "truncation_level": self.truncation_level,
            "maximum_distance": self.maximum_distance,
            "minimum_intensity": self.minimum_intensity,
            "reference_vs30": self.reference_vs30,
            "random_seed": self.random_seed,
            "export_dir": self.export_dir,
            "source_model_logic_tree": self.source_model_logic_tree,
            "gsim_logic_tree": self.gsim_logic_tree,
            "notes": self.notes,
        }


def sites_from_cells(
    cells: Iterable[Any], *, include_offshore: bool = False
) -> tuple[Site, ...]:
    """One calculation point at the centre of each grid cell.

    The centre rather than a corner, because a corner belongs to four cells and
    the ground motion computed there would describe whichever one the join
    happened to pick.

    Offshore cells are left out by default. The plan asks the Indonesian grid to
    avoid unnecessary calculation points over ocean, and the keys service
    already refuses a location that lands on one, so computing hazard there
    would be work nothing can consume.
    """
    chosen = []
    for cell in cells:
        if getattr(cell, "offshore", False) and not include_offshore:
            continue
        chosen.append(
            Site(
                area_peril_id=cell.area_peril_id,
                longitude=(cell.min_longitude + cell.max_longitude) / 2,
                latitude=(cell.min_latitude + cell.max_latitude) / 2,
            )
        )
    return tuple(sorted(chosen, key=lambda item: item.area_peril_id))


def sites_csv(job: HazardJob) -> bytes:
    """The site file, carrying the area peril as the site identifier."""
    lines = ["custom_site_id,lon,lat"]
    lines.extend(
        f"{item.site_id},{item.longitude:.5f},{item.latitude:.5f}"
        for item in job.sites
    )
    return ("\n".join(lines) + "\n").encode("utf-8")


def job_ini(job: HazardJob, *, sites_file: str = "sites.csv") -> bytes:
    """The job configuration, as OpenQuake's ``job.ini``."""
    lines = [
        "[general]",
        f"description = {job.label}",
        "calculation_mode = event_based",
        f"random_seed = {job.random_seed}",
        "",
        "[geometry]",
        f"sites_csv = {sites_file}",
        "",
        "[logic_tree]",
        # Full enumeration. Sampling produces several realisations, and
        # flattening those into one occurrence table needs a weighting rule the
        # converter refuses to invent.
        "number_of_logic_tree_samples = 0",
        "",
        "[erf]",
        f"rupture_mesh_spacing = {job.rupture_mesh_spacing}",
        f"width_of_mfd_bin = {job.width_of_mfd_bin}",
        f"area_source_discretization = {job.area_source_discretization}",
        "",
        "[site_params]",
        "reference_vs30_type = measured",
        f"reference_vs30_value = {job.reference_vs30}",
        "reference_depth_to_2pt5km_per_sec = 1.0",
        "reference_depth_to_1pt0km_per_sec = 100.0",
        "",
        "[calculation]",
        f"source_model_logic_tree_file = {job.source_model_logic_tree}",
        f"gsim_logic_tree_file = {job.gsim_logic_tree}",
        f"investigation_time = {job.investigation_time}",
        f"intensity_measure_types = {', '.join(job.imts)}",
        f"truncation_level = {job.truncation_level}",
        f"maximum_distance = {job.maximum_distance}",
        f"ses_per_logic_tree_path = {job.ses_per_logic_tree_path}",
        f"minimum_intensity = {job.minimum_intensity}",
        "",
        "[output]",
        f"export_dir = {job.export_dir}",
        "ground_motion_fields = true",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def job_checksum(job: HazardJob, *, sites_file: str = "sites.csv") -> str:
    """One checksum over the configuration and the sites it covers.

    Two calculations with the same checksum ran the same job on the same cells,
    which is what makes a footprint reproducible in the sense section 6 asks
    for. It does not cover the source model files, which are named in the
    configuration and checksummed where they are stored.
    """
    digest = hashlib.sha256()
    digest.update(job_ini(job, sites_file=sites_file))
    digest.update(sites_csv(job))
    return digest.hexdigest()


def files(job: HazardJob, *, sites_file: str = "sites.csv") -> Mapping[str, bytes]:
    """Everything the job contributes, ready to write beside a source model."""
    return {"job.ini": job_ini(job, sites_file=sites_file), sites_file: sites_csv(job)}


def coverage(job: HazardJob, cells: Sequence[Any]) -> dict[str, Any]:
    """Which cells of the grid this job computes hazard for, and which it skips.

    A cell with no calculation point produces no footprint, so every location
    in it is mapped by the keys service and then loses nothing in the engine --
    a silent zero rather than a failure. Reported before the run, because
    afterwards it is indistinguishable from an event that did no damage.
    """
    covered = {item.area_peril_id for item in job.sites}
    onshore = {
        cell.area_peril_id for cell in cells if not getattr(cell, "offshore", False)
    }
    missing = sorted(onshore - covered)
    return {
        "country_code": job.country_code,
        "grid_cells": len(cells),
        "onshore_cells": len(onshore),
        "cells_computed": len(covered),
        "cells_skipped": len(missing),
        "covered_share": len(covered & onshore) / len(onshore) if onshore else 0.0,
        "skipped_examples": missing[:10],
    }
