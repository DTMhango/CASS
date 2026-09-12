"""Reading the two sheets, independently and without joining them.

The integration brief is explicit about the order: parse both sheets into
staging records without changing the source values, validate types, required
identifiers and coordinate pairs, and only then produce a deterministic join
report. Joining during parsing is how a fan-out becomes invisible -- the rows
multiply before anyone has counted them.

So this module does one thing. It turns a workbook into two lists of rows that
keep their original values, plus the findings raised while coercing types.
Nothing here looks at the other sheet.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Iterator, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any, BinaryIO

from .schema import (
    LOCATION_FIELD_MAP,
    LOCATION_FIELDS,
    LOCATION_SHEET,
    POLICY_FIELD_MAP,
    POLICY_FIELDS,
    POLICY_SHEET,
    DataType,
    FieldSpec,
    is_not_applicable,
)


class ExtractReadError(Exception):
    """Raised when the workbook cannot be read as this extract at all."""


@dataclasses.dataclass(frozen=True, slots=True)
class Finding:
    """One problem with one cell or row, in the analyst's terms."""

    sheet: str
    row_number: int
    field: str
    code: str
    message: str
    #: Never the value itself for a confidential column: a finding is displayed
    #: and logged, and section 10 keeps counterparty names out of both.
    value: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(slots=True)
class SourceRow:
    """One parsed row, with the original text kept beside the typed values."""

    sheet: str
    row_number: int
    raw: dict[str, str]
    values: dict[str, Any]

    def get(self, field: str, default: Any = None) -> Any:
        value = self.values.get(field)
        return default if value is None else value

    def text(self, field: str, default: str = "") -> str:
        return self.raw.get(field, default)


@dataclasses.dataclass(slots=True)
class SheetRead:
    """What one sheet produced."""

    sheet: str
    columns: tuple[str, ...]
    rows: list[SourceRow]
    missing_columns: tuple[str, ...]
    unrecognised_columns: tuple[str, ...]
    findings: list[Finding]

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[SourceRow]:
        return iter(self.rows)


@dataclasses.dataclass(slots=True)
class ExtractRead:
    """Both sheets, parsed and still unjoined."""

    policies: SheetRead
    locations: SheetRead

    @property
    def findings(self) -> list[Finding]:
        return [*self.policies.findings, *self.locations.findings]

    @property
    def is_readable(self) -> bool:
        """Whether the shape is close enough to the contract to go on.

        A missing required column is fatal: the join, the cohorts and the TIV
        all depend on identifiers being present. Unrecognised extra columns are
        not -- a source system adding a field it did not have before is normal,
        and refusing the whole extract for it would be the wrong answer.
        """
        return not (self.policies.missing_columns or self.locations.missing_columns)


def read_workbook(source: BinaryIO | str) -> ExtractRead:
    """Read both sheets of the extract workbook."""
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise ExtractReadError(
            "Reading the extract needs openpyxl, which is not installed."
        ) from exc

    try:
        workbook = openpyxl.load_workbook(source, read_only=True, data_only=True)
    except Exception as exc:
        raise ExtractReadError(
            "The file could not be opened as a workbook. The extract is expected "
            "to be an .xlsx file with a Premium Policies and a Risk Locations sheet."
        ) from exc

    try:
        for sheet in (POLICY_SHEET, LOCATION_SHEET):
            if sheet not in workbook.sheetnames:
                raise ExtractReadError(
                    f"The workbook has no {sheet!r} sheet. It contains: "
                    + ", ".join(workbook.sheetnames)
                    + "."
                )
        return ExtractRead(
            policies=_read_sheet(workbook[POLICY_SHEET], POLICY_SHEET, POLICY_FIELDS),
            locations=_read_sheet(
                workbook[LOCATION_SHEET], LOCATION_SHEET, LOCATION_FIELDS
            ),
        )
    finally:
        workbook.close()


def read_rows(
    sheet: str, columns: Sequence[str], rows: Sequence[Sequence[Any]]
) -> SheetRead:
    """Read already-extracted cells. Used by tests and by a CSV fallback."""
    fields = POLICY_FIELDS if sheet == POLICY_SHEET else LOCATION_FIELDS
    return _read_values(sheet, list(columns), rows, fields)


def _read_sheet(worksheet, sheet: str, fields: tuple[FieldSpec, ...]) -> SheetRead:
    iterator = worksheet.iter_rows(values_only=True)
    try:
        header = next(iterator)
    except StopIteration:
        raise ExtractReadError(f"The {sheet!r} sheet is empty.") from None
    columns = [str(cell).strip() if cell is not None else "" for cell in header]
    return _read_values(sheet, columns, iterator, fields)


def _read_values(
    sheet: str,
    columns: list[str],
    rows,
    fields: tuple[FieldSpec, ...],
) -> SheetRead:
    field_map = POLICY_FIELD_MAP if sheet == POLICY_SHEET else LOCATION_FIELD_MAP
    known = {spec.name for spec in fields}
    missing = tuple(
        spec.name for spec in fields if spec.required and spec.name not in columns
    )
    unrecognised = tuple(name for name in columns if name and name not in known)

    parsed: list[SourceRow] = []
    findings: list[Finding] = []

    # Row 1 is the header, so the first data row is row 2. Findings quote the
    # spreadsheet row a person would scroll to, not a zero-based index.
    for offset, values in enumerate(rows, start=2):
        if values is None or all(cell is None or cell == "" for cell in values):
            continue
        raw: dict[str, str] = {}
        typed: dict[str, Any] = {}
        for position, column in enumerate(columns):
            if not column:
                continue
            cell = values[position] if position < len(values) else None
            raw[column] = "" if cell is None else str(cell).strip()
            spec = field_map.get(column)
            if spec is None:
                continue
            value, finding = _coerce(sheet, offset, spec, cell)
            if finding is not None:
                findings.append(finding)
            typed[column] = value

        for spec in fields:
            if spec.required and typed.get(spec.name) in (None, ""):
                findings.append(
                    Finding(
                        sheet=sheet,
                        row_number=offset,
                        field=spec.name,
                        code="missing_value",
                        message=f"{spec.business_label} is required and was not supplied.",
                    )
                )
        parsed.append(SourceRow(sheet=sheet, row_number=offset, raw=raw, values=typed))

    return SheetRead(
        sheet=sheet,
        columns=tuple(columns),
        rows=parsed,
        missing_columns=missing,
        unrecognised_columns=unrecognised,
        findings=findings,
    )


def _coerce(
    sheet: str, row_number: int, spec: FieldSpec, cell: Any
) -> tuple[Any, Finding | None]:
    """Turn one cell into its typed value, or explain why it could not be."""
    if cell is None or (isinstance(cell, str) and not cell.strip()):
        return None, None

    # A field the source marks as not applicable is absent, not corrupt. The
    # renewal columns carry an em dash on every policy that was not renewed;
    # treating those as unreadable numbers would raise four thousand findings
    # and bury the handful that mean something. A required field is exempt:
    # there, "not applicable" is itself the problem, and the missing-value
    # check below reports it.
    if not spec.required and is_not_applicable(cell):
        return None, None

    def bad(message: str) -> tuple[None, Finding]:
        return None, Finding(
            sheet=sheet,
            row_number=row_number,
            field=spec.name,
            code="unreadable_value",
            message=message,
            # A confidential cell's contents are not repeated into a finding.
            value="" if spec.is_confidential else str(cell)[:120],
        )

    match spec.dtype:
        case DataType.TEXT:
            return str(cell).strip(), None

        case DataType.INTEGER:
            try:
                return int(str(cell).strip()), None
            except (TypeError, ValueError):
                return bad(f"{spec.business_label} is not a whole number.")

        case DataType.MONEY:
            # Decimal from the string form: the exact reconciliation the brief
            # requires cannot survive a float.
            try:
                return Decimal(str(cell).strip()), None
            except (InvalidOperation, TypeError, ValueError):
                return bad(f"{spec.business_label} is not an amount.")

        case DataType.NUMBER:
            try:
                return Decimal(str(cell).strip()), None
            except (InvalidOperation, TypeError, ValueError):
                return bad(f"{spec.business_label} is not a number.")

        case DataType.COORDINATE:
            try:
                value = Decimal(str(cell).strip())
            except (InvalidOperation, TypeError, ValueError):
                return bad(f"{spec.business_label} is not a coordinate.")
            limit = Decimal(90) if "latitude" in spec.name.lower() else Decimal(180)
            if not -limit <= value <= limit:
                return bad(
                    f"{spec.business_label} of {value} is outside the valid range."
                )
            return value, None

        case DataType.YES_NO:
            text = str(cell).strip().lower()
            if text in ("yes", "y", "true", "1"):
                return True, None
            if text in ("no", "n", "false", "0"):
                return False, None
            return bad(f"{spec.business_label} should be Yes or No.")

        case DataType.DATE:
            if isinstance(cell, dt.datetime):
                return cell.date(), None
            if isinstance(cell, dt.date):
                return cell, None
            try:
                return dt.date.fromisoformat(str(cell).strip()[:10]), None
            except ValueError:
                return bad(f"{spec.business_label} is not a date.")

    return str(cell).strip(), None  # pragma: no cover - every type is handled


def masked(row: Mapping[str, Any], *, include_confidential: bool = False) -> dict[str, Any]:
    """A row with counterparty detail removed unless it was asked for.

    Used wherever a row leaves the importer -- a manifest, a log line, a support
    bundle. Reading the sensitivity off the schema rather than keeping a second
    list here is what stops the two drifting apart.
    """
    from .schema import CONFIDENTIAL_COLUMNS

    if include_confidential:
        return dict(row)
    return {
        key: value for key, value in row.items() if key not in CONFIDENTIAL_COLUMNS
    }
