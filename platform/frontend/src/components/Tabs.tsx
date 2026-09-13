/**
 * Tabs within one product area.
 *
 * The sidebar is the order work happens in: a portfolio, then a run, then its
 * results. Work that is neither before nor after its neighbours -- reviewing an
 * import while another portfolio is being fixed, or a hazard run and the
 * package it feeds, which a modeller moves between all day -- is not a step,
 * and giving each of those its own sidebar entry turned a sequence into a menu.
 * Those live here instead, side by side inside the area they belong to.
 *
 * The chosen tab is in the address, so a link to a tab opens on it and the
 * browser's own back button steps between them.
 */

import { useSearchParams } from "react-router-dom";

import "./Tabs.css";

export interface TabDefinition {
  id: string;
  label: string;
  /** What this tab is for, read by anyone who cannot see the layout. */
  description?: string;
  content: () => React.ReactNode;
}

export function Tabs({
  tabs,
  parameter = "tab",
  label,
}: {
  /** At least one: a tab strip with nothing in it has nothing to show. */
  tabs: [TabDefinition, ...TabDefinition[]];
  parameter?: string;
  /** Names the set for a screen reader: "Exposure sections". */
  label: string;
}) {
  const [params, setParams] = useSearchParams();
  const requested = params.get(parameter);
  const [first] = tabs;
  const active = tabs.find((tab) => tab.id === requested) ?? first;

  function choose(id: string) {
    const next = new URLSearchParams(params);
    if (id === first.id) next.delete(parameter);
    else next.set(parameter, id);
    // Replace rather than push: flipping between tabs is not navigation a
    // person wants to walk back through one step at a time.
    setParams(next, { replace: true });
  }

  return (
    <>
      <div className="tabs" role="tablist" aria-label={label}>
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`tab-${tab.id}`}
            aria-selected={tab.id === active.id}
            aria-controls={`panel-${tab.id}`}
            className={`tabs__tab ${tab.id === active.id ? "tabs__tab--active" : ""}`.trim()}
            onClick={() => choose(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`panel-${active.id}`}
        aria-labelledby={`tab-${active.id}`}
        className="tabs__panel"
      >
        {active.description ? <p className="tabs__description">{active.description}</p> : null}
        {active.content()}
      </div>
    </>
  );
}
