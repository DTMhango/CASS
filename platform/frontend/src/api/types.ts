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
  progress: number;
  pipeline: PipelineStage[];
  execution_profile: string;
  correlation_id: string;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
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

export interface ResultSet {
  id: UUID;
  run: UUID;
  project: UUID;
  label: string;
  perspective: PerspectiveKey;
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

export interface PlatformInfo {
  api_version: string;
  oed_schema_version: string;
  compatibility_matrix: Record<string, string>[];
  execution_profiles: Record<
    string,
    { cpu: number; memory_gb: number; timeout_seconds: number; max_concurrent: number }
  >;
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

export interface ImportResults {
  batch: {
    id: UUID;
    filename: string;
    source_checksum: string;
    parser_version: string;
    cohort_rule_version: string;
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
  findings: Finding[];
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
}

export interface AreaPerilGridSummary {
  id: UUID;
  reference: string;
  country_code: string;
  version: string;
  label: string;
  cell_count: number;
  publication_state: string;
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
  default_coverage_split: string;
  default_occupancy: string;
}

/** What a promotion produced, as the exposure version records it. */
export interface PromotionSummary {
  exposure_version: UUID;
  name: string;
  version: number;
  location_count: number;
  total_tiv: string;
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
  event_count: number;
  cell_count: number;
  footprint_row_count: number;
  imts: string[];
  samples_above_range: number;
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

