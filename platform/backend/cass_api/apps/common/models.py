"""Model mixins shared across the control plane.

Two decisions are applied everywhere rather than app by app.

Identifiers are UUIDs. Run, exposure and model identifiers appear in artifact
keys, engine job names and exported result packages, so a sequential integer
would leak volume and invite guessing at an object key -- which section 10
forbids from granting access.

Records are timestamped and attributed. Section 5 requires an audit event to
name an actor, action, timestamp and object, and that is only possible if the
objects themselves carry creation and modification provenance.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class UUIDModel(models.Model):
    """Primary key that is safe to expose in URLs and artifact keys."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AttributedModel(models.Model):
    """Records who created a record and who last changed it."""

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_updated",
    )

    class Meta:
        abstract = True


class BaseModel(UUIDModel, TimestampedModel, AttributedModel):
    """The default base for control-plane records."""

    class Meta:
        abstract = True


class ImmutableError(Exception):
    """Raised when a caller tries to change a frozen record."""


class FreezableModel(models.Model):
    """A record that becomes immutable once published.

    Section 5 makes exposure versions, accepted model packages and result
    packages immutable. Enforcing that in the model rather than in a view means
    a management command or a worker cannot bypass it either.
    """

    frozen_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.is_frozen and not self._state.adding:
            requested = kwargs.get("update_fields")
            # A save with no update_fields writes every column, so it can never
            # be one of the two the freeze itself needs.
            allowed = set(requested) if requested else None
            if allowed is None or not allowed <= {"frozen_at", "updated_at"}:
                raise ImmutableError(
                    f"{type(self).__name__} {self.pk} is published and cannot be modified; "
                    "create a new version instead"
                )
        return super().save(*args, **kwargs)

    @property
    def is_frozen(self) -> bool:
        return self.frozen_at is not None

    def freeze(self, *, save: bool = True) -> None:
        from django.utils import timezone

        if self.is_frozen:
            return
        self.frozen_at = timezone.now()
        if save:
            super().save(update_fields=["frozen_at", "updated_at"])
