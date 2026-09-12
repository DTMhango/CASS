"""What a read of any source format produces, before anything interprets it.

Two readers produce these: the intake reader, which reads the CASS template a
person fills in, and the legacy reader, which reads the retired two-sheet
extract for the one-off migration. Neither knows about the other, and neither
should own the shape they both return.

There is nothing here about a particular column, sheet or workbook. A row is a
row number, the text as it was written, and the values that text was read as. A
finding is a problem with one of them, in words a person can act on. A loading
API that never opens a spreadsheet produces the same records without importing
either reader.

Keeping the original text beside the typed value is the point of ``SourceRow``.
It is what makes it possible to say that a cell said ``1,000,000`` and was read
as ``1000000.00``, rather than only ever showing the reading.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from typing import Any


class ExtractReadError(Exception):
    """Raised when a file cannot be read as the format it claims to be.

    Distinct from a finding. A finding says a row is wrong; this says the file
    is not the thing at all, and there is nothing to report row by row.
    """


@dataclasses.dataclass(frozen=True, slots=True)
class Finding:
    """One problem with one cell or row, in the analyst's terms."""

    sheet: str
    row_number: int
    field: str
    code: str
    message: str
    #: The offending cell, so a person can find and correct it. Truncated by
    #: the readers rather than here: a finding is displayed and logged, and a
    #: whole paragraph pasted into a numeric cell should not travel with it.
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
