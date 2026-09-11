"""Queryset helpers that answer "what may this user see".

Every list endpoint narrows by project membership rather than relying on an
object-level check alone, so a listing cannot leak the existence of another
project's records. Anonymous callers see nothing -- including during schema
introspection, which walks the viewsets without a signed-in user.
"""

from __future__ import annotations

from apps.projects.models import Project


def visible_projects(user):
    """Projects the user may read."""
    if user is None or not getattr(user, "is_authenticated", False):
        return Project.objects.none()
    if getattr(user, "is_platform_admin", False):
        return Project.objects.all()
    return Project.objects.filter(memberships__user=user).distinct()


def writable_projects(user):
    """Projects the user may add records to."""
    if user is None or not getattr(user, "is_authenticated", False):
        return Project.objects.none()
    if getattr(user, "is_platform_admin", False):
        return Project.objects.all()
    return Project.objects.filter(
        memberships__user=user, memberships__role__in=["contributor", "owner"]
    ).distinct()
