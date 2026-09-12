"""A synthetic portfolio that keeps the real one's structure and none of its data.

The real workbook must not become a test fixture: it stays outside version
control and outside the test images. What the tests need from it is not its
values but its awkward shapes, and section 8 of the brief lists them -- a
single-site account, a multi-site account, an account reference on more than
one policy, a coordinate shared by unrelated accounts, one site of each
precision band, one flagged for review, both countries, and Fire, Engineering
and an excluded class.

Every one of those is built here twice: once in the CASS intake template, which
is what CASS reads, and once in the retired two-sheet extract, which only the
migration still reads. The two builders produce the same portfolio, so a test
of the migration can assert that nothing was lost in the conversion.

The counts are small enough to assert exactly, which is the point: a test that
says "63 locations" tells you nothing when it breaks, and one that says "this
account's second site went missing" tells you everything.
"""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

from cass_extract.legacy_schema import (
    LOCATION_FIELDS,
    LOCATION_SHEET,
    POLICY_FIELDS,
    POLICY_SHEET,
)

#: A coordinate two unrelated businesses share, so the tests can prove it is
#: reported and not deduplicated.
SHARED_LAT = Decimal("-6.200000")
SHARED_LON = Decimal("106.800000")


def priced(policy_id: str, business_id: str, policy_tiv: str, **extra: Any) -> dict[str, Any]:
    """One policy as the allocation engine reads it.

    Canonical names, not a source format's. ``policy`` below builds a row of
    the retired two-sheet workbook, and feeding one of those straight into the
    allocation engine only ever worked because the engine had that workbook's
    column name compiled into it.
    """
    return {
        "policy_id": policy_id,
        "business_id": business_id,
        "policy_tiv": policy_tiv,
        **extra,
    }


def policy(
    policy_id: str,
    business_id: str,
    gross_limit: str,
    *,
    location_count: int = 1,
    latitude: Any = None,
    longitude: Any = None,
    insured: str = "Example Insured Ltd",
    renewed: str = "N",
) -> dict[str, Any]:
    """One policy row. Renewal columns carry the source's not-applicable mark."""
    not_applicable = "—"
    return {
        "policy_id": policy_id,
        "business_id": business_id,
        "business_title": f"{insured} property programme",
        "insured_name": insured,
        "cedent_name": "Example Cedent SA",
        "broker_name": "Example Broker LLP",
        "insured_country": "Indonesia",
        "insured_continent": "Asia",
        "main_class_of_business": "Fire",
        "underwriting_year": 2026,
        "booking_year": 2026,
        "hub": "Singapore",
        "reporting_unit": "Facultative",
        "type_of_business": "Facultative",
        "business_stream": "Property",
        "insured_period_start": "2026-01-01",
        "insured_period_end": "2026-12-31",
        "gross_premium": "12500.00",
        "net_premium": "10000.00",
        "gross_limit": gross_limit,
        "gross_paid": "0.00",
        "gross_outstanding": "0.00",
        "gross_incurred": "0.00",
        "loss_ratio_pct": "0",
        "renewed": renewed,
        "renewal_policy_id": "" if renewed == "N" else f"{policy_id}-R",
        "prior_share_pct": "10",
        "renewal_premium": not_applicable if renewed == "N" else "13000.00",
        "premium_growth_pct": not_applicable if renewed == "N" else "4.0",
        "renewal_share_pct": not_applicable if renewed == "N" else "10",
        "share_change": "unchanged",
        "risk_location_status": "geocoded",
        "risk_location_count": location_count,
        "risk_latitude": latitude,
        "risk_longitude": longitude,
        "risk_location_address": "1 Example Street",
        "risk_location_precision": "parcel",
        "risk_location_method": "address",
        "risk_location_confidence": "high",
        "risk_location_needs_review": "No",
    }


def sited(
    business_id: str,
    number: int,
    latitude: Any,
    longitude: Any,
    *,
    primary: bool = True,
    precision: str = "parcel",
    needs_review: bool = False,
    country_code: str = "ID",
    class_of_business: str = "Fire",
    **extra: Any,
) -> dict[str, Any]:
    """One risk as the cohorts and the allocation read it.

    Canonical names, like ``priced``. ``location`` below builds a row of the
    retired two-sheet workbook, and a rule that reads one of those directly is
    a rule coupled to a source format it should know nothing about.
    """
    return {
        "business_id": business_id,
        "location_number": number,
        "primary_location": primary,
        "latitude": latitude,
        "longitude": longitude,
        "precision": precision,
        "needs_review": needs_review,
        "country_code": country_code,
        "class_of_business": class_of_business,
        **extra,
    }


def location(
    business_id: str,
    number: int,
    latitude: Any,
    longitude: Any,
    *,
    primary: str = "Yes",
    precision: str = "parcel",
    needs_review: str = "No",
    country: str = "Indonesia",
    class_of_business: str = "Fire",
) -> dict[str, Any]:
    return {
        "business_id": business_id,
        "location_number": number,
        "primary_location": primary,
        "status": "geocoded",
        "latitude": latitude,
        "longitude": longitude,
        "risk_location_address": f"{number} Example Street",
        "provider_address": f"{number} Example Street, Jakarta",
        "precision": precision,
        "method": "address",
        "confidence": "medium",
        "needs_review": needs_review,
        "class_of_business": class_of_business,
        "country": country,
        "provider": "example-geocoder",
        "source_id": f"src-{business_id}-{number}",
        "rationale": "Matched on the reported street address.",
    }


def structural_extract() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Policies and locations covering every shape section 8 names.

    Eight businesses:

    ``B-SINGLE``   one location, cohort A, Fire, Indonesia -- the ordinary case
    ``B-MULTI``    three locations, all cohort A Fire -- business-complete
    ``B-PARTIAL``  two locations, one flagged for review -- not complete
    ``B-TWOPOL``   two policy rows on one business, and locations
    ``B-SHARED``   shares a coordinate with B-SINGLE
    ``B-COARSE``   locality precision, so cohort B
    ``B-NEPAL``    Nepal, Engineering
    ``B-LIABILITY`` the excluded class
    """
    policies = [
        policy("P-1", "B-SINGLE", "1000000.00", latitude=SHARED_LAT, longitude=SHARED_LON),
        policy("P-2", "B-MULTI", "3000000.00", location_count=3,
               latitude=Decimal("-6.910000"), longitude=Decimal("107.600000")),
        policy("P-3", "B-PARTIAL", "2000000.00", location_count=2,
               latitude=Decimal("-7.250000"), longitude=Decimal("112.750000")),
        policy("P-4a", "B-TWOPOL", "500000.00", latitude=Decimal("-6.100000"),
               longitude=Decimal("106.700000"), renewed="Y"),
        policy("P-4b", "B-TWOPOL", "750000.00", latitude=Decimal("-6.100000"),
               longitude=Decimal("106.700000")),
        policy("P-5", "B-SHARED", "250000.00", latitude=SHARED_LAT, longitude=SHARED_LON),
        policy("P-6", "B-COARSE", "400000.00", latitude=Decimal("-8.650000"),
               longitude=Decimal("115.200000")),
        policy("P-7", "B-NEPAL", "600000.00", latitude=Decimal("27.700000"),
               longitude=Decimal("85.320000")),
        policy("P-8", "B-LIABILITY", "125000.00", latitude=Decimal("-6.300000"),
               longitude=Decimal("106.900000")),
        # A policy with no scheduled locations at all, like most of the real
        # extract: 1,140 of its 1,353 policies are not geocoded.
        policy("P-9", "B-NOLOC", "800000.00", location_count=0),
    ]

    locations = [
        location("B-SINGLE", 1, SHARED_LAT, SHARED_LON),
        location("B-MULTI", 1, Decimal("-6.910000"), Decimal("107.600000")),
        location("B-MULTI", 2, Decimal("-6.920000"), Decimal("107.610000"), primary="No"),
        location("B-MULTI", 3, Decimal("-6.930000"), Decimal("107.620000"), primary="No",
                 precision="street"),
        location("B-PARTIAL", 1, Decimal("-7.250000"), Decimal("112.750000")),
        location("B-PARTIAL", 2, Decimal("-7.260000"), Decimal("112.760000"), primary="No",
                 needs_review="Yes"),
        location("B-TWOPOL", 1, Decimal("-6.100000"), Decimal("106.700000")),
        location("B-SHARED", 1, SHARED_LAT, SHARED_LON),
        location("B-COARSE", 1, Decimal("-8.650000"), Decimal("115.200000"),
                 precision="locality"),
        location("B-NEPAL", 1, Decimal("27.700000"), Decimal("85.320000"),
                 country="Nepal", class_of_business="Engineering"),
        location("B-LIABILITY", 1, Decimal("-6.300000"), Decimal("106.900000"),
                 class_of_business="Liability"),
    ]
    return policies, locations


def as_workbook(
    policies: list[dict[str, Any]], locations: list[dict[str, Any]]
) -> io.BytesIO:
    """Write the rows to a real .xlsx, so the reader is tested on its own path.

    Columns follow the rows rather than the schema, so a test can drop a column
    by removing the key and add one by setting it. Writing the schema's columns
    regardless would make both of those invisible.
    """
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = POLICY_SHEET
    _write(sheet, _columns(POLICY_FIELDS, policies), policies)
    _write(
        workbook.create_sheet(LOCATION_SHEET),
        _columns(LOCATION_FIELDS, locations),
        locations,
    )

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer


def _columns(fields, rows: list[dict[str, Any]]) -> list[str]:
    """Schema columns the rows actually carry, then anything extra they add."""
    known = [spec.name for spec in fields]
    present: dict[str, None] = {}
    for row in rows:
        for key in row:
            present.setdefault(key, None)
    return [name for name in known if name in present] + [
        name for name in present if name not in known
    ]


def _write(sheet, columns: list[str], rows: list[dict[str, Any]]) -> None:
    sheet.append(columns)
    for row in rows:
        sheet.append([_cell(row.get(name)) for name in columns])


def _cell(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    return value


# -- the CASS intake template ------------------------------------------------------

def risk_row(
    account: str,
    reference: str,
    latitude: Any,
    longitude: Any,
    *,
    total: str = "",
    primary: str = "Yes",
    precision: str = "parcel",
    needs_review: str = "No",
    country: str = "ID",
    class_of_business: str = "Fire",
    **extra: Any,
) -> dict[str, Any]:
    """One row of the Risks sheet, in template column names."""
    row = {
        "Account reference": account,
        "Risk reference": reference,
        "Country": country,
        "Latitude": str(latitude),
        "Longitude": str(longitude),
        "Address": f"{reference} Example Street",
        "Geocode precision": precision,
        "Primary site": primary,
        "Needs review": needs_review,
        "Class of business": class_of_business,
        "Total insured value": total,
        "Perils covered": "QEQ",
        "Currency": "USD",
    }
    row.update(extra)
    return row


def policy_row(account: str, reference: str, total: str, **extra: Any) -> dict[str, Any]:
    """One row of the Policies sheet, in template column names."""
    row = {
        "Account reference": account,
        "Policy reference": reference,
        "Currency": "USD",
        "Perils covered": "QEQ",
        "Total insured value": total,
    }
    row.update(extra)
    return row


def structural_template() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The same eight businesses as ``structural_extract``, in the new format.

    Written natively rather than migrated from the retired workbook, because a
    fixture that reached the current format only by way of the previous one
    could not outlive it -- and would quietly test the converter instead of the
    thing under test.

    Single-site accounts state what their site is worth, because that is a
    fact. Multi-site accounts state nothing at the risk and carry the total on
    the policy, because the source of this shape does not say how it divides.
    """
    risks = [
        risk_row("B-SINGLE", "1", SHARED_LAT, SHARED_LON, total="1000000.00"),
        risk_row("B-MULTI", "1", "-6.910000", "107.600000"),
        risk_row("B-MULTI", "2", "-6.920000", "107.610000", primary="No"),
        risk_row("B-MULTI", "3", "-6.930000", "107.620000", primary="No",
                 precision="street"),
        risk_row("B-PARTIAL", "1", "-7.250000", "112.750000"),
        risk_row("B-PARTIAL", "2", "-7.260000", "112.760000", primary="No",
                 needs_review="Yes"),
        risk_row("B-TWOPOL", "1", "-6.100000", "106.700000", total="1250000.00"),
        risk_row("B-SHARED", "1", SHARED_LAT, SHARED_LON, total="250000.00"),
        risk_row("B-COARSE", "1", "-8.650000", "115.200000", total="400000.00",
                 precision="locality"),
        risk_row("B-NEPAL", "1", "27.700000", "85.320000", total="600000.00",
                 country="NP", class_of_business="Engineering"),
        risk_row("B-LIABILITY", "1", "-6.300000", "106.900000", total="125000.00",
                 class_of_business="Liability"),
    ]
    policies = [
        policy_row("B-SINGLE", "P-1", "1000000.00"),
        policy_row("B-MULTI", "P-2", "3000000.00"),
        policy_row("B-PARTIAL", "P-3", "2000000.00"),
        policy_row("B-TWOPOL", "P-4a", "500000.00"),
        policy_row("B-TWOPOL", "P-4b", "750000.00"),
        policy_row("B-SHARED", "P-5", "250000.00"),
        policy_row("B-COARSE", "P-6", "400000.00"),
        policy_row("B-NEPAL", "P-7", "600000.00"),
        policy_row("B-LIABILITY", "P-8", "125000.00"),
        # An account with policy terms and no risks, like most of a facultative
        # book: value CASS knows about and cannot place.
        policy_row("B-NOLOC", "P-9", "800000.00"),
    ]
    return risks, policies


def as_template(
    risks: list[dict[str, Any]] | None = None,
    policies: list[dict[str, Any]] | None = None,
) -> bytes:
    """Write rows to a real intake workbook, so the reader is tested on its own path."""
    from cass_extract import template

    if risks is None and policies is None:
        risks, policies = structural_template()
    return template.workbook(
        project_reference="idn-fac-2026",
        risks=risks or [],
        policies=policies or [],
    )
