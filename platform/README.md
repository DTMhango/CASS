# CASS — Catastrophe Analytics and Scenario Suite

An internal research tool for earthquake portfolio analysis at Klapton
Reinsurance PLC, built to the [build plan](../deliverables/Klapton%20Re%20Earthquake%20Catastrophe%20Modelling%20Platform%20Build%20Plan.md).

CASS is for research. Its results are not a basis for pricing or reserving.
The build plan described a governed decision platform, which is where the
platform roles, approval gates, the decision-use run mode and result approval
come from. They stay as built, and the remaining work is scoped for research
([ADR 15](docs/adr/0015-research-tool-and-gem-permission.md)).

React provides the analyst experience. Django provides authentication,
workflow orchestration, metadata, lineage and a stable CASS API. OpenQuake and
Oasis remain independently owned calculation engines, reached through versioned
adapters over their supported APIs.

## Layout

Backend and frontend are separate top-level trees.

```
platform/
  backend/                 Everything Python
    cass_api/              Django control plane: identity, orchestration, audit
      apps/                accounts, projects, artifacts, audit, modelregistry,
                           exposure, runs, results
    packages/
      cass_core/           Artifact store, checksums, run state machine, evidence
      cass_oed/            OED schema subset, reader, validation, perspectives
    services/
      keys/                The exposure-to-model lookup boundary
      converter/           OpenQuake to Oasis conversion
    adapters/              OpenQuake and Oasis adapters
  frontend/                React and TypeScript
    src/api/               The single route to the CASS API
    src/components/        Design system primitives
    src/layout/            Navigation rail and the persistent context bar
    src/pages/             The screens of build plan section 3
  deploy/                  Compose file, Dockerfiles, environment template
  docs/                    Decision records and schemas
  tests/fixtures/          Cross-service fixtures, including official PiWind
```

## Getting started

You need Python 3.11, Node 22 and Docker.

```bash
cd platform
make setup          # virtualenv, npm ci, and a .env from the template
```

Fill in the four secrets `make setup` leaves blank in `platform/.env`:
`CASS_SECRET_KEY`, `POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD` and
`OASIS_ADMIN_PASS`. Generate them with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Then bring up the control plane and seed a demonstration workspace:

```bash
make up
make migrate
make seed
```

The interface is on <http://localhost:8080>, the API on
<http://localhost:8000>, its documentation on
<http://localhost:8000/api/docs/>, and the MinIO console on
<http://localhost:9001>. Sign in as `analyst`, `modeller` or `reviewer`.

For faster iteration, run the two halves outside Docker against the compose
database:

```bash
make dev-api        # Django on :8000
make dev-web        # Vite on :5173, proxying /api
```

The engines are behind a compose profile, because OpenQuake and Oasis are large
and are not needed for interface or control-plane work:

```bash
make up-engines
```

## Checks

```bash
make check          # lint, typecheck and both test suites
make test-backend
make test-frontend
make schema         # regenerate the OpenAPI contract, failing on any warning
```

CI runs the same targets, builds all four container images, scans them, and
validates the compose file in both profiles.

### The live PiWind baseline

Tests marked `integration` need a real engine and are deselected by default, so
an ordinary run needs no containers. One suite is marked that way: the official
PiWind portfolio driven through CASS against a live Oasis Platform, which is
where the two halves of the boundary — what CASS sends and what Oasis actually
accepts — are checked against each other.

It needs the PiWind model root mounted and the worker identified as that model:

```bash
export CASS_MODEL_DATA_PATH=/path/to/OasisPiWind
export OASIS_MODEL_SUPPLIER_ID=OasisLMF OASIS_MODEL_ID=PiWind OASIS_MODEL_VERSION_ID=1
docker compose -f deploy/docker-compose.yml \
               -f deploy/docker-compose.integration.yml \
               --env-file .env --profile engines up -d \
               oasis-api oasis-celery oasis-worker

cd backend
CASS_OASIS_LIVE_URL=http://localhost:8100/api \
CASS_OASIS_LIVE_PASSWORD="$OASIS_ADMIN_PASS" \
  .venv/bin/python -m pytest -m integration cass_api/tests/test_piwind_live.py
```

The model root is the directory holding `model_data/`, `keys_data/`,
`meta-data/` and `oasislmf.json` — the layout the worker's `conf.ini` expects at
`/home/worker/model`. The integration overlay publishes the Oasis port to the
host; the production compose deliberately does not, because an engine API is
reached through the CASS API and never directly.

`make up-engines` starts the same services without publishing that port.

## What is built

**The M1 journey works end to end.** An analyst signs in, creates a project,
attaches OED files, validates them, reads findings against the business record
that caused each one, previews the OED interpretation and publishes an
immutable version.

**The boundary rules are enforced, not documented.** Large arrays live in the
artifact store behind `cass://` URIs with checksums and retention classes, never
in PostgreSQL. Published exposure versions and results are immutable in the
model layer, so a management command cannot bypass it either. The browser
reaches only the CASS API.

**The governance rules are enforced too.** An assumption cannot overwrite
reported data. Weighted TIV allocation reconciles exactly, in `Decimal`. A
person cannot approve a gate they requested. A perspective the source data does
not support is refused rather than silently producing zero. A model version
whose licence or IMT coverage is outstanding publishes as a research prototype
with its blockers listed, not as an approved model.

**The converter runs only under an approved policy.** Event semantics and
multi-IMT representation are open decisions in section 16, so the converter has
no default for either. A package is built only under a converter-candidate
approval that somebody other than the requester decided, naming occurrence per
event and intensity measures carried as area-peril channels
([ADR 8](docs/adr/0008-intensity-measures-as-area-peril-channels.md)).
Converting every vulnerability function to one common intensity measure is
rejected even when chosen explicitly, because section 6 requires its own
scientific derivation and approval first.

**The M2 engine slice works against a real Oasis.** An analysis run publishes
its frozen OED to an Oasis portfolio, maps it through the CASS keys service,
clears the section 8 reconciliation gate, generates the kernel files, runs the
losses and collects the ORD package as a checksummed artifact — driven from the
CASS API, with no one opening the native Oasis interface. The official PiWind
portfolio does this end to end in the `integration` suite.

**Value that cannot be mapped stops the run rather than disappearing.** Keys
reconciliation distinguishes two things. Successful, not-at-risk and failed TIV
that do not sum to the published source means the lookup lost value, which is a
defect and fails. Value that adds up but the model could not map is a fact
about the portfolio, and the run holds in `blocked` until someone records a
run-exception approval. A blocked run is not a failed one: it keeps its
progress, has its own `gate_summary`, and resumes from the gate rather than
republishing its portfolio.

**Hazard runs on OpenQuake from a published model.** A national package such as
PuSGeN 2024 is uploaded, inspected and configured on the Hazard tab. The
classical calculation it publishes is converted to an event-based run with every
change listed, the model's own science is shown but not offered for editing, a
run can be limited to a region, and the published Vs30 is joined to the cells it
covers ([ADR 12](docs/adr/0012-national-classical-model-run-event-based.md)).
The run goes through the OpenQuake REST API and registers a hazard set with one
footprint per intensity measure.

**Hazard is read from the engine's own record.** A conversion reads ground motion either from OpenQuake's CSV exports or from the HDF5 datastore the
calculation already wrote. The datastore is read a slice at a time under a
stated row budget: its rows arrive in neither event nor site order, and the
footprint accumulator needs one complete event at a time, so events are
counted once and then read in batches that fit. Memory follows the budget
rather than the size of the calculation, which is what makes a national run
possible without writing a second copy of the ground motion as text.

**A grid is built for whatever country is being worked on.** Section 6 wants a
fixed, versioned, adaptive grid per country, independent of any portfolio. A
modeller writes the specification on the Build tab — the tiles that say what is
modelled, the base resolution, the areas refined and the reason for each — and
CASS generates the cells from it, refusing a specification that would exceed the
installation's cell limit with its own count rather than taking an hour to find
out. The Indonesian pilot grid is now just a specification that happens to be
compiled in, registered through the same door; the Nepal prototype, which was
test data, is gone.

**A country's vulnerability set is built from GEM under a written enrichment.**
The functions and the stock weights are GEM's, read from the release the
installation points at, and the Build tab lists the countries that release
actually covers. What GEM does not say is which seismic design level a building
of a given age has — that depends on when a code was adopted and whether it was
enforced — so the design eras are stated as a table, each row with its reason,
rather than compiled in for two pilot countries. The set's version follows the
enrichment, because the enrichment is the assumption behind every function in
it, and an era table that is out of order or ends before today is refused rather
than applied.

**GEM's release lives on your device, not in CASS.** The 2026 release is too
large to carry in the codebase, and it is GEM's to publish. Keep GEM's
`global_exposure_model` and `global_vulnerability_model` repositories side by
side in the models folder the installation mounts (`CASS_MODELS_PATH`), cloned
with git, and choose the release on the Build tab. CASS lists the releases it
finds, checks each has GEM's layout and the files a build reads, and reads the
commit each repository is at, so it can say which release it is and whether it
is the one CASS was validated against. Nobody edits an environment file or
restarts anything.

**The two halves are assembled into the version a run names.** A grid says where
a loss can be computed and a vulnerability set says how much damage the shaking
does; the model version is the pair, and until it exists neither half is
runnable. Assembling one asks for nothing but the two halves and a version of
its own: the peril scope and the limitations are written from what was paired,
so a version's caveats cannot drift from its parts, and a pair from two
different countries is refused — such a version would calculate happily and mean
nothing. What comes out is a draft research prototype that says what still
blocks it, usually a hazard set and the multi-IMT decision. With this in place a
country CASS ships nothing for can be built on the platform from end to end,
which is why the pilot countries' test data is no longer load-bearing.

**A result says where its loss is.** Every analysis asks the engine for a
location-level summary beside the portfolio one, carrying only each location's
average annual loss — the output that decides a national run's size is the
per-location event table, and a map needs none of it. At publication each
location's loss is placed in the cell the run's own keys mapped it to, so a
cell's loss is the loss calculated against that cell's hazard, and the cells are
summed. The results workspace draws them as a grid plot with the exact table
beside it, and says how much loss could not be placed and how far the locations
add back to the portfolio number.

**A financial structure is built on the platform.** A portfolio that arrived
with locations alone can be given its policies and treaties on the financial
structure screen: policies with their layers, and quota share, surplus share and
catastrophe excess of loss contracts with the scope each reaches. They are
written into the draft version's own OED files, validated like an imported file
and read straight back with their findings. The forms ask only for the terms the
engine uses for each type, and refuse what it would reject — a surplus share
must name each risk and the share ceded on it — so a structure built here is one
the engine runs.

**A result reports its range across assumption scenarios.** The same book on the
same model, run under each assumption set, is a scenario range: the baseline is
the central estimate, every metric names the scenario at each end of its range,
and the assumptions are ranked by how far they move any number. Everything else
is held still — the model version, perspective, currency, run mode and ORD basis
must all match, and so must a digest of every setting that decides the losses
apart from the assumption set — so the spread can be put down to the assumption.
A result calculated otherwise, or published before that digest was recorded, is
left out and counted, and the range says what it does not vary, so it is not
read as the whole uncertainty.

**A coarse geocode is tested against the cell it was given.** Cohort B rows need
no review and sit in the right country, but their geocodes resolve only to a
locality, a postcode or an administrative area, and a grid fine enough to
separate districts can give such a row a cell the geocode cannot vouch for. The
review screen tries each one across the area its precision stands for, against a
chosen grid, and says which keep their cell, how much of each buffer stays in it,
and how much value sits on the ones that move. The buffers are assumptions — 5 km
for a locality or postcode, 25 km for an administrative match — so they are shown
on the report and can be changed. On the 30 June book, none of the 44 keeps its
cell at the pilot grids.

**The conversion is measured against the engine's own answer.** A finished
analysis run can be compared with an OpenQuake risk calculation on the same
events: `compare_with_openquake` rebuilds the run's keys and blend weights as
GEM taxonomies, at the centroids of the cells they mapped to and carrying the
same value to the cent, and chains the risk job onto the hazard calculation the
footprint was built from. What is left between the two sides is the conversion
itself — binned intensity, discretised damage, and measures carried as channels.
On the live stack the Jakarta–Bandung book came back at 0.904 of the engine's
average annual loss. The report carries ratios and grades none of them, because
no tolerance is approved.

**CASS builds the Oasis model package.** A model version with a hazard set
attached becomes the directory the Oasis worker loads. CASS writes the binaries,
held byte-for-byte against PiWind's, and vendors its lookup inside, so the
engine's lookup is the one CASS reconciled against
([ADR 9](docs/adr/0009-cass-writes-the-oasis-package.md)). The worker carries a
build-time patch for three oasislmf 2.5.7 defects that otherwise stop every
insured and reinsurance run ([ADR 10](docs/adr/0010-patched-oasis-worker.md)).

**An analysis ends in results a person can use.** The ORD package is read into
result sets — draft, or research for a prototype model — with the exceedance
basis recorded. Results can be approved, exported with their manifest, and
compared, with the arithmetic done on the server in `Decimal`. The invented
Jakarta–Bandung book in `samples/` runs ground-up, insured and reinsurance to
results against the live stack.

**A book in another currency is converted with evidence, not assumed.** The
Oasis Financial Module cannot calculate multi-currency terms, so a portfolio
whose currency is not the run's is normalised by CASS before generation — at a
rate somebody recorded with its source, valuation date and direction, and
somebody else approved. An unapproved rate is refused as firmly as a missing
one. The published exposure keeps the values the business reported; the
converted files the engine received are stored beside the run, and the rate
travels with the result.

**Administration is an administrator's, and cannot lock the installation out.**
An administrator changes a person's role or access on the Administration screen;
the API refuses an administrator removing their own administration, and any
change that would leave no active administrator. The same screen shows what
each resource profile is running, what the store holds by retention class and
what the nightly sweep will remove next. The support bundle an operator sends
when something is wrong carries settings by allowlist, so a secret added later
is left out by default, and failures by stage and correlation ID rather than by
their text, which names the portfolio. Viewing it is audited as a read and
downloading it as a download.

**One correlation ID follows a run into the worker, and the platform reports on itself.**
The request's correlation ID travels in the task's headers and is restored in the worker,
whose log lines are the same structured JSON the API writes, and a run records the
ID when it is queued. `/metrics/` serves Prometheus gauges for runs, profile capacity,
recent failures, artifacts, results and open approvals, read from the records at
scrape time rather than counted in memory, behind a scrape token. The API, the worker
and the scheduler run one image, so a single build cannot leave a worker on stale code.

**A run states what it is for before it runs.** The four modes of the brief's
section 5.2 are a choice in the analysis builder: geometry only, which maps the
book through CASS keys and stops without touching the engine; KRE-share
technical loss; portfolio-loss research; and decision use. Only the last can
produce a result a reviewer may approve, and it is refused at configuration
against a research prototype or an unapproved assumption set. A run nobody
labelled is a technical one, because a number nobody characterised must not be
able to become a decision. A comparison that mixes two modes says so. An
approved result is a reviewed research result: CASS does not support pricing
or reserving.

**A portfolio arrives through the intake template.** Risks and policies are
joined on the Policy ID stated on both sheets, value is read in three recorded
tiers, and storeys are collected because height decides which intensity measure
a class responds at ([ADR 11](docs/adr/0011-intake-template-and-policy-id.md)).
The 30 June extract was migrated into the template once. The rows of any
attached OED file can be read and corrected on the platform, checked against
the file's own schema; a published version is corrected into the next one.

Coordinate presence is not coordinate eligibility. Every location in the real
extract has a valid coordinate pair and 115 of 224 still need review, so rows
are assigned to governed cohorts — A precise and unflagged, B coarse and
unflagged, C flagged — with the rule version that assigned them, and cohort C
is the review queue.

The acceptance checks in the integration brief are numbers against the real
workbook, which is confidential and not in this repository. They live in
`test_extract_acceptance.py`, marked `integration` and skipped unless pointed
at a copy:

```bash
CASS_EXTRACT_PATH=/path/to/premium_policies_2026-06-30_geocoded.xlsx \
  .venv/bin/python -m pytest -m integration cass_api/tests/test_extract_acceptance.py
```

### Running an analysis needs model assets

A model version cannot map anything until its grid cells and vulnerability
mapping are attached — `apps.modelregistry.assets` registers both as immutable
model-asset artifacts and is the only place the control plane parses them. Both
are plain CSV, because a grid is a governed artifact a reviewer has to be able
to read and diff without a tool:

```
AreaPerilID,MinLatitude,MaxLatitude,MinLongitude,MaxLongitude,CountryCode,Offshore,Vs30
VulnerabilityID,CoverageTypeID,RequiredIMT,OccupancyCodes,ConstructionCodes,Label
```

Multi-valued taxonomy columns separate codes with `|`. A run against a model
version with no cells fails with that as the reason rather than proceeding on
an empty grid and mapping nothing.

## What is not built

The working tracker is [docs/delivery-status.md](docs/delivery-status.md). It
goes milestone by milestone and stage by stage, and separates what is buildable
from what waits on a decision or an outside input. In short:

- **Scientific gates.** The hazard benchmark and conversion QA stand open with no
  approved references.
- **Analyst product.** Maps, geographic summaries and scenario ranges. A result
  carries its exceedance curve and the events behind it; what it does not carry
  is loss below the portfolio level, which is an engine settings change.
- **Keeping and repeating research work.** Backing up the database and the
  artifact store, and pinning engine image digests so a run can be repeated on
  the same engines. Single sign-on, MFA, separately deployed keys and converter
  services and signed releases are not pursued, because CASS is a research tool.

## Outstanding decisions that gate the model

From section 16 as revised in plan 1.8, visible in the interface on the Build
tab:

| Decision | Position | Why it matters |
| --- | --- | --- |
| Event representation | Occurrence per event, recorded on each package by its converter approval. CASS is to measure it against the rupture-binned alternative and decide; the sign-off is dropped | Controls frequency, uncertainty, correlation and footprint probabilities |
| Multi-IMT representation | Correlated area-peril channels for classes resolving to one measure; multi-measure classes refused. CASS is to measure the candidates against an OpenQuake reference calculation and decide | Determines whether GEM vulnerability can be represented faithfully in Oasis |
| Realisation weighting | One logic-tree path sampled per hazard run. CASS is to measure what that costs and decide the rule | A single path understates hazard uncertainty |
| Oasis static storage format | Interim ktools binaries. CASS is to measure a national footprint against Parquet and decide | Earthquake footprints may be too large for uncompressed CSV |
| Secondary peril scope | Declare per country release | Defines what "earthquake loss" means |

The four CASS is to decide are backlog items 21, 22, 23 and 26 in the tracker,
and each ends in a decision record. The last row waits on a scope decision.

GEM Foundation has given explicit written permission to use the data and models
it makes publicly available, for the use KRE described to it: the Global
Exposure and Vulnerability models, and the national hazard models in its
mosaic, PuSGeN 2024 among them. GEM vulnerability sets and GEM-published hazard
models record that permission
([ADR 15](docs/adr/0015-research-tool-and-gem-permission.md)). The authors and
GEM must be credited as the source, and anything redistributed carries the same
CC BY-NC-SA terms. Data GEM does not publish is held under the internal-use
basis: used inside Klapton Re, not redistributed and not sold
([ADR 7](docs/adr/0007-internal-use-licence-basis.md)). OpenQuake is used
unmodified under the AGPL. Legal review is still needed before model data, or a
package derived from it, leaves KRE.

## The name

CASS is the Catastrophe Analytics and Scenario Suite. The name nods to
Cassandra, the prophetess granted foresight and cursed never to be believed.
CASS is built for the opposite outcome: foresight that is evidenced,
reproducible and traceable to its inputs, so that research built on it can be
believed. Every design rule in this platform — immutable published versions,
checksummed artifacts, an assumption that cannot overwrite reported data, a
model that publishes as a research prototype while its blockers stand — exists
to make the output believable.

CASS is the product. Klapton Reinsurance PLC (KRE) is the organisation that
owns and operates it, which is why `KRE` still appears against licence
positions, approver roles, the Oasis supplier ID and the house brand colour.
