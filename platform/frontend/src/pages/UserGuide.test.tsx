/**
 * The user guide: its sections open from the address, and the parts a new
 * user most needs are actually there.
 *
 * The guide reads no API, so these render it alone. What they guard is the
 * shape a reader relies on -- a section can be linked to, the glossary defines
 * the words the screens use, and the grid section explains how cells are made
 * -- rather than the wording, which should be free to improve.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { UserGuide } from "./UserGuide";

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

describe("the user guide", () => {
  it("opens on Start here and lists every section", () => {
    renderGuide();

    const sections = screen.getByRole("tablist", { name: "Guide sections" });
    expect(
      within(sections)
        .getAllByRole("tab")
        .map((tab) => tab.textContent),
    ).toEqual([
      "Start here",
      "Glossary",
      "Grids, cells and tiles",
      "Build a model",
      "Bring in a portfolio",
      "Run and read results",
      "Administration",
      "When something goes wrong",
    ]);
    expect(screen.getByRole("tab", { name: "Start here" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("heading", { name: "What CASS is for" })).toBeInTheDocument();
  });

  it("opens the section named in the address", () => {
    renderGuide("/guide?section=grids");

    expect(screen.getByRole("tab", { name: "Grids, cells and tiles" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      screen.getByRole("heading", { name: "How CASS turns a specification into cells" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: "Tiles, base cells and a refinement" }),
    ).toBeInTheDocument();
  });

  it("defines the grid terms in the glossary", () => {
    renderGuide("/guide?section=glossary");

    // A term is only defined if it sits in a definition list with its meaning.
    const terms = screen.getAllByRole("term").map((term) => term.textContent);
    for (const word of ["Tile", "Cell", "Refinement", "Base resolution", "Domain"]) {
      expect(terms).toContain(word);
    }
    expect(terms).toContain("Average annual loss (AAL)");
    expect(terms).toContain("Return period");
  });

  it("moves between sections from a link inside one", async () => {
    const user = userEvent.setup();
    renderGuide("/guide?section=model");

    await user.click(screen.getByRole("link", { name: "Grids, cells and tiles" }));

    expect(screen.getByRole("tab", { name: "Grids, cells and tiles" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("heading", { name: "Build a grid, step by step" })).toBeInTheDocument();
  });

  it("numbers the grid-building steps so each can be followed in order", () => {
    renderGuide("/guide?section=grids");

    const card = screen.getByRole("heading", { name: "Build a grid, step by step" }).closest("section");
    expect(card).not.toBeNull();
    const steps = within(card as HTMLElement).getAllByRole("heading", { level: 3 });
    expect(steps.map((step) => step.textContent)).toEqual([
      "Step 1: Start from a seed, if there is one",
      "Step 2: Choose the country",
      "Step 3: Give it a version",
      "Step 4: Give it a label",
      "Step 5: Set the base resolution",
      "Step 6: Leave the mapping tolerance at 0",
      "Step 7: Choose what the grid keeps",
      "Step 8: Add the tiles",
      "Step 9: Add refinements, if you want any",
      "Step 10: Write down anything still undecided",
      "Step 11: Check the cell counter",
      "Step 12: Build it",
    ]);
  });

  it("describes the seeds CASS ships and what a grid keeps", () => {
    renderGuide("/guide?section=grids");

    expect(screen.getByRole("heading", { name: "The seed grids CASS ships" })).toBeInTheDocument();
    const seeds = screen.getByRole("table", { name: "The seed grids" });
    expect(within(seeds).getAllByRole("row")).toHaveLength(11);
    expect(
      screen.getByText("4. What the grid keeps: land, and places near people or buildings"),
    ).toBeInTheDocument();
  });
});
