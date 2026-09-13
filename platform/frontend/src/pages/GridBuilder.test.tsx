/**
 * Building a grid from a specification.
 *
 * What these hold is that the specification a modeller wrote is what reaches
 * the API -- tiles, resolution and the reasons beside them -- and that what
 * comes back, built or refused, is shown as the API stated it rather than
 * summarised into something friendlier.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GridBuilder } from "./GridBuilder";

let posted: { url: string; body: Record<string, unknown> }[] = [];
let refuseWith: string | null = null;

const BUILT = {
  grid: {
    id: "11111111-1111-1111-1111-111111111111",
    reference: "ph-grid-0.1.0",
    country_code: "PH",
    version: "0.1.0",
    label: "Luzon prototype grid",
    cell_count: 27,
    publication_state: "draft",
  },
  summary: {
    cells: 27,
    cells_at_base_resolution: 23,
    cells_by_refinement: { "Metro Manila": 4 },
    builder_version: "1.0.0",
    specification: {},
  },
};

function respond(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  posted = [];
  refuseWith = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if ((init?.method ?? "GET") === "POST" && url.includes("/grids/build/")) {
        posted.push({ url, body: JSON.parse(String(init?.body ?? "{}")) });
        return refuseWith
          ? respond(400, { detail: refuseWith })
          : respond(201, BUILT);
      }
      if (url.includes("/grids/")) {
        return respond(200, { count: 0, next: null, previous: null, results: [] });
      }
      return respond(200, { count: 0, next: null, previous: null, results: [] });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderBuilder() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <GridBuilder />
    </QueryClientProvider>,
  );
}

async function writeSpecification(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/^Country/), "ph");
  await user.type(screen.getByLabelText(/^Version/), "0.1.0");
  await user.type(screen.getByLabelText(/^Label/), "Luzon prototype grid");
  await user.type(screen.getByLabelText(/^Base resolution/), "0.5");
  await user.type(screen.getByLabelText(/^Tile 1 name/), "Luzon");
  await user.type(screen.getByLabelText(/^Tile 1 reason/), "Where the book sits");
  await user.type(screen.getByLabelText(/^Tile 1 minimum latitude/), "13");
  await user.type(screen.getByLabelText(/^Tile 1 maximum latitude/), "16");
  await user.type(screen.getByLabelText(/^Tile 1 minimum longitude/), "120");
  await user.type(screen.getByLabelText(/^Tile 1 maximum longitude/), "122");
}

describe("GridBuilder", () => {
  it("sends the specification rather than a cell file", async () => {
    const user = userEvent.setup();
    renderBuilder();

    await writeSpecification(user);
    await user.type(
      screen.getByLabelText(/^Open questions/),
      "No site conditions are attached to any cell.",
    );
    await user.click(screen.getByRole("button", { name: "Build the grid" }));

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]?.body).toMatchObject({
      country_code: "PH",
      version: "0.1.0",
      base_resolution_deg: "0.5",
      tiles: [
        {
          name: "Luzon",
          reason: "Where the book sits",
          min_latitude: "13",
          max_latitude: "16",
          min_longitude: "120",
          max_longitude: "122",
        },
      ],
      open_questions: ["No site conditions are attached to any cell."],
    });
  });

  it("says what was built, and that it is a draft", async () => {
    const user = userEvent.setup();
    renderBuilder();

    await writeSpecification(user);
    await user.click(screen.getByRole("button", { name: "Build the grid" }));

    expect(await screen.findByText(/ph-grid-0.1.0 built/)).toBeInTheDocument();
    expect(screen.getByText(/27 cells/)).toBeInTheDocument();
    expect(screen.getByText(/4 in Metro Manila/)).toBeInTheDocument();
    expect(screen.getByText(/nothing is approved by building it/)).toBeInTheDocument();
  });

  it("shows a refusal as the API wrote it", async () => {
    refuseWith =
      "This specification would generate about 60,000 cells and this installation builds at most 100.";
    const user = userEvent.setup();
    renderBuilder();

    await writeSpecification(user);
    await user.click(screen.getByRole("button", { name: "Build the grid" }));

    expect(await screen.findByText(/builds at most 100/)).toBeInTheDocument();
  });

  it("will not build until the specification says what it is", async () => {
    renderBuilder();

    expect(screen.getByRole("button", { name: "Build the grid" })).toBeDisabled();
  });

  it("takes more than one tile and more than one refinement", async () => {
    const user = userEvent.setup();
    renderBuilder();

    await user.click(screen.getByRole("button", { name: "Add a tile" }));
    await user.click(screen.getByRole("button", { name: "Add a refinement" }));

    expect(screen.getByLabelText(/^Tile 2 name/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Refinement 1 resolution/)).toBeInTheDocument();
  });
});
