"""Allocating a policy's reported TIV across the locations it schedules.

The extract reports value at the policy, and the model needs it at the
location. Nothing in the source says how it divides, so this is an assumption
in every case where a policy schedules more than one site -- and the
integration brief is unusually specific about how to make one honestly.

Three rules shape everything here.

**Repeat nothing.** Writing the policy's whole TIV at each of its locations is
the failure the brief names first. Three sites would produce three times the
exposure, and a loss computed from it would look like a real number.

**Assume as little as possible.** With no site-value evidence, the equal split
is the maximum-ignorance allocation: it introduces no ranking among sites that
the source does not support. Geocode confidence, precision, provider, address
length and the primary flag are *not* measures of economic value and are never
used as weights -- a coordinate that was easy to find is not a building worth
more. Nor is modelled hazard: choosing weights from the answer would make the
exposure depend on the result being measured.

**Reconcile exactly.** Allocation is done in integer cents over exact rational
weights, and the residual cents are handed out by largest remainder in a
deterministic order. Not "close enough to the total" -- equal to it, for every
policy, business, country and portfolio, which is what the brief requires and
what floating point could not promise.

Location allocation is kept separate from the coverage-component split. They
are independent questions, and answering them together is how a building /
contents assumption quietly becomes a statement about which site matters.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from fractions import Fraction
from typing import Any

#: Bumped whenever a weighting or rounding rule changes. Recorded per policy,
#: so a run made under an older rule stays interpretable.
ALLOCATION_RULE_VERSION = "1.0.0"

#: The share the primary-concentrated sensitivity puts at the reported primary
#: site. It tests whether the primary flag is economically material; it does
#: not assert that it is.
PRIMARY_SHARE = Fraction(7, 10)


class AllocationMethod(enum.StrEnum):
    """How one policy's value was divided."""

    SINGLE_LOCATION = "single_location_v1"
    """One scheduled location, so it takes the whole TIV. Not an assumption."""

    EQUAL_LOCATION = "equal_location_v1"
    """The maximum-ignorance baseline: equal shares, no ranking implied."""

    PRIMARY_CONCENTRATED = "primary_concentrated_v1"
    """Sensitivity: 70% at the reported primary, 30% shared by the rest."""

    CONCENTRATION = "concentration_v1"
    """Envelope variant: the whole TIV at one nominated site."""

    REPORTED = "reported_location_tiv_v1"
    """Reported location values or percentages. Evidence, not an assumption."""


class AllocationEvidence(enum.StrEnum):
    """How much the division rests on."""

    REPORTED = "reported"
    DERIVED = "derived"
    ASSUMED = "assumed"


class AllocationError(Exception):
    """Raised when a policy cannot be allocated as asked."""


@dataclasses.dataclass(frozen=True, slots=True)
class LocationShare:
    """One location's share of one policy."""

    business_id: str
    location_number: str
    """Text, because OED makes a location reference text.

    A schedule that numbers its sites "SITE-A" is ordinary, and reading one as
    an integer turns every site into zero -- which then collides, and the
    collision looks like a duplicate rather than a parsing choice.
    """

    amount: Decimal
    #: The unrounded weight, kept because the brief requires the assumption
    #: record to hold what was intended as well as what was paid out in cents.
    weight: str
    residual_cent: bool = False
    """Whether this share received one of the cents left over by rounding."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "business_id": self.business_id,
            "location_number": self.location_number,
            "amount": str(self.amount),
            "weight": self.weight,
            "residual_cent": self.residual_cent,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class PolicyAllocation:
    """One policy's TIV, divided and reconciled."""

    policy_id: str
    business_id: str
    method: AllocationMethod
    evidence: AllocationEvidence
    policy_tiv: Decimal
    location_count: int
    shares: tuple[LocationShare, ...]
    rule_version: str = ALLOCATION_RULE_VERSION
    #: Set on a concentration variant so the envelope can name the site.
    concentrated_at: int | None = None

    @property
    def allocated(self) -> Decimal:
        return sum((share.amount for share in self.shares), Decimal("0.00"))

    @property
    def reconciles(self) -> bool:
        return self.allocated == self.policy_tiv

    @property
    def difference(self) -> Decimal:
        return self.allocated - self.policy_tiv

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "business_id": self.business_id,
            "method": str(self.method),
            "evidence": str(self.evidence),
            "policy_tiv": str(self.policy_tiv),
            "location_count": self.location_count,
            "allocated": str(self.allocated),
            "difference": str(self.difference),
            "reconciles": self.reconciles,
            "rule_version": self.rule_version,
            "concentrated_at": self.concentrated_at,
            "shares": [share.as_dict() for share in self.shares],
        }


@dataclasses.dataclass(slots=True)
class AllocationResult:
    """Every policy allocated under one scenario."""

    method: AllocationMethod
    rule_version: str
    allocations: list[PolicyAllocation]
    excluded: list[tuple[str, str]]
    """``(policy_id, reason)`` for policies deliberately left out."""

    @property
    def source_tiv(self) -> Decimal:
        return sum((item.policy_tiv for item in self.allocations), Decimal("0.00"))

    @property
    def allocated_tiv(self) -> Decimal:
        return sum((item.allocated for item in self.allocations), Decimal("0.00"))

    @property
    def reconciles(self) -> bool:
        """Every policy individually, not merely the total.

        A total that balances while two policies are wrong in opposite
        directions is exactly the error this is meant to catch.
        """
        return all(item.reconciles for item in self.allocations)

    def by_location(self) -> dict[tuple[str, str], Decimal]:
        """Total allocated to each location, summed across policies.

        A business with two policies contributes to its locations twice, which
        is correct: each policy carries its own TIV and is allocated
        independently.
        """
        totals: dict[tuple[str, str], Decimal] = {}
        for allocation in self.allocations:
            for share in allocation.shares:
                key = (share.business_id, share.location_number)
                totals[key] = totals.get(key, Decimal("0.00")) + share.amount
        return totals

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": str(self.method),
            "rule_version": self.rule_version,
            "policy_count": len(self.allocations),
            "source_tiv": str(self.source_tiv),
            "allocated_tiv": str(self.allocated_tiv),
            "difference": str(self.allocated_tiv - self.source_tiv),
            "reconciles": self.reconciles,
            "excluded": [
                {"policy_id": policy_id, "reason": reason}
                for policy_id, reason in self.excluded
            ],
            "allocations": [item.as_dict() for item in self.allocations],
        }


# -- allocating one policy ------------------------------------------------------

def allocate_policy(
    policy: Mapping[str, Any],
    schedule: Sequence[Mapping[str, Any]],
    *,
    method: AllocationMethod = AllocationMethod.EQUAL_LOCATION,
    concentrate_at: str | None = None,
) -> PolicyAllocation:
    """Divide one policy's TIV across its scheduled locations.

    ``schedule`` is every location the policy's business holds, in the order
    they should be considered. A single-location policy is recorded as
    ``single_location_v1`` rather than as an equal split of one, because a
    whole allocation to the only site is a fact and an equal split is an
    assumption; a reader should be able to tell them apart.
    """
    policy_id = _text(policy.get("policy_id"))
    business_id = _text(policy.get("business_id"))
    # ``policy_tiv`` rather than the source's own column name: the allocation
    # engine reads canonical records, so it is not shaped by what one
    # spreadsheet happened to call its value column.
    tiv = _money(policy.get("policy_tiv"))

    if not schedule:
        raise AllocationError(
            f"Policy {policy_id} schedules no locations, so its "
            f"{tiv} of TIV has nowhere to go."
        )

    ordered = sorted(schedule, key=_location_order)
    count = len(ordered)

    if count == 1:
        chosen = AllocationMethod.SINGLE_LOCATION
        evidence = AllocationEvidence.REPORTED
        weights = [Fraction(1)]
    elif method is AllocationMethod.CONCENTRATION:
        chosen = method
        evidence = AllocationEvidence.ASSUMED
        weights = _concentration_weights(ordered, concentrate_at, policy_id)
    elif method is AllocationMethod.PRIMARY_CONCENTRATED:
        chosen = method
        evidence = AllocationEvidence.ASSUMED
        weights = _primary_weights(ordered, policy_id)
    elif method is AllocationMethod.REPORTED:
        chosen = method
        evidence = AllocationEvidence.REPORTED
        weights = _reported_weights(ordered, policy_id)
    else:
        chosen = AllocationMethod.EQUAL_LOCATION
        evidence = AllocationEvidence.ASSUMED
        weights = [Fraction(1, count)] * count

    amounts = apportion(tiv, weights)
    floors = _floor_cents(tiv, weights)
    shares = tuple(
        LocationShare(
            business_id=_text(location.get("business_id")) or business_id,
            location_number=_location_reference(location),
            amount=amount,
            weight=str(weight),
            residual_cent=_cents(amount) > floor,
        )
        for location, weight, amount, floor in zip(ordered, weights, amounts, floors, strict=False)
    )

    return PolicyAllocation(
        policy_id=policy_id,
        business_id=business_id,
        method=chosen,
        evidence=evidence,
        policy_tiv=tiv,
        location_count=count,
        shares=shares,
        concentrated_at=(
            concentrate_at if chosen is AllocationMethod.CONCENTRATION else None
        ),
    )


def _reported_weights(
    schedule: Sequence[Mapping[str, Any]], policy_id: str
) -> list[Fraction]:
    """Shares in proportion to the value each site reports.

    Where a schedule states a value per site there is no allocation assumption
    left to make, which is why this is the only method whose evidence is
    ``REPORTED``. Two details decide whether it is honest.

    **Proportions, not amounts.** A business can hold several policies over one
    schedule, and site values cannot equal every policy's TIV at once. Dividing
    each policy in proportion to the reported values is the only reading that
    works for both cases, and it keeps every policy's total exact. Where a
    single policy's site values do add to its TIV, the proportions give those
    amounts back unchanged.

    **Every site or none.** A schedule where three sites carry a value and the
    fourth is blank has not reported its division; it has reported part of it.
    Treating the blank as zero would put nothing at a real building, and
    filling it with an average would be the assumption this method exists to
    avoid. So a partial schedule is refused, and it names the sites that are
    missing.
    """
    values = [_money(row.get("location_tiv")) for row in schedule]
    missing = [
        _location_reference(row)
        for row, value in zip(schedule, values, strict=True)
        if not _text(row.get("location_tiv"))
    ]
    if missing:
        raise AllocationError(
            f"Policy {policy_id} was asked for a reported allocation, but "
            f"{len(missing)} of its {len(schedule)} locations report no value "
            f"(location {', '.join(str(item) for item in missing)}). A partly "
            "reported schedule states part of a division, not a division. Supply "
            "every site's value, or choose an assumption."
        )
    if any(value < 0 for value in values):
        raise AllocationError(
            f"Policy {policy_id} has a location reporting a negative value, which "
            "is not something this platform can interpret."
        )
    total = sum(values, Decimal("0.00"))
    if total <= 0:
        raise AllocationError(
            f"Policy {policy_id} has reported location values summing to {total}, "
            "so they cannot divide anything. A schedule of zeroes is not a "
            "reported allocation."
        )
    return [Fraction(_cents(value), _cents(total)) for value in values]


def _primary_weights(schedule: Sequence[Mapping[str, Any]], policy_id: str) -> list[Fraction]:
    """70% at the reported primary, 30% shared equally by the rest."""
    primary = [index for index, row in enumerate(schedule) if _is_yes(row.get("primary_location"))]
    if len(primary) != 1:
        raise AllocationError(
            f"Policy {policy_id} has {len(primary)} locations marked primary, so the "
            "primary-concentrated sensitivity has no single site to concentrate on."
        )
    others = len(schedule) - 1
    rest = (1 - PRIMARY_SHARE) / others
    return [PRIMARY_SHARE if index == primary[0] else rest for index in range(len(schedule))]


def _concentration_weights(
    schedule: Sequence[Mapping[str, Any]], concentrate_at: str | None, policy_id: str
) -> list[Fraction]:
    """The whole TIV at one nominated location."""
    if concentrate_at is None:
        raise AllocationError(
            f"A concentration variant for policy {policy_id} must name the location "
            "the whole TIV is placed at."
        )
    numbers = [_location_reference(row) for row in schedule]
    if concentrate_at not in numbers:
        raise AllocationError(
            f"Policy {policy_id} does not schedule location {concentrate_at}."
        )
    return [Fraction(1) if number == concentrate_at else Fraction(0) for number in numbers]


# -- allocating a portfolio -------------------------------------------------------

def allocate(
    policies: Iterable[Mapping[str, Any]],
    locations: Iterable[Mapping[str, Any]],
    *,
    method: AllocationMethod = AllocationMethod.EQUAL_LOCATION,
    eligible: set[tuple[str, str]] | None = None,
) -> AllocationResult:
    """Allocate every policy independently, and refuse to redistribute quietly.

    ``eligible`` is the location cohort a run is approved for. Where a policy
    schedules a site outside it, the whole policy is excluded rather than
    having that site's share spread over the others: silently redistributing
    is how an excluded location's value ends up overstating the ones that
    remain, which the brief forbids in as many words.
    """
    schedules: dict[str, list[Mapping[str, Any]]] = {}
    for location in locations:
        schedules.setdefault(_text(location.get("business_id")), []).append(location)

    allocations: list[PolicyAllocation] = []
    excluded: list[tuple[str, str]] = []

    for policy in policies:
        policy_id = _text(policy.get("policy_id"))
        business_id = _text(policy.get("business_id"))
        schedule = schedules.get(business_id, [])

        if not schedule:
            excluded.append(
                (policy_id, "The policy's business schedules no geocoded location.")
            )
            continue

        if eligible is not None:
            outside = [
                _location_reference(row)
                for row in schedule
                if (business_id, _location_reference(row)) not in eligible
            ]
            if outside:
                excluded.append(
                    (
                        policy_id,
                        f"{len(outside)} of {len(schedule)} scheduled locations are "
                        "outside the approved cohort. The whole policy is excluded "
                        "rather than moving their share onto the rest.",
                    )
                )
                continue

        allocations.append(allocate_policy(policy, schedule, method=method))

    return AllocationResult(
        method=method,
        rule_version=ALLOCATION_RULE_VERSION,
        allocations=allocations,
        excluded=excluded,
    )


def concentration_envelope(
    policy: Mapping[str, Any], schedule: Sequence[Mapping[str, Any]]
) -> list[PolicyAllocation]:
    """One variant per scheduled location, each holding the whole policy TIV.

    Bounds the effect of unknown site weights without choosing any. The caller
    runs each variant and reports the minimum and maximum loss across them --
    which is a statement about what is not known, not a loss estimate.
    """
    return [
        allocate_policy(
            policy,
            schedule,
            method=AllocationMethod.CONCENTRATION,
            concentrate_at=_location_reference(location),
        )
        for location in sorted(schedule, key=_location_order)
    ]


# -- exact apportionment ------------------------------------------------------------

def apportion(total: Decimal, weights: Sequence[Fraction]) -> list[Decimal]:
    """Divide an amount by exact weights, to the cent, losing nothing.

    Integer cents and rational weights throughout. The cents that rounding
    leaves over go to the largest fractional remainders, and ties break on
    position, which is why two runs of the same input give the same answer
    down to which site got the odd cent.
    """
    if not weights:
        return []
    if sum(weights) != 1:
        raise AllocationError(
            f"Allocation weights sum to {sum(weights)}, not 1. A policy's value "
            "must be fully accounted for."
        )

    total_cents = _cents(total)
    floors = _floor_cents(total, weights)
    remainder = total_cents - sum(floors)

    fractions = [total_cents * weight - floor for weight, floor in zip(weights, floors, strict=False)]
    ranked = sorted(range(len(weights)), key=lambda index: (-fractions[index], index))
    for index in ranked[:remainder]:
        floors[index] += 1

    return [Decimal(value).scaleb(-2) for value in floors]


def _floor_cents(total: Decimal, weights: Sequence[Fraction]) -> list[int]:
    total_cents = _cents(total)
    return [int(total_cents * weight) for weight in weights]


def _cents(amount: Decimal) -> int:
    cents = amount.scaleb(2)
    if cents != cents.to_integral_value():
        raise AllocationError(
            f"{amount} is not a whole number of cents, so it cannot be allocated exactly."
        )
    return int(cents)


# -- helpers --------------------------------------------------------------------------

def _location_order(location: Mapping[str, Any]) -> tuple[str, int, str]:
    """The deterministic order the brief names for residual cents.

    Numeric references sort numerically -- 2 before 10, which a plain string
    sort gets backwards -- and anything else sorts after them, alphabetically.
    Two schedules that reference their sites differently must each still order
    the same way on every run, because the order decides which site receives a
    residual cent.
    """
    reference = _location_reference(location)
    numeric = int(reference) if reference.isdigit() else None
    return (
        _text(location.get("business_id")),
        numeric if numeric is not None else 2**31,
        "" if numeric is not None else reference,
    )


def _location_reference(location: Mapping[str, Any]) -> str:
    """The location's reference, as text. OED makes it text."""
    return _text(location.get("location_number"))


def _money(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _is_yes(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("yes", "y", "true", "1")


#: The location allocation and the coverage split both need exact
#: apportionment, and there must be exactly one implementation of it.
_apportion = apportion
