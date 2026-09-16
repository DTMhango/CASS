"""The pilot enrichments against the published GEM model and exposure summaries.

The numbers here are the ones a model owner needs before deciding anything:
how many classes a Klapton Re schedule can distinguish, how many of them the
schedule actually determines, and how many cannot be one Oasis function at any
damage-bin resolution.

    CASS_GEM_PATH=/path/to/models/gem/v2026.0.0 \\
        pytest -m integration services/converter/tests/test_enrichment_acceptance.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cass_converter import pilot_enrichment
from cass_converter.bins import DamageBinSet, IntensityBinSet, log_bins, oasis_damage_bins
from cass_converter.enrichment import (
    Attributes,
    Evidence,
    OccupancyClass,
    enrichment_from,
    read_stock_prior,
)
from cass_converter.gem import LossCategory, catalogue, country_identity
from cass_converter.model_build import (
    build_country,
    dictionary,
    mapping_csv,
    multi_imt_report,
)
from cass_converter.policy import ConversionPolicy, EventIdentity, IMTRepresentation

pytestmark = pytest.mark.integration

GEM_PATH = os.environ.get("CASS_GEM_PATH", "")

needs_gem = pytest.mark.skipif(
    not (GEM_PATH and Path(GEM_PATH).is_dir()),
    reason="set CASS_GEM_PATH to a GEM v2026.0.0 checkout",
)

IMTS = ("PGA", "SA(0.3)", "SA(0.6)", "SA(1.0)")
COUNTRIES = {"ID": ("Southeast_Asia", "Indonesia"), "NP": ("South_Asia", "Nepal")}

#: Nepal's enrichment, written the way any country is now given one. CASS no
#: longer compiles Nepal in, but GEM's published data for it is still the best
#: test of a building stock unlike Indonesia's.
NEPAL = enrichment_from(
    {
        "name": "np_gem_stock",
        "version": "0.1.0-written",
        "country_code": "NP",
        "iso3": "NPL",
        "design_eras": [
            {
                "to_year": 1994,
                "design_levels": ["CDN"],
                "reason": "Before the Nepal National Building Code.",
            },
            {
                "to_year": 2015,
                "design_levels": ["CDN", "CDL"],
                "reason": "The code is mandatory in principle from 2003; enforcement is uneven.",
            },
            {
                "to_year": None,
                "design_levels": ["CDL", "CDM"],
                "reason": "After Gorkha, which changed enforcement and reconstruction practice.",
            },
        ],
        "open_questions": [
            "GEM's exposure is national building stock and a facultative book is not.",
            "Only shake is modelled.",
        ],
    }
)


def enrichment_for(code: str):
    return NEPAL if code == "NP" else pilot_enrichment.enrichment(code.upper())


def sources(code: str, *, mapped: bool = True):
    region, folder = COUNTRIES[code]
    models, prior, _ = pilot_enrichment.load(
        GEM_PATH,
        code,
        use_published_mapping=mapped,
        chosen=enrichment_for(code),
        region=region,
        folder=folder,
    )
    return models, prior


@pytest.fixture()
def policy():
    return ConversionPolicy(
        event_identity=EventIdentity.RUPTURE_BINNED,
        imt_representation=IMTRepresentation.CORRELATED_CHANNELS,
        approval_reference="NOT-APPROVED-TEST-ONLY",
        imts=IMTS,
        investigation_time=1.0,
    )


@pytest.fixture()
def bins():
    return (
        DamageBinSet(version="1.0.0", bins=oasis_damage_bins(50)),
        {
            imt: IntensityBinSet(
                imt=imt, version="1.0.0", bins=log_bins("0.05", "10.0", 40)
            )
            for imt in IMTS
        },
    )


def built(code: str, policy, bins):
    damage, intensity = bins
    models, prior = sources(code)
    return build_country(
        enrichment=enrichment_for(code),
        models=models,
        prior=prior,
        intensity_bins=intensity,
        damage_bins=damage,
        policy=policy,
    )


# -- the stock priors differ, which is why they are read per country ------------------

@needs_gem
def test_the_two_countries_have_different_commercial_stock():
    """A mapping that gave both the same prior would be wrong invisibly.

    Nepali commercial value is two thirds non-ductile concrete and a quarter
    unreinforced masonry. Indonesian commercial is 42% non-ductile with a
    quarter in ductile concrete, which Nepal's commercial stock barely has.
    """
    _, indonesia = sources("ID")
    _, nepal = sources("NP")

    indonesian = indonesia.profile(OccupancyClass.COMMERCIAL)
    nepali = nepal.profile(OccupancyClass.COMMERCIAL)

    assert indonesian["CR-"] == pytest.approx(0.424, abs=0.01)
    assert indonesian["CR+"] == pytest.approx(0.257, abs=0.01)
    assert nepali["CR-"] == pytest.approx(0.665, abs=0.01)
    assert nepali.get("CR+", 0.0) == pytest.approx(0.0, abs=0.01)


@needs_gem
def test_weighting_by_value_and_by_count_disagree_sharply():
    """Which is why the basis is named on every prior rather than assumed.

    Counting buildings puts Indonesian residential stock in masonry; counting
    replacement cost moves a great deal of it into concrete, and a facultative
    book is closer to the second.
    """
    from cass_converter.enrichment import Weighting

    _, by_value = sources("ID")
    by_count = read_stock_prior(
        Path(GEM_PATH)
        / "global_exposure_model/Southeast_Asia/Indonesia/summaries"
        / "Exposure_Summary_Taxonomy.csv",
        country_code="ID",
        weighting=Weighting.BUILDINGS,
    )
    value = by_value.profile(OccupancyClass.RESIDENTIAL)
    count = by_count.profile(OccupancyClass.RESIDENTIAL)
    assert abs(value["CR-"] - count["CR-"]) > 0.05


# -- what a real schedule resolves to --------------------------------------------------

@needs_gem
def test_a_schedule_stating_nothing_but_occupancy_reaches_the_whole_stock():
    models, prior = sources("ID")
    structural = models[LossCategory.STRUCTURAL]
    mixture = pilot_enrichment.enrichment("ID").resolve(
        Attributes("1100"), structural, prior
    )
    assert len(mixture) == 15
    assert mixture.construction is Evidence.PRIOR
    assert mixture.spans_intensity_measures


@needs_gem
def test_stating_the_storey_count_is_what_collapses_the_channels():
    """The finding that matters for the intake template.

    Height decides the spectral period a structure responds at, so a risk that
    states it usually reaches one measure and a risk that does not reaches all
    four. The template already asks for storeys; the pilot book does not carry
    them, and this is what that costs.
    """
    models, prior = sources("ID")
    structural = models[LossCategory.STRUCTURAL]
    enrichment = pilot_enrichment.enrichment("ID")

    silent = enrichment.resolve(Attributes("1100", "5150"), structural, prior)
    stated = enrichment.resolve(Attributes("1100", "5150", storeys=1), structural, prior)

    assert len(silent.intensity_measures) > len(stated.intensity_measures)
    assert not stated.spans_intensity_measures


@needs_gem
def test_a_nepali_masonry_risk_reaches_many_more_candidates_than_an_indonesian_one():
    """OED has one masonry code and Nepal's stock has a dozen kinds of masonry."""
    counts = {}
    for code in ("ID", "NP"):
        models, prior = sources(code)
        mixture = enrichment_for(code).resolve(
            Attributes("1050", "5100"), models[LossCategory.STRUCTURAL], prior
        )
        counts[code] = len(mixture)
    assert counts["NP"] > 3 * counts["ID"]


@needs_gem
def test_unknown_occupancy_still_reaches_nothing():
    """The section 8 gate keeps its meaning against the real model."""
    models, prior = sources("ID")
    mixture = pilot_enrichment.enrichment("ID").resolve(
        Attributes("1000"), models[LossCategory.STRUCTURAL], prior
    )
    assert not mixture.resolved


# -- the build ---------------------------------------------------------------------

@needs_gem
@pytest.mark.parametrize("code", ["ID", "NP"])
def test_a_country_builds_every_class_within_tolerance(code, policy, bins):
    """Every blended function reproduces the mixture it came from."""
    build = built(code, policy, bins)
    assert len(build.classes) == 240
    report = build.as_dict()["table"]
    assert report["worst_absolute_mean_error"]["error"] < 1e-06


@needs_gem
@pytest.mark.parametrize("code", ["ID", "NP"])
def test_most_classes_are_one_oasis_function(code, policy, bins):
    """The multi-IMT problem is real and it is a minority of the model.

    Between three quarters and four fifths of classes reach candidates that all
    respond at the same period, so they convert under any representation. The
    open decision applies to the rest, and this is how large the rest is.
    """
    build = built(code, policy, bins)
    report = multi_imt_report(build)
    assert 0.10 < report["share_needing_multi_imt"] < 0.30


@needs_gem
def test_the_classes_spanning_every_measure_are_the_ones_with_no_height():
    """So the expensive cases are the ones the intake template could fix."""
    from cass_converter.bins import DamageBinSet, IntensityBinSet, log_bins
    from cass_converter.bins import oasis_damage_bins as bins_of

    damage = DamageBinSet(version="1.0.0", bins=bins_of(50))
    intensity = {
        imt: IntensityBinSet(imt=imt, version="1.0.0", bins=log_bins("0.05", "10.0", 40))
        for imt in IMTS
    }
    models, prior = sources("ID")
    build = build_country(
        enrichment=pilot_enrichment.enrichment("ID"),
        models=models,
        prior=prior,
        intensity_bins=intensity,
        damage_bins=damage,
        policy=ConversionPolicy(
            event_identity=EventIdentity.RUPTURE_BINNED,
            imt_representation=IMTRepresentation.CORRELATED_CHANNELS,
            approval_reference="NOT-APPROVED-TEST-ONLY",
            imts=IMTS,
            investigation_time=1.0,
        ),
    )
    widest = [
        item for item in build.multi_imt_classes if len(item.intensity_measures) == 4
    ]
    assert widest
    assert {item.storey_band for item in widest} == {"unstated"}


@needs_gem
@pytest.mark.parametrize("code", ["ID", "NP"])
def test_every_identifier_traces_back_to_published_taxonomies(code, policy, bins):
    """A loss has to be explainable as damage to particular buildings."""
    build = built(code, policy, bins)
    models, _ = sources(code)
    published = {
        item.taxonomy.text
        for model in models.values()
        for item in model.functions
    }
    for entry in dictionary(build)["entries"]:
        assert entry["blended_from"]
        for component in entry["blended_from"]:
            assert component["taxonomy"] in published


@needs_gem
def test_the_mapping_states_a_measure_and_a_weight_for_every_row(policy, bins):
    import csv
    import io

    build = built("ID", policy, bins)
    rows = list(csv.DictReader(io.StringIO(mapping_csv(build).decode())))
    assert rows
    assert all(row["RequiredIMT"] in IMTS for row in rows)
    assert all(0.0 < float(row["ChannelWeight"]) <= 1.0 for row in rows)


@needs_gem
@pytest.mark.parametrize("code", ["ID", "NP"])
def test_the_build_records_every_source_it_used(code, policy, bins):
    build = built(code, policy, bins)
    models, prior = sources(code)
    assert build.sources["exposure_summary"] == prior.checksum
    assert (
        build.sources["vulnerability_structural"]
        == models[LossCategory.STRUCTURAL].checksum
    )


@needs_gem
@pytest.mark.parametrize("code", ["ID", "NP"])
def test_the_open_questions_survive_into_the_build(code, policy, bins):
    """A release that quietly dropped them would read as a resolved model."""
    build = built(code, policy, bins)
    questions = build.as_dict()["open_questions"]
    assert any("facultative" in item for item in questions)
    assert any("Only shake is modelled" in item for item in questions)


# -- GEM's published mapping, now that it is to hand --------------------------------

@needs_gem
@pytest.mark.parametrize("code", ["ID", "NP"])
def test_the_published_mapping_places_every_dollar_of_stock(code):
    """100% in both countries, and the taxonomy strings match exactly.

    Worth asserting rather than assuming: the exposure summaries and the
    mapping are separate files, and a release that renamed a taxonomy in one
    would leave value with nowhere to go.
    """
    from cass_converter.enrichment import mapping_coverage, read_taxonomy_mapping

    enrichment = enrichment_for(code)
    _, prior = sources(code, mapped=False)
    mapping = read_taxonomy_mapping(
        Path(GEM_PATH) / pilot_enrichment.MAPPING_PATH, iso3=enrichment.iso3
    )
    assert mapping_coverage(prior, mapping)["covered_share"] == pytest.approx(1.0)


@needs_gem
@pytest.mark.parametrize("code", ["ID", "NP"])
def test_the_mapping_reaches_every_published_function(code):
    """A function nothing maps to would be a curve no risk could ever use."""
    from cass_converter.enrichment import read_taxonomy_mapping

    enrichment = enrichment_for(code)
    models, _ = sources(code)
    mapping = read_taxonomy_mapping(
        Path(GEM_PATH) / pilot_enrichment.MAPPING_PATH, iso3=enrichment.iso3
    )
    published = set(models[LossCategory.STRUCTURAL].by_taxonomy)
    assert mapping.targets == published


@needs_gem
def test_indonesias_repeated_rows_are_duplicates_and_nepals_are_mixtures():
    """The same shape in the file means two different things by country.

    Indonesia repeats six taxonomies with an identical target and weight -- its
    six urban/rural exposure splits. Nepal states five real mixtures, where an
    exposure band covering two storey counts is split between the two published
    functions. Summing the first would double a weight; collapsing the second
    would throw a split away.
    """
    from cass_converter.enrichment import read_taxonomy_mapping

    path = Path(GEM_PATH) / pilot_enrichment.MAPPING_PATH
    indonesia = read_taxonomy_mapping(path, iso3="IDN")
    nepal = read_taxonomy_mapping(path, iso3="NPL")

    assert all(len(values) == 1 for values in indonesia.entries.values())
    mixed = [values for values in nepal.entries.values() if len(values) > 1]
    assert len(mixed) == 5
    assert all(
        sum(weight for _, weight in values) == pytest.approx(1.0) for values in mixed
    )


@needs_gem
@pytest.mark.parametrize("code", ["ID", "NP"])
def test_the_published_weights_are_not_the_macro_class_fallback(code):
    """Which is the point of reading the file.

    The fallback divides a macro class's value equally among its functions, so
    a concrete mixture comes out flat. GEM's mapping gives each function the
    value that maps to it, and the shape is nothing like flat.
    """
    from cass_converter.enrichment import Attributes

    enrichment = enrichment_for(code)
    models, exact = sources(code)
    _, coarse = sources(code, mapped=False)
    structural = models[LossCategory.STRUCTURAL]

    attributes = Attributes("1100", "5150")
    flat = enrichment.resolve(attributes, structural, coarse)
    weighted = enrichment.resolve(attributes, structural, exact)

    assert coarse.uses_exact_weights is False
    assert exact.uses_exact_weights is True
    assert len(flat) == len(weighted)

    flat_weights = [round(item.weight, 9) for item in flat.candidates]
    exact_weights = [round(item.weight, 9) for item in weighted.candidates]
    assert sorted(flat_weights) != pytest.approx(sorted(exact_weights))

    # The fallback divides each macro class equally, so its weights repeat.
    # GEM's mapping gives every function its own share, so they do not.
    assert len(set(flat_weights)) < len(set(exact_weights))
    assert len(set(exact_weights)) == len(exact_weights)


@needs_gem
@pytest.mark.parametrize("code", ["ID", "NP"])
def test_the_build_records_which_weighting_produced_it(code, policy, bins):
    """Two releases weighted differently are different models."""
    damage, intensity = bins
    models, prior = sources(code)
    build = build_country(
        enrichment=enrichment_for(code),
        models=models,
        prior=prior,
        intensity_bins=intensity,
        damage_bins=damage,
        policy=policy,
    )
    assert prior.uses_exact_weights
    assert prior.mapping_checksum
    assert build.sources["exposure_summary"] == prior.checksum


# -- the whole release, not just the countries somebody happened to try ------------

#: The countries GEM v2026.0.0 publishes in HAZUS classes rather than in its own
#: building taxonomy. CASS cannot map an OED schedule onto them, and says so in
#: the catalogue rather than failing part way through a build.
HAZUS_COUNTRIES = {
    "American_Samoa",
    "Canada",
    "Guam",
    "Northern_Mariana_Islands",
    "Puerto_Rico",
    "US_Virgin_Islands",
    "United_States",
}


@needs_gem
def test_every_country_in_the_release_pairs_with_its_exposure():
    """The two repositories name two countries differently -- Turkey is published
    as Turkiye, Cape Verde as Cabo Verde -- and pairing them by folder name alone
    left those countries unbuildable. A release that renames a third should fail
    here rather than quietly drop it from what can be built."""
    listed = catalogue(GEM_PATH, identify=True)

    unidentified = {
        item["country"]: item["problem"] for item in listed if not item["iso3"]
    }

    assert listed
    assert unidentified == {}


@needs_gem
def test_only_the_hazus_countries_are_unbuildable():
    """Whatever CASS cannot build, it says so before a country is chosen. The
    parser used to read the occupancy as the fifth segment, which quietly cost
    the 75 countries whose taxonomies state a roof or an irregularity first."""
    listed = catalogue(GEM_PATH, identify=True)

    refused = {item["country"] for item in listed if item["problem"]}

    assert refused == HAZUS_COUNTRIES
    assert all("HAZUS classes" in item["problem"] for item in listed if item["problem"])


@needs_gem
@pytest.mark.parametrize(
    ("region", "country", "iso3"),
    [
        ("Europe", "Turkey", "TUR"),  # named Turkiye in the exposure repository
        ("East_Asia", "China", "CHN"),  # reports settlement rows beside totals
        ("Africa", "Zambia", "ZMB"),  # taxonomies state a wooden roof
        ("Europe", "Greece", "GRC"),  # taxonomies state a soft storey
    ],
)
def test_a_country_of_each_awkward_shape_builds(region, country, iso3, policy, bins):
    """One per fault the release-wide sweep turned up."""
    damage, intensity = bins
    identity = country_identity(GEM_PATH, region=region, country=country)
    assert identity.iso3 == iso3

    written = enrichment_from(
        {
            "name": f"{identity.country_code.lower()}_acceptance",
            "version": "0.1.0",
            "country_code": identity.country_code,
            "iso3": identity.iso3,
            "design_eras": [
                {"to_year": None, "design_levels": ["CDN", "CDL", "CDM", "CDH"],
                 "reason": "Every level stays a candidate; this is a readability check."},
            ],
        }
    )
    models, prior, resolved = pilot_enrichment.load(
        GEM_PATH, identity.country_code, chosen=written, region=region, folder=country
    )
    build = build_country(
        enrichment=resolved,
        models=models,
        prior=prior,
        intensity_bins=intensity,
        damage_bins=damage,
        policy=policy,
    )

    assert prior.uses_exact_weights
    assert build.classes and build.functions
    # The occupancy is the last segment, so every published function reaches an
    # occupancy class and a schedule can resolve to it. Read as the fifth, the
    # countries stating a roof or an irregularity reached none at all.
    published = models[LossCategory.STRUCTURAL].functions
    assert all(item.taxonomy.occupancy_class is not None for item in published)
