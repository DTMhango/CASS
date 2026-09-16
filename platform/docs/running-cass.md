# Running CASS, end to end

From a clean machine to a reinsured loss. Every step below has been run on the
live stack; where something is a known limit rather than a step, it says so.

CASS is a research tool. Its results are not a basis for pricing or reserving
([ADR 15](adr/0015-research-tool-and-gem-permission.md)).

## What "end to end" means here

A finished run produces three numbers for one portfolio:

| Perspective | What it is |
| --- | --- |
| Ground-up | Loss before any insurance terms |
| Insured | Loss after policy deductibles and limits |
| Loss net of reinsurance | What is retained after every treaty inures |

The ceded amount is the insured loss less the third. Oasis calls that third
stream `ri`, and it is the loss *net* of reinsurance rather than the amount
ceded — which is why CASS labels it that way.

This has been done. On the live stack a 64-location Jakarta–Bandung book with
3 accounts and three contracts — a surplus share on two sites, a 30% whole-book
quota share, and a catastrophe excess of loss of 2,000,000 per event over
100,000 — ran all three perspectives in 96 seconds: ground-up 566,728.13,
insured 305,479.06, net of reinsurance 151,313.83, so 154,165.23 ceded.

## Before you start: the one real limit

**A run only produces loss where there is hazard.** Building a grid and a
vulnerability set for a country is quick; computing that country's hazard is a
calculation of hours.

Today the only hazard CASS holds is Indonesia, and only over the
Jakarta–Bandung region — 962 cells of a 52,831-cell national grid. Locations
outside it are reported as outside the domain rather than silently given zero
loss, which is honest but is not an answer.

So a portfolio concentrated in that region runs end to end today. A national
Indonesian book needs step 5 run without a region first. A book in another
country needs a source model for it.

---

## Part 1 — Stand the platform up

### 1. Install and configure

You need Python 3.11, Node 22, Docker, Git and GNU make. On Windows, install
make with `winget install ezwinports.make`; the Makefile finds the shell that
Git for Windows ships, so it runs from PowerShell without WSL or a Linux
distribution.

```bash
cd platform
make setup          # virtualenv, npm ci, and a .env from the template
```

`make setup` leaves four secrets blank in `platform/.env`. Fill them in:
`CASS_SECRET_KEY`, `POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD` and
`OASIS_ADMIN_PASS`. Generate each with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Point `CASS_MODELS_PATH` at the folder holding your GEM clones and hazard
packages. They are large and licensed, so they stay on the machine and are
mounted read-only rather than baked into an image.

### 2. Start it

```bash
make up-engines     # everything, including OpenQuake and Oasis
make seed           # a demonstration project and the PiWind portfolio
```

`make up` starts the control plane alone, which is enough for everything except
running a calculation. Use `up-engines` for a real run.

The API applies database migrations each time it starts, before it serves, and
both commands return only once it is serving. After changing the code, run
`make up-engines` again: it rebuilds the images CASS builds, recreates the
containers whose image changed, and migrates. Your data is kept.

- Interface: <http://localhost:8080>
- API and its documentation: <http://localhost:8000>, `/api/docs/`
- MinIO console: <http://localhost:9001>

Sign in as `analyst`, `modeller` or `reviewer`. The four platform roles are
analyst, modeller, reviewer and administrator, and they gate what each screen
offers: only a modeller sees the Hazard and Build tabs, and only a reviewer can
decide an approval.

### 3. Choose the GEM release

**Models → Build**, on the GEM release card. CASS lists every release it finds
under the mounted models folder, reads the commit and tags of each repository
from its own git metadata, and says whether it is the v2026.0.0 release CASS was
validated against. Choose one.

Nothing is bundled and there is no environment file to edit — the release is a
choice recorded on the platform, and it takes effect without a restart. Until
one is chosen, building a vulnerability set is refused rather than returning an
empty catalogue.

---

## Part 2 — Build the model

A model version is a grid, a vulnerability set and a hazard set. The first two
are quick. The third is the calculation.

### 4. Build an area-peril grid

**Models → Build → Grid.** Write a specification — tiles, a base resolution,
named refinements and the reason for each — and post it. CASS generates the
cells and registers the grid as a draft.

The screen counts the cells as you write, so the cost of a resolution is visible
before you build: cost is quadratic, and halving the resolution quadruples the
count. A specification over the installation's cell limit is said to be over it
there, and refused with its own count if it is posted anyway.

*API:* `POST /api/v1/grids/build/`, and `POST /api/v1/grids/estimate/` for the
count on its own.

### 5. Build a vulnerability set

**Models → Build → Vulnerability.** Choose the country, and supply the
enrichment: the design eras and the reason for each. The enrichment is posted
with the build rather than compiled in, because it is the assumption behind
every function — so the set's version follows it.

The country's ISO codes are not typed. The alpha-3 is read from GEM's stock
summary for the country and the alpha-2 follows from ISO 3166-1, so a set cannot
be registered under another country's code.

208 of the 215 countries GEM v2026.0.0 publishes can be built. The seven that
cannot are the United States, Canada, Puerto Rico, the US Virgin Islands, Guam,
American Samoa and the Northern Mariana Islands: GEM publishes them in HAZUS
classes (`C1H/HC/RES3`) rather than in its own building taxonomy, and mapping an
OED schedule onto those is a second set of assumptions nobody has written. They
are listed with that reason rather than left out, and cannot be chosen.

An era table that is out of order, ends before today, or names a design level
GEM does not use is refused, and so is an era that states no reason.

An era table only decides anything for a risk whose year built is stated; a risk
without one carries the country's stock distribution over design levels either
way. Where a book states no years, or nobody has researched the country's code
history, the set can say so instead — "by the country's building stock alone",
with the reason, which then travels with every model version built on the set.
What it cannot be is blank: an empty table and a decision to have none look the
same afterwards, and only one of them is a decision.

A class that spans several shaking measures — which is every class a schedule
reaches when it states no storey counts — is carried as one earthquake sub-peril
item per measure ([ADR 16](adr/0016-multi-measure-classes-as-sub-peril-channels.md)).
That is the default; you do not have to ask for it.

*API:* `POST /api/v1/vulnerability-sets/build/`

### 6. Upload and configure a hazard model

**Models → Hazard.** Three separate steps, on purpose.

**Upload.** The published OpenQuake package goes in whole. Every file is listed
and checksummed, the `job.ini` is parsed into typed parameters, and the logic
trees are counted so the realisation number is known before anything runs.
Nothing is changed.

**Configure.** Edit the parameters that are yours to edit. The model's own logic
trees are shown and not offered — changing one does not configure this model, it
makes a different one. Every problem is listed while you are still looking at
the screen, because a national calculation is hours and finding out there that
the mode was wrong is the expensive way.

CASS applies its own defaults over the publisher's:

| Setting | CASS default | Why |
| --- | --- | --- |
| `calculation_mode` | `event_based` | A footprint needs events; published models are almost always classical |
| `investigation_time` | 50 years | |
| `ses_per_logic_tree_path` | 1 | |
| `number_of_logic_tree_samples` | 20 | Twenty views of the logic tree, pooled ([ADR 18](adr/0018-sampled-paths-pooled-as-one-catalogue.md)) |

Those last three multiply to a thousand simulated years. One sampled path was a
lottery of roughly ±20% against the model's own weighted mean; twenty cost the
same to run.

**Set the region** if you do not want the whole country. This is the single
largest lever on run time: it selects the grid cells the calculation covers.
Leave it empty for a national run.

**Run.** The spec's files are assembled and handed to the engine. What comes
back becomes a hazard set: one footprint per intensity measure, the occurrence
table, an intensity-bin dictionary per measure, and the job that produced them.

*API:* `POST /api/v1/hazard-models/{id}/configure/`, then
`POST .../specs/{spec}/launch/`

> **On timing.** The Jakarta–Bandung region (962 cells, twenty paths) takes
> about 5½ minutes: 2 minutes in the engine and 3½ converting. A national run is
> 55 times the cells and pulls far more ruptures into range, so it is several
> hours. Memory is not a constraint — the conversion streams to disk and peaks
> around a quarter of a gigabyte whatever the size.

### 7. Assemble a model version

**Models → Build → Assemble.** Pair the grid with the vulnerability set, then
attach the hazard set.

- A pair from two different countries is refused: it would calculate happily and
  mean nothing.
- A hazard set is not attached unless it carries every measure the vulnerability
  functions demand. Half the measures produces a model that answers half its own
  vulnerability set and reports zero for the rest, which is
  indistinguishable from an event that did no damage.
- Clipped hazard — ground motion above the top intensity bin — blocks
  publication rather than warning. Those are the strongest values the
  calculation produced, so a set that clips is wrong exactly where a
  reinsurance loss lives.

Then publish it. A research prototype publishes with its blockers listed rather
than hidden.

*API:* `POST /api/v1/model-versions/assemble/`, `.../attach-hazard/`,
`.../publish/`

### 8. Approve and build the Oasis package

A package is built under a converter-candidate approval that somebody has
decided. As a modeller, request the gate; as a reviewer, decide it; then build.

```
POST /api/v1/approvals/                     gate: converter_candidate
POST /api/v1/approvals/{id}/decide/         decision: approved
POST /api/v1/model-versions/{id}/build-package/
```

The package build writes the footprint, occurrence table, vulnerability and
keys data into the Oasis model root.

> **One package is served at a time** ([ADR 9](adr/0009-cass-writes-the-oasis-package.md)).
> Building a package for one model version replaces what the worker serves. A
> run against a different version then fails before it reaches the engine,
> naming both versions — it would otherwise publish one version's losses under
> the other's name. If you need an older version again, build its package again.

---

## Part 3 — Bring in the portfolio

### 9. Import the schedule

**Exposure → Import.** Download the CASS intake template, fill it in, and
upload. The reader validates on the way in and reports findings rather than
guessing.

*API:* `GET /api/v1/exposure-versions/template/`, then
`POST /api/v1/exposure-versions/upload/`

Two things worth knowing about the schedule itself:

- **Perils.** Write `QQ1` for the whole earthquake group, as OED defines it.
  CASS reads OED's codes as OED means them, including `AA1` for every peril.
- **Storeys.** Leaving `NumberOfStoreys` blank means *not stated*, and is
  handled — the risk is modelled as the blend of every building GEM thinks it
  could be. A `0` means the same thing and is read the same way.

### 10. Review and correct

**Import review** shows what needs a decision: flagged rows, rows the rules
could not place, and the geocoding sensitivity — which locations keep their cell
across the area their geocode precision actually supports, and how much value
sits on assignments the geocode cannot vouch for.

Correct rows in place, then publish the version. A published exposure version is
what a run names.

*API:* `POST /api/v1/exposure-versions/{id}/rows/edit/`, `.../publish/`

### 11. Build the financial structure

**Exposure → Financial structure.** A schedule of locations alone can produce
only a ground-up loss. Insured and reinsured losses need policies and treaties.

**Policies** — one per account. These carry the deductibles and limits that turn
ground-up into insured.

**Contracts** — the treaties. Three kinds:

| Kind | Code | Scope |
| --- | --- | --- |
| Quota share | `QS` | Whole portfolio or named accounts |
| Surplus share | `SS` | Named accounts or locations |
| Catastrophe excess of loss | `CXL` | Whole portfolio or named accounts |

Each contract takes its own inuring priority, which is the order treaties apply
in. The builder reports blocking findings before you publish, so a structure
that could not be run is caught on the screen.

*API:* `POST /api/v1/exposure-versions/{id}/structure/policies/` and
`.../structure/contracts/`; `GET .../findings/` for what still blocks.

---

## Part 4 — Run it

### 12. Create and submit the analysis

**Analysis.** Choose the project, the exposure version, the model version, and
the perspectives you want. For a reinsured loss, ask for all three.

```
POST /api/v1/analysis-runs/
  { "project": ..., "exposure_version": ..., "model_version": ...,
    "perspectives": ["ground_up", "insured", "reinsurance"],
    "label": "..." }
POST /api/v1/analysis-runs/{id}/submit/
```

Run modes: `geometry_only` stops after the keys and reports eligibility without
a loss — useful for checking a book maps before paying for a calculation.
`technical` is the ordinary loss run.

### 13. Watch it

**Runs** shows every stage as it happens. The pipeline is:

```
validate_exposure → enrich → publish_oed → keys → reconcile_keys
  → generate_inputs → validate_inputs → smoke → losses → collect → review
```

What each of the ones worth knowing does:

- **enrich** — applies the assumption set and records what share of attribute
  values rests on the assumption rather than on the schedule.
- **publish_oed** — checks the engine version and that the worker is serving
  *this* model version's package, before anything is submitted. A book stated in
  another currency is converted here, at an approved rate.
- **reconcile_keys** — every location is placed in a cell, and the mapped value
  is reconciled against the stated value. A run whose keys do not reconcile does
  not proceed quietly.
- **smoke** — a short run over a reduced event set, to catch a broken
  configuration before the full loss run.

### 14. Read the results

**Results.** Each perspective becomes a result set carrying its average annual
loss, standard deviation and return-period losses, plus the model version and
assumption set behind it.

From there:

- Event loss table — which events drove it
- Loss by area-peril cell, with its map
- EP curve chart
- Two-result comparison, and scenario ranges across assumption sets

The reinsurance perspective is the loss **net** of reinsurance. The ceded amount
is the insured loss less it.

---

## Reading the numbers honestly

Three things the platform records that are easy to skip past:

**The hazard is one draw.** A set samples twenty logic-tree paths and pools
them. Return periods at the far end — the 500- and 1,000-year losses off a
thousand-year catalogue — are the worst one or two years in it, and are not
precisely estimated. The average annual loss is the stable number.

**Site conditions default to rock.** Where the published site model has no
measurement within the join limit, a cell falls back to the model's reference
conditions. Nationally in Indonesia that is about 79% of cells. Rock understates
loss anywhere the ground is softer, and the run's site report says how much of
the grid it applied to.

**A geocode is not a coordinate.** Where a location resolves only to a locality
or a postcode, the cell it lands in is close to a coin-flip between neighbours.
The geocoding sensitivity report puts a value on how much of the book that
affects.

---

## If something goes wrong

| Symptom | Cause |
| --- | --- |
| Building a vulnerability set is refused | No GEM release chosen — step 3 |
| `build-package` refused | The converter-candidate approval has not been decided |
| A run fails at `publish_oed` naming two model versions | The worker serves another version's package. Rebuild this one's — step 8 |
| A run fails at `publish_oed` on a currency | The book is stated in a currency with no approved rate |
| Exposure refuses to publish | Open findings in the review queue or the structure builder |
| Locations report as outside the domain | There is no hazard where they are — see the limit at the top |

Logs: `make logs` follows the API and worker. Every run also carries its own
event log on the **Runs** screen, and a correlation ID that travels into the
worker's structured logs.

While a run is going, the **Runs** screen shows the clock running and, on the
stage it is in, how far through the engine says it is — OpenQuake's own
percentage for the phase it names, Oasis's completed sub-tasks. Neither is a
time remaining: nothing here predicts when a calculation will finish. The
timing note in step 6 is the only guide to that.
