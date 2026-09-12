"""Exposure workspace API.

This is the M1 journey of section 13: create or import a portfolio, preview
valid OED, see validation findings against the business record, and publish an
immutable version.
"""

from __future__ import annotations

import json

from django.conf import settings
from django.http import HttpResponse
from django.utils.dateparse import parse_date
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

import cass_extract
from apps.artifacts.models import ArtifactLink
from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.permissions import IsProjectMember
from apps.common.queries import visible_projects
from apps.projects.models import Project
from cass_oed.schema import FileKind

from . import extract as extract_service
from . import promotion, services
from .models import (
    AttributeOverride,
    EnrichmentRun,
    ExposureVersion,
    ImportBatch,
    SourceRiskLocation,
)


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


# -- the Klapton Re geocoded policy extract -----------------------------------

class ImportBatchSerializer(serializers.ModelSerializer):
    blocking = serializers.BooleanField(read_only=True)
    may_accept = serializers.BooleanField(read_only=True)

    class Meta:
        model = ImportBatch
        fields = [
            "id", "project", "profile", "state", "source_filename",
            "source_checksum", "snapshot_date", "schema_version", "parser_version",
            "cohort_rule_version", "join_rule_version", "policy_row_count",
            "location_row_count", "findings", "join_report", "cohort_profile",
            "blocking", "may_accept", "rejection_reason", "accepted_at",
            "created_at",
        ]
        read_only_fields = fields


class SourceRiskLocationSerializer(serializers.ModelSerializer):
    """A staged location, without the address unless the caller may see it.

    The address is the confidential column on this record, and section 10 keeps
    it to roles that need it. Serialising it and letting a screen decide would
    put it in every response body and every proxy log on the way.
    """

    address = serializers.SerializerMethodField()

    class Meta:
        model = SourceRiskLocation
        fields = [
            "id", "row_number", "business_id", "location_number",
            "primary_location", "latitude", "longitude", "precision",
            "needs_review", "class_of_business", "country", "country_code",
            "cohort", "cohort_reason", "cohort_rule_version", "review_state",
            "review_note", "reviewed_at", "address",
        ]
        read_only_fields = fields

    def get_address(self, obj) -> str | None:
        user = self.context["request"].user
        if not getattr(user, "may_see_counterparty_names", False):
            return None
        return obj.values.get("risk_location_address") or ""


class PortfolioImportViewSet(viewsets.ReadOnlyModelViewSet):
    """Importing and profiling a source portfolio extract.

    Read-only as a viewset: a batch is created by uploading a workbook, not by
    posting a record, and nothing about a completed read may be edited
    afterwards. What a person can change is the decision -- accept the batch,
    or review a flagged location -- and those are their own actions.
    """

    queryset = ImportBatch.objects.none()
    serializer_class = ImportBatchSerializer
    permission_classes = [IsProjectMember]
    parser_classes = [MultiPartParser, FormParser, *viewsets.ReadOnlyModelViewSet.parser_classes]
    filterset_fields = ["project", "state"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        return ImportBatch.objects.filter(
            project__in=visible_projects(self.request.user)
        ).select_related("project", "source_artifact")

    @action(detail=False, methods=["post"], url_path="upload")
    def upload(self, request, version=None):
        """Register, parse and profile one extract workbook."""
        project_id = request.data.get("project")
        upload = request.FILES.get("file")

        if not upload:
            return Response(
                {"detail": "Attach the extract workbook as 'file'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        project = Project.objects.filter(
            id=project_id, id__in=[p.id for p in visible_projects(request.user)]
        ).first()
        if project is None:
            return Response(
                {"detail": "Name a project you are a member of."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not project.may_write(request.user):
            return Response(
                {"detail": "You may not import portfolio data into this project."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if upload.size > settings.CASS_MAX_UPLOAD_BYTES:
            return Response(
                {"detail": "The file is larger than this installation accepts."},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        snapshot_date = parse_date(str(request.data.get("snapshot_date") or "")) or None
        batch = extract_service.import_extract(
            project,
            upload.read(),
            filename=upload.name,
            snapshot_date=snapshot_date,
            actor=request.user,
            request=request,
        )
        return Response(
            self.get_serializer(batch).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"])
    def locations(self, request, pk=None, version=None):
        """The staged locations, filterable by cohort and review state.

        This is the review queue of work package 1: ``?review_state=pending``
        is the backlog a person owes, and ``?cohort=A`` is what the automated
        benchmark may use.
        """
        batch = self.get_object()
        rows = batch.location_rows.all()
        cohort = request.query_params.get("cohort")
        review_state = request.query_params.get("review_state")
        if cohort:
            rows = rows.filter(cohort=cohort)
        if review_state:
            rows = rows.filter(review_state=review_state)

        page = self.paginate_queryset(rows)
        serializer = SourceRiskLocationSerializer(
            page if page is not None else rows, many=True, context={"request": request}
        )
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def manifest(self, request, pk=None, version=None):
        """The transformation manifest, without insured names by default."""
        batch = self.get_object()
        include = str(request.query_params.get("include_confidential", "")).lower() in (
            "1", "true", "yes"
        )
        if include and not getattr(request.user, "may_see_counterparty_names", False):
            return Response(
                {
                    "detail": "You may not download a manifest containing counterparty names.",
                    "hint": "Request it without include_confidential.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        audit.record(
            action=AuditAction.DOWNLOAD,
            subject_type="import_batch",
            subject_id=batch.id,
            actor=request.user,
            project=batch.project,
            subject_label=str(batch),
            after={"include_confidential": include},
            request=request,
        )
        return Response(
            extract_service.transformation_manifest(
                batch, include_confidential=include
            )
        )



    @action(detail=True, methods=["get"], url_path="coverage-template")
    def coverage_template(self, request, pk=None, version=None):
        """A CSV of the selected locations, ready for real coverage values.

        The other half of the coverage story. A percentage split is fine when
        nobody knows the breakdown, but OED lets a schedule carry whatever
        numbers each row actually has, and this is how someone supplies them:
        download, fill in, post back with the promotion.

        The allocated total travels with each row so a person can see what they
        are overriding, and it is not one of the coverage columns.
        """
        batch = self.get_object()
        try:
            rows = promotion.template_rows(
                batch,
                cohort=cass_extract.Cohort(str(request.query_params.get("cohort") or "A")),
                class_of_business=_requested_class(request.query_params),
                allocation_method=cass_extract.AllocationMethod(
                    str(
                        request.query_params.get("allocation_method")
                        or cass_extract.AllocationMethod.EQUAL_LOCATION
                    )
                ),
            )
        except (promotion.PromotionError, cass_extract.AllocationError, ValueError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        payload = cass_extract.component_template(rows)
        response = HttpResponse(payload, content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="coverage-template-{batch.id}.csv"'
        )
        return response

    @action(detail=True, methods=["post"])
    def promote(self, request, pk=None, version=None):
        """Turn a cohort selection into a published OED exposure version.

        The assumptions are the caller's to choose: which cohort, how a
        multi-location policy divides, and where the coverage values come from.
        Every one of them is recorded on the version that results, so a later
        reader can see what produced it and run it again differently.

        Coverage values may be supplied outright. Post a completed coverage
        template as ``coverage_file`` and those numbers are used as given, in
        whatever proportions each row carries -- which is how OED works, and
        what the brief's evidence hierarchy ranks above any split. Without one,
        a named or custom percentage split applies.
        """
        batch = self.get_object()
        if not batch.project.may_write(request.user):
            return Response(
                {"detail": "You may not promote imports in this project."},
                status=status.HTTP_403_FORBIDDEN,
            )

        name = str(request.data.get("name") or "").strip()
        if not name:
            return Response(
                {"detail": "Name the exposure version this selection produces."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            cohort = cass_extract.Cohort(str(request.data.get("cohort") or "A"))
            method = cass_extract.AllocationMethod(
                str(
                    request.data.get("allocation_method")
                    or cass_extract.AllocationMethod.EQUAL_LOCATION
                )
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        class_of_business = _requested_class(request.data)

        upload = request.FILES.get("coverage_file")
        accept_restated = str(
            request.data.get("accept_restated_total", "")
        ).lower() in ("1", "true", "yes")

        try:
            reported = (
                cass_extract.read_reported_components(
                    upload.read(), name=upload.name
                )
                if upload
                else None
            )
            split = _requested_split(request.data)
            exposure = promotion.promote(
                batch,
                name=name,
                cohort=cohort,
                class_of_business=class_of_business,
                allocation_method=method,
                component_split=split,
                reported_components=reported,
                accept_restated_total=accept_restated,
                actor=request.user,
                request=request,
            )
        except (promotion.PromotionError, cass_extract.AllocationError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response(
            promotion.promotion_summary(exposure), status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None, version=None):
        """Record that the join report and cohorts have been reviewed."""
        batch = self.get_object()
        if not batch.project.may_write(request.user):
            return Response(
                {"detail": "You may not accept imports in this project."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            extract_service.accept(batch, actor=request.user, request=request)
        except extract_service.ExtractImportError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(self.get_serializer(batch).data)


def _requested_split(data) -> cass_extract.ComponentSplit:
    """The coverage split the caller asked for, preset or their own.

    Custom percentages are accepted because the point of the assumption is that
    someone can change it. They are still validated to sum to 100 rather than
    normalised: normalising would silently apply something other than what was
    asked for.
    """
    percentages = data.get("coverage_percentages")
    if percentages:
        if isinstance(percentages, str):
            percentages = json.loads(percentages)
        return cass_extract.custom(
            str(data.get("coverage_split") or "custom_v1"),
            percentages,
            description=str(data.get("coverage_split_description") or ""),
        )
    requested = str(data.get("coverage_split") or "").strip()
    if not requested:
        return cass_extract.DEFAULT_SPLIT
    return cass_extract.preset(requested)



class AssumptionCatalogueView(APIView):
    """The assumptions a promotion may be run under.

    Returned as data rather than hard-coded in the browser, so the interface
    offers exactly what the platform supports and a new scenario appears
    without a frontend release. Every entry says whether an approved prior
    stands behind it, because the difference between a test assumption and an
    approved one is the difference between a research result and a usable one.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses=inline_serializer(
            name="AssumptionCatalogue",
            fields={
                "cohorts": serializers.ListField(),
                "allocation_methods": serializers.ListField(),
                "coverage_splits": serializers.ListField(),
                "coverages": serializers.ListField(),
            },
        )
    )
    def get(self, request, *args, **kwargs):
        return Response(
            {
                "cohorts": [
                    {"value": str(item), "label": item.label}
                    for item in cass_extract.Cohort
                    if item is not cass_extract.Cohort.UNCLASSIFIED
                ],
                "allocation_methods": [
                    {
                        "value": str(cass_extract.AllocationMethod.EQUAL_LOCATION),
                        "label": "Equal across locations",
                        "description": (
                            "The maximum-ignorance baseline. Introduces no ranking "
                            "among sites that the source does not support."
                        ),
                        "baseline": True,
                    },
                    {
                        "value": str(cass_extract.AllocationMethod.PRIMARY_CONCENTRATED),
                        "label": "Primary concentrated (70/30)",
                        "description": (
                            "Sensitivity: 70% at the reported primary site, 30% shared "
                            "by the rest. Tests whether the primary flag is "
                            "economically material without asserting that it is."
                        ),
                        "baseline": False,
                    },
                ],
                "coverage_splits": [
                    split.as_dict()
                    for split in sorted(
                        cass_extract.PRESETS.values(), key=lambda item: item.name
                    )
                ],
                "coverages": [
                    {"value": str(item), "label": item.label}
                    for item in cass_extract.COVERAGE_ORDER
                ],
                "default_coverage_split": cass_extract.DEFAULT_SPLIT.name,
                "custom_split_allowed": True,
            }
        )


def _requested_class(data) -> str | None:
    """The class filter, where "any" means do not filter at all."""
    requested = data.get("class_of_business", "Fire")
    if requested in ("", "any", None):
        return None
    return str(requested)
