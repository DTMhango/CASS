"""Building a fixed, versioned, adaptive area-peril grid.

Section 6 of the build plan asks each country for a grid that is independent of
any uploaded portfolio: finer where exposure concentrates or hazard gradients
are strong, coarser where exposure is sparse, tiled so Indonesia does not spend
calculation points on open ocean, and refined deliberately around Kathmandu.
Phase 0 lists defining and prototyping those grids as an open charter item with
a named owner.

So this builds a grid from a specification someone wrote down, rather than
producing one from nowhere. The specification is the artefact under discussion:
tiles, base resolution, named refinement regions and the reason for each. A
reviewer reads it, argues with it, and a change to it is a new grid version.

Nothing here produces an approved grid. What it produces is geometry from a
stated prototype specification, and every specification carries the questions
it does not answer -- site conditions, coastline treatment, mapping tolerance --
so a grid cannot quietly acquire the authority of a decision nobody made.

Two properties the rest of the platform depends on.

Area-peril identifiers are stable within a version. They are assigned in a
deterministic order over a deterministic cell set, so the same specification
always yields the same identifiers, and section 6 forbids an identifier
changing meaning.

Cells do not overlap. A refined region replaces the base cells beneath it
rather than being added on top of them, so a coordinate falls in exactly one
cell and a lookup cannot depend on which cell it happened to test first.
"""

from __future__ import annotations

import csv
import dataclasses
import io
from collections.abc import Iterable, Iterator, Sequence
from decimal import Decimal
from typing import Any

from .lookup import GridCell

#: Bumped when the generator's geometry or identifier ordering changes. A grid
#: built under a different version of this code is a different grid.
BUILDER_VERSION = "1.0.0"


class GridSpecificationError(Exception):
    """Raised when a specification cannot produce a usable grid."""


@dataclasses.dataclass(frozen=True, slots=True)
class Box:
    """A bounding box in degrees."""

    min_latitude: Decimal
    max_latitude: Decimal
    min_longitude: Decimal
    max_longitude: Decimal

    def __post_init__(self) -> None:
        if self.min_latitude >= self.max_latitude:
            raise GridSpecificationError(
                f"Latitude range {self.min_latitude}..{self.max_latitude} is empty."
            )
        if self.min_longitude >= self.max_longitude:
            raise GridSpecificationError(
                f"Longitude range {self.min_longitude}..{self.max_longitude} is empty."
            )

    def contains(self, latitude: Decimal, longitude: Decimal) -> bool:
        return (
            self.min_latitude <= latitude < self.max_latitude
            and self.min_longitude <= longitude < self.max_longitude
        )

    def overlaps(self, other: Box) -> bool:
        return not (
            self.max_latitude <= other.min_latitude
            or other.max_latitude <= self.min_latitude
            or self.max_longitude <= other.min_longitude
            or other.max_longitude <= self.min_longitude
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "min_latitude": str(self.min_latitude),
            "max_latitude": str(self.max_latitude),
            "min_longitude": str(self.min_longitude),
            "max_longitude": str(self.max_longitude),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class Tile:
    """One part of the modelled domain, named so a reviewer can locate it.

    Tiling is how Indonesia avoids open ocean without a coastline mask: the
    domain is stated as the areas that are modelled, so everything outside it
    is outside by construction rather than by a filter nobody can inspect.
    """

    name: str
    box: Box
    reason: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class Refinement:
    """An area modelled at a finer resolution, and why."""

    name: str
    box: Box
    resolution: Decimal
    reason: str

    def __post_init__(self) -> None:
        if self.resolution <= 0:
            raise GridSpecificationError(
                f"Refinement {self.name!r} has a non-positive resolution."
            )


@dataclasses.dataclass(frozen=True, slots=True)
class GridSpecification:
    """Everything needed to build one country's grid, and what it leaves open."""

    country_code: str
    version: str
    label: str
    base_resolution: Decimal
    tiles: tuple[Tile, ...]
    refinements: tuple[Refinement, ...] = ()
    mapping_tolerance_km: Decimal = Decimal("0")
    #: What this specification does not decide. Carried with the grid so a
    #: reader is never left inferring that silence meant resolution.
    open_questions: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        if self.base_resolution <= 0:
            raise GridSpecificationError("The base resolution must be positive.")
        if not self.tiles:
            raise GridSpecificationError(
                f"The {self.country_code} specification names no tiles, so it covers "
                "nothing."
            )
        for refinement in self.refinements:
            if refinement.resolution >= self.base_resolution:
                raise GridSpecificationError(
                    f"Refinement {refinement.name!r} is at {refinement.resolution} "
                    f"degrees, which is not finer than the base {self.base_resolution}. "
                    "A refinement that does not refine is a specification error."
                )

    def as_dict(self) -> dict[str, Any]:
        return {
            "country_code": self.country_code,
            "version": self.version,
            "label": self.label,
            "builder_version": BUILDER_VERSION,
            "base_resolution_deg": str(self.base_resolution),
            "mapping_tolerance_km": str(self.mapping_tolerance_km),
            "tiles": [
                {"name": tile.name, "reason": tile.reason, **tile.box.as_dict()}
                for tile in self.tiles
            ],
            "refinements": [
                {
                    "name": item.name,
                    "resolution_deg": str(item.resolution),
                    "reason": item.reason,
                    **item.box.as_dict(),
                }
                for item in self.refinements
            ],
            "open_questions": list(self.open_questions),
            "notes": self.notes,
        }


def build(specification: GridSpecification) -> tuple[GridCell, ...]:
    """Generate the cells one specification describes.

    Refined cells replace the base cells beneath them. The base pass therefore
    skips any cell whose centre falls inside a refinement, which is what keeps
    the two from overlapping without needing to subtract geometry.
    """
    cells: list[tuple[Decimal, Decimal, Decimal, Decimal]] = []

    for refinement in specification.refinements:
        cells.extend(_cells_in(refinement.box, refinement.resolution))

    for tile in specification.tiles:
        for cell in _cells_in(tile.box, specification.base_resolution):
            centre_latitude = (cell[0] + cell[1]) / 2
            centre_longitude = (cell[2] + cell[3]) / 2
            if any(
                item.box.contains(centre_latitude, centre_longitude)
                for item in specification.refinements
            ):
                continue
            cells.append(cell)

    # Deterministic order, then deterministic identifiers. Section 6 requires an
    # area-peril identifier to keep its meaning within a version, and that is
    # only true if the same specification always numbers cells the same way.
    unique = sorted(set(cells))
    if not unique:
        raise GridSpecificationError(
            f"The {specification.country_code} specification produced no cells."
        )

    return tuple(
        GridCell(
            area_peril_id=position,
            min_latitude=minimum_latitude,
            max_latitude=maximum_latitude,
            min_longitude=minimum_longitude,
            max_longitude=maximum_longitude,
            country_code=specification.country_code,
            offshore=False,
        )
        for position, (
            minimum_latitude,
            maximum_latitude,
            minimum_longitude,
            maximum_longitude,
        ) in enumerate(unique, start=1)
    )


def _cells_in(
    box: Box, resolution: Decimal
) -> Iterator[tuple[Decimal, Decimal, Decimal, Decimal]]:
    """Every cell of one resolution inside one box, on a global origin.

    Cell edges are multiples of the resolution measured from (0, 0) rather than
    from the box corner. Two tiles that meet therefore share an edge instead of
    interleaving, and a refinement lands on the base lattice rather than
    straddling it.
    """
    latitude = _floor_to(box.min_latitude, resolution)
    while latitude < box.max_latitude:
        longitude = _floor_to(box.min_longitude, resolution)
        while longitude < box.max_longitude:
            yield (latitude, latitude + resolution, longitude, longitude + resolution)
            longitude += resolution
        latitude += resolution


def _floor_to(value: Decimal, resolution: Decimal) -> Decimal:
    return (value // resolution) * resolution


def to_csv(cells: Sequence[GridCell]) -> bytes:
    """The cell file the model registry stores and the keys service reads."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "AreaPerilID",
            "MinLatitude",
            "MaxLatitude",
            "MinLongitude",
            "MaxLongitude",
            "CountryCode",
            "Offshore",
        ],
        lineterminator="\n",
    )
    writer.writeheader()
    for cell in cells:
        writer.writerow(
            {
                "AreaPerilID": cell.area_peril_id,
                "MinLatitude": _plain(cell.min_latitude),
                "MaxLatitude": _plain(cell.max_latitude),
                "MinLongitude": _plain(cell.min_longitude),
                "MaxLongitude": _plain(cell.max_longitude),
                "CountryCode": cell.country_code,
                "Offshore": "false",
            }
        )
    return buffer.getvalue().encode("utf-8")


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def coverage(
    cells: Sequence[GridCell], points: Iterable[tuple[Decimal, Decimal]]
) -> dict[str, Any]:
    """How many of a set of coordinates the grid actually covers.

    Reported rather than assumed. A grid built from a bounding-box
    specification will miss real locations near a coastline or a border, and
    section 6 wants those reported rather than snapped to the nearest cell.
    """
    inside = 0
    outside: list[str] = []
    for latitude, longitude in points:
        if any(cell.contains(latitude, longitude) for cell in cells):
            inside += 1
        else:
            outside.append(f"{latitude},{longitude}")
    total = inside + len(outside)
    return {
        "points": total,
        "inside": inside,
        "outside": len(outside),
        "share_covered": (inside / total) if total else 0.0,
        "outside_coordinates": outside[:50],
    }
