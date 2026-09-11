# 1. Separate backend and frontend trees

Status: Accepted
Date: 2026-09-11

## Context

The platform is one product with two very different halves. The backend is
Python, holds the control plane and the scientific packages, and is deployed as
several containers. The frontend is TypeScript, builds to static assets, and is
deployed behind a web server.

They share a contract — the KRE API — and nothing else. They do not share
dependencies, tooling, test runners, linters or release cadence.

## Decision

`platform/backend/` and `platform/frontend/` are separate top-level trees, each
with its own dependency manifest, lockfile, linter configuration and test
runner. Neither imports from the other.

The contract between them is the OpenAPI schema the backend generates, which CI
regenerates with `--fail-on-warn` on every change.

## Alternatives considered

**A single tree with the frontend nested under the API app.** Common in Django
projects that serve templates. It makes the interface feel like a Django
concern, which is precisely the inversion section 3 warns against: the web
application is the primary operating interface, not a reporting layer added
after the engine work.

**Separate repositories.** Clean separation, at the cost of atomic changes. A
change that alters an API response and the component reading it would become
two pull requests with a window between them where the product is broken.

## Consequences

Each half is worked on with its own toolchain, and a frontend change cannot
break the backend build or vice versa. CI runs them as independent jobs, so a
failure points at one half.

A contract change still needs care: the TypeScript types are hand-written
against the schema rather than generated from it, so that a backend field
rename surfaces as a type error rather than an `undefined` at runtime. That is
a deliberate trade of a little duplication for a compile-time signal.

## Revisit if

The hand-written types drift from the schema often enough to be a source of
defects. Generation from the OpenAPI document is the obvious answer, and the
schema is already produced cleanly enough to support it.
