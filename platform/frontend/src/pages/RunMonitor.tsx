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

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError } from "@/api/client";
import {
  useAnalysisForRun,
  useCancelRun,
  useDownloadArtifact,
  useRequestRunException,
  useResumeAnalysis,
  useRetryRun,
  useRun,
  useRunArtifacts,
  useRunEvents,
  useRunExceptions,
  useRuns,
  useSession,
} from "@/api/hooks";
import type {
  AnalysisRun,
  PipelineStage,
  ReviewCheck,
  ReviewRecord,
  Run,
  RunArtifact,
  SmokeRecord,
} from "@/api/types";
import { GateDecision } from "@/components/GateDecision";
import { RunStateBadge, StatusBadge } from "@/components/StatusBadge";
import {
  Button,
  Card,
  Disclosure,
  EmptyState,
  Field,
  Notice,
  PageHeader,
  Spinner,
  TextArea,
} from "@/components/primitives";
import { useWorkingContext } from "@/context/WorkingContext";
import { formatBytes, formatDateTime, formatDuration, formatMoney } from "@/lib/format";

import "./RunMonitor.css";

/**
 * A clock that ticks while something on the page is still moving.
 *
 * The server says how long a run has been going and the queries refresh every
 * few seconds; this fills the seconds in between, so an analyst watching a run
 * sees a clock rather than a number that jumps.
 */
function useTicker(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

/**
 * How long the run has been going, counted on from what the server last said.
 *
 * Measured from the server's own figure rather than from the start time and
 * this browser's clock: the two are not the same, and a monitor that quietly
 * told an analyst a run had been going for an hour longer than it had would be
 * worse than the blank it replaces.
 */
function elapsed(run: Run, fetchedAt: number, now: number): number | null {
  if (run.elapsed_seconds === null) return null;
  if (run.finished_at) return run.elapsed_seconds;
  return run.elapsed_seconds + Math.max(0, Math.round((now - fetchedAt) / 1_000));
}

/** A run that has started and not finished is one whose clock is still going. */
function isRunning(run: Run): boolean {
  return run.started_at !== null && run.finished_at === null;
}

export function RunMonitor() {
  const { runId } = useParams();
  const context = useWorkingContext();
  const runs = useRuns(context.projectId);
  const run = useRun(runId);
  const ticking = useTicker((runs.data ?? []).some(isRunning));

  if (runId && run.data) return <RunDetail run={run.data} fetchedAt={run.dataUpdatedAt} />;
  if (runs.isLoading) return <Spinner label="Loading runs" />;

  return (
    <>
      <PageHeader
        title="Run monitor"
        description="Every hazard, conversion and analysis run, with the stage it reached and the evidence it produced. Work continues whether or not this page stays open."
      />
      {runs.data && runs.data.length > 0 ? (
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
              {runs.data.map((item) => (
                <tr key={item.id}>
                  <th scope="row">
                    <Link to={`/runs/${item.id}`}>{item.label || item.id.slice(0, 8)}</Link>
                  </th>
                  <td>{item.kind}</td>
                  <td>
                    <RunStateBadge state={item.state} size="sm" />
                  </td>
                  <td className="muted">{item.stage_label || "—"}</td>
                  <td className="numeric">
                    {formatDuration(elapsed(item, runs.dataUpdatedAt, ticking))}
                  </td>
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

function RunDetail({ run, fetchedAt }: { run: Run; fetchedAt: number }) {
  const ticking = useTicker(isRunning(run));
  const cancel = useCancelRun(run.id);
  const retry = useRetryRun(run.id);
  const { data: events } = useRunEvents(run.id, run.is_active);
  const { data: artifacts } = useRunArtifacts(run.id);
  const { data: analysis } = useAnalysisForRun(run.id, run.kind);

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
        <Notice
          tone="warning"
          title={run.gate_summary || "Waiting for an approval"}
        >
          <p>
            This run reached a governance gate. A reviewer must clear it before the run
            rejoins the queue. It has not failed and no work has been lost.
          </p>
          {run.gate_detail ? (
            <Disclosure summary="What the gate is holding">
              <pre className="run-detail__pre">{run.gate_detail}</pre>
            </Disclosure>
          ) : null}
          {analysis ? <GateControls run={run} analysis={analysis} /> : null}
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
            <RunFact term="Elapsed">
              {formatDuration(elapsed(run, fetchedAt, ticking))}
            </RunFact>
            <RunFact term="Peak memory">
              {run.peak_memory_mb ? `${run.peak_memory_mb} MB` : "—"}
            </RunFact>

          </dl>
        </Card>
      </div>

      {analysis ? <KeysReconciliation analysis={analysis} /> : null}
      <RunChecks run={run} />

      <Card
        title="Evidence"
        description="What this run read and what it wrote."
        padded={false}
      >
        {artifacts && artifacts.length > 0 ? (
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Artifact</th>
                <th scope="col">Direction</th>
                <th scope="col" className="numeric">
                  Size
                </th>
                <th scope="col">Kept until</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {artifacts.map((artifact) => (
                <ArtifactRow key={`${artifact.role}-${artifact.uri}`} artifact={artifact} />
              ))}
            </tbody>
          </table>
        ) : (
          <div className="run-detail__empty">
            <EmptyState
              title="No artifacts recorded yet"
              description="Inputs and outputs appear here as the run produces them."
            />
          </div>
        )}
      </Card>

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

/**
 * One artifact row.
 *
 * A role a reader cannot interpret is worse than a technical name they can
 * look up, so the recorded role is shown as it was written and explained
 * beside it where CASS knows what it is. ``readable`` is the artifact store\'s
 * answer about this user, not a guess: section 10 requires per-project
 * authorization on artifact retrieval as well as metadata, so a row the
 * viewer may not open says so rather than offering a link that will refuse.
 */
function ArtifactRow({ artifact }: { artifact: RunArtifact }) {
  const download = useDownloadArtifact();

  return (
    <tr>
      <th scope="row">
        <span className="mono">{artifact.role}</span>
        {ARTIFACT_ROLES[artifact.role] ? (
          <span className="muted run-detail__role"> {ARTIFACT_ROLES[artifact.role]}</span>
        ) : null}
      </th>
      <td>
        <StatusBadge tone={artifact.direction === "output" ? "ok" : "idle"} size="sm">
          {artifact.direction}
        </StatusBadge>
      </td>

      <td className="numeric">{formatBytes(artifact.size_bytes)}</td>
      <td className="muted">{artifact.retention}</td>
      <td>
        {artifact.readable ? (
          <Button
            variant="ghost"
            size="sm"
            busy={download.isPending}
            onClick={() =>
              download.mutate({
                id: artifact.id,
                filename: artifact.uri.split("/").pop() || artifact.role,
              })
            }
            title="Retrieve this artifact. The download is authorized and recorded."
          >
            Download
          </Button>
        ) : artifact.state === "expired" ? (
          <StatusBadge
            tone="idle"
            size="sm"
            detail="Removed under its retention class. The record stays, so the run still names what it used."
          >
            expired
          </StatusBadge>
        ) : artifact.state === "quarantined" ? (
          <StatusBadge
            tone="error"
            size="sm"
            detail="It failed its checks on arrival and cannot be read by the application."
          >
            quarantined
          </StatusBadge>
        ) : (
          <StatusBadge
            tone="idle"
            size="sm"
            detail="Your project role does not permit retrieving this artifact."
          >
            not yours to read
          </StatusBadge>
        )}
      </td>
    </tr>
  );
}

/** What CASS knows an artifact role to be, where it knows. */
const ARTIFACT_ROLES: Record<string, string> = {
  oed_location: "the published location file",
  oed_account: "the published account file",
  cass_keys: "every location, coverage and sub-peril the model mapped",
  cass_keys_errors: "the rows the lookup could not map, with the reason",
  oasis_output: "the loss output collected from the engine",
};

/**
 * The section 8 keys gate.
 *
 * Two different things are shown apart because they are two different things.
 * Value that has gone missing is a defect in the lookup -- every location is
 * supposed to produce exactly one response -- and nothing approves that away.
 * Value the model could not map is a fact about the portfolio, and section 8
 * requires a person to accept it before the analysis proceeds.
 */
function KeysReconciliation({ analysis }: { analysis: AnalysisRun }) {
  const summary = analysis.keys_summary ?? {};
  const rows = KEYS_ROWS.filter((row) => summary[row.key] !== undefined);

  if (analysis.keys_reconciled === null && rows.length === 0) return null;

  return (
    <Card
      title="Keys reconciliation"
      description="Successful, not-at-risk and failed value, against the published source."
    >
      {analysis.keys_reconciled ? (
        <Notice tone="ok" title="The accounting balances">
          Every location, coverage and sub-peril produced exactly one response.
        </Notice>
      ) : (
        <Notice tone="error" title="Value is unaccounted for">
          The lookup did not return one response per location, coverage and sub-peril.
          This is a defect in the lookup rather than a fact about the portfolio, so it
          cannot be approved away.
        </Notice>
      )}

      {rows.length > 0 ? (
        <dl className="run-facts">
          {rows.map((row) => (
            <RunFact key={row.key} term={row.label}>
              <span className="numeric">{formatMoney(String(summary[row.key]))}</span>
            </RunFact>
          ))}
        </dl>
      ) : null}

      {analysis.keys_reconciled && !analysis.may_proceed_past_keys ? (
        <Notice tone="warning" title="Unmapped value is waiting to be accepted">
          Value the model could not map is a fact about this portfolio, and section 8
          requires somebody to accept it before the analysis proceeds. A reviewer
          clears this on the governance gate.
        </Notice>
      ) : null}
    </Card>
  );
}

const KEYS_ROWS = [
  { key: "successful_tiv", label: "Mapped to a vulnerability function" },
  { key: "not_at_risk_tiv", label: "Not at risk" },
  { key: "failed_tiv", label: "The model could not map" },
  { key: "source_tiv", label: "Published source total" },
] as const;

/** The shortest reason the API accepts for an exception, kept in step with it. */
const MINIMUM_REASON = 12;

/**
 * What a person can do about a run held at a gate.
 *
 * Three states, in the order a gate moves through them. Nobody has asked, so the
 * person whose run it is says why it should go on. Somebody has asked, so a
 * reviewer who did not ask decides. The gate is cleared, so the run goes back in
 * the queue. A refusal returns to the first with the reviewer's reason shown,
 * because a refused exception is an answer rather than a dead end.
 */
function GateControls({ run, analysis }: { run: Run; analysis: AnalysisRun }) {
  const { data: session } = useSession();
  const { data: requests } = useRunExceptions(analysis.id);
  const request = useRequestRunException(analysis.id);
  const resume = useResumeAnalysis(analysis.id);
  const [rationale, setRationale] = useState("");

  const mayDecide = session?.user?.capabilities.approve_gates ?? false;
  const current = requests?.find((item) => item.evidence?.stage === run.stage);
  const error = (request.error ?? resume.error) as ApiError | null;
  const reason = rationale.trim();

  return (
    <div className="gate-controls">
      {error ? (
        <Notice tone="error" title="Not recorded">
          {error.message}
        </Notice>
      ) : null}

      {current?.is_cleared ? (
        <div className="gate-controls__row">
          <p>
            Cleared by {current.decided_by_label || "a reviewer"}
            {current.rationale ? `: ${current.rationale}` : "."}
          </p>
          <Button variant="primary" busy={resume.isPending} onClick={() => resume.mutate()}>
            Resume the run
          </Button>
        </div>
      ) : current?.is_open ? (
        mayDecide ? (
          <ul className="gate-list">
            <GateDecision approval={current} mayDecide={mayDecide} />
          </ul>
        ) : (
          <p className="muted">
            Asked by {current.requested_by_label || "somebody"}. A reviewer who did not ask
            decides it.
          </p>
        )
      ) : (
        <div className="gate-controls__ask">
          {current?.decision === "rejected" ? (
            <Notice tone="warning" title="The exception was refused">
              {current.rationale || "No reason was recorded."}
            </Notice>
          ) : null}
          <Field
            label="Why the run should go on"
            htmlFor={`exception-${analysis.id}`}
            hint="A reviewer reads this beside what the gate is holding. A sentence at least."
          >
            <TextArea
              id={`exception-${analysis.id}`}
              rows={2}
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
            />
          </Field>
          <Button
            variant="secondary"
            busy={request.isPending}
            disabled={reason.length < MINIMUM_REASON}
            onClick={() => request.mutate(reason, { onSuccess: () => setRationale("") })}
          >
            Ask for an exception
          </Button>
        </div>
      )}
    </div>
  );
}

/**
 * What the smoke check and the result review found.
 *
 * Read from the run manifest, which is where the stages record their evidence.
 * A check that did not pass comes first, because that is the line a reviewer
 * deciding an exception needs.
 */
function RunChecks({ run }: { run: Run }) {
  const smoke = run.manifest?.smoke as SmokeRecord | undefined;
  const review = run.manifest?.review as ReviewRecord | undefined;
  if (!smoke && !review) return null;

  const checks = [...(review?.checks ?? [])].sort((a, b) => checkRank(a) - checkRank(b));

  return (
    <Card
      title="Checks"
      description="The reduced-event smoke run before the losses, and the review of the results they produced."
    >
      {smoke ? (
        smoke.performed ? (
          <p className="run-checks__smoke">
            Smoke check: {smoke.event_ids?.length ?? 0} of the package&apos;s largest events ran
            through every requested perspective
            {smoke.evaluated === false
              ? `, but their output could not be read (${smoke.reason ?? "no reason recorded"}).`
              : "."}
          </p>
        ) : (
          <Notice tone="info" title="Smoke check not performed">
            {smoke.reason}
          </Notice>
        )
      ) : null}
      {checks.length > 0 ? (
        <ul className="run-checks">
          {checks.map((item) => (
            <li key={item.check} className="run-checks__item">
              <StatusBadge
                tone={item.passed === false ? "error" : item.passed ? "ok" : "idle"}
                size="sm"
              >
                {item.passed === false ? "failed" : item.passed ? "passed" : "not evaluated"}
              </StatusBadge>
              <span className="run-checks__name">{item.check}</span>
              <span className="muted run-checks__detail">{item.detail}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}

function checkRank(check: ReviewCheck): number {
  if (check.passed === false) return 0;
  if (check.passed === null) return 1;
  return 2;
}

function Pipeline({ run }: { run: Run }) {
  const currentIndex = run.pipeline.findIndex((stage) => stage.key === run.stage);

  return (
    <ol className="pipeline">
      {run.pipeline.map((stage, index) => {
        const status = stepStatus(index, currentIndex, run);
        return (
          <PipelineStep
            key={stage.key}
            stage={stage}
            status={status}
            // The engine's own position, and only on the stage it is working
            // in: the calculation stages are where the hours go, and the stage
            // list says nothing at all for the whole of one.
            progress={status === "current" ? run.stage_progress : null}
            progressLabel={run.stage_progress_label}
          />
        );
      })}
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

function PipelineStep({
  stage,
  status,
  progress,
  progressLabel,
}: {
  stage: PipelineStage;
  status: StepStatus;
  progress?: number | null;
  progressLabel?: string;
}) {
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
        {progress === null || progress === undefined ? null : (
          <StageProgress fraction={progress} label={progressLabel ?? ""} />
        )}
      </div>
      <StatusBadge tone={tone[status]} size="sm">
        {status}
      </StatusBadge>
    </li>
  );
}

/**
 * How far into the stage the engine says it is.
 *
 * Shown with the name of what is being counted, never as a bare bar. OpenQuake
 * reports a percentage for each phase of a calculation and starts again at zero
 * for the next one, so "72%" alone would tell an analyst the run was nearly
 * done three separate times. "72% · classical" is what the engine actually
 * said. It is a position, not a prediction: nothing here estimates a finish.
 */
function StageProgress({ fraction, label }: { fraction: number; label: string }) {
  const percent = Math.round(fraction * 100);
  const described = label ? `${percent}% through ${label}` : `${percent}% through this stage`;

  return (
    <p className="stage-progress">
      <span
        className="stage-progress__track"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuetext={described}
      >
        <span className="stage-progress__fill" style={{ width: `${percent}%` }} />
      </span>
      <span className="stage-progress__figure numeric">{percent}%</span>
      {label ? <span className="muted stage-progress__label">{label}</span> : null}
    </p>
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
