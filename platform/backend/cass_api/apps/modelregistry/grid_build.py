"""Building an area-peril grid on the platform, for whatever country is worked on.

``cass_keys.grids`` has always been able to generate a grid from a written
specification, and the geometry it produces is general. What the platform had was
two prototype specifications, for the two pilot countries, compiled into the
image -- so a grid was something CASS shipped rather than something a modeller
could build. Anyone working on a third country had a CSV upload and no way to
say what the grid was for.

This is the door. A specification arrives as a document -- tiles, a base
resolution, named refinements and the questions it does not answer -- and comes
back as a registered grid with its cells in the artifact store, or as a refusal
naming what is wrong with it.

Two things it refuses, because both produce a grid that looks fine.

**A specification whose cells nobody could run.** Resolution is quadratic in
cost: Indonesia at 0.1 degrees is 52,831 cells, and at 0.01 it is five million.
The count is estimated from the geometry before anything is generated, and a
specification over the installation's limit is refused with the number, so the
person who wrote it can decide what to change.

**A grid that says nothing about what it leaves open.** Site conditions,
coastline treatment and mapping tolerance are decisions the specification does
not make, and the registry record carries them as notes rather than letting
silence read as resolution.

Nothing built here is approved. A grid arrives as a draft, as the prototypes do,
and a model version built on it says so.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.db import transaction

from cass_keys import grids

from .assets import attach_grid_cells
from .models import AreaPerilGrid, PublicationState

#: What the border policy is, for every grid this platform builds: the lookup
#: reports a location outside the domain rather than snapping it to the nearest
#: cell, and a record that claimed otherwise would be describing different code.
BORDER_POLICY = (
    "Reported, never snapped. A location outside every tile is returned as "
    "fail_ap with its coordinates."
)

#: What a grid carries about site response until something attaches it.
NO_SITE_CONDITIONS = (
    "None. No cell carries a site parameter, so no fallback applies and site "
    "response is absent rather than defaulted."
)


class GridBuildError(Exception):
    """Raised when a specification cannot be read, built or registered."""


def maximum_cells() -> int:
    return int(getattr(settings, "CASS_MAX_GRID_CELLS", 250_000))


def specification_from(document: Any) -> grids.GridSpecification:
    """Read a specification document, refusing one that cannot be built from."""
    if not isinstance(document, dict):
        raise GridBuildError("A grid specification must be an object.")

    tiles = tuple(
        grids.Tile(
            name=_text(item, "name", "Each tile"),
            box=_box(item, f"Tile {item.get('name', '?')!r}"),
            reason=str(item.get("reason") or ""),
        )
        for item in _items(document, "tiles")
    )
    if not tiles:
        raise GridBuildError(
            "The specification names no tiles, so it covers nothing. Tiling is how "
            "a domain excludes open ocean: state the areas that are modelled."
        )

    refinements = tuple(
        grids.Refinement(
            name=_text(item, "name", "Each refinement"),
            box=_box(item, f"Refinement {item.get('name', '?')!r}"),
            resolution=_decimal(item, "resolution_deg", f"Refinement {item.get('name', '?')!r}"),
            reason=str(item.get("reason") or ""),
        )
        for item in _items(document, "refinements", required=False)
    )

    try:
        return grids.GridSpecification(
            country_code=_text(document, "country_code", "The specification").upper(),
            version=_text(document, "version", "The specification"),
            label=_text(document, "label", "The specification"),
            base_resolution=_decimal(document, "base_resolution_deg", "The specification"),
            tiles=tiles,
            refinements=refinements,
            mapping_tolerance_km=_optional_decimal(
                document, "mapping_tolerance_km", default=Decimal(0)
            ),
            open_questions=tuple(
                str(item) for item in (document.get("open_questions") or ())
            ),
            notes=str(document.get("notes") or ""),
        )
    except grids.GridSpecificationError as exc:
        raise GridBuildError(str(exc)) from None


def estimated_cells(specification: grids.GridSpecification) -> int:
    """How many cells this specification would generate, before generating any.

    An upper bound: refined cells replace the base cells beneath them, and this
    counts both. That is the right direction to be wrong in for a guard.
    """
    total = 0
    for tile in specification.tiles:
        total += _cells_in(tile.box, specification.base_resolution)
    for refinement in specification.refinements:
        total += _cells_in(refinement.box, refinement.resolution)
    return total


def estimate(document: Any) -> dict[str, Any]:
    """What a specification would generate, before anything is generated.

    Resolution is quadratic in cost and a modeller cannot see that from a
    number typed into a box: 0.1 degrees over Indonesia is tens of thousands of
    cells and 0.01 is millions, and until now the only way to find out which
    was to press the button. This answers the same question ``build`` asks
    itself, so a specification this calls within the limit is one that builds.

    Written for a specification still being typed, so it refuses almost
    nothing. An area still missing a corner is left out of the count and
    reported as incomplete rather than turning the whole answer into an error,
    and a contradiction the build would refuse -- a refinement no finer than the
    base -- is named while the person is still in a position to change it.
    """
    if not isinstance(document, dict):
        raise GridBuildError("A grid specification must be an object.")

    problems: list[str] = []
    incomplete = 0

    base = _resolution_or_none(document, "base_resolution_deg", "The base resolution", problems)

    cells_from_tiles = 0
    counted_tiles = 0
    for position, item in enumerate(_items(document, "tiles", required=False), start=1):
        stated = len(problems)
        box = _box_or_none(item, f"Tile {_name_of(item, 'Tile', position)!r}", problems)
        if box is None:
            # An area is either wrong, and named above, or simply not finished
            # being typed. Never reported as both.
            if len(problems) == stated:
                incomplete += 1
            continue
        counted_tiles += 1
        if base is not None:
            cells_from_tiles += _cells_in(box, base)

    by_refinement: list[dict[str, Any]] = []
    for position, item in enumerate(_items(document, "refinements", required=False), start=1):
        name = _name_of(item, "Refinement", position)
        stated = len(problems)
        box = _box_or_none(item, f"Refinement {name!r}", problems)
        resolution = _resolution_or_none(
            item, "resolution_deg", f"Refinement {name!r}", problems
        )
        if box is None or resolution is None:
            if len(problems) == stated:
                incomplete += 1
            continue
        if base is not None and resolution >= base:
            problems.append(
                f"Refinement {name!r} is at {resolution} degrees, which is not finer "
                f"than the base {base}. A refinement that does not refine is a "
                "specification error."
            )
        by_refinement.append(
            {"name": name, "resolution_deg": str(resolution), "cells": _cells_in(box, resolution)}
        )

    cells = cells_from_tiles + sum(item["cells"] for item in by_refinement)
    limit = maximum_cells()
    return {
        "cells": cells,
        "cells_from_tiles": cells_from_tiles,
        "cells_by_refinement": by_refinement,
        "counted_tiles": counted_tiles,
        "incomplete": incomplete,
        "problems": problems,
        "limit": limit,
        "within_limit": cells <= limit,
        # The same upper bound the build guards against, so the two can never
        # disagree about whether a specification is buildable. It counts a
        # refined cell and the base cell it replaces, which overstates by the
        # area of the refinements and never understates.
        "is_upper_bound": bool(by_refinement),
        "estimated": base is not None and counted_tiles > 0,
    }


@transaction.atomic
def build(document: Any, *, actor=None) -> tuple[AreaPerilGrid, dict[str, Any]]:
    """Build and register the grid a specification describes.

    Idempotent by version, as the prototype registration is: rebuilding a
    version replaces its cell file and leaves the record in place, so one version
    never means two different sets of identifiers.
    """
    specification = specification_from(document)
    limit = maximum_cells()
    estimate = estimated_cells(specification)
    if estimate > limit:
        raise GridBuildError(
            f"This specification would generate about {estimate:,} cells and this "
            f"installation builds at most {limit:,}. Resolution is quadratic: "
            "halving it quadruples the count. Coarsen the base resolution, or "
            "narrow the tiles, or refine a smaller area."
        )

    try:
        cells = grids.build(specification)
    except grids.GridSpecificationError as exc:
        raise GridBuildError(str(exc)) from None

    grid = register(specification, cells, actor=actor)
    return grid, summary(specification, cells)


def register(
    specification: grids.GridSpecification, cells, *, actor=None
) -> AreaPerilGrid:
    """Record one built grid and store its cells.

    Shared with the prototype registration, so a grid built from a specification
    on the platform and one compiled into the image arrive as the same kind of
    record.
    """
    finest = min(
        (item.resolution for item in specification.refinements),
        default=specification.base_resolution,
    )
    grid, _ = AreaPerilGrid.objects.update_or_create(
        country_code=specification.country_code,
        version=specification.version,
        defaults={
            "label": specification.label,
            "base_resolution_deg": specification.base_resolution,
            "refined_resolution_deg": finest,
            "refinement_rule": refinement_rule(specification),
            "excludes_offshore": True,
            "site_condition_source": "",
            "site_condition_fallback": NO_SITE_CONDITIONS,
            "border_policy": BORDER_POLICY,
            "mapping_tolerance_km": specification.mapping_tolerance_km,
            "publication_state": PublicationState.DRAFT,
            "notes": notes(specification.notes, specification.open_questions),
            "updated_by": actor,
        },
        create_defaults={
            "label": specification.label,
            "base_resolution_deg": specification.base_resolution,
            "refined_resolution_deg": finest,
            "refinement_rule": refinement_rule(specification),
            "excludes_offshore": True,
            "site_condition_source": "",
            "site_condition_fallback": NO_SITE_CONDITIONS,
            "border_policy": BORDER_POLICY,
            "mapping_tolerance_km": specification.mapping_tolerance_km,
            "publication_state": PublicationState.DRAFT,
            "notes": notes(specification.notes, specification.open_questions),
            "created_by": actor,
            "updated_by": actor,
        },
    )
    attach_grid_cells(
        grid,
        grids.to_csv(cells),
        filename=f"{grid.reference}-cells.csv",
        actor=actor,
    )
    return grid


def summary(specification: grids.GridSpecification, cells) -> dict[str, Any]:
    """What was built, in the terms the specification was written in."""
    refined = {
        item.name: sum(
            1
            for cell in cells
            if item.box.contains(
                (cell.min_latitude + cell.max_latitude) / 2,
                (cell.min_longitude + cell.max_longitude) / 2,
            )
        )
        for item in specification.refinements
    }
    return {
        "specification": specification.as_dict(),
        "cells": len(cells),
        "cells_by_refinement": refined,
        "cells_at_base_resolution": len(cells) - sum(refined.values()),
        "builder_version": grids.BUILDER_VERSION,
    }


def refinement_rule(specification: grids.GridSpecification) -> str:
    if not specification.refinements:
        return "No refinement; the whole domain sits at the base resolution."
    finest = min(item.resolution for item in specification.refinements)
    named = ", ".join(item.name for item in specification.refinements)
    return (
        f"Named areas refined to {finest} degrees: {named}. Whether that follows "
        "exposure, hazard gradient or something else is stated in the reasons the "
        "specification gives for each."
    )


def notes(written: str, open_questions: tuple[str, ...]) -> str:
    return "\n".join([written, "", "Open questions:", *(f"- {item}" for item in open_questions)])


# -- reading the document ----------------------------------------------------

def _items(document: dict[str, Any], name: str, *, required: bool = True) -> list[dict]:
    value = document.get(name)
    if value is None and not required:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise GridBuildError(f"{name} must be a list of objects.")
    return value


def _text(document: dict[str, Any], name: str, what: str) -> str:
    value = document.get(name)
    if not isinstance(value, str) or not value.strip():
        raise GridBuildError(f"{what} must state its {name}.")
    return value.strip()


def _decimal(document: dict[str, Any], name: str, what: str) -> Decimal:
    value = document.get(name)
    if value in (None, ""):
        raise GridBuildError(f"{what} must state its {name}.")
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise GridBuildError(f"{what} has a {name} that is not a number: {value!r}.") from None


def _optional_decimal(document: dict[str, Any], name: str, *, default: Decimal) -> Decimal:
    value = document.get(name)
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise GridBuildError(f"{name} is not a number: {value!r}.") from None


def _box(document: dict[str, Any], what: str) -> grids.Box:
    try:
        return grids.Box(
            min_latitude=_decimal(document, "min_latitude", what),
            max_latitude=_decimal(document, "max_latitude", what),
            min_longitude=_decimal(document, "min_longitude", what),
            max_longitude=_decimal(document, "max_longitude", what),
        )
    except grids.GridSpecificationError as exc:
        raise GridBuildError(f"{what}: {exc}") from None


def _name_of(item: dict[str, Any], what: str, position: int) -> str:
    """What to call an area in a count, including one not yet named."""
    name = item.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else f"{what} {position}"


def _box_or_none(
    item: dict[str, Any], what: str, problems: list[str]
) -> grids.Box | None:
    """One area's box, where it has one yet.

    A box still being typed is absent, and an impossible one -- a latitude range
    that runs backwards -- is a mistake worth saying out loud straight away
    rather than at the end. The two are not the same thing and are not reported
    as though they were.
    """
    corners = ("min_latitude", "max_latitude", "min_longitude", "max_longitude")
    if any(item.get(corner) in (None, "") for corner in corners):
        return None
    try:
        return _box(item, what)
    except GridBuildError as exc:
        problems.append(str(exc))
        return None


def _resolution_or_none(
    item: dict[str, Any], name: str, what: str, problems: list[str]
) -> Decimal | None:
    """A resolution, where one has been typed and could be a resolution."""
    if item.get(name) in (None, ""):
        return None
    try:
        resolution = _decimal(item, name, what)
    except GridBuildError as exc:
        problems.append(str(exc))
        return None
    if resolution <= 0:
        problems.append(f"{what} must be positive.")
        return None
    return resolution


def _cells_in(box: grids.Box, resolution: Decimal) -> int:
    """Cells one box holds at one resolution, counted the way they are generated."""
    if resolution <= 0:
        raise GridBuildError("A resolution must be positive.")
    return _along(box.min_latitude, box.max_latitude, resolution) * _along(
        box.min_longitude, box.max_longitude, resolution
    )


def _along(minimum: Decimal, maximum: Decimal, resolution: Decimal) -> int:
    """Steps from the aligned start below ``minimum`` up to ``maximum``."""
    start = (minimum / resolution).to_integral_value(rounding=ROUND_FLOOR) * resolution
    span = (maximum - start) / resolution
    return max(int(span.to_integral_value(rounding=ROUND_CEILING)), 0)
