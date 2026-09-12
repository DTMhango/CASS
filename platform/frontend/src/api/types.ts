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
  cedant: string;
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
  settings_hash: string;
  retry_of: UUID | null;
  may_retry: boolean;
  may_publish_results: boolean;
  is_active: boolean;
  created_at: string;
}

export interface RunStageEvent {
  id: UUID;
  stage: string;
  state: RunState;
  message: string;
  metrics: Record<string, unknown>;
  created_at: string;
}

export interface ResultCaveats {
  model_version: string;
  assumption_set: string;
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
