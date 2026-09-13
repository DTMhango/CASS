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

const SPEC = {
  id: "77777777-7777-7777-7777-777777777777",
  model: MODEL_ID,
  grid: GRID_ID,
  name: "National baseline",
  overrides: { investigation_time: 50, ses_per_logic_tree_path: 20 },
  resolved_configuration: CONFIGURED.configuration,
  conversion_report: {},
  site_join_report: {},
  problems: [],
  blocking_problems: [],
  is_runnable: true,
  job_checksum: "e".repeat(64),
  created_at: "2026-09-12T00:00:00Z",
};

let specs: unknown[] = [];
let launchStatus = 202;
let servedModel: HazardModel = MODEL;

function routeFor(url: string): unknown {
  if (url.includes("/launch/")) {
    return launchStatus === 202
      ? {
          run: "88888888-8888-8888-8888-888888888888",
          hazard_run: "99999999-9999-9999-9999-999999999999",
          state: "queued",
          job_checksum: SPEC.job_checksum,
        }
      : { detail: "The licence for this model has not been cleared." };
  }
  if (url.includes("/specs/")) return specs;
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
    return { count: 1, next: null, previous: null, results: [servedModel] };
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
  specs = [SPEC];
  launchStatus = 202;
  servedModel = MODEL;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const status = url.includes("/launch/") ? launchStatus : 200;
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

describe("HazardModels", () => {
  it("flags a registered model that cannot yet be run as published", async () => {
    renderScreen();
    expect(
      await screen.findByText(/publishes as classical — converted to event-based/),
    ).toBeInTheDocument();
    expect(screen.getByText(/1,080 realisations/)).toBeInTheDocument();
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

  it("does not ask the person uploading a model about its licence", async () => {
    /* One internal-use basis covers everything on this installation, recorded
       once in Administration. Asking again per upload invited a different
       answer each time about a fact that does not vary. */
    const user = userEvent.setup();
    renderScreen();
    await user.upload(
      screen.getByLabelText(/Model archive/),
      new File(["z"], "m.zip", { type: "application/zip" }),
    );

    expect(await screen.findByLabelText(/Version/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Licence/)).not.toBeInTheDocument();
    expect(screen.queryByText(/not cleared/)).not.toBeInTheDocument();
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

  // -- the calculation mode --------------------------------------------------
  //
  // The single most consequential thing this screen does to an uploaded model.
  // It was previously one row in a table inside a disclosure, indistinguishable
  // from a change to the random seed.

  it("leads with the conversion rather than burying it among the parameters", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByText(MODEL.label));
    await user.click(
      await screen.findByRole("button", { name: /Resolve configuration/ }),
    );

    expect(
      await screen.findByText(/Converted from classical to event_based/),
    ).toBeInTheDocument();
  });

  it("says why a classical run is the trap rather than simply the wrong setting", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByText(MODEL.label));
    await user.click(
      await screen.findByRole("button", { name: /Resolve configuration/ }),
    );

    // A classical run completes, exports, and produces nothing a footprint can
    // be built from. That is the fact an operator needs before they spend the
    // hours, and it is why the mode is not offered as a choice.
    expect(
      await screen.findByText(/completes, exports, and produces curves/),
    ).toBeInTheDocument();
    expect(screen.getByText(/This is not a setting/)).toBeInTheDocument();
  });

  it("refuses a resolved configuration that is not event-based", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByText(MODEL.label));
    await user.click(
      await screen.findByRole("button", { name: /Resolve configuration/ }),
    );

    // The screen must not call something runnable that would produce curves.
    expect(
      screen.queryByText(/produces hazard curves rather than events/),
    ).not.toBeInTheDocument();
  });

  // -- running a saved configuration ----------------------------------------

  it("lists a saved configuration with the checksum that identifies it", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByText(MODEL.label));
    await user.click(await screen.findByText(/Saved configurations/));

    expect(await screen.findByText("National baseline")).toBeInTheDocument();
    expect(screen.getByText("runnable")).toBeInTheDocument();
  });

  it("launches a configuration and points at the run monitor", async () => {
    servedModel = { ...MODEL, licence_cleared: true, licence_note: "Cleared." };
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByText(MODEL.label));
    await user.click(await screen.findByText(/Saved configurations/));
    await user.click(await screen.findByRole("button", { name: "Run" }));

    // The response is the queued run, not the hazard: a national calculation
    // is hours and must not hold the browser open.
    expect(
      await screen.findByText(/Follow it on the run monitor/),
    ).toBeInTheDocument();
  });

  it("offers a saved configuration for launch without a licence caveat", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByText(MODEL.label));
    await user.click(await screen.findByText(/Saved configurations/));

    const run = await screen.findByRole("button", { name: "Run" });
    expect(run).toBeEnabled();
    expect(screen.queryByText(/research only/)).not.toBeInTheDocument();
  });

  it("shows the reason when the server refuses a launch", async () => {
    launchStatus = 409;
    servedModel = { ...MODEL, licence_cleared: true, licence_note: "Cleared." };
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByText(MODEL.label));
    await user.click(await screen.findByText(/Saved configurations/));
    await user.click(await screen.findByRole("button", { name: "Run" }));

    expect(await screen.findByText(/was not started/)).toBeInTheDocument();
    expect(
      screen.getByText(/licence for this model has not been cleared/),
    ).toBeInTheDocument();
  });

  it("will not offer to run a configuration with blocking problems", async () => {
    specs = [
      {
        ...SPEC,
        is_runnable: false,
        blocking_problems: [
          { parameter: "sites_csv", severity: "error", message: "No sites." },
        ],
      },
    ];
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByText(MODEL.label));
    await user.click(await screen.findByText(/Saved configurations/));

    expect(await screen.findByText("blocked")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  });
});
