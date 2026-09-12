"""Multi-location allocation scenarios, run through to the model.

Step 6 of the integration order. The allocation arithmetic is already proved
exact elsewhere; what these tests hold is what happens *after* it -- that a
scenario moves value between area-peril cells without creating any, that the
comparison says where the assumption is economically live, and that the answer
is the same rows a promotion would produce rather than a parallel calculation
that could drift from it.

The materiality rule is the finding worth protecting. An allocation only
changes a loss where a business's sites reach different cells. Several sites
inside one cell see the same hazard and the same vulnerability function, so
dividing value between them is arithmetic and nothing more -- and telling an
analyst to agonise over an assumption that cannot move the answer wastes the
attention that the live ones need.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

import cass_extract as extract
from apps.exposure import scenarios
from apps.exposure.extract import import_extract
from apps.exposure.promotion import prepare, promote
from apps.modelregistry import pilot

from .conftest import API
from .test_promotion import as_workbook, oed_rows, structural_extract

pytestmark = pytest.mark.django_db

EQUAL = extract.AllocationMethod.EQUAL_LOCATION
PRIMARY = extract.AllocationMethod.PRIMARY_CONCENTRATED
CONCENTRATION = extract.AllocationMethod.CONCENTRATION


@pytest.fixture()
def batch(project, analyst):
    policies, locations = structural_extract()
    return import_extract(
        project,
        as_workbook(policies, locations).getvalue(),
        filename="extract.xlsx",
        actor=analyst,
    )


@pytest.fixture()
def pilot_model(db, modeller):
    return pilot.register("ID", actor=modeller)


@pytest.fixture()
def comparison(batch, pilot_model):
    return scenarios.compare(batch, model_version=pilot_model)


# -- the invariants a sensitivity has to hold -------------------------------------------

def test_every_scenario_carries_the_same_money(comparison):
    """Otherwise these are different portfolios, not a sensitivity."""
    assert comparison.total_holds is True
    assert len({item.total_tiv for item in comparison.scenarios}) == 1


def test_every_scenario_maps_completely_and_reconciles(comparison):
    for scenario in comparison.scenarios:
        assert scenario.reconciles is True
        assert scenario.mapped_tiv == scenario.total_tiv
        assert scenario.failed_tiv == 0


def test_value_moves_between_cells_rather_than_appearing(comparison):
    """The movements must net to nothing, or a scenario invented exposure."""
    for scenario in comparison.scenarios[1:]:
        movement = comparison.movement(scenario)
        assert movement, "a sensitivity that moves nothing is not a sensitivity"
        assert sum(movement.values(), Decimal("0.00")) == 0


def test_the_baseline_is_the_maximum_ignorance_allocation(comparison):
    """Section 5.3 makes the ordering meaningful, not cosmetic."""
    assert comparison.baseline.method is EQUAL
    assert comparison.baseline.baseline is True
    assert [item.baseline for item in comparison.scenarios[1:]] == [False]


def test_a_scenario_reports_where_the_value_sits(comparison):
    """TIV by cell is what a hazard set would be applied to."""
    for scenario in comparison.scenarios:
        assert scenario.by_area_peril
        assert sum(scenario.by_area_peril.values(), Decimal("0.00")) == scenario.mapped_tiv


# -- materiality ---------------------------------------------------------------------------

def test_the_comparison_says_which_businesses_the_choice_can_reach(comparison):
    """Four businesses in the benchmark; only the multi-site one is live."""
    material = {item.business_id for item in comparison.materiality if item.material}
    assert material == {"B-MULTI"}


def test_a_single_site_business_carries_no_allocation_assumption(comparison):
    single = next(
        item for item in comparison.materiality if item.business_id == "B-SINGLE"
    )
    assert single.location_count == 1
    assert single.material is False
    assert "no allocation assumption applies" in single.reason


def test_the_material_share_is_reported_as_a_share_of_value(comparison):
    report = comparison.as_dict()["materiality"]
    assert report["businesses_where_allocation_is_material"] == 1
    assert Decimal(report["material_tiv"]) < Decimal(report["total_tiv"])
    assert 0 < report["material_share"] < 1


def test_several_sites_in_one_cell_are_not_material():
    """The finding the rule exists for: the grid cannot tell them apart.

    Tested directly rather than through a portfolio, because the case matters
    most when it is a large business whose sites happen to sit close together
    -- and a fixture that produced it by accident would stop producing it the
    moment somebody changed a coordinate.
    """
    close = scenarios.BusinessMateriality(
        business_id="B-CLOSE",
        location_count=4,
        area_peril_count=1,
        total_tiv=Decimal("10000000.00"),
    )
    assert close.material is False
    assert "one area-peril cell" in close.reason
    assert "cannot change the loss" in close.reason


def test_sites_across_cells_are_material():
    spread = scenarios.BusinessMateriality(
        business_id="B-SPREAD",
        location_count=2,
        area_peril_count=2,
        total_tiv=Decimal("1000.00"),
    )
    assert spread.material is True
    assert "changes which hazard the value sees" in spread.reason


# -- the comparison describes what a promotion would do -------------------------------------

def test_the_prepared_rows_are_the_rows_a_promotion_writes(batch, analyst):
    """A comparison of a portfolio nobody could promote would be advice about nothing."""
    prepared = prepare(batch, allocation_method=PRIMARY)
    version = promote(batch, name="Primary", allocation_method=PRIMARY, actor=analyst)

    written = oed_rows(version)
    assert [row["AccNumber"] for row in written] == [
        row["AccNumber"] for row in prepared.rows
    ]
    assert [row["BuildingTIV"] for row in written] == [
        row["BuildingTIV"] for row in prepared.rows
    ]
    assert prepared.total_tiv == version.total_tiv


def test_preparing_creates_nothing(batch, project):
    """An analyst has to be able to try six scenarios without leaving six versions."""
    from apps.exposure.models import ExposureVersion

    before = ExposureVersion.objects.filter(project=project).count()
    for method in (EQUAL, PRIMARY):
        prepare(batch, allocation_method=method)
    assert ExposureVersion.objects.filter(project=project).count() == before


def test_the_comparison_holds_every_other_assumption_fixed(batch, pilot_model):
    """A difference the reader cannot attribute to the allocation is noise."""
    comparison = scenarios.compare(
        batch,
        model_version=pilot_model,
        methods=(EQUAL, PRIMARY),
        occupancy=extract.MIXED_COMMERCIAL,
    )
    assert comparison.total_holds
    # The taxonomy is deterministic, so both scenarios reach the same functions
    # and the only thing that can differ is where the value sits.
    assert {item.location_count for item in comparison.scenarios} == {6}


# -- choosing the scenarios ------------------------------------------------------------------

def test_a_caller_may_name_the_scenarios(batch, pilot_model):
    comparison = scenarios.compare(
        batch, model_version=pilot_model, methods=(PRIMARY, EQUAL)
    )
    assert [str(item.method) for item in comparison.scenarios] == [
        "primary_concentrated_v1",
        "equal_location_v1",
    ]
    assert comparison.baseline.method is PRIMARY


def test_concentration_is_refused_as_a_portfolio_scenario(batch, pilot_model):
    """It places one policy's value at one nominated site, so it is not one.

    Accepting it would need a nominated site for every policy at once, and
    inventing that choice is exactly what the envelope exists to avoid.
    """
    with pytest.raises(scenarios.ScenarioError) as excinfo:
        scenarios.compare(
            batch, model_version=pilot_model, methods=(EQUAL, CONCENTRATION)
        )
    message = str(excinfo.value)
    assert "concentration_v1" in message
    assert "envelope in this report already bounds that" in message


def test_the_envelope_names_every_cell_a_business_could_concentrate_into(comparison):
    """The brief's concentration envelope, in the terms the model works in."""
    envelope = comparison.as_dict()["envelope"]
    assert set(envelope) == {"B-MULTI"}
    assert envelope["B-MULTI"]["count"] == 2
    assert len(envelope["B-MULTI"]["candidate_area_perils"]) == 2
    assert Decimal(envelope["B-MULTI"]["total_tiv"]) > 0


def test_a_single_site_business_has_no_envelope(comparison):
    """There is nothing to bound where there is nothing to allocate."""
    assert "B-SINGLE" not in comparison.envelope


def test_a_repeated_scenario_is_run_once(batch, pilot_model):
    comparison = scenarios.compare(
        batch, model_version=pilot_model, methods=(EQUAL, PRIMARY, EQUAL)
    )
    assert len(comparison.scenarios) == 2


def test_a_comparison_with_no_scenario_is_refused(batch, pilot_model):
    with pytest.raises(scenarios.ScenarioError, match="at least one"):
        scenarios.compare(batch, model_version=pilot_model, methods=())


def test_the_comparison_is_reproducible(batch, pilot_model):
    first = scenarios.compare(batch, model_version=pilot_model).as_dict()
    second = scenarios.compare(batch, model_version=pilot_model).as_dict()
    assert first == second


# -- what the report says it is ---------------------------------------------------------------

def test_the_report_refuses_to_call_movement_a_loss_difference(comparison):
    """No hazard set exists, so a spread in exposure is not a spread in loss."""
    interpretation = comparison.as_dict()["interpretation"]
    assert "not a loss difference" in interpretation
    assert "no scenario is more likely than another" in interpretation.lower()


def test_the_report_names_the_grid_that_decided_materiality(comparison):
    """Materiality is a property of a grid version, so it has to travel with it."""
    report = comparison.as_dict()
    assert report["grid"] == "id-grid-0.1.0-draft"
    assert report["rule_version"] == scenarios.SCENARIO_RULE_VERSION
    assert report["allocation_rule_version"] == extract.ALLOCATION_RULE_VERSION


# -- through the API -----------------------------------------------------------------------------

def test_the_comparison_is_available_over_the_api(api, batch, pilot_model):
    response = api.get(
        f"{API}/portfolio-imports/{batch.id}/allocation-scenarios/"
        f"?model_version={pilot_model.id}"
    )
    assert response.status_code == 200, response.data
    assert len(response.data["scenarios"]) == 2
    assert response.data["total_holds_across_scenarios"] is True
    assert response.data["materiality"]["businesses_where_allocation_is_material"] == 1


def test_the_api_accepts_the_scenarios_to_run(api, batch, pilot_model):
    response = api.get(
        f"{API}/portfolio-imports/{batch.id}/allocation-scenarios/"
        f"?model_version={pilot_model.id}&method=primary_concentrated_v1"
        f"&method=equal_location_v1"
    )
    assert response.status_code == 200, response.data
    assert [item["method"] for item in response.data["scenarios"]] == [
        "primary_concentrated_v1",
        "equal_location_v1",
    ]


def test_a_comparison_without_a_model_says_why_one_is_needed(api, batch):
    response = api.get(f"{API}/portfolio-imports/{batch.id}/allocation-scenarios/")
    assert response.status_code == 400
    assert "needs a grid" in response.data["detail"]


def test_an_unknown_model_is_reported_rather_than_ignored(api, batch):
    import uuid

    response = api.get(
        f"{API}/portfolio-imports/{batch.id}/allocation-scenarios/"
        f"?model_version={uuid.uuid4()}"
    )
    assert response.status_code == 404


def test_an_unreadable_scenario_name_is_refused_with_a_reason(api, batch, pilot_model):
    response = api.get(
        f"{API}/portfolio-imports/{batch.id}/allocation-scenarios/"
        f"?model_version={pilot_model.id}&method=whatever_v1"
    )
    assert response.status_code == 409
    assert "whatever_v1" in response.data["detail"]
