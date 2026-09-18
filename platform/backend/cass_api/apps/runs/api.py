"""Run monitor API.

Section 3 requires the run monitor to explain progress and failure: pipeline
stage, elapsed time, logs, warnings, artifacts and retry controls. Section 12
requires a failure to produce an intelligible state and a safe retry or
cancellation path. Those are the two things this module serves.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models, transaction
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.artifacts.models import ArtifactLink
from apps.audit import services as audit
from apps.audit.api import ApprovalSerializer
from apps.audit.models import Approval, AuditAction
from apps.common.permissions import IsProjectMember
from apps.common.queries import visible_projects
from apps.exposure.review import MINIMUM_RATIONALE
from apps.modelregistry.models import PublicationState
from apps.projects.models import Project
from cass_core.runs import RunState, describe
from cass_oed.perspectives import Perspective

from . import admission, services
from .models import (
    AnalysisRun,
    ConversionRun,
    HazardRun,
    ReinsuranceCover,
    Run,
    RunKind,
    RunMode,
    RunStageEvent,
)


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
    elapsed_seconds = serializers.SerializerMethodField()

    class Meta:
        model = Run
        fields = [
            "id", "kind", "project", "label", "state", "stage", "stage_label",
            "progress", "stage_progress", "stage_progress_label",
            "pipeline", "execution_profile", "correlation_id",
            "queued_at", "started_at", "finished_at", "duration_seconds",
            "elapsed_seconds",
            "peak_memory_mb", "failure_stage", "failure_summary", "failure_detail",
            "gate_summary", "gate_detail", "manifest",
            "settings_hash", "retry_of", "may_retry", "may_publish_results",
            "is_active", "created_at",
        ]
        read_only_fields = fields

    def get_stage_label(self, obj) -> str:
        return obj.stage_label()

    def get_elapsed_seconds(self, obj) -> int | None:
        """Time on the clock now, beside the ``duration_seconds`` finally kept.

        Both are sent. The duration is the record of a finished run, and this is
        what a monitor shows while it is still going, measured on the server so
        a browser's own clock cannot tell the analyst a different story.
        """
        return obj.elapsed_seconds

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
            "logic_tree_paths",
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
    """An analysis, and the run that carries it.

    The two are created together rather than separately. A ``Run`` with no
    detail record is a row nothing can execute and nothing can explain, so
    there is no endpoint that makes one on its own: a caller names what the
    analysis should use, and the lifecycle record comes with it.

    The three write-only fields below are the run half of that -- which
    project owns it, what to call it, and the resource envelope of section 11.
    """

    run_detail = RunSerializer(source="run", read_only=True)
    may_proceed_past_keys = serializers.BooleanField(read_only=True)

    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.none(),
        write_only=True,
        help_text="The project that will own the run, its artifacts and its results.",
    )
    label = serializers.CharField(
        max_length=200, required=False, allow_blank=True, write_only=True
    )
    execution_profile = serializers.CharField(
        max_length=32, required=False, allow_blank=True, write_only=True
    )

    class Meta:
        model = AnalysisRun
        fields = [
            "id", "run", "run_detail", "exposure_version", "enrichment_run", "assumption_set",
            "model_version", "mode", "perspectives", "analysis_settings", "run_currency",
            "reinsurance_cover",
            "oasis_analysis_id", "oasis_portfolio_id", "keys_summary",
            "keys_reconciled", "may_proceed_past_keys", "exception_approval",
            "created_at",
            # Write-only, consumed by ``create`` to build the run.
            "project", "label", "execution_profile",
        ]
        read_only_fields = [
            "id", "run", "run_detail", "oasis_analysis_id", "oasis_portfolio_id",
            "keys_summary", "keys_reconciled", "may_proceed_past_keys", "created_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and "project" in self.fields:
            self.fields["project"].queryset = visible_projects(request.user)

    def validate_execution_profile(self, value: str) -> str:
        """Refuse a profile this installation does not declare.

        Section 11 requires a declared envelope rather than an inherited
        maximum, so an unknown name is a refusal rather than a silent fallback
        to whatever the default happens to be.
        """
        if value and value not in settings.CASS_EXECUTION_PROFILES:
            raise serializers.ValidationError(
                f"{value} is not an execution profile on this installation. "
                f"Declared: {', '.join(sorted(settings.CASS_EXECUTION_PROFILES))}."
            )
        return value

    def validate(self, attrs):
        """Check the preconditions of section 8 before a run exists at all.

        Every one of these is something the analysis builder already shows as
        an outstanding precondition. Repeating them here is deliberate: a
        screen is a courtesy and the API is the rule, so a caller that skips
        the screen still cannot queue work against unpublished input.
        """
        request = self.context.get("request")
        actor = getattr(request, "user", None)

        project = attrs["project"]
        exposure = attrs["exposure_version"]
        model_version = attrs["model_version"]

        if actor is not None and not project.may_write(actor):
            raise serializers.ValidationError(
                {"project": "You may not submit runs in this project."}
            )

        if exposure.project_id != project.id:
            raise serializers.ValidationError(
                {
                    "exposure_version": (
                        f"{exposure.name} belongs to another project. A run reads "
                        "exposure from the project that owns it."
                    )
                }
            )

        if not exposure.is_usable_by_runs:
            raise serializers.ValidationError(
                {
                    "exposure_version": (
                        f"{exposure.name} v{exposure.version} is not published. "
                        "Publish it first, so the run points at immutable input."
                    )
                }
            )

        if model_version.publication_state not in (
            PublicationState.PUBLISHED,
            PublicationState.APPROVED,
        ):
            raise serializers.ValidationError(
                {
                    "model_version": (
                        f"{model_version.reference} is "
                        f"{model_version.get_publication_state_display().lower()} "
                        "rather than published, so it may not be run."
                    )
                }
            )

        # ADR 14: a run may name only an assumption set its model version was
        # built under, because the engine has no functions for any other.
        assumption_set = attrs.get("assumption_set")
        if assumption_set is not None:
            if assumption_set.country_code.upper() != model_version.country_code.upper():
                raise serializers.ValidationError(
                    {
                        "assumption_set": (
                            f"{assumption_set} is for {assumption_set.country_code}, and "
                            f"{model_version.reference} models {model_version.country_code}."
                        )
                    }
                )
            carried = model_version.vulnerability_set.assumption_variants or {}
            if assumption_set.flavour not in carried:
                raise serializers.ValidationError(
                    {
                        "assumption_set": (
                            f"{model_version.reference} carries no functions for the "
                            f"{assumption_set.get_flavour_display().lower()} assumption "
                            "set, so a run could not use it."
                        )
                    }
                )

        # Brief section 5.2: decision use is permitted only once scientific
        # validation and licensing are complete, so the mode is refused here
        # rather than discovered when a reviewer tries to approve the number.
        # The other three modes have nothing to prove: they claim less.
        mode = attrs.get("mode") or AnalysisRun._meta.get_field("mode").default
        if RunMode(mode) is RunMode.DECISION:
            if model_version.is_research_prototype:
                raise serializers.ValidationError(
                    {
                        "mode": (
                            f"{model_version.reference} is a research prototype, so a "
                            "run against it cannot be for decision use. Outstanding: "
                            + "; ".join(model_version.publication_blockers())
                        )
                    }
                )
            chosen_set = attrs.get("assumption_set")
            if chosen_set is not None and chosen_set.publication_state not in (
                PublicationState.PUBLISHED,
                PublicationState.APPROVED,
            ):
                raise serializers.ValidationError(
                    {
                        "mode": (
                            f"{chosen_set} is "
                            f"{chosen_set.get_publication_state_display().lower()}. "
                            "A decision-use run cannot rest on an assumption set "
                            "nobody has approved."
                        )
                    }
                )

        requested = attrs.get("perspectives") or []
        if not requested:
            raise serializers.ValidationError(
                {"perspectives": "Name at least one perspective for the run to produce."}
            )

        # Section 8 forbids implying a perspective the source data does not
        # support, so an unsupported request is refused with the reason the
        # exposure recorded rather than run to a silently zero answer.
        availability = {
            str(item.get("perspective")): item
            for item in (exposure.supported_perspectives or [])
        }
        for perspective in requested:
            entry = availability.get(str(perspective))
            if entry is None:
                raise serializers.ValidationError(
                    {"perspectives": f"{perspective} is not a perspective CASS produces."}
                )
            if not entry.get("available"):
                raise serializers.ValidationError(
                    {
                        "perspectives": (
                            f"{entry.get('label', perspective)} is not supported by "
                            f"this portfolio. {entry.get('reason', '')}".strip()
                        )
                    }
                )

        if attrs.get("reinsurance_cover") == ReinsuranceCover.CONTRACT_TERMS:
            self._check_limited_cover(exposure, requested)

        return attrs

    @staticmethod
    def _check_limited_cover(exposure, requested) -> None:
        """Refuse limited cover the calculation cannot apply, before any engine time."""
        from apps.exposure.services import load_files
        from cass_oed.limited_cover import CoverError, cover_classes

        if str(Perspective.REINSURANCE) not in {str(item) for item in requested}:
            raise serializers.ValidationError(
                {
                    "reinsurance_cover": (
                        "Limited cover changes the loss net of reinsurance, so ask for "
                        "that perspective too."
                    )
                }
            )
        files = load_files(exposure)
        if files.reins_info is None or files.reins_scope is None:
            raise serializers.ValidationError(
                {"reinsurance_cover": "This portfolio carries no reinsurance contracts."}
            )
        try:
            cover_classes(files.reins_info.rows, files.reins_scope.rows, files.location.rows)
        except CoverError as exc:
            raise serializers.ValidationError({"reinsurance_cover": str(exc)}) from exc

    @transaction.atomic
    def create(self, validated_data):
        project = validated_data.pop("project")
        label = validated_data.pop("label", "") or ""
        profile = (
            validated_data.pop("execution_profile", "")
            or settings.CASS_DEFAULT_EXECUTION_PROFILE
        )
        actor = self.context["request"].user
        exposure = validated_data["exposure_version"]

        run = Run.objects.create(
            kind=RunKind.ANALYSIS,
            project=project,
            label=label or f"{exposure.name} v{exposure.version}",
            execution_profile=profile,
            created_by=actor,
            updated_by=actor,
        )
        # The currency the source reconciles in, unless the caller named one.
        if not validated_data.get("run_currency"):
            validated_data["run_currency"] = exposure.run_currency
        return AnalysisRun.objects.create(
            run=run, created_by=actor, updated_by=actor, **validated_data
        )


def _gate_refusal(run, user) -> Response | None:
    """Why a gate action on this run is refused, or ``None`` where it may go ahead."""
    if run.project and not run.project.may_write(user):
        return Response(
            {"detail": "You may not act on runs in this project."},
            status=status.HTTP_403_FORBIDDEN,
        )
    if run.run_state is not RunState.BLOCKED:
        return Response(
            {
                "detail": f"A run in state {run.state} is not held at a gate.",
                "hint": "Only a blocked run needs an exception or a resume.",
            },
            status=status.HTTP_409_CONFLICT,
        )
    return None


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
                    # The id is what makes the artifact retrievable: the
                    # download endpoint is addressed by it, and a payload
                    # carrying only a URI left the monitor able to list
                    # evidence nobody could open.
                    "id": str(link.artifact_id),
                    "role": link.role,
                    "direction": link.direction,
                    "uri": link.artifact.uri,
                    "checksum": link.artifact.checksum,
                    "size_bytes": link.artifact.size_bytes,
                    "retention": link.artifact.retention,
                    # Why a row cannot be opened matters as much as that it
                    # cannot: expired under its retention class is not the same
                    # answer as not permitted, and the monitor says which.
                    "state": link.artifact.state,
                    "readable": (
                        link.artifact.is_readable and link.artifact.may_read(request.user)
                    ),
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

        # CANCELLING is a request, not an outcome. An analysis that has reached
        # Oasis has a worker to stop, and section 11 only counts the resource
        # envelope as freed once it has stopped, so the engine is told in the
        # background and the run reaches CANCELLED from there.
        analysis = getattr(run, "analysis", None)
        if analysis is not None:
            from .tasks import cancel_analysis

            cancel_analysis.delay(str(analysis.id), str(request.user.id))
            run.refresh_from_db()

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
        # A run is a lifecycle record; what makes it executable is the
        # kind-specific detail hanging off it. Copying the lifecycle alone
        # produced a row that could be seen and never run, so the analysis
        # configuration is carried across to the replacement. The engine
        # identifiers and the keys reconciliation are deliberately not: they
        # describe the attempt that failed, and the retry has to earn its own.
        analysis = getattr(run, "analysis", None)
        if analysis is not None:
            AnalysisRun.objects.create(
                run=replacement,
                exposure_version=analysis.exposure_version,
                enrichment_run=analysis.enrichment_run,
                assumption_set=analysis.assumption_set,
                model_version=analysis.model_version,
                # A retry answers the same question as the run it replaces, so
                # it may not quietly claim more than that run did.
                mode=analysis.mode,
                perspectives=analysis.perspectives,
                analysis_settings=analysis.analysis_settings,
                run_currency=analysis.run_currency,
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
    # ``run`` is filterable so the monitor can find the analysis behind a run
    # it is already showing, rather than being given a second id to carry.
    filterset_fields = ["run", "exposure_version", "model_version"]

    def get_queryset(self):
        return AnalysisRun.objects.filter(
            run__project__in=visible_projects(self.request.user)
        ).select_related("run", "exposure_version", "model_version")

    def perform_create(self, serializer):
        """Create the analysis, and record who configured it.

        Creation is separate from submission on purpose: section 3 asks the
        builder to show a validation summary before work is queued, and an
        analyst who configures a run is not always the person who releases it.
        """
        analysis = serializer.save()
        audit.record(
            action=AuditAction.CREATE,
            subject_type="analysis_run",
            subject_id=analysis.run_id,
            actor=self.request.user,
            project=analysis.run.project,
            subject_label=str(analysis.run),
            after={
                "exposure_version": str(analysis.exposure_version_id),
                "model_version": str(analysis.model_version_id),
                "perspectives": analysis.perspectives,
            },
            request=self.request,
        )

    @extend_schema(
        request=inline_serializer(
            "RunExceptionRequest", {"rationale": serializers.CharField()}
        ),
        responses={200: ApprovalSerializer, 201: ApprovalSerializer},
    )
    @action(detail=True, methods=["post"], url_path="request-exception")
    def request_exception(self, request, pk=None, version=None):
        """Ask a reviewer to let a run held at a gate go on.

        The person whose run is held asks, with a reason; a reviewer who did not
        ask decides. That is the independence rule every gate keeps, applied to
        the gates a run itself reaches. The approvals endpoint could not serve
        it: requesting there is a modeller's act, and the person whose analysis
        is waiting is usually an analyst.

        The request is tagged with the stage it was asked at. A run can reach
        more than one gate, and clearing unmapped value at the keys gate says
        nothing about a loss curve that falls with return period. Asking twice
        returns the request that already stands rather than making a second.
        """
        analysis = self.get_object()
        run = analysis.run
        refusal = _gate_refusal(run, request.user)
        if refusal is not None:
            return refusal

        rationale = str(request.data.get("rationale") or "").strip()
        if len(rationale) < MINIMUM_RATIONALE:
            return Response(
                {
                    "detail": (
                        "Say why the run should go on, in at least "
                        f"{MINIMUM_RATIONALE} characters. A reviewer decides on the "
                        "reason, and an auditor reads it afterwards."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = services.stage_approval(analysis, run.stage)
        if existing is not None and (existing.is_open or existing.is_cleared):
            return Response(ApprovalSerializer(existing).data, status=status.HTTP_200_OK)

        approval = Approval.objects.create(
            gate=Approval.Gate.RUN_EXCEPTION,
            subject_type="analysis_run",
            subject_id=analysis.id,
            requested_by=request.user,
            rationale=rationale,
            evidence={
                "stage": run.stage,
                "run": str(run.id),
                "gate_summary": run.gate_summary,
                "gate_detail": run.gate_detail,
                # Kept apart from ``rationale``, which a decision overwrites with
                # the reviewer's reason, so both halves of the exchange survive.
                "requested_because": rationale,
            },
        )
        audit.record(
            action=AuditAction.CREATE,
            subject_type="approval",
            subject_id=approval.id,
            actor=request.user,
            project=run.project,
            subject_label=str(approval),
            after={"gate": approval.gate, "stage": run.stage, "run": str(run.id)},
            request=request,
        )
        return Response(ApprovalSerializer(approval).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses={202: AnalysisRunSerializer})
    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None, version=None):
        """Put a run whose gate has been cleared back in the queue.

        Resumed from the gate rather than restarted. Everything before it stands
        and its evidence is on the run; redoing it would republish the portfolio
        and leave an orphan on the engine.
        """
        analysis = self.get_object()
        run = analysis.run
        refusal = _gate_refusal(run, request.user)
        if refusal is not None:
            return refusal

        approval = services.stage_approval(analysis, run.stage)
        if (
            run.stage == "reconcile_keys"
            and analysis.exception_approval is not None
            and analysis.exception_approval.is_cleared
        ):
            approval = analysis.exception_approval
        if approval is None or not approval.is_cleared:
            return Response(
                {
                    "detail": f"The gate at {run.stage_label() or run.stage} has not been cleared.",
                    "hint": "Ask for an exception, and have a reviewer who did not ask approve it.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        # The keys gate reads its approval from the analysis itself, which is
        # the one definition the API and the service share.
        if run.stage == "reconcile_keys" and analysis.exception_approval_id != approval.id:
            analysis.exception_approval = approval
            analysis.save(update_fields=["exception_approval", "updated_at"])

        from .tasks import execute_analysis

        audit.record(
            action=AuditAction.SUBMIT,
            subject_type="analysis_run",
            subject_id=run.id,
            actor=request.user,
            project=run.project,
            subject_label=str(run),
            after={"resumed_from": run.stage, "approval": str(approval.id)},
            request=request,
        )
        execute_analysis.delay(str(analysis.id))
        analysis = self.get_queryset().get(pk=analysis.pk)
        return Response(self.get_serializer(analysis).data, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None, version=None):
        """Queue the analysis for execution against Oasis.

        The response is the queued run, not the result. Section 3 asks the run
        monitor to explain progress and failure, so an analyst follows the run
        there rather than holding a request open for the length of a loss
        calculation.
        """
        analysis = self.get_object()
        run = analysis.run

        if run.project and not run.project.may_write(request.user):
            return Response(
                {"detail": "You may not submit runs in this project."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if run.run_state is not RunState.DRAFT:
            return Response(
                {
                    "detail": f"A run in state {run.state} cannot be submitted.",
                    "hint": "Only a draft run may be submitted. Retry a failed run instead.",
                },
                status=status.HTTP_409_CONFLICT,
            )
        if not analysis.exposure_version.is_usable_by_runs:
            return Response(
                {
                    "detail": "The exposure version is not published.",
                    "hint": (
                        "Publish the exposure version first, so the run points at "
                        "immutable input."
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Section 11: a declared envelope, enforced. A run admitted beyond its
        # profile's concurrency is one that fails on memory later, taking the
        # runs it was admitted beside with it.
        try:
            admission.admit(run)
        except admission.AdmissionRefused as exc:
            return Response(
                {"detail": str(exc), "profile": exc.profile, "running": exc.running},
                status=status.HTTP_409_CONFLICT,
            )

        from .tasks import execute_analysis

        audit.record(
            action=AuditAction.SUBMIT,
            subject_type="analysis_run",
            subject_id=run.id,
            actor=request.user,
            project=run.project,
            subject_label=str(run),
            after={"exposure_version": str(analysis.exposure_version_id)},
            request=request,
        )
        execute_analysis.delay(str(analysis.id))

        analysis.refresh_from_db()
        return Response(
            self.get_serializer(analysis).data, status=status.HTTP_202_ACCEPTED
        )
