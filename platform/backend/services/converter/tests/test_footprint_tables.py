"""The footprint written straight to files, and the properties that allows.

A national footprint is hundreds of millions of rows, so the conversion cannot
hold one. These are the three things that have to be true for it not to: the
file is the same table a sorted rendering would produce, the summary is counted
correctly on the way past, and the memory it costs does not follow the row
count.
"""

from __future__ import annotations

import tracemalloc

import pytest

from cass_converter.footprint import FootprintError, FootprintRow
from cass_converter.footprint_tables import FootprintTableWriter


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
    produced = written.path_for("PGA").read_text(encoding="utf-8").splitlines()
    assert produced == expected


def test_a_measure_becomes_its_own_file(written):
    assert sorted(written.paths()) == ["footprint_PGA.csv", "footprint_SA0p3.csv"]
    assert written.imts == ("PGA", "SA(0.3)")


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
