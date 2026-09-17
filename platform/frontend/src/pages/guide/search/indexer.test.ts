/**
 * The search index, against the guide it is supposed to describe.
 *
 * The index is generated, and a generated file that nobody checks is a file
 * that goes stale: someone adds a section, nobody runs the command, and search
 * quietly cannot find the new writing. So this rebuilds it from the sections as
 * they stand and fails when the two differ, with the command to run.
 */

import { describe, expect, it } from "vitest";

import { GUIDE_INDEX } from "./index.generated";
import { buildGuideIndex, GuideIndexError, readSections } from "./indexer";

/** Every section's source, exactly as the generator reads it. */
const sources = import.meta.glob("../*.tsx", { query: "?raw", import: "default", eager: true }) as Record<
  string,
  string
>;
const guideSource = (
  import.meta.glob("../../UserGuide.tsx", { query: "?raw", import: "default", eager: true }) as Record<
    string,
    string
  >
)["../../UserGuide.tsx"] as string;

const sections = Object.fromEntries(
  Object.entries(sources)
    .filter(([path]) => !path.endsWith(".test.tsx"))
    .map(([path, source]) => [path.replace(/^\.\.\//, "").replace(/\.tsx$/, ""), source]),
);

describe("the guide's search index", () => {
  it("matches the guide as it stands", () => {
    expect(
      buildGuideIndex(guideSource, sections),
      "The guide changed but its search index did not. Run `npm run guide:index`.",
    ).toEqual([...GUIDE_INDEX]);
  });

  it("covers every section the guide offers", () => {
    const declared = readSections(guideSource).map((section) => section.id);
    const indexed = [...new Set(GUIDE_INDEX.map((entry) => entry.section))];
    expect(indexed).toEqual(declared);
  });

  it("finds definitions, steps and topics", () => {
    const kinds = new Set(GUIDE_INDEX.map((entry) => entry.kind));
    expect([...kinds].sort()).toEqual(["card", "step", "term"]);

    const tile = GUIDE_INDEX.find((entry) => entry.kind === "term" && entry.label === "Tile");
    expect(tile).toMatchObject({ section: "glossary", anchor: "term-tile" });
    expect(tile?.text).toContain("rectangle");
  });

  it("keeps a step's number, the card it belongs to and where it happens", () => {
    const steps = GUIDE_INDEX.filter((entry) => entry.kind === "step");
    expect(steps.length).toBeGreaterThan(20);
    for (const step of steps) {
      expect(step.card, `${step.label} is not inside a card`).not.toBe("");
      expect(step.step, `${step.label} has no number`).toBeGreaterThan(0);
    }
    expect(steps.filter((step) => step.where).length).toBeGreaterThan(steps.length / 2);
  });

  it("gives a card the prose of its own paragraphs, not of its steps", () => {
    // Any card with steps: which cards have them is the guide's business, and
    // this holds however it is rewritten.
    const card = GUIDE_INDEX.find(
      (entry) =>
        entry.kind === "card" &&
        GUIDE_INDEX.some(
          (step) =>
            step.kind === "step" &&
            step.section === entry.section &&
            step.card === entry.label &&
            step.text.length > 120,
        ),
    );
    expect(card).toBeDefined();
    const step = GUIDE_INDEX.find(
      (entry) =>
        entry.kind === "step" &&
        entry.section === card?.section &&
        entry.card === card?.label &&
        entry.text.length > 120,
    );
    const sentence = step?.text.split(" ").slice(4, 14).join(" ") ?? "";
    expect(sentence.length).toBeGreaterThan(20);
    expect(card?.text).not.toContain(sentence);
  });

  it("reads the words inside a notice, which belong to what holds it", () => {
    // The intake template's warning that everything is recorded as USD is
    // inside a notice, inside a step. Searching "USD" has to reach it.
    const holder = GUIDE_INDEX.find(
      (entry) => entry.section === "portfolio" && entry.text.includes("USD"),
    );
    expect(holder, "nothing in the portfolio section mentions USD").toBeDefined();
    expect(["step", "card"]).toContain(holder?.kind);
  });

  it("gives everything in a section its own address", () => {
    for (const section of new Set(GUIDE_INDEX.map((entry) => entry.section))) {
      const anchors = GUIDE_INDEX.filter((entry) => entry.section === section).map(
        (entry) => entry.anchor,
      );
      expect(new Set(anchors).size, `duplicate anchors in ${section}`).toBe(anchors.length);
    }
  });

  it("refuses a card the reader could not be sent to", () => {
    const guide = `<Tabs tabs={[{ id: "x", label: "X", content: () => <X /> }]} />`;
    const broken = `import { Card } from "./parts";
      export function X() {
        return <Card title={someTitle}>text</Card>;
      }`;
    expect(() => buildGuideIndex(guide, { X: broken })).toThrow(GuideIndexError);
  });

  it("refuses a card taken from the design system instead of the guide's own", () => {
    const guide = `<Tabs tabs={[{ id: "x", label: "X", content: () => <X /> }]} />`;
    const broken = `import { Card } from "@/components/primitives";
      export function X() {
        return <Card title="A topic">text</Card>;
      }`;
    expect(() => buildGuideIndex(guide, { X: broken })).toThrow(/import Card from "\.\/parts"/);
  });
});
