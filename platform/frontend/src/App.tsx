/**
 * Routing.
 *
 * Every product area of build plan section 3 has a route from the first
 * release, because section 13 is explicit that the interface does not wait
 * until phase six: each engine capability is exposed through a thin CASS
 * workflow and then expanded. An area whose engine work is still ahead shows
 * what it will do and what is outstanding, rather than being absent.
 */

import { Navigate, Route, Routes } from "react-router-dom";

import { useSession } from "@/api/hooks";
import { Spinner } from "@/components/primitives";
import { AppShell } from "@/layout/AppShell";
import { Administration } from "@/pages/Administration";
import { AnalysisBuilder } from "@/pages/AnalysisBuilder";
import { Dashboard } from "@/pages/Dashboard";
import { ExposureWorkspace } from "@/pages/ExposureWorkspace";
import { ImportReview } from "@/pages/ImportReview";
import { ModelBuild } from "@/pages/ModelBuild";
import { ModelCatalogue } from "@/pages/ModelCatalogue";
import { ResultsWorkspace } from "@/pages/ResultsWorkspace";
import { RunMonitor } from "@/pages/RunMonitor";
import { SignIn } from "@/pages/SignIn";

export function App() {
  const { data: session, isLoading } = useSession();

  if (isLoading) return <Spinner label="Checking your session" />;

  if (!session?.authenticated) {
    return (
      <Routes>
        <Route path="*" element={<SignIn />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Dashboard />} />
        <Route path="models" element={<ModelCatalogue />} />
        <Route path="exposure" element={<ExposureWorkspace />} />
        <Route path="exposure/:exposureId" element={<ExposureWorkspace />} />
        <Route path="import-review" element={<ImportReview />} />
        <Route path="analysis" element={<AnalysisBuilder />} />
        <Route path="runs" element={<RunMonitor />} />
        <Route path="runs/:runId" element={<RunMonitor />} />
        <Route path="results" element={<ResultsWorkspace />} />
        <Route path="model-build" element={<ModelBuild />} />
        <Route path="administration" element={<Administration />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
