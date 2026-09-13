/**
 * Administration: who may change what somebody can do.
 *
 * What these hold to account is that the controls are offered only to an
 * administrator, that a change reaches the API as chosen, and that a refusal --
 * the lock-out the API guards against -- is shown as the API wrote it rather
 * than guessed at in the browser.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WorkingContextProvider } from "@/context/WorkingContext";

import { Administration } from "./Administration";

const ANALYST = {
  id: "aaaaaaaa-1111-1111-1111-111111111111",
  username: "ada",
  email: "",
  first_name: "Ada",
  last_name: "Analyst",
  full_name: "Ada Analyst",
  platform_role: "analyst",
  job_title: "",
  local_install_approved: false,
  is_active: true,
  capabilities: { publish_models: false, approve_gates: false, administer_platform: false },
};

let administers = false;
let refuseWith: string | null = null;
let patches: { url: string; body: unknown }[] = [];

function respond(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  administers = false;
  refuseWith = null;
  patches = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (method === "PATCH") {
        patches.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
        return refuseWith ? respond(409, { detail: refuseWith }) : respond(200, ANALYST);
      }
      if (url.includes("/session/")) {
        return respond(200, {
          authenticated: true,
          user: {
            ...ANALYST,
            id: "admin-id",
            username: "root",
            full_name: "Rui Admin",
            platform_role: administers ? "admin" : "analyst",
            capabilities: {
              publish_models: administers,
              approve_gates: administers,
              administer_platform: administers,
            },
          },
        });
      }
      if (url.includes("/support-bundle/")) {
        return respond(200, {
          generated_at: "2026-09-13T19:30:00Z",
          installation: {
            api_version: "1.0.0",
            oed_schema_version: "4.0.0",
            python: "3.11.9",
            packages: {},
            migrations: {},
          },
          configured: { CASS_METRICS_TOKEN: false },
          engines: {},
          capacity: [
            {
              profile: "standard",
              cpu: 4,
              memory_gb: 16,
              timeout_seconds: 7200,
              max_concurrent: 2,
              running: 1,
              available: 1,
              is_full: false,
            },
          ],
          runs: [],
          recent_failures: [],
          artifacts: [{ state: "registered", retention: "result", count: 12, bytes: 5242880 }],
          retention: { due_now: 3, kept_as_evidence: 2, abandoned_uploads: 1 },
          results: [],
          open_approvals: 0,
        });
      }
      if (url.includes("/users/")) {
        return respond(200, { count: 1, next: null, previous: null, results: [ANALYST] });
      }
      if (url.includes("/platform/")) {
        return respond(200, {
          api_version: "1.0.0",
          oed_schema_version: "4.0.0",
          compatibility_matrix: [],
          execution_profiles: {},
          default_execution_profile: "standard",
          artifact_backend: "s3",
          engines: {},
        });
      }
      if (url.includes("/engines/")) return respond(200, { engines: {} });
      return respond(200, { count: 0, next: null, previous: null, results: [] });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderScreen() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <WorkingContextProvider>
          <Administration />
        </WorkingContextProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Administration", () => {
  it("offers no role controls to somebody who is not an administrator", async () => {
    renderScreen();

    expect(await screen.findByText("Ada Analyst")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Platform role for Ada Analyst/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Deactivate" })).not.toBeInTheDocument();
  });

  it("sends the role an administrator chose", async () => {
    administers = true;
    const user = userEvent.setup();
    renderScreen();

    const role = await screen.findByLabelText(/Platform role for Ada Analyst/);
    await user.selectOptions(role, "reviewer");

    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0]?.url).toContain(`/users/${ANALYST.id}/`);
    expect(patches[0]?.body).toEqual({ platform_role: "reviewer" });
  });

  it("deactivates somebody without removing them from the record", async () => {
    administers = true;
    const user = userEvent.setup();
    renderScreen();

    await user.click(await screen.findByRole("button", { name: "Deactivate" }));

    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0]?.body).toEqual({ is_active: false });
  });

  it("shows the lock-out refusal as the API wrote it", async () => {
    administers = true;
    refuseWith =
      "This would leave the installation with no active administrator, and nobody able to reverse it.";
    const user = userEvent.setup();
    renderScreen();

    await user.click(await screen.findByRole("button", { name: "Deactivate" }));

    expect(await screen.findByText(/no active administrator/)).toBeInTheDocument();
  });

  it("shows an administrator what is queued, stored and due to expire", async () => {
    administers = true;
    renderScreen();

    expect(await screen.findByText("Queues, storage and retention")).toBeInTheDocument();
    // Due now, and kept because an approved result rests on it, are different facts.
    expect(await screen.findByText(/3 due to expire/)).toBeInTheDocument();
    expect(screen.getByText(/2 kept as evidence/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download support bundle" })).toBeInTheDocument();
  });

  it("offers the support bundle to nobody but an administrator", async () => {
    renderScreen();

    expect(await screen.findByText("Ada Analyst")).toBeInTheDocument();
    expect(screen.queryByText("Queues, storage and retention")).not.toBeInTheDocument();
  });
});
