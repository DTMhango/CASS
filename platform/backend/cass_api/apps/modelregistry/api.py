"""Model catalogue API.

Section 3 requires the model catalogue to show country, peril, version,
publication state, assumptions and validation date. Section 9 requires
unapproved research runs and approved decision outputs to be visually and
operationally distinct, so every model serialisation states plainly whether it
may be used for decisions and what is blocking publication.
"""

from __future__ import annotations

from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.permissions import MayPublishModels

from . import hazard_models
from .models import (
    AreaPerilGrid,
    AssumptionSet,
    HazardJobSpec,
    HazardModel,
    ModelVersion,
    PublicationState,
    VulnerabilitySet,
)


class AreaPerilGridSerializer(serializers.ModelSerializer):
    reference = serializers.CharField(read_only=True)

    class Meta:
        model = AreaPerilGrid
        fields = [
            "id", "country_code", "version", "label", "reference",
            "base_resolution_deg", "refined_resolution_deg", "refinement_rule",
            "cell_count", "excludes_offshore", "site_condition_source",
            "site_condition_fallback", "border_policy", "mapping_tolerance_km",
            "publication_state", "supersedes", "notes", "is_frozen",
            "created_at",
        ]
        read_only_fields = ["id", "reference", "is_frozen", "created_at"]


class VulnerabilitySetSerializer(serializers.ModelSerializer):
    unsupported_imts = serializers.ListField(read_only=True)

    class Meta:
        model = VulnerabilitySet
        fields = [
            "id", "country_code", "version", "source", "source_commit",
            "taxonomy_generation", "licence", "licence_cleared", "licence_note",
            "function_count", "imts_used", "unsupported_imts", "coverage_components",
            "damage_bin_count", "publication_state", "is_frozen", "created_at",
        ]
        read_only_fields = ["id", "unsupported_imts", "is_frozen", "created_at"]


class AssumptionSetSerializer(serializers.ModelSerializer):
    reference = serializers.CharField(read_only=True)

    class Meta:
        model = AssumptionSet
        fields = [
            "id", "country_code", "flavour", "version", "label", "reference",
            "segment", "rules", "provenance", "weighting_basis",
            "publication_state", "is_frozen", "created_at",
        ]
        read_only_fields = ["id", "reference", "is_frozen", "created_at"]


class ModelVersionSerializer(serializers.ModelSerializer):
    reference = serializers.CharField(read_only=True)
    usable_for_decisions = serializers.BooleanField(read_only=True)
    publication_blockers = serializers.SerializerMethodField()
    grid_detail = AreaPerilGridSerializer(source="grid", read_only=True)
    vulnerability_detail = VulnerabilitySetSerializer(
        source="vulnerability_set", read_only=True
    )

    class Meta:
        model = ModelVersion
        fields = [
            "id", "country_code", "peril", "version", "label", "reference",
            "grid", "grid_detail", "vulnerability_set", "vulnerability_detail",
            "hazard_source_model", "hazard_source_licence",
            "openquake_version", "oasis_version", "converter_version",
            "oed_schema_version", "imts", "peril_scope", "known_limitations",
            "unsupported_taxonomy_report", "is_research_prototype",
            "publication_state", "published_at", "validation_date",
            "usable_for_decisions", "publication_blockers", "supersedes",
            "is_frozen", "created_at",
        ]
        read_only_fields = [
            "id", "reference", "usable_for_decisions", "publication_blockers",
            "published_at", "is_frozen", "created_at",
        ]

    def get_publication_blockers(self, obj) -> list[str]:
        return obj.publication_blockers()


class AreaPerilGridViewSet(viewsets.ModelViewSet):
    queryset = AreaPerilGrid.objects.all()
    serializer_class = AreaPerilGridSerializer
    permission_classes = [MayPublishModels]
    filterset_fields = ["country_code", "publication_state"]


class VulnerabilitySetViewSet(viewsets.ModelViewSet):
    queryset = VulnerabilitySet.objects.all()
    serializer_class = VulnerabilitySetSerializer
    permission_classes = [MayPublishModels]
    filterset_fields = ["country_code", "publication_state", "licence_cleared"]


class AssumptionSetViewSet(viewsets.ModelViewSet):
    queryset = AssumptionSet.objects.all()
    serializer_class = AssumptionSetSerializer
    permission_classes = [MayPublishModels]
    filterset_fields = ["country_code", "flavour", "publication_state"]


class ModelVersionViewSet(viewsets.ModelViewSet):
    queryset = ModelVersion.objects.select_related("grid", "vulnerability_set")
    serializer_class = ModelVersionSerializer
    permission_classes = [MayPublishModels]
    filterset_fields = ["country_code", "peril", "publication_state", "is_research_prototype"]
    ordering_fields = ["created_at", "country_code"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None, version=None):
        """Publish a model version, refusing while blockers stand.

        Section 6 forbids the SA-only package being published as a complete
        country model while material exposure maps to PGA functions, and
        section 10 requires the data-rights gate to be cleared first. Both are
        enforced here rather than left to a reviewer to remember.
        """
        model_version = self.get_object()
        blockers = model_version.publication_blockers()

        force_research = bool(request.data.get("as_research_prototype"))
        if blockers and not force_research:
            return Response(
                {
                    "detail": "This model version cannot be published as a full country model.",
                    "blockers": blockers,
                    "hint": (
                        "Publish it as a research prototype, or clear the blockers. "
                        "A research prototype may be run but not used for decisions."
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )

        before = {
            "publication_state": model_version.publication_state,
            "is_research_prototype": model_version.is_research_prototype,
        }
        model_version.publication_state = PublicationState.PUBLISHED
        model_version.is_research_prototype = bool(blockers) or force_research
        model_version.published_at = timezone.now()
        model_version.updated_by = request.user
        model_version.save()

        audit.record(
            action=AuditAction.PUBLISH,
            subject_type="model_version",
            subject_id=model_version.id,
            actor=request.user,
            subject_label=str(model_version),
            before=before,
            after={
                "publication_state": model_version.publication_state,
                "is_research_prototype": model_version.is_research_prototype,
                "blockers": blockers,
            },
            request=request,
        )
        return Response(self.get_serializer(model_version).data)

    @action(detail=False, methods=["get"], url_path="catalogue")
    def catalogue(self, request, version=None):
        """The analyst-facing catalogue: what may be selected, and why not."""
        rows = []
        for item in self.get_queryset().filter(
            publication_state__in=[PublicationState.PUBLISHED, PublicationState.APPROVED]
        ):
            rows.append(
                {
                    "id": str(item.id),
                    "reference": item.reference,
                    "label": item.label,
                    "country_code": item.country_code,
                    "peril": item.peril,
                    "version": item.version,
                    "imts": item.imts,
                    "publication_state": item.publication_state,
                    "usable_for_decisions": item.usable_for_decisions,
                    "is_research_prototype": item.is_research_prototype,
                    "validation_date": item.validation_date,
                    "grid": item.grid.reference,
                    "assumptions_note": item.known_limitations,
                    "peril_scope": item.peril_scope,
                    "unsupported_taxonomy_report": item.unsupported_taxonomy_report,
                    "blockers": item.publication_blockers(),
                }
            )
        return Response({"models": rows})


# -- uploaded hazard models --------------------------------------------------


class HazardModelSerializer(serializers.ModelSerializer):
    """A registered PSHA package, and what running it would take."""

    reference = serializers.CharField(read_only=True)
    needs_conversion = serializers.BooleanField(read_only=True)
    needs_sampling = serializers.BooleanField(read_only=True)

    class Meta:
        model = HazardModel
        fields = [
            "id",
            "reference",
            "country_code",
            "version",
            "label",
            "source_organisation",
            "publication_reference",
            "licence",
            "licence_cleared",
            "licence_note",
            "archive_checksum",
            "archive_bytes",
            "file_manifest",
            "published_calculation_mode",
            "intensity_measures",
            "tectonic_regions",
            "estimated_realizations",
            "logic_tree_summary",
            "needs_conversion",
            "needs_sampling",
            "publication_state",
            "notes",
            "created_at",
        ]
        read_only_fields = fields


class HazardJobSpecSerializer(serializers.ModelSerializer):
    blocking_problems = serializers.ListField(read_only=True)

    class Meta:
        model = HazardJobSpec
        fields = [
            "id",
            "model",
            "grid",
            "name",
            "overrides",
            "resolved_configuration",
            "conversion_report",
            "site_join_report",
            "problems",
            "blocking_problems",
            "is_runnable",
            "job_checksum",
            "created_at",
        ]
        read_only_fields = fields


class HazardModelViewSet(viewsets.ReadOnlyModelViewSet):
    """Uploading a published PSHA model and configuring a run of it.

    Read-only as a model viewset because a hazard model is not edited through
    the API in the ordinary way: it is uploaded whole, and what an operator
    changes afterwards is a *run* of it, never the model. Editing a source
    logic tree through a REST field would make a model nobody published while
    keeping the name of one who did.
    """

    queryset = HazardModel.objects.all()
    serializer_class = HazardModelSerializer
    permission_classes = [MayPublishModels]
    filterset_fields = ["country_code", "publication_state", "licence_cleared"]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        request={
            "multipart/form-data": inline_serializer(
                name="HazardModelUpload",
                fields={
                    "archive": serializers.FileField(),
                    "country_code": serializers.CharField(),
                    "version": serializers.CharField(),
                    "label": serializers.CharField(),
                    "source_organisation": serializers.CharField(required=False),
                    "licence": serializers.CharField(required=False),
                    "licence_cleared": serializers.BooleanField(required=False),
                    "licence_note": serializers.CharField(required=False),
                },
            )
        },
        responses=HazardModelSerializer,
    )
    @action(detail=False, methods=["post"], url_path="inspect")
    def inspect(self, request, version=None):
        """Read an archive and say what it is, without storing anything.

        So an operator who uploaded the wrong file finds out from a screen
        rather than from a registry record they then have to explain.
        """
        upload = request.FILES.get("archive")
        if upload is None:
            return Response(
                {"detail": "No archive was supplied."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            package = hazard_models.read_package(upload.read())
            return Response(hazard_models.inspect(package))
        except hazard_models.HazardModelError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )

    @extend_schema(responses=HazardModelSerializer)
    @action(detail=False, methods=["post"])
    def upload(self, request, version=None):
        """Register an uploaded package as a configurable hazard model."""
        upload = request.FILES.get("archive")
        if upload is None:
            return Response(
                {"detail": "No archive was supplied."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        required = ("country_code", "version", "label")
        missing = [name for name in required if not request.data.get(name)]
        if missing:
            return Response(
                {
                    "detail": (
                        "A model needs " + ", ".join(missing) + ". None of them "
                        "is derivable from the archive: OpenQuake records that a "
                        "source model was used, never whose it is."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            model = hazard_models.register_model(
                upload.read(),
                country_code=str(request.data["country_code"]),
                version=str(request.data["version"]),
                label=str(request.data["label"]),
                source_organisation=str(request.data.get("source_organisation") or ""),
                publication_reference=str(
                    request.data.get("publication_reference") or ""
                ),
                licence=str(request.data.get("licence") or ""),
                licence_cleared=str(
                    request.data.get("licence_cleared") or ""
                ).lower()
                in ("true", "1", "yes", "on"),
                licence_note=str(request.data.get("licence_note") or ""),
                actor=request.user,
            )
        except hazard_models.HazardModelError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )

        audit.record(
            action=AuditAction.CREATE,
            subject_type="hazard_model",
            subject_id=model.id,
            actor=request.user,
            subject_label=str(model),
            request=request,
            after={
                "archive_checksum": model.archive_checksum,
                "files": len(model.file_manifest),
            },
            detail=f"Uploaded {model.label}.",
        )
        return Response(
            self.get_serializer(model).data, status=status.HTTP_201_CREATED
        )

    @action(detail=False, methods=["get"], url_path="parameters")
    def parameters(self, request, version=None):
        """Every parameter the editor can offer, with what changing it costs."""
        return Response(hazard_models.catalogue())

    @action(detail=True, methods=["post"], url_path="configure")
    def configure(self, request, pk=None, version=None):
        """Work out what a configured run would do, without running it.

        Called on every edit, so the screen shows the resolved configuration,
        the conversion's changes and every problem while the operator is still
        looking at it. A national calculation is hours; finding out there is
        the expensive way.
        """
        model = self.get_object()
        grid = AreaPerilGrid.objects.filter(pk=request.data.get("grid")).first()
        if grid is None:
            return Response(
                {"detail": "Name the area-peril grid this run computes hazard on."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            return Response(
                hazard_models.resolve(
                    model, grid, overrides=request.data.get("overrides") or {}
                )
            )
        except hazard_models.HazardModelError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_409_CONFLICT
            )

    @action(detail=True, methods=["get", "post"], url_path="specs")
    def specs(self, request, pk=None, version=None):
        """List this model's configured runs, or save another."""
        model = self.get_object()
        if request.method == "GET":
            return Response(
                HazardJobSpecSerializer(model.job_specs.all(), many=True).data
            )

        grid = AreaPerilGrid.objects.filter(pk=request.data.get("grid")).first()
        if grid is None:
            return Response(
                {"detail": "Name the area-peril grid this run computes hazard on."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            spec = hazard_models.save_spec(
                model,
                grid,
                name=str(request.data.get("name") or "Unnamed run"),
                overrides=request.data.get("overrides") or {},
                actor=request.user,
            )
        except hazard_models.HazardModelError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_409_CONFLICT
            )
        return Response(
            HazardJobSpecSerializer(spec).data, status=status.HTTP_201_CREATED
        )
