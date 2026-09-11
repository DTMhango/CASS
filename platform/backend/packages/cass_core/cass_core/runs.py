"""The CASS run state machine and pipeline definitions.

Build plan section 17 requires the run state machine to be defined before
engine work spreads across services, and section 12 requires that an engine or
worker failure produce an intelligible state with a safe retry or cancellation
path. The rules live here rather than in Django models so that workers, the
converter and the keys service all agree on what a state means.

Two ideas are kept separate:

``RunState``
    The lifecycle of the run as a whole. This is what the portfolio dashboard
    and the run monitor show, and it is the only thing that decides whether a
    result may be published.

``Stage``
    The position of the run inside its pipeline. Stages explain progress and
    locate a failure; they never bypass the lifecycle rules.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Iterable, Mapping


class RunState(enum.StrEnum):
    """Lifecycle states shared by hazard, conversion and analysis runs."""

    DRAFT = "draft"
    """Being configured. Nothing has been submitted to an engine."""

    QUEUED = "queued"
    """Admitted to a queue; waiting for a worker slot under the resource profile."""

    RUNNING = "running"
    """A worker holds the run and is executing a stage."""

    BLOCKED = "blocked"
    """Stopped at a governance gate that a person must clear.

    Used for permitted exceptions such as unreconciled keys TIV. The run is not
    failed; it is waiting for an approval that section 8 requires.
    """

    CANCELLING = "cancelling"
    """Cancellation requested; the worker is unwinding to a clean point."""

    CANCELLED = "cancelled"
    """Stopped by request. No partial result is published."""

    FAILED = "failed"
    """Stopped by error. The failure stage and evidence are retained."""

    SUCCEEDED = "succeeded"
    """All stages completed and produced their declared artifacts."""


#: States from which no further transition is possible.
TERMINAL_STATES: frozenset[RunState] = frozenset(
    {RunState.CANCELLED, RunState.FAILED, RunState.SUCCEEDED}
)

#: States in which a run is holding or waiting for compute.
ACTIVE_STATES: frozenset[RunState] = frozenset(
    {RunState.QUEUED, RunState.RUNNING, RunState.CANCELLING}
)

_ALLOWED: Mapping[RunState, frozenset[RunState]] = {
    RunState.DRAFT: frozenset({RunState.QUEUED, RunState.CANCELLED}),
    RunState.QUEUED: frozenset({RunState.RUNNING, RunState.CANCELLING, RunState.FAILED}),
    RunState.RUNNING: frozenset(
        {
            RunState.BLOCKED,
            RunState.CANCELLING,
            RunState.FAILED,
            RunState.SUCCEEDED,
        }
    ),
    # A blocked run resumes into the queue rather than straight into RUNNING, so
    # that admission control still applies after an approval.
    RunState.BLOCKED: frozenset({RunState.QUEUED, RunState.CANCELLING, RunState.FAILED}),
    RunState.CANCELLING: frozenset({RunState.CANCELLED, RunState.FAILED}),
    RunState.CANCELLED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.SUCCEEDED: frozenset(),
}


class IllegalTransition(Exception):
    """Raised when a caller attempts a transition the state machine forbids."""

    def __init__(self, current: RunState, requested: RunState) -> None:
        super().__init__(f"cannot move a run from {current} to {requested}")
        self.current = current
        self.requested = requested


def allowed_transitions(current: RunState) -> frozenset[RunState]:
    """Return the states reachable from ``current``."""
    return _ALLOWED[RunState(current)]


def can_transition(current: RunState, requested: RunState) -> bool:
    """Report whether a transition is permitted."""
    return RunState(requested) in allowed_transitions(current)


def check_transition(current: RunState, requested: RunState) -> RunState:
    """Validate a transition, returning the new state or raising."""
    if not can_transition(current, requested):
        raise IllegalTransition(RunState(current), RunState(requested))
    return RunState(requested)


def is_terminal(state: RunState) -> bool:
    """Report whether a run has reached a state it can never leave."""
    return RunState(state) in TERMINAL_STATES


def may_publish_results(state: RunState) -> bool:
    """Only a successful run may release results to decision users.

    Section 11 requires that cancellation and restart never leave published
    partial results, so this is the single predicate the result catalogue uses.
    """
    return RunState(state) is RunState.SUCCEEDED


def is_retryable(state: RunState) -> bool:
    """A failed run may be retried; a cancelled or successful one may not."""
    return RunState(state) is RunState.FAILED


@dataclasses.dataclass(frozen=True, slots=True)
class Stage:
    """One step of a pipeline.

    ``gate`` marks a stage that can legitimately hold the run in ``BLOCKED``
    while a reviewer approves a permitted exception.
    """

    key: str
    label: str
    description: str
    gate: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class Pipeline:
    """An ordered list of stages for one kind of run."""

    key: str
    label: str
    stages: tuple[Stage, ...]

    def keys(self) -> tuple[str, ...]:
        return tuple(stage.key for stage in self.stages)

    def index_of(self, stage_key: str) -> int:
        for position, stage in enumerate(self.stages):
            if stage.key == stage_key:
                return position
        raise KeyError(f"{stage_key!r} is not a stage of pipeline {self.key!r}")

    def stage(self, stage_key: str) -> Stage:
        return self.stages[self.index_of(stage_key)]

    def next_after(self, stage_key: str) -> Stage | None:
        position = self.index_of(stage_key)
        if position + 1 >= len(self.stages):
            return None
        return self.stages[position + 1]

    def progress(self, stage_key: str) -> float:
        """Fraction of the pipeline completed once ``stage_key`` finishes."""
        return (self.index_of(stage_key) + 1) / len(self.stages)


HAZARD_PIPELINE = Pipeline(
    key="hazard",
    label="OpenQuake hazard run",
    stages=(
        Stage("prepare", "Prepare job", "Resolve grid version, site model and settings into a job."),
        Stage("validate_settings", "Validate settings", "Check the OpenQuake configuration before submission."),
        Stage("submit", "Submit to OpenQuake", "Create the calculation through the supported REST boundary."),
        Stage("monitor", "Monitor calculation", "Poll calculation state and capture logs."),
        Stage("export", "Export hazard", "Export the GMF in HDF5 and register it as an artifact."),
        Stage("benchmark", "Benchmark hazard", "Compare curves against approved benchmarks.", gate=True),
    ),
)

CONVERSION_PIPELINE = Pipeline(
    key="conversion",
    label="OpenQuake to Oasis conversion",
    stages=(
        Stage("manifest", "Read manifest", "Validate the conversion manifest, versions and checksums."),
        Stage("events", "Map events", "Apply the approved event identity specification."),
        Stage("occurrence", "Build occurrence", "Apply the occurrence policy and reconcile annual frequency."),
        Stage("footprint", "Bin footprint", "Chunk the GMF and emit intensity-bin probabilities by area peril."),
        Stage("vulnerability", "Discretise vulnerability", "Translate source functions into damage-bin probabilities."),
        Stage("package", "Assemble package", "Compile runtime assets and write the model package."),
        Stage("qa", "Scientific QA", "Run acceptance tests and record tolerances.", gate=True),
    ),
)

ANALYSIS_PIPELINE = Pipeline(
    key="analysis",
    label="Portfolio loss analysis",
    stages=(
        Stage("validate_exposure", "Validate exposure", "Check identifiers, hierarchy, codes, currency and TIV."),
        Stage("enrich", "Apply assumptions", "Run the approved assumption set and reconcile allocated TIV."),
        Stage("publish_oed", "Publish OED", "Freeze immutable Location, Account and reinsurance artifacts."),
        Stage("keys", "Run keys lookup", "Map exposure to area peril and vulnerability identifiers."),
        Stage("reconcile_keys", "Reconcile keys", "Reconcile success, not-at-risk and failed TIV to source.", gate=True),
        Stage("generate_inputs", "Generate Oasis files", "Create GUL, IL and RI kernel files with the pinned library."),
        Stage("validate_inputs", "Validate Oasis files", "Check schemas, references, hierarchy and summary mappings."),
        Stage("smoke", "Pre-loss smoke check", "Run a reduced event set before admitting the full analysis."),
        Stage("losses", "Calculate losses", "Run ground-up and any supported financial perspectives."),
        Stage("collect", "Collect results", "Ingest ORD outputs, build CASS summaries and write the run manifest."),
        Stage("review", "Result review", "Hold results until operational and scientific checks pass.", gate=True),
    ),
)

PIPELINES: Mapping[str, Pipeline] = {
    HAZARD_PIPELINE.key: HAZARD_PIPELINE,
    CONVERSION_PIPELINE.key: CONVERSION_PIPELINE,
    ANALYSIS_PIPELINE.key: ANALYSIS_PIPELINE,
}


def pipeline(key: str) -> Pipeline:
    """Look up a pipeline by key."""
    try:
        return PIPELINES[key]
    except KeyError as exc:
        raise KeyError(f"unknown pipeline {key!r}") from exc


def gate_stages(key: str) -> tuple[str, ...]:
    """Return the stage keys that may legitimately block a run for approval."""
    return tuple(stage.key for stage in pipeline(key).stages if stage.gate)


def describe(key: str) -> list[dict[str, object]]:
    """Serialise a pipeline for the run monitor."""
    target = pipeline(key)
    return [
        {
            "key": stage.key,
            "label": stage.label,
            "description": stage.description,
            "gate": stage.gate,
            "position": position,
        }
        for position, stage in enumerate(target.stages)
    ]


def validate_stage_sequence(key: str, observed: Iterable[str]) -> None:
    """Raise if a recorded stage sequence skips backwards or repeats out of order.

    Workers are idempotent and may re-run a stage, but a pipeline must never
    move backwards: that would indicate lost lineage rather than a retry.
    """
    target = pipeline(key)
    highest = -1
    for stage_key in observed:
        position = target.index_of(stage_key)
        if position < highest:
            raise IllegalStageSequence(
                f"stage {stage_key!r} follows a later stage in pipeline {key!r}"
            )
        highest = max(highest, position)


class IllegalStageSequence(Exception):
    """Raised when a recorded stage history is not monotonic."""
