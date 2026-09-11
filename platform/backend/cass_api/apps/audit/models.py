"""The audit trail.

Section 10 requires access, download, deletion, model publication, exception
approval and configuration changes to be recorded in an immutable audit trail,
and section 5 requires each event to name an actor, action, timestamp, object
and the before and after references.

The table is append-only: ``save`` refuses to update an existing row, and there
is no update path through the API.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimestampedModel, UUIDModel


class AuditAction(models.TextChoices):
    """The governed actions. Adding one is a deliberate change, not a free string."""

    CREATE = "create", "Created"
    UPDATE = "update", "Updated"
    DELETE = "delete", "Deleted"
    READ = "read", "Read"
    DOWNLOAD = "download", "Downloaded"
    UPLOAD = "upload", "Uploaded"
    PUBLISH = "publish", "Published"
    SUBMIT = "submit", "Submitted"
    CANCEL = "cancel", "Cancelled"
    RETRY = "retry", "Retried"
    APPROVE = "approve", "Approved"
    REJECT = "reject", "Rejected"
    OVERRIDE = "override", "Overrode a value"
    CONFIGURE = "configure", "Changed configuration"
    SIGN_IN = "sign_in", "Signed in"
    SIGN_IN_FAILED = "sign_in_failed", "Sign-in failed"


class AuditEvent(UUIDModel, TimestampedModel):
    """One immutable governance record."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
        help_text="Null where the action was taken by a worker rather than a person.",
    )
    actor_label = models.CharField(
        max_length=150,
        blank=True,
        help_text="Actor name captured at the time, so the record survives user deletion.",
    )
    action = models.CharField(max_length=32, choices=AuditAction.choices, db_index=True)

    subject_type = models.CharField(max_length=64, db_index=True)
    subject_id = models.CharField(max_length=64, db_index=True)
    subject_label = models.CharField(max_length=255, blank=True)

    project = models.ForeignKey(
        "projects.Project",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )

    #: References to the state before and after, rather than copies of it.
    #: Section 5 asks for before and after *references*; storing full payloads
    #: would turn the audit table into a second copy of the control plane.
    before_reference = models.JSONField(null=True, blank=True)
    after_reference = models.JSONField(null=True, blank=True)

    correlation_id = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text="Follows one run across Django, the workers and the engine adapters.",
    )
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    detail = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["subject_type", "subject_id", "-created_at"]),
            models.Index(fields=["project", "-created_at"]),
            models.Index(fields=["actor", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.actor_label or 'system'} {self.action} {self.subject_type}:{self.subject_id}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AuditImmutable("audit events are append-only and cannot be modified")
        if self.actor and not self.actor_label:
            self.actor_label = str(self.actor)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditImmutable("audit events cannot be deleted")


class AuditImmutable(Exception):
    """Raised on any attempt to change or remove an audit record."""


class Approval(UUIDModel, TimestampedModel):
    """A governance gate decision.

    Section 10 lists seven gates, each with required evidence and a named
    approver. A gate decision is separate from an audit event because it has a
    lifecycle: it is requested, then granted or refused, and the evidence it
    rests on must be retrievable afterwards.
    """

    class Gate(models.TextChoices):
        HAZARD_CANDIDATE = "hazard_candidate", "Hazard candidate"
        EXPOSURE_ENRICHMENT = "exposure_enrichment", "Exposure-enrichment candidate"
        CONVERTER_CANDIDATE = "converter_candidate", "Converter candidate"
        VULNERABILITY_CANDIDATE = "vulnerability_candidate", "Vulnerability candidate"
        DATA_RIGHTS = "data_rights", "Data-rights gate"
        MODEL_RELEASE = "model_release", "Model release"
        PLATFORM_RELEASE = "platform_release", "Platform release"
        RUN_EXCEPTION = "run_exception", "Permitted run exception"

    class Decision(models.TextChoices):
        REQUESTED = "requested", "Requested"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    gate = models.CharField(max_length=32, choices=Gate.choices, db_index=True)
    decision = models.CharField(
        max_length=16, choices=Decision.choices, default=Decision.REQUESTED, db_index=True
    )

    subject_type = models.CharField(max_length=64)
    subject_id = models.UUIDField()

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="approvals_requested",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approvals_decided",
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    rationale = models.TextField(
        blank=True, help_text="Why the gate was cleared or refused."
    )
    evidence = models.JSONField(
        default=dict,
        blank=True,
        help_text="Artifact URIs and check results the decision rested on.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["subject_type", "subject_id", "gate"])]

    def __str__(self) -> str:
        return f"{self.get_gate_display()} {self.decision}"

    @property
    def is_open(self) -> bool:
        return self.decision == self.Decision.REQUESTED

    @property
    def is_cleared(self) -> bool:
        return self.decision == self.Decision.APPROVED


class SelfApprovalRefused(Exception):
    """Raised when an actor tries to approve their own submission.

    Section 10 requires independent challenge at defined gates; section 15
    names treating scientific validation as software QA a risk. Independence
    is therefore enforced rather than assumed.
    """
