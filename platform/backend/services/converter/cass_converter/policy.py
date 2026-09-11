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
import enum
from typing import Any


class PolicyNotApproved(Exception):
    """Raised when a conversion is attempted under an unapproved policy."""


class EventIdentity(enum.StrEnum):
    """Candidate mappings from OpenQuake output to an Oasis event.

    Section 6 describes two defensible designs and requires a formal
    specification with worked examples before code depends on one.
    """

    OCCURRENCE_PER_EVENT = "occurrence_per_event"
    """Each simulated occurrence becomes a separate Oasis event."""

    RUPTURE_BINNED = "rupture_binned"
    """Repeated ground-motion samples are aggregated into intensity-bin
    probabilities for a rupture-level event."""

    UNDECIDED = "undecided"
    """No policy has been approved. This is the default state, and it is not
    runnable."""


class IMTRepresentation(enum.StrEnum):
    """Candidate multi-IMT representations, from the section 6 gate."""

    CORRELATED_CHANNELS = "correlated_channels"
    """Correlated Oasis sub-peril or intensity-measure channels that retain a
    common event identity and route each vulnerability class to its IMT."""

    CUSTOM_GUL = "custom_gul"
    """A CASS ground-up-loss component consuming multi-IMT OpenQuake output
    directly, preserving the Oasis financial-module boundary."""

    OQ_LOSS_HANDOFF = "oq_loss_handoff"
    """OpenQuake damage or ground-up loss, then an event-loss interface to the
    Oasis financial calculations. Retained as a fallback."""

    COMMON_IMT = "common_imt"
    """Convert every function to one intensity measure.

    Explicitly not an accepted default. Section 6: this "would require a
    separate scientific derivation, validation and approval". It is listed so
    that choosing it is a recorded decision rather than an accident.
    """

    UNDECIDED = "undecided"


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

    @property
    def is_runnable(self) -> bool:
        return not self.blockers()

    def require_runnable(self) -> None:
        """Refuse to proceed under an unapproved policy."""
        problems = self.blockers()
        if problems:
            raise PolicyNotApproved(
                "This conversion policy has not been approved:\n- "
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
        }


#: The policy a fresh installation starts with. Named so that a conversion
#: attempt reads as a deliberate refusal rather than a missing configuration.
UNAPPROVED = ConversionPolicy()
