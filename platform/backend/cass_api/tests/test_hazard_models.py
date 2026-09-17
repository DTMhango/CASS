"""Uploading a published PSHA model and configuring a run of it on-platform.

The archive fixtures are shaped exactly like the GEM mosaic packages -- a zip
holding a nested ``job.zip`` beside a README and a licence -- because that is
what an operator will actually drag onto the screen, and requiring them to
unwrap it first would be the platform asking them to do the one step it exists
to do for them.

Two properties matter more than the rest.

**Nothing about the model is edited.** An operator sets investigation time and
event set counts; the logic trees, the source files and the maximum distances
stay exactly as published. A screen that let somebody change a GMPE would let
them make a model nobody published and call it the national one.

**The refusals happen before the run.** A national calculation is hours. Every
condition that would waste them is checked while the operator is still looking
at the screen: the wrong calculation mode, hundreds of realisations, an archive
missing the tree its configuration names.

The real integration test lives at the bottom and runs only when the PuSGeN
2024 package is on disk, because that is the model these rules were written
against.
"""

from __future__ import annotations

import io
import os
import pathlib
import zipfile

import pytest

from apps.modelregistry import hazard_models, pilot
from apps.modelregistry.models import HazardJobSpec, HazardModel, PublicationState

pytestmark = pytest.mark.django_db

JOB_INI = """[general]
description = A national model
calculation_mode = classical
random_seed = 23

[geometry]
sites_csv = national-10km.csv

[logic_tree]
number_of_logic_tree_samples = 0

[erf]
rupture_mesh_spacing = 5.0
width_of_mfd_bin = 0.1

[site_params]
reference_vs30_value = 800.0
reference_depth_to_1pt0km_per_sec = -999

[calculation]
source_model_logic_tree_file = ssmLT.xml
gsim_logic_tree_file = gmmLT.xml
maximum_distance = {'Subduction Interface':1000.0,'default':300.0}
truncation_level = 5
investigation_time = 1.
intensity_measure_types_and_levels = {"PGA": [0.01], "SA(0.3)": [0.01], "SA(0.6)": [0.01], "SA(1.0)": [0.01]}

[output]
export_dir = ../out
hazard_maps = true
poes = 0.002105
"""

SSM_LT = """<nrml>
  <logicTree>
    <logicTreeBranchSet uncertaintyType="sourceModel">
      <logicTreeBranch branchID="b1"><uncertaintyModel>src.xml</uncertaintyModel></logicTreeBranch>
      <logicTreeBranch branchID="b2"><uncertaintyModel>src.xml</uncertaintyModel></logicTreeBranch>
    </logicTreeBranchSet>
  </logicTree>
</nrml>
"""

GMM_LT = """<nrml>
  <logicTree>
    <logicTreeBranchSet uncertaintyType="gmpeModel" applyToTectonicRegionType="Active Shallow Crust">
      <logicTreeBranch branchID="a1"/><logicTreeBranch branchID="a2"/><logicTreeBranch branchID="a3"/>
    </logicTreeBranchSet>
    <logicTreeBranchSet uncertaintyType="gmpeModel" applyToTectonicRegionType="Subduction Interface">
      <logicTreeBranch branchID="s1"/><logicTreeBranch branchID="s2"/>
    </logicTreeBranchSet>
  </logicTree>
</nrml>
"""


def archive(*, nested: bool = True, job: str = JOB_INI, omit: str = "") -> bytes:
    """A package shaped like a published release."""
    inner = io.BytesIO()
    parts = {
        "job.ini": job,
        "ssmLT.xml": SSM_LT,
        "gmmLT.xml": GMM_LT,
        "ssm/src.xml": "<nrml/>",
        "national-10km.csv": "lon,lat,vs30\n106.5,-6.2,400\n",
    }
    parts.pop(omit, None)
    with zipfile.ZipFile(inner, "w") as handle:
        for name, text in parts.items():
            handle.writestr(name, text)

    if not nested:
        return inner.getvalue()

    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as handle:
        handle.writestr("v2024.0.0/job.zip", inner.getvalue())
        handle.writestr("v2024.0.0/README.md", "# A national model")
        handle.writestr("v2024.0.0/LICENSE", "CC BY-NC-SA 4.0")
    return outer.getvalue()


# -- reading a package -------------------------------------------------------------

def test_a_nested_release_archive_is_unwrapped():
    """GEM's packages ship a job.zip inside; making a person unzip it first
    would be the platform declining its own job."""
    package = hazard_models.read_package(archive())
    assert package.job_path == "job.ini"
    assert "ssm/src.xml" in package.contents
    assert "README.md" in package.contents


def test_a_plain_job_archive_is_read_too():
    package = hazard_models.read_package(archive(nested=False))
    assert package.job_path == "job.ini"


def test_every_file_is_checksummed():
    """A model is only reproducible if what was uploaded can be identified."""
    package = hazard_models.read_package(archive())
    assert all(len(item.checksum) == 64 for item in package.files)
    assert len(package.checksum) == 64


def test_an_archive_with_no_job_configuration_is_refused():
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as handle:
        handle.writestr("readme.txt", "nothing here")
    with pytest.raises(hazard_models.HazardModelError, match="no job configuration"):
        hazard_models.read_package(empty.getvalue())


def test_something_that_is_not_a_zip_is_refused():
    with pytest.raises(hazard_models.HazardModelError, match="readable zip"):
        hazard_models.read_package(b"not a zip file at all")


def test_an_entry_escaping_its_directory_is_not_extracted():
    """An archive is somebody else's file, and a published model arrives from
    outside."""
    hostile = io.BytesIO()
    with zipfile.ZipFile(hostile, "w") as handle:
        handle.writestr("../../etc/passwd", "root")
        handle.writestr("job.ini", JOB_INI)
        handle.writestr("ssmLT.xml", SSM_LT)
        handle.writestr("gmmLT.xml", GMM_LT)
    package = hazard_models.read_package(hostile.getvalue())
    assert not any(".." in item.path for item in package.files)


# -- inspecting before storing --------------------------------------------------------

def test_inspection_says_what_running_it_would_take():
    summary = hazard_models.inspect(hazard_models.read_package(archive()))
    assert summary["configuration"]["calculation_mode"] == "classical"
    assert summary["configuration"]["runnable"] is False
    assert summary["logic_trees"]["estimated_realizations"] == 2 * 3 * 2


def test_the_realisation_count_is_known_before_anything_runs():
    """A national tree is hundreds of alternative views and a footprint is one."""
    summary = hazard_models.inspect(hazard_models.read_package(archive()))
    assert summary["logic_trees"]["estimated_realizations"] > 1
    assert summary["logic_trees"]["tectonic_regions"] == [
        "Active Shallow Crust",
        "Subduction Interface",
    ]


def test_a_package_missing_the_tree_its_configuration_names_is_reported():
    summary = hazard_models.inspect(
        hazard_models.read_package(archive(omit="gmmLT.xml"))
    )
    assert "incomplete" in summary["logic_trees"]["note"]


# -- registering ------------------------------------------------------------------------

@pytest.fixture()
def model(db, modeller) -> HazardModel:
    return hazard_models.register_model(
        archive(),
        country_code="ID",
        version="test-2024",
        label="A national model",
        source_organisation="A national agency",
        licence="CC BY-NC-SA 4.0",
        actor=modeller,
    )


def test_an_uploaded_model_becomes_a_registered_record(model):
    assert model.reference == "id-hazmodel-test-2024"
    assert model.published_calculation_mode == "classical"
    assert model.needs_conversion is True
    assert model.needs_sampling is True
    assert sorted(model.intensity_measures) == ["PGA", "SA(0.3)", "SA(0.6)", "SA(1.0)"]


def test_every_file_of_the_package_reaches_the_artifact_store(model):
    from apps.artifacts.models import ArtifactLink

    roles = set(
        ArtifactLink.objects.filter(
            subject_type="hazard_model", subject_id=model.id
        ).values_list("role", flat=True)
    )
    assert "hazard_model:job.ini" in roles
    assert "hazard_model:ssm/src.xml" in roles


def test_a_model_arrives_as_a_draft_under_the_internal_use_basis(model):
    """Draft until somebody publishes it; cleared for use the moment it lands.

    Use of model data is a fact about this installation -- internal to Klapton
    Re, not redistributed and not sold -- so it is recorded once and applied,
    rather than asked of whoever happens to be uploading.
    """
    assert model.publication_state == PublicationState.DRAFT
    assert model.licence_cleared is True
    assert "Internal use within Klapton Re" in model.licence_note


def test_uploading_the_same_version_twice_replaces_it(model, modeller):
    hazard_models.register_model(
        archive(),
        country_code="ID",
        version="test-2024",
        label="A national model, again",
        actor=modeller,
    )
    assert HazardModel.objects.filter(country_code="ID").count() == 1


# -- configuring a run ----------------------------------------------------------------

@pytest.fixture()
def grid(db, modeller):
    return pilot.register_grid("ID", actor=modeller)


def test_a_configured_run_converts_the_published_model(model, grid):
    outcome = hazard_models.resolve(model, grid)
    assert outcome["configuration"]["calculation_mode"] == "event_based"
    assert outcome["runnable"] is True
    changed = {item["parameter"] for item in outcome["conversion"]["changes"]}
    assert "calculation_mode" in changed


def test_the_model_s_own_science_is_untouched_by_a_run(model, grid):
    outcome = hazard_models.resolve(model, grid)
    settings = {
        item["name"]: item["value"]
        for item in outcome["configuration"]["settings"]
    }
    assert settings["gsim_logic_tree_file"] == "gmmLT.xml"
    assert settings["maximum_distance"] == (
        "{'Subduction Interface':1000.0,'default':300.0}"
    )
    assert settings["truncation_level"] == "5"


def test_an_operator_may_change_the_event_set_length(model, grid):
    outcome = hazard_models.resolve(
        model, grid, overrides={"ses_per_logic_tree_path": 100}
    )
    settings = {
        item["name"]: item["value"]
        for item in outcome["configuration"]["settings"]
    }
    assert settings["ses_per_logic_tree_path"] == "100"
    assert "5000" in outcome["rendered"] or settings["investigation_time"] == "50.0"


def test_an_operator_may_not_change_the_model_s_logic_tree(model, grid):
    """That does not configure this model; it makes a different one."""
    with pytest.raises(hazard_models.HazardModelError, match="cannot be set here"):
        hazard_models.resolve(
            model, grid, overrides={"gsim_logic_tree_file": "mine.xml"}
        )


def test_sampling_one_path_from_a_large_tree_is_declared(model, grid):
    """One realisation is not the model's weighted mean, and the run says so."""
    outcome = hazard_models.resolve(
        model, grid, overrides={"number_of_logic_tree_samples": 1}
    )
    assert any(
        "weighted mean" in item["message"] for item in outcome["problems"]
    )


def test_a_run_samples_twenty_paths_over_ten_thousand_years(model, grid):
    """Twenty paths (ADR 18), each ten fifty-year sets long (ADR 21)."""
    outcome = hazard_models.resolve(model, grid)

    assert "number_of_logic_tree_samples = 20" in outcome["rendered"]
    assert "ses_per_logic_tree_path = 10" in outcome["rendered"]
    assert "investigation_time = 50.0" in outcome["rendered"]
    assert outcome["catalogue"]["simulated_years"] == 10_000
    assert not any(
        "weighted mean" in item["message"] for item in outcome["problems"]
    )


def test_the_catalogue_says_what_its_tail_rests_on_before_anything_runs(model, grid):
    """A 1-in-1,000-year figure from 1,000 simulated years is the single worst year."""
    from apps.modelregistry.assets import load_grid

    outcome = hazard_models.resolve(
        model,
        grid,
        overrides={"ses_per_logic_tree_path": 10, "number_of_logic_tree_samples": 20},
        cells=load_grid(grid).cells,
        region={"min_latitude": -7.2, "max_latitude": -5.9, "min_longitude": 106.5, "max_longitude": 107.9},
    )

    catalogue = outcome["catalogue"]
    assert catalogue["simulated_years"] == 10_000
    tail = {item["return_period"]: item["years_beyond"] for item in catalogue["tail"]}
    assert tail[1000] == 10
    assert tail[100] == 100
    sites = outcome["coverage"]["cells_computed"]
    assert catalogue["storage"]["hazard_set_mb"] == round(sites * (24.3 + 4.0) * 10 / 1000)


def test_a_run_that_would_store_a_great_deal_says_so_first(model, grid):
    from apps.modelregistry.assets import load_grid

    outcome = hazard_models.resolve(
        model,
        grid,
        overrides={"ses_per_logic_tree_path": 100},
        cells=load_grid(grid).cells,
    )

    warnings = [item for item in outcome["problems"] if item["severity"] == "warning"]
    assert any("could store up to about" in item["message"] for item in warnings)
    assert outcome["runnable"] is True


def test_the_rendered_configuration_is_what_would_run(model, grid):
    outcome = hazard_models.resolve(model, grid)
    assert "calculation_mode = event_based" in outcome["rendered"]
    assert "poes" not in outcome["rendered"]


def test_a_spec_records_the_choices_and_whether_they_run(model, grid, modeller):
    spec = hazard_models.save_spec(
        model, grid, name="Fifty years, twenty sets", actor=modeller
    )
    assert spec.is_runnable
    assert spec.overrides["investigation_time"] == 50.0
    assert HazardJobSpec.objects.count() == 1


def test_a_grid_for_another_country_is_refused(model, grid, modeller):
    grid.country_code = "NP"
    grid.save()
    with pytest.raises(hazard_models.HazardModelError, match="another's cells"):
        hazard_models.save_spec(model, grid, name="wrong country", actor=modeller)


def test_the_editor_catalogue_states_a_consequence_for_everything_it_offers():
    for item in hazard_models.catalogue():
        if item["editability"] in ("science", "discretisation"):
            assert item["consequence"], item["name"]


# -- against the published PuSGeN package ------------------------------------------------

PACKAGE = pathlib.Path(
    os.environ.get("CASS_HAZARD_PACKAGE", "")
    or "C:/Users/Daniel.Mhango/Documents/KRE_CATASTROPHE_MODEL/models/hazard"
    "/Indonesia_v2024.0.0.zip"
)

published = pytest.mark.skipif(
    not PACKAGE.is_file(),
    reason="set CASS_HAZARD_PACKAGE to the published PuSGeN release archive",
)


@published
@pytest.mark.integration
def test_the_published_indonesia_package_uploads_and_configures(db, modeller):
    """The model these rules were written against, end to end."""
    model = hazard_models.register_model(
        PACKAGE.read_bytes(),
        country_code="ID",
        version="2024.0.0",
        label="PuSGeN 2024 seismic hazard model for Indonesia",
        source_organisation="PuSGeN, for the GEM 2026 mosaic",
        licence="CC BY-NC-SA 4.0",
        actor=modeller,
    )
    assert model.published_calculation_mode == "classical"
    assert model.estimated_realizations > 100
    assert set(model.tectonic_regions) >= {
        "Active Shallow Crust",
        "Subduction Interface",
        "Subduction Intraslab",
    }
    assert set(model.intensity_measures) >= {"PGA", "SA(0.3)", "SA(0.6)", "SA(1.0)"}

    grid = pilot.register_grid("ID", actor=modeller)
    outcome = hazard_models.resolve(model, grid)
    assert outcome["runnable"] is True
    assert outcome["configuration"]["calculation_mode"] == "event_based"

    # The two conversions that are invisible without knowing to look for them.
    removed = set(outcome["conversion"]["removed"])
    assert "reference_depth_to_1pt0km_per_sec" in removed
    assert "poes" in removed
