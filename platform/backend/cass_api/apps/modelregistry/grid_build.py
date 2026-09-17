"""Building an area-peril grid on the platform, for whatever country is worked on.

``cass_keys.grids`` has always been able to generate a grid from a written
specification, and the geometry it produces is general. What the platform had was
two prototype specifications, for the two pilot countries, compiled into the
image -- so a grid was something CASS shipped rather than something a modeller
could build. Anyone working on a third country had a CSV upload and no way to
say what the grid was for.

This is the door. A specification arrives as a document -- tiles, a base
resolution, named refinements, the domain it keeps and the questions it does not
answer -- and comes back as a registered grid with its cells in the artifact
store, or as a refusal naming what is wrong with it.

Three things it refuses, because each produces a grid that looks fine.

**A specification whose cells nobody could run.** Every cell is a site in every
hazard calculation on the grid, and resolution is quadratic in cost: Indonesia at
0.1 degrees is 52,831 cells, and at 0.01 it is five million. The cells a
specification keeps are counted exactly -- after its refinements replace base
cells and its domain removes sea and empty land -- before any is generated, and a
specification over the installation's limit is refused with the number.

**A refinement off the base lattice**, which would leave some places in no cell
and others in two. The refusal names the box that would be on it.

**A grid that says nothing about what it leaves open.** Site conditions,
coastline treatment and mapping tolerance are decisions the specification does
not make, and the registry record carries them as notes rather than letting
silence read as resolution. So does what its domain cannot see.

Nothing built here is approved. A grid arrives as a draft, as the prototypes do,
and a model version built on it says so.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.db import transaction

from cass_keys import grids
from cass_keys.land import LandError
from cass_keys.settlement import SettlementError

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

#: Storage a grid's hazard takes, per cell and per thousand simulated years.
#: Measured on 17 September 2026 on the live platform, on the Jakarta-Bandung
#: calculation -- 962 cells, 10,000 years, PuSGeN 2024, motion stored from
#: 0.05 g (ADR 21) -- whose datastore was 233 MB, whose footprint, compressed,
#: was 38 MB, and whose package footprint was 108 MB. That region has the
#: strongest shaking CASS has computed, and quieter cells record less, so these
#: are ceilings for a national grid rather than averages.
DATASTORE_KB_PER_CELL = 24.3
FOOTPRINT_KB_PER_CELL = 4.0
PACKAGE_KB_PER_CELL = 11.2


class GridBuildError(Exception):
    """Raised when a specification cannot be read, built or registered."""


def maximum_cells() -> int:
    return int(getattr(settings, "CASS_MAX_GRID_CELLS", 500_000))


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
            domain=domain_from(document.get("domain")),
        )
    except grids.GridSpecificationError as exc:
        raise GridBuildError(str(exc)) from None


def domain_from(value: Any) -> grids.Domain:
    """Read the domain a specification keeps. Absent, it keeps every cell of its tiles."""
    if value in (None, ""):
        return grids.Domain()
    if not isinstance(value, dict):
        raise GridBuildError("The domain must be an object.")
    try:
        return grids.Domain(
            clip_to_land=_flag(value, "clip_to_land"),
            coast_buffer_km=_optional_decimal(value, "coast_buffer_km", default=Decimal("5")),
            skip_unsettled=_flag(value, "skip_unsettled"),
            settlement_buffer_km=_optional_decimal(
                value, "settlement_buffer_km", default=Decimal("5")
            ),
        )
    except grids.GridSpecificationError as exc:
        raise GridBuildError(str(exc)) from None


def estimated_cells(specification: grids.GridSpecification) -> int:
    """An upper bound on the cells, from the boxes alone.

    Counts a refined cell and the base cell it replaces, and every cell the
    domain would remove. That is the right direction to be wrong in for the one
    thing it is still used for: deciding whether counting exactly is affordable.
    """
    return grids.candidate_upper_bound(specification)


def storage(cells: int) -> dict[str, Any]:
    """What a grid of this many cells would store for its hazard, at most."""
    return {
        "hazard_set_mb_per_thousand_years": round(
            cells * (DATASTORE_KB_PER_CELL + FOOTPRINT_KB_PER_CELL) / 1000
        ),
        "package_mb_per_thousand_years": round(cells * PACKAGE_KB_PER_CELL / 1000),
        "basis": (
            "Measured on the Jakarta-Bandung calculation, the strongest shaking CASS "
            "has computed, so a ceiling: quieter cells store less. Storage grows in "
            "step with simulated years."
        ),
    }


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
    base, or off its lattice -- is named while the person is still in a position
    to change it.

    Where the specification is whole, the count is exact: the refinements
    replace the base cells beneath them and the domain removes its sea and its
    empty land, as the build will. Where it is not, the count is an upper bound
    from the boxes, and says so.
    """
    if not isinstance(document, dict):
        raise GridBuildError("A grid specification must be an object.")

    problems: list[str] = []
    incomplete = 0

    base = _resolution_or_none(document, "base_resolution_deg", "The base resolution", problems)

    tiles: list[grids.Tile] = []
    cells_from_tiles = 0
    for position, item in enumerate(_items(document, "tiles", required=False), start=1):
        name = _name_of(item, "Tile", position)
        stated = len(problems)
        box = _box_or_none(item, f"Tile {name!r}", problems)
        if box is None:
            # An area is either wrong, and named above, or simply not finished
            # being typed. Never reported as both.
            if len(problems) == stated:
                incomplete += 1
            continue
        tiles.append(grids.Tile(name=name, box=box))
        if base is not None:
            cells_from_tiles += _cells_in(box, base)

    refinements: list[grids.Refinement] = []
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
        refinements.append(grids.Refinement(name=name, box=box, resolution=resolution, reason=""))
        by_refinement.append(
            {"name": name, "resolution_deg": _plain(resolution), "cells": _cells_in(box, resolution)}
        )
    if base is not None:
        problems.extend(grids.refinement_problems(base, refinements))

    try:
        domain = domain_from(document.get("domain"))
    except GridBuildError as exc:
        problems.append(str(exc))
        domain = grids.Domain()

    upper = cells_from_tiles + sum(item["cells"] for item in by_refinement)
    counted: grids.CellCount | None = None
    uncovered = None
    country = str(document.get("country_code") or "").strip().upper()
    if base is not None and tiles and not problems:
        if domain.clip_to_land and not country:
            problems.append(
                "Name the country before clipping to its land: the clip reads that "
                "country's outline."
            )
        elif upper <= grids.MAXIMUM_CANDIDATES:
            try:
                whole = grids.GridSpecification(
                    country_code=country or "XX",
                    version="estimate",
                    label="estimate",
                    base_resolution=base,
                    tiles=tuple(tiles),
                    refinements=tuple(refinements),
                    domain=domain,
                )
                counted = grids.count(whole)
                if domain.clip_to_land:
                    uncovered = grids.uncovered_land(whole)
            except (grids.GridSpecificationError, LandError, SettlementError) as exc:
                problems.append(str(exc))
                counted = None

    if counted is not None:
        refined = dict(counted.by_refinement)
        for item in by_refinement:
            item["cells"] = refined.get(item["name"], 0)
        cells = counted.cells
    else:
        cells = upper

    limit = maximum_cells()
    return {
        "cells": cells,
        "exact": counted is not None,
        "cells_from_tiles": counted.from_tiles if counted is not None else cells_from_tiles,
        "cells_by_refinement": by_refinement,
        "candidates": counted.candidates if counted is not None else upper,
        "removed_as_sea": counted.removed_as_sea if counted is not None else 0,
        "removed_as_unsettled": counted.removed_as_unsettled if counted is not None else 0,
        "uncovered_land": uncovered,
        "counted_tiles": len(tiles),
        "incomplete": incomplete,
        "problems": problems,
        "limit": limit,
        "within_limit": cells <= limit,
        # Where the count could not be made exact, it is the bound from the
        # boxes, which counts a refined cell and the base cell it replaces and
        # overstates by the area of the refinements, never understates.
        "is_upper_bound": counted is None and bool(by_refinement or domain.is_active),
        "estimated": base is not None and bool(tiles),
        "storage": storage(cells),
    }


def build(document: Any, *, actor=None) -> tuple[AreaPerilGrid, dict[str, Any]]:
    """Build and register the grid a specification describes.

    Idempotent by version, as the prototype registration is: rebuilding a
    version replaces its cell file and leaves the record in place, so one version
    never means two different sets of identifiers.

    The cells are counted before they are generated, and generated before the
    transaction opens: clipping a national grid to its land takes seconds, and a
    transaction held open for them would block the registry for as long.
    """
    specification = specification_from(document)
    limit = maximum_cells()
    upper = estimated_cells(specification)
    if upper > grids.MAXIMUM_CANDIDATES:
        raise GridBuildError(
            f"This specification spans about {upper:,} cells before anything is "
            f"removed, and at most {grids.MAXIMUM_CANDIDATES:,} can be generated. "
            "Resolution is quadratic: halving it quadruples the count. Coarsen the "
            "base resolution, or narrow the tiles, or refine a smaller area."
        )
    try:
        counted = grids.count(specification)
        if counted.cells > limit:
            raise GridBuildError(
                f"This specification would generate {counted.cells:,} cells and this "
                f"installation builds at most {limit:,}. Resolution is quadratic: "
                "halving it quadruples the count. Coarsen the base resolution, or "
                "narrow the tiles, or refine a smaller area."
            )
        cells = grids.build(specification)
    except (grids.GridSpecificationError, LandError, SettlementError) as exc:
        raise GridBuildError(str(exc)) from None

    with transaction.atomic():
        grid = register(specification, cells, counted=counted, actor=actor)
    return grid, summary(specification, cells, counted)


def register(
    specification: grids.GridSpecification,
    cells,
    *,
    counted: grids.CellCount | None = None,
    actor=None,
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
    recorded = {
        "label": specification.label,
        "base_resolution_deg": specification.base_resolution,
        "refined_resolution_deg": finest,
        "refinement_rule": refinement_rule(specification),
        "cell_count": len(cells),
        "specification": specification.as_dict(),
        # True only where the grid was clipped to land. Tiles alone are
        # rectangles, and a rectangle over an archipelago is mostly sea.
        "excludes_offshore": specification.domain.clip_to_land,
        "site_condition_source": "",
        "site_condition_fallback": NO_SITE_CONDITIONS,
        "border_policy": border_policy(specification.domain),
        "mapping_tolerance_km": specification.mapping_tolerance_km,
        "publication_state": PublicationState.DRAFT,
        "notes": notes(
            specification.notes,
            (*specification.open_questions, *domain_questions(specification.domain)),
            domain_note(specification, counted),
        ),
        "updated_by": actor,
    }
    grid, _ = AreaPerilGrid.objects.update_or_create(
        country_code=specification.country_code,
        version=specification.version,
        defaults=recorded,
        create_defaults={**recorded, "created_by": actor},
    )
    attach_grid_cells(
        grid,
        grids.to_csv(cells),
        filename=f"{grid.reference}-cells.csv",
        actor=actor,
    )
    return grid


def summary(
    specification: grids.GridSpecification, cells, counted: grids.CellCount
) -> dict[str, Any]:
    """What was built, in the terms the specification was written in."""
    refined = dict(counted.by_refinement)
    return {
        "specification": specification.as_dict(),
        "cells": len(cells),
        "cells_by_refinement": refined,
        "cells_at_base_resolution": len(cells) - sum(refined.values()),
        "removed_as_sea": counted.removed_as_sea,
        "removed_as_unsettled": counted.removed_as_unsettled,
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


def border_policy(domain: grids.Domain) -> str:
    if not domain.is_active:
        return BORDER_POLICY
    kept = []
    if domain.clip_to_land:
        kept.append(f"within {_plain(domain.coast_buffer_km)} km of the country's land")
    if domain.skip_unsettled:
        kept.append(
            f"within {_plain(domain.settlement_buffer_km)} km of a building or a resident"
        )
    return (
        f"{BORDER_POLICY} Only cells {' and '.join(kept)} are in the grid, so a "
        "location beyond that is reported the same way."
    )


def domain_questions(domain: grids.Domain) -> tuple[str, ...]:
    """What a domain cannot see, stated on every grid that uses one."""
    questions = []
    if domain.clip_to_land:
        questions.append(
            "The land clip follows Natural Earth's 1:10m outlines, which place a coast "
            "to about 5 km, leave out many small islands, and draw boundaries as they "
            "are held on the ground rather than as they are claimed."
        )
    if domain.skip_unsettled:
        questions.append(
            "Land more than the settlement buffer from any building or resident GHSL "
            "records is outside the grid. Remote infrastructure GHSL did not detect -- "
            "a mine, a dam, a pipeline station -- would be reported as outside it."
        )
    return tuple(questions)


def domain_note(
    specification: grids.GridSpecification, counted: grids.CellCount | None
) -> str:
    domain = specification.domain
    if not domain.is_active:
        return ""
    lines = ["Domain:"]
    if domain.clip_to_land:
        from cass_keys.land import SOURCE as land_source  # noqa: PLC0415

        lines.append(
            f"- Kept only cells touching {specification.country_code}'s land, widened "
            f"{_plain(domain.coast_buffer_km)} km at the coast ({land_source})."
            + (f" {counted.removed_as_sea:,} cells removed as sea." if counted else "")
        )
    if domain.skip_unsettled:
        from cass_keys.settlement import SOURCE as settlement_source  # noqa: PLC0415

        lines.append(
            f"- Kept only cells within {_plain(domain.settlement_buffer_km)} km of a "
            f"building or a resident ({settlement_source})."
            + (
                f" {counted.removed_as_unsettled:,} cells removed as empty land."
                if counted
                else ""
            )
        )
    return "\n".join(lines)


def notes(written: str, open_questions: tuple[str, ...], domain: str = "") -> str:
    lines = [written]
    if domain:
        lines.extend(["", domain])
    lines.extend(["", "Open questions:", *(f"- {item}" for item in open_questions)])
    return "\n".join(lines)


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


def _flag(document: dict[str, Any], name: str) -> bool:
    value = document.get(name, False)
    if not isinstance(value, bool):
        raise GridBuildError(f"The domain's {name} must be true or false.")
    return value


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


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")
