"""Results workspace API."""

from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.permissions import IsProjectMember
from apps.common.queries import visible_projects

from . import comparison as comparison_service
from .models import ResultComparison, ResultSet, ResultState


class ResultSetSerializer(serializers.ModelSerializer):
    usable_for_decisions = serializers.BooleanField(read_only=True)
    caveats = serializers.SerializerMethodField()

    class Meta:
        model = ResultSet
        fields = [
            "id", "run", "project", "label", "perspective", "state",
            "average_annual_loss", "standard_deviation", "currency",
            "return_period_losses", "model_version_reference",
            "assumption_set_reference", "run_mode", "valuation_date", "exposure_quality",
            "peril_scope", "material_exclusions", "uncertainty_attribution",
            "usable_for_decisions", "caveats", "approved_at", "is_frozen",
            "created_at",
        ]
        read_only_fields = [
            "id", "usable_for_decisions", "caveats", "run_mode", "approved_at",
            "is_frozen", "created_at",
        ]

    def get_caveats(self, obj) -> dict:
        """Section 9: the caveat block travels with every decision view."""
        return obj.export_caveats()


class ResultComparisonSerializer(serializers.ModelSerializer):
    is_like_for_like = serializers.BooleanField(read_only=True)
    baseline_detail = ResultSetSerializer(source="baseline", read_only=True)
    candidate_detail = ResultSetSerializer(source="candidate", read_only=True)

    class Meta:
        model = ResultComparison
        fields = [
            "id", "project", "label", "baseline", "baseline_detail",
            "candidate", "candidate_detail", "differences",
            "commentary", "is_like_for_like", "created_at",
        ]
        # ``differences`` is computed from the two results rather than supplied.
        # ADR 5 keeps money arithmetic on the server: a difference a caller
        # posted would be a number nobody could reproduce, stored as a fact.
        read_only_fields = [
            "id", "baseline_detail", "candidate_detail", "differences",
            "is_like_for_like", "created_at",
        ]

    def validate(self, attrs):
        baseline = attrs.get("baseline")
        candidate = attrs.get("candidate")
        if baseline and candidate:
            if baseline.perspective != candidate.perspective:
                raise serializers.ValidationError(
                    "Comparing a "
                    f"{baseline.get_perspective_display().lower()} against a "
                    f"{candidate.get_perspective_display().lower()} produces a "
                    "difference that cannot be interpreted."
                )
            if baseline.currency != candidate.currency:
                raise serializers.ValidationError(
                    f"The results are in {baseline.currency} and {candidate.currency}. "
                    "Normalise to one currency before comparing."
                )
            if baseline.pk == candidate.pk:
                raise serializers.ValidationError(
                    "A result compared against itself has no difference to report."
                )
            if baseline.project_id != candidate.project_id:
                raise serializers.ValidationError(
                    "The two results belong to different projects. A comparison "
                    "belongs to one of them, and which one would be arbitrary."
                )
        return attrs


class ResultSetViewSet(viewsets.ModelViewSet):
    queryset = ResultSet.objects.none()
    serializer_class = ResultSetSerializer
    permission_classes = [IsProjectMember]
    filterset_fields = ["project", "run", "perspective", "state"]
    ordering_fields = ["created_at", "average_annual_loss"]

    def get_queryset(self):
        return ResultSet.objects.filter(
            project__in=visible_projects(self.request.user)
        ).select_related("project", "run")

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None, version=None):
        """Release a result for decision use.

        Section 9 requires approved decision outputs to be operationally
        distinct from research runs, and section 10 requires independent
        challenge, so the reviewer role is checked and the run must have
        succeeded.
        """
        result = self.get_object()
        if not request.user.may_approve_gates:
            return Response(
                {"detail": "Approving a result for decision use requires the reviewer role."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not result.run.may_publish_results:
            return Response(
                {
                    "detail": (
                        f"The run is in state {result.run.state}. Only a successful run "
                        "may release results."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )
        if result.state == ResultState.RESEARCH:
            # Research output is research whatever a reviewer does. Approving
            # it would approve the prototype model, the unapproved assumption
            # set or the run mode behind it without anyone having reviewed
            # those -- which is the substitution section 9 exists to prevent.
            return Response(
                {
                    "detail": (
                        "This is research output and cannot be approved for decision "
                        "use. Clear what makes it research -- the model version, the "
                        "assumption set or the mode the run was made under -- and run "
                        "it again for decision use."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        before = {"state": result.state}
        result.state = ResultState.APPROVED
        result.approved_by = request.user
        result.approved_at = timezone.now()
        result.updated_by = request.user
        result.save()
        result.freeze()

        audit.record(
            action=AuditAction.APPROVE,
            subject_type="result_set",
            subject_id=result.id,
            actor=request.user,
            project=result.project,
            subject_label=str(result),
            before=before,
            after={"state": result.state},
            request=request,
        )
        return Response(self.get_serializer(result).data)

    @action(detail=True, methods=["get"])
    def export(self, request, pk=None, version=None):
        """The auditable result package header.

        Section 12 requires every result to be traceable to immutable exposure,
        model, engine, converter and settings versions, so the export carries
        the run manifest alongside the metrics.
        """
        result = self.get_object()
        return Response(
            {
                "result": self.get_serializer(result).data,
                "run_manifest": result.run.manifest,
                "correlation_id": result.run.correlation_id,
                "exported_at": timezone.now().isoformat(),
                "exported_by": str(request.user),
                "warning": (
                    None
                    if result.usable_for_decisions
                    else "This result is not approved for decision use."
                ),
            }
        )


class ResultComparisonViewSet(viewsets.ModelViewSet):
    queryset = ResultComparison.objects.none()
    serializer_class = ResultComparisonSerializer
    permission_classes = [IsProjectMember]
    filterset_fields = ["project"]

    def get_queryset(self):
        return ResultComparison.objects.filter(
            project__in=visible_projects(self.request.user)
        ).select_related("baseline", "candidate")

    def perform_create(self, serializer):
        """Compute the comparison, then save it.

        Once, at creation, and stored. Recomputing on every read would let a
        saved comparison change quietly when a result was corrected, and the
        point of saving one is to be able to say what was compared and when.
        """
        serializer.save(
            created_by=self.request.user,
            updated_by=self.request.user,
            differences=comparison_service.differences(
                serializer.validated_data["baseline"],
                serializer.validated_data["candidate"],
            ),
        )

    def perform_update(self, serializer):
        """Keep the stored answer consistent with what it answers about.

        Editing the commentary must not silently leave a difference computed
        from two other results standing beside a new pair, so the arithmetic is
        redone against whichever results the record now names.
        """
        instance = serializer.instance
        baseline = serializer.validated_data.get("baseline", instance.baseline)
        candidate = serializer.validated_data.get("candidate", instance.candidate)
        serializer.save(
            updated_by=self.request.user,
            differences=comparison_service.differences(baseline, candidate),
        )
