"""Project and membership API."""

from __future__ import annotations

from django.db.models import Count, Q
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.permissions import IsProjectMember
from apps.common.queries import visible_projects

from .models import Project, ProjectMembership, ProjectRole, ProjectStatus


class ProjectMembershipSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.get_full_name", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = ProjectMembership
        fields = ["id", "user", "username", "user_name", "role", "created_at"]
        read_only_fields = ["id", "created_at"]


class ProjectSerializer(serializers.ModelSerializer):
    my_role = serializers.SerializerMethodField()
    exposure_version_count = serializers.IntegerField(read_only=True)
    active_run_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Project
        fields = [
            "id",
            "name",
            "reference",
            "purpose",
            "team",
            "status",
            "my_role",
            "exposure_version_count",
            "active_run_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_my_role(self, obj) -> str | None:
        request = self.context.get("request")
        return obj.role_for(request.user) if request else None


class ProjectViewSet(viewsets.ModelViewSet):
    """Projects the signed-in user may see.

    The queryset is filtered by membership rather than filtered in the view, so
    an object the user cannot reach is a 404 rather than a 403 that confirms it
    exists.
    """

    queryset = Project.objects.none()
    serializer_class = ProjectSerializer
    permission_classes = [IsProjectMember]
    filterset_fields = ["status", "team"]
    ordering_fields = ["created_at", "name"]

    def get_queryset(self):
        return (
            visible_projects(self.request.user)
            .annotate(
                exposure_version_count=Count("exposure_versions", distinct=True),
                active_run_count=Count(
                    "runs",
                    filter=Q(runs__state__in=["queued", "running", "cancelling"]),
                    distinct=True,
                ),
            )
        )

    def perform_create(self, serializer):
        project = serializer.save(
            created_by=self.request.user, updated_by=self.request.user
        )
        # The creator owns the workspace; without this the project would be
        # invisible to the person who just made it.
        ProjectMembership.objects.create(
            project=project,
            user=self.request.user,
            role=ProjectRole.OWNER,
            created_by=self.request.user,
        )
        audit.record(
            action=AuditAction.CREATE,
            subject_type="project",
            subject_id=project.id,
            actor=self.request.user,
            project=project,
            subject_label=str(project),
            after={"reference": project.reference, "status": project.status},
            request=self.request,
        )

    def perform_update(self, serializer):
        before = {"status": serializer.instance.status, "name": serializer.instance.name}
        project = serializer.save(updated_by=self.request.user)
        audit.record(
            action=AuditAction.UPDATE,
            subject_type="project",
            subject_id=project.id,
            actor=self.request.user,
            project=project,
            subject_label=str(project),
            before=before,
            after={"status": project.status, "name": project.name},
            request=self.request,
        )

    def perform_destroy(self, instance):
        """Projects are archived rather than deleted.

        Section 5 keeps audit history and result lineage; hard-deleting a
        project would orphan both.
        """
        instance.status = ProjectStatus.ARCHIVED
        instance.updated_by = self.request.user
        instance.save(update_fields=["status", "updated_by", "updated_at"])
        audit.record(
            action=AuditAction.DELETE,
            subject_type="project",
            subject_id=instance.id,
            actor=self.request.user,
            project=instance,
            subject_label=str(instance),
            after={"status": instance.status},
            detail="Archived rather than deleted so lineage is preserved.",
            request=self.request,
        )

    @action(detail=True, methods=["get", "post"], url_path="members")
    def members(self, request, pk=None, version=None):
        project = self.get_object()
        if request.method == "GET":
            serializer = ProjectMembershipSerializer(
                project.memberships.select_related("user"), many=True
            )
            return Response(serializer.data)

        if not project.may_administer(request.user):
            return Response(
                {"detail": "Only a project owner may change membership."}, status=403
            )
        serializer = ProjectMembershipSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = serializer.save(project=project, created_by=request.user)
        audit.record(
            action=AuditAction.CONFIGURE,
            subject_type="project_membership",
            subject_id=membership.id,
            actor=request.user,
            project=project,
            subject_label=str(membership),
            after={"user": str(membership.user), "role": membership.role},
            request=request,
        )
        return Response(ProjectMembershipSerializer(membership).data, status=201)
