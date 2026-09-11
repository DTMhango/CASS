# 3. One run table, with pipelines selected by kind

Status: Accepted
Date: 2026-09-11

## Context

Section 5 of the build plan defines three run records: hazard, conversion and
analysis. Each has its own detail, but all three share a lifecycle — queued,
running, blocked at a gate, cancelled, failed, succeeded — and all three need
the same things from the platform: admission control under a resource profile,
cancellation that leaves no partial result, an intelligible failure state, a
safe retry path, and an append-only stage history.

Section 12 makes that shared behaviour a release acceptance criterion rather
than an implementation detail.

## Decision

One `Run` table holds the lifecycle, with a `kind` that selects a pipeline
definition from `cass_core.runs`. Kind-specific detail lives in a one-to-one
record: `HazardRun`, `ConversionRun`, `AnalysisRun`.

The state machine itself lives in `cass_core`, not in the Django model, so the
workers, the converter and the keys service enforce the same rules as the API.

Pipelines are data. The run monitor renders whatever stage list the API
returns, so the interface cannot drift from the pipeline the workers follow.

## Alternatives considered

**Three separate tables.** Each would reimplement transitions, cancellation,
retry and stage history. Three implementations of "cancellation must not
publish a partial result" is three chances to get it wrong, and the one that is
wrong will be the one that matters.

**A generic job table with no pipeline concept.** Loses the ability to explain
progress, which section 3 makes the run monitor's whole purpose.

**Celery task state as the source of truth.** Task state is transient and
broker-specific. A run is a governance record that outlives its execution.

## Consequences

Queue admission, cancellation, retry and audit are written once. A new kind of
run — a future peril, a model-build variant — adds a pipeline definition and a
detail model, not a lifecycle.

The cost is a join for kind-specific fields, and a `kind` discriminator that
callers must respect. Both are cheap next to three divergent lifecycles.

## Revisit if

A kind of run needs a lifecycle the shared machine cannot express. A long
interactive session, or a run that legitimately moves backwards through its
stages, would be the signal.
