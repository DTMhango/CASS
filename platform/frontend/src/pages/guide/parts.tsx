/**
 * The pieces every section of the user guide is written with.
 *
 * Kept apart from the sections so each section file is only prose, and so a
 * new section looks like the others without copying their markup.
 */

import { createContext, useContext } from "react";
import { Link } from "react-router-dom";

import { Card as Surface, DefinitionRow } from "@/components/primitives";

import { cardId, stepId, termId } from "./anchors";

/**
 * The card a step or a definition is being written inside.
 *
 * Step titles repeat between cards, so a step's address has to carry its card.
 * Passing that down by hand on every step would be forgotten within a week, so
 * the card puts its own title here and the step reads it.
 */
const CardTitle = createContext("");

/**
 * A card in the guide, addressable so a search result can land on it.
 *
 * Shadows the design system's Card on purpose: a section writes <Card> as it
 * always did, and the guide's version adds the id and the context. Import it
 * from here rather than from the design system, or the card cannot be linked
 * to and the search indexer will say so.
 */
export function Card({
  title,
  description,
  children,
}: {
  title: string;
  description?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <CardTitle.Provider value={title}>
      <Surface title={title} description={description} id={cardId(title)}>
        {children}
      </Surface>
    </CardTitle.Provider>
  );
}

/** A numbered instruction: what to do, where on screen, and what happens. */
export function Step({
  number,
  title,
  where,
  children,
}: {
  number: number;
  title: string;
  where?: string;
  children: React.ReactNode;
}) {
  const card = useContext(CardTitle);
  return (
    <li className="guide-step" id={stepId(card, title)}>
      <span className="guide-step__number" aria-hidden="true">
        {number}
      </span>
      <div className="guide-step__body">
        <h3 className="guide-step__title">
          <span className="visually-hidden">Step {number}: </span>
          {title}
        </h3>
        {where ? (
          <p className="guide-step__where">
            <span className="visually-hidden">Where: </span>
            {where}
          </p>
        ) : null}
        <div className="guide-prose">{children}</div>
      </div>
    </li>
  );
}

export function Steps({ children }: { children: React.ReactNode }) {
  return <ol className="guide-steps">{children}</ol>;
}

/** A bulleted list. The global reset removes bullets, so the guide puts them back. */
export function Bullets({ children }: { children: React.ReactNode }) {
  return <ul className="guide-bullets">{children}</ul>;
}

/** The API call behind a set of steps, for anyone scripting it rather than clicking. */
export function Api({ children }: { children: string }) {
  return (
    <p className="guide-api">
      <span className="guide-api__label">For scripting (API)</span>
      <code className="mono">{children}</code>
    </p>
  );
}

/** A link to another section of the guide. */
export function SectionLink({ section, children }: { section: string; children: React.ReactNode }) {
  return <Link to={`/guide?section=${section}`}>{children}</Link>;
}

export function Terms({ children }: { children: React.ReactNode }) {
  return <dl className="guide-terms">{children}</dl>;
}

/** A table that scrolls sideways on a narrow screen rather than the page. */
export function GuideTable({ children }: { children: React.ReactNode }) {
  return (
    <div className="guide-table">
      <table className="data-table">{children}</table>
    </div>
  );
}

/** A worked example, set apart so it is not mistaken for an instruction. */
export function Example({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <aside className="guide-example" aria-label={title}>
      <p className="guide-example__title">{title}</p>
      <div className="guide-prose">{children}</div>
    </aside>
  );
}

/** One defined term, addressable by name. */
export function Term({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <DefinitionRow term={term} id={termId(term)}>
      {children}
    </DefinitionRow>
  );
}

/**
 * A link to one term in the glossary.
 *
 * The glossary is a different tab, so this carries both the section and the
 * term: the guide opens the tab and scrolls to the definition.
 */
export function GlossaryLink({ term, children }: { term: string; children?: React.ReactNode }) {
  return (
    <Link to={`/guide?section=glossary#${termId(term)}`}>{children ?? term.toLowerCase()}</Link>
  );
}
