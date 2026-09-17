/**
 * Deciding which parts of the guide a query means.
 *
 * Two rules do most of the work. Every word typed must appear somewhere in an
 * entry, so adding a word always narrows the list rather than widening it. And
 * a match on a name beats a match in the prose, so typing "cohort" offers the
 * definition of a cohort before the dozen paragraphs that mention one.
 *
 * Kept apart from the screen so the ranking can be read and tested on its own.
 */

import type { GuideEntry, GuideEntryKind, GuideHit } from "./types";

/**
 * Lower case with accents removed, so "Turkiye" finds "Türkiye".
 *
 * One character in, one character out. A plain NFD fold drops the combining
 * mark and shortens the text, and every position after it then points a
 * character to the left -- which is how a highlight ends up over the wrong
 * word. Where a character has no single-character fold it is left alone: it
 * will not be matched by its plain form, which is a smaller price than moving
 * every position after it.
 */
export function fold(text: string): string {
  let folded = "";
  for (const character of text) {
    const simple = character
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase();
    folded += simple.length === character.length ? simple : character;
  }
  return folded;
}

/** How well an entry answers a query. Lower is better. */
const SCORE = {
  /** The name is exactly what was typed. */
  exactName: 0,
  /** The name begins with what was typed. */
  nameStarts: 1,
  /** Every word typed is somewhere in the name. */
  inName: 2,
  /** Every word is in the name, the card it sits in, or its "where" line. */
  nearName: 3,
  /** The words are in the prose. */
  inProse: 4,
} as const;

/**
 * Which kind of thing to offer first, when two match equally well.
 *
 * A definition leads either way: someone typing one word usually wants to know
 * what it means. After that it depends on how the match was made. When the
 * words are in a name, a topic is the better answer -- it is the thing that is
 * called that. When the words are only in the prose, the smaller thing wins: a
 * step that mentions a currency is a more useful answer than the whole
 * troubleshooting topic that also mentions it somewhere.
 */
const KIND_ORDER: Record<GuideEntryKind, number> = { term: 0, card: 1, step: 2 };
const KIND_ORDER_IN_PROSE: Record<GuideEntryKind, number> = { term: 0, step: 1, card: 2 };

function kindOrder(hit: GuideHit): number {
  const order = hit.score === SCORE.inProse ? KIND_ORDER_IN_PROSE : KIND_ORDER;
  return order[hit.entry.kind];
}

/** How many results a query may return before it is asking for a browse. */
export const RESULT_LIMIT = 40;

/** The shortest query worth answering: one letter matches half the guide. */
export const SHORTEST_QUERY = 2;

/**
 * The folded form of one entry, worked out once.
 *
 * Folding the whole guide on every keystroke is wasted work: the index does not
 * change while somebody types.
 */
const folded = new WeakMap<GuideEntry, { name: string; near: string; prose: string }>();

function foldedOf(entry: GuideEntry) {
  const known = folded.get(entry);
  if (known) return known;
  const made = {
    name: fold(entry.label),
    near: fold([entry.label, entry.card, entry.where ?? ""].join(" ")),
    prose: fold(entry.text),
  };
  folded.set(entry, made);
  return made;
}

function scoreOf(entry: GuideEntry, whole: string, words: readonly string[]): number | null {
  const { name, near, prose } = foldedOf(entry);

  if (!words.every((word) => near.includes(word) || prose.includes(word))) return null;
  if (name === whole) return SCORE.exactName;
  if (name.startsWith(whole)) return SCORE.nameStarts;
  if (words.every((word) => name.includes(word))) return SCORE.inName;
  if (words.every((word) => near.includes(word))) return SCORE.nearName;
  return SCORE.inProse;
}

/**
 * Everything in the guide that answers a query, best first.
 *
 * ``total`` counts every match and ``hits`` holds as many as are worth showing,
 * so the screen can say how much it is leaving out rather than presenting a cut
 * list as the whole answer.
 */
export function searchGuide(
  index: readonly GuideEntry[],
  query: string,
  limit: number = RESULT_LIMIT,
): { hits: GuideHit[]; total: number } {
  const whole = fold(query).trim();
  const words = whole.split(/\s+/).filter(Boolean);
  if (whole.length < SHORTEST_QUERY) return { hits: [], total: 0 };

  const matched: { hit: GuideHit; position: number }[] = [];
  index.forEach((entry, position) => {
    const score = scoreOf(entry, whole, words);
    if (score !== null) matched.push({ hit: { entry, score }, position });
  });

  matched.sort(
    (a, b) =>
      a.hit.score - b.hit.score || kindOrder(a.hit) - kindOrder(b.hit) || a.position - b.position,
  );

  return { hits: matched.slice(0, limit).map((item) => item.hit), total: matched.length };
}

/** One section's worth of results. */
export interface GuideHitGroup {
  section: string;
  sectionLabel: string;
  hits: GuideHit[];
}

/**
 * Results gathered under the section they are in.
 *
 * Grouped because "where is this written?" is half of what a reader wants, and
 * ordered by each group's best result rather than by the tab order: the section
 * holding the answer comes first even when it is the last tab.
 */
export function groupHits(hits: readonly GuideHit[]): GuideHitGroup[] {
  const groups = new Map<string, GuideHitGroup>();
  for (const hit of hits) {
    const group = groups.get(hit.entry.section);
    if (group) group.hits.push(hit);
    else
      groups.set(hit.entry.section, {
        section: hit.entry.section,
        sectionLabel: hit.entry.sectionLabel,
        hits: [hit],
      });
  }
  return [...groups.values()];
}

/** A piece of prose, with the word that matched marked out of it. */
export interface Snippet {
  before: string;
  match: string;
  after: string;
}

const SNIPPET_LENGTH = 150;
const LEAD_IN = 60;

function cut(text: string, length: number): string {
  if (text.length <= length) return text;
  const edge = text.slice(0, length);
  const lastSpace = edge.lastIndexOf(" ");
  return `${lastSpace > length / 2 ? edge.slice(0, lastSpace) : edge}…`;
}

/**
 * The part of an entry's prose worth showing beside a result.
 *
 * Where a word was found in the prose, the text around it, so a reader can see
 * why the result is there. Otherwise the opening words, which for a definition
 * is the definition.
 */
export function snippetFor(entry: GuideEntry, query: string): Snippet | null {
  if (!entry.text) return null;
  const words = fold(query).trim().split(/\s+/).filter(Boolean);
  const { name, prose } = foldedOf(entry);

  const found = words
    .map((word) => ({ word, at: prose.indexOf(word) }))
    .filter((item) => item.at >= 0 && !name.includes(item.word))
    .sort((a, b) => a.at - b.at)[0];

  if (!found) return { before: cut(entry.text, SNIPPET_LENGTH), match: "", after: "" };

  const start = Math.max(0, found.at - LEAD_IN);
  return {
    before: (start === 0 ? "" : "…") + entry.text.slice(start, found.at),
    match: entry.text.slice(found.at, found.at + found.word.length),
    after: cut(entry.text.slice(found.at + found.word.length), SNIPPET_LENGTH - found.word.length),
  };
}
