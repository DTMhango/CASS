/**
 * The application frame.
 *
 * Build plan section 3 specifies a left navigation rail for stable product
 * areas and a wide content canvas for maps, curves and tables, and requires
 * model version, portfolio, financial perspective and run state to be
 * continuously visible. The context bar below the header is how the second
 * requirement is met: it persists across every screen rather than being
 * re-stated inside each one.
 */

import { NavLink, Outlet } from "react-router-dom";

import { ApiError } from "@/api/client";
import { useSession, useSignOut } from "@/api/hooks";
import { Button } from "@/components/primitives";
import { useWorkingContext } from "@/context/WorkingContext";
import logoBox from "@/assets/logo_box.png";

import { ContextBar } from "./ContextBar";
import "./AppShell.css";

interface NavItem {
  to: string;
  label: string;
  glyph: string;
  description: string;
}

/**
 * The sidebar, in the order the work happens.
 *
 * A person goes: pick a project, get the exposure right, build a run, watch it,
 * read the answer. That is the spine, and it is what the sidebar is for. Work
 * that is concurrent or cyclic rather than sequential -- import review beside
 * portfolio building, hazard runs beside the packages they feed -- sits in tabs
 * inside the area it belongs to, so the sidebar keeps meaning "what comes next"
 * instead of becoming a list of every screen.
 */
const NAVIGATION: NavItem[] = [
  {
    to: "/",
    label: "Portfolio dashboard",
    glyph: "\u25a4",
    description: "Current work, run status and exceptions",
  },
  {
    to: "/exposure",
    label: "Exposure",
    glyph: "\u25a6",
    description: "Import, review, correct and publish portfolio inputs",
  },
  {
    to: "/analysis",
    label: "Analysis builder",
    glyph: "\u25b7",
    description: "Configure a governed run",
  },
  {
    to: "/runs",
    label: "Run monitor",
    glyph: "\u25d0",
    description: "Progress and what each stage did",
  },
  {
    to: "/results",
    label: "Results",
    glyph: "\u25d4",
    description: "Loss metrics, comparisons and exports",
  },
  {
    to: "/models",
    label: "Models",
    glyph: "\u25c8",
    description: "Model versions, hazard runs and packages",
  },
  {
    to: "/administration",
    label: "Administration",
    glyph: "\u2699",
    description: "Users, engines, storage and audit",
  },
];

export function AppShell() {
  const { data: session } = useSession();
  const signOut = useSignOut();
  const context = useWorkingContext();

  const user = session?.user;

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>

      <nav className="rail" aria-label="Product areas">
        <div className="rail__brand">
          <img className="rail__logo" src={logoBox} alt="Klapton Re" />
          <span className="rail__brand-text">
            <span className="rail__brand-name">CASS</span>
            <span className="rail__brand-sub">Catastrophe Analytics & Scenario Suite</span>
          </span>
        </div>

        <ul className="rail__items">
          {NAVIGATION.map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  `rail__item ${isActive ? "rail__item--active" : ""}`.trim()
                }
                title={item.description}
              >
                <span className="rail__glyph" aria-hidden="true">
                  {item.glyph}
                </span>
                <span className="rail__label">{item.label}</span>
              </NavLink>
            </li>
          ))}
        </ul>

        <div className="rail__footer">
          {user ? (
            <>
              <p className="rail__user">{user.full_name || user.username}</p>
              <p className="rail__role">{roleLabel(user.platform_role)}</p>
            </>
          ) : null}
        </div>
      </nav>

      <div className="shell__main">
        <header className="topbar">
          <div className="topbar__title">
            {context.project ? (
              <>
                <span className="topbar__project">{context.project.name}</span>
                <span className="topbar__reference mono">{context.project.reference}</span>
              </>
            ) : (
              <span className="muted">No project selected</span>
            )}
          </div>
          <div className="topbar__actions">
            {user ? (
              <>
                {/* A sign out that fails silently is indistinguishable from a
                    button that does nothing, and the person is still signed
                    in, so the failure is said rather than swallowed. */}
                {signOut.isError ? (
                  <p className="topbar__signout-error" role="alert">
                    {signOutMessage(signOut.error)}
                  </p>
                ) : null}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => signOut.mutate()}
                  busy={signOut.isPending}
                >
                  Sign out
                </Button>
              </>
            ) : null}
          </div>
        </header>

        <ContextBar />

        <main className="canvas" id="main" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}

/** What to say when the API could not end the session. */
function signOutMessage(error: unknown): string {
  if (error instanceof ApiError) return `${error.message} You are still signed in.`;
  return "Signing out could not reach the platform. You are still signed in.";
}

function roleLabel(role: string): string {
  const labels: Record<string, string> = {
    analyst: "Portfolio analyst",
    modeller: "Catastrophe modeller",
    underwriter: "Underwriter",
    reviewer: "Reviewer",
    admin: "Platform administrator",
  };
  return labels[role] ?? role;
}
