"""The model registry.

Section 5 defines the model version as the published calculation capability,
and section 16 confirms the fixed adaptive area-peril grid as the hazard
spatial basis. Section 6 adds the rule that governs this whole app: an
area-peril identifier must never silently change meaning, so geometry,
resolution, site-condition treatment and mapping logic are attributes of a grid
*version*, and changing any of them produces a new one.
"""

from __future__ import annotations

from django.db import models

from apps.common.models import BaseModel, FreezableModel
from cass_core.policy import IMTRepresentation


class Peril(models.TextChoices):
    EARTHQUAKE = "QEQ", "Earthquake shake"


class PublicationState(models.TextChoices):
    """Where an asset sits in the section 10 governance path."""

    DRAFT = "draft", "Draft"
    CANDIDATE = "candidate", "Candidate awaiting approval"
    APPROVED = "approved", "Approved"
    PUBLISHED = "published", "Published"
    SUPERSEDED = "superseded", "Superseded"
    WITHDRAWN = "withdrawn", "Withdrawn"

    @classmethod
    def usable_for_decisions(cls) -> list[str]:
        return [cls.PUBLISHED]


class IntensityMeasure(models.TextChoices):
    """The intensity measures the SA-first converter baseline addresses.

    Section 6 limits the initial converter to the SA family, starting with
    SA(0.3). PGA is listed because the GEM 2026 functions use it and the
    coverage report must name it, not because the converter emits it yet.
    """

    SA_03 = "SA(0.3)", "Spectral acceleration, 0.3 s"
    SA_06 = "SA(0.6)", "Spectral acceleration, 0.6 s"
    SA_10 = "SA(1.0)", "Spectral acceleration, 1.0 s"
    PGA = "PGA", "Peak ground acceleration"


#: The IMTs the converter can currently produce. Section 6 forbids treating the
#: SA-only package as a complete country model while material exposure maps to
#: PGA functions, so this set is checked at publication.
SUPPORTED_IMTS: frozenset[str] = frozenset(
    {IntensityMeasure.SA_03, IntensityMeasure.SA_06, IntensityMeasure.SA_10}
)


class AreaPerilGrid(BaseModel, FreezableModel):
    """A fixed, versioned, adaptive area-peril grid for one country."""

    country_code = models.CharField(max_length=2, db_index=True)
    version = models.CharField(
        max_length=32,
        help_text="Stable grid version. A change of geometry or mapping logic needs a new one.",
    )
    label = models.CharField(max_length=200)

    #: Adaptive resolution: finer in exposure centres and strong-gradient
    #: areas, coarser where exposure is sparse (section 6).
    base_resolution_deg = models.DecimalField(max_digits=8, decimal_places=6)
    refined_resolution_deg = models.DecimalField(max_digits=8, decimal_places=6)
    refinement_rule = models.TextField(
        help_text="How refined cells are selected, such as exposure density or hazard gradient."
    )

    cell_count = models.BigIntegerField(default=0)
    excludes_offshore = models.BooleanField(
        default=True,
        help_text="Indonesia's grid should avoid unnecessary calculation points over ocean.",
    )
    site_condition_source = models.CharField(
        max_length=200,
        blank=True,
        help_text="Provenance of Vs30, soil class and any basin parameters.",
    )
    site_condition_fallback = models.TextField(
        blank=True, help_text="Fallback hierarchy where site parameters are unavailable."
    )
    border_policy = models.TextField(
        blank=True,
        help_text="Treatment of locations near national borders and coastlines.",
    )
    mapping_tolerance_km = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        help_text="Distance beyond which a location is reported rather than snapped.",
    )

    publication_state = models.CharField(
        max_length=16, choices=PublicationState.choices, default=PublicationState.DRAFT
    )
    supersedes = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="superseded_by"
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["country_code", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["country_code", "version"], name="unique_grid_version"
            )
        ]

    def __str__(self) -> str:
        return f"{self.country_code} grid {self.version}"

    @property
    def reference(self) -> str:
        return f"{self.country_code.lower()}-grid-{self.version}"


class VulnerabilitySet(BaseModel, FreezableModel):
    """A versioned set of vulnerability functions with its provenance.

    Section 8 requires vulnerability versions to be approved independently from
    hazard versions, and section 10 requires a data-rights gate before use.
    ``licence_cleared`` is therefore a stored fact, not an assumption: the GEM
    public models are CC BY-NC-SA and commercial use is unconfirmed.
    """

    country_code = models.CharField(max_length=2, db_index=True)
    version = models.CharField(max_length=32)
    source = models.CharField(
        max_length=200, help_text="Provenance, such as GEM Global Vulnerability Model v2026.0.0."
    )
    source_commit = models.CharField(max_length=64, blank=True)
    taxonomy_generation = models.CharField(
        max_length=32,
        blank=True,
        help_text="Taxonomy release. Exposure and vulnerability generations must match.",
    )

    licence = models.CharField(max_length=120, blank=True)
    licence_cleared = models.BooleanField(
        default=False,
        help_text="Whether commercial use has been confirmed in writing by the licensor.",
    )
    licence_note = models.TextField(blank=True)

    function_count = models.IntegerField(default=0)
    imts_used = models.JSONField(
        default=list, help_text="Distinct intensity measures the functions demand."
    )
    imt_representation = models.CharField(
        max_length=32,
        choices=[(item.value, item.value) for item in IMTRepresentation],
        default=IMTRepresentation.UNDECIDED,
        help_text=(
            "Which section 6 multi-IMT representation this set was built under. "
            "A class spanning intensity measures is refused under 'undecided', "
            "so this is a gate rather than a label."
        ),
    )
    multi_channel_class_count = models.IntegerField(
        default=0,
        help_text=(
            "Classes that reach more than one intensity measure. Unroutable "
            "while the representation is undecided."
        ),
    )
    coverage_components = models.JSONField(
        default=list,
        help_text="Structural, non-structural, contents or business interruption.",
    )
    damage_bin_count = models.IntegerField(default=0)

    publication_state = models.CharField(
        max_length=16, choices=PublicationState.choices, default=PublicationState.DRAFT
    )

    class Meta:
        ordering = ["country_code", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["country_code", "version"], name="unique_vulnerability_version"
            )
        ]

    def __str__(self) -> str:
        return f"{self.country_code} vulnerability {self.version}"

    @property
    def unsupported_imts(self) -> list[str]:
        """IMTs these functions demand that the converter cannot yet produce."""
        return sorted(set(self.imts_used) - SUPPORTED_IMTS)

    @property
    def awaits_imt_representation(self) -> bool:
        """Whether part of this set cannot be routed until section 6 is decided.

        A set with no multi-channel class does not care what the representation
        is, so an undecided one is not a blocker for it. A set that has them is
        carrying functions no risk can currently reach.
        """
        return (
            self.multi_channel_class_count > 0
            and self.imt_representation == IMTRepresentation.UNDECIDED
        )


class HazardModel(BaseModel, FreezableModel):
    """An uploaded PSHA source model package, before any calculation is run.

    A published national model arrives as an archive of NRML and a ``job.ini``,
    and until now getting one into CASS meant an operator with a shell. This is
    the record of one that was uploaded: its files in the artifact store, its
    configuration parsed into editable parameters, and the licence somebody
    asserted about it.

    Distinct from ``HazardSet``, which is the *output* of running one. A model
    is uploaded once and run many times -- at different investigation times, on
    different grids, with different event set counts -- and each run is its own
    hazard set. Conflating them would make re-running a model at a longer
    return period look like acquiring a different model.
    """

    country_code = models.CharField(max_length=2, db_index=True)
    version = models.CharField(max_length=32)
    label = models.CharField(max_length=200)

    #: What the publisher calls it, and who they are. Neither is derivable from
    #: the archive: OpenQuake records that a source model was used, never whose.
    source_organisation = models.CharField(max_length=200, blank=True)
    publication_reference = models.CharField(max_length=300, blank=True)
    licence = models.CharField(max_length=120, blank=True)
    licence_cleared = models.BooleanField(default=False)
    licence_note = models.TextField(blank=True)

    archive_checksum = models.CharField(max_length=64, blank=True)
    archive_bytes = models.BigIntegerField(default=0)
    file_manifest = models.JSONField(
        default=list, help_text="Every file in the package, with its size and checksum."
    )

    #: The published job.ini, parsed. Held as JSON rather than reparsed on each
    #: request so the editor renders from one reading of the file.
    job_configuration = models.JSONField(default=dict)
    published_calculation_mode = models.CharField(max_length=32, blank=True)
    intensity_measures = models.JSONField(default=list)
    tectonic_regions = models.JSONField(default=list)
    estimated_realizations = models.IntegerField(default=0)
    logic_tree_summary = models.JSONField(default=dict)

    publication_state = models.CharField(
        max_length=16, choices=PublicationState.choices, default=PublicationState.DRAFT
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["country_code", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["country_code", "version"], name="unique_hazard_model_version"
            )
        ]

    def __str__(self) -> str:
        return f"{self.country_code} hazard model {self.version}"

    @property
    def reference(self) -> str:
        return f"{self.country_code.lower()}-hazmodel-{self.version}"

    @property
    def needs_conversion(self) -> bool:
        """Whether the published configuration has to change to make a footprint."""
        return self.published_calculation_mode != "event_based"

    @property
    def needs_sampling(self) -> bool:
        """Whether the logic tree has to be sampled down to one realisation."""
        return self.estimated_realizations > 1


class HazardJobSpec(BaseModel):
    """One configured run of an uploaded model, before it is executed.

    The thing the editor edits. It holds the operator's parameter choices and
    nothing else -- the model's own files and science stay on ``HazardModel``,
    so a spec can be revised, copied and compared without touching them.

    Kept after the run so a hazard set can always name the configuration that
    produced it, which is the only way two footprints from one model can be
    told apart.
    """

    model = models.ForeignKey(
        HazardModel, on_delete=models.PROTECT, related_name="job_specs"
    )
    name = models.CharField(max_length=200)
    grid = models.ForeignKey(
        AreaPerilGrid, on_delete=models.PROTECT, related_name="hazard_job_specs"
    )

    #: The operator's edits, as parameter name to value. Applied over the
    #: model's published configuration rather than replacing it, so a setting
    #: nobody touched keeps whatever the publisher chose.
    overrides = models.JSONField(default=dict)
    #: The configuration those edits produce, rendered and validated. Stored so
    #: a reviewer sees what would run rather than having to recompute it.
    resolved_configuration = models.JSONField(default=dict)
    conversion_report = models.JSONField(default=dict)
    site_join_report = models.JSONField(default=dict)
    problems = models.JSONField(default=list)

    is_runnable = models.BooleanField(default=False)
    job_checksum = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.model})"

    @property
    def blocking_problems(self) -> list[dict]:
        return [item for item in self.problems if item.get("severity") == "error"]


class HazardSet(BaseModel, FreezableModel):
    """A versioned event set and its footprints, with the calculation behind them.

    Hazard is versioned independently of vulnerability -- section 8 requires it,
    and the reason is that they move for different reasons: a new seismic source
    model changes the events, a new building-stock study changes the damage, and
    a release that could only bump both together would force a scientifically
    unnecessary revision of one every time the other moved.

    The fields that carry weight are the timing ones. ``investigation_time`` and
    ``stochastic_event_sets`` multiply to the effective time, which is the
    denominator of every annual rate computed from this set. Stored rather than
    recomputed, because an AAL derived from the wrong denominator is wrong by
    exactly that ratio and nothing downstream would notice.
    """

    country_code = models.CharField(max_length=2, db_index=True)
    version = models.CharField(max_length=32)
    label = models.CharField(max_length=200)

    source_model = models.CharField(
        max_length=200,
        help_text="The seismic source model, such as GEM Global Hazard Mosaic v2023.",
    )
    source_model_checksum = models.CharField(max_length=64, blank=True)
    ground_motion_models = models.JSONField(
        default=list, help_text="The GMPEs the logic tree used."
    )
    licence = models.CharField(max_length=120, blank=True)
    licence_cleared = models.BooleanField(default=False)
    licence_note = models.TextField(blank=True)

    grid = models.ForeignKey(
        AreaPerilGrid, on_delete=models.PROTECT, related_name="hazard_sets"
    )
    engine_version = models.CharField(max_length=32, blank=True)
    calculation_checksum = models.CharField(max_length=64, blank=True)
    job_checksum = models.CharField(max_length=64, blank=True)

    investigation_time = models.FloatField(default=0.0)
    stochastic_event_sets = models.IntegerField(default=0)
    event_count = models.IntegerField(default=0)
    cell_count = models.IntegerField(default=0)
    footprint_row_count = models.BigIntegerField(default=0)
    imts = models.JSONField(
        default=list, help_text="Intensity measures this hazard set carries."
    )

    #: Ground motion discarded for falling above the top intensity bin. Never
    #: benign: it is the strongest shaking the calculation produced, so every
    #: loss at those cells is understated by exactly the events that drive the
    #: tail.
    samples_above_range = models.BigIntegerField(default=0)
    conversion_report = models.JSONField(default=dict)

    publication_state = models.CharField(
        max_length=16, choices=PublicationState.choices, default=PublicationState.DRAFT
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["country_code", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["country_code", "version"], name="unique_hazard_version"
            )
        ]

    def __str__(self) -> str:
        return f"{self.country_code} hazard {self.version}"

    @property
    def reference(self) -> str:
        return f"{self.country_code.lower()}-hazard-{self.version}"

    @property
    def effective_time(self) -> float:
        """Years the event set represents, and so the number of Oasis periods."""
        return self.investigation_time * self.stochastic_event_sets

    @property
    def annual_event_rate(self) -> float:
        if self.effective_time <= 0:
            return 0.0
        return self.event_count / self.effective_time

    @property
    def clips_the_hazard(self) -> bool:
        return self.samples_above_range > 0

    def publication_blockers(self) -> list[str]:
        problems = []
        if not self.licence_cleared:
            problems.append(
                f"The {self.source_model} licence has not been cleared for use."
            )
        if self.clips_the_hazard:
            problems.append(
                f"{self.samples_above_range} ground-motion values fell above the top "
                "intensity bin and were discarded, so loss at those cells is "
                "understated. Widen the intensity dictionary and convert again."
            )
        if self.effective_time <= 0:
            problems.append(
                "No effective time is recorded, so no annual rate can be derived "
                "from this event set."
            )
        if not self.imts:
            problems.append("This hazard set carries no intensity measures.")
        return problems


class ModelVersion(BaseModel, FreezableModel):
    """A published calculation capability for one country and peril."""

    country_code = models.CharField(max_length=2, db_index=True)
    peril = models.CharField(max_length=8, choices=Peril.choices, default=Peril.EARTHQUAKE)
    version = models.CharField(max_length=32)
    label = models.CharField(max_length=200)

    grid = models.ForeignKey(AreaPerilGrid, on_delete=models.PROTECT, related_name="models")
    vulnerability_set = models.ForeignKey(
        VulnerabilitySet, on_delete=models.PROTECT, related_name="models"
    )

    #: Null until a hazard set exists for this country. A model version with
    #: no hazard can hold exposure, map keys and refuse to run, which is the
    #: honest state before the seismic sources arrive -- and better than a
    #: string field describing a hazard set nothing can load.
    hazard_set = models.ForeignKey(
        HazardSet,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="models",
    )
    hazard_source_model = models.CharField(max_length=200, blank=True)
    hazard_source_licence = models.CharField(max_length=120, blank=True)
    openquake_version = models.CharField(max_length=32, blank=True)
    oasis_version = models.CharField(max_length=32, blank=True)
    converter_version = models.CharField(max_length=32, blank=True)
    oed_schema_version = models.CharField(max_length=32, blank=True)

    imts = models.JSONField(
        default=list, help_text="Intensity measures this model version produces."
    )

    #: Section 9 requires a machine-readable scope statement covering primary
    #: shaking, site response and every relevant secondary peril, each marked
    #: included, proxied, excluded or not material, with expected bias.
    peril_scope = models.JSONField(
        default=dict,
        help_text="Sub-peril treatment: included, proxied, excluded or not_material, with rationale.",
    )
    known_limitations = models.TextField(blank=True)

    #: Section 6: the SA-only package reports supported and unsupported
    #: vulnerability classes and associated TIV, and cannot pass the release
    #: gate as a full country model while material exposure maps to PGA.
    unsupported_taxonomy_report = models.JSONField(
        default=dict,
        blank=True,
        help_text="Vulnerability classes and TIV share this version cannot model.",
    )
    is_research_prototype = models.BooleanField(
        default=True,
        help_text="Research prototypes may be run but not used for decisions.",
    )

    publication_state = models.CharField(
        max_length=16, choices=PublicationState.choices, default=PublicationState.DRAFT
    )
    published_at = models.DateTimeField(null=True, blank=True)
    validation_date = models.DateField(null=True, blank=True)
    supersedes = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="superseded_by"
    )

    class Meta:
        ordering = ["country_code", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["country_code", "peril", "version"], name="unique_model_version"
            )
        ]

    def __str__(self) -> str:
        return f"{self.country_code} {self.peril} {self.version}"

    @property
    def reference(self) -> str:
        return f"{self.country_code.lower()}-{self.peril.lower()}-{self.version}"

    @property
    def usable_for_decisions(self) -> bool:
        """Section 9 separates research runs from approved decision outputs."""
        return (
            self.publication_state == PublicationState.PUBLISHED
            and not self.is_research_prototype
        )

    def publication_blockers(self) -> list[str]:
        """Reasons this version may not be published as a full country model.

        Returned as a list rather than a boolean so the model catalogue can
        show an analyst exactly what is outstanding.
        """
        blockers: list[str] = []

        if not self.vulnerability_set.licence_cleared:
            blockers.append(
                "The vulnerability licence does not yet permit this use in writing."
            )
        unsupported = self.vulnerability_set.unsupported_imts
        if unsupported:
            blockers.append(
                "Vulnerability functions demand "
                + ", ".join(unsupported)
                + ", which the converter does not produce."
            )
        if self.vulnerability_set.awaits_imt_representation:
            blockers.append(
                f"{self.vulnerability_set.multi_channel_class_count} vulnerability "
                "classes respond at more than one intensity measure, and no "
                "multi-IMT representation has been approved. Risks reaching them "
                "are refused rather than approximated."
            )
        if self.grid.publication_state != PublicationState.PUBLISHED:
            blockers.append("The area-peril grid version is not published.")

        if self.hazard_set is None:
            blockers.append(
                "No hazard set is attached, so this version can map exposure to "
                "keys but cannot produce a loss."
            )
        else:
            blockers.extend(self.hazard_set.publication_blockers())
            missing = sorted(set(self.vulnerability_set.imts_used) - set(self.hazard_set.imts))
            if missing:
                blockers.append(
                    "The hazard set carries no "
                    + ", ".join(missing)
                    + ", which vulnerability functions in this version demand. A "
                    "function cannot be answered by ground motion it was not "
                    "built for."
                )
        if not self.peril_scope:
            blockers.append(
                "No peril scope statement has been recorded, so results cannot be "
                "labelled earthquake loss."
            )
        if not self.validation_date:
            blockers.append("No end-to-end validation date has been recorded.")

        share = (self.unsupported_taxonomy_report or {}).get("unsupported_tiv_share")
        if share is not None and float(share) > 0:
            blockers.append(
                f"{float(share):.1%} of benchmark TIV maps to vulnerability classes this "
                "version cannot model."
            )
        return blockers


class AssumptionSet(BaseModel, FreezableModel):
    """An approved exposure-enrichment policy.

    Section 8 requires at least three versioned sets in release one: Baseline,
    More Robust and More Vulnerable, so an analyst can compare approved
    alternatives rather than accept a single opaque answer.
    """

    class Flavour(models.TextChoices):
        BASELINE = "baseline", "Baseline"
        MORE_ROBUST = "more_robust", "More robust"
        MORE_VULNERABLE = "more_vulnerable", "More vulnerable"

    country_code = models.CharField(max_length=2, db_index=True)
    flavour = models.CharField(max_length=20, choices=Flavour.choices)
    version = models.CharField(max_length=32)
    label = models.CharField(max_length=200)
    segment = models.CharField(
        max_length=120,
        blank=True,
        help_text="Portfolio segment the set applies to, such as facultative commercial.",
    )

    #: Conditional priors keyed by the evidence they are conditioned on
    #: (country, Adm1, occupancy). Stored as data so a new assumption set is a
    #: governed record rather than a code change.
    rules = models.JSONField(default=dict)
    provenance = models.TextField(
        blank=True, help_text="Where the priors come from and how they were calibrated."
    )
    weighting_basis = models.CharField(
        max_length=32,
        choices=[
            ("replacement_cost", "Replacement cost"),
            ("floor_area", "Floor area"),
            ("building_count", "Building count"),
        ],
        default="replacement_cost",
        help_text="Section 8 prefers cost or area weighting over building counts for facultative risks.",
    )

    publication_state = models.CharField(
        max_length=16, choices=PublicationState.choices, default=PublicationState.DRAFT
    )

    class Meta:
        ordering = ["country_code", "flavour", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["country_code", "flavour", "version"],
                name="unique_assumption_set_version",
            )
        ]

    def __str__(self) -> str:
        return f"{self.country_code} {self.get_flavour_display()} {self.version}"

    @property
    def reference(self) -> str:
        return f"{self.flavour}-{self.version}"
