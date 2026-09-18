"""A run that asks for reinsurance cover limited by contract terms.

The calculation itself is tested in ``cass_oed``; these hold the plumbing that
surrounds it. The run asks the engine for the one extra table it needs and no
more; the engine's copy of the locations says which contracts reach each one,
with the terms recorded as they were sent; and the limited result is published
beside the engine's, checked against it, and says so when the check fails.
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import numpy as np
import pytest

from apps.exposure.extract import import_portfolio
from apps.exposure.promotion import promote
from apps.modelregistry.models import PublicationState
from apps.results.models import ResultSet
from apps.runs.models import AnalysisRun, ReinsuranceCover, Run, RunKind
from apps.runs.services import (
    COVER_FIELD,
    COVER_SUMMARY_ID,
    _publish_limited_cover,
    _write_cover_classes,
    build_analysis_settings,
    oed_payloads,
)
from cass_oed import ord as ord_results
from cass_oed.limited_cover import EventLosses, LayerTerms, calculate

from .conftest import API
from .test_analysis_execution import model_version  # noqa: F401 -- the fixture

_FIXTURES = Path(__file__).resolve().parents[2] / "packages" / "cass_extract" / "tests"
if str(_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_FIXTURES))

from fixtures import as_template, financial_template  # noqa: E402

pytestmark = pytest.mark.django_db


def catastrophe_only():
    """The financial template's book under its two-layer catastrophe excess alone."""
    risks, policies, contracts, _ = financial_template()
    contracts = [row for row in contracts if row["Contract type"] == "CXL"]
    return risks, policies, contracts, [{"Contract number": "2"}]


@pytest.fixture()
def exposure(project, analyst):
    batch = import_portfolio(
        project, as_template(*catastrophe_only()), filename="cat.xlsx", actor=analyst
    )
    return promote(batch, name="Cat book", actor=analyst)


@pytest.fixture()
def limited_run(project, exposure, model_version, analyst):  # noqa: F811
    run = Run.objects.create(kind=RunKind.ANALYSIS, project=project, label="Limited", created_by=analyst)
    return AnalysisRun.objects.create(
        run=run,
        exposure_version=exposure,
        model_version=model_version,
        perspectives=["insured", "reinsurance"],
        reinsurance_cover=ReinsuranceCover.CONTRACT_TERMS,
        created_by=analyst,
    )


# -- what the engine is asked for ----------------------------------------------------

def test_limited_cover_asks_for_insured_loss_per_cover_class_year_by_year(limited_run):
    document = build_analysis_settings(limited_run)
    cover = [item for item in document["il_summaries"] if item["id"] == COVER_SUMMARY_ID]
    assert cover == [
        {"id": COVER_SUMMARY_ID, "oed_fields": [COVER_FIELD], "ord_output": {"plt_sample": True}}
    ]


def test_the_engines_cover_asks_for_nothing_extra(limited_run):
    limited_run.reinsurance_cover = ReinsuranceCover.ENGINE
    document = build_analysis_settings(limited_run)
    assert all(item["id"] != COVER_SUMMARY_ID for item in document["il_summaries"])


# -- what the engine is given ---------------------------------------------------------

def test_each_location_is_marked_with_the_contracts_that_reach_it(limited_run, analyst):
    payloads, record = _write_cover_classes(limited_run, oed_payloads(limited_run), analyst)

    location = next(payload for role, _, payload in payloads if role == "oed_location")
    rows = list(csv.DictReader(io.StringIO(location.decode("utf-8"))))
    assert {row[COVER_FIELD] for row in rows} == {"2"}
    assert record["classes"] == {"2": len(rows)}

    limited_run.refresh_from_db()
    layers = [LayerTerms.from_dict(item) for item in limited_run.cover_terms["layers"]]
    assert [(item.contract, item.layer) for item in layers] == [(2, 1), (2, 2)]
    # The first layer states its reinstatements; the second does not.
    assert (layers[0].reinstatements, layers[0].rates, layers[0].premium) == (2, (1.25, 1.0), 150000.0)
    assert layers[1].reinstatements is None


# -- what comes back -----------------------------------------------------------------

def cover_package(splt_rows, *, engine_net_aal, engine_insured_aal) -> ord_results.OrdPackage:
    """An output package with the portfolio results and the per-class sample table."""
    def csv_bytes(lines):
        return ("\n".join(lines) + "\n").encode("utf-8")

    header = "Period,PeriodWeight,EventId,Year,Month,Day,Hour,Minute,SummaryId,SampleId,Loss,ImpactedExposure"
    members = {
        "output/il_S1_palt.csv": csv_bytes(["SummaryId,SampleType,MeanLoss,SDLoss", f"1,2,{engine_insured_aal},1"]),
        "output/il_S1_ept.csv": csv_bytes(["SummaryId,EPCalc,EPType,ReturnPeriod,Loss", "1,4,3,2,1"]),
        "output/ri_S1_palt.csv": csv_bytes(["SummaryId,SampleType,MeanLoss,SDLoss", f"1,2,{engine_net_aal},1"]),
        "output/ri_S1_ept.csv": csv_bytes(
            ["SummaryId,EPCalc,EPType,ReturnPeriod,Loss", "1,4,3,2,1", "1,4,3,1,1"]
        ),
        f"output/il_S{COVER_SUMMARY_ID}_summary-info.csv": csv_bytes(
            [f"summary_id,{COVER_FIELD},tiv", "1,2,5000000"]
        ),
        f"output/il_S{COVER_SUMMARY_ID}_splt.csv": csv_bytes(
            [header, *(f"{p},0.5,{e},1,1,1,0,0,1,{s},{loss},1" for p, e, s, loss in splt_rows)]
        ),
    }
    return ord_results.OrdPackage(members)


#: Two simulated years, one sample: a year of two events and a year of one.
SPLT = [(1, 1, 1, 600000.0), (1, 2, 1, 900000.0), (2, 3, 1, 450000.0)]


def engine_figures(limited_run):
    """What the engine would report for SPLT, from the arithmetic the engine does."""
    layers = [LayerTerms.from_dict(item) for item in limited_run.cover_terms["layers"]]
    losses = EventLosses.from_rows([(s, p, e, "2", loss) for p, e, s, loss in SPLT], periods=2, samples=1)
    return calculate(losses, layers, return_periods=[2, 1])


def publish(limited_run, analyst, *, engine_net_aal=None):
    _write_cover_classes(limited_run, oed_payloads(limited_run), analyst)
    limited_run.refresh_from_db()
    expected = engine_figures(limited_run)
    package = cover_package(
        SPLT,
        engine_net_aal=engine_net_aal if engine_net_aal is not None else expected.unlimited.average_annual_loss,
        engine_insured_aal=expected.insured.average_annual_loss,
    )
    metrics = {
        name: ord_results.metrics_for(package, perspective=name) for name in ("insured", "reinsurance")
    }
    return expected, _publish_limited_cover(limited_run, package, metrics, {}, "", analyst)


def test_the_limited_result_is_published_beside_the_engines(limited_run, analyst):
    expected, outcome = publish(limited_run, analyst)
    assert outcome["agrees_with_engine"] is True

    result = ResultSet.objects.get(id=outcome["result_set"])
    assert result.perspective == "ri_terms"
    assert float(result.average_annual_loss) == pytest.approx(expected.limited.average_annual_loss, abs=0.01)
    detail = result.cover_detail
    assert detail["check"]["agrees"] is True
    assert [item["contract"] for item in detail["layers"]] == [2, 2]
    assert any("layer 2" in note for note in detail["notes"])
    assert "event-number order" in detail["notes"][0]


def test_limited_cover_costs_more_than_the_engines_unlimited_free_cover(limited_run, analyst):
    """Layer 1 (400k xs 100k, two reinstatements at 125% then 100% on 150k)."""
    expected, outcome = publish(limited_run, analyst)
    result = ResultSet.objects.get(id=outcome["result_set"])
    # Year 1 recovers 400k twice and pays 1.25 x 150k then 1.0 x 150k to reinstate.
    layer = next(item for item in result.cover_detail["layers"] if item["layer"] == 1)
    assert layer["premium_aal"] == pytest.approx((1.25 * 150000 + 1.0 * 150000 + 1.25 * 150000 * 350000 / 400000) / 2)
    assert float(result.average_annual_loss) > expected.unlimited.average_annual_loss


def test_a_result_that_disagrees_with_the_engine_says_so(limited_run, analyst):
    _, outcome = publish(limited_run, analyst, engine_net_aal=1.0)
    assert outcome["agrees_with_engine"] is False
    result = ResultSet.objects.get(id=outcome["result_set"])
    assert result.cover_detail["check"]["agrees"] is False
    assert any("does not agree with the engine" in note for note in result.cover_detail["notes"])


def test_the_sample_table_is_read_for_its_years(limited_run, analyst):
    expected, outcome = publish(limited_run, analyst)
    result = ResultSet.objects.get(id=outcome["result_set"])
    assert result.cover_detail["periods"] == 2
    assert result.cover_detail["samples"] == 1
    assert np.isclose(result.cover_detail["insured"]["average_annual_loss"], (600000 + 900000 + 450000) / 2)


# -- through the API -----------------------------------------------------------------

def submit(api, project, exposure, model_version, **overrides):  # noqa: F811
    model_version.publication_state = PublicationState.PUBLISHED
    model_version.save(update_fields=["publication_state"])
    payload = {
        "project": str(project.id),
        "exposure_version": str(exposure.id),
        "model_version": str(model_version.id),
        "perspectives": ["insured", "reinsurance"],
        "reinsurance_cover": "contract_terms",
        **overrides,
    }
    return api.post(f"{API}/analysis-runs/", payload, format="json")


def test_limited_cover_can_be_asked_for(api, project, exposure, model_version):  # noqa: F811
    response = submit(api, project, exposure, model_version)
    assert response.status_code == 201, response.content
    assert response.json()["reinsurance_cover"] == "contract_terms"


def test_limited_cover_needs_the_net_of_reinsurance_perspective(api, project, exposure, model_version):  # noqa: F811
    response = submit(api, project, exposure, model_version, perspectives=["insured"])
    assert response.status_code == 400
    assert "ask for that perspective" in str(response.json())


def test_a_programme_limited_cover_cannot_apply_is_refused_before_any_engine_time(
    api, project, analyst, model_version  # noqa: F811
):
    risks, policies, contracts, scope = financial_template()
    batch = import_portfolio(project, as_template(risks, policies, contracts, scope), filename="qs.xlsx", actor=analyst)
    with_quota_share = promote(batch, name="QS book", actor=analyst)
    response = submit(api, project, with_quota_share, model_version)
    assert response.status_code == 400
    assert "catastrophe excess of loss contracts only" in str(response.json())
