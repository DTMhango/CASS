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
