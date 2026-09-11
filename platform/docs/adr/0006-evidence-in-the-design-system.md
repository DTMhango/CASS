# 6. Evidence class is a design system primitive

Status: Accepted
Date: 2026-09-11

## Context

Section 3 requires assumed values to be visually distinguishable from reported
ones, and an analyst to be able to inspect the rule, evidence and confidence
behind every material inference. Section 8 establishes the evidence hierarchy:
reported, derived, corroborated, prior, override.

Section 15 names the risk this addresses from two directions: GEM national
stock being treated as the insured portfolio, producing false precision; and
assumed attributes overwriting reported data, losing lineage.

The backend enforcement is straightforward — an assumption cannot displace a
reported value. The interface half is easy to get wrong, because a value that
looks like every other value reads as a fact.

## Decision

`EvidenceValue` is a design system component, and evidence tokens sit in the
token file beside the brand colours. Every screen that displays a
model-required attribute uses it; none styles a value itself.

The distinction is carried three ways, not one: a tint, a rule under the value,
and a single-letter mark. Tint alone fails in greyscale and for colour-blind
readers, which section 3 rules out by requiring non-colour status cues. A
legend component documents the marks so they are a stated convention.

The source, assumption set version, confidence and any superseded value are
attached as accessible text, not only as a tooltip.

## Alternatives considered

**A footnote or a separate provenance panel.** Keeps the table clean, and
separates the number from its basis at exactly the moment a reader is forming a
judgement about it.

**Colour alone.** Fails accessibility, and fails on a printed exposure review,
which is how a lot of this work is actually checked.

**Leave it to each screen.** Guarantees drift. The first screen that forgets is
the one where a prior gets read as a survey result.

## Consequences

A prior cannot be displayed as a fact without deliberately bypassing the
component. The distinction survives greyscale printing and screen readers.

The cost is visual noise in dense tables where most values are reported. The
marks are deliberately small and the observed style is quiet, so the noise
falls on the assumed values, which is where it belongs.

## Revisit if

Analysts report that the marks interfere with reading a mostly-reported
portfolio. A per-table toggle that defaults to showing them would be the
compromise; defaulting to hiding them would not.
