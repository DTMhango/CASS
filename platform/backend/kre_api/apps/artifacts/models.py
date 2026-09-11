"""The artifact registry.

Section 4: Django stores references to large artifacts, not scientific arrays.
Section 5: an artifact is recorded only after checksum and content validation
complete, and every object carries a content checksum, retention class and
access policy.

This model is that record. It holds no payload -- the bytes live in the
artifact store, addressed by the ``kre://`` URI on each row.
"""

from __future__ import annotations

import datetime as dt

from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel
from kre_core.artifacts import AccessPolicy, RetentionClass


class ArtifactState(models.TextChoices):
    PENDING = "pending", "Awaiting upload"
    """A session has been issued; no validated bytes exist yet."""

    REGISTERED = "registered", "Registered"
    """Bytes are present, checksummed and scanned."""

    QUARANTINED = "quarantined", "Quarantined"
    """Content validation failed. The object is unreadable by the application."""

    EXPIRED = "expired", "Expired"
    """Removed under its retention class; the record is kept for lineage."""


#: Default lifetimes by retention class, from the section 5 lifecycle table.
#: ``None`` means the class is retained until a governed decision removes it.
RETENTION_DAYS: dict[str, int | None] = {
    RetentionClass.PERMANENT: None,
    RetentionClass.MODEL_ASSET: None,
    RetentionClass.PORTFOLIO: None,
    RetentionClass.RESULT: None,
    RetentionClass.HAZARD_INTERMEDIATE: 90,
    RetentionClass.DIAGNOSTIC: 14,
    RetentionClass.OPERATIONAL: 30,
}


class Artifact(BaseModel):
    """One registered object in the artifact store."""

    uri = models.CharField(max_length=1100, unique=True)
    checksum = models.CharField(
        max_length=80,
        blank=True,
        help_text="Algorithm-prefixed content digest, recorded at registration.",
    )
    size_bytes = models.BigIntegerField(default=0)
    content_type = models.CharField(max_length=160, default="application/octet-stream")

    retention = models.CharField(
        max_length=32,
        choices=[(item.value, item.name.title().replace("_", " ")) for item in RetentionClass],
    )
    access = models.CharField(
        max_length=16,
        choices=[(item.value, item.name.title()) for item in AccessPolicy],
        default=AccessPolicy.PROJECT.value,
    )
    state = models.CharField(
        max_length=16, choices=ArtifactState.choices, default=ArtifactState.PENDING
    )

    #: Owning project, where the artifact is portfolio or result data. Model
    #: assets have no project: they belong to a model version instead.
    project = models.ForeignKey(
        "projects.Project",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="artifacts",
    )

    role = models.CharField(
        max_length=64,
        blank=True,
        help_text="What this artifact is in its manifest, such as oed_location or footprint.",
    )
    original_filename = models.CharField(max_length=255, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    validation_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "role"]),
            models.Index(fields=["state", "expires_at"]),
            models.Index(fields=["checksum"]),
        ]

    def __str__(self) -> str:
        return f"{self.role or 'artifact'} {self.uri}"

    def save(self, *args, **kwargs):
        if self.expires_at is None and self.state == ArtifactState.REGISTERED:
            self.expires_at = self.default_expiry()
        return super().save(*args, **kwargs)

    def default_expiry(self) -> dt.datetime | None:
        """Expiry implied by the retention class, or None where retained."""
        days = RETENTION_DAYS.get(self.retention)
        if days is None:
            return None
        return timezone.now() + dt.timedelta(days=days)

    @property
    def is_readable(self) -> bool:
        return self.state == ArtifactState.REGISTERED

    def may_read(self, user) -> bool:
        """Authorize retrieval.

        Section 10: a guessed object key must never grant access. Holding the
        URI is not sufficient; the caller must be entitled to the owning
        project, or to model and platform artifacts by role.
        """
        if user is None or not user.is_authenticated:
            return False
        if user.is_platform_admin:
            return True
        if self.access == AccessPolicy.PLATFORM:
            return False
        if self.access == AccessPolicy.MODEL:
            return True
        if self.project is None:
            return False
        return self.project.may_read(user)


class ArtifactLink(BaseModel):
    """Ties an artifact to the record it belongs to, with its manifest role.

    A single artifact can legitimately serve more than one record: a published
    OED location file is an input to every analysis run that uses it. Modelling
    the relationship separately keeps lineage complete without copying bytes.
    """

    artifact = models.ForeignKey(
        Artifact, on_delete=models.CASCADE, related_name="links"
    )
    subject_type = models.CharField(
        max_length=64,
        help_text="The kind of record, such as exposure_version, hazard_run or result_set.",
    )
    subject_id = models.UUIDField()
    role = models.CharField(max_length=64)
    direction = models.CharField(
        max_length=8,
        choices=[("input", "Input"), ("output", "Output")],
        default="output",
    )

    class Meta:
        indexes = [models.Index(fields=["subject_type", "subject_id"])]
        constraints = [
            models.UniqueConstraint(
                fields=["artifact", "subject_type", "subject_id", "role", "direction"],
                name="unique_artifact_link",
            )
        ]

    def __str__(self) -> str:
        return f"{self.subject_type}:{self.subject_id} {self.direction} {self.role}"
