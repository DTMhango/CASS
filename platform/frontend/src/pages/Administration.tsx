/**
 * Administration.
 *
 * Section 3: "Operate the service -- users, roles, engine health, queues,
 * storage, retention and audit search." Section 11 adds the support bundle an
 * approved local installation needs: versions and health, without portfolio
 * contents.
 *
 * The version and compatibility information here is the metadata half of that
 * bundle, and it is what a local user is asked for first when something is
 * wrong.
 */

import { usePlatformInfo, useSession } from "@/api/hooks";
import { StatusBadge } from "@/components/StatusBadge";
import { Card, Notice, PageHeader, Spinner } from "@/components/primitives";

import "./Administration.css";

export function Administration() {
  const { data: platform, isLoading } = usePlatformInfo();
  const { data: session } = useSession();

  const isAdmin = session?.user?.capabilities.administer_platform ?? false;

  if (isLoading) return <Spinner label="Loading platform information" />;

  return (
    <>
      <PageHeader
        title="Administration"
        description="Versions, execution profiles and engine endpoints for this installation."
      />

      {!isAdmin ? (
        <Notice tone="info" title="Read-only view">
          User, queue and retention management is restricted to platform administrators.
          The version information below is available to everyone so it can be quoted in a
          support request.
        </Notice>
      ) : null}

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
          </dl>
        </Card>

        <Card
          title="Tested engine combinations"
          description="Promotion requires contract and regression suites to pass against a combination listed here."
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

      <Card
        title="Engine endpoints"
        description="The KRE API reaches these. The browser never calls them directly."
      >
        <dl className="admin-facts">
          {Object.entries(platform?.engines ?? {}).map(([name, url]) => (
            <AdminFact key={name} term={name}>
              <span className="mono">{url}</span>
              <StatusBadge
                tone="idle"
                size="sm"
                detail="Liveness reporting arrives with the engine adapters."
              >
                not probed
              </StatusBadge>
            </AdminFact>
          ))}
        </dl>
      </Card>
    </>
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
