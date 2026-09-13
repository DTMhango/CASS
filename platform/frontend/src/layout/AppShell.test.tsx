/**
 * Signing out, driven through the gate that decides what a person can see.
 *
 * The button is only half of signing out. The other half is that the interface
 * stops showing the signed-out person's work, so this renders the real session
 * gate rather than the shell alone: a sign out that clears the API session and
 * leaves the dashboard on screen is the failure this covers, and it is
 * indistinguishable to the user from a button that does nothing.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/App";
import { WorkingContextProvider } from "@/context/WorkingContext";

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";

const SESSION = {
  authenticated: true,
  user: {
    id: "22222222-2222-2222-2222-222222222222",
    username: "analyst",
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

let authenticated = true;
let deletes = 0;
/** What the API answers a sign out with, so a test can make it fail. */
let deleteOutcome: { status: number; body: unknown } = { status: 204, body: null };

function routeFor(url: string, method: string): { status: number; body: unknown } {
  if (url.includes("/session/")) {
    if (method === "DELETE") {
      deletes += 1;
      if (deleteOutcome.status < 400) authenticated = false;
      return deleteOutcome;
    }
    return { status: 200, body: authenticated ? SESSION : { authenticated: false } };
  }
  // Once the session has ended the API refuses everything, so a screen that is
  // still mounted cannot pull anything back after a sign out.
  if (!authenticated) {
    return { status: 401, body: { detail: "Authentication credentials were not provided." } };
  }
  if (url.includes("/platform/") || url.includes("/engines/")) {
    return { status: 200, body: { engines: {} } };
  }
  // The catalogue answers with its models under a key rather than a page, and
  // a page here left the query holding undefined.
  if (url.includes("/model-versions/catalogue/")) {
    return { status: 200, body: { models: [] } };
  }
  return { status: 200, body: { count: 0, next: null, previous: null, results: [] } };
}

let client: QueryClient;

function fetchedPaths(): string[] {
  const mock = fetch as unknown as { mock: { calls: [RequestInfo | URL][] } };
  return mock.mock.calls.map(([input]) => String(input));
}

function renderApp() {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <WorkingContextProvider>
          <App />
        </WorkingContextProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  authenticated = true;
  deletes = 0;
  deleteOutcome = { status: 204, body: null };
  window.localStorage.setItem(
    "cass.working-context.v1",
    JSON.stringify({ projectId: PROJECT_ID }),
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const { status, body } = routeFor(String(input), init?.method ?? "GET");
      if (status === 204) return new Response(null, { status: 204 });
      return new Response(JSON.stringify(body), {
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

describe("signing out", () => {
  it("ends the API session and returns to the sign-in screen", async () => {
    const user = userEvent.setup();
    renderApp();

    await user.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(deletes).toBe(1);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("drops the signed-out person's work from the cache", async () => {
    const user = userEvent.setup();
    renderApp();

    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    await screen.findByRole("button", { name: "Sign in" });

    // The shell fetched for the previous person, so there is something to drop.
    expect(fetchedPaths().some((path) => path.includes("/runs/"))).toBe(true);

    // Nothing of that work may be answered from the cache when the next person
    // signs in. A query can be rebuilt empty by a screen that has not unmounted
    // yet, which is why this asks what is readable rather than what is present.
    const readable = client
      .getQueryCache()
      .getAll()
      .filter((query) => query.queryKey[0] !== "session" && query.state.data !== undefined);
    expect(readable).toEqual([]);
  });

  it("says so when the platform cannot be reached, rather than failing quietly", async () => {
    const user = userEvent.setup();
    deleteOutcome = { status: 503, body: { detail: "The platform is unavailable." } };
    renderApp();

    await user.click(await screen.findByRole("button", { name: "Sign out" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The platform is unavailable.");
    expect(alert).toHaveTextContent("You are still signed in.");
    // Still signed in means still on the shell, not on a half-cleared screen.
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("ends the session anyway when the API says nobody is signed in", async () => {
    const user = userEvent.setup();
    // A session that expired server-side: the browser still shows the shell,
    // and the sign out it sends is refused.
    deleteOutcome = {
      status: 401,
      body: { detail: "Authentication credentials were not provided." },
    };
    renderApp();

    await user.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });
});
