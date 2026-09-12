# Indonesia PSHA source model — PuSGeN 2024, v2024.0.0

The real national model, and what the `v0.1.0-prototype` beside it was standing
in for. Published by the Team for Updating Seismic Hazard Maps of Indonesia —
under the National Center for Earthquake Studies, with the GEM Foundation — as
part of the 2026 mosaic.

**Licence: CC BY-NC-SA 4.0. Commercial use is not cleared.** The registry
records that as a stored fact, so anything computed from this is research and
platform development until somebody names the agreement that changes it.

## What it contains

| Part | Files |
|---|---|
| Crustal faults | `ssm/crust/` — characteristic and Gutenberg–Richter |
| Gridded seismicity | `ssm/gridded/` — six depth layers |
| Subduction interface | `ssm/interface/` — two segments, each CH and GR |
| Ground-motion logic tree | `gmmLT.xml` — 18 branches over 5 tectonic regions |
| Source logic tree | `ssmLT_clean.xml` — two branches, weighted 0.9 / 0.1 |
| Site model | `iid-10km.csv` — 11,523 points with measured Vs30 |
| Equivalent distances | `lookup_reqv_*.hdf5` — for collapsed gridded sources |

Everything except the manifests and the licence is gitignored: it is licensed
data and 19 MB of NRML. The platform's own copy lives in the artifact store,
registered through the hazard-models screen.

## Converting it for CASS

The package is a **classical** calculation. That is what a national model
publishes — it exists to support a building code, and produces the probability
of exceeding a ground motion at a site. A catastrophe model needs events, so
CASS converts it to `event_based`; `cass_converter.job_config` performs the
conversion and lists every change.

Two of those changes are not obvious and both were found the hard way.

**Basin depths.** The published site model writes `-999` for `z1pt0` and
`z2pt5`, meaning "not measured". Fed to the ground-motion models as a real
value it is catastrophic: Chiou and Youngs 2014 carries a term in
`exp(-(z1pt0 - E[z1pt0]) / 150)`, so −999 m evaluates to roughly 5,800. The
first converted run produced a **median PGA of 0.37 g and a maximum of
1,378 g** — about a thousandfold too high, and entirely plausible-looking as a
column of numbers. CASS now recomputes both depths from Vs30 using the same
relations the models use internally, and marks them derived.

**Realisations.** The logic tree enumerates to about **1,080** realisations.
A footprint is one event set, so the run samples a single path. That is one
alternative view of the hazard and not the model's weighted mean, and it is
why the realisation-weighting decision matters.

## Observed result, 2026-09-12

858 onshore cells around Jakarta and Bandung, 50-year investigation time × 20
stochastic event sets = 1,000 years effective, one sampled path.

| | |
|---|---|
| Events | 27,413 (27.4/year) |
| Footprint rows | 9,753,715 across four measures |
| Annual frequency after conversion | preserved exactly |
| Ground motion above the top intensity bin | none |
| Events with no footprint rows | 6,358 (23.2%) — national events that did nothing in this box |
| Maximum PGA | 2.30 g |
| Maximum SA(0.3) / SA(0.6) / SA(1.0) | 6.34 / 4.61 / 4.05 g |
| Validation problems | none |

Those maxima are what the intensity-bin ranges in `pilot_bins` were re-derived
from. Three of the four exceeded the ranges that stood before, which had been
set against a prototype crustal model that could not produce a megathrust.

## Site conditions

The published site model carries **measured Vs30**, 180 to 747 m/s in the
Jakarta–Bandung box — real soft-soil variation. It is sparse there: 62 points,
so 316 of 858 cells join within 15 km and the remaining 542 fall back to the
model's reference. The join reports both counts, because a cell given the
reference rock value understates its loss wherever the ground is softer.

This is the first thing to answer the pilot grid's largest open question, that
no cell carried a site parameter. It answers it for 37% of the cells.

## Still outstanding

- **Coverage.** Only the Jakarta–Bandung subset has been run. The whole
  Indonesian grid is 52,831 cells against 858 here.
- **One realisation.** The weighting rule for combining several is undecided.
- **The site join is 37% measured.** A denser Vs30 source would raise it.
- **Nepal has no source model at all.**
