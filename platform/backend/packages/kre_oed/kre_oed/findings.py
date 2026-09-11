"""Validation findings expressed in business language.

Build plan section 8 requires errors to be presented in business language, and
section 3 requires an analyst to be able to return to the business record that
caused a failure. A finding therefore carries three things a raw parser error
does not: what the analyst should do about it, which record it belongs to, and
whether it blocks publication or merely warrants review.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Iterable, Mapping
from typing import Any


class Severity(enum.StrEnum):
    ERROR = "error"
    """Blocks publication of the exposure version."""

    WARNING = "warning"
    """Permitted, but must be visible and may require explicit approval."""

    INFO = "info"
    """Recorded for transparency; no action required."""


@dataclasses.dataclass(frozen=True, slots=True)
class Finding:
    """One validation result tied to a record an analyst can open."""

    code: str
    severity: Severity
    message: str
    remediation: str
    file_kind: str
    row_number: int | None = None
    field: str | None = None
    value: Any = None
    record_key: str | None = None

    @property
    def blocking(self) -> bool:
        return self.severity is Severity.ERROR

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": str(self.severity),
            "message": self.message,
            "remediation": self.remediation,
            "file_kind": self.file_kind,
            "row_number": self.row_number,
            "field": self.field,
            "value": None if self.value is None else str(self.value),
            "record_key": self.record_key,
        }


@dataclasses.dataclass(slots=True)
class FindingSet:
    """The findings from one validation pass, with convenience summaries."""

    findings: list[Finding] = dataclasses.field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def extend(self, findings: Iterable[Finding]) -> None:
        self.findings.extend(findings)

    def __iter__(self):
        return iter(self.findings)

    def __len__(self) -> int:
        return len(self.findings)

    @property
    def errors(self) -> list[Finding]:
        return [item for item in self.findings if item.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [item for item in self.findings if item.severity is Severity.WARNING]

    @property
    def blocking(self) -> bool:
        """True where at least one finding prevents publication."""
        return any(item.blocking for item in self.findings)

    def by_code(self) -> Mapping[str, int]:
        counts: dict[str, int] = {}
        for item in self.findings:
            counts[item.code] = counts.get(item.code, 0) + 1
        return dict(sorted(counts.items()))

    def as_dict(self) -> dict[str, Any]:
        return {
            "blocking": self.blocking,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "counts_by_code": self.by_code(),
            "findings": [item.as_dict() for item in self.findings],
        }


#: Every code the validator can emit, with the analyst-facing remediation.
#: Keeping them in one table means an error message is a governed string rather
#: than an incidental one, and the exposure workspace can group by cause.
CATALOGUE: Mapping[str, tuple[Severity, str]] = {
    "missing_column": (
        Severity.ERROR,
        "Add the column to the source file or map it in the import template.",
    ),
    "unrecognised_column": (
        Severity.WARNING,
        "KRE did not interpret this column. Remove it or request schema support.",
    ),
    "missing_value": (
        Severity.ERROR,
        "Supply the value in the source record, or ask the cedant to confirm it.",
    ),
    "invalid_number": (
        Severity.ERROR,
        "Correct the value to a number without currency symbols or thousands separators.",
    ),
    "out_of_range": (
        Severity.ERROR,
        "Correct the value so it falls inside the permitted range for this field.",
    ),
    "invalid_date": (
        Severity.ERROR,
        "Use an ISO date of the form YYYY-MM-DD.",
    ),
    "invalid_code": (
        Severity.ERROR,
        "Use one of the values KRE supports for this field.",
    ),
    "duplicate_key": (
        Severity.ERROR,
        "Give each record a unique reference, or merge the duplicate rows.",
    ),
    "orphan_reference": (
        Severity.ERROR,
        "Add the referenced record, or correct the reference on this row.",
    ),
    "no_tiv": (
        Severity.ERROR,
        "Supply at least one insured value for the location.",
    ),
    "negative_tiv": (
        Severity.ERROR,
        "Insured values cannot be negative. Correct the reported value.",
    ),
    "zero_coordinates": (
        Severity.ERROR,
        "Null island coordinates indicate a geocoding failure. Re-geocode the address.",
    ),
    "mixed_currency": (
        Severity.WARNING,
        "Normalise to the run currency with a governed rate before generating Oasis files.",
    ),
    "unmodelled_subperil": (
        Severity.WARNING,
        "The policy covers a sub-peril this release does not model. Confirm the scope caveat.",
    ),
    "no_modelled_peril": (
        Severity.WARNING,
        "No modelled peril applies to this location, so it will contribute no loss.",
    ),
    "unsupported_financial_term": (
        Severity.ERROR,
        "Remove the term, or record an approved treatment before running an insured perspective.",
    ),
    "layer_gap": (
        Severity.WARNING,
        "Layer numbers are not contiguous. Confirm the programme structure is intended.",
    ),
    "inuring_gap": (
        Severity.WARNING,
        "Inuring priorities are not contiguous. Confirm the reinsurance order is intended.",
    ),
    "scope_matches_nothing": (
        Severity.ERROR,
        "Correct the scope filter so the contract covers at least one risk in the portfolio.",
    ),
    "building_id_reused": (
        Severity.ERROR,
        "Give each building at a location a distinct building number.",
    ),
}


def make(
    code: str,
    message: str,
    *,
    file_kind: str,
    row_number: int | None = None,
    field: str | None = None,
    value: Any = None,
    record_key: str | None = None,
    severity: Severity | None = None,
) -> Finding:
    """Build a finding from the catalogue, so remediation text stays governed."""
    try:
        default_severity, remediation = CATALOGUE[code]
    except KeyError as exc:
        raise KeyError(f"{code!r} is not a registered validation code") from exc
    return Finding(
        code=code,
        severity=severity or default_severity,
        message=message,
        remediation=remediation,
        file_kind=file_kind,
        row_number=row_number,
        field=field,
        value=value,
        record_key=record_key,
    )
