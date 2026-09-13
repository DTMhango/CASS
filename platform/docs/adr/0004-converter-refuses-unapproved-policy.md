# 4. The converter refuses to run under an unapproved policy

Status: Accepted
Date: 2026-09-11

## Context

Two scientific decisions are open in section 16 of the build plan, and both are
listed in the section 15 risk register with named consequences.

Event representation controls annual frequency, uncertainty, correlation and
footprint probabilities. The plan requires a formal study before converter
build, because getting it wrong produces an invalid annual frequency — and an
AAL computed from a wrong occurrence table is wrong by exactly the ratio of the
two, with nothing downstream to notice.

Multi-IMT representation determines whether the GEM vulnerability functions can
be represented faithfully in Oasis at all. Section 6 states plainly that
converting all functions to one common intensity measure "is not an accepted
default" and would require its own scientific derivation, validation and
approval.

Both produce plausible-looking numbers when done wrong. That is what makes them
dangerous: a converter that quietly picked a defensible default would produce
loss estimates that pass every software test and are scientifically invalid.

## Decision

`ConversionPolicy` defaults to `UNDECIDED` for both choices, and
`require_runnable()` raises `PolicyNotApproved` rather than converting.

A runnable policy must name an approved event identity, an approved multi-IMT
representation, the IMTs it will emit, an investigation time, and the
governance approval reference that cleared the choices. `COMMON_IMT` is
rejected even when selected explicitly, so choosing it is a recorded decision
that must first change this rule.

The set of self-approving policies is deliberately empty.

## Alternatives considered

**Ship a sensible default and document the caveat.** The usual approach, and
wrong here. A caveat in a docstring does not travel with an AAL into a pricing
decision.

**Leave the converter unwritten until the studies finish.** Loses the
opportunity to build and test the machinery that any of the candidate policies
will need: deterministic identifiers, intensity binning, streaming footprint
accumulation, frequency reconciliation. Those are policy-independent, and
building them now is what makes the eventual policy cheap to implement.

**Gate at the API instead of in the converter.** A gate one layer above can be
bypassed by a management command, a worker, or a future caller. The refusal
belongs where the science happens.

## Consequences

The converter cannot produce a model package today. That is the correct state:
neither study has reported.

*Amended by [ADR 8](0008-intensity-measures-as-area-peril-channels.md), 2026-09-13.*
A package is now built under a converter-candidate approval decided by someone
other than the requester, naming occurrence per event and correlated
area-peril channels. Refusing an unapproved policy is unchanged, and
`COMMON_IMT` is still rejected.

When a study reports, the change is data — an approved policy value and an
approval reference — not a code change to the conversion path. The machinery is
already tested against it.

An operator who tries to convert gets a message naming exactly which decisions
are outstanding, rather than a silent result.

## Revisit if

Both studies complete and are approved under the section 10 converter-candidate
gate. At that point `SELF_APPROVING` may gain the approved combination, or the
approval reference requirement may be satisfied by the registry rather than by
the caller.
