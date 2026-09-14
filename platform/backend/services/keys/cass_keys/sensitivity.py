"""How far a coarse geocode can move a location's area-peril assignment.

Work package 3 asks for Cohort B mappings to be compared "under appropriate
location uncertainty buffers", and the cohort exists to "measure whether grid
resolution implies more precision than the geocode supports". A geocode at
locality, postcode or administrative precision is a point standing in for an
area: the provider put it somewhere in a settlement or a district, and the grid
then assigns it to one cell as if it were a building.

So each location is tried at its recorded coordinate and at points spread across
the area its precision implies, and the report says whether they agree. A
location whose points all land in its recorded cell is stable at this grid. One
whose points reach other cells has an assignment the geocode does not support,
and the value on it is reported separately rather than merged into a benchmark.

The buffers are assumptions and are stated as such. A locality in Jakarta and a
locality in rural Papua are different sizes, and nothing in a schedule says
which a given row is. The defaults are round numbers chosen to be argued with;
every report records the ones it used, and any of them can be overridden.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from .lookup import AreaPerilGrid

#: Bumped when the sampling or the rule for stability changes.
SENSITIVITY_VERSION = "1.0.0"

#: Radius, in kilometres, of the area a geocode at each precision stands for.
#: A locality or a postcode is taken as a town-sized area and an administrative
#: match as a district-sized one. Precisions that resolve to a building or a
#: street have no buffer here: they are cohort A, and their mapping is not what
#: this measures.
DEFAULT_BUFFERS_KM: Mapping[str, Decimal] = {
    "locality": Decimal("5"),
    "postcode": Decimal("5"),
    "admin": Decimal("25"),
}

#: Kilometres per degree of latitude. A spherical approximation, which at a
#: buffer of tens of kilometres is wrong by less than the buffer is uncertain.
KM_PER_DEGREE = 111.32

#: Concentric rings inside the buffer, and bearings on each. Enough that a cell
#: edge crossing the buffer is found; fixed, so the same location always gives
#: the same answer.
RINGS = 3
BEARINGS = 16

_COORDINATE = Decimal("0.00000001")


class SensitivityError(Exception):
    """Raised when a sensitivity cannot be computed as asked."""


def sample_points(
    latitude: Decimal,
    longitude: Decimal,
    radius_km: Decimal,
    *,
    rings: int = RINGS,
    bearings: int = BEARINGS,
) -> tuple[tuple[Decimal, Decimal], ...]:
    """The recorded coordinate, then points on rings inside the buffer, in a fixed order."""
    if radius_km < 0:
        raise SensitivityError("A location uncertainty buffer cannot be negative.")
    points = [(latitude, longitude)]
    if radius_km == 0:
        return tuple(points)

    # Degrees of longitude shrink towards the poles; the floor keeps a location
    # at a pole from dividing by zero rather than claiming to model one.
    east_scale = KM_PER_DEGREE * max(math.cos(math.radians(float(latitude))), 1e-6)
    for ring in range(1, rings + 1):
        distance = float(radius_km) * ring / rings
        for step in range(bearings):
            bearing = 2 * math.pi * step / bearings
            north = distance * math.cos(bearing) / KM_PER_DEGREE
            east = distance * math.sin(bearing) / east_scale
            points.append(
                (
                    (latitude + Decimal(repr(north))).quantize(_COORDINATE),
                    (longitude + Decimal(repr(east))).quantize(_COORDINATE),
                )
            )
    return tuple(points)


@dataclasses.dataclass(frozen=True, slots=True)
class LocationSensitivity:
    """One location's area-peril assignment under its uncertainty buffer."""

    location: str
    precision: str
    radius_km: Decimal
    recorded_cell: int | None
    cells_reached: tuple[int, ...]
    points: int
    points_in_recorded_cell: int
    points_outside_grid: int
    tiv: Decimal = Decimal("0")

    @property
    def stable(self) -> bool:
        """Every point in the buffer lands in the cell the coordinate was given."""
        return self.recorded_cell is not None and self.points_in_recorded_cell == self.points

    @property
    def share_in_recorded_cell(self) -> float:
        return self.points_in_recorded_cell / self.points if self.points else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "precision": self.precision,
            "radius_km": str(self.radius_km),
            "recorded_cell": self.recorded_cell,
            "cells_reached": list(self.cells_reached),
            "points": self.points,
            "points_in_recorded_cell": self.points_in_recorded_cell,
            "points_outside_grid": self.points_outside_grid,
            "share_in_recorded_cell": round(self.share_in_recorded_cell, 4),
            "stable": self.stable,
            "tiv": str(self.tiv),
        }


def assess(
    grid: AreaPerilGrid,
    *,
    location: str,
    latitude: Decimal,
    longitude: Decimal,
    precision: str,
    radius_km: Decimal,
    tiv: Decimal = Decimal("0"),
) -> LocationSensitivity:
    """Map a location's recorded coordinate and every point in its buffer."""
    points = sample_points(latitude, longitude, radius_km)
    recorded = grid.find(latitude, longitude)
    reached: set[int] = set()
    inside = outside = 0
    for point_latitude, point_longitude in points:
        cell = grid.find(point_latitude, point_longitude)
        if cell is None:
            outside += 1
            continue
        reached.add(cell.area_peril_id)
        if recorded is not None and cell.area_peril_id == recorded.area_peril_id:
            inside += 1
    return LocationSensitivity(
        location=location,
        precision=precision,
        radius_km=radius_km,
        recorded_cell=recorded.area_peril_id if recorded is not None else None,
        cells_reached=tuple(sorted(reached)),
        points=len(points),
        points_in_recorded_cell=inside,
        points_outside_grid=outside,
        tiv=tiv,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class SensitivityReport:
    """Every assessed location, what could not be assessed, and the assumptions used."""

    grid: str
    buffers_km: Mapping[str, Decimal]
    locations: tuple[LocationSensitivity, ...]
    unassessed: tuple[Mapping[str, str], ...] = ()

    def summary(self) -> dict[str, Any]:
        stable = [item for item in self.locations if item.stable]
        unstable = [item for item in self.locations if not item.stable]
        by_precision: dict[str, dict[str, Any]] = {}
        for item in self.locations:
            entry = by_precision.setdefault(
                item.precision,
                {"locations": 0, "stable": 0, "unstable": 0, "unstable_tiv": Decimal("0")},
            )
            entry["locations"] += 1
            if item.stable:
                entry["stable"] += 1
            else:
                entry["unstable"] += 1
                entry["unstable_tiv"] += item.tiv
        return {
            "assessed": len(self.locations),
            "unassessed": len(self.unassessed),
            "stable": len(stable),
            "unstable": len(unstable),
            "outside_grid_at_recorded_coordinate": sum(
                1 for item in self.locations if item.recorded_cell is None
            ),
            "buffer_reaches_outside_grid": sum(
                1 for item in self.locations if item.points_outside_grid
            ),
            "most_cells_reached": max(
                (len(item.cells_reached) for item in self.locations), default=0
            ),
            # Stability is all-or-nothing and saturates on a fine grid: a buffer
            # wider than a cell almost always crosses an edge somewhere. The
            # share of the buffer that stays put says by how much.
            "mean_share_in_recorded_cell": round(
                sum(item.share_in_recorded_cell for item in self.locations)
                / len(self.locations),
                4,
            )
            if self.locations
            else 0.0,
            "tiv": str(sum((item.tiv for item in self.locations), Decimal("0"))),
            "stable_tiv": str(sum((item.tiv for item in stable), Decimal("0"))),
            "unstable_tiv": str(sum((item.tiv for item in unstable), Decimal("0"))),
            "by_precision": {
                name: {**entry, "unstable_tiv": str(entry["unstable_tiv"])}
                for name, entry in sorted(by_precision.items())
            },
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": SENSITIVITY_VERSION,
            "grid": self.grid,
            "buffers_km": {name: str(value) for name, value in sorted(self.buffers_km.items())},
            "sampling": {"rings": RINGS, "bearings": BEARINGS},
            "summary": self.summary(),
            "locations": [item.as_dict() for item in self.locations],
            "unassessed": [dict(item) for item in self.unassessed],
        }


def buffers(overrides: Mapping[str, Any] | None = None) -> dict[str, Decimal]:
    """The default buffers with any stated overrides applied, refusing a nonsense one."""
    chosen = dict(DEFAULT_BUFFERS_KM)
    for name, value in (overrides or {}).items():
        try:
            radius = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise SensitivityError(
                f"The {name} buffer {value!r} is not a number of kilometres."
            ) from None
        if not radius.is_finite() or radius < 0:
            raise SensitivityError(
                f"The {name} buffer must be zero or more kilometres, not {value!r}."
            )
        chosen[str(name).strip().lower()] = radius
    return chosen


def report(
    grid: AreaPerilGrid,
    locations: Iterable[Mapping[str, Any]],
    *,
    buffers_km: Mapping[str, Any] | None = None,
) -> SensitivityReport:
    """Assess every location against the grid under the buffer its precision implies.

    Each location is a mapping with ``location``, ``latitude``, ``longitude``,
    ``precision`` and, optionally, ``tiv``. A location with no coordinate, or a
    precision with no buffer, is listed as unassessed with the reason rather
    than given a buffer nobody stated.
    """
    chosen = buffers(buffers_km)
    assessed: list[LocationSensitivity] = []
    unassessed: list[dict[str, str]] = []
    for row in locations:
        name = str(row.get("location") or "")
        precision = str(row.get("precision") or "").strip().lower()
        latitude, longitude = row.get("latitude"), row.get("longitude")
        if latitude is None or longitude is None:
            unassessed.append({"location": name, "reason": "The location has no coordinate."})
            continue
        if precision not in chosen:
            unassessed.append(
                {
                    "location": name,
                    "reason": (
                        f"No uncertainty buffer is stated for {precision or 'an unknown'} "
                        "precision, so none was assumed."
                    ),
                }
            )
            continue
        assessed.append(
            assess(
                grid,
                location=name,
                latitude=Decimal(str(latitude)),
                longitude=Decimal(str(longitude)),
                precision=precision,
                radius_km=chosen[precision],
                tiv=Decimal(str(row.get("tiv") or "0")),
            )
        )
    return SensitivityReport(
        grid=grid.reference,
        buffers_km=chosen,
        locations=tuple(assessed),
        unassessed=tuple(unassessed),
    )
