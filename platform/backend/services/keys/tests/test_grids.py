"""Building a fixed, versioned, adaptive area-peril grid.

Section 6 requires an area-peril identifier to keep its meaning within a grid
version, and the mapping work package asks for a spatial index rather than a
linear scan. Both are properties rather than features, so most of what follows
asserts that the same specification always produces the same answer and that a
coordinate lands in exactly one cell.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cass_keys import grids
from cass_keys.lookup import AreaPerilGrid
from cass_keys.pilot_grids import INDONESIA, PILOT_GRIDS, specification

_D = Decimal


def box(min_lat, max_lat, min_lon, max_lon) -> grids.Box:
    return grids.Box(_D(min_lat), _D(max_lat), _D(min_lon), _D(max_lon))


def simple(**overrides) -> grids.GridSpecification:
    settings = {
        "country_code": "ID",
        "version": "test",
        "label": "Test grid",
        "base_resolution": _D("1"),
        "tiles": (grids.Tile("square", box("0", "2", "0", "2")),),
    }
    settings.update(overrides)
    return grids.GridSpecification(**settings)


# -- geometry -------------------------------------------------------------------

def test_a_tile_becomes_cells_of_the_base_resolution():
    cells = grids.build(simple())
    assert len(cells) == 4
    assert {cell.area_peril_id for cell in cells} == {1, 2, 3, 4}


def test_a_refinement_replaces_the_base_cells_beneath_it():
    """Adding them on top would let a coordinate fall in two cells."""
    cells = grids.build(
        simple(
            refinements=(
                grids.Refinement("centre", box("0", "1", "0", "1"), _D("0.5"), "test"),
            )
        )
    )
    # Three base cells survive; the fourth is replaced by four finer ones.
    assert len(cells) == 7

    grid = AreaPerilGrid(country_code="ID", version="test", cells=cells)
    found = [
        cell
        for cell in cells
        if cell.contains(_D("0.25"), _D("0.25"))
    ]
    assert len(found) == 1
    assert grid.find(_D("0.25"), _D("0.25")).max_latitude == _D("0.5")


def test_cell_edges_sit_on_a_global_lattice():
    """Two tiles that meet share an edge rather than interleaving."""
    cells = grids.build(
        simple(
            base_resolution=_D("0.5"),
            tiles=(
                grids.Tile("left", box("0", "1", "0", "0.5")),
                grids.Tile("right", box("0", "1", "0.5", "1")),
            ),
        )
    )
    assert len({(c.min_latitude, c.min_longitude) for c in cells}) == len(cells)
    edges = {cell.min_longitude for cell in cells}
    assert edges == {_D("0"), _D("0.5")}


def test_the_same_specification_always_numbers_cells_the_same_way():
    """Section 6: an identifier must not change meaning within a version."""
    first = grids.build(simple())
    second = grids.build(simple())
    assert [(c.area_peril_id, c.min_latitude, c.min_longitude) for c in first] == [
        (c.area_peril_id, c.min_latitude, c.min_longitude) for c in second
    ]


def test_a_specification_with_no_tiles_is_refused():
    with pytest.raises(grids.GridSpecificationError, match="covers\nnothing|names no tiles"):
        simple(tiles=())


def test_a_refinement_that_does_not_refine_is_a_specification_error():
    with pytest.raises(grids.GridSpecificationError, match="does not refine"):
        simple(
            base_resolution=_D("0.1"),
            refinements=(
                grids.Refinement("same", box("0", "1", "0", "1"), _D("0.1"), "test"),
            ),
        )


def test_an_inverted_box_is_refused():
    with pytest.raises(grids.GridSpecificationError, match="empty"):
        box("2", "1", "0", "1")


# -- refinements that keep a coordinate in exactly one cell ---------------------------
#
# Measured on the builder before these refusals existed: a 0.025-degree
# refinement edged at 0.06 over a 0.1 base put a quarter of its points in two
# cells, and one edged at 0.05 put three fifths in none.

def test_a_refinement_off_the_base_lattice_is_refused_with_the_box_that_is_on_it():
    with pytest.raises(grids.GridSpecificationError) as refused:
        simple(
            base_resolution=_D("0.1"),
            refinements=(
                grids.Refinement("off", box("0.06", "0.14", "0.06", "0.14"), _D("0.025"), "test"),
            ),
        )
    message = str(refused.value)
    assert "not multiples of the base resolution 0.1" in message
    assert "latitude 0 to 0.2, longitude 0 to 0.2" in message


def test_a_refinement_the_base_does_not_divide_is_refused():
    with pytest.raises(grids.GridSpecificationError, match="whole number of times"):
        simple(
            base_resolution=_D("0.1"),
            refinements=(
                grids.Refinement("thirds", box("0", "0.2", "0", "0.2"), _D("0.03"), "test"),
            ),
        )


def test_refinements_overlapping_at_different_resolutions_are_refused():
    with pytest.raises(grids.GridSpecificationError, match="overlap at different resolutions"):
        simple(
            base_resolution=_D("1"),
            refinements=(
                grids.Refinement("a", box("0", "1", "0", "1"), _D("0.5"), "test"),
                grids.Refinement("b", box("0", "1", "0", "1"), _D("0.25"), "test"),
            ),
        )


def test_an_aligned_grid_has_no_overlaps_and_no_gaps():
    cells = grids.build(
        simple(
            base_resolution=_D("0.1"),
            tiles=(grids.Tile("t", box("0", "0.2", "0", "0.2")),),
            refinements=(
                grids.Refinement("r", box("0.1", "0.2", "0.1", "0.2"), _D("0.025"), "test"),
            ),
        )
    )
    points = [
        (_D(y) / 1000 + _D("0.0005"), _D(x) / 1000 + _D("0.0005"))
        for y in range(0, 200, 5)
        for x in range(0, 200, 5)
    ]
    assert all(sum(cell.contains(*point) for cell in cells) == 1 for point in points)
    assert grids.overlaps(cells)["overlapping_cells"] == 0


def test_a_grid_built_before_the_refusal_is_found_to_overlap():
    """The check a registered grid is held to: cells that share ground are counted."""
    base = [
        grids.GridCell(1, _D("0"), _D("0.1"), _D("0"), _D("0.1"), "XX"),
        grids.GridCell(2, _D("0.1"), _D("0.2"), _D("0"), _D("0.1"), "XX"),
    ]
    straddling = grids.GridCell(3, _D("0.05"), _D("0.075"), _D("0"), _D("0.025"), "XX")
    report = grids.overlaps([*base, straddling])
    assert report["checked"] is True
    assert report["overlapping_cells"] == 2


def test_a_box_edged_south_of_the_equator_starts_on_the_right_row():
    """Decimal's // truncates towards zero, which started such a box a row too far north."""
    cells = grids.build(
        simple(base_resolution=_D("0.1"), tiles=(grids.Tile("south", box("-6.25", "-6.1", "106", "106.1")),))
    )
    assert min(cell.min_latitude for cell in cells) == _D("-6.3")


def test_a_specification_without_a_domain_builds_exactly_what_it_always_did():
    """Builder 1.1.0 must number the prototype's cells as 1.0.0 did (section 6)."""
    import hashlib

    payload = grids.to_csv(grids.build(INDONESIA))
    assert hashlib.sha256(payload).hexdigest() == (
        "620559f370767d0a1550286d59df5b9303dc77c902c16e6559405ab8245eb4c2"
    )


def test_the_count_is_the_cells_the_build_produces():
    specification = simple(
        base_resolution=_D("0.5"),
        tiles=(
            grids.Tile("a", box("0", "2", "0", "2")),
            grids.Tile("overlapping", box("1", "3", "1", "3")),
        ),
        refinements=(grids.Refinement("r", box("0", "1", "0", "1"), _D("0.25"), "test"),),
    )
    assert grids.count(specification).cells == len(grids.build(specification))


# -- the lookup index -------------------------------------------------------------

def test_a_southern_coordinate_finds_its_cell():
    """Decimal // 1 truncates toward zero, so -6.2 would bucket as -6 not -7.

    Most of Indonesia is south of the equator, so getting this wrong would have
    reported the whole country as outside the grid.
    """
    cells = grids.build(
        simple(tiles=(grids.Tile("south", box("-7", "-6", "106", "107")),))
    )
    grid = AreaPerilGrid(country_code="ID", version="test", cells=cells)
    assert grid.find(_D("-6.2088"), _D("106.8456")) is not None


def test_a_cell_larger_than_a_degree_is_still_found():
    """The index registers a cell in every bucket it touches."""
    cells = grids.build(simple(base_resolution=_D("5"), tiles=(
        grids.Tile("big", box("0", "5", "0", "5")),
    )))
    grid = AreaPerilGrid(country_code="ID", version="test", cells=cells)
    assert grid.find(_D("3.5"), _D("2.5")) is not None


def test_a_coordinate_outside_the_domain_is_reported_rather_than_snapped():
    grid = AreaPerilGrid(country_code="ID", version="test", cells=grids.build(simple()))
    assert grid.find(_D("50"), _D("50")) is None


def test_the_index_agrees_with_a_linear_scan():
    """The optimisation must not change the answer."""
    cells = grids.build(INDONESIA)
    grid = AreaPerilGrid(country_code="ID", version="t", cells=cells)
    for point in [
        (_D("-6.2088"), _D("106.8456")),
        (_D("-7.2575"), _D("112.7521")),
        (_D("-4.0"), _D("138.0")),
        (_D("0"), _D("0")),
    ]:
        by_scan = next((c for c in cells if c.contains(*point)), None)
        assert grid.find(*point) is by_scan


# -- the pilot specifications --------------------------------------------------------

def test_the_pilot_grid_states_what_it_leaves_open():
    """A grid must not quietly acquire the authority of a decision nobody made."""
    assert INDONESIA.open_questions
    assert any("Site conditions" in item for item in INDONESIA.open_questions)
    assert "not an approved model asset" in INDONESIA.notes.lower()


def test_the_pilot_grid_is_marked_a_draft():
    assert INDONESIA.version.endswith("-draft")


def test_the_pilot_grid_builds():
    indonesia = grids.build(INDONESIA)
    assert len(indonesia) > 40_000
    assert indonesia[0].country_code == "ID"


def test_jakarta_is_refined_and_a_sparse_area_is_not():
    grid = AreaPerilGrid(country_code="ID", version="t", cells=grids.build(INDONESIA))
    jakarta = grid.find(_D("-6.2088"), _D("106.8456"))
    papua = grid.find(_D("-4.0"), _D("138.0"))
    assert jakarta.max_latitude - jakarta.min_latitude == _D("0.025")
    assert papua.max_latitude - papua.min_latitude == _D("0.1")


def test_the_specification_lookup_names_what_is_available():
    assert specification("id") is INDONESIA
    assert set(PILOT_GRIDS) == {"ID"}
    with pytest.raises(KeyError, match="No prototype grid specification"):
        specification("NP")
    with pytest.raises(KeyError, match="No prototype grid specification"):
        specification("FR")


# -- the cell file ---------------------------------------------------------------------

def test_the_cell_file_matches_what_the_registry_reads():
    payload = grids.to_csv(grids.build(simple()))
    header = payload.decode("utf-8").splitlines()[0]
    assert header == (
        "AreaPerilID,MinLatitude,MaxLatitude,MinLongitude,MaxLongitude,"
        "CountryCode,Offshore"
    )


def test_coverage_reports_what_a_grid_does_not_reach():
    cells = grids.build(simple())
    report = grids.coverage(
        cells, [(_D("0.5"), _D("0.5")), (_D("50"), _D("50"))]
    )
    assert report["inside"] == 1
    assert report["outside"] == 1
    assert report["share_covered"] == 0.5
    assert report["outside_coordinates"] == ["50,50"]
