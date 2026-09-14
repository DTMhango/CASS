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

import csv
import dataclasses
import hashlib
import io
import math
import pathlib
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
    #: Paths drawn through the logic tree, each in proportion to its weight and
    #: each covering the whole span again, so the catalogue carries the model's
    #: own weighting (ADR 18). One is a single view of the hazard, which is all
    #: a single-branch prototype has.
    logic_tree_samples: int = 1
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
        """Years the catalogue covers: every path's event sets, end to end."""
        return (
            self.investigation_time
            * self.ses_per_logic_tree_path
            * self.logic_tree_samples
        )

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
        # Sampled paths, never enumeration. A sampled path is drawn in
        # proportion to its weight, so pooling several carries the model's own
        # weighting; enumeration would hand over branches of unequal weight for
        # the converter to flatten, which it refuses to do (ADR 18).
        f"number_of_logic_tree_samples = {job.logic_tree_samples}",
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


# -- site conditions -----------------------------------------------------------------

#: Columns an OpenQuake site model may carry beyond the coordinates. Only the
#: ones a published model actually supplies are written, because a column
#: present but empty is not the same as a column absent.
SITE_PARAMETERS = ("vs30", "z1pt0", "z2pt5", "vs30measured", "backarc", "region")

#: Values a published site model uses to mean "not measured".
#:
#: This matters more than it looks. ``z1pt0`` and ``z2pt5`` are basin depths,
#: and the ground-motion models take them as real numbers: Chiou and Youngs
#: 2014 carries a term in ``exp(-(z1pt0 - E[z1pt0]) / 150)``, so a depth of
#: -999 metres is not a missing value to it, it is a basin 999 metres above sea
#: level and the term evaluates to about 5,800.
#:
#: Passing the sentinel through produced ground motion on the real Indonesian
#: model with a median PGA of 0.37 g and a maximum of 1,378 g -- roughly a
#: thousand times too high, and entirely plausible-looking as a column of
#: numbers. The convention the engine actually wants is the column's absence,
#: which makes each ground-motion model infer the depth from Vs30 by its own
#: relation. That is what the published calculation was relying on.
SITE_SENTINELS = frozenset({"-999", "-999.0", "-999.00", "nan", ""})

#: Basin depths derived from Vs30 where the published file did not measure them.
#:
#: These are the standard relations the ground-motion models themselves use
#: when a depth is absent -- Chiou and Youngs 2014 for the depth to Vs 1.0 km/s
#: in metres, Campbell and Bozorgnia 2014 for the depth to 2.5 km/s in
#: kilometres. Deriving them here rather than leaving the column out is not a
#: preference: engine 3.23 refuses to start without a value, so the choice is
#: between a derived depth and a wrong one.
#:
#: They are marked derived wherever they are reported, because a depth computed
#: from Vs30 is a correlation and a measured one is a measurement.
def derive_z1pt0(vs30: float) -> float:
    """Depth to Vs 1.0 km/s in metres, after Chiou and Youngs 2014."""
    return math.exp(
        (-7.15 / 4.0)
        * math.log((vs30**4 + 571.0**4) / (1360.0**4 + 571.0**4))
    )


def derive_z2pt5(vs30: float) -> float:
    """Depth to Vs 2.5 km/s in kilometres, after Campbell and Bozorgnia 2014."""
    return math.exp(7.089 - 1.144 * math.log(vs30))


#: Which relation fills which column, where the file leaves it unmeasured.
DERIVED_FROM_VS30 = {"z1pt0": derive_z1pt0, "z2pt5": derive_z2pt5}

#: How far a grid cell may sit from the nearest published site-model point
#: before the association stops describing it. Ten kilometres is the spacing of
#: a typical national site model, so beyond about that the nearest point is a
#: neighbour rather than a measurement of this cell.
DEFAULT_JOIN_LIMIT_KM = 15.0

#: Kilometres per degree of latitude, for the bucketing below. Longitude
#: converges towards the poles; both pilot countries sit near the equator where
#: the difference is small, and the join limit is checked properly afterwards.
_KM_PER_DEGREE = 111.195


@dataclasses.dataclass(frozen=True, slots=True)
class SitePoint:
    """One point of a published site model."""

    longitude: float
    latitude: float
    values: Mapping[str, str]
    #: Parameters the published file carried only as a not-measured sentinel.
    #: Left out of ``values`` so the engine infers them from Vs30, and named
    #: here so the omission is a recorded decision rather than a gap.
    inferred: tuple[str, ...] = ()


def read_site_model(source: str | pathlib.Path) -> tuple[SitePoint, ...]:
    """Read a published site model: coordinates and the conditions at each.

    This is the file a national model ships alongside its sources, and it is
    the answer to the largest open question on the pilot grids -- that no cell
    carries a site parameter. The published model measured them; CASS does not
    have to invent them, it has to join to them.
    """
    path = pathlib.Path(source)
    try:
        rows = list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))
    except OSError as exc:
        raise HazardJobError(f"{path} could not be read: {exc}") from exc
    if not rows:
        raise HazardJobError(f"{path.name} defines no sites.")

    missing = sorted({"lon", "lat"} - set(rows[0]))
    if missing:
        raise HazardJobError(
            f"{path.name} is missing {', '.join(missing)}, so it is not a site "
            "model. It should carry lon, lat and the site parameters."
        )

    present = [name for name in SITE_PARAMETERS if name in rows[0]]

    # A column carrying the not-measured sentinel anywhere is dropped whole.
    # The engine has no way to infer one site's basin depth and take another's
    # from the file -- a column is supplied or it is not -- and supplying -999
    # as though it were a depth is how a loss comes out a thousand times too
    # high while still looking like a number.
    carried = [
        name
        for name in present
        if not any((row.get(name) or "").strip() in SITE_SENTINELS for row in rows)
    ]
    dropped = [name for name in present if name not in carried]

    points = []
    for row in rows:
        try:
            longitude = float(row["lon"])
            latitude = float(row["lat"])
        except (TypeError, ValueError):
            continue
        points.append(
            SitePoint(
                longitude=longitude,
                latitude=latitude,
                values={name: (row.get(name) or "").strip() for name in carried},
                inferred=tuple(dropped),
            )
        )
    if not points:
        raise HazardJobError(f"{path.name} carries no readable coordinates.")
    return tuple(points)


def _bucketed(
    points: Sequence[SitePoint], size: float
) -> dict[tuple[int, int], list[SitePoint]]:
    buckets: dict[tuple[int, int], list[SitePoint]] = {}
    for point in points:
        key = (int(point.latitude // size), int(point.longitude // size))
        buckets.setdefault(key, []).append(point)
    return buckets


def _nearest(
    longitude: float,
    latitude: float,
    buckets: Mapping[tuple[int, int], Sequence[SitePoint]],
    size: float,
) -> tuple[SitePoint | None, float]:
    """The closest published point, and how far away it is in kilometres.

    Searched outward through the surrounding buckets rather than over the whole
    file: a national site model is tens of thousands of points, and a scan per
    grid cell turns a site join into hundreds of millions of comparisons.
    """
    origin = (int(latitude // size), int(longitude // size))
    scale = math.cos(math.radians(latitude)) or 1.0
    best: SitePoint | None = None
    best_square = float("inf")
    found_at: int | None = None

    for radius in range(4):
        for delta_lat in range(-radius, radius + 1):
            for delta_lon in range(-radius, radius + 1):
                if radius and max(abs(delta_lat), abs(delta_lon)) != radius:
                    continue
                for point in buckets.get(
                    (origin[0] + delta_lat, origin[1] + delta_lon), ()
                ):
                    # Squared degrees with longitude scaled for latitude: enough
                    # to rank candidates. The winner's distance is converted once.
                    square = ((point.latitude - latitude) ** 2) + (
                        ((point.longitude - longitude) * scale) ** 2
                    )
                    if square < best_square:
                        best, best_square = point, square
                        found_at = radius
        # One more ring after the first hit, because a point just over a bucket
        # boundary can be closer than one in the middle of this bucket.
        if found_at is not None and radius > found_at:
            break

    if best is None:
        return None, float("inf")
    return best, math.sqrt(best_square) * _KM_PER_DEGREE


@dataclasses.dataclass(frozen=True, slots=True)
class SiteJoin:
    """A grid's site conditions, taken from a published site model."""

    rows: tuple[Mapping[str, str], ...]
    columns: tuple[str, ...]
    joined: int
    unjoined: tuple[int, ...]
    #: Site parameters the engine will infer from Vs30, because the published
    #: file carried them only as a sentinel.
    inferred: tuple[str, ...]
    worst_distance_km: float
    mean_distance_km: float
    join_limit_km: float
    #: Cells with no nearby measurement that took the reference values instead.
    #: Counted apart from the joined ones, because the difference between a
    #: measured soil and an assumed one is the whole reason for the join.
    defaulted: tuple[int, ...] = ()

    @property
    def complete(self) -> bool:
        """Whether every calculation point carries a measured site condition."""
        return not self.unjoined and not self.defaulted

    @property
    def measured_share(self) -> float:
        total = self.joined + len(self.defaulted)
        return self.joined / total if total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "sites": len(self.rows),
            "measured": self.joined,
            "defaulted": len(self.defaulted),
            "dropped": len(self.unjoined),
            "measured_share": self.measured_share,
            "defaulted_examples": list(self.defaulted[:10]),
            "dropped_examples": list(self.unjoined[:10]),
            "parameters": [name for name in self.columns if name in SITE_PARAMETERS],
            "derived_from_vs30": [
                name for name in self.inferred if name in DERIVED_FROM_VS30
            ],
            "worst_distance_km": round(self.worst_distance_km, 2),
            "mean_distance_km": round(self.mean_distance_km, 2),
            "join_limit_km": self.join_limit_km,
            "complete": self.complete,
            "note": (
                "Each cell takes the site conditions of the nearest point of the "
                "published site model. Beyond the join limit the nearest point is "
                "a neighbour rather than a measurement of that cell, so the cell "
                "falls back to the reference values -- which are rock in a "
                "national model, and understate the loss anywhere the ground is "
                "softer than that."
            ),
            "derived_note": (
                "A basin depth the published file carried only as -999 is "
                "recomputed from Vs30 by the relation the ground-motion models "
                "use themselves. Passing the sentinel through instead is read as "
                "a real depth: on this model it inflated median PGA to 0.37 g "
                "and the maximum to 1,378 g, about a thousandfold, while looking "
                "like an ordinary column of numbers."
            ),
        }


def join_site_model(
    job: HazardJob,
    points: Sequence[SitePoint],
    *,
    join_limit_km: float = DEFAULT_JOIN_LIMIT_KM,
    fallback: Mapping[str, str] | None = None,
) -> SiteJoin:
    """Give each calculation point the published site conditions nearest it.

    Refusing beyond the limit, rather than taking the nearest whatever the
    distance, is the point of the function. A cell 200 km from the nearest
    measurement given that measurement's Vs30 looks exactly like a cell that was
    measured, and a loss computed from it is confident about something nobody
    knows.

    ``fallback`` says what such a cell gets instead -- normally the model's own
    reference values. Supplying it keeps the whole grid in the calculation and
    keeps the distinction visible: the report says how many cells are measured
    and how many assumed, and since a national model's reference is rock, an
    assumed cell over soft ground understates its loss.
    """
    if not points:
        raise HazardJobError("The published site model carries no points.")

    carried = [
        name for name in SITE_PARAMETERS if points[0].values.get(name, "") != ""
    ]
    derived = [name for name in points[0].inferred if name in DERIVED_FROM_VS30]
    columns = ("custom_site_id", "lon", "lat", *carried, *derived)
    size = max(join_limit_km / _KM_PER_DEGREE, 0.05)
    buckets = _bucketed(points, size)

    rows: list[Mapping[str, str]] = []
    unjoined: list[int] = []
    defaulted: list[int] = []
    distances: list[float] = []

    for site in job.sites:
        longitude = float(site.longitude)
        latitude = float(site.latitude)
        nearest, distance = _nearest(longitude, latitude, buckets, size)
        located = {
            "custom_site_id": site.site_id,
            "lon": f"{longitude:.5f}",
            "lat": f"{latitude:.5f}",
        }

        if nearest is not None and distance <= join_limit_km:
            distances.append(distance)
            rows.append(
                located
                | {name: nearest.values.get(name, "") for name in carried}
                | _derived(nearest.values.get("vs30", ""), points[0].inferred)
            )
            continue

        if fallback is None:
            unjoined.append(site.area_peril_id)
            continue

        missing = [name for name in carried if name not in fallback]
        if missing:
            raise HazardJobError(
                f"The fallback site conditions do not carry {', '.join(missing)}, "
                "which the published site model supplies. A row missing a column "
                "the others have would make the site file unreadable."
            )
        defaulted.append(site.area_peril_id)
        rows.append(
            located
            | {name: fallback[name] for name in carried}
            | _derived(fallback.get("vs30", ""), points[0].inferred)
        )

    return SiteJoin(
        rows=tuple(rows),
        columns=columns,
        inferred=points[0].inferred,
        joined=len(distances),
        unjoined=tuple(unjoined),
        defaulted=tuple(defaulted),
        worst_distance_km=max(distances, default=0.0),
        mean_distance_km=(sum(distances) / len(distances)) if distances else 0.0,
        join_limit_km=join_limit_km,
    )


def _derived(vs30: str, inferred: Sequence[str]) -> dict[str, str]:
    """Basin depths computed from Vs30, for the columns the file did not measure."""
    wanted = [name for name in inferred if name in DERIVED_FROM_VS30]
    if not wanted:
        return {}
    try:
        value = float(vs30)
    except (TypeError, ValueError):
        raise HazardJobError(
            f"A site has no readable Vs30 ({vs30!r}), so the basin depths the "
            "ground-motion models need cannot be derived from it."
        ) from None
    return {name: f"{DERIVED_FROM_VS30[name](value):.4f}" for name in wanted}


def site_model_csv(join: SiteJoin) -> bytes:
    """The site model file, carrying the area peril as the site identifier."""
    if not join.rows:
        raise HazardJobError(
            "No calculation point fell within the join limit of the published "
            "site model, so the calculation would have no sites at all."
        )
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(join.columns), lineterminator="\n")
    writer.writeheader()
    writer.writerows(join.rows)
    return buffer.getvalue().encode("utf-8")
