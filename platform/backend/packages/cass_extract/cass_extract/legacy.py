"""Converting the retired two-sheet extract into a CASS intake template.

A one-off migration, not an ingestion path. The Klapton Re premium workbook
described a risk across two sheets with insured value on the policy, and CASS
no longer reads that shape: the account reference is now supplied rather than
inferred, and a risk is a row. This module exists so the pilot portfolio can be
carried across, and so the migrated file stays *reproducible* -- a confidential
fixture nobody can regenerate is one nobody can check when the profile changes.

The conversion makes exactly one judgement, and it makes it in the direction of
saying less.

Where a business schedules **one** site, the whole of its policy value sits at
that site. That is arithmetic, not an assumption, so the risk row carries a
total and no allocation is recorded.

Where a business schedules **several**, the source does not say how the value
divides. Those risk rows are written **blank**, with the policy total on the
Policies sheet, so the division stays an explicit, selectable assumption at run
time. Writing an equal split into the template instead would freeze an
assumption into a file that then reads as data -- which is precisely the
confusion the new format was designed to remove.

The coverage breakdown is unknown for every risk, so the migration writes the
risk total and leaves the four coverage columns empty. The component split is a
separate assumption and stays one.

Confidential columns do not cross. Insured, cedent and broker names are on the
retired policy sheet and have no destination in the template; the source
reference is what identifies a row.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, BinaryIO

from . import template
from .legacy_reader import ExtractRead, read_workbook
from .legacy_schema import country_code
from .records import SourceRow

#: Recorded on the converted file so a migrated fixture can be traced back.
MIGRATION_VERSION = "legacy-two-sheet-to-intake/1.0.0"

#: The brief confirms earthquake coverage and USD for every row of this source.
COVERED_PERIL = "QEQ"
CURRENCY = "USD"


class MigrationError(Exception):
    """Raised when the legacy workbook cannot be converted."""


@dataclasses.dataclass(slots=True)
class Migration:
    """The converted rows, and what the conversion had to decide."""

    risks: list[dict[str, Any]]
    policies: list[dict[str, Any]]
    single_site_businesses: int
    multi_site_businesses: int
    #: Risk rows left blank because the source cannot say what each site holds.
    risks_awaiting_allocation: int
    source_tiv: Decimal
    stated_tiv: Decimal
    #: Value on policies whose account schedules no site at all. Kept separate
    #: from the deferred figure: value awaiting an allocation across known
    #: risks and value with no risk to sit on are different problems, and
    #: adding them together would make the first look enormous.
    ungeocoded_tiv: Decimal = Decimal("0.00")

    @property
    def deferred_tiv(self) -> Decimal:
        """Value awaiting an allocation across risks that do exist."""
        return self.source_tiv - self.stated_tiv - self.ungeocoded_tiv

    def as_dict(self) -> dict[str, Any]:
        return {
            "migration_version": MIGRATION_VERSION,
            "risks": len(self.risks),
            "policies": len(self.policies),
            "single_site_businesses": self.single_site_businesses,
            "multi_site_businesses": self.multi_site_businesses,
            "risks_awaiting_allocation": self.risks_awaiting_allocation,
            "source_tiv": str(self.source_tiv),
            "value_stated_per_risk": str(self.stated_tiv),
            "value_left_to_allocate": str(self.deferred_tiv),
            "value_on_accounts_with_no_risks": str(self.ungeocoded_tiv),
            "geocoded_tiv": str(self.stated_tiv + self.deferred_tiv),
            "note": (
                "Risk values are written only where a business schedules one site. "
                "Where it schedules several, the source does not say how the value "
                "divides, so the rows are blank and the policy total carries it."
            ),
        }


def convert(read: ExtractRead) -> Migration:
    """Turn a parsed two-sheet extract into CASS intake rows."""
    if not read.is_readable:
        raise MigrationError(
            "The legacy workbook is missing required columns, so it cannot be "
            "converted: "
            + ", ".join([*read.policies.missing_columns, *read.locations.missing_columns])
            + "."
        )

    schedules: dict[str, list[SourceRow]] = {}
    for row in read.locations.rows:
        business = _text(row.get("business_id"))
        if business:
            schedules.setdefault(business, []).append(row)

    policies_by_business: dict[str, list[SourceRow]] = {}
    for row in read.policies.rows:
        business = _text(row.get("business_id"))
        if business:
            policies_by_business.setdefault(business, []).append(row)

    totals = {
        business: sum(
            (_money(item.get("gross_limit")) for item in rows), Decimal("0.00")
        )
        for business, rows in policies_by_business.items()
    }

    risks: list[dict[str, Any]] = []
    stated = Decimal("0.00")
    single = multi = deferred = 0

    for business, sites in sorted(schedules.items()):
        one_site = len(sites) == 1
        if one_site:
            single += 1
        else:
            multi += 1

        for site in sorted(sites, key=lambda item: _number(item.get("location_number"))):
            total = totals.get(business)
            # The whole of a one-site business's value sits at that site. That
            # is arithmetic. Several sites and the source is silent, so the row
            # stays blank rather than inheriting a split nobody stated.
            if one_site and total is not None:
                stated += total
                value = _plain(total)
            else:
                value = ""
                deferred += 1
            risks.append(_risk_row(business, site, value))

    return Migration(
        risks=risks,
        policies=[
            _policy_row(row)
            for business in sorted(policies_by_business)
            for row in policies_by_business[business]
        ],
        single_site_businesses=single,
        multi_site_businesses=multi,
        risks_awaiting_allocation=deferred,
        source_tiv=sum(totals.values(), Decimal("0.00")),
        stated_tiv=stated,
        ungeocoded_tiv=sum(
            (amount for business, amount in totals.items() if business not in schedules),
            Decimal("0.00"),
        ),
    )


def _risk_row(business: str, site: SourceRow, value: str) -> dict[str, Any]:
    return {
        "Account reference": business,
        "Risk reference": str(_number(site.get("location_number"))),
        "Country": country_code(_text(site.get("country"))),
        "Latitude": _plain(site.get("latitude")),
        "Longitude": _plain(site.get("longitude")),
        "Address": _text(site.get("risk_location_address")),
        "Geocode precision": _text(site.get("precision")),
        "Primary site": "Yes" if site.get("primary_location") else "No",
        "Needs review": "Yes" if site.get("needs_review") else "No",
        "Class of business": _text(site.get("class_of_business")),
        "Total insured value": value,
        "Perils covered": COVERED_PERIL,
        "Currency": CURRENCY,
    }


def _policy_row(row: SourceRow) -> dict[str, Any]:
    return {
        "Account reference": _text(row.get("business_id")),
        "Policy reference": _text(row.get("policy_id")),
        "Currency": CURRENCY,
        "Perils covered": COVERED_PERIL,
        "Total insured value": _plain(_money(row.get("gross_limit"))),
        "Inception date": _date(row.get("insured_period_start")),
        "Expiry date": _date(row.get("insured_period_end")),
    }


def migrate(
    source: BinaryIO | str, *, project_reference: str = "", generated: dt.date | None = None
) -> tuple[bytes, Migration]:
    """Read a legacy workbook and write the CASS intake template it becomes."""
    result = convert(read_workbook(source))
    payload = template.workbook(
        project_reference=project_reference,
        risks=result.risks,
        policies=result.policies,
        generated=generated,
    )
    return payload, result


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _money(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _number(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _plain(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _date(value: Any) -> str:
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return ""


def summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Counts a migration report shows, for a quick eyeball of the output."""
    return {
        "risks": len(rows),
        "with_value": sum(1 for row in rows if row.get("Total insured value")),
        "awaiting_allocation": sum(
            1 for row in rows if not row.get("Total insured value")
        ),
    }
