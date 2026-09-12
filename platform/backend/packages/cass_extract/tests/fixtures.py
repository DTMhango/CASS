"""A synthetic extract that keeps the real one's structure and none of its data.

The integration brief is explicit that the real workbook must not become a test
fixture: it is confidential working data. What the tests need from it is not its
values but its awkward shapes, and section 8 of the brief lists them --
a single-location business, a multi-location business, a business identifier on
more than one policy row, a coordinate shared by different businesses, one
location of each precision band, one flagged for review, both countries, and
Fire, Engineering and an excluded class.

Every one of those is built here, with invented names and round amounts. The
counts are small enough to assert exactly, which is the point: a test that says
"63 locations" tells you nothing when it breaks, and one that says "this
business's second site went missing" tells you everything.
"""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

from cass_extract.schema import LOCATION_FIELDS, LOCATION_SHEET, POLICY_FIELDS, POLICY_SHEET

#: A coordinate two unrelated businesses share, so the tests can prove it is
#: reported and not deduplicated.
SHARED_LAT = Decimal("-6.200000")
SHARED_LON = Decimal("106.800000")


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
