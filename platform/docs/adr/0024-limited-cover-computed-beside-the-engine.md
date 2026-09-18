# 24. Limited reinsurance cover is computed by CASS, beside the engine's

Status: Accepted
Date: 2026-09-18

## Context

The Oasis financial module applies a catastrophe excess of loss to each event on
its own. Nothing carries from one event to the next, so a layer pays in full on
every event of a year: unlimited reinstatements, free of charge. OED defines
`Reinstatement`, `ReinstatementCharge`, `ReinsPremium` and the aggregate terms,
but Oasis's own list of supported financial terms marks every one of them
unsupported, oasislmf 2.5.7 (the latest release) reads none of them, and the
2023 roadmap item for reinstatements has not shipped.

Klapton Re's retrocession is catastrophe layers with a stated number of
reinstatements, a rate for each (RenRe 2026 layer 1 charges 125%), and a premium
they are charged on: the minimum and deposit premium, deducted from the recovery
it restores. An analysis that ignores them overstates recoveries in any year
with more than one large event.

## Decision

- **An analysis chooses its reinsurance cover.** *As the engine applies it* is
  the default and changes nothing. *Limited by contract terms* also computes the
  net loss with each layer's reinstatements and premiums, published as a second
  result (`ri_terms`) beside the engine's.
- **The engine supplies the losses; CASS applies the limits.** Each location is
  marked, on the engine's copy only, with its cover class — the set of contracts
  whose scope reaches it — in `LocUserDef5`, and the run asks for the insured
  sample period loss table per class. CASS walks each sample's simulated years
  event by event, capping each layer at `(1 + reinstatements) × limit` a year and
  charging `premium × rate × amount reinstated / limit`, the premium taken off
  the recovery it restores. The ceded and placed shares scale both.
- **Checked against the engine every time.** The same arithmetic without the
  annual limit must reproduce the engine's net of reinsurance, and the insured
  loss it adds up must reproduce the engine's insured loss, within 0.5%. Curves
  use the engine's mean-sample basis and return-period interpolation, verified
  against `oasislmf.pytools.lec`'s own `write_ept` on 200 random catalogues. A
  result that fails the check is published with the failure stated.
- **The terms are those the engine was given**, recorded on the run when the
  files are handed over, after any currency conversion.
- **Narrow on purpose.** Programmes of catastrophe excess of loss only, scoped by
  account, location or the whole portfolio; overlapping scopes at different
  inuring priorities are refused. A layer stating no number of reinstatements is
  applied as the engine applies it, and the result lists it.

## Alternatives considered

**Wait for Oasis.** Nothing on its release record suggests a date.

**Aggregate terms in the engine's own files.** The engine ignores them, so the
number would not change and would look as if it had.

**Apply the layers to the mean event loss.** Much smaller output, but a layer is
not linear, and the check against the engine — which applies layers per sample —
would fail for a reason unrelated to reinstatements.

## Consequences

A limited-cover run adds the sample period loss table for one summary per cover
class to its output: roughly one row per event occurrence with loss, per class,
per sample. The analysis builder says so.

The catalogue places every event on the first of January of its year, so events
are applied in event-number order within a year. That can change a year's
largest single loss once an aggregate is exhausted, never its total.

Warranties on the event cover, such as RenRe's requirement that two or more
risks be involved in a loss occurrence, are not modelled.

## Revisit if

Oasis applies reinstatements itself; programmes with quota share or surplus
share inuring before a catastrophe layer need limited cover; or the catalogue
gains occurrence dates.
