/**
 * The analysis builder, and the claim a run is allowed to make.
 *
 * Section 5.2 of the portfolio brief requires the run mode to be explicit, and
 * permits decision use only after scientific validation, licensing and an
 * approved assumption set. The API refuses the rest, but a screen that offered
 * decision use against a research prototype would be inviting an analyst to
 * ask for something they cannot have, and telling them so afterwards.
 *
 * What these hold to account is that the mode reaches the API as chosen, and
 * that decision use is not offered where it could not be granted.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WorkingContextProvider } from "@/context/WorkingContext";

import { AnalysisBuilder } from "./AnalysisBuilder";

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";
const EXPOSURE_ID = "22222222-2222-2222-2222-222222222222";
const MODEL_ID = "33333333-3333-3333-3333-333333333333";

const EXPOSURE = {
  id: EXPOSURE_ID,
  project: PROJECT_ID,
  name: "Jakarta facultative",
  version: "1.0.0",
  state: "published",
  is_usable_by_runs: true,
  location_count: 64,
  total_tiv: "1360000000.00",
  run_currency: "USD",
  oed_schema_version: "4.0.0",
  unmodelled_subperils: [],
  supported_perspectives: [
    { perspective: "ground_up", label: "Ground-up loss", available: true, reason: "" },
  ],
};

/** Published, so it may be run; a prototype, so it may not decide anything. */
const PROTOTYPE = {
  id: MODEL_ID,
  reference: "id-qeq-0.1.0",
  country_code: "ID",
  peril: "earthquake",
  version: "0.1.0",
  is_research_prototype: true,
  publication_state: "published",
  assumption_sets: [],
};

let models: unknown[] = [PROTOTYPE];
const posted: { url: string; body: unknown }[] = [];

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
  if (url.includes("/model-versions/catalogue/")) {
    return { models };
  }
  if (url.includes("/exposure-versions/")) {
    return { count: 1, next: null, previous: null, results: [EXPOSURE] };
  }
  if (url.includes("/platform/")) {
    return {
      api_version: "v1",
      oed_schema_version: "4.0.0",
      compatibility_matrix: [],
      execution_profiles: {
        standard: { cpu: 2, memory_gb: 8, timeout_seconds: 7200, max_concurrent: 2 },
      },
      profile_capacity: [
        {
          profile: "standard",
          cpu: 2,
          memory_gb: 8,
          timeout_seconds: 7200,
          max_concurrent: 2,
          running: 2,
          available: 0,
          is_full: true,
        },
      ],
      default_execution_profile: "standard",
      artifact_backend: "filesystem",
      engines: {},
    };
  }
  if (url.includes("/analysis-runs/")) {
    return { id: "44444444-4444-4444-4444-444444444444", run: "55555555-5555-5555-5555-555555555555" };
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
          <AnalysisBuilder />
        </WorkingContextProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  models = [PROTOTYPE];
  posted.length = 0;
  window.localStorage.setItem(
    "cass.working-context.v1",
    JSON.stringify({
      projectId: PROJECT_ID,
      exposureId: EXPOSURE_ID,
      modelId: MODEL_ID,
      perspective: "ground_up",
    }),
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") {
        posted.push({ url, body: init.body ? JSON.parse(String(init.body)) : null });
      }
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

describe("AnalysisBuilder", () => {
  it("does not offer decision use against a research prototype", async () => {
    renderScreen();

    const mode = await screen.findByLabelText(/What this run is for/);
    const decision = within(mode).getByRole("option", { name: /Decision use/ });

    expect(decision).toBeDisabled();
    expect(decision.textContent).toContain("not yet available");
  });

  it("offers decision use once the model version is no longer a prototype", async () => {
    models = [{ ...PROTOTYPE, is_research_prototype: false }];
    renderScreen();

    const mode = await screen.findByLabelText(/What this run is for/);

    await waitFor(() =>
      expect(within(mode).getByRole("option", { name: /Decision use/ })).not.toBeDisabled(),
    );
  });

  it("says what the chosen mode will and will not produce", async () => {
    const user = userEvent.setup();
    renderScreen();

    const mode = await screen.findByLabelText(/What this run is for/);
    await user.selectOptions(mode, "geometry_only");

    expect(await screen.findByText(/No loss is calculated/)).toBeInTheDocument();
  });

  it("sends the mode the analyst chose", async () => {
    const user = userEvent.setup();
    renderScreen();

    const mode = await screen.findByLabelText(/What this run is for/);
    await user.selectOptions(mode, "geometry_only");
    await user.click(screen.getByRole("button", { name: /Submit analysis/ }));

    await waitFor(() => expect(posted.length).toBeGreaterThan(0));
    const configured = posted.find((item) => item.url.includes("/analysis-runs/"));
    expect((configured?.body as { mode: string }).mode).toBe("geometry_only");
  });

  it("says a resource profile is full before an analyst waits on it", async () => {
    renderScreen();

    const profiles = await screen.findByLabelText(/Resource profile/);

    await waitFor(() =>
      expect(within(profiles).getByRole("option").textContent).toContain(
        "full, 2 running",
      ),
    );
  });

  it("defaults to the technical mode rather than decision use", async () => {
    const user = userEvent.setup();
    renderScreen();

    await screen.findByLabelText(/What this run is for/);
    await user.click(screen.getByRole("button", { name: /Submit analysis/ }));

    await waitFor(() => expect(posted.length).toBeGreaterThan(0));
    const configured = posted.find((item) => item.url.includes("/analysis-runs/"));
    expect((configured?.body as { mode: string }).mode).toBe("technical");
  });
});
