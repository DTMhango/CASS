"""Artifact retrieval and upload sessions.

Section 10: a guessed object key must never grant access. Every read here
resolves the artifact record first and calls ``may_read`` before any byte is
streamed, and each download is written to the audit trail.
"""

from __future__ import annotations

from django.http import FileResponse, Http404
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.storage import bucket, get_store
from kre_core.artifacts import ArtifactNotFound

from .models import Artifact, ArtifactState


class ArtifactSerializer(serializers.ModelSerializer):
    readable = serializers.SerializerMethodField()

    class Meta:
        model = Artifact
        fields = [
            "id", "uri", "checksum", "size_bytes", "content_type", "retention",
            "access", "state", "project", "role", "original_filename",
            "expires_at", "readable", "created_at",
        ]
        read_only_fields = fields

    def get_readable(self, obj) -> bool:
        request = self.context.get("request")
        return obj.may_read(request.user) if request else False


class ArtifactViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Artifact.objects.none()
    serializer_class = ArtifactSerializer
    filterset_fields = ["project", "role", "state", "retention"]
    ordering_fields = ["created_at", "size_bytes"]

    def get_queryset(self):
        """Only artifacts the caller is entitled to.

        Filtering in the queryset rather than only in ``may_read`` means a
        listing cannot leak the existence of another project's objects.
        """
        user = self.request.user
        queryset = Artifact.objects.select_related("project")
        if not getattr(user, "is_authenticated", False):
            return queryset.none()
        if user.is_platform_admin:
            return queryset
        return queryset.filter(
            project__memberships__user=user
        ).distinct() | queryset.filter(access="model").distinct()

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None, version=None):
        """Stream an artifact after checking entitlement, and record the access."""
        artifact = self.get_object()
        if not artifact.may_read(request.user):
            raise Http404
        if artifact.state != ArtifactState.REGISTERED:
            return Response(
                {
                    "detail": f"The artifact is {artifact.get_state_display().lower()} "
                    "and cannot be downloaded."
                },
                status=status.HTTP_409_CONFLICT,
            )

        try:
            handle = get_store().open(artifact.uri)
        except ArtifactNotFound:
            return Response(
                {"detail": "The stored object is missing. It may have expired under its retention class."},
                status=status.HTTP_410_GONE,
            )

        audit.record(
            action=AuditAction.DOWNLOAD,
            subject_type="artifact",
            subject_id=artifact.id,
            actor=request.user,
            project=artifact.project,
            subject_label=artifact.uri,
            after={"checksum": artifact.checksum},
            request=request,
        )
        response = FileResponse(handle, content_type=artifact.content_type)
        filename = artifact.original_filename or artifact.uri.rsplit("/", 1)[-1]
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @action(detail=False, methods=["post"], url_path="upload-session")
    def upload_session(self, request, version=None):
        """Issue a short-lived direct-to-store upload grant.

        Section 5 has the browser upload large files straight to object
        storage; Django registers the artifact only once the bytes are present
        and checksummed.
        """
        purpose = request.data.get("purpose", "upload")
        key = request.data.get("key")
        content_type = request.data.get("content_type", "application/octet-stream")
        if not key:
            return Response({"detail": "A key is required."}, status=400)

        try:
            session = get_store().create_upload_session(
                bucket(purpose), key, content_type=content_type
            )
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        Artifact.objects.get_or_create(
            uri=session.uri,
            defaults={
                "content_type": content_type,
                "retention": "portfolio",
                "state": ArtifactState.PENDING,
                "created_by": request.user,
            },
        )
        return Response(
            {
                "uri": session.uri,
                "url": session.url,
                "method": session.method,
                "headers": session.headers,
                "expires_at": session.expires_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )
