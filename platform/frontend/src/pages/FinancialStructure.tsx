/**
 * The financial structure workspace.
 *
 * Section 3 asks for accounts and layers, contracts, a scope preview, the
 * inuring order and the reconciliation between them. What it shows is read
 * from the portfolio's own published files rather than entered here, because
 * the structure is part of the exposure version and that version is immutable:
 * a correction produces a new version, exactly as a corrected location does.
 *
 * The screen's job is to make what the treaty does visible before a run rests
 * on it. So the reconciliation is not a tidy green tick. Value no contract
 * reaches is stated in money, a layer with no term is named, and a contract the
 * engine will not apply says so in the row rather than in a footnote --
 * because "reinsurance ran" and "this contract was applied" are different
 * claims, and only the second one is about a treaty.
 */

import { ApiError } from "@/api/client";
import { useExposureVersions, useFinancialStructure } from "@/api/hooks";
import { StatusBadge } from "@/components/StatusBadge";
import {
  Card,
  Disclosure,
  EmptyState,
  Notice,
  PageHeader,
} from "@/components/primitives";
import { useWorkingContext } from "@/context/WorkingContext";
import { formatCount, formatMoney, formatMoneyExact, formatPercent } from "@/lib/format";

import { StructureBuilder } from "./StructureBuilder";

import "./FinancialStructure.css";

/** Shares cross the API as decimal strings, as every other proportion does. */
function share(value: string | null): string {
  return value === null || value === "" ? "—" : formatPercent(Number(value));
}

export function FinancialStructure({ embedded = false }: { embedded?: boolean } = {}) {
  const context = useWorkingContext();
  const { data: exposures } = useExposureVersions(context.projectId);
  const exposure = exposures?.find((item) => item.id === context.exposureId);
  const { data: structure, error, isPending } = useFinancialStructure(exposure?.id);

  if (!exposure) {
    return (
      <>
        {embedded ? null : (
          <PageHeader
            title="Financial structure"
            description="Accounts, layers, contracts and what they reach."
          />
        )}
        <EmptyState
          title="No portfolio is selected"
          description="Choose a portfolio in the context bar to see the structure behind it."
        />
      </>
    );
  }

  const refusal = error as ApiError | null;
  const blocking = (structure?.findings ?? []).filter((item) => item.blocking);
  const advisory = (structure?.findings ?? []).filter((item) => !item.blocking);

  return (
    <>
      {embedded ? null : (
        <PageHeader
          title="Financial structure"
          description="Accounts, layers, contracts and what they reach, read from the published portfolio."
        />
      )}

      {refusal ? (
        <Notice tone="error" title="The structure could not be read">
          {refusal.message}
        </Notice>
      ) : null}

      {isPending || !structure ? null : (
        <div className="structure">
          <Card
            title={`${exposure.name} v${exposure.version}`}
            description={`${formatCount(structure.location_count)} locations, ${formatMoney(
              structure.total_tiv,
            )} ${structure.currency} insured`}
          >
            {blocking.length > 0 ? (
              <Notice
                tone="error"
                title={`${blocking.length} thing(s) stop this structure being used as written`}
              >
                <ul className="structure__findings">
                  {blocking.map((finding) => (
                    <li key={`${finding.code}-${finding.subject}`}>
                      <strong>{finding.subject}</strong> {finding.message}
                    </li>
                  ))}
                </ul>
              </Notice>
            ) : null}

            {advisory.length > 0 ? (
              <Notice tone="warning" title="Worth a decision before a run rests on this">
                <ul className="structure__findings">
                  {advisory.map((finding) => (
                    <li key={`${finding.code}-${finding.subject}`}>
                      <strong>{finding.subject}</strong> {finding.message}
                    </li>
                  ))}
                </ul>
              </Notice>
            ) : null}

            {!structure.has_accounts && !structure.has_contracts ? (
              <Notice tone="info" title="This portfolio carries no financial structure">
                It holds locations only, which supports a ground-up run. Insured loss
                needs an account file and reinsurance needs contracts and scope.
              </Notice>
            ) : null}
          </Card>

          {structure.has_accounts ? (
            <Card
              title="Accounts and layers"
              description="Evaluated in order. A layer's attachment is where its cover begins."
            >
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Account</th>
                    <th scope="col">Policy</th>
                    <th scope="col" className="numeric">
                      Layer
                    </th>
                    <th scope="col" className="numeric">
                      Attachment
                    </th>
                    <th scope="col" className="numeric">
                      Limit
                    </th>
                    <th scope="col" className="numeric">
                      Signed share
                    </th>
                    <th scope="col">Perils</th>
                  </tr>
                </thead>
                <tbody>
                  {structure.layers.map((layer, index) => (
                    <tr key={`${layer.account}-${layer.policy}-${layer.layer_number}-${index}`}>
                      <th scope="row" className="mono">
                        {layer.account}
                      </th>
                      <td className="mono">{layer.policy}</td>
                      <td className="numeric">{layer.layer_number ?? "—"}</td>
                      <td className="numeric">{formatMoney(layer.attachment)}</td>
                      <td className="numeric">
                        {layer.limit ? formatMoney(layer.limit) : "unlimited"}
                      </td>
                      <td className="numeric">{share(layer.participation)}</td>
                      <td>{layer.perils.join(", ") || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          ) : null}

          {structure.has_contracts ? (
            <Card
              title="Reinsurance contracts"
              description="In inuring order: a lower priority inures to the benefit of a higher one."
            >
              {structure.inuring_order.map((step) => (
                <div key={step.priority} className="structure__step">
                  <h3 className="structure__step-heading">
                    Priority {step.priority}
                    {step.contracts.length > 1 ? " — these run together" : ""}
                  </h3>
                  {structure.contracts
                    .filter((contract) => step.contracts.includes(contract.number ?? -1))
                    .map((contract) => (
                      <div key={contract.number} className="structure__contract">
                        <div className="structure__contract-head">
                          <span className="mono">{contract.number}</span>
                          <strong>{contract.name || contract.type_label}</strong>
                          <StatusBadge
                            tone={contract.applied_by_the_engine ? "ok" : "warning"}
                            size="sm"
                            detail={
                              contract.applied_by_the_engine
                                ? "Applied in a reinsurance run."
                                : contract.notes.join(" ")
                            }
                          >
                            {contract.applied_by_the_engine ? "applied" : "not applied"}
                          </StatusBadge>
                        </div>
                        <dl className="structure__terms">
                          <Term label="Type">{contract.type_label}</Term>
                          <Term label="Ceded">{share(contract.ceded_percent)}</Term>
                          <Term label="Placed">{share(contract.placed_percent)}</Term>
                          {contract.occurrence_limit ? (
                            <Term label="Occurrence limit">
                              {formatMoney(contract.occurrence_limit)} xs{" "}
                              {formatMoney(contract.occurrence_attachment)}
                            </Term>
                          ) : null}
                          {contract.risk_limit ? (
                            <Term label="Risk limit">
                              {formatMoney(contract.risk_limit)} xs{" "}
                              {formatMoney(contract.risk_attachment)}
                            </Term>
                          ) : null}
                          <Term label="Reaches">
                            {formatCount(contract.locations_reached)} location(s),{" "}
                            {formatMoney(contract.scope_tiv)} {contract.currency}
                          </Term>
                        </dl>
                        {contract.notes.length > 0 ? (
                          <p className="muted">{contract.notes.join(" ")}</p>
                        ) : null}
                      </div>
                    ))}
                </div>
              ))}

              <Disclosure summary="What the scope reaches, against the whole portfolio">
                <dl className="structure__terms">
                  <Term label="Insured value">
                    {formatMoneyExact(structure.total_tiv)} {structure.currency}
                  </Term>
                  <Term label="Reached by no contract">
                    {formatMoneyExact(structure.uncovered_tiv)} {structure.currency} across{" "}
                    {formatCount(structure.uncovered_locations)} location(s)
                  </Term>
                </dl>
                <p className="muted">
                  Value no contract reaches is retained in full. That is a fact about the
                  book rather than a fault, but a ceded number read without it is the
                  wrong number.
                </p>
              </Disclosure>
            </Card>
          ) : null}

          <StructureBuilder exposure={exposure} structure={structure} />
        </div>
      )}
    </>
  );
}

function Term({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="structure__term">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
