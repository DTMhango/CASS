"""The join report: what happens when the two sheets meet.

The brief treats the join as something to report on rather than something to
perform quietly, and the reason is in its acceptance checks. Two business
identifiers carry more than one policy row. Join naively on ``business_id`` and
those businesses' locations are duplicated once per policy, the location count
goes up, and a TIV total computed afterwards is wrong in a way that looks
plausible.

So this module counts first and refuses second. It produces the findings the
importer displays -- fan-out, orphans, count disagreements, repeated
coordinates -- and it never returns a joined row set that could hide any of
them.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any

JOIN_RULE_VERSION = "1.0.0"


class JoinSeverity:
    """Whether a finding stops the import or is reported alongside it."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclasses.dataclass(frozen=True, slots=True)
class JoinFinding:
    code: str
    severity: str
    message: str
    remediation: str = ""
    subjects: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "remediation": self.remediation,
            # Business and policy references only. Section 10 keeps insured,
            # cedent and broker names out of anything that gets logged.
            "subjects": list(self.subjects),
        }


@dataclasses.dataclass(slots=True)
class JoinReport:
    """A deterministic account of how the two sheets relate."""

    rule_version: str
    policy_rows: int
    location_rows: int
    policy_businesses: int
    located_businesses: int
    primary_locations: int
    secondary_locations: int
    unique_location_keys: int
    businesses_with_many_policies: tuple[str, ...]
    orphan_locations: tuple[str, ...]
    duplicate_location_keys: tuple[str, ...]
    shared_coordinates: tuple[tuple[str, ...], ...]
    distinct_coordinates: int
    count_disagreements: tuple[str, ...]
    primary_coordinate_mismatches: tuple[str, ...]
    total_tiv: Decimal
    located_tiv: Decimal
    findings: list[JoinFinding]

    @property
    def blocking(self) -> bool:
        return any(item.severity == JoinSeverity.ERROR for item in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_version": self.rule_version,
            "policy_rows": self.policy_rows,
            "location_rows": self.location_rows,
            "policy_businesses": self.policy_businesses,
            "located_businesses": self.located_businesses,
            "primary_locations": self.primary_locations,
            "secondary_locations": self.secondary_locations,
            "unique_location_keys": self.unique_location_keys,
            "businesses_with_many_policies": list(self.businesses_with_many_policies),
            "orphan_locations": list(self.orphan_locations),
            "duplicate_location_keys": list(self.duplicate_location_keys),
            "shared_coordinates": [list(group) for group in self.shared_coordinates],
            "distinct_coordinates": self.distinct_coordinates,
            "count_disagreements": list(self.count_disagreements),
            "primary_coordinate_mismatches": list(self.primary_coordinate_mismatches),
            "total_tiv": str(self.total_tiv),
            "located_tiv": str(self.located_tiv),
            "blocking": self.blocking,
            "findings": [item.as_dict() for item in self.findings],
        }


def build(
    policies: Sequence[Mapping[str, Any]],
    locations: Sequence[Mapping[str, Any]],
) -> JoinReport:
    """Report how the policy and location sheets relate, without joining them."""
    findings: list[JoinFinding] = []

    policies_by_business: dict[str, list[Mapping[str, Any]]] = {}
    for policy in policies:
        business_id = _text(policy.get("business_id"))
        if business_id:
            policies_by_business.setdefault(business_id, []).append(policy)

    locations_by_business: dict[str, list[Mapping[str, Any]]] = {}
    key_counts: dict[tuple[str, str], int] = {}
    for location in locations:
        business_id = _text(location.get("business_id"))
        number = _text(location.get("location_number"))
        locations_by_business.setdefault(business_id, []).append(location)
        key = (business_id, number)
        key_counts[key] = key_counts.get(key, 0) + 1

    # -- the fan-out risk ---------------------------------------------------
    many_policies = tuple(
        sorted(
            business_id
            for business_id, rows in policies_by_business.items()
            if len(rows) > 1
        )
    )
    if many_policies:
        at_risk = sorted(
            business_id
            for business_id in many_policies
            if business_id in locations_by_business
        )
        findings.append(
            JoinFinding(
                code="many_policies_per_business",
                severity=JoinSeverity.ERROR if at_risk else JoinSeverity.WARNING,
                message=(
                    f"{len(many_policies)} business references carry more than one "
                    "policy row"
                    + (
                        f", and {len(at_risk)} of them have scheduled locations. "
                        "Joining on the business reference alone would repeat those "
                        "locations once per policy."
                        if at_risk
                        else "."
                    )
                ),
                remediation=(
                    "Record a policy-selection or split rule for these businesses "
                    "before importing, so each policy's TIV is allocated once."
                ),
                subjects=tuple(at_risk or many_policies),
            )
        )

    # -- duplicate natural keys ---------------------------------------------
    duplicates = tuple(
        sorted(f"{business}/{number}" for (business, number), count in key_counts.items() if count > 1)
    )
    if duplicates:
        findings.append(
            JoinFinding(
                code="duplicate_location_key",
                severity=JoinSeverity.ERROR,
                message=(
                    f"{len(duplicates)} business and location-number combinations "
                    "appear more than once, so the natural key does not identify a row."
                ),
                remediation="Correct the source location numbering before importing.",
                subjects=duplicates,
            )
        )

    # -- orphans -------------------------------------------------------------
    orphans = tuple(
        sorted(
            business_id
            for business_id in locations_by_business
            if business_id not in policies_by_business
        )
    )
    if orphans:
        findings.append(
            JoinFinding(
                code="orphan_location",
                severity=JoinSeverity.WARNING,
                message=(
                    f"{len(orphans)} business references appear in the location sheet "
                    "but not in the policy sheet, so their locations carry no value."
                ),
                remediation="Confirm whether these businesses belong in this extract.",
                subjects=orphans,
            )
        )

    # -- the scheduled count each policy declares ----------------------------
    disagreements: list[str] = []
    for business_id, rows in policies_by_business.items():
        scheduled = len(locations_by_business.get(business_id, ()))
        for policy in rows:
            declared = policy.get("risk_location_count")
            if declared is None:
                continue
            if int(declared) != scheduled:
                disagreements.append(
                    f"{_text(policy.get('policy_id'))}: declares {int(declared)}, "
                    f"schedule holds {scheduled}"
                )
    if disagreements:
        findings.append(
            JoinFinding(
                code="location_count_disagreement",
                severity=JoinSeverity.ERROR,
                message=(
                    f"{len(disagreements)} policies declare a scheduled-location count "
                    "that the location sheet does not match."
                ),
                remediation=(
                    "Reconcile the source before importing: a policy whose schedule is "
                    "incomplete cannot have its TIV allocated across locations."
                ),
                subjects=tuple(sorted(disagreements)),
            )
        )

    # -- primary coordinates ---------------------------------------------------
    mismatches: list[str] = []
    for location in locations:
        if not _is_yes(location.get("primary_location")):
            continue
        business_id = _text(location.get("business_id"))
        for policy in policies_by_business.get(business_id, ()):
            if policy.get("risk_latitude") is None:
                continue
            if not (
                _same(policy.get("risk_latitude"), location.get("latitude"))
                and _same(policy.get("risk_longitude"), location.get("longitude"))
            ):
                mismatches.append(_text(policy.get("policy_id")))
    if mismatches:
        findings.append(
            JoinFinding(
                code="primary_coordinate_mismatch",
                severity=JoinSeverity.ERROR,
                message=(
                    f"{len(mismatches)} policies carry a primary coordinate that differs "
                    "from the primary row in the location sheet."
                ),
                remediation=(
                    "The two sheets disagree about where the risk is. Resolve which is "
                    "correct at source rather than choosing one here."
                ),
                subjects=tuple(sorted(set(mismatches))),
            )
        )

    # -- shared coordinates ---------------------------------------------------
    by_coordinate: dict[tuple[str, str], list[str]] = {}
    for location in locations:
        pair = (_text(location.get("latitude")), _text(location.get("longitude")))
        by_coordinate.setdefault(pair, []).append(
            f"{_text(location.get('business_id'))}/{_text(location.get('location_number'))}"
        )
    shared = tuple(
        tuple(sorted(group)) for group in by_coordinate.values() if len(group) > 1
    )
    if shared:
        findings.append(
            JoinFinding(
                code="shared_coordinate",
                severity=JoinSeverity.INFO,
                message=(
                    f"{len(shared)} coordinate pairs are shared by more than one "
                    "location. These may be genuine shared sites, group risks or "
                    "geocoding centroids."
                ),
                remediation=(
                    "Review them. Do not deduplicate on coordinates: separate business "
                    "and policy identities must be retained whatever the geometry says."
                ),
                subjects=tuple(sorted("; ".join(group) for group in shared))[:50],
            )
        )

    located_businesses = set(locations_by_business) & set(policies_by_business)
    primary = sum(1 for row in locations if _is_yes(row.get("primary_location")))

    return JoinReport(
        rule_version=JOIN_RULE_VERSION,
        policy_rows=len(policies),
        location_rows=len(locations),
        policy_businesses=len(policies_by_business),
        located_businesses=len(located_businesses),
        primary_locations=primary,
        secondary_locations=len(locations) - primary,
        unique_location_keys=len(key_counts),
        businesses_with_many_policies=many_policies,
        orphan_locations=orphans,
        duplicate_location_keys=duplicates,
        shared_coordinates=shared,
        distinct_coordinates=len(by_coordinate),
        count_disagreements=tuple(sorted(disagreements)),
        primary_coordinate_mismatches=tuple(sorted(set(mismatches))),
        total_tiv=_sum_tiv(policies),
        located_tiv=_sum_tiv(
            policy
            for policy in policies
            if _text(policy.get("business_id")) in located_businesses
        ),
        findings=findings,
    )


def _sum_tiv(policies: Iterable[Mapping[str, Any]]) -> Decimal:
    total = Decimal(0)
    for policy in policies:
        value = policy.get("gross_limit")
        if value is not None:
            total += Decimal(str(value))
    return total


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value).strip()


def _is_yes(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("yes", "y", "true", "1")


def _same(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    return Decimal(str(left)) == Decimal(str(right))
