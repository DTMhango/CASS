"""Artifact retrieval and upload sessions.

Section 10: a guessed object key must never grant access. Every read here
resolves the artifact record first and calls ``may_read`` before any byte is
streamed, and each download is written to the audit trail.

Uploads follow the same rule in the other direction. A caller never names a
key: a session is issued into the owning project's prefix to somebody who may
write there, and what arrives is only registered once it has been read back,
checksummed and scanned (``intake``).
"""

from __future__ import annotations

from django.conf import settings
from django.http import FileResponse, Http404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.queries import visible_projects
from apps.common.storage import get_store
from cass_core.artifacts import AccessPolicy, ArtifactNotFound, RetentionClass, parse_uri

from . import intake
from .models import Artifact, ArtifactState


class ArtifactSerializer(serializers.ModelSerializer):
    readable = serializers.SerializerMethodField()

    class Meta:
        model = Artifact
        fields = [
            "id", "uri", "checksum", "size_bytes", "content_type", "retention",
            "access", "state", "project", "role", "original_filename",
            "expires_at", "validation_note", "readable", "created_at",
        ]
        read_only_fields = fields

    def get_readable(self, obj) -> bool:
        """Whether this caller can actually read it now.

        Entitlement alone is not enough. A quarantined, pending or expired
        object is unreadable whoever asks, and a listing that called it
        readable would promise a download the endpoint then refuses.
        """
        request = self.context.get("request")
        if request is None:
            return False
        return obj.is_readable and obj.may_read(request.user)


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
        """Issue a short-lived direct-to-store upload into a project.

        Section 5 has the browser upload large files straight to object
        storage. The caller names the project and the file; this service
        chooses the key, under that project's prefix, so a session can never be
        pointed at somebody else's object.
        """
        project_id = request.data.get("project")
        filename = str(request.data.get("filename") or "").strip()
        content_type = str(request.data.get("content_type") or "application/octet-stream")
        if not project_id or not filename:
            return Response(
                {"detail": "Name the project the upload belongs to and the file being sent."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        project = visible_projects(request.user).filter(id=project_id).first()
        if project is None:
            raise Http404

        try:
            artifact, session = intake.issue_session(
                project, filename=filename, content_type=content_type, actor=request.user
            )
        except intake.IntakeError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        return Response(
            {
                "artifact": str(artifact.id),
                "uri": session.uri,
                "url": session.url,
                "method": session.method,
                "headers": session.headers,
                "expires_at": session.expires_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None, version=None):
        """Read back what arrived, and register or quarantine it.

        ``checksum`` and ``size_bytes`` are what the uploader says they sent,
        and both are optional. Where given they are compared with what arrived,
        which is how a truncated or altered upload is caught.
        """
        artifact = self.get_object()
        if artifact.created_by_id != request.user.id and not request.user.is_platform_admin:
            return Response(
                {"detail": "Only whoever opened this upload may complete it."},
                status=status.HTTP_403_FORBIDDEN,
            )

        declared_size = request.data.get("size_bytes")
        try:
            completed = intake.complete(
                artifact,
                declared_checksum=str(request.data.get("checksum") or ""),
                declared_size=int(declared_size) if declared_size not in (None, "") else None,
                actor=request.user,
            )
        except intake.ScannerUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except intake.IntakeError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ValueError:
            return Response(
                {"detail": "size_bytes must be a whole number."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            self.get_serializer(completed).data,
            status=(
                status.HTTP_200_OK
                if completed.state == ArtifactState.REGISTERED
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
        )


class ArtifactUploadView(APIView):
    """Receive an upload's body where the store cannot sign one.

    A filesystem store has no signing authority, so its session URL points
    here. It accepts a body only for a pending upload the caller opened, before
    the session expires -- the same terms a presigned URL enforces on S3 -- and
    registers nothing: completion is still a separate act.
    """

    @extend_schema(
        request={"application/octet-stream": OpenApiTypes.BINARY},
        responses={
            204: OpenApiResponse(description="Received. Complete the upload to register it."),
            404: OpenApiResponse(description="No upload this caller opened is at this address."),
            409: OpenApiResponse(description="The upload is no longer accepting content."),
            410: OpenApiResponse(description="The upload session has expired."),
            413: OpenApiResponse(description="Larger than this installation accepts."),
        },
    )
    def put(self, request, bucket_name: str, key: str, version=None):
        from cass_core.artifacts import build_uri

        try:
            uri = build_uri(bucket_name, key)
            parse_uri(uri)
        except Exception:
            raise Http404 from None

        artifact = Artifact.objects.filter(uri=uri).first()
        if artifact is None or artifact.created_by_id != request.user.id:
            raise Http404
        if artifact.state != ArtifactState.PENDING:
            return Response(
                {"detail": "This upload is no longer accepting content."},
                status=status.HTTP_409_CONFLICT,
            )
        if artifact.expires_at is not None and artifact.expires_at < timezone.now():
            return Response(
                {"detail": "The upload session has expired. Start a new upload."},
                status=status.HTTP_410_GONE,
            )

        body = request.body
        if len(body) > settings.CASS_MAX_UPLOAD_BYTES:
            return Response(
                {"detail": "The file is larger than this installation accepts."},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        get_store().put_bytes(
            bucket_name,
            key,
            body,
            content_type=artifact.content_type,
            retention=RetentionClass(artifact.retention),
            access=AccessPolicy(artifact.access),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
