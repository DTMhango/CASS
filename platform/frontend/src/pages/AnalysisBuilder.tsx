/**
 * The analysis builder.
 *
 * Section 3: "Configure a governed run -- model settings, financial options,
 * outputs, resource profile and validation summary." Section 8 sets the
 * preconditions: a published exposure version, an approved model, and a
 * perspective the source data supports.
 *
 * The builder checks those preconditions and says which are unmet. It does not
 * submit work yet: the Oasis and OpenQuake adapters are phase 2 and 3 of the
 * roadmap. Showing the gates now is deliberate -- section 13 requires each
 * engine capability to be exposed through a thin CASS workflow first.
 */

import { useMemo } from "react";
import { Link } from "react-router-dom";

import { useExposureVersions, useModelCatalogue, usePlatformInfo } from "@/api/hooks";
import type { PerspectiveKey } from "@/api/types";
import { StatusBadge } from "@/components/StatusBadge";
import {
  Button,
  Card,
  Field,
  Notice,
  PageHeader,
  Select,
} from "@/components/primitives";
import { useWorkingContext } from "@/context/WorkingContext";
import { formatCount, formatMoney } from "@/lib/format";

import "./AnalysisBuilder.css";

interface Precondition {
  label: string;
  met: boolean;
  detail: string;
  fix?: { to: string; label: string };
}

export function AnalysisBuilder() {
  const context = useWorkingContext();
  const { data: exposures } = useExposureVersions(context.projectId);
  const { data: models } = useModelCatalogue();
  const { data: platform } = usePlatformInfo();

  const exposure = exposures?.find((item) => item.id === context.exposureId);
  const model = models?.find((item) => item.id === context.modelId);

  const perspectiveAvailability = exposure?.supported_perspectives?.find(
    (item) => item.perspective === context.perspective,
  );

  const preconditions = useMemo<Precondition[]>(
    () => [
      {
        label: "A project is selected",
        met: Boolean(context.projectId),
        detail: context.projectId
          ? "Runs and results will belong to this project."
          : "Choose a project so the run, its artifacts and its results have an owner.",
        fix: { to: "/", label: "Choose a project" },
      },
      {
        label: "A published exposure version is selected",
        met: Boolean(exposure?.is_usable_by_runs),
        detail: exposure
          ? exposure.is_usable_by_runs
            ? `${exposure.name} v${exposure.version} is published and immutable.`
            : "The selected version is still a draft. Publish it so the run has a fixed input."
          : "No portfolio is selected.",
        fix: { to: "/exposure", label: "Open the exposure workspace" },
      },
      {
        label: "An approved model version is selected",
        met: Boolean(model),
        detail: model
          ? model.is_research_prototype
            ? `${model.reference} is a research prototype. It may be run, but its output must not be used for decisions.`
            : `${model.reference} is approved for decision use.`
          : "No model version is selected.",
        fix: { to: "/models", label: "Open the model catalogue" },
      },
      {
        label: "The perspective is supported by the source data",
        met: Boolean(perspectiveAvailability?.available),
        detail:
          perspectiveAvailability?.reason ??
          "Select a portfolio to see which perspectives its files support.",
        fix: { to: "/exposure", label: "Review the source files" },
      },
    ],
    [context.projectId, exposure, model, perspectiveAvailability],
  );

  const unmet = preconditions.filter((item) => !item.met);

  return (
    <>
      <PageHeader
        title="Analysis builder"
        description="Select what the run will use, confirm the preconditions, then submit. Every setting comes from the approved model rather than a hand-edited engine file."
      />

      <div className="builder">
        <Card title="What this run will use">
          <Field
            label="Portfolio"
            htmlFor="builder-exposure"
            hint="Only published versions may be used, so the input is fixed for the life of the run."
          >
            <Select
              id="builder-exposure"
              value={context.exposureId ?? ""}
              onChange={(event) =>
                context.setExposure(exposures?.find((item) => item.id === event.target.value))
              }
            >
              <option value="">Select a portfolio</option>
              {exposures?.map((item) => (
                <option key={item.id} value={item.id} disabled={!item.is_usable_by_runs}>
                  {item.name} v{item.version}
                  {item.is_usable_by_runs ? "" : " (draft)"}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Model version" htmlFor="builder-model">
            <Select
              id="builder-model"
              value={context.modelId ?? ""}
              onChange={(event) =>
                context.setModel(models?.find((item) => item.id === event.target.value))
              }
            >
              <option value="">Select a model version</option>
              {models?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.reference}
                  {item.is_research_prototype ? " (research only)" : ""}
                </option>
              ))}
            </Select>
          </Field>

          <Field
            label="Financial perspective"
            htmlFor="builder-perspective"
            hint="Ground-up needs locations only. Insured needs an account file. Reinsurance needs contracts and scope."
          >
            <Select
              id="builder-perspective"
              value={context.perspective}
              onChange={(event) =>
                context.setPerspective(event.target.value as PerspectiveKey)
              }
            >
              {(exposure?.supported_perspectives ?? DEFAULT_PERSPECTIVES).map((item) => (
                <option key={item.perspective} value={item.perspective}>
                  {item.label}
                  {item.available ? "" : " — not supported by this data"}
                </option>
              ))}
            </Select>
          </Field>

          <Field
            label="Resource profile"
            htmlFor="builder-profile"
            hint="Declared limits, so one large job cannot exhaust the host."
          >
            <Select id="builder-profile" defaultValue={platform?.default_execution_profile}>
              {Object.entries(platform?.execution_profiles ?? {}).map(([key, profile]) => (
                <option key={key} value={key}>
                  {key} — {profile.cpu} CPU, {profile.memory_gb} GB,{" "}
                  {Math.round(profile.timeout_seconds / 3600)}h limit
                </option>
              ))}
            </Select>
          </Field>
        </Card>

        <div className="builder__side">
          <Card title="Before this run may be submitted">
            <ul className="precondition-list">
              {preconditions.map((item) => (
                <li key={item.label} className="precondition">
                  <StatusBadge tone={item.met ? "ok" : "warning"} size="sm">
                    {item.met ? "ready" : "outstanding"}
                  </StatusBadge>
                  <div>
                    <p className="precondition__label">{item.label}</p>
                    <p className="precondition__detail">{item.detail}</p>
                    {!item.met && item.fix ? (
                      <Link to={item.fix.to} className="precondition__fix">
                        {item.fix.label}
                      </Link>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>

            <div className="builder__submit">
              <Button variant="primary" disabled title={submitTitle(unmet.length)}>
                Submit analysis
              </Button>
              <p className="muted builder__submit-note">
                {unmet.length > 0
                  ? `${unmet.length} precondition(s) outstanding.`
                  : "Submission is enabled once the Oasis adapter lands in phase 2 of the roadmap."}
              </p>
            </div>
          </Card>

          {exposure ? (
            <Card title="Input summary">
              <dl className="builder__facts">
                <div>
                  <dt>Locations</dt>
                  <dd className="numeric">{formatCount(exposure.location_count)}</dd>
                </div>
                <div>
                  <dt>Total insured value</dt>
                  <dd className="numeric">
                    {formatMoney(exposure.total_tiv)} {exposure.run_currency}
                  </dd>
                </div>
                <div>
                  <dt>OED schema</dt>
                  <dd>{exposure.oed_schema_version || "—"}</dd>
                </div>
              </dl>

              {exposure.unmodelled_subperils?.length ? (
                <Notice tone="warning" title="Sub-perils outside this release">
                  {exposure.unmodelled_subperils.join(", ")}. The result will state this.
                </Notice>
              ) : null}
            </Card>
          ) : null}
        </div>
      </div>
    </>
  );
}

const DEFAULT_PERSPECTIVES = [
  { perspective: "ground_up" as PerspectiveKey, label: "Ground-up loss", available: true, reason: "" },
  { perspective: "insured" as PerspectiveKey, label: "Insured loss", available: true, reason: "" },
  {
    perspective: "reinsurance" as PerspectiveKey,
    label: "Reinsurance loss",
    available: true,
    reason: "",
  },
];

function submitTitle(unmetCount: number): string {
  return unmetCount > 0
    ? "Resolve the outstanding preconditions first."
    : "Analysis submission arrives with the Oasis adapter.";
}
