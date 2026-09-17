/**
 * Searching the guide, driven as a reader drives it.
 *
 * The test that matters most is the last one: every result the index offers
 * must actually exist on the page it points at. An index and a page that drift
 * apart send every search result to the top of a section, which looks exactly
 * like a search that found nothing useful.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { UserGuide } from "@/pages/UserGuide";

import { GUIDE_INDEX } from "./index.generated";

function renderGuide(address = "/guide") {
  return render(
    <MemoryRouter
      initialEntries={[address]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/guide" element={<UserGuide />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** The box, once the index it searches has arrived. */
async function searchBox() {
  const box = await screen.findByRole("combobox", { name: "Search the guide" });
  await waitFor(() =>
    expect(screen.getByText(/definitions, steps and topics/)).toHaveTextContent(
      `${GUIDE_INDEX.length}`,
    ),
  );
  return box;
}

describe("searching the guide", () => {
  it("offers the definition of a word ahead of the pages that mention it", async () => {
    const user = userEvent.setup();
    renderGuide();

    await user.type(await searchBox(), "cohort");

    const results = await screen.findByRole("listbox", { name: "Results" });
    const first = within(results).getAllByRole("option")[0];
    expect(first).toHaveTextContent("Cohort");
    expect(first).toHaveTextContent("Definition");
    // Said plainly, so a reader knows which section they are about to open.
    expect(within(results).getByText("Glossary")).toBeInTheDocument();
  });

  it("searches sections other than the one being read", async () => {
    const user = userEvent.setup();
    renderGuide("/guide?section=grids");

    await user.type(await searchBox(), "quota share");

    const results = await screen.findByRole("listbox", { name: "Results" });
    expect(within(results).getAllByRole("option")[0]).toHaveTextContent("Quota share");
  });

  it("finds a word that appears only inside a warning, and shows where", async () => {
    const user = userEvent.setup();
    renderGuide();

    await user.type(await searchBox(), "USD");

    const results = await screen.findByRole("listbox", { name: "Results" });
    const options = within(results).getAllByRole("option");
    expect(options.length).toBeGreaterThan(0);
    expect(options[0]).toHaveTextContent("Bring in a portfolio");
  });

  it("opens the result the keyboard chose, at the thing itself", async () => {
    const user = userEvent.setup();
    renderGuide();

    await user.type(await searchBox(), "tile");
    await screen.findByRole("listbox", { name: "Results" });
    await user.keyboard("{Enter}");

    expect(await screen.findByRole("tab", { name: "Glossary" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await waitFor(() => expect(document.getElementById("term-tile")).not.toBeNull());
    // Marked, so the eye lands on the definition rather than near it.
    expect(document.getElementById("term-tile")).toHaveClass("guide-found");
  });

  it("moves down the list with the arrow keys", async () => {
    const user = userEvent.setup();
    renderGuide();

    const box = await searchBox();
    await user.type(box, "grid");
    const results = await screen.findByRole("listbox", { name: "Results" });
    const second = within(results).getAllByRole("option")[1];

    await user.keyboard("{ArrowDown}");

    expect(second).toHaveAttribute("aria-selected", "true");
    expect(box).toHaveAttribute("aria-activedescendant", second?.id ?? "");
  });

  it("says so, in plain words, when nothing matches", async () => {
    const user = userEvent.setup();
    renderGuide();

    await user.type(await searchBox(), "xyzzy");

    expect(await screen.findByText(/Nothing in the guide matches/)).toBeInTheDocument();
    expect(screen.queryByRole("listbox", { name: "Results" })).not.toBeInTheDocument();
  });

  it("closes on Escape, and clears on a second Escape", async () => {
    const user = userEvent.setup();
    renderGuide();

    const box = await searchBox();
    await user.type(box, "tile");
    await screen.findByRole("listbox", { name: "Results" });

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox", { name: "Results" })).not.toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(box).toHaveValue("");
  });

  it("comes back to the box when the slash key is pressed", async () => {
    const user = userEvent.setup();
    renderGuide();
    const box = await searchBox();
    box.blur();

    await user.keyboard("/");

    expect(box).toHaveFocus();
    // The slash itself is not typed into the box it just opened.
    expect(box).toHaveValue("");
  });
});

describe("every result the index offers", () => {
  const sections = [...new Set(GUIDE_INDEX.map((entry) => entry.section))];

  it.each(sections)("exists on the %s section", async (section) => {
    renderGuide(`/guide?section=${section}`);
    await screen.findByRole("tablist", { name: "Guide sections" });

    const missing = GUIDE_INDEX.filter(
      (entry) => entry.section === section && document.getElementById(entry.anchor) === null,
    ).map((entry) => `${entry.kind} "${entry.label}" (${entry.anchor})`);

    expect(missing, `${section}: the index points at things the page does not have`).toEqual([]);
  });
});
