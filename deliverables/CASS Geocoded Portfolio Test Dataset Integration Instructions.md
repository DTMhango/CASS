# CASS Geocoded Portfolio Test Dataset Integration Instructions

*Implementation brief for the 30 June 2026 portfolio extract*

| Item | Position |
| --- | --- |
| Source snapshot | `premium_policies_2026-06-30_geocoded.xlsx` |
| Source status | Confidential KRE working data; keep outside version control and test images |
| Snapshot date | 30 June 2026 |
| Countries | Indonesia and Nepal |
| Immediate use | Import, geospatial lookup, grid validation and platform workflow testing |
| Value basis | `gross_limit` is reported KRE-share TIV; all monetary values are USD |
| Peril coverage | Assume earthquake coverage for every policy in this extract |
| Loss-use status | Research/testing until location allocation, coverage-component allocation, vulnerability taxonomy and model validation are approved |

## 1  Decision

Scope this extract into the programme now as reported KRE-share earthquake exposure. It provides a strong real-portfolio test of identifiers, one-to-many locations, coordinates, quality evidence, country coverage and facultative workflow. The reported `gross_limit` is the TIV at KRE's share, all values are USD and every policy is assumed to cover earthquake. The extract still lacks the coverage-component split and building attributes needed to select vulnerability functions and interpret the resulting loss without material assumptions.

The work therefore has three separate purposes:

1. **Platform and geospatial testing.** Use the real hierarchy and approved coordinates to test import, review, fixed-grid lookup, keys, artifacts, manifests and run monitoring.
2. **Controlled KRE-share earthquake loss.** Use the reported USD TIV with explicit multi-location and coverage-component allocation scenarios to test the OpenQuake-to-Oasis workflow. These are research results until taxonomy and model validation gates pass.
3. **Business-loss preparation.** Enrich missing vulnerability attributes and confirm any financial terms required beyond the proportional KRE-share gross-damage view before a result is used for a decision.

This dataset does not change the fixed adaptive-grid architecture. Production hazard remains a reusable model asset generated on the approved Indonesia and Nepal grids. Portfolio coordinates map to those grids. Direct OpenQuake calculations at selected portfolio sites are validation studies only.

## 2  Profile of the available data

The source workbook contains:

- **1,353 policy rows** representing **1,351 business identifiers**. Policy identifiers are complete and unique; two business identifiers occur on two policy rows and need an explicit join rule.
- **224 risk-location rows** linked to **213 business identifiers**. Ten businesses have more than one location, producing eleven secondary-location rows.
- **194 location rows in Indonesia** and **30 in Nepal**.
- Complete latitude/longitude pairs for all 224 risk-location rows. No coordinate is partial, out of range or `(0, 0)`.
- Exact agreement between the policy-level primary coordinates and the 213 primary rows in the Risk Locations sheet.
- Location classes of **148 Fire**, **75 Engineering** and **1 Liability**. Marine policies have no rows in the available geocoded location sheet.
- **115 location rows marked as needing review** and **109 marked as not needing review**.
- 167 distinct coordinate pairs. Repeated coordinates must be investigated as possible shared sites, group risks or geocoding centroids; they must not be deduplicated merely because latitude and longitude match.

The extract's `gross_limit` is the reported TIV at KRE's share and all monetary fields are USD. Total TIV is **USD 3,327,746,598.60** across the complete policy sheet. The 213 businesses with coordinates account for **USD 822,816,504.04** of that TIV. The source does not split TIV into building, contents, stock, machinery or business-interruption components, so those allocations remain explicit scenario assumptions that must reconcile to the reported total.

Every policy in this extract is to be treated as earthquake-covered. Because the TIV is already at KRE's share, the initial physical-damage result should be labelled **KRE-share gross damage**, not 100%-of-risk ground-up loss. Do not apply the KRE share a second time. The extract still lacks deductible and attachment interpretation, occupancy, construction, storeys, height, year built, code level and ductility. Class of business is useful evidence for enrichment but is not a vulnerability taxonomy.

## 3  Governed coordinate cohorts

Coordinate presence alone is not the eligibility rule. Create three derived, versioned cohorts using the source `precision` and `needs_review` fields. Preserve every source row and record the rule version that assigned the cohort.

### Cohort A — automated test cohort

Eligibility:

- latitude and longitude both present and within valid ranges;
- not `(0, 0)`;
- `needs_review = No`; and
- precision is `parcel`, `street` or `embedded`.

Current size: **63 location rows** — 53 Indonesia and 10 Nepal. This contains 43 Fire, 19 Engineering and 1 Liability location.

Use:

- automated import and mapping regression;
- area-peril lookup and border/offshore checks;
- a small controlled earthquake workflow after scope filters below are applied.

### Cohort B — geocoding-sensitivity cohort

Eligibility:

- valid coordinate pair;
- `needs_review = No`; and
- precision is `locality`, `postcode` or `admin`.

Current size: **46 location rows** — 31 Indonesia and 15 Nepal.

Use:

- compare area-peril assignment under the recorded coordinate and plausible spatial alternatives;
- measure whether grid resolution implies more precision than the geocode supports;
- test uncertainty flags in the interface.

Do not merge Cohort B into the primary benchmark without reporting its sensitivity separately.

### Cohort C — analyst-review backlog

Eligibility: `needs_review = Yes`.

Current size: **115 location rows** — 110 Indonesia and 5 Nepal.

Use:

- exercise review queues and correction/versioning;
- prioritize manual work by potential materiality once a valid value measure is available;
- exclude from the automated benchmark until reviewed or explicitly approved for a stated sensitivity run.

### Initial earthquake subset

Start with a **business-complete Cohort A Fire subset**: include a policy only when every location attached to its business is Cohort A and classified as Fire. The current extract yields **42 businesses and 42 locations**: 39 Indonesia and 3 Nepal, with **USD 147,044,599.14** of reported KRE-share TIV. Requiring a complete business prevents the test from silently moving TIV away from an excluded or review-required secondary site. Fire is still only a coarse business classification, but it is closer to the first property physical-damage test than Liability or undifferentiated Engineering.

Keep Engineering as a separate workstream. Construction/erection risks, operational plants, machinery and civil infrastructure may require different occupancy, duration, value and vulnerability treatment. Exclude the single Liability location from physical-damage loss testing. Never infer that Marine has no earthquake accumulation merely because it is absent from the geocoded subset.

## 4  Source ingestion contract

### 4.1 Preserve the source

1. Register the workbook as a confidential, immutable source artifact.
2. Record its SHA-256 checksum, snapshot date, uploader, project, source description and retention policy.
3. Store it in the private artifact store, not in Git, container images, logs or browser bundles.
4. Restrict download to project members with portfolio-data permission.
5. Display insured, cedent and broker names only where the user's role requires them. Use source references in technical logs.

> *Superseded 12 September 2026.* Every CASS user sees portfolio data, including names and addresses, because the geocoding review needs the address beside the coordinate. Only what leaves the platform — logs and support bundles — carries identifiers instead of portfolio rows ([ADR 11](../platform/docs/adr/0011-intake-template-and-policy-id.md)).

### 4.2 Parse both sheets independently

Create staging records without changing the source values:

- `SourcePolicyRow` from **Premium Policies**;
- `SourceRiskLocation` from **Risk Locations**;
- `ImportBatch` holding checksum, parser version, warnings and row counts.

Do not join during parsing. Validate types, required identifiers and coordinate pairs first, then create a deterministic join report.

### 4.3 Join rules

1. Join risk locations to policy/business data through `business_id`.
2. Use `(business_id, location_number)` as the risk-location natural key; all 224 current combinations are unique.
3. Refuse an accidental many-to-many expansion. Two business identifiers have more than one policy row and require a documented policy-selection or split rule.
4. Preserve all locations for the ten multi-location businesses. `primary_location` is an attribute, not a filter that deletes secondary sites.
5. Confirm that the policy `risk_location_count` equals the number of location rows after each import. It does in the current snapshot.
6. Do not deduplicate by coordinates. Flag shared coordinate pairs for review while retaining their separate business and policy identities.

### 4.4 Suggested business-to-OED identity mapping

Use stable, non-name-based identifiers:

- CASS project/KRE extract reference → `PortNumber`;
- `business_id` → account/business source reference and candidate `AccNumber`;
- `policy_id` → policy source reference;
- `location_number` → `LocNumber` within the business;
- a deterministic location UUID → internal CASS location ID;
- country name → validated ISO `CountryCode` (`ID` or `NP`);
- latitude/longitude → OED location coordinates;
- geocode method, precision, confidence, rationale and review flag → CASS evidence/lineage fields, not silent OED substitutions.

Confirm the exact OED account/policy hierarchy against the pinned OED/ODS Tools version before publishing the importer. Never use insured or broker name as an identifier.

## 5  Value and financial treatment

### 5.1 Confirmed source interpretation

Apply these confirmed rules:

- map `gross_limit` to total TIV at KRE's share;
- set source and run currency to USD;
- set earthquake as covered for every selected location;
- preserve `gross_limit` as the reported source field and create a distinct normalized `total_tiv_kre_share` field;
- reconcile all derived location and coverage-component values exactly to `total_tiv_kre_share`;
- do not multiply by a share percentage or apply a KRE share again.

Obtain or confirm the remaining definitions for:
- the allocation of total TIV among building, contents, stock, machinery and business interruption;
- replacement-cost versus indemnity-value interpretation of the reported TIV;
- deductibles, limits, attachments, layers, reinstatements and occurrence terms;
- whether multi-location limits are shared, scheduled or location-specific;
- premium and limit treatment for renewals and endorsements.

Until these remaining points are answered, results must disclose that TIV is reported at KRE's share in USD and that component and multi-location allocations are assumptions.

### 5.2 Separate the test modes

For the first engine test, provide an explicit run mode:

- **Geometry-only:** map coordinates to the model grid and vulnerability eligibility; calculate no portfolio loss.
- **KRE-share technical loss:** use reported USD TIV, an explicit location-allocation scenario and deliberately selected test taxonomies. Store the reported TIV lineage and block decision-use approval.
- **Portfolio-loss research:** use reported USD KRE-share TIV after missing building attributes are enriched under a versioned assumption set; display the component and multi-location allocation scenarios.
- **Decision use:** permitted only after scientific validation, licensing, data-quality thresholds and independent approval are complete.

Never mix outputs from these modes in one comparison without an explicit warning.

### 5.3 Multi-location allocation

Do not repeat a policy/business-level TIV at every location. Ten multi-location businesses hold USD 55,176,423.17, or 6.7% of geocoded TIV, so the allocation is material enough to test.

Apply the following versioned rule separately to every `policy_id`. A repeated `business_id` may provide the same location schedule to more than one distinct policy, but each policy's TIV is allocated and reconciled once before any aggregation.

#### Eligibility gate

1. Build the complete location schedule for the policy's `business_id`.
2. If the run requires an approved location cohort and any scheduled location is outside it, do not silently redistribute that location's share. Either exclude the whole policy or obtain an explicit scenario approval covering the incomplete schedule.
3. Never remove a secondary location because it is not marked primary.

#### Evidence-first allocation

Use the first available rule:

1. reported location TIV or reported allocation percentages;
2. a supported value driver such as replacement cost, declared asset value, floor area or insured machinery value;
3. the default equal-location rule below.

Geocode confidence, precision, provider, address length and `primary_location` are not measures of economic value and must not be used as baseline TIV weights.

#### Default baseline: equal location allocation

For a policy with `n` scheduled locations and no site-value evidence:

`location_tiv = policy_tiv / n`

This is the maximum-ignorance allocation: it introduces no unsupported ranking among sites. Record `allocation_method = equal_location_v1`, `allocation_evidence = assumed` and the location count. A single-location policy receives 100% automatically.

#### Required sensitivity scenarios

For every multi-location policy, calculate alongside the equal baseline:

- **Primary concentrated:** allocate 70% to the reported primary location and divide 30% equally among secondary locations. This tests whether the primary flag is also economically material without asserting that it is.
- **Location concentration envelope:** run one variant per scheduled location with 100% of policy TIV at that location. Report the minimum and maximum portfolio loss across the variants. This bounds the effect of unknown site weights without selecting weights from modelled hazard.

Do not use “highest hazard” or “highest modelled loss” to choose the baseline location. That would make exposure allocation depend on the result being measured. It is acceptable only as a separately labelled stress result within the concentration envelope.

#### Exact reconciliation and rounding

1. Calculate with `Decimal`, never binary floating point.
2. Keep unrounded allocation weights in the assumption record.
3. Allocate to USD cents using a largest-remainder method: round down each provisional amount, then distribute remaining cents in ascending deterministic `(policy_id, location_number)` order among the largest fractional remainders.
4. Require `sum(location_tiv) = gross_limit` for every policy, every business, each country and the portfolio.
5. Store the pre-allocation TIV, weights, rounded allocations, residual-cents decisions and rule version in the run manifest.

#### Override rule

An analyst may replace an assumed weight only with a recorded rationale and evidence reference. The override creates a new enrichment/exposure version. It does not edit the source workbook or a published run.

### 5.4 Coverage-component allocation

Location allocation and coverage-component allocation are independent. First allocate each policy's TIV to locations; then split each location total into OED building, contents, other/machinery and BI components. The component weights must sum to one and the component values must reconcile to the location total.

Use reported component values first. Otherwise derive country-, class- and occupancy-conditioned weights from the approved exposure/enrichment methodology. Do not use the location allocation scenarios to imply a component split. Until component priors are approved, a building-only mapping may be used solely as an engine smoke fixture and must be labelled `building_only_technical_v1`; it is not a complete physical-damage result.

## 6  Exposure enrichment and vulnerability eligibility

1. Preserve `class_of_business` as reported evidence.
2. Define controlled mappings from Fire and Engineering descriptions to occupancy candidates; do not map the labels directly to a GEM taxonomy.
3. Use other reported evidence, including business title and source documents, only through permission-controlled rules. Record which fields contributed to the classification without copying sensitive text into logs.
4. Apply GEM exposure distributions only where the property attributes are absent, conditioned by country, administrative area, occupancy evidence and facultative selection.
5. Produce Baseline, More Robust and More Vulnerable assumption sets. Every probability vector and allocated value must sum to its source total.
6. Measure which resulting taxonomies use SA(0.3), SA(0.6), SA(1.0) or deferred PGA vulnerability functions. Block a claim of complete model coverage where PGA-dependent exposure remains.
7. Keep geocoding uncertainty separate from vulnerability uncertainty so the user can see which source drives a loss range.

## 7  Platform work items

### Work package 1 — secure importer

Build a `Klapton Re geocoded policy extract` import profile in the CASS Exposure workspace.

Required outputs:

- immutable raw artifact;
- parsed policy and location staging records;
- schema/version check;
- join report;
- coordinate-quality cohort assignment;
- exclusions and review queue;
- a downloadable transformation manifest without insured names by default.

Acceptance checks:

- 1,353 policy rows read;
- 224 risk-location rows read;
- 224 unique `(business_id, location_number)` keys retained;
- 213 primary and 11 secondary locations retained;
- 213 primary coordinates match their policy-level coordinates;
- two repeated business identifiers are reported without creating duplicate locations;
- source checksum and parser version appear in the audit trail.

### Work package 2 — eligibility and review interface

Add an import-results screen showing:

- included rows by country, class and cohort;
- missing model inputs;
- coordinate precision and review status;
- multi-location and repeated-coordinate findings;
- excluded classes and reasons;
- policy-to-location TIV allocations, method and reconciliation;
- the equal, primary-concentrated and concentration-envelope scenario selection;
- the difference between geometry, technical-test, research and decision-use modes.

An analyst may change a cohort decision only by recording a rationale. The change creates a new derived exposure version; it does not edit the source artifact.

### Work package 3 — area-peril and keys test

1. Load the approved version of the Indonesia and Nepal fixed grids.
2. Map Cohort A coordinates using a spatial index, not a linear scan.
3. Report outside-domain, border, offshore and tolerance cases.
4. Compare Cohort B mappings under appropriate location uncertainty buffers.
5. Run selected portfolio sites directly in OpenQuake only as a grid-validation comparison.
6. Retain the grid ID, mapping algorithm version and mapping distance with each key result.

### Work package 4 — controlled Oasis earthquake test

After the PiWind platform slice proves the Oasis adapter:

1. publish the business-complete 42-risk Cohort A Fire subset as a research exposure version;
2. load `gross_limit` as reported KRE-share TIV in USD and assume earthquake coverage;
3. apply and record the approved multi-location and coverage-component allocation scenario;
4. apply a small approved test taxonomy set covering the active SA channels;
5. run CASS keys and require complete record/value reconciliation;
6. generate Oasis files through the pinned OasisLMF implementation;
7. execute the small earthquake golden model;
8. ingest losses and display the KRE-share basis, data-quality cohort, allocation scenario, assumption set, model version and manifest;
9. compare against the controlled OpenQuake reference calculation.

### Work package 5 — portfolio-loss readiness

1. Resolve the remaining data dictionary questions in section 5.1.
2. Import or derive valid TIV coverage components while retaining USD and KRE-share lineage.
3. implement reported-versus-inferred attribute lineage;
4. obtain and map supported financial terms;
5. run enrichment sensitivities;
6. perform value and keys reconciliation;
7. complete actuarial, underwriting and catastrophe-model review before allowing a decision-use result.

## 8  Automated tests

Add masked or synthetic fixtures that preserve the structural cases but not names, addresses or live amounts:

- one ordinary single-location business;
- one multi-location business;
- one business identifier with multiple policy rows;
- one repeated coordinate shared by different businesses;
- one parcel/street location, one locality/admin location and one review-required location;
- Indonesia and Nepal rows;
- Fire, Engineering and excluded-class rows;
- KRE-share USD TIV, component-allocation and multi-location-allocation variants.

Tests must prove:

- deterministic import and IDs;
- no join fan-out;
- no coordinate-based deduplication;
- no policy value duplication across locations;
- equal-location, primary-concentrated and concentration-envelope calculations reconcile to every policy TIV;
- rounding remains deterministic to USD cents;
- exact source-to-OED-to-coverages reconciliation;
- unavailable loss modes remain blocked;
- confidential columns do not enter logs or support bundles;
- rerunning the same checksum is idempotent;
- correcting a source mapping creates a new version;
- the original source and prior outputs remain reproducible.

Do not commit the real workbook as a test fixture.

## 9  Delivery sequence

Execute in this order:

1. Protect and register the source workbook; approve who may access it.
2. Record the confirmed KRE-share TIV, USD and earthquake-coverage interpretation; complete the remaining hierarchy, component-allocation and financial-term definitions.
3. Build the parser, join report and cohort rules with masked structural fixtures.
4. Complete the CASS-to-PiWind-to-Oasis vertical slice and engine contract tests.
5. Import the workbook into a restricted CASS project owned by KRE and verify the aggregate acceptance counts.
6. Map Cohort A and B to versioned grids; complete the geocoding sensitivity report.
7. Run the Cohort A Fire geometry-only test.
8. Run the business-complete 42-risk Cohort A Fire earthquake golden test using USD 147,044,599.14 of KRE-share TIV; then complete the OpenQuake reference comparison.
9. Add the ten multi-location businesses as a dedicated USD 55,176,423.17 allocation-sensitivity test using equal, primary-concentrated and concentration-envelope scenarios.
10. Add coverage-component evidence, supported financial terms and governed enrichment.
11. Promote selected records into the controlled portfolio-loss pilot only after review gates pass.

The real workbook must not become a shortcut around the PiWind integration milestone. PiWind proves the supported Oasis workflow. This extract then proves that CASS handles KRE's real hierarchy, geography, incomplete attributes and review decisions.

## 10  Completion evidence

This dataset is successfully scoped when the programme can show:

- a registered immutable source artifact with restricted access;
- a repeatable import producing the expected policy/location counts without fan-out;
- visible Cohort A, B and C assignments with reasons;
- a fixed-grid mapping report for Indonesia and Nepal;
- a geometry-only run that makes no financial claim;
- a reported KRE-share USD end-to-end run clearly labelled with allocation assumptions and blocked from decision use until approval;
- exact policy-level TIV reconciliation under all multi-location allocation scenarios;
- a documented list of unresolved component-allocation, financial-term and taxonomy questions;
- masked automated fixtures covering the source's structural edge cases;
- a signed readiness decision before any result is presented as a KRE portfolio loss estimate.

## 11  Status at 13 September 2026

The source now reaches CASS through the CASS intake template rather than as the two-sheet workbook. The 30 June extract was migrated into the template once, joined on the Policy ID stated on both sheets, and the join rules of section 4.3 now describe that migration only ([ADR 11](../platform/docs/adr/0011-intake-template-and-policy-id.md)). The working tracker is [CASS delivery status](../platform/docs/delivery-status.md).

| Completion evidence (section 10) | Status |
| --- | --- |
| Registered immutable source artifact with restricted access | Done |
| Repeatable import with the expected counts and no fan-out | Done; checked in `test_extract_acceptance.py` against the real workbook |
| Visible Cohort A, B and C assignments with reasons | Done, on the Import review tab |
| Fixed-grid mapping report for Indonesia and Nepal | Partly done: keys reports per run against the prototype grids; Cohort B sensitivity not built |
| Geometry-only run making no financial claim | Not built |
| KRE-share USD end-to-end run, labelled and blocked from decision use | Done against the fixture model |
| Exact policy-level TIV reconciliation under every allocation scenario | Done |
| Documented list of unresolved component, financial-term and taxonomy questions | Listed in section 5.1 and the tracker; unanswered |
| Masked automated fixtures covering the structural edge cases | Done |
| Signed readiness decision | Not taken |

| Work package | Status |
| --- | --- |
| WP1 Secure importer | Done, then superseded by the intake template |
| WP2 Eligibility and review interface | Done |
| WP3 Area-peril and keys test | Partly done: mapping and outside-domain reporting; Cohort B sensitivity and OpenQuake site comparison not built |
| WP4 Controlled Oasis earthquake test | Steps 1 to 8 done against the fixture model; step 9, the OpenQuake reference comparison, not built |
| WP5 Portfolio-loss readiness | Not started |
