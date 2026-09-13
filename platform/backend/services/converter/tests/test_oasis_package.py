"""Assembling an Oasis model package.

Every binary this module writes is held byte-for-byte against the one the
official PiWind model ships, where PiWind is on disk. That is the only check
that means anything for a binary format: a writer that agrees with its own
reader proves the two share a mistake, and a package the engine misreads fails
as a loss that is quietly wrong rather than as an error.

The rest hold the decisions the package records: measures become area-peril
channels, a function demanding a measure the hazard does not carry is refused,
and the lookup the engine runs is the lookup CASS runs.
"""

from __future__ import annotations

import csv
import io
import json
import pathlib
import struct

import pytest

from cass_converter import oasis_package
from cass_converter.oasis_package import (
    ModelIdentity,
    PackageError,
    PackageInputs,
    channel_area_peril,
)

PIWIND = (
    pathlib.Path(__file__).resolve().parents[5]
    / ".piwind_e2e_main"
    / "model_data"
    / "PiWind"
)

piwind = pytest.mark.skipif(
    not PIWIND.is_dir(), reason="the PiWind model data is not on disk"
)


# -- channels ---------------------------------------------------------------

def test_each_measure_is_its_own_channel_of_a_cell():
    assert channel_area_peril(4217, "PGA") == 42171
    assert channel_area_peril(4217, "SA(1.0)") == 42174


def test_a_measure_with_no_channel_is_refused_rather_than_dropped():
    with pytest.raises(PackageError, match="has no channel"):
        channel_area_peril(1, "SA(2.0)")


def test_channels_of_neighbouring_cells_never_collide():
    """Ten slots per cell: cell 1's last channel sits below cell 2's first."""
    assert channel_area_peril(1, "SA(1.0)") < channel_area_peril(2, "PGA")


# -- the binaries, against PiWind -------------------------------------------

@piwind
def test_the_footprint_matches_the_engines_own_binary():
    events: dict[int, list[tuple[int, int, float]]] = {}
    with (PIWIND / "footprint.csv").open() as handle:
        for row in csv.DictReader(handle):
            events.setdefault(int(row["event_id"]), []).append(
                (int(row["areaperil_id"]), int(row["intensity_bin_id"]), float(row["probability"]))
            )
    footprint, index = io.BytesIO(), io.BytesIO()

    oasis_package.write_footprint(
        sorted(events.items()), footprint, index, intensity_bin_count=58, uncertainty=True
    )

    assert footprint.getvalue() == (PIWIND / "footprint.bin").read_bytes()
    assert index.getvalue() == (PIWIND / "footprint.idx").read_bytes()


@piwind
def test_the_vulnerability_table_matches_the_engines_own_binary():
    written = oasis_package.vulnerability_bin((PIWIND / "vulnerability.csv").read_bytes(), 12)
    assert written == (PIWIND / "vulnerability.bin").read_bytes()


@piwind
def test_the_damage_bins_match_the_engines_own_binary():
    binary, _, count = oasis_package.damage_bins_bin((PIWIND / "damage_bin_dict.csv").read_bytes())
    assert count == 12
    assert binary == (PIWIND / "damage_bin_dict.bin").read_bytes()


@piwind
def test_the_occurrence_table_matches_the_engines_own_binary():
    binary, _ = oasis_package.occurrence_bin((PIWIND / "occurrence_lt.csv").read_bytes(), 1000)
    assert binary == (PIWIND / "occurrence_lt.bin").read_bytes()


@piwind
def test_the_event_list_matches_the_engines_own_binary():
    with (PIWIND / "events_p.csv").open() as handle:
        identifiers = [int(row["event_id"]) for row in csv.DictReader(handle)]
    assert oasis_package.events_bin(identifiers) == (PIWIND / "events_p.bin").read_bytes()


@piwind
def test_the_return_periods_match_the_engines_own_binary():
    with (PIWIND / "returnperiods.csv").open() as handle:
        periods = [int(row["return_period"]) for row in csv.DictReader(handle)]
    assert oasis_package.return_periods_bin(periods) == (PIWIND / "returnperiods.bin").read_bytes()


# -- refusals ---------------------------------------------------------------

def test_an_occurrence_outside_the_event_set_is_refused():
    """A year that does not exist would count its frequency against nothing."""
    with pytest.raises(PackageError, match="outside the 1000 periods"):
        oasis_package.occurrence_bin(b"event_id,period_no\n1,1001\n", 1000)


def test_a_damage_bin_past_the_dictionary_is_refused():
    table = b"vulnerability_id,intensity_bin_id,damage_bin_id,probability\n1,1,23,1.0\n"
    with pytest.raises(PackageError, match="damage bin 23"):
        oasis_package.vulnerability_bin(table, 22)


def test_footprint_events_out_of_order_are_refused():
    with pytest.raises(PackageError, match="arrived after"):
        oasis_package.write_footprint(
            [(2, [(11, 1, 1.0)]), (1, [(11, 1, 1.0)])], io.BytesIO(), io.BytesIO()
        )


# -- merging measures -------------------------------------------------------

FOOTPRINT_PGA = b"""event_id,areaperil_id,intensity_bin_id,probability
1,7,3,1.00000000
1,9,2,1.00000000
2,7,1,1.00000000
"""

FOOTPRINT_SA03 = b"""event_id,areaperil_id,intensity_bin_id,probability
1,7,5,0.50000000
1,7,6,0.50000000
3,9,4,1.00000000
"""


def test_measures_merge_by_event_with_channel_area_perils():
    merged = dict(
        oasis_package.merged_footprint_events({"PGA": FOOTPRINT_PGA, "SA(0.3)": FOOTPRINT_SA03})
    )

    assert sorted(merged) == [1, 2, 3]
    assert sorted(merged[1]) == [(71, 3, 1.0), (72, 5, 0.5), (72, 6, 0.5), (91, 2, 1.0)]
    assert merged[3] == [(92, 4, 1.0)]


def test_an_event_merged_from_several_measures_is_written_sorted_by_area_peril():
    """The engine searches an event's rows by area peril."""
    footprint, index = io.BytesIO(), io.BytesIO()
    oasis_package.write_footprint(
        oasis_package.merged_footprint_events({"PGA": FOOTPRINT_PGA, "SA(0.3)": FOOTPRINT_SA03}),
        footprint,
        index,
    )

    data = footprint.getvalue()
    num_bins, uncertain = struct.unpack("<ii", data[:8])
    first_event = [struct.unpack("<Iif", data[8 + 12 * i : 20 + 12 * i])[0] for i in range(4)]
    assert first_event == sorted(first_event)
    assert (num_bins, uncertain) == (6, 1)


# -- the whole package ------------------------------------------------------

MAPPING = b"""VulnerabilityID,CoverageTypeID,RequiredIMT,ChannelWeight,OccupancyCodes,ConstructionCodes,StoreyBand,MinStoreys,MaxStoreys,Label
1,1,PGA,1.000000,1050,5000,low,1,3,res masonry low coverage 1
2,1,SA(0.3),1.000000,1100,5050,mid,4,7,com rc mid coverage 1
"""

GRID = b"""AreaPerilID,MinLatitude,MaxLatitude,MinLongitude,MaxLongitude,CountryCode,Offshore,Vs30
7,-6.3,-6.2,106.8,106.9,ID,0,
9,-6.2,-6.1,106.8,106.9,ID,0,
"""

VULNERABILITY = b"""vulnerability_id,intensity_bin_id,damage_bin_id,probability
1,1,1,1.0
2,1,2,1.0
"""

DAMAGE_BINS = b"""bin_index,bin_from,bin_to,interpolation,interval_type
1,0,0,0,1201
2,0,1,0.5,1201
"""

OCCURRENCE = b"""event_id,period_no
1,4
2,40
3,700
"""


def inputs(**overrides) -> PackageInputs:
    values = {
        "identity": ModelIdentity(),
        "country_code": "ID",
        "grid_version": "0.1.0-draft",
        "grid_tolerance_km": "0",
        "imt_representation": "undecided",
        "grid_cells_csv": GRID,
        "mapping_csv": MAPPING,
        "vulnerability_csv": VULNERABILITY,
        "damage_bins_csv": DAMAGE_BINS,
        "footprints": {"PGA": FOOTPRINT_PGA, "SA(0.3)": FOOTPRINT_SA03},
        "occurrence_csv": OCCURRENCE,
        "period_count": 1000,
        "vendored": {"cass_keys/__init__.py": b""},
        "intensity_bin_count": 50,
    }
    values.update(overrides)
    return PackageInputs(**values)


def test_a_package_has_the_layout_an_oasis_worker_mounts(tmp_path):
    manifest = oasis_package.build(inputs(), tmp_path)

    for path in (
        "oasislmf.json",
        "meta-data/model_settings.json",
        "keys_data/lookup_config.json",
        "keys_data/lookup.py",
        "keys_data/grid_cells.csv",
        "keys_data/vulnerability_mapping.csv",
        "keys_data/vendor/cass_keys/__init__.py",
        "model_data/footprint.bin",
        "model_data/footprint.idx",
        "model_data/vulnerability.bin",
        "model_data/damage_bin_dict.bin",
        "model_data/events.bin",
        "model_data/events_p.bin",
        "model_data/occurrence.bin",
        "model_data/occurrence_lt.bin",
        "model_data/returnperiods.bin",
        "MANIFEST.json",
    ):
        assert (tmp_path / path).is_file(), path
    assert manifest["events"] == 3
    assert "model_data/footprint.bin" in manifest["files"]


def test_the_lookup_is_named_as_oasislmf_loads_it(tmp_path):
    """oasislmf loads ``<model_id>KeysLookup`` from the module path."""
    oasis_package.build(inputs(), tmp_path)

    config = json.loads((tmp_path / "keys_data/lookup_config.json").read_text())
    assert config["model"]["model_id"] == "EQ"
    assert config["lookup_module_path"] == "lookup.py"
    assert "class EQKeysLookup" in (tmp_path / "keys_data/lookup.py").read_text()


def test_the_lookup_source_is_valid_python():
    compile(oasis_package.LOOKUP_SOURCE, "lookup.py", "exec")


def test_the_engine_is_told_where_everything_is(tmp_path):
    oasis_package.build(inputs(), tmp_path)

    config = json.loads((tmp_path / "oasislmf.json").read_text())
    assert config["model_data_dir"] == "model_data"
    assert config["lookup_config_json"] == "keys_data/lookup_config.json"
    settings = json.loads((tmp_path / "meta-data/model_settings.json").read_text())
    assert settings["lookup_settings"]["supported_perils"][0]["id"] == "QEQ"


def test_a_function_demanding_a_measure_the_hazard_lacks_is_refused(tmp_path):
    """Answered by nothing, it would report zero -- indistinguishable from no damage."""
    with pytest.raises(PackageError, match="demand SA\\(0.3\\)"):
        oasis_package.build(inputs(footprints={"PGA": FOOTPRINT_PGA}), tmp_path)


def test_the_manifest_records_the_representation_the_package_was_built_under(tmp_path):
    manifest = oasis_package.build(inputs(), tmp_path)

    assert manifest["imt_channel_codes"]["SA(0.3)"] == 2
    assert manifest["channel_base"] == 10
    assert manifest["measures_demanded"] == ["PGA", "SA(0.3)"]


# -- the lookup the engine runs ------------------------------------------------------


def load_lookup(root: pathlib.Path):
    """Execute the packaged lookup with the engine's own imports stubbed out.

    The point is the adapter code -- how the location frame is read and what a
    key becomes -- not oasislmf. Stubbing its base class and pandas keeps the
    check here, where a change to the adapter can be caught, rather than only in
    a live engine run.
    """
    import sys
    import types

    class Frame(list):
        def to_dict(self, _orient):
            return list(self)

    pandas = types.ModuleType("pandas")
    pandas.DataFrame = Frame
    base = types.ModuleType("oasislmf.lookup.base")

    class Stub:
        def __init__(self, config, **_kwargs):
            self.config = config

    base.AbstractBasicKeyLookup = Stub
    base.MultiprocLookupMixin = object

    lookup_package = types.ModuleType("oasislmf.lookup")
    lookup_package.base = base
    oasislmf = types.ModuleType("oasislmf")
    oasislmf.lookup = lookup_package

    saved = {name: sys.modules.get(name) for name in
             ("pandas", "oasislmf", "oasislmf.lookup", "oasislmf.lookup.base")}
    sys.modules.update({
        "pandas": pandas,
        "oasislmf": oasislmf,
        "oasislmf.lookup": lookup_package,
        "oasislmf.lookup.base": base,
    })
    try:
        namespace: dict = {
            "__name__": "packaged_lookup",
            "__file__": str(root / "keys_data" / "lookup.py"),
        }
        exec(compile(oasis_package.LOOKUP_SOURCE, "lookup.py", "exec"), namespace)
        engine = namespace["EQKeysLookup"](
            {"keys_data_path": str(root / "keys_data")}, config_dir=str(root / "keys_data")
        )
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return engine, Frame


LOCATION = {
    "PortNumber": "P1",
    "AccNumber": "A1",
    "LocNumber": "L1",
    "Latitude": -6.25,
    "Longitude": 106.85,
    "OccupancyCode": "1050",
    "ConstructionCode": "5000",
    "NumberOfStoreys": 2,
    "LocPerilsCovered": "QEQ",
    "BuildingTIV": "100000",
    "loc_id": 1,
}


@pytest.mark.parametrize("spelling", ["oed", "lower"])
def test_the_lookup_keys_a_location_however_the_engine_spells_its_columns(
    tmp_path, spelling
):
    """oasislmf hands the frame over in OED's own case on one path and lower-cased
    on another. Reading only one of them loses every identifier, and a portfolio
    the model can key perfectly comes back as one it could not key at all."""
    oasis_package.build(inputs(), tmp_path)
    engine, Frame = load_lookup(tmp_path)
    record = (
        LOCATION
        if spelling == "oed"
        else {key.lower(): value for key, value in LOCATION.items()}
    )

    keys = engine.process_locations(Frame([record]))

    building = [key for key in keys if key["coverage_type"] == 1]
    assert [key["status"] for key in building] == ["success"]
    assert building[0]["loc_id"] == 1
    assert building[0]["vulnerability_id"] == 1
    # Area peril 7, PGA: the channel encoding the footprint is written under.
    assert building[0]["area_peril_id"] == channel_area_peril(7, "PGA")


# -- event identifiers ---------------------------------------------------------------

ZERO_BASED_PGA = b"""event_id,areaperil_id,intensity_bin_id,probability
0,7,3,1.00000000
1,7,1,1.00000000
"""

ZERO_BASED_SA03 = b"""event_id,areaperil_id,intensity_bin_id,probability
0,9,4,1.00000000
"""

ZERO_BASED_OCCURRENCE = b"""event_id,period_no
0,4
1,40
"""


def test_events_numbered_from_zero_are_shifted_onto_the_engine_s_numbering(tmp_path):
    """ktools reserves zero, and an event numbered there is read as no event.

    OpenQuake numbers its events from zero, so this is the ordinary case. Left
    alone, the financial module treats the event after the zeroth as a
    continuation of it, pushes every item twice and writes past the end of a
    node's children -- a segmentation fault in the middle of a loss calculation.
    """
    manifest = oasis_package.build(
        inputs(
            footprints={"PGA": ZERO_BASED_PGA, "SA(0.3)": ZERO_BASED_SA03},
            occurrence_csv=ZERO_BASED_OCCURRENCE,
        ),
        tmp_path,
    )

    assert manifest["event_id_offset"] == 1
    events = struct.unpack("<2i", (tmp_path / "model_data/events.bin").read_bytes())
    assert events == (1, 2)
    occurrence = (tmp_path / "model_data/occurrence.bin").read_bytes()
    rows = [
        struct.unpack("<iii", occurrence[position:position + 12])
        for position in range(8, len(occurrence), 12)
    ]
    assert [row[0] for row in rows] == [1, 2]
    index = (tmp_path / "model_data/footprint.idx").read_bytes()
    assert struct.unpack("<iqq", index[:20])[0] == 1


def test_events_already_numbered_from_one_are_left_alone(tmp_path):
    """A shift nobody needs would renumber a published hazard set for nothing."""
    manifest = oasis_package.build(inputs(), tmp_path)

    assert manifest["event_id_offset"] == 0
    events = struct.unpack("<3i", (tmp_path / "model_data/events.bin").read_bytes())
    assert events == (1, 2, 3)


def test_a_footprint_event_the_engine_cannot_name_is_refused(tmp_path):
    """Reached only if the offset is wrong: it is the check behind the shift."""
    with pytest.raises(PackageError, match="not a usable engine identifier"):
        oasis_package.write_footprint(
            [(0, [(7, 3, 1.0)])], io.BytesIO(), io.BytesIO(), intensity_bin_count=50
        )


# -- reading the index back ---------------------------------------------------

def test_the_footprint_index_reads_back_what_was_written():
    """The control plane chooses smoke events from this, so it must read true."""
    footprint, index = io.BytesIO(), io.BytesIO()
    oasis_package.write_footprint(
        [(1, [(10, 2, 1.0)]), (4, [(10, 1, 0.5), (11, 3, 0.5)])],
        footprint,
        index,
        intensity_bin_count=50,
    )

    entries = oasis_package.read_footprint_index(index.getvalue())

    assert [(item.event_id, item.size) for item in entries] == [(1, 12), (4, 24)]
    # Rows begin after the eight-byte header, and each event follows the last.
    assert [item.offset for item in entries] == [8, 20]


def test_an_index_that_is_not_whole_rows_is_refused():
    with pytest.raises(PackageError, match="whole number"):
        oasis_package.read_footprint_index(b"\0" * 21)
