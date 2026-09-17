"""The footprint written straight to files, and the properties that allows.

A national footprint is hundreds of millions of rows, so the conversion cannot
hold one. These are the three things that have to be true for it not to: the
file is the same table a sorted rendering would produce, the summary is counted
correctly on the way past, and the memory it costs does not follow the row
count.
"""

from __future__ import annotations

import gzip
import tracemalloc
from decimal import Decimal

import pytest

from cass_converter.footprint import FootprintError, FootprintRow
from cass_converter.footprint_tables import FootprintTableWriter, open_table


def row(event, cell, imt, bin_id, probability):
    return FootprintRow(
        event_id=event,
        area_peril_id=cell,
        imt=imt,
        intensity_bin_id=bin_id,
        probability=probability,
    )


#: Rows in the order the accumulator emits them: events ascending, then cell
#: and measure, then bin.
ROWS = [
    row(1, 10, "PGA", 3, 0.25),
    row(1, 10, "PGA", 4, 0.75),
    row(1, 10, "SA(0.3)", 2, 1.0),
    row(1, 11, "PGA", 5, 1.0),
    row(2, 10, "PGA", 6, 1.0),
]


@pytest.fixture()
def written(tmp_path):
    writer = FootprintTableWriter(tmp_path)
    writer.extend(ROWS)
    return writer.close(expected_event_ids=[1, 2, 3])


# -- the table itself --------------------------------------------------------------

def test_the_file_is_the_table_a_sorted_rendering_would_produce(written):
    """Written in one pass, and identical to sorting the rows afterwards.

    The accumulator emits events in ascending order and, within an event,
    ``sorted((cell, measure))`` and then ascending bins -- so filtering that to
    one measure is already event, cell, bin. This is that claim as a test: if it
    ever stopped holding, a footprint would be written in an order the engine
    reads as a different hazard.
    """
    expected = ["event_id,areaperil_id,intensity_bin_id,probability"]
    expected += [
        f"{item.event_id},{item.area_peril_id},{item.intensity_bin_id},"
        f"{item.probability:.8f}"
        for item in sorted(
            (item for item in ROWS if item.imt == "PGA"),
            key=lambda item: (item.event_id, item.area_peril_id, item.intensity_bin_id),
        )
    ]
    with gzip.open(written.path_for("PGA"), "rt", encoding="utf-8") as handle:
        produced = handle.read().splitlines()
    assert produced == expected


def test_a_measure_becomes_its_own_file(written):
    assert sorted(written.paths()) == ["footprint_PGA.csv.gz", "footprint_SA0p3.csv.gz"]
    assert written.imts == ("PGA", "SA(0.3)")


# -- stored once, and compactly --------------------------------------------------------

def test_the_footprint_is_compressed_as_it_is_written(written):
    """The largest table a hazard set stores is never kept as uncompressed text."""
    assert written.path_for("PGA").read_bytes()[:2] == bytes((0x1F, 0x8B))
    assert written.csv_bytes("PGA").startswith(b"event_id,areaperil_id")


def test_the_same_rows_write_the_same_bytes(tmp_path):
    """gzip records a time and a filename by default; left out, a rebuild is identical."""
    first = FootprintTableWriter(tmp_path / "first")
    first.extend(ROWS)
    one = first.close()
    second = FootprintTableWriter(tmp_path / "second")
    second.extend(ROWS)
    two = second.close()
    assert one.path_for("PGA").read_bytes() == two.path_for("PGA").read_bytes()


def test_a_table_stored_before_compression_still_reads(tmp_path):
    """Hazard sets registered earlier hold plain CSV, and must still build a package."""
    plain = tmp_path / "footprint_PGA.csv"
    plain.write_text("event_id,areaperil_id,intensity_bin_id,probability\n1,10,3,1.0\n")
    compressed = gzip.compress(plain.read_bytes())

    for source in (plain, plain.read_bytes(), compressed):
        with open_table(source) as handle:
            assert handle.read().splitlines()[1] == "1,10,3,1.0"


def test_a_row_arriving_out_of_order_is_refused(tmp_path):
    """Nothing is sorted here, so the order has to be enforced rather than hoped."""
    writer = FootprintTableWriter(tmp_path)
    writer.add(row(2, 10, "PGA", 3, 1.0))
    with pytest.raises(FootprintError, match="out of order"):
        writer.add(row(1, 10, "PGA", 3, 1.0))


def test_the_rows_can_be_read_back(written):
    assert [item.intensity_bin_id for item in written.rows_for("PGA")] == [3, 4, 5, 6]
    assert len(list(written)) == len(ROWS)


# -- what was counted on the way past ------------------------------------------------

def test_the_summary_is_counted_as_the_rows_pass(written):
    assert len(written) == len(ROWS)
    assert dict(written.rows_by_measure) == {"PGA": 4, "SA(0.3)": 1}
    assert written.cells == {10, 11}
    assert written.events == {1, 2}


def test_an_event_that_reached_nothing_is_reported_rather_than_lost(written):
    """Event 3 was expected and produced no ground motion here."""
    assert written.coverage["events_expected"] == 3
    assert written.coverage["events_without_footprint"] == 1
    assert written.coverage["silent_examples"] == [3]
    assert not written.coverage["problems"]


def test_probabilities_that_do_not_sum_to_one_are_named(tmp_path):
    writer = FootprintTableWriter(tmp_path)
    writer.add(row(1, 10, "PGA", 3, 0.25))
    writer.add(row(1, 10, "PGA", 4, 0.25))
    tables = writer.close()
    assert any("sum" in problem for problem in tables.problems)
    assert tables.probability_distance[0] == pytest.approx(0.5)


def test_a_footprint_with_nothing_wrong_reports_no_problems(written):
    assert written.problems == ()
    assert written.probability_distance[0] == pytest.approx(0.0)


def test_asking_for_a_measure_the_set_does_not_carry_is_refused(written):
    with pytest.raises(FootprintError, match="no rows for"):
        written.path_for("SA(1.0)")


# -- the reason all of this exists ---------------------------------------------------

def test_the_memory_it_costs_does_not_follow_the_row_count(tmp_path):
    """Ten times the rows for materially the same memory.

    The point of writing a footprint to a file rather than a list. Holding the
    rows instead would make the larger run roughly ten times the smaller, which
    at national scale was tens of gigabytes.
    """

    def peak_for(events: int, directory) -> int:
        writer = FootprintTableWriter(directory)
        tracemalloc.start()
        try:
            for event in range(1, events + 1):
                for cell in range(1, 51):
                    writer.add(row(event, cell, "PGA", 3, 0.5))
                    writer.add(row(event, cell, "PGA", 4, 0.5))
            writer.close()
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    small = peak_for(20, tmp_path / "small")
    large = peak_for(200, tmp_path / "large")

    # Ten times the rows. The sets of events and cells grow a little, which is
    # bounded by the event set and the grid rather than by the table.
    assert large < small * 3


# -- the vectorised path must stay the scalar one ------------------------------------

def test_binning_an_array_agrees_with_binning_one_value_at_a_time():
    """The whole speed-up rests on this, so it is a test rather than a comment.

    A national conversion bins about eight hundred million values, and doing
    that one at a time means a string-formatted ``Decimal`` per value. The array
    path rounds in float instead, which can only be adopted if it lands every
    value in the same bin -- including at the boundaries, where rounding to six
    significant figures is what decides which side a value falls.
    """
    # Skipped rather than imported, because the vectorised path is optional:
    # numpy belongs to converting a calculation, not to describing a bin set.
    # The file's other tests do not need it, so the skip is per test.
    np = pytest.importorskip("numpy")

    from cass_converter import pilot_bins

    for imt, bin_set in pilot_bins.intensity_bins().items():
        edges = [float(item.lower) for item in bin_set.bins]
        # The bin edges themselves, either side of each, and a spread across the
        # range -- the values where the two paths could disagree if anywhere.
        values = np.array(
            edges
            + [edge * (1 + 1e-9) for edge in edges]
            + [edge * (1 - 1e-9) for edge in edges]
            + list(np.linspace(1e-5, float(bin_set.bins[-1].upper) * 1.1, 500)),
            dtype=np.float64,
        )
        vector = bin_set.find_many(values)
        for position, raw in enumerate(values):
            value = Decimal(f"{float(raw):.6g}")
            found = bin_set.find(value)
            if found is None:
                expected = (
                    bin_set.BELOW_RANGE
                    if value < bin_set.bins[0].lower
                    else bin_set.ABOVE_RANGE
                )
            else:
                expected = found.bin_index
            assert int(vector[position]) == expected, (imt, raw)


def test_a_block_counts_what_the_samples_would_have_counted():
    """``add_block`` is the twin of ``add``, and produces the same rows."""
    np = pytest.importorskip("numpy")

    from cass_converter import pilot_bins
    from cass_converter.footprint import (
        FootprintAccumulator,
        GroundMotionBlock,
        GroundMotionSample,
    )

    bins = pilot_bins.intensity_bins()
    cells = np.array([10, 10, 11, 11, 10, 12], dtype=np.int64)
    values = np.array([0.2, 0.25, 0.4, 0.4, 0.2, 0.0001], dtype=np.float64)

    one_at_a_time = FootprintAccumulator(bins)
    rows = []
    for cell, value in zip(cells.tolist(), values.tolist(), strict=True):
        rows.extend(
            one_at_a_time.add(
                GroundMotionSample(
                    event_id=7,
                    area_peril_id=cell,
                    imt="PGA",
                    value=Decimal(f"{value:.6g}"),
                )
            )
        )
    rows.extend(one_at_a_time.close())

    in_bulk = FootprintAccumulator(bins)
    bulk_rows = list(
        in_bulk.add_block(
            GroundMotionBlock(
                event_id=7, imt="PGA", area_peril_ids=cells, values=values
            )
        )
    )
    bulk_rows.extend(in_bulk.close())

    assert bulk_rows == rows
    assert in_bulk.metrics.samples_binned == one_at_a_time.metrics.samples_binned
    assert (
        in_bulk.metrics.samples_below_range
        == one_at_a_time.metrics.samples_below_range
    )
    assert in_bulk.metrics.cells_seen == one_at_a_time.metrics.cells_seen
