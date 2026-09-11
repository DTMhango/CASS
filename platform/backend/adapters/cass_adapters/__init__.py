"""Engine adapters: the versioned boundary to OpenQuake and Oasis.

Section 4 requires every engine boundary to be crossed through a versioned
adapter over a supported API, and section 15 names what that prevents: custom
engine forks accumulating, and an upgrade breaking integration silently.

The contract lives in base.py. Concrete adapters arrive with the engine
integration phases of the roadmap; the compatibility gate is enforced now so
they cannot be written without one.
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

__version__ = "0.1.0"

__all__ = [
    "AdapterError",
    "EngineAdapter",
    "EngineJob",
    "EngineRejected",
    "EngineState",
    "EngineUnavailable",
    "EngineVersion",
    "IncompatibleEngine",
    "__version__",
]
