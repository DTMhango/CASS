/**
 * Types mirroring the CASS API.
 *
 * These are hand-written against the OpenAPI schema the backend generates
 * rather than produced from it, so that a backend field rename shows up as a
 * type error here instead of as an undefined at runtime. Regenerate them from
 * `/api/schema/` when the contract changes materially.
 */

export type UUID = string;

export type PlatformRole =
  | "analyst"
  | "modeller"
  | "underwriter"
  | "reviewer"
  | "admin";

export type ProjectRole = "viewer" | "contributor" | "owner";

export interface Capabilities {
  publish_models: boolean;
  approve_gates: boolean;
  administer_platform: boolean;
}

export interface User {
  id: UUID;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  full_name: string;
  platform_role: PlatformRole;
  job_title: string;
  local_install_approved: boolean;
  /** Whether they may sign in. Only an administrator's directory lists inactive people. */
  is_active?: boolean;
  capabilities: Capabilities;
}

export interface Session {
  authenticated: boolean;
  user?: User;
}

export interface Project {
  id: UUID;
  name: string;
  reference: string;
  purpose: string;
  team: string;
  status: "active" | "archived";
  my_role: ProjectRole | null;
  exposure_version_count: number;
  active_run_count: number;
  created_at: string;
  updated_at: string;
}

/** The four OED source inputs of build plan section 8. */
export type OEDFileKind = "location" | "account" | "reins_info" | "reins_scope";

export type ExposureState =
  | "draft"
  | "validating"
  | "validated"
  | "published"
  | "rejected";

export type Severity = "error" | "warning" | "info";

export interface Finding {
  code: string;
  severity: Severity;
  message: string;
  /** What the analyst should do about it, from the governed catalogue. */
  remediation: string;
  file_kind: string;
  row_number: number | null;
  field: string | null;
  value: string | null;
  /** Identifies the business record, so a user can go back to it. */
  record_key: string | null;
}

export interface ValidationSummary {
  blocking: boolean;
  error_count: number;
  warning_count: number;
  counts_by_code: Record<string, number>;
  findings: Finding[];
}

export type PerspectiveKey = "ground_up" | "insured" | "reinsurance";

/**
 * What a run is for, and so what its output may claim (brief section 5.2).
 * Only a decision-use run can produce a result a reviewer may approve.
 */
export type RunMode = "geometry_only" | "technical" | "research" | "decision";

export interface PerspectiveAvailability {
  perspective: PerspectiveKey;
  label: string;
  available: boolean;
  /** Why it is unavailable, in words an analyst can act on. */
  reason: string;
}

export interface AttachedFile {
  role: string;
  uri: string;
  checksum: string;
  size_bytes: number;
  original_filename: string;
}

export interface ExposureVersion {
  id: UUID;
  project: UUID;
  name: string;
  version: number;
  state: ExposureState;
  valuation_date: string | null;
  source_description: string;
  run_currency: string;
  oed_schema_version: string;
  location_count: number;
  account_count: number;
  total_tiv: string;
  tiv_by_coverage: Record<string, string>;
  tiv_by_country: Record<string, string>;
  tiv_by_currency: Record<string, string>;
  unmodelled_subperils: string[];
  supported_perspectives: PerspectiveAvailability[];
  validation_report: { publishable?: boolean; validation?: ValidationSummary } | null;
  attached_files: AttachedFile[];
  is_frozen: boolean;
  is_publishable: boolean;
  is_usable_by_runs: boolean;
  created_at: string;
  updated_at: string;
}

export interface ExposurePreview {
  oed_schema_version: string;
  files: Record<
    string,
    {
      row_count: number;
      columns: string[];
      unrecognised_columns: string[];
      rows: Record<string, string>[];
    }
  >;
}

export type PublicationState =
  | "draft"
  | "candidate"
  | "approved"
  | "published"
  | "superseded"
  | "withdrawn";

export interface CatalogueModel {
  id: UUID;
  reference: string;
  label: string;
  country_code: string;
  peril: string;
  version: string;
  imts: string[];
  publication_state: PublicationState;
  usable_for_decisions: boolean;
  is_research_prototype: boolean;
  validation_date: string | null;
  grid: string;
  assumptions_note: string;
  peril_scope: Record<string, unknown>;
  unsupported_taxonomy_report: Record<string, unknown>;
  /** Why this version may not be published as a full country model. */
  blockers: string[];
  /** Assumption sets a run against this version may name (ADR 14). */
  assumption_sets?: CatalogueAssumptionSet[];
}

/** An assumption set a model version carries functions for. */
export interface CatalogueAssumptionSet {
  id: UUID;
  flavour: string;
  label: string;
  reference: string;
  publication_state: string;
  /** A run under a set nobody has approved produces research output. */
  approved: boolean;
}

export type RunState =
  | "draft"
  | "queued"
  | "running"
  | "blocked"
  | "cancelling"
  | "cancelled"
  | "failed"
  | "succeeded";

export type RunKind = "hazard" | "conversion" | "analysis";

export interface PipelineStage {
  key: string;
  label: string;
  description: string;
  /** Stages that may legitimately hold a run for approval. */
  gate: boolean;
  position: number;
}

export interface Run {
  id: UUID;
  kind: RunKind;
  project: UUID | null;
  label: string;
  state: RunState;
  stage: string;
  stage_label: string;
  /** Fraction of the pipeline's stages completed. Stages, not work. */
  progress: number;
  /**
   * How far into the current stage the engine says it has got, where it says
   * anything. Null is the honest answer when it does not, and the label is what
   * the fraction counts: an OpenQuake phase restarts its own percentage, so the
   * number means nothing without it.
   */
  stage_progress: number | null;
  stage_progress_label: string;
  pipeline: PipelineStage[];
  execution_profile: string;
  correlation_id: string;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  /** How long a finished run took. Null until it finishes. */
  duration_seconds: number | null;
  /** Time on the clock now, measured on the server. Null until it starts. */
  elapsed_seconds: number | null;
  peak_memory_mb: number | null;
  failure_stage: string;
  failure_summary: string;
  failure_detail: string;
  /** Why a run is waiting at a governance gate. A blocked run has not failed. */
  gate_summary: string;
  gate_detail: string;
  /** What the run has recorded so far: stage evidence, checks and lineage. */
  manifest: Record<string, unknown>;
  settings_hash: string;
  retry_of: UUID | null;
  may_retry: boolean;
  may_publish_results: boolean;
  is_active: boolean;
  created_at: string;
}

/** One operational check the review stage made of a published result. */
export interface ReviewCheck {
  check: string;
  /** Null where there was nothing to evaluate, which is not the same as a pass. */
  passed: boolean | null;
  detail: string;
}

/** What the review stage recorded on the run manifest. */
export interface ReviewRecord {
  checks: ReviewCheck[];
  failed: number;
  approved_exception?: string;
  model_version?: {
    reference: string;
    publication_state: string;
    research_prototype: boolean;
  };
}

/** What the pre-loss smoke check recorded on the run manifest. */
export interface SmokeRecord {
  performed: boolean;
  reason?: string;
  event_ids?: number[];
  evaluated?: boolean;
  problems?: string[];
  perspectives?: Record<string, { rows: number; events_with_loss: number }>;
}

export interface RunStageEvent {
  id: UUID;
  stage: string;
  state: RunState;
  message: string;
  metrics: Record<string, unknown>;
  created_at: string;
}

/** What the keys lookup and any currency conversion said about the book. */
export interface ExposureQuality {
  location_count?: number;
  /** Policies whose insured loss is their ground-up loss for want of a limit. */
  possibly_overstated?: {
    policy_count: number;
    tiv?: string;
    uncapped: number;
    attachment_read_as_zero: number;
    no_policy_row: number;
    first: string[];
  };
  source_tiv?: string;
  successful_tiv?: string;
  not_at_risk_tiv?: string;
  unmapped_tiv?: string;
  keys_reconciled?: boolean | null;
  currency_conversion?: {
    from_currency?: string;
    to_currency?: string;
    rate?: string;
    direction?: string;
    valuation_date?: string;
    source?: string;
    reference?: string;
  };
}

export interface ResultCaveats {
  model_version: string;
  assumption_set: string;
  run_mode: RunMode | "";
  valuation_date: string | null;
  perspective: string;
  currency: string;
  approval_status: string;
  usable_for_decisions: boolean;
  exposure_quality: Record<string, unknown>;
  peril_scope: Record<string, unknown>;
  material_exclusions: string[];
  uncertainty_attribution: Record<string, unknown>;
}

/** One catastrophe layer as a limited-cover result applied it, per simulated year. */
export interface CoverLayerDetail {
  contract: number;
  layer: number;
  name: string;
  attachment: number;
  limit: number;
  ceded: number;
  placed: number;
  reinstatements: number | null;
  rates: number[];
  premium: number | null;
  recovered_aal: number;
  premium_aal: number;
  unlimited_recovered_aal: number;
  exhausted_share: number;
  applied_as_engine: boolean;
}

/** A result on the mean-sample basis, as the limited calculation states one. */
export interface CoverMetricsDetail {
  average_annual_loss: number;
  standard_deviation: number;
  aep: Record<string, number>;
  oep: Record<string, number>;
}

/** Why a limited-cover result says what it says, and how it was checked. */
export interface CoverDetail {
  limited?: CoverMetricsDetail;
  unlimited?: CoverMetricsDetail;
  insured?: CoverMetricsDetail;
  layers?: CoverLayerDetail[];
  periods?: number;
  samples?: number;
  event_order?: string;
  check?: {
    unlimited_net_aal: number;
    engine_net_aal: string;
    net_difference_share: number | null;
    insured_aal: number;
    engine_insured_aal: string | null;
    insured_difference_share: number | null;
    tolerance: number;
    agrees: boolean;
  };
  notes?: string[];
}

export interface ResultSet {
  id: UUID;
  run: UUID;
  project: UUID;
  label: string;
  /** ``ri_terms`` is net of reinsurance with cover limited by contract terms. */
  perspective: PerspectiveKey | "ri_terms";
  cover_detail?: CoverDetail;
  state: "draft" | "approved" | "research" | "withdrawn";
  average_annual_loss: string | null;
  standard_deviation: string | null;
  currency: string;
  return_period_losses: Record<string, string>;
  model_version_reference: string;
  assumption_set_reference: string;
  /** The mode the run was made under. Blank on results published before modes. */
  run_mode: RunMode | "";
  valuation_date: string | null;
  exposure_quality: Record<string, unknown>;
  peril_scope: Record<string, unknown>;
  material_exclusions: string[];
  uncertainty_attribution: Record<string, unknown>;
  usable_for_decisions: boolean;
  caveats: ResultCaveats;
  approved_at: string | null;
  is_frozen: boolean;
  created_at: string;
}

/** One event's loss to the portfolio, as the moment event loss table reports it. */
export interface EventLoss {
  event_id: string;
  mean_loss: string;
  standard_deviation: string | null;
  maximum_loss: string | null;
  event_rate: string | null;
  chance_of_loss: string | null;
  impacted_exposure: string | null;
}

export interface EventLossPage {
  count: number;
  limit: number;
  offset: number;
  currency: string;
  results: EventLoss[];
}

/** What one execution profile declares, and what is using it now. */
export interface ProfileCapacity {
  profile: string;
  cpu: number;
  memory_gb: number;
  timeout_seconds: number;
  max_concurrent: number;
  running: number;
  available: number;
  is_full: boolean;
}

export interface PlatformInfo {
  api_version: string;
  oed_schema_version: string;
  compatibility_matrix: Record<string, string>[];
  execution_profiles: Record<
    string,
    { cpu: number; memory_gb: number; timeout_seconds: number; max_concurrent: number }
  >;
  /** Section 11's admission control: what each profile has room for right now. */
  profile_capacity?: ProfileCapacity[];
  default_execution_profile: string;
  artifact_backend: string;
  engines: Record<string, string>;
}

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

// -- the import review, work package 2 ---------------------------------------

export interface PortfolioImport {
  id: UUID;
  project: UUID;
  source_filename: string;
  source_checksum: string;
  parser_version: string;
  cohort_rule_version: string;
  policy_row_count: number;
  risk_row_count: number;
  state: string;
  findings: Finding[];
}

export interface StoreyCoverage {
  locations: number;
  stated_in_source: number;
  established_in_review: number;
  unstated: number;
  stated_share: number;
  value_stated_in_source: number;
  value_established_in_review: number;
  value_unstated: number;
  value_stated_share: number;
  note: string;
}

export interface ReviewDecisionRecord {
  field: string;
  from: string;
  to: string;
  rationale: string;
  decided_by: string;
  decided_at: string;
}

export interface QueuedLocation {
  id: UUID;
  business_id: string;
  location_number: string;
  primary_location: boolean;
  class_of_business: string;
  country_code: string;
  coordinate: string;
  precision: string;
  needs_review: boolean;
  total_insured_value: number | null;
  cohort: string;
  cohort_reason: string;
  storeys: number | null;
  storeys_are_reviewed: boolean;
  history: ReviewDecisionRecord[];
}

export interface ReviewQueue {
  batch: UUID;
  outstanding: number;
  outstanding_value: number;
  locations: QueuedLocation[];
}

/**
 * Something the import found in the workbook: a cell to correct, a row the
 * country screen could not place, a contract layer the rules refuse. Grouped
 * where one cause repeats, so ``row_number`` is the first row it applies to.
 */
export interface IntakeFinding {
  sheet: string;
  row_number: number | null;
  field: string;
  code: string;
  message: string;
  value: string;
}

export interface ImportResults {
  batch: {
    id: UUID;
    filename: string;
    source_checksum: string;
    /** The date the schedule describes, where the importer stated one. */
    snapshot_date: string | null;
    parser_version: string;
    cohort_rule_version: string;
    /** The rules a fresh import applies; importing again re-reads under these. */
    current_cohort_rule_version: string;
    overlay_version: string;
    policy_row_count: number;
    risk_row_count: number;
    state: string;
  };
  included: {
    country_code: string;
    class_of_business: string;
    cohort: string;
    locations: number;
    value: number;
  }[];
  total_value: number;
  review: {
    overlay_version: string;
    cohort_rule_version: string;
    locations: number;
    by_cohort: Record<string, number>;
    by_review_state: Record<string, number>;
    outstanding: number;
    decided: number;
    decisions: Record<string, unknown>[];
    storeys: StoreyCoverage;
  };
  missing_model_inputs: {
    field: string;
    locations: number;
    value: number;
    consequence: string;
  }[];
  multi_location_businesses: {
    business_id: string;
    locations: number;
    value: number;
    states_own_values: boolean;
  }[];
  repeated_coordinates: {
    coordinate: string;
    count: number;
    businesses: string[];
    value: number;
  }[];
  findings: IntakeFinding[];
  intake_report: Record<string, unknown>;
  cohort_profile: Record<string, unknown>;
  use_modes: { mode: string; meaning: string }[];
  allocation_note: string;
}

// -- uploaded hazard models --------------------------------------------------

export interface JobParameter {
  name: string;
  section: string;
  kind: string;
  label: string;
  editability: "model" | "science" | "discretisation" | "output";
  help_text: string;
  consequence: string;
  choices: string[];
  minimum: number | null;
  maximum: number | null;
  unit: string;
  required_for_footprint: string;
}

export interface JobSetting {
  name: string;
  section: string;
  value: string;
  recognised: boolean;
  parameter: JobParameter | null;
}

export interface JobProblem {
  parameter: string;
  severity: "error" | "warning";
  message: string;
}

export interface JobConfiguration {
  source_name: string;
  checksum: string;
  calculation_mode: string;
  is_event_based: boolean;
  intensity_measures: string[];
  effective_time: number | null;
  unrecognised: string[];
  settings: JobSetting[];
  problems: JobProblem[];
  runnable: boolean;
  footprint_requirements: string[];
  editable: Record<string, string[]>;
}

export interface LogicTreeSummary {
  estimated_realizations: number;
  tectonic_regions?: string[];
  source_branch_sets?: { tectonic_region: string; branches: number }[];
  gsim_branch_sets?: { tectonic_region: string; branches: number }[];
  note: string;
}

export interface HazardModel {
  id: UUID;
  reference: string;
  country_code: string;
  version: string;
  label: string;
  source_organisation: string;
  publication_reference: string;
  licence: string;
  licence_cleared: boolean;
  licence_note: string;
  archive_checksum: string;
  archive_bytes: number;
  file_manifest: { path: string; size_bytes: number; checksum: string }[];
  published_calculation_mode: string;
  intensity_measures: string[];
  tectonic_regions: string[];
  estimated_realizations: number;
  logic_tree_summary: LogicTreeSummary;
  needs_conversion: boolean;
  needs_sampling: boolean;
  publication_state: string;
  notes: string;
  created_at: string;
}

export interface PackageInspection {
  job_path: string;
  files: { path: string; size_bytes: number; checksum: string }[];
  file_count: number;
  archive_checksum: string;
  archive_bytes: number;
  configuration: JobConfiguration;
  logic_trees: LogicTreeSummary;
}

export interface ConfiguredRun {
  overrides: Record<string, string | number>;
  configuration: JobConfiguration;
  conversion: {
    changes: { parameter: string; from: string; to: string }[];
    removed: string[];
    notes: string[];
  };
  site_join: Record<string, unknown>;
  /** How much of the grid the run computes, against the whole grid. */
  coverage?: {
    grid_cells?: number;
    onshore_cells?: number;
    cells_computed?: number;
    cells_skipped?: number;
    covered_share?: number;
    region?: Record<string, number> | null;
  };
  region?: Record<string, number> | null;
  problems: JobProblem[];
  runnable: boolean;
  job_checksum: string;
  rendered: string;
  /** How long the catalogue is, what its tail rests on, and what it would store. */
  catalogue?: {
    simulated_years: number;
    tail: { return_period: number; years_beyond: number }[];
    storage?: { hazard_set_mb: number; package_mb: number; basis: string };
  } | null;
}

export interface AreaPerilGridSummary {
  id: UUID;
  reference: string;
  country_code: string;
  version: string;
  label: string;
  cell_count: number;
  publication_state: string;
  /** True only where the grid was clipped to its country's land. */
  excludes_offshore?: boolean;
}

/**
 * Which of the tiles' cells a grid keeps. Both off keeps every cell of every
 * tile, as grids always did. Buffers are kilometres, typed as text.
 */
export interface GridDomain {
  /** Keep only cells touching the country's land, in Natural Earth's outline. */
  clip_to_land: boolean;
  coast_buffer_km: string;
  /** Keep only cells near anywhere a building stands or a resident lives. */
  skip_unsettled: boolean;
  settlement_buffer_km: string;
}

/** One named area of a grid specification, with the reason it is there. */
export interface GridArea {
  name: string;
  reason: string;
  min_latitude: string;
  max_latitude: string;
  min_longitude: string;
  max_longitude: string;
}

/** An area modelled more finely than the base resolution. */
export interface GridRefinement extends GridArea {
  resolution_deg: string;
}

/**
 * A written grid specification, as the build endpoint takes it. Every number is
 * a string: these are decimals a reviewer reads and compares, and a float would
 * change them on the way through the browser.
 */
export interface GridSpecificationInput {
  country_code: string;
  version: string;
  label: string;
  base_resolution_deg: string;
  mapping_tolerance_km: string;
  tiles: GridArea[];
  refinements: GridRefinement[];
  open_questions: string[];
  notes: string;
  domain: GridDomain;
}

/** One grid specification CASS ships, as the catalogue lists it. */
export interface GridSeedSummary {
  country_code: string;
  label: string;
  version: string;
  base_resolution_deg: string;
  tiles: number;
  refinements: number;
  domain: Partial<GridDomain>;
  cells: number | null;
}

export interface GridSeed {
  specification: GridSpecificationInput;
  measured: {
    cells: number;
    cells_by_refinement: Record<string, number>;
    removed_as_sea: number;
    removed_as_unsettled: number;
    uncovered_land_cells: number | null;
    builder_version: string;
  };
}

/** A country a grid can be clipped to: Natural Earth's outline for it. */
export interface GridCountry {
  code: string;
  name: string;
  parts: string[];
  bounds: Record<string, number>;
  seeded: boolean;
}

/** One end of a metric's range, and the scenario that sets it. */
export interface ScenarioRangeEnd {
  scenario: string;
  label: string;
  value: string;
}

/** How far a result moves across the assumption scenarios run for its book and model. */
export interface ScenarioRange {
  varies: string;
  not_varied: string[];
  currency: string;
  central: { scenario: string; label: string; result: UUID; is_baseline: boolean };
  scenarios: {
    scenario: string;
    label: string;
    result: UUID;
    created_at: string;
    average_annual_loss: string | null;
  }[];
  metrics: {
    metric: string;
    label: string;
    central: string | null;
    low: ScenarioRangeEnd;
    high: ScenarioRangeEnd;
    spread: string;
    relative_spread: string | null;
    most_influential?: {
      scenario: string;
      label: string;
      change: string;
      relative_change: string | null;
    };
  }[];
  influence: { scenario: string; label: string; largest_relative_change: string }[];
  /** Results for the same book and model that could not be shown to be calculated alike. */
  left_out: { calculated_differently: number; calculation_not_recorded: number };
  note?: string;
}

/** One area-peril cell's share of a result's average annual loss. */
export interface GeographicCell {
  area_peril_id: number;
  min_latitude: string;
  max_latitude: string;
  min_longitude: string;
  max_longitude: string;
  locations: number;
  average_annual_loss: string;
  tiv: string;
}

/** Where a result's loss is, placed by the run's keys. */
export interface GeographicSummary {
  perspective: string;
  grid: string;
  currency: string;
  basis: {
    average_loss: string;
    summary_level: number;
    grouped_by: string[];
    placed_by: string;
  };
  cells: GeographicCell[];
  locations_with_loss: number;
  unplaced_locations: number;
  unplaced_loss: string;
  location_total: string;
  portfolio_average_annual_loss: string | null;
  difference: string | null;
}

/** One Cohort B location, tried across the area its geocode stands for. */
export interface GeocodingLocation {
  location: string;
  precision: string;
  radius_km: string;
  recorded_cell: number | null;
  cells_reached: number[];
  points: number;
  points_in_recorded_cell: number;
  points_outside_grid: number;
  share_in_recorded_cell: number;
  stable: boolean;
  tiv: string;
}

/** Whether Cohort B's geocodes support the cells a grid gives them: WP3 step 4. */
export interface GeocodingSensitivity {
  version: string;
  grid: string;
  cohort: string;
  country: string;
  buffers_km: Record<string, string>;
  sampling: { rings: number; bearings: number };
  summary: {
    assessed: number;
    unassessed: number;
    stable: number;
    unstable: number;
    outside_grid_at_recorded_coordinate: number;
    buffer_reaches_outside_grid: number;
    most_cells_reached: number;
    mean_share_in_recorded_cell: number;
    tiv: string;
    stable_tiv: string;
    unstable_tiv: string;
    without_stated_value: number;
    by_precision: Record<
      string,
      { locations: number; stable: number; unstable: number; unstable_tiv: string }
    >;
  };
  locations: GeocodingLocation[];
  unassessed: { location: string; reason: string }[];
  other_countries: Record<string, number>;
  value_basis: string;
}

export interface VulnerabilitySetSummary {
  id: UUID;
  country_code: string;
  version: string;
  source: string;
  function_count: number;
  imts_used: string[];
  publication_state: string;
}

/** What a folder on the installation holds, read from the folder itself. */
export interface GemReleaseInspection {
  path: string;
  usable: boolean;
  release: string;
  matches_validated: boolean;
  validated_release: string;
  countries: number;
  repositories: {
    name: string;
    present: boolean;
    commit: string;
    tags: string[];
    matches_validated: boolean;
  }[];
  problems: string[];
  notes: string[];
}

/** The GEM release builds read from, where it came from, and what else is on the device. */
export interface GemReleaseStatus {
  source: "chosen" | "installation setting" | "none";
  path: string;
  chosen_at: string | null;
  current: GemReleaseInspection | null;
  mount: string;
  discovered: GemReleaseInspection[];
  validated_release: string;
}

/** One country a GEM release publishes vulnerability functions for. */
export interface GemCountry {
  region: string;
  country: string;
  loss_categories: string[];
  /** ISO 3166-1 alpha-3, as GEM's stock summary for the country states it. */
  iso3: string;
  /** ISO 3166-1 alpha-2, which CASS keys the vulnerability set by. */
  country_code: string;
  /** Why the country cannot be built, or empty where it can. */
  problem: string;
}

export interface GemCatalogue {
  release: string;
  countries: GemCountry[];
}

/** A period of construction and the seismic design levels it implies. */
export interface DesignEraInput {
  to_year: string;
  design_levels: string;
  reason: string;
}

/**
 * A written enrichment and where GEM publishes the country, as the build
 * endpoint takes them. The design eras are the assumption a reviewer argues
 * with, so each carries its reason. The country's codes are not sent: the API
 * reads them from the release for the country chosen.
 */
export interface VulnerabilitySpecificationInput {
  gem: { region: string; country: string };
  enrichment: {
    name: string;
    version: string;
    weighting: string;
    /** Why the set states no design eras. Empty where it states a table of them. */
    no_design_eras_reason: string;
    design_eras: { to_year: number | null; design_levels: string[]; reason: string }[];
    open_questions: string[];
    notes: string;
  };
}

export interface VulnerabilityBuildResult {
  vulnerability_set: { id: UUID; country_code: string; version: string };
  report: {
    classes: number;
    functions: number;
    vulnerability_version: string;
    multi_imt: { classes_needing_multi_imt: number };
  };
}

/**
 * What a specification would generate, answered while it is being written.
 *
 * The same count the build guards against, so a specification this calls within
 * the limit is one that builds. It is an upper bound wherever there are
 * refinements: a refined cell and the base cell it replaces are both counted.
 */
export interface GridEstimate {
  cells: number;
  /** True where the count is the build's own; false where it is a bound from the boxes. */
  exact: boolean;
  cells_from_tiles: number;
  cells_by_refinement: { name: string; resolution_deg: string; cells: number }[];
  /** Cells the tiles and refinements span before the domain removes any. */
  candidates: number;
  removed_as_sea: number;
  removed_as_unsettled: number;
  /** The country's land no tile or refinement covers, where the grid clips to land. */
  uncovered_land: {
    cells: number;
    examples: { cells: number; latitude: number; longitude: number }[];
  } | null;
  counted_tiles: number;
  /** Areas not yet complete enough to count, rather than wrong. */
  incomplete: number;
  /** What the build would refuse, said while it can still be changed. */
  problems: string[];
  limit: number;
  within_limit: boolean;
  is_upper_bound: boolean;
  /** False where there is not yet enough to count anything at all. */
  estimated: boolean;
  /** The most its hazard would store, per thousand simulated years. */
  storage: {
    hazard_set_mb_per_thousand_years: number;
    package_mb_per_thousand_years: number;
    basis: string;
  };
}

export interface GridBuildResult {
  grid: AreaPerilGridSummary;
  summary: {
    cells: number;
    cells_at_base_resolution: number;
    cells_by_refinement: Record<string, number>;
    removed_as_sea: number;
    removed_as_unsettled: number;
    builder_version: string;
    specification: Record<string, unknown>;
  };
}


// -- analysis configuration and execution ------------------------------------

export interface AnalysisRun {
  id: UUID;
  run: UUID;
  run_detail: Run;
  exposure_version: UUID;
  enrichment_run: UUID | null;
  /** The assumption set chosen; null means the model's baseline weights. */
  assumption_set?: UUID | null;
  model_version: UUID;
  /** What the run is for. Decides how far it goes and what it may claim. */
  mode: RunMode;
  perspectives: PerspectiveKey[];
  analysis_settings: Record<string, unknown>;
  run_currency: string;
  oasis_analysis_id: string;
  oasis_portfolio_id: string;
  /** Section 8: successful, not-at-risk and failed TIV against the source. */
  keys_summary: Record<string, unknown>;
  keys_reconciled: boolean | null;
  may_proceed_past_keys: boolean;
  exception_approval: UUID | null;
  created_at: string;
}

/** What a run consumed and produced, as the artifact store recorded it. */
export interface RunArtifact {
  /** Addresses the download endpoint; a URI alone cannot be retrieved. */
  id: UUID;
  role: string;
  direction: "input" | "output";
  uri: string;
  checksum: string;
  size_bytes: number;
  retention: string;
  /** Registered, pending, quarantined or expired: why an unreadable row is so. */
  state?: "pending" | "registered" | "quarantined" | "expired";
  /** Whether this viewer can open it now: entitled, and still present. */
  readable: boolean;
}

// -- operations --------------------------------------------------------------

export interface EngineStatus {
  engine: string;
  /** Null where no adapter exists yet: unknown is not the same as unreachable. */
  reachable: boolean | null;
  url: string;
  version?: string;
  adapter?: string;
  compatible: boolean | null;
  detail?: string;
}

export type AuditActionKey =
  | "create" | "update" | "delete" | "read" | "download" | "upload"
  | "publish" | "submit" | "cancel" | "retry" | "approve" | "reject"
  | "override" | "configure" | "sign_in" | "sign_in_failed";

export interface AuditEvent {
  id: UUID;
  actor_label: string;
  action: AuditActionKey;
  subject_type: string;
  subject_id: UUID | null;
  subject_label: string;
  project: UUID | null;
  before_reference: string;
  after_reference: string;
  correlation_id: string;
  source_ip: string;
  detail: string;
  created_at: string;
}

export interface Approval {
  id: UUID;
  gate: string;
  decision: "requested" | "approved" | "rejected" | "withdrawn";
  subject_type: string;
  subject_id: UUID | null;
  requested_by: UUID | null;
  requested_by_label: string;
  decided_by: UUID | null;
  decided_by_label: string;
  decided_at: string | null;
  rationale: string;
  evidence: Record<string, unknown>;
  is_open: boolean;
  is_cleared: boolean;
  created_at: string;
}

/** A configured hazard job, saved against the model it runs. */
export interface HazardJobSpec {
  id: UUID;
  model: UUID;
  grid: UUID;
  name: string;
  overrides: Record<string, string | number>;
  /** Bounds of the cells the run computes; empty means the whole grid. */
  region: Record<string, number>;
  resolved_configuration: JobConfiguration;
  conversion_report: Record<string, unknown>;
  site_join_report: Record<string, unknown>;
  problems: JobProblem[];
  blocking_problems: JobProblem[];
  is_runnable: boolean;
  job_checksum: string;
  created_at: string;
}


/**
 * A model version in the registry, at any stage of the section 10 path.
 *
 * Distinct from ``CatalogueModel``, which is the analyst's view and only ever
 * lists what may be selected. This one includes candidates that are not
 * published, because the model build workspace exists to work on those.
 */
export interface ModelVersion {
  id: UUID;
  country_code: string;
  peril: string;
  version: string;
  label: string;
  reference: string;
  grid: UUID;
  vulnerability_set: UUID;
  hazard_set: UUID | null;
  hazard_source_model: string;
  hazard_source_licence: string;
  openquake_version: string;
  oasis_version: string;
  converter_version: string;
  oed_schema_version: string;
  imts: string[];
  peril_scope: Record<string, unknown>;
  known_limitations: string;
  unsupported_taxonomy_report: Record<string, unknown>;
  is_research_prototype: boolean;
  publication_state: PublicationState;
  published_at: string | null;
  validation_date: string | null;
  usable_for_decisions: boolean;
  publication_blockers: string[];
  supersedes: UUID | null;
  is_frozen: boolean;
  created_at: string;
}


// -- the assumptions a promotion may run under -------------------------------

export interface AssumptionOption {
  value: string;
  label: string;
  description?: string;
  baseline?: boolean;
}

export interface CoverageSplitOption {
  name: string;
  description: string;
  percentages: Record<string, number>;
  /** Whether an approved prior stands behind this, rather than a test value. */
  approved?: boolean;
}

export interface OccupancyOption {
  name: string;
  description: string;
  occupancy_code: string;
  construction_code: string;
  approved?: boolean;
}

export interface AssumptionCatalogue {
  cohorts: AssumptionOption[];
  allocation_methods: AssumptionOption[];
  coverage_splits: CoverageSplitOption[];
  coverages: AssumptionOption[];
  occupancy_assumptions: OccupancyOption[];
  /** What a promotion does with the workbook's policy terms and reinsurance. */
  policy_terms?: { value: string; label: string; default: boolean }[];
  default_coverage_split: string;
  default_occupancy: string;
}

/**
 * What an import can write beyond its locations, counted per Policy ID because
 * a policy's terms are written for all of its rows or none.
 */
export interface ImportFinancialStructure {
  policy_ids: number;
  policy_ids_with_terms: number;
  policy_ids_without_terms: number;
  contracts: number;
  contract_layers: number;
  contract_layers_refused: number;
  contracts_without_scope: number[];
}

/** What a promotion wrote beyond the locations, and what it may overstate. */
export interface PromotedFinancialStructure {
  policy_terms: string;
  applied: boolean;
  reason: string;
  policy_ids_written: number;
  possibly_overstated: {
    policy_ids: string[];
    tiv: string;
    uncapped: string[];
    attachment_read_as_zero: string[];
    no_policy_row: string[];
  };
  policy_rows_written: number;
  locations_with_terms: number;
  contracts_written: number[];
  contract_layers_written: number;
  reinsurance_note: string;
}

/** What a promotion produced, as the exposure version records it. */
export interface PromotionSummary {
  exposure_version: UUID;
  name: string;
  version: number;
  location_count: number;
  total_tiv: string;
  financial_structure?: PromotedFinancialStructure;
  [key: string]: unknown;
}


// -- comparing two governed results ------------------------------------------

/**
 * One compared quantity.
 *
 * Every monetary field is a string, and every one of them was computed on the
 * server: ADR 5 keeps money arithmetic off the browser, so the interface reads
 * these and formats them, and never subtracts one from another.
 */
export interface ComparedMetric {
  metric: string;
  label: string;
  baseline: string | null;
  candidate: string | null;
  change: string | null;
  /** A ratio, or null where the baseline was zero and a proportion is meaningless. */
  relative_change: string | null;
  direction: "increase" | "decrease" | "unchanged" | "unknown";
}

export interface ComparedReturnPeriod extends Omit<ComparedMetric, "metric" | "label"> {
  return_period: string;
}

export interface ComparisonDriver {
  driver: string;
  label: string;
  baseline: string;
  candidate: string;
  note: string;
}

export interface ComparisonDifferences {
  perspective: string;
  currency: string;
  metrics: ComparedMetric[];
  return_periods: {
    shared: ComparedReturnPeriod[];
    only_in_baseline: string[];
    only_in_candidate: string[];
  };
  drivers: ComparisonDriver[];
  /** True where nothing either result records accounts for the change. */
  unexplained: boolean;
  unexplained_note: string;
  decision_use: {
    baseline: boolean;
    candidate: boolean;
    both_approved: boolean;
    warning: string;
  };
  /** Brief section 5.2: two modes are never compared without a warning. */
  run_modes: {
    baseline: RunMode | "";
    candidate: RunMode | "";
    mixed: boolean;
    warning: string;
  };
}

export interface ResultComparison {
  id: UUID;
  project: UUID;
  label: string;
  baseline: UUID;
  baseline_detail: ResultSet;
  candidate: UUID;
  candidate_detail: ResultSet;
  differences: ComparisonDifferences;
  commentary: string;
  is_like_for_like: boolean;
  created_at: string;
}

// -- allocation scenarios ----------------------------------------------------

export interface AllocationScenario {
  method: string;
  baseline: boolean;
  total_tiv: string;
  mapped_tiv: string;
  failed_tiv: string;
  reconciles: boolean;
  location_count: number;
  area_peril_count: number;
  tiv_by_area_peril: Record<string, string>;
  methods_used: string[];
}

export interface BusinessMateriality {
  business_id: string;
  location_count: number;
  area_peril_count: number;
  total_tiv: string;
  /** Whether moving value between this business's sites changes anything. */
  material: boolean;
  reason: string;
}

export interface AllocationScenarios {
  rule_version: string;
  allocation_rule_version: string;
  grid: string;
  vulnerability: string;
  /** Whether every scenario carries the same money, which makes it a sensitivity. */
  total_holds_across_scenarios: boolean;
  scenarios: AllocationScenario[];
  movement_from_baseline: Record<string, Record<string, string>>;
  materiality: {
    businesses: number;
    businesses_where_allocation_is_material: number;
    material_tiv: string;
    total_tiv: string;
    material_share: number;
    detail: BusinessMateriality[];
  };
  envelope: Record<
    string,
    { candidate_area_perils: number[]; count: number; total_tiv: string }
  >;
  interpretation: string;
}

/** The queued run a launched hazard configuration produces. */
export interface LaunchedHazardRun {
  run: UUID;
  hazard_run: UUID;
  state: RunState;
  job_checksum: string;
}

/** A registered event set and its footprints, with the calculation behind them. */
export interface HazardSet {
  id: UUID;
  reference: string;
  country_code: string;
  version: string;
  label: string;
  source_model: string;
  licence: string;
  licence_cleared: boolean;
  grid: UUID;
  engine_version: string;
  investigation_time: number;
  stochastic_event_sets: number;
  /** Logic-tree paths pooled into this catalogue (ADR 18). */
  logic_tree_paths: number;
  /** Years the catalogue covers: the three factors above multiplied. */
  effective_time: number;
  event_count: number;
  cell_count: number;
  footprint_row_count: number;
  imts: string[];
  samples_above_range: number;
  /** Whether OpenQuake's own copy was removed once CASS held the calculation. */
  openquake_calculation_removed?: boolean;
  /** Whether the footprint matches the current intensity bins, and whether it can be rebuilt. */
  rebuild?: {
    intensity_bins_current: boolean | null;
    datastore_available: boolean;
    datastore_expires_at: string | null;
    unavailable_reason: string;
    rebuilt_as: string | null;
    rebuilt_from: string | null;
  };
  publication_state: string;
  notes: string;
  created_at: string;
}

/** What one column of an OED file holds, and what it will accept. */
export interface RowColumn {
  name: string;
  label: string;
  help: string;
  required: boolean;
  type: string;
  allowed: string[] | null;
  minimum: number | null;
  maximum: number | null;
  is_tiv: boolean;
  is_financial_term: boolean;
}

export interface PortfolioRow {
  row_number: number;
  values: Record<string, string>;
}

/** A page of a portfolio's rows, with the schema the editor builds fields from. */
export interface RowPage {
  kind: string;
  columns: RowColumn[];
  rows: PortfolioRow[];
  count: number;
  total: number;
  editable: boolean;
  attached: string[];
}


// -- the financial structure workspace ---------------------------------------

/** One layer of one policy, as the account file states it. */
export interface StructureLayer {
  account: string;
  policy: string;
  layer_number: number | null;
  participation: string | null;
  limit: string | null;
  attachment: string | null;
  deductible: string | null;
  policy_limit: string | null;
  perils: string[];
  inception: string;
  expiry: string;
}

/** One reinsurance contract, with the scope it reaches. */
export interface StructureContract {
  number: number | null;
  layer_number: number | null;
  name: string;
  type: string;
  type_label: string;
  perils: string[];
  inuring_priority: number | null;
  ceded_percent: string | null;
  placed_percent: string | null;
  risk_limit: string | null;
  risk_attachment: string | null;
  occurrence_limit: string | null;
  occurrence_attachment: string | null;
  currency: string;
  scope_rows: number;
  scope_tiv: string;
  locations_reached: number;
  /** False where CASS reads and reconciles the contract but the engine will not apply it. */
  applied_by_the_engine: boolean;
  notes: string[];
}

export interface StructureFinding {
  code: string;
  subject: string;
  message: string;
  blocking: boolean;
}

/** One layer of a policy being written: amounts as decimal strings, a share as a proportion. */
export interface PolicyLayerInput {
  attachment: string;
  limit: string;
  participation: string;
}

/** A policy to write into a draft portfolio's account file. */
export interface PolicyInput {
  account: string;
  policy: string;
  perils: string;
  deductible?: string;
  policy_limit?: string;
  layers: PolicyLayerInput[];
}

/** One risk a contract's scope names; a surplus share also states the share ceded on it. */
export interface ContractScopeInput {
  account: string;
  policy?: string;
  location?: string;
  ceded_percent?: string;
}

/** A reinsurance contract to write into a draft portfolio. Only the engine's terms for the type are sent. */
export interface ContractInput {
  type: "QS" | "SS" | "CXL";
  name?: string;
  perils: string;
  inuring_priority?: number;
  ceded_percent?: string;
  placed_percent?: string;
  occurrence_attachment?: string;
  occurrence_limit?: string;
  risk_level?: "" | "LOC" | "POL" | "ACC";
  risk_limit?: string;
  risk_attachment?: string;
  /** Catastrophe excess of loss only; applied by limited cover, never by the engine. */
  reinstatements?: string;
  reinstatement_rate?: string;
  reinstatement_premium?: string;
  whole_portfolio?: boolean;
  scope?: ContractScopeInput[];
}

export interface FinancialStructureSummary {
  exposure_version: UUID;
  currency: string;
  total_tiv: string;
  location_count: number;
  layers: StructureLayer[];
  contracts: StructureContract[];
  inuring_order: { priority: number; contracts: number[] }[];
  uncovered_locations: number;
  uncovered_tiv: string;
  findings: StructureFinding[];
  has_accounts: boolean;
  has_contracts: boolean;
}

// -- the data standards registry ---------------------------------------------

/** One registered version of a data standard, as its owner published it. */
export interface DataStandardVersion {
  id: UUID;
  standard: string;
  version: string;
  state: "candidate" | "active" | "superseded";
  source: string;
  reference_uri: string;
  checksum: string;
  file_kinds: Record<string, number>;
  field_count: number;
  notes: string;
  adopted_at: string | null;
  is_active: boolean;
  /** Whether this is the version the validator actually implements. */
  matches_the_reader: boolean;
  created_at: string;
}

// -- the support bundle --------------------------------------------------------

/** What an operator sends when something is wrong: no secrets, no portfolio contents. */
export interface SupportBundle {
  generated_at: string;
  installation: {
    api_version: string;
    oed_schema_version: string;
    python: string;
    packages: Record<string, string>;
    migrations: Record<string, string>;
  };
  /** Whether each secret is set, never its value. */
  configured: Record<string, boolean>;
  engines: Record<string, unknown>;
  capacity: ProfileCapacity[];
  runs: { kind: string; state: string; count: number }[];
  /** Where failures happened, not what they said: a summary names the portfolio. */
  recent_failures: {
    run: UUID;
    kind: string;
    stage: string;
    profile: string;
    correlation_id: string;
    finished_at: string | null;
  }[];
  artifacts: { state: string; retention: string; count: number; bytes: number }[];
  retention: { due_now: number; kept_as_evidence: number; abandoned_uploads: number };
  results: { state: string; count: number }[];
  open_approvals: number;
}
