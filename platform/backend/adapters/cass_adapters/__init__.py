"""Engine adapters: the versioned boundary to OpenQuake and Oasis.

Section 4 requires every engine boundary to be crossed through a versioned
adapter over a supported API, and section 15 names what that prevents: custom
engine forks accumulating, and an upgrade breaking integration silently.

The contract lives in base.py and the compatibility gate is enforced there, so
no adapter can be written without one. ``oasis.py`` is the first concrete
implementation, covering the analysis half of section 5's pipeline. The
OpenQuake adapter arrives with the hazard phase of the roadmap.
"""

from .base import (
    AdapterError,
    EngineAdapter,
    EngineJob,
    EngineRejected,
    EngineState,
    EngineUnavailable,
    EngineVersion,
    IncompatibleEngine,
)
from .oasis import (
    AnalysisStatus,
    OasisAdapter,
    OasisModel,
    OasisPhase,
    PortfolioFileKind,
    analysis_state,
)

__version__ = "0.1.0"

__all__ = [
    "AdapterError",
    "AnalysisStatus",
    "EngineAdapter",
    "EngineJob",
    "EngineRejected",
    "EngineState",
    "EngineUnavailable",
    "EngineVersion",
    "IncompatibleEngine",
    "OasisAdapter",
    "OasisModel",
    "OasisPhase",
    "PortfolioFileKind",
    "__version__",
    "analysis_state",
]
