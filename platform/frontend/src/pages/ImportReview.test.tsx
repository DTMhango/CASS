/**
 * The import review screen, driven through the API it actually calls.
 *
 * These stub fetch rather than the hooks, so the client, the query keys and
 * the components are exercised together. What they hold to account is the one
 * property that makes the screen worth having: a gap is never shown as a count
 * on its own. Every one carries the value behind it and the consequence in the
 * words the failure will appear in, because "40 rows missing occupancy" reads
 * like tidying and the same fact with "USD 180m" and "fail_v" beside it reads
 * like what it is.
 *
 * The review rule is the other half. A decision needs a rationale, the request
 * carries one, and the screen shows the reason the server gives when it does
 * not.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ImportResults, Project, ReviewQueue } from "@/api/types";
import { WorkingContextProvider } from "@/context/WorkingContext";

import { ImportReview } from "./ImportReview";

const PROJECT: Project = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Indonesia facultative 2026",
  reference: "idn-fac-2026",
  purpose: "",
  team: "",
  status: "active",
  my_role: "owner",
  exposure_version_count: 1,
  active_run_count: 0,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

const BATCH_ID = "33333333-3333-3333-3333-333333333333";
const LOCATION_ID = "44444444-4444-4444-4444-444444444444";

const IMPORTS = {
  count: 1,
  next: null,
  previous: null,
  results: [
    {
      id: BATCH_ID,
      project: PROJECT.id,
      source_filename: "premium_policies.xlsx",
      source_checksum: "a".repeat(64),
      parser_version: "intake-1.0.0",
      cohort_rule_version: "cohort-1.0.0",
      policy_row_count: 1353,
      risk_row_count: 224,
      state: "parsed",
      findings: [],
    },
  ],
};

const RESULTS: ImportResults = {
  batch: {
    id: BATCH_ID,
    filename: "premium_policies.xlsx",
    source_checksum: "a".repeat(64),
    parser_version: "intake-1.0.0",
    cohort_rule_version: "cohort-1.0.0",
    overlay_version: "1.0.0",
    policy_row_count: 1353,
    risk_row_count: 224,
    state: "parsed",
  },
  included: [
    {
      country_code: "ID",
      class_of_business: "Fire",
      cohort: "A",
      locations: 42,
      value: 180_000_000,
    },
  ],
  total_value: 822_817_000,
  review: {
    overlay_version: "1.0.0",
    cohort_rule_version: "cohort-1.0.0",
    locations: 224,
    by_cohort: { A: 42, B: 171, C: 11 },
    by_review_state: { not_required: 213, pending: 11 },
    outstanding: 11,
    decided: 0,
    decisions: [],
    storeys: {
      locations: 224,
      stated_in_source: 0,
      established_in_review: 0,
      unstated: 224,
      stated_share: 0,
      value_stated_in_source: 0,
      value_established_in_review: 0,
      value_unstated: 822_817_000,
      value_stated_share: 0,
      note: "A risk with no storey count reaches vulnerability candidates across several intensity measures and cannot become one Oasis function.",
    },
  },
  missing_model_inputs: [
    {
      field: "storeys",
      locations: 224,
      value: 822_817_000,
      consequence:
        "The risk reaches vulnerability candidates across several intensity measures and cannot become one Oasis function.",
    },
    {
      field: "occupancy",
      locations: 40,
      value: 180_000_000,
      consequence:
        "fail_v: OED unknown occupancy reaches no vulnerability function, by design.",
    },
  ],
  multi_location_businesses: [
    {
      business_id: "BUS-1",
      locations: 3,
      value: 55_176_000,
      states_own_values: false,
    },
  ],
  repeated_coordinates: [
    {
      coordinate: "-6.2,106.8",
      count: 2,
      businesses: ["BUS-1", "BUS-2"],
      value: 12_000_000,
    },
  ],
  findings: [],
  intake_report: {},
  cohort_profile: {},
  use_modes: [
    { mode: "geometry", meaning: "No loss is calculated at all." },
    { mode: "technical_test", meaning: "The numbers describe the pipeline." },
    { mode: "research", meaning: "Not a priced view." },
    { mode: "decision_use", meaning: "Requires review first." },
  ],
  allocation_note:
    "A business with one location carries its own value and no allocation assumption applies to it.",
};

const QUEUE: ReviewQueue = {
  batch: BATCH_ID,
  outstanding: 1,
  outstanding_value: 40_000_000,
  locations: [
    {
      id: LOCATION_ID,
      business_id: "BUS-9",
      location_number: "1",
      primary_location: true,
      class_of_business: "Fire",
      country_code: "ID",
      coordinate: "-6.21,106.84",
      precision: "approximate",
      needs_review: true,
      total_insured_value: 40_000_000,
      cohort: "C",
      cohort_reason: "Flagged for review by the geocoder.",
      storeys: null,
      storeys_are_reviewed: false,
      history: [],
    },
  ],
};

let decideBody: Record<string, unknown> | null = null;
let decideStatus = 201;
let decideDetail = "";

function routeFor(url: string): unknown {
  if (url.includes("/projects/")) {
    return { count: 1, next: null, previous: null, results: [PROJECT] };
  }
  if (url.includes("/import-results/")) return RESULTS;
  if (url.includes("/review-queue/")) return QUEUE;
  if (url.includes("/decide/")) {
    return decideStatus === 201
      ? { location: { storeys: 6 }, history: [] }
      : { detail: decideDetail };
  }
  if (url.includes("/portfolio-imports/")) return IMPORTS;
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
          <ImportReview />
        </WorkingContextProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  decideBody = null;
  decideStatus = 201;
  decideDetail = "";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/decide/") && init?.body) {
        decideBody = JSON.parse(String(init.body));
      }
      const status = url.includes("/decide/") ? decideStatus : 200;
      return new Response(JSON.stringify(routeFor(url)), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ImportReview", () => {
  it("names the versions of the rules that read the import", async () => {
    renderScreen();
    expect(await screen.findByText("intake-1.0.0")).toBeInTheDocument();
    expect(screen.getByText("cohort-1.0.0")).toBeInTheDocument();
  });

  it("shows every gap with the value behind it and what it costs", async () => {
    renderScreen();
    expect(
      await screen.findByText(
        "fail_v: OED unknown occupancy reaches no vulnerability function, by design.",
      ),
    ).toBeInTheDocument();
    // The count alone would read as tidying; the value is what makes it a finding.
    expect(screen.getAllByText("180M").length).toBeGreaterThan(0);
  });

  it("gives the storey gap its own panel, because it is the largest lever", async () => {
    renderScreen();
    expect(
      await screen.findByText("Height, and what not knowing it costs"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/100\.0% of value sits on risks with no storey count/),
    ).toBeInTheDocument();
  });

  it("distinguishes the four modes a result may be used in", async () => {
    renderScreen();
    expect(await screen.findByText("Geometry only")).toBeInTheDocument();
    expect(screen.getByText("Decision use")).toBeInTheDocument();
    expect(screen.getByText("Requires review first.")).toBeInTheDocument();
  });

  it("says a decision is recorded beside the source rather than applied to it", async () => {
    renderScreen();
    expect(
      await screen.findByText(/never edits the source/),
    ).toBeInTheDocument();
  });

  it("reports a repeated coordinate without calling it an error", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.click(
      await screen.findByText("Locations sharing a coordinate (1)"),
    );
    expect(
      await screen.findByText(/Two units of one business at one address are ordinary/),
    ).toBeInTheDocument();
  });

  it("sends the rationale with a decision", async () => {
    const user = userEvent.setup();
    renderScreen();

    const rationale = await screen.findByLabelText(/Rationale/);
    await user.type(screen.getByLabelText("Value"), "6");
    await user.type(rationale, "Six floors counted on the site visit.");
    await user.click(screen.getByRole("button", { name: "Record decision" }));

    await waitFor(() => expect(decideBody).not.toBeNull());
    expect(decideBody).toMatchObject({
      field: "storeys",
      value: "6",
      rationale: "Six floors counted on the site visit.",
    });
  });

  it("shows the reason when the server refuses a decision", async () => {
    decideStatus = 409;
    decideDetail = "A decision needs a rationale of at least 12 characters.";
    const user = userEvent.setup();
    renderScreen();

    await user.type(await screen.findByLabelText(/Rationale/), "ok");
    await user.click(screen.getByRole("button", { name: "Record decision" }));

    expect(
      await screen.findByText(
        "A decision needs a rationale of at least 12 characters.",
      ),
    ).toBeInTheDocument();
  });

  it("shows the queued risk with its cohort and the reason for it", async () => {
    renderScreen();
    expect(await screen.findByText(/BUS-9 \/ 1/)).toBeInTheDocument();
    expect(
      screen.getByText(/Flagged for review by the geocoder/),
    ).toBeInTheDocument();
  });
});
