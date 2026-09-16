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

from cass_converter import iso3166
from cass_converter.enrichment import EnrichmentError, Weighting, enrichment_from
from cass_converter.gem import (
    CountryIdentity,
    GemError,
    LossCategory,
    catalogue,
    country_identity,
)


def document(category: str, taxonomy: str = "CR/LFINF/CDL+ERM/H:2/COM") -> bytes:
    """One published vulnerability file, in the shape GEM writes them."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nrml xmlns="http://openquake.org/xmlns/nrml/0.5">
<vulnerabilityModel id="vulnerability_model" assetCategory="buildings" \
lossCategory="{category}">
<description>Test Vulnerability Model</description>
<vulnerabilityFunction id="{taxonomy}" dist="BT">
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


def test_an_enrichment_with_no_eras_and_no_reason_is_refused():
    with pytest.raises(EnrichmentError, match="it has to be decided"):
        enrichment_from({**WRITTEN, "design_eras": []})


def test_an_enrichment_may_state_that_it_has_no_eras_and_why():
    """An era table does nothing for a risk whose year is unknown, and a book
    that states no years is the normal case. Writing a table nobody researched
    and nothing will read is worse than saying the stock distribution stands."""
    without = enrichment_from(
        {
            **WRITTEN,
            "design_eras": [],
            "no_design_eras_reason": (
                "No location in this book states a year built, and nobody has "
                "reviewed this country's code adoption."
            ),
        }
    )

    assert without.design_eras == ()
    assert without.design_levels(2024) == ()
    assert "nobody has reviewed" in without.no_design_eras_reason
    # It survives the provenance dictionary, so a run records why.
    assert enrichment_from(without.as_dict()).no_design_eras_reason == (
        without.no_design_eras_reason
    )


def test_stating_eras_and_a_reason_for_having_none_is_refused():
    with pytest.raises(EnrichmentError, match="One of them is not true"):
        enrichment_from({**WRITTEN, "no_design_eras_reason": "The stock stands."})


def test_an_unknown_weighting_basis_is_refused():
    with pytest.raises(EnrichmentError, match="weighting basis"):
        enrichment_from({**WRITTEN, "weighting": "by vibes"})


def test_the_country_name_and_version_are_required():
    with pytest.raises(EnrichmentError, match="must state its name"):
        enrichment_from({**WRITTEN, "name": ""})
    with pytest.raises(EnrichmentError, match="must state its version"):
        enrichment_from({**WRITTEN, "version": ""})


# -- what the release holds --------------------------------------------------------

def release(
    tmp_path,
    *,
    region: str = "Southeast_Asia",
    country: str = "Atlantis",
    taxonomy: str = "CR/LFINF/CDL+ERM/H:2/COM",
):
    directory = tmp_path / "global_vulnerability_model" / region / country
    directory.mkdir(parents=True)
    for category in LossCategory:
        (directory / f"vulnerability_{category}.xml").write_bytes(
            document(category.declared, taxonomy)
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


def stock_summary(root, *, region: str, country: str, rows: str) -> None:
    folder = root / "global_exposure_model" / region / country / "summaries"
    folder.mkdir(parents=True)
    (folder / "Exposure_Summary_Taxonomy.csv").write_text(
        "ID_0,NAME_0,OCCUPANCY,MACRO_TAXONOMY,TAXONOMY,SETTLEMENT,BUILDINGS,BLDG_REPL_COST_USD\n"
        + rows,
        encoding="utf-8",
    )


def test_a_country_s_codes_are_read_from_its_stock_summary(tmp_path):
    """GEM states the alpha-3; the alpha-2 is ISO 3166-1's, not somebody's typing."""
    release(tmp_path, region="Europe", country="Iceland")
    stock_summary(
        tmp_path,
        region="Europe",
        country="Iceland",
        rows="ISL,Iceland,COM,CR+,CR/LFINF/CDH+ERH/H:2/COM,TOTAL,1,100\n",
    )

    assert country_identity(tmp_path, region="Europe", country="Iceland") == CountryIdentity(
        iso3="ISL", country_code="IS"
    )
    [found] = catalogue(tmp_path, identify=True)
    assert (found["iso3"], found["country_code"], found["problem"]) == ("ISL", "IS", "")


def test_the_two_repositories_meet_where_gem_names_a_country_differently(tmp_path):
    """Turkey's functions are published under Turkey and its stock under
    Turkiye. Pairing them only by folder name left the country unbuildable."""
    release(tmp_path, region="Europe", country="Turkey")
    stock_summary(
        tmp_path,
        region="Europe",
        country="Turkiye",
        rows="TUR,Türkiye,COM,CR-,CR/LFINF/CDL+ERM/H:2/COM,TOTAL,1,100\n",
    )

    assert country_identity(tmp_path, region="Europe", country="Turkey") == CountryIdentity(
        iso3="TUR", country_code="TR"
    )
    [found] = catalogue(tmp_path, identify=True)
    assert (found["country"], found["iso3"], found["problem"]) == ("Turkey", "TUR", "")


def test_a_country_with_no_stock_summary_at_all_is_listed_with_the_reason(tmp_path):
    release(tmp_path, region="Europe", country="Ruritania")

    [found] = catalogue(tmp_path, identify=True)

    assert found["country"] == "Ruritania"
    assert found["iso3"] == ""
    assert "no stock summary for Ruritania" in found["problem"]


def test_a_country_published_in_another_alphabet_says_so_before_it_is_chosen(tmp_path):
    """The United States and Canada are published in HAZUS classes, which CASS
    cannot map an OED schedule onto. Saying so in the catalogue beats failing
    part way through a build with an unreadable taxonomy string."""
    release(tmp_path, region="North_America", country="Canada", taxonomy="C1H/HC/RES3")
    stock_summary(
        tmp_path,
        region="North_America",
        country="Canada",
        rows="CAN,Canada,COM,CR-,C1H/HC/COM4,TOTAL,1,100\n",
    )

    [found] = catalogue(tmp_path, identify=True)

    assert found["iso3"] == "CAN"
    assert "HAZUS" in found["problem"]


def test_a_code_outside_iso_3166_is_refused(tmp_path):
    release(tmp_path)
    stock_summary(
        tmp_path,
        region="Southeast_Asia",
        country="Atlantis",
        rows="ATL,Atlantis,COM,CR-,CR/LFINF/CDL+ERM/H:2/COM,TOTAL,1,100\n",
    )

    with pytest.raises(GemError, match="not an ISO 3166-1 alpha-3 code"):
        country_identity(tmp_path, region="Southeast_Asia", country="Atlantis")


def test_iso_3166_maps_each_alpha_3_to_one_alpha_2():
    assert iso3166.alpha_2("idn") == "ID"
    assert iso3166.alpha_2("XKX") == "XK"
    assert iso3166.alpha_2("ATL") == ""
    codes = list(iso3166.ALPHA_2.values())
    assert len(codes) == len(set(codes))
    assert all(len(code) == 2 and code.isupper() for code in codes)
