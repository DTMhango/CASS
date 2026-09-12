"""Event identity and intensity-measure policy.

This module is where the two open scientific decisions of build plan section 16
live, and it is written to make them impossible to skip.

Section 15 names both as risks with named consequences:

  "Event semantics are defined too late" -> converter rework and invalid
  annual frequency. Mitigation: complete and approve the event identity study
  before production converter development.

  "Multi-IMT vulnerability demand is forced into one intensity channel" ->
  biased or scientifically invalid loss results. Mitigation: complete the
  multi-IMT prototype and OpenQuake reference comparison before converter
  architecture approval.

So the converter does not ship a default for either. A conversion must name an
approved policy, and an unapproved one refuses to run rather than quietly
picking something defensible-looking. A silent default here would be the most
expensive kind of bug: one that produces plausible numbers.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from cass_core.policy import EventIdentity, IMTRepresentation

#: Re-exported so a reader of this module sees the whole vocabulary it
#: gates on. The definitions live in ``cass_core`` because the keys service
#: reads the multi-IMT choice too.
__all__ = [
    "SELF_APPROVING",
    "UNAPPROVED",
    "ConversionPolicy",
    "EventIdentity",
    "IMTRepresentation",
    "PolicyNotApproved",
]


class PolicyNotApproved(Exception):
    """Raised when a conversion is attempted under an unapproved policy."""


#: Policies that may be used without a specific scientific approval reference.
#: The set is empty, and that is the point: section 16 lists both decisions as
#: open, so nothing is pre-approved.
SELF_APPROVING: frozenset[str] = frozenset()


@dataclasses.dataclass(frozen=True, slots=True)
class ConversionPolicy:
    """The approved scientific choices a conversion runs under.

    ``approval_reference`` is the identifier of the governance approval that
    cleared the choice -- section 10 makes the converter candidate a gate with
    a named approver, so the converter records which approval it relied on.
    """

    event_identity: EventIdentity = EventIdentity.UNDECIDED
    imt_representation: IMTRepresentation = IMTRepresentation.UNDECIDED
    approval_reference: str = ""
    #: The IMTs this conversion will emit. Section 16 confirms SA-first:
    #: SA(0.3), then SA(0.6) and SA(1.0), with PGA deferred.
    imts: tuple[str, ...] = ()
    investigation_time: float | None = None
    #: Recorded so a reproducible conversion can be repeated exactly.
    random_seed: int | None = None
    notes: str = ""

    def blockers(self) -> list[str]:
        """Reasons this policy may not be used, in words a modeller can act on."""
        problems: list[str] = []

        if self.event_identity is EventIdentity.UNDECIDED:
            problems.append(
                "No event identity specification has been approved. Complete the "
                "event-semantics study before running a production conversion."
            )
        if self.imt_representation is IMTRepresentation.UNDECIDED:
            problems.append(
                "No multi-IMT representation has been approved. Complete the "
                "prototype comparison and the OpenQuake reference calculation first."
            )
        if self.imt_representation is IMTRepresentation.COMMON_IMT:
            problems.append(
                "Converting every vulnerability function to one common intensity "
                "measure is not an accepted default. It needs its own scientific "
                "derivation, validation and approval."
            )
        if not self.imts:
            problems.append("No intensity measures were declared for this conversion.")
        if not self.approval_reference and str(self.event_identity) not in SELF_APPROVING:
            problems.append(
                "No governance approval reference was supplied for these choices."
            )
        if self.investigation_time is None or self.investigation_time <= 0:
            problems.append(
                "No investigation time was recorded, so annual frequency cannot be "
                "reconciled after conversion."
            )
        return problems

    def vulnerability_blockers(self) -> list[str]:
        """Reasons a *vulnerability* build in particular may not proceed.

        A narrower gate than the full one, because a vulnerability set is not a
        conversion of hazard. Which OpenQuake occurrence becomes which Oasis
        event, and over what investigation time, says nothing about how a
        building responds to shaking -- so demanding those approvals before a
        damage table can be built would block work that does not depend on
        them, and blocking work for no reason is how a gate stops being taken
        seriously.

        What does bear on it: the intensity measures must be declared, because
        a function is built against one. The multi-IMT representation
        deliberately does *not* appear. A build under an undecided one is
        expected and useful -- it is how the classes that need the decision get
        counted -- and the refusal happens later, when a risk actually reaches
        such a class and the keys service has to answer for it.
        """
        problems: list[str] = []
        if not self.imts:
            problems.append("No intensity measures were declared for this conversion.")
        if self.imt_representation is IMTRepresentation.COMMON_IMT:
            problems.append(
                "Converting every vulnerability function to one common intensity "
                "measure is not an accepted default. It needs its own scientific "
                "derivation, validation and approval."
            )
        return problems

    @property
    def is_runnable(self) -> bool:
        return not self.blockers()

    @property
    def builds_vulnerability(self) -> bool:
        return not self.vulnerability_blockers()

    def require_runnable(self) -> None:
        """Refuse to proceed under an unapproved policy."""
        problems = self.blockers()
        if problems:
            raise PolicyNotApproved(
                "This conversion policy has not been approved:\n- "
                + "\n- ".join(problems)
            )

    def require_vulnerability_build(self) -> None:
        """Refuse a vulnerability build the policy does not support."""
        problems = self.vulnerability_blockers()
        if problems:
            raise PolicyNotApproved(
                "This policy cannot build a vulnerability set:\n- "
                + "\n- ".join(problems)
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_identity": str(self.event_identity),
            "imt_representation": str(self.imt_representation),
            "approval_reference": self.approval_reference,
            "imts": list(self.imts),
            "investigation_time": self.investigation_time,
            "random_seed": self.random_seed,
            "notes": self.notes,
            "runnable": self.is_runnable,
            "blockers": self.blockers(),
            "builds_vulnerability": self.builds_vulnerability,
            "vulnerability_blockers": self.vulnerability_blockers(),
        }


#: The policy a fresh installation starts with. Named so that a conversion
#: attempt reads as a deliberate refusal rather than a missing configuration.
UNAPPROVED = ConversionPolicy()
