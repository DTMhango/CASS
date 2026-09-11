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
  type UseQueryOptions,
} from "@tanstack/react-query";

import { api, rows } from "./client";
import type {
  CatalogueModel,
  ExposurePreview,
  ExposureVersion,
  OEDFileKind,
  Paginated,
  PlatformInfo,
  Project,
  ResultSet,
  Run,
  RunStageEvent,
  Session,
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
  catalogue: ["model-versions", "catalogue"] as const,
  runs: (projectId?: UUID) => ["runs", projectId ?? "all"] as const,
  run: (id: UUID) => ["runs", id] as const,
  runEvents: (id: UUID) => ["runs", id, "events"] as const,
  results: (projectId?: UUID) => ["results", projectId ?? "all"] as const,
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
    onSuccess: () => {
      client.clear();
      client.setQueryData(keys.session, { authenticated: false });
    },
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

export function useCreateExposureVersion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      project: UUID;
      name: string;
      cedant?: string;
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

export function useModelCatalogue() {
  return useQuery({
    queryKey: keys.catalogue,
    queryFn: async () =>
      (await api.get<{ models: CatalogueModel[] }>("/model-versions/catalogue/")).models,
    staleTime: 60_000,
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
