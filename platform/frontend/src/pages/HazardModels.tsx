/**
 * Uploading a published seismic model, and configuring a run of it.
 *
 * The OpenQuake web interface takes a zip and runs whatever the job.ini in it
 * says. That is the right tool for a seismologist and the wrong one here, for
 * a specific reason: a published national model is almost always a *classical*
 * calculation producing hazard curves, and a catastrophe model needs events.
 * Running the package as published would complete successfully and produce
 * nothing an Oasis footprint can be made from.
 *
 * So this screen does the conversion, and shows it. Three panels, in the order
 * the questions arrive.
 *
 * **What is in the package.** Read before anything is stored, so an operator
 * who uploaded the wrong file finds out here rather than from a registry
 * record they then have to explain. The realisation count is the number that
 * matters: a national logic tree enumerates to hundreds of alternative views
 * of the hazard, and a footprint is one event set.
 *
 * **What the run will change.** Every difference between the published
 * configuration and the one that will execute, as a list. Ten changes for the
 * PuSGeN 2024 model, two of which are invisible unless you know to look.
 *
 * **What is yours to change.** The parameters divide into the model's own
 * science, which is shown and not offered, and the run's choices, which are
 * editable and each state what moves in the answer when they move. Editing a
 * logic tree would not configure this model; it would make a different one
 * while keeping the name of the agency that published the original.
 */

import { useMemo, useState } from "react";

import { ApiError } from "@/api/client";
import {
  useConfigureRun,
  useGrids,
  useHazardModels,
  useInspectPackage,
  useJobParameters,
  useSaveRunSpec,
  useUploadHazardModel,
} from "@/api/hooks";
import type {
  ConfiguredRun,
  HazardModel,
  JobParameter,
  JobProblem,
  PackageInspection,
  UUID,
} from "@/api/types";
import {
  Button,
  Card,
  Disclosure,
  Field,
  MetricTile,
  Notice,
  PageHeader,
  Select,
  Spinner,
  TextInput,
} from "@/components/primitives";
import { formatCount } from "@/lib/format";

import "./HazardModels.css";

/** The run parameters offered on the form, in the order a person decides them. */
const OFFERED = [
  "investigation_time",
  "ses_per_logic_tree_path",
  "truncation_level",
  "random_seed",
] as const;

function bytes(value: number): string {
  if (value >= 1e9) return `${(value / 1e9).toFixed(1)} GB`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)} MB`;
  return `${(value / 1e3).toFixed(0)} kB`;
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return error ? String(error) : "";
}

export function HazardModels() {
  const models = useHazardModels();
  const [selected, setSelected] = useState<UUID | undefined>();
  const model = models.data?.find((item) => item.id === selected);

  return (
    <>
      <PageHeader
        title="Hazard models"
        description="Upload a published seismic model and configure a run of it"
      />

      <UploadPanel />

      {models.isLoading ? <Spinner label="Loading models" /> : null}

      {models.data?.length ? (
        <Card title="Registered models">
          <ul className="models">
            {models.data.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className={`models__item ${
                    item.id === selected ? "models__item--active" : ""
                  }`}
                  onClick={() =>
                    setSelected(item.id === selected ? undefined : item.id)
                  }
                >
                  <span className="models__name">{item.label}</span>
                  <span className="muted">
                    {item.country_code} · {item.version} ·{" "}
                    {item.source_organisation || "publisher not stated"}
                  </span>
                  <span className="models__flags">
                    {item.needs_conversion ? (
                      <em>{item.published_calculation_mode} — needs conversion</em>
                    ) : null}
                    {item.needs_sampling ? (
                      <em>
                        ~{formatCount(item.estimated_realizations)} realisations
                      </em>
                    ) : null}
                    {item.licence_cleared ? null : <em>licence not cleared</em>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {model ? <ConfigurePanel model={model} /> : null}
    </>
  );
}

/** Read an archive, show what it is, then register it. */
function UploadPanel() {
  const inspect = useInspectPackage();
  const upload = useUploadHazardModel();
  const [file, setFile] = useState<File | null>(null);
  const [countryCode, setCountryCode] = useState("ID");
  const [version, setVersion] = useState("");
  const [label, setLabel] = useState("");
  const [organisation, setOrganisation] = useState("");
  const [licence, setLicence] = useState("CC BY-NC-SA 4.0");

  const inspection = inspect.data as PackageInspection | undefined;

  function choose(event: React.ChangeEvent<HTMLInputElement>) {
    const chosen = event.target.files?.[0] ?? null;
    setFile(chosen);
    upload.reset();
    if (chosen) inspect.mutate(chosen);
  }

  return (
    <Card
      title="Upload a model"
      description="A published OpenQuake package: its logic trees, source files and job configuration."
    >
      <Field
        label="Model archive"
        htmlFor="hazard-archive"
        hint="A release zip. GEM's mosaic packages hold a job.zip inside, and that is unwrapped for you."
      >
        <input
          id="hazard-archive"
          type="file"
          accept=".zip"
          onChange={choose}
        />
      </Field>

      {inspect.isPending ? <Spinner label="Reading the package" /> : null}
      {inspect.error ? (
        <Notice tone="error">{errorText(inspect.error)}</Notice>
      ) : null}

      {inspection ? (
        <>
          <div className="tiles">
            <MetricTile
              label="Files"
              value={formatCount(inspection.file_count)}
              footnote={bytes(inspection.archive_bytes)}
            />
            <MetricTile
              label="Published as"
              value={inspection.configuration.calculation_mode}
              footnote={
                inspection.configuration.is_event_based
                  ? "produces events"
                  : "produces hazard curves, not events"
              }
            />
            <MetricTile
              label="Realisations"
              value={formatCount(
                inspection.logic_trees.estimated_realizations ?? 0,
              )}
              footnote={
                (inspection.logic_trees.estimated_realizations ?? 0) > 1
                  ? "a footprint needs one"
                  : ""
              }
            />
            <MetricTile
              label="Measures"
              value={inspection.configuration.intensity_measures.length}
              footnote={inspection.configuration.intensity_measures
                .slice(0, 4)
                .join(", ")}
            />
          </div>

          {inspection.logic_trees.tectonic_regions?.length ? (
            <p className="muted">
              Tectonic regions:{" "}
              {inspection.logic_trees.tectonic_regions.join(", ")}.
            </p>
          ) : null}

          {inspection.configuration.is_event_based ? null : (
            <Notice tone="info">
              This package is a {inspection.configuration.calculation_mode}{" "}
              calculation, which is what a national model publishes: it produces
              the probability of exceeding a ground motion, not a set of
              earthquakes. CASS converts it to an event-based run, and shows
              every change it makes.
            </Notice>
          )}

          <Disclosure summary={`Files in the package (${inspection.file_count})`}>
            <ul className="files">
              {inspection.files.map((item) => (
                <li key={item.path}>
                  <span className="mono">{item.path}</span>
                  <span className="muted">{bytes(item.size_bytes)}</span>
                </li>
              ))}
            </ul>
          </Disclosure>

          <div className="upload-form">
            <Field
              label="Country"
              htmlFor="hazard-country"
              hint="Two-letter ISO code."
            >
              <TextInput
                id="hazard-country"
                value={countryCode}
                onChange={(event) => setCountryCode(event.target.value)}
              />
            </Field>
            <Field label="Version" htmlFor="hazard-version" required>
              <TextInput
                id="hazard-version"
                value={version}
                onChange={(event) => setVersion(event.target.value)}
                placeholder="2024.0.0"
              />
            </Field>
            <Field label="Name" htmlFor="hazard-label" required>
              <TextInput
                id="hazard-label"
                value={label}
                onChange={(event) => setLabel(event.target.value)}
                placeholder="PuSGeN 2024 seismic hazard model for Indonesia"
              />
            </Field>
            <Field
              label="Published by"
              htmlFor="hazard-org"
              hint="Nothing in the archive records this, and a footprint that cannot name its sources is untraceable."
            >
              <TextInput
                id="hazard-org"
                value={organisation}
                onChange={(event) => setOrganisation(event.target.value)}
              />
            </Field>
            <Field label="Licence" htmlFor="hazard-licence">
              <TextInput
                id="hazard-licence"
                value={licence}
                onChange={(event) => setLicence(event.target.value)}
              />
            </Field>
          </div>

          <Notice tone="info">
            A model is registered as a draft with its licence not cleared. It
            may be used for research and platform development, and not for a
            pricing or reserving decision, until somebody records what grants
            that use.
          </Notice>

          {upload.error ? (
            <Notice tone="error">{errorText(upload.error)}</Notice>
          ) : null}
          {upload.isSuccess ? (
            <Notice tone="ok">Registered {upload.data?.reference}.</Notice>
          ) : null}

          <Button
            onClick={() =>
              file &&
              upload.mutate({
                file,
                country_code: countryCode,
                version,
                label,
                source_organisation: organisation,
                licence,
              })
            }
            disabled={!file || !version || !label || upload.isPending}
          >
            {upload.isPending ? "Registering…" : "Register model"}
          </Button>
        </>
      ) : null}
    </Card>
  );
}

/** Edit the run's parameters and see what they resolve to. */
function ConfigurePanel({ model }: { model: HazardModel }) {
  const grids = useGrids(model.country_code);
  const parameters = useJobParameters();
  const configure = useConfigureRun(model.id);
  const save = useSaveRunSpec(model.id);

  const [gridId, setGridId] = useState<UUID | undefined>();
  const [overrides, setOverrides] = useState<Record<string, string>>({
    investigation_time: "50",
    ses_per_logic_tree_path: "20",
  });
  const [name, setName] = useState(`${model.label} run`);

  const grid = gridId ?? grids.data?.[0]?.id;
  const outcome = configure.data as ConfiguredRun | undefined;

  const offered = useMemo(
    () =>
      OFFERED.map((key) =>
        parameters.data?.find((item) => item.name === key),
      ).filter(Boolean) as JobParameter[],
    [parameters.data],
  );

  function resolve(next: Record<string, string>) {
    if (!grid) return;
    const typed: Record<string, number | string> = {};
    for (const [key, value] of Object.entries(next)) {
      if (value === "") continue;
      const asNumber = Number(value);
      typed[key] = Number.isFinite(asNumber) ? asNumber : value;
    }
    configure.mutate({ grid, overrides: typed });
  }

  function change(parameter: string, value: string) {
    const next = { ...overrides, [parameter]: value };
    setOverrides(next);
    resolve(next);
  }

  return (
    <Card
      title={`Configure a run of ${model.label}`}
      description="The model's own science is shown and not offered. What you set is the run."
    >
      <div className="upload-form">
        <Field
          label="Area-peril grid"
          htmlFor="hazard-grid"
          hint="Ground motion is computed at the centre of each cell."
        >
          <Select
            id="hazard-grid"
            value={grid ?? ""}
            onChange={(event) => setGridId(event.target.value as UUID)}
          >
            {grids.data?.map((item) => (
              <option key={item.id} value={item.id}>
                {item.reference}
              </option>
            ))}
          </Select>
        </Field>
        {offered.map((parameter) => (
          <Field
            key={parameter.name}
            label={`${parameter.label}${parameter.unit ? ` (${parameter.unit})` : ""}`}
            htmlFor={`param-${parameter.name}`}
            hint={parameter.help_text}
          >
            <TextInput
              id={`param-${parameter.name}`}
              value={overrides[parameter.name] ?? ""}
              onChange={(event) => change(parameter.name, event.target.value)}
            />
          </Field>
        ))}
      </div>

      {offered.length > 0 ? (
        <Disclosure summary="What each of these changes">
          <dl className="consequences">
            {offered.map((parameter) => (
              <div key={parameter.name}>
                <dt>{parameter.label}</dt>
                <dd>{parameter.consequence}</dd>
              </div>
            ))}
          </dl>
        </Disclosure>
      ) : null}

      <Button onClick={() => resolve(overrides)} disabled={!grid}>
        {configure.isPending ? "Resolving…" : "Resolve configuration"}
      </Button>

      {configure.error ? (
        <Notice tone="error">{errorText(configure.error)}</Notice>
      ) : null}

      {outcome ? <Resolved outcome={outcome} /> : null}

      {outcome?.runnable ? (
        <div className="save-run">
          <Field label="Name this run" htmlFor="run-name">
            <TextInput
              id="run-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Button
            onClick={() => {
              const typed: Record<string, number> = {};
              for (const [key, value] of Object.entries(overrides)) {
                if (value !== "") typed[key] = Number(value);
              }
              if (grid) save.mutate({ grid, name, overrides: typed });
            }}
            disabled={save.isPending}
          >
            {save.isPending ? "Saving…" : "Save this configuration"}
          </Button>
          {save.isSuccess ? <Notice tone="ok">Configuration saved.</Notice> : null}
        </div>
      ) : null}
    </Card>
  );
}

/** What the run would do, and what is wrong with it. */
function Resolved({ outcome }: { outcome: ConfiguredRun }) {
  const errors = outcome.problems.filter((item) => item.severity === "error");
  const warnings = outcome.problems.filter((item) => item.severity === "warning");

  return (
    <>
      {errors.length === 0 ? (
        <Notice tone="ok">
          This configuration produces an event set CASS can convert to a
          footprint.
        </Notice>
      ) : null}
      {errors.map((item) => (
        <Problem key={item.parameter + item.message} problem={item} />
      ))}
      {warnings.map((item) => (
        <Problem key={item.parameter + item.message} problem={item} />
      ))}

      <Disclosure
        summary={`Changes from the published configuration (${outcome.conversion.changes.length})`}
        defaultOpen
      >
        <table className="grid">
          <thead>
            <tr>
              <th scope="col">Parameter</th>
              <th scope="col">As published</th>
              <th scope="col">For this run</th>
            </tr>
          </thead>
          <tbody>
            {outcome.conversion.changes.map((item) => (
              <tr key={item.parameter}>
                <th scope="row" className="mono">
                  {item.parameter}
                </th>
                <td className="mono">{item.from || "not set"}</td>
                <td className="mono">{item.to || "removed"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {outcome.conversion.removed.length > 0 ? (
          <p className="muted">
            Removed as meaningless in an event-based run:{" "}
            <span className="mono">
              {outcome.conversion.removed.join(", ")}
            </span>
            .
          </p>
        ) : null}
        <ul className="notes">
          {outcome.conversion.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </Disclosure>

      <Disclosure summary="The job configuration that would run">
        <pre className="rendered">{outcome.rendered}</pre>
      </Disclosure>
    </>
  );
}

function Problem({ problem }: { problem: JobProblem }) {
  return (
    <Notice tone={problem.severity === "error" ? "error" : "warning"}>
      <strong className="mono">{problem.parameter}</strong> {problem.message}
    </Notice>
  );
}
