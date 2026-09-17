/**
 * The addresses of everything in the guide a link can point at.
 *
 * A card, a step and a definition each get an id, so search results and
 * cross-references land on the thing itself rather than the top of a section
 * somebody then has to read through.
 *
 * This module is the single source of those ids. The guide's own components
 * call it when they render, and the search indexer calls it when it reads the
 * same files at build time. If the two ever disagreed, every search result
 * would scroll to nothing, so neither is allowed its own copy of the rule.
 *
 * Deliberately free of React and of Node, so both callers can use it.
 */

/** Lower case, accents folded, everything else a hyphen. */
export function slug(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/**
 * The address of one defined term, such as "term-average-annual-loss-aal".
 *
 * Terms are addressed by name alone, without the card they sit in, because a
 * link to a definition is written by hand -- GlossaryLink term="Cohort" -- and
 * nobody should have to know which card holds it.
 */
export function termId(term: string): string {
  return `term-${slug(term)}`;
}

/** The address of one card, such as "card-what-a-cell-is". */
export function cardId(title: string): string {
  return `card-${slug(title)}`;
}

/**
 * The address of one step.
 *
 * Carries its card, because step titles repeat across a section: "Publish it"
 * is a step of more than one card, and two elements cannot share an id.
 */
export function stepId(cardTitle: string, stepTitle: string): string {
  return `step-${slug(cardTitle)}--${slug(stepTitle)}`;
}
