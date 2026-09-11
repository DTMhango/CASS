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
 */

import { useResults } from "@/api/hooks";
import type { ResultSet } from "@/api/types";
import { StatusBadge } from "@/components/StatusBadge";
import {
  Card,
  Disclosure,
  EmptyState,
  MetricTile,
  Notice,
  PageHeader,
} from "@/components/primitives";
import { useWorkingContext } from "@/context/WorkingContext";
import { formatDate, formatMoney, formatMoneyExact } from "@/lib/format";

import "./ResultsWorkspace.css";

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
    </>
  );
}

function ResultCard({ result }: { result: ResultSet }) {
  const returnPeriods = Object.entries(result.return_period_losses ?? {});

  return (
    <Card
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
      {!result.usable_for_decisions ? (
        <Notice tone="warning" title="Not approved for decision use">
          This result may be inspected and compared, but it must not inform pricing,
          capital or underwriting decisions until it is approved.
        </Notice>
      ) : null}

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
        <Caveat term="Valuation date">{formatDate(caveats.valuation_date)}</Caveat>
        <Caveat term="Perspective">{caveats.perspective}</Caveat>
        <Caveat term="Currency">{caveats.currency || "not recorded"}</Caveat>
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
