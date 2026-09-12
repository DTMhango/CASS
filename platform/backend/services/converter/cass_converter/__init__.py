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
from .footprint import (
    ConversionMetrics,
    FootprintAccumulator,
    FootprintError,
    FootprintRow,
    GroundMotionSample,
    build_footprint,
    check_event_coverage,
    validate_footprint,
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
from .identifiers import (
    DeterministicIdMap,
    EventLineage,
    IdentifierError,
    assign_event_ids,
)
from .occurrence import (
    FrequencyCheck,
    OccurrenceError,
    OccurrenceRow,
    check_frequency,
    empty_period_share,
    validate_occurrences,
)
from .policy import (
    ConversionPolicy,
    EventIdentity,
    IMTRepresentation,
    PolicyNotApproved,
)
from .vulnerability import (
    DiscretisationError,
    DiscretisedFunction,
    Inadequacy,
    Placement,
    Reconstruction,
    Row,
    build_table,
    check_reconstruction,
    damage_bins_to_csv,
    discretise,
    reconstruction_failures,
    table_report,
    to_csv,
)

__version__ = "0.1.0"

__all__ = [
    "Bin",
    "BinError",
    "BetaError",
    "ConversionMetrics",
    "ConversionPolicy",
    "DamageBinSet",
    "DeterministicIdMap",
    "DiscretisationError",
    "DiscretisedFunction",
    "EventIdentity",
    "EventLineage",
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
    "OccupancyClass",
    "OccurrenceError",
    "OccurrenceRow",
    "Placement",
    "PolicyNotApproved",
    "Reconstruction",
    "Row",
    "Taxonomy",
    "VulnerabilityFunction",
    "VulnerabilityModel",
    "__version__",
    "assign_event_ids",
    "build_footprint",
    "build_table",
    "cdf",
    "check_event_coverage",
    "check_frequency",
    "check_reconstruction",
    "damage_bins_to_csv",
    "discretise",
    "empty_period_share",
    "linear_bins",
    "log_bins",
    "oasis_damage_bins",
    "parse_taxonomy",
    "partial_expectation",
    "read_country",
    "read_model",
    "reconstruction_failures",
    "shape_parameters",
    "table_report",
    "to_csv",
    "validate_footprint",
    "validate_occurrences",
]
