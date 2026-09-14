"""Prototype grid specifications for the pilot countries.

These are drafts, and the code says so. Phase 0 of the build plan lists
defining the Indonesia adaptive grid as an open charter item with a named
owner; what is written here is a starting point for that conversation, not its
conclusion. Every specification carries the questions it does not answer, and
any grid built from one is published as a draft.

Nepal had a prototype here as well. It was test data, and it was removed once a
grid could be written for any country on the platform: a country somebody works
on is built from a specification, not compiled in.

The tiles approximate land. Indonesia is an archipelago and the plan asks its
grid to avoid unnecessary calculation points over ocean; without a coastline
mask the honest way to do that is to state which areas are modelled, so a
reviewer can see the shape being claimed and correct it. That is the single
largest open question on the Indonesian specification.

Refinements name the exposure centres the pilot portfolio actually sits in --
Jakarta, Bandung, Surabaya, Denpasar and Medan. That is deliberate
and it is a limitation: refining around today's exposure is a reasonable first
pass and a poor permanent rule, because it makes the grid depend on the book
rather than on the hazard. The charter has to settle a refinement rule based on
hazard gradient and site conditions, not on where the current portfolio happens
to be.
"""

from __future__ import annotations

from decimal import Decimal

from .grids import Box, GridSpecification, Refinement, Tile

_D = Decimal


def _box(min_lat: str, max_lat: str, min_lon: str, max_lon: str) -> Box:
    return Box(_D(min_lat), _D(max_lat), _D(min_lon), _D(max_lon))


#: Questions every pilot grid leaves open, whichever country it covers.
_SHARED_QUESTIONS = (
    "Site conditions are not represented. Vs30, soil class and basin parameters "
    "have no provenance, no fallback hierarchy and no sensitivity test yet, and "
    "no cell carries a site parameter.",
    "The refinement rule follows current exposure centres. A durable rule has to "
    "follow hazard gradient and site-condition gradient, or the grid depends on "
    "the book rather than on the hazard.",
    "Mapping tolerance is zero, so a location outside the domain is reported "
    "rather than snapped. The distance at which snapping would be acceptable, if "
    "any, is undecided.",
    "Border and coastline treatment is undecided beyond reporting. A location "
    "just outside a tile is reported as outside the domain.",
    "No hazard has been generated on these cells. They are geometry only.",
)

INDONESIA = GridSpecification(
    country_code="ID",
    version="0.1.0-draft",
    label="Indonesia earthquake prototype grid",
    base_resolution=_D("0.1"),
    tiles=(
        Tile(
            "Sumatra",
            _box("-6.2", "6.0", "94.8", "106.2"),
            "Sunda megathrust and Sumatran fault exposure.",
        ),
        Tile(
            "Java and Madura",
            _box("-9.0", "-5.7", "105.0", "114.8"),
            "The largest concentration of insured exposure in the country.",
        ),
        Tile("Bali and Nusa Tenggara", _box("-11.0", "-8.0", "114.4", "125.5"), ""),
        Tile(
            "Bangka Belitung",
            _box("-3.4", "-1.2", "105.0", "108.6"),
            "Added after mapping the pilot portfolio: a real location fell "
            "between the Sumatra and Kalimantan tiles.",
        ),
        Tile("Kalimantan", _box("-4.3", "4.4", "108.8", "119.1"), "Lower seismicity."),
        Tile("Sulawesi", _box("-6.1", "2.0", "118.6", "125.5"), ""),
        Tile("Maluku", _box("-8.5", "2.5", "125.5", "135.2"), ""),
        Tile("Papua", _box("-9.2", "0.0", "130.8", "141.1"), ""),
    ),
    refinements=(
        Refinement(
            "Jakarta",
            _box("-6.5", "-5.9", "106.5", "107.1"),
            _D("0.025"),
            "The largest exposure centre, on soft basin sediments.",
        ),
        Refinement(
            "Bandung",
            _box("-7.1", "-6.7", "107.4", "107.8"),
            _D("0.025"),
            "Material exposure in a basin with strong site effects.",
        ),
        Refinement(
            "Surabaya",
            _box("-7.4", "-7.1", "112.6", "112.9"),
            _D("0.025"),
            "Material industrial exposure.",
        ),
        Refinement("Denpasar", _box("-8.8", "-8.5", "115.1", "115.4"), _D("0.025"), ""),
        Refinement("Medan", _box("3.4", "3.8", "98.5", "98.9"), _D("0.025"), ""),
    ),
    mapping_tolerance_km=_D("0"),
    open_questions=(
        "The tiles approximate the archipelago with rectangles and therefore "
        "include open water inside their bounds and exclude land outside them. A "
        "coastline mask, or a tighter tiling, is the largest outstanding item on "
        "this specification.",
        *_SHARED_QUESTIONS,
    ),
    notes=(
        "Draft geometry for the phase 3 grid design spike. Not an approved model "
        "asset and not usable for a decision."
    ),
)

PILOT_GRIDS: dict[str, GridSpecification] = {
    INDONESIA.country_code: INDONESIA,
}


def specification(country_code: str) -> GridSpecification:
    try:
        return PILOT_GRIDS[country_code.upper()]
    except KeyError:
        raise KeyError(
            f"No prototype grid specification for {country_code!r}. Available: "
            + ", ".join(sorted(PILOT_GRIDS))
            + "."
        ) from None
