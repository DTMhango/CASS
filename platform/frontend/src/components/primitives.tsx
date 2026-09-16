/**
 * The small components every screen is built from.
 *
 * Keeping them together makes the design system checkable: a screen that needs
 * a new kind of surface adds it here rather than inventing a local style.
 */

import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import "./primitives.css";

// -- buttons ----------------------------------------------------------------

type ButtonVariant = "primary" | "secondary" | "outline" | "ghost" | "danger";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "sm" | "md";
  busy?: boolean;
}

export function Button({
  variant = "secondary",
  size = "md",
  busy = false,
  disabled,
  children,
  className = "",
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      className={`btn btn--${variant} btn--${size} ${className}`.trim()}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {busy ? <span className="btn__spinner" aria-hidden="true" /> : null}
      {children}
    </button>
  );
}

// -- surfaces ---------------------------------------------------------------

interface CardProps {
  title?: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  padded?: boolean;
}

export function Card({ title, description, actions, children, padded = true }: CardProps) {
  return (
    <section className="card">
      {title || actions ? (
        <header className="card__header">
          <div>
            {title ? <h2 className="card__title">{title}</h2> : null}
            {description ? <p className="card__description">{description}</p> : null}
          </div>
          {actions ? <div className="card__actions">{actions}</div> : null}
        </header>
      ) : null}
      <div className={padded ? "card__body" : "card__body card__body--flush"}>{children}</div>
    </section>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="page-header__text">
        <h1>{title}</h1>
        {description ? <p className="page-header__description">{description}</p> : null}
      </div>
      {actions ? <div className="page-header__actions">{actions}</div> : null}
    </header>
  );
}

// -- feedback ---------------------------------------------------------------

type NoticeTone = "info" | "warning" | "error" | "ok";

export function Notice({
  tone = "info",
  title,
  children,
  action,
}: {
  tone?: NoticeTone;
  title?: React.ReactNode;
  children?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div
      className={`notice notice--${tone}`}
      role={tone === "error" ? "alert" : "status"}
    >
      <div className="notice__body">
        {title ? <p className="notice__title">{title}</p> : null}
        {children ? <div className="notice__content">{children}</div> : null}
      </div>
      {action ? <div className="notice__action">{action}</div> : null}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty-state">
      <p className="empty-state__title">{title}</p>
      {description ? <p className="empty-state__description">{description}</p> : null}
      {action ? <div className="empty-state__action">{action}</div> : null}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="spinner" role="status">
      <span className="spinner__dot" aria-hidden="true" />
      <span className="visually-hidden">{label}</span>
    </div>
  );
}

// -- data display -----------------------------------------------------------

export function MetricTile({
  label,
  value,
  unit,
  footnote,
  tone,
}: {
  label: string;
  value: React.ReactNode;
  unit?: string;
  footnote?: React.ReactNode;
  tone?: "default" | "muted";
}) {
  return (
    <div className={`metric-tile ${tone === "muted" ? "metric-tile--muted" : ""}`.trim()}>
      <p className="metric-tile__label">{label}</p>
      <p className="metric-tile__value numeric">
        {value}
        {unit ? <span className="metric-tile__unit">{unit}</span> : null}
      </p>
      {footnote ? <p className="metric-tile__footnote">{footnote}</p> : null}
    </div>
  );
}

export function DefinitionRow({
  term,
  children,
}: {
  term: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="definition-row">
      <dt>{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}

// -- form controls ----------------------------------------------------------

export function Field({
  label,
  hint,
  error,
  required,
  htmlFor,
  children,
}: {
  label: React.ReactNode;
  hint?: React.ReactNode;
  error?: React.ReactNode;
  required?: boolean;
  htmlFor: string;
  children: React.ReactNode;
}) {
  const hintId = `${htmlFor}-hint`;
  const errorId = `${htmlFor}-error`;
  return (
    <div className={`field ${error ? "field--invalid" : ""}`.trim()}>
      <label className="field__label" htmlFor={htmlFor}>
        {label}
        {required ? (
          <span className="field__required" aria-hidden="true">
            {" "}
            *
          </span>
        ) : null}
        {required ? <span className="visually-hidden"> (required)</span> : null}
      </label>
      {hint ? (
        <p className="field__hint" id={hintId}>
          {hint}
        </p>
      ) : null}
      {children}
      {error ? (
        <p className="field__error" id={errorId} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export const TextInput = (props: React.InputHTMLAttributes<HTMLInputElement>) => (
  <input {...props} className={`input ${props.className ?? ""}`.trim()} />
);

export const TextArea = (props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) => (
  <textarea {...props} className={`input input--area ${props.className ?? ""}`.trim()} />
);

/** A native select, for a handful of fixed choices where there is nothing to search. */
export const Select = (props: React.SelectHTMLAttributes<HTMLSelectElement>) => (
  <select {...props} className={`input input--select ${props.className ?? ""}`.trim()} />
);

// -- combobox ---------------------------------------------------------------

export interface ComboboxOption {
  value: string;
  label: string;
  /** A second line under the label, searched with it. */
  detail?: string;
  /** Words that find the option without being shown, such as a country's ISO codes. */
  keywords?: readonly string[];
  disabled?: boolean;
}

/** Lower case with accents removed, so "turkiye" finds "Türkiye". */
function fold(text: string) {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

/**
 * Options holding every word typed, in any order and anywhere in the label,
 * detail or keywords. An exact keyword comes first, then a label that starts
 * with what was typed, so "isl" finds Iceland ahead of every island.
 */
function filterOptions(options: readonly ComboboxOption[], text: string) {
  const whole = fold(text).trim();
  const terms = whole.split(/\s+/).filter(Boolean);
  if (terms.length === 0) return options;
  const rank = (option: ComboboxOption) =>
    (option.keywords ?? []).some((keyword) => fold(keyword) === whole)
      ? 0
      : fold(option.label).startsWith(whole)
        ? 1
        : 2;
  return options
    .filter((option) => {
      const haystack = fold(
        [option.label, option.detail ?? "", ...(option.keywords ?? [])].join(" "),
      );
      return terms.every((term) => haystack.includes(term));
    })
    .map((option, index) => ({ option, index, rank: rank(option) }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((item) => item.option);
}

/** The first choosable option from ``start``, moving by ``step``, or -1. */
function nextEnabled(options: readonly ComboboxOption[], start: number, step: 1 | -1) {
  for (let index = start; index >= 0 && index < options.length; index += step) {
    if (!options[index]!.disabled) return index;
  }
  return -1;
}

/** The index of the option an event happened inside, or -1. */
function optionIndex(target: EventTarget) {
  const found = target instanceof Element ? target.closest("[data-index]") : null;
  return found ? Number(found.getAttribute("data-index")) : -1;
}

/**
 * A selector that can be typed into to find an option.
 *
 * For picking a record or a code from a list that grows -- countries, portfolios,
 * model versions -- where scrolling a native select is the slow way to find one.
 * Typing filters the list; the arrow keys, Enter and Escape work as the WAI-ARIA
 * combobox pattern describes. The list is drawn over the page rather than inside
 * the field's container, so a card or a scrolling table does not clip it.
 */
export function Combobox({
  id,
  "aria-label": ariaLabel,
  options,
  value,
  onChange,
  placeholder,
  disabled = false,
  noMatch = "Nothing matches",
}: {
  id?: string;
  "aria-label"?: string;
  options: readonly ComboboxOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  /** Said in place of the list when nothing matches what was typed. */
  noMatch?: string;
}) {
  const generated = useId();
  const inputId = id ?? `${generated}input`;
  const listId = `${generated}list`;
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState<string | null>(null);
  const [active, setActive] = useState(-1);
  const [position, setPosition] = useState<React.CSSProperties>({});

  const selected = options.find((option) => option.value === value);
  const visible = useMemo(() => filterOptions(options, query ?? ""), [options, query]);
  const showingList = open && visible.length > 0;

  function openList() {
    if (disabled) return;
    const current = visible.findIndex((option) => option.value === value && !option.disabled);
    setActive(current >= 0 ? current : nextEnabled(visible, 0, 1));
    setOpen(true);
  }

  function close() {
    setOpen(false);
    setQuery(null);
    setActive(-1);
  }

  function choose(option: ComboboxOption) {
    if (option.disabled) return;
    onChange(option.value);
    close();
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        openList();
        return;
      }
      const step = event.key === "ArrowDown" ? 1 : -1;
      const from = active < 0 ? (step === 1 ? 0 : visible.length - 1) : active + step;
      const next = nextEnabled(visible, from, step);
      if (next >= 0) setActive(next);
    } else if (event.key === "Enter" && open) {
      event.preventDefault();
      const option = visible[active];
      if (option) choose(option);
    } else if (event.key === "Escape" && (open || query !== null)) {
      event.preventDefault();
      close();
    } else if (event.key === "Tab") {
      close();
    }
  }

  // Placed against the field on every scroll and resize, below it where there
  // is room and above it where there is not.
  useLayoutEffect(() => {
    if (!open) return;
    function place() {
      const box = inputRef.current?.getBoundingClientRect();
      if (!box) return;
      const width = Math.max(box.width, 240);
      const below = window.innerHeight - box.bottom;
      const upward = below < 240 && box.top > below;
      const room = (upward ? box.top : below) - 12;
      setPosition({
        left: Math.max(8, Math.min(box.left, window.innerWidth - width - 8)),
        width,
        maxHeight: Math.max(120, Math.min(320, room)),
        ...(upward ? { bottom: window.innerHeight - box.top + 4 } : { top: box.bottom + 4 }),
      });
    }
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open || active < 0) return;
    listRef.current
      ?.querySelector<HTMLElement>(`[data-index="${active}"]`)
      ?.scrollIntoView?.({ block: "nearest" });
  }, [open, active]);

  return (
    <div className="combobox">
      <input
        ref={inputRef}
        id={inputId}
        type="text"
        role="combobox"
        className="input combobox__input"
        aria-label={ariaLabel}
        aria-autocomplete="list"
        aria-expanded={showingList}
        aria-controls={showingList ? listId : undefined}
        aria-activedescendant={showingList && active >= 0 ? `${listId}-${active}` : undefined}
        autoComplete="off"
        spellCheck={false}
        placeholder={placeholder}
        disabled={disabled}
        value={query ?? selected?.label ?? ""}
        onChange={(event) => {
          const text = event.target.value;
          setQuery(text);
          setActive(nextEnabled(filterOptions(options, text), 0, 1));
          setOpen(true);
        }}
        onClick={(event) => {
          // Selected on click rather than on focus, where the mouse-up that
          // follows would undo it, so typing replaces the current choice.
          if (query === null) event.currentTarget.select();
          if (!open) openList();
        }}
        onFocus={(event) => event.currentTarget.select()}
        onBlur={close}
        onKeyDown={onKeyDown}
      />
      <button
        type="button"
        className="combobox__toggle"
        tabIndex={-1}
        aria-label="Show options"
        aria-expanded={showingList}
        aria-controls={showingList ? listId : undefined}
        disabled={disabled}
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => {
          if (open) {
            close();
          } else {
            inputRef.current?.focus();
            openList();
          }
        }}
      />
      {open
        ? createPortal(
            <div
              className="combobox__popup"
              style={position}
              role="presentation"
              // Keeps focus in the field, so choosing an option is not a blur.
              // The keyboard is handled there too, which is why the pointer is
              // handled here once rather than on each option.
              onMouseDown={(event) => event.preventDefault()}
              onMouseMove={(event) => {
                const index = optionIndex(event.target);
                if (index >= 0 && index !== active && !visible[index]?.disabled) {
                  setActive(index);
                }
              }}
              onClick={(event) => {
                const option = visible[optionIndex(event.target)];
                if (option) choose(option);
              }}
            >
              {visible.length === 0 ? (
                <p className="combobox__empty" role="status">
                  {options.length === 0 ? "Nothing to choose from" : noMatch}
                </p>
              ) : (
                <ul ref={listRef} id={listId} role="listbox" className="combobox__list">
                  {visible.map((option, index) => (
                    <li
                      key={option.value}
                      id={`${listId}-${index}`}
                      data-index={index}
                      role="option"
                      aria-selected={option.value === value}
                      aria-disabled={option.disabled || undefined}
                      className={[
                        "combobox__option",
                        index === active ? "combobox__option--active" : "",
                        option.disabled ? "combobox__option--disabled" : "",
                      ]
                        .filter(Boolean)
                        .join(" ")}
                    >
                      <span className="combobox__label">{option.label}</span>
                      {option.detail ? (
                        <span className="combobox__detail">{option.detail}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}

// -- progressive disclosure -------------------------------------------------

/**
 * Section 3 asks for progressive disclosure of advanced engine settings, and
 * for technical detail to stay reachable behind analyst language.
 */
export function Disclosure({
  summary,
  children,
  defaultOpen = false,
}: {
  summary: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details className="disclosure" open={defaultOpen}>
      <summary className="disclosure__summary">{summary}</summary>
      <div className="disclosure__content">{children}</div>
    </details>
  );
}
