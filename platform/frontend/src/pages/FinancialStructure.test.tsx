/**
 * The financial structure workspace.
 *
 * What these hold to account is that the screen states the structure rather
 * than tidying it. A contract the engine will not apply must say so where the
 * contract is read, not in a footnote; value no treaty reaches must appear as
 * money; and a blocking finding must be visible without opening anything,
 * because a person who does not open it is exactly the person about to run a
 * model over the gap.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FinancialStructureSummary } from "@/api/types";
import { WorkingContextProvider } from "@/context/WorkingContext";

import { FinancialStructure } from "./FinancialStructure";

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";
const EXPOSURE_ID = "22222222-2222-2222-2222-222222222222";

const EXPOSURE = {
  id: EXPOSURE_ID,
  project: PROJECT_ID,
  name: "Jakarta facultative",
  version: 1,
  state: "published",
  is_usable_by_runs: true,
  location_count: 3,
  total_tiv: "9000000.00",
  run_currency: "USD",
  supported_perspectives: [],
  unmodelled_subperils: [],
};

const STRUCTURE: FinancialStructureSummary = {
  exposure_version: EXPOSURE_ID,
  currency: "USD",
  total_tiv: "9000000.00",
  location_count: 3,
  layers: [
    {
      account: "ACC-1",
      policy: "POL-1",
      layer_number: 1,
      participation: "0.5",
      limit: "5000000",
      attachment: "1000000",
      deductible: "50000",
      policy_limit: "6000000",
      perils: ["QEQ"],
      inception: "2026-01-01",
      expiry: "2026-12-31",
    },
  ],
  contracts: [
    {
      number: 1,
      layer_number: 1,
      name: "Cat XL layer 1",
      type: "CXL",
      type_label: "Catastrophe excess of loss",
      perils: ["QEQ"],
      inuring_priority: 1,
      ceded_percent: "0.9",
      placed_percent: "1.0",
      risk_limit: null,
      risk_attachment: null,
      occurrence_limit: "5000000",
      occurrence_attachment: "2000000",
      currency: "USD",
      scope_rows: 1,
      scope_tiv: "9000000.00",
      locations_reached: 3,
      applied_by_the_engine: true,
      notes: [],
    },
    {
      number: 2,
      layer_number: 1,
      name: "Facultative on the Jakarta tower",
      type: "FAC",
      type_label: "Facultative",
      perils: ["QEQ"],
      inuring_priority: 2,
      ceded_percent: "0.25",
      placed_percent: "1.0",
      risk_limit: "2000000",
      risk_attachment: "500000",
      occurrence_limit: null,
      occurrence_attachment: null,
      currency: "USD",
      scope_rows: 1,
      scope_tiv: "5000000.00",
      locations_reached: 1,
      applied_by_the_engine: false,
      notes: [
        "CASS runs reinsurance at portfolio level on the patched worker (ADR 10), so a per-risk contract is read and reconciled here but not applied in a run.",
      ],
    },
  ],
  inuring_order: [
    { priority: 1, contracts: [1] },
    { priority: 2, contracts: [2] },
  ],
  uncovered_locations: 0,
  uncovered_tiv: "0.00",
  findings: [
    {
      code: "no_limit",
      subject: "ACC-2/POL-9 layer 1",
      message: "Neither a layer limit nor a policy limit is stated.",
      blocking: false,
    },
  ],
  has_accounts: true,
  has_contracts: true,
};

let structure: FinancialStructureSummary = STRUCTURE;

function routeFor(url: string): unknown {
  if (url.includes("/session/")) {
    return {
      authenticated: true,
      user: {
        id: "u1",
        username: "ada",
        email: "",
        first_name: "Ada",
        last_name: "Analyst",
        full_name: "Ada Analyst",
        platform_role: "analyst",
        job_title: "",
        local_install_approved: false,
        capabilities: {
          publish_models: false,
          approve_gates: false,
          administer_platform: false,
        },
      },
    };
  }
  if (url.includes("/financial-structure/")) {
    return structure;
  }
  if (url.includes("/exposure-versions/")) {
    return { count: 1, next: null, previous: null, results: [EXPOSURE] };
  }
  return {};
}

function renderScreen() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <WorkingContextProvider>
          <FinancialStructure />
        </WorkingContextProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  structure = STRUCTURE;
  window.localStorage.setItem(
    "cass.working-context.v1",
    JSON.stringify({ projectId: PROJECT_ID, exposureId: EXPOSURE_ID }),
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      return new Response(JSON.stringify(routeFor(url)), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("FinancialStructure", () => {
  it("shows the layers of a policy with the attachment its cover begins at", async () => {
    renderScreen();

    expect(await screen.findByText("ACC-1")).toBeInTheDocument();
    expect(screen.getByText("POL-1")).toBeInTheDocument();
    // A layer is read for where its cover begins and what share KRE signed.
    expect(screen.getByText("1M")).toBeInTheDocument();
    expect(screen.getByText("50.0%")).toBeInTheDocument();
  });

  it("says which contracts the engine will not apply, where the contract is read", async () => {
    renderScreen();

    expect(await screen.findByText(/Facultative on the Jakarta tower/)).toBeInTheDocument();
    expect(screen.getByText("not applied")).toBeInTheDocument();
    expect(
      screen.getAllByText(/portfolio level on the patched worker/).length,
    ).toBeGreaterThan(0);
  });

  it("puts the contracts in inuring order", async () => {
    renderScreen();

    const headings = await screen.findAllByText(/^Priority \d/);
    expect(headings.map((item) => item.textContent)).toEqual(["Priority 1", "Priority 2"]);
  });

  it("shows an advisory finding without anything having to be opened", async () => {
    renderScreen();

    expect(await screen.findByText(/ACC-2\/POL-9 layer 1/)).toBeInTheDocument();
  });

  it("puts a blocking finding in front of the reader", async () => {
    structure = {
      ...STRUCTURE,
      findings: [
        {
          code: "contract_without_scope",
          subject: "Contract 2",
          message: "No scope row names this contract, so it reaches nothing.",
          blocking: true,
        },
      ],
    };
    renderScreen();

    expect(await screen.findByText(/so it reaches nothing/)).toBeInTheDocument();
    expect(screen.getByText(/1 thing\(s\) stop this structure/)).toBeInTheDocument();
  });

  it("says plainly when a portfolio carries no financial structure at all", async () => {
    structure = {
      ...STRUCTURE,
      layers: [],
      contracts: [],
      inuring_order: [],
      findings: [],
      has_accounts: false,
      has_contracts: false,
    };
    renderScreen();

    expect(
      await screen.findByText(/carries no financial structure/),
    ).toBeInTheDocument();
  });
});
