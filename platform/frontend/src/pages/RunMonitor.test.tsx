/**
 * The run monitor, where a run held at a gate is released.
 *
 * A run blocked at a gate used to have no way forward on the screen: it said a
 * reviewer must clear the gate and offered nothing to clear it with. These hold
 * the three states a gate moves through -- nobody has asked, somebody has
 * asked, the gate is cleared -- and the checks the review stage records.
 *
 * They stub fetch rather than the hooks, so the client, the query keys and the
 * payload shapes are exercised together.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnalysisRun, Approval, Run } from "@/api/types";
import { WorkingContextProvider } from "@/context/WorkingContext";

import { RunMonitor } from "./RunMonitor";

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";
const RUN_ID = "33333333-3333-3333-3333-333333333333";
const ANALYSIS_ID = "44444444-4444-4444-4444-444444444444";

const HELD_RUN: Run = {
  id: RUN_ID,
  kind: "analysis",
  project: PROJECT_ID,
  label: "Jakarta-Bandung ground-up",
  state: "blocked",
  stage: "review",
  stage_label: "Result review",
  progress: 0.91,
  stage_progress: null,
  stage_progress_label: "",
  pipeline: [],
  execution_profile: "standard",
  correlation_id: "",
  queued_at: null,
  started_at: null,
  finished_at: null,
  duration_seconds: null,
  elapsed_seconds: null,
  peak_memory_mb: null,
  failure_stage: "",
  failure_summary: "",
  failure_detail: "",
  gate_summary: "1 result check(s) did not pass, so the results are held until a reviewer decides.",
  gate_detail: "  Ground-up loss: no loss exceeds the portfolio's insured value",
  manifest: {
    smoke: {
      performed: false,
      reason: "No footprint index is readable at /oasis-model from the control plane.",
    },
    review: {
      failed: 1,
      checks: [
        {
          check: "Keys reconcile to the published source",
          passed: true,
          detail: "Every location, coverage and sub-peril produced one response.",
        },
        {
          check: "Ground-up loss: no loss exceeds the portfolio's insured value",
          passed: false,
          detail: "The largest is 50000000 against 9900000 insured.",
        },
      ],
    },
  },
  settings_hash: "",
  retry_of: null,
  may_retry: false,
  may_publish_results: false,
  is_active: false,
  created_at: "2026-09-13T10:00:00Z",
};

const ANALYSIS: AnalysisRun = {
  id: ANALYSIS_ID,
  run: RUN_ID,
  run_detail: HELD_RUN,
  exposure_version: "55555555-5555-5555-5555-555555555555",
  enrichment_run: null,
  model_version: "66666666-6666-6666-6666-666666666666",
  perspectives: ["ground_up"],
  mode: "technical",
  analysis_settings: {},
  run_currency: "USD",
  oasis_analysis_id: "7",
  oasis_portfolio_id: "11",
  keys_summary: {},
  keys_reconciled: true,
  may_proceed_past_keys: true,
  exception_approval: null,
  created_at: "2026-09-13T09:00:00Z",
};

function exception(overrides: Partial<Approval> = {}): Approval {
  return {
    id: "77777777-7777-7777-7777-777777777777",
    gate: "run_exception",
    decision: "requested",
    subject_type: "analysis_run",
    subject_id: ANALYSIS_ID,
    requested_by: "u2",
    requested_by_label: "Ada Analyst",
    decided_by: null,
    decided_by_label: "",
    decided_at: null,
    rationale: "The duplicate location is known and recorded; research use only.",
    evidence: { stage: "review" },
    is_open: true,
    is_cleared: false,
    created_at: "2026-09-13T10:05:00Z",
    ...overrides,
  };
}

let approvals: Approval[] = [];
let artifacts: unknown[] = [];
let mayDecide = false;
let posts: { url: string; body: unknown }[] = [];
/** The run the API is holding. Held at a gate unless a test says otherwise. */
let detail: Run = HELD_RUN;

function page<T>(results: T[]) {
  return { count: results.length, next: null, previous: null, results };
}

function routeFor(url: string, method: string, body: unknown): { status: number; body: unknown } {
  if (url.includes("/session/")) {
    return {
      status: 200,
      body: {
        authenticated: true,
        user: {
          id: "u1",
          username: "rui",
          email: "",
          first_name: "Rui",
          last_name: "Reviewer",
          full_name: "Rui Reviewer",
          platform_role: mayDecide ? "reviewer" : "analyst",
          job_title: "",
          local_install_approved: false,
          capabilities: {
            publish_models: false,
            approve_gates: mayDecide,
            administer_platform: false,
          },
        },
      },
    };
  }
  if (method === "POST") {
    posts.push({ url, body });
    if (url.includes("/request-exception/")) return { status: 201, body: exception() };
    if (url.includes("/resume/")) return { status: 202, body: ANALYSIS };
    return { status: 200, body: {} };
  }
  if (url.includes("/approvals/")) return { status: 200, body: page(approvals) };
  if (url.includes("/analysis-runs/")) return { status: 200, body: page([ANALYSIS]) };
  if (url.includes(`/runs/${RUN_ID}/events/`)) return { status: 200, body: [] };
  if (url.includes(`/runs/${RUN_ID}/artifacts/`)) return { status: 200, body: artifacts };
  if (url.includes(`/runs/${RUN_ID}/`)) return { status: 200, body: detail };
  if (url.includes("/runs/")) return { status: 200, body: page([detail]) };
  return { status: 200, body: {} };
}

function renderMonitor() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[`/runs/${RUN_ID}`]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <WorkingContextProvider>
          <Routes>
            <Route path="/runs/:runId" element={<RunMonitor />} />
          </Routes>
        </WorkingContextProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  approvals = [];
  artifacts = [];
  mayDecide = false;
  posts = [];
  detail = HELD_RUN;
  window.localStorage.setItem(
    "cass.working-context.v1",
    JSON.stringify({ projectId: PROJECT_ID }),
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      const { status, body: payload } = routeFor(String(input), init?.method ?? "GET", body);
      return new Response(JSON.stringify(payload), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("a run held at a gate", () => {
  it("asks for an exception with the reason written", async () => {
    const user = userEvent.setup();
    renderMonitor();

    const reason = await screen.findByLabelText(/Why the run should go on/);
    const ask = screen.getByRole("button", { name: "Ask for an exception" });
    // A token reason is what a required field collects when nothing enforces
    // that it say something.
    expect(ask).toBeDisabled();

    await user.type(reason, "The duplicate location is known and recorded.");
    await user.click(ask);

    await waitFor(() =>
      expect(posts.some((post) => post.url.includes("/request-exception/"))).toBe(true),
    );
    const sent = posts.find((post) => post.url.includes("/request-exception/"));
    expect(sent?.url).toContain(`/analysis-runs/${ANALYSIS_ID}/`);
    expect(sent?.body).toEqual({ rationale: "The duplicate location is known and recorded." });
  });

  it("puts the check that failed before the ones that passed", async () => {
    renderMonitor();

    // The gate detail quotes the same check, so the one wanted is the one in
    // the checks list.
    const matches = await screen.findAllByText(
      "Ground-up loss: no loss exceeds the portfolio's insured value",
    );
    const failed = matches.find((element) => element.closest(".run-checks__item"));
    expect(failed?.closest("li")).toHaveTextContent("failed");
    expect(failed?.closest("ul")?.querySelector("li")).toHaveTextContent("failed");
    // A smoke check that could not run says so rather than reading as passed.
    expect(screen.getByText("Smoke check not performed")).toBeInTheDocument();
  });

  it("tells an analyst that a reviewer who did not ask decides", async () => {
    approvals = [exception()];
    renderMonitor();

    expect(await screen.findByText(/A reviewer who did not ask/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("lets a reviewer decide the request on the run itself", async () => {
    approvals = [exception()];
    mayDecide = true;
    renderMonitor();

    expect(await screen.findByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("offers to resume once the gate is cleared", async () => {
    approvals = [
      exception({
        decision: "approved",
        is_open: false,
        is_cleared: true,
        decided_by_label: "Rui Reviewer",
        rationale: "Research run; the duplicate is recorded.",
      }),
    ];
    const user = userEvent.setup();
    renderMonitor();

    await user.click(await screen.findByRole("button", { name: "Resume the run" }));

    await waitFor(() =>
      expect(
        posts.some((post) => post.url.includes(`/analysis-runs/${ANALYSIS_ID}/resume/`)),
      ).toBe(true),
    );
  });

  it("does not treat an exception cleared at another gate as this one", async () => {
    approvals = [
      exception({
        decision: "approved",
        is_open: false,
        is_cleared: true,
        evidence: { stage: "reconcile_keys" },
      }),
    ];
    renderMonitor();

    expect(await screen.findByLabelText(/Why the run should go on/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume the run" })).not.toBeInTheDocument();
  });
  it("says an artifact expired under its retention class rather than offering it", async () => {
    artifacts = [
      {
        id: "88888888-8888-8888-8888-888888888888",
        role: "cass_keys",
        direction: "output",
        uri: "cass://cass-portfolio/analysis/run/cass_keys.csv",
        checksum: "sha256:abc",
        size_bytes: 1024,
        retention: "diagnostic",
        state: "expired",
        readable: false,
      },
    ];
    renderMonitor();

    // The row stays, so the run still names what it used; the download does not.
    expect(await screen.findByText("expired")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Download/ })).not.toBeInTheDocument();
    expect(screen.queryByText("not yours to read")).not.toBeInTheDocument();
  });
});

/**
 * A run that is still going.
 *
 * The monitor used to be at its least informative exactly when it was worth
 * watching: the elapsed column read "—" until the run finished, and the stage
 * list stood still for the hours an engine spends inside one stage.
 */
describe("a run that is still going", () => {
  const RUNNING: Run = {
    ...HELD_RUN,
    state: "running",
    stage: "losses",
    stage_label: "Calculate losses",
    is_active: true,
    started_at: "2026-09-13T10:00:00Z",
    finished_at: null,
    duration_seconds: null,
    elapsed_seconds: 187,
    stage_progress: 0.35,
    stage_progress_label: "classical",
    gate_summary: "",
    gate_detail: "",
    manifest: {},
    pipeline: [
      {
        key: "publish_oed",
        label: "Publish OED",
        description: "Write the portfolio the engine will read.",
        gate: false,
        position: 1,
      },
      {
        key: "losses",
        label: "Calculate losses",
        description: "Run the loss calculation.",
        gate: false,
        position: 2,
      },
    ],
  };

  it("shows the time it has been running, rather than nothing until it ends", async () => {
    detail = RUNNING;
    renderMonitor();

    // Seconds may have passed between the fetch and the assertion; the point is
    // that a clock is running at all.
    expect(await screen.findByText(/^3m \d+s$/)).toBeInTheDocument();
  });

  it("shows how far into the stage the engine says it is, and of what", async () => {
    detail = RUNNING;
    renderMonitor();

    const bar = await screen.findByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "35");
    // The phase is part of the reading: OpenQuake starts its percentage again
    // for each phase, so the bare number would say "nearly done" three times.
    expect(bar).toHaveAttribute("aria-valuetext", "35% through classical");
    expect(screen.getByText("classical")).toBeInTheDocument();
  });

  it("puts the fraction on the stage being worked on and nowhere else", async () => {
    detail = RUNNING;
    renderMonitor();

    await screen.findByRole("progressbar");
    expect(screen.getAllByRole("progressbar")).toHaveLength(1);
    const current = screen.getByText("Calculate losses").closest("li");
    expect(current?.querySelector(".stage-progress")).not.toBeNull();
  });

  it("says nothing about a fraction the engine does not report", async () => {
    detail = { ...RUNNING, stage_progress: null, stage_progress_label: "" };
    renderMonitor();

    await screen.findByText("Calculate losses");
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});
