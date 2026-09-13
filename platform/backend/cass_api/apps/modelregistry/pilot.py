"""Registering the prototype grid, and assembling a model version around it.

``cass_keys`` can generate a grid from a written specification. Until it is a
registry record with its cell file in the artifact store, no run can reach it:
section 5 keeps the arrays out of Django and ``assets`` is the only door.

So this is the bridge, and its job is as much bookkeeping as plumbing. A
specification's open questions become the registry record's notes, its draft
version becomes ``DRAFT`` publication state, and the model version is marked a
research prototype. A prototype that arrived in the registry looking like an
approved asset would be worse than no prototype at all -- the registry is where
an analyst goes to find out what a result rests on.

**No vulnerability set is created here.** There used to be one: a hand-written
routing table naming four functions, which let the platform be exercised end to
end before any damage relationship existed. It has been retired, because one
now does. A vulnerability set is built from the published GEM model by
``modelregistry.gem``, and a model version cannot be registered without one --
so the platform can no longer be pointed at a placeholder by accident.

Nothing here approves anything. Everything registered is a draft, and
``ModelVersion.publication_blockers`` will say so for as long as that is true.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from cass_keys import grids, pilot_grids

from . import grid_build
from .models import AreaPerilGrid, ModelVersion, Peril, PublicationState, VulnerabilitySet

#: Countries with a prototype grid specification.
PILOT_COUNTRIES = tuple(sorted(pilot_grids.PILOT_GRIDS))

#: Suffix marking a model version built from prototype specifications. Section 6
#: limits the first converter to the SA family, and these route only to it.
MODEL_VERSION_SUFFIX = "0.1.0-sa-draft"


class PilotRegistrationError(Exception):
    """Raised when a prototype specification cannot be registered."""


@transaction.atomic
def register_grid(country_code: str, *, actor=None) -> AreaPerilGrid:
    """Register one country's prototype area-peril grid.

    Idempotent by version. Re-registering replaces the stored cell file and
    leaves the registry record in place, so running this twice does not produce
    two grids whose identifiers mean different things.
    """
    code = country_code.upper()
    try:
        specification = pilot_grids.specification(code)
    except KeyError as exc:
        raise PilotRegistrationError(str(exc)) from None
    # The same registration a specification written on the platform goes
    # through, so a prototype arrives as the same kind of record.
    return grid_build.register(specification, grids.build(specification), actor=actor)


@transaction.atomic
def register_model_version(
    country_code: str,
    *,
    vulnerability_set: VulnerabilitySet,
    vulnerability_limitations: str = "",
    actor=None,
) -> ModelVersion:
    """Assemble a model version from the prototype grid and a vulnerability set.

    The vulnerability set is a required argument rather than something this
    builds, and that is the point of the signature. A model version is the
    published calculation capability; there is no honest default for what its
    damage relationships are, so the caller has to have obtained one.
    """
    code = country_code.upper()
    if vulnerability_set.country_code.upper() != code:
        raise PilotRegistrationError(
            f"The vulnerability set is for {vulnerability_set.country_code} and the "
            f"grid for {code}. A model version pairing them would apply one "
            "country's buildings to another's ground motion."
        )
    try:
        grid_spec = pilot_grids.specification(code)
    except KeyError as exc:
        raise PilotRegistrationError(str(exc)) from None

    grid = register_grid(code, actor=actor)
    model, _ = ModelVersion.objects.update_or_create(
        country_code=code,
        peril=Peril.EARTHQUAKE,
        version=MODEL_VERSION_SUFFIX,
        defaults={
            "label": f"{grid_spec.label.split(' earthquake')[0]} earthquake, SA-only prototype",
            "grid": grid,
            "vulnerability_set": vulnerability_set,
            "imts": sorted(vulnerability_set.imts_used),
            "oed_schema_version": "4.0.0",
            "peril_scope": _peril_scope(),
            "known_limitations": _limitations(grid_spec, vulnerability_limitations),
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


def _limitations(grid_specification, vulnerability_limitations: str) -> str:
    return "\n".join(
        [
            "Built on a prototype area-peril grid. Not a country model and not "
            "usable for a decision.",
            "",
            "Grid:",
            *(f"- {item}" for item in grid_specification.open_questions),
            "",
            "Vulnerability:",
            vulnerability_limitations
            or "- No limitations were recorded with the vulnerability set.",
        ]
    )


