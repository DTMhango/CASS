/**
 * Searching the guide.
 *
 * The browser's own find searches the page in front of it, and the guide only
 * ever has one section in the page, so a reader on "Grids" cannot use it to
 * find something defined under "Glossary". This searches all eight at once.
 *
 * It follows the combobox pattern the rest of CASS uses -- type to filter, the
 * arrow keys to move, Enter to go, Escape to close -- so it behaves like the
 * selectors elsewhere rather than being a second thing to learn.
 *
 * The index is loaded on its own, after the page: it is the whole guide's prose
 * a second time, and nobody should wait for it to read the first section.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { groupHits, searchGuide, snippetFor, RESULT_LIMIT, SHORTEST_QUERY } from "./match";
import type { GuideEntry, GuideHit } from "./types";

/** What each kind of result is called, in the words the guide itself uses. */
const KIND_LABEL = {
  term: "Definition",
  card: "Topic",
  step: "Step",
} as const;

/** Where a result sits, said in one line under its name. */
function whereItIs(entry: GuideEntry): string {
  const parts = [entry.sectionLabel];
  if (entry.card) parts.push(entry.card);
  if (entry.step !== undefined) parts.push(`Step ${entry.step}`);
  return parts.join(" › ");
}

export function GuideSearch() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [index, setIndex] = useState<readonly GuideEntry[] | null>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  useEffect(() => {
    let live = true;
    void import("./index.generated").then((module) => {
      if (live) setIndex(module.GUIDE_INDEX);
    });
    return () => {
      live = false;
    };
  }, []);

  // A slash focuses the box, as it does in most things that can be searched.
  // Ignored while somebody is typing somewhere else, so it cannot eat a slash
  // meant for a field.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
      const typing =
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
      if (typing) return;
      event.preventDefault();
      inputRef.current?.focus();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  const { hits, total } = useMemo(
    () => (index ? searchGuide(index, query) : { hits: [], total: 0 }),
    [index, query],
  );
  const groups = useMemo(() => groupHits(hits), [hits]);
  const asked = query.trim().length >= SHORTEST_QUERY;
  const showing = open && asked;

  const go = useCallback(
    (hit: GuideHit) => {
      setOpen(false);
      navigate(`/guide?section=${hit.entry.section}#${hit.entry.anchor}`);
    },
    [navigate],
  );

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (hits.length === 0) return;
      event.preventDefault();
      setOpen(true);
      setActive((current) => {
        const next = current + (event.key === "ArrowDown" ? 1 : -1);
        return (next + hits.length) % hits.length;
      });
    } else if (event.key === "Enter") {
      const hit = hits[active];
      if (showing && hit) {
        event.preventDefault();
        go(hit);
      }
    } else if (event.key === "Escape") {
      if (showing) {
        event.preventDefault();
        setOpen(false);
      } else if (query) {
        setQuery("");
      }
    }
  }

  const listId = "guide-search-results";
  const status = !asked
    ? ""
    : total === 0
      ? `No results for ${query}`
      : `${total} result${total === 1 ? "" : "s"} for ${query}`;

  return (
    <div role="search" className="guide-search">
      <label className="visually-hidden" htmlFor="guide-search-input">
        Search the guide
      </label>
      <div className="guide-search__field">
        <svg className="guide-search__icon" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="7" cy="7" r="4.5" />
          <path d="M10.5 10.5 L14 14" />
        </svg>
        <input
          ref={inputRef}
          id="guide-search-input"
          type="text"
          role="combobox"
          className="input guide-search__input"
          placeholder="Search the guide: a word, a button, or what you are trying to do"
          autoComplete="off"
          spellCheck={false}
          aria-autocomplete="list"
          aria-expanded={showing}
          aria-controls={showing ? listId : undefined}
          aria-activedescendant={showing && hits[active] ? `guide-search-hit-${active}` : undefined}
          aria-describedby="guide-search-hint"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
        />
        <kbd className="guide-search__key" aria-hidden="true">
          /
        </kbd>
      </div>
      <p className="guide-search__hint muted" id="guide-search-hint">
        Searches every section, including the {index?.length ?? 0} definitions, steps and topics
        in it. Press the slash key to come back here.
      </p>
      <p className="visually-hidden" role="status">
        {status}
      </p>

      {showing ? (
        <div
          className="guide-search__panel"
          role="presentation"
          // Handled once here rather than on every result: the pointer must not
          // take focus out of the field, which is where the keyboard is being
          // read, and a mouse moving through the list should move the choice.
          onMouseDown={(event) => event.preventDefault()}
          onMouseMove={(event) => {
            const at = positionOf(event.target);
            if (at >= 0 && at !== active) setActive(at);
          }}
          onClick={(event) => {
            const hit = hits[positionOf(event.target)];
            if (hit) go(hit);
          }}
        >
          {hits.length === 0 ? (
            <p className="guide-search__empty">
              Nothing in the guide matches <strong>{query}</strong>. Try one word rather than a
              phrase, or a plainer one: the guide avoids jargon, so &ldquo;shaking&rdquo; finds
              more than &ldquo;seismicity&rdquo;.
            </p>
          ) : (
            <>
              <ul className="guide-search__groups" id={listId} role="listbox" aria-label="Results">
                {groups.map((group) => (
                  <li key={group.section} className="guide-search__group" role="presentation">
                    <p className="guide-search__group-name" id={`guide-search-group-${group.section}`}>
                      {group.sectionLabel}
                    </p>
                    <ul role="group" aria-labelledby={`guide-search-group-${group.section}`}>
                      {group.hits.map((hit) => {
                        const at = hits.indexOf(hit);
                        const snippet = snippetFor(hit.entry, query);
                        return (
                          <li
                            key={hit.entry.anchor}
                            id={`guide-search-hit-${at}`}
                            data-at={at}
                            role="option"
                            aria-selected={at === active}
                            className={`guide-search__hit ${
                              at === active ? "guide-search__hit--active" : ""
                            }`.trim()}
                          >
                            <p className="guide-search__hit-name">
                              {hit.entry.label}
                              <span className="guide-search__kind">
                                {KIND_LABEL[hit.entry.kind]}
                              </span>
                            </p>
                            <p className="guide-search__hit-where">{whereItIs(hit.entry)}</p>
                            {snippet ? (
                              <p className="guide-search__hit-text">
                                {snippet.before}
                                {snippet.match ? <mark>{snippet.match}</mark> : null}
                                {snippet.after}
                              </p>
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                  </li>
                ))}
              </ul>
              {total > hits.length ? (
                <p className="guide-search__more">
                  Showing the {RESULT_LIMIT} best of {total} matches. Add another word to narrow
                  it down.
                </p>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}

/** Which result an event happened inside, or -1. */
function positionOf(target: EventTarget): number {
  const found = target instanceof Element ? target.closest("[data-at]") : null;
  return found ? Number(found.getAttribute("data-at")) : -1;
}
