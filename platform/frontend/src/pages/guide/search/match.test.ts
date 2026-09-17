/**
 * What a query means, and in what order.
 *
 * The ordering is the whole value of the feature: a list that puts eleven
 * paragraphs mentioning a cohort above the definition of one is a list nobody
 * reads twice.
 */

import { describe, expect, it } from "vitest";

import { GUIDE_INDEX } from "./index.generated";
import { fold, groupHits, searchGuide, snippetFor } from "./match";
import type { GuideEntry } from "./types";

function entry(overrides: Partial<GuideEntry>): GuideEntry {
  return {
    section: "glossary",
    sectionLabel: "Glossary",
    kind: "term",
    label: "Tile",
    anchor: "term-tile",
    card: "Grid terms",
    text: "A rectangle you draw around part of a country.",
    ...overrides,
  };
}

describe("folding", () => {
  it("ignores case and accents", () => {
    expect(fold("Türkiye")).toBe("turkiye");
  });

  it("keeps one character per character, so positions still point at the right word", () => {
    const text = "Türkiye and Ivory Coast";
    expect(fold(text)).toHaveLength(text.length);
    expect(fold(text).indexOf("ivory")).toBe(text.indexOf("Ivory"));
  });
});

describe("searching the guide", () => {
  it("says nothing for a single letter, which would match half the guide", () => {
    expect(searchGuide(GUIDE_INDEX, "t").hits).toHaveLength(0);
  });

  it("puts the definition of a word above the paragraphs that mention it", () => {
    const { hits } = searchGuide(GUIDE_INDEX, "cohort");
    expect(hits[0]?.entry).toMatchObject({ kind: "term", label: "Cohort" });
    expect(hits.length).toBeGreaterThan(1);
  });

  it("finds a word that appears only in the prose", () => {
    const { hits, total } = searchGuide(GUIDE_INDEX, "liquefaction");
    expect(total).toBeGreaterThan(0);
    expect(hits.every((hit) => hit.entry.text.toLowerCase().includes("liquefaction"))).toBe(true);
  });

  it("narrows as words are added, never widens", () => {
    const one = searchGuide(GUIDE_INDEX, "grid").total;
    const two = searchGuide(GUIDE_INDEX, "grid refinement").total;
    expect(two).toBeGreaterThan(0);
    expect(two).toBeLessThanOrEqual(one);
  });

  it("ranks an exact name, then one that starts with it, then the prose", () => {
    const index = [
      entry({ label: "Mapping tolerance", anchor: "a", text: "" }),
      entry({ label: "Tile", anchor: "b", text: "" }),
      entry({ label: "Tiles and refinements", anchor: "c", text: "" }),
      entry({ label: "Domain", anchor: "d", text: "All the tiles put together." }),
    ];
    expect(searchGuide(index, "tile").hits.map((hit) => hit.entry.anchor)).toEqual([
      "b",
      "c",
      "d",
    ]);
  });

  it("offers a definition before a step when both match equally", () => {
    const index = [
      entry({ kind: "step", label: "Add the tiles", anchor: "s", card: "Build a grid", text: "" }),
      entry({ kind: "term", label: "Add the tiles", anchor: "t", text: "" }),
    ];
    expect(searchGuide(index, "add the tiles").hits[0]?.entry.anchor).toBe("t");
  });

  it("counts every match but hands back only as many as are worth showing", () => {
    const { hits, total } = searchGuide(GUIDE_INDEX, "the", 5);
    expect(hits).toHaveLength(5);
    expect(total).toBeGreaterThan(5);
  });

  it("gathers results under their section, best section first", () => {
    const { hits } = searchGuide(GUIDE_INDEX, "tile");
    const groups = groupHits(hits);
    expect(groups.length).toBeGreaterThan(1);
    expect(groups[0]?.section).toBe(hits[0]?.entry.section);
    expect(groups.flatMap((group) => group.hits)).toHaveLength(hits.length);
  });
});

describe("the words shown beside a result", () => {
  it("shows the definition itself where the name matched", () => {
    const snippet = snippetFor(entry({}), "tile");
    expect(snippet?.before).toContain("A rectangle");
    expect(snippet?.match).toBe("");
  });

  it("shows the words around a match found in the prose, and marks it", () => {
    const snippet = snippetFor(
      entry({ label: "Domain", text: "Everything the grid covers: all of its tiles put together." }),
      "tiles",
    );
    expect(snippet?.match).toBe("tiles");
    expect(snippet?.before).toContain("grid covers");
    expect(snippet?.after).toContain("put together");
  });

  it("marks the word as it is written, not as it was typed", () => {
    const snippet = snippetFor(entry({ label: "Domain", text: "The Tiles of a grid." }), "tiles");
    expect(snippet?.match).toBe("Tiles");
  });
});
