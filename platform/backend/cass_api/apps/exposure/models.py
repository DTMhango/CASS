"""Exposure versions, enrichment runs and source-extract staging.

Section 5 defines the exposure version as an immutable input version and the
enrichment run as the traceable application of assumptions. Section 8 adds the
rules these models exist to hold: raw exposure is immutable, enriched exposure
is a new artifact linked to its source, and no transformation silently replaces
a reported field.

Large enriched exposure tables stay in Parquet or OED artifacts. This app holds
the control-plane record and the attribute-level lineage summary, not the rows.

The staging models at the end are the exception, and deliberately so. A source
extract's rows are neither large nor scientific: they are a few thousand
records a person has to query, filter and record review decisions against, and
holding them in an artifact would turn every one of those into a file download.
"""

from __future__ import annotations

from django.db import models

from apps.common.models import BaseModel, FreezableModel


class ExposureState(models.TextChoices):
    DRAFT = "draft", "Draft"
    """Editable business records; the source of truth until published."""

    VALIDATING = "validating", "Validating"
    VALIDATED = "validated", "Validated"
    PUBLISHED = "published", "Published"
    """Immutable OED artifacts exist and may be used by a run."""

    REJECTED = "rejected", "Rejected"


class ExposureVersion(BaseModel, FreezableModel):
    """One immutable version of a portfolio, published as OED artifacts."""

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="exposure_versions"
    )
    name = models.CharField(max_length=200)
    version = models.PositiveIntegerField(
        default=1, help_text="Increments within a project; a correction never edits in place."
    )
    state = models.CharField(
        max_length=16, choices=ExposureState.choices, default=ExposureState.DRAFT
    )

    oed_schema_version = models.CharField(max_length=32, blank=True)
    source_description = models.TextField(
        blank=True, help_text="Where the portfolio came from and the extract date."
    )
    valuation_date = models.DateField(null=True, blank=True)

    #: Currency handling. Section 15 names multiple currencies reaching the
    #: Oasis FM as a material risk, so the run currency and the evidence for
    #: any conversion are recorded before generation.
    run_currency = models.CharField(max_length=3, blank=True)
    currency_conversion_evidence = models.JSONField(
        default=dict,
        blank=True,
        help_text="Rates, valuation date, source and direction used to normalise values.",
    )

    #: Summaries the exposure workspace shows. Derived from the artifacts, held
    #: here so a dashboard need not open a Parquet file.
    location_count = models.IntegerField(default=0)
    account_count = models.IntegerField(default=0)
    total_tiv = models.DecimalField(max_digits=22, decimal_places=2, default=0)
    tiv_by_coverage = models.JSONField(default=dict, blank=True)
    tiv_by_country = models.JSONField(default=dict, blank=True)
    tiv_by_currency = models.JSONField(default=dict, blank=True)

    validation_report = models.JSONField(
        default=dict,
        blank=True,
        help_text="Findings, counts and summaries from the last validation pass.",
    )
    supported_perspectives = models.JSONField(
        default=list,
        blank=True,
        help_text="Perspectives the supplied files actually support.",
    )
    unmodelled_subperils = models.JSONField(default=list, blank=True)

    #: How this version was derived from a source extract: the batch it came
    #: from, the cohort selected, the allocation scenario and which attributes
    #: were not reported. Section 8 forbids an assumed attribute overwriting a
    #: reported one, and this is where the difference between the two is kept.
    source_lineage = models.JSONField(default=dict, blank=True)

    supersedes = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="superseded_by"
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name", "version"], name="unique_exposure_version"
            )
        ]
        indexes = [models.Index(fields=["project", "state"])]

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"

    @property
    def artifact_prefix(self) -> str:
        return f"{self.project.artifact_prefix}/exposure/{self.id}"

    @property
    def is_publishable(self) -> bool:
        """Section 8: a blocking validation finding stops publication."""
        report = self.validation_report or {}
        return self.state == ExposureState.VALIDATED and report.get("publishable") is True

    @property
    def is_usable_by_runs(self) -> bool:
        return self.state == ExposureState.PUBLISHED and self.is_frozen


class EnrichmentRun(BaseModel):
    """One traceable application of an assumption set to an exposure version.

    Section 5 requires observed, derived and imputed counts, confidence,
    exceptions and an output checksum. Section 15 requires record-, location-
    and portfolio-level TIV reconciliation before the run may be used.
    """

    exposure_version = models.ForeignKey(
        ExposureVersion, on_delete=models.CASCADE, related_name="enrichment_runs"
    )
    #: Null where the run used the baseline weights its model was built with and
    #: named no assumption set. The lineage is recorded either way.
    assumption_set = models.ForeignKey(
        "modelregistry.AssumptionSet",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="enrichment_runs",
    )

    #: Counts by evidence class, which is what makes the hierarchy auditable
    #: rather than a stated intention.
    reported_count = models.IntegerField(default=0)
    derived_count = models.IntegerField(default=0)
    corroborated_count = models.IntegerField(default=0)
    imputed_count = models.IntegerField(default=0)
    override_count = models.IntegerField(default=0)

    mean_confidence = models.FloatField(default=0.0)
    missingness_profile = models.JSONField(
        default=dict,
        blank=True,
        help_text="Field completeness before enrichment, by count and by value.",
    )
    attribute_lineage = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-attribute evidence class distribution and rule versions.",
    )

    #: The reconciliation evidence. An enrichment run is not usable until every
    #: scope balances.
    reconciliation = models.JSONField(default=dict, blank=True)
    reconciled = models.BooleanField(default=False)

    exceptions = models.JSONField(
        default=list,
        blank=True,
        help_text="Records that could not be enriched and the reason.",
    )
    output_checksum = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["exposure_version", "-created_at"])]

    def __str__(self) -> str:
        applied = self.assumption_set.reference if self.assumption_set else "baseline weights"
        return f"{self.exposure_version} under {applied}"

    @property
    def total_attributes(self) -> int:
        return (
            self.reported_count
            + self.derived_count
            + self.corroborated_count
            + self.imputed_count
            + self.override_count
        )

    @property
    def assumption_share(self) -> float:
        """Share of attribute values that are inferred rather than observed.

        Shown beside every result so a decision user can see how much of the
        answer rests on priors.
        """
        total = self.total_attributes
        if total == 0:
            return 0.0
        return (self.imputed_count + self.override_count) / total

    @property
    def is_usable(self) -> bool:
        return self.reconciled and bool(self.output_checksum)


class AttributeOverride(BaseModel):
    """A recorded analyst override of an enriched value.

    Section 8 requires the effect of an override to be recorded and the
    pre-override value retained for audit and comparison, so the previous value
    is a field rather than something to reconstruct from logs.
    """

    enrichment_run = models.ForeignKey(
        EnrichmentRun, on_delete=models.CASCADE, related_name="overrides"
    )
    location_reference = models.CharField(max_length=120)
    attribute = models.CharField(max_length=64)
    previous_value = models.CharField(max_length=200, blank=True)
    previous_evidence = models.CharField(max_length=32, blank=True)
    new_value = models.CharField(max_length=200)
    rationale = models.TextField()
    approved_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="attribute_overrides_approved",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["enrichment_run", "location_reference"])]

    def __str__(self) -> str:
        return f"{self.location_reference}.{self.attribute} -> {self.new_value}"


# -- the Klapton Re geocoded policy extract ----------------------------------
#
# Staging records for the two-sheet source workbook. Section 5 keeps rows out
# of the control plane where they are large scientific arrays; these are
# neither. They are 1,353 policy rows and 224 locations that a person has to
# be able to query, filter, review and correct decisions about, and putting
# them in an artifact would make every one of those a file download.
#
# They are staging, not exposure. Nothing here is an ExposureVersion: the
# import produces evidence about the source, and the mapping into OED is a
# later, separate step that must be able to fail without discarding the read.


class ImportState(models.TextChoices):
    PARSED = "parsed", "Parsed"
    """Read and profiled. Nothing has been promoted to an exposure version."""

    REJECTED = "rejected", "Rejected"
    """A blocking finding stands. The batch is kept as evidence of the attempt."""

    ACCEPTED = "accepted", "Accepted"
    """A person has reviewed the join report and cohorts and allowed the batch on."""


class ReviewState(models.TextChoices):
    NOT_REQUIRED = "not_required", "No review required"
    PENDING = "pending", "Awaiting review"
    CONFIRMED = "confirmed", "Confirmed as located"
    CORRECTED = "corrected", "Corrected"
    EXCLUDED = "excluded", "Excluded from modelling"


class ImportBatch(BaseModel):
    """One read of one source workbook.

    Holds what section 4.2 of the integration brief asks an ``ImportBatch`` to
    hold -- checksum, parser version, warnings and row counts -- plus the three
    rule versions that decided the outcome. A later rerun under changed rules
    produces a new batch rather than reinterpreting this one, so the versions
    are stored per batch and not looked up from the code that happens to be
    deployed when someone opens the screen.
    """

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="import_batches"
    )
    profile = models.CharField(
        max_length=120,
        help_text="The import profile that read this source, such as the Klapton Re "
        "geocoded policy extract.",
    )

    #: The immutable raw workbook. Registered before parsing, so a source that
    #: cannot be read is still retained as evidence of what was supplied.
    source_artifact = models.ForeignKey(
        "artifacts.Artifact",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="import_batches",
    )
    source_filename = models.CharField(max_length=255, blank=True)
    source_checksum = models.CharField(max_length=80, db_index=True)
    snapshot_date = models.DateField(null=True, blank=True)

    schema_version = models.CharField(max_length=80, blank=True)
    parser_version = models.CharField(max_length=32, blank=True)
    cohort_rule_version = models.CharField(max_length=32, blank=True)

    state = models.CharField(
        max_length=16, choices=ImportState.choices, default=ImportState.PARSED
    )

    policy_row_count = models.IntegerField(default=0)
    risk_row_count = models.IntegerField(default=0)

    findings = models.JSONField(
        default=list,
        blank=True,
        help_text="Type, required-value and cross-sheet findings raised while parsing.",
    )
    #: What the read found across the two sheets: the evidence tier every risk
    #: sits in, accounts named on one sheet and not the other, and whether
    #: anything blocks a promotion. It replaces the join report the retired
    #: two-sheet format needed, and it is much shorter -- the account reference
    #: is supplied on both sheets, so a mismatch is a contradiction between two
    #: stated facts rather than a reconstruction that did not converge.
    intake_report = models.JSONField(default=dict, blank=True)
    cohort_profile = models.JSONField(default=dict, blank=True)

    #: The exposure versions promoted out of this batch. A batch can produce
    #: more than one -- a cohort A benchmark and a multi-location sensitivity
    #: are different selections of the same read.
    exposure_versions = models.ManyToManyField(
        "exposure.ExposureVersion", blank=True, related_name="source_batches"
    )

    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="import_batches_accepted",
    )
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Re-reading the same file into the same project is idempotent: it
            # returns the batch that already exists rather than a second copy
            # whose counts a reader would have to reconcile against the first.
            models.UniqueConstraint(
                fields=["project", "source_checksum"], name="unique_import_per_source"
            )
        ]
        indexes = [models.Index(fields=["project", "state", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.profile} {self.source_filename or self.source_checksum[:16]}"

    @property
    def blocking(self) -> bool:
        """Whether a finding stands that stops the batch being promoted."""
        return bool((self.intake_report or {}).get("blocking"))

    @property
    def may_accept(self) -> bool:
        return self.state == ImportState.PARSED and not self.blocking


class SourcePolicyRow(BaseModel):
    """One row of the Policies sheet of a CASS intake template.

    The whole row is kept in ``values`` and ``raw``; the columns promoted to
    fields are the ones the importer filters and totals on. Section 5 keeps the
    reported text beside the typed value so no transformation silently replaces
    what was supplied.
    """

    batch = models.ForeignKey(
        ImportBatch, on_delete=models.CASCADE, related_name="policy_rows"
    )
    row_number = models.IntegerField(help_text="The spreadsheet row, counting the header.")

    policy_id = models.CharField(max_length=64, db_index=True)
    business_id = models.CharField(max_length=64, db_index=True)

    #: The policy's total insured value, at the share CASS writes. Optional:
    #: it is supplied only where the risks do not state their own values, and
    #: it is what the allocation scenarios divide. Never applied a second time
    #: as a share -- the values are already at the share CASS writes.
    policy_tiv = models.DecimalField(
        max_digits=22, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=8, blank=True)
    layer_number = models.IntegerField(null=True, blank=True)

    values = models.JSONField(default=dict, blank=True)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["row_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "business_id", "policy_id", "layer_number"],
                name="unique_policy_layer_per_batch",
            )
        ]
        indexes = [models.Index(fields=["batch", "business_id"])]

    def __str__(self) -> str:
        return self.policy_id


class SourceRiskLocation(BaseModel):
    """One row of the Risks sheet, with its cohort assignment.

    ``(business_id, location_number)`` is the natural key, and it is enforced
    here: a duplicate is a source defect, not something to absorb quietly. The
    reference is text because OED makes ``LocNumber`` text -- a schedule that
    numbers its sites "SITE-A" is ordinary, and an integer column would have
    read every one of them as zero.
    """

    batch = models.ForeignKey(
        ImportBatch, on_delete=models.CASCADE, related_name="location_rows"
    )
    row_number = models.IntegerField()

    business_id = models.CharField(max_length=64, db_index=True)
    location_number = models.CharField(max_length=64)
    primary_location = models.BooleanField(default=False)

    #: Stated where the risk knows what it is worth but not how that splits
    #: between coverages. Null where the risk states its coverages outright, and
    #: null where it states nothing and awaits an allocation -- ``values`` says
    #: which of the two, and they are different situations.
    total_insured_value = models.DecimalField(
        max_digits=22, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=8, blank=True)

    latitude = models.DecimalField(max_digits=11, decimal_places=8, null=True, blank=True)
    longitude = models.DecimalField(max_digits=12, decimal_places=8, null=True, blank=True)

    #: Storeys as the schedule stated them, or null where it did not.
    #:
    #: Worth more than it looks. Height decides the spectral period a structure
    #: responds at, so a risk that states it reaches GEM candidates at one
    #: measure and a risk that does not reaches them across four -- which no
    #: choice of damage bins can make into a single Oasis function. On this
    #: book a commercial reinforced-concrete risk with no storey count cannot
    #: be answered at all until the multi-IMT representation is approved; the
    #: same risk at two storeys resolves to PGA alone.
    storeys = models.IntegerField(null=True, blank=True)

    precision = models.CharField(max_length=32, blank=True)
    needs_review = models.BooleanField(default=False)
    class_of_business = models.CharField(max_length=120, blank=True)
    #: ISO code, as the template asks for it. A name has spellings and a code
    #: does not, and a wrong one routes a risk to the wrong national grid.
    country_code = models.CharField(max_length=2, blank=True)

    #: Section 4.4 asks for a deterministic location identifier. Derived from
    #: the source checksum and the natural key, so the same location in the
    #: same source is the same identifier on every read, in every project.
    cass_location_id = models.UUIDField(null=True, blank=True, db_index=True)

    cohort = models.CharField(max_length=16, db_index=True)
    cohort_reason = models.CharField(max_length=200, blank=True)
    cohort_rule_version = models.CharField(max_length=32, blank=True)

    review_state = models.CharField(
        max_length=16, choices=ReviewState.choices, default=ReviewState.NOT_REQUIRED
    )
    review_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="locations_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    values = models.JSONField(default=dict, blank=True)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["business_id", "location_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "business_id", "location_number"],
                name="unique_location_per_batch",
            )
        ]
        indexes = [
            models.Index(fields=["batch", "cohort"]),
            models.Index(fields=["batch", "review_state"]),
        ]

    def __str__(self) -> str:
        return f"{self.business_id}/{self.location_number}"

    @property
    def coordinate(self) -> str:
        if self.latitude is None or self.longitude is None:
            return ""
        return f"{self.latitude},{self.longitude}"


class DecisionField(models.TextChoices):
    """What a reviewer is allowed to decide about a staged location."""

    COHORT = "cohort", "Coordinate-quality cohort"
    REVIEW_STATE = "review_state", "Review outcome"
    STOREYS = "storeys", "Number of storeys"


class ReviewDecision(BaseModel):
    """One recorded decision a person made about one staged location.

    Append-only, and that is the whole design. The integration brief allows an
    analyst to change a cohort decision "only by recording a rationale", and
    says the change "creates a new derived exposure version; it does not edit
    the source artifact". Both halves matter: a staged row is what the source
    said, and a row edited in place would leave nothing able to answer what the
    workbook contained. So a decision is a separate record laid over the row,
    and promotion reads the overlay.

    The rationale is required at the database level rather than by a form. A
    reviewer's reason is the only thing that distinguishes a corrected geocode
    from a number somebody preferred, and it is what an auditor reads two years
    later when the loss is being questioned.
    """

    location = models.ForeignKey(
        SourceRiskLocation, on_delete=models.CASCADE, related_name="decisions"
    )
    field = models.CharField(max_length=32, choices=DecisionField.choices)
    #: Held as text whatever the field's type, because this is a record of what
    #: somebody decided rather than a typed value to compute with. The typed
    #: reading happens where the overlay is applied.
    previous_value = models.CharField(max_length=200, blank=True)
    new_value = models.CharField(max_length=200, blank=True)
    rationale = models.TextField()
    decided_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="review_decisions",
    )

    class Meta:
        ordering = ["location", "created_at"]
        indexes = [models.Index(fields=["location", "field"])]

    def __str__(self) -> str:
        return f"{self.location}: {self.field} -> {self.new_value}"
