/**
 * The hazard model upload and configuration screen.
 *
 * What these hold to account is the difference between this screen and the
 * OpenQuake web interface. That one takes a zip and runs what the job.ini
 * says; a published national model says "classical", which completes
 * successfully and produces nothing a footprint can be made from.
 *
 * So the screen has to say three things before anybody runs anything: what the
 * package is, what the run will change about it, and what those changes cost.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConfiguredRun, HazardModel, PackageInspection } from "@/api/types";

import { HazardModels } from "./HazardModels";

const MODEL_ID = "55555555-5555-5555-5555-555555555555";
const GRID_ID = "66666666-6666-6666-6666-666666666666";

const MODEL: HazardModel = {
  id: MODEL_ID,
  reference: "id-hazmodel-2024.0.0",
  country_code: "ID",
  version: "2024.0.0",
  label: "PuSGeN 2024 seismic hazard model for Indonesia",
  source_organisation: "PuSGeN, for the GEM 2026 mosaic",
  publication_reference: "",
  licence: "CC BY-NC-SA 4.0",
  licence_cleared: false,
  licence_note: "Use of this model has not been cleared.",
  archive_checksum: "a".repeat(64),
  archive_bytes: 19_000_000,
  file_manifest: [],
  published_calculation_mode: "classical",
  intensity_measures: ["PGA", "SA(0.3)", "SA(0.6)", "SA(1.0)"],
  tectonic_regions: ["Active Shallow Crust", "Subduction Interface"],
  estimated_realizations: 1080,
  logic_tree_summary: { estimated_realizations: 1080, note: "" },
  needs_conversion: true,
  needs_sampling: true,
  publication_state: "draft",
  notes: "",
  created_at: "2026-09-12T00:00:00Z",
};

const INSPECTION: PackageInspection = {
  job_path: "job_clean.ini",
  files: [
    { path: "job_clean.ini", size_bytes: 2356, checksum: "b".repeat(64) },
    { path: "ssm/crust/Indo_Faults_CH.xml", size_bytes: 540951, checksum: "c".repeat(64) },
  ],
  file_count: 2,
  archive_checksum: "a".repeat(64),
  archive_bytes: 19_000_000,
  configuration: {
    source_name: "job_clean.ini",
    checksum: "b".repeat(64),
    calculation_mode: "classical",
    is_event_based: false,
    intensity_measures: ["PGA", "SA(0.3)", "SA(0.6)", "SA(1.0)"],
    effective_time: null,
    unrecognised: ["use_rates"],
    settings: [],
    problems: [],
    runnable: false,
    footprint_requirements: [],
    editable: {},
  },
  logic_trees: {
    estimated_realizations: 1080,
    tectonic_regions: ["Active Shallow Crust", "Subduction Interface"],
    note: "",
  },
};

const PARAMETERS = [
  {
    name: "investigation_time",
    section: "calculation",
    kind: "number",
    label: "Investigation time",
    editability: "science",
    help_text: "The period one stochastic event set represents.",
    consequence:
      "Multiplied by the event set count, this is the effective time and therefore the number of Oasis periods.",
    choices: [],
    minimum: 0,
    maximum: null,
    unit: "years",
    required_for_footprint: "",
  },
  {
    name: "ses_per_logic_tree_path",
    section: "calculation",
    kind: "integer",
    label: "Stochastic event sets",
    editability: "science",
    help_text: "How many independent simulations to run.",
    consequence:
      "More gives a better-sampled tail and costs proportionally more time.",
    choices: [],
    minimum: 1,
    maximum: null,
    unit: "",
    required_for_footprint: "",
  },
];

const CONFIGURED: ConfiguredRun = {
  overrides: { investigation_time: 50, ses_per_logic_tree_path: 20 },
  configuration: {
    ...INSPECTION.configuration,
    calculation_mode: "event_based",
    is_event_based: true,
    runnable: true,
  },
  conversion: {
    changes: [
      { parameter: "calculation_mode", from: "classical", to: "event_based" },
      { parameter: "number_of_logic_tree_samples", from: "0", to: "1" },
    ],
    removed: ["poes", "reference_depth_to_1pt0km_per_sec"],
    notes: ["One logic-tree path is sampled, so this is one realisation."],
  },
  site_join: {},
  problems: [
    {
      parameter: "number_of_logic_tree_samples",
      severity: "warning",
      message:
        "This model's logic tree enumerates to about 1080 realisations and the run samples one. The result is not the model's weighted mean.",
    },
  ],
  runnable: true,
  job_checksum: "d".repeat(64),
  rendered: "[general]\ncalculation_mode = event_based\n",
};

function routeFor(url: string): unknown {
  if (url.includes("/hazard-models/parameters/")) return PARAMETERS;
  if (url.includes("/inspect/")) return INSPECTION;
  if (url.includes("/configure/")) return CONFIGURED;
  if (url.includes("/grids/")) {
    return {
      count: 1,
      next: null,
      previous: null,
      results: [
        {
          id: GRID_ID,
          reference: "id-grid-0.1.0-draft",
          country_code: "ID",
          version: "0.1.0-draft",
          label: "Indonesia prototype grid",
          cell_count: 52831,
          publication_state: "draft",
        },
      ],
    };
  }
  if (url.includes("/hazard-models/")) {
    return { count: 1, next: null, previous: null, results: [MODEL] };
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
        <HazardModels />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
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
  vi.restoreAllMocks();
});

describe("HazardModels", () => {
  it("flags a registered model that cannot yet be run as published", async () => {
    renderScreen();
    expect(await screen.findByText(/classical — needs conversion/)).toBeInTheDocument();
    expect(screen.getByText(/1,080 realisations/)).toBeInTheDocument();
    expect(screen.getByText("licence not cleared")).toBeInTheDocument();
  });

  it("reads an uploaded package before storing anything", async () => {
    const user = userEvent.setup();
    renderScreen();

    const file = new File(["zip bytes"], "Indonesia_v2024.0.0.zip", {
      type: "application/zip",
    });
    await user.upload(screen.getByLabelText(/Model archive/), file);

    expect(await screen.findByText("job_clean.ini")).toBeInTheDocument();
    expect(
      screen.getByText(/produces hazard curves, not events/),
    ).toBeInTheDocument();
  });

  it("explains why a published classical model has to be converted", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.upload(
      screen.getByLabelText(/Model archive/),
      new File(["z"], "m.zip", { type: "application/zip" }),
    );
    expect(
      await screen.findByText(/it produces the probability of exceeding a ground motion/),
    ).toBeInTheDocument();
  });

  it("says a model is registered as a draft that is not cleared for decisions", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.upload(
      screen.getByLabelText(/Model archive/),
      new File(["z"], "m.zip", { type: "application/zip" }),
    );
    expect(
      await screen.findByText(/not for a pricing or reserving decision/),
    ).toBeInTheDocument();
  });

  it("shows every change the run makes to the published configuration", async () => {
    const user = userEvent.setup();
    renderScreen();

    await user.click(await screen.findByText(MODEL.label));
    await user.click(
      await screen.findByRole("button", { name: /Resolve configuration/ }),
    );

    expect(await screen.findByText("calculation_mode")).toBeInTheDocument();
    expect(screen.getByText("classical")).toBeInTheDocument();
    expect(screen.getByText("event_based")).toBeInTheDocument();
    expect(screen.getByText(/poes, reference_depth_to_1pt0km_per_sec/)).toBeInTheDocument();
  });

  it("states what each editable parameter costs", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByText(MODEL.label));
    await user.click(await screen.findByText("What each of these changes"));
    expect(
      await screen.findByText(/the number of Oasis periods/),
    ).toBeInTheDocument();
  });

  it("warns that one sampled path is not the model's weighted mean", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByText(MODEL.label));
    await user.click(
      await screen.findByRole("button", { name: /Resolve configuration/ }),
    );
    expect(
      await screen.findByText(/not the model's weighted mean/),
    ).toBeInTheDocument();
  });

  it("shows the job configuration that would actually run", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByText(MODEL.label));
    await user.click(
      await screen.findByRole("button", { name: /Resolve configuration/ }),
    );
    await user.click(
      await screen.findByText("The job configuration that would run"),
    );
    await waitFor(() =>
      expect(
        screen.getByText(/calculation_mode = event_based/),
      ).toBeInTheDocument(),
    );
  });
});
