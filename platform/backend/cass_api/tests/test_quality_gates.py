"""The two scientific gates of section 7, and what keeps them gates.

A gate that passes itself is not a gate. Both of these produce their numbers
whether or not anybody has approved a reference, and both record acceptance as
undecided until somebody has — which is the platform's actual position and the
one a reviewer has to be able to see.

The other rule here is the one the platform applies everywhere else: the person
who registers a reference does not approve it. A benchmark somebody wrote and
then approved themselves is a number checking itself.
"""

from __future__ import annotations

import pytest

from apps.modelregistry import quality
from apps.modelregistry.models import (
    ConversionTolerances,
    HazardBenchmark,
    PublicationState,
)

from .conftest import API

pytestmark = pytest.mark.django_db

MEASUREMENTS = {
    "checks": [
        {
            "check": "probability_sum",
            "label": "Furthest a cell's bin probabilities sum from one",
            "value": 0.0004,
            "tolerance": None,
            "within_tolerance": None,
            "detail": "event 2 at cell 7 on PGA sums to 0.999600",
        },
        {
            "check": "clipped_share",
            "label": "Ground motion discarded above the top intensity bin",
            "value": 0.02,
            "tolerance": None,
            "within_tolerance": None,
            "detail": "200 of 10000 samples",
        },
    ],
    "problems": [],
}


def benchmark_body(**extra):
    body = {
        "country_code": "ID",
        "label": "PuSGeN 2024 published PGA",
        "source": "PuSGeN 2024 national seismic hazard map",
        "reference": "https://example.invalid/pusgen-2024",
        "points": [
            {"areaperil_id": 8056, "imt": "PGA", "return_period": 475, "intensity": 0.35}
        ],
        "tolerance": "0.2500",
    }
    body.update(extra)
    return body


def tolerance_body(**extra):
    body = {
        "label": "Pilot conversion tolerances",
        "source": "Converter acceptance judgement, pilot release",
        "values": {"probability_sum": 0.001, "clipped_share": 0.01},
    }
    body.update(extra)
    return body


# -- deciding measurements ---------------------------------------------------

def test_measurements_with_no_approved_tolerances_decide_nothing():
    decision = quality.decide(MEASUREMENTS, None)

    assert decision["decided"] is False
    assert decision["passed"] is None
    assert "gate stands open" in decision["reason"]


def test_measurements_are_judged_against_the_tolerances_approved_now():
    """The numbers were taken at conversion; the judgement is taken here."""
    decision = quality.decide(
        MEASUREMENTS, {"probability_sum": 0.001, "clipped_share": 0.05}
    )

    assert decision["decided"] is True
    assert decision["passed"] is True


def test_a_measurement_outside_its_tolerance_fails_and_is_named():
    decision = quality.decide(
        MEASUREMENTS, {"probability_sum": 0.001, "clipped_share": 0.01}
    )

    assert decision["passed"] is False
    assert decision["failed"] == ["clipped_share"]


def test_a_hazard_set_converted_before_the_measurements_existed_says_so():
    decision = quality.decide({}, {"probability_sum": 0.001})

    assert decision["decided"] is False
    assert "Convert it again to measure it" in decision["reason"]


def test_a_conversion_problem_stops_a_pass_whatever_the_tolerances_say():
    decision = quality.decide(
        {**MEASUREMENTS, "problems": ["The footprint does not validate."]},
        {"probability_sum": 0.001, "clipped_share": 0.05},
    )

    assert decision["passed"] is False


# -- the registries ----------------------------------------------------------

def test_a_benchmark_is_registered_as_a_candidate(client_for, modeller):
    created = client_for(modeller).post(
        f"{API}/hazard-benchmarks/", benchmark_body(), format="json"
    )

    assert created.status_code == 201, created.data
    assert created.data["publication_state"] == PublicationState.DRAFT
    assert created.data["is_approved"] is False
    assert created.data["point_count"] == 1


def test_a_benchmark_with_no_points_compares_nothing_and_is_refused(client_for, modeller):
    refused = client_for(modeller).post(
        f"{API}/hazard-benchmarks/", benchmark_body(points=[]), format="json"
    )

    assert refused.status_code == 400
    assert "compares nothing" in str(refused.data["points"])


def test_a_benchmark_point_missing_its_measure_is_refused(client_for, modeller):
    refused = client_for(modeller).post(
        f"{API}/hazard-benchmarks/",
        benchmark_body(points=[{"areaperil_id": 1, "return_period": 475, "intensity": 0.3}]),
        format="json",
    )

    assert refused.status_code == 400
    assert "must state" in str(refused.data["points"])


def test_whoever_registered_a_benchmark_may_not_approve_it(client_for, admin):
    """A reference somebody wrote and approved themselves checks nothing.

    An administrator can do both halves, which is exactly the account this has
    to be refused for: the rule is about the person, not the permission.
    """
    theirs = client_for(admin)
    created = theirs.post(f"{API}/hazard-benchmarks/", benchmark_body(), format="json")
    assert created.status_code == 201, created.data

    refused = theirs.post(f"{API}/hazard-benchmarks/{created.data['id']}/approve/")

    assert refused.status_code == 409
    assert "may not also approve it" in refused.data["detail"]


def test_an_approved_benchmark_is_frozen_and_becomes_the_one_in_force(
    client_for, modeller, reviewer
):
    created = client_for(modeller).post(
        f"{API}/hazard-benchmarks/", benchmark_body(), format="json"
    )

    approved = client_for(reviewer).post(
        f"{API}/hazard-benchmarks/{created.data['id']}/approve/"
    )

    assert approved.status_code == 200
    assert approved.data["is_approved"] is True
    record = HazardBenchmark.objects.get(id=created.data["id"])
    assert record.is_frozen is True
    assert quality.approved_benchmark("ID") == record


def test_a_tolerance_for_a_check_the_converter_does_not_measure_is_refused(
    client_for, modeller
):
    """An approved tolerance nobody checks reads as covered and is not."""
    refused = client_for(modeller).post(
        f"{API}/conversion-tolerances/",
        tolerance_body(values={"something_else": 0.1}),
        format="json",
    )

    assert refused.status_code == 400
    assert "not measured by the conversion" in str(refused.data["values"])


def test_an_approved_tolerance_set_is_the_one_conversions_are_judged_against(
    client_for, modeller, reviewer
):
    created = client_for(modeller).post(
        f"{API}/conversion-tolerances/", tolerance_body(), format="json"
    )
    client_for(reviewer).post(f"{API}/conversion-tolerances/{created.data['id']}/approve/")

    record = ConversionTolerances.objects.get(id=created.data["id"])
    assert quality.approved_tolerances() == record
    assert quality.tolerance_values(record) == {
        "probability_sum": 0.001,
        "clipped_share": 0.01,
    }


def test_the_registries_are_not_writable_by_an_analyst(api):
    refused = api.post(f"{API}/hazard-benchmarks/", benchmark_body(), format="json")

    assert refused.status_code == 403
