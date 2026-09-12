"""Building a country's classes, channels and Oasis tables.

The build is where the honest answer stops being one function per risk. These
hold it to producing what the data supports -- a mixture where the schedule
underdetermines the building, a channel per intensity measure where the
candidates respond at different periods -- and to saying which classes need the
decision nobody has made yet rather than making it.
"""

from __future__ import annotations

import csv
import io

import pytest

from cass_converter.bins import DamageBinSet, IntensityBinSet, log_bins, oasis_damage_bins
from cass_converter.enrichment import (
    UNKNOWN_CONSTRUCTION,
    Enrichment,
    StockPrior,
)
from cass_converter.gem import (
    LossCategory,
    VulnerabilityFunction,
    VulnerabilityModel,
    parse_taxonomy,
)
from cass_converter.model_build import (
    BAND_STOREYS,
    STOREY_BANDS,
    BuildError,
    build_country,
    classes,
    dictionary,
    evidence_summary,
    mapping_csv,
    multi_imt_report,
    vulnerability_csv,
)
from cass_converter.policy import ConversionPolicy, EventIdentity, IMTRepresentation

IMTS = ("PGA", "SA(0.3)", "SA(1.0)")


def function(taxonomy: str, category: LossCategory, imt: str) -> VulnerabilityFunction:
    return VulnerabilityFunction(
        taxonomy=parse_taxonomy(taxonomy),
        loss_category=category,
        imt=imt,
        intensities=(0.1, 0.5, 1.0),
        mean_loss_ratios=(0.001, 0.2, 0.8),
        coefficients_of_variation=(2.0, 0.8, 0.2),
    )


def model_for(category: LossCategory) -> VulnerabilityModel:
    return VulnerabilityModel(
        country_code="ID",
        loss_category=category,
        functions=(
            function("CR/LFINF/CDL+ERM/H:1/COM", category, "PGA"),
            function("CR/LDUAL/CDM+ERM/H:9/COM", category, "SA(1.0)"),
            function("MUR+CLBRS/LWAL/CDN+ERN/H:1/COM", category, "PGA"),
            function("MCF/LWAL/CDL+ERL/H:4/COM", category, "SA(0.3)"),
            function("S/LFM/CDL+ERM/H:1/IND", category, "PGA"),
            function("W/LFM/CDL+ERM/H:1/RES", category, "PGA"),
        ),
        source_name=f"{category}.xml",
        checksum=f"{category}".ljust(64, "0"),
    )


@pytest.fixture()
def models():
    return {category: model_for(category) for category in LossCategory}


@pytest.fixture()
def prior():
    return StockPrior(
        country_code="ID",
        shares={
            ("COM", "CR-"): (10.0, 400.0),
            ("COM", "CR+"): (10.0, 300.0),
            ("COM", "MUR"): (10.0, 200.0),
            ("COM", "MR|MCF"): (10.0, 100.0),
        },
        source_name="summary.csv",
        checksum="1" * 64,
    )


@pytest.fixture()
def enrichment():
    return Enrichment(name="id_test", version="1.0.0", country_code="ID")


@pytest.fixture()
def bins():
    return {
        "damage": DamageBinSet(version="1.0.0", bins=oasis_damage_bins(20)),
        "intensity": {
            imt: IntensityBinSet(imt=imt, version="1.0.0", bins=log_bins("0.05", "3", 10))
            for imt in IMTS
        },
    }


@pytest.fixture()
def policy():
    return ConversionPolicy(
        event_identity=EventIdentity.RUPTURE_BINNED,
        imt_representation=IMTRepresentation.CORRELATED_CHANNELS,
        approval_reference="TEST-ONLY",
        imts=IMTS,
        investigation_time=1.0,
    )


@pytest.fixture()
def build(enrichment, models, prior, bins, policy):
    return build_country(
        enrichment=enrichment,
        models=models,
        prior=prior,
        intensity_bins=bins["intensity"],
        damage_bins=bins["damage"],
        policy=policy,
        coverage_types=(1, 3),
    )


# -- the class set -------------------------------------------------------------------

def test_a_class_is_an_occupancy_a_construction_and_a_height(enrichment):
    combinations = classes(enrichment)
    assert ("1100", "5150", "mid") in combinations
    assert len({item[0] for item in combinations}) == 3
    assert len({item[2] for item in combinations}) == len(STOREY_BANDS)


def test_unstated_construction_is_a_class_rather_than_an_absence(enrichment):
    """It is the common case: most schedules do not state the material."""
    assert any(item[1] == UNKNOWN_CONSTRUCTION for item in classes(enrichment))


def test_unstated_height_is_a_class_too(enrichment):
    """And the expensive one: height decides the period a structure responds at."""
    assert "unstated" in STOREY_BANDS
    assert BAND_STOREYS["unstated"] is None
    assert any(item[2] == "unstated" for item in classes(enrichment))


def test_unknown_occupancy_gets_no_class(enrichment):
    assert not any(item[0] == "1000" for item in classes(enrichment))


# -- the build ---------------------------------------------------------------------

def test_every_class_that_resolves_becomes_at_least_one_function(build):
    assert build.classes
    assert len(build.functions) >= len(build.classes)


def test_identifiers_are_assigned_once_and_never_repeated(build):
    identifiers = [item.vulnerability_id for item in build.functions]
    assert identifiers == sorted(identifiers)
    assert len(set(identifiers)) == len(identifiers)


def test_a_class_whose_candidates_share_a_measure_is_one_function(build):
    single = [item for item in build.classes if not item.needs_multi_imt]
    assert single
    assert all(len(item.channels) == 1 for item in single)


def test_a_class_whose_candidates_do_not_becomes_a_channel_each(build):
    spanning = build.multi_imt_classes
    assert spanning
    for item in spanning:
        assert len(item.channels) == len(set(item.intensity_measures))
        assert sum(channel.weight for channel in item.channels) == pytest.approx(1.0)


def test_each_channel_carries_the_taxonomies_it_blended(build):
    for item in build.classes:
        for channel in item.channels:
            assert channel.function.components
            assert sum(
                weight for _, weight in channel.function.components
            ) == pytest.approx(1.0)


def test_the_build_records_the_checksums_it_was_built_from(build):
    assert "vulnerability_structural" in build.sources
    assert "exposure_summary" in build.sources


def test_contents_is_routed_through_the_contents_functions(build):
    contents = [item for item in build.classes if item.coverage_type == 3]
    assert contents
    assert {item.loss_category for item in contents} == {LossCategory.CONTENTS}


def test_fatalities_can_never_be_a_coverage_type(
    enrichment, models, prior, bins, policy
):
    """It is a ratio of occupants and nothing may multiply it by a TIV."""
    from cass_converter import model_build

    original = model_build.COVERAGE_CATEGORIES
    model_build.COVERAGE_CATEGORIES = {**original, 9: LossCategory.FATALITIES}
    try:
        with pytest.raises(BuildError, match="ratio of occupants"):
            build_country(
                enrichment=enrichment,
                models=models,
                prior=prior,
                intensity_bins=bins["intensity"],
                damage_bins=bins["damage"],
                policy=policy,
                coverage_types=(9,),
            )
    finally:
        model_build.COVERAGE_CATEGORIES = original


def test_a_missing_loss_category_is_refused(enrichment, prior, bins, policy):
    with pytest.raises(BuildError, match="was not given them"):
        build_country(
            enrichment=enrichment,
            models={LossCategory.STRUCTURAL: model_for(LossCategory.STRUCTURAL)},
            prior=prior,
            intensity_bins=bins["intensity"],
            damage_bins=bins["damage"],
            policy=policy,
            coverage_types=(1, 3),
        )


def test_an_unapproved_policy_refuses_to_build(enrichment, models, prior, bins):
    with pytest.raises(Exception, match="has not been approved"):
        build_country(
            enrichment=enrichment,
            models=models,
            prior=prior,
            intensity_bins=bins["intensity"],
            damage_bins=bins["damage"],
            policy=ConversionPolicy(),
        )


def test_a_prior_for_another_country_is_refused(enrichment, models, bins, policy):
    from cass_converter.enrichment import EnrichmentError

    nepal = StockPrior(
        country_code="NP", shares={}, source_name="np.csv", checksum="3" * 64
    )
    with pytest.raises(EnrichmentError, match="prior is for NP"):
        build_country(
            enrichment=enrichment,
            models=models,
            prior=nepal,
            intensity_bins=bins["intensity"],
            damage_bins=bins["damage"],
            policy=policy,
        )


def test_a_class_reaching_an_undeclared_measure_is_refused(
    enrichment, models, prior, bins
):
    narrow = ConversionPolicy(
        event_identity=EventIdentity.RUPTURE_BINNED,
        imt_representation=IMTRepresentation.CORRELATED_CHANNELS,
        approval_reference="TEST-ONLY",
        imts=("PGA",),
        investigation_time=1.0,
    )
    with pytest.raises(BuildError, match="does not declare"):
        build_country(
            enrichment=enrichment,
            models=models,
            prior=prior,
            intensity_bins=bins["intensity"],
            damage_bins=bins["damage"],
            policy=narrow,
            coverage_types=(1,),
        )


# -- what the build produces ------------------------------------------------------

def test_the_vulnerability_table_covers_every_channel(build):
    rows = list(csv.DictReader(io.StringIO(vulnerability_csv(build).decode())))
    identifiers = {int(row["vulnerability_id"]) for row in rows}
    assert identifiers == {item.vulnerability_id for item in build.functions}


def test_the_mapping_has_a_row_per_channel(build):
    rows = list(csv.DictReader(io.StringIO(mapping_csv(build).decode())))
    assert len(rows) == len(build.functions)
    assert {row["RequiredIMT"] for row in rows} <= set(IMTS)


def test_the_mapping_leaves_unknown_construction_blank(build):
    """An empty code means "matches anything", which is what the class is."""
    rows = list(csv.DictReader(io.StringIO(mapping_csv(build).decode())))
    unstated = [row for row in rows if row["Label"].startswith("com construction not")]
    assert unstated
    assert all(row["ConstructionCodes"] == "" for row in unstated)


def test_the_dictionary_traces_an_identifier_back_to_its_buildings(build):
    entries = dictionary(build)["entries"]
    assert entries
    blended = next(item for item in entries if len(item["blended_from"]) > 1)
    assert sum(item["weight"] for item in blended["blended_from"]) == pytest.approx(1.0)
    assert blended["required_imt"] in IMTS


def test_the_multi_imt_report_counts_and_names_the_cases(build):
    report = multi_imt_report(build)
    assert report["classes_needing_multi_imt"] == len(build.multi_imt_classes)
    assert 0.0 <= report["share_needing_multi_imt"] <= 1.0
    assert report["combinations"]
    assert report["examples"]
    assert "Neither is chosen here" in report["note"]


def test_the_evidence_summary_says_how_much_each_class_assumed(build):
    summary = evidence_summary(build)
    assert summary["classes"] == len(build.classes)
    assert summary["evidence"]["design"]["prior"] > 0
    assert summary["mean_candidates_per_class"] >= 1.0


def test_the_build_report_carries_the_open_questions(enrichment, build):
    assert build.as_dict()["open_questions"] == list(enrichment.open_questions)
