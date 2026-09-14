/**
 * Data hooks over the CASS API.
 *
 * Long-running work is a background job, so the run queries poll while a run
 * is active and stop once it reaches a terminal state. Section 3 requires that
 * an analyst never has to keep the browser open for work to continue; polling
 * is how the interface catches up when they return, not how the work proceeds.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query";

import { ApiError, api, rows, saveBlob } from "./client";
import type {
  AllocationScenarios,
  AnalysisRun,
  Approval,
  AssumptionCatalogue,
  AreaPerilGridSummary,
  GemCatalogue,
  GridBuildResult,
  GridSpecificationInput,
  VulnerabilityBuildResult,
  GeocodingSensitivity,
  GeographicSummary,
  ScenarioRange,
  VulnerabilitySetSummary,
  VulnerabilitySpecificationInput,
  AuditEvent,
  CatalogueModel,
  DataStandardVersion,
  EngineStatus,
  EventLossPage,
  ExposurePreview,
  FinancialStructureSummary,
  ContractInput,
  PolicyInput,
  ConfiguredRun,
  ExposureVersion,
  HazardJobSpec,
  HazardModel,
  HazardSet,
  PortfolioRow,
  RowPage,
  ImportResults,
  JobParameter,
  LaunchedHazardRun,
  ModelVersion,
  OEDFileKind,
  PackageInspection,
  Paginated,
  PerspectiveKey,
  PortfolioImport,
  PlatformInfo,
  PromotionSummary,
  Project,
  ResultComparison,
  ResultSet,
  ReviewDecisionRecord,
  ReviewQueue,
  Run,
  RunArtifact,
  RunMode,
  RunStageEvent,
  Session,
  SupportBundle,
  User,
  UUID,
  ValidationSummary,
} from "./types";

/** Run states from which no further change can come. */
const TERMINAL: ReadonlySet<string> = new Set(["succeeded", "failed", "cancelled"]);

export const keys = {
  session: ["session"] as const,
  platform: ["platform"] as const,
  projects: ["projects"] as const,
  project: (id: UUID) => ["projects", id] as const,
  exposures: (projectId?: UUID) => ["exposure-versions", projectId ?? "all"] as const,
  exposure: (id: UUID) => ["exposure-versions", id] as const,
  exposurePreview: (id: UUID) => ["exposure-versions", id, "preview"] as const,
  exposureFindings: (id: UUID) => ["exposure-versions", id, "findings"] as const,
  financialStructure: (id: UUID) =>
    ["exposure-versions", id, "financial-structure"] as const,
  catalogue: ["model-versions", "catalogue"] as const,
  runs: (projectId?: UUID) => ["runs", projectId ?? "all"] as const,
  run: (id: UUID) => ["runs", id] as const,
  runEvents: (id: UUID) => ["runs", id, "events"] as const,
  runArtifacts: (id: UUID) => ["runs", id, "artifacts"] as const,
  analysisForRun: (id: UUID) => ["analysis-runs", "by-run", id] as const,
  results: (projectId?: UUID) => ["results", projectId ?? "all"] as const,
  eventLosses: (id: UUID, limit: number) => ["results", id, "events", limit] as const,
  engines: ["engines"] as const,
  dataStandards: ["data-standards"] as const,
  users: ["users"] as const,
  approvals: ["approvals"] as const,
};

// -- identity ---------------------------------------------------------------

export function useSession() {
  return useQuery({
    queryKey: keys.session,
    queryFn: () => api.get<Session>("/session/"),
    staleTime: 60_000,
    retry: false,
  });
}

export function useSignIn() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (credentials: { username: string; password: string }) =>
      api.post<Session>("/session/", credentials),
    onSuccess: (session) => {
      client.setQueryData(keys.session, session);
      client.invalidateQueries();
    },
  });
}

export function useSignOut() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<void>("/session/"),
    // A session the API has already refused is one nobody is signed in to, so
    // an unauthenticated failure ends the session here too rather than leaving
    // a person holding a button that cannot work.
    onSettled: (_data, error) => {
      if (error && !(error instanceof ApiError && error.isUnauthenticated)) return;
      endSession(client);
    },
  });
}

/**
 * Drop everything the signed-out person could see.
 *
 * Order matters. Emptying the cache detaches the mounted session observer from
 * it, so a value written afterwards reaches nothing: the API session ends, the
 * sign-in screen never arrives, and the button reads as broken. Stating the
 * ended session first is what moves the interface, and the removal that
 * follows takes the portfolios, runs and results with it so nothing of one
 * person's work is on screen when the next one signs in.
 */
function endSession(client: QueryClient): void {
  client.setQueryData(keys.session, { authenticated: false });
  client.removeQueries({
    predicate: (query) => query.queryKey[0] !== keys.session[0],
  });
}

export function usePlatformInfo() {
  return useQuery({
    queryKey: keys.platform,
    queryFn: () => api.get<PlatformInfo>("/platform/"),
    staleTime: 5 * 60_000,
  });
}

// -- projects ---------------------------------------------------------------

export function useProjects() {
  return useQuery({
    queryKey: keys.projects,
    queryFn: async () => rows(await api.get<Paginated<Project>>("/projects/")),
  });
}

export function useProject(id: UUID | undefined) {
  return useQuery({
    queryKey: keys.project(id ?? ""),
    queryFn: () => api.get<Project>(`/projects/${id}/`),
    enabled: Boolean(id),
  });
}

export function useCreateProject() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; reference: string; purpose?: string; team?: string }) =>
      api.post<Project>("/projects/", body),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.projects }),
  });
}

// -- exposure ---------------------------------------------------------------

export function useExposureVersions(projectId?: UUID) {
  return useQuery({
    queryKey: keys.exposures(projectId),
    queryFn: async () =>
      rows(
        await api.get<Paginated<ExposureVersion>>(
          "/exposure-versions/",
          projectId ? { project: projectId } : undefined,
        ),
      ),
  });
}

export function useExposureVersion(id: UUID | undefined) {
  return useQuery({
    queryKey: keys.exposure(id ?? ""),
    queryFn: () => api.get<ExposureVersion>(`/exposure-versions/${id}/`),
    enabled: Boolean(id),
  });
}

export function useExposurePreview(id: UUID | undefined, enabled = true) {
  return useQuery({
    queryKey: keys.exposurePreview(id ?? ""),
    queryFn: () => api.get<ExposurePreview>(`/exposure-versions/${id}/preview/`),
    enabled: Boolean(id) && enabled,
    retry: false,
  });
}

export function useExposureFindings(id: UUID | undefined) {
  return useQuery({
    queryKey: keys.exposureFindings(id ?? ""),
    queryFn: () => api.get<ValidationSummary>(`/exposure-versions/${id}/findings/`),
    enabled: Boolean(id),
  });
}

/**
 * The financial structure of one published portfolio.
 *
 * Read rather than assembled here: the structure is part of the exposure
 * version, and the version is immutable, so a correction is a new version
 * exactly as it is for a location.
 */
export function useFinancialStructure(id: UUID | undefined) {
  return useQuery({
    queryKey: keys.financialStructure(id ?? ""),
    queryFn: () =>
      api.get<FinancialStructureSummary>(`/exposure-versions/${id}/financial-structure/`),
    enabled: Boolean(id),
    retry: false,
  });
}

export function useCreateExposureVersion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      project: UUID;
      name: string;
      source_description?: string;
    }) => api.post<ExposureVersion>("/exposure-versions/", body),
    onSuccess: () => client.invalidateQueries({ queryKey: ["exposure-versions"] }),
  });
}

export function useUploadOedFile(exposureId: UUID) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, file }: { kind: OEDFileKind; file: File }) => {
      const form = new FormData();
      form.append("kind", kind);
      form.append("file", file);
      return api.upload<{ role: string; uri: string; checksum: string; size_bytes: number }>(
        `/exposure-versions/${exposureId}/files/`,
        form,
      );
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: keys.exposure(exposureId) });
      client.invalidateQueries({ queryKey: keys.exposurePreview(exposureId) });
    },
  });
}

export function useValidateExposure(exposureId: UUID) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post<ExposureVersion>(`/exposure-versions/${exposureId}/validate/`),
    onSuccess: (updated) => {
      client.setQueryData(keys.exposure(exposureId), updated);
      client.invalidateQueries({ queryKey: keys.exposureFindings(exposureId) });
      client.invalidateQueries({ queryKey: ["exposure-versions"] });
    },
  });
}

export function usePublishExposure(exposureId: UUID) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<ExposureVersion>(`/exposure-versions/${exposureId}/publish/`),
    onSuccess: (updated) => {
      client.setQueryData(keys.exposure(exposureId), updated);
      client.invalidateQueries({ queryKey: ["exposure-versions"] });
    },
  });
}

// -- model catalogue --------------------------------------------------------

/**
 * The model versions an analyst may select.
 *
 * ``enabled`` exists for the working context, which needs the catalogue only
 * to resolve a stored selection into a record. Fetching it unconditionally
 * there would make every screen in the application load the catalogue to
 * discover that no model is selected.
 */
export function useModelCatalogue(enabled = true) {
  return useQuery({
    queryKey: keys.catalogue,
    queryFn: async () =>
      (await api.get<{ models: CatalogueModel[] }>("/model-versions/catalogue/")).models,
    staleTime: 60_000,
    enabled,
  });
}

// -- runs -------------------------------------------------------------------

export function useRuns(projectId?: UUID, options?: Partial<UseQueryOptions<Run[]>>) {
  return useQuery({
    queryKey: keys.runs(projectId),
    queryFn: async () =>
      rows(await api.get<Paginated<Run>>("/runs/", projectId ? { project: projectId } : undefined)),
    // Refresh while anything is still moving, then leave the server alone.
    refetchInterval: (query) =>
      (query.state.data ?? []).some((run) => !TERMINAL.has(run.state)) ? 5_000 : false,
    ...options,
  });
}

export function useRun(id: UUID | undefined) {
  return useQuery({
    queryKey: keys.run(id ?? ""),
    queryFn: () => api.get<Run>(`/runs/${id}/`),
    enabled: Boolean(id),
    refetchInterval: (query) =>
      query.state.data && !TERMINAL.has(query.state.data.state) ? 3_000 : false,
  });
}

export function useRunEvents(id: UUID | undefined, active: boolean) {
  return useQuery({
    queryKey: keys.runEvents(id ?? ""),
    queryFn: () => api.get<RunStageEvent[]>(`/runs/${id}/events/`),
    enabled: Boolean(id),
    refetchInterval: active ? 3_000 : false,
  });
}

export function useCancelRun(id: UUID) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<Run>(`/runs/${id}/cancel/`),
    onSuccess: (updated) => {
      client.setQueryData(keys.run(id), updated);
      client.invalidateQueries({ queryKey: ["runs"] });
    },
  });
}

export function useRetryRun(id: UUID) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<Run>(`/runs/${id}/retry/`),
    onSuccess: () => client.invalidateQueries({ queryKey: ["runs"] }),
  });
}

/**
 * The data standards this installation has pinned.
 *
 * Read-only here: adopting a version is a decision with a validation exercise
 * behind it (section 8), not a control on a settings screen.
 */
export function useDataStandards() {
  return useQuery({
    queryKey: keys.dataStandards,
    queryFn: async () =>
      rows(await api.get<Paginated<DataStandardVersion>>("/data-standards/")),
    retry: false,
  });
}

// -- results ----------------------------------------------------------------

export function useResults(projectId?: UUID) {
  return useQuery({
    queryKey: keys.results(projectId),
    queryFn: async () =>
      rows(
        await api.get<Paginated<ResultSet>>(
          "/results/",
          projectId ? { project: projectId } : undefined,
        ),
      ),
  });
}

/**
 * The events behind one result, largest loss first.
 *
 * Read from the API a page at a time rather than held whole: a national event
 * set produces a row for every event that caused a loss, and a browser has no
 * business holding tens of thousands of them to show twenty-five.
 */
export function useEventLosses(resultId?: UUID, limit = 25) {
  return useQuery({
    queryKey: keys.eventLosses(resultId ?? "none", limit),
    enabled: Boolean(resultId),
    queryFn: async () =>
      api.get<EventLossPage>(`/results/${resultId}/event-losses/`, {
        limit: String(limit),
      }),
    // A result whose run predates the event table has none, and that is an
    // answer rather than a fault to retry.
    retry: false,
  });
}

// -- the import review, work package 2 ---------------------------------------

export function usePortfolioImports(projectId?: UUID) {
  return useQuery({
    queryKey: ["portfolio-imports", projectId ?? "all"] as const,
    queryFn: async () =>
      rows(
        await api.get<Paginated<PortfolioImport>>(
          "/portfolio-imports/",
          projectId ? { project: projectId } : undefined,
        ),
      ),
  });
}

export function useImportResults(id: UUID | undefined) {
  return useQuery({
    queryKey: ["portfolio-imports", id ?? "", "results"] as const,
    queryFn: () => api.get<ImportResults>(`/portfolio-imports/${id}/import-results/`),
    enabled: Boolean(id),
  });
}

export function useReviewQueue(id: UUID | undefined) {
  return useQuery({
    queryKey: ["portfolio-imports", id ?? "", "queue"] as const,
    queryFn: () => api.get<ReviewQueue>(`/portfolio-imports/${id}/review-queue/`),
    enabled: Boolean(id),
  });
}

/**
 * Record one review decision.
 *
 * Both queries are invalidated on success rather than patched, because a
 * decision changes more than the row it names: a cohort override moves a
 * location between the counts on the summary, and a storey count changes how
 * much of the book can be modelled at all.
 */
export function useRecordDecision(batchId: UUID) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      locationId: UUID;
      field: string;
      value: string | number | null;
      rationale: string;
    }) =>
      api.post<{ location: Record<string, unknown>; history: ReviewDecisionRecord[] }>(
        `/portfolio-imports/${batchId}/locations/${input.locationId}/decide/`,
        { field: input.field, value: input.value, rationale: input.rationale },
      ),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: ["portfolio-imports", batchId] }),
  });
}

// -- uploaded hazard models --------------------------------------------------

export function useHazardModels(countryCode?: string) {
  return useQuery({
    queryKey: ["hazard-models", countryCode ?? "all"] as const,
    queryFn: async () =>
      rows(
        await api.get<Paginated<HazardModel>>(
          "/hazard-models/",
          countryCode ? { country_code: countryCode } : undefined,
        ),
      ),
  });
}

export function useJobParameters() {
  return useQuery({
    queryKey: ["hazard-models", "parameters"] as const,
    queryFn: () => api.get<JobParameter[]>("/hazard-models/parameters/"),
    staleTime: Infinity,
  });
}

/** Read an archive and say what it is, storing nothing. */
export function useInspectPackage() {
  return useMutation({
    mutationFn: (file: File) => {
      const body = new FormData();
      body.append("archive", file);
      return api.upload<PackageInspection>("/hazard-models/inspect/", body);
    },
  });
}

export function useUploadHazardModel() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      file: File;
      country_code: string;
      version: string;
      label: string;
      source_organisation?: string;
      licence?: string;
    }) => {
      const body = new FormData();
      body.append("archive", input.file);
      body.append("country_code", input.country_code);
      body.append("version", input.version);
      body.append("label", input.label);
      if (input.source_organisation)
        body.append("source_organisation", input.source_organisation);
      if (input.licence) body.append("licence", input.licence);
      return api.upload<HazardModel>("/hazard-models/upload/", body);
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["hazard-models"] }),
  });
}

/**
 * Resolve a configuration without running it.
 *
 * Called on every edit rather than on submit, because the point of the screen
 * is that a national calculation's problems are visible while somebody is
 * still looking at it. Finding out after four hours is the expensive way.
 */
export function useConfigureRun(modelId: UUID) {
  return useMutation({
    mutationFn: (input: {
      grid: UUID;
      overrides: Record<string, unknown>;
      region?: Record<string, number> | null;
    }) => api.post<ConfiguredRun>(`/hazard-models/${modelId}/configure/`, input),
  });
}

export function useSaveRunSpec(modelId: UUID) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      grid: UUID;
      name: string;
      overrides: Record<string, unknown>;
      region?: Record<string, number> | null;
    }) => api.post(`/hazard-models/${modelId}/specs/`, input),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: ["hazard-models", modelId] }),
  });
}

/**
 * Build a country's grid from a specification.
 *
 * The cells are generated from what the specification says, so what is sent is
 * the specification rather than a cell file: the artefact a reviewer argues
 * with is the thing the platform keeps.
 */
export function useBuildGrid() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (specification: GridSpecificationInput) =>
      api.post<GridBuildResult>("/grids/build/", specification),
    onSuccess: () => client.invalidateQueries({ queryKey: ["grids"] }),
  });
}

export function useVulnerabilitySets(countryCode?: string) {
  return useQuery({
    queryKey: ["vulnerability-sets", countryCode ?? "all"] as const,
    queryFn: async () =>
      rows(
        await api.get<Paginated<VulnerabilitySetSummary>>(
          "/vulnerability-sets/",
          countryCode ? { country_code: countryCode } : undefined,
        ),
      ),
  });
}

/**
 * Pair a grid with a vulnerability set into a model version a run can name.
 *
 * The scope statement and both halves' limitations are written by the API, not
 * here: they follow from what was paired, and a form that asked for them would
 * be asking somebody to retype what the registry already knows.
 */
export function useAssembleModelVersion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      grid: UUID;
      vulnerability_set: UUID;
      version: string;
      label?: string;
    }) => api.post<ModelVersion>("/model-versions/assemble/", input),
    onSuccess: () => client.invalidateQueries({ queryKey: ["model-versions"] }),
  });
}

/** Which countries the GEM release on this installation covers. */
export function useGemCountries() {
  return useQuery({
    queryKey: ["gem-countries"] as const,
    queryFn: () => api.get<GemCatalogue>("/vulnerability-sets/gem-countries/"),
    retry: false,
  });
}

/**
 * Build a country's vulnerability set from GEM and a written enrichment.
 *
 * The functions and their weights are GEM's; the design eras are the
 * assumption somebody local has to state, which is why they are sent rather
 * than compiled in.
 */
export function useBuildVulnerabilitySet() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (specification: VulnerabilitySpecificationInput) =>
      api.post<VulnerabilityBuildResult>("/vulnerability-sets/build/", specification),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: ["vulnerability-sets"] }),
  });
}

export function useGrids(countryCode?: string) {
  return useQuery({
    queryKey: ["grids", countryCode ?? "all"] as const,
    queryFn: async () =>
      rows(
        await api.get<Paginated<AreaPerilGridSummary>>(
          "/grids/",
          countryCode ? { country_code: countryCode } : undefined,
        ),
      ),
  });
}


// -- configuring and submitting an analysis ----------------------------------

/**
 * Configure a run, without starting it.
 *
 * Two steps rather than one, because they are two decisions. Section 3 asks
 * the builder to show a validation summary before work is queued, and the API
 * refuses unpublished input at configuration time, so an analyst finds out
 * that a precondition is unmet while they are still in the builder.
 */
export function useCreateAnalysis() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      project: UUID;
      exposure_version: UUID;
      model_version: UUID;
      perspectives: PerspectiveKey[];
      label?: string;
      execution_profile?: string;
      assumption_set?: UUID;
      mode?: RunMode;
    }) => api.post<AnalysisRun>("/analysis-runs/", input),
    onSuccess: () => client.invalidateQueries({ queryKey: ["runs"] }),
  });
}

/** Queue a configured analysis. The response is the queued run, not a result. */
export function useSubmitAnalysis() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (analysisId: UUID) =>
      api.post<AnalysisRun>(`/analysis-runs/${analysisId}/submit/`),
    onSuccess: () => client.invalidateQueries({ queryKey: ["runs"] }),
  });
}

/**
 * The analysis behind a run the monitor is already showing.
 *
 * Only analysis runs have one, so a hazard or conversion run simply resolves
 * to nothing rather than the caller having to know which kinds to ask about.
 */
export function useAnalysisForRun(runId: UUID | undefined, kind?: string) {
  return useQuery({
    queryKey: keys.analysisForRun(runId ?? ""),
    queryFn: async () => {
      const page = await api.get<Paginated<AnalysisRun>>("/analysis-runs/", {
        run: runId,
      });
      return rows(page)[0] ?? null;
    },
    enabled: Boolean(runId) && kind === "analysis",
  });
}

/** What a run consumed and produced. */
export function useRunArtifacts(id: UUID | undefined) {
  return useQuery({
    queryKey: keys.runArtifacts(id ?? ""),
    queryFn: () => api.get<RunArtifact[]>(`/runs/${id}/artifacts/`),
    enabled: Boolean(id),
  });
}

// -- results -----------------------------------------------------------------

export function useApproveResult() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (resultId: UUID) => api.post<ResultSet>(`/results/${resultId}/approve/`),
    onSuccess: () => client.invalidateQueries({ queryKey: ["results"] }),
  });
}

/**
 * The auditable result package.
 *
 * Fetched on demand rather than held in a query, because an export is an act
 * a person takes at a moment, and the header it returns records that moment.
 */
export function useExportResult() {
  return useMutation({
    mutationFn: (resultId: UUID) =>
      api.get<{
        result: ResultSet;
        run_manifest: Record<string, unknown>;
        correlation_id: string;
        exported_at: string;
        exported_by: string;
        warning: string | null;
      }>(`/results/${resultId}/export/`),
  });
}

// -- the model catalogue -----------------------------------------------------

/**
 * Publish a model version.
 *
 * ``as_research_prototype`` is how a modeller accepts the blockers rather than
 * clearing them: section 6 forbids publishing an incomplete package as a full
 * country model, but a prototype that says so may still be run.
 */
export function usePublishModelVersion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: UUID; as_research_prototype?: boolean }) =>
      api.post(`/model-versions/${input.id}/publish/`, {
        as_research_prototype: Boolean(input.as_research_prototype),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["model-versions"] }),
  });
}

// -- operations --------------------------------------------------------------

/**
 * Live engine reachability and version compatibility.
 *
 * Kept off the platform metadata query on purpose: this one makes a network
 * call per engine, and an Oasis server that is down must not hold up a screen
 * that only wanted a version string. It polls slowly for the same reason.
 */
export function useEngineStatus() {
  return useQuery({
    queryKey: keys.engines,
    queryFn: async () =>
      (await api.get<{ engines: Record<string, EngineStatus> }>("/engines/")).engines,
    refetchInterval: 60_000,
    retry: false,
  });
}

export function useUsers() {
  return useQuery({
    queryKey: keys.users,
    queryFn: async () => rows(await api.get<Paginated<User>>("/users/")),
    staleTime: 5 * 60_000,
  });
}

/**
 * Change what somebody may do: their platform role, job title, local-install
 * approval, or whether they are active.
 *
 * The API refuses the changes that would lock the installation out -- an
 * administrator removing their own administration, or the last active
 * administrator going -- and the refusal is left on the mutation for the
 * screen to show, rather than predicted here.
 */
export function useUpdateUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      changes,
    }: {
      id: UUID;
      changes: Partial<
        Pick<User, "platform_role" | "job_title" | "local_install_approved"> & {
          is_active: boolean;
        }
      >;
    }) => api.patch<User>(`/users/${id}/`, changes),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.users }),
  });
}

/**
 * The support bundle, for an administrator.
 *
 * Fetched only when asked for: it probes the engines, which is slow when one of
 * them is the reason the bundle is wanted.
 */
export function useSupportBundle(enabled: boolean) {
  return useQuery({
    queryKey: ["support-bundle"] as const,
    queryFn: () => api.get<SupportBundle>("/support-bundle/"),
    enabled,
    retry: false,
    staleTime: 60_000,
  });
}

/**
 * Take the support bundle away as a file.
 *
 * A separate request from the one that shows the numbers, because the API
 * audits looking and downloading as different acts.
 */
export function useDownloadSupportBundle() {
  return useMutation({
    mutationFn: async () => {
      const bundle = await api.get<SupportBundle>("/support-bundle/", { download: true });
      const stamp = bundle.generated_at.replace(/[:.]/g, "-");
      saveBlob(
        new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" }),
        `cass-support-bundle-${stamp}.json`,
      );
      return bundle;
    },
  });
}

/** Audit search. Every filter the API offers is a column an operator asked about. */
export function useAuditEvents(filters: {
  action?: string;
  subject_type?: string;
  project?: UUID;
  correlation_id?: string;
}) {
  return useQuery({
    queryKey: ["audit-events", filters] as const,
    queryFn: async () =>
      rows(
        await api.get<Paginated<AuditEvent>>("/audit-events/", {
          action: filters.action,
          subject_type: filters.subject_type,
          project: filters.project,
          correlation_id: filters.correlation_id,
        }),
      ),
  });
}

// -- governance gates --------------------------------------------------------

export function useApprovals() {
  return useQuery({
    queryKey: keys.approvals,
    queryFn: async () => rows(await api.get<Paginated<Approval>>("/approvals/")),
  });
}

/**
 * Grant or refuse a gate.
 *
 * The rationale is sent with the decision rather than after it, because the
 * API refuses a decision from the person who requested it and an auditor
 * reading a cleared gate needs to see why on the same record.
 */
export function useDecideApproval() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      id: UUID;
      decision: "approved" | "rejected";
      rationale: string;
    }) =>
      api.post<Approval>(`/approvals/${input.id}/decide/`, {
        decision: input.decision,
        rationale: input.rationale,
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: keys.approvals });
      client.invalidateQueries({ queryKey: ["runs"] });
    },
  });
}

// -- gates a run reaches -----------------------------------------------------

/** The exceptions asked for on one analysis run, newest first. */
export function useRunExceptions(analysisId: UUID | undefined) {
  return useQuery({
    queryKey: [...keys.approvals, "run-exception", analysisId ?? ""],
    queryFn: async () =>
      rows(
        await api.get<Paginated<Approval>>("/approvals/", {
          gate: "run_exception",
          subject_id: analysisId,
        }),
      ),
    enabled: Boolean(analysisId),
  });
}

/**
 * Ask a reviewer to let a run held at a gate continue.
 *
 * Asked on the run rather than through the approvals endpoint: the person whose
 * analysis is waiting is usually an analyst, and requesting a model gate is a
 * modeller's act.
 */
export function useRequestRunException(analysisId: UUID) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (rationale: string) =>
      api.post<Approval>(`/analysis-runs/${analysisId}/request-exception/`, { rationale }),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.approvals }),
  });
}

/** Put a run whose gate has been cleared back in the queue. */
export function useResumeAnalysis(analysisId: UUID) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<AnalysisRun>(`/analysis-runs/${analysisId}/resume/`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["runs"] });
      client.invalidateQueries({ queryKey: keys.approvals });
    },
  });
}

// -- saved hazard job specifications -----------------------------------------

export function useHazardSpecs(modelId: UUID | undefined) {
  return useQuery({
    queryKey: ["hazard-models", modelId ?? "", "specs"] as const,
    queryFn: () => api.get<HazardJobSpec[]>(`/hazard-models/${modelId}/specs/`),
    enabled: Boolean(modelId),
  });
}


// -- the model registry ------------------------------------------------------

/**
 * Every model version, not only the ones an analyst may select.
 *
 * The catalogue answers "what may I run"; this answers "what are we building",
 * which is the model build workspace's question and includes candidates that
 * have not been published and must not be.
 */
export function useModelVersions() {
  return useQuery({
    queryKey: ["model-versions", "registry"] as const,
    queryFn: async () => rows(await api.get<Paginated<ModelVersion>>("/model-versions/")),
  });
}


// -- the intake path: template in, exposure version out ----------------------

/**
 * Download a blank CASS intake template.
 *
 * Generated by the API from the intake profile rather than kept as a file, so
 * the workbook somebody fills in can never describe a mapping the platform
 * does not implement.
 */
export function useIntakeTemplate() {
  return useMutation({
    mutationFn: async (projectId: UUID | undefined) => {
      const { blob, filename } = await api.download(
        "/portfolio-imports/template/",
        projectId ? { project: projectId } : undefined,
      );
      saveBlob(blob, filename);
    },
  });
}

/** Register, read and profile one completed intake template. */
export function useImportWorkbook() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { project: UUID; file: File; snapshot_date?: string }) => {
      const body = new FormData();
      body.append("project", input.project);
      body.append("file", input.file);
      if (input.snapshot_date) body.append("snapshot_date", input.snapshot_date);
      return api.upload<PortfolioImport>("/portfolio-imports/upload/", body);
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["portfolio-imports"] }),
  });
}

/** Record that the join report and cohorts have been reviewed. */
export function useAcceptImport() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (batchId: UUID) =>
      api.post<PortfolioImport>(`/portfolio-imports/${batchId}/accept/`),
    onSuccess: () => client.invalidateQueries({ queryKey: ["portfolio-imports"] }),
  });
}

/**
 * The assumptions a promotion may be run under.
 *
 * Served as data rather than hard-coded here, so the interface offers exactly
 * what the platform supports and a new scenario needs no frontend release.
 */
export function useAssumptionCatalogue() {
  return useQuery({
    queryKey: ["assumptions"] as const,
    queryFn: () => api.get<AssumptionCatalogue>("/assumptions/"),
    staleTime: Infinity,
  });
}

/**
 * Turn a reviewed cohort selection into a published OED exposure version.
 *
 * Every assumption the caller chose is recorded on the version that results,
 * so a later reader can see what produced it and run it again differently.
 */
export function usePromoteImport(batchId: UUID) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      name: string;
      cohort: string;
      allocation_method: string;
      coverage_split?: string;
      occupancy?: string;
      country?: string;
    }) => api.post<PromotionSummary>(`/portfolio-imports/${batchId}/promote/`, input),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["exposure-versions"] });
      client.invalidateQueries({ queryKey: ["portfolio-imports"] });
    },
  });
}


// -- comparing two governed results ------------------------------------------

export function useComparisons(projectId?: UUID) {
  return useQuery({
    queryKey: ["comparisons", projectId ?? "all"] as const,
    queryFn: async () =>
      rows(
        await api.get<Paginated<ResultComparison>>(
          "/comparisons/",
          projectId ? { project: projectId } : undefined,
        ),
      ),
  });
}

/**
 * Save a comparison of two results.
 *
 * The differences are not sent. They are computed on the server from the two
 * results named here, because ADR 5 keeps money arithmetic where it is decimal
 * and audited -- a difference worked out in a browser would be a number nobody
 * could reproduce.
 */
export function useCreateComparison() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      project: UUID;
      label: string;
      baseline: UUID;
      candidate: UUID;
      commentary?: string;
    }) => api.post<ResultComparison>("/comparisons/", input),
    onSuccess: () => client.invalidateQueries({ queryKey: ["comparisons"] }),
  });
}

// -- allocation scenarios ----------------------------------------------------

/**
 * Whether the allocation assumption is economically live for this import.
 *
 * Needs a model version because it needs that model's grid: the question is
 * not how value divides, which the allocation engine answers exactly, but
 * whether the division moves value between cells the hazard can tell apart.
 * Without a grid there is nothing to answer it against, so the query stays
 * disabled until one is chosen rather than guessing at a default.
 */
/**
 * How far a result moves across the assumption scenarios run for its book and model.
 *
 * Every value, spread and ranking is the server's; the screen formats them.
 */
export function useScenarioRange(resultId: UUID | undefined) {
  return useQuery({
    queryKey: ["results", resultId ?? "", "scenario-range"] as const,
    queryFn: () => api.get<ScenarioRange>(`/results/${resultId}/scenario-range/`),
    enabled: Boolean(resultId),
    retry: false,
  });
}

/**
 * Where a result's loss is, by area-peril cell.
 *
 * Not retried: a result from a run that predates the location summary has
 * none, and the 404 says so rather than being a failure worth repeating.
 */
export function useGeographicSummary(resultId: UUID | undefined) {
  return useQuery({
    queryKey: ["results", resultId ?? "", "geographic"] as const,
    queryFn: () => api.get<GeographicSummary>(`/results/${resultId}/geographic/`),
    enabled: Boolean(resultId),
    retry: false,
  });
}

/**
 * Whether each Cohort B geocode supports the cell a model version's grid gives it.
 *
 * Off until a model version is chosen, for the allocation comparison's reason:
 * the answer depends entirely on how fine the cells are.
 */
export function useGeocodingSensitivity(
  batchId: UUID | undefined,
  modelVersionId: UUID | undefined,
) {
  return useQuery({
    queryKey: [
      "portfolio-imports",
      batchId ?? "",
      "geocoding-sensitivity",
      modelVersionId ?? "",
    ] as const,
    queryFn: () =>
      api.get<GeocodingSensitivity>(
        `/portfolio-imports/${batchId}/geocoding-sensitivity/`,
        { model_version: modelVersionId },
      ),
    enabled: Boolean(batchId) && Boolean(modelVersionId),
    retry: false,
  });
}

export function useAllocationScenarios(
  batchId: UUID | undefined,
  modelVersionId: UUID | undefined,
  options?: { cohort?: string; country?: string },
) {
  return useQuery({
    queryKey: [
      "portfolio-imports",
      batchId ?? "",
      "scenarios",
      modelVersionId ?? "",
      options,
    ] as const,
    queryFn: () =>
      api.get<AllocationScenarios>(
        `/portfolio-imports/${batchId}/allocation-scenarios/`,
        {
          model_version: modelVersionId,
          cohort: options?.cohort,
          country: options?.country,
        },
      ),
    enabled: Boolean(batchId) && Boolean(modelVersionId),
    retry: false,
  });
}

/**
 * Run a saved hazard configuration.
 *
 * The response is the queued run, not the hazard. A national calculation is
 * hours, so the modeller follows it on the run monitor rather than holding the
 * browser open -- which is the same rule the analysis builder follows.
 */
export function useLaunchHazardRun(modelId: UUID) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (specId: UUID) =>
      api.post<LaunchedHazardRun>(
        `/hazard-models/${modelId}/specs/${specId}/launch/`,
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["runs"] });
      client.invalidateQueries({ queryKey: ["hazard-models", modelId, "specs"] });
    },
  });
}

/**
 * Retrieve one artifact.
 *
 * Streamed through the API rather than linked directly, because section 10
 * requires per-project authorization on retrieval and records the access. A
 * plain anchor to the store would bypass both.
 */
export function useDownloadArtifact() {
  return useMutation({
    mutationFn: async (input: { id: UUID; filename?: string }) => {
      const { blob, filename } = await api.download(`/artifacts/${input.id}/download/`);
      saveBlob(blob, input.filename || filename);
    },
  });
}

// -- the Oasis model package -------------------------------------------------

export function useHazardSets() {
  return useQuery({
    queryKey: ["hazard-sets"] as const,
    queryFn: async () => rows(await api.get<Paginated<HazardSet>>("/hazard-sets/")),
  });
}

/** Point a model version at a hazard set; refused unless the grid and measures agree. */
export function useAttachHazardSet() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { modelVersion: UUID; hazardSet: UUID }) =>
      api.post<ModelVersion>(`/model-versions/${input.modelVersion}/attach-hazard/`, {
        hazard_set: input.hazardSet,
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["model-versions"] }),
  });
}

/**
 * Ask for a governance gate.
 *
 * Requested by the person whose work it governs and decided by somebody else,
 * which is the independence the gate exists for.
 */
export function useRequestApproval() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      gate: string;
      subject_type: string;
      subject_id: UUID;
      rationale: string;
    }) => api.post<Approval>("/approvals/", input),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.approvals }),
  });
}

/** Convert a model version into an Oasis package under an approved policy. */
export function useBuildPackage() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { modelVersion: UUID; approval: UUID }) =>
      api.post<{ run: UUID; conversion_run: UUID; state: string }>(
        `/model-versions/${input.modelVersion}/build-package/`,
        { approval: input.approval },
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["runs"] }),
  });
}

// -- reading and correcting a portfolio on the platform ----------------------

export function usePortfolioRows(
  id: UUID | undefined,
  kind: string,
  page: { offset: number; limit: number; search: string },
) {
  return useQuery({
    queryKey: ["exposure-rows", id ?? "", kind, page.offset, page.limit, page.search] as const,
    queryFn: () =>
      api.get<RowPage>(`/exposure-versions/${id}/rows/`, {
        kind,
        offset: String(page.offset),
        limit: String(page.limit),
        ...(page.search ? { search: page.search } : {}),
      }),
    enabled: Boolean(id),
  });
}

/**
 * Correct one row.
 *
 * The server checks the value against the same schema the file is validated
 * with and refuses it field by field, so a refusal arrives as something to fix
 * in the form rather than as a finding to chase later.
 */
export function useEditRow(id: UUID | undefined) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { kind: string; row_number: number; values: Record<string, string> }) =>
      api.post<{ row: PortfolioRow; version: ExposureVersion }>(
        `/exposure-versions/${id}/rows/edit/`,
        input,
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["exposure-rows"] });
      client.invalidateQueries({ queryKey: keys.exposure(id ?? "") });
      client.invalidateQueries({ queryKey: ["exposure-versions"] });
    },
  });
}

export function useRemoveRow(id: UUID | undefined) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { kind: string; row_number: number }) =>
      api.post<ExposureVersion>(`/exposure-versions/${id}/rows/remove/`, input),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["exposure-rows"] });
      client.invalidateQueries({ queryKey: keys.exposure(id ?? "") });
    },
  });
}

/**
 * Change a draft portfolio's financial structure.
 *
 * Every change comes back as the structure read from the files just written,
 * so the screen shows what the portfolio now says rather than what was sent.
 */
function useStructureChange<Body>(id: UUID | undefined, path: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: Body) =>
      api.post<FinancialStructureSummary>(`/exposure-versions/${id}/structure/${path}/`, body),
    onSuccess: (structure) => {
      if (id) client.setQueryData(keys.financialStructure(id), structure);
      client.invalidateQueries({ queryKey: ["exposure-versions"] });
    },
  });
}

export function useAddPolicy(id: UUID | undefined) {
  return useStructureChange<PolicyInput>(id, "policies");
}

export function useRemovePolicy(id: UUID | undefined) {
  return useStructureChange<{ account: string; policy: string }>(id, "policies/remove");
}

export function useAddContract(id: UUID | undefined) {
  return useStructureChange<ContractInput>(id, "contracts");
}

export function useRemoveContract(id: UUID | undefined) {
  return useStructureChange<{ number: number }>(id, "contracts/remove");
}

/** Copy a published portfolio into the next version, so it can be corrected. */
export function useCorrectExposure(id: UUID | undefined) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<ExposureVersion>(`/exposure-versions/${id}/correct/`, {}),
    onSuccess: () => client.invalidateQueries({ queryKey: ["exposure-versions"] }),
  });
}

