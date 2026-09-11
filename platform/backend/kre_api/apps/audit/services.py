"""Recording audit events and deciding governance gates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.utils import timezone

from .middleware import current_correlation_id
from .models import Approval, AuditAction, AuditEvent, SelfApprovalRefused


def record(
    *,
    action: AuditAction | str,
    subject_type: str,
    subject_id: Any,
    actor=None,
    project=None,
    subject_label: str = "",
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    detail: str = "",
    request=None,
) -> AuditEvent:
    """Append one audit event.

    ``before`` and ``after`` should be references -- identifiers, checksums,
    states -- not full copies of the record.
    """
    source_ip = None
    user_agent = ""
    if request is not None:
        source_ip = _client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")[:255]
        if actor is None and getattr(request, "user", None) and request.user.is_authenticated:
            actor = request.user

    return AuditEvent.objects.create(
        actor=actor,
        actor_label=str(actor) if actor else "",
        action=str(action),
        subject_type=subject_type,
        subject_id=str(subject_id),
        subject_label=subject_label[:255],
        project=project,
        before_reference=dict(before) if before else None,
        after_reference=dict(after) if after else None,
        correlation_id=current_correlation_id() or "",
        source_ip=source_ip,
        user_agent=user_agent,
        detail=detail,
    )


def _client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def request_gate(
    *,
    gate: Approval.Gate | str,
    subject_type: str,
    subject_id,
    requested_by,
    evidence: Mapping[str, Any] | None = None,
) -> Approval:
    """Open a governance gate for review."""
    approval = Approval.objects.create(
        gate=str(gate),
        subject_type=subject_type,
        subject_id=subject_id,
        requested_by=requested_by,
        evidence=dict(evidence or {}),
    )
    record(
        action=AuditAction.SUBMIT,
        subject_type="approval",
        subject_id=approval.id,
        actor=requested_by,
        subject_label=approval.get_gate_display(),
        after={"gate": str(gate), "subject": f"{subject_type}:{subject_id}"},
    )
    return approval


def decide_gate(
    approval: Approval,
    *,
    decision: Approval.Decision | str,
    decided_by,
    rationale: str = "",
    evidence: Mapping[str, Any] | None = None,
) -> Approval:
    """Grant or refuse a gate, enforcing independent challenge.

    The approver must be entitled to approve and must not be the person who
    requested the gate. Section 10 assigns each gate a named approver precisely
    so the decision is not self-certified.
    """
    if not getattr(decided_by, "may_approve_gates", False):
        raise SelfApprovalRefused(
            f"{decided_by} does not hold a role that may decide the "
            f"{approval.get_gate_display()} gate"
        )
    if approval.requested_by_id and approval.requested_by_id == getattr(decided_by, "id", None):
        raise SelfApprovalRefused(
            "a gate may not be decided by the person who requested it"
        )
    if not approval.is_open:
        raise SelfApprovalRefused(
            f"the {approval.get_gate_display()} gate has already been {approval.decision}"
        )

    before = {"decision": approval.decision}
    approval.decision = str(decision)
    approval.decided_by = decided_by
    approval.decided_at = timezone.now()
    approval.rationale = rationale
    if evidence:
        approval.evidence = {**approval.evidence, **dict(evidence)}
    approval.save(
        update_fields=["decision", "decided_by", "decided_at", "rationale", "evidence"]
    )

    record(
        action=(
            AuditAction.APPROVE
            if approval.is_cleared
            else AuditAction.REJECT
        ),
        subject_type="approval",
        subject_id=approval.id,
        actor=decided_by,
        subject_label=approval.get_gate_display(),
        before=before,
        after={"decision": approval.decision},
        detail=rationale,
    )
    return approval


def gate_is_cleared(gate: Approval.Gate | str, subject_type: str, subject_id) -> bool:
    """Report whether a subject has a granted approval for a gate."""
    return Approval.objects.filter(
        gate=str(gate),
        subject_type=subject_type,
        subject_id=subject_id,
        decision=Approval.Decision.APPROVED,
    ).exists()
