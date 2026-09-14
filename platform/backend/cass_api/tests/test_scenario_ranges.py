"""The range a number spans across the assumption scenarios run for it.

A range is only worth reporting if everything but the assumption is held still,
so most of what is held here is about what may not be brought into one: a
result on another model, perspective, run mode or ORD basis. The rest is that
the central estimate is the baseline, that each end names the scenario behind
it, and that the assumptions are ranked by how far they move the numbers.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.modelregistry.models import AssumptionSet
from apps.results.models import ResultSet, ResultState
from apps.results.ranges import scenario_range
from apps.runs.models import AnalysisRun, Run, RunKind

from .conftest import API
from .test_analysis_execution import model_version, published_exposure  # noqa: F401

pytestmark = pytest.mark.django_db

BASIS = {"average_loss": "sample", "ep_type": "AEP"}


@pytest.fixture()
def sets(modeller):
    return {
        flavour: AssumptionSet.objects.create(
            country_code="ID",
            flavour=flavour,
            version="0.1.0",
            label=flavour,
            created_by=modeller,
        )
        for flavour in ("baseline", "more_robust", "more_vulnerable")
    }


@pytest.fixture()
def run_under(project, analyst, published_exposure, model_version):  # noqa: F811
    """A published result for the book and model, under one assumption set."""

    def made(assumption_set=None, *, aal, returns, **overrides) -> ResultSet:
        run = Run.objects.create(
            kind=RunKind.ANALYSIS, project=project, label="run", created_by=analyst
        )
        AnalysisRun.objects.create(
            run=run,
            exposure_version=published_exposure,
            model_version=model_version,
            assumption_set=assumption_set,
            perspectives=["ground_up"],
            created_by=analyst,
        )
        values = {
            "run": run,
            "project": project,
            "label": "result",
            "perspective": "ground_up",
            "state": ResultState.RESEARCH,
            "average_annual_loss": Decimal(aal),
            "currency": "USD",
            "return_period_losses": returns,
            "model_version_reference": model_version.reference,
            "assumption_set_reference": (
                assumption_set.reference if assumption_set else "baseline weights"
            ),
            "run_mode": "technical",
            "calculation_digest": "sha256:same-settings",
            "valuation_date": date(2026, 6, 30),
            "uncertainty_attribution": {"ord_basis": BASIS},
            "created_by": analyst,
        }
        values.update(overrides)
        return ResultSet.objects.create(**values)

    return made


@pytest.fixture()
def three(run_under, sets):
    baseline = run_under(aal="1000.00", returns={"100": "5000.00", "250": "9000.00"})
    robust = run_under(
        sets["more_robust"], aal="850.00", returns={"100": "4400.00", "250": "7650.00"}
    )
    vulnerable = run_under(
        sets["more_vulnerable"], aal="1300.00", returns={"100": "6500.00", "250": "12600.00"}
    )
    return baseline, robust, vulnerable


def metric(document, name):
    return next(item for item in document["metrics"] if item["metric"] == name)


def test_the_baseline_is_the_centre_and_each_end_names_its_scenario(three):
    baseline, _, _ = three

    document = scenario_range(baseline)

    assert document["central"]["scenario"] == "baseline"
    aal = metric(document, "average_annual_loss")
    assert aal["central"] == "1000.00"
    assert (aal["low"]["scenario"], aal["low"]["value"]) == ("more_robust", "850.00")
    assert (aal["high"]["scenario"], aal["high"]["value"]) == ("more_vulnerable", "1300.00")
    assert aal["spread"] == "450.00"
    assert aal["relative_spread"] == "0.450000"
    assert aal["most_influential"]["scenario"] == "more_vulnerable"


def test_the_assumptions_are_ranked_by_how_far_they_move_any_number(three):
    """More vulnerable moves the 250-year loss 40%; more robust moves nothing over 15%."""
    baseline, _, _ = three

    influence = scenario_range(baseline)["influence"]

    assert [item["scenario"] for item in influence] == ["more_vulnerable", "more_robust"]
    assert influence[0]["largest_relative_change"] == "0.400000"
    assert influence[1]["largest_relative_change"] == "0.150000"


def test_the_range_is_the_same_whichever_scenario_asks(three):
    _, robust, _ = three

    document = scenario_range(robust)

    assert document["central"]["scenario"] == "baseline"
    assert metric(document, "return_period_250")["spread"] == "4950.00"


def test_nothing_run_on_other_terms_is_brought_into_the_range(run_under, sets, model_version):  # noqa: F811
    """Another model, perspective, run mode or basis would make the spread mean something else."""
    baseline = run_under(aal="1000.00", returns={"100": "5000.00"})
    run_under(sets["more_vulnerable"], aal="9999.00", returns={}, model_version_reference="id-other")
    run_under(sets["more_robust"], aal="1.00", returns={}, perspective="insured")
    run_under(sets["more_robust"], aal="2.00", returns={}, run_mode="research")
    run_under(
        sets["more_vulnerable"],
        aal="3.00",
        returns={},
        uncertainty_attribution={"ord_basis": {"average_loss": "analytical"}},
    )

    document = scenario_range(baseline)

    assert [item["scenario"] for item in document["scenarios"]] == ["baseline"]
    assert document["metrics"] == []
    assert "no range" in document["note"]


def test_a_run_calculated_under_other_settings_is_left_out_and_counted(run_under, sets):
    """Otherwise a settings difference would be credited to the assumption."""
    baseline = run_under(aal="1000.00", returns={})
    run_under(sets["more_vulnerable"], aal="1300.00", returns={}, calculation_digest="sha256:other")
    run_under(sets["more_robust"], aal="900.00", returns={}, calculation_digest="")

    document = scenario_range(baseline)

    assert [item["scenario"] for item in document["scenarios"]] == ["baseline"]
    assert document["left_out"] == {"calculated_differently": 1, "calculation_not_recorded": 1}
    assert "2 other result(s)" in document["note"]


def test_a_result_from_before_the_digest_has_no_range(run_under, sets):
    older = run_under(aal="1000.00", returns={}, calculation_digest="")
    run_under(sets["more_vulnerable"], aal="1300.00", returns={}, calculation_digest="")

    document = scenario_range(older)

    assert document["metrics"] == []
    assert "before CASS recorded how" in document["note"]


def test_a_result_that_predates_the_mode_copy_is_read_through_its_run(run_under, sets):
    """A blank run mode is not recorded, not a different mode."""
    baseline = run_under(aal="1000.00", returns={})
    run_under(sets["more_vulnerable"], aal="1300.00", returns={}, run_mode="")

    document = scenario_range(baseline)

    assert [item["scenario"] for item in document["scenarios"]] == ["baseline", "more_vulnerable"]


def test_the_latest_run_stands_for_a_scenario_but_a_result_stands_for_itself(run_under, sets):
    baseline = run_under(aal="1000.00", returns={})
    older = run_under(sets["more_vulnerable"], aal="1300.00", returns={})
    ResultSet.objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(days=1))
    run_under(sets["more_vulnerable"], aal="1400.00", returns={})

    from_baseline = metric(scenario_range(baseline), "average_annual_loss")
    older.refresh_from_db()
    from_older = metric(scenario_range(older), "average_annual_loss")

    assert from_baseline["high"]["value"] == "1400.00"
    assert from_older["high"]["value"] == "1300.00"


def test_without_a_baseline_the_centre_is_the_result_and_says_so(run_under, sets):
    robust = run_under(sets["more_robust"], aal="850.00", returns={})
    run_under(sets["more_vulnerable"], aal="1300.00", returns={})

    document = scenario_range(robust)

    assert document["central"] == {
        "scenario": "more_robust",
        "label": document["central"]["label"],
        "result": str(robust.id),
        "is_baseline": False,
    }
    assert "No baseline run" in document["note"]


def test_a_set_whose_flavour_is_baseline_is_the_baseline(run_under, sets):
    named = run_under(sets["baseline"], aal="1000.00", returns={})
    run_under(sets["more_vulnerable"], aal="1100.00", returns={})

    assert scenario_range(named)["central"]["is_baseline"] is True


def test_the_results_api_serves_the_range(api, three):
    baseline, _, _ = three

    served = api.get(f"{API}/results/{baseline.id}/scenario-range/")

    assert served.status_code == 200
    assert served.data["varies"] == "assumption set"
    assert served.data["not_varied"]
    assert len(served.data["scenarios"]) == 3
