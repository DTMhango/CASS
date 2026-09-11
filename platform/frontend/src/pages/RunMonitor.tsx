/**
 * The run monitor.
 *
 * Section 3: "Explain progress and failure -- pipeline stage, elapsed time,
 * logs, warnings, artifacts and retry controls." Section 12 adds that an
 * engine or worker failure must produce an intelligible state and a safe retry
 * or cancellation path.
 *
 * The pipeline is drawn from the stage list the API returns rather than
 * hard-coded here, so the interface cannot drift from the state machine the
 * workers actually follow.
 */

import { useParams } from "react-router-dom";

import { ApiError } from "@/api/client";
import { useCancelRun, useRetryRun, useRun, useRunEvents, useRuns } from "@/api/hooks";
import type { PipelineStage, Run } from "@/api/types";
import { RunStateBadge, StatusBadge } from "@/components/StatusBadge";
import {
  Button,
  Card,
  Disclosure,
  EmptyState,
  Notice,
  PageHeader,
  Spinner,
} from "@/components/primitives";
import { useWorkingContext } from "@/context/WorkingContext";
import { formatDateTime, formatDuration } from "@/lib/format";

import "./RunMonitor.css";

export function RunMonitor() {
  const { runId } = useParams();
  const context = useWorkingContext();
  const { data: runs, isLoading } = useRuns(context.projectId);
  const { data: run } = useRun(runId);

  if (runId && run) return <RunDetail run={run} />;
  if (isLoading) return <Spinner label="Loading runs" />;

  return (
    <>
      <PageHeader
        title="Run monitor"
        description="Every hazard, conversion and analysis run, with the stage it reached and the evidence it produced. Work continues whether or not this page stays open."
      />
      {runs && runs.length > 0 ? (
        <Card padded={false}>
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">Kind</th>
                <th scope="col">State</th>
                <th scope="col">Stage</th>
                <th scope="col">Elapsed</th>
                <th scope="col">Started</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((item) => (
                <tr key={item.id}>
                  <th scope="row">
                    <a href={`/runs/${item.id}`}>{item.label || item.id.slice(0, 8)}</a>
                  </th>
                  <td>{item.kind}</td>
                  <td>
                    <RunStateBadge state={item.state} size="sm" />
                  </td>
                  <td className="muted">{item.stage_label || "—"}</td>
                  <td className="numeric">{formatDuration(item.duration_seconds)}</td>
                  <td className="muted">{formatDateTime(item.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : (
        <Card>
          <EmptyState
            title="No runs yet"
            description="Submit an analysis from the analysis builder to see it here."
          />
        </Card>
      )}
    </>
  );
}

function RunDetail({ run }: { run: Run }) {
  const cancel = useCancelRun(run.id);
  const retry = useRetryRun(run.id);
  const { data: events } = useRunEvents(run.id, run.is_active);

  const cancelError = cancel.error as ApiError | null;
  const retryError = retry.error as ApiError | null;

  return (
    <>
      <PageHeader
        title={run.label || `${run.kind} run`}
        description={
          <span className="mono">
            {run.id} · correlation {run.correlation_id || "not recorded"}
          </span>
        }
        actions={
          <>
            {run.is_active ? (
              <Button variant="danger" onClick={() => cancel.mutate()} busy={cancel.isPending}>
                Cancel
              </Button>
            ) : null}
            {run.may_retry ? (
              <Button variant="primary" onClick={() => retry.mutate()} busy={retry.isPending}>
                Retry
              </Button>
            ) : null}
          </>
        }
      />

      {cancelError ? (
        <Notice tone="error" title="Cancellation refused">
          {cancelError.message}
        </Notice>
      ) : null}
      {retryError ? (
        <Notice tone="error" title="Retry refused">
          {retryError.message}
        </Notice>
      ) : null}

      {run.state === "failed" ? (
        <Notice
          tone="error"
          title={`Failed at ${run.failure_stage || "an unrecorded stage"}`}
        >
          <p>{run.failure_summary || "No summary was recorded."}</p>
          {run.failure_detail ? (
            <Disclosure summary="Technical detail">
              <pre className="run-detail__pre">{run.failure_detail}</pre>
            </Disclosure>
          ) : null}
        </Notice>
      ) : null}

      {run.state === "blocked" ? (
        <Notice tone="warning" title="Waiting for an approval">
          This run reached a governance gate. A reviewer must clear it before the run
          rejoins the queue. It has not failed and no work has been lost.
        </Notice>
      ) : null}

      {run.state === "cancelled" ? (
        <Notice tone="info" title="Cancelled">
          The run was stopped by request and published no partial result.
        </Notice>
      ) : null}

      <div className="run-detail">
        <Card title="Pipeline">
          <Pipeline run={run} />
        </Card>

        <Card title="Execution">
          <dl className="run-facts">
            <RunFact term="State">
              <RunStateBadge state={run.state} />
            </RunFact>
            <RunFact term="Resource profile">{run.execution_profile}</RunFact>
            <RunFact term="Queued">{formatDateTime(run.queued_at)}</RunFact>
            <RunFact term="Started">{formatDateTime(run.started_at)}</RunFact>
            <RunFact term="Finished">{formatDateTime(run.finished_at)}</RunFact>
            <RunFact term="Elapsed">{formatDuration(run.duration_seconds)}</RunFact>
            <RunFact term="Peak memory">
              {run.peak_memory_mb ? `${run.peak_memory_mb} MB` : "—"}
            </RunFact>
            <RunFact term="Settings hash">
              <span className="mono">{run.settings_hash || "—"}</span>
            </RunFact>
          </dl>
        </Card>
      </div>

      <Card title="Stage history" description="Append-only. A retry creates a new run.">
        {events && events.length > 0 ? (
          <ol className="event-log">
            {events.map((event) => (
              <li key={event.id} className="event-log__item">
                <span className="event-log__time mono">{formatDateTime(event.created_at)}</span>
                <RunStateBadge state={event.state} size="sm" />
                <span className="event-log__stage">{event.stage || "—"}</span>
                {event.message ? (
                  <span className="event-log__message muted">{event.message}</span>
                ) : null}
              </li>
            ))}
          </ol>
        ) : (
          <EmptyState title="No stage events recorded yet" />
        )}
      </Card>
    </>
  );
}

function Pipeline({ run }: { run: Run }) {
  const currentIndex = run.pipeline.findIndex((stage) => stage.key === run.stage);

  return (
    <ol className="pipeline">
      {run.pipeline.map((stage, index) => (
        <PipelineStep
          key={stage.key}
          stage={stage}
          status={stepStatus(index, currentIndex, run)}
        />
      ))}
    </ol>
  );
}

type StepStatus = "done" | "current" | "blocked" | "failed" | "pending";

function stepStatus(index: number, currentIndex: number, run: Run): StepStatus {
  if (run.state === "succeeded") return "done";
  if (currentIndex === -1) return "pending";
  if (index < currentIndex) return "done";
  if (index > currentIndex) return "pending";
  if (run.state === "failed") return "failed";
  if (run.state === "blocked") return "blocked";
  return "current";
}

function PipelineStep({ stage, status }: { stage: PipelineStage; status: StepStatus }) {
  const tone = {
    done: "ok",
    current: "running",
    blocked: "warning",
    failed: "error",
    pending: "idle",
  } as const;

  return (
    <li className={`pipeline__step pipeline__step--${status}`}>
      <div className="pipeline__marker" aria-hidden="true" />
      <div className="pipeline__body">
        <p className="pipeline__label">
          {stage.label}
          {stage.gate ? (
            <StatusBadge
              tone="info"
              size="sm"
              detail="A reviewer must clear this stage before the run continues."
            >
              Gate
            </StatusBadge>
          ) : null}
        </p>
        <p className="pipeline__description">{stage.description}</p>
      </div>
      <StatusBadge tone={tone[status]} size="sm">
        {status}
      </StatusBadge>
    </li>
  );
}

function RunFact({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="run-facts__item">
      <dt>{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}
