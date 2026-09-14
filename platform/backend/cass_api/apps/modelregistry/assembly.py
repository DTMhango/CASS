"""Pairing a grid and a vulnerability set into a model version.

A grid says where a loss can be computed and a vulnerability set says how much
damage the shaking does. Neither is a model: the model version is the pair, and
it is what a run names. The platform could build both halves for any country
and then only assemble them for the two it shipped prototypes for, which left a
country one step short of being runnable.

What assembly is, and what it is not:

* It **pairs**, and refuses to pair halves from different countries. A model
  version carrying one country's buildings and another's ground motion would
  calculate happily and mean nothing.
* It **states the scope**. Which sub-perils are modelled, what is excluded and
  why, and that no cell carries a site parameter -- section 9 requires the
  machine-readable statement, and an absent one reads as complete coverage.
* It **carries the limitations forward** from both halves. The grid's open
  questions and the vulnerability set's live on the model version, because that
  is what a result's caveats are read from.
* It **approves nothing**. Everything assembled is a draft and a research
  prototype, and ``ModelVersion.publication_blockers`` keeps saying what is
  outstanding.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from .models import (
    AreaPerilGrid,
    ModelVersion,
    Peril,
    PublicationState,
    VulnerabilitySet,
)


class AssemblyError(Exception):
    """Raised when a grid and a vulnerability set cannot become a model version."""


def peril_scope() -> dict[str, Any]:
    """Section 9's machine-readable scope statement.

    Stated for every model version CASS assembles, because what a model does not
    model is not visible in its output. A result that omitted tsunami without
    saying so is a number somebody would use as though it included one.
    """
    return {
        "QEQ": {
            "treatment": "included",
            "rationale": "Shake is the only sub-peril CASS routes.",
        },
        "QFF": {"treatment": "excluded", "rationale": "No fire-following module."},
        "QTS": {"treatment": "excluded", "rationale": "No tsunami module."},
        "QSL": {
            "treatment": "excluded",
            "rationale": (
                "No liquefaction module, and no cell carries the site parameters "
                "one would need."
            ),
        },
        "QLS": {"treatment": "excluded", "rationale": "No landslide module."},
        "site_response": {
            "treatment": "excluded",
            "rationale": (
                "No Vs30, soil class or basin parameter is attached to any cell. "
                "Loss on soft soil will be understated, and by an unknown amount."
            ),
        },
    }


def limitations(grid: AreaPerilGrid, vulnerability_set: VulnerabilitySet) -> str:
    """What both halves said about themselves, carried onto the pair."""
    return "\n".join(
        [
            "Assembled from a grid and a vulnerability set that are both drafts. "
            "Not a country model and not usable for a decision.",
            "",
            f"Grid ({grid.reference}):",
            grid.notes or "- The grid recorded no notes.",
            "",
            f"Vulnerability ({vulnerability_set.country_code.lower()}-vuln-"
            f"{vulnerability_set.version}):",
            vulnerability_set.licence_note or "- No licence note was recorded.",
        ]
    )


@transaction.atomic
def assemble(
    *,
    grid: AreaPerilGrid,
    vulnerability_set: VulnerabilitySet,
    version: str,
    label: str = "",
    peril: str = Peril.EARTHQUAKE,
    actor=None,
) -> ModelVersion:
    """Pair one country's grid with its vulnerability set.

    Idempotent by country, peril and version, as every other registration here
    is: assembling twice replaces the record rather than producing two versions
    a result could name interchangeably.
    """
    country = grid.country_code.upper()
    if vulnerability_set.country_code.upper() != country:
        raise AssemblyError(
            f"The grid is for {country} and the vulnerability set for "
            f"{vulnerability_set.country_code.upper()}. A model version pairing them "
            "would apply one country's buildings to another's ground motion."
        )
    if not version.strip():
        raise AssemblyError("A model version needs a version of its own.")
    if not vulnerability_set.imts_used:
        raise AssemblyError(
            f"{vulnerability_set} carries no intensity measures, so nothing would "
            "say which hazard a run should read."
        )

    model, _ = ModelVersion.objects.update_or_create(
        country_code=country,
        peril=peril,
        version=version.strip(),
        defaults={
            "label": label.strip() or f"{country} {peril}, assembled on the platform",
            "grid": grid,
            "vulnerability_set": vulnerability_set,
            "imts": sorted(vulnerability_set.imts_used),
            "oed_schema_version": "4.0.0",
            "peril_scope": peril_scope(),
            "known_limitations": limitations(grid, vulnerability_set),
            "unsupported_taxonomy_report": {
                "note": (
                    "OED unknown occupancy (1000) reaches no function by design, so "
                    "exposure whose occupancy is not known is reported as fail_v "
                    "rather than routed to a generic curve."
                ),
                "unmapped_occupancy_codes": ["1000"],
            },
            "is_research_prototype": True,
            "publication_state": PublicationState.DRAFT,
            "updated_by": actor,
        },
        create_defaults={
            "label": label.strip() or f"{country} {peril}, assembled on the platform",
            "grid": grid,
            "vulnerability_set": vulnerability_set,
            "imts": sorted(vulnerability_set.imts_used),
            "oed_schema_version": "4.0.0",
            "peril_scope": peril_scope(),
            "known_limitations": limitations(grid, vulnerability_set),
            "unsupported_taxonomy_report": {
                "note": (
                    "OED unknown occupancy (1000) reaches no function by design, so "
                    "exposure whose occupancy is not known is reported as fail_v "
                    "rather than routed to a generic curve."
                ),
                "unmapped_occupancy_codes": ["1000"],
            },
            "is_research_prototype": True,
            "publication_state": PublicationState.DRAFT,
            "created_by": actor,
            "updated_by": actor,
        },
    )
    return model
