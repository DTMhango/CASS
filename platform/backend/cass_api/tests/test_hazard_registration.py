"""Registering a hazard set, and the two things that must not slip through.

A footprint is a large table of numbers that looks the same whatever produced
it, so almost everything here is about provenance and about refusing a set that
would look complete while answering less than it appears to.

The exports below are the shape engine 3.23.4 writes. They are tiny -- two
events, two cells -- because what is being tested is the registry, not the
binning; the binning has its own tests against the converter.
"""

from __future__ import annotations

import pytest

from apps.modelregistry import hazard, pilot
from apps.modelregistry.models import HazardSet, PublicationState

from . import fixture_model

pytestmark = pytest.mark.django_db

HEADER = (
    "#,,,,,\"generated_by='OpenQuake engine 3.23.4', "
    "start_date='2026-09-12T16:30:50', checksum=556311143\""
)
RUPTURE_HEADER = (
    "#,,\"generated_by='OpenQuake engine 3.23.4', checksum=556311143, "
    "investigation_time=50.0, ses_per_logic_tree_path=20\""
)


@pytest.fixture()
def export(tmp_path):
    """A calculation on two cells of the Indonesia prototype grid."""
    directory = tmp_path / "out"
    directory.mkdir()
    files = {
        "gmf-data": (
            f"{HEADER}\nevent_id,gmv_PGA,gmv_SA(0.3),gmv_SA(0.6),gmv_SA(1.0),"
            "custom_site_id\n"
            "0,2.0E-01,4.0E-01,3.0E-01,1.0E-01,8055\n"
            "0,3.0E-01,5.0E-01,2.0E-01,1.5E-01,8056\n"
            "1,1.0E-01,2.0E-01,1.5E-01,8.0E-02,8055\n"
        ),
        "events": (
            f"{HEADER}\nevent_id,rup_id,rlz_id,year,ses_id\n"
            "0,2,0,90,14\n1,7,0,774,9\n"
        ),
        "ruptures": f"{RUPTURE_HEADER}\nrup_id,source_id,mag\n2,1,5.1\n7,1,6.4\n",
        "realizations": f"{HEADER}\nrlz_id,branch_path,weight\n0,A~A,1.0\n",
    }
    for stem, payload in files.items():
        (directory / f"{stem}_1.csv").write_text(payload, encoding="utf-8")
    return directory


#: The same calculation sampled the way ADR 18 runs one: one fifty-year event
#: set along each of twenty logic-tree paths, pooled into a single catalogue.
POOLED_RUPTURE_HEADER = (
    "#,,\"generated_by='OpenQuake engine 3.23.4', checksum=556311143, "
    "investigation_time=50.0, ses_per_logic_tree_path=1\""
)


@pytest.fixture()
def pooled_export(tmp_path):
    """A calculation pooled across twenty equally weighted sampled paths."""
    directory = tmp_path / "pooled"
    directory.mkdir()
    weights = "\n".join(f"{index},A~A,0.05" for index in range(20))
    files = {
        "gmf-data": (
            f"{HEADER}\nevent_id,gmv_PGA,gmv_SA(0.3),gmv_SA(0.6),gmv_SA(1.0),"
            "custom_site_id\n"
            "0,2.0E-01,4.0E-01,3.0E-01,1.0E-01,8055\n"
            "1,1.0E-01,2.0E-01,1.5E-01,8.0E-02,8056\n"
        ),
        # Years run across the pooled catalogue: the first path's fifty, then
        # the next path's, which is what makes the span a thousand years.
        "events": (
            f"{HEADER}\nevent_id,rup_id,rlz_id,year,ses_id\n"
            "0,2,0,37,1\n1,7,19,962,1\n"
        ),
        "ruptures": (
            f"{POOLED_RUPTURE_HEADER}\nrup_id,source_id,mag\n2,1,5.1\n7,1,6.4\n"
        ),
        "realizations": f"{HEADER}\nrlz_id,branch_path,weight\n{weights}\n",
    }
    for stem, payload in files.items():
        (directory / f"{stem}_1.csv").write_text(payload, encoding="utf-8")
    return directory


@pytest.fixture()
def source():
    return hazard.SourceStatement(
        model="CASS West Java prototype area source",
        licence="CASS internal",
        ground_motion_models=("BooreEtAl2014",),
    )


@pytest.fixture()
def grid(db, modeller):
    return pilot.register_grid("ID", actor=modeller)


@pytest.fixture()
def registered(export, source, grid, modeller):
    return hazard.register(
        export,
        country_code="ID",
        version="0.1.0-test",
        source=source,
        grid=grid,
        actor=modeller,
    )


# -- what gets recorded ------------------------------------------------------------

def test_the_calculation_becomes_a_versioned_hazard_set(registered):
    hazard_set, _ = registered
    assert hazard_set.reference == "id-hazard-0.1.0-test"
    assert hazard_set.event_count == 2
    assert hazard_set.cell_count == 2
    assert sorted(hazard_set.imts) == ["PGA", "SA(0.3)", "SA(0.6)", "SA(1.0)"]


def test_the_timing_that_decides_annual_frequency_is_stored(registered):
    """An AAL from the wrong denominator is wrong by exactly that ratio."""
    hazard_set, _ = registered
    assert hazard_set.investigation_time == 50.0
    assert hazard_set.stochastic_event_sets == 20
    assert hazard_set.logic_tree_paths == 1
    assert hazard_set.effective_time == 1000.0
    assert hazard_set.annual_event_rate == pytest.approx(0.002)


def test_the_paths_a_pooled_catalogue_spans_count_towards_its_years(
    pooled_export, source, grid, modeller
):
    """Twenty paths of one fifty-year set span a thousand years, not fifty.

    The engine numbers years across the whole pooled catalogue (ADR 18), so the
    path count is a factor of the effective time exactly as the event set count
    is. Leaving it out would divide every annual rate by twenty while the record
    still looked complete.
    """
    hazard_set, _ = hazard.register(
        pooled_export,
        country_code="ID",
        version="0.1.0-pooled",
        source=source,
        grid=grid,
        actor=modeller,
    )
    assert hazard_set.investigation_time == 50.0
    assert hazard_set.stochastic_event_sets == 1
    assert hazard_set.logic_tree_paths == 20
    assert hazard_set.effective_time == 1000.0


def test_the_engine_and_the_calculation_are_identified(registered):
    """Two footprints are only comparable if the calculations behind them are."""
    hazard_set, _ = registered
    assert hazard_set.engine_version == "OpenQuake engine 3.23.4"
    assert hazard_set.calculation_checksum == "556311143"


def test_the_source_model_is_recorded_because_nothing_else_records_it(registered):
    hazard_set, _ = registered
    assert hazard_set.source_model == "CASS West Java prototype area source"
    assert hazard_set.ground_motion_models == ["BooreEtAl2014"]


def test_a_hazard_set_arrives_as_a_draft_under_the_internal_use_basis(registered):
    """A draft until it is published, and usable here from the moment it lands.

    What may be done with the data is settled once for the installation rather
    than asserted per calculation.
    """
    hazard_set, _ = registered
    assert hazard_set.publication_state == PublicationState.DRAFT
    assert hazard_set.licence_cleared is True
    assert "Internal use within Klapton Re" in hazard_set.licence_note


def test_registering_twice_replaces_rather_than_duplicates(
    export, source, grid, modeller
):
    """Two sets whose event identifiers meant different things would be worse."""
    for _ in range(2):
        hazard.register(
            export,
            country_code="ID",
            version="0.1.0-test",
            source=source,
            grid=grid,
            actor=modeller,
        )
    assert HazardSet.objects.filter(country_code="ID").count() == 1


# -- the artifacts -----------------------------------------------------------------

def test_one_footprint_per_measure_reaches_the_artifact_store(registered):
    """An Oasis footprint carries no measure, so four measures are four files."""
    from apps.artifacts.models import ArtifactLink

    hazard_set, _ = registered
    roles = set(
        ArtifactLink.objects.filter(
            subject_type="hazard_set", subject_id=hazard_set.id
        ).values_list("role", flat=True)
    )
    assert "hazard_footprint_PGA" in roles
    assert "hazard_footprint_SA1p0" in roles
    assert "hazard_occurrence" in roles
    assert "hazard_intensity_bin_dict_PGA" in roles


# -- the refusals --------------------------------------------------------------------

def test_a_hazard_set_must_name_its_source_model():
    with pytest.raises(hazard.HazardRegistrationError, match="name its seismic"):
        hazard.SourceStatement(model="  ")


def test_a_clearance_still_needs_the_basis_that_grants_it():
    """The default names one. Clearing with nothing behind it is still refused."""
    with pytest.raises(hazard.HazardRegistrationError, match="needs a reference"):
        hazard.SourceStatement(model="x", cleared=True, reference="")


def test_a_calculation_with_no_registered_grid_is_refused(export, source, db):
    """Its cells would name nothing."""
    with pytest.raises(hazard.HazardRegistrationError, match="No area-peril grid"):
        hazard.register(
            export, country_code="ID", version="0.1.0-test", source=source
        )


# -- attaching to a model version -------------------------------------------------------

def test_a_model_version_with_no_hazard_cannot_produce_a_loss(db, modeller):
    """It can hold exposure and map keys, and that is the honest state."""
    model = fixture_model.register("ID", actor=modeller)
    assert model.hazard_set is None
    assert any("cannot produce a loss" in item for item in model.publication_blockers())


def test_attaching_a_hazard_set_records_what_produced_the_ground_motion(
    registered, modeller
):
    model = fixture_model.register("ID", actor=modeller)
    hazard_set, _ = registered
    attached = hazard.attach(model, hazard_set, actor=modeller)

    assert attached.hazard_set == hazard_set
    assert attached.hazard_source_model == hazard_set.source_model
    assert attached.openquake_version == "OpenQuake engine 3.23.4"
    assert not any(
        "cannot produce a loss" in item for item in attached.publication_blockers()
    )


def test_a_hazard_set_missing_a_measure_the_functions_demand_is_refused(
    export, source, grid, modeller
):
    """Those functions would be answered by nothing and report zero."""
    (export / "gmf-data_1.csv").write_text(
        f"{HEADER}\nevent_id,gmv_PGA,custom_site_id\n0,0.2,8055\n1,0.1,8055\n",
        encoding="utf-8",
    )
    hazard_set, _ = hazard.register(
        export,
        country_code="ID",
        version="0.1.0-pga-only",
        source=source,
        grid=grid,
        actor=modeller,
    )
    model = fixture_model.register("ID", actor=modeller)
    with pytest.raises(hazard.HazardRegistrationError, match="SA\\(0.3\\)"):
        hazard.attach(model, hazard_set, actor=modeller)


def test_another_countrys_hazard_is_refused(registered, modeller):
    """It would apply one country's ground motion to another's buildings."""
    model = fixture_model.register_written(fixture_model.NEPAL_GRID, actor=modeller)
    hazard_set, _ = registered
    with pytest.raises(hazard.HazardRegistrationError, match="apply one"):
        hazard.attach(model, hazard_set, actor=modeller)
