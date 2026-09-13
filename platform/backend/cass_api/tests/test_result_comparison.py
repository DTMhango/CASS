"""Comparing two governed results.

Milestone M6 is completing the analyst journey and comparing two governed runs.
Section 9 adds what makes a comparison worth saving: the drivers behind a
change, not only its size.

The arithmetic is asserted exactly rather than approximately. ADR 5 keeps money
as ``Decimal`` on the server precisely so that it can be, and a comparison that
was only approximately right would be the first place that guarantee quietly
stopped holding.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.results.models import ResultSet, ResultState
from apps.runs.models import Run, RunKind

from .conftest import API

pytestmark = pytest.mark.django_db


def make_result(project, analyst, **overrides) -> ResultSet:
    run = Run.objects.create(
        kind=RunKind.ANALYSIS, project=project, label="run", created_by=analyst
    )
    values = {
        "run": run,
        "project": project,
        "label": "Baseline",
        "perspective": "ground_up",
        "state": ResultState.APPROVED,
        "average_annual_loss": Decimal("1000000.00"),
        "standard_deviation": Decimal("250000.00"),
        "currency": "USD",
        "return_period_losses": {"100": "5000000.00", "250": "9000000.00"},
        "model_version_reference": "id-eq-0.1.0",
        "assumption_set_reference": "id-baseline-1.0",
        "valuation_date": date(2026, 6, 30),
        "created_by": analyst,
    }
    values.update(overrides)
    return ResultSet.objects.create(**values)


def compare(api, project, baseline, candidate, label="Release impact"):
    return api.post(
        f"{API}/comparisons/",
        {
            "project": str(project.id),
            "label": label,
            "baseline": str(baseline.id),
            "candidate": str(candidate.id),
        },
        format="json",
    )


# -- the arithmetic ---------------------------------------------------------

def test_the_difference_is_computed_by_the_server_not_supplied(api, project, analyst):
    """A caller cannot post a number and have it stored as the finding."""
    baseline = make_result(project, analyst)
    candidate = make_result(
        project, analyst, label="Candidate", average_annual_loss=Decimal("1250000.00")
    )

    response = api.post(
        f"{API}/comparisons/",
        {
            "project": str(project.id),
            "label": "Release impact",
            "baseline": str(baseline.id),
            "candidate": str(candidate.id),
            "differences": {"average_annual_loss": "nonsense"},
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    aal = next(
        item
        for item in response.data["differences"]["metrics"]
        if item["metric"] == "average_annual_loss"
    )
    assert aal["change"] == "250000.00"
    assert aal["relative_change"] == "0.250000"
    assert aal["direction"] == "increase"


def test_a_fall_is_reported_as_a_fall(api, project, analyst):
    baseline = make_result(project, analyst)
    candidate = make_result(
        project, analyst, label="Candidate", average_annual_loss=Decimal("750000.00")
    )

    differences = compare(api, project, baseline, candidate).data["differences"]

    aal = differences["metrics"][0]
    assert aal["change"] == "-250000.00"
    assert aal["direction"] == "decrease"


def test_a_baseline_of_zero_reports_the_change_but_not_a_proportion(
    api, project, analyst
):
    """A move from zero is a change of kind, not an infinite percentage."""
    baseline = make_result(project, analyst, average_annual_loss=Decimal("0.00"))
    candidate = make_result(
        project, analyst, label="Candidate", average_annual_loss=Decimal("400000.00")
    )

    aal = compare(api, project, baseline, candidate).data["differences"]["metrics"][0]

    assert aal["change"] == "400000.00"
    assert aal["relative_change"] is None


def test_the_exceedance_curve_is_compared_only_where_both_sides_report_it(
    api, project, analyst
):
    """A period one side never reported is listed, not treated as zero."""
    baseline = make_result(
        project, analyst, return_period_losses={"100": "5000000.00", "250": "9000000.00"}
    )
    candidate = make_result(
        project,
        analyst,
        label="Candidate",
        return_period_losses={"250": "9900000.00", "500": "14000000.00"},
    )

    periods = compare(api, project, baseline, candidate).data["differences"][
        "return_periods"
    ]

    assert [row["return_period"] for row in periods["shared"]] == ["250"]
    assert periods["shared"][0]["change"] == "900000.00"
    assert periods["only_in_baseline"] == ["100"]
    assert periods["only_in_candidate"] == ["500"]


def test_return_periods_are_ordered_numerically_not_lexically(api, project, analyst):
    losses = {"1000": "1.00", "100": "2.00", "250": "3.00"}
    baseline = make_result(project, analyst, return_period_losses=losses)
    candidate = make_result(
        project, analyst, label="Candidate", return_period_losses=losses
    )

    periods = compare(api, project, baseline, candidate).data["differences"][
        "return_periods"
    ]

    assert [row["return_period"] for row in periods["shared"]] == ["100", "250", "1000"]


# -- the drivers ------------------------------------------------------------

def test_a_changed_model_version_is_named_as_the_driver(api, project, analyst):
    baseline = make_result(project, analyst)
    candidate = make_result(
        project,
        analyst,
        label="Candidate",
        model_version_reference="id-eq-0.2.0",
        average_annual_loss=Decimal("1250000.00"),
    )

    differences = compare(api, project, baseline, candidate).data["differences"]

    drivers = {item["driver"] for item in differences["drivers"]}
    assert "model_version" in drivers
    assert differences["unexplained"] is False


def test_differing_exclusions_are_reported_as_scope_rather_than_loss(
    api, project, analyst
):
    """Two numbers covering different perils are not the same kind of number."""
    baseline = make_result(project, analyst, material_exclusions=["tsunami"])
    candidate = make_result(
        project,
        analyst,
        label="Candidate",
        material_exclusions=["tsunami", "liquefaction"],
    )

    drivers = compare(api, project, baseline, candidate).data["differences"]["drivers"]

    exclusions = next(item for item in drivers if item["driver"] == "material_exclusions")
    assert "liquefaction" in exclusions["candidate"]
    assert "scope rather than" in exclusions["note"]


def test_a_difference_nothing_recorded_explains_says_so(api, project, analyst):
    """An empty driver list reads as "no cause"; this says "no cause recorded"."""
    baseline = make_result(project, analyst)
    candidate = make_result(
        project, analyst, label="Candidate", average_annual_loss=Decimal("1100000.00")
    )

    differences = compare(api, project, baseline, candidate).data["differences"]

    assert differences["drivers"] == []
    assert differences["unexplained"] is True
    assert "exposure or the run settings" in differences["unexplained_note"]


# -- what a comparison refuses ----------------------------------------------

def test_two_perspectives_cannot_be_compared(api, project, analyst):
    """A ground-up against an insured produces a difference meaning nothing."""
    baseline = make_result(project, analyst)
    candidate = make_result(project, analyst, label="Candidate", perspective="insured")

    response = compare(api, project, baseline, candidate)

    assert response.status_code == 400
    assert "cannot be interpreted" in str(response.data)


def test_two_currencies_cannot_be_compared(api, project, analyst):
    baseline = make_result(project, analyst)
    candidate = make_result(project, analyst, label="Candidate", currency="IDR")

    response = compare(api, project, baseline, candidate)

    assert response.status_code == 400
    assert "Normalise to one currency" in str(response.data)


def test_a_result_cannot_be_compared_against_itself(api, project, analyst):
    baseline = make_result(project, analyst)

    response = compare(api, project, baseline, baseline)

    assert response.status_code == 400
    assert "no difference to report" in str(response.data)


def test_an_unapproved_side_makes_the_comparison_unusable_for_decisions(
    api, project, analyst
):
    """Section 9 keeps research output distinct, and this is where it would blend."""
    baseline = make_result(project, analyst)
    candidate = make_result(
        project, analyst, label="Candidate", state=ResultState.RESEARCH
    )

    decision_use = compare(api, project, baseline, candidate).data["differences"][
        "decision_use"
    ]

    assert decision_use["both_approved"] is False
    assert "not a decision number" in decision_use["warning"]


def test_someone_outside_the_project_cannot_read_a_comparison(
    api, client_for, outsider, project, analyst
):
    baseline = make_result(project, analyst)
    candidate = make_result(project, analyst, label="Candidate")
    created = compare(api, project, baseline, candidate)

    listed = client_for(outsider).get(f"{API}/comparisons/")

    assert created.status_code == 201
    assert [item["id"] for item in listed.data["results"]] == []


def test_editing_the_commentary_does_not_leave_a_stale_difference(
    api, project, analyst
):
    """The stored answer must keep matching what it answers about."""
    baseline = make_result(project, analyst)
    candidate = make_result(
        project, analyst, label="Candidate", average_annual_loss=Decimal("1250000.00")
    )
    other = make_result(
        project, analyst, label="Third", average_annual_loss=Decimal("2000000.00")
    )
    created = compare(api, project, baseline, candidate)

    updated = api.patch(
        f"{API}/comparisons/{created.data['id']}/",
        {"candidate": str(other.id), "commentary": "Re-pointed at the third run."},
        format="json",
    )

    assert updated.status_code == 200, updated.data
    aal = updated.data["differences"]["metrics"][0]
    assert aal["change"] == "1000000.00"
