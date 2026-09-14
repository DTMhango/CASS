/**
 * Choosing the GEM release on the device.
 *
 * The card shows what was found and whether it is the validated release, sends
 * the path of the one chosen, and shows a refusal problem by problem.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { GemReleaseInspection, GemReleaseStatus } from "@/api/types";

import { GemReleaseCard } from "./GemReleaseCard";

const FOUND: GemReleaseInspection = {
  path: "/models/gem/v2026.0.0",
  usable: true,
  release: "v2026.0.0",
  matches_validated: true,
  validated_release: "v2026.0.0",
  countries: 42,
  repositories: [],
  problems: [],
  notes: [],
};

const NONE: GemReleaseStatus = {
  source: "none",
  path: "",
  chosen_at: null,
  current: null,
  mount: "/models",
  discovered: [FOUND],
  validated_release: "v2026.0.0",
};

let status: GemReleaseStatus = NONE;
let posted: Record<string, unknown>[] = [];
let refuse = false;

beforeEach(() => {
  status = NONE;
  posted = [];
  refuse = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/gem-release/choose/")) {
        posted.push(JSON.parse(String(init?.body ?? "{}")));
        const body = refuse
          ? {
              detail: "That folder is not a GEM release CASS can build from.",
              problems: ["There is no global_vulnerability_model folder in /models/other."],
            }
          : { ...NONE, source: "chosen", path: FOUND.path, current: FOUND };
        return new Response(JSON.stringify(body), {
          status: refuse ? 400 : 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify(status), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderCard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <GemReleaseCard />
    </QueryClientProvider>,
  );
}

describe("GemReleaseCard", () => {
  it("says no release is chosen and offers the one found on the device", async () => {
    const user = userEvent.setup();
    renderCard();

    expect(await screen.findByText("No GEM release is chosen")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Use this release" }));

    await waitFor(() => expect(posted).toEqual([{ path: "/models/gem/v2026.0.0" }]));
    expect(await screen.findByText("Building from v2026.0.0")).toBeInTheDocument();
  });

  it("says when the release in use is not the one CASS was validated against", async () => {
    status = {
      ...NONE,
      source: "chosen",
      path: "/models/gem/next",
      current: {
        ...FOUND,
        path: "/models/gem/next",
        release: "v2027.0.0",
        matches_validated: false,
        notes: ["The repositories are not at the v2026.0.0 commits CASS was validated against."],
      },
    };
    renderCard();

    expect(await screen.findByText("Building from v2027.0.0")).toBeInTheDocument();
    expect(screen.getAllByText("not the validated commits").length).toBeGreaterThan(0);
  });

  it("shows why a named folder was refused", async () => {
    refuse = true;
    const user = userEvent.setup();
    renderCard();

    await user.type(await screen.findByLabelText(/Or name the folder/), "/models/other");
    await user.click(screen.getByRole("button", { name: "Use this folder" }));

    expect(
      await screen.findByText("There is no global_vulnerability_model folder in /models/other."),
    ).toBeInTheDocument();
  });
});
