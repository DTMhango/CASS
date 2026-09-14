"""A coarse geocode, tried across the area it stands for.

The grid here is four one-degree cells, about 111 km across, so a five-kilometre
buffer is small against a cell and what decides stability is how close the
coordinate sits to an edge -- which is exactly the question the report exists
to answer.
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from cass_keys import grids, sensitivity
from cass_keys.lookup import AreaPerilGrid

_D = Decimal


@pytest.fixture()
def grid() -> AreaPerilGrid:
    specification = grids.GridSpecification(
        country_code="ID",
        version="test",
        label="Four cells",
        base_resolution=_D("1"),
        tiles=(grids.Tile("square", grids.Box(_D("0"), _D("2"), _D("0"), _D("2"))),),
    )
    return AreaPerilGrid(country_code="ID", version="test", cells=grids.build(specification))


def assessed(grid, latitude, longitude, radius="5", precision="locality"):
    return sensitivity.assess(
        grid,
        location="LOC",
        latitude=_D(latitude),
        longitude=_D(longitude),
        precision=precision,
        radius_km=_D(radius),
    )


# -- sampling ------------------------------------------------------------------

def test_a_zero_buffer_samples_only_the_recorded_coordinate():
    points = sensitivity.sample_points(_D("0.5"), _D("0.5"), _D("0"))
    assert points == ((_D("0.5"), _D("0.5")),)


def test_the_samples_start_at_the_coordinate_and_stay_inside_the_buffer():
    latitude, longitude = _D("-6.2"), _D("106.8")
    points = sensitivity.sample_points(latitude, longitude, _D("5"))

    assert points[0] == (latitude, longitude)
    assert len(points) == 1 + sensitivity.RINGS * sensitivity.BEARINGS
    scale = math.cos(math.radians(float(latitude)))
    for point_latitude, point_longitude in points:
        north = float(point_latitude - latitude) * sensitivity.KM_PER_DEGREE
        east = float(point_longitude - longitude) * sensitivity.KM_PER_DEGREE * scale
        assert math.hypot(north, east) <= 5.0 + 1e-3


def test_the_same_location_always_gives_the_same_points():
    first = sensitivity.sample_points(_D("27.7"), _D("85.3"), _D("25"))
    second = sensitivity.sample_points(_D("27.7"), _D("85.3"), _D("25"))
    assert first == second


def test_a_negative_buffer_is_refused():
    with pytest.raises(sensitivity.SensitivityError, match="negative"):
        sensitivity.sample_points(_D("0"), _D("0"), _D("-1"))


# -- one location ----------------------------------------------------------------

def test_a_location_well_inside_a_cell_is_stable(grid):
    result = assessed(grid, "0.5", "0.5")

    assert result.stable
    assert result.cells_reached == (result.recorded_cell,)
    assert result.share_in_recorded_cell == 1.0


def test_a_location_near_a_cell_edge_reaches_its_neighbour(grid):
    """Within a kilometre or so of the edge, a town-sized area straddles two cells."""
    result = assessed(grid, "0.99", "0.5")

    assert not result.stable
    assert len(result.cells_reached) == 2
    assert 0 < result.points_in_recorded_cell < result.points


def test_a_buffer_crossing_the_edge_of_the_grid_says_so(grid):
    result = assessed(grid, "0.01", "0.5")

    assert result.points_outside_grid > 0
    assert not result.stable


def test_a_location_outside_the_grid_is_never_stable(grid):
    result = assessed(grid, "5", "5", radius="0")

    assert result.recorded_cell is None
    assert not result.stable


def test_a_wider_buffer_is_less_stable(grid):
    """The same coordinate is stable as a locality and not as a district."""
    assert assessed(grid, "0.9", "0.5", radius="5").stable
    assert not assessed(grid, "0.9", "0.5", radius="25").stable


# -- the report ------------------------------------------------------------------

def rows():
    return [
        {"location": "MIDDLE", "latitude": "0.5", "longitude": "0.5",
         "precision": "locality", "tiv": "1000"},
        {"location": "EDGE", "latitude": "0.99", "longitude": "0.5",
         "precision": "postcode", "tiv": "250"},
        {"location": "STREET", "latitude": "1.5", "longitude": "1.5",
         "precision": "street", "tiv": "75"},
        {"location": "NOWHERE", "latitude": None, "longitude": None,
         "precision": "admin", "tiv": "10"},
    ]


def test_the_report_separates_the_value_on_unstable_locations(grid):
    summary = sensitivity.report(grid, rows()).summary()

    assert (summary["assessed"], summary["stable"], summary["unstable"]) == (2, 1, 1)
    assert summary["stable_tiv"] == "1000"
    assert summary["unstable_tiv"] == "250"
    assert summary["by_precision"]["postcode"]["unstable_tiv"] == "250"
    # One location wholly in its cell and one partly: the mean says how much.
    assert 0.5 < summary["mean_share_in_recorded_cell"] < 1.0


def test_a_precision_with_no_buffer_is_unassessed_rather_than_guessed(grid):
    unassessed = sensitivity.report(grid, rows()).as_dict()["unassessed"]

    reasons = {item["location"]: item["reason"] for item in unassessed}
    assert "street" in reasons["STREET"]
    assert "no coordinate" in reasons["NOWHERE"]


def test_the_buffers_used_are_recorded_and_can_be_overridden(grid):
    document = sensitivity.report(grid, rows(), buffers_km={"postcode": "0.5"}).as_dict()

    assert document["buffers_km"] == {"admin": "25", "locality": "5", "postcode": "0.5"}
    assert document["sampling"] == {
        "rings": sensitivity.RINGS,
        "bearings": sensitivity.BEARINGS,
    }
    # Half a kilometre is inside the one kilometre to the edge.
    assert document["summary"]["unstable"] == 0


@pytest.mark.parametrize("value", ["-1", "wide", "NaN"])
def test_a_nonsense_buffer_is_refused(grid, value):
    with pytest.raises(sensitivity.SensitivityError):
        sensitivity.report(grid, rows(), buffers_km={"locality": value})
