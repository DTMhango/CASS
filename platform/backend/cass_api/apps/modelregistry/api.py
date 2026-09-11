"""Model catalogue API.

Section 3 requires the model catalogue to show country, peril, version,
publication state, assumptions and validation date. Section 9 requires
unapproved research runs and approved decision outputs to be visually and
operationally distinct, so every model serialisation states plainly whether it
may be used for decisions and what is blocking publication.
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.permissions import MayPublishModels

from .models import (
    AreaPerilGrid,
    AssumptionSet,
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
