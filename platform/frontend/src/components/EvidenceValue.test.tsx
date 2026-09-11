/**
 * Evidence display rules.
 *
 * Section 3 requires assumed values to be visually distinguishable from
 * reported ones, and section 8 requires the rule, evidence and confidence
 * behind an inference to be inspectable. Both are testable, so they are
 * tested: a regression here would let a prior be displayed as a fact.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EvidenceLegend, EvidenceValue } from "./EvidenceValue";

describe("EvidenceValue", () => {
  it("marks a reported value as observed", () => {
    const { container } = render(
      <EvidenceValue value="Office" evidence="reported" source="cedant schedule" />,
    );
    const node = container.querySelector(".evidence");
    expect(node).toHaveClass("evidence--observed");
    expect(node).toHaveAttribute("data-evidence", "reported");
  });

  it("marks an assumed value differently from a reported one", () => {
    const reported = render(<EvidenceValue value="CR" evidence="reported" />);
    const assumed = render(
      <EvidenceValue value="CR" evidence="prior" assumptionSet="baseline-1.0.0" />,
    );

    expect(reported.container.querySelector(".evidence")).toHaveClass("evidence--observed");
    expect(assumed.container.querySelector(".evidence")).toHaveClass("evidence--assumed");
  });

  it("distinguishes an expert override from both", () => {
    const { container } = render(
      <EvidenceValue value="Warehouse" evidence="override" supersededValue="Office" />,
    );
    expect(container.querySelector(".evidence")).toHaveClass("evidence--override");
  });

  it("exposes the source, assumption set and confidence to assistive technology", () => {
    render(
      <EvidenceValue
        value="Industrial"
        evidence="prior"
        source="gem:v2026.0.0/IDN/adm1"
        assumptionSet="baseline-1.0.0"
        confidence={0.42}
      />,
    );

    // The explanation is read out, not only shown as a tooltip.
    expect(
      screen.getByText(/gem:v2026\.0\.0\/IDN\/adm1/, { exact: false }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Confidence: 42%/, { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/baseline-1\.0\.0/, { exact: false })).toBeInTheDocument();
  });

  it("carries the value it replaced, so the override is auditable on screen", () => {
    render(
      <EvidenceValue value="Warehouse" evidence="override" supersededValue="Office" />,
    );
    expect(screen.getByText(/Replaced: Office/, { exact: false })).toBeInTheDocument();
  });

  it("uses a distinct mark per evidence class, so the code is not colour-only", () => {
    const marks = (["reported", "derived", "corroborated", "prior", "override"] as const).map(
      (evidence) => {
        const { container } = render(
          <EvidenceValue
            value="x"
            evidence={evidence}
            assumptionSet={evidence === "prior" || evidence === "override" ? "s" : undefined}
          />,
        );
        return container.querySelector(".evidence__mark")?.textContent;
      },
    );
    expect(new Set(marks).size).toBe(marks.length);
  });
});

describe("EvidenceLegend", () => {
  it("documents every mark so the abbreviations are not an in-house code", () => {
    render(<EvidenceLegend />);
    expect(screen.getByText("Reported by the cedant")).toBeInTheDocument();
    expect(screen.getByText("Assumed from a conditional prior")).toBeInTheDocument();
    expect(screen.getByText("Expert override")).toBeInTheDocument();
  });
});
