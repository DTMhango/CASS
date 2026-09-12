"""Splitting a location's value into OED coverage components.

Section 5.4 of the integration brief keeps this independent of the location
split, and the order matters: allocate each policy's TIV to its locations
first, then divide each location total into building, other, contents and
business interruption. Doing both at once is how a building/contents
assumption quietly becomes a statement about which site matters.

The source reports one number per policy and no component breakdown, so every
split here is an assumption. That is the reason they are named, versioned and
selectable rather than compiled in: an analyst has to be able to see which
assumption produced a result, change it, and compare. A split nobody can name
is one nobody can argue with.

None of the presets is an approved component prior. They are test assumptions
for exercising the workflow, and each says so. The brief permits exactly one of
them -- ``building_only_technical_v1`` -- as an engine smoke fixture, and
requires that label to travel with any result built on it.

Whatever the weights, the components reconcile exactly to the location total.
The split is done in integer cents over rational weights, like the location
allocation, so no value is created or lost by rounding.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Mapping
from decimal import Decimal
from fractions import Fraction
from typing import Any

from .allocation import AllocationError, AllocationEvidence, apportion

#: Bumped whenever a preset's weights change. Stored with every result, so a
#: run made under an earlier definition stays interpretable.
COMPONENT_RULE_VERSION = "1.0.0"


class Coverage(enum.StrEnum):
    """The OED coverage columns a component split writes."""

    BUILDING = "BuildingTIV"
    OTHER = "OtherTIV"
    CONTENTS = "ContentsTIV"
    BI = "BITIV"

    @property
    def label(self) -> str:
        return {
            Coverage.BUILDING: "Building",
            Coverage.OTHER: "Other structures and machinery",
            Coverage.CONTENTS: "Contents and stock",
            Coverage.BI: "Business interruption",
        }[self]


#: The order components are written and displayed in.
COVERAGE_ORDER: tuple[Coverage, ...] = (
    Coverage.BUILDING,
    Coverage.OTHER,
    Coverage.CONTENTS,
    Coverage.BI,
)


@dataclasses.dataclass(frozen=True, slots=True)
class ComponentSplit:
    """One named way of dividing a location total into coverages."""

    name: str
    weights: Mapping[str, Fraction]
    description: str
    evidence: AllocationEvidence = AllocationEvidence.ASSUMED
    approved: bool = False
    """Whether an approved component prior stands behind this.

    False for every preset. A result built on an unapproved split is a
    research or engine-test result and must be labelled as one.
    """

    rule_version: str = COMPONENT_RULE_VERSION

    def __post_init__(self) -> None:
        total = sum(self.weights.values(), Fraction(0))
        if total != 1:
            raise AllocationError(
                f"Component split {self.name!r} has weights summing to {total}, not 1. "
                "Every unit of a location's value must land in exactly one coverage."
            )
        unknown = set(self.weights) - {str(item) for item in Coverage}
        if unknown:
            raise AllocationError(
                f"Component split {self.name!r} names coverages OED does not have: "
                + ", ".join(sorted(unknown))
                + "."
            )

    @property
    def coverages(self) -> tuple[str, ...]:
        """The coverages that receive a non-zero share, in OED order."""
        return tuple(
            str(coverage)
            for coverage in COVERAGE_ORDER
            if self.weights.get(str(coverage), Fraction(0)) > 0
        )

    def apply(self, amount: Decimal) -> dict[str, Decimal]:
        """Divide one location total, exactly.

        Every coverage appears in the result, including the ones that receive
        nothing: an OED row with a blank contents column and one with a zero
        say different things, and the second is what an unallocated coverage
        means here.
        """
        ordered = [str(coverage) for coverage in COVERAGE_ORDER]
        weights = [self.weights.get(name, Fraction(0)) for name in ordered]
        amounts = apportion(amount, weights)
        return dict(zip(ordered, amounts, strict=True))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "evidence": str(self.evidence),
            "approved": self.approved,
            "rule_version": self.rule_version,
            "weights": {
                name: str(self.weights.get(name, Fraction(0)))
                for name in (str(item) for item in COVERAGE_ORDER)
            },
            "percentages": {
                name: float(self.weights.get(name, Fraction(0)) * 100)
                for name in (str(item) for item in COVERAGE_ORDER)
            },
        }


def _split(name: str, description: str, **percentages: int | Fraction) -> ComponentSplit:
    weights = {
        str(coverage): Fraction(percentages.get(coverage.name.lower(), 0), 100)
        for coverage in COVERAGE_ORDER
    }
    return ComponentSplit(name=name, weights=weights, description=description)


#: The only split the brief permits, and only as an engine smoke fixture.
BUILDING_ONLY = _split(
    "building_only_technical_v1",
    "The whole location value as building. Permitted by the integration brief "
    "solely as an engine smoke fixture; not a complete physical-damage result.",
    building=100,
)

BUILDING_AND_CONTENTS = _split(
    "building_contents_test_v1",
    "A simple two-way test split. No approved prior stands behind the ratio.",
    building=80,
    contents=20,
)

COMMERCIAL_PROPERTY = _split(
    "commercial_property_test_v1",
    "A four-way test split of the kind a commercial property schedule might "
    "show. Illustrative only: no approved prior stands behind these weights.",
    building=65,
    other=5,
    contents=25,
    bi=5,
)

#: What the interface offers. Adding one is a deliberate change, not a free
#: string, so a result can always name the split that produced it.
PRESETS: Mapping[str, ComponentSplit] = {
    split.name: split
    for split in (BUILDING_ONLY, BUILDING_AND_CONTENTS, COMMERCIAL_PROPERTY)
}

DEFAULT_SPLIT = BUILDING_ONLY
"""What a run uses when nobody chooses.

Deliberately the most conservative option. A default that spread value across
coverages would put an unapproved prior into every result that never asked for
one; building-only at least announces itself.
"""


def preset(name: str) -> ComponentSplit:
    """Look up a named split."""
    try:
        return PRESETS[name]
    except KeyError:
        raise AllocationError(
            f"{name!r} is not a known component split. Available: "
            + ", ".join(sorted(PRESETS))
            + "."
        ) from None


def custom(
    name: str, percentages: Mapping[str, float | int | str], *, description: str = ""
) -> ComponentSplit:
    """Build a split from percentages an analyst supplied.

    Percentages rather than fractions because that is how the assumption is
    discussed, and exact rationals underneath because that is how it has to be
    applied. A split that does not add to 100 is refused rather than
    normalised: normalising would silently change what the analyst asked for.
    """
    weights: dict[str, Fraction] = {}
    for key, value in percentages.items():
        coverage = _coverage(key)
        weights[str(coverage)] = Fraction(str(value)) / 100
    return ComponentSplit(
        name=name,
        weights=weights,
        description=description or "Analyst-supplied component split.",
    )


def _coverage(key: str) -> Coverage:
    text = str(key).strip()
    for coverage in Coverage:
        if text.lower() in (str(coverage).lower(), coverage.name.lower()):
            return coverage
    raise AllocationError(
        f"{key!r} is not an OED coverage. Available: "
        + ", ".join(str(item) for item in COVERAGE_ORDER)
        + "."
    )


def split_locations(
    totals: Mapping[tuple[str, str], Decimal], component_split: ComponentSplit
) -> dict[tuple[str, str], dict[str, Decimal]]:
    """Apply one split to every location total."""
    return {key: component_split.apply(amount) for key, amount in totals.items()}


def reconciliation(
    totals: Mapping[tuple[str, str], Decimal],
    components: Mapping[tuple[str, str], Mapping[str, Decimal]],
) -> dict[str, Any]:
    """Prove the components add back to the location totals.

    Reported per location as well as in total. A portfolio that balances while
    two locations are wrong in opposite directions is the error worth catching.
    """
    mismatched = [
        {"location": f"{business}/{number}", "expected": str(amount),
         "components": str(sum(components[(business, number)].values(), Decimal("0.00")))}
        for (business, number), amount in totals.items()
        if sum(components[(business, number)].values(), Decimal("0.00")) != amount
    ]
    expected = sum(totals.values(), Decimal("0.00"))
    allocated = sum(
        (sum(values.values(), Decimal("0.00")) for values in components.values()),
        Decimal("0.00"),
    )
    return {
        "location_count": len(totals),
        "expected_total": str(expected),
        "component_total": str(allocated),
        "difference": str(allocated - expected),
        "reconciles": not mismatched and allocated == expected,
        "mismatched_locations": mismatched,
    }
