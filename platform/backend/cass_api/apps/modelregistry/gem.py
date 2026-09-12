"""Registering a vulnerability set built from the published GEM model.

This is the join the platform was missing. ``cass_converter`` could read GEM's
functions, map a Klapton Re risk onto a weighted mixture of GEM taxonomies,
blend that mixture and discretise it into Oasis tables -- and none of it
reached a run, because the registry held a hand-written routing table instead.
That table is gone. This puts the real one in its place.

Four artifacts come out of a build and all four are stored, because they answer
different questions and a set missing any of them is not reproducible:

* the **mapping**, which function a risk reaches -- what the keys service reads;
* the **functions**, the damage relationships themselves;
* the **damage-bin dictionary** they were discretised against, without which
  the functions are a table of numbers with no scale;
* the **provenance dictionary**, which GEM taxonomies were blended into each
  identifier and at what weight, so a loss can be traced to the buildings it
  was computed from.

Three things this deliberately does not decide.

**The licence.** Section 10 puts a data-rights gate before use and the GEM
public models are CC BY-NC-SA with commercial use needing confirmation. Whoever
runs the registration states whether that confirmation exists and names it; the
default is that it does not. An assertion recorded against a named person is
worth something. A default of ``True`` would be worth nothing.

**The multi-IMT representation.** A build under an undecided one is the normal
case and produces the multi-channel classes anyway -- that is how they get
counted. The refusal happens where it belongs, at the point a risk reaches such
a class and the keys service has to answer for it.

**Whether any of this is good enough.** Everything registered is ``DRAFT`` and
a research prototype. The design eras are CASS assumptions awaiting local
structural-engineering review, the stock prior describes national building
stock rather than a facultative book, and no hazard exists to run it against.
``ModelVersion.publication_blockers`` says so and will keep saying so.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Any

from django.db import transaction

from cass_converter import pilot_bins, pilot_enrichment
from cass_converter.model_build import (
    CountryBuild,
    build_country,
    dictionary,
    evidence_summary,
    mapping_csv,
    multi_imt_report,
    vulnerability_csv,
)
from cass_converter.policy import ConversionPolicy, IMTRepresentation
from cass_converter.vulnerability import damage_bins_to_csv

from . import pilot
from .assets import (
    attach_damage_bins,
    attach_vulnerability_dictionary,
    attach_vulnerability_functions,
    attach_vulnerability_mapping,
)
from .models import ModelVersion, PublicationState, VulnerabilitySet

#: The release every pilot build reads. Exposure and vulnerability generations
#: must match, and mixing a 2023-era exposure summary with 2026 functions --
#: or their taxonomies -- is the error this constant exists to make visible.
GEM_RELEASE = "v2026.0.0"

#: Provenance recorded on the registry record.
GEM_SOURCE = f"GEM Global Vulnerability Model and Global Exposure Model {GEM_RELEASE}"

#: The licence the published models carry. Stored whether or not commercial use
#: has been confirmed, because a reader needs to know which licence the
#: confirmation would have to be against.
GEM_LICENCE = "CC BY-NC-SA 4.0"

#: GEM's taxonomy generation. Distinct from the OED codes a schedule states:
#: the whole enrichment step exists to get from one to the other.
GEM_TAXONOMY = "GEM Building Taxonomy v4.0"

#: Version of a vulnerability set built by this module. Moves with the pilot
#: enrichment, since changing an era or an OED mapping changes every function.
VULNERABILITY_VERSION = f"{pilot_enrichment.PILOT_VERSION}-gem"


class GemRegistrationError(Exception):
    """Raised when a GEM vulnerability set cannot be built or registered."""


@dataclasses.dataclass(frozen=True, slots=True)
class LicenceStatement:
    """What the operator asserts about the right to use this data.

    A dataclass rather than a boolean so the assertion cannot be made without
    the reference that supports it. ``cleared`` without ``reference`` is
    refused: an unevidenced clearance in a governance record is worse than an
    honest absence, because it looks like it was checked.
    """

    cleared: bool = False
    reference: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.cleared and not self.reference.strip():
            raise GemRegistrationError(
                "A licence clearance needs a reference -- the agreement, approval or "
                "correspondence that grants it. Section 10 makes this a gate with a "
                "named approver, and a cleared flag with nothing behind it would "
                "pass that gate without anyone having done anything."
            )

    def as_note(self) -> str:
        if not self.cleared:
            return (
                f"Commercial use of the {GEM_LICENCE} data has not been confirmed. "
                "This set may be used for research and platform development and not "
                "for a pricing or reserving decision."
            ) + (f" {self.note}" if self.note else "")
        return (
            f"Commercial use confirmed under {self.reference}."
            + (f" {self.note}" if self.note else "")
        )


def build(
    country_code: str,
    *,
    root: str | pathlib.Path,
    policy: ConversionPolicy | None = None,
    coverage_types: tuple[int, ...] = (1, 2, 3, 4),
) -> CountryBuild:
    """Build one country's vulnerability set from a GEM release directory.

    ``root`` is the release root -- the directory holding
    ``global_vulnerability_model`` and ``global_exposure_model``. It is a
    parameter rather than a setting because the GEM clones are large, licensed
    and deliberately outside version control; where they sit is a property of
    the machine doing the build, not of the platform.
    """
    base = pathlib.Path(root)
    if not base.is_dir():
        raise GemRegistrationError(
            f"{base} is not a directory. It should be the GEM {GEM_RELEASE} release "
            "root, holding global_vulnerability_model and global_exposure_model."
        )

    chosen = policy or default_policy()
    try:
        models, prior, enrichment = pilot_enrichment.load(base, country_code)
    except (KeyError, OSError) as exc:
        raise GemRegistrationError(
            f"The GEM model for {country_code} could not be read from {base}: {exc}"
        ) from exc

    return build_country(
        enrichment=enrichment,
        models=models,
        prior=prior,
        intensity_bins=pilot_bins.intensity_bins(chosen.imts),
        damage_bins=pilot_bins.damage_bins(),
        policy=chosen,
        coverage_types=coverage_types,
    )


def default_policy() -> ConversionPolicy:
    """The policy a pilot vulnerability build runs under.

    Undecided on both open questions, which is what the record should say. It
    declares the four measures GEM's pilot-country functions actually demand --
    not the three the SA-first sequencing prefers -- because a function built
    for PGA is not made an SA function by leaving PGA off a list.
    """
    return ConversionPolicy(
        imt_representation=IMTRepresentation.UNDECIDED,
        imts=pilot_bins.PILOT_IMTS,
        notes=(
            "Vulnerability build only. No event identity or investigation time is "
            "declared, and neither bears on a damage table."
        ),
    )


@transaction.atomic
def register_vulnerability(
    country_code: str,
    *,
    root: str | pathlib.Path,
    licence: LicenceStatement | None = None,
    policy: ConversionPolicy | None = None,
    actor=None,
) -> tuple[VulnerabilitySet, CountryBuild]:
    """Build and register one country's GEM vulnerability set with its assets.

    Idempotent by version. Re-registering replaces the stored files and leaves
    the registry record in place, so running this twice does not produce two
    sets whose identifiers mean different things.
    """
    built = build(country_code, root=root, policy=policy)
    statement = licence or LicenceStatement()
    code = built.country_code.upper()

    channels = built.functions
    multi_channel = len(built.multi_imt_classes)

    vulnerability_set, _ = VulnerabilitySet.objects.update_or_create(
        country_code=code,
        version=VULNERABILITY_VERSION,
        defaults={
            "source": GEM_SOURCE,
            "source_commit": built.sources.get("vulnerability_structural", "")[:64],
            "taxonomy_generation": GEM_TAXONOMY,
            "licence": GEM_LICENCE,
            "licence_cleared": statement.cleared,
            "licence_note": statement.as_note(),
            "function_count": len(channels),
            "imts_used": sorted({item.imt for item in channels}),
            "imt_representation": str(
                (policy or default_policy()).imt_representation
            ),
            "multi_channel_class_count": multi_channel,
            "coverage_components": sorted(
                {str(item.coverage_type) for item in built.classes}
            ),
            "damage_bin_count": len(pilot_bins.damage_bins().bins),
            "publication_state": PublicationState.DRAFT,
            "updated_by": actor,
        },
    )

    attach_vulnerability_mapping(
        vulnerability_set,
        mapping_csv(built),
        filename=f"{code.lower()}-vuln-{VULNERABILITY_VERSION}-mapping.csv",
        actor=actor,
    )
    attach_vulnerability_functions(
        vulnerability_set,
        vulnerability_csv(built),
        filename=f"{code.lower()}-vuln-{VULNERABILITY_VERSION}.csv",
        actor=actor,
    )
    attach_damage_bins(
        vulnerability_set,
        damage_bins_to_csv(pilot_bins.damage_bins()),
        filename=f"damage-bins-{pilot_bins.PILOT_BIN_VERSION}.csv",
        actor=actor,
    )
    attach_vulnerability_dictionary(
        vulnerability_set,
        json.dumps(dictionary(built), indent=2, sort_keys=True).encode("utf-8"),
        filename=f"{code.lower()}-vuln-{VULNERABILITY_VERSION}-dictionary.json",
        actor=actor,
    )
    return vulnerability_set, built


@transaction.atomic
def register(
    country_code: str,
    *,
    root: str | pathlib.Path,
    licence: LicenceStatement | None = None,
    policy: ConversionPolicy | None = None,
    actor=None,
) -> ModelVersion:
    """Register the GEM vulnerability set and the model version that uses it."""
    vulnerability_set, built = register_vulnerability(
        country_code, root=root, licence=licence, policy=policy, actor=actor
    )
    return pilot.register_model_version(
        built.country_code,
        vulnerability_set=vulnerability_set,
        vulnerability_limitations=_limitations(built),
        actor=actor,
    )


def _limitations(built: CountryBuild) -> str:
    """The vulnerability half of the model version's known limitations."""
    spanning = len(built.multi_imt_classes)
    lines = [
        f"- Built from {GEM_SOURCE}. The candidate sets and weights are GEM's; "
        "the OED mapping and the seismic design eras are CASS assumptions and "
        "have not been reviewed by a local structural engineer.",
        f"- {spanning} of {len(built.classes)} classes respond at more than one "
        "intensity measure and cannot be one Oasis function. Until the section 6 "
        "multi-IMT representation is approved, a risk reaching one of them is "
        "refused rather than approximated.",
    ]
    lines.extend(f"- {item}" for item in built.enrichment.open_questions)
    return "\n".join(lines)


def report(built: CountryBuild) -> dict[str, Any]:
    """What a build produced, for the operator running it."""
    return {
        "country_code": built.country_code,
        "gem_release": GEM_RELEASE,
        "vulnerability_version": VULNERABILITY_VERSION,
        "classes": len(built.classes),
        "functions": len(built.functions),
        "sources": dict(built.sources),
        "multi_imt": multi_imt_report(built),
        "evidence": evidence_summary(built),
    }
