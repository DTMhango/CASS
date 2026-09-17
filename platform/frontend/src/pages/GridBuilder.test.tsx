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

/** What the server says the specification on the screen would generate. */
const ESTIMATE = {
  cells: 28,
  exact: false,
  cells_from_tiles: 24,
  cells_by_refinement: [{ name: "Metro Manila", resolution_deg: "0.25", cells: 4 }],
  candidates: 28,
  removed_as_sea: 0,
  removed_as_unsettled: 0,
  uncovered_land: null as null | {
    cells: number;
    examples: { cells: number; latitude: number; longitude: number }[];
  },
  counted_tiles: 1,
  incomplete: 0,
  problems: [] as string[],
  limit: 500000,
  within_limit: true,
  is_upper_bound: true,
  estimated: true,
  storage: {
    hazard_set_mb_per_thousand_years: 7,
    package_mb_per_thousand_years: 5,
    basis: "A ceiling.",
  },
};

const COUNTRIES = [
  { code: "PH", name: "Philippines", parts: ["Philippines"], bounds: {}, seeded: true },
  { code: "QA", name: "Qatar", parts: ["Qatar"], bounds: {}, seeded: true },
  { code: "FR", name: "France", parts: ["France"], bounds: {}, seeded: false },
];

const SEEDS = [
  {
    country_code: "QA",
    label: "Qatar seed grid",
    version: "1.0.0-seed",
    base_resolution_deg: "0.0125",
    tiles: 1,
    refinements: 0,
    domain: { clip_to_land: true, skip_unsettled: true },
    cells: 8752,
  },
];

const QATAR = {
  specification: {
    country_code: "QA",
    version: "1.0.0-seed",
    label: "Qatar seed grid",
    base_resolution_deg: "0.0125",
    mapping_tolerance_km: "0",
    domain: {
      clip_to_land: true,
      coast_buffer_km: "5",
      skip_unsettled: true,
      settlement_buffer_km: "5",
    },
    tiles: [
      {
        name: "Qatar",
        reason: "The whole peninsula; the domain removes the sea.",
        min_latitude: "24.45",
        max_latitude: "26.2",
        min_longitude: "50.7",
        max_longitude: "51.7",
      },
    ],
    refinements: [],
    open_questions: ["Earthquake hazard here is low.", "Site conditions are not represented."],
    notes: "Seed grid shipped with CASS.",
  },
  measured: { cells: 8752 },
};

let posted: { url: string; body: Record<string, unknown> }[] = [];
let estimated: Record<string, unknown>[] = [];
let refuseWith: string | null = null;
let estimate = ESTIMATE;

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
    removed_as_sea: 0,
    removed_as_unsettled: 0,
    builder_version: "1.1.0",
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
  estimated = [];
  refuseWith = null;
  estimate = ESTIMATE;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if ((init?.method ?? "GET") === "POST" && url.includes("/grids/estimate/")) {
        estimated.push(JSON.parse(String(init?.body ?? "{}")));
        return respond(200, estimate);
      }
      if ((init?.method ?? "GET") === "POST" && url.includes("/grids/build/")) {
        posted.push({ url, body: JSON.parse(String(init?.body ?? "{}")) });
        return refuseWith
          ? respond(400, { detail: refuseWith })
          : respond(201, BUILT);
      }
      if (url.includes("/grids/countries/")) {
        return respond(200, COUNTRIES);
      }
      if (url.includes("/grids/seeds/qa/")) {
        return respond(200, QATAR);
      }
      if (url.includes("/grids/seeds/")) {
        return respond(200, SEEDS);
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
  const country = screen.getByRole("combobox", { name: /^Country/ });
  await waitFor(() => expect(country).not.toBeDisabled());
  await user.type(country, "ph");
  await screen.findByRole("option", { name: /Philippines \(PH\)/ });
  await user.keyboard("{Enter}");
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

  it("says what the specification would generate, before it is built", async () => {
    const user = userEvent.setup();
    renderBuilder();

    await writeSpecification(user);

    expect(await screen.findByText("28 cells at most")).toBeInTheDocument();
    expect(screen.getByText(/24 at the base resolution/)).toBeInTheDocument();
    expect(screen.getByText(/4 in Metro Manila at 0.25/)).toBeInTheDocument();
    // Only what decides the cells is asked about: a label or a reason changes
    // nothing about how many there are, and the country and domain do.
    expect(Object.keys(estimated[estimated.length - 1] ?? {}).sort()).toEqual([
      "base_resolution_deg",
      "country_code",
      "domain",
      "refinements",
      "tiles",
    ]);
  });

  it("will not build what the count says the server would refuse", async () => {
    estimate = { ...ESTIMATE, cells: 6_000_000, limit: 500_000, within_limit: false };
    const user = userEvent.setup();
    renderBuilder();

    await writeSpecification(user);

    expect(
      await screen.findByText(/More cells than this installation builds/),
    ).toBeInTheDocument();
    expect(screen.getByText(/halving it quadruples the count/)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Build the grid" })).toBeDisabled(),
    );
    expect(posted).toHaveLength(0);
  });

  it("names what the build would refuse while it can still be changed", async () => {
    estimate = {
      ...ESTIMATE,
      problems: ["Refinement 'Metro Manila' is at 1 degrees, which is not finer than the base 0.5."],
    };
    const user = userEvent.setup();
    renderBuilder();

    await writeSpecification(user);

    expect(await screen.findByText(/is not finer than the base/)).toBeInTheDocument();
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

  it("keeps only land near where people live or build, unless told otherwise", async () => {
    const user = userEvent.setup();
    renderBuilder();

    await writeSpecification(user);
    await user.click(
      screen.getByRole("checkbox", { name: /Skip land with no buildings or people nearby/ }),
    );
    await user.click(screen.getByRole("button", { name: "Build the grid" }));

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]?.body.domain).toEqual({
      clip_to_land: true,
      coast_buffer_km: "5",
      skip_unsettled: false,
      settlement_buffer_km: "5",
    });
  });

  it("says how many cells the domain leaves out, and what the hazard would store", async () => {
    estimate = {
      ...ESTIMATE,
      cells: 8752,
      exact: true,
      is_upper_bound: false,
      cells_by_refinement: [],
      candidates: 11200,
      removed_as_sea: 2424,
      removed_as_unsettled: 24,
      storage: { hazard_set_mb_per_thousand_years: 2100, package_mb_per_thousand_years: 1575, basis: "" },
    };
    const user = userEvent.setup();
    renderBuilder();

    await writeSpecification(user);

    expect(await screen.findByText("8,752 cells")).toBeInTheDocument();
    expect(screen.getByText(/2,424 over the sea and 24 on empty land/)).toBeInTheDocument();
    expect(screen.getByText(/at most 2.1 GB for every thousand simulated years/)).toBeInTheDocument();
  });

  it("warns where the country's land is in no tile", async () => {
    estimate = {
      ...ESTIMATE,
      exact: true,
      uncovered_land: { cells: 13, examples: [{ cells: 13, latitude: -4.79, longitude: 115.82 }] },
    };
    const user = userEvent.setup();
    renderBuilder();

    await writeSpecification(user);

    expect(await screen.findByText(/Some of the country's land is in no tile/)).toBeInTheDocument();
    expect(screen.getByText(/-4.79, 115.82/)).toBeInTheDocument();
  });

  it("starts a specification from a seed CASS ships", async () => {
    const user = userEvent.setup();
    renderBuilder();

    const seed = await screen.findByRole("combobox", { name: /^Seed/ });
    await user.type(seed, "qa");
    await screen.findByRole("option", { name: /Qatar seed grid/ });
    await user.keyboard("{Enter}");
    await user.click(screen.getByRole("button", { name: "Load into the form" }));

    expect(await screen.findByText(/The QA seed is in the form/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Label/)).toHaveValue("Qatar seed grid");
    expect(screen.getByLabelText(/^Base resolution/)).toHaveValue("0.0125");
    expect(screen.getByLabelText(/^Tile 1 name/)).toHaveValue("Qatar");
    expect(screen.getByLabelText(/^Open questions/)).toHaveValue(
      "Earthquake hazard here is low.\nSite conditions are not represented.",
    );

    await user.click(screen.getByRole("button", { name: "Build the grid" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]?.body).toMatchObject({ country_code: "QA", version: "1.0.0-seed" });
  });
});
