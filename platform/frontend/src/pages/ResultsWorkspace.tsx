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
  useEventLosses,
  useGeographicSummary,
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
  formatCount,
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
            This result may be inspected and compared, but no reviewer has approved it.
            CASS is a research tool, so even an approved result is not a basis for
            pricing or reserving.
          </p>
          {result.state === "research" ? (
            <p>
              It came from a research prototype, so approval is not the missing step.
              A result that can be approved needs a model version published as a full
              country model.
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

      {returnPeriods.length > 1 ? (
        <ExceedanceChart
          curve={returnPeriods}
          currency={result.currency}
          label={result.label}
        />
      ) : null}

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

      <EventLossTable result={result} />

      <GeographicPanel result={result} />

      <CaveatBlock result={result} />
    </Card>
  );
}

/**
 * The exceedance curve, drawn from the losses the server computed.
 *
 * Nothing is calculated here beyond the pixel positions: every loss is a
 * decimal string from the API, and ADR 5 keeps money arithmetic on the server.
 * The return-period axis is logarithmic because an EP curve read on a linear
 * one is a vertical line at the left and a flat line everywhere else.
 *
 * The table below it remains the accessible reading of the same numbers, so
 * this carries a description rather than trying to be one.
 */
function ExceedanceChart({
  curve,
  currency,
  label,
}: {
  curve: [string, string][];
  currency: string;
  label: string;
}) {
  const points = curve
    .map(([period, loss]) => ({ period: Number(period), loss: Number(loss) }))
    .filter((point) => Number.isFinite(point.period) && Number.isFinite(point.loss))
    .sort((a, b) => a.period - b.period);
  const first = points[0];
  const last = points[points.length - 1];
  if (points.length < 2 || !first || !last) return null;

  const width = 640;
  const height = 220;
  const pad = { top: 16, right: 16, bottom: 34, left: 72 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;

  const minPeriod = Math.log10(first.period);
  const maxPeriod = Math.log10(last.period);
  const maxLoss = Math.max(...points.map((point) => point.loss));
  const span = maxPeriod - minPeriod || 1;

  const x = (period: number) =>
    pad.left + ((Math.log10(period) - minPeriod) / span) * plotWidth;
  const y = (loss: number) =>
    pad.top + plotHeight - (maxLoss ? (loss / maxLoss) * plotHeight : 0);

  const line = points
    .map((point, index) => `${index === 0 ? "M" : "L"}${x(point.period)},${y(point.loss)}`)
    .join(" ");

  return (
    <figure className="ep-chart">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="ep-chart__plot"
        role="img"
        aria-label={`Exceedance probability curve for ${label}, ${points.length} return periods from ${first.period} to ${last.period} years, peaking at ${formatMoney(String(maxLoss))} ${currency}.`}
      >
        <line
          x1={pad.left}
          y1={pad.top + plotHeight}
          x2={pad.left + plotWidth}
          y2={pad.top + plotHeight}
          className="ep-chart__axis"
        />
        <line
          x1={pad.left}
          y1={pad.top}
          x2={pad.left}
          y2={pad.top + plotHeight}
          className="ep-chart__axis"
        />
        <path d={line} className="ep-chart__line" />
        {points.map((point) => (
          <circle
            key={point.period}
            cx={x(point.period)}
            cy={y(point.loss)}
            r={3}
            className="ep-chart__point"
          >
            <title>
              {point.period}-year: {formatMoneyExact(String(point.loss))} {currency}
            </title>
          </circle>
        ))}
        {points.map((point) => (
          <text
            key={`label-${point.period}`}
            x={x(point.period)}
            y={pad.top + plotHeight + 18}
            className="ep-chart__tick"
            textAnchor="middle"
          >
            {point.period}
          </text>
        ))}
        <text x={pad.left - 8} y={pad.top + 6} className="ep-chart__tick" textAnchor="end">
          {formatMoney(String(maxLoss))}
        </text>
        <text
          x={pad.left - 8}
          y={pad.top + plotHeight}
          className="ep-chart__tick"
          textAnchor="end"
        >
          0
        </text>
      </svg>
      <figcaption className="muted">
        Loss ({currency}) against return period (years, logarithmic). The table below
        carries the same numbers exactly.
      </figcaption>
    </figure>
  );
}

/** Which events drive the number, from the moment event loss table. */
function EventLossTable({ result }: { result: ResultSet }) {
  const { data, error, isPending } = useEventLosses(result.id);

  if (isPending) return null;
  if (error || !data?.results?.length) return null;

  return (
    <Disclosure
      summary={`Events behind this number (${formatCount(data.count)} with a loss)`}
    >
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">Event</th>
            <th scope="col" className="numeric">
              Mean loss ({data.currency})
            </th>
            <th scope="col" className="numeric">
              Standard deviation
            </th>
            <th scope="col" className="numeric">
              Largest sampled loss
            </th>
            <th scope="col" className="numeric">
              Annual rate
            </th>
          </tr>
        </thead>
        <tbody>
          {data.results.map((event) => (
            <tr key={event.event_id}>
              <th scope="row" className="mono">
                {event.event_id}
              </th>
              <td className="numeric">{formatMoneyExact(event.mean_loss)}</td>
              <td className="numeric">{formatMoney(event.standard_deviation)}</td>
              <td className="numeric">{formatMoney(event.maximum_loss)}</td>
              <td className="numeric">{event.event_rate ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.count > data.results.length ? (
        <p className="muted">
          The {formatCount(data.results.length)} largest of {formatCount(data.count)}.
        </p>
      ) : null}
    </Disclosure>
  );
}

/**
 * Where the loss is, by area-peril cell.
 *
 * Drawn from what the server placed: each location's average annual loss in
 * the cell the run's keys mapped it to, summed per cell. Longitude runs across
 * and latitude up, and each cell is shaded by its share of the largest cell's
 * loss. It is a plot of the grid rather than a basemap: the cells are what the
 * number was calculated against, and a map service would need a network the
 * platform does not assume. The table under it carries the numbers exactly.
 */
function GeographicPanel({ result }: { result: ResultSet }) {
  const { data, error, isPending } = useGeographicSummary(result.id);

  if (isPending || error || !data?.cells?.length) return null;

  const cells = data.cells.map((cell) => ({
    ...cell,
    west: Number(cell.min_longitude),
    east: Number(cell.max_longitude),
    south: Number(cell.min_latitude),
    north: Number(cell.max_latitude),
    loss: Number(cell.average_annual_loss),
  }));
  const west = Math.min(...cells.map((cell) => cell.west));
  const east = Math.max(...cells.map((cell) => cell.east));
  const south = Math.min(...cells.map((cell) => cell.south));
  const north = Math.max(...cells.map((cell) => cell.north));
  const largest = Math.max(...cells.map((cell) => cell.loss)) || 1;

  const width = 640;
  const spanX = east - west || 1;
  const spanY = north - south || 1;
  const height = Math.max(160, Math.min(480, (width * spanY) / spanX));
  const x = (longitude: number) => ((longitude - west) / spanX) * width;
  const y = (latitude: number) => ((north - latitude) / spanY) * height;

  return (
    <Disclosure summary={`Where the loss is (${formatCount(cells.length)} cells)`}>
      <figure className="ep-chart">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="ep-chart__plot"
          role="img"
          aria-label={`Average annual loss by area-peril cell on ${data.grid}: ${cells.length} cells, the largest ${formatMoney(largest)} ${data.currency}.`}
        >
          {cells.map((cell) => (
            <rect
              key={cell.area_peril_id}
              x={x(cell.west)}
              y={y(cell.north)}
              width={Math.max(x(cell.east) - x(cell.west), 1)}
              height={Math.max(y(cell.south) - y(cell.north), 1)}
              style={{ fill: "currentColor", fillOpacity: 0.15 + 0.85 * (cell.loss / largest) }}
            >
              <title>
                Cell {cell.area_peril_id}: {formatMoneyExact(cell.average_annual_loss)}{" "}
                {data.currency} across {cell.locations} location(s)
              </title>
            </rect>
          ))}
        </svg>
        <figcaption className="muted">
          Average annual loss by cell on {data.grid}, placed by the run&apos;s keys. Darker
          cells carry more of it; the table below carries the same numbers exactly.
        </figcaption>
      </figure>

      {data.unplaced_locations > 0 ? (
        <Notice tone="warning" title="Some loss could not be placed">
          {formatCount(data.unplaced_locations)} location(s) carrying{" "}
          {formatMoney(data.unplaced_loss)} {data.currency} have no cell in the run&apos;s
          keys, so they are missing from the map.
        </Notice>
      ) : null}

      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">Cell</th>
            <th scope="col" className="numeric">
              Locations
            </th>
            <th scope="col" className="numeric">
              Average annual loss ({data.currency})
            </th>
            <th scope="col" className="numeric">
              Insured value
            </th>
          </tr>
        </thead>
        <tbody>
          {data.cells.slice(0, 10).map((cell) => (
            <tr key={cell.area_peril_id}>
              <th scope="row" className="mono">
                {cell.area_peril_id}
              </th>
              <td className="numeric">{formatCount(cell.locations)}</td>
              <td className="numeric">{formatMoneyExact(cell.average_annual_loss)}</td>
              <td className="numeric">{formatMoney(cell.tiv)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.cells.length > 10 ? (
        <p className="muted">
          The 10 largest of {formatCount(data.cells.length)} cells.
        </p>
      ) : null}
    </Disclosure>
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
