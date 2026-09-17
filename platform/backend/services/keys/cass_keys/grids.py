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

Three properties the rest of the platform depends on.

Area-peril identifiers are stable within a version. They are assigned in a
deterministic order over a deterministic cell set, so the same specification
always yields the same identifiers, and section 6 forbids an identifier
changing meaning.

Cells do not overlap and leave no gaps. A refined region replaces the base cells
beneath it rather than being added on top of them, so a coordinate falls in
exactly one cell. That only holds when a refinement's edges sit on the base
lattice and its resolution divides the base a whole number of times -- measured,
a box edged at 0.06 over a 0.1 base put a quarter of its points in two cells, and
one edged at 0.05 put three fifths in none -- so a refinement that does not is
refused, with the box that would be.

A grid can keep only the cells it needs. Tiles are rectangles and a country is
not, so a grid can ask to keep only cells that touch the country's land
(:mod:`.land`) and only cells near anywhere somebody lives or something is built
(:mod:`.settlement`). Every cell dropped is a hazard site no calculation spends
time on and no footprint stores.
"""

from __future__ import annotations

import csv
import dataclasses
import io
from collections.abc import Iterable, Sequence
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Protocol

from .lookup import GridCell

#: Bumped when the generator's geometry or identifier ordering changes. A grid
#: built under a different version of this code is a different grid.
#:
#: 1.1.0 adds the domain -- land and settlement -- and refuses refinements off
#: the base lattice. A specification with no domain and aligned refinements
#: builds exactly the cells, in exactly the order, 1.0.0 did.
BUILDER_VERSION = "1.1.0"

#: The most candidate cells generated before the domain is applied. A
#: specification over this is refused before anything is generated, because
#: the arrays alone would be gigabytes; the installation limit on the cells a
#: grid keeps is far below it.
MAXIMUM_CANDIDATES = 25_000_000

#: The widest buffer a domain may state. Beyond this the buffer is no longer a
#: tolerance around land or settlement but a second, undeclared domain.
MAXIMUM_BUFFER_KM = Decimal("50")


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

    def aligned_to(self, resolution: Decimal) -> Box:
        """The smallest box on ``resolution``'s lattice that contains this one."""
        return Box(
            _floor_to(self.min_latitude, resolution),
            _ceiling_to(self.max_latitude, resolution),
            _floor_to(self.min_longitude, resolution),
            _ceiling_to(self.max_longitude, resolution),
        )

    def is_aligned_to(self, resolution: Decimal) -> bool:
        return all(
            edge % resolution == 0
            for edge in (
                self.min_latitude,
                self.max_latitude,
                self.min_longitude,
                self.max_longitude,
            )
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "min_latitude": _plain(self.min_latitude),
            "max_latitude": _plain(self.max_latitude),
            "min_longitude": _plain(self.min_longitude),
            "max_longitude": _plain(self.max_longitude),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class Tile:
    """One part of the modelled domain, named so a reviewer can locate it.

    Tiling is how a grid states the areas it models, so everything outside them
    is outside by construction rather than by a filter nobody can inspect. With
    a land clip the tiles no longer have to follow a coast: they name regions,
    and the clip removes their sea.
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
class Domain:
    """Which of the tiles' cells a grid keeps.

    ``clip_to_land`` keeps cells touching the country's land, widened by
    ``coast_buffer_km``. ``skip_unsettled`` keeps cells within
    ``settlement_buffer_km`` of anywhere a building stands or a resident lives.
    Both default off, so a specification that says nothing about its domain
    keeps every cell of its tiles, as it always did.
    """

    clip_to_land: bool = False
    coast_buffer_km: Decimal = Decimal("5")
    skip_unsettled: bool = False
    settlement_buffer_km: Decimal = Decimal("5")

    def __post_init__(self) -> None:
        for name in ("coast_buffer_km", "settlement_buffer_km"):
            value = getattr(self, name)
            if value < 0 or value > MAXIMUM_BUFFER_KM:
                raise GridSpecificationError(
                    f"The {name.replace('_', ' ')} is {value}. It must be between 0 "
                    f"and {MAXIMUM_BUFFER_KM}: a buffer is a tolerance around land or "
                    "settlement, and a wider one is a different domain."
                )

    @property
    def is_active(self) -> bool:
        return self.clip_to_land or self.skip_unsettled

    def as_dict(self) -> dict[str, Any]:
        described: dict[str, Any] = {
            "clip_to_land": self.clip_to_land,
            "coast_buffer_km": _plain(self.coast_buffer_km),
            "skip_unsettled": self.skip_unsettled,
            "settlement_buffer_km": _plain(self.settlement_buffer_km),
        }
        if self.clip_to_land:
            from .land import SOURCE as land_source  # noqa: PLC0415

            described["land_source"] = land_source
        if self.skip_unsettled:
            from .settlement import SOURCE as settlement_source  # noqa: PLC0415

            described["settlement_source"] = settlement_source
        return described


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
    domain: Domain = Domain()

    def __post_init__(self) -> None:
        if self.base_resolution <= 0:
            raise GridSpecificationError("The base resolution must be positive.")
        if not self.tiles:
            raise GridSpecificationError(
                f"The {self.country_code} specification names no tiles, so it covers "
                "nothing."
            )
        for problem in refinement_problems(self.base_resolution, self.refinements):
            raise GridSpecificationError(problem)

    def as_dict(self) -> dict[str, Any]:
        return {
            "country_code": self.country_code,
            "version": self.version,
            "label": self.label,
            "builder_version": BUILDER_VERSION,
            "base_resolution_deg": _plain(self.base_resolution),
            "mapping_tolerance_km": _plain(self.mapping_tolerance_km),
            "domain": self.domain.as_dict(),
            "tiles": [
                {"name": tile.name, "reason": tile.reason, **tile.box.as_dict()}
                for tile in self.tiles
            ],
            "refinements": [
                {
                    "name": item.name,
                    "resolution_deg": _plain(item.resolution),
                    "reason": item.reason,
                    **item.box.as_dict(),
                }
                for item in self.refinements
            ],
            "open_questions": list(self.open_questions),
            "notes": self.notes,
        }


def refinement_problems(
    base_resolution: Decimal, refinements: Sequence[Refinement]
) -> list[str]:
    """Everything about a set of refinements that would break the grid's geometry.

    Each is stated with what would fix it, because each is found while a
    specification is being written and the person writing it can act on it.
    """
    problems = []
    for item in refinements:
        if item.resolution >= base_resolution:
            problems.append(
                f"Refinement {item.name!r} is at {_plain(item.resolution)} degrees, "
                f"which is not finer than the base {_plain(base_resolution)}. A "
                "refinement that does not refine is a specification error."
            )
            continue
        if base_resolution % item.resolution != 0:
            problems.append(
                f"Refinement {item.name!r} is at {_plain(item.resolution)} degrees, "
                f"which does not divide the base {_plain(base_resolution)} a whole "
                "number of times, so its cells would straddle the base cells beside "
                "them. Use a resolution the base divides by, such as "
                f"{_plain(base_resolution / 2)} or {_plain(base_resolution / 4)}."
            )
            continue
        if not item.box.is_aligned_to(base_resolution):
            suggested = item.box.aligned_to(base_resolution)
            problems.append(
                f"Refinement {item.name!r} has edges that are not multiples of the "
                f"base resolution {_plain(base_resolution)}, so it would leave some "
                "places in no cell and others in two. The nearest box that covers "
                f"it and is: latitude {_plain(suggested.min_latitude)} to "
                f"{_plain(suggested.max_latitude)}, longitude "
                f"{_plain(suggested.min_longitude)} to {_plain(suggested.max_longitude)}."
            )
    for position, first in enumerate(refinements):
        for second in refinements[position + 1 :]:
            if first.resolution != second.resolution and first.box.overlaps(second.box):
                problems.append(
                    f"Refinements {first.name!r} and {second.name!r} overlap at "
                    "different resolutions, so the place they share would be in two "
                    "cells at once. Make them meet at an edge, or give them the same "
                    "resolution."
                )
    return problems


# -- the lattice -----------------------------------------------------------------------

class DomainMasks(Protocol):
    """Where the land and settlement tests come from. Replaced in tests."""

    def land(self, code, resolution, buffer_km, first_row, last_row, first_column, last_column): ...

    def settled(self, resolution, buffer_km, first_row, last_row, first_column, last_column): ...


class PublishedMasks:
    """The land and settlement layers that ship with CASS."""

    def land(self, code, resolution, buffer_km, first_row, last_row, first_column, last_column):
        from .land import land_mask  # noqa: PLC0415

        return land_mask(code, resolution, buffer_km, first_row, last_row, first_column, last_column)

    def settled(self, resolution, buffer_km, first_row, last_row, first_column, last_column):
        from .settlement import settlement_mask  # noqa: PLC0415

        return settlement_mask(resolution, buffer_km, first_row, last_row, first_column, last_column)


@dataclasses.dataclass(frozen=True, slots=True)
class CellCount:
    """How many cells a specification keeps, counted exactly, and where the rest went."""

    cells: int
    candidates: int
    from_tiles: int
    by_refinement: tuple[tuple[str, int], ...]
    removed_as_sea: int
    removed_as_unsettled: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "cells": self.cells,
            "candidates": self.candidates,
            "cells_from_tiles": self.from_tiles,
            "cells_by_refinement": [
                {"name": name, "cells": cells} for name, cells in self.by_refinement
            ],
            "removed_as_sea": self.removed_as_sea,
            "removed_as_unsettled": self.removed_as_unsettled,
        }


def candidate_upper_bound(specification: GridSpecification) -> int:
    """Cells the tiles and refinements span before overlaps and the domain.

    Counted from the boxes alone, so it costs nothing however large the grid;
    it is what decides whether counting exactly is affordable at all.
    """
    total = 0
    for tile in specification.tiles:
        total += _cells_in_box(tile.box, specification.base_resolution)
    for item in specification.refinements:
        total += _cells_in_box(item.box, item.resolution)
    return total


def _numpy():
    try:
        import numpy
    except ImportError as exc:  # pragma: no cover - installed with the geodata extra
        raise GridSpecificationError(
            "Building a grid needs numpy. Install the keys service with its geodata "
            "extra: pip install 'cass-keys[geodata]'."
        ) from exc
    return numpy


def _index_range(minimum: Decimal, maximum: Decimal, resolution: Decimal) -> tuple[int, int]:
    """The first lattice index a box reaches, and the one after its last."""
    first = int((minimum / resolution).to_integral_value(rounding=ROUND_FLOOR))
    after = int((maximum / resolution).to_integral_value(rounding=ROUND_CEILING))
    return first, after


def _cells_in_box(box: Box, resolution: Decimal) -> int:
    rows = _index_range(box.min_latitude, box.max_latitude, resolution)
    columns = _index_range(box.min_longitude, box.max_longitude, resolution)
    return max(rows[1] - rows[0], 0) * max(columns[1] - columns[0], 0)


def _box_indices(box: Box, resolution: Decimal):
    numpy = _numpy()
    first_row, after_row = _index_range(box.min_latitude, box.max_latitude, resolution)
    first_column, after_column = _index_range(box.min_longitude, box.max_longitude, resolution)
    rows, columns = numpy.meshgrid(
        numpy.arange(first_row, after_row, dtype=numpy.int64),
        numpy.arange(first_column, after_column, dtype=numpy.int64),
        indexing="ij",
    )
    return rows.ravel(), columns.ravel()


def _unique(rows, columns):
    numpy = _numpy()
    if not rows.size:
        return rows, columns
    pairs = numpy.unique(numpy.stack([rows, columns], axis=1), axis=0)
    return pairs[:, 0], pairs[:, 1]


@dataclasses.dataclass(slots=True)
class _Generated:
    """The kept cells of each resolution, as lattice indices, with the counts."""

    groups: dict[Decimal, tuple[Any, Any]]
    count: CellCount


def _generate(specification: GridSpecification, masks: DomainMasks | None) -> _Generated:
    numpy = _numpy()
    upper = candidate_upper_bound(specification)
    if upper > MAXIMUM_CANDIDATES:
        raise GridSpecificationError(
            f"This specification spans about {upper:,} cells before anything is "
            f"removed, and at most {MAXIMUM_CANDIDATES:,} can be generated. Coarsen "
            "the base resolution, narrow the tiles, or refine a smaller area."
        )
    base = specification.base_resolution
    domain = specification.domain
    masks = masks if masks is not None else PublishedMasks()

    # Base cells from every tile, then without those a refinement replaces.
    # Refinements sit on the base lattice (refused otherwise), so a base cell is
    # replaced exactly when its index lies inside a refinement's index range --
    # which is the same test as its centre lying inside the refinement's box.
    pieces = [_box_indices(tile.box, base) for tile in specification.tiles]
    rows = numpy.concatenate([piece[0] for piece in pieces])
    columns = numpy.concatenate([piece[1] for piece in pieces])
    rows, columns = _unique(rows, columns)
    for item in specification.refinements:
        first_row, after_row = _index_range(item.box.min_latitude, item.box.max_latitude, base)
        first_column, after_column = _index_range(
            item.box.min_longitude, item.box.max_longitude, base
        )
        replaced = (
            (rows >= first_row) & (rows < after_row)
            & (columns >= first_column) & (columns < after_column)
        )
        rows, columns = rows[~replaced], columns[~replaced]

    named: list[tuple[str | None, Decimal, Any, Any]] = [(None, base, rows, columns)]
    for item in specification.refinements:
        refined_rows, refined_columns = _box_indices(item.box, item.resolution)
        named.append((item.name, item.resolution, refined_rows, refined_columns))

    candidates = 0
    removed_as_sea = 0
    removed_as_unsettled = 0
    kept_by_name: dict[str | None, tuple[Decimal, Any, Any]] = {}
    windows: dict[Decimal, tuple[int, int, int, int]] = {}
    for _, resolution, group_rows, group_columns in named:
        if not group_rows.size:
            continue
        window = (
            int(group_rows.min()), int(group_rows.max()),
            int(group_columns.min()), int(group_columns.max()),
        )
        known = windows.get(resolution)
        windows[resolution] = window if known is None else (
            min(known[0], window[0]), max(known[1], window[1]),
            min(known[2], window[2]), max(known[3], window[3]),
        )

    land_masks: dict[Decimal, Any] = {}
    settled_masks: dict[Decimal, Any] = {}
    for name, resolution, group_rows, group_columns in named:
        candidates += int(group_rows.size)
        keep = numpy.ones(group_rows.shape, dtype=bool)
        if group_rows.size and domain.clip_to_land:
            if resolution not in land_masks:
                land_masks[resolution] = masks.land(
                    specification.country_code, resolution, domain.coast_buffer_km,
                    *windows[resolution],
                )
            on_land = land_masks[resolution].contains(group_rows, group_columns)
            removed_as_sea += int((~on_land).sum())
            keep &= on_land
        if group_rows.size and domain.skip_unsettled:
            if resolution not in settled_masks:
                settled_masks[resolution] = masks.settled(
                    resolution, domain.settlement_buffer_km, *windows[resolution]
                )
            settled = settled_masks[resolution].contains(group_rows, group_columns)
            removed_as_unsettled += int((keep & ~settled).sum())
            keep &= settled
        kept_by_name[name] = (resolution, group_rows[keep], group_columns[keep])

    groups: dict[Decimal, tuple[Any, Any]] = {}
    by_refinement: list[tuple[str, int]] = []
    for name, (resolution, kept_rows, kept_columns) in kept_by_name.items():
        if resolution in groups:
            before = groups[resolution][0].size
            merged = _unique(
                numpy.concatenate([groups[resolution][0], kept_rows]),
                numpy.concatenate([groups[resolution][1], kept_columns]),
            )
            added = merged[0].size - before
            groups[resolution] = merged
        else:
            groups[resolution] = (kept_rows, kept_columns)
            added = kept_rows.size
        if name is not None:
            by_refinement.append((name, int(added)))

    from_tiles = int(kept_by_name[None][1].size) if None in kept_by_name else 0
    total = sum(int(group[0].size) for group in groups.values())
    return _Generated(
        groups=groups,
        count=CellCount(
            cells=total,
            candidates=candidates,
            from_tiles=from_tiles,
            by_refinement=tuple(by_refinement),
            removed_as_sea=removed_as_sea,
            removed_as_unsettled=removed_as_unsettled,
        ),
    )


def count(specification: GridSpecification, *, masks: DomainMasks | None = None) -> CellCount:
    """How many cells a specification keeps, without building any of them."""
    return _generate(specification, masks).count


def build(
    specification: GridSpecification, *, masks: DomainMasks | None = None
) -> tuple[GridCell, ...]:
    """Generate the cells one specification describes."""
    generated = _generate(specification, masks)
    cells: list[tuple[Decimal, Decimal, Decimal, Decimal]] = []
    for resolution, (rows, columns) in generated.groups.items():
        for row, column in zip(rows.tolist(), columns.tolist(), strict=True):
            south = Decimal(row) * resolution
            west = Decimal(column) * resolution
            cells.append((south, south + resolution, west, west + resolution))

    # Deterministic order, then deterministic identifiers. Section 6 requires an
    # area-peril identifier to keep its meaning within a version, and that is
    # only true if the same specification always numbers cells the same way.
    unique = sorted(set(cells))
    if not unique:
        raise GridSpecificationError(
            f"The {specification.country_code} specification produced no cells."
            + (
                " Its domain removed every one: check the country code, and that "
                "the tiles cover the country's land."
                if specification.domain.is_active
                else ""
            )
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


def uncovered_land(
    specification: GridSpecification,
    *,
    masks: DomainMasks | None = None,
    examples: int = 5,
    maximum_window: int = 20_000_000,
) -> dict[str, Any] | None:
    """Land of the country, at the base resolution, that no tile or refinement covers.

    A tile list drawn by hand leaves islands out -- the Indonesian prototype's
    eight rectangles miss Natuna, Anambas, Talaud and Sangihe -- and a location
    there is reported as outside the grid. Counted against the country's own
    outline with no buffer, so it is land the map draws and not coast tolerance.

    None where the country is too large to count at this resolution in one pass.
    """
    numpy = _numpy()
    from .land import country as outline  # noqa: PLC0415

    base = specification.base_resolution
    shape = outline(specification.country_code)
    first_row, after_row = _index_range(
        Decimal(str(shape.min_latitude)), Decimal(str(shape.max_latitude)), base
    )
    first_column, after_column = _index_range(
        Decimal(str(shape.min_longitude)), Decimal(str(shape.max_longitude)), base
    )
    if (after_row - first_row) * (after_column - first_column) > maximum_window:
        return None
    masks = masks if masks is not None else PublishedMasks()
    land = masks.land(
        specification.country_code, base, Decimal("0"),
        first_row, after_row - 1, first_column, after_column - 1,
    ).kept.copy()

    for box in [tile.box for tile in specification.tiles] + [
        item.box for item in specification.refinements
    ]:
        rows = _index_range(box.min_latitude, box.max_latitude, base)
        columns = _index_range(box.min_longitude, box.max_longitude, base)
        land[
            max(rows[0] - first_row, 0) : max(rows[1] - first_row, 0),
            max(columns[0] - first_column, 0) : max(columns[1] - first_column, 0),
        ] = False

    missing_rows, missing_columns = numpy.nonzero(land)
    step = float(base)
    latitudes = (missing_rows + first_row + 0.5) * step
    longitudes = (missing_columns + first_column + 0.5) * step
    # Grouped by whole degree, largest first, so the examples name the biggest
    # pieces of land left out rather than the first cells of one of them.
    places: dict[tuple[int, int], list[float]] = {}
    for latitude, longitude in zip(latitudes.tolist(), longitudes.tolist(), strict=True):
        key = (int(latitude // 1), int(longitude // 1))
        entry = places.setdefault(key, [0, 0.0, 0.0])
        entry[0] += 1
        entry[1] += latitude
        entry[2] += longitude
    largest = sorted(places.values(), key=lambda entry: -entry[0])[:examples]
    return {
        "cells": int(missing_rows.size),
        "examples": [
            {
                "cells": int(entry[0]),
                "latitude": round(entry[1] / entry[0], 2),
                "longitude": round(entry[2] / entry[0], 2),
            }
            for entry in largest
        ],
    }


def overlaps(cells: Sequence[GridCell], *, maximum_window: int = 50_000_000) -> dict[str, Any]:
    """How many cells of a built grid share ground with another cell.

    The builder refuses the refinements that cause this, but grids built before
    it did may carry it, and a coordinate in an overlap maps to whichever cell a
    lookup finds first. Measured on the finest lattice every cell sits on: each
    cell adds one over its area, and anywhere counted twice is an overlap.
    """
    numpy = _numpy()
    if not cells:
        return {"cells": 0, "overlapping_cells": 0, "checked": True}

    sizes = {cell.max_latitude - cell.min_latitude for cell in cells}
    sizes |= {cell.max_longitude - cell.min_longitude for cell in cells}
    unit = _lattice_unit(sizes | {cell.min_latitude for cell in cells} | {cell.min_longitude for cell in cells})
    south = min(cell.min_latitude for cell in cells)
    west = min(cell.min_longitude for cell in cells)
    height = int((max(cell.max_latitude for cell in cells) - south) / unit)
    width = int((max(cell.max_longitude for cell in cells) - west) / unit)
    if height * width > maximum_window:
        return {"cells": len(cells), "overlapping_cells": None, "checked": False}

    top = numpy.array([int((cell.min_latitude - south) / unit) for cell in cells])
    bottom = numpy.array([int((cell.max_latitude - south) / unit) for cell in cells])
    left = numpy.array([int((cell.min_longitude - west) / unit) for cell in cells])
    right = numpy.array([int((cell.max_longitude - west) / unit) for cell in cells])
    change = numpy.zeros((height + 1, width + 1), dtype=numpy.int32)
    numpy.add.at(change, (top, left), 1)
    numpy.add.at(change, (top, right), -1)
    numpy.add.at(change, (bottom, left), -1)
    numpy.add.at(change, (bottom, right), 1)
    depth = change.cumsum(axis=0).cumsum(axis=1)[:height, :width]
    doubled = numpy.zeros((height + 1, width + 1), dtype=numpy.int64)
    doubled[1:, 1:] = (depth > 1).astype(numpy.int64).cumsum(axis=0).cumsum(axis=1)
    shared = (
        doubled[bottom, right] - doubled[top, right] - doubled[bottom, left] + doubled[top, left]
    )
    return {
        "cells": len(cells),
        "overlapping_cells": int((shared > 0).sum()),
        "checked": True,
    }


def _lattice_unit(values: Iterable[Decimal]) -> Decimal:
    """The largest step every value is a whole multiple of."""
    import math  # noqa: PLC0415

    exponent = min(value.normalize().as_tuple().exponent for value in values if value != 0)
    scale = Decimal(10) ** exponent
    step = 0
    for value in values:
        step = math.gcd(step, abs(int(value / scale)))
    return scale * (step or 1)


def _floor_to(value: Decimal, resolution: Decimal) -> Decimal:
    """``value`` rounded down to a multiple of ``resolution``.

    Rounded down, not towards zero: ``Decimal``'s ``//`` truncates, which puts
    the first cell of a box edged south of the equator one row too far north.
    """
    return (value / resolution).to_integral_value(rounding=ROUND_FLOOR) * resolution


def _ceiling_to(value: Decimal, resolution: Decimal) -> Decimal:
    return (value / resolution).to_integral_value(rounding=ROUND_CEILING) * resolution


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
