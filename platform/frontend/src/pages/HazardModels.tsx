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
 *
 * The calculation mode sits on neither side of that line, which is why it has
 * a panel of its own. It is not the model's science and it is not the
 * operator's choice: a published national model says ``classical``, and a
 * classical run cannot produce a footprint no matter how it is configured. So
 * the platform converts it, every time, and the screen says so in those words
 * rather than leaving it as one row in a table of parameter changes.
 */

import { useMemo, useState } from "react";

import { ApiError } from "@/api/client";
import {
  useConfigureRun,
  useGrids,
  useHazardModels,
  useHazardSpecs,
  useInspectPackage,
  useJobParameters,
  useLaunchHazardRun,
  useSaveRunSpec,
  useUploadHazardModel,
} from "@/api/hooks";
import type {
  ConfiguredRun,
  HazardJobSpec,
  HazardModel,
  JobParameter,
  JobProblem,
  PackageInspection,
  UUID,
} from "@/api/types";
import { StatusBadge } from "@/components/StatusBadge";
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
import { Link } from "react-router-dom";

import { formatCount, formatDateTime } from "@/lib/format";

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

export function HazardModels({ embedded = false }: { embedded?: boolean } = {}) {
  const models = useHazardModels();
  const [selected, setSelected] = useState<UUID | undefined>();
  const model = models.data?.find((item) => item.id === selected);

  return (
    <>
      {embedded ? null : <PageHeader
        title="Hazard models"
        description="Upload a published seismic model and configure a run of it"
      />}

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
                      <em>
                        publishes as {item.published_calculation_mode} —
                        converted to event-based for the run
                      </em>
                    ) : null}
                    {item.needs_sampling ? (
                      <em>
                        ~{formatCount(item.estimated_realizations)} realisations
                      </em>
                    ) : null}
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
              hint="Nothing in the archive records this, and a footprint that cannot name its sources is untraceable. Name GEM where the model comes from a GEM release: GEM's permission is then recorded against it."
            >
              <TextInput
                id="hazard-org"
                value={organisation}
                onChange={(event) => setOrganisation(event.target.value)}
              />
            </Field>
          </div>

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

/**
 * Regions a run is commonly limited to.
 *
 * Offered because a national model over every onshore cell is a calculation
 * of many hours, and the default should not be the expensive one. The presets
 * are Indonesian because that is the model on the platform; any other country
 * gets the whole grid or bounds of its own.
 */
const REGIONS: Record<
  string,
  { label: string; country?: string; bounds: Record<string, string> | null }
> = {
  "jakarta-bandung": {
    label: "Jakarta and Bandung",
    country: "ID",
    bounds: {
      min_latitude: "-7.2",
      max_latitude: "-5.9",
      min_longitude: "106.5",
      max_longitude: "107.9",
    },
  },
  java: {
    label: "Java and Madura",
    country: "ID",
    bounds: {
      min_latitude: "-9.0",
      max_latitude: "-5.7",
      min_longitude: "105.0",
      max_longitude: "114.8",
    },
  },
  custom: {
    label: "Custom bounds",
    bounds: { min_latitude: "", max_latitude: "", min_longitude: "", max_longitude: "" },
  },
  national: { label: "The whole grid", bounds: null },
};

const BOUND_LABELS: Record<string, string> = {
  min_latitude: "Minimum latitude",
  max_latitude: "Maximum latitude",
  min_longitude: "Minimum longitude",
  max_longitude: "Maximum longitude",
};

/** The region as the API takes it: null for the whole grid, undefined while incomplete. */
function regionPayload(
  bounds: Record<string, string> | null,
): Record<string, number> | null | undefined {
  if (bounds === null) return null;
  const numbers: Record<string, number> = {};
  for (const [key, value] of Object.entries(bounds)) {
    if (value.trim() === "" || !Number.isFinite(Number(value))) return undefined;
    numbers[key] = Number(value);
  }
  return numbers;
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
  const initialRegion = model.country_code === "ID" ? "jakarta-bandung" : "national";
  const [regionKey, setRegionKey] = useState(initialRegion);
  const [bounds, setBounds] = useState<Record<string, string> | null>(
    REGIONS[initialRegion]?.bounds ?? null,
  );

  const grid = gridId ?? grids.data?.[0]?.id;
  const outcome = configure.data as ConfiguredRun | undefined;

  const offered = useMemo(
    () =>
      OFFERED.map((key) =>
        parameters.data?.find((item) => item.name === key),
      ).filter(Boolean) as JobParameter[],
    [parameters.data],
  );

  function resolve(
    next: Record<string, string>,
    nextBounds: Record<string, string> | null = bounds,
  ) {
    if (!grid) return;
    const region = regionPayload(nextBounds);
    // Incomplete custom bounds resolve nothing yet rather than falling back to
    // the whole grid, which is the one silent substitution to avoid here.
    if (region === undefined) return;
    const typed: Record<string, number | string> = {};
    for (const [key, value] of Object.entries(next)) {
      if (value === "") continue;
      const asNumber = Number(value);
      typed[key] = Number.isFinite(asNumber) ? asNumber : value;
    }
    configure.mutate({ grid, overrides: typed, region });
  }

  function chooseRegion(key: string) {
    const nextBounds = REGIONS[key]?.bounds ?? null;
    setRegionKey(key);
    setBounds(nextBounds ? { ...nextBounds } : null);
    resolve(overrides, nextBounds);
  }

  function changeBound(key: string, value: string) {
    const nextBounds = { ...(bounds ?? {}), [key]: value };
    setBounds(nextBounds);
    resolve(overrides, nextBounds);
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
        <Field
          label="Region"
          htmlFor="hazard-region"
          hint="Which cells the run computes. The whole grid is a calculation of many hours."
        >
          <Select
            id="hazard-region"
            value={regionKey}
            onChange={(event) => chooseRegion(event.target.value)}
          >
            {Object.entries(REGIONS)
              .filter(([, item]) => !item.country || item.country === model.country_code)
              .map(([key, item]) => (
                <option key={key} value={key}>
                  {item.label}
                </option>
              ))}
          </Select>
        </Field>
        {bounds
          ? Object.keys(BOUND_LABELS).map((key) => (
              <Field
                key={key}
                label={`${BOUND_LABELS[key]} (degrees)`}
                htmlFor={`region-${key}`}
              >
                <TextInput
                  id={`region-${key}`}
                  value={bounds[key] ?? ""}
                  onChange={(event) => changeBound(key, event.target.value)}
                />
              </Field>
            ))
          : null}
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
              const region = regionPayload(bounds);
              if (grid && region !== undefined) {
                save.mutate({ grid, name, overrides: typed, region });
              }
            }}
            disabled={save.isPending}
          >
            {save.isPending ? "Saving…" : "Save this configuration"}
          </Button>
          {save.isSuccess ? <Notice tone="ok">Configuration saved.</Notice> : null}
        </div>
      ) : null}

      <SavedSpecs model={model} />
    </Card>
  );
}

/**
 * The configurations already saved against this model.
 *
 * A saved configuration that cannot be seen again is a saved configuration
 * nobody will trust, and the checksum is the whole point of saving one: it is
 * what says two runs asked the engine for the same thing. So the list is part
 * of the editor rather than a separate screen, and it states runnability the
 * same way the editor does, because a specification can stop being runnable
 * after it was saved -- a grid is republished, a licence lapses.
 */
function SavedSpecs({ model }: { model: HazardModel }) {
  const specs = useHazardSpecs(model.id);
  const launch = useLaunchHazardRun(model.id);

  if (!specs.data?.length) return null;

  const refusal = launch.error as ApiError | null;

  return (
    <Disclosure summary={`Saved configurations (${specs.data.length})`}>
      {refusal ? (
        <Notice tone="error" title="The run was not started">
          {refusal.message}
        </Notice>
      ) : null}

      {launch.isSuccess ? (
        <Notice tone="ok" title="Queued">
          The calculation is running in the background.{" "}
          <Link to={`/runs/${launch.data.run}`}>Follow it on the run monitor</Link>.
          You do not have to keep this page open.
        </Notice>
      ) : null}

      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Runnable</th>
            <th scope="col">Saved</th>
            <th scope="col" />
          </tr>
        </thead>
        <tbody>
          {specs.data.map((spec) => (
            <SpecRow
              key={spec.id}
              spec={spec}
                            onLaunch={() => launch.mutate(spec.id)}
              busy={launch.isPending}
            />
          ))}
        </tbody>
      </table>
    </Disclosure>
  );
}

function SpecRow({
  spec,
  onLaunch,
  busy,
}: {
  spec: HazardJobSpec;
  onLaunch: () => void;
  busy: boolean;
}) {
  const blocking = spec.blocking_problems?.length ?? 0;
  const mayRun = spec.is_runnable;

  return (
    <tr>
      <th scope="row">{spec.name}</th>
      <td>
        <StatusBadge
          tone={spec.is_runnable ? "ok" : "error"}
          size="sm"
          detail={
            blocking > 0
              ? `${blocking} problem(s) would stop this run.`
              : undefined
          }
        >
          {spec.is_runnable ? "runnable" : "blocked"}
        </StatusBadge>
      </td>
      <td className="muted">{formatDateTime(spec.created_at)}</td>
      <td>
        <Button
          variant="primary"
          size="sm"
          disabled={!mayRun || busy}
          busy={busy}
          onClick={onLaunch}
          title={
            !spec.is_runnable
              ? "Resolve the problems and save the configuration again."
              : "Start the calculation. It runs in the background for hours."
          }
        >
          Run
        </Button>
      </td>
    </tr>
  );
}

/** What the run would do, and what is wrong with it. */
function Resolved({ outcome }: { outcome: ConfiguredRun }) {
  const errors = outcome.problems.filter((item) => item.severity === "error");
  const warnings = outcome.problems.filter((item) => item.severity === "warning");

  return (
    <>
      <CalculationMode outcome={outcome} />
      <Coverage outcome={outcome} />

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

/**
 * What kind of answer this run asks the engine for.
 *
 * The difference is the whole reason the platform exists between a published
 * model and Oasis. A classical calculation answers "how often is this
 * intensity exceeded at this site", which is a hazard curve. A footprint is
 * made of events: it needs a ground-motion field per simulated earthquake, and
 * only an event-based calculation produces those.
 *
 * The trap is that a classical run does not fail. It completes, exports, and
 * produces a set of curves -- and nothing a footprint can be built from. So
 * this is stated before anything else on the resolved output, and stated as a
 * conversion that has happened rather than an option that is available: there
 * is no useful run on the other side of that choice, and offering it would
 * only let somebody spend four hours discovering why.
 */
function CalculationMode({ outcome }: { outcome: ConfiguredRun }) {
  const change = outcome.conversion.changes.find(
    (item) => item.parameter === "calculation_mode",
  );
  const mode = outcome.configuration.calculation_mode;
  const converted = Boolean(change);

  return (
    <Notice
      tone={outcome.configuration.is_event_based ? "ok" : "error"}
      title={
        converted
          ? `Converted from ${change?.from} to ${change?.to}`
          : `This model already publishes as ${mode}`
      }
    >
      {outcome.configuration.is_event_based ? (
        <>
          <p>
            {converted
              ? "The published model computes hazard curves: how often each intensity is exceeded at a site. A footprint is made of events, so the run asks for ground-motion fields per simulated earthquake instead."
              : "The run asks the engine for ground-motion fields per simulated earthquake, which is what a footprint is built from."}
          </p>
          {converted ? (
            <p className="muted">
              This is not a setting. A classical run does not fail — it
              completes, exports, and produces curves that no footprint can be
              built from, so the conversion is applied to every run.{" "}
              {outcome.conversion.changes.length > 1
                ? `It forces ${outcome.conversion.changes.length - 1} other change(s) to the published configuration`
                : "No other change to the published configuration is needed"}
              {outcome.conversion.removed.length > 0
                ? `, and removes ${outcome.conversion.removed.length} setting(s) that mean nothing in an event-based run`
                : ""}
              . Both are listed below.
            </p>
          ) : null}
        </>
      ) : (
        <p>
          This configuration resolves to <span className="mono">{mode}</span>,
          which produces hazard curves rather than events. Nothing downstream
          can build a footprint from it.
        </p>
      )}
    </Notice>
  );
}

/**
 * How much of the country the run computes, and on what ground.
 *
 * Both are ways a footprint can be quietly short. A cell outside the region
 * produces no ground motion, so every location in it loses nothing in the
 * engine -- a zero indistinguishable from an event that did no damage. And a
 * cell with no nearby measurement takes the model's reference rock, which
 * understates the loss wherever the ground is softer.
 */
function Coverage({ outcome }: { outcome: ConfiguredRun }) {
  const coverage = outcome.coverage;
  const site = outcome.site_join as {
    sites?: number;
    measured?: number;
    defaulted?: number;
  };
  if (!coverage?.grid_cells) return null;

  return (
    <Notice
      tone={coverage.cells_skipped ? "warning" : "info"}
      title={`${formatCount(coverage.cells_computed ?? 0)} of ${formatCount(
        coverage.onshore_cells ?? 0,
      )} onshore cells computed`}
    >
      {coverage.cells_skipped ? (
        <p>
          {formatCount(coverage.cells_skipped)} cells are outside this run. A location in
          one of them maps to the grid and loses nothing, which reads exactly like an event
          that did no damage.
        </p>
      ) : null}
      {site?.sites ? (
        <p>
          {formatCount(site.measured ?? 0)} cells take the published model&rsquo;s measured
          Vs30; {formatCount(site.defaulted ?? 0)} are beyond its measurements and take its
          reference rock, which understates loss on softer ground.
        </p>
      ) : (
        <p>
          No published site model could be joined, so every cell uses the reference
          conditions.
        </p>
      )}
    </Notice>
  );
}

function Problem({ problem }: { problem: JobProblem }) {
  return (
    <Notice tone={problem.severity === "error" ? "error" : "warning"}>
      <strong className="mono">{problem.parameter}</strong> {problem.message}
    </Notice>
  );
}
