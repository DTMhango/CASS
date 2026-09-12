"""Building one country's vulnerability set, from GEM files to Oasis tables.

This is where the reading, the assumption and the arithmetic meet. Given a
country's published GEM model, its exposure summary and a chosen enrichment, it
produces the classes a Klapton Re schedule can actually distinguish, the GEM
mixture behind each, the blended function for each, and the Oasis tables.

The unit is a **class**: one combination of OED occupancy, construction and
storey band that CASS can tell apart on the intake template. Everything finer
than that -- design level, lateral system, the difference between confined and
unreinforced masonry -- is inside the mixture, because the schedule cannot
express it and pretending otherwise is where false precision comes from.

A class does not always become one Oasis function. GEM assigns each taxonomy
the spectral period its structures respond at, so a class whose candidates
differ in height or stiffness usually spans intensity measures, and no choice
of damage bins changes that. Each measure becomes a **channel**: a blend of the
candidates that demand it, carrying the share of the class's weight they hold.

Where a class has one channel, it is one Oasis function and there is nothing to
decide. Where it has several, converting it needs the multi-IMT representation
the build plan leaves open, and ``ClassBuild.needs_multi_imt`` says which
classes those are. The build produces them either way and refuses to guess: the
report counts them so the decision is made against a number rather than a
worry.
"""

from __future__ import annotations

import csv
import dataclasses
import io
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .bins import DamageBinSet, IntensityBinSet
from .enrichment import (
    OED_CONSTRUCTION,
    OED_OCCUPANCY,
    UNKNOWN_CONSTRUCTION,
    Attributes,
    Enrichment,
    EnrichmentError,
    Evidence,
    Mixture,
    StockPrior,
)
from .gem import LossCategory, VulnerabilityModel
from .policy import ConversionPolicy
from .vulnerability import (
    Component,
    DiscretisedFunction,
    Placement,
    blend,
    table_report,
    to_csv,
)

#: Bumped when the class enumeration or the identifier ordering changes.
BUILD_VERSION = "1.0.0"

#: Storey bands a schedule is taken to distinguish. Finer than this is not
#: useful -- GEM publishes functions at particular heights and the difference
#: between a four- and a five-storey frame is inside the mixture -- and coarser
#: throws away a field the template already collects.
#:
#: ``unstated`` is the one that matters most, because it is the common case.
#: Facultative schedules mostly do not carry a storey count, and a class set
#: that required one would leave the whole book unclassified. It is also the
#: expensive case: height is what decides the spectral period a structure
#: responds at, so a risk that does not state it reaches candidates across
#: every intensity measure GEM uses and cannot be one Oasis function.
STOREY_BANDS: tuple[str, ...] = ("unstated", "low", "mid", "high")

#: The representative storey count each band resolves with, or ``None`` where
#: the schedule said nothing. The middle of the band rather than its edge,
#: because a band is a statement about a population and its centre is the least
#: wrong single member.
BAND_STOREYS: Mapping[str, int | None] = {
    "unstated": None,
    "low": 1,
    "mid": 4,
    "high": 10,
}

#: OED coverage type to the GEM loss category that describes it.
#:
#: Buildings and other structures are structural; contents is contents; and
#: business interruption is routed through non-structural, which is the closest
#: published thing and is not the same thing. Downtime follows damage to the
#: fabric and services rather than to the frame, so non-structural is the right
#: family -- but GEM's non-structural functions describe damage to components,
#: not the time it takes to reinstate them, and a real BI view needs a downtime
#: relationship this model does not carry.
COVERAGE_CATEGORIES: Mapping[int, LossCategory] = {
    1: LossCategory.STRUCTURAL,
    2: LossCategory.STRUCTURAL,
    3: LossCategory.CONTENTS,
    4: LossCategory.NONSTRUCTURAL,
}

#: The columns the keys service reads back.
MAPPING_COLUMNS = (
    "VulnerabilityID",
    "CoverageTypeID",
    "RequiredIMT",
    "ChannelWeight",
    "OccupancyCodes",
    "ConstructionCodes",
    "StoreyBand",
    "Label",
)


class BuildError(Exception):
    """Raised when a country's vulnerability set cannot be built."""


@dataclasses.dataclass(frozen=True, slots=True)
class Channel:
    """One intensity measure's share of a class, and the function that carries it."""

    imt: str
    weight: float
    vulnerability_id: int
    function: DiscretisedFunction

    def as_dict(self) -> dict[str, Any]:
        return {
            "imt": self.imt,
            "weight": self.weight,
            "vulnerability_id": self.vulnerability_id,
            "taxonomies": [
                {"taxonomy": name, "weight": share}
                for name, share in self.function.components
            ],
        }


@dataclasses.dataclass(frozen=True, slots=True)
class ClassBuild:
    """One distinguishable class of risk, for one coverage type."""

    label: str
    occupancy_code: str
    construction_code: str
    storey_band: str
    coverage_type: int
    loss_category: LossCategory
    mixture: Mixture
    channels: tuple[Channel, ...]

    @property
    def needs_multi_imt(self) -> bool:
        """Whether this class cannot be one Oasis function.

        Not a defect and not a resolution: it is the statement that converting
        this class requires the representation section 6 leaves open.
        """
        return len(self.channels) > 1

    @property
    def intensity_measures(self) -> tuple[str, ...]:
        return tuple(item.imt for item in self.channels)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "occupancy_code": self.occupancy_code,
            "construction_code": self.construction_code,
            "storey_band": self.storey_band,
            "coverage_type": self.coverage_type,
            "loss_category": str(self.loss_category),
            "needs_multi_imt": self.needs_multi_imt,
            "candidate_count": len(self.mixture),
            "evidence": self.mixture.as_dict()["evidence"],
            "channels": [item.as_dict() for item in self.channels],
        }


@dataclasses.dataclass(frozen=True, slots=True)
class CountryBuild:
    """Everything one country's vulnerability release contains."""

    country_code: str
    enrichment: Enrichment
    classes: tuple[ClassBuild, ...]
    damage_bin_reference: str
    sources: Mapping[str, str]
    build_version: str = BUILD_VERSION

    @property
    def functions(self) -> tuple[DiscretisedFunction, ...]:
        return tuple(
            channel.function for item in self.classes for channel in item.channels
        )

    @property
    def multi_imt_classes(self) -> tuple[ClassBuild, ...]:
        return tuple(item for item in self.classes if item.needs_multi_imt)

    def as_dict(self) -> dict[str, Any]:
        functions = self.functions
        measures = sorted({item.imt for item in functions})
        return {
            "country_code": self.country_code,
            "build_version": self.build_version,
            "enrichment": self.enrichment.as_dict(),
            "damage_bins": self.damage_bin_reference,
            "sources": dict(self.sources),
            "classes": len(self.classes),
            "functions": len(functions),
            "intensity_measures": measures,
            "classes_needing_multi_imt": len(self.multi_imt_classes),
            "single_channel_classes": len(self.classes) - len(self.multi_imt_classes),
            "table": table_report(functions),
            "open_questions": list(self.enrichment.open_questions),
        }


# -- the classes a schedule can distinguish --------------------------------------------

def classes(enrichment: Enrichment) -> tuple[tuple[str, str, str], ...]:
    """Every (occupancy, construction, storey band) CASS can tell apart.

    Unknown construction is a class of its own rather than an absence: a
    schedule that does not state the material is the common case, and it needs
    a function as much as one that does. Unknown *occupancy* is not, and gets
    no class at all -- see the enrichment module for why that gap is deliberate.
    """
    constructions = (UNKNOWN_CONSTRUCTION, *sorted(OED_CONSTRUCTION))
    return tuple(
        (occupancy, construction, band)
        for occupancy in sorted(OED_OCCUPANCY)
        for construction in constructions
        for band in STOREY_BANDS
    )


def _label(occupancy: str, construction: str, band: str, coverage: int) -> str:
    occupancy_name = str(OED_OCCUPANCY[occupancy]).lower()
    material = (
        "construction not stated"
        if construction == UNKNOWN_CONSTRUCTION
        else "/".join(OED_CONSTRUCTION[construction]).lower()
    )
    height = "height not stated" if band == "unstated" else f"{band}-rise"
    return f"{occupancy_name} {material} {height} coverage {coverage}"


# -- the build ---------------------------------------------------------------------------

def build_country(
    *,
    enrichment: Enrichment,
    models: Mapping[LossCategory, VulnerabilityModel],
    prior: StockPrior | None,
    intensity_bins: Mapping[str, IntensityBinSet],
    damage_bins: DamageBinSet,
    policy: ConversionPolicy,
    coverage_types: Sequence[int] = (1, 2, 3, 4),
    placement: Placement = Placement.MEAN_PRESERVING,
) -> CountryBuild:
    """Build one country's classes, channels and Oasis functions."""
    policy.require_runnable()

    needed = {COVERAGE_CATEGORIES[item] for item in coverage_types}
    missing = sorted(str(item) for item in needed - set(models))
    if missing:
        raise BuildError(
            f"The {enrichment.country_code} build needs these loss categories and "
            f"was not given them: {', '.join(missing)}."
        )
    for category in needed:
        if not category.is_monetary:
            raise BuildError(
                f"{category} is a ratio of occupants rather than of value and must "
                "not reach a table Oasis multiplies by a TIV."
            )
    if prior is not None and prior.country_code != enrichment.country_code:
        raise EnrichmentError(
            f"The stock prior is for {prior.country_code} and the enrichment for "
            f"{enrichment.country_code}."
        )

    built: list[ClassBuild] = []
    identifier = 0

    for coverage in sorted(coverage_types):
        category = COVERAGE_CATEGORIES[coverage]
        model = models[category]
        for occupancy, construction, band in classes(enrichment):
            attributes = Attributes(
                occupancy_code=occupancy,
                construction_code=construction,
                storeys=BAND_STOREYS[band],
            )
            mixture = enrichment.resolve(attributes, model, prior)
            if not mixture.resolved:
                continue

            label = _label(occupancy, construction, band, coverage)
            channels: list[Channel] = []
            for imt, candidates in mixture.by_imt().items():
                if imt not in policy.imts:
                    raise BuildError(
                        f"{label} reaches {imt}, which this conversion does not "
                        f"declare. Declared: {', '.join(policy.imts) or 'none'}."
                    )
                if imt not in intensity_bins:
                    raise BuildError(
                        f"No intensity-bin dictionary was supplied for {imt}."
                    )

                identifier += 1
                components = [
                    Component(model.by_taxonomy[item.taxonomy], item.weight)
                    for item in candidates
                ]
                channels.append(
                    Channel(
                        imt=imt,
                        weight=sum(item.weight for item in candidates),
                        vulnerability_id=identifier,
                        function=blend(
                            components,
                            vulnerability_id=identifier,
                            label=f"{label} [{imt}]",
                            intensity_bins=intensity_bins[imt],
                            damage_bins=damage_bins,
                            placement=placement,
                        ),
                    )
                )

            built.append(
                ClassBuild(
                    label=label,
                    occupancy_code=occupancy,
                    construction_code=construction,
                    storey_band=band,
                    coverage_type=coverage,
                    loss_category=category,
                    mixture=mixture,
                    channels=tuple(channels),
                )
            )

    if not built:
        raise BuildError(
            f"The {enrichment.country_code} build produced no classes at all."
        )

    sources = {
        f"vulnerability_{category}": model.checksum
        for category, model in sorted(models.items())
    }
    if prior is not None:
        sources["exposure_summary"] = prior.checksum

    return CountryBuild(
        country_code=enrichment.country_code,
        enrichment=enrichment,
        classes=tuple(built),
        damage_bin_reference=damage_bins.reference,
        sources=sources,
    )


# -- what the build produces ----------------------------------------------------------

def vulnerability_csv(build: CountryBuild) -> bytes:
    """The Oasis ``vulnerability.csv`` for every channel of every class."""
    return to_csv(build.functions)


def mapping_csv(build: CountryBuild) -> bytes:
    """The taxonomy mapping the keys service resolves against.

    One row per channel, so a class that spans intensity measures appears more
    than once. That is the shape the data has; whether the keys service may
    return several rows for one risk is the multi-IMT decision, and this file
    states the question rather than answering it.
    """
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=list(MAPPING_COLUMNS), lineterminator="\n"
    )
    writer.writeheader()
    for item in sorted(
        build.classes, key=lambda entry: (entry.coverage_type, entry.label)
    ):
        for channel in item.channels:
            writer.writerow(
                {
                    "VulnerabilityID": channel.vulnerability_id,
                    "CoverageTypeID": item.coverage_type,
                    "RequiredIMT": channel.imt,
                    "ChannelWeight": f"{channel.weight:.6f}",
                    "OccupancyCodes": item.occupancy_code,
                    "ConstructionCodes": (
                        "" if item.construction_code == UNKNOWN_CONSTRUCTION
                        else item.construction_code
                    ),
                    "StoreyBand": item.storey_band,
                    "Label": item.label,
                }
            )
    return buffer.getvalue().encode("utf-8")


def dictionary(build: CountryBuild) -> dict[str, Any]:
    """The vulnerability dictionary: what each identifier means and why.

    The build plan asks for a dictionary relating taxonomy and coverage type to
    an Oasis identifier and its required IMT. This is that, plus the part that
    matters more for a model nobody has approved yet: the GEM taxonomies behind
    each identifier and the weight each carries, so a loss can be traced back to
    the buildings it was computed from.
    """
    return {
        "country_code": build.country_code,
        "build_version": build.build_version,
        "enrichment": build.enrichment.reference,
        "damage_bins": build.damage_bin_reference,
        "sources": dict(build.sources),
        "entries": [
            {
                "vulnerability_id": channel.vulnerability_id,
                "label": item.label,
                "coverage_type": item.coverage_type,
                "loss_category": str(item.loss_category),
                "required_imt": channel.imt,
                "channel_weight": channel.weight,
                "occupancy_code": item.occupancy_code,
                "construction_code": item.construction_code,
                "storey_band": item.storey_band,
                "blended_from": [
                    {"taxonomy": name, "weight": share}
                    for name, share in channel.function.components
                ],
            }
            for item in build.classes
            for channel in item.channels
        ],
    }


def multi_imt_report(build: CountryBuild) -> dict[str, Any]:
    """How much of this country's vulnerability needs the open decision.

    Written to be read by someone deciding it. The counts say how many classes
    cannot be one Oasis function; the examples say which, so the decision is
    taken against the actual cases rather than against the idea of them.
    """
    spanning = build.multi_imt_classes
    by_measures: dict[str, int] = {}
    for item in spanning:
        key = "+".join(item.intensity_measures)
        by_measures[key] = by_measures.get(key, 0) + 1

    return {
        "country_code": build.country_code,
        "classes": len(build.classes),
        "classes_needing_multi_imt": len(spanning),
        "share_needing_multi_imt": (
            len(spanning) / len(build.classes) if build.classes else 0.0
        ),
        "combinations": dict(sorted(by_measures.items())),
        "examples": [
            {
                "label": item.label,
                "intensity_measures": list(item.intensity_measures),
                "channel_weights": {
                    channel.imt: round(channel.weight, 4) for channel in item.channels
                },
            }
            for item in spanning[:5]
        ],
        "note": (
            "A class listed here reaches GEM taxonomies that respond at different "
            "spectral periods. Under a correlated-channel representation each "
            "channel is a sub-peril with its own footprint; under any single-channel "
            "representation the others are reinterpreted, which section 6 requires "
            "to be approved on its own evidence. Neither is chosen here."
        ),
    }


def build_report(builds: Iterable[CountryBuild]) -> dict[str, Any]:
    """One report over several countries, for a release."""
    items = list(builds)
    return {
        "countries": [item.country_code for item in items],
        "builds": [item.as_dict() for item in items],
        "multi_imt": [multi_imt_report(item) for item in items],
    }


def evidence_summary(build: CountryBuild) -> dict[str, Any]:
    """How much of each class was decided by the schedule and how much by prior."""
    counts: dict[str, dict[str, int]] = {
        dimension: {str(value): 0 for value in Evidence}
        for dimension in ("construction", "height", "design")
    }
    for item in build.classes:
        for dimension in counts:
            counts[dimension][str(getattr(item.mixture, dimension))] += 1
    return {
        "country_code": build.country_code,
        "classes": len(build.classes),
        "evidence": counts,
        "mean_candidates_per_class": (
            sum(len(item.mixture) for item in build.classes) / len(build.classes)
            if build.classes
            else 0.0
        ),
    }
