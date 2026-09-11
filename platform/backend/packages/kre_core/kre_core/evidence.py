"""The exposure evidence hierarchy and its arithmetic guarantees.

Build plan section 8 sets the rule that assumptions never overwrite reported
facts, that every inferred field keeps its source, confidence and assumption-set
version, and that weighted realizations reconcile exactly to the source TIV.
Section 15 lists the two failure modes this module exists to prevent: assumed
attributes overwriting reported data, and weighted exposure splitting that
duplicates or loses value.

The types here are deliberately small and free of framework dependencies so
that the enrichment engine, the keys service and the API all enforce the same
rules.
"""

from __future__ import annotations

import dataclasses
import decimal
import enum
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any

#: Monetary reconciliation runs in Decimal, never float, because section 8
#: requires allocated TIV to reconcile *exactly* to the source record.
TIV_PRECISION = Decimal("0.01")

#: Weights are compared at this tolerance; they are dimensionless and may come
#: from ratios that do not terminate in decimal.
WEIGHT_TOLERANCE = Decimal("0.000001")


class EvidenceClass(enum.IntEnum):
    """The evidence hierarchy of build plan section 8, strongest first.

    Ordering is meaningful: a lower value outranks a higher one, so a candidate
    value may only replace an existing one when it comes from a strictly
    stronger class, or when a person records an explicit override.
    """

    REPORTED = 1
    """Reported KRE or cedant data that passes validation."""

    DERIVED = 2
    """Reliably derived, such as a height class from a valid storey count."""

    CORROBORATED = 3
    """Permitted external corroboration tied to the actual risk or location."""

    PRIOR = 4
    """GEM-informed conditional prior for a missing attribute."""

    OVERRIDE = 5
    """Documented expert override approved under model governance.

    Ranked last in the hierarchy but handled explicitly: an override is the one
    route by which a person may displace a stronger class, and doing so records
    the prior value for audit.
    """

    @property
    def is_assumption(self) -> bool:
        """True where the value is inferred rather than observed."""
        return self in (EvidenceClass.PRIOR, EvidenceClass.OVERRIDE)


class EvidenceError(Exception):
    """Base class for evidence and allocation failures."""


class ReportedValueProtected(EvidenceError):
    """Raised when an assumption would overwrite a reported fact."""


class AllocationImbalance(EvidenceError):
    """Raised when a split does not reconcile to its source value."""


@dataclasses.dataclass(frozen=True, slots=True)
class AttributeEvidence:
    """The provenance of one model-required attribute on one exposure record."""

    attribute: str
    value: Any
    evidence: EvidenceClass
    source_reference: str
    confidence: float
    assumption_set_version: str | None = None
    rule_version: str | None = None
    note: str | None = None
    superseded_value: Any = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise EvidenceError(
                f"confidence for {self.attribute!r} must lie in [0, 1], got {self.confidence}"
            )
        if self.evidence.is_assumption and not self.assumption_set_version:
            raise EvidenceError(
                f"{self.attribute!r} is an assumption and must name an assumption set version"
            )

    @property
    def is_assumption(self) -> bool:
        return self.evidence.is_assumption

    def as_dict(self) -> dict[str, Any]:
        return {
            "attribute": self.attribute,
            "value": self.value,
            "evidence": self.evidence.name.lower(),
            "evidence_rank": int(self.evidence),
            "source_reference": self.source_reference,
            "confidence": self.confidence,
            "assumption_set_version": self.assumption_set_version,
            "rule_version": self.rule_version,
            "note": self.note,
            "superseded_value": self.superseded_value,
            "is_assumption": self.is_assumption,
        }


def apply_evidence(
    current: AttributeEvidence | None,
    candidate: AttributeEvidence,
    *,
    allow_override: bool = False,
) -> AttributeEvidence:
    """Decide whether a candidate value may replace the value already held.

    The rule is the one in section 8: assumptions apply only where an attribute
    is absent, unusable or materially uncertain. A candidate wins only when it
    outranks what is held. An OVERRIDE candidate may displace anything, but
    only when the caller has an approved override and the previous value is
    carried forward in ``superseded_value``.
    """
    if current is None:
        return candidate

    if current.attribute != candidate.attribute:
        raise EvidenceError(
            f"cannot merge evidence for {current.attribute!r} with {candidate.attribute!r}"
        )

    if candidate.evidence is EvidenceClass.OVERRIDE:
        if not allow_override:
            raise ReportedValueProtected(
                f"override of {current.attribute!r} requires an approved override"
            )
        return dataclasses.replace(candidate, superseded_value=current.value)

    if candidate.evidence < current.evidence:
        return dataclasses.replace(candidate, superseded_value=current.value)

    if current.evidence is EvidenceClass.REPORTED and candidate.is_assumption:
        raise ReportedValueProtected(
            f"{current.attribute!r} is reported; an assumption may not overwrite it"
        )

    return current


@dataclasses.dataclass(frozen=True, slots=True)
class Alternative:
    """One weighted possibility for an uncertain attribute."""

    value: Any
    weight: Decimal
    rationale: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "weight": str(self.weight),
            "rationale": self.rationale,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class WeightedAlternatives:
    """A distribution over the possible values of one uncertain attribute.

    Section 8 requires missing construction, material, height, code or
    ductility to be represented as weighted alternatives whose weights sum to
    one and whose allocated TIV reconciles exactly to the source record.
    """

    attribute: str
    alternatives: tuple[Alternative, ...]
    assumption_set_version: str
    evidence: EvidenceClass = EvidenceClass.PRIOR

    def __post_init__(self) -> None:
        if not self.alternatives:
            raise EvidenceError(f"{self.attribute!r} has no alternatives")
        total = sum((item.weight for item in self.alternatives), Decimal(0))
        if abs(total - Decimal(1)) > WEIGHT_TOLERANCE:
            raise EvidenceError(
                f"weights for {self.attribute!r} sum to {total}, not 1"
            )
        if any(item.weight < 0 for item in self.alternatives):
            raise EvidenceError(f"{self.attribute!r} has a negative weight")

    def allocate(self, total_value: Decimal) -> tuple[tuple[Alternative, Decimal], ...]:
        """Split a monetary value across the alternatives without loss.

        Each share is rounded to the TIV precision and the residue from
        rounding is placed on the largest share, so the parts always sum to the
        whole. Returning the residue to the largest share keeps the relative
        error smallest.
        """
        return allocate_value(
            total_value,
            [(item, item.weight) for item in self.alternatives],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "attribute": self.attribute,
            "assumption_set_version": self.assumption_set_version,
            "evidence": self.evidence.name.lower(),
            "alternatives": [item.as_dict() for item in self.alternatives],
        }


def allocate_value(
    total_value: Decimal,
    weighted: Sequence[tuple[Any, Decimal]],
) -> tuple[tuple[Any, Decimal], ...]:
    """Split ``total_value`` by weight so the parts sum exactly to the whole.

    This is the arithmetic behind the section 15 mitigation that every
    enrichment run reconcile TIV at record, location and portfolio level.
    """
    if not weighted:
        raise EvidenceError("cannot allocate a value across no alternatives")
    total_value = Decimal(total_value)
    weight_total = sum((weight for _, weight in weighted), Decimal(0))
    if weight_total <= 0:
        raise EvidenceError("total allocation weight must be positive")

    shares: list[Decimal] = []
    for _, weight in weighted:
        share = (total_value * weight / weight_total).quantize(
            TIV_PRECISION, rounding=decimal.ROUND_HALF_EVEN
        )
        shares.append(share)

    residue = total_value.quantize(TIV_PRECISION, rounding=decimal.ROUND_HALF_EVEN) - sum(
        shares, Decimal(0)
    )
    if residue != 0:
        largest = max(range(len(shares)), key=lambda index: shares[index])
        shares[largest] += residue

    return tuple((item, share) for (item, _), share in zip(weighted, shares, strict=False))


@dataclasses.dataclass(frozen=True, slots=True)
class ReconciliationLine:
    """One row of a TIV reconciliation report."""

    scope: str
    source_total: Decimal
    allocated_total: Decimal

    @property
    def difference(self) -> Decimal:
        return self.allocated_total - self.source_total

    @property
    def balanced(self) -> bool:
        return self.difference == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "source_total": str(self.source_total),
            "allocated_total": str(self.allocated_total),
            "difference": str(self.difference),
            "balanced": self.balanced,
        }


def reconcile(
    lines: Iterable[ReconciliationLine],
    *,
    strict: bool = True,
) -> list[ReconciliationLine]:
    """Check a set of reconciliation lines and return the unbalanced ones.

    With ``strict`` set, any imbalance raises. An enrichment run must not be
    publishable while a single scope fails to reconcile.
    """
    unbalanced = [line for line in lines if not line.balanced]
    if unbalanced and strict:
        summary = "; ".join(
            f"{line.scope}: {line.difference:+}" for line in unbalanced[:5]
        )
        raise AllocationImbalance(f"TIV reconciliation failed for {len(unbalanced)} scope(s): {summary}")
    return unbalanced


def missingness_profile(
    records: Iterable[Mapping[str, Any]],
    attributes: Sequence[str],
    *,
    value_field: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Profile how complete each model-required attribute is.

    Section 8 requires a field-completeness and usability profile before
    enrichment so that systematic missingness is visible, and section 12 makes
    it test evidence. Where ``value_field`` names a monetary column the profile
    also reports the value behind the gap, because a small count of large
    risks matters more than a large count of small ones.
    """
    profile: dict[str, dict[str, Any]] = {
        attribute: {
            "present_count": 0,
            "missing_count": 0,
            "present_value": Decimal(0),
            "missing_value": Decimal(0),
        }
        for attribute in attributes
    }

    for record in records:
        weight = Decimal(str(record.get(value_field) or 0)) if value_field else Decimal(0)
        for attribute in attributes:
            raw = record.get(attribute)
            present = raw is not None and str(raw).strip() != ""
            bucket = profile[attribute]
            if present:
                bucket["present_count"] += 1
                bucket["present_value"] += weight
            else:
                bucket["missing_count"] += 1
                bucket["missing_value"] += weight

    for bucket in profile.values():
        total_count = bucket["present_count"] + bucket["missing_count"]
        total_value = bucket["present_value"] + bucket["missing_value"]
        bucket["record_count"] = total_count
        bucket["completeness"] = (
            bucket["present_count"] / total_count if total_count else 0.0
        )
        bucket["value_completeness"] = (
            float(bucket["present_value"] / total_value) if total_value else 0.0
        )
        bucket["present_value"] = str(bucket["present_value"])
        bucket["missing_value"] = str(bucket["missing_value"])

    return profile
