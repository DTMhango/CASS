"""Authorization shared by the API.

Section 10 requires per-project authorization on both metadata and artifact
retrieval, and least-privilege roles. These classes are the single place that
is decided, so a new viewset cannot accidentally be built without it.
"""

from __future__ import annotations

from rest_framework import permissions

SAFE = permissions.SAFE_METHODS


class IsProjectMember(permissions.BasePermission):
    """Read requires membership; write requires contributor or owner.

    Objects expose their project either directly as ``project`` or through a
    ``project_for_permissions`` property, so nested records are covered by the
    same rule as the project itself.
    """

    message = "You do not have access to this project."

    def has_object_permission(self, request, view, obj) -> bool:
        project = _project_of(obj)
        if project is None:
            # A record with no project is model or platform scope; those are
            # governed by the role permissions below rather than membership.
            return request.method in SAFE
        if request.method in SAFE:
            return project.may_read(request.user)
        return project.may_write(request.user)


class IsProjectOwner(permissions.BasePermission):
    message = "Only a project owner may change membership or delete a project."

    def has_object_permission(self, request, view, obj) -> bool:
        project = _project_of(obj)
        return project is not None and project.may_administer(request.user)


class MayPublishModels(permissions.BasePermission):
    """Publishing a model version is a modeller or administrator action."""

    message = "Publishing a model version requires the catastrophe modeller role."

    def has_permission(self, request, view) -> bool:
        if request.method in SAFE:
            return True
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.may_publish_models
        )


class MayApproveGates(permissions.BasePermission):
    """Deciding a governance gate requires the reviewer role."""

    message = "Deciding this gate requires the reviewer or administrator role."

    def has_permission(self, request, view) -> bool:
        if request.method in SAFE:
            return True
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.may_approve_gates
        )


class MayRequestGates(permissions.BasePermission):
    """Requesting a gate is a modeller's act; deciding it is a reviewer's.

    Before this the whole approvals endpoint required the reviewer role, which
    meant the person whose work a gate governs could not ask for it -- and the
    independence rule, that nobody decides a gate they requested, had nobody to
    apply to.
    """

    message = "Requesting a gate requires the modeller, reviewer or administrator role."

    def has_permission(self, request, view) -> bool:
        if request.method in SAFE:
            return True
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if getattr(view, "action", None) == "decide":
            return bool(user.may_approve_gates)
        return bool(user.may_publish_models or user.may_approve_gates)


class IsPlatformAdmin(permissions.BasePermission):
    message = "This action is restricted to platform administrators."

    def has_permission(self, request, view) -> bool:
        return bool(
            request.user and request.user.is_authenticated and request.user.is_platform_admin
        )


def _project_of(obj):
    if hasattr(obj, "project_for_permissions"):
        return obj.project_for_permissions
    project = getattr(obj, "project", None)
    if project is not None:
        return project
    # Records one level down, such as an enrichment run under an exposure
    # version, resolve through their parent.
    parent = getattr(obj, "exposure_version", None) or getattr(obj, "run", None)
    if parent is not None:
        return getattr(parent, "project", None)
    return None
