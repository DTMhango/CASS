"""Pairing a grid and a vulnerability set into a model version.

Both halves can now be built on the platform for any country. This is the step
that makes them runnable, and the step where the two halves are checked against
each other: a version carrying one country's buildings and another's ground
motion would calculate perfectly well and mean nothing.

What is assembled is a draft and a research prototype, and it says what both
halves left open, because that is what a result's caveats are read from.
"""

from __future__ import annotations

import pytest

from apps.modelregistry.models import (
    AreaPerilGrid,
    ModelVersion,
    PublicationState,
    VulnerabilitySet,
)

from .conftest import API

pytestmark = pytest.mark.django_db


@pytest.fixture()
def grid(db, modeller) -> AreaPerilGrid:
    from apps.modelregistry.assets import attach_grid_cells

    record = AreaPerilGrid.objects.create(
        country_code="PH",
        version="0.1.0",
        label="Luzon prototype grid",
        base_resolution_deg="0.500000",
        refined_resolution_deg="0.250000",
        cell_count=0,
        notes="A first cut.\n\nOpen questions:\n- No site conditions are attached.",
        created_by=modeller,
    )
    attach_grid_cells(
        record,
        b"AreaPerilID,MinLatitude,MaxLatitude,MinLongitude,MaxLongitude,CountryCode\n"
        b"1,13.0,13.5,120.0,120.5,PH\n",
        actor=modeller,
    )
    return record


@pytest.fixture()
def vulnerability_set(db, modeller) -> VulnerabilitySet:
    return VulnerabilitySet.objects.create(
        country_code="PH",
        version="0.1.0-gem",
        source="GEM Global Vulnerability Model v2026.0.0",
        function_count=484,
        imts_used=["PGA", "SA(0.3)"],
        licence="CC BY-NC-SA 4.0",
        licence_note="GEM Foundation has granted explicit permission.",
        created_by=modeller,
    )


def assemble(client, grid, vulnerability_set, **changes):
    body = {
        "grid": str(grid.id),
        "vulnerability_set": str(vulnerability_set.id),
        "version": "0.1.0",
        "label": "Philippines earthquake, assembled",
    }
    body.update(changes)
    return client.post(f"{API}/model-versions/assemble/", body, format="json")


def test_a_grid_and_a_vulnerability_set_become_a_runnable_version(
    client_for, modeller, grid, vulnerability_set
):
    response = assemble(client_for(modeller), grid, vulnerability_set)

    assert response.status_code == 201, response.data
    model = ModelVersion.objects.get(country_code="PH")
    assert model.grid_id == grid.id
    assert model.vulnerability_set_id == vulnerability_set.id
    assert model.imts == ["PGA", "SA(0.3)"]
    assert model.publication_state == PublicationState.DRAFT
    assert model.is_research_prototype is True


def test_it_says_what_it_does_not_model(client_for, modeller, grid, vulnerability_set):
    """Section 9: what a model omits is invisible in its output unless stated."""
    assemble(client_for(modeller), grid, vulnerability_set)

    scope = ModelVersion.objects.get(country_code="PH").peril_scope

    assert scope["QEQ"]["treatment"] == "included"
    assert scope["QTS"]["treatment"] == "excluded"
    assert "Vs30" in scope["site_response"]["rationale"]


def test_both_halves_limitations_are_carried_onto_the_pair(
    client_for, modeller, grid, vulnerability_set
):
    assemble(client_for(modeller), grid, vulnerability_set)

    limitations = ModelVersion.objects.get(country_code="PH").known_limitations

    assert "ph-grid-0.1.0" in limitations
    assert "No site conditions are attached" in limitations
    assert "not usable for a decision" in limitations


def test_halves_from_different_countries_are_refused(
    client_for, modeller, grid, vulnerability_set
):
    elsewhere = VulnerabilitySet.objects.create(
        country_code="ID",
        version="0.1.0-gem",
        source="GEM",
        imts_used=["PGA"],
        created_by=modeller,
    )

    refused = assemble(client_for(modeller), grid, elsewhere)

    assert refused.status_code == 400
    assert "another's ground motion" in refused.data["detail"]
    assert not ModelVersion.objects.exists()


def test_a_vulnerability_set_with_no_measures_is_refused(
    client_for, modeller, grid, vulnerability_set
):
    """Nothing would say which hazard a run should read."""
    vulnerability_set.imts_used = []
    vulnerability_set.save()

    refused = assemble(client_for(modeller), grid, vulnerability_set)

    assert refused.status_code == 400
    assert "intensity measures" in refused.data["detail"]


def test_an_id_that_is_not_registered_is_refused(
    client_for, modeller, grid, vulnerability_set
):
    refused = client_for(modeller).post(
        f"{API}/model-versions/assemble/",
        {"grid": str(grid.id), "vulnerability_set": "not-an-id", "version": "0.1.0"},
        format="json",
    )

    assert refused.status_code == 400
    assert "vulnerability set" in refused.data["detail"]


def test_assembling_twice_replaces_rather_than_forks(
    client_for, modeller, grid, vulnerability_set
):
    assemble(client_for(modeller), grid, vulnerability_set)
    again = assemble(client_for(modeller), grid, vulnerability_set, label="Renamed")

    assert again.status_code == 201
    assert ModelVersion.objects.filter(country_code="PH").count() == 1
    assert ModelVersion.objects.get(country_code="PH").label == "Renamed"


def test_assembling_is_a_model_publisher_s_action(api, grid, vulnerability_set):
    refused = api.post(
        f"{API}/model-versions/assemble/",
        {"grid": str(grid.id), "vulnerability_set": str(vulnerability_set.id), "version": "0.1.0"},
        format="json",
    )

    assert refused.status_code == 403


def test_the_assembly_is_audited_with_what_is_still_outstanding(
    client_for, modeller, grid, vulnerability_set
):
    from apps.audit.models import AuditEvent

    assemble(client_for(modeller), grid, vulnerability_set)

    event = AuditEvent.objects.get(subject_type="model_version", action="create")
    assert event.after_reference["grid"] == "ph-grid-0.1.0"
    # A version with no hazard set cannot be published, and the record says so.
    assert any("hazard" in blocker for blocker in event.after_reference["blockers"])
