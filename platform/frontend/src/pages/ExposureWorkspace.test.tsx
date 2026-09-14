/**
 * The exposure workspace, driven through the API it actually calls.
 *
 * These tests stub fetch rather than the hooks, so the client, the query keys
 * and the components are all exercised together. What they assert is the
 * behaviour section 8 requires: a finding is shown with its remediation and
 * the record it belongs to, and an unsupported perspective is shown as
 * unavailable with its reason rather than silently offered.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ExposureVersion, Project } from "@/api/types";
import { WorkingContextProvider } from "@/context/WorkingContext";

import { ExposureWorkspace } from "./ExposureWorkspace";

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

const EXPOSURE: ExposureVersion = {
  id: "22222222-2222-2222-2222-222222222222",
  project: PROJECT.id,
  name: "Pilot portfolio",
  version: 1,
  state: "validated",
  valuation_date: "2026-06-30",
  source_description: "",
  run_currency: "IDR",
  oed_schema_version: "4.0.0",
  location_count: 3,
  account_count: 0,
  total_tiv: "9900000",
  tiv_by_coverage: { BuildingTIV: "8450000", ContentsTIV: "1450000" },
  tiv_by_country: { ID: "9900000" },
  tiv_by_currency: { IDR: "9900000" },
  unmodelled_subperils: ["QTS"],
  supported_perspectives: [
    {
      perspective: "ground_up",
      label: "Ground-up loss",
      available: true,
      reason: "Location records are present.",
    },
    {
      perspective: "insured",
      label: "Insured loss",
      available: false,
      reason: "An account file is required for insured loss; none was supplied.",
    },
    {
      perspective: "reinsurance",
      label: "Loss net of reinsurance",
      available: false,
      reason: "No reinsurance contracts were supplied.",
    },
  ],
  validation_report: {
    publishable: false,
    validation: {
      blocking: true,
      error_count: 1,
      warning_count: 1,
      counts_by_code: { missing_value: 1, unmodelled_subperil: 1 },
      findings: [],
    },
  },
  attached_files: [
    {
      role: "oed_location",
      uri: "cass://cass-portfolio/project/idn-fac-2026/exposure/22222222/oed_location.csv",
      checksum: "sha256:abc123def456abc123def456abc123def456abc123def456abc123def456abcd",
      size_bytes: 1024,
      original_filename: "locations.csv",
    },
  ],
  is_frozen: false,
  is_publishable: false,
  is_usable_by_runs: false,
  created_at: "2026-09-10T00:00:00Z",
  updated_at: "2026-09-10T00:00:00Z",
};

const FINDINGS = {
  blocking: true,
  error_count: 1,
  warning_count: 1,
  counts_by_code: { missing_value: 1, unmodelled_subperil: 1 },
  findings: [
    {
      code: "missing_value",
      severity: "error" as const,
      message: "Latitude is required and was not supplied.",
      remediation: "Supply the value in the source record, or ask the cedant to confirm it.",
      file_kind: "location",
      row_number: 2,
      field: "Latitude",
      value: null,
      record_key: "PortNumber=1; AccNumber=ACC-1; LocNumber=LOC-1",
    },
    {
      code: "unmodelled_subperil",
      severity: "warning" as const,
      message: "The location covers Tsunami, which this release does not model.",
      remediation:
        "The policy covers a sub-peril this release does not model. Confirm the scope caveat.",
      file_kind: "location",
      row_number: 3,
      field: "LocPerilsCovered",
      value: "QQ1",
      record_key: "PortNumber=1; AccNumber=ACC-1; LocNumber=LOC-2",
    },
  ],
};

const ROWS = {
  kind: "location",
  columns: [
    {
      name: "LocNumber", label: "Location reference", help: "", required: true,
      type: "text", allowed: null, minimum: null, maximum: null,
      is_tiv: false, is_financial_term: false,
    },
    {
      name: "OccupancyCode", label: "Occupancy", help: "", required: true,
      type: "text", allowed: null, minimum: null, maximum: null,
      is_tiv: false, is_financial_term: false,
    },
    {
      name: "Latitude", label: "Latitude", help: "", required: true,
      type: "latitude", allowed: null, minimum: -90, maximum: 90,
      is_tiv: false, is_financial_term: false,
    },
  ],
  rows: [
    { row_number: 1, values: { LocNumber: "LOC-1", OccupancyCode: "1100", Latitude: "-6.2088" } },
    { row_number: 2, values: { LocNumber: "LOC-2", OccupancyCode: "1200", Latitude: "-6.9175" } },
  ],
  count: 2,
  total: 2,
  editable: true,
  attached: ["location"],
};

function routeFor(url: string): unknown {
  if (url.includes("/rows/")) return ROWS;
  if (url.includes("/projects/")) return { count: 1, next: null, previous: null, results: [PROJECT] };
  if (url.includes("/findings/")) return FINDINGS;
  if (url.includes(`/exposure-versions/${EXPOSURE.id}/`)) return EXPOSURE;
  if (url.includes("/exposure-versions/")) {
    return { count: 1, next: null, previous: null, results: [EXPOSURE] };
  }
  return {};
}

function renderWorkspace() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <WorkingContextProvider>
          <ExposureWorkspace />
        </WorkingContextProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
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

describe("ExposureWorkspace", () => {
  it("lists portfolio versions with their state", async () => {
    renderWorkspace();
    expect(await screen.findByText("Pilot portfolio")).toBeInTheDocument();
    expect(screen.getByText("3 locations")).toBeInTheDocument();
  });

  it("shows how many findings must be resolved before publication", async () => {
    renderWorkspace();
    expect(await screen.findByText("1 to resolve")).toBeInTheDocument();
  });

  it("shows a finding with its remediation and the record it belongs to", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText("Pilot portfolio"));

    expect(
      await screen.findByText("Latitude is required and was not supplied."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Supply the value in the source record, or ask the cedant to confirm it.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/LocNumber=LOC-1/)).toBeInTheDocument();
  });

  it("refuses to enable publish while a blocking finding stands", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText("Pilot portfolio"));

    const publish = await screen.findByRole("button", { name: "Publish" });
    expect(publish).toBeDisabled();
  });

  it("states why an unsupported perspective is unavailable", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText("Pilot portfolio"));

    expect(
      await screen.findByText(
        "An account file is required for insured loss; none was supplied.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("No reinsurance contracts were supplied.")).toBeInTheDocument();
  });

  it("discloses covered sub-perils the release does not model", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText("Pilot portfolio"));

    expect(
      await screen.findByText(/Covered sub-perils this release does not model/),
    ).toBeInTheDocument();
  });

  it("shows the rows of the portfolio, not just the files", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText("Pilot portfolio"));

    expect(await screen.findByText("LOC-1")).toBeInTheDocument();
    expect(screen.getByText("LOC-2")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Occupancy/ })).toBeInTheDocument();
  });

  it("builds each field from the column's own limits, so a bad value cannot be typed", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText("Pilot portfolio"));
    await user.click((await screen.findAllByRole("button", { name: "Correct" }))[0]!);

    const latitude = await screen.findByLabelText("Latitude");
    expect(latitude).toHaveAttribute("type", "number");
    expect(latitude).toHaveAttribute("min", "-90");
    expect(latitude).toHaveAttribute("max", "90");
  });

  it("puts a refusal against the field that caused it", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/rows/edit/")) {
        return new Response(
          JSON.stringify({
            detail: "The correction was not stored.",
            fields: { Latitude: "Latitude cannot be below -90." },
          }),
          { status: 400, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(routeFor(url)), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    renderWorkspace();

    await user.click(await screen.findByText("Pilot portfolio"));
    await user.click((await screen.findAllByRole("button", { name: "Correct" }))[0]!);
    await user.click(await screen.findByRole("button", { name: "Save" }));

    expect(await screen.findByText("Latitude cannot be below -90.")).toBeInTheDocument();
  });

  it("names each attached file and keeps its checksum off the screen", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByText("Pilot portfolio"));

    await waitFor(() => expect(screen.getByText("locations.csv")).toBeInTheDocument());
    // The checksum is how CASS keeps the file fixed, not something the person
    // attaching it came to read.
    expect(screen.queryByText(/sha256:/)).not.toBeInTheDocument();
  });

  it("calls only the CASS API", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await user.click(await screen.findByText("Pilot portfolio"));

    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
    for (const call of vi.mocked(fetch).mock.calls) {
      expect(String(call[0])).toMatch(/^\/api\/v1\//);
    }
  });
});
