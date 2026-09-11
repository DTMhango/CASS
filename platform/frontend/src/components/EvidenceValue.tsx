/**
 * A value shown together with where it came from.
 *
 * Build plan section 3 requires assumed values to be visually distinguishable
 * from reported ones, and section 8 requires an analyst to be able to inspect
 * the rule, evidence and confidence behind every material inference. This
 * component is the single place both are honoured, so no screen can display a
 * prior as though it were a fact.
 */

import "./EvidenceValue.css";

export type EvidenceClass =
  | "reported"
  | "derived"
  | "corroborated"
  | "prior"
  | "override";

const PRESENTATION: Record<
  EvidenceClass,
  { label: string; abbreviation: string; kind: "observed" | "assumed" | "override" }
> = {
  reported: { label: "Reported by the cedant", abbreviation: "R", kind: "observed" },
  derived: { label: "Derived from reported data", abbreviation: "D", kind: "observed" },
  corroborated: { label: "Corroborated externally", abbreviation: "C", kind: "observed" },
  prior: { label: "Assumed from a conditional prior", abbreviation: "A", kind: "assumed" },
  override: { label: "Expert override", abbreviation: "O", kind: "override" },
};

export interface EvidenceValueProps {
  value: React.ReactNode;
  evidence: EvidenceClass;
  /** 0 to 1. Shown for assumed values, where it changes how much to trust them. */
  confidence?: number;
  source?: string;
  assumptionSet?: string;
  /** The value this one replaced, retained for audit under section 8. */
  supersededValue?: string | null;
}

export function EvidenceValue({
  value,
  evidence,
  confidence,
  source,
  assumptionSet,
  supersededValue,
}: EvidenceValueProps) {
  const presentation = PRESENTATION[evidence];

  const explanation = [
    presentation.label,
    source ? `Source: ${source}` : null,
    assumptionSet ? `Assumption set: ${assumptionSet}` : null,
    confidence !== undefined ? `Confidence: ${Math.round(confidence * 100)}%` : null,
    supersededValue ? `Replaced: ${supersededValue}` : null,
  ]
    .filter(Boolean)
    .join(". ");

  return (
    <span
      className={`evidence evidence--${presentation.kind}`}
      title={explanation}
      data-evidence={evidence}
    >
      <span className="evidence__value">{value}</span>
      <abbr className="evidence__mark" title={presentation.label} aria-hidden="true">
        {presentation.abbreviation}
      </abbr>
      <span className="visually-hidden">. {explanation}</span>
    </span>
  );
}

/**
 * A legend for the marks above.
 *
 * Without it the single-letter marks are an in-house code. With it they are a
 * documented convention, which is what section 3 asks for when it says an
 * analyst must be able to inspect the evidence behind an inference.
 */
export function EvidenceLegend() {
  return (
    <dl className="evidence-legend">
      {(Object.keys(PRESENTATION) as EvidenceClass[]).map((key) => (
        <div key={key} className="evidence-legend__item">
          <dt>
            <span className={`evidence evidence--${PRESENTATION[key].kind}`}>
              <span className="evidence__mark">{PRESENTATION[key].abbreviation}</span>
            </span>
          </dt>
          <dd>{PRESENTATION[key].label}</dd>
        </div>
      ))}
    </dl>
  );
}
