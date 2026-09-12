"""Reading, editing and converting a published OpenQuake job configuration.

The fixture below is the real PuSGeN 2024 Indonesia configuration, trimmed to
the settings that matter. Using the real one matters: every rule here exists
because that file broke something, and a tidied-up invention would not have.

Three properties carry the weight.

**Nothing is dropped silently.** A published model may carry settings the
platform does not model. They are preserved and reported as unrecognised,
because discarding one would change the science without saying so.

**The conversion is a list, not a diff.** Turning a classical model into an
event-based one changes ten things, and a reviewer has to be able to read what
changed rather than compare two ini files.

**The refusals happen before the run.** A national calculation is hours. Every
condition that would make it produce nothing usable -- the wrong mode, hundreds
of realisations, a missing measure, ground motion fields switched off -- is
checked while somebody is still looking at the screen.
"""

from __future__ import annotations

import pytest

from cass_converter import job_config
from cass_converter.job_config import Editability, JobConfig, JobConfigError

PUBLISHED = """[general]
description = PSHA_Indo 2024, GEM 2026 run
calculation_mode = classical
random_seed = 23

[geometry]
sites_csv = iid-10km.csv

[logic_tree]
number_of_logic_tree_samples = 0

[erf]
width_of_mfd_bin = 0.1
rupture_mesh_spacing = 5.0
area_source_discretization = 10.0
complex_fault_mesh_spacing = 20.0

[site_params]
reference_vs30_type = measured
reference_vs30_value = 800.0
reference_depth_to_1pt0km_per_sec = -999
reference_depth_to_2pt5km_per_sec = -999

[calculation]
source_model_logic_tree_file = ssmLT_clean.xml
gsim_logic_tree_file = gmmLT.xml
maximum_distance = {'Subduction Interface':1000.0,'default':300.0}
truncation_level = 5
investigation_time = 1.
intensity_measure_types_and_levels = {"PGA": [0.001, 0.005], "SA(0.3)": [0.001, 0.005], "SA(0.6)": [0.001], "SA(1.0)": [0.001]}
horiz_comp_to_geom_mean = true
reqv_file = {"Active Shallow Crust": "lookup_reqv_asc.hdf5"}
ps_grid_spacing = 50.0
use_rates = true

[output]
export_dir = ../out
mean_hazard_curves = true
hazard_maps = true
uniform_hazard_spectra = true
poes = 0.002105 0.000404
"""

MEASURES = ("PGA", "SA(0.3)", "SA(0.6)", "SA(1.0)")


@pytest.fixture()
def published() -> JobConfig:
    return JobConfig.parse(PUBLISHED, source_name="job_clean.ini")


# -- reading -----------------------------------------------------------------------

def test_the_published_configuration_reads(published):
    assert published.mode == "classical"
    assert published.is_event_based is False
    assert published.get("truncation_level") == "5"
    assert published.checksum


def test_a_value_containing_a_dict_survives_being_read(published):
    """maximum_distance is per tectonic region, and losing that would drop the
    megathrust."""
    assert published.get("maximum_distance") == (
        "{'Subduction Interface':1000.0,'default':300.0}"
    )


def test_the_measures_are_read_from_whichever_form_states_them(published):
    """Classical states measures with levels; event-based states them plainly."""
    assert set(published.measures()) == set(MEASURES)


def test_settings_the_platform_does_not_model_are_kept_and_named(published):
    """Dropping one would change the science without saying so."""
    assert "use_rates" in published.unrecognised
    assert "horiz_comp_to_geom_mean" in published.unrecognised
    assert published.get("use_rates") == "true"


def test_every_setting_carries_what_it_is_for(published):
    described = [item for item in published.settings if item.recognised]
    assert all(item.parameter.label for item in described)
    assert all(item.parameter.help_text for item in described)


def test_the_model_s_own_science_is_not_offered_for_editing(published):
    """Replacing a logic tree is not configuring this model, it is another one."""
    trees = {"source_model_logic_tree_file", "gsim_logic_tree_file", "reqv_file"}
    for item in published.settings:
        if item.name in trees:
            assert item.parameter.editability is Editability.MODEL


def test_a_file_that_is_not_a_job_ini_is_refused():
    with pytest.raises(JobConfigError, match="before any"):
        JobConfig.parse("calculation_mode = classical\n")


def test_a_line_that_is_neither_a_section_nor_a_setting_is_refused():
    with pytest.raises(JobConfigError, match="neither a section"):
        JobConfig.parse("[general]\nnonsense\n")


def test_comments_and_blank_lines_are_ignored():
    config = JobConfig.parse("# a note\n[general]\n\ncalculation_mode = classical\n")
    assert config.mode == "classical"


# -- editing ------------------------------------------------------------------------

def test_setting_a_value_leaves_the_original_untouched(published):
    changed = published.set("truncation_level", 3)
    assert changed.get("truncation_level") == "3"
    assert published.get("truncation_level") == "5"


def test_a_new_setting_lands_in_the_section_its_parameter_belongs_to(published):
    changed = published.set("ses_per_logic_tree_path", 20)
    rendered = changed.render().decode()
    calculation = rendered.split("[calculation]")[1]
    assert "ses_per_logic_tree_path = 20" in calculation


def test_a_setting_the_platform_does_not_know_needs_its_section_named(published):
    with pytest.raises(JobConfigError, match="no section"):
        published.set("some_new_engine_flag", "true")
    assert published.set("some_new_engine_flag", "true", section="calculation")


def test_rendering_preserves_every_setting_that_was_read(published):
    rendered = JobConfig.parse(published.render())
    assert {item.name for item in rendered.settings} == {
        item.name for item in published.settings
    }


def test_a_boolean_renders_the_way_the_engine_reads_it(published):
    assert "ground_motion_fields = true" in published.set(
        "ground_motion_fields", True
    ).render().decode()


def test_a_list_of_measures_renders_as_the_engine_expects(published):
    rendered = published.set("intensity_measure_types", list(MEASURES)).render()
    assert "intensity_measure_types = PGA, SA(0.3), SA(0.6), SA(1.0)" in (
        rendered.decode()
    )


# -- validation ----------------------------------------------------------------------

def test_a_classical_configuration_cannot_make_a_footprint(published):
    problems = job_config.validate(published, required_measures=MEASURES)
    assert any(
        item.parameter == "calculation_mode" and item.severity == "error"
        for item in problems
    )


def test_full_enumeration_is_refused_because_it_is_many_realisations(published):
    problems = job_config.validate(published)
    message = next(
        item.message
        for item in problems
        if item.parameter == "number_of_logic_tree_samples"
    )
    assert "weighting rule" in message


def test_a_measure_the_calculation_does_not_produce_is_refused(published):
    problems = job_config.validate(published, required_measures=("SA(4.0)",))
    assert any("SA(4.0)" in item.message for item in problems)


def test_an_event_based_run_with_no_ground_motion_fields_is_refused(published):
    converted = published.set("calculation_mode", "event_based").set(
        "ground_motion_fields", False
    )
    problems = job_config.validate(converted)
    assert any(item.parameter == "ground_motion_fields" for item in problems)


def test_a_short_event_set_is_a_warning_rather_than_a_refusal(published):
    """It can still run; a return period just cannot be read beyond it."""
    short = (
        published.set("calculation_mode", "event_based")
        .set("ground_motion_fields", True)
        .set("investigation_time", 1)
        .set("ses_per_logic_tree_path", 10)
        .set("number_of_logic_tree_samples", 1)
    )
    problems = job_config.validate(short)
    warning = next(
        item for item in problems if item.parameter == "ses_per_logic_tree_path"
    )
    assert warning.severity == "warning"
    assert "10 years" in warning.message


def test_truncating_variability_low_warns_about_the_tail(published):
    problems = job_config.validate(published.set("truncation_level", 2))
    assert any(
        item.parameter == "truncation_level" and "tail" not in item.message.lower()
        or "strongest shaking" in item.message
        for item in problems
    )


# -- the conversion --------------------------------------------------------------------

@pytest.fixture()
def converted(published):
    return job_config.to_event_based(
        published,
        measures=MEASURES,
        investigation_time=50.0,
        ses_per_logic_tree_path=20,
        minimum_intensity=0.005,
        site_model_file="sites.csv",
    )


def test_the_converted_configuration_can_make_a_footprint(converted):
    assert job_config.validate(converted.config, required_measures=MEASURES) == []


def test_the_conversion_reports_every_change_it_made(converted):
    changed = {name for name, _, _ in converted.changes}
    assert "calculation_mode" in changed
    assert "number_of_logic_tree_samples" in changed
    assert "ses_per_logic_tree_path" in changed
    assert "intensity_measure_types" in changed


def test_the_model_s_own_science_is_left_exactly_as_published(published, converted):
    for name in (
        "source_model_logic_tree_file",
        "gsim_logic_tree_file",
        "reqv_file",
        "maximum_distance",
        "truncation_level",
        "rupture_mesh_spacing",
        "complex_fault_mesh_spacing",
    ):
        assert converted.config.get(name) == published.get(name)


def test_classical_only_outputs_are_removed_and_named(converted):
    assert "poes" in converted.removed
    assert "hazard_maps" in converted.removed
    assert "intensity_measure_types_and_levels" in converted.removed
    assert "poes" not in converted.config


def test_the_not_specified_sentinel_is_removed_because_event_based_refuses_it(
    converted,
):
    """-999 means 'not measured'; an event-based run validates it as positive."""
    assert "reference_depth_to_1pt0km_per_sec" in converted.removed
    assert any("-999" in note for note in converted.notes)


def test_a_site_model_replaces_the_sites_file_rather_than_joining_it(converted):
    """Engine 3.23 refuses both, and two lists of where to calculate is one too
    many."""
    assert converted.config.get("site_model_file") == "sites.csv"
    assert "sites_csv" not in converted.config


def test_the_conversion_says_it_is_one_realisation_not_the_mean(converted):
    assert any("one realisation" in note for note in converted.notes)


def test_settings_the_platform_does_not_model_survive_the_conversion(converted):
    """A published model's own flags are not the converter's to discard."""
    assert converted.config.get("use_rates") == "true"
    assert converted.config.get("horiz_comp_to_geom_mean") == "true"


# -- what the logic trees cost ------------------------------------------------------

SOURCE_TREE = """<nrml>
  <logicTree>
    <logicTreeBranchSet uncertaintyType="sourceModel">
      <logicTreeBranch branchID="b1"><uncertaintyWeight>0.9</uncertaintyWeight></logicTreeBranch>
      <logicTreeBranch branchID="b2"><uncertaintyWeight>0.1</uncertaintyWeight></logicTreeBranch>
    </logicTreeBranchSet>
  </logicTree>
</nrml>
"""

GSIM_TREE = """<nrml>
  <logicTree>
    <logicTreeBranchSet uncertaintyType="gmpeModel" applyToTectonicRegionType="Active Shallow Crust">
      <logicTreeBranch branchID="a1"/><logicTreeBranch branchID="a2"/><logicTreeBranch branchID="a3"/>
    </logicTreeBranchSet>
    <logicTreeBranchSet uncertaintyType="gmpeModel" applyToTectonicRegionType="Subduction Interface">
      <logicTreeBranch branchID="s1"/><logicTreeBranch branchID="s2"/><logicTreeBranch branchID="s3"/>
    </logicTreeBranchSet>
  </logicTree>
</nrml>
"""


def test_the_realisation_count_is_known_before_anything_runs():
    """Finding out from a failed conversion after four hours is the expensive way."""
    summary = job_config.logic_tree_summary(SOURCE_TREE, GSIM_TREE)
    assert summary["estimated_realizations"] == 2 * 3 * 3
    assert summary["tectonic_regions"] == [
        "Active Shallow Crust",
        "Subduction Interface",
    ]


def test_the_estimate_says_it_is_an_estimate():
    summary = job_config.logic_tree_summary(SOURCE_TREE, GSIM_TREE)
    assert "upper bound" in summary["note"]


# -- what an editor renders -----------------------------------------------------------

def test_every_parameter_the_platform_offers_states_a_consequence():
    """A field with no stated consequence is a field somebody will change blind."""
    for item in job_config.parameter_catalogue():
        if item["editability"] in ("science", "discretisation"):
            assert item["consequence"], item["name"]


def test_the_description_separates_what_can_be_edited_from_what_cannot(published):
    described = job_config.describe(published, required_measures=MEASURES)
    assert "source_model_logic_tree_file" in described["editable"]["model"]
    assert "truncation_level" in described["editable"]["science"]
    assert "rupture_mesh_spacing" in described["editable"]["discretisation"]
    assert described["runnable"] is False
