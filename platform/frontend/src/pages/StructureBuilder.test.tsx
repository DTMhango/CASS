/**
 * Building a structure from the screen.
 *
 * The forms send only the terms the engine uses for each contract type, a
 * published portfolio is offered the correction rather than a form, and a
 * refusal is shown field by field as the API wrote it.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ExposureVersion, FinancialStructureSummary } from "@/api/types";

import { StructureBuilder } from "./StructureBuilder";

const EXPOSURE_ID = "22222222-2222-2222-2222-222222222222";

const DRAFT = { id: EXPOSURE_ID, is_frozen: false } as ExposureVersion;
const PUBLISHED = { id: EXPOSURE_ID, is_frozen: true } as ExposureVersion;

const EMPTY: FinancialStructureSummary = {
  exposure_version: EXPOSURE_ID,
  currency: "IDR",
  total_tiv: "9900000",
  location_count: 3,
  layers: [],
  contracts: [],
  inuring_order: [],
  uncovered_locations: 0,
  uncovered_tiv: "0",
  findings: [],
  has_accounts: false,
  has_contracts: false,
};

const LOCATIONS = {
  kind: "location",
  columns: [],
  rows: ["LOC-1", "LOC-2", "LOC-3"].map((location, index) => ({
    row_number: index + 1,
    values: { AccNumber: "ACC-1", LocNumber: location },
  })),
  count: 3,
  total: 3,
};

let posted: { url: string; body: Record<string, unknown> }[] = [];
let refuse: Record<string, string> | null = null;

beforeEach(() => {
  posted = [];
  refuse = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/structure/")) {
        posted.push({ url, body: JSON.parse(String(init?.body ?? "{}")) });
        const payload = refuse ? { detail: "The structure was not changed.", fields: refuse } : EMPTY;
        return new Response(JSON.stringify(payload), {
          status: refuse ? 400 : 201,
          headers: { "Content-Type": "application/json" },
        });
      }
      const payload = url.includes("/rows/") ? LOCATIONS : {};
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderBuilder(exposure: ExposureVersion, structure = EMPTY) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <StructureBuilder exposure={exposure} structure={structure} />
    </QueryClientProvider>,
  );
}

describe("StructureBuilder", () => {
  it("offers a published portfolio its correction, not a form that could not be saved", () => {
    renderBuilder(PUBLISHED);

    expect(screen.getByRole("button", { name: "Correct in a new version" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Write the policy" })).not.toBeInTheDocument();
  });

  it("writes a policy with its layers", async () => {
    const user = userEvent.setup();
    renderBuilder(DRAFT);

    await screen.findByRole("option", { name: "ACC-1" });
    await user.selectOptions(screen.getAllByLabelText(/^Account/)[0]!, "ACC-1");
    await user.type(screen.getByLabelText(/^Policy reference/), "POL-1");
    await user.type(screen.getByLabelText(/^Layer 1 limit/), "5000000");
    await user.click(screen.getByRole("button", { name: "Add a layer" }));
    await user.type(screen.getByLabelText(/^Layer 2 attachment/), "5000000");
    await user.click(screen.getByRole("button", { name: "Write the policy" }));

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]!.url).toContain("/structure/policies/");
    expect(posted[0]!.body).toMatchObject({
      account: "ACC-1",
      policy: "POL-1",
      perils: "QEQ",
      layers: [
        { attachment: "0", limit: "5000000", participation: "1" },
        { attachment: "5000000", limit: "", participation: "1" },
      ],
    });
  });

  it("sends a catastrophe excess of loss its per-event terms and nothing per risk", async () => {
    const user = userEvent.setup();
    renderBuilder(DRAFT);

    await user.type(screen.getByLabelText(/^Attachment per event/), "2000000");
    await user.type(screen.getByLabelText(/^Limit per event/), "5000000");
    await user.click(screen.getByRole("button", { name: "Write the contract" }));

    await waitFor(() => expect(posted).toHaveLength(1));
    const body = posted[0]!.body;
    expect(body).toMatchObject({
      type: "CXL",
      occurrence_attachment: "2000000",
      occurrence_limit: "5000000",
      whole_portfolio: true,
    });
    expect(body).not.toHaveProperty("risk_level");
    expect(body).not.toHaveProperty("scope");
  });

  it("asks a surplus share for the share ceded on each risk it names", async () => {
    const user = userEvent.setup();
    renderBuilder(DRAFT);

    await screen.findAllByRole("option", { name: "ACC-1" });
    await user.selectOptions(screen.getByLabelText(/^Contract type/), "SS");
    expect(screen.queryByLabelText(/^Covers/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Name a risk" }));
    await user.selectOptions(screen.getByLabelText(/^Risk 1 account/), "ACC-1");
    await user.selectOptions(screen.getByLabelText(/^Risk 1 location/), "LOC-1");
    await user.type(screen.getByLabelText(/^Risk 1 ceded share/), "0.4");
    await user.click(screen.getByRole("button", { name: "Write the contract" }));

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]!.body).toMatchObject({
      type: "SS",
      risk_level: "LOC",
      scope: [{ account: "ACC-1", location: "LOC-1", ceded_percent: "0.4" }],
    });
    expect(posted[0]!.body).not.toHaveProperty("whole_portfolio");
  });

  it("shows each refusal as the API wrote it", async () => {
    refuse = { OccLimit: "A catastrophe excess of loss needs an occurrence limit." };
    const user = userEvent.setup();
    renderBuilder(DRAFT);

    await user.click(screen.getByRole("button", { name: "Write the contract" }));

    expect(
      await screen.findByText("A catastrophe excess of loss needs an occurrence limit."),
    ).toBeInTheDocument();
  });

  it("removes a written contract by its number", async () => {
    const user = userEvent.setup();
    const written: FinancialStructureSummary = {
      ...EMPTY,
      has_contracts: true,
      contracts: [
        {
          number: 3,
          layer_number: 1,
          name: "Cat XL",
          type: "CXL",
          type_label: "Catastrophe excess of loss",
          perils: ["QEQ"],
          inuring_priority: 1,
          ceded_percent: "1",
          placed_percent: "1",
          risk_limit: null,
          risk_attachment: null,
          occurrence_limit: "5000000",
          occurrence_attachment: "2000000",
          currency: "IDR",
          scope_rows: 1,
          scope_tiv: "9900000",
          locations_reached: 3,
          applied_by_the_engine: true,
          notes: [],
        },
      ],
    };
    renderBuilder(DRAFT, written);

    await user.click(screen.getByRole("button", { name: "Remove contract 3" }));

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]!.url).toContain("/structure/contracts/remove/");
    expect(posted[0]!.body).toEqual({ number: 3 });
  });
});
