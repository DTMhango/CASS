"""Draft enrichments for the pilot countries, and what they do not know.

These say which GEM building a Klapton Re risk is taken to be. They are drafts,
and the code says so, because the judgement they encode is local structural
engineering and CASS has none: when a seismic code was adopted, whether it was
enforced, and what the enforcement was worth in practice are questions for
someone who has looked at buildings in Jakarta.

Nepal had an enrichment here as well. It was test data, and it was removed once
an enrichment could be written for any country and posted with its build: a
country's design eras are local expertise, not a property of the platform.

What is not a draft is the machinery underneath. The candidate sets come from
the published GEM vulnerability model and the weights from its published
exposure summaries, both checksummed. What these add is the two things GEM
cannot supply: which OED code means which GEM class, and which construction
years imply which seismic design level. Both are stated here so a reviewer can
disagree with a specific line rather than with a result.

The design eras are the weaker half and are marked as such. They are drawn from
the published adoption dates of each country's seismic code, which is a poor
proxy for what was actually built: adoption is not enforcement, enforcement is
not compliance, and a facultative risk is more likely than the national stock
to have been engineered whatever the year. They will move once someone with
local knowledge reads them, and the version will move with them.
"""

from __future__ import annotations

import pathlib
from collections.abc import Sequence

from .enrichment import (
    DesignEra,
    Enrichment,
    StockPrior,
    Weighting,
    apply_taxonomy_mapping,
    read_stock_prior,
    read_taxonomy_mapping,
)
from .gem import LossCategory, VulnerabilityModel, read_country, stock_summary_path

#: Bumped with any change to a country's eras or overrides.
PILOT_VERSION = "0.1.0-draft"

_SHARED_QUESTIONS = (
    "GEM's exposure is national building stock and a facultative book is not. "
    "It is large, engineered, urban and underwriter-selected, and every weight "
    "here describes the wrong population. Weighting by replacement cost rather "
    "than building count moves the prior towards larger buildings, which is the "
    "only correction the published summaries support on their own; it does not "
    "make this a facultative prior. Survey, underwriting review and claims are "
    "what should replace it.",
    "Where GEM's published taxonomy mapping is supplied, each vulnerability "
    "function receives exactly the exposure value that maps to it. Without it "
    "the weights fall back to macro classes reconstructed from the taxonomy "
    "strings, which divides a class's value equally among its functions.",
    "OED construction 5100 covers both unreinforced and confined masonry, which "
    "behave very differently in a moderate shake. A risk stating it reaches both "
    "as a mixture, so the difference shows up as width rather than as a choice -- "
    "but a schedule that knows which one it is has no way to say so.",
    "Design level is inferred from the year built, where the year is stated at "
    "all. Most of the book does not state it, so most risks carry the country's "
    "stock distribution over design levels instead.",
    "Only shake is modelled. Liquefaction, tsunami, landslide and fire following "
    "carry no function, and in Indonesia at least one of those has driven a "
    "large historical loss.",
    "Contents and business interruption are routed through GEM's contents and "
    "non-structural functions, which describe building stock rather than the "
    "specific plant and stock a commercial policy insures.",
)


#: Indonesia. SNI 1726 is the seismic code, revised in 1983, 1989, 2002, 2012
#: and 2019, with the 2002 and 2012 revisions the substantial ones for demand
#: levels and detailing.
INDONESIA = Enrichment(
    name="id_gem_stock",
    version=PILOT_VERSION,
    country_code="ID",
    iso3="IDN",
    weighting=Weighting.VALUE,
    design_eras=(
        DesignEra(
            to_year=1983,
            design_levels=("CDN", "CDL"),
            reason=(
                "Before the first SNI seismic provisions. Engineered buildings "
                "exist but detailing for ductility is not expected."
            ),
        ),
        DesignEra(
            to_year=2002,
            design_levels=("CDL",),
            reason=(
                "SNI 1726:1983 and 1989 are in force. Seismic demand is "
                "considered; capacity design is not yet the norm."
            ),
        ),
        DesignEra(
            to_year=None,
            design_levels=("CDM", "CDH"),
            reason=(
                "SNI 1726:2002 onward, which introduced the demand levels and "
                "ductile detailing GEM's medium and high classes describe. Taken "
                "to apply to engineered commercial and industrial construction; "
                "it is the assumption most likely to overstate the stock."
            ),
        ),
    ),
    open_questions=(
        "Indonesian commercial stock spans a very wide range of seismic design "
        "vintage and enforcement, and the 2002 cut-off treats a step change as a "
        "date. Buildings either side of it in the same street may differ more "
        "than the eras do.",
        *_SHARED_QUESTIONS,
    ),
    notes=(
        "Draft. Candidate sets and weights come from GEM v2026.0.0; the OED "
        "mapping and the design eras are CASS assumptions and are not approved."
    ),
)


PILOT_ENRICHMENTS: dict[str, Enrichment] = {
    INDONESIA.country_code: INDONESIA,
}


#: The draft tilts behind the three assumption sets section 8 requires, used
#: where a registered assumption set states no rule of its own.
#:
#: Illustrative sensitivities, not calibrated priors. More robust halves the
#: weight of buildings designed to no code and doubles those designed to a high
#: one, with the two middle levels moved a quarter and a half; more vulnerable
#: does the opposite. That moves a typical concrete mixture's mean loss far
#: enough to see without removing any building type. They exist so the platform
#: can show how far a result moves when the design-level assumption does, and
#: they must be replaced by values a local structural engineer and the model
#: owner approve at the exposure-enrichment gate before a result under them is
#: used for a decision.
ASSUMPTION_TILTS: dict[str, dict[str, float]] = {
    "baseline": {},
    "more_robust": {"CDN": 0.5, "CDL": 0.75, "CDM": 1.5, "CDH": 2.0},
    "more_vulnerable": {"CDN": 2.0, "CDL": 1.5, "CDM": 0.75, "CDH": 0.5},
}


def enrichment(country_code: str) -> Enrichment:
    try:
        return PILOT_ENRICHMENTS[country_code.upper()]
    except KeyError:
        raise KeyError(
            f"No draft enrichment for {country_code!r}. Available: "
            + ", ".join(sorted(PILOT_ENRICHMENTS))
            + "."
        ) from None


#: Where the pilot country sits in the GEM repositories. A table rather than a
#: rule, because the regional folders are GEM's editorial choice and cannot be
#: derived from a country code. Any other country states its place when it is
#: built, and its vulnerability set records it.
GEM_LAYOUT: dict[str, tuple[str, str]] = {
    "ID": ("Southeast_Asia", "Indonesia"),
}

#: GEM's published exposure-taxonomy to vulnerability-function mapping, relative
#: to the release root. One file for the world, keyed by ISO alpha-3.
MAPPING_PATH = "global_exposure_model/World/summaries/Vulnerability_mapping_country.csv"


def load(
    root: str | pathlib.Path,
    country_code: str,
    *,
    categories: Sequence[LossCategory] | None = None,
    use_published_mapping: bool = True,
    chosen: Enrichment | None = None,
    region: str | None = None,
    folder: str | None = None,
) -> tuple[dict[LossCategory, VulnerabilityModel], StockPrior, Enrichment]:
    """Everything one country's build needs, from a GEM release directory.

    The published taxonomy mapping is applied by default, so each vulnerability
    function receives the exposure value GEM says maps to it. Passing
    ``use_published_mapping=False`` falls back to the macro-class reconstruction
    -- worth keeping, because comparing the two is how the difference the
    mapping makes gets measured rather than asserted.

    ``chosen``, ``region`` and ``folder`` are what make this usable beyond the
    pilots: the enrichment written for a country, and where that country sits in
    the release. Without them the compiled-in pilot specification applies, so the
    existing callers are unchanged.
    """
    base = pathlib.Path(root)
    chosen = chosen or enrichment(country_code)
    if region is None or folder is None:
        region, folder = GEM_LAYOUT[chosen.country_code]
    name = folder

    models = read_country(
        base / "global_vulnerability_model" / region / name,
        country_code=chosen.country_code,
        categories=categories,
    )
    prior = read_stock_prior(
        stock_summary_path(base, region=region, country=name),
        country_code=chosen.country_code,
        weighting=chosen.weighting,
        iso3=chosen.iso3,
    )
    if use_published_mapping:
        mapping = read_taxonomy_mapping(base / MAPPING_PATH, iso3=chosen.iso3)
        prior = apply_taxonomy_mapping(prior, mapping)

    return models, prior, chosen
