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

**The converter refuses to run.** Event semantics and multi-IMT representation
are open decisions in section 16, so the converter has no default for either: a
conversion under an unapproved policy raises rather than picking something
plausible. Converting every vulnerability function to one common intensity
measure is rejected even when chosen explicitly, because section 6 requires its
own scientific derivation and approval first.

## What is not built

Ordered as the roadmap orders it.

- **Phase 2, engine vertical slice.** The Oasis adapter, keys generation
  through the platform, `generate-oasis-files`, and loss submission. The
  analysis builder shows its preconditions and states that submission awaits
  this.
- **Phase 3, hazard.** OpenQuake job submission, the Indonesia and Nepal
  adaptive grids, and site-condition treatment. The grid model and its
  versioning rules exist; the geometry does not.
- **Phase 4, converter.** Chunked HDF5 reading and vulnerability
  discretisation. The framework around them — policy gating, deterministic
  identifiers, intensity binning, streaming footprint accumulation, frequency
  reconciliation — is built and tested.
- **Phase 5 onward.** Enrichment execution, maps, EP curves and comparison
  views.

None of this is blocked by the platform. Each is blocked by a scientific
decision or an engine integration that the plan sequences deliberately.

## Outstanding decisions that gate the model

From section 16, visible in the interface on the model build screen:

| Decision | Position | Why it matters |
| --- | --- | --- |
| Event representation | Formal study before converter build | Controls frequency, uncertainty, correlation and footprint probabilities |
| Multi-IMT representation | Prototype correlated channels, custom GUL and an OpenQuake-loss fallback | Determines whether GEM vulnerability can be represented faithfully in Oasis |
| Oasis static storage format | Select Parquet or binary through performance tests | Earthquake footprints may be too large for uncompressed CSV |
| Secondary peril scope | Declare per country release | Defines what "earthquake loss" means |

The GEM public models are CC BY-NC-SA. KRE's intended use supports commercial
reinsurance decisions, so it remains a research activity until GEM confirms
permitted commercial use in writing. The seeded model version records this as a
publication blocker rather than assuming it resolved.

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
