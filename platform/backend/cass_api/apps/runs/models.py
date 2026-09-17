"""Runs: hazard, conversion and analysis.

Section 5 lists the three run records and what each must retain. Section 12
requires that a failure produce an intelligible state with a safe retry or
cancellation path, and that every result be traceable to immutable versions.

One ``Run`` table holds the lifecycle common to all three, with a ``kind`` that
selects the pipeline from ``cass_core.runs``; the kind-specific detail lives in a
one-to-one record. This keeps the state machine, queue admission, cancellation
and audit in a single place rather than reimplemented three times.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel
from cass_core.runs import (
    ACTIVE_STATES,
    IllegalStageSequence,
    RunState,
    check_transition,
    is_retryable,
    is_terminal,
    may_publish_results,
    pipeline,
)


class RunKind(models.TextChoices):
    HAZARD = "hazard", "Hazard run"
    CONVERSION = "conversion", "Conversion run"
    ANALYSIS = "analysis", "Analysis run"


class RunMode(models.TextChoices):
    """What an analysis is for, and therefore what it may be used to claim.

    Section 5.2 of the geocoded portfolio brief requires the mode to be
    explicit, and requires that outputs from different modes are never mixed in
    one comparison without a warning. It is stated on the run rather than
    inferred from what the run happens to contain, because the claim a number
    makes is a decision somebody takes before the calculation, not a property
    the calculation acquires afterwards.

    The default is the technical mode rather than decision use: a run nobody
    labelled must not be capable of producing a decision number, and decision
    use is what the brief permits last.
    """

    GEOMETRY_ONLY = "geometry_only", "Geometry only, no loss calculated"
    TECHNICAL = "technical", "KRE-share technical loss"
    RESEARCH = "research", "Portfolio-loss research"
    DECISION = "decision", "Decision use"


class Run(BaseModel):
    """The lifecycle record shared by every kind of run."""

    kind = models.CharField(max_length=16, choices=RunKind.choices, db_index=True)
    project = models.ForeignKey(
        "projects.Project",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="runs",
        help_text="Null for model-build runs, which belong to a model version rather than a project.",
    )
    label = models.CharField(max_length=200, blank=True)

    state = models.CharField(
        max_length=16,
        choices=[(item.value, item.name.title()) for item in RunState],
        default=RunState.DRAFT.value,
        db_index=True,
    )
    stage = models.CharField(
        max_length=32, blank=True, help_text="Current pipeline stage key."
    )
    progress = models.FloatField(default=0.0)

    #: How far into the current stage the engine says it has got, where it says
    #: anything at all. ``progress`` counts pipeline stages, which is the wrong
    #: unit for a monitor an analyst watches: an OpenQuake calculation is one
    #: stage of six and most of the wall clock, so the stage fraction sits still
    #: for hours while the work moves. Null where the engine reports nothing,
    #: because a bar nobody can derive is an invented number.
    stage_progress = models.FloatField(null=True, blank=True)
    #: What that fraction counts -- the OpenQuake phase, or Oasis sub-tasks.
    #: Recorded with it because OpenQuake's percentage restarts for each phase,
    #: and a number that reaches a hundred three times is a lie without the
    #: name of what finished.
    stage_progress_label = models.CharField(max_length=80, blank=True)

    execution_profile = models.CharField(
        max_length=32,
        default="standard",
        help_text="Declared CPU, memory and timeout envelope from section 11.",
    )
    correlation_id = models.CharField(max_length=64, blank=True, db_index=True)

    queued_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    #: Failure is recorded in business terms as well as technically, because
    #: section 12 asks for an intelligible state rather than a stack trace.
    failure_stage = models.CharField(max_length=32, blank=True)
    failure_summary = models.CharField(max_length=500, blank=True)
    failure_detail = models.TextField(blank=True)

    #: Why a run is waiting at a governance gate. Kept apart from the failure
    #: fields on purpose: a blocked run has not failed, and a monitor that
    #: reads a gate reason out of ``failure_summary`` tells an analyst their
    #: run broke when it is simply waiting for someone to decide something.
    gate_summary = models.CharField(max_length=500, blank=True)
    gate_detail = models.TextField(blank=True)

    retry_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="retries"
    )
    cancelled_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="runs_cancelled",
    )

    #: The manifest body assembled as the run proceeds. Section 12 requires a
    #: result to be traceable to immutable exposure, model, engine, converter
    #: and settings versions; this is where that record accumulates.
    manifest = models.JSONField(default=dict, blank=True)
    settings_hash = models.CharField(max_length=80, blank=True)

    peak_memory_mb = models.IntegerField(null=True, blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "state", "-created_at"]),
            models.Index(fields=["kind", "state"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.label or self.id}"

    # -- pipeline ----------------------------------------------------------
    @property
    def pipeline(self):
        return pipeline(self.kind)

    @property
    def run_state(self) -> RunState:
        return RunState(self.state)

    @property
    def is_active(self) -> bool:
        return self.run_state in ACTIVE_STATES

    @property
    def may_publish_results(self) -> bool:
        return may_publish_results(self.run_state)

    @property
    def may_retry(self) -> bool:
        return is_retryable(self.run_state)

    @property
    def elapsed_seconds(self) -> int | None:
        """Wall-clock seconds since the run started, running clock included.

        ``duration_seconds`` is written when a run finishes, so reading it on
        its own leaves the monitor blank for exactly as long as the run is
        worth watching. A run still going is timed against now instead.
        """
        if self.started_at is None:
            return None
        if self.duration_seconds is not None:
            return self.duration_seconds
        return max(int((timezone.now() - self.started_at).total_seconds()), 0)

    def stage_label(self) -> str:
        if not self.stage:
            return ""
        return self.pipeline.stage(self.stage).label

    # -- progress ----------------------------------------------------------
    def record_stage_progress(
        self, fraction: float | None, label: str = "", *, save: bool = True
    ) -> bool:
        """Record how far into the current stage an engine says it has got.

        Written straight onto the run rather than as a stage event: this is the
        current position, not something that happened, and a ten-hour
        calculation reporting every whole percent would otherwise write a
        thousand rows nobody reads. Says whether it changed anything, so a
        poller can leave the database alone while the number stands still.
        """
        if fraction is None:
            value = None
        else:
            value = round(max(0.0, min(1.0, float(fraction))), 4)

        label = (label or "")[:80]
        if value == self.stage_progress and label == self.stage_progress_label:
            return False

        self.stage_progress = value
        self.stage_progress_label = label
        if save:
            self.save(
                update_fields=["stage_progress", "stage_progress_label", "updated_at"]
            )
        return True

    def advance(
        self,
        stage: str,
        *,
        actor=None,
        message: str = "",
        metrics: dict | None = None,
        save: bool = True,
    ) -> Run:
        """Move to the next stage without changing the lifecycle state.

        A run spends its whole execution in ``RUNNING`` while working through
        the pipeline, and the state machine rightly refuses ``RUNNING`` to
        ``RUNNING``. Stage progress therefore needs its own path, and this is
        it: the stage, the progress fraction and an append-only event, with no
        lifecycle change.

        A pipeline may skip forward over a stage that a given run does not
        perform, but it may never move backwards. Backwards is not a retry --
        a retry is a new run -- it is lost lineage, so it is refused here
        rather than silently recorded.
        """
        target = self.pipeline.index_of(stage)
        if self.stage:
            current = self.pipeline.index_of(self.stage)
            if target < current:
                raise IllegalStageSequence(
                    f"cannot move run {self.id} back from {self.stage!r} to {stage!r}"
                )

        self.stage = stage
        self.progress = self.pipeline.progress(stage)
        # A fraction measured inside the stage just left says nothing about the
        # one just entered, so it goes rather than being carried forward.
        self.stage_progress = None
        self.stage_progress_label = ""
        if save:
            self.save(
                update_fields=[
                    "stage", "progress", "stage_progress", "stage_progress_label",
                    "updated_at",
                ]
            )

        RunStageEvent.objects.create(
            run=self,
            stage=stage,
            state=self.state,
            message=message[:500],
            metrics=metrics or {},
            created_by=actor,
        )
        return self

    # -- transitions -------------------------------------------------------
    def transition(
        self,
        target: RunState | str,
        *,
        actor=None,
        stage: str | None = None,
        failure_summary: str = "",
        failure_detail: str = "",
        save: bool = True,
    ) -> Run:
        """Move the run to a new state, refusing anything the machine forbids.

        The state machine lives in ``cass_core`` so that workers enforce the same
        rules; this method applies the timestamps and failure fields that go
        with each transition.
        """
        new_state = check_transition(self.run_state, RunState(target))
        now = timezone.now()

        self.state = new_state.value
        if stage and stage != self.stage:
            self.stage_progress = None
            self.stage_progress_label = ""
        if stage:
            self.stage = stage
            self.progress = self.pipeline.progress(stage)
        if is_terminal(new_state):
            # Nothing is working on it any more, so a fraction left on screen
            # would describe a calculation that is no longer running.
            self.stage_progress = None
            self.stage_progress_label = ""

        match new_state:
            case RunState.QUEUED:
                self.queued_at = self.queued_at or now
                # Section 4: one correlation ID follows the run everywhere.
                # Stamped here, where every kind of run passes, from whatever
                # is current -- the request, or the task the broker carried it
                # into -- so a run's record and its log lines share one key.
                if not self.correlation_id:
                    from apps.audit.middleware import current_correlation_id

                    self.correlation_id = current_correlation_id()[:64]
            case RunState.RUNNING:
                self.started_at = self.started_at or now
            case RunState.FAILED:
                self.finished_at = now
                self.failure_stage = stage or self.stage
                self.failure_summary = failure_summary[:500]
                self.failure_detail = failure_detail
            case RunState.CANCELLED:
                self.finished_at = now
                self.cancelled_by = actor or self.cancelled_by
            case RunState.SUCCEEDED:
                self.finished_at = now
                self.progress = 1.0

        if self.finished_at and self.started_at:
            self.duration_seconds = int((self.finished_at - self.started_at).total_seconds())

        if save:
            self.save()

        RunStageEvent.objects.create(
            run=self,
            stage=stage or self.stage,
            state=self.state,
            # Truncated for the same reason the field above is: an engine can
            # hand back a failure longer than this column, and a run whose
            # failure could not be written stays RUNNING for ever -- the
            # unintelligible state the monitor exists to prevent.
            message=failure_summary[:500],
            created_by=actor,
        )
        return self


class RunStageEvent(BaseModel):
    """An append-only history of stage and state changes.

    The run monitor reads this to explain progress and failure, and section 12
    uses it as resilience evidence: a killed worker leaves a legible trail.
    """

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="events")
    stage = models.CharField(max_length=32, blank=True)
    state = models.CharField(max_length=16)
    message = models.CharField(max_length=500, blank=True)
    metrics = models.JSONField(
        default=dict,
        blank=True,
        help_text="Throughput, memory and I/O measurements captured at this stage.",
    )

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["run", "created_at"])]

    def __str__(self) -> str:
        return f"{self.run_id} {self.stage} {self.state}"


class HazardRun(BaseModel):
    """OpenQuake execution detail (section 5)."""

    run = models.OneToOneField(Run, on_delete=models.CASCADE, related_name="hazard")
    model_version = models.ForeignKey(
        "modelregistry.ModelVersion", null=True, blank=True,
        on_delete=models.PROTECT, related_name="hazard_runs",
    )
    grid = models.ForeignKey(
        "modelregistry.AreaPerilGrid", on_delete=models.PROTECT, related_name="hazard_runs"
    )
    #: Set where this run rebuilds an existing hazard set's footprint from its
    #: stored datastore rather than computing new ground motion. Such a run
    #: skips submitting and monitoring: the calculation already happened.
    rebuild_of = models.ForeignKey(
        "modelregistry.HazardSet",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="rebuild_runs",
    )

    openquake_calculation_id = models.CharField(max_length=32, blank=True)
    openquake_version = models.CharField(max_length=32, blank=True)
    image_digest = models.CharField(max_length=120, blank=True)

    job_settings = models.JSONField(default=dict, blank=True)
    imts = models.JSONField(default=list, blank=True)
    investigation_time = models.FloatField(null=True, blank=True)
    stochastic_event_sets = models.IntegerField(null=True, blank=True)
    #: Paths sampled through the logic tree (ADR 18). Recorded beside the seed,
    #: because between them they are what makes this run's catalogue the one it
    #: was rather than another draw of the same configuration.
    logic_tree_paths = models.IntegerField(null=True, blank=True)
    random_seed = models.BigIntegerField(
        null=True, blank=True,
        help_text="Recorded so a reproducible run can be repeated exactly.",
    )

    event_count = models.BigIntegerField(null=True, blank=True)
    site_count = models.BigIntegerField(null=True, blank=True)
    gmf_bytes = models.BigIntegerField(null=True, blank=True)

    def __str__(self) -> str:
        return f"hazard {self.openquake_calculation_id or self.run_id}"


class ConversionRun(BaseModel):
    """OpenQuake to Oasis lineage (section 5)."""

    run = models.OneToOneField(Run, on_delete=models.CASCADE, related_name="conversion")
    #: The hazard run whose output is converted, where the hazard set came from
    #: one. A set registered from exports on disk has none, and its lineage is on
    #: the hazard set record instead.
    hazard_run = models.ForeignKey(
        HazardRun,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="conversions",
    )
    model_version = models.ForeignKey(
        "modelregistry.ModelVersion", null=True, blank=True,
        on_delete=models.PROTECT, related_name="conversion_runs",
    )

    converter_version = models.CharField(max_length=32, blank=True)
    event_policy = models.CharField(
        max_length=64,
        blank=True,
        help_text="The approved event identity specification applied.",
    )
    occurrence_policy = models.CharField(max_length=64, blank=True)
    intensity_bin_set = models.CharField(max_length=64, blank=True)

    source_checksum = models.CharField(max_length=80, blank=True)
    target_checksum = models.CharField(max_length=80, blank=True)

    #: Scientific acceptance evidence from section 7, retained with the run so
    #: an approver reads the numbers rather than a pass or fail flag.
    qa_state = models.CharField(
        max_length=16,
        choices=[
            ("pending", "Pending"),
            ("passed", "Passed"),
            ("failed", "Failed"),
            ("waived", "Waived with rationale"),
        ],
        default="pending",
    )
    qa_report = models.JSONField(default=dict, blank=True)
    frequency_preserved = models.BooleanField(null=True, blank=True)
    checkpoint = models.JSONField(
        default=dict,
        blank=True,
        help_text="Durable restart point, so a long conversion can resume without republishing.",
    )

    def __str__(self) -> str:
        return f"conversion {self.run_id}"


class AnalysisRun(BaseModel):
    """Portfolio loss execution (section 5)."""

    run = models.OneToOneField(Run, on_delete=models.CASCADE, related_name="analysis")
    exposure_version = models.ForeignKey(
        "exposure.ExposureVersion", on_delete=models.PROTECT, related_name="analysis_runs"
    )
    enrichment_run = models.ForeignKey(
        "exposure.EnrichmentRun", null=True, blank=True,
        on_delete=models.PROTECT, related_name="analysis_runs",
    )
    #: The assumption set the run weighs unknown attributes under. Null means the
    #: baseline weights the model was built with. What the enrich stage applied
    #: is recorded as the ``enrichment_run`` above.
    assumption_set = models.ForeignKey(
        "modelregistry.AssumptionSet", null=True, blank=True,
        on_delete=models.PROTECT, related_name="analysis_runs",
    )
    model_version = models.ForeignKey(
        "modelregistry.ModelVersion", on_delete=models.PROTECT, related_name="analysis_runs"
    )

    #: What the run is for (brief section 5.2). It decides how far the pipeline
    #: goes and what the results may claim, so it is chosen when the run is
    #: created and never afterwards.
    mode = models.CharField(
        max_length=16,
        choices=RunMode.choices,
        default=RunMode.TECHNICAL,
        db_index=True,
        help_text="What this analysis is for, and therefore what its output may claim.",
    )

    perspectives = models.JSONField(
        default=list, help_text="Requested perspectives, checked against the source data."
    )
    analysis_settings = models.JSONField(default=dict, blank=True)
    run_currency = models.CharField(max_length=3, blank=True)
    #: The rate, valuation date, source and direction used to normalise the book
    #: into the run currency, and what that moved. Empty where the book was
    #: already in the run currency. Section 8 requires this before generation,
    #: and it is held on the run because the published exposure is immutable and
    #: states what the business reported.
    currency_conversion = models.JSONField(default=dict, blank=True)

    oasis_analysis_id = models.CharField(max_length=32, blank=True)
    oasis_portfolio_id = models.CharField(max_length=32, blank=True)

    #: Keys reconciliation. Section 8 forbids proceeding until successful,
    #: not-at-risk and failed TIV reconcile to the published OED source.
    keys_summary = models.JSONField(default=dict, blank=True)
    keys_reconciled = models.BooleanField(null=True, blank=True)
    exception_approval = models.ForeignKey(
        "audit.Approval", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="analysis_runs",
        help_text="The approval that permitted an unreconciled or exceptional run to proceed.",
    )

    def __str__(self) -> str:
        return f"analysis {self.run_id}"

    @property
    def run_mode(self) -> RunMode:
        return RunMode(self.mode)

    @property
    def calculates_loss(self) -> bool:
        """Whether this run submits anything to the engine at all.

        A geometry-only run maps coordinates to the grid and reports which
        risks the model can answer for. It makes no financial claim, so it
        stops at the keys report rather than producing a loss nobody asked for.
        """
        return self.run_mode is not RunMode.GEOMETRY_ONLY

    @property
    def may_produce_decision_output(self) -> bool:
        """Whether a result from this run may ever be approved for decision use.

        Only the decision mode, and the brief permits that mode only once
        scientific validation, licensing and data-quality thresholds are
        complete -- which is why choosing it is checked when the run is made.
        """
        return self.run_mode is RunMode.DECISION

    @property
    def unmapped_tiv(self):
        """Value the keys lookup could not map, from the recorded summary."""
        from decimal import Decimal

        raw = (self.keys_summary or {}).get("failed_tiv") or "0"
        try:
            return Decimal(str(raw))
        except Exception:
            return Decimal(0)

    @property
    def may_proceed_past_keys(self) -> bool:
        """Whether the section 8 keys gate is clear.

        Two conditions, and they are not the same kind of thing.

        The accounting has to balance: successful, not-at-risk and failed TIV
        summing to the published source. Every location, coverage and sub-peril
        is supposed to produce exactly one response, so value that has gone
        missing is a defect in the lookup rather than a fact about the
        portfolio. Nothing approves that away.

        Beyond that, value the model could not map is a fact about the
        portfolio, and section 8 requires a person to accept it before the
        analysis proceeds. That is what the exception approval clears.
        """
        if not self.keys_reconciled:
            return False
        if not self.unmapped_tiv:
            return True
        return bool(self.exception_approval and self.exception_approval.is_cleared)
