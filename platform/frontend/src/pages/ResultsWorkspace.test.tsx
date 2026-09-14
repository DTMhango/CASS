/**
 * The results workspace, and comparing two governed runs.
 *
 * What these hold to account is the rule that makes a comparison meaningful
 * rather than merely possible. A ground-up result against an insured one, or
 * two currencies, produces a difference nobody can interpret, and the API
 * refuses both. The screen must not offer the pairing and then report the
 * refusal: an analyst who can select it will believe it means something.
 *
 * And the arithmetic is never done here. Every compared number arrives from
 * the API as a decimal string, because ADR 5 keeps money off JavaScript's
 * float. A test that computed an expected difference in the browser would be
 * asserting the very thing the ADR forbids, so these assert what is rendered
 * from what the server sent.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ResultComparison, ResultSet } from "@/api/types";
import { WorkingContextProvider } from "@/context/WorkingContext";

import { ResultsWorkspace } from "./ResultsWorkspace";

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";

function makeResult(overrides: Partial<ResultSet> & { id: string; label: string }): ResultSet {
  return {
    run: "99999999-9999-9999-9999-999999999999",
    project: PROJECT_ID,
    perspective: "ground_up",
    state: "approved",
    average_annual_loss: "1000000.00",
    standard_deviation: "250000.00",
    currency: "USD",
    return_period_losses: { "250": "9000000.00" },
    model_version_reference: "id-eq-0.1.0",
    assumption_set_reference: "id-baseline-1.0",
    run_mode: "decision",
    valuation_date: "2026-06-30",
    exposure_quality: {},
    peril_scope: {},
    material_exclusions: [],
    uncertainty_attribution: {},
    usable_for_decisions: true,
    approved_at: "2026-09-01T00:00:00Z",
    is_frozen: true,
    created_at: "2026-09-01T00:00:00Z",
    caveats: {
      model_version: "id-eq-0.1.0",
      assumption_set: "id-baseline-1.0",
      run_mode: "decision",
      valuation_date: "2026-06-30",
      perspective: "Ground-up loss",
      currency: "USD",
      approval_status: "Approved for decision use",
      usable_for_decisions: true,
      exposure_quality: {},
      peril_scope: {},
      material_exclusions: [],
      uncertainty_attribution: {},
    },
    ...overrides,
  } as ResultSet;
}

const BASELINE = makeResult({ id: "aaaaaaaa-0000-0000-0000-000000000001", label: "Q2 baseline" });

const SAME_BASIS = makeResult({
  id: "aaaaaaaa-0000-0000-0000-000000000002",
  label: "Q2 candidate",
});

const OTHER_PERSPECTIVE = makeResult({
  id: "aaaaaaaa-0000-0000-0000-000000000003",
  label: "Q2 insured",
  perspective: "insured",
  caveats: { ...BASELINE.caveats, perspective: "Insured loss" },
});

const OTHER_CURRENCY = makeResult({
  id: "aaaaaaaa-0000-0000-0000-000000000004",
  label: "Q2 in rupiah",
  currency: "IDR",
  caveats: { ...BASELINE.caveats, currency: "IDR" },
});

/** As the server computes it. Nothing here is worked out in the browser. */
const COMPARISON: ResultComparison = {
  id: "bbbbbbbb-0000-0000-0000-000000000001",
  project: PROJECT_ID,
  label: "Impact of the 0.2.0 release",
  baseline: BASELINE.id,
  baseline_detail: BASELINE,
  candidate: SAME_BASIS.id,
  candidate_detail: SAME_BASIS,
  commentary: "",
  is_like_for_like: true,
  created_at: "2026-09-12T00:00:00Z",
  differences: {
    perspective: "Ground-up loss",
    currency: "USD",
    metrics: [
      {
        metric: "average_annual_loss",
        label: "Average annual loss",
        baseline: "1000000.00",
        candidate: "1250000.00",
        change: "250000.00",
        relative_change: "0.250000",
        direction: "increase",
      },
    ],
    return_periods: {
      shared: [
        {
          return_period: "250",
          baseline: "9000000.00",
          candidate: "9900000.00",
          change: "900000.00",
          relative_change: "0.100000",
          direction: "increase",
        },
      ],
      only_in_baseline: ["100"],
      only_in_candidate: [],
    },
    drivers: [
      {
        driver: "model_version",
        label: "Model version",
        baseline: "id-eq-0.1.0",
        candidate: "id-eq-0.2.0",
        note: "A different model version changes hazard, vulnerability or both.",
      },
    ],
    unexplained: false,
    unexplained_note: "",
    decision_use: {
      baseline: true,
      candidate: true,
      both_approved: true,
      warning: "",
    },
    run_modes: {
      baseline: "decision",
      candidate: "decision",
      mixed: false,
      warning: "",
    },
  },
};

/** The range across assumption scenarios, as the server computed it. */
const SCENARIO_RANGE = {
  varies: "assumption set",
  not_varied: [
    "The hazard realisation and event set",
    "How a business's value is allocated between its sites",
  ],
  currency: "USD",
  central: {
    scenario: "baseline",
    label: "Baseline weights",
    result: "aaaaaaaa-0000-0000-0000-000000000001",
    is_baseline: true,
  },
  scenarios: [
    { scenario: "baseline", label: "Baseline weights", result: "r1", created_at: "", average_annual_loss: "1000.00" },
    { scenario: "more_robust", label: "More robust (more_robust-0.1.0)", result: "r2", created_at: "", average_annual_loss: "850.00" },
    { scenario: "more_vulnerable", label: "More vulnerable (more_vulnerable-0.1.0)", result: "r3", created_at: "", average_annual_loss: "1300.00" },
  ],
  metrics: [
    {
      metric: "average_annual_loss",
      label: "Average annual loss",
      central: "1000.00",
      low: { scenario: "more_robust", label: "More robust (more_robust-0.1.0)", value: "850.00" },
      high: { scenario: "more_vulnerable", label: "More vulnerable (more_vulnerable-0.1.0)", value: "1300.00" },
      spread: "450.00",
      relative_spread: "0.450000",
    },
  ],
  influence: [
    { scenario: "more_vulnerable", label: "More vulnerable (more_vulnerable-0.1.0)", largest_relative_change: "0.400000" },
    { scenario: "more_robust", label: "More robust (more_robust-0.1.0)", largest_relative_change: "0.150000" },
  ],
  left_out: { calculated_differently: 2, calculation_not_recorded: 3 },
};

/** Where the loss is, as the server placed it by the run's keys. */
const GEOGRAPHIC = {
  perspective: "ground_up",
  grid: "id-grid-0.1.0",
  currency: "USD",
  basis: {
    average_loss: "sample",
    summary_level: 2,
    grouped_by: ["AccNumber", "LocNumber"],
    placed_by: "the run's keys",
  },
  cells: [
    {
      area_peril_id: 7,
      min_latitude: "-7",
      max_latitude: "-6",
      min_longitude: "106",
      max_longitude: "108",
      locations: 2,
      average_annual_loss: "800000.00",
      tiv: "9000000.00",
    },
    {
      area_peril_id: 9,
      min_latitude: "-8",
      max_latitude: "-7",
      min_longitude: "112",
      max_longitude: "113",
      locations: 1,
      average_annual_loss: "200000.00",
      tiv: "1500000.00",
    },
  ],
  locations_with_loss: 3,
  unplaced_locations: 0,
  unplaced_loss: "0",
  location_total: "1000000.00",
  portfolio_average_annual_loss: "1000000.00",
  difference: "0.00",
};

let comparisons: ResultComparison[] = [];
let results: ResultSet[] = [];
let eventLosses: Record<string, unknown> = {
  count: 0,
  limit: 25,
  offset: 0,
  currency: "USD",
  results: [],
};

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
        platform_role: "reviewer",
        job_title: "",
        local_install_approved: false,
        capabilities: {
          publish_models: false,
          approve_gates: true,
          administer_platform: false,
        },
      },
    };
  }
  if (url.includes("/comparisons/")) {
    return { count: comparisons.length, next: null, previous: null, results: comparisons };
  }
  if (url.includes("/event-losses/")) {
    return eventLosses;
  }
  if (url.includes("/geographic/")) {
    return GEOGRAPHIC;
  }
  if (url.includes("/scenario-range/")) {
    return SCENARIO_RANGE;
  }
  if (url.includes("/results/")) {
    return { count: results.length, next: null, previous: null, results };
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
          <ResultsWorkspace />
        </WorkingContextProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  results = [BASELINE, SAME_BASIS, OTHER_PERSPECTIVE, OTHER_CURRENCY];
  comparisons = [];
  eventLosses = { count: 0, limit: 25, offset: 0, currency: "USD", results: [] };
  // A comparison belongs to a project, so the screen needs one selected. The
  // working context restores it from storage exactly as a reload would.
  window.localStorage.setItem(
    "cass.working-context.v1",
    JSON.stringify({ projectId: PROJECT_ID }),
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

describe("ResultsWorkspace", () => {
  it("offers as a candidate only what the baseline can be compared against", async () => {
    const user = userEvent.setup();
    renderScreen();

    const baseline = await screen.findByLabelText(/Baseline/);
    await user.selectOptions(baseline, BASELINE.id);

    const candidate = screen.getByLabelText(/Candidate/);
    const options = within(candidate).getAllByRole("option").map((item) => item.textContent);

    expect(options).toContain(SAME_BASIS.label);
    // A ground-up against an insured, or two currencies, is a difference that
    // cannot be interpreted. The screen must not let it be selected at all.
    expect(options).not.toContain(OTHER_PERSPECTIVE.label);
    expect(options).not.toContain(OTHER_CURRENCY.label);
  });

  it("shows where a result's loss is, from the cells the server placed", async () => {
    const user = userEvent.setup();
    renderScreen();

    const [summary] = await screen.findAllByText("Where the loss is (2 cells)");
    expect(summary).toBeDefined();
    await user.click(summary!);

    const panel = summary!.closest("details") as HTMLElement;
    expect(
      within(panel).getByRole("img", {
        name: /Average annual loss by area-peril cell on id-grid-0\.1\.0: 2 cells/,
      }),
    ).toBeInTheDocument();
    // The table is the exact reading of the same cells, largest first.
    const rows = within(panel).getAllByRole("row");
    expect(rows[1]?.textContent).toContain("7");
  });

  it("shows how far a number moves across assumption scenarios, and what does not vary", async () => {
    const user = userEvent.setup();
    renderScreen();

    const [summary] = await screen.findAllByText("Scenario range across 3 assumption sets");
    await user.click(summary!);

    const panel = summary!.closest("details") as HTMLElement;
    expect(within(panel).getByText("Average annual loss")).toBeInTheDocument();
    expect(within(panel).getByText(/Moves the numbers most: More vulnerable/)).toBeInTheDocument();
    // A range across one assumption must not read as the whole uncertainty.
    expect(within(panel).getByText(/Only the assumption set varies in this range/)).toBeInTheDocument();
    // What could not be shown to be calculated alike is counted, not dropped silently.
    expect(within(panel).getByText(/Left out: 5 other result/)).toBeInTheDocument();
  });

  it("says so when nothing shares the baseline's basis", async () => {
    results = [BASELINE, OTHER_PERSPECTIVE];
    const user = userEvent.setup();
    renderScreen();

    const baseline = await screen.findByLabelText(/Baseline/);
    await user.selectOptions(baseline, BASELINE.id);

    expect(
      await screen.findByText(/Nothing can be compared against this baseline/),
    ).toBeInTheDocument();
  });

  it("renders the difference the server computed rather than deriving one", async () => {
    comparisons = [COMPARISON];
    renderScreen();

    await user_expand();

    // 250,000 on a 1,000,000 baseline. Both come from the payload; the screen
    // formats them and does no arithmetic of its own.
    expect(await screen.findByText(/25\.0%\s+increase/)).toBeInTheDocument();
  });

  it("names the driver behind the change", async () => {
    comparisons = [COMPARISON];
    renderScreen();

    await user_expand();

    // "Model version" alone also names a caveat on every result card. What
    // belongs to the driver is the move from one version to the other.
    expect(
      await screen.findByText(/id-eq-0\.1\.0 → id-eq-0\.2\.0/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/changes hazard, vulnerability or both/),
    ).toBeInTheDocument();
  });

  it("lists a return period only one side reported rather than showing it as zero", async () => {
    comparisons = [COMPARISON];
    renderScreen();

    await user_expand();

    expect(await screen.findByText(/Baseline only: 100/)).toBeInTheDocument();
  });

  it("says plainly when nothing recorded accounts for the change", async () => {
    comparisons = [
      {
        ...COMPARISON,
        differences: {
          ...COMPARISON.differences,
          drivers: [],
          unexplained: true,
          unexplained_note:
            "Both results name the same model version, assumption set and valuation date, so the difference lies in the exposure or the run settings rather than in anything recorded here.",
        },
      },
    ];
    renderScreen();

    await user_expand();

    expect(
      await screen.findByText(/difference lies in the exposure or the run settings/),
    ).toBeInTheDocument();
  });

  it("draws the exceedance curve from the losses the server computed", async () => {
    results = [
      makeResult({
        id: "aaaaaaaa-0000-0000-0000-000000000010",
        label: "Q2 with a curve",
        return_period_losses: {
          "10": "633300.00",
          "100": "5000000.00",
          "250": "9000000.00",
        },
      }),
    ];
    renderScreen();

    const chart = await screen.findByRole("img", { name: /Exceedance probability curve/ });

    // Drawn from what arrived: three return periods, ending at 250 years.
    expect(chart).toHaveAccessibleName(/3 return periods from 10 to 250 years/);
  });

  it("lists the events behind a number, largest first", async () => {
    results = [BASELINE];
    eventLosses = {
      count: 27313,
      limit: 25,
      offset: 0,
      currency: "USD",
      results: [
        {
          event_id: "102",
          mean_loss: "5000000.00",
          standard_deviation: "250000.00",
          maximum_loss: "9000000.00",
          event_rate: "0.002",
          chance_of_loss: "0.5",
          impacted_exposure: "70000000",
        },
      ],
    };
    const user = userEvent.setup();
    renderScreen();

    await user.click(await screen.findByText(/Events behind this number/));

    expect(screen.getByText("102")).toBeInTheDocument();
    // The count is the whole table, not the page.
    expect(screen.getByText(/27,313 with a loss/)).toBeInTheDocument();
  });

  it("shows the rate a converted number rests on", async () => {
    results = [
      makeResult({
        id: "aaaaaaaa-0000-0000-0000-000000000009",
        label: "Q2 in dollars",
        caveats: {
          ...BASELINE.caveats,
          exposure_quality: {
            currency_conversion: {
              direction: "1 IDR = 0.0000613 USD",
              source: "Bank Indonesia middle rate",
              valuation_date: "2026-06-30",
            },
          },
        },
      }),
    ];
    renderScreen();

    expect(await screen.findByText(/1 IDR = 0.0000613 USD/)).toBeInTheDocument();
    expect(screen.getByText(/Bank Indonesia middle rate/)).toBeInTheDocument();
  });

  it("warns when the two runs were made for different purposes", async () => {
    comparisons = [
      {
        ...COMPARISON,
        differences: {
          ...COMPARISON.differences,
          run_modes: {
            baseline: "decision",
            candidate: "research",
            mixed: true,
            warning:
              "These results come from runs made for different purposes (decision against research), so the difference between them is not a like-for-like one.",
          },
        },
      },
    ];
    renderScreen();

    await user_expand();

    expect(
      await screen.findByText(/not a like-for-like one/),
    ).toBeInTheDocument();
  });

  it("marks a comparison as not a decision number when either side is unapproved", async () => {
    comparisons = [
      {
        ...COMPARISON,
        differences: {
          ...COMPARISON.differences,
          decision_use: {
            baseline: true,
            candidate: false,
            both_approved: false,
            warning:
              "At least one side of this comparison is not approved for decision use, so the difference is not a decision number either.",
          },
        },
      },
    ];
    renderScreen();

    await user_expand();

    expect(await screen.findByText(/not a decision number either/)).toBeInTheDocument();
  });

  it("sends only the two results, leaving the arithmetic to the server", async () => {
    const user = userEvent.setup();
    renderScreen();

    await user.selectOptions(await screen.findByLabelText(/Baseline/), BASELINE.id);
    await user.selectOptions(screen.getByLabelText(/Candidate/), SAME_BASIS.id);
    await user.type(screen.getByLabelText(/Name this comparison/), "Release impact");
    await user.click(screen.getByRole("button", { name: "Compare" }));

    await waitFor(() => {
      const posted = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.find(
        (call) => String(call[0]).includes("/comparisons/") && call[1]?.method === "POST",
      );
      expect(posted).toBeDefined();
      const body = JSON.parse(String(posted?.[1]?.body));
      expect(body).toEqual({
        project: PROJECT_ID,
        label: "Release impact",
        baseline: BASELINE.id,
        candidate: SAME_BASIS.id,
      });
      expect(body).not.toHaveProperty("differences");
    });
  });
});

/** Open the saved comparison, which renders inside a disclosure. */
async function user_expand() {
  const user = userEvent.setup();
  const summary = await screen.findByText(/Impact of the 0\.2\.0 release/);
  await user.click(summary);
}
