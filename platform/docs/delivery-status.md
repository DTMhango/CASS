# CASS delivery status

Updated 13 September 2026. Measured against
[build plan 1.8](../../deliverables/Klapton%20Re%20Earthquake%20Catastrophe%20Modelling%20Platform%20Build%20Plan.md)
and the
[geocoded portfolio brief](../../deliverables/CASS%20Geocoded%20Portfolio%20Test%20Dataset%20Integration%20Instructions.md).

Update this file in the same change that moves any line in it. A tracker
updated afterwards is a tracker somebody has to reconcile.

## Purpose

CASS is a research tool. Its results are not a basis for pricing or reserving.

Build plan 1.8 described a governed decision platform, and much of what is built
follows from that: platform roles, approval gates, the decision-use run mode,
result approval and production controls. That stays as built.

The work that remains is scoped for research: validating the model against
reference calculations, sensitivity studies, reading results and reproducing a
run. Production items that serve only a governed deployment are not pursued
([ADR 15](adr/0015-research-tool-and-gem-permission.md)).

## Evidence at this revision

- 1,901 backend tests pass, 1 skipped. 99 integration tests pass against the
  real GEM v2026.0.0 files, the PuSGeN 2024 package, the 30 June workbook, the
  pinned ODS Tools specifications and an OpenQuake datastore the engine wrote.
  112 frontend tests pass. Ruff, ESLint and TypeScript are clean, the OpenAPI
  contract matches the code, and no model change lacks a migration.
- On the live stack, one Jakarta–Bandung book ran twice through the patched
  Oasis under two assumption sets, and the engine took the set the run named:
  `model_settings.vulnerability_set` was `baseline` on one and `more_vulnerable`
  on the other, against one package carrying all three. The keys, their
  reconciliation and the insured value were identical, so the assumption is the
  only difference between the two numbers. The tilt is a change of curve shape
  rather than a severity dial: over the functions this book maps to it lowers
  expected damage by about 0.5% below intensity bin 28 and raises it by up to
  5.1% above, so the sampled AAL fell 0.12% while the 200-year loss rose 0.99%
  and the 1,000-year loss 0.85%. The pilot tilts stay draft on that evidence.
- The smoke check's engine behaviour was confirmed against the live Oasis: an
  analysis re-runs on a 25-event `event_ids` subset, reports `RUN_QUEUED` at once
  rather than its previous completion, finishes in 19 seconds, and writes only
  the requested moment event loss table.
- On the live Docker Compose stack, on 13 September, the invented Jakarta–Bandung
  book in `samples/` (64 locations, USD 1.36bn) ran ground-up, insured and
  reinsurance through the patched Oasis 2.5.7 worker to collected result sets.
  The hazard behind it is a PuSGeN 2024 set built on the platform: 27,313
  events over 962 cells, all four intensity measures.
- The official PiWind portfolio runs end to end through CASS in the live
  integration suite.
- The datastore reader was run against the 168 MB datastore of the PuSGeN 2024
  calculation on the live stack: 6,584,748 rows over 27,313 events and 962
  cells, streamed with every event whole and in order, at a peak of 35 MB of
  Python memory under a one-million-row budget.
- OED 4.0.0 and 5.0.0 are registered on the live stack from ODS Tools 5.0.8,
  with 4.0.0 active: 565 fields against 574.
- Direct uploads were run against the live MinIO store: each session was
  presigned into the owning project's prefix, a clean CSV registered with the
  checksum it was sent with, and a tampered upload and an executable declared
  as CSV were both quarantined. No antivirus engine is configured on this
  installation, and the registered artifact's note says so.
- On the live stack the worker was found running an image built hours before
  the scheduler that fed it: compose gave the API, the worker and the
  scheduler separate image tags, and a build of the API alone left the other
  two behind, so the worker refused the retention sweep as an unregistered
  task. All three now share one image, and a single build put them on the
  same image ID. On that worker the retention sweep and the stale-run task
  are registered and ran, and a sweep published under a request's
  correlation ID was logged by the worker as structured JSON carrying that
  ID. The metrics were collected from the live Postgres.
- GEM Foundation's permission was recorded on the live stack by migrations
  `modelregistry.0009_gem_permission` and `0010_gem_permission_public_models`:
  the GEM v2026.0.0 vulnerability set, the PuSGeN 2024 hazard model published in
  GEM's mosaic and the hazard set computed from it all cite the permission
  beside their CC BY-NC-SA 4.0 licence, and the Indonesia model version no
  longer lists a licence blocker.
- The OpenQuake reference comparison was run on the live stack for the
  Jakarta–Bandung book: 432 assets over 64 locations and 14 GEM taxonomies,
  carrying the book's value to the cent, each at the centroid of the cell CASS
  mapped it to. OpenQuake calculation 4 was chained onto the hazard calculation
  the footprint was built from, so both sides read one set of ground-motion
  fields, and 2,644 of its events produced loss. Against the engine's own
  answer the Oasis representation is 10% low on average annual loss — 566,728
  against 626,732, a ratio of 0.904 — and between 0.70 and 1.26 at the reported
  return periods, with the 500- and 1,000-year ranks resting on two events and
  one. No tolerance is approved, so nothing passed or failed.
- The OED 4.0.0 and 5.0.0 specifications ODS Tools 5.0.8 ships were compared
  field by field: 565 fields become 574, and every change is an addition of an
  optional location field (the nine photovoltaic attributes). Nothing is
  removed, no requirement changes and no type changes. CASS reads 28 of the
  234 location fields, 17 of 286 account, 18 of 32 reinsurance info and 13 of
  13 reinsurance scope, and no required field of any of them goes unread.

## Milestones (plan section 13)

| Milestone | Status | What shows it | Still missing |
| --- | --- | --- | --- |
| M1 Foundation | Met | Sign in, projects, OED attach, validation, preview, publication, background runs | — |
| M2 Engine integration | Met | PiWind live suite; the sample book through keys, generation, losses and collection; the smoke check proven against the live engine | — |
| M3 Hazard | Partly met | OpenQuake adapter and hazard runs; PuSGeN 2024 on the Jakarta–Bandung region; published Vs30 joined to 37% of cells; benchmark comparison machinery | Approved benchmark curves; a full-country run; a realisation-weighting rule (item 22). Nepal acquires no hazard: it was a test country and Indonesia is covered |
| M4 Conversion | Partly met | Four-measure footprints with frequency preserved; package built under a converter approval; the engine's own datastore read in slices; acceptance measurements taken and judged | Approved QA tolerances |
| M5 Loss | Partly met | Ground-up, insured and reinsurance with keys reconciliation; allocation scenarios reconcile exactly; an assumption set applied within a run and compared live against the baseline; a book converted to the run currency under an approved rate; the financial structure read, reconciled and shown | Building a structure on the platform rather than importing one |
| M6 Product | Partly met | Result approval, export, two-result comparison, EP curve chart, event loss table and the financial structure workspace | Maps, geographic summaries, scenario ranges |
| M7 Production | Not pursued | CI builds and scans the CASS images | Nothing as a milestone: CASS is a research tool, so no production release gate applies ([ADR 15](adr/0015-research-tool-and-gem-permission.md)). Backing up research work stays in the backlog as item 18 |

## Pipelines

### Analysis

| Stage | Status |
| --- | --- |
| `validate_exposure` | Done, in the exposure workspace and again inside the run: the published files must still validate, match the version record, carry one currency and support the requested perspectives. Where that currency is not the run's, an approved rate must exist, and the run stops here rather than at the engine if it does not |
| `enrich` | Done. The run's assumption set chooses the vulnerability set the engine uses ([ADR 14](adr/0014-assumption-sets-as-vulnerability-sets.md)), and an `EnrichmentRun` records reported, derived, imputed and unresolved attributes, missingness by value, exceptions, and a lineage table as an artifact. No value moves |
| `publish_oed` | Done. Before it, the run checks the engine version and that the worker serves this model version's package ([ADR 9](adr/0009-cass-writes-the-oasis-package.md)). A book stated in another currency is converted at an approved rate first, and the converted files the engine received are kept |
| `keys` | Done, through CASS keys |
| `reconcile_keys` | Done. Unmapped value holds the run; the analyst asks for an exception on the run monitor, a reviewer who did not ask decides, and the run resumes from the gate. A geometry-only run reports that value instead of holding for it, because it calculates no loss for the value to be missing from |
| `generate_inputs` | Done |
| `validate_inputs` | Done; compares Oasis's lookup with the CASS keys result |
| `smoke` | Done. The served package's 25 largest-footprint events run through every requested perspective using `event_ids`, and their event losses are checked before the full event set. Recorded as not performed where no package is readable. On the live stack: 25 events in 19 seconds |
| `losses` | Done: ground-up, insured, reinsurance (reinsurance at portfolio level only, [ADR 10](adr/0010-patched-oasis-worker.md)) |
| `collect` | Done: ORD package stored, the event loss table kept beside each result, and result sets published as draft under a decision-use run and research under any other |
| `review` | Done. Published results must not be negative, must rise with return period, stay within the insured value, and insured must not exceed ground-up. A failure holds the run at the gate until a run exception is cleared; results are then approved one by one |

### Run modes (brief section 5.2)

Stated when the run is made, because what a number may claim is decided before
the calculation rather than acquired by it. Two modes are never compared
without the comparison saying so.

| Mode | What it does | What its output may claim |
| --- | --- | --- |
| Geometry only | Validates, enriches, maps through CASS keys and stops. Nothing reaches the engine | Eligibility and mapping. No financial claim |
| KRE-share technical loss | The full pipeline on reported KRE-share value | Research output. Decision-use approval is refused |
| Portfolio-loss research | The full pipeline under a named assumption set | Research output. Decision-use approval is refused |
| Decision use | The full pipeline | May be approved by a reviewer. Refused at configuration unless the model version is no longer a research prototype and any assumption set it names is approved |

The default is the technical mode: a run nobody labelled cannot produce a
decision number.

CASS is a research tool, so a decision-use result a reviewer approves is a
reviewed research result, not a basis for pricing or reserving.

### Hazard

| Stage | Status |
| --- | --- |
| `prepare`, `validate_settings`, `submit`, `monitor`, `export` | Done |
| `benchmark` | Machinery built: a hazard curve is derived from the footprint the engine will be given, compared point by point against a registered benchmark, and reported with ratios. The gate stands open because no benchmark curves are approved |

### Conversion

| Stage | Status |
| --- | --- |
| `manifest`, `events`, `occurrence`, `footprint`, `vulnerability`, `package` | Done, from a registered hazard set. Ground motion is read either from the engine's CSV exports or, for a national run, from its HDF5 datastore a slice at a time under a stated row budget |
| `qa` | Machinery built: probability sums, discarded ground motion above the top bin and events with no footprint are measured when the hazard is converted and judged at the gate against whatever tolerances are approved then. The gate stands open because none are approved |

## Screens (plan section 3)

| Screen | Status | Missing |
| --- | --- | --- |
| Portfolio dashboard | Built | Storage and model notices |
| Model catalogue | Built, Models tab | — |
| Exposure workspace | Built, Exposure tab: intake import, OED attach, validation, row correction, publication | Assumption scenarios; reported-versus-inferred display across attributes |
| Financial structure workspace | Built, Exposure tab: accounts and layers, contracts in inuring order, scope preview, reconciliation, and which contracts the engine will not apply | Building or editing a structure on the platform; guided contract forms |
| Analysis builder | Built, including the assumption set, the run mode and what each resource profile has room for | Output selection |
| Run monitor | Built: stages, events, artifacts, keys gate, cancel, retry, exceptions and resume at a gate, smoke and review checks | — |
| Results workspace | Partly built: AAL, return-period table, EP curve chart, event loss table, caveats, approval, export, comparison | Maps, geographic summaries, scenario ranges |
| Model build workspace | Built, Hazard and Build tabs | Benchmark and QA evidence views |
| Administration | Built: installation facts, compatibility, profiles, engine health, data standards, users with role and access changes, queues, storage, retention, support bundle, audit search | — |

## Geocoded portfolio brief

| Work package | Status |
| --- | --- |
| WP1 Secure importer | Done, then superseded by the intake template ([ADR 11](adr/0011-intake-template-and-policy-id.md)); acceptance counts in `test_extract_acceptance.py` |
| WP2 Eligibility and review interface | Done |
| WP3 Area-peril and keys test | Partly done: mapping, offshore and outside-domain reporting, and a geometry-only run that reports eligibility without a loss. Cohort B sensitivity and OpenQuake site comparison not built |
| WP4 Controlled Oasis earthquake test | Steps 1 to 8 done against the fixture model; step 9 built and run on the live stack, putting the Oasis representation at 0.904 of the engine's own average annual loss |
| WP5 Portfolio-loss readiness | Partly done: enrichment applied in a run under a named assumption set, and the four run modes separate what a number may claim. Section 5.1 questions unanswered |

## Where the design changed

| Area | Plan 1.7 | Now | Record |
| --- | --- | --- | --- |
| Purpose | A governed platform whose results support pricing, reserving and capital decisions | A research tool. Built governance kept as it is and not extended; production-only items not pursued | [ADR 15](adr/0015-research-tool-and-gem-permission.md) |
| Model data rights | Research only until GEM confirms commercial use in writing | Everything GEM makes publicly available, its exposure and vulnerability models and PuSGeN 2024 among them, used under GEM Foundation's explicit permission and credited; data GEM does not publish under the internal-use basis | [ADR 7](adr/0007-internal-use-licence-basis.md), [ADR 15](adr/0015-research-tool-and-gem-permission.md) |
| Intensity measures | SA only, PGA deferred | All four measures as correlated area-peril channels; multi-measure classes refused | [ADR 8](adr/0008-intensity-measures-as-area-peril-channels.md) |
| Model package | Binaries compiled with Oasis tools | Written by CASS, byte-checked against PiWind, CASS lookup inside | [ADR 9](adr/0009-cass-writes-the-oasis-package.md) |
| Engine images | Unmodified upstream | Oasis worker patched at build time; OpenQuake unmodified | [ADR 10](adr/0010-patched-oasis-worker.md) |
| Portfolio intake | Two-sheet extract; role-gated names; cedant segmentation | Intake template joined on Policy ID; no role gate; no cedant | [ADR 11](adr/0011-intake-template-and-policy-id.md) |
| Hazard source | GEM source models | PuSGeN 2024 converted to event-based, one sampled path | [ADR 12](adr/0012-national-classical-model-run-event-based.md) |
| Navigation | One screen per sidebar entry | Models and Exposure areas with tabs | [ADR 13](adr/0013-product-areas-hold-tabs.md) |
| Assumption sets | Enrichment applied to exposure | Re-weighted mixtures carried as vulnerability sets the engine selects per analysis; no value moves; pilot tilts are draft | [ADR 14](adr/0014-assumption-sets-as-vulnerability-sets.md) |

## Backlog: buildable in code

| # | Item | Plan | Status |
| --- | --- | --- | --- |
| 1 | Analysis pipeline: exposure validation inside the run, `smoke` on a reduced event set, `review` stage, and releasing a run held at a gate | §8, M2 | Done |
| 1b | `enrich`: applying an assumption set within a run, with reconciliation | §8, M5 | Done; two sets compared live on one book |
| 2 | Run modes: geometry-only, technical loss, research, decision use | Brief §5.2 | Done |
| 3 | Currency conversion evidence captured and applied before generation | §8 | Done |
| 4 | Results: event loss tables, geographic summaries, EP curve chart, map, scenario ranges | §3, M6 | Partly done: event loss table and EP curve chart built. Geographic summaries, the map and scenario ranges need loss at a summary level below the portfolio, which is an engine settings change |
| 5 | Financial structure workspace | §3, M5 | Partly done: accounts and layers, contracts, inuring order, scope preview and reconciliation are read from the published portfolio and checked. Building a structure on the platform, rather than importing one, is not built |
| 6 | OED standards registry: `DataStandardVersion`, pinned OED 4.0.0 and 5.0.0 reference JSON, schema API, ODS Tools validation, version diff | §8, §17 | Done |
| 7 | Converter reads the OpenQuake HDF5 datastore in chunks | §7, M4 | Done |
| 8 | Hazard benchmark and conversion QA gates: registered references, comparison, report | §7, M3, M4 | Done as machinery. Both gates still stand open, because no benchmark curves and no tolerances have been approved — which is a decision, not code |
| 9 | OpenQuake reference loss comparison for a controlled portfolio | §7, WP4 | Done: `compare_with_openquake` rebuilds the run's own keys and blend weights as GEM taxonomies at the cells they mapped to, chains the risk job onto the hazard calculation so both sides read one set of ground-motion fields, and reports ratios it does not grade. First measurement on the live stack: 0.904 of the engine's average annual loss |
| 10 | Cohort B geocoding sensitivity | WP3 | Not started |
| 11 | Direct-to-store uploads completed, checksummed and scanned | §5, §10 | Done: sessions are issued into the owning project's prefix to someone who may write there, completion reads back and checksums what arrived, and a content check plus an optional clamd engine scan it before release. An installation with no antivirus engine says so on each artifact |
| 12 | Execution profiles enforced: time limits and admission control | §11 | Done |
| 13 | Keys and converter HTTP services | §4 | Not pursued: the keys lookup and the converter run inside the worker, with the same results. Separate services only serve a governed deployment ([ADR 15](adr/0015-research-tool-and-gem-permission.md)) |
| 14 | Artifact retention expiry | §5 | Done: a scheduled sweep expires due payloads and keeps their records, and refuses anything a running run is reading, anything behind an approved result, and published exposure |
| 15 | Administration: users and roles, queues, storage, retention, support bundle | §3, §4 | Done: an administrator changes a person's role or access, and the API refuses any change that would leave nobody able to undo it; the screen shows what each profile is running, what the store holds and what the retention sweep removes next; the support bundle carries settings by allowlist and failures by stage, never secrets or portfolio contents, and viewing it is audited apart from downloading it |
| 16 | Observability: metrics, correlation IDs through background tasks | §4 | Done: the request's correlation ID travels in task headers into the worker and is stamped on the run when it is queued; /metrics/ serves run, profile, failure, artifact, result and approval gauges read from the records, behind a scrape token |
| 17 | Multi-factor authentication and single sign-on configuration | §10 | Not pursued: a research tool with local accounts ([ADR 15](adr/0015-research-tool-and-gem-permission.md)) |
| 18 | Backup and restore of research work: the database and the artifact store | §11 | Not started. Re-scoped from a production restore drill to a procedure that keeps research work from being lost |
| 19 | CI: Oasis worker image build and the integration workflow | §17 | Not started. Re-scoped: SBOMs for releases dropped |
| 20 | Pinned image digests, so a run can be repeated on the same engines | §18 | Not started. Re-scoped: the signed release bundle for approved local installations dropped |
| 21 | Multi-IMT representation: measure the candidates against an OpenQuake reference calculation and decide | §6, §16 | Not started. Item 9 is the measurement, and its first run puts the channel representation 10% below the engine on the pilot book. The decision needs the candidates measured against each other, and classes whose taxonomies span measures stay refused until it is taken ([ADR 8](adr/0008-intensity-measures-as-area-peril-channels.md)) |
| 22 | Realisation weighting: measure what one sampled logic-tree path costs against weighted realisations, and decide the rule | §7, M3 | Not started. The hazard behind every Indonesian loss is one sampled path until it is decided ([ADR 12](adr/0012-national-classical-model-run-event-based.md)) |
| 23 | Footprint storage: measure a national footprint as ktools binary and as Parquet, and decide the runtime format | §7, §18 | Not started. Needs a national-scale footprint, which the full-country run would produce |
| 24 | Build an area-peril grid on the platform, for any country, from a written specification | §6 | Not started. The geometry is in `cass_keys.grids` and it is general; what is missing is the door. Only the two pilot prototypes and a CSV upload reach the registry today, which makes a grid something CASS ships rather than something a modeller builds |
| 25 | Build a vulnerability set for any country GEM covers | §6, §8 | Not started. `modelregistry.gem` builds one from a GEM release, but where a country sits in that release, and the design-era judgement behind its mixtures, are written into the two pilots |
| 26 | Event representation study: build one calculation both ways, compare each against the OpenQuake reference, and decide | §6, §16 | Not started. Occurrence per event is in use and rupture-binned is the alternative; item 9 is how either is measured. It ends in a decision record, not an approval |

## Needs a decision or outside input

These cannot be closed by writing code. Where a backlog item builds the
machinery around one, the decision still has to be taken.

- Secondary-peril scope for each country release, and business interruption
  treatment.
- Approved hazard benchmark curves and converter QA tolerances, with named
  reviewers. The machinery now waits on them: register the curves and the
  tolerances, have somebody who did not register them approve them, and both
  gates begin deciding.
- The brief's section 5.1 questions: coverage-component split, replacement cost
  or indemnity value, deductibles, limits and attachments, shared or scheduled
  multi-location limits, renewals.
- Whether to adopt OED 5.0.0. The registry now has the comparison the decision
  needs, and it is a small one: nine optional location fields added, nothing
  removed or re-typed, and nothing CASS reads affected. Adoption still means
  moving the reader and the pinned ODS Tools together, and re-running the
  acceptance checks against PiWind and the KRE extract.
- A full Indonesian grid run: 52,831 cells, hours of compute.

### Answered on 13 September 2026

- **Sign-off of the event representation study.** The sign-off is dropped: no
  named reviewer approves it. The study itself stays, as research CASS does —
  item 26 — and every package still records the event identity it was built
  under ([ADR 4](adr/0004-converter-refuses-unapproved-policy.md)).
- **Legal review before model data, or a package derived from it, leaves KRE.**
  Cleared. GEM Foundation's permission covers the use KRE described to it
  ([ADR 15](adr/0015-research-tool-and-gem-permission.md)); anything
  redistributed still carries GEM's share-alike terms and credits the authors
  and GEM.
- **A Nepal seismic source model, a denser Vs30 source, and the licensed GEM
  ~1 km exposure.** Not needed, and the reason changes what CASS builds rather
  than what it acquires. Indonesia and Nepal were test countries: somebody using
  a finished CASS builds the grid and the keys for the country they work on, so
  the pilot data is disposable and the native path is what matters. That path is
  items 24 and 25.
- **The multi-IMT representation, the realisation-weighting rule and the
  footprint storage format.** Not waiting on anybody else: CASS is to research
  each and decide. They are backlog items 21, 22 and 23, and each ends in a
  decision record rather than a preference.
