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
    # The same upper bound the guard uses: base cells, and the refined cells
    # that will replace one of them.
    assert answer.data["cells"] == 28
    assert answer.data["cells_from_tiles"] == 24
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
