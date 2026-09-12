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
from cass_keys.pilot_grids import INDONESIA, NEPAL, PILOT_GRIDS, specification

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
    cells = grids.build(NEPAL)
    grid = AreaPerilGrid(country_code="NP", version="t", cells=cells)
    for point in [
        (_D("27.7172"), _D("85.3240")),
        (_D("28.2096"), _D("83.9856")),
        (_D("26.4525"), _D("87.2718")),
        (_D("0"), _D("0")),
    ]:
        by_scan = next((c for c in cells if c.contains(*point)), None)
        assert grid.find(*point) is by_scan


# -- the pilot specifications --------------------------------------------------------

@pytest.mark.parametrize("spec", [INDONESIA, NEPAL])
def test_every_pilot_grid_states_what_it_leaves_open(spec):
    """A grid must not quietly acquire the authority of a decision nobody made."""
    assert spec.open_questions
    assert any("Site conditions" in item for item in spec.open_questions)
    assert "not an approved model asset" in spec.notes.lower()


@pytest.mark.parametrize("spec", [INDONESIA, NEPAL])
def test_every_pilot_grid_is_marked_a_draft(spec):
    assert spec.version.endswith("-draft")


def test_the_pilot_grids_build():
    indonesia = grids.build(INDONESIA)
    nepal = grids.build(NEPAL)
    assert len(indonesia) > 40_000
    assert len(nepal) > 3_000
    assert indonesia[0].country_code == "ID"
    assert nepal[0].country_code == "NP"


def test_kathmandu_is_refined_as_the_plan_asks():
    grid = AreaPerilGrid(country_code="NP", version="t", cells=grids.build(NEPAL))
    cell = grid.find(_D("27.7172"), _D("85.3240"))
    assert cell is not None
    assert cell.max_latitude - cell.min_latitude == _D("0.025")


def test_jakarta_is_refined_and_a_sparse_area_is_not():
    grid = AreaPerilGrid(country_code="ID", version="t", cells=grids.build(INDONESIA))
    jakarta = grid.find(_D("-6.2088"), _D("106.8456"))
    papua = grid.find(_D("-4.0"), _D("138.0"))
    assert jakarta.max_latitude - jakarta.min_latitude == _D("0.025")
    assert papua.max_latitude - papua.min_latitude == _D("0.1")


def test_the_specification_lookup_names_what_is_available():
    assert specification("id") is INDONESIA
    assert set(PILOT_GRIDS) == {"ID", "NP"}
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
