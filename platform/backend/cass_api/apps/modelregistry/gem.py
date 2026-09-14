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

**The licence.** GEM Foundation has given explicit written permission to use the
data and models it makes publicly available, these among them, for the use KRE described
(ADR 15), so a set built here is cleared under that permission by default and
says so. The licence the data carries is recorded beside it, because GEM must be
credited and anything redistributed carries the same terms. A narrower
entitlement can still be stated by passing one.

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
from cass_converter.enrichment import Enrichment, EnrichmentError, enrichment_from
from cass_converter.gem import GemError
from cass_converter.model_build import (
    BASELINE,
    CountryBuild,
    build_country,
    build_variants,
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
    attach_vulnerability_variant,
)
from .models import (
    GEM_PERMISSION,
    AssumptionSet,
    ModelVersion,
    PublicationState,
    VulnerabilitySet,
)

#: The release every pilot build reads. Exposure and vulnerability generations
#: must match, and mixing a 2023-era exposure summary with 2026 functions --
#: or their taxonomies -- is the error this constant exists to make visible.
GEM_RELEASE = "v2026.0.0"

#: Provenance recorded on the registry record.
GEM_SOURCE = f"GEM Global Vulnerability Model and Global Exposure Model {GEM_RELEASE}"

#: The licence the published models carry. Stored beside the permission, because
#: GEM Foundation must be credited and anything redistributed carries the same
#: terms whatever KRE is permitted to do with it.
GEM_LICENCE = "CC BY-NC-SA 4.0"

#: GEM's taxonomy generation. Distinct from the OED codes a schedule states:
#: the whole enrichment step exists to get from one to the other.
GEM_TAXONOMY = "GEM Building Taxonomy v4.0"

#: Version of a vulnerability set built under a pilot enrichment. Moves with the
#: enrichment, since changing an era or an OED mapping changes every function.
VULNERABILITY_VERSION = f"{pilot_enrichment.PILOT_VERSION}-gem"


def vulnerability_version(enrichment: Enrichment) -> str:
    """The version a set built under this enrichment carries.

    The enrichment is the assumption behind every function, so it is what the
    version follows: two sets built from one GEM release under different design
    eras are different models and must not share a version.
    """
    return f"{enrichment.version}-gem"


class GemRegistrationError(Exception):
    """Raised when a GEM vulnerability set cannot be built or registered."""


@dataclasses.dataclass(frozen=True, slots=True)
class LicenceStatement:
    """What the installation is entitled to do with this data.

    GEM Foundation's written permission is the default (ADR 15). It is a fact
    about these models rather than about any one upload, so the screens do not
    ask. A narrower entitlement can still be stated by passing one, and it is
    recorded with the set either way.
    """

    cleared: bool = True
    reference: str = GEM_PERMISSION
    note: str = ""

    def __post_init__(self) -> None:
        if self.cleared and not self.reference.strip():
            raise GemRegistrationError(
                "A licence clearance needs a reference -- the basis that grants it. "
                "A cleared flag with nothing behind it records an entitlement "
                "nobody could point to."
            )

    def as_note(self) -> str:
        if not self.cleared:
            return (
                f"Use of the {GEM_LICENCE} data has not been cleared on this "
                "installation, so this set is for platform development only."
            ) + (f" {self.note}" if self.note else "")
        return f"{self.reference}" + (f" {self.note}" if self.note else "")


def build(
    country_code: str,
    *,
    root: str | pathlib.Path,
    policy: ConversionPolicy | None = None,
    coverage_types: tuple[int, ...] = (1, 2, 3, 4),
    enrichment: Enrichment | None = None,
    region: str = "",
    folder: str = "",
) -> CountryBuild:
    """Build one country's vulnerability set from a GEM release directory.

    ``root`` is the release root -- the directory holding
    ``global_vulnerability_model`` and ``global_exposure_model``. It is a
    parameter rather than a setting because the GEM clones are large, licensed
    and deliberately outside version control; where they sit is a property of
    the machine doing the build, not of the platform.

    ``enrichment``, ``region`` and ``folder`` are what a country outside the two
    pilots needs: the design eras somebody wrote for it, and where GEM publishes
    it in the release. Omitted, the compiled-in pilot specification applies.
    """
    base = pathlib.Path(root)
    if not base.is_dir():
        raise GemRegistrationError(
            f"{base} is not a directory. It should be the GEM {GEM_RELEASE} release "
            "root, holding global_vulnerability_model and global_exposure_model."
        )

    chosen = policy or default_policy()
    try:
        models, prior, resolved = pilot_enrichment.load(
            base,
            country_code,
            chosen=enrichment,
            region=region or None,
            folder=folder or None,
        )
    except (KeyError, OSError, GemError, EnrichmentError) as exc:
        # A country the release does not hold, or an enrichment the release
        # cannot answer, is a refusal rather than a failure: the specification
        # named something that is not there.
        raise GemRegistrationError(
            f"The GEM model for {country_code} could not be read from {base}: {exc}"
        ) from exc

    return build_country(
        enrichment=resolved,
        models=models,
        prior=prior,
        intensity_bins=pilot_bins.intensity_bins(chosen.imts),
        damage_bins=pilot_bins.damage_bins(),
        policy=chosen,
        coverage_types=coverage_types,
    )


def assumption_rules(country_code: str) -> dict[str, dict[str, Any]]:
    """The assumption sets a country's vulnerability build carries, with their rules.

    Always the three section 8 names, baseline first. A registered set's own
    rules win where it states any; otherwise the draft pilot tilt for its
    flavour applies. The rules are recorded on the vulnerability set as they
    were built, so a later change to an assumption set record does not quietly
    re-describe functions that were weighted under the old rules.
    """
    variants: dict[str, dict[str, Any]] = {
        key: ({"design_level_factors": dict(tilt)} if tilt else {})
        for key, tilt in pilot_enrichment.ASSUMPTION_TILTS.items()
    }
    latest: dict[str, AssumptionSet] = {}
    for item in AssumptionSet.objects.filter(country_code=country_code.upper()).order_by(
        "-created_at"
    ):
        latest.setdefault(item.flavour, item)
    for flavour, item in latest.items():
        if item.rules:
            variants[flavour] = dict(item.rules)
    return variants


def build_all(
    country_code: str,
    *,
    root: str | pathlib.Path,
    variants: dict[str, dict[str, Any]],
    policy: ConversionPolicy | None = None,
    coverage_types: tuple[int, ...] = (1, 2, 3, 4),
    enrichment: Enrichment | None = None,
    region: str = "",
    folder: str = "",
) -> dict[str, CountryBuild]:
    """Build one country's vulnerability set once per assumption set (ADR 14)."""
    base = pathlib.Path(root)
    if not base.is_dir():
        raise GemRegistrationError(
            f"{base} is not a directory. It should be the GEM {GEM_RELEASE} release "
            "root, holding global_vulnerability_model and global_exposure_model."
        )

    chosen = policy or default_policy()
    try:
        models, prior, resolved = pilot_enrichment.load(
            base,
            country_code,
            chosen=enrichment,
            region=region or None,
            folder=folder or None,
        )
    except (KeyError, OSError, GemError, EnrichmentError) as exc:
        # A country the release does not hold, or an enrichment the release
        # cannot answer, is a refusal rather than a failure: the specification
        # named something that is not there.
        raise GemRegistrationError(
            f"The GEM model for {country_code} could not be read from {base}: {exc}"
        ) from exc

    return build_variants(
        enrichment=resolved,
        variants=variants,
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
    enrichment: Enrichment | None = None,
    region: str = "",
    folder: str = "",
    actor=None,
) -> tuple[VulnerabilitySet, CountryBuild]:
    """Build and register one country's GEM vulnerability set with its assets.

    Idempotent by version. Re-registering replaces the stored files and leaves
    the registry record in place, so running this twice does not produce two
    sets whose identifiers mean different things.
    """
    variants = assumption_rules(country_code)
    builds = build_all(
        country_code,
        root=root,
        variants=variants,
        policy=policy,
        enrichment=enrichment,
        region=region,
        folder=folder,
    )
    built = builds[BASELINE]
    statement = licence or LicenceStatement()
    code = built.country_code.upper()
    # The set's version follows the enrichment it was built under, so a country
    # somebody wrote an enrichment for is versioned by that rather than by the
    # pilot's number.
    version = vulnerability_version(built.enrichment)

    channels = built.functions
    multi_channel = len(built.multi_imt_classes)

    vulnerability_set, _ = VulnerabilitySet.objects.update_or_create(
        country_code=code,
        version=version,
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
            "assumption_variants": {key: variants[key] for key in builds},
            "publication_state": PublicationState.DRAFT,
            "updated_by": actor,
        },
    )

    attach_vulnerability_mapping(
        vulnerability_set,
        mapping_csv(built),
        filename=f"{code.lower()}-vuln-{version}-mapping.csv",
        actor=actor,
    )
    attach_vulnerability_functions(
        vulnerability_set,
        vulnerability_csv(built),
        filename=f"{code.lower()}-vuln-{version}.csv",
        actor=actor,
    )
    for key, variant in builds.items():
        attach_vulnerability_variant(
            vulnerability_set,
            key,
            vulnerability_csv(variant),
            filename=f"{code.lower()}-vuln-{version}-{key}.csv",
            actor=actor,
        )
    attach_damage_bins(
        vulnerability_set,
        damage_bins_to_csv(pilot_bins.damage_bins()),
        filename=f"damage-bins-{pilot_bins.PILOT_BIN_VERSION}.csv",
        actor=actor,
    )
    provenance = dictionary(built)
    # Where GEM publishes the country, so whatever reads the functions back from
    # the release -- the OpenQuake reference comparison -- finds them without a
    # table of the countries CASS was compiled with.
    located = pilot_enrichment.GEM_LAYOUT.get(code, ("", ""))
    provenance["gem"] = {
        "release": GEM_RELEASE,
        "region": region or located[0],
        "country": folder or located[1],
    }
    # What each set's weights were, so a loss under one can be traced to the
    # tilt that produced it.
    provenance["assumption_sets"] = {
        key: variant.enrichment.as_dict() for key, variant in builds.items()
    }
    attach_vulnerability_dictionary(
        vulnerability_set,
        json.dumps(provenance, indent=2, sort_keys=True).encode("utf-8"),
        filename=f"{code.lower()}-vuln-{version}-dictionary.json",
        actor=actor,
    )
    return vulnerability_set, built


@transaction.atomic
def register_from_specification(
    document: Any,
    *,
    root: str | pathlib.Path,
    licence: LicenceStatement | None = None,
    policy: ConversionPolicy | None = None,
    actor=None,
) -> tuple[VulnerabilitySet, CountryBuild]:
    """Build a country's vulnerability set from a written enrichment.

    The two pilot countries have their design eras compiled in, which was right
    while there were two and wrong for a third: what a country's eras are is
    local expertise rather than a property of the platform. This takes them as a
    document -- with the reason for each era, because that is what a reviewer
    argues with -- alongside where GEM publishes the country in the release.
    """
    if not isinstance(document, dict):
        raise GemRegistrationError("A vulnerability specification must be an object.")
    try:
        written = enrichment_from(document.get("enrichment") or {})
    except EnrichmentError as exc:
        raise GemRegistrationError(str(exc)) from None

    release = document.get("gem") or {}
    region = str(release.get("region") or "").strip()
    folder = str(release.get("country") or "").strip()
    if not region or not folder:
        raise GemRegistrationError(
            "The specification must say where GEM publishes this country: the region "
            "and country folder in the release, which the catalogue lists."
        )
    if not written.iso3:
        raise GemRegistrationError(
            "The enrichment must state the country's ISO alpha-3 code. GEM's taxonomy "
            "mapping file is keyed by it, and there is no rule that derives it -- only "
            "a table, and a wrong one would read another country's mapping."
        )

    return register_vulnerability(
        written.country_code,
        root=root,
        licence=licence,
        policy=policy,
        enrichment=written,
        region=region,
        folder=folder,
        actor=actor,
    )


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
        "vulnerability_version": vulnerability_version(built.enrichment),
        "classes": len(built.classes),
        "functions": len(built.functions),
        "sources": dict(built.sources),
        "multi_imt": multi_imt_report(built),
        "evidence": evidence_summary(built),
    }
