"""Building a country's area-peril grid on the platform (section 6).

The geometry has always been general; what was not was the way in. Two pilot
specifications were compiled into the image, so a grid was something CASS
shipped rather than something a modeller built for the country in front of them.
These hold the door open: a specification arrives, and what comes back is either
a registered grid whose cells the lookup can read, or a refusal naming what is
wrong with the specification.

The refusals are the interesting half. A refinement that does not refine, a
domain that covers nothing, and a resolution that would generate a grid nobody
could run are all specifications that look reasonable written down.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.modelregistry.assets import load_grid
from apps.modelregistry.models import AreaPerilGrid, PublicationState

from .conftest import API

pytestmark = pytest.mark.django_db

#: A first cut at a country CASS ships no prototype for: one tile over Luzon at
#: half a degree, with Metro Manila refined.
SPECIFICATION = {
    "country_code": "PH",
    "version": "0.1.0",
    "label": "Luzon prototype grid",
    "base_resolution_deg": "0.5",
    "mapping_tolerance_km": "0",
    "tiles": [
        {
            "name": "Luzon",
            "reason": "Where the book this was written for sits.",
            "min_latitude": "13",
            "max_latitude": "16",
            "min_longitude": "120",
            "max_longitude": "122",
        }
    ],
    "refinements": [
        {
            "name": "Metro Manila",
            "reason": "Exposure concentration.",
            "resolution_deg": "0.25",
            "min_latitude": "14.5",
            "max_latitude": "15",
            "min_longitude": "120.5",
            "max_longitude": "121",
        }
    ],
    "open_questions": ["No site conditions are attached to any cell."],
    "notes": "A first cut, written to be argued with.",
}


def build(client, **changes):
    return client.post(f"{API}/grids/build/", {**SPECIFICATION, **changes}, format="json")


def test_a_specification_becomes_a_registered_grid_with_its_cells(client_for, modeller):
    response = build(client_for(modeller))

    assert response.status_code == 201, response.data
    grid = AreaPerilGrid.objects.get(country_code="PH")
    assert grid.reference == "ph-grid-0.1.0"
    assert grid.publication_state == PublicationState.DRAFT
    # Twenty-four base cells, one of them replaced by four refined ones.
    assert response.data["summary"]["cells"] == 27
    assert response.data["summary"]["cells_by_refinement"] == {"Metro Manila": 4}
    assert grid.cell_count == 27


def test_the_record_says_what_the_specification_left_open(client_for, modeller):
    """Silence in a specification must not read as resolution."""
    build(client_for(modeller))

    grid = AreaPerilGrid.objects.get(country_code="PH")

    assert "Metro Manila" in grid.refinement_rule
    assert "site conditions" in grid.notes
    assert "never snapped" in grid.border_policy
    assert "absent rather than defaulted" in grid.site_condition_fallback


def test_the_cells_are_the_ones_the_lookup_reads(client_for, modeller):
    """Stored through the same door an uploaded cell file goes through."""
    build(client_for(modeller))

    cells = load_grid(AreaPerilGrid.objects.get(country_code="PH"))
    refined = cells.find(Decimal("14.6"), Decimal("120.6"))
    coarse = cells.find(Decimal("13.2"), Decimal("121.2"))

    assert refined is not None
    assert refined.max_latitude - refined.min_latitude == Decimal("0.25")
    assert coarse is not None
    assert coarse.max_latitude - coarse.min_latitude == Decimal("0.5")


def test_rebuilding_a_version_replaces_its_cells_rather_than_forking_it(
    client_for, modeller
):
    """Section 6: an area-peril identifier may not change meaning within a version."""
    first = build(client_for(modeller))
    again = build(client_for(modeller))

    assert again.status_code == 201
    assert AreaPerilGrid.objects.filter(country_code="PH").count() == 1
    assert again.data["summary"]["cells"] == first.data["summary"]["cells"]


# -- what it refuses -------------------------------------------------------------

def test_a_refinement_that_does_not_refine_is_refused(client_for, modeller):
    refused = build(
        client_for(modeller),
        refinements=[{**SPECIFICATION["refinements"][0], "resolution_deg": "0.5"}],
    )

    assert refused.status_code == 400
    assert "not finer" in refused.data["detail"]


def test_a_specification_with_no_tiles_covers_nothing(client_for, modeller):
    refused = build(client_for(modeller), tiles=[])

    assert refused.status_code == 400
    assert "covers nothing" in refused.data["detail"]


def test_a_grid_too_large_for_this_installation_is_refused_with_its_count(
    client_for, modeller, settings
):
    """Resolution is quadratic, and the refusal says so rather than timing out."""
    settings.CASS_MAX_GRID_CELLS = 100

    refused = build(client_for(modeller), base_resolution_deg="0.01", refinements=[])

    assert refused.status_code == 400
    assert "60,000 cells" in refused.data["detail"]
    assert "at most 100" in refused.data["detail"]
    assert "quadratic" in refused.data["detail"]
    assert not AreaPerilGrid.objects.filter(country_code="PH").exists()


def test_a_resolution_that_is_not_a_number_is_refused(client_for, modeller):
    refused = build(client_for(modeller), base_resolution_deg="coarse")

    assert refused.status_code == 400
    assert "not a number" in refused.data["detail"]


def test_a_tile_with_an_empty_extent_is_refused(client_for, modeller):
    refused = build(
        client_for(modeller),
        tiles=[{**SPECIFICATION["tiles"][0], "max_latitude": "13"}],
    )

    assert refused.status_code == 400
    assert "empty" in refused.data["detail"]


def test_building_a_grid_is_a_model_publisher_s_action(api):
    refused = api.post(f"{API}/grids/build/", SPECIFICATION, format="json")

    assert refused.status_code == 403


def test_the_build_is_audited_with_what_it_produced(client_for, modeller):
    from apps.audit.models import AuditEvent

    build(client_for(modeller))

    event = AuditEvent.objects.get(subject_type="area_peril_grid")
    assert event.action == "create"
    assert event.after_reference["cells"] == 27


def test_the_count_is_estimated_before_any_cell_is_generated():
    """The guard has to hold for a specification too big to generate."""
    from apps.modelregistry import grid_build

    specification = grid_build.specification_from(SPECIFICATION)

    # An upper bound: it counts the base cells the refinement replaces as well.
    assert grid_build.estimated_cells(specification) == 28
    # Three degrees by two at a hundredth of a degree: sixty thousand cells,
    # from arithmetic rather than from generating them.
    finer = grid_build.specification_from(
        {**SPECIFICATION, "base_resolution_deg": "0.01", "refinements": []}
    )
    assert grid_build.estimated_cells(finer) == 60_000


# -- what it would cost, before it costs it ---------------------------------
#
# Resolution is quadratic and a number typed into a box does not say so. These
# hold that the count a modeller sees while writing the specification is the
# same count the build guards against, so nothing changes under them when they
# press the button.

def estimate(client, **changes):
    return client.post(f"{API}/grids/estimate/", {**SPECIFICATION, **changes}, format="json")


def test_the_count_is_answered_before_anything_is_built(client_for, modeller):
    answer = estimate(client_for(modeller))

    assert answer.status_code == 200, answer.data
    # Exact, as the build will produce it: twenty-four base cells less the one
    # the refinement replaces, and the four refined cells that replace it.
    assert answer.data["cells"] == 27
    assert answer.data["exact"] is True
    assert answer.data["cells_from_tiles"] == 23
    assert answer.data["cells_by_refinement"] == [
        {"name": "Metro Manila", "resolution_deg": "0.25", "cells": 4}
    ]
    assert answer.data["within_limit"] is True
    assert not AreaPerilGrid.objects.filter(country_code="PH").exists()


def test_a_finer_resolution_shows_what_it_would_cost(client_for, modeller):
    """Halving the resolution quadruples the count, and it is visible at once."""
    coarse = estimate(client_for(modeller), base_resolution_deg="0.5", refinements=[])
    fine = estimate(client_for(modeller), base_resolution_deg="0.25", refinements=[])

    assert coarse.data["cells"] == 24
    assert fine.data["cells"] == 96


def test_a_specification_over_the_limit_is_said_to_be_over_it(
    client_for, modeller, settings
):
    settings.CASS_MAX_GRID_CELLS = 100

    answer = estimate(client_for(modeller), base_resolution_deg="0.01", refinements=[])

    assert answer.data["cells"] == 60_000
    assert answer.data["limit"] == 100
    assert answer.data["within_limit"] is False


def test_a_half_written_specification_still_counts_what_it_can(client_for, modeller):
    """It is answered while somebody is typing, so it cannot insist on a whole one."""
    answer = estimate(
        client_for(modeller),
        country_code="",
        version="",
        label="",
        tiles=[
            SPECIFICATION["tiles"][0],
            {"name": "Visayas", "min_latitude": "9", "max_latitude": "12"},
        ],
        refinements=[],
    )

    assert answer.status_code == 200, answer.data
    assert answer.data["cells"] == 24
    assert answer.data["counted_tiles"] == 1
    assert answer.data["incomplete"] == 1


def test_a_refinement_that_does_not_refine_is_named_while_it_can_be_changed(
    client_for, modeller
):
    answer = estimate(
        client_for(modeller),
        refinements=[{**SPECIFICATION["refinements"][0], "resolution_deg": "1"}],
    )

    assert answer.status_code == 200
    assert any("not finer" in problem for problem in answer.data["problems"])


def test_a_backwards_extent_is_reported_rather_than_counted(client_for, modeller):
    answer = estimate(
        client_for(modeller),
        tiles=[{**SPECIFICATION["tiles"][0], "max_latitude": "12"}],
        refinements=[],
    )

    assert answer.data["cells"] == 0
    assert any("empty" in problem for problem in answer.data["problems"])


def test_estimating_a_grid_is_a_model_publisher_s_action(api):
    refused = api.post(f"{API}/grids/estimate/", SPECIFICATION, format="json")

    assert refused.status_code == 403


# -- the limit, and what a grid costs -----------------------------------------

def test_the_installation_builds_grids_of_up_to_half_a_million_cells(client_for, modeller):
    """250,000 was a guard against a typo, never a measurement."""
    answer = estimate(client_for(modeller))
    assert answer.data["limit"] == 500_000


def test_the_estimate_says_what_the_hazard_would_store(client_for, modeller):
    answer = estimate(client_for(modeller))
    stored = answer.data["storage"]
    assert stored["hazard_set_mb_per_thousand_years"] == round(27 * (24.3 + 4.0) / 1000)
    assert "ceiling" in stored["basis"]


def test_an_exact_count_matches_the_build(client_for, modeller):
    client = client_for(modeller)
    counted = estimate(client).data["cells"]
    built = build(client)
    assert built.status_code == 201, built.data
    assert built.data["summary"]["cells"] == counted


# -- a refinement off the base lattice ----------------------------------------

def test_a_refinement_off_the_lattice_is_named_while_it_can_be_changed(client_for, modeller):
    answer = estimate(
        client_for(modeller),
        refinements=[{**SPECIFICATION["refinements"][0], "min_latitude": "14.6"}],
    )
    assert any("not multiples of the base resolution" in item for item in answer.data["problems"])
    assert answer.data["exact"] is False


def test_a_refinement_off_the_lattice_is_refused_by_the_build(client_for, modeller):
    refused = build(
        client_for(modeller),
        refinements=[{**SPECIFICATION["refinements"][0], "min_latitude": "14.6"}],
    )
    assert refused.status_code == 400
    assert "latitude 14.5 to 15" in refused.data["detail"]


# -- the domain ---------------------------------------------------------------

LUZON_LAND = {
    **SPECIFICATION,
    "base_resolution_deg": "0.1",
    "refinements": [],
    "domain": {"clip_to_land": True, "coast_buffer_km": "5"},
}


def test_clipping_to_land_is_counted_before_anything_is_built(client_for, modeller):
    answer = client_for(modeller).post(f"{API}/grids/estimate/", LUZON_LAND, format="json")

    assert answer.status_code == 200, answer.data
    assert answer.data["exact"] is True
    assert answer.data["removed_as_sea"] > 0
    assert answer.data["cells"] + answer.data["removed_as_sea"] == answer.data["candidates"]
    # One tile over part of Luzon leaves most of the Philippines uncovered.
    assert answer.data["uncovered_land"]["cells"] > 0


def test_a_clip_needs_a_country_to_clip_to(client_for, modeller):
    answer = client_for(modeller).post(
        f"{API}/grids/estimate/", {**LUZON_LAND, "country_code": ""}, format="json"
    )
    assert any("Name the country" in item for item in answer.data["problems"])


def test_a_clipped_grid_records_its_domain_and_what_it_cannot_see(client_for, modeller):
    built = client_for(modeller).post(
        f"{API}/grids/build/",
        {**LUZON_LAND, "domain": {**LUZON_LAND["domain"], "skip_unsettled": True}},
        format="json",
    )

    assert built.status_code == 201, built.data
    grid = AreaPerilGrid.objects.get(country_code="PH")
    assert grid.excludes_offshore is True
    assert grid.specification["domain"]["clip_to_land"] is True
    assert "Natural Earth" in grid.specification["domain"]["land_source"]
    assert "GHSL" in grid.specification["domain"]["settlement_source"]
    assert "removed as sea" in grid.notes
    assert "Remote infrastructure" in grid.notes
    assert "within 5 km of the country's land" in grid.border_policy
    assert built.data["summary"]["removed_as_sea"] > 0


def test_a_grid_without_a_domain_does_not_claim_to_exclude_the_sea(client_for, modeller):
    build(client_for(modeller))
    assert AreaPerilGrid.objects.get(country_code="PH").excludes_offshore is False


def test_a_domain_that_is_not_an_object_is_refused(client_for, modeller):
    refused = build(client_for(modeller), domain="land")
    assert refused.status_code == 400
    assert "domain must be an object" in refused.data["detail"]


# -- grids registered before the refusal ---------------------------------------

def test_registered_grids_are_checked_for_overlapping_cells(client_for, modeller):
    import io

    from django.core.management import call_command

    from apps.modelregistry.assets import attach_grid_cells

    build(client_for(modeller))
    straddling = AreaPerilGrid.objects.create(
        country_code="PH",
        version="0.0.1-old",
        label="Built before refinements had to align",
        base_resolution_deg=Decimal("0.1"),
        refined_resolution_deg=Decimal("0.025"),
        refinement_rule="test",
    )
    attach_grid_cells(
        straddling,
        (
            b"AreaPerilID,MinLatitude,MaxLatitude,MinLongitude,MaxLongitude,CountryCode,Offshore\n"
            b"1,0,0.1,0,0.1,PH,false\n"
            b"2,0.05,0.075,0,0.025,PH,false\n"
        ),
        filename="old-cells.csv",
        actor=modeller,
    )

    output = io.StringIO()
    call_command("check_grids", stdout=output)

    report = output.getvalue()
    assert "ph-grid-0.1.0: 27 cells, none overlapping." in report
    assert "ph-grid-0.0.1-old: 2 of 2 cells share ground" in report
    assert "1 grid(s) overlap." in report


# -- seeds ----------------------------------------------------------------------

def test_the_seeds_are_listed_for_anyone_building_a_grid(api):
    listed = api.get(f"{API}/grids/seeds/")

    assert listed.status_code == 200
    codes = {item["country_code"] for item in listed.data}
    assert codes == {"ID", "NP", "MV", "TR", "BD", "KW", "BT", "PH", "OM", "QA"}


def test_a_seed_loads_as_a_specification_the_builder_accepts(client_for, modeller):
    client = client_for(modeller)
    seed = client.get(f"{API}/grids/seeds/qa/")

    assert seed.status_code == 200, seed.data
    assert "measured" not in seed.data["specification"]
    counted = client.post(f"{API}/grids/estimate/", seed.data["specification"], format="json")
    assert counted.data["cells"] == seed.data["measured"]["cells"]

    built = client.post(f"{API}/grids/build/", seed.data["specification"], format="json")
    assert built.status_code == 201, built.data
    grid = AreaPerilGrid.objects.get(country_code="QA", version="1.0.0-seed")
    assert grid.cell_count == seed.data["measured"]["cells"]
    assert grid.excludes_offshore is True


def test_a_country_without_a_seed_is_not_found(api):
    missing = api.get(f"{API}/grids/seeds/fr/")
    assert missing.status_code == 404
    assert "Seeds exist for" in missing.data["detail"]


def test_the_countries_a_grid_can_be_clipped_to_are_listed(api):
    listed = api.get(f"{API}/grids/countries/")

    assert listed.status_code == 200
    by_code = {item["code"]: item for item in listed.data}
    assert len(by_code) > 230
    assert by_code["NP"]["name"] == "Nepal"
    assert by_code["NP"]["seeded"] is True
    assert by_code["FR"]["seeded"] is False
