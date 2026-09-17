/** What the guide's search index holds, and what a search gives back. */

/** The kinds of thing a search can find. */
export type GuideEntryKind = "card" | "step" | "term";

/** One findable thing in the guide. */
export interface GuideEntry {
  /** The section's tab id, such as "grids". */
  section: string;
  /** The section's tab label, such as "Grids, cells and tiles". */
  sectionLabel: string;
  kind: GuideEntryKind;
  /** The card title, step title or term, exactly as the guide shows it. */
  label: string;
  /** The id of the element to scroll to. */
  anchor: string;
  /** The card this sits in; empty for a card itself. */
  card: string;
  /** A step's number, so a result can say "Step 6". */
  step?: number;
  /** A step's "where on screen" line. */
  where?: string;
  /**
   * The prose of this entry alone, with the prose of anything indexed inside it
   * left out: a card does not repeat its own steps, so one sentence cannot
   * match twice.
   */
  text: string;
}

/** One entry a query matched, and how well. */
export interface GuideHit {
  entry: GuideEntry;
  /** Lower is better. 0 is an exact name; 4 is a mention in the prose. */
  score: number;
}
