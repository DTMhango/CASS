/**
 * The model build workspace.
 *
 * Section 3: "Manage scientific assets -- hazard runs, converter QA,
 * vulnerability sets, model packages and approval gates." Section 10 lists the
 * seven governance gates and their named approvers.
 *
 * Most of the engine work behind this screen is phases 3 and 4 of the
 * roadmap. What it can show today, and what a modeller needs first, is the
 * decision state: which open questions from section 16 still gate the
 * converter, and which gates a candidate must pass.
 */

import { useRuns } from "@/api/hooks";
import { RunStateBadge, StatusBadge } from "@/components/StatusBadge";
import { Card, EmptyState, PageHeader } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";

import "./ModelBuild.css";

/** The governance gates of build plan section 10, with their approvers. */
const GATES = [
  {
    gate: "Hazard candidate",
    evidence: "Source, GMM, logic tree, site model, grid and benchmark report",
    approver: "Earthquake hazard reviewer",
  },
  {
    gate: "Exposure-enrichment candidate",
    evidence:
      "Missingness audit, evidence hierarchy, conditional priors, reconciliation, scenarios and review results",
    approver: "Exposure data owner and catastrophe model owner",
  },
  {
    gate: "Converter candidate",
    evidence: "Compatibility, lineage, frequency, discretisation and regression evidence",
    approver: "Model engineering reviewer",
  },
  {
    gate: "Vulnerability candidate",
    evidence: "Provenance, taxonomy mapping, uncertainty and sensitivity evidence",
    approver: "Vulnerability reviewer",
  },
  {
    gate: "Data-rights gate",
    evidence:
      "Licence register, permitted uses, attribution, redistribution and local-package conditions",
    approver: "KRE legal or authorised licence owner",
  },
  {
    gate: "Model release",
    evidence: "End-to-end loss validation, performance, known limitations and reproducible package",
    approver: "KRE model owner",
  },
  {
    gate: "Platform release",
    evidence: "Security, restore, monitoring, UAT and rollback evidence",
    approver: "Product and technology owners",
  },
];

/** Open decisions from section 16 that gate converter architecture. */
const OPEN_DECISIONS = [
  {
    decision: "Event representation",
    position: "Complete a formal study before converter build",
    matters:
      "Controls annual frequency, uncertainty, correlation and footprint probabilities.",
  },
  {
    decision: "Multi-IMT representation",
    position:
      "Prototype correlated IMT channels, custom ground-up loss and an OpenQuake-loss fallback",
    matters:
      "Determines whether the GEM vulnerability functions can be represented faithfully in Oasis. Converting everything to one common intensity measure is not an accepted default.",
  },
  {
    decision: "Oasis static storage format",
    position: "Select Parquet or binary runtime assets through performance tests",
    matters: "Earthquake footprints may be too large for uncompressed CSV.",
  },
  {
    decision: "Secondary peril scope",
    position:
      "Declare inclusion or exclusion of liquefaction, landslide, tsunami and fire following earthquake by country release",
    matters: "Defines what earthquake loss means and the expected bias from omissions.",
  },
];

export function ModelBuild() {
  const { data: runs } = useRuns();
  const buildRuns = runs?.filter((run) => run.kind !== "analysis") ?? [];

  return (
    <>
      <PageHeader
        title="Model build workspace"
        description="Hazard runs, conversion QA and the governance gates a candidate must clear before it becomes a published model version."
      />

      <Card
        title="Hazard and conversion runs"
        description="Model-build work belongs to a model version rather than a project."
      >
        {buildRuns.length > 0 ? (
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">Kind</th>
                <th scope="col">State</th>
                <th scope="col">Stage</th>
                <th scope="col">Started</th>
              </tr>
            </thead>
            <tbody>
              {buildRuns.map((run) => (
                <tr key={run.id}>
                  <th scope="row">
                    <a href={`/runs/${run.id}`}>{run.label || run.id.slice(0, 8)}</a>
                  </th>
                  <td>{run.kind}</td>
                  <td>
                    <RunStateBadge state={run.state} size="sm" />
                  </td>
                  <td className="muted">{run.stage_label || "—"}</td>
                  <td className="muted">{formatDateTime(run.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyState
            title="No model-build runs yet"
            description="Hazard generation and conversion arrive with the OpenQuake and converter services in phases 3 and 4."
          />
        )}
      </Card>

      <Card
        title="Decisions still open"
        description="From section 16 of the build plan. The converter architecture cannot be approved while these stand."
      >
        <ul className="decision-list">
          {OPEN_DECISIONS.map((item) => (
            <li key={item.decision} className="decision">
              <div className="decision__header">
                <StatusBadge tone="warning" size="sm">
                  open
                </StatusBadge>
                <span className="decision__name">{item.decision}</span>
              </div>
              <p className="decision__position">{item.position}</p>
              <p className="decision__matters muted">{item.matters}</p>
            </li>
          ))}
        </ul>
      </Card>

      <Card
        title="Governance gates"
        description="Each gate needs named evidence and an approver who did not request it."
      >
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Gate</th>
              <th scope="col">Required evidence</th>
              <th scope="col">Approver</th>
            </tr>
          </thead>
          <tbody>
            {GATES.map((gate) => (
              <tr key={gate.gate}>
                <th scope="row">{gate.gate}</th>
                <td>{gate.evidence}</td>
                <td className="muted">{gate.approver}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}
