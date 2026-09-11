"""Converter framework: policy gating, bins, identifiers, footprint, frequency.

The scientific choices are open (section 16), so what is tested here is the
machinery that will carry whichever choice is approved -- and, above all, that
the converter refuses to run until one is.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cass_converter.bins import (
    Bin,
    BinError,
    DamageBinSet,
    IntensityBinSet,
    linear_bins,
    log_bins,
)
from cass_converter.footprint import (
    FootprintAccumulator,
    FootprintError,
    FootprintRow,
    GroundMotionSample,
    build_footprint,
    check_event_coverage,
    validate_footprint,
)
from cass_converter.identifiers import (
    DeterministicIdMap,
    IdentifierError,
    assign_event_ids,
)
from cass_converter.occurrence import (
    OccurrenceError,
    OccurrenceRow,
    check_frequency,
    empty_period_share,
    validate_occurrences,
)
from cass_converter.policy import (
    ConversionPolicy,
    EventIdentity,
    IMTRepresentation,
    PolicyNotApproved,
)

# -- policy gating ----------------------------------------------------------

def test_a_fresh_installation_cannot_convert():
    """Section 15: event semantics defined too late causes converter rework."""
    policy = ConversionPolicy()
    assert policy.is_runnable is False
    with pytest.raises(PolicyNotApproved):
        policy.require_runnable()


def test_blockers_name_both_open_decisions():
    blockers = " ".join(ConversionPolicy().blockers())
    assert "event identity" in blockers
    assert "multi-IMT representation" in blockers


def test_common_imt_is_refused_even_when_chosen_explicitly():
    """Section 6: converting everything to one IMT is not an accepted default."""
    policy = ConversionPolicy(
        event_identity=EventIdentity.OCCURRENCE_PER_EVENT,
        imt_representation=IMTRepresentation.COMMON_IMT,
        approval_reference="APPROVAL-1",
        imts=("SA(0.3)",),
        investigation_time=10000,
    )
    assert policy.is_runnable is False
    assert any("not an accepted default" in item for item in policy.blockers())


def test_a_policy_without_an_approval_reference_is_refused():
    policy = ConversionPolicy(
        event_identity=EventIdentity.OCCURRENCE_PER_EVENT,
        imt_representation=IMTRepresentation.CORRELATED_CHANNELS,
        imts=("SA(0.3)",),
        investigation_time=10000,
    )
    assert any("approval reference" in item for item in policy.blockers())


def test_a_policy_without_investigation_time_is_refused():
    """Without it, annual frequency cannot be reconciled after conversion."""
    policy = ConversionPolicy(
        event_identity=EventIdentity.OCCURRENCE_PER_EVENT,
        imt_representation=IMTRepresentation.CORRELATED_CHANNELS,
        approval_reference="APPROVAL-1",
        imts=("SA(0.3)",),
    )
    assert any("investigation time" in item for item in policy.blockers())


def test_a_fully_approved_sa_policy_is_runnable():
    """The SA-first baseline of section 16, once its gates are cleared."""
    policy = ConversionPolicy(
        event_identity=EventIdentity.OCCURRENCE_PER_EVENT,
        imt_representation=IMTRepresentation.CORRELATED_CHANNELS,
        approval_reference="APPROVAL-CONV-2026-01",
        imts=("SA(0.3)",),
        investigation_time=10000,
        random_seed=42,
    )
    assert policy.is_runnable is True
    policy.require_runnable()
    assert policy.as_dict()["blockers"] == []


# -- bins -------------------------------------------------------------------

def test_linear_bins_tile_their_range():
    bins = linear_bins("0", "2", 4)
    assert len(bins) == 4
    assert bins[0].lower == Decimal(0)
    assert bins[-1].upper == Decimal(2)
    for previous, current in zip(bins, bins[1:], strict=False):
        assert current.lower == previous.upper


def test_log_bins_are_finer_at_low_intensity():
    bins = log_bins("0.01", "4.0", 8)
    first_width = bins[0].upper - bins[0].lower
    last_width = bins[-1].upper - bins[-1].lower
    assert first_width < last_width


def test_a_gap_between_bins_is_rejected():
    """A gap silently discards ground motion."""
    with pytest.raises(BinError, match="not a tiling"):
        IntensityBinSet(
            imt="SA(0.3)",
            version="1",
            bins=(
                Bin(1, Decimal("0"), Decimal("1"), Decimal("0.5")),
                Bin(2, Decimal("2"), Decimal("3"), Decimal("2.5")),
            ),
        )


def test_bin_indices_must_start_at_one_without_gaps():
    with pytest.raises(BinError, match="indices"):
        IntensityBinSet(
            imt="SA(0.3)",
            version="1",
            bins=(
                Bin(1, Decimal("0"), Decimal("1"), Decimal("0.5")),
                Bin(3, Decimal("1"), Decimal("2"), Decimal("1.5")),
            ),
        )


def test_intensity_lookup_reports_out_of_range_rather_than_clamping():
    bin_set = IntensityBinSet(imt="SA(0.3)", version="1", bins=linear_bins("0", "2", 4))
    assert bin_set.find(Decimal("0.75")).bin_index == 2
    # Above the dictionary means the dictionary is wrong for this hazard.
    assert bin_set.find(Decimal("5.0")) is None


def test_damage_bins_must_span_zero_to_one():
    with pytest.raises(BinError, match="damage ratio of 0"):
        DamageBinSet(version="1", bins=linear_bins("0.1", "1", 3))


def test_total_loss_lands_in_the_top_damage_bin():
    damage = DamageBinSet(version="1", bins=linear_bins("0", "1", 5))
    assert damage.find(Decimal("1")).bin_index == 5
    assert damage.find(Decimal("0")).bin_index == 1


# -- identifiers ------------------------------------------------------------

def test_event_ids_do_not_depend_on_arrival_order():
    """Section 7: byte-stable output when inputs and settings are unchanged."""
    rows = [
        {"source_rupture_id": "rup-2", "occurrence_index": 1},
        {"source_rupture_id": "rup-1", "occurrence_index": 2},
        {"source_rupture_id": "rup-1", "occurrence_index": 1},
    ]
    forward, map_a = assign_event_ids(rows)
    backward, map_b = assign_event_ids(list(reversed(rows)))

    assert map_a.digest() == map_b.digest()
    assert [item.as_dict() for item in forward] == [item.as_dict() for item in backward]


def test_event_ids_are_dense_and_start_at_one():
    lineage, _ = assign_event_ids(
        [{"source_rupture_id": f"rup-{index}"} for index in range(5)]
    )
    assert [item.oasis_event_id for item in lineage] == [1, 2, 3, 4, 5]


def test_numeric_key_parts_sort_numerically():
    """Zero padding stops occurrence 10 sorting between 1 and 2."""
    lineage, _ = assign_event_ids(
        [{"source_rupture_id": "rup", "occurrence_index": index} for index in (1, 2, 10)]
    )
    order = [item.occurrence_index for item in lineage]
    assert order == [1, 2, 10]


def test_lineage_traces_an_event_back_to_openquake():
    """Section 6: any material loss event must be traceable to its source."""
    lineage, _ = assign_event_ids(
        [{"source_rupture_id": "rup-7", "realization": 3, "occurrence_index": 12}]
    )
    record = lineage[0]
    assert record.source_rupture_id == "rup-7"
    assert record.realization == 3
    assert record.occurrence_index == 12


def test_duplicate_source_events_are_rejected():
    with pytest.raises(IdentifierError, match="not unique"):
        assign_event_ids(
            [
                {"source_rupture_id": "rup-1", "occurrence_index": 1},
                {"source_rupture_id": "rup-1", "occurrence_index": 1},
            ]
        )


def test_an_event_without_a_rupture_is_rejected():
    with pytest.raises(IdentifierError, match="rupture"):
        assign_event_ids([{"occurrence_index": 1}])


def test_id_map_round_trips():
    id_map = DeterministicIdMap(["c", "a", "b"])
    assert id_map.to_id("a") == 1
    assert id_map.to_key(1) == "a"
    with pytest.raises(IdentifierError):
        id_map.to_id("z")


# -- footprint --------------------------------------------------------------

@pytest.fixture()
def bin_sets():
    return {
        "SA(0.3)": IntensityBinSet(imt="SA(0.3)", version="1", bins=linear_bins("0", "2", 4)),
        "SA(1.0)": IntensityBinSet(imt="SA(1.0)", version="1", bins=linear_bins("0", "2", 4)),
    }


def sample(event_id, cell, value, imt="SA(0.3)"):
    return GroundMotionSample(event_id, cell, imt, Decimal(str(value)))


def test_a_single_sample_gives_a_deterministic_footprint(bin_sets):
    rows, _ = build_footprint([sample(1, 10, "0.75")], bin_sets)
    assert rows == [FootprintRow(1, 10, "SA(0.3)", 2, 1.0)]


def test_samples_spread_across_bins_carry_variability(bin_sets):
    rows, _ = build_footprint(
        [sample(1, 10, "0.25"), sample(1, 10, "0.75"), sample(1, 10, "0.80")],
        bin_sets,
    )
    probabilities = {row.intensity_bin_id: row.probability for row in rows}
    assert probabilities[1] == pytest.approx(1 / 3)
    assert probabilities[2] == pytest.approx(2 / 3)
    assert sum(probabilities.values()) == pytest.approx(1.0)


def test_probabilities_sum_to_one_per_event_cell_and_imt(bin_sets):
    rows, _ = build_footprint(
        [
            sample(1, 10, "0.25"),
            sample(1, 10, "1.75"),
            sample(1, 10, "0.25", imt="SA(1.0)"),
            sample(2, 10, "0.75"),
        ],
        bin_sets,
    )
    assert validate_footprint(rows) == []


def test_each_imt_keeps_its_own_channel(bin_sets):
    """Section 6: one undifferentiated channel cannot represent several periods."""
    rows, _ = build_footprint(
        [sample(1, 10, "0.25"), sample(1, 10, "1.75", imt="SA(1.0)")],
        bin_sets,
    )
    by_imt = {row.imt: row.intensity_bin_id for row in rows}
    assert by_imt == {"SA(0.3)": 1, "SA(1.0)": 4}


def test_out_of_range_samples_are_counted_not_clamped(bin_sets):
    rows, metrics = build_footprint(
        [sample(1, 10, "0.75"), sample(1, 10, "9.0")], bin_sets
    )
    assert metrics.samples_out_of_range == 1
    assert metrics.out_of_range_share == pytest.approx(0.5)
    assert all(row.intensity_bin_id == 2 for row in rows)


def test_an_undeclared_imt_is_an_error(bin_sets):
    with pytest.raises(FootprintError, match="no intensity bin set"):
        build_footprint([sample(1, 10, "0.5", imt="PGA")], bin_sets)


def test_samples_must_arrive_grouped_by_event(bin_sets):
    """Streaming only holds one event, so out-of-order input is a hard error."""
    with pytest.raises(FootprintError, match="not grouped by event"):
        build_footprint([sample(2, 10, "0.5"), sample(1, 10, "0.5")], bin_sets)


def test_the_accumulator_holds_one_event_at_a_time(bin_sets):
    """Section 15: large files must not pass through in one piece."""
    accumulator = FootprintAccumulator(bin_sets)
    emitted = []
    for event_id in range(1, 4):
        for cell in range(20):
            emitted.extend(accumulator.add(sample(event_id, cell, "0.5")))
    emitted.extend(accumulator.close())

    assert accumulator.metrics.peak_open_events == 1
    assert accumulator.metrics.events_emitted == 3
    assert len(emitted) == 60


def test_dropping_negligible_bins_still_sums_to_one(bin_sets):
    samples = [sample(1, 10, "0.25")] * 99 + [sample(1, 10, "1.75")]
    rows, _ = build_footprint(samples, bin_sets, drop_below=0.05)
    assert len(rows) == 1
    assert validate_footprint(rows) == []


def test_validation_catches_probabilities_that_do_not_sum_to_one():
    broken = [FootprintRow(1, 10, "SA(0.3)", 1, 0.4), FootprintRow(1, 10, "SA(0.3)", 2, 0.4)]
    problems = validate_footprint(broken)
    assert any("sum" in problem for problem in problems)


def test_validation_catches_a_repeated_intensity_bin():
    broken = [FootprintRow(1, 10, "SA(0.3)", 1, 0.5), FootprintRow(1, 10, "SA(0.3)", 1, 0.5)]
    assert any("more than once" in problem for problem in validate_footprint(broken))


def test_an_event_with_no_footprint_is_reported(bin_sets):
    """Frequency without loss understates the answer silently."""
    rows, _ = build_footprint([sample(1, 10, "0.5")], bin_sets)
    problems = check_event_coverage(rows, [1, 2, 3])
    assert any("no footprint rows" in problem for problem in problems)


def test_a_footprint_event_outside_the_event_set_is_reported(bin_sets):
    rows, _ = build_footprint([sample(9, 10, "0.5")], bin_sets)
    assert any("not in the event set" in p for p in check_event_coverage(rows, [1]))


# -- occurrence and frequency ----------------------------------------------

def test_frequency_is_preserved_when_rates_match():
    """10,000 events over 10,000 years is one per year, either way."""
    occurrences = [OccurrenceRow(event_id=index, period_no=index) for index in range(1, 101)]
    check = check_frequency(
        source_event_count=100,
        source_investigation_time=100,
        occurrences=occurrences,
        period_count=100,
    )
    assert check.source_annual_rate == pytest.approx(1.0)
    assert check.converted_annual_rate == pytest.approx(1.0)
    assert check.preserved is True
    check.require_preserved()


def test_a_mismatched_period_count_is_caught():
    """Halving the period count doubles the apparent rate, and AAL with it."""
    occurrences = [OccurrenceRow(event_id=index, period_no=index) for index in range(1, 101)]
    check = check_frequency(
        source_event_count=100,
        source_investigation_time=100,
        occurrences=occurrences,
        period_count=50,
    )
    assert check.preserved is False
    assert check.relative_difference == pytest.approx(1.0)
    with pytest.raises(OccurrenceError, match="not preserved"):
        check.require_preserved()


def test_zero_investigation_time_is_rejected():
    check = check_frequency(
        source_event_count=10,
        source_investigation_time=0,
        occurrences=[OccurrenceRow(1, 1)],
        period_count=10,
    )
    with pytest.raises(OccurrenceError, match="investigation time"):
        _ = check.source_annual_rate


def test_duplicate_event_period_pairs_are_reported():
    problems = validate_occurrences(
        [OccurrenceRow(1, 1), OccurrenceRow(1, 1)], period_count=10
    )
    assert any("double-counts" in problem for problem in problems)


def test_a_period_outside_the_range_is_reported():
    problems = validate_occurrences([OccurrenceRow(1, 99)], period_count=10)
    assert any("outside 1 to 10" in problem for problem in problems)


def test_an_event_that_never_occurs_is_reported():
    problems = validate_occurrences(
        [OccurrenceRow(1, 1)], period_count=10, known_event_ids=[1, 2]
    )
    assert any("never occur" in problem for problem in problems)


def test_an_empty_occurrence_table_is_reported():
    problems = validate_occurrences([], period_count=10)
    assert any("empty" in problem for problem in problems)


def test_empty_period_share_is_reported_rather_than_judged():
    """Most years are quiet in a low-seismicity region; that is evidence, not an error."""
    occurrences = [OccurrenceRow(1, 1), OccurrenceRow(2, 2)]
    assert empty_period_share(occurrences, 10) == pytest.approx(0.8)
