"""What GEM building a risk is taken to be, and how much of that is assumed.

These hold the enrichment to two things. It must never state more than the
schedule and the prior together support -- an unknown attribute produces a
wider mixture, not a confident guess -- and it must say, for every dimension,
whether the answer came from the schedule or from a prior, because a loss built
on the second is a different claim from one built on the first.
"""

from __future__ import annotations

import pytest

from cass_converter.enrichment import (
    MACRO_MATERIALS,
    OED_CONSTRUCTION,
    OED_OCCUPANCY,
    Attributes,
    DesignEra,
    Enrichment,
    EnrichmentError,
    Evidence,
    StockPrior,
    Weighting,
    coverage,
    macro_class,
    mixture_report,
    read_stock_prior,
    resolve_all,
)
from cass_converter.gem import (
    LossCategory,
    OccupancyClass,
    VulnerabilityFunction,
    VulnerabilityModel,
    parse_taxonomy,
)


def function(taxonomy: str, imt: str = "PGA") -> VulnerabilityFunction:
    return VulnerabilityFunction(
        taxonomy=parse_taxonomy(taxonomy),
        loss_category=LossCategory.STRUCTURAL,
        imt=imt,
        intensities=(0.1, 0.5, 1.0),
        mean_loss_ratios=(0.001, 0.2, 0.8),
        coefficients_of_variation=(2.0, 0.8, 0.2),
    )


@pytest.fixture()
def model():
    return VulnerabilityModel(
        country_code="ID",
        loss_category=LossCategory.STRUCTURAL,
        functions=(
            function("CR/LFINF/CDL+ERM/H:2/COM"),
            function("CR/LFINF/CDM+ERM/H:2/COM"),
            function("CR/LDUAL/CDL+ERM/H:8/COM", imt="SA(1.0)"),
            function("MUR+CLBRS/LWAL/CDN+ERN/H:1/COM"),
            function("MCF/LWAL/CDL+ERL/H:2/COM"),
            function("S/LFM/CDL+ERM/H:1/IND"),
            function("W/LFM/CDL+ERM/H:1/RES", imt="SA(0.3)"),
        ),
        source_name="test.xml",
        checksum="0" * 64,
    )


@pytest.fixture()
def prior():
    return StockPrior(
        country_code="ID",
        shares={
            ("COM", "CR-"): (100.0, 400.0),
            ("COM", "CR+"): (50.0, 300.0),
            ("COM", "MUR"): (500.0, 200.0),
            ("COM", "MR|MCF"): (200.0, 100.0),
        },
        source_name="summary.csv",
        checksum="1" * 64,
    )


@pytest.fixture()
def enrichment():
    return Enrichment(
        name="id_test",
        version="1.0.0",
        country_code="ID",
        design_eras=(
            DesignEra(to_year=2002, design_levels=("CDN", "CDL"), reason="pre-code"),
            DesignEra(to_year=None, design_levels=("CDM", "CDH"), reason="post-code"),
        ),
    )


# -- placing a taxonomy in its macro class ------------------------------------------

@pytest.mark.parametrize(
    ("taxonomy", "expected"),
    [
        ("CR/LFINF/CDL+ERM/H:2/COM", "CR-"),
        ("CR/LFINF/CDM+ERM/H:2/COM", "CR+"),
        ("CR/LWAL/CDH+ERH/H:4/RES", "CR+"),
        ("MUR+CLBRS/LWAL/CDN+ERN/H:1/COM", "MUR"),
        ("MUR+ADO/LWAL/CDN+ERL/H:2/RES", "ADO|ST|E"),
        ("MUR+STRUB/LWAL/CDN+ERN/H:1/RES", "ADO|ST|E"),
        ("MCF/LWAL/CDL+ERL/H:2/RES", "MR|MCF"),
        ("S/LFM/CDL+ERM/H:1/IND", "S"),
        ("W+WBB/LFM/CDN+ERM/H:1/RES", "W"),
    ],
)
def test_a_taxonomy_is_placed_in_the_macro_class_the_summary_uses(taxonomy, expected):
    """Concrete splits on ductility, not material, which is why it is read."""
    assert macro_class(parse_taxonomy(taxonomy)) == expected
    assert expected in MACRO_MATERIALS


# -- the stock prior -------------------------------------------------------------------

def test_the_prior_reports_a_share_of_value_by_default(prior):
    assert prior.weight(OccupancyClass.COMMERCIAL, "CR-") == pytest.approx(0.4)
    assert prior.weight(OccupancyClass.COMMERCIAL, "MUR") == pytest.approx(0.2)


def test_counting_buildings_gives_a_different_answer(prior):
    """Which is the point of stating the basis rather than assuming one."""
    counted = StockPrior(
        country_code=prior.country_code,
        shares=prior.shares,
        source_name=prior.source_name,
        checksum=prior.checksum,
        weighting=Weighting.BUILDINGS,
    )
    assert counted.weight(OccupancyClass.COMMERCIAL, "MUR") == pytest.approx(0.5882, abs=1e-3)
    assert counted.weight(OccupancyClass.COMMERCIAL, "CR-") == pytest.approx(0.1176, abs=1e-3)


def test_a_macro_class_the_summary_does_not_report_has_no_share(prior):
    assert prior.weight(OccupancyClass.COMMERCIAL, "W") == 0.0


def test_a_summary_is_read_by_occupancy_and_macro_class(tmp_path):
    path = tmp_path / "Exposure_Summary_Taxonomy.csv"
    path.write_text(
        "OCCUPANCY,MACRO_TAXONOMY,TAXONOMY,SETTLEMENT,BUILDINGS,BLDG_REPL_COST_USD\n"
        "COM,CR-,CR/LFINF/CDL+ERL/H:1-3/COM,TOTAL,10,1000\n"
        "COM,MUR,MUR/LWAL/CDN+ERN/H:1/COM,TOTAL,90,1000\n",
        encoding="utf-8",
    )
    read = read_stock_prior(path, country_code="id")
    assert read.country_code == "ID"
    assert read.weight(OccupancyClass.COMMERCIAL, "CR-") == pytest.approx(0.5)


def test_a_taxonomy_reported_both_as_a_total_and_by_settlement_is_refused(tmp_path):
    """Summing both would count the same buildings twice.

    GEM does not currently do this -- a taxonomy is one or the other -- which is
    exactly why a later release that changed it would go unnoticed.
    """
    path = tmp_path / "Exposure_Summary_Taxonomy.csv"
    path.write_text(
        "OCCUPANCY,MACRO_TAXONOMY,TAXONOMY,SETTLEMENT,BUILDINGS,BLDG_REPL_COST_USD\n"
        "COM,CR-,CR/LFINF/CDL+ERL/H:1-3/COM,TOTAL,10,1000\n"
        "COM,CR-,CR/LFINF/CDL+ERL/H:1-3/COM,URBAN,6,600\n",
        encoding="utf-8",
    )
    with pytest.raises(EnrichmentError, match="count the same buildings twice"):
        read_stock_prior(path, country_code="ID")


def test_settlement_parts_without_a_total_are_summed(tmp_path):
    """Which is how GEM actually reports a taxonomy that splits urban and rural."""
    path = tmp_path / "Exposure_Summary_Taxonomy.csv"
    path.write_text(
        "OCCUPANCY,MACRO_TAXONOMY,TAXONOMY,SETTLEMENT,BUILDINGS,BLDG_REPL_COST_USD\n"
        "COM,CR-,CR/LFINF/CDL+ERL/H:1-3/COM,URBAN,6,600\n"
        "COM,CR-,CR/LFINF/CDL+ERL/H:1-3/COM,RURAL,4,400\n"
        "COM,MUR,MUR/LWAL/CDN+ERN/H:1/COM,TOTAL,90,1000\n",
        encoding="utf-8",
    )
    read = read_stock_prior(path, country_code="ID")
    assert read.weight(OccupancyClass.COMMERCIAL, "CR-") == pytest.approx(0.5)


def test_a_summary_missing_its_columns_is_refused(tmp_path):
    path = tmp_path / "summary.csv"
    path.write_text("OCCUPANCY,TAXONOMY\nCOM,CR\n", encoding="utf-8")
    with pytest.raises(EnrichmentError, match="missing columns"):
        read_stock_prior(path, country_code="ID")


# -- resolving one risk ------------------------------------------------------------------

def test_unknown_occupancy_reaches_nothing(enrichment, model, prior):
    """Deliberate. A risk whose use nobody knows should fail the lookup."""
    mixture = enrichment.resolve(Attributes("1000"), model, prior)
    assert not mixture.resolved
    assert "deliberately unmapped" in mixture.notes[0]
    assert "1000" not in OED_OCCUPANCY


def test_a_stated_occupancy_narrows_to_that_class(enrichment, model, prior):
    mixture = enrichment.resolve(Attributes("1150"), model, prior)
    assert [item.taxonomy for item in mixture.candidates] == ["S/LFM/CDL+ERM/H:1/IND"]
    assert mixture.occupancy is Evidence.STATED


def test_unstated_construction_carries_every_material_as_a_mixture(
    enrichment, model, prior
):
    mixture = enrichment.resolve(Attributes("1100"), model, prior)
    assert len(mixture) == 5
    assert mixture.construction is Evidence.PRIOR
    assert sum(item.weight for item in mixture.candidates) == pytest.approx(1.0)


def test_stated_construction_narrows_the_mixture(enrichment, model, prior):
    mixture = enrichment.resolve(Attributes("1100", "5150"), model, prior)
    assert {item.macro for item in mixture.candidates} == {"CR-", "CR+"}
    assert mixture.construction is Evidence.STATED


def test_oed_masonry_reaches_both_unreinforced_and_confined(enrichment, model, prior):
    """They behave very differently and OED has one code for both."""
    mixture = enrichment.resolve(Attributes("1100", "5100"), model, prior)
    assert {item.macro for item in mixture.candidates} == {"MUR", "MR|MCF"}
    assert OED_CONSTRUCTION["5100"] == ("MUR", "MCF")


def test_the_prior_decides_the_weights_where_the_schedule_cannot(
    enrichment, model, prior
):
    """Commercial value is 40% CR-, 30% CR+, so a concrete risk splits that way."""
    mixture = enrichment.resolve(Attributes("1100", "5150"), model, prior)
    weights = {item.macro: 0.0 for item in mixture.candidates}
    for item in mixture.candidates:
        weights[item.macro] += item.weight
    assert weights["CR-"] == pytest.approx(0.4 / 0.7, abs=1e-06)
    assert weights["CR+"] == pytest.approx(0.3 / 0.7, abs=1e-06)


def test_without_a_prior_the_candidates_are_equally_weighted(enrichment, model):
    mixture = enrichment.resolve(Attributes("1100", "5150"), model, None)
    assert {round(item.weight, 6) for item in mixture.candidates} == {
        round(1 / len(mixture), 6)
    }
    assert mixture.construction is Evidence.STATED
    assert mixture.design is Evidence.UNCONSTRAINED


def test_a_stated_height_picks_the_nearest_published_one(enrichment, model, prior):
    """GEM publishes functions at particular heights, not for bands."""
    mixture = enrichment.resolve(Attributes("1100", "5150", storeys=9), model, prior)
    assert [item.taxonomy for item in mixture.candidates] == [
        "CR/LDUAL/CDL+ERM/H:8/COM"
    ]
    assert mixture.height is Evidence.STATED


def test_an_unstated_height_leaves_every_height_a_candidate(enrichment, model, prior):
    mixture = enrichment.resolve(Attributes("1100", "5150"), model, prior)
    assert len(mixture) == 3
    assert mixture.height is Evidence.PRIOR


def test_a_stated_year_narrows_the_design_level(enrichment, model, prior):
    mixture = enrichment.resolve(
        Attributes("1100", "5150", storeys=2, year_built=2015), model, prior
    )
    assert [item.taxonomy for item in mixture.candidates] == [
        "CR/LFINF/CDM+ERM/H:2/COM"
    ]
    assert mixture.design is Evidence.STATED


def test_a_year_the_enrichment_has_no_era_for_narrows_nothing(model, prior):
    bare = Enrichment(name="bare", version="1.0.0", country_code="ID")
    mixture = bare.resolve(
        Attributes("1100", "5150", storeys=2, year_built=1975), model, prior
    )
    assert mixture.design is Evidence.PRIOR
    assert any("states no design era" in note for note in mixture.notes)


def test_a_fully_determined_risk_is_certain(enrichment, model, prior):
    mixture = enrichment.resolve(
        Attributes("1100", "5150", storeys=2, year_built=2015), model, prior
    )
    assert mixture.is_certain
    assert len(mixture) == 1


def test_anything_less_is_not(enrichment, model, prior):
    """One candidate is not the same as knowing which building it is."""
    mixture = enrichment.resolve(Attributes("1100", "5150", storeys=9), model, prior)
    assert len(mixture) == 1
    assert not mixture.is_certain
    assert mixture.design is Evidence.PRIOR


# -- intensity measures --------------------------------------------------------------

def test_a_mixture_reports_the_measures_it_spans(enrichment, model, prior):
    mixture = enrichment.resolve(Attributes("1100", "5150"), model, prior)
    assert mixture.intensity_measures == ("PGA", "SA(1.0)")
    assert mixture.spans_intensity_measures


def test_a_narrowed_mixture_may_not_span_any(enrichment, model, prior):
    mixture = enrichment.resolve(Attributes("1100", "5150", storeys=2), model, prior)
    assert not mixture.spans_intensity_measures


def test_a_mixture_partitions_into_channels(enrichment, model, prior):
    mixture = enrichment.resolve(Attributes("1100", "5150"), model, prior)
    channels = mixture.by_imt()
    assert set(channels) == {"PGA", "SA(1.0)"}
    assert sum(
        item.weight for group in channels.values() for item in group
    ) == pytest.approx(1.0)


# -- the enrichment itself -----------------------------------------------------------

def test_an_enrichment_for_the_wrong_country_is_refused(enrichment, prior):
    nepal = VulnerabilityModel(
        country_code="NP",
        loss_category=LossCategory.STRUCTURAL,
        functions=(function("S/LFM/CDL+ERM/H:1/IND"),),
        source_name="np.xml",
        checksum="2" * 64,
    )
    with pytest.raises(EnrichmentError, match="is for ID"):
        enrichment.resolve(Attributes("1150"), nepal, prior)


def test_an_enrichment_needs_a_name_and_a_version():
    with pytest.raises(EnrichmentError, match="needs a name"):
        Enrichment(name="", version="1.0.0", country_code="ID")
    with pytest.raises(EnrichmentError, match="needs a version"):
        Enrichment(name="x", version="", country_code="ID")


def test_an_override_of_an_unknown_macro_class_is_refused():
    with pytest.raises(EnrichmentError, match="not a GEM macro class"):
        Enrichment(
            name="x", version="1", country_code="ID", weight_overrides={"BRICK": 0.5}
        )


def test_a_weight_override_replaces_the_published_share(model, prior):
    """For a book whose composition is known better than national stock."""
    tuned = Enrichment(
        name="x",
        version="1",
        country_code="ID",
        weight_overrides={"CR-": 0.9, "CR+": 0.1},
    )
    mixture = tuned.resolve(Attributes("1100", "5150", storeys=2), model, prior)
    weights = {item.macro: item.weight for item in mixture.candidates}
    assert weights["CR-"] == pytest.approx(0.9)
    assert weights["CR+"] == pytest.approx(0.1)


def test_a_minimum_height_drops_the_candidates_below_it(model, prior):
    tall = Enrichment(
        name="x", version="1", country_code="ID", minimum_storeys=5
    )
    mixture = tall.resolve(Attributes("1100", "5150"), model, prior)
    assert [item.taxonomy for item in mixture.candidates] == [
        "CR/LDUAL/CDL+ERM/H:8/COM"
    ]
    assert any("were dropped" in note for note in mixture.notes)


# -- reporting ---------------------------------------------------------------------

def test_the_coverage_report_says_what_reaches_nothing(enrichment, model):
    report = coverage(enrichment, model)
    assert report["unknown_occupancy_is_unmapped"] is True
    assert set(report["occupancy_codes_reaching_functions"]) == {"1050", "1100", "1150"}


def test_the_mixture_report_counts_what_was_assumed(enrichment, model, prior):
    mixtures = resolve_all(
        enrichment,
        [
            Attributes("1100", "5150", storeys=2, year_built=2015),
            Attributes("1100"),
            Attributes("1000"),
        ],
        model,
        prior,
    )
    report = mixture_report(mixtures)
    assert report["risks"] == 3
    assert report["resolved"] == 2
    assert report["unresolved"] == 1
    assert report["certain"] == 1


def test_an_empty_report_is_not_an_error():
    assert mixture_report([]) == {"risks": 0}
