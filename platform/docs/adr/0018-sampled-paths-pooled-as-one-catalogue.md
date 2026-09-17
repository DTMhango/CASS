# 18. Hazard is sampled along several logic-tree paths, pooled as one catalogue

Status: Accepted, amended by [ADR 21](0021-ten-thousand-simulated-years.md)
Date: 2026-09-14
Amends: [ADR 12](0012-national-classical-model-run-event-based.md)

## Context

A national hazard model is not one answer but a logic tree of alternative
scientific views, each branch carrying a weight, and the hazard it publishes is
their weighted mean. PuSGeN 2024 enumerates to about 1,080 realisations.

An Oasis footprint is one event set, so a conversion has to decide which
realisations to simulate and how to weigh their events. ADR 12 sampled one path
and CASS refused anything else: several realisations, each with its own weight,
could not be flattened into one occurrence table without a rule nobody had
decided. Item 22 is the measurement that decides it.

## Decision

A hazard run samples several paths through the logic tree, and their events are
pooled into one catalogue in which every event counts once.

- **Sampling, never enumeration.** OpenQuake draws each path with probability
  equal to its weight, so a pooled catalogue already carries the model's
  weighting. An enumerated tree hands over branches of unequal weight and stays
  refused: pooling those as equals would treat a low-weight branch as the mean.
  The converter checks the realisations' weights and refuses them where they
  differ.
- **A path is more simulated years, not an alternative history of the same
  ones.** The engine numbers years across the whole pooled catalogue — the first
  path's years, then the second's — and states the product as its own effective
  time. CASS's effective time is now investigation time × event sets per path ×
  paths, which is what every annual rate is divided by.
- **A run samples twenty paths of one event set each**, so the span stays the
  thousand simulated years it was, drawn across twenty views instead of one.
  The number is a parameter of the hazard run, and a run that samples one says
  so as a warning rather than silently.

## Evidence

Measured on 14 September 2026 with the published PuSGeN 2024 model over 12 sites
in the Jakarta–Bandung region, against its own weighted mean: a classical
calculation enumerating all 1,080 realisations, which took 79 seconds. Each
event-based arm covered the same 2,000 simulated years.

Median ratio of the sampled hazard to that weighted mean, over 36 site-measures:

| Arm | 100-year | 475-year | 1,000-year | Run |
| --- | --- | --- | --- | --- |
| 1 path, seed 23 | 1.198 | 1.193 | 1.156 | 150s |
| 1 path, seed 101 | 0.836 | 0.826 | 0.844 | 153s |
| 1 path, seed 202 | 1.237 | 1.158 | 1.069 | 139s |
| 1 path, seed 303 | 0.988 | 1.008 | 1.042 | 131s |
| 5 paths | 1.031 | 0.982 | 1.049 | 164s |
| 20 paths, seed 23 | 0.952 | 0.943 | 0.974 | 167s |
| 20 paths, seed 101 | 0.927 | 0.895 | 0.908 | 188s |

So a single path landed between 0.84 and 1.24 of the model's own answer
depending on which path was drawn; twenty paths landed within 8% on both draws.
The spread across sites narrows too: at the 100-year return period a single
path ranged 0.56 to 1.43 across site-measures, twenty paths 0.74 to 1.15.

Sampling more paths also fills the tail. With one path, the 1,000-year return
period was undefined at 9 to 15 of the 36 site-measures, because that path's
catalogue reached no such loss; with twenty it was defined at 33 to 36.

And it is free. Every arm covered the same simulated years and took between 131
and 188 seconds: how many paths those years are drawn across does not change the
work.

## Alternatives considered

**One sampled path (ADR 12).** Measured above: one view, and which view is luck.

**Full enumeration with weights.** Exact, about 1,080 times the compute, and it
needs weighted events an Oasis occurrence table cannot express.

**The single highest-weight path.** Biased towards one view by construction, and
not the mean of any distribution.

## Consequences

A hazard set's events come from several paths, and its record says how many.
Nothing about the occurrence table changes: the engine's own year numbering
already spans the pooled catalogue, so each event still falls in one period.

The Indonesian hazard has been rebuilt on this decision: `id-hazard-2024.0.0-b88ea45f`
covers the same thousand simulated years as one fifty-year event set along each
of twenty sampled paths, 27,001 events over 962 cells with nothing clipped.

What that changed, measured on the same books with everything but the hazard
held fixed, is mostly the tail. Ground-up average annual loss moved between
−0.3% and +6.8%; the 1,000-year loss rose between 51% and 93%. The footprints
say why: the body of the two catalogues is the same distribution — mean peak
PGA bin 11.57 against 11.60 — while the strongest shaking anywhere rises from
bin 42 to bin 50, because among twenty draws of the ground-motion tree are
branches no single draw produced. Those far return periods are the worst one or
two years of a thousand-year catalogue and are not precisely estimated by either
run; the direction is firm, the factor is one draw's worth of evidence.

Results produced before that rebuild sit on the single-path hazard. They are not
wrong, but they are one view, and it is their tail rather than their average to
distrust.

The measurement is 12 sites of one region, one source model, and one or two
draws per arm. It says a single path is a lottery of about ±20% on this model,
not that twenty paths are converged.

## Revisit if

A model's logic tree is small enough to enumerate within the compute available,
and its branch weights can be carried into the occurrence table; the engine
changes how it numbers years across sampled paths; or a study shows twenty paths
still leaving material error at the return periods CASS reports.
