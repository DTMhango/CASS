"""An enrichment somebody wrote, and what a GEM release actually contains.

The pilot countries' design eras are Python constants, which was right while
there were two of them. A third country makes the shape of the problem plain:
when a country adopted a code, and whether it was enforced, is local expertise
rather than a property of the platform.

So an enrichment can be written down instead -- and read back with its table
checked, because the eras are applied first-match. An unordered table applies
the wrong design levels to a year, and a table with no open-ended era leaves
recent construction reaching none at all. Both produce a mixture that looks
perfectly reasonable.
"""

from __future__ import annotations

import pytest

from cass_converter.enrichment import EnrichmentError, Weighting, enrichment_from
from cass_converter.gem import GemError, LossCategory, catalogue


def document(category: str) -> bytes:
    """One published vulnerability file, in the shape GEM writes them."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nrml xmlns="http://openquake.org/xmlns/nrml/0.5">
<vulnerabilityModel id="vulnerability_model" assetCategory="buildings" \
lossCategory="{category}">
<description>Test Vulnerability Model</description>
<vulnerabilityFunction id="CR/LFINF/CDL+ERM/H:2/COM" dist="BT">
<imls imt="PGA"> 0.05 0.1 0.2 0.4 </imls>
<meanLRs>0.0001 0.01 0.2 0.8</meanLRs>
<covLRs>3.0 1.5 0.8 0.3</covLRs>
</vulnerabilityFunction>
</vulnerabilityModel>
</nrml>
""".encode()

WRITTEN = {
    "name": "Atlantis, draft",
    "version": "0.1.0",
    "country_code": "at",
    "iso3": "atl",
    "weighting": "building_count",
    "design_eras": [
        {
            "to_year": 1975,
            "design_levels": ["CDN"],
            "reason": "Before the first code, and nothing was enforced after it either.",
        },
        {
            "to_year": None,
            "design_levels": ["CDL", "CDM"],
            "reason": "Code adopted in 1976; enforcement uneven, so both stay candidates.",
        },
    ],
    "open_questions": ["Nobody has checked this against local practice."],
    "notes": "Draft.",
}


def test_a_written_enrichment_reads_back_as_the_one_the_build_uses():
    written = enrichment_from(WRITTEN)

    assert written.reference == "Atlantis, draft/0.1.0"
    assert written.country_code == "AT"
    assert written.iso3 == "ATL"
    assert written.weighting is Weighting.BUILDINGS
    assert written.design_levels(1970) == ("CDN",)
    assert written.design_levels(2024) == ("CDL", "CDM")


def test_eras_out_of_order_are_refused_rather_than_applied_first_match():
    out_of_order = {
        **WRITTEN,
        "design_eras": [
            {**WRITTEN["design_eras"][0], "to_year": 2000},
            {**WRITTEN["design_eras"][0], "to_year": 1975},
            WRITTEN["design_eras"][1],
        ],
    }

    with pytest.raises(EnrichmentError, match="out of order"):
        enrichment_from(out_of_order)


def test_a_table_that_ends_before_today_is_refused():
    """Anything built after the last era would reach no design level at all."""
    bounded = {
        **WRITTEN,
        "design_eras": [{**WRITTEN["design_eras"][0]}, {**WRITTEN["design_eras"][1], "to_year": 2000}],
    }

    with pytest.raises(EnrichmentError, match="open-ended"):
        enrichment_from(bounded)


def test_two_open_ended_eras_are_refused():
    doubled = {
        **WRITTEN,
        "design_eras": [
            {**WRITTEN["design_eras"][1]},
            {**WRITTEN["design_eras"][1]},
        ],
    }

    with pytest.raises(EnrichmentError, match="open-ended"):
        enrichment_from(doubled)


def test_an_era_with_no_reason_is_refused():
    """The eras are the assumption; one without provenance cannot be argued with."""
    unexplained = {
        **WRITTEN,
        "design_eras": [{"to_year": None, "design_levels": ["CDL"], "reason": ""}],
    }

    with pytest.raises(EnrichmentError, match="no reason"):
        enrichment_from(unexplained)


def test_a_design_level_gem_does_not_use_is_refused():
    invented = {
        **WRITTEN,
        "design_eras": [{"to_year": None, "design_levels": ["CDX"], "reason": "Why not."}],
    }

    with pytest.raises(EnrichmentError, match="CDX"):
        enrichment_from(invented)


def test_an_enrichment_with_no_eras_is_refused():
    with pytest.raises(EnrichmentError, match="no design eras"):
        enrichment_from({**WRITTEN, "design_eras": []})


def test_an_unknown_weighting_basis_is_refused():
    with pytest.raises(EnrichmentError, match="weighting basis"):
        enrichment_from({**WRITTEN, "weighting": "by vibes"})


def test_the_country_name_and_version_are_required():
    with pytest.raises(EnrichmentError, match="must state its name"):
        enrichment_from({**WRITTEN, "name": ""})
    with pytest.raises(EnrichmentError, match="must state its version"):
        enrichment_from({**WRITTEN, "version": ""})


# -- what the release holds --------------------------------------------------------

def release(tmp_path, *, region: str = "Southeast_Asia", country: str = "Atlantis"):
    directory = tmp_path / "global_vulnerability_model" / region / country
    directory.mkdir(parents=True)
    for category in LossCategory:
        (directory / f"vulnerability_{category}.xml").write_bytes(
            document(category.declared)
        )
    return tmp_path


def test_the_catalogue_lists_what_the_release_publishes(tmp_path):
    """Read from the release, because a list inside CASS goes stale."""
    release(tmp_path)
    release(tmp_path, region="South_Asia", country="Ruritania")

    found = catalogue(tmp_path)

    assert [(item["region"], item["country"]) for item in found] == [
        ("South_Asia", "Ruritania"),
        ("Southeast_Asia", "Atlantis"),
    ]
    assert set(found[0]["loss_categories"]) == {str(item) for item in LossCategory}


def test_a_folder_with_no_vulnerability_files_is_not_a_country(tmp_path):
    release(tmp_path)
    (tmp_path / "global_vulnerability_model" / "Southeast_Asia" / "README").mkdir()

    assert [item["country"] for item in catalogue(tmp_path)] == ["Atlantis"]


def test_a_release_that_is_not_one_is_refused(tmp_path):
    with pytest.raises(GemError, match="not a directory"):
        catalogue(tmp_path / "nothing-here")
