"""KRE OED source-input handling: schema, reading, validation and publication."""

from .perspectives import Perspective, available_perspectives, highest_available
from .reader import ReadResult, Row, read_bytes, read_path, read_stream
from .schema import OED_SCHEMA_VERSION, FileKind
from .validation import PortfolioFiles, ValidationReport, validate

__all__ = [
    "FileKind",
    "OED_SCHEMA_VERSION",
    "PortfolioFiles",
    "Perspective",
    "ReadResult",
    "Row",
    "ValidationReport",
    "available_perspectives",
    "highest_available",
    "read_bytes",
    "read_path",
    "read_stream",
    "validate",
]
