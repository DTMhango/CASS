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

- 2,062 backend tests pass, 1 skipped. Of the 106 integration tests, the
  portfolio and enrichment acceptance suites (57 and 27) were re-run at this
  revision against the 30 June workbook and the real GEM v2026.0.0 files; the
  rest last passed against the PuSGeN 2024 package, the pinned ODS Tools
  specifications and an OpenQuake datastore the engine wrote.
  140 frontend tests pass. Ruff, ESLint and TypeScript are clean, the OpenAPI
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
- The live installation had no GEM release configured, so building a
  vulnerability set from the Build tab was refused there. With the release
  location now chosen on the platform, the installation found
  `/models/gem/v2026.0.0` under the folder mounted from the device, read both
  repositories as tagged v2026.0.0 at exactly the commits CASS was validated
  against, and reported 215 countries. Choosing it took no environment change
  and no restart, and the GEM country catalogue that had been refused listed all
  215.
- A financial structure was built on the live stack through the builder's API
  and run through the engine. The Jakarta–Bandung book's 64 locations were taken
  into a new portfolio labelled "Structure builder live check", given a policy on
  each of its 3 accounts and three contracts — a surplus share on two sites, a
  whole-book quota share of 30% and a catastrophe excess of loss of 2,000,000
  per event over 100,000 — with no blocking finding, then published and run for
  all three perspectives in 96 seconds. It is the first surplus share CASS has
  run through the engine. The ground-up AAL was 566,728.13, the book's figure
  under baseline weights. The insured AAL was 305,479.06, below ground-up
  because 38 of the locations carry the book's own location deductibles and
  limits, which apply once an account file exists. The reinsurance perspective
  was 151,313.83, and in Oasis that perspective is the loss net of reinsurance
  (oasislmf's `ri` stream): the three contracts ceded 154,165.23, about half the
  insured loss. CASS's label "Reinsurance loss" does not say that, which is item
  32.
- The Jakarta–Bandung book was run twice more on the live stack, under baseline
  weights and under the more vulnerable assumption set, and the two results
  recorded the same calculation digest, so the scenario range brought them
  together; seven older results for the book were left out and counted, because
  they were published before the digest was recorded. Across the two, the
  average annual loss spans 0.20% (565,610.88 to 566,728.13), and the 250-year
  loss is where the assumption bites hardest, 3.99% lower under more vulnerable
  while the 200-year loss is 1.19% higher — the same reshaping of the curve,
  rather than a uniform scaling, that the assumption set was built to test.
- A ground-up analysis of the Jakarta–Bandung book was run on the live stack
  with the location summary level asked for, and the patched Oasis worker wrote
  it beside the portfolio level. At publication the 64 locations with a loss
  were all placed through the run's keys, into 33 cells of the Indonesian pilot
  grid; the largest cell carries 21% of the average annual loss, and the
  locations add back to the portfolio figure within 0.002 — the rounding of the
  engine's table. The run took 61 seconds, where an otherwise identical run
  without the location level took 42.
- The Cohort B geocoding sensitivity was run on the 30 June book, against the
  Indonesian pilot grid and a Nepal grid written on the platform. Every one of
  the 44 Cohort B locations — 29 in Indonesia (25 locality, 3 postcode, 1
  administrative) and 15 in Nepal (all locality) — reaches more than one cell
  within its buffer, up to 24 cells; on average only 51% of an Indonesian
  buffer and 48% of a Nepali one stays in the recorded cell. At 0.1-degree cells
  refined to 0.025 degrees, the grid implies more precision than any Cohort B
  geocode supports, so their mapping is roughly a coin-flip between
  neighbouring cells. That puts USD 69.8m of stated Indonesian value and USD
  36.6m of Nepali value on assignments the geocode cannot vouch for, as a
  floor: three of the locations state no value of their own.
- A whole country was built on the live stack out of nothing but the product,
  for a country CASS ships no constants for: a specification became a 27-cell
  grid, the GEM release became a 484-function vulnerability set under a written
  enrichment, and the two were assembled into `ph-qeq-scratch-check` — a draft
  research prototype carrying all four intensity measures and four stated
  blockers, among them the multi-IMT decision and the absence of a hazard set.
  A Manila coordinate read back through the pair to its refined cell. The check
  removed all three afterwards.
- A run now takes its enrichment from what the vulnerability set recorded when
  it was built, not from the compiled-in pilots. On the live stack the
  Indonesian set's three records — baseline, more robust and more vulnerable —
  all read back as the enrichments the pilot path produces, with their three
  eras and their design-level tilts, so Indonesian runs record the same lineage
  as before and a country built on the platform records its own.
- A vulnerability set for a country CASS ships no constants for was built on the
  live stack from the real GEM v2026.0.0 release: the catalogue read the release,
  and a written enrichment — three design eras, each with its reason — produced
  the Philippines at 240 classes and 484 functions across all four intensity
  measures, with 144 classes flagged as needing the multi-IMT decision. All
  seven assets a run reads were stored (21.4 MB: the mapping, the functions, the
  three assumption-set variants, the damage bins and the provenance dictionary),
  cleared under GEM's permission. The check removed the set afterwards.
- A grid for a country CASS ships no prototype for was built on the live stack
  from a specification alone: a modeller posted tiles, a base resolution and a
  refinement over Metro Manila, and got 27 cells back — 4 of them refined —
  registered as a draft with its cell file in the store and read back through
  the keys lookup at 0.25 and 0.5 degrees. The same specification at 0.001
  degrees was refused as about 6,000,000 cells against a limit of 250,000, and
  an analyst attempting it was refused. The check removed the grid afterwards.
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
- The multi-IMT representation was decided on the live stack (item 21,
  [ADR 16](adr/0016-multi-measure-classes-as-sub-peril-channels.md)). A building
  of unknown height was run through the live model package over all 27,313
  events at 20 samples, split into one item per shaking measure and unsplit: the
  split items' mean loss matched the weighted sum of the single-measure items to
  under a cent per event, and the insured loss matched the location deductible
  and limit in every event-sample — while the control, with the terms left
  scoped to shake, was wrong in 89 of 114 and 40% too high. Three books derived
  from the Jakarta–Bandung test book were then run ground-up against the
  OpenQuake reference on one set of ground-motion fields: with heights stated
  (no split value) 0.904 of the engine's average annual loss, with heights
  withheld (7.2% split) 1.032, and as commercial buildings of unknown height
  (all value split) 0.968. The stated book reproduced its earlier ratio exactly,
  so the change leaves a class of one measure untouched. The study's model
  version, `id-qeq-0.1.0-study21`, is what the worker now serves: one package is
  served at a time ([ADR 9](adr/0009-cass-writes-the-oasis-package.md)), so a run
  against the earlier Indonesian version needs its package built again.
- The realisation-weighting rule was decided against the published model's own
  answer (item 22, [ADR 18](adr/0018-sampled-paths-pooled-as-one-catalogue.md)).
  PuSGeN 2024 was run over 12 sites of the pilot region as a classical
  calculation enumerating all 1,080 realisations — the weighted mean the model
  states, 79 seconds — and then event-based over the same 2,000 simulated years
  along 1, 5 and 20 sampled paths. Median ratio to that mean at the 100-year
  return period: 1.198, 0.836, 1.237 and 0.988 for one path under four seeds,
  1.031 for five paths, and 0.952 and 0.927 for twenty under two seeds. Every
  arm took between 131 and 188 seconds. With one path the 1,000-year point was
  undefined at up to 15 of the 36 site-measures; with twenty it was defined at
  33 to 36.
- The footprint storage format was decided at national scale (item 23,
  [ADR 19](adr/0019-footprint-stays-a-ktools-binary.md)). The regional footprint
  was repeated across 52,831 cells, labelled synthetic, and the same book run
  through three formats: the ktools binary at 9,494 MB, built in 44s and read in
  17s; compressed binary at 2,366 MB, 710s and 18s; Parquet at 2,566 MB, 296s
  and 22s. All three produced the same loss to the cent, 570,652.25
  analytically, which is also what the live run of that book produced.
- The event representation was decided on the live event set (item 26,
  [ADR 17](adr/0017-an-oasis-event-is-a-simulated-occurrence.md)). The deployed
  package was rebuilt with every rupture's occurrences pooled into one event and
  the occurrence table pointing each of them at it, and the Jakarta–Bandung book
  was run through both packages with the same settings. Average annual loss
  moved 0.04% (570,652 to 570,854), the spread of annual loss narrowed 2.1%, the
  100-year occurrence loss thinned 6.2%, the 500- and 1,000-year losses were
  identical, and both footprints were 153 MB and both runs 17 seconds. The event
  set holds 27,313 occurrences of 26,651 ruptures, 97.7% of which occur once, so
  there was almost nothing to pool.
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
| M3 Hazard | Partly met | OpenQuake adapter and hazard runs; PuSGeN 2024 on the Jakarta–Bandung region; published Vs30 joined to 37% of cells; benchmark comparison machinery; the realisation-weighting rule decided and built ([ADR 18](adr/0018-sampled-paths-pooled-as-one-catalogue.md)) | Approved benchmark curves; a full-country run; an Indonesian hazard set rebuilt under the new weighting, since the one in use is the single path it was run on. Nepal acquires no hazard: it was a test country and Indonesia is covered |
| M4 Conversion | Partly met | Four-measure footprints with frequency preserved; package built under a converter approval; the engine's own datastore read in slices; acceptance measurements taken and judged | Approved QA tolerances |
| M5 Loss | Met | Ground-up, insured and reinsurance with keys reconciliation; allocation scenarios reconcile exactly; an assumption set applied within a run and compared live against the baseline; a book converted to the run currency under an approved rate; the financial structure read, reconciled, shown, and built on the platform | — |
| M6 Product | Met | Result approval, export, two-result comparison, EP curve chart, event loss table, loss by area-peril cell with its map, scenario ranges across assumption sets, and the financial structure workspace | — |
| M7 Production | Not pursued | CI builds the CASS images and the patched Oasis worker and scans the CASS ones; an integration workflow runs on demand against the pinned GEM release; every pulled image is pinned by digest | Nothing as a milestone: CASS is a research tool, so no production release gate applies ([ADR 15](adr/0015-research-tool-and-gem-permission.md)). Backing up research work stays in the backlog as item 18 |

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
| Financial structure workspace | Built, Exposure tab: accounts and layers, contracts in inuring order, scope preview, reconciliation, which contracts the engine will not apply, and guided forms that build policies and contracts on a draft portfolio | — |
| Analysis builder | Built, including the assumption set, the run mode and what each resource profile has room for | Output selection |
| Run monitor | Built: stages, events, artifacts, keys gate, cancel, retry, exceptions and resume at a gate, smoke and review checks | — |
| Results workspace | Built: AAL, return-period table, EP curve chart, event loss table, loss by area-peril cell with its map, scenario ranges across assumption sets, caveats, approval, export, comparison | — |
| Model build workspace | Built, Hazard and Build tabs: a country's grid from a written specification, its vulnerability set from GEM under a written enrichment, and the two assembled into the model version a run names | Benchmark and QA evidence views |
| Administration | Built: installation facts, compatibility, profiles, engine health, data standards, users with role and access changes, queues, storage, retention, support bundle, audit search | — |

## Geocoded portfolio brief

| Work package | Status |
| --- | --- |
| WP1 Secure importer | Done, then superseded by the intake template ([ADR 11](adr/0011-intake-template-and-policy-id.md)); acceptance counts in `test_extract_acceptance.py` |
| WP2 Eligibility and review interface | Done |
| WP3 Area-peril and keys test | Partly done: mapping, offshore and outside-domain reporting, a geometry-only run that reports eligibility without a loss, and the Cohort B geocoding sensitivity (item 10). The OpenQuake site comparison is not built |
| WP4 Controlled Oasis earthquake test | Steps 1 to 8 done against the fixture model; step 9 built and run on the live stack, putting the Oasis representation at 0.904 of the engine's own average annual loss |
| WP5 Portfolio-loss readiness | Partly done: enrichment applied in a run under a named assumption set, and the four run modes separate what a number may claim. Section 5.1 questions unanswered |

## Where the design changed

| Area | Plan 1.7 | Now | Record |
| --- | --- | --- | --- |
| Purpose | A governed platform whose results support pricing, reserving and capital decisions | A research tool. Built governance kept as it is and not extended; production-only items not pursued | [ADR 15](adr/0015-research-tool-and-gem-permission.md) |
| Model data rights | Research only until GEM confirms commercial use in writing | Everything GEM makes publicly available, its exposure and vulnerability models and PuSGeN 2024 among them, used under GEM Foundation's explicit permission and credited; data GEM does not publish under the internal-use basis | [ADR 7](adr/0007-internal-use-licence-basis.md), [ADR 15](adr/0015-research-tool-and-gem-permission.md) |
| Intensity measures | SA only, PGA deferred | All four measures as correlated area-peril channels; a class spanning measures carried as one earthquake sub-peril item per measure, on pre-weighted functions | [ADR 8](adr/0008-intensity-measures-as-area-peril-channels.md), [ADR 16](adr/0016-multi-measure-classes-as-sub-peril-channels.md) |
| Model package | Binaries compiled with Oasis tools | Written by CASS, byte-checked against PiWind, CASS lookup inside | [ADR 9](adr/0009-cass-writes-the-oasis-package.md) |
| Engine images | Unmodified upstream | Oasis worker patched at build time; OpenQuake unmodified | [ADR 10](adr/0010-patched-oasis-worker.md) |
| Portfolio intake | Two-sheet extract; role-gated names; cedant segmentation | Intake template joined on Policy ID; no role gate; no cedant | [ADR 11](adr/0011-intake-template-and-policy-id.md) |
| Hazard source | GEM source models | PuSGeN 2024 converted to event-based, twenty sampled logic-tree paths pooled as one catalogue | [ADR 12](adr/0012-national-classical-model-run-event-based.md), [ADR 18](adr/0018-sampled-paths-pooled-as-one-catalogue.md) |
| Navigation | One screen per sidebar entry | Models and Exposure areas with tabs | [ADR 13](adr/0013-product-areas-hold-tabs.md) |
| Assumption sets | Enrichment applied to exposure | Re-weighted mixtures carried as vulnerability sets the engine selects per analysis; no value moves; pilot tilts are draft | [ADR 14](adr/0014-assumption-sets-as-vulnerability-sets.md) |

## Backlog: buildable in code

| # | Item | Plan | Status |
| --- | --- | --- | --- |
| 1 | Analysis pipeline: exposure validation inside the run, `smoke` on a reduced event set, `review` stage, and releasing a run held at a gate | §8, M2 | Done |
| 1b | `enrich`: applying an assumption set within a run, with reconciliation | §8, M5 | Done; two sets compared live on one book |
| 2 | Run modes: geometry-only, technical loss, research, decision use | Brief §5.2 | Done |
| 3 | Currency conversion evidence captured and applied before generation | §8 | Done |
| 4 | Results: event loss tables, geographic summaries, EP curve chart, map, scenario ranges | §3, M6 | Done: event loss table, EP curve chart, geographic summary and map, and scenario ranges built. Every analysis now asks the engine for a location summary level beside the portfolio one, carrying only the period average loss; at publication each location's average annual loss is placed in the cell the run's own keys mapped it to, the cells are summed, and the result says how much could not be placed and how far the locations add back to the portfolio number. The results workspace draws the cells as a grid plot rather than on a basemap, with the exact table beside it. A scenario range takes the same book on the same model version, perspective, currency, run mode and ORD basis, calculated under the same settings apart from the assumption set, run under each assumption set, and reports every metric's central estimate (the baseline), its low and high and the scenario behind each, and ranks the assumptions by how far they move any number; it lists what does not vary — the hazard realisation, the allocation of value between sites, where a coarse geocode places a location — so a range across one assumption is not read as the whole uncertainty |
| 5 | Financial structure workspace | §3, M5 | Done: accounts and layers, contracts, inuring order, scope preview and reconciliation are read from the portfolio and checked, and a structure can now be built on the platform rather than only imported. Policies with their layers, and quota share, surplus share and catastrophe excess of loss contracts with their scope, are written into the draft version's own OED files and read straight back with their findings. The forms ask only for the terms oasislmf 2.5.7 uses for each type — a quota share's ceded share, a surplus share's share on each risk it names, a catastrophe excess of loss's attachment and limit per event — and refuse what the engine would reject, such as a surplus share whose scope does not name exactly the risk at its risk level. A type CASS does not apply in a run (ADR 10) is refused rather than written. A published version is offered its correction |
| 6 | OED standards registry: `DataStandardVersion`, pinned OED 4.0.0 and 5.0.0 reference JSON, schema API, ODS Tools validation, version diff | §8, §17 | Done |
| 7 | Converter reads the OpenQuake HDF5 datastore in chunks | §7, M4 | Done |
| 8 | Hazard benchmark and conversion QA gates: registered references, comparison, report | §7, M3, M4 | Done as machinery. Both gates still stand open, because no benchmark curves and no tolerances have been approved — which is a decision, not code |
| 9 | OpenQuake reference loss comparison for a controlled portfolio | §7, WP4 | Done: `compare_with_openquake` rebuilds the run's own keys and blend weights as GEM taxonomies at the cells they mapped to, chains the risk job onto the hazard calculation so both sides read one set of ground-motion fields, and reports ratios it does not grade. First measurement on the live stack: 0.904 of the engine's average annual loss |
| 10 | Cohort B geocoding sensitivity | WP3 | Done: each Cohort B location — no review needed, but a geocode that resolves only to a locality, a postcode or an administrative area — is tried at its recorded coordinate and across the area its precision stands for, against a chosen grid, and the report says which keep their cell, which reach others or leave the grid, and how much stated value sits on the ones that move. The buffers are assumptions (5 km for a locality or postcode, 25 km for an administrative match), recorded on every report and open to override; a precision with no buffer is listed as unassessed rather than given one. The review screen carries it beside the allocation comparison |
| 11 | Direct-to-store uploads completed, checksummed and scanned | §5, §10 | Done: sessions are issued into the owning project's prefix to someone who may write there, completion reads back and checksums what arrived, and a content check plus an optional clamd engine scan it before release. An installation with no antivirus engine says so on each artifact |
| 12 | Execution profiles enforced: time limits and admission control | §11 | Done |
| 13 | Keys and converter HTTP services | §4 | Not pursued: the keys lookup and the converter run inside the worker, with the same results. Separate services only serve a governed deployment ([ADR 15](adr/0015-research-tool-and-gem-permission.md)) |
| 14 | Artifact retention expiry | §5 | Done: a scheduled sweep expires due payloads and keeps their records, and refuses anything a running run is reading, anything behind an approved result, and published exposure |
| 15 | Administration: users and roles, queues, storage, retention, support bundle | §3, §4 | Done: an administrator changes a person's role or access, and the API refuses any change that would leave nobody able to undo it; the screen shows what each profile is running, what the store holds and what the retention sweep removes next; the support bundle carries settings by allowlist and failures by stage, never secrets or portfolio contents, and viewing it is audited apart from downloading it |
| 16 | Observability: metrics, correlation IDs through background tasks | §4 | Done: the request's correlation ID travels in task headers into the worker and is stamped on the run when it is queued; /metrics/ serves run, profile, failure, artifact, result and approval gauges read from the records, behind a scrape token |
| 17 | Multi-factor authentication and single sign-on configuration | §10 | Not pursued: a research tool with local accounts ([ADR 15](adr/0015-research-tool-and-gem-permission.md)) |
| 18 | Backup and restore of research work: the database and the artifact store | §11 | Postponed on 14 September 2026: not yet necessary. Re-scoped earlier from a production restore drill to a procedure that keeps research work from being lost |
| 19 | CI: Oasis worker image build and the integration workflow | §17 | Done: CI builds the patched Oasis worker beside the other images, so raising the upstream version past the patched defect fails the build; it is the one image not scanned, because its findings are the upstream image's. A separate integration workflow runs on demand and weekly: it fetches GEM's exposure and vulnerability models at the commits the model manifest pins, runs the integration suite, and lists what it skipped for data CI cannot have — the national hazard package, an OpenQuake datastore, a live Oasis. The portfolio workbook never enters CI, and a deployment test refuses a workflow that names it. Neither workflow has run on GitHub yet: both parse, and reading the pinned commits from the manifest was run locally. SBOMs for releases stay dropped |
| 20 | Pinned image digests, so a run can be repeated on the same engines | §18 | Done: every image the compose file pulls from a registry is pinned by digest as well as tag — `openquake/engine:3.23` among them, a minor-version tag that would otherwise move — and each pinned reference resolves to the image the installation already runs. Every base a CASS build starts from is pinned the same way, the patched Oasis worker's included; those digests were read from the registry, and no image has been rebuilt from them yet. The images this repository builds are not pinned, because their digest changes with every build: a run records the engine version and, where the deployment exposes it, the image digest it ran on. A deployment test refuses an unpinned image. The signed release bundle stays dropped |
| 21 | Multi-IMT representation: measure the candidates against an OpenQuake reference calculation and decide | §6, §16 | Done, and decided in [ADR 16](adr/0016-multi-measure-classes-as-sub-peril-channels.md): a class spanning measures is carried as one earthquake sub-peril item per measure, each answered by its channel's function scaled by the channel's share, with the terms the engine receives scoped to all earthquake perils. Under the old refusal the 30 June benchmark book could not be modelled at all — it states no storey counts, so every class it reaches spans all four measures. On the live engine the split items add back to the class to under a cent per event and honour the location terms exactly; without the wider peril scope the insured loss was 40% too high, silently. Against the OpenQuake reference the book carried entirely this way is at 0.968 of the engine's average annual loss, inside the spread the book of stated heights shows (0.904, and 0.70–1.26 across return periods), and the stated book reproduced its earlier ratio exactly. A set is now built this way unless its specification asks for undecided. The plan, the numbers and what they do not settle are in [the decision studies](research/decision-studies.md) |
| 22 | Realisation weighting: measure what one sampled logic-tree path costs against weighted realisations, and decide the rule | §7, M3 | Done, and decided in [ADR 18](adr/0018-sampled-paths-pooled-as-one-catalogue.md): a run samples twenty paths and pools them as one catalogue of the same thousand simulated years, and an enumerated tree stays refused because its branches carry unequal weights. Measured against the published model's own weighted mean over 12 sites, enumerating all 1,080 realisations: one sampled path landed between 0.84 and 1.24 of it across four draws, five paths at 1.03, and twenty paths at 0.95 and 0.93 — for the same run time, because the work follows the simulated years rather than the number of views they are drawn across. With one path the 1,000-year return period was undefined at up to 15 of 36 site-measures; with twenty, at none or few. CASS now reads the realisations' weights, pools equally weighted paths, counts their years, and refuses a tree whose weights differ. The hazard behind existing Indonesian results is still the one path they were run on |
| 23 | Footprint storage: measure a national footprint as ktools binary and as Parquet, and decide the runtime format | §7, §18 | Done, and decided in [ADR 19](adr/0019-footprint-stays-a-ktools-binary.md): the ktools binary stays, with compressed binary measured and held in reserve and Parquet rejected. The regional footprint was repeated across Indonesia's 52,831 cells — the same shaking at the size of a country — and the same book run through each format: binary 9,494 MB built in 44s and read in 17s, compressed 2,366 MB in 710s and 18s, Parquet 2,566 MB in 296s and 22s, and all three returned the same loss to the cent. Compression is four times smaller at no read cost, and at a faster setting takes about a minute rather than twelve; it is adopted when a deployment cannot hold a national package, which needs no further study |
| 24 | Build an area-peril grid on the platform, for any country, from a written specification | §6 | Done: a specification — tiles, a base resolution, named refinements and the reason for each — is posted to `/grids/build/`, generated through the same builder the prototypes use, and registered as a draft with its cells. A specification that would exceed the installation's cell limit is refused with its own count, before anything is generated. The prototypes now go through the same registration, and the Build tab carries the form |
| 25 | Build a vulnerability set for any country GEM covers | §6, §8 | Done: the catalogue is read from the release on the installation, and the enrichment — the design eras, each with its reason — is posted with it rather than compiled in. The set's version follows the enrichment, because the enrichment is the assumption behind every function. An era table that is out of order, ends before today, or names a design level GEM does not use is refused |
| 26 | Event representation study: build one calculation both ways, compare each against the OpenQuake reference, and decide | §6, §16 | Done, and decided in [ADR 17](adr/0017-an-oasis-event-is-a-simulated-occurrence.md): an Oasis event is one simulated occurrence, and rupture binning is not built. The catalogue settles most of it — 27,313 occurrences of 26,651 ruptures over 1,000 years, 97.7% of them occurring exactly once, so for those the two representations are the same table. The deployed package was rebuilt with the remaining 617 ruptures' occurrences pooled and the same book run through both: the average annual loss moved 0.04%, the annual-loss spread narrowed 2.1%, the 100-year loss thinned 6.2%, the 500- and 1,000-year losses were identical, and neither footprint size nor run time changed. Pooling also needs an approximation occurrence per event does not, because a footprint cannot say that an occurrence did not reach a cell. The numbers are in [the decision studies](research/decision-studies.md) |
| 27 | Assemble a model version for any country from a built grid and a built vulnerability set | §6 | Done: the two halves are paired at `/model-versions/assemble/` into the draft a run names. A pair from two different countries is refused, because such a version would calculate happily and mean nothing. The scope statement and the limitations are written from the halves rather than typed, so a version's caveats cannot drift from its parts |
| 28 | A run on a country built on the platform is described under the enrichment its vulnerability set was built under | §5, §8 | Done: the enrich stage looked the enrichment up among the compiled-in pilots by country code, so a run on any other country recorded its lineage under an empty enrichment and no year could derive a design level. It now reads the enrichment each assumption set's functions were built under from the set's own provenance dictionary. A set registered before the dictionary recorded it is described as before, and a record that cannot be read stops the run at enrich rather than being passed over |
| 29 | The OpenQuake reference comparison, for a country built on the platform | WP4, §16 | Done: the comparison found GEM's functions for a run's country through a table of the two pilots, so it refused any other country — and it is the measurement items 21 and 26 rest on. A vulnerability set's dictionary now records where GEM publishes its country, the comparison reads GEM's functions from there, and a set registered before that record is found through the table as before |
| 32 | Say that the reinsurance perspective is the loss net of reinsurance | §9 | Done: Oasis's `ri` stream is the loss retained after every inuring priority (oasislmf documents it as "reinsurance net loss"), and CASS had labelled it "Reinsurance loss", which reads as the ceded amount. It is now "Loss net of reinsurance" on results, comparisons, the analysis builder, the context bar and the exposure workspace, and the ceded amount is the insured loss less it |
| 31 | Point CASS at the GEM release on the user's own device, and check it | §6 | Done: the release is not bundled. The person running CASS keeps GEM's two repositories on their own device, in the models folder the installation mounts (`CASS_MODELS_PATH`), and the Build tab lists every release it finds there and lets one be chosen, with no environment file to edit and no restart. A folder is checked before it is kept — both repositories in GEM's published layout, the world exposure-to-vulnerability mapping, and at least one country's functions — and the commit and tags of each repository are read from its own git metadata, so the card says which release it is and whether it is the v2026.0.0 CASS was validated against. Each choice is kept as a record; `CASS_GEM_ROOT` still applies until anybody chooses |
| 33 | A schedule without storey counts is keyed the same way in the engine as in CASS | §5, §6 | Done: found while testing item 21 on the live engine. CASS writes a blank storey count where a schedule gives none, and oasislmf fills a blank `NumberOfStoreys` with OED's default of 0 before the engine's lookup reads the row. The lookup read 0 as a height no storey band covers, so in the engine every such risk failed with no vulnerability function while CASS's own keys answered it as unstated — which no live run had shown, because every live book states its storeys. 0 now reads as not stated, in both lookups |
| 34 | Read OED's peril codes as OED defines them | §5, §9 | Done: found while deciding item 21. CASS expanded `QQ`, which OED does not define, and did not recognise `QQ1` — OED's code for the whole earthquake group — or `AA1` for every peril, so a schedule written the ordinary way was read as covering no sub-peril CASS models and refused at validation. It also called `QSL` liquefaction, where OED uses `QSL` for sprinkler leakage and `QLF` for liquefaction, so a model version's scope statement excluded the wrong thing by name. The group codes now expand, the labels follow OED, the scope statement names both, and CASS's own demonstration book uses `QQ1` |
| 35 | Count the sampled logic-tree paths in a hazard set's effective time | §5, §7 | Done: found while preparing the rebuild ADR 18 calls for. The converter's effective time was corrected to investigation time × event sets × paths, but the registry's own `HazardSet.effective_time` still multiplied the first two — and the new default samples twenty paths of one event set each, where the old one ran twenty event sets along one path. The same thousand years would have been recorded as fifty, dividing every annual rate, every AAL and every return period's frequency by twenty, in a record that still read as complete. A hazard set now stores the paths it pools, as ADR 18 requires of it, and the package builder reads the set's effective time rather than multiplying a second time in a second place. A test registers a twenty-path catalogue and fails at 50 against 1,000 without the fix. Sets registered before this pooled one path, so their recorded span is unchanged |
| 30 | Remove the Nepal prototype | — | Done: the compiled-in Nepal grid, enrichment and GEM location are gone, now that a grid, a vulnerability set and a model version can be built on the platform for any country. The 30 June book still carries Nepali business, so the tests that map it build Nepal from a written grid specification, and the acceptance tests against GEM's published Nepal data use a written enrichment — the path somebody working on a country now takes. KRE's own Nepal policies are untouched |

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
  items 24, 25 and 27, and the Nepal prototype was removed once it existed
  (item 30).
- **The multi-IMT representation, the realisation-weighting rule and the
  footprint storage format.** Not waiting on anybody else: CASS is to research
  each and decide. They are backlog items 21, 22 and 23, and each ends in a
  decision record rather than a preference.

### Answered on 14 September 2026

- **Who decides the modelling-science questions.** CASS researches and tests
  the candidates for the multi-IMT representation, the realisation-weighting
  rule, the footprint storage format and the event representation (items 21,
  22, 23 and 26), measures them on the platform, and decides with the evidence
  written down in plain terms. They are not waiting on a reviewer's judgement.
- **The realisation-weighting rule.** Decided, on the platform's own
  measurements ([ADR 18](adr/0018-sampled-paths-pooled-as-one-catalogue.md)): a
  hazard run samples twenty paths through the logic tree and pools them as one
  catalogue, and an enumerated tree stays refused. One path was a lottery of
  roughly ±20% against the model's own weighted mean, and twenty cost the same
  to run. A hazard set now records how many paths it pools, and its effective
  time counts them: without that, the twenty-path default would have divided
  every annual rate and every AAL by twenty while the record still read as
  complete (item 35).
- **The footprint storage format.** Decided
  ([ADR 19](adr/0019-footprint-stays-a-ktools-binary.md)): the ktools binary
  stays, compression is measured and held in reserve, and Parquet is rejected.
  At national scale every format gave the same loss to the cent, so the choice
  is disk and build time rather than the answer.
- **The event representation.** Decided, on the platform's own measurements
  ([ADR 17](adr/0017-an-oasis-event-is-a-simulated-occurrence.md)): an Oasis
  event is one simulated occurrence, and rupture binning is not built. In this
  catalogue ruptures almost never recur, so pooling them changed the average
  annual loss by 0.04% and saved neither space nor time.
- **The multi-IMT representation.** Decided, on the platform's own measurements
  ([ADR 16](adr/0016-multi-measure-classes-as-sub-peril-channels.md)): a class
  spanning intensity measures is carried as one earthquake sub-peril item per
  measure, on functions pre-weighted by each measure's share of the class, with
  the financial terms the engine receives scoped to all earthquake perils. The
  alternative was to keep refusing such a class, which left a book with no
  storey counts unmodelled.
- **Backup and restore of research work.** Postponed: not yet necessary
  (item 18).
- **Where the GEM release comes from.** Not bundled: the 2026 release is too
  large to carry in the codebase. The person using CASS points it at the GEM
  clone on their own device, and CASS checks that the folder has the exact GEM
  repository layout and says which release it found (item 31).
