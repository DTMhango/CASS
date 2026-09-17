/**
 * The exposure workspace.
 *
 * Build plan section 3 gives this screen its job: create and prepare model
 * inputs, with OED preview, field completeness, reported versus inferred
 * attributes, TIV summaries and unmapped records. Section 8 gives it its
 * gates: validate, then preview, then publish an immutable version.
 *
 * Two rules are visible in the interface rather than implied. Findings are
 * shown with the remediation the API returned and the business record they
 * belong to, so a user can act on them. And a perspective the source data does
 * not support is shown as unavailable with its reason, rather than being
 * offered and silently producing zero.
 *
 * There are two ways in, because there are two kinds of source. A cedant who
 * already works in OED attaches the four files directly. Everybody else fills
 * in the CASS intake template, and that arrives as an import to be reviewed
 * before any of it becomes a portfolio. The second path starts here and
 * finishes on the import review screen.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "@/api/client";
import {
  useCreateExposureVersion,
  useImportWorkbook,
  useIntakeTemplate,
  useExposureFindings,
  useExposureVersion,
  useExposureVersions,
  useProjects,
  usePublishExposure,
  useUploadOedFile,
  useValidateExposure,
} from "@/api/hooks";
import type { ExposureVersion, Finding, OEDFileKind, Severity } from "@/api/types";
import { StatusBadge } from "@/components/StatusBadge";
import {
  Button,
  Card,
  Combobox,
  EmptyState,
  Field,
  FileInput,
  MetricTile,
  Notice,
  PageHeader,
  Select,
  Spinner,
  TextInput,
} from "@/components/primitives";
import { useWorkingContext } from "@/context/WorkingContext";
import { formatCount, formatMoney } from "@/lib/format";

import { PortfolioRows } from "./PortfolioRows";

import "./ExposureWorkspace.css";

const FILE_KINDS: { kind: OEDFileKind; label: string; requirement: string }[] = [
  {
    kind: "location",
    label: "Location file",
    requirement: "Required. The source for ground-up loss.",
  },
  {
    kind: "account",
    label: "Account file",
    requirement: "Required for insured loss: policies, deductibles, limits and layers.",
  },
  {
    kind: "reins_info",
    label: "Reinsurance Info file",
    requirement: "Required for loss net of reinsurance: one record per contract.",
  },
  {
    kind: "reins_scope",
    label: "Reinsurance Scope file",
    requirement: "Required for loss net of reinsurance: which risks each contract covers.",
  },
];

export function ExposureWorkspace({ embedded = false }: { embedded?: boolean } = {}) {
  const context = useWorkingContext();
  const { data: versions, isLoading } = useExposureVersions(context.projectId);
  const { data: projects } = useProjects();
  const [selectedId, setSelectedId] = useState<string | undefined>(context.exposureId);
  const [adding, setAdding] = useState(false);

  const selected = versions?.find((item) => item.id === selectedId);

  /* The two ways in used to sit under the list, in a third of the screen,
     while the other two thirds held one sentence asking for a selection. They
     are the same master-and-detail pair as everything else on the screen: the
     list chooses, and the wide side shows either the portfolio chosen or, when
     there is nothing to show, how to add one. */
  const showAdd = adding || !selected;

  const projectNames = useMemo(
    () => new Map((projects ?? []).map((project) => [project.id, project.name])),
    [projects],
  );

  function choose(version: ExposureVersion) {
    setSelectedId(version.id);
    context.setExposure(version);
    setAdding(false);
  }

  return (
    <>
      {embedded ? null : <PageHeader
        title="Exposure workspace"
        description="Import or create portfolio records, review how CASS interprets them as OED, resolve findings, then publish an immutable version that an analysis can use."
      />}

      <div className="exposure-layout">
        <div className="exposure-layout__list">
          <Card
            title="Portfolios"
            description={
              context.project
                ? `In ${context.project.name}`
                : "Every project you can see."
            }
            actions={
              selected ? (
                <Button size="sm" onClick={() => setAdding(!adding)}>
                  {adding ? "Cancel" : "Add portfolio"}
                </Button>
              ) : null
            }
          >
            <ProjectPicker />
            {isLoading ? (
              <Spinner label="Loading portfolios" />
            ) : versions && versions.length > 0 ? (
              <ul className="exposure-list">
                {versions.map((version) => (
                  <li key={version.id}>
                    <button
                      type="button"
                      className={`exposure-list__item ${
                        version.id === selectedId && !adding
                          ? "exposure-list__item--active"
                          : ""
                      }`.trim()}
                      onClick={() => choose(version)}
                      aria-current={version.id === selectedId && !adding}
                    >
                      <span className="exposure-list__name">
                        {version.name}
                        <span className="exposure-list__version">v{version.version}</span>
                      </span>
                      <span className="exposure-list__meta">
                        <ExposureStateBadge version={version} />
                        <span className="muted">
                          {formatCount(version.location_count)} locations
                        </span>
                      </span>
                      {/* Which project a portfolio belongs to matters only
                          while the list is showing several of them. */}
                      {!context.projectId && projectNames.get(version.project) ? (
                        <span className="exposure-list__project muted">
                          {projectNames.get(version.project)}
                        </span>
                      ) : null}
                    </button>
                  </li>
                ))}
              </ul>
            ) : projects && projects.length === 0 ? (
              <EmptyState
                title="No project yet"
                description="A portfolio belongs to a project, and you are not in one yet. Create a project first; it is the workspace that owns portfolios, runs and results."
                action={
                  <Link className="btn btn--primary btn--md" to="/">
                    Go to the Portfolio dashboard
                  </Link>
                }
              />
            ) : (
              <EmptyState
                title="No portfolios yet"
                description="Bring one in from a spreadsheet with the intake template, or start from OED files if you already have them. Both ways in are under Add a portfolio."
              />
            )}
          </Card>
        </div>

        <div className="exposure-layout__detail">
          {selected && !showAdd ? (
            <ExposureDetail version={selected} />
          ) : (
            <AddPortfolio onCreated={choose} />
          )}
        </div>
      </div>

      {/* Full width rather than in the column beside the portfolio list: a
          location file is twenty-odd columns, and reading it through a third
          of the screen is how it stayed unread. */}
      {selected && !showAdd ? <PortfolioRows version={selected} /> : null}
    </>
  );
}

/**
 * The two ways in, side by side, with the choice between them stated.
 *
 * Which route applies is decided by the source data rather than by preference,
 * so each one is headed by the data it suits rather than by what it does. A
 * person who was sent a spreadsheet and a person who was sent OED files can
 * both find their own sentence and stop reading.
 */
function AddPortfolio({ onCreated }: { onCreated: (version: ExposureVersion) => void }) {
  const { data: projects } = useProjects();
  const context = useWorkingContext();

  const hasProjects = Boolean(projects && projects.length > 0);

  return (
    <Card
      title="Add a portfolio"
      description="Two ways in. Which one you use is decided by what you were sent, not by preference."
    >
      {/* Both forms below need a project, and the screen used to say so twice,
          in grey, under two disabled buttons. It is said once, before the
          forms, with the way out of it. */}
      {!context.projectId ? (
        <Notice
          tone="info"
          title={hasProjects ? "Choose a project first" : "Create a project first"}
        >
          {hasProjects ? (
            "A portfolio belongs to one project. Choose one in the Project list, and both routes here become available."
          ) : (
            <>
              A portfolio belongs to one project, and you are not in one yet.{" "}
              <Link to="/">Create a project on the Portfolio dashboard</Link>, then
              come back here.
            </>
          )}
        </Notice>
      ) : null}

      <div className="exposure-routes">
        <IntakeImportForm />
        <CreateVersionForm onCreated={onCreated} />
      </div>
    </Card>
  );
}

/**
 * Import a completed CASS intake template.
 *
 * The template is downloaded rather than described, because a schedule typed
 * into a workbook of somebody\'s own design is the thing the import review
 * spends its afternoon reconciling. Generating it from the intake profile
 * means the columns a person fills in are exactly the ones the reader
 * interprets.
 *
 * Nothing becomes a portfolio here. An import is staged, profiled and put in
 * front of a person, and only a promotion turns a reviewed cohort into an
 * exposure version -- so the finish line is the import review, not this form.
 */
function IntakeImportForm() {
  const context = useWorkingContext();
  const template = useIntakeTemplate();
  const importWorkbook = useImportWorkbook();

  const [file, setFile] = useState<File | null>(null);
  const [asAtDate, setAsAtDate] = useState("");

  const projectId = context.projectId;
  const error = (template.error ?? importWorkbook.error) as ApiError | null;

  return (
    <section className="exposure-route">
      <h3 className="exposure-route__title">Your data is a spreadsheet</h3>
      <p className="exposure-route__lead">
        The usual case: a schedule in whatever layout it arrived in. Copy it into the
        CASS intake template, whose columns are the ones CASS reads. The import is
        staged and profiled for review, and nothing becomes a portfolio until you
        promote it.
      </p>

      {error ? (
        <Notice tone="error" title="The import was refused">
          {error.message}
        </Notice>
      ) : null}

      <div className="exposure-route__download">
        <Button
          variant="secondary"
          onClick={() => template.mutate(projectId)}
          busy={template.isPending}
        >
          Download the intake template
        </Button>
      </div>

      <Field
        label="Completed template"
        htmlFor="intake-file"
        hint="Read as it stands. Nothing in it is corrected on the way in."
      >
        <FileInput
          id="intake-file"
          accept=".xlsx,.xls"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </Field>

      <Field
        label="As-at date"
        htmlFor="intake-as-at"
        hint="Optional. The date this schedule describes, if that is not today — the end of last quarter, say. It labels the import and is shown on the import review; it changes nothing CASS reads."
      >
        <TextInput
          id="intake-as-at"
          type="date"
          className="exposure-route__date"
          value={asAtDate}
          onChange={(event) => setAsAtDate(event.target.value)}
        />
      </Field>

      <div className="exposure-route__actions">
        <Button
          variant="primary"
          disabled={!file || !projectId}
          busy={importWorkbook.isPending}
          onClick={() => {
            if (file && projectId) {
              importWorkbook.mutate({
                project: projectId,
                file,
                snapshot_date: asAtDate || undefined,
              });
            }
          }}
          title={
            projectId
              ? "Stage the workbook and profile what it contains."
              : "Choose a project first."
          }
        >
          Import workbook
        </Button>
      </div>

      {importWorkbook.isSuccess ? (
        <Notice tone="ok" title="Imported and profiled">
          Nothing has become a portfolio yet.{" "}
          <Link to="/import-review">
            Review what came in, what is missing and what it would cost
          </Link>
          , then promote a cohort to an exposure version.
        </Notice>
      ) : null}
    </section>
  );
}

function ProjectPicker() {
  const { data: projects } = useProjects();
  const context = useWorkingContext();

  if (!projects || projects.length === 0) return null;

  return (
    <Field label="Project" htmlFor="project-picker">
      <Combobox
        id="project-picker"
        value={context.projectId ?? ""}
        onChange={(value) => context.setProject(projects.find((item) => item.id === value))}
        options={[
          { value: "", label: "All projects I can see" },
          ...projects.map((project) => ({ value: project.id, label: project.name })),
        ]}
      />
    </Field>
  );
}

function ExposureStateBadge({ version }: { version: ExposureVersion }) {
  if (version.is_frozen) {
    return (
      <StatusBadge tone="ok" size="sm" detail="Published and immutable.">
        Published
      </StatusBadge>
    );
  }
  const report = version.validation_report?.validation;
  if (report?.blocking) {
    return (
      <StatusBadge
        tone="error"
        size="sm"
        detail={`${report.error_count} finding(s) must be resolved before publication.`}
      >
        {report.error_count} to resolve
      </StatusBadge>
    );
  }
  if (version.state === "validated") {
    return (
      <StatusBadge tone="info" size="sm" detail="Validated and ready to publish.">
        Ready
      </StatusBadge>
    );
  }
  return (
    <StatusBadge tone="idle" size="sm" detail="Not yet validated.">
      Draft
    </StatusBadge>
  );
}

/**
 * Start a portfolio from OED files.
 *
 * This is the other way in. The route beside it takes a completed intake
 * template and stages it for review; this one makes an empty portfolio for
 * source data that is already OED, which the four attachments on the portfolio
 * itself then fill.
 *
 * It asks for a name and nothing else. Everything else about a portfolio --
 * how many locations, what they are worth, which perspectives the files
 * support -- is read from the files rather than typed here, and a field a
 * person fills in that nothing then reads is a field that will eventually
 * disagree with the data.
 *
 * The project is the one that is chosen, never the first one that happens to
 * come back from the API: a portfolio filed under a project nobody picked is
 * found again only by whoever goes looking for it.
 */
function CreateVersionForm({ onCreated }: { onCreated: (version: ExposureVersion) => void }) {
  const context = useWorkingContext();
  const create = useCreateExposureVersion();
  const [name, setName] = useState("");

  const projectId = context.projectId;
  const error = create.error as ApiError | null;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!projectId) return;
    create.mutate(
      { project: projectId, name },
      {
        onSuccess: (version) => {
          context.setExposure(version);
          setName("");
          onCreated(version);
        },
      },
    );
  }

  return (
    <section className="exposure-route">
      <h3 className="exposure-route__title">Your data is already OED</h3>
      <p className="exposure-route__lead">
        OED is the industry-standard layout. If a colleague or another system has
        already produced the location, account and reinsurance files, name the
        portfolio here and attach them to it directly. No review step is needed:
        the files are read where they stand.
      </p>

      <form onSubmit={submit}>
        {error ? (
          <Notice tone="error" title="Could not create the version">
            {error.message}
          </Notice>
        ) : null}
        <Field
          label="Portfolio name"
          htmlFor="new-exposure-name"
          required
          hint="A later correction creates version 2 of the same name rather than editing this one."
        >
          <TextInput
            id="new-exposure-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
          />
        </Field>
        <div className="exposure-route__actions">
          <Button
            type="submit"
            variant="primary"
            busy={create.isPending}
            disabled={!projectId || !name}
            title={
              projectId
                ? "Create version 1 of this portfolio, ready for its files."
                : "Choose a project first."
            }
          >
            Create version
          </Button>
        </div>
      </form>
    </section>
  );
}

function ExposureDetail({ version }: { version: ExposureVersion }) {
  const { data: detail } = useExposureVersion(version.id);
  const current = detail ?? version;

  const validate = useValidateExposure(current.id);
  const publish = usePublishExposure(current.id);

  const validateError = validate.error as ApiError | null;
  const publishError = publish.error as ApiError | null;
  const report = current.validation_report?.validation;

  return (
    <div className="exposure-detail">
      <Card
        title={`${current.name} v${current.version}`}
        description={
          current.is_frozen
            ? "This version is published and immutable. Corrections create a new version."
            : "Attach the OED files, validate, then publish."
        }
        actions={
          <>
            <Button
              onClick={() => validate.mutate()}
              busy={validate.isPending}
              disabled={current.is_frozen}
            >
              Validate
            </Button>
            <Button
              variant="primary"
              onClick={() => publish.mutate()}
              busy={publish.isPending}
              disabled={current.is_frozen || !current.is_publishable}
            >
              {current.is_frozen ? "Published" : "Publish"}
            </Button>
          </>
        }
      >
        {validateError ? (
          <Notice tone="error" title="Validation could not run">
            {validateError.message}
          </Notice>
        ) : null}
        {publishError ? (
          <Notice tone="error" title="Publication refused">
            {publishError.message}
          </Notice>
        ) : null}
        {current.is_frozen ? (
          <Notice tone="ok" title="Published">
            Analyses may now use this version, and it can no longer change. A
            correction creates version {current.version + 1}.
          </Notice>
        ) : null}

        <div className="exposure-metrics">
          <MetricTile label="Locations" value={formatCount(current.location_count)} />
          <MetricTile label="Accounts" value={formatCount(current.account_count)} />
          <MetricTile
            label="Total insured value"
            value={formatMoney(current.total_tiv)}
            unit={current.run_currency}
          />
          <MetricTile
            label="OED schema"
            value={current.oed_schema_version || "not yet determined"}
            tone="muted"
          />
        </div>
      </Card>

      <FileAttachments version={current} />
      <PerspectiveAvailabilityCard version={current} />
      {report ? <FindingsCard exposureId={current.id} /> : null}
      <TivBreakdown version={current} />
    </div>
  );
}

function FileAttachments({ version }: { version: ExposureVersion }) {
  const upload = useUploadOedFile(version.id);
  const [busyKind, setBusyKind] = useState<OEDFileKind | null>(null);
  const error = upload.error as ApiError | null;

  const attached = new Map(version.attached_files.map((file) => [file.role, file]));

  function handleFile(kind: OEDFileKind, file: File | undefined) {
    if (!file) return;
    setBusyKind(kind);
    upload.mutate({ kind, file }, { onSettled: () => setBusyKind(null) });
  }

  return (
    <Card
      title="Source files"
      description="CASS reads these exactly as supplied and never rewrites them."
    >
      {error ? (
        <Notice tone="error" title="Upload refused">
          {error.message}
        </Notice>
      ) : null}

      <ul className="file-list">
        {FILE_KINDS.map(({ kind, label, requirement }) => {
          const file = attached.get(`oed_${kind}`);
          const inputId = `upload-${kind}`;
          return (
            <li key={kind} className="file-list__item">
              <div className="file-list__text">
                <p className="file-list__label">
                  {label}
                  {file ? (
                    <StatusBadge tone="ok" size="sm">
                      Attached
                    </StatusBadge>
                  ) : null}
                </p>
                <p className="file-list__requirement">{requirement}</p>
                {file ? (
                  <p className="file-list__name">{file.original_filename}</p>
                ) : null}
              </div>
              <div className="file-list__action">
                <label className="file-list__button" htmlFor={inputId}>
                  {file ? "Replace" : "Attach"}
                  {busyKind === kind ? "…" : ""}
                </label>
                <input
                  id={inputId}
                  className="visually-hidden"
                  type="file"
                  accept=".csv,text/csv"
                  disabled={version.is_frozen || busyKind !== null}
                  onChange={(event) => {
                    handleFile(kind, event.target.files?.[0]);
                    event.target.value = "";
                  }}
                />
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function PerspectiveAvailabilityCard({ version }: { version: ExposureVersion }) {
  if (!version.supported_perspectives?.length) return null;

  return (
    <Card
      title="Perspectives this data supports"
      description="A perspective is offered only where the source files support it. CASS does not generate empty financial files to imply one."
    >
      <ul className="perspective-list">
        {version.supported_perspectives.map((item) => (
          <li key={item.perspective} className="perspective-list__item">
            <StatusBadge
              tone={item.available ? "ok" : "idle"}
              detail={item.available ? "Available" : "Not available"}
            >
              {item.label}
            </StatusBadge>
            <span className={item.available ? "" : "muted"}>{item.reason}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

const SEVERITY_TONE: Record<Severity, "error" | "warning" | "info"> = {
  error: "error",
  warning: "warning",
  info: "info",
};

function FindingsCard({ exposureId }: { exposureId: string }) {
  const { data: findings, isLoading } = useExposureFindings(exposureId);
  const [severity, setSeverity] = useState<"" | Severity>("");

  if (isLoading) return <Spinner label="Loading findings" />;
  if (!findings) return null;

  const visible = severity
    ? findings.findings.filter((item) => item.severity === severity)
    : findings.findings;

  return (
    <Card
      title="Validation findings"
      description={
        findings.blocking
          ? "These must be resolved in the source records before this version can be published."
          : "No finding blocks publication. Warnings are recorded and travel with the result."
      }
      actions={
        <Select
          aria-label="Filter findings by severity"
          value={severity}
          onChange={(event) => setSeverity(event.target.value as "" | Severity)}
        >
          <option value="">All ({findings.findings.length})</option>
          <option value="error">Errors ({findings.error_count})</option>
          <option value="warning">Warnings ({findings.warning_count})</option>
        </Select>
      }
    >
      {visible.length === 0 ? (
        <EmptyState title="Nothing to show" description="No findings match this filter." />
      ) : (
        <ul className="finding-list">
          {visible.slice(0, 100).map((finding, index) => (
            <FindingRow key={`${finding.code}-${index}`} finding={finding} />
          ))}
        </ul>
      )}
      {visible.length > 100 ? (
        <p className="muted finding-list__more">
          Showing the first 100 of {formatCount(visible.length)}. Export the full report for
          the rest.
        </p>
      ) : null}
    </Card>
  );
}

function FindingRow({ finding }: { finding: Finding }) {
  return (
    <li className={`finding finding--${finding.severity}`}>
      <div className="finding__header">
        <StatusBadge tone={SEVERITY_TONE[finding.severity]} size="sm">
          {finding.severity}
        </StatusBadge>
        <span className="finding__message">{finding.message}</span>
      </div>
      <p className="finding__remediation">{finding.remediation}</p>
      <p className="finding__locator mono">
        {finding.record_key ?? "portfolio"}
        {finding.row_number ? ` · row ${finding.row_number}` : ""}
        {finding.field ? ` · ${finding.field}` : ""}
        {finding.value ? ` · "${finding.value}"` : ""}
      </p>
    </li>
  );
}

function TivBreakdown({ version }: { version: ExposureVersion }) {
  const coverage = Object.entries(version.tiv_by_coverage ?? {});
  const country = Object.entries(version.tiv_by_country ?? {});
  const currency = Object.entries(version.tiv_by_currency ?? {});

  if (!coverage.length && !country.length) return null;

  return (
    <Card title="Value summary" description="Reconciles to the source files exactly.">
      <div className="tiv-grid">
        <TivTable title="By coverage" rows={coverage} currency={version.run_currency} />
        <TivTable title="By country" rows={country} currency={version.run_currency} />
        <TivTable title="By currency" rows={currency} />
      </div>

      {version.unmodelled_subperils?.length ? (
        <Notice tone="warning" title="Covered sub-perils this release does not model">
          {version.unmodelled_subperils.join(", ")}. Results from this portfolio will
          understate total earthquake loss, and the scope statement will say so.
        </Notice>
      ) : null}
    </Card>
  );
}

function TivTable({
  title,
  rows,
  currency,
}: {
  title: string;
  rows: [string, string][];
  currency?: string;
}) {
  if (!rows.length) return null;
  return (
    <div>
      <h3 className="tiv-table__title">{title}</h3>
      <table className="data-table">
        <tbody>
          {rows.map(([key, value]) => (
            <tr key={key}>
              <th scope="row">{key}</th>
              <td className="numeric">{formatMoney(value)}</td>
              {currency ? <td className="muted">{currency}</td> : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

