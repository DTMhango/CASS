"""Users and roles.

Build plan section 2 names five user types and section 10 requires
least-privilege roles. Roles are modelled in two places on purpose: a platform
role that governs service-wide capability, and a project role that governs one
workspace. A catastrophe modeller on one project is not automatically one
everywhere.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models

from apps.common.models import UUIDModel


class PlatformRole(models.TextChoices):
    """Service-wide capability, independent of any project."""

    ANALYST = "analyst", "Portfolio analyst"
    MODELLER = "modeller", "Catastrophe modeller"
    UNDERWRITER = "underwriter", "Underwriter or pricing user"
    REVIEWER = "reviewer", "Reviewer or approver"
    ADMIN = "admin", "Platform administrator"


class User(UUIDModel, AbstractUser):
    """A KRE user.

    ``platform_role`` decides what a person may do outside a project: publish a
    model version, approve a gate, administer the service. Project membership
    decides what they may see inside one.
    """

    platform_role = models.CharField(
        max_length=32,
        choices=PlatformRole.choices,
        default=PlatformRole.ANALYST,
        help_text="Service-wide capability. Project access is granted separately.",
    )
    job_title = models.CharField(max_length=120, blank=True)

    #: Section 10 requires device eligibility for approved local deployments.
    local_install_approved = models.BooleanField(
        default=False,
        help_text="Whether this user may run an approved local Docker installation.",
    )

    class Meta(AbstractUser.Meta):
        db_table = "kre_user"

    def __str__(self) -> str:
        return self.get_full_name() or self.username

    # -- capability checks used by permission classes ----------------------
    @property
    def is_platform_admin(self) -> bool:
        return self.is_superuser or self.platform_role == PlatformRole.ADMIN

    @property
    def may_publish_models(self) -> bool:
        """Publishing a model version is a modeller or administrator action."""
        return self.is_platform_admin or self.platform_role == PlatformRole.MODELLER

    @property
    def may_approve_gates(self) -> bool:
        """Section 10 requires independent challenge at defined gates.

        A reviewer approves; an administrator may act as one operationally. A
        modeller cannot approve their own model gate, which the gate service
        enforces separately by checking the requesting actor.
        """
        return self.is_platform_admin or self.platform_role == PlatformRole.REVIEWER
