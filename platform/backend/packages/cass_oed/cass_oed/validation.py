"""Portfolio validation across the four OED source files.

This implements the checks build plan section 8 lists as gates before an
exposure version may be published: identifiers, hierarchy, code lists, dates,
coordinates, currencies, TIV and financial terms, each reported against the
business record that caused it.

The validator never repairs data. Section 5 makes raw exposure immutable, so a
correction is a new version created by a person, not a silent normalisation
here.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from . import findings as fnd
from .findings import FindingSet, Severity
from .reader import ReadResult, Row
from .schema import (
    COVERAGE_TYPES,
    EARTHQUAKE_PERILS,
    KEY_FIELDS,
    MODELLED_SUBPERILS,
    FileKind,
    expand_perils,
    field_map,
    tiv_columns,
    unmodelled_subperils,
)

#: OED financial term columns CASS does not yet calculate. Section 15 requires
#: unsupported terms to be blocked or explicitly approved rather than silently
#: approximated, so presence of a non-empty value here is an error.
UNSUPPORTED_TERM_COLUMNS: frozenset[str] = frozenset(
    {
        "StepTriggerType",
        "PolStepTrigger",
        "CondTag",
        "CondNumber",
    }
)

#: Each financial amount and the basis column OED requires beside it. An amount
#: with no basis is refused inside the engine's own file reader, several stages
#: after a person could have been told about it, so it is checked here.
TERM_BASIS: Mapping[str, tuple[str, str]] = {
    "LocDed6All": ("LocDedType6All", "LocPeril"),
    "LocLimit6All": ("LocLimitType6All", "LocPeril"),
    "PolDed6All": ("PolDedType6All", "PolPeril"),
    "PolLimit6All": ("PolLimitType6All", "PolPeril"),
}


@dataclasses.dataclass(slots=True)
class PortfolioFiles:
    """The source files presented together for validation."""

    location: ReadResult
    account: ReadResult | None = None
    reins_info: ReadResult | None = None
    reins_scope: ReadResult | None = None

    def present(self) -> dict[FileKind, ReadResult]:
        mapping = {FileKind.LOCATION: self.location}
        if self.account is not None:
            mapping[FileKind.ACCOUNT] = self.account
        if self.reins_info is not None:
            mapping[FileKind.REINS_INFO] = self.reins_info
        if self.reins_scope is not None:
            mapping[FileKind.REINS_SCOPE] = self.reins_scope
        return mapping


@dataclasses.dataclass(slots=True)
class ValidationReport:
    """Findings plus the summaries the exposure workspace displays."""

    findings: FindingSet
    tiv_by_coverage: dict[str, Decimal]
    tiv_by_currency: dict[str, Decimal]
    tiv_by_country: dict[str, Decimal]
    location_count: int
    account_count: int
    currencies: tuple[str, ...]
    modelled_subperils: tuple[str, ...]
    unmodelled_subperils: tuple[str, ...]

    @property
    def total_tiv(self) -> Decimal:
        return sum(self.tiv_by_coverage.values(), Decimal(0))

    @property
    def publishable(self) -> bool:
        """Section 8: a blocking finding stops publication of the version."""
        return not self.findings.blocking

    def as_dict(self) -> dict[str, Any]:
        return {
            "publishable": self.publishable,
            "location_count": self.location_count,
            "account_count": self.account_count,
            "total_tiv": str(self.total_tiv),
            "tiv_by_coverage": {k: str(v) for k, v in self.tiv_by_coverage.items()},
            "tiv_by_currency": {k: str(v) for k, v in self.tiv_by_currency.items()},
            "tiv_by_country": {k: str(v) for k, v in self.tiv_by_country.items()},
            "currencies": list(self.currencies),
            "modelled_subperils": list(self.modelled_subperils),
            "unmodelled_subperils": list(self.unmodelled_subperils),
            "validation": self.findings.as_dict(),
        }


def _record_key(kind: FileKind, row: Row) -> str:
    parts = [
        f"{name}={row.text(name)}"
        for name in KEY_FIELDS[kind]
        if row.text(name) != ""
    ]
    return "; ".join(parts) or f"row {row.row_number}"


def _check_columns(result: ReadResult, out: FindingSet) -> None:
    specs = field_map(result.kind)
    for name in result.missing_columns:
        spec = specs[name]
        severity = Severity.ERROR if spec.required else Severity.INFO
        out.add(
            fnd.make(
                "missing_column",
                f"{result.kind.label} has no {spec.business_label} column.",
                file_kind=str(result.kind),
                field=name,
                severity=severity,
            )
        )
    for name in result.unrecognised_columns:
        out.add(
            fnd.make(
                "unrecognised_column",
                f"{result.kind.label} column {name} was not interpreted by CASS.",
                file_kind=str(result.kind),
                field=name,
            )
        )


def _check_rows(result: ReadResult, out: FindingSet) -> None:
    """Field-level checks: parse errors, requiredness, ranges and code lists."""
    specs = field_map(result.kind)
    for row in result.rows:
        key = _record_key(result.kind, row)

        for field, code, message in row.parse_errors:
            out.add(
                fnd.make(
                    code,
                    message,
                    file_kind=str(result.kind),
                    row_number=row.row_number,
                    field=field,
                    value=row.text(field),
                    record_key=key,
                )
            )

        for name, spec in specs.items():
            if name in result.missing_columns:
                continue
            value = row.values.get(name)
            text = row.text(name)

            if spec.required and (value is None or text == ""):
                out.add(
                    fnd.make(
                        "missing_value",
                        f"{spec.business_label} is required and was not supplied.",
                        file_kind=str(result.kind),
                        row_number=row.row_number,
                        field=name,
                        record_key=key,
                    )
                )
                continue

            if value is None:
                continue

            if spec.allowed is not None and str(value).upper() not in spec.allowed:
                out.add(
                    fnd.make(
                        "invalid_code",
                        f"{spec.business_label} value {value} is not supported by CASS. "
                        f"Supported values are {', '.join(spec.allowed)}.",
                        file_kind=str(result.kind),
                        row_number=row.row_number,
                        field=name,
                        value=value,
                        record_key=key,
                    )
                )

            if isinstance(value, int | Decimal) and not isinstance(value, bool):
                numeric = Decimal(value)
                if spec.minimum is not None and numeric < Decimal(str(spec.minimum)):
                    out.add(
                        fnd.make(
                            "out_of_range",
                            f"{spec.business_label} is {value}, below the permitted minimum "
                            f"of {spec.minimum}.",
                            file_kind=str(result.kind),
                            row_number=row.row_number,
                            field=name,
                            value=value,
                            record_key=key,
                        )
                    )
                if spec.maximum is not None and numeric > Decimal(str(spec.maximum)):
                    out.add(
                        fnd.make(
                            "out_of_range",
                            f"{spec.business_label} is {value}, above the permitted maximum "
                            f"of {spec.maximum}.",
                            file_kind=str(result.kind),
                            row_number=row.row_number,
                            field=name,
                            value=value,
                            record_key=key,
                        )
                    )

        for amount, (basis, peril) in TERM_BASIS.items():
            if amount not in specs or _is_blank_amount(row.text(amount)):
                continue
            for companion, why in (
                (basis, "says whether it is a flat amount or a percentage"),
                (peril, "says which peril it is written against"),
            ):
                if companion in specs and row.text(companion) == "":
                    out.add(
                        fnd.make(
                            "missing_term_basis",
                            f"{specs[amount].business_label} carries a value but "
                            f"{companion}, which {why}, is empty. OED requires the two "
                            "together and the engine refuses the file without it.",
                            file_kind=str(result.kind),
                            row_number=row.row_number,
                            field=companion,
                            value=row.text(amount),
                            record_key=key,
                        )
                    )

        for name in UNSUPPORTED_TERM_COLUMNS:
            if row.text(name) != "":
                out.add(
                    fnd.make(
                        "unsupported_financial_term",
                        f"{name} carries a value CASS does not calculate in this release.",
                        file_kind=str(result.kind),
                        row_number=row.row_number,
                        field=name,
                        value=row.text(name),
                        record_key=key,
                    )
                )



def _is_blank_amount(text: str) -> bool:
    """Whether a money column holds nothing OED would call a term.

    An empty cell and a zero are both "no term here". A zero deductible needs
    no basis and no peril, and demanding them would report every untermed row.
    """
    stripped = text.strip()
    if not stripped:
        return True
    try:
        return Decimal(stripped) == 0
    except ArithmeticError:
        return False


def _check_keys(result: ReadResult, out: FindingSet) -> None:
    """Identifier uniqueness at the level the file is keyed on."""
    key_fields = KEY_FIELDS[result.kind]
    if not key_fields:
        return
    seen: dict[tuple[str, ...], int] = {}
    for row in result.rows:
        key = tuple(row.text(name) for name in key_fields)
        if all(part == "" for part in key):
            continue
        if key in seen:
            code = (
                "building_id_reused"
                if result.kind is FileKind.LOCATION
                else "duplicate_key"
            )
            out.add(
                fnd.make(
                    code,
                    f"{result.kind.label} row repeats the reference already used on row "
                    f"{seen[key]}.",
                    file_kind=str(result.kind),
                    row_number=row.row_number,
                    value="; ".join(key),
                    record_key=_record_key(result.kind, row),
                )
            )
        else:
            seen[key] = row.row_number


def _check_locations(result: ReadResult, out: FindingSet) -> None:
    """Location checks that cannot be expressed as single-field rules."""
    coverage_columns = tiv_columns()
    for row in result.rows:
        key = _record_key(FileKind.LOCATION, row)

        values = [row.get(name) for name in coverage_columns]
        supplied = [value for value in values if value is not None]
        total = sum((Decimal(value) for value in supplied), Decimal(0))
        if not supplied or total <= 0:
            out.add(
                fnd.make(
                    "no_tiv",
                    "The location carries no insured value, so it cannot produce loss.",
                    file_kind=str(FileKind.LOCATION),
                    row_number=row.row_number,
                    record_key=key,
                )
            )
        for name, value in zip(coverage_columns, values, strict=False):
            if value is not None and Decimal(value) < 0:
                out.add(
                    fnd.make(
                        "negative_tiv",
                        f"{name} is negative.",
                        file_kind=str(FileKind.LOCATION),
                        row_number=row.row_number,
                        field=name,
                        value=value,
                        record_key=key,
                    )
                )

        latitude, longitude = row.get("Latitude"), row.get("Longitude")
        if latitude is not None and longitude is not None:
            if Decimal(latitude) == 0 and Decimal(longitude) == 0:
                out.add(
                    fnd.make(
                        "zero_coordinates",
                        "Coordinates are 0, 0, which indicates a failed geocode "
                        "rather than a location in the Gulf of Guinea.",
                        file_kind=str(FileKind.LOCATION),
                        row_number=row.row_number,
                        field="Latitude",
                        record_key=key,
                    )
                )

        covered = expand_perils(row.text("LocPerilsCovered"))
        modelled = [code for code in covered if code in MODELLED_SUBPERILS]
        unmodelled = unmodelled_subperils(covered)
        if not modelled:
            out.add(
                fnd.make(
                    "no_modelled_peril",
                    "No sub-peril on this location is modelled by the earthquake release.",
                    file_kind=str(FileKind.LOCATION),
                    row_number=row.row_number,
                    field="LocPerilsCovered",
                    value=row.text("LocPerilsCovered"),
                    record_key=key,
                )
            )
        if unmodelled:
            labels = ", ".join(EARTHQUAKE_PERILS[code] for code in unmodelled)
            out.add(
                fnd.make(
                    "unmodelled_subperil",
                    f"The location covers {labels}, which this release does not model.",
                    file_kind=str(FileKind.LOCATION),
                    row_number=row.row_number,
                    field="LocPerilsCovered",
                    value=row.text("LocPerilsCovered"),
                    record_key=key,
                )
            )


def _check_account_hierarchy(files: PortfolioFiles, out: FindingSet) -> None:
    """Locations must resolve to an account when an account file is supplied."""
    if files.account is None:
        return
    accounts = {
        (row.text("PortNumber"), row.text("AccNumber")) for row in files.account.rows
    }
    for row in files.location.rows:
        reference = (row.text("PortNumber"), row.text("AccNumber"))
        if reference not in accounts:
            out.add(
                fnd.make(
                    "orphan_reference",
                    f"Location references account {reference[1]} in portfolio "
                    f"{reference[0]}, which the account file does not contain.",
                    file_kind=str(FileKind.LOCATION),
                    row_number=row.row_number,
                    field="AccNumber",
                    value=reference[1],
                    record_key=_record_key(FileKind.LOCATION, row),
                )
            )

    _check_contiguous(
        files.account.rows,
        group_fields=("PortNumber", "AccNumber", "PolNumber"),
        sequence_field="LayerNumber",
        code="layer_gap",
        label="Layer",
        kind=FileKind.ACCOUNT,
        out=out,
    )


def _check_contiguous(
    rows: Sequence[Row],
    *,
    group_fields: tuple[str, ...],
    sequence_field: str,
    code: str,
    label: str,
    kind: FileKind,
    out: FindingSet,
) -> None:
    """Report non-contiguous sequences such as layer or inuring numbers."""
    groups: dict[tuple[str, ...], list[tuple[int, int]]] = {}
    for row in rows:
        value = row.get(sequence_field)
        if value is None:
            continue
        key = tuple(row.text(name) for name in group_fields)
        groups.setdefault(key, []).append((int(value), row.row_number))

    for key, entries in groups.items():
        numbers = sorted({number for number, _ in entries})
        expected = list(range(numbers[0], numbers[0] + len(numbers)))
        if numbers != expected:
            out.add(
                fnd.make(
                    code,
                    f"{label} numbers for {' / '.join(part for part in key if part)} are "
                    f"{', '.join(str(number) for number in numbers)}, which is not a "
                    "contiguous sequence.",
                    file_kind=str(kind),
                    row_number=entries[0][1],
                    field=sequence_field,
                    record_key="; ".join(f"{f}={v}" for f, v in zip(group_fields, key, strict=False)),
                )
            )


def _check_reinsurance(files: PortfolioFiles, out: FindingSet) -> None:
    """Contract references, scope coverage and inuring order."""
    if files.reins_info is None:
        return

    contracts = {row.text("ReinsNumber") for row in files.reins_info.rows}

    _check_contiguous(
        files.reins_info.rows,
        group_fields=(),
        sequence_field="InuringPriority",
        code="inuring_gap",
        label="Inuring priority",
        kind=FileKind.REINS_INFO,
        out=out,
    )

    if files.reins_scope is None:
        return

    location_refs = {
        (row.text("PortNumber"), row.text("AccNumber"), row.text("LocNumber"))
        for row in files.location.rows
    }
    account_refs = {(port, acc) for port, acc, _ in location_refs}
    groups = {row.text("LocGroup") for row in files.location.rows if row.text("LocGroup")}

    scoped: set[str] = set()
    for row in files.reins_scope.rows:
        contract = row.text("ReinsNumber")
        if contract not in contracts:
            out.add(
                fnd.make(
                    "orphan_reference",
                    f"Reinsurance scope references contract {contract}, which the "
                    "reinsurance info file does not contain.",
                    file_kind=str(FileKind.REINS_SCOPE),
                    row_number=row.row_number,
                    field="ReinsNumber",
                    value=contract,
                )
            )
            continue

        port, acc, loc = row.text("PortNumber"), row.text("AccNumber"), row.text("LocNumber")
        group = row.text("LocGroup")
        if loc:
            matched = (port, acc, loc) in location_refs
        elif group:
            matched = group in groups
        elif acc:
            matched = (port, acc) in account_refs
        else:
            # An unfiltered row covers the whole portfolio.
            matched = bool(files.location.rows)

        if matched:
            scoped.add(contract)
        else:
            out.add(
                fnd.make(
                    "scope_matches_nothing",
                    f"Reinsurance scope row for contract {contract} matches no risk in "
                    "the portfolio.",
                    file_kind=str(FileKind.REINS_SCOPE),
                    row_number=row.row_number,
                    record_key=f"ReinsNumber={contract}",
                )
            )

    for contract in sorted(contracts - scoped):
        out.add(
            fnd.make(
                "scope_matches_nothing",
                f"Contract {contract} has no scope row that matches a risk in the portfolio.",
                file_kind=str(FileKind.REINS_INFO),
                record_key=f"ReinsNumber={contract}",
            )
        )


def _summarise(files: PortfolioFiles, out: FindingSet) -> dict[str, Any]:
    """Build the TIV and peril summaries shown before a run."""
    by_coverage: dict[str, Decimal] = {name: Decimal(0) for name in COVERAGE_TYPES}
    by_currency: dict[str, Decimal] = {}
    by_country: dict[str, Decimal] = {}
    modelled: set[str] = set()
    unmodelled: set[str] = set()

    for row in files.location.rows:
        row_total = Decimal(0)
        for name in COVERAGE_TYPES:
            value = row.get(name)
            if value is None:
                continue
            amount = Decimal(value)
            by_coverage[name] += amount
            row_total += amount

        currency = row.text("LocCurrency") or "unknown"
        by_currency[currency] = by_currency.get(currency, Decimal(0)) + row_total

        country = row.text("CountryCode") or "unknown"
        by_country[country] = by_country.get(country, Decimal(0)) + row_total

        covered = expand_perils(row.text("LocPerilsCovered"))
        modelled.update(code for code in covered if code in MODELLED_SUBPERILS)
        unmodelled.update(unmodelled_subperils(covered))

    if len(by_currency) > 1:
        out.add(
            fnd.make(
                "mixed_currency",
                "The portfolio holds values in "
                + ", ".join(sorted(by_currency))
                + ". The Oasis Financial Module does not calculate multi-currency terms.",
                file_kind=str(FileKind.LOCATION),
                field="LocCurrency",
            )
        )

    return {
        "tiv_by_coverage": by_coverage,
        "tiv_by_currency": by_currency,
        "tiv_by_country": by_country,
        "currencies": tuple(sorted(by_currency)),
        "modelled_subperils": tuple(sorted(modelled)),
        "unmodelled_subperils": tuple(sorted(unmodelled)),
    }


def validate(files: PortfolioFiles) -> ValidationReport:
    """Run every check and return the report the exposure workspace displays."""
    out = FindingSet()

    for result in files.present().values():
        _check_columns(result, out)
        _check_rows(result, out)
        _check_keys(result, out)

    _check_locations(files.location, out)
    _check_account_hierarchy(files, out)
    _check_reinsurance(files, out)

    summary = _summarise(files, out)

    return ValidationReport(
        findings=out,
        tiv_by_coverage=summary["tiv_by_coverage"],
        tiv_by_currency=summary["tiv_by_currency"],
        tiv_by_country=summary["tiv_by_country"],
        location_count=len(files.location.rows),
        account_count=len(files.account.rows) if files.account else 0,
        currencies=summary["currencies"],
        modelled_subperils=summary["modelled_subperils"],
        unmodelled_subperils=summary["unmodelled_subperils"],
    )
