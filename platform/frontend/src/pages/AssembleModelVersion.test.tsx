/**
 * Pairing a grid and a vulnerability set into a model version.
 *
 * The form asks for the two halves and a version and nothing else, and what
 * comes back says what still blocks publication rather than reading as an
 * approved model.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssembleModelVersion } from "./AssembleModelVersion";

let posted: Record<string, unknown>[] = [];
let refuseWith: string | null = null;
let halves = true;

const GRID = {
  id: "g1",
  reference: "ph-grid-0.1.0",
  country_code: "PH",
  version: "0.1.0",
  label: "Luzon",
  cell_count: 27,
  publication_state: "draft",
};

const SET = {
  id: "v1",
  country_code: "PH",
  version: "0.1.0-gem",
  source: "GEM",
  function_count: 484,
  imts_used: ["PGA"],
  publication_state: "draft",
};

const ASSEMBLED = {
  id: "m1",
  reference: "ph-qeq-0.1.0",
  country_code: "PH",
  version: "0.1.0",
  publication_blockers: ["No hazard set is attached."],
};

function respond(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function page(results: unknown[]) {
  return { count: results.length, next: null, previous: null, results };
}

beforeEach(() => {
  posted = [];
  refuseWith = null;
  halves = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/model-versions/assemble/")) {
        posted.push(JSON.parse(String(init?.body ?? "{}")));
        return refuseWith
          ? respond(400, { detail: refuseWith })
          : respond(201, ASSEMBLED);
      }
      if (url.includes("/grids/")) return respond(200, page(halves ? [GRID] : []));
      if (url.includes("/vulnerability-sets/"))
        return respond(200, page(halves ? [SET] : []));
      return respond(200, page([]));
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderCard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AssembleModelVersion />
    </QueryClientProvider>,
  );
}

describe("AssembleModelVersion", () => {
  it("sends the two halves and a version of its own", async () => {
    const user = userEvent.setup();
    renderCard();

    await user.selectOptions(await screen.findByLabelText(/^Grid/), "g1");
    await user.selectOptions(screen.getByLabelText(/^Vulnerability set/), "v1");
    await user.type(screen.getByLabelText(/^Model version/), "0.1.0");
    await user.click(
      screen.getByRole("button", { name: "Assemble the model version" }),
    );

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({
      grid: "g1",
      vulnerability_set: "v1",
      version: "0.1.0",
    });
  });

  it("says what still blocks publication rather than reading as approved", async () => {
    const user = userEvent.setup();
    renderCard();

    await user.selectOptions(await screen.findByLabelText(/^Grid/), "g1");
    await user.selectOptions(screen.getByLabelText(/^Vulnerability set/), "v1");
    await user.type(screen.getByLabelText(/^Model version/), "0.1.0");
    await user.click(
      screen.getByRole("button", { name: "Assemble the model version" }),
    );

    expect(await screen.findByText(/ph-qeq-0.1.0 assembled/)).toBeInTheDocument();
    expect(screen.getByText(/draft research prototype/)).toBeInTheDocument();
    expect(screen.getByText(/No hazard set is attached/)).toBeInTheDocument();
  });

  it("shows a mismatched pair refusal as the API wrote it", async () => {
    refuseWith =
      "The grid is for PH and the vulnerability set for ID. A model version pairing them would apply one country's buildings to another's ground motion.";
    const user = userEvent.setup();
    renderCard();

    await user.selectOptions(await screen.findByLabelText(/^Grid/), "g1");
    await user.selectOptions(screen.getByLabelText(/^Vulnerability set/), "v1");
    await user.type(screen.getByLabelText(/^Model version/), "0.1.0");
    await user.click(
      screen.getByRole("button", { name: "Assemble the model version" }),
    );

    expect(
      await screen.findByText(/The grid is for PH and the vulnerability set for ID/),
    ).toBeInTheDocument();
  });

  it("asks for the halves to be built first where there are none", async () => {
    halves = false;
    renderCard();

    expect(
      await screen.findByText("A model version needs both halves"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Assemble the model version" }),
    ).not.toBeInTheDocument();
  });
});
