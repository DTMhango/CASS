/**
 * The model build workspace.
 *
 * Section 3: "Manage scientific assets -- hazard runs, converter QA,
 * vulnerability sets, model packages and approval gates." Section 10 lists the
 * seven governance gates and their named approvers.
 *
 * The gate table is the static half: which evidence each gate needs and who
 * may decide it, which does not change between installations. Beside it are
 * the gates actually open on this one, because a list of what a gate requires
 * is reference material and a list of what is waiting on somebody is work.
 *
 * The open decisions of section 16 stay on the page. They are not a to-do
 * list: they are the reason the converter architecture cannot be approved, and
 * a modeller who cannot see them will keep asking why a candidate is stuck.
 */

import { useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "@/api/client";
import {
  useApprovals,
  useAttachHazardSet,
  useBuildPackage,
  useHazardSets,
  useModelVersions,
  usePublishModelVersion,
  useRequestApproval,
  useRuns,
  useSession,
} from "@/api/hooks";
import type { HazardSet, ModelVersion } from "@/api/types";
import { GateDecision } from "@/components/GateDecision";
import { RunStateBadge, StatusBadge } from "@/components/StatusBadge";
import {
  Button,
  Card,
  EmptyState,
  Notice,
  PageHeader,
  Select,
} from "@/components/primitives";
import { formatCount, formatDate, formatDateTime } from "@/lib/format";

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

/**
 * Open decisions from section 16 that gate converter architecture, as they
 * stand in build plan 1.8. A prototype in use is not a decision taken, so each
 * says what is running meanwhile as well as what is still to be decided.
 */
const OPEN_DECISIONS = [
  {
    decision: "Event representation",
    position:
      "Occurrence per event is implemented, and each package is built under a converter approval. The formal study has not reported.",
    matters:
      "Controls annual frequency, uncertainty, correlation and footprint probabilities.",
  },
  {
    decision: "Multi-IMT representation",
    position:
      "Correlated area-peril channels are implemented for classes that resolve to one intensity measure. Classes that respond at several are refused, and the OpenQuake reference comparison is outstanding.",
    matters:
      "Determines whether the GEM vulnerability functions can be represented faithfully in Oasis. Converting everything to one common intensity measure is not an accepted default.",
  },
  {
    decision: "Realisation weighting",
    position: "Each hazard run samples one logic-tree path until a weighting rule is approved.",
    matters:
      "One path is one view of the hazard, not the model's weighted mean, so hazard uncertainty is understated.",
  },
  {
    decision: "Oasis static storage format",
    position: "Interim: ktools binaries written by CASS. Parquet has not been measured.",
    matters: "Earthquake footprints may be too large for uncompressed CSV.",
  },
  {
    decision: "Secondary peril scope",
    position:
      "Declare inclusion or exclusion of liquefaction, landslide, tsunami and fire following earthquake by country release",
    matters: "Defines what earthquake loss means and the expected bias from omissions.",
  },
];

export function ModelBuild({ embedded = false }: { embedded?: boolean } = {}) {
  const { data: runs } = useRuns();
  const { data: versions } = useModelVersions();
  const buildRuns = runs?.filter((run) => run.kind !== "analysis") ?? [];

  return (
    <>
      {embedded ? null : <PageHeader
        title="Model build workspace"
        description="Hazard runs, conversion QA and the governance gates a candidate must clear before it becomes a published model version."
      />}

      <OpenGates />

      <Card
        title="Model versions"
        description="Every version in the registry, published or not. What is missing from a candidate is stated rather than implied by its absence from the catalogue."
        padded={false}
      >
        {versions && versions.length > 0 ? (
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Version</th>
                <th scope="col">State</th>
                <th scope="col">Validated</th>
                <th scope="col">Outstanding</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {versions.map((version) => (
                <ModelVersionRow key={version.id} version={version} />
              ))}
            </tbody>
          </table>
        ) : (
          <div className="build__empty">
            <EmptyState
              title="No model versions are registered"
              description="A version appears here once its grid, vulnerability set and validation evidence have been registered."
            />
          </div>
        )}
      </Card>

      <HazardSets versions={versions ?? []} />

      <Card
        title="Hazard and conversion runs"
        description="Model-build work belongs to a model version rather than a project."
        padded={false}
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
                    <Link to={`/runs/${run.id}`}>{run.label || run.id.slice(0, 8)}</Link>
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
          <div className="build__empty">
            <EmptyState
              title="No model-build runs yet"
              description="Launch a saved configuration from the Hazard tab, or build a package for a model version above. Both runs appear here once started."
            />
          </div>
        )}
      </Card>

      <Card
        title="Decisions still open"
        description="From section 16 of the build plan. Packages are built under a converter approval while these stand, but the converter architecture cannot be signed off until they close."
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
        padded={false}
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

/**
 * One model version, and the act available on it.
 *
 * Publication is offered where the state allows it. Where blockers stand, the
 * only offer is publication as a research prototype, because section 6 forbids
 * an incomplete package being published as a full country model and a button
 * that would be refused is not a useful thing to present.
 */
function ModelVersionRow({ version }: { version: ModelVersion }) {
  const { data: session } = useSession();
  const publish = usePublishModelVersion();
  const mayPublish = session?.user?.capabilities.publish_models ?? false;

  const blocked = version.publication_blockers.length > 0;
  const published = version.publication_state === "published";
  const error = publish.error as ApiError | null;

  return (
    <tr>
      <th scope="row">
        <span className="mono">{version.reference}</span>
        {version.label ? <span className="muted"> {version.label}</span> : null}
      </th>
      <td>
        <StatusBadge
          tone={version.usable_for_decisions ? "ok" : published ? "warning" : "idle"}
          size="sm"
          detail={
            version.usable_for_decisions
              ? "Approved for decision use."
              : published
                ? "Published as a research prototype: runnable, with its blockers still standing."
                : undefined
          }
        >
          {version.usable_for_decisions
            ? "published"
            : published
              ? "research only"
              : version.publication_state}
        </StatusBadge>
      </td>
      <td className="muted">{formatDate(version.validation_date)}</td>
      <td>
        {blocked ? (
          <ul className="build__blockers">
            {version.publication_blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        ) : (
          <span className="muted">Nothing outstanding.</span>
        )}
        {error ? <p className="build__error">{error.message}</p> : null}
      </td>
      <td className="build__actions">
        <PackageActions version={version} />
        {mayPublish && !published ? (
          <Button
            variant={blocked ? "secondary" : "primary"}
            size="sm"
            busy={publish.isPending}
            onClick={() =>
              publish.mutate({ id: version.id, as_research_prototype: blocked })
            }
            title={
              blocked
                ? "Publish as a research prototype. It may be run, but its output must not be used for decisions."
                : "Publish as a full country model, approved for decision use."
            }
          >
            {blocked ? "Publish as research" : "Publish"}
          </Button>
        ) : null}
      </td>
    </tr>
  );
}

/**
 * Hazard sets, and pointing a model version at one.
 *
 * A model version maps exposure to keys without hazard; it produces a loss only
 * once a hazard set computed on its grid, carrying every measure its functions
 * demand, is attached. The API refuses any other pairing and says why.
 */
function HazardSets({ versions }: { versions: ModelVersion[] }) {
  const { data: sets } = useHazardSets();
  const { data: session } = useSession();
  const attach = useAttachHazardSet();
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const mayPublish = session?.user?.capabilities.publish_models ?? false;
  const error = attach.error as ApiError | null;

  return (
    <Card
      title="Hazard sets"
      description="Event sets and footprints registered from hazard runs. Attach one to a model version to let it produce a loss."
      padded={false}
    >
      {error ? (
        <div className="build__empty">
          <Notice tone="error" title="Not attached">
            {error.message}
          </Notice>
        </div>
      ) : null}
      {sets && sets.length > 0 ? (
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Hazard set</th>
              <th scope="col">Events</th>
              <th scope="col">Cells</th>
              <th scope="col">Measures</th>
              <th scope="col" />
            </tr>
          </thead>
          <tbody>
            {sets.map((item: HazardSet) => {
              const candidates = versions.filter(
                (version) => version.grid === item.grid && version.country_code === item.country_code,
              );
              const target = chosen[item.id] ?? candidates[0]?.id ?? "";
              const attachedTo = versions.filter((version) => version.hazard_set === item.id);
              return (
                <tr key={item.id}>
                  <th scope="row">
                    <span className="mono">{item.reference}</span>
                    <span className="muted"> {item.label}</span>
                  </th>
                  <td className="numeric">
                    {formatCount(item.event_count)}
                    <span className="muted">
                      {" "}
                      / {formatCount(item.investigation_time * item.stochastic_event_sets)} yrs
                    </span>
                  </td>
                  <td className="numeric">{formatCount(item.cell_count)}</td>
                  <td>{item.imts.join(", ")}</td>
                  <td>
                    {attachedTo.length ? (
                      <span className="muted">
                        Attached to {attachedTo.map((version) => version.reference).join(", ")}
                      </span>
                    ) : mayPublish && candidates.length ? (
                      <div className="build__attach">
                        <Select
                          aria-label={`Model version for ${item.reference}`}
                          value={target}
                          onChange={(event) =>
                            setChosen({ ...chosen, [item.id]: event.target.value })
                          }
                        >
                          {candidates.map((version) => (
                            <option key={version.id} value={version.id}>
                              {version.reference}
                            </option>
                          ))}
                        </Select>
                        <Button
                          size="sm"
                          busy={attach.isPending}
                          onClick={() =>
                            attach.mutate({ modelVersion: target, hazardSet: item.id })
                          }
                        >
                          Attach
                        </Button>
                      </div>
                    ) : (
                      <span className="muted">No model version on this grid.</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <div className="build__empty">
          <EmptyState
            title="No hazard sets registered"
            description="A hazard run registers its event set and footprints here when it completes."
          />
        </div>
      )}
    </Card>
  );
}

/**
 * The Oasis package for one model version, and the gate it is built under.
 *
 * The converter has no default for event identity or the multi-IMT
 * representation, so a package is built under a converter-candidate approval:
 * requested here by the modeller, decided by a reviewer in the gates above.
 */
function PackageActions({ version }: { version: ModelVersion }) {
  const { data: session } = useSession();
  const { data: approvals } = useApprovals();
  const request = useRequestApproval();
  const buildPackage = useBuildPackage();
  const mayPublish = session?.user?.capabilities.publish_models ?? false;

  if (!version.hazard_set) {
    return <span className="muted build__package-note">Attach a hazard set to build a package.</span>;
  }

  const gates = (approvals ?? []).filter(
    (item) => item.gate === "converter_candidate" && item.subject_id === version.id,
  );
  const cleared = gates.find((item) => item.is_cleared);
  const open = gates.find((item) => item.is_open);
  const error = (request.error ?? buildPackage.error) as ApiError | null;

  return (
    <div className="build__package">
      {error ? <p className="build__error">{error.message}</p> : null}
      {buildPackage.isSuccess ? (
        <p>
          Building.{" "}
          <Link to={`/runs/${buildPackage.data.run}`}>Follow the conversion run</Link>.
        </p>
      ) : cleared ? (
        mayPublish ? (
          <Button
            size="sm"
            variant="primary"
            busy={buildPackage.isPending}
            onClick={() => buildPackage.mutate({ modelVersion: version.id, approval: cleared.id })}
            title="Convert the hazard and vulnerability into the Oasis model package the engine loads."
          >
            Build Oasis package
          </Button>
        ) : null
      ) : open ? (
        <StatusBadge tone="warning" size="sm" detail="A reviewer decides this gate above.">
          converter gate awaiting a reviewer
        </StatusBadge>
      ) : mayPublish ? (
        <Button
          size="sm"
          variant="secondary"
          busy={request.isPending}
          onClick={() =>
            request.mutate({
              gate: "converter_candidate",
              subject_type: "model_version",
              subject_id: version.id,
              rationale:
                "Convert under occurrence-per-event identity with intensity measures " +
                "carried as area-peril channels; single-channel classes only.",
            })
          }
        >
          Request converter approval
        </Button>
      ) : null}
    </div>
  );
}

/** The gates waiting on somebody on this installation. */
function OpenGates() {
  const { data: approvals, isLoading } = useApprovals();
  const { data: session } = useSession();
  const mayDecide = session?.user?.capabilities.approve_gates ?? false;

  const open = approvals?.filter((item) => item.is_open) ?? [];

  if (isLoading || open.length === 0) return null;

  return (
    <Card
      title={`${open.length} gate(s) waiting on a decision`}
      description="A gate holds work rather than failing it. Nothing has been lost while one stands."
    >
      <ul className="gate-list">
        {open.map((approval) => (
          <GateDecision key={approval.id} approval={approval} mayDecide={mayDecide} />
        ))}
      </ul>
    </Card>
  );
}

