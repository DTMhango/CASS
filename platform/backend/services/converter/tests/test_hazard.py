"""Reading an OpenQuake calculation and making it an Oasis hazard set.

The fixtures below are written in the exact shape engine 3.23.4 exports --
provenance comment on the first line, ``gmv_`` prefixed measure columns,
``custom_site_id``, a ``year`` column on the events file. They are small enough
to reason about and real enough that a change in the engine's format breaks
them, which is the point: the alternative is discovering it against a
production calculation.

What these hold to account is one asymmetry. Anything that would quietly lose
hazard is refused -- a site with no area peril, a measure the export does not
carry, motion above the top bin, realisations whose weights are not all equal.
Anything that merely produces less hazard honestly is allowed: motion below the
bottom bin is dropped and counted, because it is shaking too weak to damage
anything and keeping it would double the size of every footprint.
"""

from __future__ import annotations

import dataclasses
import pathlib

import pytest

from cass_converter import hazard_build, hazard_job, openquake
from cass_converter.bins import IntensityBinSet, log_bins

HEADER = (
    "#,,,,,\"generated_by='OpenQuake engine 3.23.4', "
    "start_date='2026-09-12T16:30:50', checksum=556311143\""
)
RUPTURE_HEADER = (
    "#,,,,,,,,,,\"generated_by='OpenQuake engine 3.23.4', "
    "start_date='2026-09-12T16:30:50', checksum=556311143, "
    "investigation_time=50.0, ses_per_logic_tree_path=20\""
)

GMF = f"""{HEADER}
event_id,gmv_PGA,gmv_SA(0.3),custom_site_id
0,2.00000E-01,4.00000E-01,101
0,3.00000E-01,5.00000E-01,102
1,1.00000E-01,2.00000E-01,101
"""

EVENTS = f"""{HEADER}
event_id,rup_id,rlz_id,year,ses_id
0,2,0,90,14
1,7,0,774,9
"""

RUPTURES = f"""{RUPTURE_HEADER}
rup_id,source_id,multiplicity,mag,centroid_lon,centroid_lat,centroid_depth,trt,strike,dip,rake
2,1,1,5.1,106.5,-6.2,10.0,Active Shallow Crust,270.0,45.0,90.0
7,1,1,6.4,106.9,-6.8,20.0,Active Shallow Crust,90.0,45.0,90.0
"""

REALIZATIONS = f"""{HEADER}
rlz_id,branch_path,weight
0,A~A,1.0
"""

SITEMESH = f"""{HEADER}
custom_site_id,lon,lat
101,106.45000,-7.15000
102,106.55000,-7.15000
"""


@pytest.fixture()
def export(tmp_path) -> pathlib.Path:
    directory = tmp_path / "out"
    directory.mkdir()
    for stem, payload in (
        ("gmf-data", GMF),
        ("events", EVENTS),
        ("ruptures", RUPTURES),
        ("realizations", REALIZATIONS),
        ("sitemesh", SITEMESH),
    ):
        (directory / f"{stem}_1.csv").write_text(payload, encoding="utf-8")
    return directory


@pytest.fixture()
def bins() -> dict[str, IntensityBinSet]:
    return {
        imt: IntensityBinSet(imt=imt, version="t", bins=log_bins("0.05", "2.0", 10))
        for imt in ("PGA", "SA(0.3)")
    }


# -- the provenance line ---------------------------------------------------------------

def test_the_timing_that_decides_annual_frequency_is_read_from_the_file(export):
    """Retyping it is how it gets mistyped, and nothing downstream would notice."""
    metadata = openquake.read_metadata(export / "ruptures_1.csv")
    assert metadata.investigation_time == 50.0
    assert metadata.ses_per_logic_tree_path == 20
    assert metadata.effective_time == 1000.0
    assert metadata.period_count == 1000


def test_the_engine_version_and_checksum_are_kept(export):
    metadata = openquake.read_metadata(export / "gmf-data_1.csv")
    assert metadata.engine_version == "OpenQuake engine 3.23.4"
    assert metadata.checksum == "556311143"


def test_headers_from_two_calculations_are_refused(export, tmp_path):
    """Their events and their ground motion do not belong to each other."""
    other = tmp_path / "other.csv"
    other.write_text(HEADER.replace("556311143", "999999999"), encoding="utf-8")
    with pytest.raises(openquake.OpenQuakeError, match="different checksums"):
        openquake.read_metadata(export / "gmf-data_1.csv", other)


def test_timing_absent_from_the_header_is_refused_rather_than_defaulted(export):
    metadata = openquake.read_metadata(export / "gmf-data_1.csv")
    with pytest.raises(openquake.OpenQuakeError, match="no investigation time"):
        _ = metadata.effective_time


# -- ground motion ---------------------------------------------------------------------

def test_the_measures_the_export_carries_are_read_off_the_header(export):
    assert openquake.measures(export / "gmf-data_1.csv") == ("PGA", "SA(0.3)")


def test_every_measure_of_every_row_becomes_a_sample(export):
    samples = list(
        openquake.read_ground_motion(
            export / "gmf-data_1.csv", area_perils={"101": 101, "102": 102}
        )
    )
    assert len(samples) == 6
    assert {item.imt for item in samples} == {"PGA", "SA(0.3)"}
    assert {item.area_peril_id for item in samples} == {101, 102}


def test_a_site_with_no_area_peril_is_refused_not_skipped(export):
    """Its ground motion would vanish and the cell would report no loss."""
    with pytest.raises(openquake.OpenQuakeError, match="has no area peril"):
        list(
            openquake.read_ground_motion(
                export / "gmf-data_1.csv", area_perils={"101": 101}
            )
        )


def test_asking_for_a_measure_the_export_does_not_carry_is_refused(export):
    with pytest.raises(openquake.OpenQuakeError, match="SA\\(1.0\\)"):
        list(
            openquake.read_ground_motion(
                export / "gmf-data_1.csv",
                area_perils={"101": 101, "102": 102},
                imts=("PGA", "SA(1.0)"),
            )
        )


def test_a_calculation_covering_more_measures_than_needed_is_fine(export):
    samples = list(
        openquake.read_ground_motion(
            export / "gmf-data_1.csv",
            area_perils={"101": 101, "102": 102},
            imts=("PGA",),
        )
    )
    assert {item.imt for item in samples} == {"PGA"}


def test_rows_out_of_event_order_are_refused(export):
    """The accumulator holds one event, so it would emit two partial footprints."""
    (export / "gmf-data_1.csv").write_text(
        f"{HEADER}\nevent_id,gmv_PGA,custom_site_id\n1,0.2,101\n0,0.3,101\n",
        encoding="utf-8",
    )
    with pytest.raises(openquake.OpenQuakeError, match="not grouped by event"):
        list(
            openquake.read_ground_motion(
                export / "gmf-data_1.csv", area_perils={"101": 101}
            )
        )


# -- events and occurrence ---------------------------------------------------------------

def test_the_engines_year_becomes_the_oasis_period(export):
    """A rename, not a calculation. Any arithmetic would be a second opinion."""
    events = openquake.read_events(export / "events_1.csv")
    table = openquake.occurrences(events)
    assert [(item.event_id, item.period_no) for item in table] == [(0, 90), (1, 774)]


def test_a_repeated_event_identifier_is_refused(export):
    (export / "events_1.csv").write_text(
        f"{HEADER}\nevent_id,rup_id,rlz_id,year,ses_id\n0,2,0,90,14\n0,7,0,774,9\n",
        encoding="utf-8",
    )
    with pytest.raises(openquake.OpenQuakeError, match="repeats an event identifier"):
        openquake.read_events(export / "events_1.csv")


def test_equally_weighted_paths_are_pooled(export):
    """Sampled paths are drawn in proportion to their weights, so pooling them
    carries the model's own weighting (ADR 18)."""
    metadata = openquake.CalculationMetadata(realization_count=3)

    openquake.require_equal_realization_weights(metadata, (0.25, 0.25, 0.25))


def test_an_enumerated_logic_tree_is_still_refused():
    """Its branches carry their weights explicitly, and pooling them as equals
    would treat a low-weight branch as the model's mean."""
    metadata = openquake.CalculationMetadata(realization_count=3)

    with pytest.raises(openquake.OpenQuakeError, match="enumerated"):
        openquake.require_equal_realization_weights(metadata, (0.6, 0.3, 0.1))


def test_realisations_with_no_stated_weight_are_refused():
    """Sampled and enumerated are told apart by the weights, so their absence
    is not something to assume past."""
    metadata = openquake.CalculationMetadata(realization_count=3)

    with pytest.raises(openquake.OpenQuakeError, match="states no weight"):
        openquake.require_equal_realization_weights(metadata, ())


def test_sampled_paths_are_more_simulated_years():
    """The engine numbers years across the pooled catalogue and states the same
    effective time, so a path is years rather than an alternative history."""
    one = openquake.CalculationMetadata(investigation_time=50.0, ses_per_logic_tree_path=20)
    twenty = openquake.CalculationMetadata(
        investigation_time=50.0, ses_per_logic_tree_path=1, realization_count=20
    )

    assert one.effective_time == 1000.0
    assert twenty.effective_time == 1000.0
    assert twenty.period_count == 1000


# -- the whole build ---------------------------------------------------------------------

def test_a_calculation_becomes_a_valid_hazard_set(export, bins):
    hazard = hazard_build.build_hazard(
        export, country_code="ID", intensity_bins=bins
    )
    assert hazard.is_valid
    assert hazard.problems == ()
    assert len(hazard.events) == 2
    assert hazard.imts == ("PGA", "SA(0.3)")
    assert hazard.metadata.period_count == 1000


def test_the_annual_rate_survives_conversion(export, bins):
    """An AAL is wrong by exactly the ratio if this is not preserved."""
    hazard = hazard_build.build_hazard(
        export, country_code="ID", intensity_bins=bins
    )
    assert hazard.as_dict()["annual_rate"] == pytest.approx(2 / 1000)


def test_site_keys_that_are_already_area_perils_need_no_mapping(export, bins):
    hazard = hazard_build.build_hazard(
        export, country_code="ID", intensity_bins=bins
    )
    assert {row.area_peril_id for row in hazard.footprint} == {101, 102}


def test_site_keys_that_are_not_area_perils_are_refused_without_a_mapping(
    export, bins
):
    (export / "gmf-data_1.csv").write_text(
        GMF.replace(",101\n", ",qqez2d7k\n").replace(",102\n", ",qqexykdd\n"),
        encoding="utf-8",
    )
    with pytest.raises(hazard_build.HazardBuildError, match="not area perils"):
        hazard_build.build_hazard(export, country_code="ID", intensity_bins=bins)


def test_a_calculation_on_one_branch_publishes_no_realizations_and_is_still_read(
    export, bins
):
    """OpenQuake writes that export only where there is a logic tree to describe.

    A single-branch calculation has none, and refusing it would refuse the only
    kind this converter accepts. The events name the realisation they belong to,
    which is the same fact counted one row lower down.
    """
    (export / "realizations_1.csv").unlink()

    hazard = hazard_build.build_hazard(export, country_code="ID", intensity_bins=bins)

    assert hazard.metadata.realization_count == 1
    assert hazard.is_valid


def test_events_from_several_branches_are_refused_even_with_no_realizations_export(
    export, bins
):
    (export / "realizations_1.csv").unlink()
    (export / "events_1.csv").write_text(
        EVENTS.replace("1,7,0,774,9", "1,7,1,774,9"), encoding="utf-8"
    )

    with pytest.raises(openquake.OpenQuakeError, match="states no weight"):
        hazard_build.build_hazard(export, country_code="ID", intensity_bins=bins)


def test_two_calculations_in_one_directory_are_refused(export, bins):
    (export / "events_2.csv").write_text(EVENTS, encoding="utf-8")
    with pytest.raises(hazard_build.HazardBuildError, match="own directory"):
        hazard_build.build_hazard(export, country_code="ID", intensity_bins=bins)


def test_a_measure_with_no_bin_dictionary_is_refused(export, bins):
    """Leaving it out would answer fewer vulnerability functions, quietly."""
    with pytest.raises(hazard_build.HazardBuildError, match="No intensity-bin"):
        hazard_build.build_hazard(
            export, country_code="ID", intensity_bins={"PGA": bins["PGA"]}
        )


def test_motion_below_the_bottom_bin_is_dropped_and_counted(export, bins):
    """It damages nothing, and keeping it would double every footprint."""
    narrow = {
        imt: IntensityBinSet(imt=imt, version="t", bins=log_bins("0.25", "2.0", 5))
        for imt in ("PGA", "SA(0.3)")
    }
    hazard = hazard_build.build_hazard(
        export, country_code="ID", intensity_bins=narrow
    )
    assert hazard.metrics.samples_below_range > 0
    assert hazard.metrics.samples_above_range == 0
    assert hazard.metrics.clips_the_hazard is False


def test_motion_above_the_top_bin_is_reported_as_a_problem(export, bins):
    """It is the strongest shaking in the calculation, and it is being lost."""
    narrow = {
        imt: IntensityBinSet(imt=imt, version="t", bins=log_bins("0.01", "0.15", 5))
        for imt in ("PGA", "SA(0.3)")
    }
    hazard = hazard_build.build_hazard(
        export, country_code="ID", intensity_bins=narrow
    )
    assert hazard.metrics.clips_the_hazard is True
    assert not hazard.is_valid
    assert any("above the top intensity bin" in item for item in hazard.problems)


# -- the Oasis tables ---------------------------------------------------------------------

def test_one_footprint_is_produced_for_each_measure(export, bins):
    """An Oasis footprint carries no measure, so four measures are four files."""
    hazard = hazard_build.build_hazard(
        export, country_code="ID", intensity_bins=bins
    )
    # The footprints are files by the time this is called -- they are written
    # as the conversion streams -- so they are named by path rather than handed
    # over as bytes. The small tables still come back as payloads.
    footprints = hazard_build.table_paths(hazard)
    assert "footprint_PGA.csv" in footprints
    assert "footprint_SA0p3.csv" in footprints
    assert all(path.is_file() for path in footprints.values())
    assert "occurrence.csv" in hazard_build.tables(hazard)


def test_the_footprint_has_no_intensity_measure_column(export, bins):
    hazard = hazard_build.build_hazard(
        export, country_code="ID", intensity_bins=bins
    )
    header = hazard_build.footprint_csv(hazard, "PGA").decode().splitlines()[0]
    assert header == "event_id,areaperil_id,intensity_bin_id,probability"


def test_asking_for_a_footprint_the_set_does_not_have_is_refused(export, bins):
    hazard = hazard_build.build_hazard(
        export, country_code="ID", intensity_bins=bins
    )
    with pytest.raises(hazard_build.HazardBuildError, match="no rows for"):
        hazard_build.footprint_csv(hazard, "SA(1.0)")


def test_one_occurrence_table_is_shared_across_measures(export, bins):
    """They are the same events; a table per measure would triple the frequency."""
    hazard = hazard_build.build_hazard(
        export, country_code="ID", intensity_bins=bins
    )
    produced = hazard_build.tables(hazard)
    assert sum(1 for name in produced if name.startswith("occurrence")) == 1
    assert produced["occurrence.csv"].decode().count("\n") == 3


# -- the job the calculation runs from -----------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Cell:
    area_peril_id: int
    min_latitude: float
    max_latitude: float
    min_longitude: float
    max_longitude: float
    offshore: bool = False


CELLS = (
    Cell(1, -7.0, -6.9, 106.0, 106.1),
    Cell(2, -7.0, -6.9, 106.1, 106.2),
    Cell(3, -7.0, -6.9, 106.2, 106.3, offshore=True),
)


def job(**overrides) -> hazard_job.HazardJob:
    settings = {
        "country_code": "ID",
        "label": "test",
        "sites": hazard_job.sites_from_cells(CELLS),
        "imts": ("PGA",),
        "source_model_logic_tree": "smlt.xml",
        "gsim_logic_tree": "gmpelt.xml",
    }
    settings.update(overrides)
    return hazard_job.HazardJob(**settings)


def test_a_calculation_point_sits_at_the_centre_of_its_cell():
    """A corner belongs to four cells and would describe whichever won the join."""
    site = job().sites[0]
    assert float(site.longitude) == pytest.approx(106.05)
    assert float(site.latitude) == pytest.approx(-6.95)


def test_offshore_cells_get_no_calculation_point():
    """The keys service refuses a location there, so the hazard has no consumer."""
    assert {item.area_peril_id for item in job().sites} == {1, 2}


def test_the_site_file_carries_the_area_peril_as_the_site_identifier():
    """It removes the coordinate join, and with it a float comparison."""
    lines = hazard_job.sites_csv(job()).decode().splitlines()
    assert lines[0] == "custom_site_id,lon,lat"
    assert lines[1].startswith("1,106.05000,")


def test_the_job_declares_the_measures_and_the_effective_time():
    text = hazard_job.job_ini(job(imts=("PGA", "SA(1.0)"))).decode()
    assert "intensity_measure_types = PGA, SA(1.0)" in text
    assert "investigation_time = 50.0" in text
    assert "ses_per_logic_tree_path = 20" in text
    assert job().effective_time == 1000.0


def test_the_job_samples_the_logic_tree_rather_than_enumerating_it():
    """A sampled path is drawn in proportion to its weight, so a catalogue of
    several carries the model's own weighting; enumeration hands over branches
    of unequal weight, which the reader refuses (ADR 18)."""
    assert "number_of_logic_tree_samples = 1" in hazard_job.job_ini(job()).decode()
    sampled = hazard_job.job_ini(job(logic_tree_samples=20)).decode()
    assert "number_of_logic_tree_samples = 20" in sampled
    assert job(logic_tree_samples=20).effective_time == 20000.0


def test_a_job_covering_no_sites_is_refused():
    with pytest.raises(hazard_job.HazardJobError, match="no sites"):
        job(sites=())


def test_an_area_peril_too_long_for_a_site_identifier_is_refused():
    """OpenQuake truncates it, which silently merges two cells into one."""
    with pytest.raises(hazard_job.HazardJobError, match="truncates"):
        job(sites=(hazard_job.Site(123456789, 106.0, -6.0),))


def test_the_checksum_changes_with_the_sites_as_well_as_the_settings():
    first = hazard_job.job_checksum(job())
    assert first != hazard_job.job_checksum(job(imts=("SA(1.0)",)))
    assert first != hazard_job.job_checksum(
        job(sites=hazard_job.sites_from_cells(CELLS[:1]))
    )


def test_coverage_reports_the_onshore_cells_the_job_skips():
    """A cell with no calculation point loses nothing in the engine, silently."""
    partial = job(sites=hazard_job.sites_from_cells(CELLS[:1]))
    report = hazard_job.coverage(partial, CELLS)
    assert report["onshore_cells"] == 2
    assert report["cells_computed"] == 1
    assert report["cells_skipped"] == 1
    assert report["skipped_examples"] == [2]


def test_the_job_can_supply_the_area_perils_the_reader_needs():
    assert job().area_perils == {"1": 1, "2": 2}
