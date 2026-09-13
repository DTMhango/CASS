"""The OpenQuake reference comparison (work package 4, step 9).

The comparison is only worth as much as the portfolio behind it, so most of
what is held here is arithmetic on value: the assets OpenQuake reads must carry
the same money the keys file accounted for, spread across the GEM taxonomies at
the weights the CASS blend used. A comparison whose two sides insure different
amounts would report a difference in method that was really a difference in
exposure.

The rest is the refusals: an identifier the dictionary cannot explain, a
location with no coordinate, a loss type OpenQuake does not read as money, and a
measurement with no approved tolerance, which is reported rather than passed.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cass_converter import reference
from cass_converter.reference import ReferenceError, ReferenceLosses

DICTIONARY = {
    "country_code": "ID",
    "entries": [
        {
            "vulnerability_id": 1,
            "coverage_type": 1,
            "loss_category": "structural",
            "required_imt": "PGA",
            "channel_weight": 0.6,
            "blended_from": [
                {"taxonomy": "CR/LFINF/CDM+ERM/H:3/RES", "weight": 0.7},
                {"taxonomy": "MCF/LWAL/CDL+ERL/H:2/RES", "weight": 0.3},
            ],
        },
        {
            "vulnerability_id": 2,
            "coverage_type": 1,
            "loss_category": "structural",
            "required_imt": "SA(0.3)",
            "channel_weight": 0.4,
            "blended_from": [{"taxonomy": "W/LFM/CDL+ERM/H:1/RES", "weight": 1.0}],
        },
        {
            "vulnerability_id": 3,
            "coverage_type": 3,
            "loss_category": "contents",
            "required_imt": "PGA",
            "channel_weight": 1.0,
            "blended_from": [{"taxonomy": "CR/LFINF/CDM+ERM/H:3/RES", "weight": 1.0}],
        },
    ],
}

LOCATIONS = {
    "ACC-1/LOC-1": {
        "AccNumber": "ACC-1",
        "LocNumber": "LOC-1",
        "Latitude": "-6.2088",
        "Longitude": "106.8456",
        "BuildingTIV": "1000000",
        "OtherTIV": "50000",
        "ContentsTIV": "200000",
        "BITIV": "0",
    }
}


def key(coverage: int, vulnerability: int, weight: str, status: str = "success"):
    return {
        "LocID": "ACC-1/LOC-1",
        "AccNumber": "ACC-1",
        "LocNumber": "LOC-1",
        "PerilID": "QEQ",
        "CoverageTypeID": coverage,
        "AreaPerilID": 11510,
        "VulnerabilityID": vulnerability,
        "ChannelWeight": weight,
        "Status": status,
        "Message": "",
    }


#: One building mapped through two measures, its contents through one, and two
#: rows that are not modelled: the shape a real keys file has.
KEYS = [
    key(1, 1, "0.600000"),
    key(1, 2, "0.400000"),
    key(3, 3, "1.000000"),
    key(2, 0, "1.000000", status="notatrisk"),
    key(4, 0, "1.000000", status="fail"),
]


@pytest.fixture()
def exposure():
    return reference.build_exposure(KEYS, locations=LOCATIONS, dictionary=DICTIONARY)


# -- the money -------------------------------------------------------------------

def test_the_assets_carry_the_value_the_keys_accounted_for(exposure):
    assert exposure.written_value["structural"] == Decimal("1000000.00")
    assert exposure.written_value["contents"] == Decimal("200000.00")
    assert exposure.reconciles is True
    assert exposure.difference == 0


def test_value_is_split_across_the_taxonomies_the_blend_used(exposure):
    structural = {
        asset.taxonomy: asset.value("structural")
        for asset in exposure.assets
        if "structural" in asset.values
    }

    # 1,000,000 through a 0.6 channel, split 0.7 and 0.3; and a 0.4 channel whole.
    assert structural == {
        "CR/LFINF/CDM+ERM/H:3/RES": Decimal("420000.00"),
        "MCF/LWAL/CDL+ERL/H:2/RES": Decimal("180000.00"),
        "W/LFM/CDL+ERM/H:1/RES": Decimal("400000.00"),
    }


def test_a_split_that_does_not_divide_evenly_still_adds_back_to_the_whole():
    """The residue goes on the largest share, as every TIV allocation here does."""
    dictionary = {
        "entries": [
            {
                "vulnerability_id": 1,
                "coverage_type": 1,
                "loss_category": "structural",
                "blended_from": [
                    {"taxonomy": "A/LFM/CDN+ERN/H:1/RES", "weight": 1 / 3},
                    {"taxonomy": "B/LFM/CDN+ERN/H:1/RES", "weight": 1 / 3},
                    {"taxonomy": "C/LFM/CDN+ERN/H:1/RES", "weight": 1 / 3},
                ],
            }
        ]
    }
    locations = {"ACC-1/LOC-1": {**LOCATIONS["ACC-1/LOC-1"], "BuildingTIV": "100.00"}}

    built = reference.build_exposure(
        [key(1, 1, "1.000000")], locations=locations, dictionary=dictionary
    )

    assert sorted(asset.value("structural") for asset in built.assets) == [
        Decimal("33.33"),
        Decimal("33.33"),
        Decimal("33.34"),
    ]
    assert built.written_value["structural"] == Decimal("100.00")
    assert built.reconciles is True


def test_channel_weights_that_do_not_divide_evenly_add_back_to_the_coverage():
    """The weights a live vulnerability set carries, on a value that rounds."""
    dictionary = {
        "entries": [
            {
                "vulnerability_id": 1,
                "coverage_type": 1,
                "loss_category": "structural",
                "blended_from": [{"taxonomy": "A/LFM/CDN+ERN/H:1/RES", "weight": 1.0}],
            },
            {
                "vulnerability_id": 2,
                "coverage_type": 1,
                "loss_category": "structural",
                "blended_from": [{"taxonomy": "B/LFM/CDN+ERN/H:1/RES", "weight": 1.0}],
            },
        ]
    }
    locations = {
        "ACC-1/LOC-1": {**LOCATIONS["ACC-1/LOC-1"], "BuildingTIV": "1234567.89"}
    }

    built = reference.build_exposure(
        [key(1, 1, "0.570660516533007"), key(1, 2, "0.429339483466993")],
        locations=locations,
        dictionary=dictionary,
    )

    assert built.written_value["structural"] == Decimal("1234567.89")
    assert built.reconciles is True


def test_an_identifier_with_no_taxonomy_behind_it_is_refused():
    dictionary = {
        "entries": [
            {
                "vulnerability_id": 1,
                "coverage_type": 1,
                "loss_category": "structural",
                "blended_from": [],
            }
        ]
    }

    with pytest.raises(ReferenceError, match="no taxonomy"):
        reference.build_exposure(
            [key(1, 1, "1.000000")], locations=LOCATIONS, dictionary=dictionary
        )


def test_a_coverage_answered_by_several_channels_is_counted_once(exposure):
    """The channels describe one sum of money, not several."""
    assert exposure.source_value["structural"] == Decimal("1000000")


def test_value_that_was_not_mapped_reaches_neither_side(exposure):
    """Not-at-risk and failed rows are not modelled, here or in Oasis."""
    assert "OtherTIV" not in str(exposure.as_dict())
    assert exposure.written_value.get("nonstructural") is None
    assert sum(len(asset.values) for asset in exposure.assets) == len(exposure.assets)


def test_every_asset_sits_at_its_locations_coordinate(exposure):
    assert {(asset.longitude, asset.latitude) for asset in exposure.assets} == {
        (106.8456, -6.2088)
    }
    assert exposure.placement == {"location_coordinate": 3}


def test_assets_are_placed_at_the_cell_cass_mapped_them_to_when_it_is_known():
    """Both sides then read one site's ground motion, not two nearby ones."""
    built = reference.build_exposure(
        KEYS,
        locations=LOCATIONS,
        dictionary=DICTIONARY,
        cell_centroids={11510: (107.0, -6.5)},
    )

    assert {(asset.longitude, asset.latitude) for asset in built.assets} == {
        (107.0, -6.5)
    }
    assert built.placement == {"cell_centroid": 3}


def test_a_cell_the_grid_does_not_carry_leaves_the_asset_where_it_is():
    built = reference.build_exposure(
        KEYS, locations=LOCATIONS, dictionary=DICTIONARY, cell_centroids={999: (1.0, 2.0)}
    )

    assert {(asset.longitude, asset.latitude) for asset in built.assets} == {
        (106.8456, -6.2088)
    }
    assert built.placement == {"location_coordinate": 3}


# -- what it refuses ---------------------------------------------------------------

def test_an_identifier_the_dictionary_cannot_explain_is_reported():
    rows = [key(1, 99, "1.000000")]

    built = reference.build_exposure(rows, locations=LOCATIONS, dictionary=DICTIONARY)

    assert built.unmatched == ("99",)
    assert built.reconciles is False


def test_a_location_the_exposure_does_not_hold_is_refused():
    rows = [{**key(1, 1, "1.000000"), "LocID": "ACC-9/LOC-9"}]

    with pytest.raises(ReferenceError, match="same run"):
        reference.build_exposure(rows, locations=LOCATIONS, dictionary=DICTIONARY)


def test_a_location_without_a_coordinate_is_refused():
    locations = {"ACC-1/LOC-1": {**LOCATIONS["ACC-1/LOC-1"], "Latitude": ""}}

    with pytest.raises(ReferenceError, match="coordinate"):
        reference.build_exposure(KEYS, locations=locations, dictionary=DICTIONARY)


def test_a_loss_category_that_is_not_money_is_refused():
    """Fatalities share the file format and are a ratio of occupants."""
    dictionary = {
        "entries": [
            {
                "vulnerability_id": 1,
                "coverage_type": 1,
                "loss_category": "fatalities",
                "blended_from": [{"taxonomy": "CR/LFINF/CDM+ERM/H:3/RES", "weight": 1.0}],
            }
        ]
    }

    with pytest.raises(ReferenceError, match="money"):
        reference.build_exposure(
            [key(1, 1, "1.000000")], locations=LOCATIONS, dictionary=dictionary
        )


def test_a_dictionary_with_no_entries_is_refused():
    with pytest.raises(ReferenceError, match="no entries"):
        reference.build_exposure(KEYS, locations=LOCATIONS, dictionary={"entries": []})


# -- the files the engine reads -----------------------------------------------------

def test_the_asset_table_carries_the_columns_the_engine_reads(exposure):
    lines = reference.exposure_csv(exposure).decode().splitlines()

    assert lines[0] == "id,lon,lat,taxonomy,number,structural,contents"
    assert len(lines) == len(exposure.assets) + 1
    assert lines[1].startswith("a000001,106.845600,-6.208800,CR/LFINF/CDM+ERM/H:3/RES,1,")


def test_the_exposure_model_declares_a_cost_type_for_each_loss_type(exposure):
    document = reference.exposure_xml(exposure, currency="USD").decode()

    assert '<costType name="structural" type="aggregated" unit="USD"/>' in document
    assert '<costType name="contents" type="aggregated" unit="USD"/>' in document
    assert "nonstructural" not in document
    assert "<assets>exposure.csv</assets>" in document


def test_a_portfolio_that_mapped_nothing_produces_no_job():
    empty = reference.build_exposure([], locations=LOCATIONS, dictionary=DICTIONARY)

    with pytest.raises(ReferenceError, match="nothing"):
        reference.exposure_xml(empty)


def test_the_job_names_a_vulnerability_file_for_every_loss_type(exposure):
    produced = reference.files(
        exposure,
        vulnerability={"structural": b"<nrml/>", "contents": b"<nrml/>"},
        description="A comparison",
    )
    ini = produced["job.ini"].decode()

    assert "calculation_mode = event_based_risk" in ini
    assert "structural_vulnerability_file = vulnerability_structural.xml" in ini
    assert "contents_vulnerability_file = vulnerability_contents.xml" in ini
    # No hazard is named and none is described: the job is chained onto the
    # calculation that produced the footprint, so both sides read the same
    # events rather than two sets computed the same way.
    assert "hazard_calculation_id" not in ini
    assert "gsim" not in ini
    assert "source_model" not in ini
    assert set(produced) == {
        "job.ini",
        "exposure.xml",
        "exposure.csv",
        "vulnerability_structural.xml",
        "vulnerability_contents.xml",
    }


def test_value_with_no_vulnerability_file_is_refused_rather_than_run(exposure):
    """OpenQuake would report zero for it, which reads as no damage."""
    with pytest.raises(ReferenceError, match="contents"):
        reference.files(
            exposure, vulnerability={"structural": b"<nrml/>"}, description="A comparison"
        )


# -- reading what the engine computed ------------------------------------------------

EVENT_LOSSES = (
    "#,,,\"generated_by='OpenQuake engine 3.23.4'\"\n"
    "event_id,agg_id,loss_id,loss\n"
    "7,0,0,1000.5\n"
    "7,0,1,499.5\n"
    "9,0,0,250.0\n"
    "11,0,0,0.0\n"
)


def test_losses_are_summed_for_each_event_across_loss_types():
    losses = reference.read_losses(
        {"risk_by_event_3.csv": EVENT_LOSSES.encode()}, effective_time=1000.0
    )

    assert losses.by_event == {7: Decimal("1500.0"), 9: Decimal("250.0")}
    assert losses.total == Decimal("1750.0")
    assert losses.average_annual_loss == Decimal("1.75")


def test_an_export_that_is_missing_or_empty_says_so():
    with pytest.raises(ReferenceError, match="exported no risk_by_event"):
        reference.read_losses({"avg_losses_3.csv": b"asset_id\n"}, effective_time=1000.0)

    header = "event_id,agg_id,loss_id,loss\n"
    with pytest.raises(ReferenceError, match="no event losses"):
        reference.read_losses({"risk_by_event.csv": header.encode()}, effective_time=1000.0)


def test_the_exceedance_loss_is_the_one_at_that_rank():
    """1,000 years of events: the 100-year loss is the tenth largest."""
    by_event = {index: Decimal(str(1000 - index)) for index in range(1, 41)}
    losses = ReferenceLosses(by_event=by_event, effective_time=1000.0)

    found = losses.exceedance([100, 50, 25])

    assert found["100"] == Decimal("990")  # tenth largest
    assert found["50"] == Decimal("980")  # twentieth
    assert found["25"] == Decimal("960")  # fortieth


def test_a_return_period_the_event_set_cannot_reach_is_left_out():
    losses = ReferenceLosses(by_event={1: Decimal("10")}, effective_time=1000.0)

    assert losses.exceedance([1000, 500]) == {"1000": Decimal("10")}


def test_losses_with_no_effective_time_refuse_to_become_a_rate():
    losses = ReferenceLosses(by_event={1: Decimal("10")}, effective_time=0.0)

    with pytest.raises(ReferenceError, match="effective time"):
        _ = losses.average_annual_loss


# -- the comparison --------------------------------------------------------------------

def test_the_comparison_reports_a_ratio_and_decides_nothing_by_default(exposure):
    losses = ReferenceLosses(by_event={1: Decimal("2000")}, effective_time=1000.0)

    report = reference.compare(
        exposure=exposure,
        reference=losses,
        oasis_average_annual_loss=Decimal("1.90"),
    )

    [measurement] = report["measurements"]
    assert measurement["openquake"] == "2"
    assert measurement["ratio"] == pytest.approx(0.95)
    assert measurement["decided"] is False
    assert report["decided"] is False
    assert report["within_tolerance"] is None
    assert "no tolerance is approved" in report["note"].lower()


def test_an_approved_tolerance_decides_the_measurement(exposure):
    losses = ReferenceLosses(by_event={1: Decimal("2000")}, effective_time=1000.0)

    within = reference.compare(
        exposure=exposure,
        reference=losses,
        oasis_average_annual_loss=Decimal("1.90"),
        tolerances={"average_annual_loss": 0.10},
    )
    outside = reference.compare(
        exposure=exposure,
        reference=losses,
        oasis_average_annual_loss=Decimal("1.50"),
        tolerances={"average_annual_loss": 0.10},
    )

    assert within["decided"] is True and within["within_tolerance"] is True
    assert outside["decided"] is True and outside["within_tolerance"] is False


def test_return_periods_are_compared_where_both_sides_have_one(exposure):
    by_event = {index: Decimal(str(1000 - index)) for index in range(1, 41)}
    losses = ReferenceLosses(by_event=by_event, effective_time=1000.0)

    report = reference.compare(
        exposure=exposure,
        reference=losses,
        oasis_average_annual_loss=Decimal("39"),
        oasis_return_period_losses={"100": Decimal("980"), "10000": Decimal("1")},
    )

    keys = {item["key"] for item in report["measurements"]}
    assert "return_period_100" in keys
    # Nothing is extrapolated beyond what the event set supports.
    assert "return_period_10000" not in keys


def test_a_portfolio_that_does_not_reconcile_is_refused_rather_than_compared():
    built = reference.build_exposure(
        [key(1, 99, "1.000000")], locations=LOCATIONS, dictionary=DICTIONARY
    )
    losses = ReferenceLosses(by_event={1: Decimal("10")}, effective_time=1000.0)

    with pytest.raises(ReferenceError, match="different portfolios"):
        reference.compare(
            exposure=built,
            reference=losses,
            oasis_average_annual_loss=Decimal("1"),
        )
