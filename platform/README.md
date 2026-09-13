# CASS — Catastrophe Analytics and Scenario Suite

The internal earthquake portfolio analysis platform for Klapton Reinsurance
PLC, built to the [build plan](../deliverables/Klapton%20Re%20Earthquake%20Catastrophe%20Modelling%20Platform%20Build%20Plan.md).

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

**A run states what it is for before it runs.** The four modes of the brief's
section 5.2 are a choice in the analysis builder: geometry only, which maps the
book through CASS keys and stops without touching the engine; KRE-share
technical loss; portfolio-loss research; and decision use. Only the last can
produce a result a reviewer may approve, and it is refused at configuration
against a research prototype or an unapproved assumption set. A run nobody
labelled is a technical one, because a number nobody characterised must not be
able to become a decision. A comparison that mixes two modes says so.

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
  approved references, and the converter reads OpenQuake's CSV exports rather
  than the HDF5 datastore.
- **Analyst product.** Maps, EP charts, event loss tables, geographic summaries,
  the financial structure workspace and the OED standards registry.
- **Production readiness.** Backup and restore, observability, single sign-on
  and MFA, upload scanning, retention expiry and signed releases.

## Outstanding decisions that gate the model

From section 16 as revised in plan 1.8, visible in the interface on the Build
tab:

| Decision | Position | Why it matters |
| --- | --- | --- |
| Event representation | Occurrence per event in use under a converter approval; formal study not reported | Controls frequency, uncertainty, correlation and footprint probabilities |
| Multi-IMT representation | Correlated area-peril channels for classes resolving to one measure; multi-measure classes refused | Determines whether GEM vulnerability can be represented faithfully in Oasis |
| Realisation weighting | One logic-tree path sampled per hazard run | A single path understates hazard uncertainty |
| Oasis static storage format | Interim ktools binaries; Parquet not measured | Earthquake footprints may be too large for uncompressed CSV |
| Secondary peril scope | Declare per country release | Defines what "earthquake loss" means |

Model data is held under one internal-use basis: used inside Klapton Re, not
redistributed and not sold ([ADR 7](docs/adr/0007-internal-use-licence-basis.md)).
The GEM models and PuSGeN 2024 are CC BY-NC-SA, and GEM has not confirmed in
writing that the NonCommercial term permits internal use supporting pricing and
reserving. That question, and legal review before any use outside KRE, remain
open.

## The name

CASS is the Catastrophe Analytics and Scenario Suite. The name nods to
Cassandra, the prophetess granted foresight and cursed never to be believed.
CASS is built for the opposite outcome: foresight that is evidenced,
reproducible and traceable to its inputs, so that a decision-maker can act on
it. Every design rule in this platform — immutable published versions,
checksummed artifacts, an assumption that cannot overwrite reported data, a
model that publishes as a research prototype while its blockers stand — exists
to make the output believable.

CASS is the product. Klapton Reinsurance PLC (KRE) is the organisation that
owns and operates it, which is why `KRE` still appears against licence
positions, approver roles, the Oasis supplier ID and the house brand colour.
