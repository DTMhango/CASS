"""Normalising a portfolio to the run currency.

Section 15 names multiple currencies reaching the Oasis Financial Module as a
material risk, because the module cannot calculate multi-currency terms, and
section 8 requires the rate, valuation date, source and direction to be
captured before generation. These hold the conversion to three things.

It moves money and nothing else. A column that is not an amount comes back
exactly as it went in, because a conversion that rewrote an identifier or a
coordinate would be a different portfolio rather than the same one in another
currency.

It converts at a rate somebody stated for the money in front of it. A row in a
currency the rate does not cover is refused, not converted on the assumption
that it was probably meant.

And the arithmetic is ``Decimal`` and quantised at the cent, for the reason
ADR 5 keeps money off floats: the value that reaches the engine has to be one
a person can reconcile against a statement.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from decimal import Decimal

import pytest

from cass_oed.currency import (
    ConversionRate,
    CurrencyError,
    convert_file,
    convert_portfolio,
    money_columns,
)
from cass_oed.schema import FileKind

RATE = ConversionRate(
    from_currency="IDR",
    to_currency="USD",
    rate=Decimal("0.0000613"),
    valuation_date=dt.date(2026, 6, 30),
    source="Bank Indonesia middle rate",
    reference="https://www.bi.go.id/en/statistik/informasi-kurs/",
)

LOCATIONS = (
    b"PortNumber,AccNumber,LocNumber,CountryCode,Latitude,Longitude,"
    b"OccupancyCode,ConstructionCode,LocPerilsCovered,BuildingTIV,ContentsTIV,LocCurrency\n"
    b"1,ACC-1,LOC-1,ID,-6.2088,106.8456,1100,5000,QEQ,4500000000,900000000,IDR\n"
    b"1,ACC-1,LOC-2,ID,-6.9175,107.6191,1200,5000,QEQ,2750000000,,IDR\n"
)


def rows_of(payload: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))


def test_every_monetary_column_moves_at_the_rate():
    converted = convert_file(FileKind.LOCATION, LOCATIONS, RATE)
    rows = rows_of(converted.payload)

    # 4,500,000,000 IDR at 0.0000613 is 275,850.00 USD, to the cent.
    assert rows[0]["BuildingTIV"] == "275850.00"
    assert rows[0]["ContentsTIV"] == "55170.00"
    assert rows[1]["BuildingTIV"] == "168575.00"


def test_the_currency_the_file_states_becomes_the_one_it_is_now_in():
    converted = convert_file(FileKind.LOCATION, LOCATIONS, RATE)

    assert {row["LocCurrency"] for row in rows_of(converted.payload)} == {"USD"}


def test_a_column_that_is_not_money_is_returned_exactly_as_it_arrived():
    """A conversion that moved a coordinate would be a different portfolio."""
    converted = convert_file(FileKind.LOCATION, LOCATIONS, RATE)
    before, after = rows_of(LOCATIONS)[0], rows_of(converted.payload)[0]

    for column in ("PortNumber", "AccNumber", "LocNumber", "CountryCode",
                   "Latitude", "Longitude", "OccupancyCode", "ConstructionCode",
                   "LocPerilsCovered"):
        assert after[column] == before[column]


def test_a_cell_stating_no_amount_is_left_stating_none():
    """Zero and "not stated" are different facts, and converting one into the other loses one."""
    converted = convert_file(FileKind.LOCATION, LOCATIONS, RATE)

    assert rows_of(converted.payload)[1]["ContentsTIV"] == ""


def test_a_row_in_another_currency_is_refused_rather_than_converted():
    mixed = LOCATIONS.replace(b"2750000000,,IDR", b"2750000000,,SGD")

    with pytest.raises(CurrencyError, match="states SGD"):
        convert_file(FileKind.LOCATION, mixed, RATE)


def test_a_value_nobody_can_read_is_refused():
    broken = LOCATIONS.replace(b"4500000000", b"4.5bn")

    with pytest.raises(CurrencyError, match="not an amount"):
        convert_file(FileKind.LOCATION, broken, RATE)


def test_the_conversion_reports_what_it_moved():
    converted = convert_file(FileKind.LOCATION, LOCATIONS, RATE)

    assert converted.rows == 2
    assert converted.converted_fields == 3
    assert converted.source_total == Decimal("8150000000")
    assert converted.converted_total == Decimal("499595.00")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("rate", Decimal("0"), "positive decimal"),
        ("rate", Decimal("-1"), "positive decimal"),
        ("to_currency", "IDR", "converts nothing"),
        ("source", "", "the source it came from"),
    ],
)
def test_a_rate_that_is_not_evidence_is_refused(field, value, message):
    stated = {
        "from_currency": "IDR",
        "to_currency": "USD",
        "rate": Decimal("0.0000613"),
        "valuation_date": dt.date(2026, 6, 30),
        "source": "Bank Indonesia middle rate",
    }
    stated[field] = value

    with pytest.raises(CurrencyError, match=message):
        ConversionRate(**stated)


def test_the_evidence_states_the_direction_rather_than_leaving_it_to_be_inferred():
    """Applying a published quote upside down is the commonest way to be wrong."""
    evidence = RATE.as_evidence()

    assert evidence["direction"] == "1 IDR = 0.0000613 USD"
    assert evidence["valuation_date"] == "2026-06-30"
    assert evidence["source"] == "Bank Indonesia middle rate"


def test_a_portfolio_converts_file_by_file_with_one_block_of_evidence():
    payloads, evidence = convert_portfolio(
        {"oed_location": (FileKind.LOCATION, LOCATIONS)}, RATE
    )

    assert rows_of(payloads["oed_location"])[0]["BuildingTIV"] == "275850.00"
    assert evidence["converted_fields"] == 3
    assert evidence["files"][0]["role"] == "oed_location"
    assert evidence["files"][0]["converted_total"] == "499595.00"


def test_the_money_columns_come_from_the_schema_rather_than_a_list_here():
    """A file that gains a monetary column gains the conversion with it."""
    columns = money_columns(FileKind.LOCATION)

    assert "BuildingTIV" in columns
    assert "LocCurrency" not in columns
