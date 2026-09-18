"""Result sets and the caveats that must travel with them.

Section 5 defines the result set as the discoverable approved output. Section 9
adds the constraint that shapes this model: every decision view and export must
identify model version, valuation date, financial perspective, exposure
quality, assumption set, material exclusions and approval status, and
unapproved research runs must be operationally distinct from approved outputs.

A result therefore cannot be represented as a bag of numbers. The caveat fields
are part of the record, not an attachment.
"""

from __future__ import annotations

from django.db import models

from apps.common.models import BaseModel, FreezableModel


class ResultState(models.TextChoices):
    DRAFT = "draft", "Awaiting review"
    APPROVED = "approved", "Approved for decision use"
    RESEARCH = "research", "Research only"
    WITHDRAWN = "withdrawn", "Withdrawn"


class ResultSet(BaseModel, FreezableModel):
    """One published set of loss outputs from one analysis run."""

    run = models.ForeignKey(
        "runs.Run", on_delete=models.CASCADE, related_name="result_sets"
    )
    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="result_sets"
    )
    label = models.CharField(max_length=200)
    perspective = models.CharField(
        max_length=16,
        choices=[
            ("ground_up", "Ground-up loss"),
            ("insured", "Insured loss"),
            # Oasis's reinsurance stream is what is retained after the treaties,
            # not what they ceded.
            ("reinsurance", "Loss net of reinsurance"),
            # The same, computed by CASS with each catastrophe layer's
            # reinstatements and their premiums (ADR 24).
            ("ri_terms", "Loss net of reinsurance, cover limited by contract terms"),
        ],
    )
    #: For a result computed with limited cover: each layer's recoveries and
    #: premiums, the engine-basis check, and what the calculation assumed.
    cover_detail = models.JSONField(default=dict, blank=True)
    state = models.CharField(
        max_length=16, choices=ResultState.choices, default=ResultState.DRAFT
    )

    #: Headline metrics, held here so a dashboard need not open an ORD file.
    #: The full outputs remain artifacts.
    average_annual_loss = models.DecimalField(
        max_digits=22, decimal_places=2, null=True, blank=True
    )
    standard_deviation = models.DecimalField(
        max_digits=22, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=3, blank=True)
    return_period_losses = models.JSONField(
        default=dict, blank=True, help_text="Approved reporting return periods to loss."
    )

    #: Section 9 decision-use controls. These are required on export.
    model_version_reference = models.CharField(max_length=120, blank=True)
    assumption_set_reference = models.CharField(max_length=120, blank=True)
    #: The mode the run was made under, copied here rather than followed
    #: through the run: section 5.2 of the brief forbids mixing modes in one
    #: comparison, and a comparison reads results.
    run_mode = models.CharField(
        max_length=16,
        blank=True,
        help_text="What the run that produced this was for, and so what it may claim.",
    )
    #: How the losses were calculated, apart from the assumption set. Two runs
    #: of one book on one model differ by design in the vulnerability set their
    #: assumption set names and in the outputs they ask for; everything else in
    #: the settings decides the numbers. A scenario range only brings together
    #: results whose digests agree, and a result from before the digest was
    #: recorded carries none and cannot be shown to match anything.
    calculation_digest = models.CharField(
        max_length=80,
        blank=True,
        help_text="Digest of the settings that decided the losses, apart from the assumption set.",
    )
    valuation_date = models.DateField(null=True, blank=True)
    exposure_quality = models.JSONField(
        default=dict,
        blank=True,
        help_text="Completeness, assumption share and unmapped TIV behind this result.",
    )
    peril_scope = models.JSONField(
        default=dict, blank=True, help_text="Sub-peril treatment for this model release."
    )
    material_exclusions = models.JSONField(
        default=list,
        blank=True,
        help_text="What this number does not include, such as tsunami or business interruption.",
    )
    uncertainty_attribution = models.JSONField(
        default=dict,
        blank=True,
        help_text="Contribution of hazard, conversion, exposure, vulnerability and financial uncertainty.",
    )

    approved_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="results_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    retention_class = models.CharField(max_length=32, default="result")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "state", "-created_at"]),
            models.Index(fields=["run", "perspective"]),
        ]

    def __str__(self) -> str:
        return f"{self.label} ({self.get_perspective_display()})"

    @property
    def usable_for_decisions(self) -> bool:
        """Research output must never be mistaken for an approved answer."""
        return self.state == ResultState.APPROVED

    def export_caveats(self) -> dict[str, object]:
        """The block section 9 requires on every decision view and export."""
        return {
            "model_version": self.model_version_reference,
            "assumption_set": self.assumption_set_reference,
            "run_mode": self.run_mode,
            "valuation_date": self.valuation_date.isoformat() if self.valuation_date else None,
            "perspective": self.get_perspective_display(),
            "currency": self.currency,
            "approval_status": self.get_state_display(),
            "usable_for_decisions": self.usable_for_decisions,
            "exposure_quality": self.exposure_quality,
            "peril_scope": self.peril_scope,
            "material_exclusions": self.material_exclusions,
            "uncertainty_attribution": self.uncertainty_attribution,
            "cover_detail": self.cover_detail,
        }


class ResultComparison(BaseModel):
    """A saved comparison of two governed runs.

    Milestone M6 in section 13 is completing the analyst journey and comparing
    two governed runs, and section 9 requires model-change impact reports to
    compare benchmark AAL, EP curves and segments against the previous release.
    Both use this record.
    """

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="comparisons"
    )
    label = models.CharField(max_length=200)
    baseline = models.ForeignKey(
        ResultSet, on_delete=models.CASCADE, related_name="comparisons_as_baseline"
    )
    candidate = models.ForeignKey(
        ResultSet, on_delete=models.CASCADE, related_name="comparisons_as_candidate"
    )
    differences = models.JSONField(
        default=dict,
        blank=True,
        help_text="AAL, return-period and segment differences with the drivers identified.",
    )
    commentary = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.label

    @property
    def is_like_for_like(self) -> bool:
        """Whether the two results share a perspective and currency.

        Comparing a ground-up result against an insured one, or two currencies,
        produces a difference that means nothing. The interface uses this to
        refuse the comparison rather than draw a misleading chart.
        """
        return (
            self.baseline.perspective == self.candidate.perspective
            and self.baseline.currency == self.candidate.currency
        )
