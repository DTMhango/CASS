"""The model assets the keys service reads.

Section 4 keeps large arrays out of Django and section 5 puts them in the
artifact store behind a reference, so a grid and a taxonomy reach the keys
service through this module or not at all. These tests cover the reading, and
the refusals -- an asset that cannot be parsed has to be rejected when someone
publishes it, not discovered by the first run that needs it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.artifacts.models import Artifact, ArtifactLink
from apps.modelregistry.assets import (
    GRID_CELLS_ROLE,
    VULNERABILITY_MAPPING_ROLE,
    ModelAssetError,
    attach_grid_cells,
    attach_vulnerability_mapping,
    load_grid,
    load_vulnerability,
)
from apps.modelregistry.models import AreaPerilGrid, VulnerabilitySet
from cass_core.artifacts import AccessPolicy, RetentionClass

pytestmark = pytest.mark.django_db

CELLS = (
    b"AreaPerilID,MinLatitude,MaxLatitude,MinLongitude,MaxLongitude,CountryCode,Offshore,Vs30\n"
    b"1,-7.0,-6.0,106.0,108.0,ID,false,320\n"
    b"2,-8.0,-7.0,112.0,113.0,ID,true,\n"
)

MAPPING = (
    b"VulnerabilityID,CoverageTypeID,RequiredIMT,OccupancyCodes,ConstructionCodes,Label\n"
    b"1,1,SA(0.3),1100|1200,5000,Mid-rise reinforced concrete\n"
    b"2,3,PGA,,,Generic contents\n"
)


@pytest.fixture()
def grid(db, modeller) -> AreaPerilGrid:
    return AreaPerilGrid.objects.create(
        country_code="ID",
        version="0.1.0",
        label="Indonesia prototype grid",
        base_resolution_deg="0.100000",
        refined_resolution_deg="0.025000",
        mapping_tolerance_km="1.50",
        created_by=modeller,
    )


@pytest.fixture()
def vulnerability(db, modeller) -> VulnerabilitySet:
    return VulnerabilitySet.objects.create(
        country_code="ID", version="2026.0.0", source="GEM", created_by=modeller
    )


# -- grids -------------------------------------------------------------------

def test_a_grid_round_trips_through_the_artifact_store(grid, modeller):
    attach_grid_cells(grid, CELLS, actor=modeller)
    loaded = load_grid(grid)

    assert loaded.reference == "id-grid-0.1.0"
    assert loaded.tolerance_km == Decimal("1.50")
    assert len(loaded.cells) == 2


def test_a_cell_carries_its_extent_offshore_flag_and_site_condition(grid, modeller):
    attach_grid_cells(grid, CELLS, actor=modeller)
    first, second = load_grid(grid).cells

    assert first.area_peril_id == 1
    assert first.min_latitude == Decimal("-7.0")
    assert first.max_longitude == Decimal("108.0")
    assert first.offshore is False
    assert first.vs30 == 320
    assert second.offshore is True
    assert second.vs30 is None


def test_a_loaded_grid_finds_the_cell_a_location_falls_in(grid, modeller):
    attach_grid_cells(grid, CELLS, actor=modeller)
    loaded = load_grid(grid)

    jakarta = loaded.find(Decimal("-6.2088"), Decimal("106.8456"))
    assert jakarta is not None and jakarta.area_peril_id == 1
    assert loaded.find(Decimal("0.0"), Decimal("0.0")) is None


def test_attaching_a_grid_records_the_cell_count_on_the_registry(grid, modeller):
    """The count a reviewer reads must be the count the file actually holds."""
    attach_grid_cells(grid, CELLS, actor=modeller)
    grid.refresh_from_db()
    assert grid.cell_count == 2


def test_a_grid_asset_is_model_data_rather_than_project_data(grid, modeller):
    """Entitlement follows the model version, not a project membership."""
    artifact = attach_grid_cells(grid, CELLS, actor=modeller)
    assert artifact.project_id is None
    assert artifact.retention == str(RetentionClass.MODEL_ASSET)
    assert artifact.access == str(AccessPolicy.MODEL)


def test_a_grid_with_no_cells_is_refused_when_it_is_published(grid, modeller):
    header = b"AreaPerilID,MinLatitude,MaxLatitude,MinLongitude,MaxLongitude\n"
    with pytest.raises(ModelAssetError, match="no cells"):
        attach_grid_cells(grid, header, actor=modeller)


def test_a_grid_missing_a_required_column_is_refused(grid, modeller):
    broken = b"AreaPerilID,MinLatitude,MaxLatitude\n1,-7.0,-6.0\n"
    with pytest.raises(ModelAssetError, match="missing required columns"):
        attach_grid_cells(grid, broken, actor=modeller)


def test_an_inverted_cell_is_refused_because_it_would_map_nothing(grid, modeller):
    inverted = (
        b"AreaPerilID,MinLatitude,MaxLatitude,MinLongitude,MaxLongitude\n"
        b"1,-6.0,-7.0,106.0,108.0\n"
    )
    with pytest.raises(ModelAssetError, match="empty or inverted"):
        attach_grid_cells(grid, inverted, actor=modeller)


def test_an_unreadable_coordinate_names_the_line_it_is_on(grid, modeller):
    broken = (
        b"AreaPerilID,MinLatitude,MaxLatitude,MinLongitude,MaxLongitude\n"
        b"1,-7.0,-6.0,106.0,108.0\n"
        b"2,north,-6.0,106.0,108.0\n"
    )
    with pytest.raises(ModelAssetError, match="line 3"):
        attach_grid_cells(grid, broken, actor=modeller)


def test_a_grid_with_no_registered_cells_says_what_to_do(grid):
    with pytest.raises(ModelAssetError, match="Attach it to the model version"):
        load_grid(grid)


def test_replacing_a_grid_file_loads_the_newer_one(grid, modeller):
    attach_grid_cells(grid, CELLS, actor=modeller)
    smaller = (
        b"AreaPerilID,MinLatitude,MaxLatitude,MinLongitude,MaxLongitude\n"
        b"9,-7.0,-6.0,106.0,108.0\n"
    )
    attach_grid_cells(grid, smaller, actor=modeller)

    loaded = load_grid(grid)
    assert [cell.area_peril_id for cell in loaded.cells] == [9]


# -- vulnerability -----------------------------------------------------------

def test_a_mapping_round_trips_through_the_artifact_store(vulnerability, modeller):
    attach_vulnerability_mapping(vulnerability, MAPPING, actor=modeller)
    loaded = load_vulnerability(vulnerability)

    assert loaded.country_code == "ID"
    assert len(loaded.entries) == 2


def test_taxonomy_codes_are_read_as_a_set(vulnerability, modeller):
    attach_vulnerability_mapping(vulnerability, MAPPING, actor=modeller)
    first = load_vulnerability(vulnerability).entries[0]

    assert first.occupancy_codes == frozenset({"1100", "1200"})
    assert first.construction_codes == frozenset({"5000"})
    assert first.label == "Mid-rise reinforced concrete"


def test_an_entry_with_no_codes_matches_anything(vulnerability, modeller):
    attach_vulnerability_mapping(vulnerability, MAPPING, actor=modeller)
    loaded = load_vulnerability(vulnerability)

    contents = loaded.find(occupancy="9999", construction="9999", coverage_type=3)
    assert contents is not None and contents.vulnerability_id == 2


def test_the_supported_imts_stay_at_the_release_default(vulnerability, modeller):
    """Which IMTs exist is a property of the converter, not of the taxonomy."""
    attach_vulnerability_mapping(vulnerability, MAPPING, actor=modeller)
    loaded = load_vulnerability(vulnerability)

    assert "SA(0.3)" in loaded.supported_imts
    assert "PGA" not in loaded.supported_imts


def test_a_function_without_a_required_imt_is_refused(vulnerability, modeller):
    """A function with no demand cannot be routed to a hazard channel."""
    broken = b"VulnerabilityID,CoverageTypeID,RequiredIMT\n1,1,\n"
    with pytest.raises(ModelAssetError, match="no required intensity measure"):
        attach_vulnerability_mapping(vulnerability, broken, actor=modeller)


def test_a_mapping_with_no_functions_is_refused(vulnerability, modeller):
    with pytest.raises(ModelAssetError, match="no functions"):
        attach_vulnerability_mapping(
            vulnerability, b"VulnerabilityID,CoverageTypeID,RequiredIMT\n", actor=modeller
        )


def test_a_mapping_with_an_unreadable_identifier_names_the_line(vulnerability, modeller):
    broken = b"VulnerabilityID,CoverageTypeID,RequiredIMT\nfirst,1,SA(0.3)\n"
    with pytest.raises(ModelAssetError, match="line 2"):
        attach_vulnerability_mapping(vulnerability, broken, actor=modeller)


def test_a_vulnerability_set_with_no_registered_mapping_says_what_to_do(vulnerability):
    with pytest.raises(ModelAssetError, match="Attach it to the model version"):
        load_vulnerability(vulnerability)


# -- lineage -----------------------------------------------------------------

def test_each_asset_is_linked_to_its_registry_record(grid, vulnerability, modeller):
    attach_grid_cells(grid, CELLS, actor=modeller)
    attach_vulnerability_mapping(vulnerability, MAPPING, actor=modeller)

    assert ArtifactLink.objects.filter(
        subject_type="area_peril_grid", subject_id=grid.id, role=GRID_CELLS_ROLE
    ).exists()
    assert ArtifactLink.objects.filter(
        subject_type="vulnerability_set",
        subject_id=vulnerability.id,
        role=VULNERABILITY_MAPPING_ROLE,
    ).exists()


def test_an_asset_carries_a_checksum_for_the_run_manifest(grid, modeller):
    attach_grid_cells(grid, CELLS, actor=modeller)
    artifact = Artifact.objects.get(role=GRID_CELLS_ROLE)
    assert artifact.checksum.startswith("sha256:")
    assert artifact.size_bytes == len(CELLS)
