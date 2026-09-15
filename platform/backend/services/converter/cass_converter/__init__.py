"""CASS OpenQuake to Oasis converter.

The converter is a CASS product component, not an export script. Its public
contract is a conversion manifest and its output is a candidate Oasis model
package plus machine-readable validation evidence.

It will not run under an unapproved scientific policy. See policy.py.

Two halves. The footprint side turns OpenQuake ground motion into Oasis events
and intensity bins. The vulnerability side reads the GEM global vulnerability
model as published and discretises its beta loss-ratio distributions into the
same bin dictionaries -- ``gem`` for the reading, ``beta`` for the arithmetic,
``vulnerability`` for the choices the discretisation requires.

On top of those sits the enrichment: ``enrichment`` decides which GEM buildings
a Klapton Re risk might be and how much of that was assumed, ``pilot_enrichment``
holds the draft answers for the pilot countries, and ``model_build`` turns the
whole thing into the classes, channels and Oasis tables of one country release.

Beside those sit the measurements. ``qa`` and ``benchmark`` check a conversion
against its own arithmetic and against approved reference curves; ``reference``
builds the OpenQuake risk calculation that measures the whole representation --
binned intensity, discretised damage, measures carried as channels -- against
the engine's own answer for the same portfolio on the same events.
"""

from .beta import BetaError, cdf, partial_expectation, shape_parameters
from .bins import (
    Bin,
    BinError,
    DamageBinSet,
    IntensityBinSet,
    linear_bins,
    log_bins,
    oasis_damage_bins,
)
from .enrichment import (
    Attributes,
    Candidate,
    DesignEra,
    Enrichment,
    EnrichmentError,
    Evidence,
    Mixture,
    StockPrior,
    TaxonomyMapping,
    Weighting,
    apply_taxonomy_mapping,
    coverage,
    macro_class,
    mapping_coverage,
    mixture_report,
    read_stock_prior,
    read_taxonomy_mapping,
    resolve_all,
)
from .footprint import (
    ConversionMetrics,
    FootprintAccumulator,
    FootprintError,
    FootprintRow,
    GroundMotionBlock,
    GroundMotionSample,
    build_footprint,
    check_event_coverage,
    event_coverage,
    validate_footprint,
)
from .footprint_tables import (
    FootprintTables,
    FootprintTableWriter,
)
from .gem import (
    GemError,
    LossCategory,
    OccupancyClass,
    Taxonomy,
    VulnerabilityFunction,
    VulnerabilityModel,
    parse_taxonomy,
    read_country,
    read_model,
)
from .hazard_build import (
    HAZARD_BUILD_VERSION,
    HazardBuildError,
    HazardSet,
    build_hazard,
    footprint_csv,
    hazard_report,
    intensity_bins_csv,
    occurrence_csv,
    table_paths,
)
from .hazard_job import (
    HazardJob,
    HazardJobError,
    Site,
    job_checksum,
    job_ini,
    sites_csv,
    sites_from_cells,
)
from .identifiers import (
    DeterministicIdMap,
    EventLineage,
    IdentifierError,
    assign_event_ids,
)
from .model_build import (
    BuildError,
    Channel,
    ClassBuild,
    CountryBuild,
    build_country,
    build_report,
    dictionary,
    evidence_summary,
    mapping_csv,
    multi_imt_report,
    vulnerability_csv,
)
from .occurrence import (
    FrequencyCheck,
    OccurrenceError,
    OccurrenceRow,
    check_frequency,
    empty_period_share,
    validate_occurrences,
)
from .openquake import (
    CalculationMetadata,
    Event,
    OpenQuakeError,
    read_events,
    read_ground_motion,
    read_metadata,
    read_sites,
)
from .pilot_enrichment import PILOT_ENRICHMENTS, enrichment, load
from .policy import (
    ConversionPolicy,
    EventIdentity,
    IMTRepresentation,
    PolicyNotApproved,
)
from .vulnerability import (
    Component,
    DiscretisationError,
    DiscretisedFunction,
    Inadequacy,
    Placement,
    Reconstruction,
    Row,
    blend,
    build_table,
    check_reconstruction,
    damage_bins_to_csv,
    discretise,
    mixture_moments,
    partition_by_imt,
    reconstruction_failures,
    table_report,
    to_csv,
)

__version__ = "0.1.0"

__all__ = [
    "GroundMotionBlock",
    "table_paths",
    "event_coverage",
    "FootprintTableWriter",
    "FootprintTables",
    "load",
    "read_taxonomy_mapping",
    "mapping_coverage",
    "TaxonomyMapping",
    "apply_taxonomy_mapping",
    "Attributes",
    "BetaError",
    "Bin",
    "BinError",
    "BuildError",
    "Candidate",
    "Channel",
    "ClassBuild",
    "Component",
    "ConversionMetrics",
    "ConversionPolicy",
    "CountryBuild",
    "DamageBinSet",
    "DesignEra",
    "DeterministicIdMap",
    "DiscretisationError",
    "DiscretisedFunction",
    "Enrichment",
    "EnrichmentError",
    "EventIdentity",
    "EventLineage",
    "Evidence",
    "CalculationMetadata",
    "Event",
    "HAZARD_BUILD_VERSION",
    "HazardBuildError",
    "HazardJob",
    "HazardJobError",
    "HazardSet",
    "OpenQuakeError",
    "Site",
    "build_hazard",
    "footprint_csv",
    "hazard_report",
    "intensity_bins_csv",
    "job_checksum",
    "job_ini",
    "occurrence_csv",
    "read_events",
    "read_ground_motion",
    "read_metadata",
    "read_sites",
    "sites_csv",
    "sites_from_cells",
    "FootprintAccumulator",
    "FootprintError",
    "FootprintRow",
    "FrequencyCheck",
    "GemError",
    "GroundMotionSample",
    "IMTRepresentation",
    "IdentifierError",
    "Inadequacy",
    "IntensityBinSet",
    "LossCategory",
    "Mixture",
    "OccupancyClass",
    "OccurrenceError",
    "OccurrenceRow",
    "PILOT_ENRICHMENTS",
    "Placement",
    "PolicyNotApproved",
    "Reconstruction",
    "Row",
    "StockPrior",
    "Taxonomy",
    "VulnerabilityFunction",
    "VulnerabilityModel",
    "Weighting",
    "__version__",
    "assign_event_ids",
    "blend",
    "build_country",
    "build_footprint",
    "build_report",
    "build_table",
    "cdf",
    "check_event_coverage",
    "check_frequency",
    "check_reconstruction",
    "coverage",
    "damage_bins_to_csv",
    "dictionary",
    "discretise",
    "empty_period_share",
    "enrichment",
    "evidence_summary",
    "linear_bins",
    "log_bins",
    "macro_class",
    "mapping_csv",
    "mixture_moments",
    "mixture_report",
    "multi_imt_report",
    "oasis_damage_bins",
    "parse_taxonomy",
    "partial_expectation",
    "partition_by_imt",
    "read_country",
    "read_model",
    "read_stock_prior",
    "reconstruction_failures",
    "resolve_all",
    "shape_parameters",
    "table_report",
    "to_csv",
    "validate_footprint",
    "validate_occurrences",
    "vulnerability_csv",
]
