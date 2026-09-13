/**
 * Deciding one governance gate.
 *
 * Shared by the model build workspace, where model gates wait, and the run
 * monitor, where a run held at a gate waits. The rule is the same in both: the
 * reason is recorded with the decision, and the person who asked cannot be the
 * person who decides.
 */

import { useState } from "react";

import { ApiError } from "@/api/client";
import { useDecideApproval } from "@/api/hooks";
import type { Approval } from "@/api/types";
import { StatusBadge } from "@/components/StatusBadge";
import { Button, Field, Notice, TextArea } from "@/components/primitives";

import "./GateDecision.css";

export function GateDecision({
  approval,
  mayDecide,
}: {
  approval: Approval;
  mayDecide: boolean;
}) {
  const decide = useDecideApproval();
  const [rationale, setRationale] = useState("");
  const error = decide.error as ApiError | null;

  function submit(decision: "approved" | "rejected") {
    decide.mutate({ id: approval.id, decision, rationale: rationale.trim() });
  }

  return (
    <li className="gate">
      <div className="gate__header">
        <StatusBadge tone="warning" size="sm">
          open
        </StatusBadge>
        <span className="gate__name">{approval.gate.replace(/_/g, " ")}</span>
        <span className="muted">
          {approval.subject_type.replace(/_/g, " ")} · requested by{" "}
          {approval.requested_by_label || "somebody"}
        </span>
      </div>

      {approval.rationale ? <p className="gate__rationale">{approval.rationale}</p> : null}

      {error ? (
        <Notice tone="error" title="The decision was not recorded">
          {error.message}
        </Notice>
      ) : null}

      {mayDecide ? (
        <div className="gate__decide">
          <Field
            label="Why"
            htmlFor={`gate-rationale-${approval.id}`}
            hint="Recorded with the decision. An auditor reads this, not the ticket it came from."
          >
            <TextArea
              id={`gate-rationale-${approval.id}`}
              rows={2}
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
            />
          </Field>
          <div className="gate__actions">
            <Button
              variant="primary"
              size="sm"
              busy={decide.isPending}
              disabled={!rationale.trim()}
              onClick={() => submit("approved")}
              title={
                rationale.trim()
                  ? "Clear the gate so the work rejoins the queue."
                  : "Say why first."
              }
            >
              Approve
            </Button>
            <Button
              variant="danger"
              size="sm"
              busy={decide.isPending}
              disabled={!rationale.trim()}
              onClick={() => submit("rejected")}
            >
              Reject
            </Button>
          </div>
          <p className="muted gate__independence">
            A gate cannot be decided by the person who requested it.
          </p>
        </div>
      ) : (
        <p className="muted">Deciding this gate requires the reviewer role.</p>
      )}
    </li>
  );
}
