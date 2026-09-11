"""Run state machine and pipeline rules."""

from __future__ import annotations

import pytest

from cass_core.runs import (
    ANALYSIS_PIPELINE,
    CONVERSION_PIPELINE,
    HAZARD_PIPELINE,
    IllegalStageSequence,
    IllegalTransition,
    RunState,
    can_transition,
    check_transition,
    describe,
    gate_stages,
    is_retryable,
    is_terminal,
    may_publish_results,
    pipeline,
    validate_stage_sequence,
)


def test_happy_path_transitions():
    state = RunState.DRAFT
    for target in (RunState.QUEUED, RunState.RUNNING, RunState.SUCCEEDED):
        state = check_transition(state, target)
    assert state is RunState.SUCCEEDED


def test_blocked_run_resumes_through_the_queue():
    """Admission control must still apply after a governance approval."""
    assert can_transition(RunState.RUNNING, RunState.BLOCKED)
    assert can_transition(RunState.BLOCKED, RunState.QUEUED)
    assert not can_transition(RunState.BLOCKED, RunState.RUNNING)


def test_terminal_states_admit_no_transition():
    for state in (RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED):
        assert is_terminal(state)
        with pytest.raises(IllegalTransition):
            check_transition(state, RunState.QUEUED)


def test_cancellation_passes_through_cancelling():
    assert can_transition(RunState.RUNNING, RunState.CANCELLING)
    assert can_transition(RunState.CANCELLING, RunState.CANCELLED)
    assert not can_transition(RunState.RUNNING, RunState.CANCELLED)


def test_only_a_successful_run_may_publish_results():
    """Section 11: cancellation must never leave published partial results."""
    assert may_publish_results(RunState.SUCCEEDED)
    for state in RunState:
        if state is not RunState.SUCCEEDED:
            assert not may_publish_results(state)


def test_only_failed_runs_are_retryable():
    assert is_retryable(RunState.FAILED)
    assert not is_retryable(RunState.CANCELLED)
    assert not is_retryable(RunState.SUCCEEDED)


@pytest.mark.parametrize(
    "target", [HAZARD_PIPELINE, CONVERSION_PIPELINE, ANALYSIS_PIPELINE]
)
def test_pipelines_have_unique_ordered_stages(target):
    keys = target.keys()
    assert len(keys) == len(set(keys))
    assert target.index_of(keys[0]) == 0
    assert target.next_after(keys[-1]) is None
    assert target.progress(keys[-1]) == pytest.approx(1.0)


def test_every_pipeline_ends_at_a_governance_gate():
    """No pipeline may complete without a reviewable gate at its end."""
    for key in ("hazard", "conversion", "analysis"):
        assert pipeline(key).stages[-1].gate is True
        assert gate_stages(key)


def test_analysis_gates_cover_keys_reconciliation():
    """Section 8 requires TIV reconciliation before a run may proceed."""
    assert "reconcile_keys" in gate_stages("analysis")


def test_stage_sequence_must_be_monotonic():
    validate_stage_sequence("analysis", ["validate_exposure", "enrich", "publish_oed"])
    # a retry of the same stage is legitimate
    validate_stage_sequence("analysis", ["keys", "keys", "reconcile_keys"])
    with pytest.raises(IllegalStageSequence):
        validate_stage_sequence("analysis", ["keys", "validate_exposure"])


def test_describe_is_serialisable_for_the_run_monitor():
    rows = describe("conversion")
    assert rows[0]["position"] == 0
    assert {"key", "label", "description", "gate", "position"} == set(rows[0])


def test_unknown_pipeline_is_an_error():
    with pytest.raises(KeyError):
        pipeline("flood")
