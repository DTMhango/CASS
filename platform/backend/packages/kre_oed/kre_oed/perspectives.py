"""Which loss perspectives the supplied data actually supports.

Build plan section 8 is explicit: the location file alone is sufficient only
for a ground-up run, the account file is required for insured loss, and
reinsurance information and scope are required for reinsurance. Empty
placeholder financial files will not be generated to imply a perspective the
source data does not support.

This module turns that rule into a decision the analysis builder can enforce,
so a user cannot request a ceded result from a portfolio that has no treaty
data and receive a silently zero answer.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Sequence

from .validation import PortfolioFiles


class Perspective(enum.StrEnum):
    """The analysis perspectives of build plan section 8."""

    GROUND_UP = "ground_up"
    INSURED = "insured"
    REINSURANCE = "reinsurance"

    @property
    def label(self) -> str:
        return {
            Perspective.GROUND_UP: "Ground-up loss",
            Perspective.INSURED: "Insured loss",
            Perspective.REINSURANCE: "Reinsurance loss",
        }[self]


@dataclasses.dataclass(frozen=True, slots=True)
class PerspectiveAvailability:
    """Whether one perspective can be run, and why not when it cannot."""

    perspective: Perspective
    available: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "perspective": str(self.perspective),
            "label": self.perspective.label,
            "available": self.available,
            "reason": self.reason,
        }


def available_perspectives(files: PortfolioFiles) -> list[PerspectiveAvailability]:
    """Report which perspectives the supplied files support."""
    has_locations = bool(files.location.rows)
    has_accounts = files.account is not None and bool(files.account.rows)
    has_reins_info = files.reins_info is not None and bool(files.reins_info.rows)
    has_reins_scope = files.reins_scope is not None and bool(files.reins_scope.rows)

    results = [
        PerspectiveAvailability(
            Perspective.GROUND_UP,
            has_locations,
            "Location records are present."
            if has_locations
            else "No location records were supplied.",
        ),
        PerspectiveAvailability(
            Perspective.INSURED,
            has_accounts,
            "Account and policy terms are present."
            if has_accounts
            else "An account file is required for insured loss; none was supplied.",
        ),
    ]

    if has_reins_info and has_reins_scope:
        reinsurance_reason = "Reinsurance contracts and scope are present."
    elif has_reins_info:
        reinsurance_reason = (
            "Reinsurance contracts were supplied without a scope file, so the "
            "risks each contract covers are undefined."
        )
    elif has_reins_scope:
        reinsurance_reason = (
            "A reinsurance scope file was supplied without contract terms."
        )
    else:
        reinsurance_reason = "No reinsurance contracts were supplied."

    reinsurance_available = has_reins_info and has_reins_scope and has_accounts
    if reinsurance_available is False and has_reins_info and has_reins_scope and not has_accounts:
        reinsurance_reason = (
            "Reinsurance applies to insured loss, so an account file is also required."
        )

    results.append(
        PerspectiveAvailability(
            Perspective.REINSURANCE, reinsurance_available, reinsurance_reason
        )
    )
    return results


def highest_available(files: PortfolioFiles) -> Perspective:
    """Return the most complete perspective the data supports."""
    availability = {item.perspective: item.available for item in available_perspectives(files)}
    for perspective in (Perspective.REINSURANCE, Perspective.INSURED, Perspective.GROUND_UP):
        if availability.get(perspective):
            return perspective
    raise UnsupportedPerspective("the portfolio supports no loss perspective")


def require(files: PortfolioFiles, requested: Sequence[Perspective]) -> None:
    """Raise unless every requested perspective is supported by the data."""
    availability = {item.perspective: item for item in available_perspectives(files)}
    unsupported = [
        availability[Perspective(item)]
        for item in requested
        if not availability[Perspective(item)].available
    ]
    if unsupported:
        detail = "; ".join(
            f"{item.perspective.label}: {item.reason}" for item in unsupported
        )
        raise UnsupportedPerspective(detail)


class UnsupportedPerspective(Exception):
    """Raised when a requested perspective is not supported by the source data."""
