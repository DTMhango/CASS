"""Exposure workspace API.

This is the M1 journey of section 13: create or import a portfolio, preview
valid OED, see validation findings against the business record, and publish an
immutable version.
"""

from __future__ import annotations

import json

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    inline_serializer,
)
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
from apps.modelregistry.assets import ModelAssetError, load_grid
from apps.modelregistry.models import AreaPerilGrid, ModelVersion
from apps.projects.models import Project
from cass_extract import profile as intake_profile
from cass_extract import template as intake_template
from cass_keys.sensitivity import SensitivityError
from cass_oed import structure as oed_structure
from cass_oed.schema import FileKind

from . import editing, geocoding, promotion, review, scenarios, services, structure_builder
from . import extract as extract_service
from .models import (
    AttributeOverride,
    CurrencyRate,
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
    filterset_fields = ["project", "state"]
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

    # -- reading and correcting the rows themselves --------------------------

    @extend_schema(
        parameters=[
            OpenApiParameter("kind", str, description="location, account, reins_info or reins_scope"),
            OpenApiParameter("offset", int),
            OpenApiParameter("limit", int),
            OpenApiParameter("search", str),
        ],
        responses=OpenApiTypes.OBJECT,
    )
    @action(detail=True, methods=["get"], url_path="rows")
    def rows(self, request, pk=None, version=None):
        """The rows of one attached file, with what each column accepts.

        The columns come from the same schema the file was validated against,
        so the editor can refuse a value before it is stored rather than
        reporting it as a finding afterwards.
        """
        exposure = self.get_object()
        kind = request.query_params.get("kind") or str(FileKind.LOCATION)
        try:
            page = editing.rows_page(
                exposure,
                FileKind(kind),
                offset=max(0, int(request.query_params.get("offset", 0))),
                limit=min(int(request.query_params.get("limit", 50)), 500),
                search=request.query_params.get("search", ""),
            )
        except ValueError:
            return Response(
                {"detail": f"{kind} is not an OED file CASS reads."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except services.ExposureError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        page["attached"] = [str(item) for item in editing.attached_kinds(exposure)]
        return Response(page)

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"], url_path="rows/edit")
    def edit_row(self, request, pk=None, version=None):
        """Correct one row in place, or refuse it with the reason."""
        exposure = self.get_object()
        kind = request.data.get("kind") or str(FileKind.LOCATION)
        values = request.data.get("values") or {}
        try:
            row_number = int(request.data.get("row_number"))
        except (TypeError, ValueError):
            return Response(
                {"detail": "Which row is being corrected was not stated."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            updated = editing.edit_row(
                exposure, FileKind(kind), row_number, values, actor=request.user
            )
        except editing.RowError as exc:
            return Response(
                {"detail": "The correction was not stored.", "fields": exc.problems},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except services.ExposureError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        exposure.refresh_from_db()
        return Response(
            {"row": {"row_number": row_number, "values": updated},
             "version": self.get_serializer(exposure).data}
        )

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"], url_path="rows/add")
    def add_row(self, request, pk=None, version=None):
        """Add a row, checked the same way a correction is."""
        exposure = self.get_object()
        kind = request.data.get("kind") or str(FileKind.LOCATION)
        try:
            row = editing.add_row(
                exposure, FileKind(kind), request.data.get("values") or {}, actor=request.user
            )
        except editing.RowError as exc:
            return Response(
                {"detail": "The row was not added.", "fields": exc.problems},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except services.ExposureError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        exposure.refresh_from_db()
        return Response(
            {"row": row, "version": self.get_serializer(exposure).data},
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"], url_path="rows/remove")
    def remove_row(self, request, pk=None, version=None):
        exposure = self.get_object()
        kind = request.data.get("kind") or str(FileKind.LOCATION)
        try:
            editing.delete_row(
                exposure, FileKind(kind), int(request.data.get("row_number") or 0),
                actor=request.user,
            )
        except services.ExposureError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        exposure.refresh_from_db()
        return Response(self.get_serializer(exposure).data)

    @extend_schema(responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"], url_path="correct")
    def correct(self, request, pk=None, version=None):
        """Copy a published version into the next one, for correcting."""
        exposure = self.get_object()
        try:
            corrected = editing.correct(exposure, actor=request.user)
        except services.ExposureError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(
            self.get_serializer(corrected).data, status=status.HTTP_201_CREATED
        )

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

    @action(detail=True, methods=["get"], url_path="financial-structure")
    def financial_structure(self, request, pk=None, version=None):
        """Accounts, layers, contracts, inuring order and what they reach.

        Section 3 asks for a financial structure workspace with a scope preview
        and a reconciliation. This reads the structure out of the portfolio's
        own files and states what does not add up -- a layer with no term, a
        contract whose scope reaches nothing, value no contract covers -- rather
        than repairing any of it, because a structure that quietly fixed itself
        would produce a ceded loss nobody could tie back to a treaty.
        """
        exposure = self.get_object()
        try:
            files = services.load_files(exposure)
        except services.ExposureError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        structure = oed_structure.read(files)
        return Response(
            {
                "exposure_version": str(exposure.id),
                "currency": exposure.run_currency,
                **structure.as_dict(),
            }
        )

    def _built(self, operation, *, created: bool = False) -> Response:
        """Apply one change to a draft portfolio's structure, and return it read back."""
        try:
            document = operation()
        except structure_builder.BuildError as exc:
            return Response(
                {"detail": "The structure was not changed.", "fields": exc.problems},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except services.ExposureError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(document, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"], url_path="structure/policies")
    def add_policy(self, request, pk=None, version=None):
        """Write a policy and its layers into this draft portfolio's account file."""
        exposure = self.get_object()
        data = request.data
        return self._built(
            lambda: structure_builder.add_policy(
                exposure,
                account=data.get("account"),
                policy=data.get("policy"),
                perils=data.get("perils"),
                layers=data.get("layers") or [],
                deductible=data.get("deductible"),
                policy_limit=data.get("policy_limit"),
                inception=data.get("inception") or "",
                expiry=data.get("expiry") or "",
                actor=request.user,
            ),
            created=True,
        )

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"], url_path="structure/policies/remove")
    def remove_policy(self, request, pk=None, version=None):
        """Remove every layer of one policy from this draft portfolio."""
        exposure = self.get_object()
        return self._built(
            lambda: structure_builder.remove_policy(
                exposure,
                account=request.data.get("account"),
                policy=request.data.get("policy"),
                actor=request.user,
            )
        )

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"], url_path="structure/contracts")
    def add_contract(self, request, pk=None, version=None):
        """Write a reinsurance contract and the scope it reaches into this draft portfolio.

        Only the terms the engine uses for the contract type are accepted, and a
        type CASS does not apply in a run is refused rather than written.
        """
        exposure = self.get_object()
        data = request.data
        return self._built(
            lambda: structure_builder.add_contract(
                exposure,
                contract_type=data.get("type"),
                perils=data.get("perils"),
                name=data.get("name") or "",
                currency=data.get("currency") or "",
                inuring_priority=data.get("inuring_priority") or 1,
                ceded_percent=data.get("ceded_percent"),
                placed_percent=data.get("placed_percent"),
                occurrence_attachment=data.get("occurrence_attachment"),
                occurrence_limit=data.get("occurrence_limit"),
                risk_level=data.get("risk_level") or "",
                risk_attachment=data.get("risk_attachment"),
                risk_limit=data.get("risk_limit"),
                scope=data.get("scope") or [],
                whole_portfolio=bool(data.get("whole_portfolio")),
                actor=request.user,
            ),
            created=True,
        )

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["post"], url_path="structure/contracts/remove")
    def remove_contract(self, request, pk=None, version=None):
        """Remove one contract, and every scope row naming it, from this draft portfolio."""
        exposure = self.get_object()
        return self._built(
            lambda: structure_builder.remove_contract(
                exposure, number=request.data.get("number"), actor=request.user
            )
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


class CurrencyRateSerializer(serializers.ModelSerializer):
    is_approved = serializers.BooleanField(read_only=True)
    description = serializers.SerializerMethodField()

    class Meta:
        model = CurrencyRate
        fields = [
            "id", "from_currency", "to_currency", "rate", "valuation_date",
            "source", "reference", "notes", "is_approved", "description",
            "approved_at", "is_frozen", "created_at",
        ]
        read_only_fields = [
            "id", "is_approved", "description", "approved_at", "is_frozen", "created_at",
        ]

    def get_description(self, obj) -> str:
        return str(obj)

    def validate(self, attrs):
        source = (attrs.get("from_currency") or "").upper()
        target = (attrs.get("to_currency") or "").upper()
        if source and target and source == target:
            raise serializers.ValidationError(
                {"to_currency": "A rate from a currency to itself converts nothing."}
            )
        if attrs.get("rate") is not None and attrs["rate"] <= 0:
            raise serializers.ValidationError(
                {"rate": "A rate must be positive: a zero or negative rate is not a quote."}
            )
        attrs["from_currency"] = source
        attrs["to_currency"] = target
        return attrs


class CurrencyRateViewSet(viewsets.ModelViewSet):
    """Governed rates for normalising a portfolio to the run currency.

    Section 8 requires the rate, its valuation date, its source and its
    direction to be captured before generation; section 15 explains why, since
    the Oasis Financial Module cannot calculate multi-currency terms. A rate is
    evidence only once somebody other than its author has approved it, which is
    the rule section 10 applies to every other gate on the platform.
    """

    queryset = CurrencyRate.objects.none()
    serializer_class = CurrencyRateSerializer
    # Recording the quote is ordinary exposure work; approving it is the gate,
    # and the action checks the reviewer role and the independence rule itself.
    permission_classes = [IsAuthenticated]
    filterset_fields = ["from_currency", "to_currency", "valuation_date"]
    ordering_fields = ["valuation_date", "created_at"]

    def get_queryset(self):
        return CurrencyRate.objects.all()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None, version=None):
        """Stand behind a rate, so a run may rest its numbers on it."""
        rate = self.get_object()
        if not request.user.may_approve_gates:
            return Response(
                {"detail": "Approving a currency rate requires the reviewer role."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if rate.created_by_id == request.user.id:
            return Response(
                {
                    "detail": (
                        "You recorded this rate, so you may not also approve it. "
                        "Independent challenge is the point of the approval."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )
        if rate.is_approved:
            return Response(self.get_serializer(rate).data)

        rate.approved_by = request.user
        rate.approved_at = timezone.now()
        rate.updated_by = request.user
        rate.save()
        # Frozen once approved: a run's numbers cannot be explained by a rate
        # that was edited after they were calculated.
        rate.freeze()

        audit.record(
            action=AuditAction.APPROVE,
            subject_type="currency_rate",
            subject_id=rate.id,
            actor=request.user,
            subject_label=str(rate),
            after={"approved": True},
            request=request,
        )
        return Response(self.get_serializer(rate).data)


# -- the Klapton Re geocoded policy extract -----------------------------------

class ImportBatchSerializer(serializers.ModelSerializer):
    blocking = serializers.BooleanField(read_only=True)
    may_accept = serializers.BooleanField(read_only=True)

    class Meta:
        model = ImportBatch
        fields = [
            "id", "project", "profile", "state", "source_filename",
            "source_checksum", "snapshot_date", "schema_version", "parser_version",
            "cohort_rule_version", "policy_row_count",
            "risk_row_count", "findings", "intake_report", "cohort_profile",
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
            "needs_review", "class_of_business", "country_code",
            "total_insured_value", "cohort", "cohort_reason",
            "cohort_rule_version", "review_state", "review_note", "reviewed_at",
            "address",
        ]
        read_only_fields = fields

    def get_address(self, obj) -> str:
        """The risk address, to every project member.

        Checking a coordinate against the address it came from is the whole of
        the geocoding review, so withholding it from a modeller would withhold
        the evidence from the person doing the work.
        """
        return (obj.values or {}).get("address") or ""


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

    @action(detail=False, methods=["get"], url_path="template")
    def template(self, request, version=None):
        """Download a blank CASS intake template.

        Generated from the profile rather than kept as a file beside it, so the
        workbook a person fills in can never describe a mapping the platform
        does not implement.
        """
        project = Project.objects.filter(
            id=request.query_params.get("project"),
            id__in=[item.id for item in visible_projects(request.user)],
        ).first()
        payload = intake_template.workbook(
            project_reference=project.reference if project else ""
        )
        response = HttpResponse(
            payload,
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        response["Content-Disposition"] = (
            'attachment; filename="cass-portfolio-intake.xlsx"'
        )
        return response

    @action(detail=False, methods=["get"], url_path="profile")
    def profile(self, request, version=None):
        """The intake profile: every column, and the OED field it lands in.

        Published as data because three things read it -- the template a person
        downloads, the reader that interprets a completed one, and any loading
        API that populates CASS from a source system. A mapping reimplemented
        by a caller is a mapping that drifts.
        """
        return Response(intake_profile.as_dict())

    @action(detail=False, methods=["post"], url_path="upload")
    def upload(self, request, version=None):
        """Register, read and profile one completed intake template."""
        project_id = request.data.get("project")
        upload = request.FILES.get("file")

        if not upload:
            return Response(
                {"detail": "Attach the completed intake template as 'file'."},
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
        batch = extract_service.import_portfolio(
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
        """What was read, under which rules, and what it produced.

        Downloaded rather than displayed, so the download is audited: a
        manifest is the artefact most likely to be attached to a ticket or an
        email, and knowing which of them left the platform is worth more than
        deciding who may ask for one.
        """
        batch = self.get_object()
        audit.record(
            action=AuditAction.DOWNLOAD,
            subject_type="import_batch",
            subject_id=batch.id,
            actor=request.user,
            project=batch.project,
            subject_label=str(batch),
            request=request,
        )
        return Response(extract_service.transformation_manifest(batch))



    @action(detail=True, methods=["get"], url_path="allocation-scenarios")
    def allocation_scenarios(self, request, pk=None, version=None):
        """Compare multi-location allocation scenarios against a model version.

        The question this answers is not "how does the value divide" -- the
        allocation engine answers that exactly -- but "does the division
        matter". It matters only where a business's sites fall in different
        area-peril cells, so the comparison is run against a real grid and
        reports the share of value for which the assumption is economically
        live.

        Nothing is promoted. An analyst can try scenarios freely and promote
        the one they choose, which is the separate audited act.
        """
        batch = self.get_object()
        model_reference = str(request.query_params.get("model_version") or "").strip()
        if not model_reference:
            return Response(
                {
                    "detail": (
                        "Name the model version to compare against. A scenario "
                        "comparison needs a grid: without one there is no way to say "
                        "whether moving value between two sites changes anything."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        model_version = ModelVersion.objects.filter(id=model_reference).first()
        if model_version is None:
            return Response(
                {"detail": f"No model version {model_reference}."},
                status=status.HTTP_404_NOT_FOUND,
            )

        requested = request.query_params.getlist("method") or None
        try:
            methods = (
                tuple(cass_extract.AllocationMethod(item) for item in requested)
                if requested
                else scenarios.DEFAULT_METHODS
            )
            comparison = scenarios.compare(
                batch,
                model_version=model_version,
                methods=methods,
                cohort=cass_extract.Cohort(
                    str(request.query_params.get("cohort") or "A")
                ),
                class_of_business=_requested_class(request.query_params),
                country=str(request.query_params.get("country") or "").strip() or None,
                occupancy=_requested_occupancy(request.query_params),
            )
        except (
            promotion.PromotionError,
            scenarios.ScenarioError,
            cass_extract.AllocationError,
            ModelAssetError,
            ValueError,
        ) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response(comparison.as_dict())

    @action(detail=True, methods=["post"])
    def promote(self, request, pk=None, version=None):
        """Turn a cohort selection into a published OED exposure version.

        The assumptions are the caller's to choose: which cohort, how a
        multi-location policy divides, and where the coverage values come from.
        Every one of them is recorded on the version that results, so a later
        reader can see what produced it and run it again differently.

        Coverage values, occupancy and construction come from the intake
        template itself, row by row, and are used exactly as stated. The
        assumptions named here fill only what a schedule left blank: a
        component split where a risk states a total but no breakdown, an
        allocation where it states neither, and an occupancy where it names
        none.

        ``country`` narrows the selection to one country, because a model
        version covers one. A business with sites in two is excluded from both
        rather than split, for the same reason a partial schedule is.
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

        try:
            exposure = promotion.promote(
                batch,
                name=name,
                cohort=cohort,
                class_of_business=class_of_business,
                country=str(request.data.get("country") or "").strip() or None,
                allocation_method=method,
                component_split=_requested_split(request.data),
                occupancy=_requested_occupancy(request.data),
                actor=request.user,
                request=request,
            )
        except (promotion.PromotionError, cass_extract.AllocationError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response(
            promotion.promotion_summary(exposure), status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["get"], url_path="import-results")
    def import_results(self, request, pk=None, version=None):
        """Everything work package 2's screen shows, in one response.

        One payload rather than six calls, because the screen's whole job is to
        let a person see the import as a single picture -- what came in, what
        was excluded and why, how value was allocated, what a review has
        changed, and which mode the result may be used in. Six requests would
        let it render half a picture and look complete.
        """
        batch = self.get_object()
        return Response(review.import_results(batch))

    @action(detail=True, methods=["get"], url_path="review-queue")
    def review_queue(self, request, pk=None, version=None):
        """The locations a person still owes a decision on."""
        batch = self.get_object()
        return Response(review.queue(batch))

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "model_version",
                OpenApiTypes.UUID,
                description="Compare against this model version's grid.",
            ),
            OpenApiParameter(
                "grid",
                OpenApiTypes.UUID,
                description="Or against a grid directly, before any version uses it.",
            ),
            *(
                OpenApiParameter(
                    f"buffer_{name}_km",
                    OpenApiTypes.NUMBER,
                    description=f"Radius a {name}-precision geocode stands for.",
                )
                for name in ("locality", "postcode", "admin")
            ),
        ],
        responses=OpenApiTypes.OBJECT,
    )
    @action(detail=True, methods=["get"], url_path="geocoding-sensitivity")
    def geocoding_sensitivity(self, request, pk=None, version=None):
        """Whether each Cohort B geocode supports the cell the grid gives it.

        Work package 3 step 4. Each location is tried across the area its
        precision stands for, and the report says which keep their cell, which
        reach others, and how much value sits on the ones that move. The buffers
        are assumptions, recorded on the report and open to override.
        """
        batch = self.get_object()
        model_reference = str(request.query_params.get("model_version") or "").strip()
        grid_reference = str(request.query_params.get("grid") or "").strip()
        if not (model_reference or grid_reference):
            return Response(
                {
                    "detail": (
                        "Name the model version or the grid to test against. Whether a "
                        "geocode supports its cell depends on how fine the cells are."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if model_reference:
            model_version = ModelVersion.objects.filter(id=model_reference).first()
            registered = model_version.grid if model_version else None
        else:
            registered = AreaPerilGrid.objects.filter(id=grid_reference).first()
        if registered is None:
            return Response(
                {"detail": f"No model version or grid {model_reference or grid_reference}."},
                status=status.HTTP_404_NOT_FOUND,
            )

        overrides = {
            name: request.query_params[f"buffer_{name}_km"]
            for name in ("locality", "postcode", "admin")
            if request.query_params.get(f"buffer_{name}_km") not in (None, "")
        }
        try:
            document = geocoding.cohort_b_sensitivity(
                batch, grid=load_grid(registered), buffers_km=overrides
            )
        except (SensitivityError, ModelAssetError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(document)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "location_id",
                OpenApiTypes.UUID,
                OpenApiParameter.PATH,
                description="The staged location the decision is about.",
            )
        ]
    )
    @action(
        detail=True,
        methods=["post"],
        url_path=r"locations/(?P<location_id>[^/.]+)/decide",
    )
    def decide(self, request, pk=None, version=None, location_id=None):
        """Record one review decision, with the reason for it.

        The staged row is not edited. A decision is a separate record laid over
        it, so the batch keeps saying what the workbook said and a later
        promotion reads the overlay.
        """
        batch = self.get_object()
        if not batch.project.may_write(request.user):
            return Response(
                {"detail": "You may not review imports in this project."},
                status=status.HTTP_403_FORBIDDEN,
            )
        location = batch.location_rows.filter(pk=location_id).first()
        if location is None:
            return Response(
                {"detail": "That location is not part of this import."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            decision = review.decide(
                location,
                field=str(request.data.get("field") or ""),
                value=request.data.get("value"),
                rationale=str(request.data.get("rationale") or ""),
                actor=request.user,
            )
        except review.ReviewError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        audit.record(
            action=AuditAction.UPDATE,
            subject_type="import_batch",
            subject_id=batch.id,
            actor=request.user,
            project=batch.project,
            subject_label=str(location),
            request=request,
            before={decision.field: decision.previous_value},
            after={decision.field: decision.new_value},
            # The reason belongs in the trail, not only in the row it changed:
            # an auditor reads the events, and one that recorded the change
            # without the reason would record the least useful half.
            detail=decision.rationale,
        )
        return Response(
            {
                "location": review.overlay(location).as_dict(),
                "history": review.history(location),
            },
            status=status.HTTP_201_CREATED,
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


def _requested_occupancy(data) -> cass_extract.OccupancyAssumption:
    """The occupancy assumption the caller asked for, preset or their own.

    An arbitrary OED code is accepted, because the interface has to be able to
    ask "what if this book were all industrial?" without a release. The code is
    not checked against OED's list here: an occupancy OED does not define is
    reported by validation, and one no vulnerability function covers is
    reported by the keys lookup as fail_v. Refusing it here would only move the
    same answer earlier and pretend this layer owned the code list.
    """
    code = str(data.get("occupancy_code") or "").strip()
    if code:
        return cass_extract.uniform(
            str(data.get("occupancy") or f"occupancy_{code}_v1"),
            code,
            str(data.get("construction_code") or cass_extract.UNKNOWN_CONSTRUCTION),
            description=str(data.get("occupancy_description") or ""),
        )
    requested = str(data.get("occupancy") or "").strip()
    if not requested:
        return cass_extract.DEFAULT_OCCUPANCY
    return cass_extract.occupancy_preset(requested)


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
                "occupancy_assumptions": serializers.ListField(),
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
                "occupancy_assumptions": [
                    item.as_dict()
                    for item in sorted(
                        cass_extract.OCCUPANCY_PRESETS.values(),
                        key=lambda entry: entry.name,
                    )
                ],
                "default_coverage_split": cass_extract.DEFAULT_SPLIT.name,
                "default_occupancy": cass_extract.DEFAULT_OCCUPANCY.name,
                "custom_split_allowed": True,
                "custom_occupancy_allowed": True,
            }
        )


def _requested_class(data) -> str | None:
    """The class filter, where "any" means do not filter at all."""
    requested = data.get("class_of_business", "Fire")
    if requested in ("", "any", None):
        return None
    return str(requested)
