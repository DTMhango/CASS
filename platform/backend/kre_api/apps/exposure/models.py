"""Exposure versions and enrichment runs.

Section 5 defines the exposure version as an immutable input version and the
enrichment run as the traceable application of assumptions. Section 8 adds the
rules these models exist to hold: raw exposure is immutable, enriched exposure
is a new artifact linked to its source, and no transformation silently replaces
a reported field.

Large enriched exposure tables stay in Parquet or OED artifacts. This app holds
the control-plane record and the attribute-level lineage summary, not the rows.
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
    cedant = models.CharField(max_length=200, blank=True)
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
    assumption_set = models.ForeignKey(
        "modelregistry.AssumptionSet", on_delete=models.PROTECT, related_name="enrichment_runs"
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
        return f"{self.exposure_version} under {self.assumption_set.reference}"

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
