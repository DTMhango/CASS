"""KRE OpenQuake to Oasis converter.

The converter is a KRE product component, not an export script. Its public
contract is a conversion manifest and its output is a candidate Oasis model
package plus machine-readable validation evidence.

It will not run under an unapproved scientific policy. See policy.py.
"""

from .bins import Bin, BinError, DamageBinSet, IntensityBinSet, linear_bins, log_bins
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

__version__ = "0.1.0"

__all__ = [
    "Bin",
    "BinError",
    "ConversionMetrics",
    "ConversionPolicy",
    "DamageBinSet",
    "DeterministicIdMap",
    "EventIdentity",
    "EventLineage",
    "FootprintAccumulator",
    "FootprintError",
    "FootprintRow",
    "FrequencyCheck",
    "GroundMotionSample",
    "IMTRepresentation",
    "IdentifierError",
    "IntensityBinSet",
    "OccurrenceError",
    "OccurrenceRow",
    "PolicyNotApproved",
    "__version__",
    "assign_event_ids",
    "build_footprint",
    "check_event_coverage",
    "check_frequency",
    "empty_period_share",
    "linear_bins",
    "log_bins",
    "validate_footprint",
    "validate_occurrences",
]
