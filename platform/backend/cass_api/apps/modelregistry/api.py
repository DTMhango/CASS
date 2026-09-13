"""Model catalogue API.

Section 3 requires the model catalogue to show country, peril, version,
publication state, assumptions and validation date. Section 9 requires
unapproved research runs and approved decision outputs to be visually and
operationally distinct, so every model serialisation states plainly whether it
may be used for decisions and what is blocking publication.
"""

from __future__ import annotations

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.permissions import MayApproveGates, MayPublishModels
from apps.modelregistry.assets import ModelAssetError, load_grid
from apps.runs.models import HazardRun, Run, RunKind

from . import grid_build, hazard_models, quality
from . import hazard as hazard_registry
from .models import (
    AreaPerilGrid,
    AssumptionSet,
    ConversionTolerances,
    HazardBenchmark,
    HazardJobSpec,
    HazardModel,
    HazardSet,
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
            "damage_bin_count", "assumption_variants", "publication_state", "is_frozen",
            "created_at",
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
            "hazard_set", "hazard_source_model", "hazard_source_licence",
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


def catalogue_assumption_sets(model_version) -> list[dict]:
    """The assumption sets a run against this version may name.

    Only sets its vulnerability set carries functions for, the latest version of
    each, and whether anyone has approved it: a run under an unapproved set
    produces research output, and the analyst choosing one should see that
    before choosing rather than after.
    """
    carried = list((model_version.vulnerability_set.assumption_variants or {}).keys())
    latest: dict[str, AssumptionSet] = {}
    for item in AssumptionSet.objects.filter(
        country_code=model_version.country_code.upper()
    ).order_by("-created_at"):
        if item.flavour in carried:
            latest.setdefault(item.flavour, item)
    return [
        {
            "id": str(item.id),
            "flavour": item.flavour,
            "label": item.label,
            "reference": item.reference,
            "publication_state": item.publication_state,
            "approved": item.publication_state
            in (PublicationState.APPROVED, PublicationState.PUBLISHED),
        }
        for flavour, item in sorted(latest.items(), key=lambda pair: carried.index(pair[0]))
    ]


class AreaPerilGridViewSet(viewsets.ModelViewSet):
    queryset = AreaPerilGrid.objects.all()
    serializer_class = AreaPerilGridSerializer
    permission_classes = [MayPublishModels]
    filterset_fields = ["country_code", "publication_state"]

    @extend_schema(
        request=inline_serializer(
            name="GridSpecificationRequest",
            fields={
                "country_code": serializers.CharField(),
                "version": serializers.CharField(),
                "label": serializers.CharField(),
                "base_resolution_deg": serializers.CharField(),
                "mapping_tolerance_km": serializers.CharField(required=False),
                "tiles": serializers.ListField(child=serializers.DictField()),
                "refinements": serializers.ListField(
                    child=serializers.DictField(), required=False
                ),
                "open_questions": serializers.ListField(
                    child=serializers.CharField(), required=False
                ),
                "notes": serializers.CharField(required=False),
            },
        ),
        responses={
            201: inline_serializer(
                name="GridBuildResult",
                fields={
                    "grid": AreaPerilGridSerializer(),
                    "summary": serializers.DictField(),
                },
            )
        },
    )
    @action(detail=False, methods=["post"], url_path="build")
    def build(self, request, version=None):
        """Build a country's grid from a specification, rather than uploading cells.

        Section 6 asks each country for a fixed, versioned, adaptive grid that is
        independent of any portfolio. The specification is the artefact a
        reviewer argues with -- tiles, a base resolution, named refinements and
        the reason for each -- and the geometry follows from it, so the two
        cannot drift apart.
        """
        try:
            grid, summary = grid_build.build(request.data, actor=request.user)
        except grid_build.GridBuildError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        audit.record(
            action=AuditAction.CREATE,
            subject_type="area_peril_grid",
            subject_id=grid.id,
            actor=request.user,
            subject_label=str(grid),
            request=request,
            after={
                "cells": summary["cells"],
                "builder_version": summary["builder_version"],
            },
            detail=f"Built {grid.reference} from a specification.",
        )
        return Response(
            {"grid": AreaPerilGridSerializer(grid).data, "summary": summary},
            status=status.HTTP_201_CREATED,
        )


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

    @action(detail=True, methods=["post"], url_path="attach-hazard")
    def attach_hazard(self, request, pk=None, version=None):
        """Point this model version at a hazard set.

        Refused unless the set was computed on this version's grid and carries
        every measure its vulnerability functions demand -- half the measures
        would answer half the functions and report zero for the rest.
        """
        model_version = self.get_object()
        hazard_set = HazardSet.objects.filter(pk=request.data.get("hazard_set") or None).first()
        if hazard_set is None:
            return Response(
                {"detail": "Name the hazard set to attach."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        before = {"hazard_set": str(model_version.hazard_set_id or "")}
        try:
            hazard_registry.attach(model_version, hazard_set, actor=request.user)
        except hazard_registry.HazardRegistrationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        audit.record(
            action=AuditAction.CONFIGURE,
            subject_type="model_version",
            subject_id=model_version.id,
            actor=request.user,
            subject_label=str(model_version),
            before=before,
            after={"hazard_set": str(hazard_set.id)},
            request=request,
        )
        return Response(self.get_serializer(model_version).data)

    @action(detail=True, methods=["post"], url_path="build-package")
    def build_package(self, request, pk=None, version=None):
        """Convert this version into an Oasis model package, as a run.

        The response is the queued conversion run. The policy is stated by the
        caller and cleared by a converter-candidate approval somebody else
        decided; the converter has no default for either open question.
        """
        from apps.audit.models import Approval
        from apps.runs.models import ConversionRun, HazardRun, Run, RunKind
        from apps.runs.tasks import execute_conversion
        from cass_converter import oasis_package, pilot_bins
        from cass_converter.policy import ConversionPolicy, EventIdentity, IMTRepresentation

        model_version = self.get_object()
        hazard_set = model_version.hazard_set
        if hazard_set is None:
            return Response(
                {"detail": "Attach a hazard set before building a package."},
                status=status.HTTP_409_CONFLICT,
            )
        approval = Approval.objects.filter(
            pk=request.data.get("approval") or None,
            gate=Approval.Gate.CONVERTER_CANDIDATE,
        ).first()
        if approval is None or not approval.is_cleared:
            return Response(
                {
                    "detail": (
                        "A package is built under a converter-candidate approval that "
                        "has been decided. Request the gate for this model version and "
                        "have a reviewer approve it."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )
        try:
            event_identity = EventIdentity(
                request.data.get("event_identity") or EventIdentity.OCCURRENCE_PER_EVENT
            )
            imt_representation = IMTRepresentation(
                request.data.get("imt_representation") or IMTRepresentation.CORRELATED_CHANNELS
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        policy = ConversionPolicy(
            event_identity=event_identity,
            imt_representation=imt_representation,
            approval_reference=f"approval:{approval.id}",
            imts=tuple(hazard_set.imts),
            investigation_time=hazard_set.investigation_time,
        )
        blockers = policy.blockers()
        if blockers:
            return Response(
                {"detail": "The conversion policy is not runnable.", "blockers": blockers},
                status=status.HTTP_409_CONFLICT,
            )

        hazard_run = HazardRun.objects.filter(
            run__manifest__hazard__hazard_set__id=str(hazard_set.id)
        ).first()
        run = Run.objects.create(
            kind=RunKind.CONVERSION,
            project=None,
            label=f"Oasis package for {model_version.reference}",
            execution_profile="model_build",
            manifest={
                "policy": {
                    "event_identity": str(event_identity),
                    "imt_representation": str(imt_representation),
                    "approval": str(approval.id),
                    "approval_reference": policy.approval_reference,
                },
                "hazard_set": str(hazard_set.id),
            },
            created_by=request.user,
            updated_by=request.user,
        )
        conversion = ConversionRun.objects.create(
            run=run,
            hazard_run=hazard_run,
            model_version=model_version,
            converter_version=oasis_package.PACKAGE_VERSION,
            event_policy=str(event_identity),
            occurrence_policy="engine_year_as_period",
            intensity_bin_set=pilot_bins.PILOT_BIN_VERSION,
            created_by=request.user,
            updated_by=request.user,
        )
        audit.record(
            action=AuditAction.SUBMIT,
            subject_type="conversion_run",
            subject_id=run.id,
            actor=request.user,
            subject_label=str(run),
            after={"model_version": model_version.reference, "approval": str(approval.id)},
            request=request,
        )
        execute_conversion.delay(str(conversion.id))
        run.refresh_from_db()
        return Response(
            {"run": str(run.id), "conversion_run": str(conversion.id), "state": run.state},
            status=status.HTTP_202_ACCEPTED,
        )

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
                    "assumption_sets": catalogue_assumption_sets(item),
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


class HazardSetSerializer(serializers.ModelSerializer):
    reference = serializers.CharField(read_only=True)

    class Meta:
        model = HazardSet
        fields = [
            "id", "reference", "country_code", "version", "label", "source_model",
            "licence", "licence_cleared", "grid", "engine_version",
            "investigation_time", "stochastic_event_sets", "event_count",
            "cell_count", "footprint_row_count", "imts", "samples_above_range",
            "publication_state", "notes", "created_at",
        ]
        read_only_fields = fields


class HazardSetViewSet(viewsets.ReadOnlyModelViewSet):
    """Registered hazard sets, which a model version is pointed at to produce loss."""

    queryset = HazardSet.objects.select_related("grid").order_by("-created_at")
    serializer_class = HazardSetSerializer
    permission_classes = [MayPublishModels]
    filterset_fields = ["country_code", "grid", "publication_state"]

    @action(detail=True, methods=["post"])
    def benchmark(self, request, pk=None, version=None):
        """Compare this hazard against the approved benchmark for its country.

        Section 7's hazard gate. The comparison is against the footprint the
        engine will be given rather than against the engine's own hazard
        curves, because a conversion that lost shaking on the way into the bins
        would otherwise check out against itself.
        """
        hazard_set = self.get_object()
        try:
            report = quality.record_benchmark(hazard_set, actor=request.user)
        except quality.QualityGateError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        if report.get("compared"):
            audit.record(
                action=AuditAction.UPDATE,
                subject_type="hazard_set",
                subject_id=hazard_set.id,
                actor=request.user,
                subject_label=str(hazard_set),
                after={"benchmark": report.get("passed")},
                request=request,
            )
        return Response(report)


class HazardBenchmarkSerializer(serializers.ModelSerializer):
    is_approved = serializers.BooleanField(read_only=True)
    point_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = HazardBenchmark
        fields = [
            "id", "country_code", "label", "source", "reference", "grid", "points",
            "tolerance", "publication_state", "approved_at", "notes", "is_approved",
            "point_count", "created_at",
        ]
        read_only_fields = [
            "id", "publication_state", "approved_at", "is_approved", "point_count",
            "created_at",
        ]

    def validate_points(self, value):
        """Every point must state a cell, a measure, a period and an intensity."""
        if not value:
            raise serializers.ValidationError(
                "A benchmark with no points compares nothing."
            )
        wanted = {"areaperil_id", "imt", "return_period", "intensity"}
        for position, item in enumerate(value, start=1):
            if not isinstance(item, dict) or not wanted <= set(item):
                raise serializers.ValidationError(
                    f"Point {position} must state {', '.join(sorted(wanted))}."
                )
            if float(item["return_period"]) <= 0 or float(item["intensity"]) <= 0:
                raise serializers.ValidationError(
                    f"Point {position} states a return period or intensity that is "
                    "not positive, which no published curve does."
                )
        return value


class ConversionTolerancesSerializer(serializers.ModelSerializer):
    is_approved = serializers.BooleanField(read_only=True)

    class Meta:
        model = ConversionTolerances
        fields = [
            "id", "label", "source", "values", "publication_state", "approved_at",
            "notes", "is_approved", "created_at",
        ]
        read_only_fields = [
            "id", "publication_state", "approved_at", "is_approved", "created_at",
        ]

    def validate_values(self, value):
        """Refuse a tolerance for a check the converter does not measure.

        An approved tolerance nobody checks is worse than none: it reads as
        covered.
        """
        from cass_converter.qa import TOLERANCE_KEYS

        unknown = sorted(set(value or {}) - set(TOLERANCE_KEYS))
        if unknown:
            raise serializers.ValidationError(
                f"{', '.join(unknown)} is not measured by the conversion. Known: "
                + ", ".join(sorted(TOLERANCE_KEYS))
                + "."
            )
        if not value:
            raise serializers.ValidationError(
                "A tolerance set that states nothing decides nothing."
            )
        return value


class _ApprovableRegistry(viewsets.ModelViewSet):
    """A governed reference somebody other than its author approves."""

    permission_classes = [MayPublishModels]

    def get_permissions(self):
        """Registering is a modeller's act; approving is a reviewer's.

        The same split section 10 applies to every other gate. One permission
        for both would mean either that a reviewer could not approve a
        reference or that whoever wrote it could.
        """
        if self.action == "approve":
            return [MayApproveGates()]
        return super().get_permissions()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None, version=None):
        record = self.get_object()
        if not request.user.may_approve_gates:
            return Response(
                {"detail": "Approving a scientific reference requires the reviewer role."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if record.created_by_id == request.user.id:
            return Response(
                {
                    "detail": (
                        "You registered this, so you may not also approve it. "
                        "Independent challenge is the point of the approval."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        record.publication_state = PublicationState.APPROVED
        record.approved_by = request.user
        record.approved_at = timezone.now()
        record.updated_by = request.user
        record.save()
        record.freeze()

        audit.record(
            action=AuditAction.APPROVE,
            subject_type=record._meta.model_name,
            subject_id=record.id,
            actor=request.user,
            subject_label=str(record),
            after={"publication_state": record.publication_state},
            request=request,
        )
        return Response(self.get_serializer(record).data)


class HazardBenchmarkViewSet(_ApprovableRegistry):
    """Published hazard curves a converted set is checked against."""

    queryset = HazardBenchmark.objects.all()
    serializer_class = HazardBenchmarkSerializer
    filterset_fields = ["country_code", "publication_state"]


class ConversionTolerancesViewSet(_ApprovableRegistry):
    """How far a conversion's acceptance measurements may be off."""

    queryset = ConversionTolerances.objects.all()
    serializer_class = ConversionTolerancesSerializer
    filterset_fields = ["publication_state"]


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
            "region",
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
                    "publication_reference": serializers.CharField(required=False),
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
                # Not asked for on upload: the basis follows from who published
                # the model -- GEM's permission for one GEM makes publicly
                # available, the internal-use basis otherwise -- rather than being
                # re-stated by whoever happens to be uploading.
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
                    model,
                    grid,
                    overrides=request.data.get("overrides") or {},
                    # The grid's cells and the published site model, so the
                    # editor shows how much of the country the run covers and
                    # which cells carry measured ground rather than rock.
                    cells=load_grid(grid).cells,
                    region=request.data.get("region") or None,
                    site_points=hazard_models.published_site_points(model),
                )
            )
        except (hazard_models.HazardModelError, ModelAssetError) as exc:
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
                cells=load_grid(grid).cells,
                region=request.data.get("region") or None,
                site_points=hazard_models.published_site_points(model),
                actor=request.user,
            )
        except (hazard_models.HazardModelError, ModelAssetError) as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_409_CONFLICT
            )
        return Response(
            HazardJobSpecSerializer(spec).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "spec_id",
                OpenApiTypes.UUID,
                OpenApiParameter.PATH,
                description="The saved configuration to launch.",
            )
        ]
    )
    @action(
        detail=True,
        methods=["post"],
        url_path=r"specs/(?P<spec_id>[^/.]+)/launch",
    )
    def launch(self, request, pk=None, version=None, spec_id=None):
        """Run a saved configuration on OpenQuake.

        The run is created and queued here; the calculation itself happens in a
        worker. A national hazard calculation runs for hours, and section 3
        requires the work to continue whether or not the browser stays open, so
        the response is the queued run rather than the hazard.
        """
        model = self.get_object()
        spec = model.job_specs.filter(pk=spec_id).first()
        if spec is None:
            return Response(
                {"detail": "That configuration does not belong to this model."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not spec.is_runnable:
            return Response(
                {
                    "detail": (
                        f"{spec.name} has problems that stop a run. Resolve the "
                        "configuration again and save it before launching."
                    ),
                    "problems": spec.blocking_problems,
                },
                status=status.HTTP_409_CONFLICT,
            )
        # Everything on this installation is held under GEM's permission or the
        # internal-use basis, so this is normally cleared and the run is an
        # ordinary one. It stays here for the case it was written for: data
        # somebody has deliberately marked as not usable here, which still
        # calculates and is labelled research only.
        research_only = not model.licence_cleared

        try:
            assembled = hazard_models.job_files(spec)
        except (hazard_models.HazardModelError, ModelAssetError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        if not assembled["checksum_matches"]:
            return Response(
                {
                    "detail": (
                        "The configuration no longer resolves to the job it was "
                        "saved as, usually because the grid was republished. "
                        "Resolve and save it again so the run has a checksum "
                        "somebody reviewed."
                    ),
                    "saved_checksum": assembled["saved_checksum"],
                    "current_checksum": assembled["job_checksum"],
                },
                status=status.HTTP_409_CONFLICT,
            )

        run = Run.objects.create(
            kind=RunKind.HAZARD,
            project=None,
            label=(
                f"{spec.name} ({model.reference})"
                + (" -- research only, licence not cleared" if research_only else "")
            ),
            execution_profile="model_build",
            created_by=request.user,
            updated_by=request.user,
        )
        hazard_run = HazardRun.objects.create(
            run=run,
            grid=spec.grid,
            job_settings={
                # The specification and the checksum, not the package. A
                # national source model is hundreds of megabytes of NRML, and
                # carrying it through a JSON column to be read once would put
                # the whole model in the database. The worker rebuilds from the
                # specification and refuses if the checksum has moved.
                "spec": str(spec.id),
                "job_checksum": assembled["job_checksum"],
                "file_names": sorted(assembled["files"]),
                "problems": assembled["problems"],
            },
            imts=list(spec.resolved_configuration.get("intensity_measures") or []),
            investigation_time=spec.overrides.get("investigation_time"),
            stochastic_event_sets=spec.overrides.get("ses_per_logic_tree_path"),
            random_seed=spec.overrides.get("random_seed"),
            created_by=request.user,
            updated_by=request.user,
        )

        audit.record(
            action=AuditAction.SUBMIT,
            subject_type="hazard_run",
            subject_id=run.id,
            actor=request.user,
            subject_label=str(run),
            after={
                "spec": str(spec.id),
                "job_checksum": assembled["job_checksum"],
                "research_only": research_only,
            },
            request=request,
        )

        from apps.runs.tasks import execute_hazard

        execute_hazard.delay(str(hazard_run.id))
        run.refresh_from_db()
        return Response(
            {
                "run": str(run.id),
                "hazard_run": str(hazard_run.id),
                "state": run.state,
                "job_checksum": assembled["job_checksum"],
                "research_only": research_only,
                "licence_note": model.licence_note if research_only else "",
            },
            status=status.HTTP_202_ACCEPTED,
        )
