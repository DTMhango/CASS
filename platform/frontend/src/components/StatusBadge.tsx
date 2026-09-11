/**
 * Status shown with a glyph, a word and a colour.
 *
 * Build plan section 3 requires non-colour status cues. A badge that is only
 * green or red fails that, so every badge carries a shape and a label; colour
 * is the third signal, not the only one.
 */

import type { RunState } from "@/api/types";
import "./StatusBadge.css";

export type Tone = "ok" | "warning" | "error" | "info" | "idle" | "running";

const GLYPH: Record<Tone, string> = {
  ok: "●", // filled circle
  warning: "▲", // triangle
  error: "■", // square
  info: "◆", // diamond
  idle: "○", // hollow circle
  running: "◑", // half-filled circle
};

interface StatusBadgeProps {
  tone: Tone;
  children: React.ReactNode;
  /** Longer explanation, surfaced as a title and to assistive technology. */
  detail?: string;
  size?: "sm" | "md";
}

export function StatusBadge({ tone, children, detail, size = "md" }: StatusBadgeProps) {
  return (
    <span className={`status-badge status-badge--${tone} status-badge--${size}`} title={detail}>
      <span className="status-badge__glyph" aria-hidden="true">
        {GLYPH[tone]}
      </span>
      <span className="status-badge__label">{children}</span>
      {detail ? <span className="visually-hidden">. {detail}</span> : null}
    </span>
  );
}

/** How each run state should read in the interface. */
const RUN_PRESENTATION: Record<RunState, { tone: Tone; label: string; detail: string }> = {
  draft: { tone: "idle", label: "Draft", detail: "Being configured; nothing has been submitted." },
  queued: { tone: "info", label: "Queued", detail: "Waiting for a worker slot under its resource profile." },
  running: { tone: "running", label: "Running", detail: "A worker is executing a stage." },
  blocked: {
    tone: "warning",
    label: "Awaiting approval",
    detail: "Stopped at a governance gate that a reviewer must clear.",
  },
  cancelling: { tone: "warning", label: "Cancelling", detail: "Unwinding to a clean point." },
  cancelled: { tone: "idle", label: "Cancelled", detail: "Stopped by request. No result was published." },
  failed: { tone: "error", label: "Failed", detail: "Stopped by error. The failure evidence is retained." },
  succeeded: { tone: "ok", label: "Succeeded", detail: "Every stage completed and produced its artifacts." },
};

export function RunStateBadge({ state, size }: { state: RunState; size?: "sm" | "md" }) {
  const presentation = RUN_PRESENTATION[state];
  return (
    <StatusBadge tone={presentation.tone} detail={presentation.detail} size={size}>
      {presentation.label}
    </StatusBadge>
  );
}
