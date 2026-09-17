"""The grid specifications CASS ships for its first ten countries.

Each seed was measured with the builder when it was written, and those counts
are stored with it. Building every seed again here holds them to that, so a
change to the builder, the outlines or the settlement layer that moves a seed's
cells has to be noticed and the seed re-measured on purpose.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytest.importorskip("numpy")

from cass_keys import grids, seeds  # noqa: E402
from cass_keys.lookup import AreaPerilGrid  # noqa: E402

_D = Decimal

LIMIT = 500_000


def specification_of(code: str) -> grids.GridSpecification:
    document = seeds.specification(code)
    domain = document["domain"]
    return grids.GridSpecification(
        country_code=document["country_code"],
        version=document["version"],
        label=document["label"],
        base_resolution=_D(document["base_resolution_deg"]),
        tiles=tuple(
            grids.Tile(item["name"], _box(item), item["reason"]) for item in document["tiles"]
        ),
        refinements=tuple(
            grids.Refinement(item["name"], _box(item), _D(item["resolution_deg"]), item["reason"])
            for item in document["refinements"]
        ),
        open_questions=tuple(document["open_questions"]),
        notes=document["notes"],
        domain=grids.Domain(
            clip_to_land=domain["clip_to_land"],
            coast_buffer_km=_D(domain["coast_buffer_km"]),
            skip_unsettled=domain["skip_unsettled"],
            settlement_buffer_km=_D(domain["settlement_buffer_km"]),
        ),
    )


def _box(item) -> grids.Box:
    return grids.Box(
        _D(item["min_latitude"]),
        _D(item["max_latitude"]),
        _D(item["min_longitude"]),
        _D(item["max_longitude"]),
    )


def test_the_ten_countries_are_seeded():
    assert set(seeds.countries()) == {"ID", "NP", "MV", "TR", "BD", "KW", "BT", "PH", "OM", "QA"}


@pytest.mark.parametrize("code", ["ID", "NP", "MV", "TR", "BD", "KW", "BT", "PH", "OM", "QA"])
def test_each_seed_builds_the_cells_it_was_measured_at(code):
    specification = specification_of(code)
    recorded = seeds.measured(code)

    counted = grids.count(specification)

    assert counted.cells == recorded["cells"]
    assert counted.cells <= LIMIT
    assert counted.removed_as_sea == recorded["removed_as_sea"]
    assert counted.removed_as_unsettled == recorded["removed_as_unsettled"]
    assert dict(counted.by_refinement) == recorded["cells_by_refinement"]


@pytest.mark.parametrize("code", ["ID", "NP", "TR", "BD", "KW", "BT", "PH", "OM", "QA"])
def test_each_clipped_seed_leaves_none_of_its_country_s_land_uncovered(code):
    """A tile list drawn by hand leaves islands out; each seed was checked, and is held to it."""
    found = grids.uncovered_land(specification_of(code))
    assert found["cells"] == 0, found["examples"]


def test_every_seed_states_what_it_leaves_open_and_the_rule_it_follows():
    for code in seeds.countries():
        document = seeds.specification(code)
        assert any("Site conditions" in item for item in document["open_questions"]), code
        assert "resolution" in document["notes"], code
        assert all(item["reason"] for item in document["tiles"]), code
        assert all(item["reason"] for item in document["refinements"]), code


def test_the_maldives_seed_does_not_clip_to_land_and_says_why():
    document = seeds.specification("MV")
    assert document["domain"]["clip_to_land"] is False
    assert document["domain"]["skip_unsettled"] is True
    assert any("176" in item for item in document["open_questions"])
    assert any("tsunami" in item for item in document["open_questions"])


def test_the_indonesian_seed_finds_its_cities_at_the_refined_resolution():
    cells = grids.build(specification_of("ID"))
    grid = AreaPerilGrid(country_code="ID", version="seed", cells=cells)
    jakarta = grid.find(_D("-6.1754"), _D("106.8272"))
    papua = grid.find(_D("-2.5337"), _D("140.7181"))  # Jayapura
    assert jakarta.max_latitude - jakarta.min_latitude == _D("0.0125")
    assert papua.max_latitude - papua.min_latitude == _D("0.025")
    assert grid.find(_D("-5.0"), _D("110.0")) is None  # the Java Sea


def test_a_seed_is_a_copy_the_caller_may_change():
    first = seeds.specification("QA")
    first["tiles"].clear()
    assert seeds.specification("QA")["tiles"]


def test_a_country_without_a_seed_is_named_with_the_ones_that_have_one():
    with pytest.raises(seeds.SeedError, match="Seeds exist for BD, BT, ID"):
        seeds.specification("FR")


def test_the_catalogue_lists_each_seed_with_its_cells():
    listed = {item["country_code"]: item for item in seeds.catalogue()}
    assert listed["ID"]["base_resolution_deg"] == "0.025"
    assert listed["ID"]["refinements"] == 10
    assert listed["QA"]["cells"] == seeds.measured("QA")["cells"]
