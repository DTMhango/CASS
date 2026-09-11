"""Run monitor API.

Section 3 requires the run monitor to explain progress and failure: pipeline
stage, elapsed time, logs, warnings, artifacts and retry controls. Section 12
requires a failure to produce an intelligible state and a safe retry or
cancellation path. Those are the two things this module serves.
"""

from __future__ import annotations

from django.db import models
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.artifacts.models import ArtifactLink
from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.permissions import IsProjectMember
from apps.common.queries import visible_projects
from cass_core.runs import RunState, describe

from .models import AnalysisRun, ConversionRun, HazardRun, Run, RunStageEvent


class RunStageEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = RunStageEvent
        fields = ["id", "stage", "state", "message", "metrics", "created_at"]
        read_only_fields = fields


class RunSerializer(serializers.ModelSerializer):
    stage_label = serializers.SerializerMethodField()
    may_retry = serializers.BooleanField(read_only=True)
    may_publish_results = serializers.BooleanField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    pipeline = serializers.SerializerMethodField()

    class Meta:
        model = Run
        fields = [
            "id", "kind", "project", "label", "state", "stage", "stage_label",
            "progress", "pipeline", "execution_profile", "correlation_id",
            "queued_at", "started_at", "finished_at", "duration_seconds",
            "peak_memory_mb", "failure_stage", "failure_summary", "failure_detail",
            "settings_hash", "retry_of", "may_retry", "may_publish_results",
            "is_active", "created_at",
        ]
        read_only_fields = fields

    def get_stage_label(self, obj) -> str:
        return obj.stage_label()

    def get_pipeline(self, obj) -> list[dict]:
        return describe(obj.kind)


class HazardRunSerializer(serializers.ModelSerializer):
    run_detail = RunSerializer(source="run", read_only=True)

    class Meta:
        model = HazardRun
        fields = [
            "id", "run", "run_detail", "model_version", "grid",
            "openquake_calculation_id", "openquake_version", "image_digest",
            "job_settings", "imts", "investigation_time", "stochastic_event_sets",
            "random_seed", "event_count", "site_count", "gmf_bytes", "created_at",
        ]
        read_only_fields = ["id", "run_detail", "created_at"]


class ConversionRunSerializer(serializers.ModelSerializer):
    run_detail = RunSerializer(source="run", read_only=True)

    class Meta:
        model = ConversionRun
        fields = [
            "id", "run", "run_detail", "hazard_run", "model_version",
            "converter_version", "event_policy", "occurrence_policy",
            "intensity_bin_set", "source_checksum", "target_checksum",
            "qa_state", "qa_report", "frequency_preserved", "created_at",
        ]
        read_only_fields = ["id", "run_detail", "created_at"]


class AnalysisRunSerializer(serializers.ModelSerializer):
    run_detail = RunSerializer(source="run", read_only=True)
    may_proceed_past_keys = serializers.BooleanField(read_only=True)

    class Meta:
        model = AnalysisRun
        fields = [
            "id", "run", "run_detail", "exposure_version", "enrichment_run",
            "model_version", "perspectives", "analysis_settings", "run_currency",
            "oasis_analysis_id", "oasis_portfolio_id", "keys_summary",
            "keys_reconciled", "may_proceed_past_keys", "exception_approval",
            "created_at",
        ]
        read_only_fields = [
            "id", "run_detail", "oasis_analysis_id", "oasis_portfolio_id",
            "keys_summary", "keys_reconciled", "may_proceed_past_keys", "created_at",
        ]


class RunViewSet(viewsets.ReadOnlyModelViewSet):
    """Runs are created through their kind-specific endpoints, then monitored here."""

    queryset = Run.objects.none()
    serializer_class = RunSerializer
    permission_classes = [IsProjectMember]
    filterset_fields = ["kind", "state", "project"]
    ordering_fields = ["created_at", "finished_at"]

    def get_queryset(self):
        user = self.request.user
        queryset = Run.objects.all()
        if not getattr(user, "is_platform_admin", False):
            # Model-build runs have no project; they are visible to anyone who
            # may see the catalogue, which every authenticated user may.
            queryset = queryset.filter(
                models.Q(project__in=visible_projects(user)) | models.Q(project__isnull=True)
            )
        return queryset.select_related("project")

    @action(detail=True, methods=["get"])
    def events(self, request, pk=None, version=None):
        """The stage history that explains what happened and when."""
        run = self.get_object()
        return Response(
            RunStageEventSerializer(run.events.all(), many=True).data
        )

    @action(detail=True, methods=["get"])
    def artifacts(self, request, pk=None, version=None):
        """Inputs and outputs recorded against this run."""
        run = self.get_object()
        links = ArtifactLink.objects.filter(
            subject_type=f"{run.kind}_run", subject_id=run.id
        ).select_related("artifact")
        return Response(
            [
                {
                    "role": link.role,
                    "direction": link.direction,
                    "uri": link.artifact.uri,
                    "checksum": link.artifact.checksum,
                    "size_bytes": link.artifact.size_bytes,
                    "retention": link.artifact.retention,
                    "readable": link.artifact.may_read(request.user),
                }
                for link in links
            ]
        )

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None, version=None):
        """Request cancellation.

        The run moves to CANCELLING so the worker can unwind to a clean point;
        section 11 requires cancellation to leave no published partial result.
        """
        run = self.get_object()
        if run.project and not run.project.may_write(request.user):
            return Response({"detail": "You may not cancel runs in this project."}, status=403)
        try:
            run.transition(RunState.CANCELLING, actor=request.user)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        audit.record(
            action=AuditAction.CANCEL,
            subject_type="run",
            subject_id=run.id,
            actor=request.user,
            project=run.project,
            subject_label=str(run),
            after={"state": run.state},
            request=request,
        )
        return Response(self.get_serializer(run).data)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None, version=None):
        """Create a fresh run from a failed one.

        A retry is a new run rather than a reset of the old one, so the failed
        attempt keeps its evidence and the lineage of both stays intact.
        """
        run = self.get_object()
        if not run.may_retry:
            return Response(
                {
                    "detail": f"A run in state {run.state} cannot be retried.",
                    "hint": "Only a failed run may be retried.",
                },
                status=status.HTTP_409_CONFLICT,
            )
        if run.project and not run.project.may_write(request.user):
            return Response({"detail": "You may not retry runs in this project."}, status=403)

        replacement = Run.objects.create(
            kind=run.kind,
            project=run.project,
            label=f"{run.label} (retry)".strip(),
            execution_profile=run.execution_profile,
            manifest=run.manifest,
            settings_hash=run.settings_hash,
            retry_of=run,
            created_by=request.user,
            updated_by=request.user,
        )
        audit.record(
            action=AuditAction.RETRY,
            subject_type="run",
            subject_id=replacement.id,
            actor=request.user,
            project=run.project,
            subject_label=str(replacement),
            before={"run": str(run.id), "failure_stage": run.failure_stage},
            after={"run": str(replacement.id)},
            request=request,
        )
        return Response(self.get_serializer(replacement).data, status=201)


class HazardRunViewSet(viewsets.ModelViewSet):
    queryset = HazardRun.objects.select_related("run", "grid", "model_version")
    serializer_class = HazardRunSerializer
    permission_classes = [IsProjectMember]
    filterset_fields = ["grid", "model_version"]


class ConversionRunViewSet(viewsets.ModelViewSet):
    queryset = ConversionRun.objects.select_related("run", "hazard_run", "model_version")
    serializer_class = ConversionRunSerializer
    permission_classes = [IsProjectMember]
    filterset_fields = ["hazard_run", "model_version", "qa_state"]


class AnalysisRunViewSet(viewsets.ModelViewSet):
    queryset = AnalysisRun.objects.none()
    serializer_class = AnalysisRunSerializer
    permission_classes = [IsProjectMember]
    filterset_fields = ["exposure_version", "model_version"]

    def get_queryset(self):
        return AnalysisRun.objects.filter(
            run__project__in=visible_projects(self.request.user)
        ).select_related("run", "exposure_version", "model_version")
