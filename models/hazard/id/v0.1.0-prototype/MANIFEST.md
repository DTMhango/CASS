# Indonesia prototype seismic source model — v0.1.0-prototype

**This is not a hazard model for Indonesia.** It is a deliberately simple,
fully-stated source model that exists so the hazard pipeline can be built,
tested and measured before the real sources arrive. Nothing computed from it
describes Indonesian seismic risk, and nothing computed from it may inform a
decision.

It is version-controlled — unlike the GEM clones, which are licensed and
gitignored — because it is small, it is ours, and a footprint that cannot be
traced to the sources behind it is untraceable the moment anyone asks why a
number moved.

## What it contains

| File | What it is |
|---|---|
| `source_model.xml` | One area source over West Java, truncated Gutenberg–Richter, NRML 0.4 |
| `source_model_logic_tree.xml` | A single branch at weight 1.0 |
| `gmpe_logic_tree.xml` | A single GMPE, `BooreEtAl2014`, for active shallow crust |

### The source

An area source spanning roughly 105.5–108.8°E and 5.6–8.4°S, seismogenic depth
0–25 km, `a = 4.2`, `b = 1.0`, magnitudes 5.0 to 7.5, dipping nodal planes at
45° with reverse rake and hypocentres at 10 and 20 km.

## What is wrong with it, specifically

Stated in full, because the numbers it produces look exactly like the numbers a
real model produces.

- **The tectonics are wrong.** Indonesian hazard is dominated by the Sunda
  megathrust and the Benioff zone beneath Java — subduction interface and
  intraslab sources. This model has neither. It represents all seismicity as
  shallow crustal, which is the smallest contributor of the three.
- **The GMPE is for the wrong region and the wrong tectonic setting.**
  `BooreEtAl2014` is an active-shallow-crust model built largely on Californian
  data. Subduction attenuation differs substantially, particularly at the long
  periods that matter for taller buildings.
- **The recurrence parameters are illustrative.** `a = 4.2, b = 1.0` was not
  derived from an Indonesian catalogue. It produces about 0.16 events per year
  above M5 in the modelled area, which is far too few.
- **The maximum magnitude is far too low.** M7.5 excludes exactly the events
  that drive a reinsurance loss; the 2004 Sumatra–Andaman earthquake was M9.1.
- **No logic tree.** One source branch and one GMPE, so the result carries no
  epistemic uncertainty at all — a single-point estimate presented in a format
  that usually carries a distribution.
- **No site response.** The grid holds no site parameters, so the calculation
  runs at a uniform reference Vs30 of 400 m/s everywhere. Jakarta sits on a
  deep alluvial basin where soft-soil amplification is large.

Taken together these bias the answer downward and there is no reason to think
the bias is small.

## What it is good for

- Proving the path: grid → job → OpenQuake → ground motion → intensity bins →
  Oasis footprint and occurrence table, with frequency preserved.
- Measuring the multi-IMT decision against a real footprint rather than an
  argument. Four measures come out of one calculation, which is precisely the
  case the section 6 gate has to rule on.
- Sizing things: how big a footprint gets, how long a calculation takes, where
  the intensity dictionary's range needs to sit.

## Reproducing the run

The grid subset, the job and the conversion all come from code — nothing here
is hand-built except the three XML files.

```bash
# From platform/backend, generate job.ini and sites.csv for the grid subset,
# then run the pinned engine over them.
docker run --rm -v "<job-dir>:/job" -w /job --entrypoint oq \
    openquake/engine:3.23 engine --run /job/job.ini --exports csv

# Then register what came out.
python manage.py register_hazard --export-dir <job-dir>/out --country ID \
    --version 0.1.0-prototype \
    --source-model "CASS West Java prototype area source" \
    --gmpe BooreEtAl2014
```

The job is built by `cass_converter.hazard_job` from the registered area-peril
grid, so the sites cannot drift from the cells they stand for. The area peril
is carried as OpenQuake's `custom_site_id`, so the ground-motion export names
the cell directly and there is no coordinate join.

## Observed result, 2026-09-12

858 onshore cells around Jakarta and Bandung, 50-year investigation time × 20
stochastic event sets = 1,000 years effective.

| | |
|---|---|
| Events | 161 (0.161/year) |
| Footprint rows | 293,127 across four measures |
| Annual frequency after conversion | preserved exactly (0.00e+00 relative difference) |
| Ground motion above the top intensity bin | none |
| Ground motion below the bottom bin | 146,721 samples, producing no rows |
| Validation problems | none |

The intensity dictionary's ceiling was never approached — the largest PGA was
1.25 g against a 4.0 g top bin. That is **not** evidence the ceiling is set
correctly: this source model's maximum magnitude is 7.5 and its attenuation is
crustal. A real subduction model will produce much stronger motion, and the
range has to be re-checked against it rather than tuned to this.

## What replaces it

GEM's Global Hazard Mosaic, or a national model such as the Indonesian
`PuSGeN` 2017 hazard maps and their underlying source model. Acquiring one is
an external dependency and the outstanding item on the hazard leg. Whichever
arrives, it slots in as three files at this path under a new version — nothing
downstream changes, which is the point of having built the pipeline against
this one.
