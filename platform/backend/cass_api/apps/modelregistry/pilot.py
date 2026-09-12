"""Registering the prototype grid and vulnerability specifications.

``cass_keys`` can generate a grid and a routing table from a written
specification. Until they are registry records with their cell and mapping
files in the artifact store, no run can reach them: section 5 keeps the arrays
out of Django and ``assets`` is the only door.

So this is the bridge, and its job is as much bookkeeping as plumbing. A
specification's open questions become the registry record's notes, its draft
version becomes ``DRAFT`` publication state, and the model version is marked a
research prototype with the licence explicitly not cleared. A prototype that
arrived in the registry looking like an approved asset would be worse than no
prototype at all -- the registry is where an analyst goes to find out what a
result rests on.

Nothing here approves anything. Everything registered is a draft, and
``ModelVersion.publication_blockers`` will say so for as long as that is true.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from cass_keys import grids, pilot_grids, pilot_vulnerability
from cass_keys import vulnerability as vulnerability_specs

from .assets import attach_grid_cells, attach_vulnerability_mapping
from .models import AreaPerilGrid, ModelVersion, Peril, PublicationState, VulnerabilitySet

#: Countries with a prototype specification of both kinds.
PILOT_COUNTRIES = tuple(
    sorted(set(pilot_grids.PILOT_GRIDS) & set(pilot_vulnerability.PILOT_VULNERABILITY))
)

#: Suffix marking a model version built from prototype specifications. Section 6
#: limits the first converter to the SA family, and these route only to it.
MODEL_VERSION_SUFFIX = "0.1.0-sa-draft"


class PilotRegistrationError(Exception):
    """Raised when a prototype specification cannot be registered."""


@transaction.atomic
def register(country_code: str, *, actor=None) -> ModelVersion:
    """Register one country's prototype grid, routing table and model version.

    Idempotent by version. Re-registering replaces the stored files and leaves
    the registry records in place, so running this twice does not produce two
    grids whose identifiers mean different things.
    """
    code = country_code.upper()
    try:
        grid_spec = pilot_grids.specification(code)
        vulnerability_spec = pilot_vulnerability.specification(code)
    except KeyError as exc:
        raise PilotRegistrationError(str(exc)) from None

    cells = grids.build(grid_spec)
    grid = _register_grid(grid_spec, cells, actor=actor)

    mapping = vulnerability_specs.build(vulnerability_spec)
    vulnerability_set = _register_vulnerability(vulnerability_spec, mapping, actor=actor)

    model, _ = ModelVersion.objects.update_or_create(
        country_code=code,
        peril=Peril.EARTHQUAKE,
        version=MODEL_VERSION_SUFFIX,
        defaults={
            "label": f"{grid_spec.label.split(' earthquake')[0]} earthquake, SA-only prototype",
            "grid": grid,
            "vulnerability_set": vulnerability_set,
            "imts": sorted(vulnerability_spec.supported_imts),
            "oed_schema_version": "4.0.0",
            "peril_scope": _peril_scope(),
            "known_limitations": _limitations(grid_spec, vulnerability_spec),
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
    )
    return model


def register_all(*, actor=None) -> list[ModelVersion]:
    """Register every country that has both prototype specifications."""
    return [register(code, actor=actor) for code in PILOT_COUNTRIES]


def _register_grid(specification, cells, *, actor) -> AreaPerilGrid:
    finest = min(
        (item.resolution for item in specification.refinements),
        default=specification.base_resolution,
    )
    grid, _ = AreaPerilGrid.objects.update_or_create(
        country_code=specification.country_code,
        version=specification.version,
        defaults={
            "label": specification.label,
            "base_resolution_deg": specification.base_resolution,
            "refined_resolution_deg": finest,
            "refinement_rule": _refinement_rule(specification),
            "excludes_offshore": True,
            "site_condition_source": "",
            "site_condition_fallback": (
                "None. No cell carries a site parameter, so no fallback applies and "
                "site response is absent rather than defaulted."
            ),
            "border_policy": (
                "Reported, never snapped. A location outside every tile is returned "
                "as fail_ap with its coordinates."
            ),
            "mapping_tolerance_km": specification.mapping_tolerance_km,
            "publication_state": PublicationState.DRAFT,
            "notes": _notes(specification.notes, specification.open_questions),
            "updated_by": actor,
        },
    )
    attach_grid_cells(
        grid,
        grids.to_csv(cells),
        filename=f"{grid.reference}-cells.csv",
        actor=actor,
    )
    return grid


def _register_vulnerability(specification, mapping, *, actor) -> VulnerabilitySet:
    vulnerability_set, _ = VulnerabilitySet.objects.update_or_create(
        country_code=specification.country_code,
        version=specification.version,
        defaults={
            "source": specification.source,
            "taxonomy_generation": "OED 4.0.0 occupancy and construction codes",
            "licence": "",
            # Not a formality. Section 10 puts a data-rights gate before use and
            # the GEM public models are CC BY-NC-SA with commercial use
            # unconfirmed, so nothing here may be presumed cleared.
            "licence_cleared": False,
            "licence_note": (
                "No licensed vulnerability source is attached. These identifiers "
                "route a taxonomy to a function; no damage relationship stands "
                "behind them yet."
            ),
            "function_count": len(mapping.entries),
            "imts_used": sorted({entry.required_imt for entry in mapping.entries}),
            "coverage_components": sorted(
                {str(entry.coverage_type) for entry in mapping.entries}
            ),
            "publication_state": PublicationState.DRAFT,
            "updated_by": actor,
        },
    )
    attach_vulnerability_mapping(
        vulnerability_set,
        vulnerability_specs.to_csv(mapping),
        filename=f"{specification.country_code.lower()}-vuln-{specification.version}.csv",
        actor=actor,
    )
    return vulnerability_set


def _refinement_rule(specification) -> str:
    if not specification.refinements:
        return "No refinement; the whole domain sits at the base resolution."
    finest = min(item.resolution for item in specification.refinements)
    named = ", ".join(item.name for item in specification.refinements)
    return (
        f"Named exposure centres refined to {finest} degrees: {named}. This follows "
        "current exposure rather than hazard gradient, which is a starting point and "
        "not a durable rule."
    )


def _peril_scope() -> dict[str, Any]:
    """Section 9's machine-readable scope statement for the prototype."""
    return {
        "QEQ": {
            "treatment": "included",
            "rationale": "Shake is the only sub-peril this prototype routes.",
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


def _limitations(grid_specification, vulnerability_specification) -> str:
    return "\n".join(
        [
            "Built from prototype specifications for the phase 3 engine slice. Not a "
            "country model and not usable for a decision.",
            "",
            "Grid:",
            *(f"- {item}" for item in grid_specification.open_questions),
            "",
            "Vulnerability:",
            *(f"- {item}" for item in vulnerability_specification.open_questions),
        ]
    )


def _notes(notes: str, open_questions: tuple[str, ...]) -> str:
    return "\n".join([notes, "", "Open questions:", *(f"- {item}" for item in open_questions)])
