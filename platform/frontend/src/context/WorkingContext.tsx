/**
 * What the analyst is currently working on.
 *
 * Section 3 requires model version, portfolio, financial perspective and run
 * state to be continuously visible. Holding that selection in one place means
 * the context bar, the analysis builder and the results workspace all agree
 * about it, and a user does not re-select the same portfolio on every screen.
 *
 * The selection persists across reloads because losing it mid-task is the kind
 * of small friction that pushes people back to engine tooling. Only the ids
 * are stored: a name cached in a browser goes stale the moment somebody
 * renames a project, and a stale label beside a live number is worse than no
 * label. The records are resolved from the API on every load, so what the
 * context bar shows is what the platform currently holds.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { keys, useExposureVersion, useModelCatalogue, useProject } from "@/api/hooks";
import type { CatalogueModel, ExposureVersion, PerspectiveKey, Project } from "@/api/types";

const STORAGE_KEY = "cass.working-context.v1";

interface StoredSelection {
  projectId?: string;
  exposureId?: string;
  modelId?: string;
  perspective?: PerspectiveKey;
}

interface WorkingContextValue extends StoredSelection {
  project?: Project;
  exposure?: ExposureVersion;
  model?: CatalogueModel;
  perspective: PerspectiveKey;
  /** True while a stored selection is still being resolved into records. */
  isResolving: boolean;
  setProject: (project: Project | undefined) => void;
  setExposure: (exposure: ExposureVersion | undefined) => void;
  setModel: (model: CatalogueModel | undefined) => void;
  setPerspective: (perspective: PerspectiveKey) => void;
  clear: () => void;
}

const WorkingContext = createContext<WorkingContextValue | null>(null);

function readStored(): StoredSelection {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as StoredSelection) : {};
  } catch {
    // A private window or blocked storage must not break the application.
    return {};
  }
}

function writeStored(selection: StoredSelection): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(selection));
  } catch {
    // Persistence is a convenience, never a requirement.
  }
}

export function WorkingContextProvider({ children }: { children: React.ReactNode }) {
  const stored = useMemo(readStored, []);
  const client = useQueryClient();

  const [projectId, setProjectId] = useState(stored.projectId);
  const [exposureId, setExposureId] = useState(stored.exposureId);
  const [modelId, setModelId] = useState(stored.modelId);
  const [perspective, setPerspective] = useState<PerspectiveKey>(
    stored.perspective ?? "ground_up",
  );

  useEffect(() => {
    writeStored({ projectId, exposureId, modelId, perspective });
  }, [projectId, exposureId, modelId, perspective]);

  // The records behind the ids. Selecting one seeds the same cache entry
  // below, so choosing a project on the dashboard shows its name immediately
  // and a reload resolves the identical record from the API.
  const projectQuery = useProject(projectId);
  const exposureQuery = useExposureVersion(exposureId);
  const catalogue = useModelCatalogue(Boolean(modelId));

  const project = projectQuery.data;
  const exposure = exposureQuery.data;
  const model = useMemo(
    () => (modelId ? catalogue.data?.find((item) => item.id === modelId) : undefined),
    [catalogue.data, modelId],
  );

  // A selection that no longer resolves is dropped rather than left pointing
  // at nothing: a deleted project must not keep filtering every list on a
  // screen that cannot say which project it is filtering by.
  useEffect(() => {
    if (projectId && projectQuery.isError) setProjectId(undefined);
  }, [projectId, projectQuery.isError]);

  useEffect(() => {
    if (exposureId && exposureQuery.isError) setExposureId(undefined);
  }, [exposureId, exposureQuery.isError]);

  useEffect(() => {
    if (modelId && catalogue.isSuccess && !model) setModelId(undefined);
  }, [catalogue.isSuccess, model, modelId]);

  const setProject = useCallback(
    (next: Project | undefined) => {
      if (next) client.setQueryData(keys.project(next.id), next);
      setProjectId(next?.id);
      // A portfolio belongs to a project, so changing project drops the
      // selected exposure rather than leaving a mismatched pair on screen.
      setExposureId(undefined);
    },
    [client],
  );

  const setExposure = useCallback(
    (next: ExposureVersion | undefined) => {
      if (next) client.setQueryData(keys.exposure(next.id), next);
      setExposureId(next?.id);
      // A portfolio names the project it belongs to, so picking one with no
      // project selected adopts it rather than leaving the analysis builder
      // holding a portfolio whose run would have no owner. An explicit project
      // is never overridden: that pairing is the analyst's own.
      if (next?.project) setProjectId((current) => current ?? next.project);
    },
    [client],
  );

  const setModel = useCallback((next: CatalogueModel | undefined) => {
    setModelId(next?.id);
  }, []);

  const clear = useCallback(() => {
    setProjectId(undefined);
    setExposureId(undefined);
    setModelId(undefined);
    setPerspective("ground_up");
  }, []);

  const isResolving =
    (Boolean(projectId) && projectQuery.isLoading) ||
    (Boolean(exposureId) && exposureQuery.isLoading) ||
    (Boolean(modelId) && catalogue.isLoading);

  const value = useMemo<WorkingContextValue>(
    () => ({
      projectId,
      exposureId,
      modelId,
      project,
      exposure,
      model,
      perspective,
      isResolving,
      setProject,
      setExposure,
      setModel,
      setPerspective,
      clear,
    }),
    [
      projectId,
      exposureId,
      modelId,
      project,
      exposure,
      model,
      perspective,
      isResolving,
      setProject,
      setExposure,
      setModel,
      clear,
    ],
  );

  return <WorkingContext.Provider value={value}>{children}</WorkingContext.Provider>;
}

export function useWorkingContext(): WorkingContextValue {
  const value = useContext(WorkingContext);
  if (!value) {
    throw new Error("useWorkingContext must be used inside a WorkingContextProvider");
  }
  return value;
}
