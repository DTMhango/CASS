"""Normalising a portfolio to the run currency, with the evidence for it.

Section 8 requires currency-conversion evidence -- rates, valuation date,
source and direction -- to be captured before Oasis generation, and section 15
names multiple currencies reaching the Oasis Financial Module as a material
risk: the module does not calculate multi-currency terms, so a book whose
values are not in the run currency would be aggregated as though a rupiah and a
dollar were the same unit.

Two rules shape what is here.

Reported values are never overwritten. The conversion produces a new set of
files for the engine and leaves the published exposure exactly as the business
reported it, so a reader can always see both the reported figure and the
converted one with the rate between them.

Nothing is converted at a rate nobody stated for it. A row whose currency is
not the rate's source currency is refused rather than converted on the
assumption that it must have been meant: a book that reached here with two
currencies in it is a validation failure, not a rounding problem.

Money is ``Decimal`` throughout and quantised once, at the cent, for the same
reason ADR 5 keeps money off JavaScript's float: a value that reaches the
engine has to be the value somebody can reconcile against a statement.
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import io
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from .reader import ENCODING
from .schema import DataType, FileKind, fields_for

#: Money is held to the cent. A converted value is quantised once, here, rather
#: than left at the rate's precision for something downstream to round
#: differently.
CENTS = Decimal("0.01")


def currency_column(kind: FileKind) -> str:
    """The column this file kind states its currency in, if it states one.

    Taken from the schema rather than a list of names, so a file gaining a
    currency column gains the check with it.
    """
    stated = [
        spec.name for spec in fields_for(kind) if spec.dtype is DataType.CURRENCY
    ]
    return stated[0] if stated else ""


class CurrencyError(Exception):
    """Raised when a portfolio cannot be normalised to the run currency."""


@dataclasses.dataclass(frozen=True, slots=True)
class ConversionRate:
    """One governed rate, and where it came from.

    ``rate`` is how many units of ``to_currency`` one unit of ``from_currency``
    buys. The direction is stated rather than inferred because the commonest
    way to get a conversion wrong by a factor of the rate squared is to apply a
    published quote the wrong way round.
    """

    from_currency: str
    to_currency: str
    rate: Decimal
    valuation_date: dt.date
    source: str
    reference: str = ""

    def __post_init__(self) -> None:
        if not self.from_currency or not self.to_currency:
            raise CurrencyError("A rate needs both currencies.")
        if self.from_currency.upper() == self.to_currency.upper():
            raise CurrencyError(
                f"A rate from {self.from_currency} to itself converts nothing."
            )
        if not isinstance(self.rate, Decimal) or self.rate <= 0:
            raise CurrencyError(
                f"The rate from {self.from_currency} to {self.to_currency} is "
                f"{self.rate!r}. A rate must be a positive decimal."
            )
        if not self.source:
            raise CurrencyError(
                "A rate needs the source it came from, so a reviewer can check it "
                "against the published quote."
            )

    @property
    def description(self) -> str:
        return (
            f"1 {self.from_currency} = {self.rate} {self.to_currency} "
            f"at {self.valuation_date.isoformat()} ({self.source})"
        )

    def apply(self, amount: Decimal) -> Decimal:
        return (amount * self.rate).quantize(CENTS)

    def as_evidence(self) -> dict[str, Any]:
        """The block section 8 asks to be captured before generation."""
        return {
            "from_currency": self.from_currency,
            "to_currency": self.to_currency,
            "rate": str(self.rate),
            "direction": f"1 {self.from_currency} = {self.rate} {self.to_currency}",
            "valuation_date": self.valuation_date.isoformat(),
            "source": self.source,
            "reference": self.reference,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class ConvertedFile:
    """One file as the engine will receive it, with what changed in it."""

    kind: FileKind
    payload: bytes
    rows: int
    converted_fields: int
    source_total: Decimal
    converted_total: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": str(self.kind),
            "rows": self.rows,
            "converted_fields": self.converted_fields,
            "source_total": str(self.source_total),
            "converted_total": str(self.converted_total),
        }


def money_columns(kind: FileKind) -> tuple[str, ...]:
    """Every column of this file kind that holds an amount of money."""
    return tuple(
        spec.name for spec in fields_for(kind) if spec.dtype is DataType.MONEY
    )


def _amount(text: str) -> Decimal | None:
    """Read a monetary cell, or nothing where it states none."""
    cleaned = (text or "").strip()
    if cleaned == "":
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise CurrencyError(
            f"{cleaned!r} is not an amount this file can convert. Correct the value "
            "before running, rather than converting a number nobody can read."
        ) from exc


def convert_file(kind: FileKind, payload: bytes, rate: ConversionRate) -> ConvertedFile:
    """Rewrite one OED file into the rate's target currency.

    Every monetary column moves at the rate, every currency column that stated
    the source currency now states the target, and every other column is
    written back exactly as it arrived.
    """
    kind = FileKind(kind)
    text = payload.decode(ENCODING)
    reader = csv.DictReader(io.StringIO(text))
    columns = list(reader.fieldnames or [])
    if not columns:
        raise CurrencyError(
            f"The {kind.label().lower()} has no header row, so there is nothing to convert."
        )

    amounts = [name for name in money_columns(kind) if name in columns]
    stated_in = currency_column(kind) if currency_column(kind) in columns else ""
    source = rate.from_currency.upper()
    target = rate.to_currency.upper()

    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=columns, lineterminator="\n")
    writer.writeheader()

    rows = 0
    converted_fields = 0
    source_total = Decimal(0)
    converted_total = Decimal(0)

    for position, row in enumerate(reader, start=2):
        rows += 1
        stated = (row.get(stated_in) or "").strip().upper() if stated_in else ""
        if stated_in and stated and stated != source:
            raise CurrencyError(
                f"Row {position} of the {kind.label().lower()} states {stated}, and the "
                f"rate converts {source}. A book holding more than one currency is a "
                "validation failure rather than something to convert at this rate."
            )

        for name in amounts:
            amount = _amount(row.get(name, ""))
            if amount is None:
                continue
            moved = rate.apply(amount)
            row[name] = f"{moved:.2f}"
            converted_fields += 1
            source_total += amount
            converted_total += moved

        if stated_in and stated:
            row[stated_in] = target

        writer.writerow({name: row.get(name, "") for name in columns})

    return ConvertedFile(
        kind=kind,
        payload=out.getvalue().encode("utf-8"),
        rows=rows,
        converted_fields=converted_fields,
        source_total=source_total,
        converted_total=converted_total,
    )


def convert_portfolio(
    files: Mapping[str, tuple[FileKind, bytes]], rate: ConversionRate
) -> tuple[dict[str, bytes], dict[str, Any]]:
    """Convert every supplied file, returning the payloads and the evidence.

    The evidence carries the rate, what it was applied to and what came out,
    file by file, so the conversion can be checked without re-running it.
    """
    payloads: dict[str, bytes] = {}
    converted: list[dict[str, Any]] = []
    for role, (kind, payload) in files.items():
        outcome = convert_file(kind, payload, rate)
        payloads[role] = outcome.payload
        converted.append({"role": role, **outcome.as_dict()})

    evidence = {
        **rate.as_evidence(),
        "files": converted,
        "converted_fields": sum(item["converted_fields"] for item in converted),
    }
    return payloads, evidence
