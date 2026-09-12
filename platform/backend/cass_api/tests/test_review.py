"""The eligibility and review interface, and what a decision is allowed to be.

Work package 2's rule is one sentence and it decides the shape of everything
here: an analyst may change a cohort decision "only by recording a rationale",
and the change "creates a new derived exposure version; it does not edit the
source artifact". So the tests that matter are about what a decision cannot do
-- it cannot edit a staged row, it cannot be recorded without a reason, and it
cannot quietly become the source's own answer once applied.

The storey tests carry the other half. Height is the one attribute worth
collecting while a person is already looking at a risk, because it is the
difference between a class that resolves to one Oasis function and one that
spans four and cannot be answered at all. These check that a reviewed height
reaches the OED file and that the exposure version says it was reviewed rather
than reported.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.exposure import review
from apps.exposure.models import DecisionField, ReviewDecision, ReviewState

from .test_promotion import as_template, oed_rows  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture()
def batch(project, analyst):
    from apps.exposure.extract import import_portfolio

    return import_portfolio(
        project, as_template(), filename="portfolio.xlsx", actor=analyst
    )


@pytest.fixture()
def location(batch):
    return batch.location_rows.first()


# -- a decision is a record, not an edit ------------------------------------------------

def test_a_decision_does_not_touch_the_staged_row(location, analyst):
    """The batch must keep saying what the workbook said."""
    before = location.cohort
    review.decide(
        location,
        field=DecisionField.COHORT,
        value="A",
        rationale="Confirmed against the site survey photographs.",
        actor=analyst,
    )
    location.refresh_from_db()
    assert location.cohort == before
    assert review.overlay(location).cohort == "A"


def test_a_decision_without_a_reason_is_refused(location, analyst):
    """The rationale is what separates a correction from a preference."""
    with pytest.raises(review.ReviewError, match="rationale"):
        review.decide(
            location,
            field=DecisionField.COHORT,
            value="A",
            rationale="ok",
            actor=analyst,
        )
    assert ReviewDecision.objects.count() == 0


def test_a_decision_that_changes_nothing_is_refused(location, analyst):
    """It would put a rationale in the trail for a change that never happened."""
    with pytest.raises(review.ReviewError, match="changes nothing"):
        review.decide(
            location,
            field=DecisionField.COHORT,
            value=location.cohort,
            rationale="Looks right to me on reflection.",
            actor=analyst,
        )


def test_the_last_decision_stands_and_the_earlier_ones_are_kept(location, analyst):
    """An audit that saw only the answer could not tell considered from hasty."""
    review.decide(
        location,
        field=DecisionField.STOREYS,
        value=4,
        rationale="Counted from the street-level photograph.",
        actor=analyst,
    )
    review.decide(
        location,
        field=DecisionField.STOREYS,
        value=6,
        rationale="Broker confirmed two further floors above the parapet.",
        actor=analyst,
    )
    assert review.overlay(location).storeys == 6
    assert len(review.history(location)) == 2
    assert review.history(location)[0]["to"] == "4"


def test_the_history_records_who_decided_and_why(location, analyst):
    review.decide(
        location,
        field=DecisionField.STOREYS,
        value=3,
        rationale="Three floors visible in the survey report of March 2026.",
        actor=analyst,
    )
    entry = review.history(location)[0]
    assert entry["field"] == "storeys"
    assert entry["decided_by"] == analyst.email
    assert "survey report" in entry["rationale"]


# -- what may be decided -----------------------------------------------------------------

def test_coordinates_are_not_reviewable_here(location, analyst):
    """Moving a risk on the map is a different act with different evidence."""
    with pytest.raises(review.ReviewError, match="not reviewable"):
        review.decide(
            location,
            field="latitude",
            value="-6.2",
            rationale="The geocoder put it in the wrong district entirely.",
            actor=analyst,
        )


def test_a_cohort_the_rules_do_not_define_is_refused(location, analyst):
    with pytest.raises(review.ReviewError, match="not a cohort"):
        review.decide(
            location,
            field=DecisionField.COHORT,
            value="Z",
            rationale="Seems like its own kind of thing to me.",
            actor=analyst,
        )


def test_a_storey_count_that_is_not_a_number_is_refused(location, analyst):
    with pytest.raises(review.ReviewError, match="not a storey count"):
        review.decide(
            location,
            field=DecisionField.STOREYS,
            value="about six",
            rationale="The broker said it was around six floors.",
            actor=analyst,
        )


def test_an_impossible_storey_count_is_refused(location, analyst):
    with pytest.raises(review.ReviewError, match="not a building"):
        review.decide(
            location,
            field=DecisionField.STOREYS,
            value=0,
            rationale="The schedule appears to show zero floors here.",
            actor=analyst,
        )


def test_clearing_a_storey_count_says_a_reviewer_could_not_establish_it(
    batch, analyst
):
    """Different from never having had one, and worth being able to record."""
    stated = batch.location_rows.exclude(storeys=None).first()
    if stated is None:
        pytest.skip("no location in this fixture states a storey count")
    review.decide(
        stated,
        field=DecisionField.STOREYS,
        value=None,
        rationale="The stated height belongs to the neighbouring unit, not this.",
        actor=analyst,
    )
    assert review.overlay(stated).storeys is None
    assert review.overlay(stated).storeys_are_reviewed


# -- the storeys payoff -------------------------------------------------------------------

def test_storey_coverage_reports_value_as_well_as_count(batch):
    """The risks that state the least are not the small ones."""
    coverage = review.storey_coverage(list(batch.location_rows.all()))
    assert coverage["locations"] == batch.location_rows.count()
    assert (
        coverage["stated_in_source"]
        + coverage["established_in_review"]
        + coverage["unstated"]
        == coverage["locations"]
    )
    assert "value_unstated" in coverage
    assert "cannot become one Oasis function" in coverage["note"]


def test_a_reviewed_height_counts_separately_from_a_reported_one(location, analyst):
    """They are worth different amounts and a version must not conflate them."""
    review.decide(
        location,
        field=DecisionField.STOREYS,
        value=5,
        rationale="Five floors counted on the site visit of 2026-08-14.",
        actor=analyst,
    )
    coverage = review.storey_coverage(list(location.batch.location_rows.all()))
    assert coverage["established_in_review"] == 1


def test_a_reviewed_height_reaches_the_oed_file(batch, analyst):
    """Otherwise the reviewer's work stays out of the model."""
    from apps.exposure.promotion import promote

    baseline = promote(batch, name="Before review", actor=analyst)
    promoted = {
        (row["AccNumber"], row["LocNumber"]) for row in oed_rows(baseline)
    }
    assert promoted, "the fixture promotes no locations"

    business, number = sorted(promoted)[0]
    target = batch.location_rows.get(business_id=business, location_number=number)
    review.decide(
        target,
        field=DecisionField.STOREYS,
        value=7,
        rationale="Seven floors counted on the site visit of 2026-08-14.",
        actor=analyst,
    )

    exposure = promote(batch, name="With reviewed height", actor=analyst)
    written = next(
        row
        for row in oed_rows(exposure)
        if (row["AccNumber"], row["LocNumber"]) == (business, number)
    )
    assert written["NumberOfStoreys"] == "7"


def test_a_height_nobody_knows_is_written_blank_rather_than_defaulted(batch, analyst):
    """A guess is a different vulnerability function, not a small error."""
    from apps.exposure.promotion import promote

    exposure = promote(batch, name="No heights", actor=analyst)
    heights = {row["NumberOfStoreys"] for row in oed_rows(exposure)}
    assert heights == {""}


def test_the_exposure_version_says_where_its_heights_came_from(batch, analyst):
    """Section 8: an assumed attribute must never look like a reported one."""
    from apps.exposure.promotion import promote

    baseline = promote(batch, name="Before review", actor=analyst)
    business, number = sorted(
        (row["AccNumber"], row["LocNumber"]) for row in oed_rows(baseline)
    )[0]
    review.decide(
        batch.location_rows.get(business_id=business, location_number=number),
        field=DecisionField.STOREYS,
        value=3,
        rationale="Three floors counted on the site visit of 2026-08-14.",
        actor=analyst,
    )
    exposure = promote(batch, name="Lineage", actor=analyst)
    basis = exposure.source_lineage["attributes_not_reported"]["NumberOfStoreys"]
    assert "established in review" in basis
    assert "written blank rather than defaulted" in basis


# -- the import-results payload ------------------------------------------------------------

def test_the_payload_carries_everything_the_screen_shows(batch):
    payload = review.import_results(batch)
    for key in (
        "batch",
        "included",
        "review",
        "missing_model_inputs",
        "multi_location_businesses",
        "repeated_coordinates",
        "findings",
        "use_modes",
        "allocation_note",
    ):
        assert key in payload


def test_the_payload_names_the_versions_that_produced_it(batch):
    """A screen that could not say which rules assigned a cohort is decoration."""
    payload = review.import_results(batch)
    assert payload["batch"]["source_checksum"]
    assert payload["batch"]["parser_version"]
    assert payload["batch"]["cohort_rule_version"]
    assert payload["batch"]["overlay_version"] == review.OVERLAY_VERSION


def test_missing_inputs_say_what_each_one_costs(batch):
    """"Occupancy missing" means nothing; "fail_v" means something."""
    payload = review.import_results(batch)
    fields = {item["field"]: item for item in payload["missing_model_inputs"]}
    assert "storeys" in fields
    assert "several intensity measures" in fields["storeys"]["consequence"]
    assert fields["storeys"]["value"] >= 0


def test_the_four_use_modes_are_distinguished(batch):
    """The gap between research and decision use is not visible in the number."""
    payload = review.import_results(batch)
    modes = {item["mode"] for item in payload["use_modes"]}
    assert modes == {"geometry", "technical_test", "research", "decision_use"}


def test_multi_location_businesses_are_reported_because_allocation_bites_there(batch):
    payload = review.import_results(batch)
    for entry in payload["multi_location_businesses"]:
        assert entry["locations"] > 1


def test_the_queue_is_ordered_by_value(batch):
    """An afternoon of review should be spent on the risks carrying the money."""
    queued = review.queue(batch)
    values = [
        item["total_insured_value"] or 0 for item in queued["locations"]
    ]
    assert values == sorted(values, reverse=True)


def test_deciding_a_row_takes_it_out_of_the_queue(batch, analyst):
    queued = review.queue(batch)
    if not queued["locations"]:
        pytest.skip("this fixture leaves nothing pending review")
    first = batch.location_rows.get(pk=queued["locations"][0]["id"])
    review.decide(
        first,
        field=DecisionField.REVIEW_STATE,
        value=ReviewState.CONFIRMED,
        rationale="Coordinate confirmed against the broker's site plan.",
        actor=analyst,
    )
    assert review.queue(batch)["outstanding"] == queued["outstanding"] - 1


def test_the_summary_counts_cohorts_after_decisions_rather_than_before(
    batch, analyst
):
    """A screen showing the rules' answer after a person overruled it is wrong."""
    target = batch.location_rows.exclude(cohort="A").first()
    if target is None:
        pytest.skip("every location in this fixture is already cohort A")
    before = review.review_summary(batch)["by_cohort"].get("A", 0)
    review.decide(
        target,
        field=DecisionField.COHORT,
        value="A",
        rationale="Rooftop confirmed against the broker's site photographs.",
        actor=analyst,
    )
    after = review.review_summary(batch)
    assert after["by_cohort"]["A"] == before + 1
    assert after["decided"] == 1


# -- over the API ---------------------------------------------------------------------------

def test_a_decision_can_be_recorded_over_the_api(client_for, batch, analyst):
    api_client = client_for(analyst)
    target = batch.location_rows.first()
    response = api_client.post(
        f"/api/v1/portfolio-imports/{batch.id}/locations/{target.id}/decide/",
        {
            "field": "storeys",
            "value": 4,
            "rationale": "Four floors counted on the site visit of 2026-08-14.",
        },
        format="json",
    )
    assert response.status_code == 201
    assert response.data["location"]["storeys"] == 4
    assert len(response.data["history"]) == 1


def test_the_api_refuses_a_decision_with_no_reason(client_for, batch, analyst):
    api_client = client_for(analyst)
    target = batch.location_rows.first()
    response = api_client.post(
        f"/api/v1/portfolio-imports/{batch.id}/locations/{target.id}/decide/",
        {"field": "storeys", "value": 4, "rationale": ""},
        format="json",
    )
    assert response.status_code == 409
    assert "rationale" in response.data["detail"]


def test_a_decision_is_audited(client_for, batch, analyst):
    """Who changed what, and why, is the point of the whole mechanism."""
    from apps.audit.models import AuditEvent

    api_client = client_for(analyst)
    target = batch.location_rows.first()
    api_client.post(
        f"/api/v1/portfolio-imports/{batch.id}/locations/{target.id}/decide/",
        {
            "field": "storeys",
            "value": 9,
            "rationale": "Nine floors counted on the site visit of 2026-08-14.",
        },
        format="json",
    )
    event = AuditEvent.objects.order_by("-created_at").first()
    assert event.after_reference == {"storeys": "9"}
    assert "Nine floors" in event.detail


def test_the_import_results_screen_is_available_over_the_api(
    client_for, batch, analyst
):
    api_client = client_for(analyst)
    response = api_client.get(f"/api/v1/portfolio-imports/{batch.id}/import-results/")
    assert response.status_code == 200
    assert response.data["review"]["storeys"]["locations"] > 0


def test_a_project_viewer_may_not_record_a_decision(client_for, batch, outsider):
    """Reading an import and deciding its eligibility are different rights."""
    from apps.projects.models import ProjectMembership, ProjectRole

    ProjectMembership.objects.create(
        project=batch.project, user=outsider, role=ProjectRole.VIEWER
    )
    api_client = client_for(outsider)
    target = batch.location_rows.first()
    response = api_client.post(
        f"/api/v1/portfolio-imports/{batch.id}/locations/{target.id}/decide/",
        {
            "field": "storeys",
            "value": 4,
            "rationale": "Four floors counted on the site visit of 2026-08-14.",
        },
        format="json",
    )
    assert response.status_code == 403


def test_a_location_that_is_not_part_of_this_import_is_not_reachable(
    client_for, batch, analyst
):
    """A decision must land on the row the caller thinks it is landing on."""
    import uuid

    api_client = client_for(analyst)
    response = api_client.post(
        f"/api/v1/portfolio-imports/{batch.id}/locations/{uuid.uuid4()}/decide/",
        {
            "field": "storeys",
            "value": 4,
            "rationale": "Four floors counted on the site visit of 2026-08-14.",
        },
        format="json",
    )
    assert response.status_code == 404


def test_the_value_in_the_queue_reconciles_with_the_rows_in_it(batch):
    queued = review.queue(batch)
    total = sum(
        Decimal(str(item["total_insured_value"] or 0))
        for item in queued["locations"]
    )
    assert Decimal(str(queued["outstanding_value"])) == pytest.approx(total)
