# 17. An Oasis event is a simulated occurrence, not a rupture

Status: Accepted
Date: 2026-09-14
Relates to: [ADR 4](0004-converter-refuses-unapproved-policy.md),
[ADR 12](0012-national-classical-model-run-event-based.md)

## Context

OpenQuake simulates an earthquake rupture's shaking as often as the rupture
occurs in the simulated catalogue, and the same rupture shakes the ground
differently each time. CASS can make each simulated occurrence its own Oasis
event, keeping that occurrence's pattern of shaking across sites, or it can
group a rupture's occurrences into one event whose footprint holds, for each
cell, the spread of intensities they showed.

Build plan section 6 named both as defensible and asked for a specification with
worked examples before code depended on either. ADR 4 made every package record
the identity it was built under; occurrence per event has been in use since.
Item 26 is the measurement that decides it. The sign-off was dropped on 13
September: the study stays, as research CASS does.

## Decision

An Oasis event is one simulated occurrence. Rupture binning is not built.

## Evidence

Measured on 14 September 2026 against the live PuSGeN 2024 event set for the
Jakarta–Bandung region, and the Jakarta–Bandung test book of 64 locations.

**The catalogue decides most of it.** The event set holds 27,313 occurrences of
26,651 distinct ruptures over 1,000 simulated years. 26,034 ruptures — 97.7% —
occur exactly once, so for them a rupture-binned footprint is the occurrence's
own footprint and the two representations are the same table. Only 617 ruptures,
covering 1,279 occurrences (4.7%), have anything to pool.

**What pooling those changed.** The deployed package was rebuilt with each
rupture's occurrences merged into one event, the occurrence table pointing every
occurrence at it, and the same book run through both packages with the same
settings:

| | Occurrence per event | Rupture-binned |
| --- | --- | --- |
| Average annual loss, analytical | 570,652 | 570,854 (+0.04%) |
| Standard deviation of annual loss | 2,840,652 | 2,781,092 (−2.1%) |
| 100-year occurrence exceedance loss | 23,121,316 | 21,685,938 (−6.2%) |
| 25-, 200- and 250-year | — | within 1.2% |
| 500- and 1,000-year | 49,141,712 and 51,811,376 | identical |
| Footprint | 153.1 MB over 21,772 events | 152.9 MB over 21,274 events |
| Loss run | 17s | 17s |

So on this hazard the mean is unchanged, the tail thins slightly where pooling
applies, and nothing is saved in size or time.

**Two reasons beyond the numbers.** The reference calculation computes loss
occurrence by occurrence, so occurrence per event is the representation the
comparison in ADR 16 was measured under and the one it can measure at all.
And a footprint cannot say "this occurrence did not shake this cell": pooling
has to condition on the cell being reached, which overstates a cell that only
some of a rupture's occurrences touch. That is an approximation occurrence per
event does not need.

## Alternatives considered

**Rupture-binned events.** Measured above. It would be worth revisiting on a
catalogue long enough for a rupture to recur often, where one event per rupture
would be materially smaller than one per occurrence.

## Consequences

Each Oasis event carries one occurrence's pattern of shaking across sites, which
is what makes an aggregate loss across a portfolio meaningful.

The event set's size is the number of occurrences. At 1,000 years and this
source model that is 27,313 events and a 153 MB footprint; a longer catalogue
grows it proportionally, which is what item 23 measures.

Every package still records the event identity it was built under, so a result
can be read years later without assuming this.

## Revisit if

A catalogue is simulated long enough that ruptures recur many times — the ratio
of occurrences to ruptures here is 1.02, and binning only begins to pay above
it; the engine gains a way to express an occurrence that does not reach a cell;
or the reference calculation moves to rupture-level losses.
