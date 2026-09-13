"""The data standards CASS reads exposure against.

Section 17 requires the OED version in use to be pinned, registered and
comparable: the official release JSON is published by the ODS Open Exposure
Data project, runtime interpretation belongs to a pinned ODS Tools, and section
8 makes the adoption of OED 5 a decision somebody takes against evidence rather
than a dependency bump.

So a standard version is a registry record like any other governed input. It
names where the specification came from, carries the specification itself as an
immutable checksummed artifact, and states whether CASS reads exposure against
it, is considering it, or has moved past it. What CASS cannot do is quietly
follow whatever version the installed library happens to ship.
"""

from __future__ import annotations

from django.db import models

from apps.common.models import BaseModel, FreezableModel


class StandardState(models.TextChoices):
    CANDIDATE = "candidate", "Candidate"
    """Registered and comparable, but not what exposure is read against."""

    ACTIVE = "active", "Active"
    """The version CASS validates and publishes exposure against."""

    SUPERSEDED = "superseded", "Superseded"


class DataStandardVersion(BaseModel, FreezableModel):
    """One version of one data standard, as published by its owner."""

    standard = models.CharField(
        max_length=16, default="OED", db_index=True,
        help_text="The standard itself. OED is the only one CASS reads today.",
    )
    version = models.CharField(max_length=16, db_index=True)
    state = models.CharField(
        max_length=16, choices=StandardState.choices, default=StandardState.CANDIDATE
    )

    source = models.CharField(
        max_length=200,
        help_text="Where the specification came from, such as the pinned ODS Tools release.",
    )
    reference_uri = models.CharField(
        max_length=500,
        blank=True,
        help_text="The registered specification artifact this record was read from.",
    )
    checksum = models.CharField(max_length=80, blank=True)

    #: Counted from the specification rather than stated, so a record cannot
    #: claim a shape its own artifact does not have.
    file_kinds = models.JSONField(default=dict, blank=True)
    field_count = models.IntegerField(default=0)

    notes = models.TextField(blank=True)
    adopted_at = models.DateTimeField(null=True, blank=True)
    adopted_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="data_standards_adopted",
    )

    class Meta:
        ordering = ["standard", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["standard", "version"], name="unique_data_standard_version"
            )
        ]

    def __str__(self) -> str:
        return f"{self.standard} {self.version}"

    @property
    def is_active(self) -> bool:
        return self.state == StandardState.ACTIVE
