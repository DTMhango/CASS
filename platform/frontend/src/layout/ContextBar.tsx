/**
 * The always-visible working context.
 *
 * Section 3: "Make model version, portfolio, financial perspective and run
 * state continuously visible." This bar is that sentence. It sits under the
 * header on every screen so a user can never lose track of which model version
 * and which portfolio the numbers on screen belong to.
 *
 * Where a model version is a research prototype, that is stated here rather
 * than only in the catalogue, because section 9 requires research runs and
 * approved decision outputs to be operationally distinct everywhere.
 */

import { useRuns } from "@/api/hooks";
import { RunStateBadge, StatusBadge } from "@/components/StatusBadge";
import { useWorkingContext } from "@/context/WorkingContext";

import "./ContextBar.css";

const PERSPECTIVE_LABEL: Record<string, string> = {
  ground_up: "Ground-up loss",
  insured: "Insured loss",
  reinsurance: "Reinsurance loss",
};

export function ContextBar() {
  const { project, exposure, model, perspective } = useWorkingContext();
  const { data: runs } = useRuns(project?.id);

  const activeRun = runs?.find((run) => run.is_active);

  return (
    <div className="context-bar" aria-label="Working context">
      <ContextItem label="Model version">
        {model ? (
          <span className="context-bar__value">
            <span className="mono">{model.reference}</span>
            {model.is_research_prototype ? (
              <StatusBadge
                tone="warning"
                size="sm"
                detail="A research prototype may be run but not used for decisions."
              >
                Research only
              </StatusBadge>
            ) : (
              <StatusBadge tone="ok" size="sm" detail="Approved for decision use.">
                Approved
              </StatusBadge>
            )}
          </span>
        ) : (
          <span className="muted">Not selected</span>
        )}
      </ContextItem>

      <ContextItem label="Portfolio">
        {exposure ? (
          <span className="context-bar__value">
            {exposure.name}
            <span className="context-bar__version">v{exposure.version}</span>
            {exposure.is_frozen ? (
              <StatusBadge
                tone="ok"
                size="sm"
                detail="Published and immutable; runs may use it."
              >
                Published
              </StatusBadge>
            ) : (
              <StatusBadge
                tone="idle"
                size="sm"
                detail="Draft. Publish it before an analysis may use it."
              >
                Draft
              </StatusBadge>
            )}
          </span>
        ) : (
          <span className="muted">Not selected</span>
        )}
      </ContextItem>

      <ContextItem label="Perspective">
        <span className="context-bar__value">
          {PERSPECTIVE_LABEL[perspective] ?? perspective}
        </span>
      </ContextItem>

      <ContextItem label="Run state">
        {activeRun ? (
          <span className="context-bar__value">
            <RunStateBadge state={activeRun.state} size="sm" />
            <span className="muted">{activeRun.stage_label || activeRun.kind}</span>
          </span>
        ) : (
          <span className="muted">No active run</span>
        )}
      </ContextItem>
    </div>
  );
}

function ContextItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="context-bar__item">
      <span className="context-bar__label">{label}</span>
      {children}
    </div>
  );
}
