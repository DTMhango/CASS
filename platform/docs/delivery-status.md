# CASS delivery status

Updated 13 September 2026. Measured against
[build plan 1.8](../../deliverables/Klapton%20Re%20Earthquake%20Catastrophe%20Modelling%20Platform%20Build%20Plan.md)
and the
[geocoded portfolio brief](../../deliverables/CASS%20Geocoded%20Portfolio%20Test%20Dataset%20Integration%20Instructions.md).

Update this file in the same change that moves any line in it. A tracker
updated afterwards is a tracker somebody has to reconcile.

## Evidence at this revision

- 1,594 backend tests pass, 1 skipped. 97 integration tests pass against the
  real GEM v2026.0.0 files, the PuSGeN 2024 package and the 30 June workbook.
  83 frontend tests pass. Ruff, ESLint and TypeScript are clean, the OpenAPI
  contract matches the code, and no model change lacks a migration.
- On the live Docker Compose stack, on 13 September, the invented Jakarta–Bandung
  book in `samples/` (64 locations, USD 1.36bn) ran ground-up, insured and
  reinsurance through the patched Oasis 2.5.7 worker to collected result sets.
  The hazard behind it is a PuSGeN 2024 set built on the platform: 27,313
  events over 962 cells, all four intensity measures.
- The official PiWind portfolio runs end to end through CASS in the live
  integration suite.

## Milestones (plan section 13)

| Milestone | Status | What shows it | Still missing |
| --- | --- | --- | --- |
| M1 Foundation | Met | Sign in, projects, OED attach, validation, preview, publication, background runs | — |
| M2 Engine integration | Met, one stage short | PiWind live suite; the sample book through keys, generation, losses and collection | The pre-loss `smoke` check |
| M3 Hazard | Partly met | OpenQuake adapter and hazard runs; PuSGeN 2024 on the Jakarta–Bandung region; published Vs30 joined to 37% of cells | Benchmark gate; full-country run; realisation weighting; Nepal source model |
| M4 Conversion | Partly met | Four-measure footprints with frequency preserved; package built under a converter approval | Reading the HDF5 datastore instead of CSV exports; QA gate; OpenQuake reference comparison |
| M5 Loss | Not met | Ground-up, insured and reinsurance with keys reconciliation; allocation scenarios reconcile exactly | Applying an assumption set within a run; currency evidence |
| M6 Product | Partly met | Result approval, export and two-result comparison | Maps, EP charts, event loss tables, geographic summaries, financial structure workspace, scenario ranges |
| M7 Production | Not met | CI builds and scans the CASS images | Backup and restore drill, and the rest of sections 10 and 11 |

## Pipelines

### Analysis

| Stage | Status |
| --- | --- |
| `validate_exposure` | Done before submission in the exposure workspace; not repeated inside the run |
| `enrich` | Not built: no run applies an assumption set, and nothing creates an `EnrichmentRun` |
| `publish_oed` | Done |
| `keys` | Done, through CASS keys |
| `reconcile_keys` | Done; unmapped value blocks for a run-exception approval |
| `generate_inputs` | Done |
| `validate_inputs` | Done; compares Oasis's lookup with the CASS keys result |
| `smoke` | Not built |
| `losses` | Done: ground-up, insured, reinsurance (reinsurance at portfolio level only, [ADR 10](adr/0010-patched-oasis-worker.md)) |
| `collect` | Done: ORD package stored, result sets published as draft or research |
| `review` | Not performed as a stage; results are approved one by one |

### Hazard

| Stage | Status |
| --- | --- |
| `prepare`, `validate_settings`, `submit`, `monitor`, `export` | Done |
| `benchmark` | Gate stands open: no approved benchmark curves, and no comparison machinery |

### Conversion

| Stage | Status |
| --- | --- |
| `manifest`, `events`, `occurrence`, `footprint`, `vulnerability`, `package` | Done, from a registered hazard set |
| `qa` | Gate stands open: no approved acceptance tolerances, and no report machinery |

## Screens (plan section 3)

| Screen | Status | Missing |
| --- | --- | --- |
| Portfolio dashboard | Built | Storage and model notices |
| Model catalogue | Built, Models tab | — |
| Exposure workspace | Built, Exposure tab: intake import, OED attach, validation, row correction, publication | Assumption scenarios; reported-versus-inferred display across attributes |
| Financial structure workspace | Not built | Accounts and layers, contracts, scope preview, inuring, reconciliation |
| Analysis builder | Built | Assumption set, run mode, output selection |
| Run monitor | Built: stages, events, artifacts, keys gate, cancel, retry | — |
| Results workspace | Partly built: AAL, return-period table, caveats, approval, export, comparison | Maps, EP curve charts, event tables, scenario ranges |
| Model build workspace | Built, Hazard and Build tabs | Benchmark and QA evidence views |
| Administration | Partly built: installation facts, compatibility, profiles, engine health, users, audit search | User and role changes, queues, storage, retention, support bundle |

## Geocoded portfolio brief

| Work package | Status |
| --- | --- |
| WP1 Secure importer | Done, then superseded by the intake template ([ADR 11](adr/0011-intake-template-and-policy-id.md)); acceptance counts in `test_extract_acceptance.py` |
| WP2 Eligibility and review interface | Done |
| WP3 Area-peril and keys test | Partly done: mapping, offshore and outside-domain reporting. Cohort B sensitivity and OpenQuake site comparison not built |
| WP4 Controlled Oasis earthquake test | Steps 1 to 8 done against the fixture model; step 9, the OpenQuake reference comparison, not built |
| WP5 Portfolio-loss readiness | Not started: section 5.1 questions unanswered, enrichment not applied in a run |

## Where the design changed

| Area | Plan 1.7 | Now | Record |
| --- | --- | --- | --- |
| Model data rights | Research only until GEM confirms commercial use in writing | One internal-use basis; legal confirmation still open | [ADR 7](adr/0007-internal-use-licence-basis.md) |
| Intensity measures | SA only, PGA deferred | All four measures as correlated area-peril channels; multi-measure classes refused | [ADR 8](adr/0008-intensity-measures-as-area-peril-channels.md) |
| Model package | Binaries compiled with Oasis tools | Written by CASS, byte-checked against PiWind, CASS lookup inside | [ADR 9](adr/0009-cass-writes-the-oasis-package.md) |
| Engine images | Unmodified upstream | Oasis worker patched at build time; OpenQuake unmodified | [ADR 10](adr/0010-patched-oasis-worker.md) |
| Portfolio intake | Two-sheet extract; role-gated names; cedant segmentation | Intake template joined on Policy ID; no role gate; no cedant | [ADR 11](adr/0011-intake-template-and-policy-id.md) |
| Hazard source | GEM source models | PuSGeN 2024 converted to event-based, one sampled path | [ADR 12](adr/0012-national-classical-model-run-event-based.md) |
| Navigation | One screen per sidebar entry | Models and Exposure areas with tabs | [ADR 13](adr/0013-product-areas-hold-tabs.md) |

## Backlog: buildable in code

| # | Item | Plan | Status |
| --- | --- | --- | --- |
| 1 | Analysis pipeline: exposure validation inside the run, `enrich` applying an assumption set with reconciliation, `smoke` on a reduced event set, `review` stage | §8, M2, M5 | Not started |
| 2 | Run modes: geometry-only, technical loss, research, decision use | Brief §5.2 | Not started |
| 3 | Currency conversion evidence captured and applied before generation | §8 | Not started |
| 4 | Results: event loss tables, geographic summaries, EP curve chart, map, scenario ranges | §3, M6 | Not started |
| 5 | Financial structure workspace | §3, M5 | Not started |
| 6 | OED standards registry: `DataStandardVersion`, pinned OED 4.0.0 and 5.0.0 reference JSON, schema API, ODS Tools validation, version diff | §8, §17 | Not started |
| 7 | Converter reads the OpenQuake HDF5 datastore in chunks | §7, M4 | Not started |
| 8 | Hazard benchmark and conversion QA gates: registered references, comparison, report | §7, M3, M4 | Not started |
| 9 | OpenQuake reference loss comparison for a controlled portfolio | §7, WP4 | Not started |
| 10 | Cohort B geocoding sensitivity | WP3 | Not started |
| 11 | Direct-to-store uploads completed, checksummed and scanned | §5, §10 | Not started |
| 12 | Execution profiles enforced: time limits and admission control | §11 | Not started |
| 13 | Keys and converter HTTP services | §4 | Not started |
| 14 | Artifact retention expiry | §5 | Not started |
| 15 | Administration: users and roles, queues, storage, retention, support bundle | §3, §4 | Not started |
| 16 | Observability: metrics, correlation IDs through background tasks | §4 | Not started |
| 17 | Multi-factor authentication and single sign-on configuration | §10 | Not started |
| 18 | Backup and restore tooling, and a restore drill | §11, M7 | Not started |
| 19 | CI: Oasis worker image build and scan, SBOMs, integration workflow | §10, §17 | Not started |
| 20 | Pinned image digests and a checksummed release bundle | §4, §18 | Not started |

## Needs a decision or outside input

These cannot be closed by writing code. Where a backlog item builds the
machinery around one, the decision still has to be taken.

- Sign-off of the event representation study.
- A representation for classes whose taxonomies respond at several intensity
  measures.
- A realisation-weighting rule for hazard.
- Parquet or binary footprints, which needs a national-scale measurement.
- Secondary-peril scope for each country release, and business interruption
  treatment.
- GEM's written position on internal use supporting pricing and reserving
  ([ADR 7](adr/0007-internal-use-licence-basis.md)), and legal review before
  any use outside KRE.
- A Nepal seismic source model, a denser Vs30 source, and the licensed GEM
  ~1 km exposure.
- Approved hazard benchmark curves and converter QA tolerances, with named
  reviewers.
- The brief's section 5.1 questions: coverage-component split, replacement cost
  or indemnity value, deductibles, limits and attachments, shared or scheduled
  multi-location limits, renewals.
- A full Indonesian grid run: 52,831 cells, hours of compute.
- Production choices: identity provider, message broker, and availability and
  recovery targets.
