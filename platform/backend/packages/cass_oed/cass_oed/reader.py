"""Reading OED source files into typed records.

Parsing is kept apart from validation so that a malformed value produces a
finding an analyst can act on, rather than an exception that stops the import
at the first bad row. Section 8 requires the platform to present errors in
business language and let a user correct the business record, which is only
possible if every row is read.
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import io
from collections.abc import Iterator, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, BinaryIO, TextIO

from .schema import DataType, FieldSpec, FileKind, field_map

#: OED files are UTF-8; a byte-order mark from a spreadsheet export is common
#: enough that stripping it is part of reading rather than a failure.
ENCODING = "utf-8-sig"


@dataclasses.dataclass(slots=True)
class Row:
    """One source row, with raw text preserved beside the typed values.

    Section 5 requires raw exposure to be immutable and no transformation to
    silently replace a reported field, so the original text of every cell stays
    available for display and audit.
    """

    row_number: int
    raw: Mapping[str, str]
    values: dict[str, Any] = dataclasses.field(default_factory=dict)
    parse_errors: list[tuple[str, str, str]] = dataclasses.field(default_factory=list)
    """Tuples of (field, code, message) produced while coercing types."""

    def get(self, field: str, default: Any = None) -> Any:
        value = self.values.get(field)
        return default if value is None else value

    def text(self, field: str, default: str = "") -> str:
        raw = self.raw.get(field)
        return default if raw is None else raw.strip()


@dataclasses.dataclass(slots=True)
class ReadResult:
    """The outcome of reading one file."""

    kind: FileKind
    columns: tuple[str, ...]
    rows: list[Row]
    missing_columns: tuple[str, ...]
    unrecognised_columns: tuple[str, ...]

    def __iter__(self) -> Iterator[Row]:
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)


class OEDReadError(Exception):
    """Raised when a file cannot be read at all, such as an empty header."""


def _coerce(spec: FieldSpec, raw: str) -> tuple[Any, tuple[str, str] | None]:
    """Coerce one cell, returning the value and any (code, message) problem."""
    text = (raw or "").strip()
    if text == "":
        return None, None

    match spec.dtype:
        case DataType.TEXT | DataType.CURRENCY | DataType.PERIL:
            return text, None

        case DataType.FLAG:
            lowered = text.lower()
            if lowered in ("1", "y", "yes", "true", "t"):
                return True, None
            if lowered in ("0", "n", "no", "false", "f"):
                return False, None
            return None, ("invalid_code", f"{spec.business_label} must be yes or no.")

        case DataType.INTEGER:
            try:
                # Accept "3" and "3.0" from spreadsheet exports, reject "3.5".
                number = Decimal(text)
                if number != number.to_integral_value():
                    raise InvalidOperation
                return int(number), None
            except (InvalidOperation, ValueError):
                return None, ("invalid_number", f"{spec.business_label} must be a whole number.")

        case DataType.DECIMAL | DataType.MONEY | DataType.RATE | DataType.LATITUDE | DataType.LONGITUDE:
            try:
                return Decimal(text), None
            except (InvalidOperation, ValueError):
                return None, ("invalid_number", f"{spec.business_label} must be a number.")

        case DataType.DATE:
            try:
                return dt.date.fromisoformat(text), None
            except ValueError:
                return None, ("invalid_date", f"{spec.business_label} must be an ISO date.")

    return text, None


def read_stream(kind: FileKind, stream: TextIO) -> ReadResult:
    """Read an OED file from a text stream."""
    kind = FileKind(kind)
    specs = field_map(kind)

    reader = csv.DictReader(stream)
    if reader.fieldnames is None:
        raise OEDReadError(f"{kind.label} has no header row")

    columns = tuple(name.strip() for name in reader.fieldnames if name is not None)
    known = set(specs)
    present = set(columns)
    missing = tuple(sorted(name for name in known if name not in present))
    unrecognised = tuple(sorted(name for name in present if name not in known))

    rows: list[Row] = []
    for offset, raw_row in enumerate(reader, start=2):  # row 1 is the header
        cleaned = {
            (key.strip() if key else ""): (value if value is not None else "")
            for key, value in raw_row.items()
            if key is not None
        }
        row = Row(row_number=offset, raw=cleaned)
        for name, spec in specs.items():
            if name not in cleaned:
                continue
            value, problem = _coerce(spec, cleaned[name])
            row.values[name] = value
            if problem is not None:
                code, message = problem
                row.parse_errors.append((name, code, message))
        rows.append(row)

    return ReadResult(
        kind=kind,
        columns=columns,
        rows=rows,
        missing_columns=missing,
        unrecognised_columns=unrecognised,
    )


def read_bytes(kind: FileKind, payload: bytes) -> ReadResult:
    """Read an OED file held in memory."""
    return read_stream(kind, io.StringIO(payload.decode(ENCODING)))


def read_binary(kind: FileKind, stream: BinaryIO) -> ReadResult:
    """Read an OED file from the artifact store."""
    return read_bytes(kind, stream.read())


def read_path(kind: FileKind, path) -> ReadResult:
    """Read an OED file from disk. Used by fixtures and by the CLI."""
    with open(path, encoding=ENCODING, newline="") as handle:
        return read_stream(kind, handle)
