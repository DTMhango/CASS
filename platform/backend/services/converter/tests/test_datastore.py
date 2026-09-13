"""Reading an OpenQuake datastore instead of its CSV exports.

The datastore is written in the order the workers produced it: neither by
event nor by site. That single fact is what these tests are mostly about,
because the footprint accumulator takes one complete event at a time, and a
reader that emitted an event in two pieces would produce two partial footprints
for it and nobody downstream would know.

The rest is the same rule the CSV reader holds to. Ground motion that cannot
reach a cell of the grid is refused rather than skipped -- hazard dropped
silently is the failure of section 15 one step before the loss -- and a measure
the calculation did not produce is refused rather than answered from one that
was not built for it.

The shapes here are the ones a real 3.23 datastore has: parallel eid, sid and
gmv_ columns, the measures named positionally in an attribute, custom site ids
as bytes, and an events table carrying the year each event fell in.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

h5py = pytest.importorskip("h5py")
numpy = pytest.importorskip("numpy")

from cass_converter.datastore import (  # noqa: E402 - after the extra is checked
    DatastoreError,
    events,
    metadata,
    read_ground_motion,
    site_keys,
)

AREA_PERILS = {"C-1": 1, "C-2": 2}


def build(
    path,
    *,
    rows=(
        # Deliberately out of order, as the engine writes them: event 2 before
        # event 1, and the sites interleaved.
        (2, 0, 0.40, 0.12),
        (1, 1, 0.10, 0.03),
        (2, 1, 0.35, 0.09),
        (1, 0, 0.20, 0.06),
        (3, 0, 0.00, 0.05),
    ),
    imts="PGA SA(0.3)",
    years=((1, 7), (2, 12), (3, 12)),
    custom=(b"C-1", b"C-2"),
    investigation_time=50.0,
    effective_time=1000.0,
):
    with h5py.File(path, "w") as store:
        group = store.create_group("gmf_data")
        group.create_dataset("eid", data=numpy.array([row[0] for row in rows], dtype="u4"))
        group.create_dataset("sid", data=numpy.array([row[1] for row in rows], dtype="u4"))
        for index in range(len(imts.split())):
            group.create_dataset(
                f"gmv_{index}",
                data=numpy.array([row[2 + index] for row in rows], dtype="f4"),
            )
        group.attrs["imts"] = imts
        group.attrs["investigation_time"] = investigation_time
        group.attrs["effective_time"] = effective_time
        group.attrs["num_events"] = len(years)

        sites = store.create_group("sitecol")
        sites.create_dataset("sids", data=numpy.arange(len(custom), dtype="u4"))
        if custom:
            sites.create_dataset("custom_site_id", data=numpy.array(custom, dtype="S8"))

        table = numpy.array(
            [(event, year) for event, year in years],
            dtype=[("id", "u4"), ("year", "u4")],
        )
        store.create_dataset("events", data=table)
    return path


@pytest.fixture()
def datastore(tmp_path):
    return build(tmp_path / "calc_1.hdf5")


def test_the_calculation_states_its_own_frequency(datastore):
    """Investigation time and effective time decide every annual rate."""
    facts = metadata(datastore)

    assert facts.imts == ("PGA", "SA(0.3)")
    assert facts.investigation_time == 50.0
    assert facts.effective_time == 1000.0
    # 1,000 years of effective time over a 50-year investigation is 20 runs of
    # the stochastic event set, and the reader derives it rather than being told.
    assert facts.ses_per_logic_tree_path == 20
    assert facts.event_count == 3
    assert facts.row_count == 5


def test_sites_are_identified_by_the_key_the_grid_maps(datastore):
    assert site_keys(datastore) == {0: "C-1", 1: "C-2"}


def test_every_event_arrives_whole_and_in_order(datastore):
    """The accumulator holds one event at a time, so a split event is two footprints."""
    samples = list(read_ground_motion(datastore, area_perils=AREA_PERILS))

    order = [sample.event_id for sample in samples]
    assert order == sorted(order)
    # Every row of event 1 comes before any row of event 2.
    assert order.index(2) > max(index for index, item in enumerate(order) if item == 1)


def test_a_value_carries_the_precision_the_engine_stored(datastore):
    samples = [
        sample
        for sample in read_ground_motion(datastore, area_perils=AREA_PERILS)
        if sample.event_id == 1 and sample.imt == "PGA"
    ]

    # 0.1 and 0.2 as float32, read back at the precision the calculation had
    # rather than the full binary expansion of a single-precision number.
    assert sorted(sample.value for sample in samples) == [
        Decimal("0.1"),
        Decimal("0.2"),
    ]


def test_the_measures_are_matched_to_their_columns_positionally(datastore):
    samples = [
        sample
        for sample in read_ground_motion(datastore, area_perils=AREA_PERILS)
        if sample.event_id == 1 and sample.area_peril_id == 1
    ]

    assert {sample.imt: sample.value for sample in samples} == {
        "PGA": Decimal("0.2"),
        "SA(0.3)": Decimal("0.06"),
    }


def test_asking_for_one_measure_leaves_the_others_unread(datastore):
    samples = list(
        read_ground_motion(datastore, area_perils=AREA_PERILS, imts=["SA(0.3)"])
    )

    assert {sample.imt for sample in samples} == {"SA(0.3)"}


def test_a_measure_the_calculation_did_not_produce_is_refused(datastore):
    with pytest.raises(DatastoreError, match="does not carry SA\\(1.0\\)"):
        list(read_ground_motion(datastore, area_perils=AREA_PERILS, imts=["SA(1.0)"]))


def test_a_site_with_no_area_peril_is_refused_rather_than_skipped(datastore):
    """Hazard that reaches no cell is hazard dropped, which is never silent."""
    with pytest.raises(DatastoreError, match="has no area peril"):
        list(read_ground_motion(datastore, area_perils={"C-1": 1}))


def test_a_value_of_zero_is_not_carried_as_a_measurement(datastore):
    """A zero in the store is an absence of shaking, not a measurement of none."""
    samples = [
        sample
        for sample in read_ground_motion(datastore, area_perils=AREA_PERILS)
        if sample.event_id == 3
    ]

    assert [sample.imt for sample in samples] == ["SA(0.3)"]


def test_the_occurrence_table_is_the_year_the_engine_assigned(datastore):
    rows = events(datastore)

    assert [(row.event_id, row.period_no) for row in rows] == [(1, 7), (2, 12), (3, 12)]


def test_a_batch_budget_of_one_event_reads_the_same_samples(datastore):
    """Memory follows the budget; the answer does not."""
    whole = list(read_ground_motion(datastore, area_perils=AREA_PERILS))
    batched = list(
        read_ground_motion(datastore, area_perils=AREA_PERILS, row_budget=1, chunk=2)
    )

    assert batched == whole


def test_a_datastore_without_ground_motion_says_so(tmp_path):
    path = tmp_path / "empty.hdf5"
    with h5py.File(path, "w") as store:
        store.create_group("something_else")

    with pytest.raises(DatastoreError, match="holds no gmf_data"):
        metadata(path)


def test_a_file_that_is_not_a_datastore_is_refused(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_bytes(b"not hdf5 at all")

    with pytest.raises(DatastoreError, match="not a readable HDF5"):
        metadata(path)


def test_an_events_table_with_no_year_cannot_become_periods(tmp_path):
    path = tmp_path / "calc_2.hdf5"
    build(path)
    with h5py.File(path, "a") as store:
        del store["events"]
        store.create_dataset(
            "events", data=numpy.array([(1,)], dtype=[("id", "u4")])
        )

    with pytest.raises(DatastoreError, match="carries no year"):
        events(path)


# -- the same tables the export path produces --------------------------------

def test_a_hazard_set_can_be_built_from_the_datastore(datastore):
    """Section 7: the datastore, read in slices, produces the Oasis tables."""
    from cass_converter.bins import IntensityBinSet, linear_bins
    from cass_converter.hazard_build import build_hazard_from_datastore

    bins = {
        name: IntensityBinSet(imt=name, version="1", bins=linear_bins("0", "1", 10))
        for name in ("PGA", "SA(0.3)")
    }

    hazard = build_hazard_from_datastore(
        datastore,
        country_code="ID",
        intensity_bins=bins,
        area_perils=AREA_PERILS,
    )

    assert hazard.country_code == "ID"
    assert hazard.metadata.investigation_time == 50.0
    assert hazard.metadata.ses_per_logic_tree_path == 20
    assert [row.period_no for row in hazard.occurrences] == [7, 12, 12]
    assert hazard.footprint
    # Every event that produced ground motion reaches the footprint.
    assert {row.event_id for row in hazard.footprint} == {1, 2, 3}


# -- against a datastore the engine actually wrote ---------------------------

@pytest.mark.integration
def test_a_real_datastore_reads_in_slices_with_every_event_whole():
    """Marked integration because it needs a datastore from a real calculation.

    What a fixture cannot prove is the thing that matters here: a real file is
    written in neither event nor site order, is far larger than memory, and
    names its measures positionally. Point CASS_DATASTORE_PATH at one.
    """
    import os

    stated = os.environ.get("CASS_DATASTORE_PATH")
    if not stated:
        pytest.skip("Set CASS_DATASTORE_PATH to an OpenQuake datastore to run this.")

    facts = metadata(stated)
    assert facts.imts
    assert facts.row_count > 0
    assert facts.investigation_time and facts.effective_time

    keys = site_keys(stated)
    perils = {key: index + 1 for index, key in enumerate(sorted(set(keys.values())))}

    seen: list[int] = []
    read = 0
    for sample in read_ground_motion(stated, area_perils=perils, row_budget=250_000):
        read += 1
        if not seen or seen[-1] != sample.event_id:
            seen.append(sample.event_id)
        if read >= 200_000:
            break

    assert read > 0
    # Ascending, and no event returned to after it was left: that is what the
    # footprint accumulator needs and what the file's own order does not give.
    assert seen == sorted(seen)
    assert len(seen) == len(set(seen))
