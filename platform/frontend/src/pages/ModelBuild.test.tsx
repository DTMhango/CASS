/**
 * Hazard sets in the model build workspace.
 *
 * What these hold is that a catalogue's length is stated as the span it really
 * covers -- every logic-tree path included -- and that a footprint binned against
 * intensity bins that have since changed says so, and offers to rebuild it from
 * the calculation CASS kept rather than leaving it to fail at packaging.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HazardSet } from "@/api/types";

import { HazardSets } from "./ModelBuild";

const SET: HazardSet = {
  id: "11111111-1111-1111-1111-111111111111",
  reference: "id-hazard-2024-abcd1234",
  country_code: "ID",
  version: "2024-abcd1234",
  label: "PuSGeN 2024, Jakarta and Bandung",
  source_model: "PuSGeN 2024",
  licence: "CC BY-SA 4.0",
  licence_cleared: true,
  grid: "22222222-2222-2222-2222-222222222222",
  engine_version: "OpenQuake engine 3.23.4",
  investigation_time: 50,
  stochastic_event_sets: 10,
  logic_tree_paths: 20,
  effective_time: 10000,
  event_count: 271939,
  cell_count: 962,
  footprint_row_count: 136000000,
  imts: ["PGA", "SA(0.3)"],
  samples_above_range: 0,
  openquake_calculation_removed: true,
  rebuild: {
    intensity_bins_current: true,
    datastore_available: true,
    datastore_expires_at: "2026-12-15T00:00:00Z",
    unavailable_reason: "",
    rebuilt_as: null,
    rebuilt_from: null,
  },
  publication_state: "draft",
  notes: "",
  created_at: "2026-09-16T00:00:00Z",
};

let served: HazardSet[] = [SET];
let rebuilds: string[] = [];

function respond(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  served = [SET];
  rebuilds = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/session/")) {
        return respond(200, {
          authenticated: true,
          user: {
            id: "u",
            username: "modeller",
            capabilities: { publish_models: true, approve_gates: false, administer_platform: false },
          },
        });
      }
      if ((init?.method ?? "GET") === "POST" && url.includes("/rebuild/")) {
        rebuilds.push(url);
        return respond(202, {
          run: "33333333-3333-3333-3333-333333333333",
          hazard_run: "44444444-4444-4444-4444-444444444444",
          state: "queued",
        });
      }
      if (url.includes("/hazard-sets/")) {
        return respond(200, { count: served.length, next: null, previous: null, results: served });
      }
      return respond(200, { count: 0, next: null, previous: null, results: [] });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderSets() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <HazardSets versions={[]} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("HazardSets", () => {
  it("states the span a catalogue covers across every path it pooled", async () => {
    renderSets();

    // Fifty years times ten sets times twenty paths, not fifty times ten.
    expect(await screen.findByText(/10,000 yrs/)).toBeInTheDocument();
  });

  it("says nothing about a footprint binned against the current bins", async () => {
    renderSets();

    await screen.findByText(/10,000 yrs/);
    expect(screen.queryByText(/have since changed/)).not.toBeInTheDocument();
  });

  it("offers to rebuild a footprint binned against bins that have changed", async () => {
    served = [{ ...SET, rebuild: { ...SET.rebuild!, intensity_bins_current: false } }];
    const user = userEvent.setup();
    renderSets();

    expect(await screen.findByText(/have since changed, so it cannot be packaged/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Rebuild the footprint" }));

    await waitFor(() => expect(rebuilds).toHaveLength(1));
    expect(rebuilds[0]).toContain(`/hazard-sets/${SET.id}/rebuild/`);
    expect(await screen.findByRole("link", { name: /Follow it on the run monitor/ })).toHaveAttribute(
      "href",
      "/runs/33333333-3333-3333-3333-333333333333",
    );
  });

  it("says why a footprint whose calculation has expired cannot be rebuilt", async () => {
    served = [
      {
        ...SET,
        rebuild: {
          ...SET.rebuild!,
          intensity_bins_current: false,
          datastore_available: false,
          unavailable_reason: "The calculation is no longer stored.",
        },
      },
    ];
    renderSets();

    expect(await screen.findByText(/The calculation is no longer stored/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rebuild the footprint" })).not.toBeInTheDocument();
  });

  it("names the set a rebuilt one came from", async () => {
    served = [{ ...SET, rebuild: { ...SET.rebuild!, rebuilt_from: "id-hazard-2024-old" } }];
    renderSets();

    expect(await screen.findByText(/Rebuilt from id-hazard-2024-old/)).toBeInTheDocument();
  });
});
