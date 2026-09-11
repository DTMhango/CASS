/**
 * The portfolio dashboard.
 *
 * Section 3: "Show current work and exceptions -- recent projects, run status,
 * failed checks, storage and model notices." Exceptions lead, because the
 * things that need a person are the reason to open this screen at all.
 */

import { Link } from "react-router-dom";

import {
  useExposureVersions,
  useModelCatalogue,
  useProjects,
  useRuns,
  useSession,
} from "@/api/hooks";
import { RunStateBadge, StatusBadge } from "@/components/StatusBadge";
import {
  Card,
  EmptyState,
  MetricTile,
  Notice,
  PageHeader,
  Spinner,
} from "@/components/primitives";
import { useWorkingContext } from "@/context/WorkingContext";
import { formatCount, formatDateTime } from "@/lib/format";

import "./Dashboard.css";

export function Dashboard() {
  const { data: session } = useSession();
  const { data: projects, isLoading: projectsLoading } = useProjects();
  const { data: runs } = useRuns();
  const { data: exposures } = useExposureVersions();
  const { data: models } = useModelCatalogue();
  const context = useWorkingContext();

  const activeRuns = runs?.filter((run) => run.is_active) ?? [];
  const failedRuns = runs?.filter((run) => run.state === "failed") ?? [];
  const blockedRuns = runs?.filter((run) => run.state === "blocked") ?? [];

  const exposuresNeedingWork =
    exposures?.filter((item) => item.validation_report?.validation?.blocking) ?? [];
  const researchModels = models?.filter((item) => item.is_research_prototype) ?? [];

  const firstName = session?.user?.first_name;

  return (
    <>
      <PageHeader
        title={firstName ? `Good day, ${firstName}` : "Portfolio dashboard"}
        description="Work in progress, anything waiting on a person, and notices about the models available to you."
      />

      <div className="dashboard__metrics">
        <MetricTile label="Projects" value={formatCount(projects?.length ?? 0)} />
        <MetricTile label="Runs in progress" value={formatCount(activeRuns.length)} />
        <MetricTile
          label="Awaiting approval"
          value={formatCount(blockedRuns.length)}
          footnote={blockedRuns.length ? "A reviewer must clear a gate." : undefined}
        />
        <MetricTile
          label="Portfolios to resolve"
          value={formatCount(exposuresNeedingWork.length)}
          footnote={
            exposuresNeedingWork.length ? "Blocking findings stop publication." : undefined
          }
        />
      </div>

      {failedRuns.length > 0 ? (
        <Notice tone="error" title={`${failedRuns.length} run(s) failed`}>
          <ul className="dashboard__inline-list">
            {failedRuns.slice(0, 3).map((run) => (
              <li key={run.id}>
                <Link to={`/runs/${run.id}`}>{run.label || run.kind}</Link>
                {run.failure_summary ? ` — ${run.failure_summary}` : null}
              </li>
            ))}
          </ul>
        </Notice>
      ) : null}

      {researchModels.length > 0 ? (
        <Notice
          tone="warning"
          title="Some available model versions are research prototypes"
        >
          {researchModels.map((model) => model.reference).join(", ")}. These may be run, but
          their output must not be used for decisions.
        </Notice>
      ) : null}

      <div className="dashboard__grid">
        <Card
          title="Projects"
          description="Select one to scope the exposure, run and result screens."
        >
          {projectsLoading ? (
            <Spinner label="Loading projects" />
          ) : projects && projects.length > 0 ? (
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Project</th>
                  <th scope="col">Role</th>
                  <th scope="col" className="numeric">
                    Portfolios
                  </th>
                  <th scope="col" className="numeric">
                    Active runs
                  </th>
                </tr>
              </thead>
              <tbody>
                {projects.map((project) => (
                  <tr key={project.id}>
                    <th scope="row">
                      <button
                        type="button"
                        className="dashboard__link-button"
                        onClick={() => context.setProject(project)}
                      >
                        {project.name}
                      </button>
                      <span className="mono muted"> {project.reference}</span>
                    </th>
                    <td>{project.my_role ?? "—"}</td>
                    <td className="numeric">{formatCount(project.exposure_version_count)}</td>
                    <td className="numeric">{formatCount(project.active_run_count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <EmptyState
              title="No projects yet"
              description="A project is the workspace that owns exposure, runs and results."
            />
          )}
        </Card>

        <Card title="Recent runs" description="The last ten, newest first.">
          {runs && runs.length > 0 ? (
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Run</th>
                  <th scope="col">State</th>
                  <th scope="col">Stage</th>
                  <th scope="col">Started</th>
                </tr>
              </thead>
              <tbody>
                {runs.slice(0, 10).map((run) => (
                  <tr key={run.id}>
                    <th scope="row">
                      <Link to={`/runs/${run.id}`}>{run.label || run.kind}</Link>
                    </th>
                    <td>
                      <RunStateBadge state={run.state} size="sm" />
                    </td>
                    <td className="muted">{run.stage_label || "—"}</td>
                    <td className="muted">{formatDateTime(run.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <EmptyState
              title="No runs yet"
              description="Publish an exposure version and select a model to submit the first analysis."
            />
          )}
        </Card>
      </div>

      {exposuresNeedingWork.length > 0 ? (
        <Card
          title="Portfolios with blocking findings"
          description="These cannot be published until the source records are corrected."
        >
          <ul className="dashboard__exception-list">
            {exposuresNeedingWork.map((item) => (
              <li key={item.id}>
                <Link to="/exposure">
                  {item.name} v{item.version}
                </Link>
                <StatusBadge tone="error" size="sm">
                  {item.validation_report?.validation?.error_count ?? 0} to resolve
                </StatusBadge>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </>
  );
}
