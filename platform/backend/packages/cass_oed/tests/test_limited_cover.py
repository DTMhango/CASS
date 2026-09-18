"""Limited cover: reinstatements and their premiums, checked against worked answers.

Every expected figure here was worked by hand first. The first is the one Klapton
Re stated when the calculation was specified: a 5m xs 1m layer, a 4m loss and an
MDP of 800k, which recovers 3m, uses 60% of the layer, owes 480k to reinstate it
and so nets 2.52m.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from cass_oed.limited_cover import (
    UNCOVERED,
    CoverError,
    EventLosses,
    LayerTerms,
    apply_layer,
    calculate,
    cover_classes,
    exceedance,
    metrics,
    parse_rates,
)


def layer(**overrides) -> LayerTerms:
    terms = {
        "contract": 1,
        "layer": 1,
        "name": "",
        "priority": 1,
        "attachment": 1_000_000.0,
        "limit": 5_000_000.0,
        "ceded": 1.0,
        "placed": 1.0,
        "reinstatements": 1,
        "rates": (1.0,),
        "premium": 800_000.0,
        "classes": frozenset({"1"}),
    }
    terms.update(overrides)
    return LayerTerms(**terms)


def year(*losses: float, periods: int = 1, samples: int = 1) -> EventLosses:
    """One simulated year of events, in the order given, all in cover class 1."""
    return EventLosses.from_rows(
        [(1, 1, index + 1, "1", loss) for index, loss in enumerate(losses)],
        periods=periods,
        samples=samples,
    )


# -- one layer ------------------------------------------------------------------------

def test_the_stated_example_nets_two_point_five_two_million():
    outcome = apply_layer(year(4_000_000), layer(), limited=True)
    assert outcome.recovered.tolist() == [3_000_000]
    assert outcome.premium.tolist() == pytest.approx([480_000])
    assert outcome.net.tolist() == pytest.approx([2_520_000])


def test_a_second_event_pays_only_what_the_reinstatement_restored():
    """10m xs 5m, one reinstatement at 100% on 1m: 12m then 20m then 30m enter."""
    terms = layer(attachment=5e6, limit=10e6, premium=1e6)
    outcome = apply_layer(year(12e6, 20e6, 30e6), terms, limited=True)
    assert outcome.recovered.tolist() == pytest.approx([7e6, 10e6, 3e6])
    # 7m reinstated, then the remaining 3m of the one reinstatement; nothing after.
    assert outcome.premium.tolist() == pytest.approx([0.7e6, 0.3e6, 0.0])
    assert outcome.net.tolist() == pytest.approx([6.3e6, 9.7e6, 3e6])
    assert outcome.exhausted_years == 1


def test_each_reinstatement_is_charged_at_its_own_rate():
    """Two reinstatements, the first at 125% and the second at 100%, on 5m of limit."""
    terms = layer(attachment=3e6, limit=5e6, reinstatements=2, rates=(1.25, 1.0), premium=2_075_000)
    outcome = apply_layer(year(8e6, 8e6, 8e6), terms, limited=True)
    assert outcome.recovered.tolist() == pytest.approx([5e6, 5e6, 5e6])
    assert outcome.premium.tolist() == pytest.approx([1.25 * 2_075_000, 1.0 * 2_075_000, 0.0])


def test_one_rate_applies_to_every_reinstatement():
    assert layer(reinstatements=3, rates=(1.0,)).rate(3) == 1.0
    assert parse_rates("0;0.5;1") == (0.0, 0.5, 1.0)
    assert parse_rates("1.25") == (1.25,)
    with pytest.raises(CoverError, match="not a number"):
        parse_rates("full")


def test_no_reinstatements_means_one_limit_a_year():
    terms = layer(reinstatements=0, premium=None)
    outcome = apply_layer(year(4e6, 4e6), terms, limited=True)
    assert outcome.recovered.tolist() == pytest.approx([3e6, 2e6])
    assert outcome.premium.tolist() == [0.0, 0.0]


def test_a_layer_stating_no_reinstatements_is_applied_as_the_engine_applies_it():
    terms = layer(reinstatements=None)
    assert math.isinf(terms.aggregate)
    outcome = apply_layer(year(4e6, 4e6, 4e6), terms, limited=True)
    assert outcome.recovered.tolist() == pytest.approx([3e6, 3e6, 3e6])
    assert outcome.premium.tolist() == [0.0, 0.0, 0.0]


def test_the_unlimited_reading_is_the_engines_arithmetic():
    """Each event on its own, in full, free: what the Oasis financial module does."""
    outcome = apply_layer(year(4e6, 9e6, 0.5e6), layer(), limited=False)
    assert outcome.recovered.tolist() == pytest.approx([3e6, 5e6, 0.0])
    assert outcome.premium.tolist() == [0.0, 0.0, 0.0]


def test_shares_scale_the_recovery_and_its_premium_alike():
    outcome = apply_layer(year(4e6), layer(ceded=0.5, placed=0.9), limited=True)
    assert outcome.recovered.tolist() == pytest.approx([3e6 * 0.45])
    assert outcome.premium.tolist() == pytest.approx([480_000 * 0.45])


def test_the_aggregate_starts_again_each_year():
    losses = EventLosses.from_rows(
        [(1, 1, 1, "1", 12e6), (1, 1, 2, "1", 20e6), (1, 2, 3, "1", 20e6)],
        periods=2,
        samples=1,
    )
    terms = layer(attachment=5e6, limit=10e6, premium=1e6)
    outcome = apply_layer(losses, terms, limited=True)
    assert outcome.recovered.tolist() == pytest.approx([7e6, 10e6, 10e6])
    assert outcome.premium.tolist() == pytest.approx([0.7e6, 0.3e6, 1e6])


def test_each_sample_is_its_own_sequence_of_years():
    losses = EventLosses.from_rows(
        [(2, 1, 1, "1", 12e6), (1, 1, 1, "1", 12e6), (1, 1, 2, "1", 20e6), (2, 1, 2, "1", 20e6)],
        periods=1,
        samples=2,
    )
    outcome = apply_layer(losses, layer(attachment=5e6, limit=10e6, premium=1e6), limited=True)
    assert outcome.recovered.tolist() == pytest.approx([7e6, 10e6, 7e6, 10e6])


# -- the programme -------------------------------------------------------------------

LOCATIONS = [
    {"PortNumber": "P", "AccNumber": "A1", "LocNumber": "1"},
    {"PortNumber": "P", "AccNumber": "A2", "LocNumber": "1"},
    {"PortNumber": "P", "AccNumber": "A3", "LocNumber": "1"},
]


def info(number, layer_number=1, kind="CXL", priority=1, **terms):
    return {
        "ReinsNumber": str(number),
        "ReinsLayerNumber": str(layer_number),
        "ReinsType": kind,
        "InuringPriority": str(priority),
        "OccAttachment": "1000000",
        "OccLimit": "5000000",
        **terms,
    }


def test_cover_classes_follow_each_contracts_scope():
    layers, classes = cover_classes(
        [info(1), info(1, 2), info(2)],
        [
            {"ReinsNumber": "1", "PortNumber": "P", "AccNumber": "A1"},
            {"ReinsNumber": "2", "PortNumber": "P"},
        ],
        LOCATIONS,
    )
    assert classes == {
        ("P", "A1", "1"): "1+2",
        ("P", "A2", "1"): "2",
        ("P", "A3", "1"): "2",
    }
    assert [(item.contract, item.layer) for item in layers] == [(1, 1), (1, 2), (2, 1)]
    assert layers[0].classes == frozenset({"1+2"})
    assert layers[2].classes == frozenset({"1+2", "2"})


def test_an_uncovered_location_keeps_its_loss():
    _, classes = cover_classes(
        [info(1)], [{"ReinsNumber": "1", "AccNumber": "A1"}], LOCATIONS
    )
    assert classes[("P", "A2", "1")] == UNCOVERED


def test_the_contract_terms_are_read_from_oed():
    layers, _ = cover_classes(
        [info(1, Reinstatement="2", ReinstatementCharge="1.25;1", ReinsPremium="2075000",
              PlacedPercent="0.85")],
        [{"ReinsNumber": "1"}],
        LOCATIONS,
    )
    assert layers[0].reinstatements == 2
    assert layers[0].rates == (1.25, 1.0)
    assert layers[0].premium == 2_075_000
    assert layers[0].placed == 0.85
    assert layers[0].aggregate == 15_000_000


def test_a_programme_with_a_quota_share_is_refused_with_the_reason():
    with pytest.raises(CoverError, match="catastrophe excess of loss contracts only"):
        cover_classes([info(1, kind="QS")], [{"ReinsNumber": "1"}], LOCATIONS)


def test_a_scope_naming_a_policy_is_refused():
    with pytest.raises(CoverError, match="names a policy"):
        cover_classes([info(1)], [{"ReinsNumber": "1", "AccNumber": "A1", "PolNumber": "X"}], LOCATIONS)


def test_overlapping_scopes_at_different_priorities_are_refused():
    with pytest.raises(CoverError, match="priorities 1 and 2"):
        cover_classes(
            [info(1), info(2, priority=2)],
            [{"ReinsNumber": "1"}, {"ReinsNumber": "2", "AccNumber": "A1"}],
            LOCATIONS,
        )


def test_disjoint_scopes_at_different_priorities_are_applied():
    layers, _ = cover_classes(
        [info(1), info(2, priority=2)],
        [{"ReinsNumber": "1", "AccNumber": "A1"}, {"ReinsNumber": "2", "AccNumber": "A2"}],
        LOCATIONS,
    )
    assert {item.priority for item in layers} == {1, 2}


# -- the curves ---------------------------------------------------------------------

def test_the_curve_is_read_as_the_engine_reads_it():
    """Ten years, five with loss: ranks give return periods 10, 5, 3.33, 2.5, 2."""
    curve = exceedance(np.array([5.0, 4.0, 3.0, 2.0, 1.0]), periods=10, return_periods=[10, 5, 4, 2, 1])
    assert curve[10] == 5.0
    assert curve[5] == 4.0
    # Between rank 2 (5 years, 4.0) and rank 3 (3.33 years, 3.0), linearly.
    assert curve[4] == pytest.approx(3.4)
    assert curve[2] == 1.0
    # Beyond the last year with a loss the engine reports nothing lost.
    assert curve[1] == 0.0


def test_a_return_period_longer_than_the_catalogue_is_not_reported():
    assert 20 not in exceedance(np.array([5.0]), periods=10, return_periods=[20, 10])


def test_average_loss_and_deviation_are_over_every_year_and_sample():
    losses = EventLosses.from_rows(
        [(1, 1, 1, "1", 10.0), (1, 2, 2, "1", 20.0), (2, 1, 1, "1", 30.0)],
        periods=2,
        samples=2,
    )
    result = metrics(losses, losses.insured, return_periods=[2, 1])
    annual = np.array([10.0, 20.0, 30.0, 0.0])
    assert result.average_annual_loss == pytest.approx(annual.mean())
    assert result.standard_deviation == pytest.approx(annual.std(ddof=1))
    # The occurrence curve averages each year's largest event over samples.
    assert result.oep[2] == pytest.approx(20.0)


def test_the_whole_calculation_nets_premiums_off_recoveries():
    losses = EventLosses.from_rows(
        [(1, 1, 1, "1", 12e6), (1, 1, 2, "1", 20e6), (1, 1, 3, UNCOVERED, 1e6)],
        periods=1,
        samples=1,
    )
    result = calculate(losses, [layer(attachment=5e6, limit=10e6, premium=1e6)], return_periods=[1])
    # Kept: 33m insured, less 7m + 10m recovered, plus 1m of reinstatement premium.
    assert result.limited.aep[1] == pytest.approx(33e6 - 17e6 + 1e6)
    assert result.unlimited.aep[1] == pytest.approx(33e6 - 17e6)
    assert result.insured.aep[1] == pytest.approx(33e6)
    assert result.layers[0]["premium_aal"] == pytest.approx(1e6)


def test_the_engine_period_loss_table_is_read_by_its_own_columns():
    csv = (
        b"Period,PeriodWeight,EventId,Year,Month,Day,Hour,Minute,SummaryId,SampleId,Loss,ImpactedExposure\n"
        b"1,0.5,7,1,1,1,0,0,1,1,4000000,1\n"
        b"1,0.5,7,1,1,1,0,0,2,1,1000000,1\n"
        b"1,0.5,7,1,1,1,0,0,1,-1,999,1\n"
        b"2,0.5,9,2,1,1,0,0,1,1,2000000,1\n"
    )
    losses = EventLosses.from_splt(csv, summary_classes={1: "1", 2: UNCOVERED}, samples=1)
    assert losses.periods == 2
    assert losses.classes == ("1", UNCOVERED)
    assert losses.event.tolist() == [7, 9]
    assert losses.losses.tolist() == [[4e6, 1e6], [2e6, 0.0]]
