"""The KRE-share earthquake golden test.

Step 5 of the integration order: take a cohort of the Klapton Re extract all
the way from source rows to a completed analysis, against the prototype
Indonesia model, and pin the numbers.

What "golden" means here is narrow and worth stating, because the alternative
reading would be dishonest. No hazard footprint stands behind the prototype
model and no damage relationship stands behind its vulnerability identifiers,
so this cannot pin a loss. What it pins is everything between the workbook and
the engine: that every unit of KRE-share TIV reaches a grid cell and a
vulnerability function, that the accounting reconciles to the cent, that the
same inputs produce the same keys on every run, and that the run completes.
A loss number becomes possible when a hazard set arrives; the path it will
travel is what is fixed here.

The three properties that make it a regression test rather than a smoke test:

* **Complete.** Every location, coverage and modelled sub-peril receives a
  response, and mapped plus not-at-risk plus failed equals the published
  source TIV exactly.
* **Reproducible.** The occupancy assumption, the allocation and the grid are
  all deterministic, so the keys file is byte-identical between runs. A
  difference means something changed, and the diff says what.
* **Still gated.** Promoting the same portfolio under ``not_reported_v1``
  produces ``fail_v`` on all of it and holds the run at the section 8 gate.
  The gate is what makes the passing case mean anything.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

import cass_extract as extract
from apps.exposure.extract import import_portfolio
from apps.exposure.promotion import promote
from apps.modelregistry import pilot
from apps.modelregistry.models import PublicationState
from apps.runs.models import AnalysisRun, Run, RunKind
from apps.runs.services import RunBlocked, execute
from cass_core.runs import RunState

from .test_analysis_execution import engine_for, oasis_server
from .test_promotion import as_template, oed_rows

pytestmark = pytest.mark.django_db


@pytest.fixture()
def pilot_model(db, modeller):
    """The Indonesia prototype grid and routing table, in the registry."""
    return pilot.register("ID", actor=modeller)


@pytest.fixture()
def batch(project, analyst):
    return import_portfolio(
        project, as_template(), filename="portfolio.xlsx", actor=analyst
    )


@pytest.fixture()
def benchmark(batch, analyst):
    """The cohort A Fire selection, promoted under the default assumptions."""
    return promote(batch, name="KRE-share earthquake benchmark", actor=analyst)


def analysis_for(project, exposure, model, analyst) -> AnalysisRun:
    run = Run.objects.create(
        kind=RunKind.ANALYSIS,
        project=project,
        label="KRE-share earthquake golden",
        created_by=analyst,
    )
    return AnalysisRun.objects.create(
        run=run,
        exposure_version=exposure,
        model_version=model,
        perspectives=["ground_up"],
        created_by=analyst,
    )


# -- the prototype model reaches the registry ----------------------------------------

def test_the_prototype_model_registers_with_its_cells_and_functions(pilot_model):
    """The gap the mapping work package left: a grid a run can actually load."""
    assert pilot_model.grid.cell_count == 52_831
    assert pilot_model.vulnerability_set.function_count == 16
    assert pilot_model.imts == ["SA(0.3)", "SA(0.6)", "SA(1.0)"]


def test_the_prototype_arrives_as_a_draft_that_cannot_be_used_for_a_decision(pilot_model):
    """A prototype that looked approved would be worse than no prototype."""
    assert pilot_model.publication_state == PublicationState.DRAFT
    assert pilot_model.is_research_prototype is True
    assert pilot_model.usable_for_decisions is False
    assert pilot_model.vulnerability_set.licence_cleared is False
    assert pilot_model.publication_blockers()


def test_the_registered_model_carries_the_questions_it_does_not_answer(pilot_model):
    limitations = pilot_model.known_limitations
    assert "Site conditions are not represented" in limitations
    assert "No damage relationship" in limitations
    assert "not usable for a decision" in limitations.lower()
    assert "Open questions:" in pilot_model.grid.notes


def test_the_scope_statement_names_every_excluded_sub_peril(pilot_model):
    """Section 9 forbids calling a shake-only result earthquake loss."""
    scope = pilot_model.peril_scope
    assert scope["QEQ"]["treatment"] == "included"
    assert {scope[code]["treatment"] for code in ("QFF", "QTS", "QSL", "QLS")} == {
        "excluded"
    }
    assert scope["site_response"]["treatment"] == "excluded"


def test_registering_twice_does_not_produce_a_second_grid(modeller):
    """An identifier that moved would re-point every key ever issued."""
    first = pilot.register("ID", actor=modeller)
    second = pilot.register("ID", actor=modeller)
    assert first.pk == second.pk
    assert first.grid.pk == second.grid.pk


# -- the benchmark maps completely -----------------------------------------------------

def test_every_benchmark_location_maps_to_the_prototype_model(benchmark, pilot_model, project, analyst):
    """The point of step 5: no location left outside the grid or the taxonomy."""
    analysis = analysis_for(project, benchmark, pilot_model, analyst)
    execute(analysis, adapter=engine_for(oasis_server(lookup_rows=6)), poll_interval=0, actor=analyst)

    analysis.refresh_from_db()
    summary = analysis.keys_summary
    assert summary["mapped_locations"] == benchmark.location_count
    assert summary["counts_by_status"].get("fail_ap", 0) == 0
    assert summary["counts_by_status"].get("fail_v", 0) == 0


def test_the_whole_kre_share_tiv_is_mapped(benchmark, pilot_model, project, analyst):
    """Not a share of it. Unmapped TIV is exposure omitted from the loss."""
    analysis = analysis_for(project, benchmark, pilot_model, analyst)
    execute(analysis, adapter=engine_for(oasis_server(lookup_rows=6)), poll_interval=0, actor=analyst)

    analysis.refresh_from_db()
    summary = analysis.keys_summary
    assert Decimal(summary["source_tiv"]) == benchmark.total_tiv
    assert Decimal(summary["mapped_tiv"]) == benchmark.total_tiv
    assert summary["mapped_share"] == 1.0


def test_the_keys_accounting_reconciles_to_the_cent(benchmark, pilot_model, project, analyst):
    """Section 15: value must not be able to disappear between the two."""
    analysis = analysis_for(project, benchmark, pilot_model, analyst)
    execute(analysis, adapter=engine_for(oasis_server(lookup_rows=6)), poll_interval=0, actor=analyst)

    analysis.refresh_from_db()
    assert analysis.keys_reconciled is True
    assert Decimal(analysis.keys_summary["difference"]) == 0


def test_the_default_taxonomy_reaches_the_general_commercial_function(
    benchmark, pilot_model, project, analyst
):
    """The assumption catalogue and the routing table have to agree."""
    analysis = analysis_for(project, benchmark, pilot_model, analyst)
    execute(analysis, adapter=engine_for(oasis_server(lookup_rows=6)), poll_interval=0, actor=analyst)

    analysis.refresh_from_db()
    assert analysis.keys_summary["vulnerability"] == "id-vuln-0.1.0-draft"
    assert analysis.keys_summary["grid"] == "id-grid-0.1.0-draft"


def test_the_same_inputs_produce_the_same_keys(benchmark, pilot_model, project, analyst):
    """Reproducibility is the property a golden test exists to hold."""
    summaries = []
    for _ in range(2):
        analysis = analysis_for(project, benchmark, pilot_model, analyst)
        execute(
            analysis,
            adapter=engine_for(oasis_server(lookup_rows=6)),
            poll_interval=0,
            actor=analyst,
        )
        analysis.refresh_from_db()
        summaries.append(analysis.keys_summary)

    assert summaries[0] == summaries[1]


# -- the run completes -------------------------------------------------------------------

def test_the_golden_run_reaches_the_engine_and_completes(
    benchmark, pilot_model, project, analyst
):
    analysis = analysis_for(project, benchmark, pilot_model, analyst)
    execute(analysis, adapter=engine_for(oasis_server(lookup_rows=6)), poll_interval=0, actor=analyst)

    run = Run.objects.get(id=analysis.run_id)
    assert run.state == RunState.SUCCEEDED
    assert run.progress == 1.0


def test_the_manifest_traces_the_result_to_the_prototype_it_used(
    benchmark, pilot_model, project, analyst
):
    """A reader must be able to see which draft produced a number."""
    analysis = analysis_for(project, benchmark, pilot_model, analyst)
    execute(analysis, adapter=engine_for(oasis_server(lookup_rows=6)), poll_interval=0, actor=analyst)

    manifest = Run.objects.get(id=analysis.run_id).manifest
    assert manifest["exposure_version"] == str(benchmark.id)
    assert manifest["model_version"] == str(pilot_model.id)
    # The draft assets by name, so a reader does not have to resolve a uuid to
    # find out that the grid and the routing table were prototypes.
    assert manifest["keys"]["grid"] == "id-grid-0.1.0-draft"
    assert manifest["keys"]["vulnerability"] == "id-vuln-0.1.0-draft"


# -- and the gate still holds ---------------------------------------------------------------

def test_an_unknown_occupancy_fails_every_location_and_holds_the_run(
    batch, pilot_model, project, analyst
):
    """The assumption is what unlocks the engine, and it is a choice each time.

    Without it the honest answer is that nothing can be routed, and the run has
    to stop rather than proceed on exposure it could not map.
    """
    unknown = promote(
        batch,
        name="Occupancy not reported",
        occupancy=extract.NOT_REPORTED,
        actor=analyst,
    )
    assert {row["OccupancyCode"] for row in oed_rows(unknown)} == {"1000"}

    analysis = analysis_for(project, unknown, pilot_model, analyst)
    with pytest.raises(RunBlocked):
        execute(
            analysis,
            adapter=engine_for(oasis_server(lookup_rows=6)),
            poll_interval=0,
            actor=analyst,
        )

    analysis.refresh_from_db()
    summary = analysis.keys_summary
    assert Decimal(summary["mapped_tiv"]) == 0
    assert Decimal(summary["failed_tiv"]) == unknown.total_tiv
    assert summary["reconciled"] is True


def test_the_blocked_run_says_what_would_unblock_it(batch, pilot_model, project, analyst):
    unknown = promote(
        batch, name="Occupancy not reported", occupancy=extract.NOT_REPORTED, actor=analyst
    )
    analysis = analysis_for(project, unknown, pilot_model, analyst)
    with pytest.raises(RunBlocked):
        execute(
            analysis,
            adapter=engine_for(oasis_server(lookup_rows=6)),
            poll_interval=0,
            actor=analyst,
        )

    run = Run.objects.get(id=analysis.run_id)
    assert run.state == RunState.BLOCKED
    assert "occupancy 1000" in (run.gate_detail or "") + (run.gate_summary or "")


def test_a_spread_assumption_routes_through_more_than_one_function(
    batch, pilot_model, project, analyst
):
    """The mixed preset exists to exercise the taxonomy, so it must actually do so."""
    mixed = promote(
        batch, name="Mixed taxonomy", occupancy=extract.MIXED_COMMERCIAL, actor=analyst
    )
    analysis = analysis_for(project, mixed, pilot_model, analyst)
    execute(analysis, adapter=engine_for(oasis_server(lookup_rows=6)), poll_interval=0, actor=analyst)

    analysis.refresh_from_db()
    assert analysis.keys_reconciled is True
    assert Decimal(analysis.keys_summary["mapped_tiv"]) == mixed.total_tiv
    assert len(set(mixed.source_lineage["taxonomy"]["occupancy_counts"])) > 1


# -- one model version covers one country ---------------------------------------------------

def test_a_portfolio_outside_the_model_country_is_held_rather_than_run(
    batch, pilot_model, project, analyst
):
    """A model version covers one country, and the grid says so honestly.

    The Nepali business in this extract falls outside every Indonesian tile, so
    an Indonesia-only selection is not a convenience -- it is the difference
    between a run and a gate.
    """
    nepal = promote(
        batch,
        name="Nepal only",
        class_of_business=None,
        country="NP",
        actor=analyst,
    )
    analysis = analysis_for(project, nepal, pilot_model, analyst)
    with pytest.raises(RunBlocked):
        execute(
            analysis,
            adapter=engine_for(oasis_server(lookup_rows=1)),
            poll_interval=0,
            actor=analyst,
        )

    analysis.refresh_from_db()
    assert Decimal(analysis.keys_summary["failed_tiv"]) == nepal.total_tiv
    assert analysis.keys_summary["counts_by_status"]["fail_ap"] > 0


def test_a_country_selection_records_what_it_narrowed_to(batch, analyst):
    indonesia = promote(
        batch,
        name="Indonesia only",
        class_of_business=None,
        country="ID",
        actor=analyst,
    )
    assert indonesia.source_lineage["country_filter"] == "ID"
    assert indonesia.source_lineage["countries"] == ["ID"]
    assert "ID only" in indonesia.source_description


# -- what the result may be used for -----------------------------------------------------

def test_the_benchmark_version_states_it_is_not_fit_for_a_decision(benchmark):
    """Every assumption behind the number, in the place a reader will look."""
    lineage = benchmark.source_lineage
    assert "Blocked" in lineage["decision_use"]
    assert "commercial_general_v1" in lineage["decision_use"]
    assert lineage["taxonomy"]["assumption"]["approved"] is False
    assert "KRE-share gross damage" in lineage["value_basis"]
