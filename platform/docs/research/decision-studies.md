# The modelling decisions CASS studies for itself

CASS is a research tool, and four questions about how it turns hazard into loss
are decided by measuring the candidates on the platform rather than by opinion.
Each study ends in a decision record: what was compared, what the numbers were,
and why the winner won. This page is the plan for all four, written for a reader
who is learning catastrophe modelling. It is updated as each study runs.

The four are tracker items 21, 26, 22 and 23, in the order they will be run.

## The measuring stick

Most of these questions have a reference answer. OpenQuake, the engine that
computes CASS's hazard, can also compute loss: given the same events and GEM's
own vulnerability functions, it produces the loss that a model with no
Oasis-specific approximations would produce. CASS's reference comparison (item
9) runs a finished analysis through OpenQuake on exactly the events the Oasis
footprint was built from, and reports the ratio of the two.

Its first run, on the Jakarta–Bandung book, put CASS's average annual loss at
0.904 of OpenQuake's, and its return-period losses between 0.70 and 1.26 of
OpenQuake's. That gap is the combined effect of every approximation CASS makes
today, which is why a candidate that closes part of it, or does not widen it, is
measurable.

## 1. How a building class that responds to several shaking measures is modelled (item 21)

**The question, plainly.** Buildings of different heights respond to different
frequencies of shaking: a low, stiff building to short, sharp motion (PGA, or
spectral acceleration at 0.3 seconds), a tall one to longer, rolling motion
(0.6 or 1.0 seconds). GEM ties each building type's damage function to the one
measure it responds to. When a schedule does not say how tall a building is,
CASS blends GEM's candidate building types into one class, and that class can
reach types responding to different measures. An Oasis footprint gives each
location one intensity per peril, so a class like that cannot be a single Oasis
function.

**What CASS does today.** Measures are carried as correlated channels of each
cell, and a class that resolves to one measure is routed to its channel (ADR 8).
A class that spans several is refused, and its value is reported as unmodelled
rather than guessed. In the GEM v2026.0.0 build, 56 of Indonesia's 240 classes
span measures (ADR 8).

**What is already established** (read from oasislmf 2.5.7, 14 September 2026):

- Oasis identifies an item by location, peril, coverage and building, so one
  coverage cannot carry two rows of the same peril.
- Oasis correlates damage and hazard sampling by location by default, so several
  items at one location share their random draws.

**The candidates.**

- **A. Refuse** — today's rule. Honest, but the value is simply not modelled.
- **B. Weighted channels as sub-perils.** Split a spanning class into one item
  per measure, each under its own peril code and each with a function already
  weighted by GEM's share of that building type. The expected loss is then the
  blend's expected loss exactly, and the location-level correlation keeps the
  items moving together. The open question is whether the financial module
  combines the items at the coverage level exactly, policy terms included.
- **C. Convert every function to one measure.** Rejected by build plan section 6
  without a separate scientific derivation.
- **D. A CASS ground-up component** that reads several measures directly. It
  crosses the Oasis module boundary.
- **E. OpenQuake computes the ground-up loss** and Oasis applies only the
  financial terms.

**How it will be measured.**

1. Size the problem: the share of value refused on a book that states no storey
   counts (the 30 June benchmark) and on one that does (the pilot book).
2. Check candidate B's mechanics on the real engine before building it into
   CASS: that the split items add back to the blend, that the policy terms still
   apply to their sum, and that nothing is capped or dropped.
3. Build candidate B and run the pilot book with its storey counts withheld, so
   classes span measures; run ground-up and insured.
4. Compare with the OpenQuake reference, which handles several measures
   natively, and check the insured loss reconciles with the book's location
   terms.

**What decides it.** B is adopted if the multi-measure classes' reference ratio
falls within the band the single-measure classes already show and the insured
loss reconciles. Otherwise refusal stays, and D and E are costed.

### Step 1, measured 14 September 2026: how much is refused today

| Book | Storey counts | Value on classes spanning measures |
| --- | --- | --- |
| 30 June benchmark, cohort A (42 locations), default occupancy assumption | None stated | **100%**, every class spanning all four measures |
| 30 June benchmark, mixed commercial occupancy assumption | None stated | 42% |
| 30 June benchmark, any single storey band assumed for every risk | Assumed | 0% |
| Jakarta–Bandung test book (64 locations), as published | All stated | 0% |
| Jakarta–Bandung test book, storey counts withheld | Withheld | 7.2% |

In plain terms: under today's rule CASS cannot model the real benchmark book at
all, because no risk in it says how tall the building is. Height is what
decides which shaking measure a building responds to, so a building of unknown
height could be any of GEM's candidates, and those respond to all four measures.
The question is therefore not a corner case: it decides whether a schedule
without heights can be modelled.

### Step 2, measured 14 September 2026: candidate B on the real engine

How oasislmf 2.5.7 treats the pieces, read from its source:

- The ground-up module prices every item at its coverage's full value and then
  caps the coverage's total at that value. So a split item has to carry a
  function whose damage is already scaled by its measure's share; the shares
  cannot go on the value.
- Items are identified by location, peril, coverage and building. Two rows with
  the same peril on one coverage are silently collapsed to one, so each measure
  needs its own earthquake sub-peril code.
- A financial term applies only to items whose peril its peril filter names.
  CASS writes `QEQ` (shake) on its terms, which would leave the other
  sub-perils' items with no deductible or limit, so the terms sent to the engine
  have to name all earthquake perils (`QQ1`).
- Every item at one location shares a damage group, so the split items draw the
  same random numbers and move together.

The test put one building of unknown height, whose class spans all four
measures, through the live model package six ways over all 27,313 events with
20 samples each. W was split into four sub-peril items with pre-weighted
functions and its location terms widened. Q was the same split with the terms
left on `QEQ`. C1 to C4 each carried one measure alone, unweighted.

| Check | Result |
| --- | --- |
| W's mean ground-up loss per event and coverage against the weighted sum of C1–C4 | 141,827.56 against 141,827.47 over 28,788 event-coverage pairs; the largest single gap under one cent |
| W's insured loss per event and sample against its deductible and limit applied to its ground-up loss | Exact in all 119 event-samples with loss |
| Q, the control with terms left on `QEQ` | Wrong in 89 of 114; insured loss 40% too high, because three measures' damage escaped the terms |
| Any coverage's sampled loss above its value | None of 1,114 |
| Damage groups per location | One, shared by all four items |

So the representation is mechanically exact in the Oasis engine as long as the
peril scope is widened; without the widening it is wrong, and it would not
announce it.

The test also found a defect unrelated to the choice. oasislmf fills a blank
storey count with OED's default of 0, and CASS's lookup inside the engine read
0 as a real height, so every risk without one failed there while CASS's own
keys answered it. No live run had shown it because every live book states its
storeys. It is fixed (tracker item 33).

### Step 3, 14 September 2026: candidate B built into CASS

What the engine test did by hand, CASS now does itself, for a vulnerability set
built as correlated channels:

- The build writes, beside each channel's own function, the same function with
  its damage scaled by the channel's share of the class, one million identifiers
  above it. Each assumption set scales by its own shares.
- The package keys a class spanning measures as one item per measure, under the
  sub-perils QEQ, QFF, QLS and QTS for PGA, SA(0.3), SA(0.6) and SA(1.0), and
  refuses to build from a set that lacks the scaled functions.
- A run against such a package sends the engine a copy of the portfolio whose
  financial terms are scoped to all earthquake perils (`QQ1`), keeps that copy
  with the run, and leaves the published files as reported.

A set built without naming a representation stayed undecided, and refused these
classes as before, until the live measurement below decided the question.

### Step 4, measured 14 September 2026: against the OpenQuake reference

A set was built for Indonesia as correlated channels under its own version, so
the Indonesian set in use was left alone, and assembled with the grid and
PuSGeN 2024 hazard already on the live stack. Three books were derived from the
synthetic Jakarta–Bandung test book, each run ground-up and each compared with
OpenQuake on the same ground-motion fields:

| Book | Value through split items | Average annual loss ratio | Return-period ratios |
| --- | --- | --- | --- |
| Storeys stated, as published | 0% | 0.904 | 0.70–1.26 |
| Storeys withheld | 7.2% | 1.032 | 0.91–1.30 |
| Commercial, construction and height unknown — the benchmark's default | 100% | 0.968 | 0.75–1.38 |

On every book CASS's keys and the engine's lookup agreed on all 64 locations,
and all of each book's value was mapped. The storeys-stated book reproduced its
earlier ratio exactly, so the new package changes nothing for a class of one
measure.

### Decision

**Adopted: candidate B** ([ADR 16](../adr/0016-multi-measure-classes-as-sub-peril-channels.md)).

In plain terms: when CASS cannot tell how tall a building is, it now models it
as the blend of every building GEM thinks it could be, sending the engine one
piece per shaking measure, each carrying its share of the damage. On the real
engine the pieces add back to the blend exactly, and the insurance terms apply
to their total. Against OpenQuake's own calculation, a book modelled entirely
this way is as close as a book of known heights is — and the book of known
heights was already 10% low, which is a property of the conversion as a whole
(binning the shaking and the damage), not of this choice.

What it does not settle: the ratios rest on one hazard realisation and 64
locations, and the tail ratios on few events. A single building's damage is the
weighted sum of its candidates' damage rather than a draw from one of them, so
its mean is right and its spread narrower; the OpenQuake reference splits value
the same way and cannot see that.

## 2. Whether a simulated occurrence or a rupture is the Oasis event (item 26)

**The question, plainly.** OpenQuake simulates each earthquake rupture's shaking,
often many times over, because the same rupture shakes the ground differently
each time. CASS can make each simulated occurrence its own Oasis event, or it
can group the simulations of one rupture into a single event whose intensity at
each cell is a spread of probabilities across intensity bins.

**What CASS does today.** Occurrence per event. Every package records the event
identity it was built under (ADR 4).

**The candidates.** Occurrence per event; rupture-binned.

**How it will be measured.** Build the pilot package both ways from the same
hazard, run the pilot book through each, and compare the average annual loss and
return-period losses with the OpenQuake reference. Record event counts, footprint
size and run time alongside.

**What to look for.** Occurrence per event keeps each simulation's pattern across
sites — when one cell shakes hard, its neighbours usually do too. Rupture binning
keeps each cell's spread of shaking but not the pattern across cells, which is
expected to thin the tail of an aggregate loss. That expectation is a hypothesis
for the study to test, not a finding.

**What decides it.** The representation closer to the reference at the reporting
return periods. A tie goes to occurrence per event, which is simpler and keeps
the pattern across sites.

### Measured 14 September 2026

The catalogue answers most of the question before any loss is run. The live
PuSGeN 2024 event set for the Jakarta–Bandung region holds 27,313 occurrences of
26,651 ruptures over 1,000 simulated years, and 97.7% of those ruptures occur
exactly once. For them a rupture-binned footprint is the occurrence's own
footprint: the same table. Only 617 ruptures, covering 4.7% of occurrences, have
anything to pool.

Pooling them and running the Jakarta–Bandung book through both packages, with
everything else held the same:

| | Occurrence per event | Rupture-binned |
| --- | --- | --- |
| Average annual loss, analytical | 570,652 | 570,854 (+0.04%) |
| Standard deviation of annual loss | 2,840,652 | 2,781,092 (−2.1%) |
| 100-year occurrence exceedance loss | 23,121,316 | 21,685,938 (−6.2%) |
| 25-, 200- and 250-year | — | within 1.2% |
| 500- and 1,000-year | 49,141,712 and 51,811,376 | identical |
| Footprint | 153.1 MB over 21,772 events | 152.9 MB over 21,274 events |
| Loss run | 17s | 17s |

### Decision

**Occurrence per event stays, and rupture binning is not built**
([ADR 17](../adr/0017-an-oasis-event-is-a-simulated-occurrence.md)).

In plain terms: grouping a rupture's repeats would only matter if ruptures
repeated, and in a thousand years of this catalogue they almost never do. Where
it could be applied it left the average untouched, thinned the hundred-year loss
by 6%, and saved neither space nor time. It also needs an approximation
occurrence per event does not: a footprint has no way of saying that one
occurrence did not shake a particular cell at all, so pooling has to assume the
cell was reached every time.

Worth revisiting if CASS ever simulates a catalogue long enough for ruptures to
recur often — here there are 1.02 occurrences per rupture, and one event per
rupture only begins to pay well above that.

## 3. How the branches of a national hazard model are weighted (item 22)

**The question, plainly.** A national hazard model is not one answer but a logic
tree of alternative scientific views: which faults are active and how often,
which ground-motion model applies. PuSGeN 2024 has about 1,080 combinations,
each with a weight, and the published hazard is their weighted mean. An event
set for Oasis has to decide which combinations to simulate and how to weigh
their events.

**What CASS does today.** One path through the tree is sampled (ADR 12), so a
footprint is one view of the hazard, and the configuration refuses more than one
sample until a weighting rule is decided.

**The candidates.**

- **A. One sampled path** — today.
- **B. Sample many paths.** OpenQuake draws N paths with probability equal to
  their weights; the events of all N form one catalogue with its investigation
  time multiplied by N. The weights are honoured by how often each branch is
  drawn, so every event carries the same weight.
- **C. Full enumeration** of all combinations with their weights: exact, and
  about 1,080 times the compute.
- **D. The single highest-weight path.** Biased towards one view by
  construction.

**How it will be measured.** On the 858-cell Jakarta–Bandung region, compute the
model's own weighted-mean hazard curves with a classical calculation at a sample
of cells — the published answer. Then run event-based hazard with A and with B at
N = 10, 50 and 100, at the same total simulated years, and compare each run's
exceedance curves with the weighted mean at the 100- to 1,000-year return
periods, together with compute time.

**What decides it.** The smallest N whose curves sit within the Monte Carlo error
of the weighted mean at the reporting return periods. The converter then accepts
sampled paths as one equally weighted catalogue.

### Measured 14 September 2026

The published model was run over 12 sites in the Jakarta–Bandung region: once as
a classical calculation enumerating all 1,080 realisations — the weighted mean
the model states, 79 seconds — and then event-based, each arm covering the same
2,000 simulated years.

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

With one path the 1,000-year return period was undefined at 9 to 15 of the 36
site-measures, because that one path's catalogue never reached such a loss; with
twenty paths it was defined at 33 to 36.

### Decision

**Sample twenty paths and pool them as one catalogue**
([ADR 18](../adr/0018-sampled-paths-pooled-as-one-catalogue.md)).

In plain terms: the hazard model is not one opinion but about a thousand, each
with a weight, and its published answer is their weighted average. CASS used to
pick one at random and model that. Which one it picked mattered: across four
draws the hazard came out between 16% below and 24% above the model's own
answer. Drawing twenty and using them all landed within 8% both times, filled in
the rare losses a single draw never produced, and cost the same — the work
depends on how many years you simulate, not how many views you spread them
across.

Enumerating every branch stays refused, and for a reason worth stating: the
branches carry different weights, and an Oasis event set has no way to say one
event is likelier than another. Sampling sidesteps that, because a path is drawn
as often as its weight says it should be.

What it does not settle: 12 sites of one region, one source model, one or two
draws per arm. It says one path is a lottery of roughly ±20% on this model, not
that twenty paths have converged.

## 4. How a footprint is stored for the engine (item 23)

**The question, plainly.** A footprint is the table of shaking for every event,
cell and intensity bin — for a national model, many millions of rows. The engine
reads it throughout a loss run, so its format decides disk use, memory and speed.

**What is already established.** The Oasis engine in the worker reads four
formats: `footprint.bin` (what CASS writes today), `footprint.bin.z` (compressed),
`footprint.parquet` and `footprint.csv`.

**How it will be measured.** Build the Jakarta–Bandung package in the binary,
compressed binary and Parquet formats, and a national-scale footprint made by
repeating the regional footprint's statistics across Indonesia's 52,831 cells,
labelled as synthetic. For each, measure size, package build time, the engine's
loss run time and peak memory, and confirm the losses are identical.

**What decides it.** Among the formats that give identical losses, the one with
the smallest run-time cost at national scale; size breaks a tie.

### Measured 14 September 2026

The same book through each format, at three scales. National scale is the
regional footprint's rows repeated across Indonesia's 52,831 cells, labelled
synthetic: the same shaking, the size of a country.

| Scale | Format | Size | Build | Loss run |
| --- | --- | --- | --- | --- |
| Region, 858 cells | binary | 154 MB | — | 16s |
| | compressed | 40 MB | 7s | 16s |
| | Parquet | 107 MB | 50s | 30s |
| A third of Indonesia | binary | 3,063 MB | 18s | 14s |
| | compressed | 765 MB | 283s | 21s |
| | Parquet | 866 MB | 170s | 23s |
| Indonesia, 52,831 cells | binary | 9,494 MB | 44s | 17s |
| | compressed | 2,366 MB | 710s | 18s |
| | Parquet | 2,566 MB | 296s | 22s |

Every format returned the same loss to the cent, which is what makes this a
storage choice rather than a modelling one. The build times compress at the
slowest setting; at a middling one a national footprint compresses in about
three and a half minutes, and at the fastest in about one, for 27% of the
binary's size rather than 24.5%.

### Decision

**The binary stays, and compression is the measured reserve**
([ADR 19](../adr/0019-footprint-stays-a-ktools-binary.md)).

In plain terms: how the footprint is stored changes nothing about the answer —
all three gave the same loss to the cent. It changes disk and build time. The
compressed form is four times smaller and just as fast to read, but writing it
means a second binary format in CASS, and at 9.5 GB for a whole country disk is
not what CASS is short of. So the decision is to keep the simple format, record
exactly what compression would buy, and switch when a deployment actually cannot
hold a national package. Parquet is out: bigger than the compressed form, slower
to read, and a directory of 27,313 files.

## Why this order

The multi-measure study changes how much of a book is modelled at all, so it
comes first. The event representation study uses the same reference machinery
and the same package build. Realisation weighting is a hazard-only study.
Footprint storage needs national-scale data and is least likely to change a
number.
