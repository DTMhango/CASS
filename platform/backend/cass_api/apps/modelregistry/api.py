"""Model catalogue API.

Section 3 requires the model catalogue to show country, peril, version,
publication state, assumptions and validation date. Section 9 requires
unapproved research runs and approved decision outputs to be visually and
operationally distinct, so every model serialisation states plainly whether it
may be used for decisions and what is blocking publication.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_field,
    inline_serializer,
)
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.permissions import MayApproveGates, MayPublishModels
from apps.modelregistry.assets import ModelAssetError, load_grid
from apps.runs.models import HazardRun, Run, RunKind
from cass_converter import gem as gem_release
from cass_keys import land as land_outlines
from cass_keys import seeds as grid_seeds

from . import assembly, gem_location, grid_build, hazard_models, quality
from . import gem as gem_registry
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
            "specification", "created_at",
        ]
        read_only_fields = ["id", "reference", "is_frozen", "specification", "created_at"]


class VulnerabilitySetSerializer(serializers.ModelSerializer):
    unsupported_imts = serializers.ListField(read_only=True)
    #: Whether the functions were discretised against the intensity bins CASS
    #: uses now; null for a set that records no fingerprint.
    intensity_bins_current = serializers.SerializerMethodField()

    class Meta:
        model = VulnerabilitySet
        fields = [
            "id", "country_code", "version", "source", "source_commit",
            "taxonomy_generation", "licence", "licence_cleared", "licence_note",
            "function_count", "imts_used", "unsupported_imts", "coverage_components",
            "damage_bin_count", "intensity_bins_current", "assumption_variants",
            "publication_state", "is_frozen", "created_at",
        ]
        read_only_fields = [
            "id", "unsupported_imts", "intensity_bins_current", "is_frozen", "created_at",
        ]

    def get_intensity_bins_current(self, vulnerability_set) -> bool | None:
        recorded = vulnerability_set.intensity_bins_checksum
        if not recorded:
            return None
        return recorded == hazard_registry.current_intensity_bins_checksum()


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
                "domain": serializers.DictField(required=False),
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

    @extend_schema(
        request=inline_serializer(
            name="GridEstimateRequest",
            fields={
                "base_resolution_deg": serializers.CharField(required=False),
                "tiles": serializers.ListField(child=serializers.DictField(), required=False),
                "refinements": serializers.ListField(
                    child=serializers.DictField(), required=False
                ),
            },
        ),
        responses={
            200: inline_serializer(
                name="GridEstimate",
                fields={
                    "cells": serializers.IntegerField(),
                    "exact": serializers.BooleanField(),
                    "cells_from_tiles": serializers.IntegerField(),
                    "cells_by_refinement": serializers.ListField(child=serializers.DictField()),
                    "candidates": serializers.IntegerField(),
                    "removed_as_sea": serializers.IntegerField(),
                    "removed_as_unsettled": serializers.IntegerField(),
                    "uncovered_land": serializers.DictField(allow_null=True),
                    "counted_tiles": serializers.IntegerField(),
                    "incomplete": serializers.IntegerField(),
                    "limit": serializers.IntegerField(),
                    "within_limit": serializers.BooleanField(),
                    "is_upper_bound": serializers.BooleanField(),
                    "estimated": serializers.BooleanField(),
                    "problems": serializers.ListField(child=serializers.CharField()),
                    "storage": serializers.DictField(),
                },
            )
        },
    )
    @action(detail=False, methods=["post"], url_path="estimate")
    def estimate(self, request, version=None):
        """How many cells a specification would generate, before it is built.

        The same count ``build`` guards against, answered while the
        specification is still being written. Nothing is created, registered or
        changed, so nothing is audited: this reads a document the caller is
        holding and does arithmetic on it.
        """
        try:
            return Response(grid_build.estimate(request.data))
        except grid_build.GridBuildError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        responses={
            200: inline_serializer(
                name="GridSeed",
                fields={
                    "country_code": serializers.CharField(),
                    "label": serializers.CharField(),
                    "version": serializers.CharField(),
                    "base_resolution_deg": serializers.CharField(),
                    "tiles": serializers.IntegerField(),
                    "refinements": serializers.IntegerField(),
                    "domain": serializers.DictField(),
                    "cells": serializers.IntegerField(allow_null=True),
                },
                many=True,
            )
        },
    )
    @action(detail=False, methods=["get"], url_path="seeds")
    def seeds(self, request, version=None):
        """The grid specifications CASS ships, one line each.

        A seed is where a specification starts, not a grid: it is loaded into the
        builder, changed like anything typed there, and built by whoever builds
        it. Nothing is registered by reading one.
        """
        return Response(grid_seeds.catalogue())

    @extend_schema(
        responses={
            200: inline_serializer(
                name="GridSeedDetail",
                fields={
                    "specification": serializers.DictField(),
                    "measured": serializers.DictField(),
                },
            )
        },
    )
    @action(detail=False, methods=["get"], url_path=r"seeds/(?P<code>[A-Za-z]{2})")
    def seed(self, request, code=None, version=None):
        """One seed's specification, as the grid builder takes it, and what it measured."""
        try:
            return Response(
                {
                    "specification": grid_seeds.specification(code),
                    "measured": grid_seeds.measured(code),
                }
            )
        except grid_seeds.SeedError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)

    @extend_schema(
        responses={
            200: inline_serializer(
                name="GridCountry",
                fields={
                    "code": serializers.CharField(),
                    "name": serializers.CharField(),
                    "parts": serializers.ListField(child=serializers.CharField()),
                    "bounds": serializers.DictField(),
                    "seeded": serializers.BooleanField(),
                },
                many=True,
            )
        },
    )
    @action(detail=False, methods=["get"], url_path="countries")
    def countries(self, request, version=None):
        """Every country a grid can be clipped to, by its two-letter code.

        Natural Earth's list, because it is the land the clip reads: a code it
        does not draw has no outline to clip to.
        """
        seeded = set(grid_seeds.countries())
        return Response(
            [
                {**outline.as_dict(), "seeded": code in seeded}
                for code, outline in sorted(
                    land_outlines.countries().items(), key=lambda item: item[1].name
                )
            ]
        )


class VulnerabilitySetViewSet(viewsets.ModelViewSet):
    queryset = VulnerabilitySet.objects.all()
    serializer_class = VulnerabilitySetSerializer
    permission_classes = [MayPublishModels]
    filterset_fields = ["country_code", "publication_state", "licence_cleared"]

    @extend_schema(
        responses={
            200: inline_serializer(
                name="GemCatalogue",
                fields={
                    "release": serializers.CharField(),
                    "countries": serializers.ListField(child=serializers.DictField()),
                },
            )
        }
    )
    @action(detail=False, methods=["get"], url_path="gem-countries")
    def gem_countries(self, request, version=None):
        """Which countries the GEM release on this installation covers.

        Read from the release each time rather than from a list inside CASS. A
        compiled-in list would be wrong the first time GEM published another
        country, which is exactly when somebody would be looking at it. Each
        country carries the ISO codes its stock summary states, or the reason
        they cannot be read.
        """
        root = gem_location.current_root()
        if not root:
            return Response(
                {"detail": gem_location.NOT_CONFIGURED}, status=status.HTTP_409_CONFLICT
            )
        try:
            countries = gem_release.catalogue(root, identify=True)
        except gem_release.GemError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response({"release": root, "countries": list(countries)})

    @extend_schema(
        request=inline_serializer(
            name="VulnerabilitySpecificationRequest",
            fields={
                "gem": serializers.DictField(),
                "enrichment": serializers.DictField(),
                "imt_representation": serializers.CharField(required=False),
            },
        ),
        responses={
            201: inline_serializer(
                name="VulnerabilityBuildResult",
                fields={
                    "vulnerability_set": VulnerabilitySetSerializer(),
                    "report": serializers.DictField(),
                },
            )
        },
    )
    @action(detail=False, methods=["post"], url_path="build")
    def build(self, request, version=None):
        """Build a country's vulnerability set from GEM and a written enrichment.

        The enrichment -- which design eras a country had, what each implies and
        why -- is local expertise rather than a property of the platform, so it
        is stated here rather than compiled in. The candidate sets and weights
        remain GEM's, and so do the country's codes: they are read from the
        release for the country named, and an enrichment stating others is
        refused.
        """
        root = gem_location.current_root()
        if not root:
            return Response(
                {"detail": gem_location.NOT_CONFIGURED}, status=status.HTTP_409_CONFLICT
            )
        try:
            vulnerability_set, built = gem_registry.register_from_specification(
                request.data, root=root, actor=request.user
            )
        except gem_registry.GemRegistrationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        report = gem_registry.report(built)
        audit.record(
            action=AuditAction.CREATE,
            subject_type="vulnerability_set",
            subject_id=vulnerability_set.id,
            actor=request.user,
            subject_label=str(vulnerability_set),
            request=request,
            after={
                "functions": report["functions"],
                "classes": report["classes"],
                "enrichment": built.enrichment.reference,
            },
            detail=f"Built {vulnerability_set} from {gem_registry.GEM_SOURCE}.",
        )
        return Response(
            {
                "vulnerability_set": VulnerabilitySetSerializer(vulnerability_set).data,
                "report": report,
            },
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["get"], url_path="gem-release")
    def gem_release_status(self, request, version=None):
        """Which GEM release this installation builds from, and which releases it can see.

        The release is not bundled: it is the clone on the user's own device,
        mounted into the installation. Each folder is read for its repository
        layout and the commits it is at, so the screen can say which release it
        is and whether it is the one CASS was validated against.
        """
        return Response(gem_location.status())

    @extend_schema(
        request=inline_serializer(
            name="GemReleaseChoice", fields={"path": serializers.CharField()}
        ),
        responses=OpenApiTypes.OBJECT,
    )
    @action(detail=False, methods=["post"], url_path="gem-release/choose")
    def choose_gem_release(self, request, version=None):
        """Choose the folder vulnerability sets are built from, refusing one that is not a release."""
        try:
            record = gem_location.choose(request.data.get("path"), actor=request.user)
        except gem_location.GemLocationError as exc:
            return Response(
                {
                    "detail": "That folder is not a GEM release CASS can build from.",
                    "problems": exc.problems,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        audit.record(
            action=AuditAction.UPDATE,
            subject_type="gem_release",
            subject_id=record.id,
            actor=request.user,
            subject_label=str(record),
            request=request,
            after={
                "path": record.path,
                "release": record.release,
                "matches_validated": record.matches_validated,
            },
            detail=f"Chose the GEM release at {record.path}.",
        )
        return Response(gem_location.status())


class AssumptionSetViewSet(viewsets.ModelViewSet):
    queryset = AssumptionSet.objects.all()
    serializer_class = AssumptionSetSerializer
    permission_classes = [MayPublishModels]
    filterset_fields = ["country_code", "flavour", "publication_state"]


def _registered(model, identifier):
    """One record by id, or nothing where the id is unusable or unknown."""
    try:
        return model.objects.filter(pk=identifier).first()
    except (DjangoValidationError, TypeError, ValueError):
        return None


class ModelVersionViewSet(viewsets.ModelViewSet):
    queryset = ModelVersion.objects.select_related("grid", "vulnerability_set")
    serializer_class = ModelVersionSerializer
    permission_classes = [MayPublishModels]
    filterset_fields = ["country_code", "peril", "publication_state", "is_research_prototype"]
    ordering_fields = ["created_at", "country_code"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    @extend_schema(
        request=inline_serializer(
            name="ModelVersionAssemblyRequest",
            fields={
                "grid": serializers.UUIDField(),
                "vulnerability_set": serializers.UUIDField(),
                "version": serializers.CharField(),
                "label": serializers.CharField(required=False),
            },
        ),
        responses={201: ModelVersionSerializer},
    )
    @action(detail=False, methods=["post"], url_path="assemble")
    def assemble(self, request, version=None):
        """Pair a country's grid with its vulnerability set.

        A grid says where a loss can be computed and a vulnerability set says how
        much damage the shaking does. The model version is the pair, and it is
        what a run names -- so the scope statement and both halves' limitations
        are written onto it here rather than left to whoever fills the form.
        """
        grid = _registered(AreaPerilGrid, request.data.get("grid"))
        vulnerability_set = _registered(
            VulnerabilitySet, request.data.get("vulnerability_set")
        )
        missing = [
            name
            for name, value in (("grid", grid), ("vulnerability set", vulnerability_set))
            if value is None
        ]
        if missing:
            return Response(
                {"detail": f"No {' and no '.join(missing)} with that id is registered."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            model = assembly.assemble(
                grid=grid,
                vulnerability_set=vulnerability_set,
                version=str(request.data.get("version") or ""),
                label=str(request.data.get("label") or ""),
                actor=request.user,
            )
        except assembly.AssemblyError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        audit.record(
            action=AuditAction.CREATE,
            subject_type="model_version",
            subject_id=model.id,
            actor=request.user,
            subject_label=str(model),
            request=request,
            after={
                "grid": grid.reference,
                "vulnerability_set": vulnerability_set.version,
                "blockers": model.publication_blockers(),
            },
            detail=f"Assembled {model.reference} from {grid.reference}.",
        )
        return Response(
            ModelVersionSerializer(model).data, status=status.HTTP_201_CREATED
        )

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
    #: The span the catalogue covers, served rather than left to be recomputed:
    #: it is investigation time by event sets by logic-tree paths, and a reader
    #: multiplying the first two alone gets every annual rate wrong (ADR 18).
    effective_time = serializers.FloatField(read_only=True)
    #: Whether the footprint matches the current intensity bins, and whether it
    #: can be rebuilt from the stored calculation if not.
    rebuild = serializers.SerializerMethodField()

    class Meta:
        model = HazardSet
        fields = [
            "id", "reference", "country_code", "version", "label", "source_model",
            "licence", "licence_cleared", "grid", "engine_version",
            "investigation_time", "stochastic_event_sets", "logic_tree_paths",
            "effective_time", "event_count",
            "cell_count", "footprint_row_count", "imts", "samples_above_range",
            "openquake_calculation_removed", "rebuild",
            "publication_state", "notes", "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(
        inline_serializer(
            name="HazardSetRebuildStatus",
            fields={
                "intensity_bins_current": serializers.BooleanField(allow_null=True),
                "datastore_available": serializers.BooleanField(),
                "datastore_expires_at": serializers.DateTimeField(allow_null=True),
                "unavailable_reason": serializers.CharField(),
                "rebuilt_as": serializers.CharField(allow_null=True),
                "rebuilt_from": serializers.CharField(allow_null=True),
            },
        )
    )
    def get_rebuild(self, hazard_set) -> dict:
        return hazard_registry.rebuild_status(hazard_set)


class HazardSetViewSet(viewsets.ReadOnlyModelViewSet):
    """Registered hazard sets, which a model version is pointed at to produce loss."""

    queryset = HazardSet.objects.select_related("grid", "rebuilt_from").order_by("-created_at")
    serializer_class = HazardSetSerializer
    permission_classes = [MayPublishModels]
    filterset_fields = ["country_code", "grid", "publication_state"]

    @extend_schema(
        request=None,
        responses={
            202: inline_serializer(
                name="HazardRebuildLaunched",
                fields={
                    "run": serializers.UUIDField(),
                    "hazard_run": serializers.UUIDField(),
                    "state": serializers.CharField(),
                },
            )
        },
    )
    @action(detail=True, methods=["post"])
    def rebuild(self, request, pk=None, version=None):
        """Rebuild this set's footprint from its stored calculation, as a run.

        For when the intensity bins have changed since the set was built. The
        ground motion is read again from the datastore CASS kept, so OpenQuake
        is not asked for anything; the new footprint is registered as a new set
        that names this one, and this one's footprint is removed once no model
        version uses it.
        """
        hazard_set = self.get_object()
        refusal = hazard_registry.rebuild_refusal(hazard_set)
        if refusal:
            return Response({"detail": refusal}, status=status.HTTP_409_CONFLICT)

        run = Run.objects.create(
            kind=RunKind.HAZARD,
            project=None,
            label=f"Rebuild footprint of {hazard_set.reference}",
            execution_profile="model_build",
            created_by=request.user,
            updated_by=request.user,
        )
        hazard_run = HazardRun.objects.create(
            run=run,
            grid=hazard_set.grid,
            rebuild_of=hazard_set,
            openquake_calculation_id=hazard_set.openquake_calculation_id,
            job_settings={"rebuild_of": str(hazard_set.id)},
            imts=list(hazard_set.imts),
            investigation_time=hazard_set.investigation_time,
            stochastic_event_sets=hazard_set.stochastic_event_sets,
            logic_tree_paths=hazard_set.logic_tree_paths,
            created_by=request.user,
            updated_by=request.user,
        )
        audit.record(
            action=AuditAction.SUBMIT,
            subject_type="hazard_run",
            subject_id=run.id,
            actor=request.user,
            subject_label=str(run),
            after={"rebuild_of": hazard_set.reference},
            request=request,
        )

        from apps.runs.tasks import rebuild_hazard

        rebuild_hazard.delay(str(hazard_run.id))
        run.refresh_from_db()
        return Response(
            {"run": str(run.id), "hazard_run": str(hazard_run.id), "state": run.state},
            status=status.HTTP_202_ACCEPTED,
        )

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
            logic_tree_paths=spec.overrides.get("number_of_logic_tree_samples"),
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
