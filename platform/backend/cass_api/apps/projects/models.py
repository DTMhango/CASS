"""Project workspaces.

Section 5 defines the project as the business workspace that owns exposure,
runs and results. Section 10 requires per-project authorization on both
metadata and artifact retrieval, so the membership table here is the single
place that answers "may this person see this object".
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class ProjectRole(models.TextChoices):
    VIEWER = "viewer", "Viewer"
    """May read exposure, runs and approved results."""

    CONTRIBUTOR = "contributor", "Contributor"
    """May create exposure versions and submit runs."""

    OWNER = "owner", "Owner"
    """May manage membership and delete the project."""


class ProjectStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class Project(BaseModel):
    """A business workspace."""

    name = models.CharField(max_length=200)
    reference = models.SlugField(
        max_length=64,
        unique=True,
        help_text="Short stable reference used in artifact keys and exports.",
    )
    purpose = models.TextField(
        blank=True,
        help_text="Why the analysis is being run. Appears on exported result packages.",
    )
    team = models.CharField(max_length=120, blank=True)
    status = models.CharField(
        max_length=16, choices=ProjectStatus.choices, default=ProjectStatus.ACTIVE
    )
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="ProjectMembership",
        # Membership also carries created_by and updated_by, so the join
        # columns have to be named explicitly.
        through_fields=("project", "user"),
        related_name="projects",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.reference} {self.name}"

    @property
    def artifact_prefix(self) -> str:
        """The key prefix every artifact belonging to this project sits under.

        Keeping projects on distinct prefixes lets a storage lifecycle rule or
        a deletion request apply to one project without touching another.
        """
        return f"project/{self.reference}"

    def role_for(self, user) -> str | None:
        """Return the user's role in this project, or None."""
        if user is None or not user.is_authenticated:
            return None
        if user.is_platform_admin:
            return ProjectRole.OWNER
        membership = self.memberships.filter(user=user).first()
        return membership.role if membership else None

    def may_read(self, user) -> bool:
        return self.role_for(user) is not None

    def may_write(self, user) -> bool:
        return self.role_for(user) in (ProjectRole.CONTRIBUTOR, ProjectRole.OWNER)

    def may_administer(self, user) -> bool:
        return self.role_for(user) == ProjectRole.OWNER


class ProjectMembership(BaseModel):
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(
        max_length=16, choices=ProjectRole.choices, default=ProjectRole.VIEWER
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "user"], name="unique_project_membership"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} as {self.role} on {self.project.reference}"
