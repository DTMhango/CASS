"""Exposure workspace API.

This is the M1 journey of section 13: create or import a portfolio, preview
valid OED, see validation findings against the business record, and publish an
immutable version.
"""

from __future__ import annotations

from django.conf import settings
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from apps.artifacts.models import ArtifactLink
from apps.common.permissions import IsProjectMember
from apps.common.queries import visible_projects
from apps.projects.models import Project
from cass_oed.schema import FileKind

from . import services
from .models import AttributeOverride, EnrichmentRun, ExposureVersion


class ExposureVersionSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.none())
    is_frozen = serializers.BooleanField(read_only=True)
    is_publishable = serializers.BooleanField(read_only=True)
    is_usable_by_runs = serializers.BooleanField(read_only=True)
    attached_files = serializers.SerializerMethodField()

    class Meta:
        model = ExposureVersion
        fields = [
            "id",
            "project",
            "name",
            "version",
            "state",
            "cedant",
            "valuation_date",
            "source_description",
            "run_currency",
            "oed_schema_version",
            "location_count",
            "account_count",
            "total_tiv",
            "tiv_by_coverage",
            "tiv_by_country",
            "tiv_by_currency",
            "unmodelled_subperils",
            "supported_perspectives",
            "validation_report",
            "attached_files",
            "is_frozen",
            "is_publishable",
            "is_usable_by_runs",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "version",
            "state",
            "oed_schema_version",
            "location_count",
            "account_count",
            "total_tiv",
            "tiv_by_coverage",
            "tiv_by_country",
            "tiv_by_currency",
            "unmodelled_subperils",
            "supported_perspectives",
            "validation_report",
            "created_at",
            "updated_at",
        ]
        # The (project, name, version) constraint is enforced by the database.
        # DRF would otherwise validate it against the default version of 1,
        # which rejects the second version of a portfolio before the server
        # has had a chance to number it.
        validators = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None:
            self.fields["project"].queryset = visible_projects(request.user)

    def get_attached_files(self, obj) -> list[dict]:
        links = ArtifactLink.objects.filter(
            subject_type="exposure_version", subject_id=obj.id, direction="input"
        ).select_related("artifact")
        return [
            {
                "role": link.role,
                "uri": link.artifact.uri,
                "checksum": link.artifact.checksum,
                "size_bytes": link.artifact.size_bytes,
                "original_filename": link.artifact.original_filename,
            }
            for link in links
        ]


class EnrichmentRunSerializer(serializers.ModelSerializer):
    assumption_share = serializers.FloatField(read_only=True)
    is_usable = serializers.BooleanField(read_only=True)

    class Meta:
        model = EnrichmentRun
        fields = [
            "id",
            "exposure_version",
            "assumption_set",
            "reported_count",
            "derived_count",
            "corroborated_count",
            "imputed_count",
            "override_count",
            "mean_confidence",
            "assumption_share",
            "missingness_profile",
            "attribute_lineage",
            "reconciliation",
            "reconciled",
            "exceptions",
            "output_checksum",
            "is_usable",
            "created_at",
        ]
        read_only_fields = fields


class AttributeOverrideSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttributeOverride
        fields = [
            "id",
            "enrichment_run",
            "location_reference",
            "attribute",
            "previous_value",
            "previous_evidence",
            "new_value",
            "rationale",
            "created_at",
        ]
        read_only_fields = ["id", "previous_value", "previous_evidence", "created_at"]


class ExposureVersionViewSet(viewsets.ModelViewSet):
    queryset = ExposureVersion.objects.none()
    serializer_class = ExposureVersionSerializer
    permission_classes = [IsProjectMember]
    parser_classes = [MultiPartParser, FormParser, *viewsets.ModelViewSet.parser_classes]
    filterset_fields = ["project", "state", "cedant"]
    ordering_fields = ["created_at", "total_tiv"]

    def get_queryset(self):
        return (
            ExposureVersion.objects.filter(
                project__in=visible_projects(self.request.user)
            )
            .select_related("project")
        )

    def perform_create(self, serializer):
        project = serializer.validated_data["project"]
        if not project.may_write(self.request.user):
            raise serializers.ValidationError(
                {"project": "You may not create exposure versions in this project."}
            )
        serializer.save(
            version=services.next_version_number(project, serializer.validated_data["name"]),
            created_by=self.request.user,
            updated_by=self.request.user,
        )

    @action(detail=True, methods=["post"], url_path="files")
    def upload_file(self, request, pk=None, version=None):
        """Attach one OED source file to a draft version."""
        exposure = self.get_object()
        kind = request.data.get("kind")
        upload = request.FILES.get("file")

        if kind not in {item.value for item in FileKind}:
            return Response(
                {
                    "detail": "Specify which OED file this is.",
                    "accepted": [item.value for item in FileKind],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if upload is None:
            return Response({"detail": "No file was supplied."}, status=400)
        if upload.size > settings.CASS_MAX_UPLOAD_BYTES:
            return Response(
                {
                    "detail": (
                        f"The file is {upload.size} bytes, above the "
                        f"{settings.CASS_MAX_UPLOAD_BYTES} byte limit for API uploads."
                    )
                },
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        try:
            artifact = services.attach_file(
                exposure,
                FileKind(kind),
                upload.read(),
                filename=upload.name,
                actor=request.user,
                request=request,
            )
        except services.ExposureError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response(
            {
                "role": artifact.role,
                "uri": artifact.uri,
                "checksum": artifact.checksum,
                "size_bytes": artifact.size_bytes,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def validate(self, request, pk=None, version=None):
        """Run validation and return the findings in business language."""
        exposure = self.get_object()
        try:
            services.run_validation(exposure, actor=request.user, request=request)
        except services.ExposureError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(self.get_serializer(exposure).data)

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None, version=None):
        """Freeze the version so analyses may use it."""
        exposure = self.get_object()
        try:
            services.publish(exposure, actor=request.user, request=request)
        except services.ExposureError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(self.get_serializer(exposure).data)

    @action(detail=True, methods=["get"], url_path="preview")
    def preview(self, request, pk=None, version=None):
        """Show the OED interpretation before a run.

        Section 8 requires the platform to display what it will generate and
        let the analyst download the exact files.
        """
        exposure = self.get_object()
        try:
            files = services.load_files(exposure)
        except services.ExposureError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        limit = min(int(request.query_params.get("limit", 25)), 200)
        return Response(
            {
                "oed_schema_version": exposure.oed_schema_version,
                "files": {
                    str(kind): {
                        "row_count": len(result),
                        "columns": list(result.columns),
                        "unrecognised_columns": list(result.unrecognised_columns),
                        "rows": [row.raw for row in result.rows[:limit]],
                    }
                    for kind, result in files.present().items()
                },
            }
        )

    @action(detail=True, methods=["get"], url_path="findings")
    def findings(self, request, pk=None, version=None):
        """Validation findings, optionally filtered by severity or code."""
        exposure = self.get_object()
        report = (exposure.validation_report or {}).get("validation", {})
        findings = report.get("findings", [])

        severity = request.query_params.get("severity")
        code = request.query_params.get("code")
        if severity:
            findings = [item for item in findings if item.get("severity") == severity]
        if code:
            findings = [item for item in findings if item.get("code") == code]

        return Response(
            {
                "blocking": report.get("blocking", False),
                "error_count": report.get("error_count", 0),
                "warning_count": report.get("warning_count", 0),
                "counts_by_code": report.get("counts_by_code", {}),
                "findings": findings,
            }
        )


class EnrichmentRunViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = EnrichmentRun.objects.none()
    serializer_class = EnrichmentRunSerializer
    permission_classes = [IsProjectMember]
    filterset_fields = ["exposure_version", "assumption_set", "reconciled"]

    def get_queryset(self):
        return EnrichmentRun.objects.filter(
            exposure_version__project__in=visible_projects(self.request.user)
        ).select_related("exposure_version", "assumption_set")
