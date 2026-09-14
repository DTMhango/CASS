"""Cohort B against a grid: does a coarse geocode support the cell it is given?

The fixture book has one Indonesian Cohort B location, a locality-precision
geocode in Bali. It sits in the Denpasar refinement of the Indonesian pilot
grid, whose cells are 0.025 degrees -- under three kilometres -- so a geocode
standing for a town-sized area cannot honestly be held to one of them. That is
the case this report exists to surface.
"""

from __future__ import annotations

import pytest

from apps.exposure import review
from apps.exposure.extract import import_portfolio
from apps.exposure.models import DecisionField

from . import fixture_model
from .conftest import API
from .test_promotion import as_template

pytestmark = pytest.mark.django_db


@pytest.fixture()
def batch(project, analyst):
    return import_portfolio(project, as_template(), filename="portfolio.xlsx", actor=analyst)


@pytest.fixture()
def model_version(db, modeller):
    return fixture_model.register("ID", actor=modeller)


def sensitivity_of(client, batch, **params):
    return client.get(f"{API}/portfolio-imports/{batch.id}/geocoding-sensitivity/", params)


def test_a_coarse_geocode_on_a_fine_grid_is_reported_as_unsupported(
    client_for, analyst, batch, model_version
):
    response = sensitivity_of(client_for(analyst), batch, model_version=str(model_version.id))

    assert response.status_code == 200, response.data
    assert response.data["cohort"] == "B"
    assert response.data["country"] == "ID"
    [location] = response.data["locations"]
    assert location["location"] == "B-COARSE/1"
    assert location["precision"] == "locality"
    assert location["stable"] is False
    assert len(location["cells_reached"]) > 1
    assert response.data["summary"]["unstable"] == 1


def test_the_buffers_are_assumptions_the_report_states_and_a_caller_can_change(
    client_for, analyst, batch, model_version
):
    """At no buffer at all the geocode is taken as a point, and it has one cell."""
    response = sensitivity_of(
        client_for(analyst),
        batch,
        model_version=str(model_version.id),
        buffer_locality_km="0",
    )

    assert response.data["buffers_km"]["locality"] == "0"
    assert response.data["buffers_km"]["admin"] == "25"
    [location] = response.data["locations"]
    assert location["points"] == 1
    assert location["stable"] is True
    assert "floor" in response.data["value_basis"]


def test_a_location_a_reviewer_moved_out_of_cohort_b_is_not_counted(
    client_for, analyst, batch, model_version
):
    coarse = batch.location_rows.get(business_id="B-COARSE")
    review.decide(
        coarse,
        field=DecisionField.COHORT,
        value="C",
        rationale="The locality is too broad to trust without a site visit.",
        actor=analyst,
    )

    response = sensitivity_of(client_for(analyst), batch, model_version=str(model_version.id))

    assert response.data["summary"]["assessed"] == 0


def test_the_grid_can_be_named_directly(client_for, analyst, batch, model_version):
    response = sensitivity_of(client_for(analyst), batch, grid=str(model_version.grid_id))

    assert response.status_code == 200, response.data
    assert response.data["grid"] == "id-grid-0.1.0-draft"


def test_without_a_grid_there_is_nothing_to_measure_against(client_for, analyst, batch):
    response = sensitivity_of(client_for(analyst), batch)

    assert response.status_code == 400
    assert "how fine the cells are" in response.data["detail"]


def test_an_unknown_grid_is_not_found(client_for, analyst, batch):
    response = sensitivity_of(
        client_for(analyst), batch, grid="00000000-0000-0000-0000-000000000000"
    )

    assert response.status_code == 404


def test_a_nonsense_buffer_is_refused(client_for, analyst, batch, model_version):
    response = sensitivity_of(
        client_for(analyst),
        batch,
        model_version=str(model_version.id),
        buffer_admin_km="-3",
    )

    assert response.status_code == 409
    assert "admin" in response.data["detail"]
