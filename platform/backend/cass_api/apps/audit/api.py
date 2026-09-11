"""Audit search and governance gates."""

from __future__ import annotations

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.permissions import MayApproveGates
from apps.projects.models import Project

from . import services
from .models import Approval, AuditEvent, SelfApprovalRefused


class AuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditEvent
        fields = [
            "id", "actor_label", "action", "subject_type", "subject_id",
            "subject_label", "project", "before_reference", "after_reference",
            "correlation_id", "source_ip", "detail", "created_at",
        ]
        read_only_fields = fields


class ApprovalSerializer(serializers.ModelSerializer):
    requested_by_label = serializers.CharField(source="requested_by", read_only=True)
    decided_by_label = serializers.CharField(source="decided_by", read_only=True)
    is_open = serializers.BooleanField(read_only=True)
    is_cleared = serializers.BooleanField(read_only=True)

    class Meta:
        model = Approval
        fields = [
            "id", "gate", "decision", "subject_type", "subject_id",
            "requested_by", "requested_by_label", "decided_by", "decided_by_label",
            "decided_at", "rationale", "evidence", "is_open", "is_cleared",
            "created_at",
        ]
        read_only_fields = [
            "id", "decision", "decided_by", "decided_by_label", "decided_at",
            "is_open", "is_cleared", "created_at",
        ]


class AuditEventViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only by construction: the table is append-only."""

    queryset = AuditEvent.objects.none()
    serializer_class = AuditEventSerializer
    filterset_fields = ["action", "subject_type", "subject_id", "project", "correlation_id"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = AuditEvent.objects.select_related("project")
        if not getattr(user, "is_authenticated", False):
            return queryset.none()
        if user.is_platform_admin:
            return queryset
        # A user sees audit history for their own projects, plus events that
        # carry no project, which are model and platform scope.
        visible = Project.objects.filter(memberships__user=user)
        return queryset.filter(project__in=visible) | queryset.filter(project__isnull=True)


class ApprovalViewSet(viewsets.ModelViewSet):
    queryset = Approval.objects.select_related("requested_by", "decided_by")
    serializer_class = ApprovalSerializer
    permission_classes = [MayApproveGates]
    filterset_fields = ["gate", "decision", "subject_type", "subject_id"]

    def perform_create(self, serializer):
        serializer.save(requested_by=self.request.user)

    @action(detail=True, methods=["post"])
    def decide(self, request, pk=None, version=None):
        """Grant or refuse a gate.

        Independence is enforced: a person may not decide a gate they
        requested, and the role must permit the decision.
        """
        approval = self.get_object()
        decision = request.data.get("decision")
        if decision not in {Approval.Decision.APPROVED, Approval.Decision.REJECTED}:
            return Response(
                {
                    "detail": "Specify whether the gate is approved or rejected.",
                    "accepted": [Approval.Decision.APPROVED, Approval.Decision.REJECTED],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            services.decide_gate(
                approval,
                decision=decision,
                decided_by=request.user,
                rationale=request.data.get("rationale", ""),
                evidence=request.data.get("evidence") or {},
            )
        except SelfApprovalRefused as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        return Response(self.get_serializer(approval).data)
