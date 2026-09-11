/**
 * Status cues must not depend on colour.
 *
 * Section 3 requires non-colour status cues for WCAG 2.1 AA. A badge carries a
 * glyph and a word as well as a colour; these tests assert the first two,
 * which are the ones that survive greyscale and colour-blindness.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RunStateBadge, StatusBadge } from "./StatusBadge";
import type { RunState } from "@/api/types";

const ALL_STATES: RunState[] = [
  "draft",
  "queued",
  "running",
  "blocked",
  "cancelling",
  "cancelled",
  "failed",
  "succeeded",
];

describe("StatusBadge", () => {
  it("shows a glyph alongside the label", () => {
    const { container } = render(<StatusBadge tone="error">Failed</StatusBadge>);
    const glyph = container.querySelector(".status-badge__glyph");
    expect(glyph?.textContent?.trim()).toBeTruthy();
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("uses a different glyph for each tone", () => {
    const glyphs = (["ok", "warning", "error", "info", "idle", "running"] as const).map(
      (tone) => {
        const { container } = render(<StatusBadge tone={tone}>x</StatusBadge>);
        return container.querySelector(".status-badge__glyph")?.textContent;
      },
    );
    expect(new Set(glyphs).size).toBe(glyphs.length);
  });

  it("reads the detail out rather than hiding it in a tooltip only", () => {
    render(
      <StatusBadge tone="warning" detail="A reviewer must clear this gate.">
        Awaiting approval
      </StatusBadge>,
    );
    expect(screen.getByText(/A reviewer must clear this gate\./)).toBeInTheDocument();
  });
});

describe("RunStateBadge", () => {
  it.each(ALL_STATES)("renders a label and explanation for %s", (state) => {
    const { container } = render(<RunStateBadge state={state} />);
    expect(container.querySelector(".status-badge__label")?.textContent).toBeTruthy();
    expect(container.querySelector(".status-badge")).toHaveAttribute("title");
  });

  it("describes a blocked run as awaiting approval rather than as an error", () => {
    render(<RunStateBadge state="blocked" />);
    expect(screen.getByText("Awaiting approval")).toBeInTheDocument();
  });

  it("says plainly that a cancelled run published nothing", () => {
    render(<RunStateBadge state="cancelled" />);
    expect(screen.getByText(/No result was published\./)).toBeInTheDocument();
  });
});
