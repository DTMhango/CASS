/**
 * The analysis builder.
 *
 * Section 3: "Configure a governed run -- model settings, financial options,
 * outputs, resource profile and validation summary." Section 8 sets the
 * preconditions: a published exposure version, an approved model, and a
 * perspective the source data supports.
 *
 * The builder checks those preconditions before it offers to submit, and the
 * API checks them again when it does. That is not duplication: a screen is a
 * courtesy and the API is the rule. What the screen adds is that an analyst
 * finds out here, with the fix one link away, rather than from a refusal after
 * they thought the work had started.
 *
 * Submission creates the run and queues it in one act, then hands the analyst
 * to the run monitor. Nothing is held open: section 3 requires that work
 * continues whether or not the browser stays open, so the response a submit
 * returns is the queued run, never the loss calculation.
 */

import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError } from "@/api/client";
import {
  useCreateAnalysis,
  useExposureVersions,
  useModelCatalogue,
  usePlatformInfo,
  useSubmitAnalysis,
} from "@/api/hooks";
import type { PerspectiveKey } from "@/api/types";
import { StatusBadge } from "@/components/StatusBadge";
import {
  Button,
  Card,
  Field,
  Notice,
  PageHeader,
  Select,
  TextInput,
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
  const navigate = useNavigate();
  const { data: exposures } = useExposureVersions(context.projectId);
  const { data: models } = useModelCatalogue();
  const { data: platform } = usePlatformInfo();

  const create = useCreateAnalysis();
  const submit = useSubmitAnalysis();

  const [label, setLabel] = useState("");
  const [profile, setProfile] = useState("");

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
  const ready = unmet.length === 0;
  const busy = create.isPending || submit.isPending;
  const refusal = (create.error ?? submit.error) as ApiError | null;

  async function submitAnalysis() {
    if (!ready || !context.projectId || !exposure || !model) return;

    try {
      const analysis = await create.mutateAsync({
        project: context.projectId,
        exposure_version: exposure.id,
        model_version: model.id,
        perspectives: [context.perspective],
        label: label.trim(),
        execution_profile: profile || undefined,
      });
      await submit.mutateAsync(analysis.id);
      navigate(`/runs/${analysis.run}`);
    } catch {
      // The refusal is already on the mutation and rendered above. Swallowing
      // it here keeps a governance answer from being reported as a crash.
    }
  }

  return (
    <>
      <PageHeader
        title="Analysis builder"
        description="Select what the run will use, confirm the preconditions, then submit. Every setting comes from the approved model rather than a hand-edited engine file."
      />

      {refusal ? (
        <Notice tone="error" title="The run was not submitted">
          <p>{refusal.message}</p>
          <p className="muted">
            Nothing was queued, and no partial run was left behind.
          </p>
        </Notice>
      ) : null}

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
            <Select
              id="builder-profile"
              value={profile || (platform?.default_execution_profile ?? "")}
              onChange={(event) => setProfile(event.target.value)}
            >
              {Object.entries(platform?.execution_profiles ?? {}).map(([key, item]) => (
                <option key={key} value={key}>
                  {key} — {item.cpu} CPU, {item.memory_gb} GB,{" "}
                  {Math.round(item.timeout_seconds / 3600)}h limit
                </option>
              ))}
            </Select>
          </Field>

          <Field
            label="Run name"
            htmlFor="builder-label"
            hint="What this run is for, in the words you would use to find it again. The portfolio and version are used if you leave it blank."
          >
            <TextInput
              id="builder-label"
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              placeholder={
                exposure ? `${exposure.name} v${exposure.version}` : "Quarterly baseline"
              }
            />
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
              <Button
                variant="primary"
                disabled={!ready || busy}
                busy={busy}
                onClick={submitAnalysis}
                title={
                  ready
                    ? "Queue the run and follow it on the run monitor."
                    : "Resolve the outstanding preconditions first."
                }
              >
                Submit analysis
              </Button>
              <p className="muted builder__submit-note">
                {unmet.length > 0
                  ? `${unmet.length} precondition(s) outstanding.`
                  : "The run is queued in the background. You do not have to keep this page open."}
              </p>
            </div>

            {ready && model?.is_research_prototype ? (
              <Notice tone="warning" title="This run will produce research output">
                {model.reference} is a research prototype. The result will be marked so
                it cannot be used for a pricing, capital or underwriting decision.
              </Notice>
            ) : null}
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
