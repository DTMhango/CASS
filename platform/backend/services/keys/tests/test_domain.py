"""Keeping only the cells a grid needs: land, and anywhere settled.

These read the data CASS ships -- Natural Earth's outlines and the GHSL
settlement layer -- rather than fixtures, because the claims worth testing are
claims about that data: that every country Natural Earth draws can be clipped
to, that a coast is kept however its cells fall, that Jakarta is settled and the
Sahara is not.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

numpy = pytest.importorskip("numpy")

from cass_keys import grids, land, settlement  # noqa: E402
from cass_keys.lookup import AreaPerilGrid  # noqa: E402
from cass_keys.pilot_grids import INDONESIA  # noqa: E402

_D = Decimal

#: The ten countries CASS ships a seed for.
SEEDED = ("ID", "NP", "MV", "TR", "BD", "KW", "BT", "PH", "OM", "QA")


def box(min_lat, max_lat, min_lon, max_lon) -> grids.Box:
    return grids.Box(_D(min_lat), _D(max_lat), _D(min_lon), _D(max_lon))


def spec(**overrides) -> grids.GridSpecification:
    settings = {
        "country_code": "ID",
        "version": "test",
        "label": "Test grid",
        "base_resolution": _D("0.1"),
        "tiles": (grids.Tile("Java", box("-9", "-5.7", "105", "114.8")),),
    }
    settings.update(overrides)
    return grids.GridSpecification(**settings)


def cell_of(latitude: float, longitude: float, resolution: str) -> tuple[int, int]:
    step = float(resolution)
    return int(numpy.floor(latitude / step)), int(numpy.floor(longitude / step))


# -- the outlines ----------------------------------------------------------------------

def test_every_seeded_country_is_drawn_as_exactly_one_country():
    for code in SEEDED:
        outline = land.country(code)
        assert outline.rings, code
        assert outline.parts == (outline.name,), code


def test_a_code_natural_earth_does_not_draw_is_refused_with_why():
    with pytest.raises(land.LandError, match="no country with the code"):
        land.country("XX")


def test_every_country_natural_earth_draws_can_be_clipped_to():
    """Sweep them all rather than trusting the ten: shapes differ country to country.

    Each is rasterised at a degree, with no buffer, over its own bounds. Every
    country has land, so every one must keep at least one cell.
    """
    empty = []
    for code, outline in land.countries().items():
        mask = land.land_mask(
            code,
            _D("1"),
            _D("0"),
            int(numpy.floor(outline.min_latitude)),
            int(numpy.floor(outline.max_latitude)),
            int(numpy.floor(outline.min_longitude)),
            int(numpy.floor(outline.max_longitude)),
        )
        if not mask.kept.any():
            empty.append(code)
    assert len(land.countries()) > 230
    assert empty == []


def test_the_archive_is_the_one_the_code_was_written_against():
    land._archive.cache_clear()
    land._archive()


# -- touching, not centred -------------------------------------------------------------

def test_a_cell_the_outline_touches_is_kept_though_its_centre_is_outside():
    """Testing centres alone lost 9% of Indonesia's cells, all of them on a coast.

    A square island from 0.06 to 0.94 degrees on a 0.1-degree lattice: the cells
    along its edge have centres at 0.05 and 0.95, outside it, and every one of
    them is touched by it.
    """
    ring = numpy.array([[0.06, 0.06], [0.94, 0.06], [0.94, 0.94], [0.06, 0.94], [0.06, 0.06]])
    kept = land.touching(
        (ring,), _D("0.1"), first_row=-1, last_row=10, first_column=-1, last_column=10
    )
    island = kept[1:11, 1:11]
    assert island.all()
    # Nothing beyond it: the rows and columns either side stay sea.
    assert not kept[0, :].any() and not kept[:, 0].any()
    assert not kept[11, :].any() and not kept[:, 11].any()


def test_an_island_smaller_than_a_cell_is_kept():
    """The Maldives' islands are mostly smaller than any cell a grid would use."""
    ring = numpy.array([[0.041, 0.041], [0.043, 0.041], [0.043, 0.043], [0.041, 0.043], [0.041, 0.041]])
    kept = land.touching((ring,), _D("0.1"), first_row=0, last_row=0, first_column=0, last_column=0)
    assert kept[0, 0]


def test_land_west_of_the_window_still_counts_towards_inside():
    """Whether a cell is inside is decided by every crossing on its row.

    A window in the middle of a large island has no coastline in it at all, and
    must still read as land.
    """
    ring = numpy.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]])
    kept = land.touching((ring,), _D("0.1"), first_row=50, last_row=52, first_column=50, last_column=52)
    assert kept.all()


def test_the_coast_buffer_only_ever_adds_cells():
    resolution = _D("0.05")
    window = (-180, -110, 2100, 2300)
    none = land.land_mask("ID", resolution, _D("0"), *window).kept
    five = land.land_mask("ID", resolution, _D("5"), *window).kept
    assert (five | none).sum() == five.sum()
    assert five.sum() > none.sum()


def test_jakarta_is_land_and_the_java_sea_is_not():
    resolution = "0.1"
    jakarta = cell_of(-6.1754, 106.8272, resolution)
    java_sea = cell_of(-5.0, 110.0, resolution)
    mask = land.land_mask("ID", _D(resolution), _D("5"), -100, -40, 1000, 1200)
    assert mask.contains([jakarta[0]], [jakarta[1]])[0]
    assert not mask.contains([java_sea[0]], [java_sea[1]])[0]


def test_the_maldives_outline_is_incomplete_and_the_code_says_so():
    """176 islands drawn of about 1,190: why the Maldivian seed does not clip."""
    assert len(land.country("MV").rings) < 300
    assert "Maldives" in land.__doc__


def test_a_country_drawn_with_its_dependencies_is_named_for_itself():
    """France and Clipperton Island are both -99 in ISO_A2; the country leads."""
    assert land.country("FR").name == "France"
    assert land.country("AU").name == "Australia"


# -- screening a portfolio's coordinates -----------------------------------------------

def test_each_country_holds_its_own_capital_and_not_its_neighbours():
    capitals = {
        "ID": (-6.1754, 106.8272),  # Jakarta
        "NP": (27.7045, 85.3077),   # Kathmandu
        "BD": (23.8103, 90.4125),   # Dhaka
        "BT": (27.4728, 89.6390),   # Thimphu
        "QA": (25.2854, 51.5310),   # Doha
        "TR": (41.0082, 28.9784),   # Istanbul
        "LB": (33.8938, 35.5018),   # Beirut
    }
    for code, point in capitals.items():
        assert land.within(code, [point]) == [True], code
        others = [other for other in capitals.values() if other != point]
        assert land.within(code, others) == [False] * len(others), code


def test_the_outline_refuses_what_the_old_box_let_through():
    """Lucknow sat inside the box that screened Nepal; it is 90 km over the border."""
    assert land.within("NP", [(26.8467, 80.9462)]) == [False]


def test_a_coordinate_just_offshore_is_ashore_and_one_at_sea_is_not():
    # Tanjung Priok's quay, which a 1:10m coast places in the water, and the
    # middle of the Java Sea.
    assert land.within("ID", [(-6.1, 106.88), (-5.0, 110.0)]) == [True, False]


def test_an_offshore_platform_is_outside_and_left_to_a_person():
    """Halul Island's terminal is 90 km off Qatar: flagged, then confirmed in review."""
    assert land.within("QA", [(25.67, 52.41)]) == [False]


def test_every_territory_drawn_inside_another_country_is_screened_there():
    places = {
        "BQ": (12.15, -68.27), "BV": (-54.42, 3.36), "CC": (-12.19, 96.83),
        "CX": (-10.42, 105.68), "GF": (4.93, -52.33), "GP": (16.24, -61.53),
        "MQ": (14.60, -61.07), "RE": (-21.10, 55.50), "SJ": (78.22, 15.65),
        "TK": (-9.38, -171.22), "YT": (-12.78, 45.23),
    }
    assert set(places) == set(land.DRAWN_WITHIN)
    for code, point in places.items():
        assert code not in land.countries(), code
        assert land.within(code, [point]) == [True], code
        assert land.country_name(code) == land.DRAWN_WITHIN[code][1]


def test_every_officially_assigned_code_has_an_outline():
    """249 officially assigned ISO 3166-1 codes; Natural Earth draws Kosovo too."""
    covered = set(land.countries()) | set(land.DRAWN_WITHIN)
    assert len(covered - {"XK"}) == 249


def test_a_code_that_names_no_country_is_told_apart_from_a_coordinate_outside_one():
    assert land.within("UK", [(51.5, -0.1)]) is None
    assert land.outline_code("UK") is None
    assert land.within("GB", [(51.5, -0.1)]) == [True]


def test_the_maldives_is_screened_against_the_extent_of_its_islands():
    """Resorts sit on islands the 1:10m outline does not draw."""
    resorts_and_towns = [(4.175, 73.509), (-0.69, 73.15), (6.62, 73.07), (3.62, 72.72)]
    assert land.within("MV", resorts_and_towns) == [True] * 4
    assert land.within("MV", [(10.0, 73.0), (4.2, 80.0)]) == [False, False]


def test_screening_follows_the_portfolio_and_answers_in_order():
    points = [(-6.1754, 106.8272), (33.8938, 35.5018)] * 500
    assert land.within("ID", points) == [True, False] * 500
    assert land.within("ID", []) == []


def test_the_screen_the_cohort_rules_take_describes_itself():
    screen = land.CountryScreen()
    assert screen.buffer_km == land.DEFAULT_COAST_BUFFER_KM
    assert "Natural Earth" in screen.source
    assert screen.name("BD") == "Bangladesh"
    assert screen.within("BD", [(23.8103, 90.4125)]) == [True]


# -- the settlement layer --------------------------------------------------------------

def test_settled_places_are_settled_and_empty_ones_are_not():
    resolution = _D("0.05")
    for name, latitude, longitude, expected in (
        ("Jakarta", -6.1754, 106.8272, True),
        ("Kathmandu", 27.7045, 85.3077, True),
        ("Doha", 25.2854, 51.5310, True),
        ("Male", 4.1755, 73.5093, True),
        ("the Sahara", 23.0, 12.0, False),
        ("the Rub' al Khali", 20.0, 52.0, False),
        ("the Indian Ocean", -10.0, 80.0, False),
    ):
        row, column = cell_of(latitude, longitude, str(resolution))
        mask = settlement.settlement_mask(resolution, _D("0"), row, row, column, column)
        assert bool(mask.kept[0, 0]) is expected, name


def test_the_settlement_buffer_only_ever_adds_cells():
    resolution = _D("0.05")
    window = (400, 520, 1000, 1180)  # Oman
    none = settlement.settlement_mask(resolution, _D("0"), *window).kept
    five = settlement.settlement_mask(resolution, _D("5"), *window).kept
    assert (five | none).sum() == five.sum()
    assert five.sum() > none.sum()


def test_a_window_beyond_the_layer_reads_as_unsettled():
    mask = settlement.settlement_mask(_D("1"), _D("0"), 89, 89, 0, 3)
    assert not mask.kept.any()


def test_the_settlement_layer_is_the_one_the_code_was_written_against():
    settlement._layer.cache_clear()
    assert settlement.describe()["default_buffer_km"] == "5"


# -- grids with a domain ----------------------------------------------------------------

def test_clipping_to_land_removes_sea_and_keeps_jakarta():
    clipped = spec(domain=grids.Domain(clip_to_land=True))
    counted = grids.count(clipped)
    cells = grids.build(clipped)

    assert counted.cells == len(cells)
    assert counted.removed_as_sea > 0
    assert counted.candidates == len(cells) + counted.removed_as_sea
    grid = AreaPerilGrid(country_code="ID", version="t", cells=cells)
    assert grid.find(_D("-6.1754"), _D("106.8272")) is not None
    assert grid.find(_D("-5.0"), _D("110.0")) is None


def test_skipping_empty_land_counts_what_it_removed():
    domain = grids.Domain(clip_to_land=True, skip_unsettled=True, settlement_buffer_km=_D("0"))
    counted = grids.count(spec(domain=domain))
    assert counted.removed_as_unsettled > 0
    assert counted.cells + counted.removed_as_sea + counted.removed_as_unsettled == counted.candidates


def test_a_refinement_is_clipped_at_its_own_resolution():
    refined = spec(
        refinements=(
            grids.Refinement("Jakarta", box("-6.5", "-5.9", "106.5", "107.1"), _D("0.025"), "test"),
        ),
        domain=grids.Domain(clip_to_land=True, coast_buffer_km=_D("0")),
    )
    counted = grids.count(refined)
    jakarta = dict(counted.by_refinement)["Jakarta"]
    # 24 by 24 cells in the box, and Jakarta Bay takes some of them.
    assert 0 < jakarta < 576
    cells = grids.build(refined)
    assert grids.overlaps(cells)["overlapping_cells"] == 0


def test_a_domain_that_removes_everything_says_why():
    ocean = spec(
        tiles=(grids.Tile("open sea", box("-12", "-11", "90", "91")),),
        domain=grids.Domain(clip_to_land=True, coast_buffer_km=_D("0")),
    )
    with pytest.raises(grids.GridSpecificationError, match="domain removed every one"):
        grids.build(ocean)


def test_a_buffer_wider_than_a_tolerance_is_refused():
    with pytest.raises(grids.GridSpecificationError, match="between 0 and 50"):
        grids.Domain(clip_to_land=True, coast_buffer_km=_D("80"))


def test_land_the_tiles_leave_out_is_found_and_named():
    """The prototype's eight rectangles miss Natuna, Anambas, Talaud and Sangihe."""
    found = grids.uncovered_land(INDONESIA)
    assert found["cells"] > 50
    places = {(round(item["latitude"]), round(item["longitude"])) for item in found["examples"]}
    assert (4, 108) in places  # Natuna
