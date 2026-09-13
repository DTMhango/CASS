/**
 * The results workspace.
 *
 * Section 9 is strict about this screen: every decision view must identify
 * model version, valuation date, financial perspective, exposure quality,
 * assumption set, material exclusions and approval status, and research runs
 * must be operationally distinct from approved outputs.
 *
 * So a result is never rendered as a bare number here. The caveat block is
 * part of the card, not a footnote a user can miss.
 *
 * Comparison is the other half of the screen. Milestone M6 is completing the
 * analyst journey and comparing two governed runs, and section 9 requires a
 * model-change impact report to name the drivers behind a change rather than
 * only its size. Not one number of that comparison is worked out here: ADR 5
 * keeps money arithmetic on the server, where it is decimal and audited, and
 * this screen reads the answer and formats it.
 */

import { useState } from "react";

import { ApiError, saveBlob } from "@/api/client";
import {
  useApproveResult,
  useComparisons,
  useCreateComparison,
  useExportResult,
  useResults,
  useSession,
} from "@/api/hooks";
import type {
  ComparedMetric,
  ComparedReturnPeriod,
  ExposureQuality,
  ResultComparison,
  ResultSet,
  RunMode,
} from "@/api/types";
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
  TextInput,
} from "@/components/primitives";
import { useWorkingContext } from "@/context/WorkingContext";
import {
  formatDate,
  formatDateTime,
  formatMoney,
  formatMoneyExact,
  formatPercent,
} from "@/lib/format";

import "./ResultsWorkspace.css";

/** What each run mode claims, in the words the brief's section 5.2 uses. */
const RUN_MODE_LABELS: Record<RunMode | "", string> = {
  geometry_only: "Geometry only — no loss calculated",
  technical: "KRE-share technical loss",
  research: "Portfolio-loss research",
  decision: "Decision use",
  "": "not recorded",
};

export function ResultsWorkspace() {
  const context = useWorkingContext();
  const { data: results } = useResults(context.projectId);

  return (
    <>
      <PageHeader
        title="Results workspace"
        description="Approved loss metrics, the assumptions behind them, and what they exclude. Research output is marked so it cannot be mistaken for a decision number."
      />

      {!results || results.length === 0 ? (
        <Card>
          <EmptyState
            title="No results yet"
            description="A result appears here once an analysis run completes and its outputs have been ingested."
          />
        </Card>
      ) : (
        <div className="results">
          {results.map((result) => (
            <ResultCard key={result.id} result={result} />
          ))}
        </div>
      )}

      <Comparisons results={results ?? []} />
    </>
  );
}

/**
 * Compare two governed runs.
 *
 * Only results the platform will actually let a person compare are offered.
 * A ground-up against an insured, or two currencies, produces a difference
 * that cannot be interpreted, and the API refuses both -- so the candidate
 * list is narrowed to what the chosen baseline can be compared against rather
 * than offering everything and reporting a refusal afterwards.
 */
function Comparisons({ results }: { results: ResultSet[] }) {
  const context = useWorkingContext();
  const { data: comparisons } = useComparisons(context.projectId);
  const create = useCreateComparison();

  const [baselineId, setBaselineId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [label, setLabel] = useState("");

  const baseline = results.find((item) => item.id === baselineId);
  const candidates = baseline
    ? results.filter(
        (item) =>
          item.id !== baseline.id &&
          item.perspective === baseline.perspective &&
          item.currency === baseline.currency,
      )
    : [];

  const refusal = create.error as ApiError | null;
  const ready = Boolean(baselineId && candidateId && label.trim() && context.projectId);

  if (results.length < 2 && !comparisons?.length) return null;

  return (
    <Card
      title="Compare two runs"
      description="What changed between two results, and what on either of them accounts for it."
    >
      {refusal ? (
        <Notice tone="error" title="The comparison was refused">
          {refusal.message}
        </Notice>
      ) : null}

      <div className="compare__form">
        <Field
          label="Baseline"
          htmlFor="compare-baseline"
          hint="The result the other is measured against."
        >
          <Select
            id="compare-baseline"
            value={baselineId}
            onChange={(event) => {
              setBaselineId(event.target.value);
              setCandidateId("");
            }}
          >
            <option value="">Select a baseline</option>
            {results.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label} — {item.caveats.perspective}, {item.currency}
              </option>
            ))}
          </Select>
        </Field>

        <Field
          label="Candidate"
          htmlFor="compare-candidate"
          hint={
            baseline
              ? "Only results in the same perspective and currency can be compared."
              : "Choose a baseline first."
          }
        >
          <Select
            id="compare-candidate"
            value={candidateId}
            disabled={!baseline}
            onChange={(event) => setCandidateId(event.target.value)}
          >
            <option value="">Select a candidate</option>
            {candidates.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </Select>
        </Field>

        <Field label="Name this comparison" htmlFor="compare-label">
          <TextInput
            id="compare-label"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder="Impact of the 0.2.0 release"
          />
        </Field>
      </div>

      {baseline && candidates.length === 0 ? (
        <Notice tone="info" title="Nothing can be compared against this baseline">
          No other result shares its perspective and currency. A difference
          between two of those is not a difference anybody can interpret.
        </Notice>
      ) : null}

      <Button
        variant="primary"
        disabled={!ready}
        busy={create.isPending}
        onClick={() => {
          if (context.projectId) {
            create.mutate({
              project: context.projectId,
              label: label.trim(),
              baseline: baselineId,
              candidate: candidateId,
            });
          }
        }}
        title={ready ? "Compute and save the comparison." : "Choose two results and name the comparison."}
      >
        Compare
      </Button>

      {comparisons?.length ? (
        <div className="compare__saved">
          {comparisons.map((comparison) => (
            <ComparisonReport key={comparison.id} comparison={comparison} />
          ))}
        </div>
      ) : null}
    </Card>
  );
}

function ComparisonReport({ comparison }: { comparison: ResultComparison }) {
  const differences = comparison.differences;
  const shared = differences?.return_periods?.shared ?? [];

  return (
    <Disclosure
      summary={`${comparison.label} — ${comparison.baseline_detail?.label ?? "baseline"} against ${
        comparison.candidate_detail?.label ?? "candidate"
      }`}
    >
      {differences?.run_modes?.warning ? (
        <Notice tone="warning" title="These runs were made for different purposes">
          {differences.run_modes.warning}
        </Notice>
      ) : null}

      {differences?.decision_use?.warning ? (
        <Notice tone="warning" title="Not a decision number">
          {differences.decision_use.warning}
        </Notice>
      ) : null}

      <div className="compare__metrics">
        {(differences?.metrics ?? []).map((metric) => (
          <ChangeTile key={metric.metric} metric={metric} currency={differences.currency} />
        ))}
      </div>

      {shared.length > 0 ? (
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Return period (years)</th>
              <th scope="col" className="numeric">
                Baseline
              </th>
              <th scope="col" className="numeric">
                Candidate
              </th>
              <th scope="col" className="numeric">
                Change
              </th>
            </tr>
          </thead>
          <tbody>
            {shared.map((row) => (
              <ReturnPeriodRow key={row.return_period} row={row} />
            ))}
          </tbody>
        </table>
      ) : null}

      {differences?.return_periods?.only_in_baseline?.length ||
      differences?.return_periods?.only_in_candidate?.length ? (
        <Notice tone="info" title="Return periods only one side reports">
          {/* Treated as absent rather than zero: zero would draw a cliff where
              the truth is that nobody asked the question at that period. */}
          {differences.return_periods.only_in_baseline.length
            ? `Baseline only: ${differences.return_periods.only_in_baseline.join(", ")}. `
            : ""}
          {differences.return_periods.only_in_candidate.length
            ? `Candidate only: ${differences.return_periods.only_in_candidate.join(", ")}.`
            : ""}
        </Notice>
      ) : null}

      <h4 className="compare__heading">What accounts for the change</h4>
      {differences?.unexplained ? (
        <Notice tone="warning" title="Nothing recorded explains this">
          {differences.unexplained_note}
        </Notice>
      ) : (
        <ul className="compare__drivers">
          {(differences?.drivers ?? []).map((driver) => (
            <li key={driver.driver} className="compare__driver">
              <span className="compare__driver-name">{driver.label}</span>
              <span className="mono">
                {driver.baseline || "not recorded"} → {driver.candidate || "not recorded"}
              </span>
              <span className="muted">{driver.note}</span>
            </li>
          ))}
        </ul>
      )}
    </Disclosure>
  );
}

/** One changed quantity. The sign is carried by wording, not only by colour. */
function ChangeTile({ metric, currency }: { metric: ComparedMetric; currency: string }) {
  return (
    <MetricTile
      label={metric.label}
      value={metric.change ? formatMoney(metric.change) : "—"}
      unit={currency}
      footnote={
        metric.relative_change
          ? `${formatPercent(Number(metric.relative_change))} ${metric.direction}`
          : metric.direction === "unchanged"
            ? "unchanged"
            : "no proportion: the baseline was zero"
      }
    />
  );
}

function ReturnPeriodRow({ row }: { row: ComparedReturnPeriod }) {
  return (
    <tr>
      <th scope="row" className="numeric">
        {row.return_period}
      </th>
      <td className="numeric">{formatMoneyExact(row.baseline)}</td>
      <td className="numeric">{formatMoneyExact(row.candidate)}</td>
      <td className="numeric">
        {row.change ? formatMoneyExact(row.change) : "—"}
        {row.relative_change ? (
          <span className="muted"> ({formatPercent(Number(row.relative_change))})</span>
        ) : null}
      </td>
    </tr>
  );
}

function ResultCard({ result }: { result: ResultSet }) {
  const returnPeriods = Object.entries(result.return_period_losses ?? {});
  const { data: session } = useSession();
  const approve = useApproveResult();
  const exportPackage = useExportResult();

  const mayApprove = session?.user?.capabilities.approve_gates ?? false;
  const refusal = (approve.error ?? exportPackage.error) as ApiError | null;

  /**
   * Take the auditable package away from the platform.
   *
   * The manifest travels with the metrics rather than beside them, because
   * section 12 requires a result to be traceable to immutable exposure, model,
   * engine, converter and settings versions, and a number saved without them
   * is the thing the requirement exists to prevent.
   */
  async function download() {
    try {
      const payload = await exportPackage.mutateAsync(result.id);
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json",
      });
      saveBlob(blob, `${result.label.replace(/\s+/g, "-").toLowerCase()}-package.json`);
    } catch {
      // Rendered as a refusal above rather than thrown at the console.
    }
  }

  return (
    <Card
      actions={
        <>
          <Button
            variant="secondary"
            size="sm"
            onClick={download}
            busy={exportPackage.isPending}
            title="Download the metrics with the run manifest that makes them traceable."
          >
            Export package
          </Button>
          {mayApprove && !result.usable_for_decisions && result.state !== "research" ? (
            <Button
              variant="primary"
              size="sm"
              onClick={() => approve.mutate(result.id)}
              busy={approve.isPending}
              title="Release this result for decision use. It is frozen at that point."
            >
              Approve for decision use
            </Button>
          ) : null}
        </>
      }
      title={
        <span className="results__title">
          {result.label}
          {result.usable_for_decisions ? (
            <StatusBadge tone="ok" detail="Approved for decision use.">
              Approved
            </StatusBadge>
          ) : (
            <StatusBadge
              tone="warning"
              detail="Not approved. This number must not be used for a decision."
            >
              {result.state === "research" ? "Research only" : "Awaiting review"}
            </StatusBadge>
          )}
        </span>
      }
      description={result.caveats.perspective}
    >
      {refusal ? (
        <Notice tone="error" title="Refused">
          {refusal.message}
        </Notice>
      ) : null}

      {!result.usable_for_decisions ? (
        <Notice tone="warning" title="Not approved for decision use">
          <p>
            This result may be inspected and compared, but it must not inform pricing,
            capital or underwriting decisions until it is approved.
          </p>
          {result.state === "research" ? (
            <p>
              It came from a research prototype, so approval is not the missing step.
              A decision number needs a model version published as a full country model.
            </p>
          ) : mayApprove ? null : (
            <p className="muted">Approval is a reviewer action.</p>
          )}
        </Notice>
      ) : (
        <Notice tone="ok" title="Approved for decision use">
          Approved {formatDateTime(result.approved_at)}. The numbers are frozen: a
          further run produces a new result rather than changing this one.
        </Notice>
      )}

      <div className="results__metrics">
        <MetricTile
          label="Average annual loss"
          value={formatMoney(result.average_annual_loss)}
          unit={result.currency}
          footnote={
            result.average_annual_loss
              ? formatMoneyExact(result.average_annual_loss)
              : undefined
          }
        />
        <MetricTile
          label="Standard deviation"
          value={formatMoney(result.standard_deviation)}
          unit={result.currency}
        />
        {returnPeriods.slice(0, 2).map(([period, loss]) => (
          <MetricTile
            key={period}
            label={`${period}-year return period`}
            value={formatMoney(loss)}
            unit={result.currency}
          />
        ))}
      </div>

      {returnPeriods.length > 2 ? (
        <Disclosure summary="Full exceedance probability table">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Return period (years)</th>
                <th scope="col" className="numeric">
                  Loss ({result.currency})
                </th>
              </tr>
            </thead>
            <tbody>
              {returnPeriods.map(([period, loss]) => (
                <tr key={period}>
                  <th scope="row" className="numeric">
                    {period}
                  </th>
                  <td className="numeric">{formatMoneyExact(loss)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Disclosure>
      ) : null}

      <CaveatBlock result={result} />
    </Card>
  );
}

/** The block section 9 requires beside every decision metric. */
function CaveatBlock({ result }: { result: ResultSet }) {
  const caveats = result.caveats;
  const exclusions = caveats.material_exclusions ?? [];
  const uncertainty = Object.entries(caveats.uncertainty_attribution ?? {});
  // Section 8: where the book was converted, the rate belongs beside the
  // number rather than only in the run that produced it.
  const conversion = (caveats.exposure_quality as ExposureQuality | undefined)
    ?.currency_conversion;

  return (
    <div className="caveats">
      <h3 className="caveats__heading">What this number rests on</h3>
      <dl className="caveats__facts">
        <Caveat term="Model version">
          <span className="mono">{caveats.model_version || "not recorded"}</span>
        </Caveat>
        <Caveat term="Assumption set">
          <span className="mono">{caveats.assumption_set || "not recorded"}</span>
        </Caveat>
        <Caveat term="Run mode">{RUN_MODE_LABELS[caveats.run_mode] ?? "not recorded"}</Caveat>
        <Caveat term="Valuation date">{formatDate(caveats.valuation_date)}</Caveat>
        <Caveat term="Perspective">{caveats.perspective}</Caveat>
        <Caveat term="Currency">{caveats.currency || "not recorded"}</Caveat>
        {conversion?.direction ? (
          <Caveat term="Converted at">
            <span className="mono">{conversion.direction}</span>, {conversion.source} as at{" "}
            {formatDate(conversion.valuation_date)}
          </Caveat>
        ) : null}
        <Caveat term="Approval">{caveats.approval_status}</Caveat>
      </dl>

      {exclusions.length > 0 ? (
        <Notice tone="warning" title="Not included in this number">
          {exclusions.join(", ")}
        </Notice>
      ) : (
        <Notice tone="info" title="No material exclusions recorded">
          Confirm the peril scope statement before treating this as complete earthquake
          loss.
        </Notice>
      )}

      {uncertainty.length > 0 ? (
        <Disclosure summary="Where the uncertainty comes from">
          <ul className="caveats__uncertainty">
            {uncertainty.map(([source, contribution]) => (
              <li key={source}>
                <span className="caveats__source">{source.replace(/_/g, " ")}</span>
                <span className="numeric">{String(contribution)}</span>
              </li>
            ))}
          </ul>
        </Disclosure>
      ) : null}
    </div>
  );
}

function Caveat({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="caveats__fact">
      <dt>{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}
