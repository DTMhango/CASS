# 16. A class spanning intensity measures is carried as one sub-peril item per measure

Status: Accepted
Date: 2026-09-14
Amends: [ADR 8](0008-intensity-measures-as-area-peril-channels.md)

## Context

GEM ties each building type's damage function to the one intensity measure it
responds to, and height decides which: a low stiff building to PGA or SA(0.3),
a tall one to SA(0.6) or SA(1.0). A schedule that does not state a building's
height reaches GEM candidates of every height, so its class spans measures. ADR
8 carried every class resolving to one measure as a correlated channel and
refused the rest.

Measured on 14 September 2026, that refusal was not a corner case. The 30 June
benchmark book states no storey count for any risk, and under the default
occupancy assumption all of its value reached classes spanning all four
measures: under ADR 8 the book could not be modelled at all. Withholding the
storey counts of the Jakarta–Bandung test book put 7.2% of its value there.

Item 21 set the rule before measuring: the candidate is adopted if its ratio to
the OpenQuake reference falls within the band the single-measure classes already
show and its insured loss reconciles. The candidates and the measurements are in
[the decision studies](../research/decision-studies.md).

## Decision

Where a vulnerability set is built as correlated channels, a class spanning
measures reaches the engine as one item per measure.

- **Each measure is its own earthquake sub-peril:** QEQ for PGA, QFF for
  SA(0.3), QLS for SA(0.6) and QTS for SA(1.0). oasislmf 2.5.7 identifies an
  item by location, peril, coverage and building, and of two rows sharing all
  four it keeps one. The codes name shaking channels here; CASS models no fire
  following, landslide or tsunami.
- **Each item is answered by its channel's function with the damage scaled by
  the channel's share of the class**, written by the build one million
  identifiers above the channel (`WEIGHTED_CHANNEL_OFFSET`). The engine prices
  every item at the coverage's whole value and caps the coverage's total at it,
  so the share cannot go on the value. Each assumption set scales by its own
  shares.
- **The financial terms the engine receives are scoped to all earthquake
  perils** (`QQ1`) in every column oasislmf filters terms by: `LocPeril`,
  `CondPeril`, `PolPeril`, `AccPeril` and `ReinsPeril`. A term applies only to
  the perils it names. The run keeps that copy; the published files keep what
  was reported, and what a location covers is not changed.
- The package refuses a set that lacks the scaled functions, and records the
  sub-perils in its manifest.

Correlated channels is now the representation a vulnerability set is built
under unless its specification names another. A set may still be built
undecided, and then refuses such classes as ADR 8 did.

## Evidence

On the live engine, one building of unknown height was run over all 27,313
events with 20 samples, split and unsplit:

- Its mean ground-up loss matched the weighted sum of its four single-measure
  items to under a cent per event (141,827.56 against 141,827.47 in total).
- Its insured loss matched its deductible and limit in every one of 119
  event-samples. With the terms left on shake, 89 of 114 were wrong and the
  insured loss was 40% too high.
- No coverage's sampled loss exceeded its value, and its items shared one
  damage group, so their draws move together.

Against the OpenQuake reference on one set of ground-motion fields:

| Book | Value through split items | Average annual loss ratio | Return-period ratios |
| --- | --- | --- | --- |
| Storeys stated | 0% | 0.904 | 0.70–1.26 |
| Storeys withheld | 7.2% | 1.032 | 0.91–1.30 |
| Commercial, construction and height unknown | 100% | 0.968 | 0.75–1.38 |

The book carried entirely through split items sits inside the spread the
single-measure book already shows, and the storeys-stated book reproduced its
earlier ratio exactly, so the package change left single-measure classes
untouched.

## Alternatives considered

**Refusal (ADR 8).** Honest, and it left a book without storey counts
unmodelled.

**Aggregate vulnerability.** oasislmf blends weighted sub-functions per area
peril, but every part reads the same area peril's intensity, so it cannot blend
measures.

**One common intensity measure.** Still rejected: it needs its own derivation.

**A CASS ground-up component, or OpenQuake computing ground-up loss for Oasis's
financial terms.** Not needed now that the Oasis-native form is exact. Costed
only if this representation is shown biased.

## Consequences

A book whose schedules state no heights can be modelled. Collecting storeys
still matters: it narrows the mixture to the buildings the schedule describes.

A location's damage under this representation is the weighted sum of its
measures' damage, not a draw from one of GEM's candidates. The mean is exact;
the spread for a single building is narrower than the mixture's. The OpenQuake
reference splits value the same way, so it does not measure that difference.

Sets built before this record carry no scaled functions and must be built
again to be carried this way; the package says so rather than falling back.

The reference comparison splits a coverage by the mapping's channel weights,
which are the baseline's. A run under another assumption set is compared
against the baseline's split.

Three books on one hazard realisation and 64 locations are what the decision
rests on. Tail ratios rest on few events.

## Revisit if

The reference comparison on a larger or national book puts classes carried this
way outside the single-measure band; oasislmf changes how it keys items or
applies peril filters; or CASS begins to model an earthquake sub-peril it now
uses as a channel code.
