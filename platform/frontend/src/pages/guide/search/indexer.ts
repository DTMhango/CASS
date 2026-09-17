/**
 * Reading the guide's own source into a search index.
 *
 * The guide is prose written as components, not data, so a search box has
 * nothing to search until something has read it. Three ways were possible:
 * search the rendered page, keep a hand-written list, or read the source. The
 * first cannot work -- only the open tab is in the document, so a reader on
 * "Grids" could never find a term defined under "Glossary". The second is a
 * list somebody has to remember to update, which is the same failure the guide
 * itself exists to avoid. So this reads the sections, and a test fails the
 * build when what it produces no longer matches what is committed.
 *
 * It parses rather than pattern-matches. A regular expression over JSX breaks
 * on the first title that wraps onto a second line, and the guide is full of
 * them; the TypeScript parser the project already depends on does not.
 *
 * **This runs at build time only.** It pulls in the TypeScript compiler, so
 * importing it from anything the browser loads would put a parser into the
 * application bundle. The page imports the generated index instead.
 */

import ts from "typescript";

import { cardId, stepId, termId } from "../anchors";
import type { GuideEntry } from "./types";

/** The sections to read: the component's file name, without extension, to its source. */
export type SectionSources = Readonly<Record<string, string>>;

export class GuideIndexError extends Error {}

/** One tab of the guide, as UserGuide.tsx declares it. */
interface SectionDeclaration {
  id: string;
  label: string;
  /** The component's name, which is also its file name. */
  component: string;
}

/**
 * Elements whose visible text belongs to whatever contains them.
 *
 * A notice inside a step is part of that step, so its title is indexed with
 * the step's prose rather than becoming a result of its own.
 */
const TITLE_BEARING = new Set(["Notice", "Example"]);

/** Left out of the prose: a path to call is not something a reader searches for. */
const NOT_PROSE = new Set(["Api"]);

const ENTITIES: Record<string, string> = {
  amp: "&",
  apos: "'",
  quot: '"',
  lt: "<",
  gt: ">",
  nbsp: " ",
  ldquo: "“",
  rdquo: "”",
  lsquo: "‘",
  rsquo: "’",
  ndash: "–",
  mdash: "—",
  hellip: "…",
  times: "×",
  deg: "°",
};

function decode(text: string): string {
  return text
    .replace(/&#(\d+);/g, (_, code: string) => String.fromCodePoint(Number(code)))
    .replace(/&([a-zA-Z]+);/g, (whole, name: string) => ENTITIES[name] ?? whole);
}

function tidy(text: string): string {
  return decode(text).replace(/\s+/g, " ").trim();
}

function parse(fileName: string, source: string): ts.SourceFile {
  return ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
}

function tagNameOf(node: ts.JsxElement | ts.JsxSelfClosingElement): string {
  const opening = ts.isJsxElement(node) ? node.openingElement : node;
  return opening.tagName.getText();
}

function attributesOf(node: ts.JsxElement | ts.JsxSelfClosingElement): ts.JsxAttributes {
  return (ts.isJsxElement(node) ? node.openingElement : node).attributes;
}

/** The value of one attribute, where it is a plain string. */
function stringAttribute(
  node: ts.JsxElement | ts.JsxSelfClosingElement,
  name: string,
): string | undefined {
  for (const attribute of attributesOf(node).properties) {
    if (!ts.isJsxAttribute(attribute) || attribute.name.getText() !== name) continue;
    const value = attribute.initializer;
    if (value && ts.isStringLiteral(value)) return value.text;
    if (value && ts.isJsxExpression(value) && value.expression) {
      const inner = value.expression;
      if (ts.isStringLiteral(inner) || ts.isNoSubstitutionTemplateLiteral(inner)) return inner.text;
    }
    return undefined;
  }
  return undefined;
}

function numberAttribute(
  node: ts.JsxElement | ts.JsxSelfClosingElement,
  name: string,
): number | undefined {
  for (const attribute of attributesOf(node).properties) {
    if (!ts.isJsxAttribute(attribute) || attribute.name.getText() !== name) continue;
    const value = attribute.initializer;
    if (value && ts.isJsxExpression(value) && value.expression) {
      const inner = value.expression;
      if (ts.isNumericLiteral(inner)) return Number(inner.text);
    }
  }
  return undefined;
}

function isElement(node: ts.Node): node is ts.JsxElement | ts.JsxSelfClosingElement {
  return ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node);
}

/**
 * The visible words inside one element, stopping at anything indexed itself.
 *
 * Text is collected from the prose and from the attributes a reader can see --
 * a notice's title, a step's "where" line -- so searching for words that only
 * appear in a warning still finds the step carrying it.
 */
function proseOf(node: ts.Node, stopAt: ReadonlySet<string>): string {
  const pieces: string[] = [];

  const walk = (current: ts.Node, depth: number): void => {
    if (depth > 0 && isElement(current)) {
      const tag = tagNameOf(current);
      if (stopAt.has(tag) || NOT_PROSE.has(tag)) return;
      if (TITLE_BEARING.has(tag)) {
        const title = stringAttribute(current, "title");
        if (title) pieces.push(title);
      }
    }
    if (ts.isJsxText(current)) {
      // The full text, not getText(): that skips leading whitespace as if it
      // were trivia, which in JSX it is not. Dropping it runs the last word of
      // one element into the first of the next -- "a portfoliois your list".
      const text = current.getFullText();
      if (text.trim()) pieces.push(text);
      else if (text) pieces.push(" ");
    }
    if (ts.isStringLiteral(current) && ts.isJsxExpression(current.parent)) {
      pieces.push(current.text);
    }
    current.forEachChild((child) => walk(child, depth + 1));
  };

  walk(node, 0);
  return tidy(pieces.join(""));
}

/** The sections, in the order the guide's tabs declare them. */
export function readSections(guideSource: string): SectionDeclaration[] {
  const file = parse("UserGuide.tsx", guideSource);
  const sections: SectionDeclaration[] = [];

  const visit = (node: ts.Node): void => {
    if (isElement(node) && tagNameOf(node) === "Tabs") {
      for (const attribute of attributesOf(node).properties) {
        if (!ts.isJsxAttribute(attribute) || attribute.name.getText() !== "tabs") continue;
        const value = attribute.initializer;
        if (!value || !ts.isJsxExpression(value) || !value.expression) continue;
        if (!ts.isArrayLiteralExpression(value.expression)) continue;
        for (const element of value.expression.elements) {
          if (!ts.isObjectLiteralExpression(element)) continue;
          let id: string | undefined;
          let label: string | undefined;
          let component: string | undefined;
          for (const property of element.properties) {
            if (!ts.isPropertyAssignment(property)) continue;
            const key = property.name.getText();
            const assigned = property.initializer;
            if (key === "id" && ts.isStringLiteral(assigned)) id = assigned.text;
            if (key === "label" && ts.isStringLiteral(assigned)) label = assigned.text;
            if (key === "content") {
              assigned.forEachChild(function find(child: ts.Node) {
                if (isElement(child)) component ??= tagNameOf(child);
                else child.forEachChild(find);
              });
              if (isElement(assigned)) component ??= tagNameOf(assigned);
            }
          }
          if (id && label && component) sections.push({ id, label, component });
        }
      }
    }
    node.forEachChild(visit);
  };

  visit(file);
  if (sections.length === 0) {
    throw new GuideIndexError(
      "No sections were found in UserGuide.tsx. The index is read from the tabs " +
        "declared there, so each one needs a plain id, label and component.",
    );
  }
  return sections;
}

/** Everything findable in one section. */
function readSection(section: SectionDeclaration, source: string): GuideEntry[] {
  const file = parse(`${section.component}.tsx`, source);
  const entries: GuideEntry[] = [];
  const base = { section: section.id, sectionLabel: section.label };

  const importsGuideCard = source.includes('Card') && /import \{[^}]*\bCard\b[^}]*\} from "\.\/parts"/.test(source);
  if (/<Card[\s/>]/.test(source) && !importsGuideCard) {
    throw new GuideIndexError(
      `${section.component}.tsx writes <Card> but does not import Card from "./parts". ` +
        "The guide's own Card carries the id a search result links to, so import it from there.",
    );
  }

  const visit = (node: ts.Node, card: string): void => {
    if (isElement(node)) {
      const tag = tagNameOf(node);

      if (tag === "Card") {
        const title = stringAttribute(node, "title");
        if (!title) {
          throw new GuideIndexError(
            `A card in ${section.component}.tsx has no plain title. Every card needs one ` +
              "so it can be found and linked to.",
          );
        }
        const description = stringAttribute(node, "description") ?? "";
        entries.push({
          ...base,
          kind: "card",
          label: title,
          anchor: cardId(title),
          card: "",
          text: tidy([description, proseOf(node, new Set(["Step", "Term"]))].join(" ")),
        });
        node.forEachChild((child) => visit(child, title));
        return;
      }

      if (tag === "Step") {
        const title = stringAttribute(node, "title");
        if (!title) {
          throw new GuideIndexError(
            `A step in ${section.component}.tsx has no plain title. Every step needs one ` +
              "so it can be found and linked to.",
          );
        }
        const where = stringAttribute(node, "where");
        entries.push({
          ...base,
          kind: "step",
          label: title,
          anchor: stepId(card, title),
          card,
          ...(numberAttribute(node, "number") === undefined
            ? {}
            : { step: numberAttribute(node, "number") }),
          ...(where ? { where } : {}),
          text: tidy([where ?? "", proseOf(node, new Set(["Term"]))].join(" ")),
        });
        node.forEachChild((child) => visit(child, card));
        return;
      }

      if (tag === "Term") {
        const term = stringAttribute(node, "term");
        if (!term) {
          throw new GuideIndexError(
            `A definition in ${section.component}.tsx has no plain term. Every definition ` +
              "needs one so it can be found and linked to.",
          );
        }
        entries.push({
          ...base,
          kind: "term",
          label: term,
          anchor: termId(term),
          card,
          text: proseOf(node, new Set()),
        });
        return;
      }
    }
    node.forEachChild((child) => visit(child, card));
  };

  visit(file, "");
  return entries;
}

/**
 * The whole guide, as one index.
 *
 * Throws rather than skipping whatever it cannot address: an entry quietly left
 * out is a search that quietly cannot find something.
 */
export function buildGuideIndex(guideSource: string, sections: SectionSources): GuideEntry[] {
  const entries: GuideEntry[] = [];

  for (const section of readSections(guideSource)) {
    const source = sections[section.component];
    if (source === undefined) {
      throw new GuideIndexError(
        `The guide declares a ${section.id} section drawn from ${section.component}, ` +
          "but that file was not among the sources given to the indexer.",
      );
    }
    const found = readSection(section, source);

    const seen = new Map<string, string>();
    for (const entry of found) {
      const clash = seen.get(entry.anchor);
      if (clash !== undefined) {
        throw new GuideIndexError(
          `Two things in the ${section.id} section address as "${entry.anchor}": ` +
            `"${clash}" and "${entry.label}". Two elements cannot share an id, so a link ` +
            "would reach whichever came first. Reword one of them.",
        );
      }
      seen.set(entry.anchor, entry.label);
    }
    entries.push(...found);
  }

  return entries;
}

/** The generated module, as text ready to write. */
export function renderIndexModule(entries: readonly GuideEntry[]): string {
  const lines = entries.map((entry) => `  ${JSON.stringify(entry)},`).join("\n");
  return `/**
 * The guide's search index. Generated -- do not edit.
 *
 * Run \`npm run guide:index\` after changing the guide. A test compares this
 * file with what the guide's own sections say, so a stale index fails the
 * build rather than quietly losing search results.
 */

import type { GuideEntry } from "./types";

export const GUIDE_INDEX: readonly GuideEntry[] = [
${lines}
];
`;
}
