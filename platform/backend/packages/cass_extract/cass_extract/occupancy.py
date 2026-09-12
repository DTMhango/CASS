"""Occupancy and construction, when the source reports neither.

The extract carries no vulnerability attributes at all. OED requires
``OccupancyCode``, and until now the promoter wrote OED's own unknown code
(1000), which is the truthful answer and maps to no vulnerability function --
so a keys lookup returns ``fail_v``, the section 8 gate holds the run, and
nothing reaches the engine. That is correct behaviour and a dead end: the
platform cannot be exercised end to end while the only available answer is "we
do not know".

So occupancy becomes what the coverage split already is: a named, versioned,
selectable assumption, recorded on every version it produces. Three properties
make that safe.

**It is stated, never derived.** A flat assumption is transparently an
assumption. Deriving occupancy from class of business would produce something
that looks like information -- a warehouse here, an office there -- while
resting on nothing, and section 6 puts that behind an approved enrichment
mapping. ``not_reported_v1`` remains available and remains honest.

**It is deterministic.** Where an assumption spreads several classes across a
portfolio, the class a location receives is a stable function of that
location's identity, not a draw. The same portfolio always produces the same
assignment, so a loss that moves between runs moved because something real
changed. A random assignment would make a golden test meaningless: nobody could
tell an engine regression from a different roll.

**It is outranked by evidence.** Where a schedule states occupancy and
construction per row -- which is how OED works -- those values are used and the
assumption gets out of the way, exactly as reported coverage values outrank a
derived split.

None of the presets is an approved vulnerability prior. Each says so, and the
label travels with any result built on it.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Iterable, Mapping
from typing import Any

from .allocation import AllocationError, AllocationEvidence

#: Bumped whenever a preset's classes or weights change, so a version promoted
#: under an earlier definition stays interpretable.
OCCUPANCY_RULE_VERSION = "1.0.0"

#: OED's own codes for an attribute that is not known. Writing one states a
#: fact; writing anything else states an assumption.
UNKNOWN_OCCUPANCY = "1000"
UNKNOWN_CONSTRUCTION = "5000"

LocationKey = tuple[str, str]


@dataclasses.dataclass(frozen=True, slots=True)
class TaxonomyClass:
    """One occupancy and construction pair an assumption may assign."""

    occupancy: str
    construction: str
    label: str
    #: Relative share within a spread assumption. Ignored where an assumption
    #: names a single class.
    weight: int = 1

    def __post_init__(self) -> None:
        if self.weight <= 0:
            raise AllocationError(
                f"Taxonomy class {self.label!r} has weight {self.weight}. A class that "
                "can never be assigned should be removed rather than weighted zero."
            )
        if not self.occupancy:
            raise AllocationError(
                f"Taxonomy class {self.label!r} names no occupancy code. OED requires "
                f"one; use {UNKNOWN_OCCUPANCY} to say it is unknown."
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "occupancy_code": self.occupancy,
            "construction_code": self.construction,
            "label": self.label,
            "weight": self.weight,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class OccupancyAssumption:
    """One named way of giving every location a taxonomy.

    ``approved`` is false for every preset. A result built on an unapproved
    taxonomy is a research or engine-test result and must be labelled as one:
    the vulnerability function a location reaches is what turns ground shaking
    into a loss, so an assumed occupancy is an assumption about the answer.
    """

    name: str
    description: str
    classes: tuple[TaxonomyClass, ...]
    evidence: AllocationEvidence = AllocationEvidence.ASSUMED
    approved: bool = False
    rule_version: str = OCCUPANCY_RULE_VERSION

    def __post_init__(self) -> None:
        if not self.classes:
            raise AllocationError(
                f"Occupancy assumption {self.name!r} names no classes, so it cannot "
                "give a location an OccupancyCode."
            )
        labels = [item.label for item in self.classes]
        if len(set(labels)) != len(labels):
            raise AllocationError(
                f"Occupancy assumption {self.name!r} repeats a class label. Labels "
                "identify a class in the distribution report, so they must be distinct."
            )

    @property
    def is_uniform(self) -> bool:
        """Whether every location receives the same class."""
        return len(self.classes) == 1

    @property
    def occupancy_codes(self) -> tuple[str, ...]:
        return tuple(sorted({item.occupancy for item in self.classes}))

    def apply(self, key: LocationKey) -> dict[str, str]:
        """The taxonomy one location receives.

        Deterministic by construction. A uniform assumption returns its single
        class; a spread one selects by a stable digest of the assumption name
        and the location identity, so the same location in the same portfolio
        under the same assumption always lands in the same class, on any
        machine and in any process.
        """
        if self.is_uniform:
            chosen = self.classes[0]
        else:
            chosen = self.classes[self._index(key)]
        return {
            "OccupancyCode": chosen.occupancy,
            "ConstructionCode": chosen.construction,
        }

    def label_for(self, key: LocationKey) -> str:
        return (self.classes[0] if self.is_uniform else self.classes[self._index(key)]).label

    def _index(self, key: LocationKey) -> int:
        """Pick a class by weight, from a digest rather than a random draw.

        ``hash()`` is salted per process and would give a different portfolio
        on every run; ``random`` would need a seed nobody records. A blake2b
        digest of the assumption name and the location identity needs neither
        and is reproducible for ever.
        """
        ladder: list[int] = []
        running = 0
        for item in self.classes:
            running += item.weight
            ladder.append(running)
        digest = hashlib.blake2b(
            f"{self.name}|{key[0]}|{key[1]}".encode(), digest_size=8
        ).digest()
        point = int.from_bytes(digest, "big") % running
        for position, ceiling in enumerate(ladder):
            if point < ceiling:
                return position
        return len(self.classes) - 1  # pragma: no cover - the ladder covers the range

    def distribution(self, keys: Iterable[LocationKey]) -> dict[str, int]:
        """How many locations each class would receive.

        Reported on the version, because an assumption whose effect nobody can
        see is one nobody can argue with.
        """
        counts = {item.label: 0 for item in self.classes}
        for key in keys:
            counts[self.label_for(key)] += 1
        return counts

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "evidence": str(self.evidence),
            "approved": self.approved,
            "rule_version": self.rule_version,
            "uniform": self.is_uniform,
            "deterministic": True,
            "occupancy_codes": list(self.occupancy_codes),
            "classes": [item.as_dict() for item in self.classes],
        }


NOT_REPORTED = OccupancyAssumption(
    name="not_reported_v1",
    description=(
        "OED unknown on every row, which is what the source actually supports. "
        "No vulnerability function covers unknown occupancy, so a keys lookup "
        "reports fail_v and the section 8 gate holds the run. That is the "
        "honest state of the data, not a defect."
    ),
    classes=(
        TaxonomyClass(UNKNOWN_OCCUPANCY, UNKNOWN_CONSTRUCTION, "Unknown"),
    ),
    # Reported and approved because it asserts nothing about any risk: the
    # absence of an occupancy is a fact about the source, and stating a fact
    # needs nobody's sign-off. Every other preset asserts something.
    evidence=AllocationEvidence.REPORTED,
    approved=True,
)
"""The only assumption that asserts nothing. It is also the one that cannot run."""

COMMERCIAL_GENERAL = OccupancyAssumption(
    name="commercial_general_v1",
    description=(
        "OED general commercial (1100) with unknown construction on every row. "
        "A single stated class for the whole portfolio: visibly an assumption, "
        "identical for every location, and reproducible. Not an approved prior "
        "and not evidence about any individual risk."
    ),
    classes=(TaxonomyClass("1100", UNKNOWN_CONSTRUCTION, "Commercial, general"),),
)

MIXED_COMMERCIAL = OccupancyAssumption(
    name="mixed_commercial_test_v1",
    description=(
        "Three classes spread deterministically across the portfolio, to "
        "exercise more than one vulnerability function. The class a location "
        "receives is a digest of its identity, not a draw, so the assignment "
        "is identical on every run. Illustrative only: the mix reflects no "
        "study of this book."
    ),
    classes=(
        TaxonomyClass("1100", "5150", "Commercial, reinforced concrete", weight=5),
        TaxonomyClass("1150", "5200", "Industrial, steel", weight=3),
        TaxonomyClass("1050", "5100", "Residential, masonry", weight=2),
    ),
)

#: What the interface offers. Adding one is a deliberate change rather than a
#: free string, so a result can always name the assumption that produced it.
OCCUPANCY_PRESETS: Mapping[str, OccupancyAssumption] = {
    item.name: item
    for item in (NOT_REPORTED, COMMERCIAL_GENERAL, MIXED_COMMERCIAL)
}

DEFAULT_OCCUPANCY = COMMERCIAL_GENERAL
"""What a promotion uses when nobody chooses.

Deliberately the uniform single class rather than the spread. A spread default
would put three different vulnerability priors into every version that never
asked for one, and would make the portfolio's loss depend on an assignment
nobody selected. One stated class is the smallest assumption that still lets
the engine run, and ``not_reported_v1`` remains one parameter away for anyone
who would rather the run stopped at the gate.
"""


def occupancy_preset(name: str) -> OccupancyAssumption:
    """Look up a named assumption."""
    try:
        return OCCUPANCY_PRESETS[name]
    except KeyError:
        raise AllocationError(
            f"{name!r} is not a known occupancy assumption. Available: "
            + ", ".join(sorted(OCCUPANCY_PRESETS))
            + "."
        ) from None


def uniform(
    name: str, occupancy: str, construction: str = UNKNOWN_CONSTRUCTION, *, description: str = ""
) -> OccupancyAssumption:
    """Build a single-class assumption from codes an analyst supplied.

    The codes are not validated against the OED code list here. This package
    does not own that list, and refusing an unrecognised code at this layer
    would only move the same failure later; an occupancy OED does not define
    will be reported by validation, and one no function covers will be reported
    by the keys lookup as ``fail_v``.
    """
    return OccupancyAssumption(
        name=name,
        description=description or "Analyst-supplied occupancy assumption.",
        classes=(
            TaxonomyClass(
                str(occupancy).strip(),
                str(construction or "").strip(),
                f"Occupancy {occupancy}",
            ),
        ),
    )


def assign_taxonomy(
    keys: Iterable[LocationKey],
    assumption: OccupancyAssumption,
    *,
    reported: Mapping[LocationKey, Mapping[str, str]] | None = None,
) -> dict[LocationKey, dict[str, str]]:
    """The taxonomy every location receives, with reported values winning.

    A row that states its own occupancy keeps it. The assumption fills only
    the rows that state nothing, which is what makes a partially completed
    schedule usable rather than an all-or-nothing choice.
    """
    supplied = reported or {}
    resolved: dict[LocationKey, dict[str, str]] = {}
    for key in keys:
        assumed = assumption.apply(key)
        stated = supplied.get(key) or {}
        resolved[key] = {
            "OccupancyCode": str(stated.get("OccupancyCode") or "").strip()
            or assumed["OccupancyCode"],
            "ConstructionCode": str(stated.get("ConstructionCode") or "").strip()
            or assumed["ConstructionCode"],
        }
    return resolved


def taxonomy_record(
    assumption: OccupancyAssumption,
    resolved: Mapping[LocationKey, Mapping[str, str]],
    *,
    reported: Mapping[LocationKey, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """What the version's lineage records about how it got its taxonomy."""
    supplied = {
        key: values
        for key, values in (reported or {}).items()
        if str(values.get("OccupancyCode") or "").strip()
    }
    counts: dict[str, int] = {}
    for values in resolved.values():
        code = values["OccupancyCode"]
        counts[code] = counts.get(code, 0) + 1

    reported_count = len([key for key in supplied if key in resolved])
    return {
        "source": (
            "reported_location_taxonomy"
            if reported_count == len(resolved) and resolved
            else "assumed" if reported_count == 0
            else "mixed_reported_and_assumed"
        ),
        "assumption": assumption.as_dict(),
        "location_count": len(resolved),
        "reported_locations": reported_count,
        "assumed_locations": len(resolved) - reported_count,
        "occupancy_counts": dict(sorted(counts.items())),
        "class_distribution": assumption.distribution(
            key for key in resolved if key not in supplied
        ),
        "basis": (
            "Occupancy and construction are supplied per location."
            if reported_count == len(resolved) and resolved
            else (
                f"Occupancy and construction are not in the source. The "
                f"{assumption.name} assumption applies, which is "
                + ("approved." if assumption.approved else "not an approved prior.")
            )
        ),
        "decision_note": (
            "reported vulnerability attributes"
            if reported_count == len(resolved) and resolved
            else (
                "no reported vulnerability attributes"
                if assumption.approved
                else f"an unapproved occupancy assumption ({assumption.name})"
            )
        ),
    }
