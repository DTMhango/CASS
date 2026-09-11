/**
 * What the analyst is currently working on.
 *
 * Section 3 requires model version, portfolio, financial perspective and run
 * state to be continuously visible. Holding that selection in one place means
 * the context bar, the analysis builder and the results workspace all agree
 * about it, and a user does not re-select the same portfolio on every screen.
 *
 * The selection persists across reloads because losing it mid-task is the kind
 * of small friction that pushes people back to engine tooling.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import type { CatalogueModel, ExposureVersion, PerspectiveKey, Project } from "@/api/types";

const STORAGE_KEY = "kre.working-context.v1";

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

  const [project, setProjectState] = useState<Project | undefined>();
  const [exposure, setExposureState] = useState<ExposureVersion | undefined>();
  const [model, setModelState] = useState<CatalogueModel | undefined>();
  const [perspective, setPerspective] = useState<PerspectiveKey>(
    stored.perspective ?? "ground_up",
  );

  // Ids are what survive a reload; the full records are refetched by the
  // screens that need them.
  const [projectId, setProjectId] = useState(stored.projectId);
  const [exposureId, setExposureId] = useState(stored.exposureId);
  const [modelId, setModelId] = useState(stored.modelId);

  useEffect(() => {
    writeStored({ projectId, exposureId, modelId, perspective });
  }, [projectId, exposureId, modelId, perspective]);

  const setProject = useCallback((next: Project | undefined) => {
    setProjectState(next);
    setProjectId(next?.id);
    // A portfolio belongs to a project, so changing project drops the
    // selected exposure rather than leaving a mismatched pair on screen.
    setExposureState(undefined);
    setExposureId(undefined);
  }, []);

  const setExposure = useCallback((next: ExposureVersion | undefined) => {
    setExposureState(next);
    setExposureId(next?.id);
  }, []);

  const setModel = useCallback((next: CatalogueModel | undefined) => {
    setModelState(next);
    setModelId(next?.id);
  }, []);

  const clear = useCallback(() => {
    setProjectState(undefined);
    setExposureState(undefined);
    setModelState(undefined);
    setProjectId(undefined);
    setExposureId(undefined);
    setModelId(undefined);
    setPerspective("ground_up");
  }, []);

  const value = useMemo<WorkingContextValue>(
    () => ({
      projectId,
      exposureId,
      modelId,
      project,
      exposure,
      model,
      perspective,
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
