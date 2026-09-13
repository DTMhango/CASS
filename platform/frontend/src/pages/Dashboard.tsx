/**
 * The portfolio dashboard.
 *
 * Section 3: "Show current work and exceptions -- recent projects, run status,
 * failed checks, storage and model notices." Exceptions lead, because the
 * things that need a person are the reason to open this screen at all.
 */

import { useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "@/api/client";
import {
  useApprovals,
  useCreateProject,
  useExposureVersions,
  useModelCatalogue,
  usePortfolioImports,
  useProjects,
  useRuns,
  useSession,
} from "@/api/hooks";
import { RunStateBadge, StatusBadge } from "@/components/StatusBadge";
import {
  Button,
  Card,
  EmptyState,
  Field,
  MetricTile,
  Notice,
  PageHeader,
  Spinner,
  TextInput,
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
  const { data: imports } = usePortfolioImports();
  const { data: approvals } = useApprovals();
  const context = useWorkingContext();

  const activeRuns = runs?.filter((run) => run.is_active) ?? [];
  const failedRuns = runs?.filter((run) => run.state === "failed") ?? [];
  const blockedRuns = runs?.filter((run) => run.state === "blocked") ?? [];

  const exposuresNeedingWork =
    exposures?.filter((item) => item.validation_report?.validation?.blocking) ?? [];
  const researchModels = models?.filter((item) => item.is_research_prototype) ?? [];

  // An import that has been read but not accepted is waiting on a person, and
  // it is the cheapest thing on this page to clear: nothing downstream of it
  // can start.
  const unreviewedImports = imports?.filter((item) => item.state !== "accepted") ?? [];
  const openGates = approvals?.filter((item) => item.is_open) ?? [];

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
          value={formatCount(blockedRuns.length + openGates.length)}
          footnote={
            blockedRuns.length + openGates.length
              ? "A reviewer must clear a gate."
              : undefined
          }
        />
        <MetricTile
          label="Imports to review"
          value={formatCount(unreviewedImports.length)}
          footnote={
            unreviewedImports.length ? "Nothing downstream can start first." : undefined
          }
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

      {unreviewedImports.length > 0 ? (
        <Notice
          tone="info"
          title={`${unreviewedImports.length} import(s) waiting on a review`}
        >
          <ul className="dashboard__inline-list">
            {unreviewedImports.slice(0, 3).map((item) => (
              <li key={item.id}>
                <Link to="/exposure?tab=import-review">
                  {item.source_filename || "Imported spreadsheet"}
                </Link>
                {" — "}
                {formatCount(item.risk_row_count)} risk row(s) read, none promoted
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
          padded={false}
          actions={<NewProject />}
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
            <div className="dashboard__empty">
              <EmptyState
                title="No projects yet"
                description="A project is the workspace that owns exposure, runs and results. Create one to begin: nothing else on the platform can be scoped without it."
              />
            </div>
          )}
        </Card>

        <Card title="Recent runs" description="The last ten, newest first." padded={false}>
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

/**
 * Create a project.
 *
 * Every screen on the platform scopes by project, and until this existed a
 * fresh installation was a dead end: the dashboard explained what a project
 * was for and offered no way to make one, so the first portfolio could only be
 * loaded by somebody with database access.
 *
 * The reference is the short identifier that names the project's artifacts in
 * the store, which is why it is asked for rather than derived. A generated one
 * would be a second name for the same thing, and the store keeps whichever was
 * used at creation for the life of the project.
 */
function NewProject() {
  const create = useCreateProject();
  const context = useWorkingContext();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [reference, setReference] = useState("");
  const [purpose, setPurpose] = useState("");

  const error = create.error as ApiError | null;
  const ready = Boolean(name.trim() && reference.trim());

  if (!open) {
    return (
      <Button variant="secondary" size="sm" onClick={() => setOpen(true)}>
        New project
      </Button>
    );
  }

  async function submit() {
    if (!ready) return;
    try {
      const project = await create.mutateAsync({
        name: name.trim(),
        reference: reference.trim(),
        purpose: purpose.trim(),
      });
      // Selected immediately: somebody who just made a project is about to
      // work in it, and making them pick it from a list of one is friction.
      context.setProject(project);
      setOpen(false);
      setName("");
      setReference("");
      setPurpose("");
    } catch {
      // Rendered as a refusal below rather than thrown at the console.
    }
  }

  return (
    <div className="dashboard__new-project">
      {error ? (
        <Notice tone="error" title="The project was not created">
          {error.message}
        </Notice>
      ) : null}

      <Field label="Name" htmlFor="project-name">
        <TextInput
          id="project-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Indonesia facultative 2026"
        />
      </Field>

      <Field
        label="Reference"
        htmlFor="project-reference"
        hint="Short and stable: it names this project's artifacts in the store for the life of the project."
      >
        <TextInput
          id="project-reference"
          value={reference}
          onChange={(event) => setReference(event.target.value)}
          placeholder="idn-fac-2026"
        />
      </Field>

      <Field label="Purpose" htmlFor="project-purpose">
        <TextInput
          id="project-purpose"
          value={purpose}
          onChange={(event) => setPurpose(event.target.value)}
          placeholder="Pilot earthquake portfolio analysis"
        />
      </Field>

      <div className="dashboard__new-project-actions">
        <Button
          variant="primary"
          size="sm"
          disabled={!ready}
          busy={create.isPending}
          onClick={submit}
        >
          Create
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
