/**
 * Administration.
 *
 * Section 3: "Operate the service -- users, roles, engine health, queues,
 * storage, retention and audit search." Section 11 adds the support bundle an
 * approved local installation needs: versions and health, without portfolio
 * contents.
 *
 * Engine health is a live probe rather than a configured URL, because the
 * question an operator is actually asking is not "what did we point this at"
 * but "is it there, and is it a version we have tested against". Section 18 is
 * the reason the second half matters: an engine answering on an untested
 * version is a compatibility problem to see before submitting a run, not after
 * one produces a result nobody can defend.
 *
 * The audit search is here rather than on each screen for the same reason the
 * table is append-only: an auditor follows a correlation id across projects,
 * runs and model versions, and a trail split across the screens that wrote it
 * is not a trail.
 */

import { useState } from "react";

import { ApiError } from "@/api/client";

import {
  useAuditEvents,
  useDataStandards,
  useDownloadSupportBundle,
  useEngineStatus,
  usePlatformInfo,
  useProjects,
  useSession,
  useSupportBundle,
  useUpdateUser,
  useUsers,
} from "@/api/hooks";
import type { AuditEvent, EngineStatus, User } from "@/api/types";
import { StatusBadge } from "@/components/StatusBadge";
import {
  Button,
  Card,
  Combobox,
  EmptyState,
  Field,
  Notice,
  PageHeader,
  Select,
  Spinner,
  TextInput,
} from "@/components/primitives";
import { formatBytes, formatCount, formatDateTime } from "@/lib/format";

import "./Administration.css";

const ROLE_LABELS: Record<string, string> = {
  analyst: "Portfolio analyst",
  modeller: "Catastrophe modeller",
  underwriter: "Underwriter",
  reviewer: "Reviewer",
  admin: "Platform administrator",
};

/** The governed actions, as the audit table records them. */
const AUDIT_ACTIONS: { value: string; label: string }[] = [
  { value: "", label: "Every action" },
  { value: "create", label: "Created" },
  { value: "update", label: "Updated" },
  { value: "publish", label: "Published" },
  { value: "submit", label: "Submitted" },
  { value: "cancel", label: "Cancelled" },
  { value: "retry", label: "Retried" },
  { value: "approve", label: "Approved" },
  { value: "reject", label: "Rejected" },
  { value: "override", label: "Overrode a value" },
  { value: "download", label: "Downloaded" },
  { value: "upload", label: "Uploaded" },
  { value: "sign_in", label: "Signed in" },
  { value: "sign_in_failed", label: "Sign-in failed" },
];

export function Administration() {
  const { data: platform, isLoading } = usePlatformInfo();
  const { data: session } = useSession();

  const isAdmin = session?.user?.capabilities.administer_platform ?? false;

  if (isLoading) return <Spinner label="Loading platform information" />;

  return (
    <>
      <PageHeader
        title="Administration"
        description="Versions, engine health, roles and the audit trail for this installation."
      />

      {!isAdmin ? (
        <Notice tone="info" title="Read-only view">
          User and role changes are made by a platform administrator. The version and
          health information below is available to everyone so it can be quoted in a
          support request.
        </Notice>
      ) : null}

      <EngineHealth />

      <DataStandards />

      {isAdmin ? <Operations /> : null}

      <div className="admin-grid">
        <Card title="This installation">
          <dl className="admin-facts">
            <AdminFact term="API version">{platform?.api_version ?? "—"}</AdminFact>
            <AdminFact term="OED schema">{platform?.oed_schema_version ?? "—"}</AdminFact>
            <AdminFact term="Artifact backend">
              {platform?.artifact_backend ?? "—"}
            </AdminFact>
            <AdminFact term="Default resource profile">
              {platform?.default_execution_profile ?? "—"}
            </AdminFact>
            {/* Said once, here, rather than on every model and result screen:
                it is a fact about this installation and it does not change
                between uploads. */}
            <AdminFact term="Use of model data">
              Internal to Klapton Re. The models and data CASS carries are used
              inside the company: not redistributed outside it and not sold.
            </AdminFact>
          </dl>
        </Card>

        <Card
          title="Tested engine combinations"
          description="Promotion requires contract and regression suites to pass against a combination listed here."
          padded={false}
        >
          {platform?.compatibility_matrix?.length ? (
            <table className="data-table">
              <thead>
                <tr>
                  {Object.keys(platform.compatibility_matrix[0] ?? {}).map((column) => (
                    <th key={column} scope="col">
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {platform.compatibility_matrix.map((entry, index) => (
                  <tr key={index}>
                    {Object.values(entry).map((value, cell) => (
                      <td key={cell} className="mono">
                        {value}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">No combinations are recorded.</p>
          )}
        </Card>
      </div>

      <Card
        title="Execution profiles"
        description="Declared limits, so no run inherits maximum parallelism and exhausts the host."
        padded={false}
      >
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Profile</th>
              <th scope="col" className="numeric">
                CPU
              </th>
              <th scope="col" className="numeric">
                Memory
              </th>
              <th scope="col" className="numeric">
                Timeout
              </th>
              <th scope="col" className="numeric">
                Concurrent
              </th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(platform?.execution_profiles ?? {}).map(([name, profile]) => (
              <tr key={name}>
                <th scope="row">{name}</th>
                <td className="numeric">{profile.cpu}</td>
                <td className="numeric">{profile.memory_gb} GB</td>
                <td className="numeric">{Math.round(profile.timeout_seconds / 3600)} h</td>
                <td className="numeric">{profile.max_concurrent}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Directory />
      <AuditSearch />
    </>
  );
}

/**
 * Whether each engine is reachable, and on a version that has been tested.
 *
 * An engine with no adapter is reported as such rather than omitted: a missing
 * row reads as "nothing to see", which is the wrong thing to tell an operator
 * about a service the deployment is running.
 */
/**
 * Queues, storage and retention: what the installation is doing with its room.
 *
 * Section 3 puts queues, storage and retention on this screen and section 11
 * adds the support bundle. The numbers come from the bundle, read as a read;
 * taking the bundle away is a separate button, audited as a download.
 */
function Operations() {
  const { data: bundle, isLoading, error } = useSupportBundle(true);
  const download = useDownloadSupportBundle();
  const refusal = (error ?? download.error) as ApiError | null;

  return (
    <Card
      title="Queues, storage and retention"
      description="What each resource profile is running, what the store holds, and what the retention sweep removes next."
    >
      {refusal ? (
        <Notice tone="error" title="The installation could not be read">
          {refusal.message}
        </Notice>
      ) : null}
      {isLoading ? <Spinner label="Reading the installation" /> : null}
      {bundle ? (
        <>
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Profile</th>
                <th scope="col" className="numeric">
                  Running
                </th>
                <th scope="col" className="numeric">
                  Admits at once
                </th>
              </tr>
            </thead>
            <tbody>
              {bundle.capacity.map((item) => (
                <tr key={item.profile}>
                  <th scope="row" className="mono">
                    {item.profile}
                  </th>
                  <td className="numeric">{formatCount(item.running)}</td>
                  <td className="numeric">
                    {formatCount(item.max_concurrent)}
                    {item.is_full ? " (full)" : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Retention class</th>
                <th scope="col">State</th>
                <th scope="col" className="numeric">
                  Artifacts
                </th>
                <th scope="col" className="numeric">
                  Size
                </th>
              </tr>
            </thead>
            <tbody>
              {bundle.artifacts.map((item) => (
                <tr key={`${item.retention}-${item.state}`}>
                  <th scope="row">{item.retention}</th>
                  <td>{item.state}</td>
                  <td className="numeric">{formatCount(item.count)}</td>
                  <td className="numeric">{formatBytes(item.bytes)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <ul className="admin-retention">
            <li>{formatCount(bundle.retention.due_now)} due to expire</li>
            <li>{formatCount(bundle.retention.kept_as_evidence)} kept as evidence</li>
            <li>{formatCount(bundle.retention.abandoned_uploads)} abandoned uploads</li>
          </ul>

          <Button
            variant="ghost"
            busy={download.isPending}
            onClick={() => download.mutate()}
            title="Versions, health, capacity and counts. No secrets and nothing from inside a portfolio."
          >
            Download support bundle
          </Button>
        </>
      ) : null}
    </Card>
  );
}

/**
 * Which version of the exposure standard this installation reads against.
 *
 * Section 17 pins the OED version rather than following whatever the installed
 * library ships, and section 8 makes moving to OED 5 a decision taken against
 * a field-level comparison. What this shows is the pin and, where the registry
 * and the validator disagree, that they do -- because a registry nobody can
 * trust is worse than none.
 */
function DataStandards() {
  const { data: standards, error } = useDataStandards();

  // A reader without the modeller role is refused the registry, which is a
  // permission rather than a fault: the rest of the screen still stands.
  if (error || !standards?.length) return null;

  return (
    <Card
      title="Exposure data standards"
      description="The version CASS validates and publishes exposure against, and the ones registered beside it."
      padded={false}
    >
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">Standard</th>
            <th scope="col">State</th>
            <th scope="col" className="numeric">
              Fields
            </th>
            <th scope="col">Source</th>
          </tr>
        </thead>
        <tbody>
          {standards.map((standard) => (
            <tr key={standard.id}>
              <th scope="row" className="mono">
                {standard.standard} {standard.version}
              </th>
              <td>
                <StatusBadge
                  tone={standard.is_active ? "ok" : "idle"}
                  size="sm"
                  detail={
                    standard.is_active
                      ? "Exposure is validated and published against this version."
                      : "Registered and comparable, but not what exposure is read against."
                  }
                >
                  {standard.state}
                </StatusBadge>
                {standard.is_active && !standard.matches_the_reader ? (
                  <StatusBadge
                    tone="error"
                    size="sm"
                    detail="The registry and the validator name different versions."
                  >
                    disagrees with the reader
                  </StatusBadge>
                ) : null}
              </td>
              <td className="numeric">{formatCount(standard.field_count)}</td>
              <td>{standard.source}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function EngineHealth() {
  const { data: engines, isLoading, error } = useEngineStatus();

  const rows = Object.entries(engines ?? {});
  const unreachable = rows.filter(([, engine]) => engine.reachable === false);
  const untested = rows.filter(
    ([, engine]) => engine.reachable === true && engine.compatible === false,
  );

  return (
    <Card
      title="Engine health"
      description="Probed from the CASS API, which is the only thing that reaches an engine. The browser never calls one directly."
      padded={false}
    >
      {error ? (
        <div className="admin-engines__message">
          <Notice tone="warning" title="Health could not be read">
            The CASS API did not answer the engine probe. The endpoints below are what
            this installation is configured to reach.
          </Notice>
        </div>
      ) : null}

      {untested.length > 0 ? (
        <div className="admin-engines__message">
          <Notice tone="warning" title="An engine is answering on an untested version">
            {untested.map(([name]) => name).join(", ")}. A run against an untested
            combination produces a result that cannot be defended, so submit nothing
            until the compatibility matrix is updated or the engine is rolled back.
          </Notice>
        </div>
      ) : null}

      {unreachable.length > 0 ? (
        <div className="admin-engines__message">
          <Notice tone="error" title="An engine is not reachable">
            {unreachable.map(([name]) => name).join(", ")}. Work that needs it will
            queue rather than fail, and will proceed once it returns.
          </Notice>
        </div>
      ) : null}

      {isLoading ? (
        <div className="admin-engines__message">
          <Spinner label="Probing the engines" />
        </div>
      ) : rows.length > 0 ? (
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Engine</th>
              <th scope="col">Reachable</th>
              <th scope="col">Version</th>
              <th scope="col">Tested combination</th>
              <th scope="col">Endpoint</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([name, engine]) => (
              <EngineRow key={name} name={name} engine={engine} />
            ))}
          </tbody>
        </table>
      ) : (
        <div className="admin-engines__message">
          <EmptyState title="No engines are configured for this installation" />
        </div>
      )}
    </Card>
  );
}

function EngineRow({ name, engine }: { name: string; engine: EngineStatus }) {
  return (
    <tr>
      <th scope="row">
        {engine.engine || name}
        {engine.adapter ? (
          <span className="muted admin-engines__adapter"> {engine.adapter}</span>
        ) : null}
      </th>
      <td>
        <StatusBadge
          tone={
            engine.reachable === true ? "ok" : engine.reachable === false ? "error" : "idle"
          }
          size="sm"
          detail={engine.detail}
        >
          {/* Unknown is not the same as down, and an operator must not read one
              as the other: an engine with no adapter has not been asked. */}
          {engine.reachable === true
            ? "reachable"
            : engine.reachable === false
              ? "not reachable"
              : "not probed"}
        </StatusBadge>
      </td>
      <td className="mono">{engine.version || "—"}</td>
      <td>
        <StatusBadge
          tone={
            engine.compatible === true
              ? "ok"
              : engine.compatible === false
                ? "warning"
                : "idle"
          }
          size="sm"
        >
          {engine.compatible === true
            ? "tested"
            : engine.compatible === false
              ? "untested"
              : "unknown"}
        </StatusBadge>
      </td>
      <td className="mono admin-engines__url">{engine.url}</td>
    </tr>
  );
}

/**
 * Who has which platform role. Membership of a project is set on the project.
 *
 * An administrator changes a role or deactivates somebody here. The API refuses
 * the changes that would lock the installation out -- an administrator
 * removing their own administration, or the last active one going -- and the
 * refusal is shown as the API wrote it rather than predicted in the browser.
 */
function Directory() {
  const { data: users, isLoading } = useUsers();
  const { data: session } = useSession();
  const update = useUpdateUser();
  const mayAdminister = session?.user?.capabilities.administer_platform ?? false;
  const refusal = update.error as ApiError | null;

  return (
    <Card
      title="Users and roles"
      description="A platform role decides what somebody may do anywhere; project membership decides where."
      padded={false}
    >
      {refusal ? (
        <Notice tone="error" title="The change was refused">
          {refusal.message}
        </Notice>
      ) : null}
      {isLoading ? (
        <div className="admin-engines__message">
          <Spinner label="Loading the directory" />
        </div>
      ) : users && users.length > 0 ? (
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Person</th>
              <th scope="col">Platform role</th>
              <th scope="col">May publish models</th>
              <th scope="col">May decide gates</th>
              <th scope="col">Local install</th>
              {mayAdminister ? <th scope="col">Access</th> : null}
            </tr>
          </thead>
          <tbody>
            {users.map((user) => {
              const name = user.full_name || user.username;
              const active = user.is_active !== false;
              return (
                <tr key={user.id}>
                  <th scope="row">
                    {name}
                    {user.job_title ? (
                      <span className="muted"> · {user.job_title}</span>
                    ) : null}
                  </th>
                  <td>
                    {mayAdminister ? (
                      <Select
                        aria-label={`Platform role for ${name}`}
                        value={user.platform_role}
                        disabled={update.isPending}
                        onChange={(event) =>
                          update.mutate({
                            id: user.id,
                            changes: { platform_role: event.target.value as User["platform_role"] },
                          })
                        }
                      >
                        {Object.entries(ROLE_LABELS).map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </Select>
                    ) : (
                      (ROLE_LABELS[user.platform_role] ?? user.platform_role)
                    )}
                  </td>
                  <td>
                    <Permitted allowed={user.capabilities.publish_models} />
                  </td>
                  <td>
                    <Permitted allowed={user.capabilities.approve_gates} />
                  </td>
                  <td>
                    <StatusBadge
                      tone={user.local_install_approved ? "ok" : "idle"}
                      size="sm"
                      detail={
                        user.local_install_approved
                          ? "May run an approved local installation."
                          : "Uses the hosted installation only."
                      }
                    >
                      {user.local_install_approved ? "approved" : "hosted only"}
                    </StatusBadge>
                  </td>
                  {mayAdminister ? (
                    <td>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={update.isPending}
                        onClick={() =>
                          update.mutate({ id: user.id, changes: { is_active: !active } })
                        }
                        title={
                          active
                            ? "Stop this person signing in. Their history stays."
                            : "Let this person sign in again."
                        }
                      >
                        {active ? "Deactivate" : "Reactivate"}
                      </Button>
                    </td>
                  ) : null}
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <div className="admin-engines__message">
          <EmptyState title="No users are listed" />
        </div>
      )}
    </Card>
  );
}

function Permitted({ allowed }: { allowed: boolean }) {
  return (
    <StatusBadge tone={allowed ? "ok" : "idle"} size="sm">
      {allowed ? "yes" : "no"}
    </StatusBadge>
  );
}

/**
 * Audit search.
 *
 * The correlation id is offered as its own field because it is the one an
 * incident actually starts from: a person asks what else happened under the
 * request that produced this run, and every event it touched carries it.
 */
function AuditSearch() {
  const { data: projects } = useProjects();
  const [action, setAction] = useState("");
  const [project, setProject] = useState("");
  const [correlationId, setCorrelationId] = useState("");

  const { data: events, isLoading } = useAuditEvents({
    action: action || undefined,
    project: project || undefined,
    correlation_id: correlationId.trim() || undefined,
  });

  return (
    <Card
      title="Audit trail"
      description="Append-only. Every governed action, who took it and what it touched."
      padded={false}
    >
      <div className="admin-audit__filters form-row">
        <Field label="Action" htmlFor="audit-action">
          <Combobox
            id="audit-action"
            value={action}
            onChange={setAction}
            options={AUDIT_ACTIONS}
          />
        </Field>

        <Field label="Project" htmlFor="audit-project">
          <Combobox
            id="audit-project"
            value={project}
            onChange={setProject}
            options={[
              { value: "", label: "Every project" },
              ...(projects ?? []).map((item) => ({ value: item.id, label: item.name })),
            ]}
          />
        </Field>

        <Field
          label="Correlation id"
          htmlFor="audit-correlation"
          hint="Everything that happened under one request."
        >
          <TextInput
            id="audit-correlation"
            value={correlationId}
            onChange={(event) => setCorrelationId(event.target.value)}
            placeholder="Paste from a run"
          />
        </Field>
      </div>

      {isLoading ? (
        <div className="admin-engines__message">
          <Spinner label="Searching the trail" />
        </div>
      ) : events && events.length > 0 ? (
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">When</th>
              <th scope="col">Who</th>
              <th scope="col">Action</th>
              <th scope="col">Subject</th>
              <th scope="col">Detail</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <AuditRow key={event.id} event={event} />
            ))}
          </tbody>
        </table>
      ) : (
        <div className="admin-engines__message">
          <EmptyState
            title="Nothing matches"
            description="No governed action matches these filters. An empty trail under a correlation id usually means the id came from somewhere other than a run."
          />
        </div>
      )}
    </Card>
  );
}

function AuditRow({ event }: { event: AuditEvent }) {
  return (
    <tr>
      <th scope="row" className="muted">
        {formatDateTime(event.created_at)}
      </th>
      <td>{event.actor_label || "the platform"}</td>
      <td>
        <StatusBadge
          tone={
            event.action === "reject" || event.action === "sign_in_failed"
              ? "error"
              : event.action === "approve" || event.action === "publish"
                ? "ok"
                : "idle"
          }
          size="sm"
        >
          {event.action.replace(/_/g, " ")}
        </StatusBadge>
      </td>
      <td>
        {event.subject_label || event.subject_type}
        <span className="muted admin-audit__subject-type"> {event.subject_type}</span>
      </td>
      <td className="muted">{event.detail || "—"}</td>
    </tr>
  );
}

function AdminFact({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="admin-facts__item">
      <dt>{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}
