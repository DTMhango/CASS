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
from cass_converter.enrichment import Attributes, Evidence, OccupancyClass, read_stock_prior
from cass_converter.gem import LossCategory, read_country
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


def sources(code: str):
    region, name = COUNTRIES[code]
    root = Path(GEM_PATH)
    models = read_country(
        root / "global_vulnerability_model" / region / name, country_code=code
    )
    prior = read_stock_prior(
        root
        / "global_exposure_model"
        / region
        / name
        / "summaries"
        / "Exposure_Summary_Taxonomy.csv",
        country_code=code,
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
        enrichment=pilot_enrichment.enrichment(code),
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
        mixture = pilot_enrichment.enrichment(code).resolve(
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
    assert any("licensed" in item for item in questions)
