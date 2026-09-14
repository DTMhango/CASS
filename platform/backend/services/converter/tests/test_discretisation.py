"""Turning a GEM function into an Oasis vulnerability table.

What these hold to is that the table still says what the function said. A
discretisation is lossy by construction, so the question is never whether it
loses something but whether what it loses is the shape at a resolution nobody
can act on, or the expected loss.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

import pytest

from cass_converter.bins import (
    Bin,
    DamageBinSet,
    IntensityBinSet,
    linear_bins,
    log_bins,
    oasis_damage_bins,
)
from cass_converter.gem import LossCategory, VulnerabilityFunction, parse_taxonomy
from cass_converter.policy import ConversionPolicy, EventIdentity, IMTRepresentation
from cass_converter.vulnerability import (
    DiscretisationError,
    Placement,
    bin_probabilities,
    build_table,
    check_reconstruction,
    damage_bins_to_csv,
    discretise,
    reconstruct,
    reconstruction_failures,
    sample,
    table_report,
    to_csv,
    weighted,
)


def curve(
    *,
    taxonomy: str = "CR/LFINF/CDL+ERM/H:2/COM",
    imt: str = "PGA",
    intensities=(0.1, 0.2, 0.4, 0.8),
    means=(0.001, 0.05, 0.3, 0.9),
    covs=(2.0, 1.2, 0.6, 0.15),
) -> VulnerabilityFunction:
    return VulnerabilityFunction(
        taxonomy=parse_taxonomy(taxonomy),
        loss_category=LossCategory.STRUCTURAL,
        imt=imt,
        intensities=tuple(intensities),
        mean_loss_ratios=tuple(means),
        coefficients_of_variation=tuple(covs),
    )


@pytest.fixture()
def damage():
    return DamageBinSet(version="1.0.0", bins=oasis_damage_bins(20))


@pytest.fixture()
def intensity():
    return IntensityBinSet(
        imt="PGA", version="1.0.0", bins=log_bins("0.05", "2.0", 12)
    )


@pytest.fixture()
def approved():
    return ConversionPolicy(
        event_identity=EventIdentity.RUPTURE_BINNED,
        imt_representation=IMTRepresentation.CORRELATED_CHANNELS,
        approval_reference="TEST-ONLY",
        imts=("PGA",),
        investigation_time=1.0,
    )


# -- sampling the source function ---------------------------------------------------

def test_a_published_level_reads_back_exactly():
    assert sample(curve(), 0.2) == (0.05, 1.2)


def test_between_levels_the_mean_and_cov_are_interpolated_linearly():
    mean, cov = sample(curve(), 0.3)
    assert mean == pytest.approx(0.175)
    assert cov == pytest.approx(0.9)


def test_below_the_lowest_level_there_is_no_damage():
    """Not extrapolated. The function says nothing about intensities beneath it."""
    assert sample(curve(), 0.05) == (0.0, 0.0)


def test_above_the_highest_level_the_last_value_holds():
    """A linear extrapolation would climb past a loss ratio of 1."""
    assert sample(curve(), 5.0) == (0.9, 0.15)


# -- probabilities over the damage bins ----------------------------------------------

def test_the_probabilities_in_one_intensity_bin_sum_to_one(damage):
    masses = bin_probabilities(0.3, 0.6, damage)
    assert sum(masses) == pytest.approx(1.0, abs=1e-12)


def test_no_damage_lands_in_the_no_damage_bin(damage):
    masses = bin_probabilities(0.0, 0.0, damage)
    assert masses[0] == pytest.approx(1.0)
    assert damage.bins[0].is_point
    assert damage.bins[0].interpolation == Decimal(0)


def test_total_loss_lands_at_a_damage_ratio_of_one(damage):
    masses = bin_probabilities(1.0, 0.0, damage)
    mean, _ = reconstruct(masses, damage)
    assert mean == pytest.approx(1.0)


def test_a_continuous_distribution_puts_no_mass_on_the_exact_endpoints(damage):
    """The point bins hold degenerate outcomes, and a beta reaches neither."""
    masses = bin_probabilities(0.5, 0.4, damage)
    assert masses[0] == pytest.approx(0.0, abs=1e-15)
    assert masses[-1] == pytest.approx(0.0, abs=1e-15)


# -- the placement rule, which is the choice that matters -----------------------------

def test_mean_preserving_placement_reproduces_the_mean_exactly(damage):
    masses = bin_probabilities(0.0731, 1.4, damage, placement=Placement.MEAN_PRESERVING)
    mean, _ = reconstruct(masses, damage)
    assert mean == pytest.approx(0.0731, abs=1e-09)


def test_midpoint_placement_does_not(damage):
    """The failure this default exists to avoid, at the scale it happens.

    A distribution whose mass is entirely inside the first bin above zero is
    read at that bin's midpoint. With twenty bins the midpoint is 0.025, so a
    mean loss ratio of one in ten thousand comes out at two and a half per cent
    of the insured value -- at an intensity every risk in the portfolio sees.
    """
    masses = bin_probabilities(0.0001, 1.2, damage, placement=Placement.MIDPOINT)
    mean, _ = reconstruct(masses, damage)
    assert mean == pytest.approx(0.025, abs=1e-04)
    assert mean > 200 * 0.0001


def test_placement_moves_mass_without_creating_or_losing_any(damage):
    raw = bin_probabilities(0.2, 0.9, damage, placement=Placement.MIDPOINT)
    placed = bin_probabilities(0.2, 0.9, damage, placement=Placement.MEAN_PRESERVING)
    assert sum(placed) == pytest.approx(sum(raw), abs=1e-12)
    assert placed != raw


def test_mean_preservation_holds_however_coarse_the_bins_are():
    """Which is what makes the dictionary a limit on shape rather than a bias."""
    coarse = DamageBinSet(version="1", bins=oasis_damage_bins(2))
    masses = bin_probabilities(0.37, 0.8, coarse)
    mean, _ = reconstruct(masses, coarse)
    assert mean == pytest.approx(0.37, abs=1e-09)


# -- a channel's share of its class --------------------------------------------------

def test_a_weighted_twin_carries_its_share_of_the_expected_damage(damage, intensity):
    """The engine prices every item at the whole coverage, so the share is in the damage."""
    channel = discretise(
        curve(), vulnerability_id=7, intensity_bins=intensity, damage_bins=damage
    )

    twin = weighted(channel, 0.3, vulnerability_id=1_000_007, damage_bins=damage)

    assert {row.vulnerability_id for row in twin.rows} == {1_000_007}
    assert twin.scaled_by == 0.3
    # To the probability floor: the twin is scaled from the table the engine reads,
    # which drops probabilities below 1e-09.
    for source, scaled in zip(channel.reconstruction, twin.reconstruction, strict=True):
        assert scaled.table_mean == pytest.approx(0.3 * source.table_mean, abs=1e-08)
        assert scaled.source_mean == pytest.approx(0.3 * source.source_mean)
        assert scaled.source_cov == source.source_cov
    assert twin.worst_mean_error < 1e-07


def test_a_weighted_twin_moves_probability_without_creating_or_losing_any(damage, intensity):
    channel = discretise(
        curve(), vulnerability_id=7, intensity_bins=intensity, damage_bins=damage
    )

    twin = weighted(channel, 0.55, vulnerability_id=9, damage_bins=damage)

    totals: dict[int, float] = {}
    for row in twin.rows:
        totals[row.intensity_bin_id] = totals.get(row.intensity_bin_id, 0.0) + row.probability
    assert set(totals) == {item.bin_index for item in intensity.bins}
    assert all(total == pytest.approx(1.0, abs=1e-06) for total in totals.values())


def test_a_share_of_one_is_the_channel_itself(damage, intensity):
    channel = discretise(
        curve(), vulnerability_id=7, intensity_bins=intensity, damage_bins=damage
    )

    twin = weighted(channel, 1.0, vulnerability_id=7, damage_bins=damage)

    assert [(row.intensity_bin_id, row.damage_bin_id) for row in twin.rows] == [
        (row.intensity_bin_id, row.damage_bin_id) for row in channel.rows
    ]
    for scaled, source in zip(twin.rows, channel.rows, strict=True):
        assert scaled.probability == pytest.approx(source.probability, abs=1e-12)


@pytest.mark.parametrize("share", [0.0, -0.2, 1.2])
def test_a_share_outside_the_class_is_refused(damage, intensity, share):
    channel = discretise(
        curve(), vulnerability_id=7, intensity_bins=intensity, damage_bins=damage
    )
    with pytest.raises(DiscretisationError, match="share of its class"):
        weighted(channel, share, vulnerability_id=2, damage_bins=damage)


def test_a_twin_is_scaled_on_the_grid_its_channel_was_built_on(damage, intensity):
    """A probability read against another dictionary describes another damage ratio."""
    channel = discretise(
        curve(), vulnerability_id=7, intensity_bins=intensity, damage_bins=damage
    )
    coarse = DamageBinSet(version="2.0.0", bins=oasis_damage_bins(5))
    with pytest.raises(DiscretisationError, match="another dictionary"):
        weighted(channel, 0.5, vulnerability_id=2, damage_bins=coarse)


# -- one function -------------------------------------------------------------------

def test_a_discretised_function_carries_a_row_per_occupied_bin(damage, intensity):
    result = discretise(
        curve(), vulnerability_id=7, intensity_bins=intensity, damage_bins=damage
    )
    assert {row.vulnerability_id for row in result.rows} == {7}
    assert {row.intensity_bin_id for row in result.rows} == {
        item.bin_index for item in intensity.bins
    }


def test_every_intensity_bin_is_reconstructed(damage, intensity):
    result = discretise(
        curve(), vulnerability_id=1, intensity_bins=intensity, damage_bins=damage
    )
    assert len(result.reconstruction) == len(intensity.bins)
    assert result.worst_mean_error < 1e-07


def test_a_function_discretised_against_the_wrong_imt_is_refused(damage):
    """0.4 g of SA(0.3) and 0.4 g of SA(1.0) are different demands."""
    wrong = IntensityBinSet(imt="SA(1.0)", version="1", bins=log_bins("0.05", "2", 8))
    with pytest.raises(DiscretisationError, match="demands PGA and was given"):
        discretise(curve(), vulnerability_id=1, intensity_bins=wrong, damage_bins=damage)


def test_a_vulnerability_identifier_must_be_positive(damage, intensity):
    with pytest.raises(DiscretisationError, match="must be positive"):
        discretise(
            curve(), vulnerability_id=0, intensity_bins=intensity, damage_bins=damage
        )


def test_the_dropped_tail_mass_is_measured_rather_than_assumed(damage, intensity):
    result = discretise(
        curve(), vulnerability_id=1, intensity_bins=intensity, damage_bins=damage
    )
    assert result.worst_dropped_mass < 1e-06
    assert all(item.dropped_mass >= 0 for item in result.reconstruction)


# -- the reconstruction gate -----------------------------------------------------------

def test_a_dictionary_that_reproduces_its_functions_passes(damage, intensity):
    result = discretise(
        curve(), vulnerability_id=1, intensity_bins=intensity, damage_bins=damage
    )
    check_reconstruction([result])


def test_a_dictionary_that_does_not_is_refused(damage, intensity):
    result = discretise(
        curve(),
        vulnerability_id=1,
        intensity_bins=intensity,
        damage_bins=damage,
        placement=Placement.MIDPOINT,
    )
    with pytest.raises(DiscretisationError, match="cannot represent these"):
        check_reconstruction([result])


def test_a_failure_names_the_taxonomy_and_the_intensity(damage, intensity):
    result = discretise(
        curve(),
        vulnerability_id=1,
        intensity_bins=intensity,
        damage_bins=damage,
        placement=Placement.MIDPOINT,
    )
    failures = reconstruction_failures([result])
    assert failures
    assert all(item.taxonomy == "CR/LFINF/CDL+ERM/H:2/COM" for item in failures)
    assert {item.measure for item in failures} <= {
        "mean_loss_ratio",
        "coefficient_of_variation",
    }


def test_the_cov_check_is_not_applied_where_the_mean_is_negligible(damage, intensity):
    """Holding a bin grid to the spread of a millionth of a per cent of value
    would fail every dictionary for a reason nobody could use."""
    result = discretise(
        curve(means=(1e-08, 1e-08, 1e-08, 1e-08), covs=(50.0, 50.0, 50.0, 50.0)),
        vulnerability_id=1,
        intensity_bins=intensity,
        damage_bins=damage,
    )
    assert reconstruction_failures([result]) == ()


# -- a whole table ------------------------------------------------------------------

def test_a_table_is_built_under_an_approved_policy(damage, intensity, approved):
    table = build_table(
        {1: curve()},
        intensity_bins={"PGA": intensity},
        damage_bins=damage,
        policy=approved,
    )
    assert len(table) == 1
    assert table[0].vulnerability_id == 1


def test_an_unapproved_policy_refuses_to_build(damage, intensity):
    with pytest.raises(Exception, match="has not been approved"):
        build_table(
            {1: curve()},
            intensity_bins={"PGA": intensity},
            damage_bins=damage,
            policy=ConversionPolicy(),
        )


def test_a_function_demanding_an_undeclared_imt_is_refused(damage, intensity, approved):
    """Dropping it leaves risks with no vulnerability; converting it is the
    common-IMT substitution the plan refuses as a default."""
    spectral = IntensityBinSet(
        imt="SA(1.0)", version="1", bins=log_bins("0.05", "2", 8)
    )
    with pytest.raises(DiscretisationError, match="does not\\s+declare: SA\\(1.0\\)"):
        build_table(
            {1: curve(), 2: curve(imt="SA(1.0)")},
            intensity_bins={"PGA": intensity, "SA(1.0)": spectral},
            damage_bins=damage,
            policy=approved,
        )


def test_a_missing_intensity_dictionary_is_refused(damage, approved):
    with pytest.raises(DiscretisationError, match="No intensity-bin dictionary"):
        build_table(
            {1: curve()}, intensity_bins={}, damage_bins=damage, policy=approved
        )


def test_an_empty_table_is_refused(damage, approved):
    with pytest.raises(DiscretisationError, match="No functions"):
        build_table({}, intensity_bins={}, damage_bins=damage, policy=approved)


# -- what Oasis reads ------------------------------------------------------------------

def test_the_csv_carries_the_columns_oasis_reads(damage, intensity, approved):
    table = build_table(
        {1: curve()},
        intensity_bins={"PGA": intensity},
        damage_bins=damage,
        policy=approved,
    )
    rows = list(csv.DictReader(io.StringIO(to_csv(table).decode())))
    assert list(rows[0]) == [
        "vulnerability_id",
        "intensity_bin_id",
        "damage_bin_id",
        "probability",
    ]


def test_the_csv_is_sorted_by_identifier_then_bin(damage, intensity, approved):
    table = build_table(
        {2: curve(), 1: curve(taxonomy="S/LFM/CDL+ERM/H:1/COM")},
        intensity_bins={"PGA": intensity},
        damage_bins=damage,
        policy=approved,
    )
    rows = list(csv.DictReader(io.StringIO(to_csv(table).decode())))
    keys = [
        (int(row["vulnerability_id"]), int(row["intensity_bin_id"]),
         int(row["damage_bin_id"]))
        for row in rows
    ]
    assert keys == sorted(keys)


def test_the_damage_dictionary_states_its_interval_type(damage):
    rows = list(csv.reader(io.StringIO(damage_bins_to_csv(damage).decode())))
    assert rows[0] == [
        "bin_index", "bin_from", "bin_to", "interpolation", "interval_type"
    ]
    assert rows[1] == ["1", "0", "0", "0", "1201"]


def test_the_report_says_what_the_discretisation_did(damage, intensity, approved):
    table = build_table(
        {1: curve()},
        intensity_bins={"PGA": intensity},
        damage_bins=damage,
        policy=approved,
    )
    report = table_report(table)
    assert report["functions"] == 1
    assert report["placement"] == "mean_preserving"
    assert report["intensity_measures"] == ["PGA"]
    assert report["worst_absolute_mean_error"]["error"] < 1e-07


def test_an_empty_report_is_not_an_error():
    assert table_report([]) == {"functions": 0, "rows": 0}


# -- damage bin sets -------------------------------------------------------------------

def test_the_oasis_shape_has_a_point_bin_at_each_end():
    bins = oasis_damage_bins(5)
    damage = DamageBinSet(version="1", bins=bins)
    assert damage.has_no_damage_bin
    assert damage.has_total_loss_bin
    assert len(bins) == 7


def test_an_undamaged_risk_finds_the_no_damage_bin_rather_than_the_first_slice():
    damage = DamageBinSet(version="1", bins=oasis_damage_bins(5))
    assert damage.find(Decimal("0")).bin_index == 1
    assert damage.find(Decimal("0.01")).bin_index == 2


def test_a_point_bin_in_the_middle_is_refused():
    """No damage and total loss are exact outcomes. A ratio of 0.4 is not."""
    bins = (
        Bin(1, Decimal(0), Decimal("0.4"), Decimal("0.2")),
        Bin(2, Decimal("0.4"), Decimal("0.4"), Decimal("0.4")),
        Bin(3, Decimal("0.4"), Decimal(1), Decimal("0.7")),
    )
    with pytest.raises(Exception, match="Only\\s+the ends may be points"):
        DamageBinSet(version="1", bins=bins)


def test_an_intensity_bin_may_not_be_a_point():
    """A ground motion of exactly one value has no probability to hold."""
    bins = (
        Bin(1, Decimal("0.1"), Decimal("0.1"), Decimal("0.1")),
        Bin(2, Decimal("0.1"), Decimal("1"), Decimal("0.5")),
    )
    with pytest.raises(Exception, match="Only damage takes point bins"):
        IntensityBinSet(imt="PGA", version="1", bins=bins)


def test_uniform_damage_bins_are_still_a_valid_set():
    """The older shape still works; it is just biased at the ends."""
    damage = DamageBinSet(version="1", bins=linear_bins("0", "1", 4))
    assert damage.has_no_damage_bin is False
    assert damage.find(Decimal("0")).bin_index == 1
