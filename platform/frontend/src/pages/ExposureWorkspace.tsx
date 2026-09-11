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
 */

import { useState } from "react";

import { ApiError } from "@/api/client";
import {
  useCreateExposureVersion,
  useExposureFindings,
  useExposurePreview,
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
  Disclosure,
  EmptyState,
  Field,
  MetricTile,
  Notice,
  PageHeader,
  Select,
  Spinner,
  TextInput,
} from "@/components/primitives";
import { useWorkingContext } from "@/context/WorkingContext";
import { formatCount, formatMoney } from "@/lib/format";

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
    requirement: "Required for reinsurance loss: one record per contract.",
  },
  {
    kind: "reins_scope",
    label: "Reinsurance Scope file",
    requirement: "Required for reinsurance loss: which risks each contract covers.",
  },
];

export function ExposureWorkspace() {
  const context = useWorkingContext();
  const { data: versions, isLoading } = useExposureVersions(context.projectId);
  const [selectedId, setSelectedId] = useState<string | undefined>(context.exposureId);

  const selected = versions?.find((item) => item.id === selectedId);

  return (
    <>
      <PageHeader
        title="Exposure workspace"
        description="Import or create portfolio records, review how KRE interprets them as OED, resolve findings, then publish an immutable version that an analysis can use."
      />

      <div className="exposure-layout">
        <div className="exposure-layout__list">
          <Card
            title="Portfolios"
            description={
              context.project
                ? `In ${context.project.name}`
                : "Select a project to scope the list."
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
                        version.id === selectedId ? "exposure-list__item--active" : ""
                      }`.trim()}
                      onClick={() => {
                        setSelectedId(version.id);
                        context.setExposure(version);
                      }}
                      aria-current={version.id === selectedId}
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
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState
                title="No portfolios yet"
                description="Create a portfolio version, then attach the OED files it is built from."
              />
            )}
          </Card>

          <CreateVersionForm onCreated={setSelectedId} />
        </div>

        <div className="exposure-layout__detail">
          {selected ? (
            <ExposureDetail version={selected} />
          ) : (
            <Card>
              <EmptyState
                title="Select a portfolio"
                description="Choose a portfolio version on the left to review its files, findings and summaries."
              />
            </Card>
          )}
        </div>
      </div>
    </>
  );
}

function ProjectPicker() {
  const { data: projects } = useProjects();
  const context = useWorkingContext();

  if (!projects || projects.length === 0) return null;

  return (
    <Field label="Project" htmlFor="project-picker">
      <Select
        id="project-picker"
        value={context.projectId ?? ""}
        onChange={(event) => {
          const next = projects.find((item) => item.id === event.target.value);
          context.setProject(next);
        }}
      >
        <option value="">All projects I can see</option>
        {projects.map((project) => (
          <option key={project.id} value={project.id}>
            {project.name}
          </option>
        ))}
      </Select>
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

function CreateVersionForm({ onCreated }: { onCreated: (id: string) => void }) {
  const { data: projects } = useProjects();
  const context = useWorkingContext();
  const create = useCreateExposureVersion();
  const [name, setName] = useState("");
  const [cedant, setCedant] = useState("");

  const projectId = context.projectId ?? projects?.[0]?.id;
  const error = create.error as ApiError | null;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!projectId) return;
    create.mutate(
      { project: projectId, name, cedant },
      {
        onSuccess: (version) => {
          onCreated(version.id);
          context.setExposure(version);
          setName("");
          setCedant("");
        },
      },
    );
  }

  return (
    <Card title="New portfolio version">
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
        <Field label="Cedant" htmlFor="new-exposure-cedant">
          <TextInput
            id="new-exposure-cedant"
            value={cedant}
            onChange={(event) => setCedant(event.target.value)}
          />
        </Field>
        <div className="exposure-form__actions">
          <Button
            type="submit"
            variant="primary"
            busy={create.isPending}
            disabled={!projectId || !name}
          >
            Create version
          </Button>
        </div>
        {!projectId ? (
          <p className="muted exposure-form__hint">
            Create a project before adding a portfolio.
          </p>
        ) : null}
      </form>
    </Card>
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
            Analyses may now use this version. The OED artifacts and their checksums are
            fixed.
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
      <PreviewCard exposureId={current.id} />
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
      description="KRE reads these exactly as supplied and never rewrites them. Each is checksummed on upload."
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
                    <StatusBadge tone="ok" size="sm" detail="Registered and checksummed.">
                      Attached
                    </StatusBadge>
                  ) : null}
                </p>
                <p className="file-list__requirement">{requirement}</p>
                {file ? (
                  <p className="file-list__checksum mono">
                    {file.original_filename} · {file.checksum.slice(0, 23)}…
                  </p>
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
      description="A perspective is offered only where the source files support it. KRE does not generate empty financial files to imply one."
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

function PreviewCard({ exposureId }: { exposureId: string }) {
  const [open, setOpen] = useState(false);
  const { data: preview, error } = useExposurePreview(exposureId, open);

  return (
    <Card
      title="OED interpretation"
      description="Exactly what KRE will hand to the model. Columns KRE does not interpret are listed rather than dropped silently."
      actions={
        <Button size="sm" onClick={() => setOpen((value) => !value)}>
          {open ? "Hide" : "Show"} preview
        </Button>
      }
    >
      {!open ? (
        <p className="muted">The preview is loaded on request because it reads the files.</p>
      ) : error ? (
        <Notice tone="warning" title="Preview unavailable">
          {(error as ApiError).message}
        </Notice>
      ) : !preview ? (
        <Spinner label="Reading files" />
      ) : (
        Object.entries(preview.files).map(([kind, file]) => (
          <Disclosure
            key={kind}
            summary={`${kind} · ${formatCount(file.row_count)} rows`}
            defaultOpen={kind === "location"}
          >
            {file.unrecognised_columns.length ? (
              <Notice tone="warning" title="Columns KRE did not interpret">
                {file.unrecognised_columns.join(", ")}
              </Notice>
            ) : null}
            <div className="preview-scroll">
              <table className="data-table data-table--compact">
                <thead>
                  <tr>
                    {file.columns.map((column) => (
                      <th key={column} scope="col">
                        {column}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {file.rows.map((row, index) => (
                    <tr key={index}>
                      {file.columns.map((column) => (
                        <td key={column}>{row[column]}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Disclosure>
        ))
      )}
    </Card>
  );
}
