"""The retired two-sheet extract, as a contract, for the migration to read.

The 30 June 2026 extract is a two-sheet workbook of Klapton Reinsurance's own
book, not an OED file and no longer a format CASS accepts. It is described here
rather than inferred at read time so that a change of shape is a refusal with a
reason, not a column quietly arriving as ``None`` and a total quietly coming
out wrong.

The live contract is :mod:`cass_extract.profile`, whose columns are bound to
OED fields. This one is kept because the existing book is still in the old
shape and :mod:`cass_extract.legacy` converts it once.

Two things it fixes that the migration depends on.

**Value is money.** ``gross_limit`` is the reported TIV at KRE's share in USD,
and it is read as ``Decimal`` from the first moment. A float here would make
the exact reconciliation the brief requires impossible to promise.

**The version is stated.** ``LEGACY_SCHEMA_VERSION`` identifies the shape this parser
was written against, and every migration records it beside the source checksum,
so a later extract that moved a column can be told apart from one that did not.

What it no longer fixes is confidentiality. An earlier draft graded each column
by sensitivity, on the reading that insured, cedent and broker names had to be
kept from some colleagues. Klapton Re holds no portfolio information a Klapton
Re colleague may not see, and the grading is gone rather than left in place
unread.
"""


from __future__ import annotations

import dataclasses
import enum

#: The source format, named as the integration brief named it. Prefixed
#: ``LEGACY_`` so it cannot be mistaken for the live one: the profile CASS
#: imports against is ``profile.PROFILE_NAME``, and a migration report that
#: quoted the wrong one would misstate where its numbers came from.
LEGACY_PROFILE_NAME = "Klapton Re geocoded policy extract"

#: The extract shape this reader understands.
LEGACY_SCHEMA_VERSION = "klapton-re-geocoded-policy-extract/2026-06-30.1"

#: The reader itself, recorded beside the source checksum on a migration so a
#: reread of the same file by a later build is distinguishable.
LEGACY_PARSER_VERSION = "1.0.0"

POLICY_SHEET = "Premium Policies"
LOCATION_SHEET = "Risk Locations"


class DataType(enum.StrEnum):
    TEXT = "text"
    INTEGER = "integer"
    MONEY = "money"
    COORDINATE = "coordinate"
    YES_NO = "yes_no"
    DATE = "date"
    NUMBER = "number"


@dataclasses.dataclass(frozen=True, slots=True)
class FieldSpec:
    """One source column as CASS interprets it."""

    name: str
    dtype: DataType
    business_label: str
    required: bool = False
    business_help: str = ""


def _f(
    name: str,
    dtype: DataType,
    label: str,
    *,
    required: bool = False,
    help_text: str = "",
) -> FieldSpec:
    return FieldSpec(name, dtype, label, required, help_text)


POLICY_FIELDS: tuple[FieldSpec, ...] = (
    _f("policy_id", DataType.TEXT, "Policy reference", required=True),
    _f("business_id", DataType.TEXT, "Business reference", required=True),
    _f(
        "business_title",
        DataType.TEXT,
        "Business title",
    ),
    _f("insured_name", DataType.TEXT, "Insured"),
    _f("cedent_name", DataType.TEXT, "Cedent"),
    _f("broker_name", DataType.TEXT, "Broker"),
    _f("insured_country", DataType.TEXT, "Insured country"),
    _f("insured_continent", DataType.TEXT, "Continent"),
    _f("main_class_of_business", DataType.TEXT, "Class of business"),
    _f("underwriting_year", DataType.INTEGER, "Underwriting year"),
    _f("booking_year", DataType.INTEGER, "Booking year"),
    _f("hub", DataType.TEXT, "Hub"),
    _f("reporting_unit", DataType.TEXT, "Reporting unit"),
    _f("type_of_business", DataType.TEXT, "Type of business"),
    _f("business_stream", DataType.TEXT, "Business stream"),
    _f("insured_period_start", DataType.DATE, "Period start"),
    _f("insured_period_end", DataType.DATE, "Period end"),
    _f(
        "gross_premium",
        DataType.MONEY,
        "Gross premium",
    ),
    _f("net_premium", DataType.MONEY, "Net premium"),
    _f(
        "gross_limit",
        DataType.MONEY,
        "Total insured value at KRE share",
        required=True,
        help_text=(
            "Confirmed as TIV at KRE's share in USD. The share is not applied "
            "again during loss calculation."
        ),
    ),
    _f("gross_paid", DataType.MONEY, "Gross paid"),
    _f(
        "gross_outstanding",
        DataType.MONEY,
        "Gross outstanding",
    ),
    _f(
        "gross_incurred",
        DataType.MONEY,
        "Gross incurred",
    ),
    _f("loss_ratio_pct", DataType.NUMBER, "Loss ratio %"),
    _f("renewed", DataType.TEXT, "Renewed"),
    _f("renewal_policy_id", DataType.TEXT, "Renewal policy reference"),
    _f("prior_share_pct", DataType.NUMBER, "Prior share %"),
    _f(
        "renewal_premium",
        DataType.MONEY,
        "Renewal premium",
    ),
    _f(
        "premium_growth_pct",
        DataType.NUMBER,
        "Premium growth %",
    ),
    _f(
        "renewal_share_pct",
        DataType.NUMBER,
        "Renewal share %",
    ),
    _f("share_change", DataType.TEXT, "Share change"),
    _f("risk_location_status", DataType.TEXT, "Geocoding status"),
    _f("risk_location_count", DataType.INTEGER, "Scheduled locations"),
    _f("risk_latitude", DataType.COORDINATE, "Primary latitude"),
    _f("risk_longitude", DataType.COORDINATE, "Primary longitude"),
    _f(
        "risk_location_address",
        DataType.TEXT,
        "Primary address",
    ),
    _f("risk_location_precision", DataType.TEXT, "Primary geocode precision"),
    _f("risk_location_method", DataType.TEXT, "Primary geocode method"),
    _f("risk_location_confidence", DataType.TEXT, "Primary geocode confidence"),
    _f("risk_location_needs_review", DataType.YES_NO, "Primary needs review"),
)

LOCATION_FIELDS: tuple[FieldSpec, ...] = (
    _f("business_id", DataType.TEXT, "Business reference", required=True),
    _f("location_number", DataType.INTEGER, "Location number", required=True),
    _f("primary_location", DataType.YES_NO, "Primary location", required=True),
    _f("status", DataType.TEXT, "Geocoding status"),
    _f("latitude", DataType.COORDINATE, "Latitude", required=True),
    _f("longitude", DataType.COORDINATE, "Longitude", required=True),
    _f(
        "risk_location_address",
        DataType.TEXT,
        "Address",
    ),
    _f(
        "provider_address",
        DataType.TEXT,
        "Provider address",
    ),
    _f("precision", DataType.TEXT, "Geocode precision", required=True),
    _f("method", DataType.TEXT, "Geocode method"),
    # Categorical, not numeric: the source records labels such as "medium",
    # "both_agents_risk" and "explicit_risk_heading". Reading it as a number
    # would fail every row, and the brief forbids using it as a value weight
    # in any case.
    _f("confidence", DataType.TEXT, "Geocode confidence"),
    _f("needs_review", DataType.YES_NO, "Needs review", required=True),
    # Optional, and absent from the 30 June 2026 extract. Where a source does
    # carry a value per site, there is no allocation question left to answer:
    # the reported_location_tiv_v1 method uses these and assumes nothing. It is
    # declared here so that a schedule which has them is read rather than
    # ignored, and it is not required so that one without them still parses
    # cleanly.
    _f("location_tiv", DataType.MONEY, "Reported location TIV"),
    _f("class_of_business", DataType.TEXT, "Class of business"),
    _f("country", DataType.TEXT, "Country"),
    _f("provider", DataType.TEXT, "Geocode provider"),
    _f("source_id", DataType.TEXT, "Provider source reference"),
    _f("rationale", DataType.TEXT, "Geocode rationale"),
)

POLICY_FIELD_MAP = {spec.name: spec for spec in POLICY_FIELDS}
LOCATION_FIELD_MAP = {spec.name: spec for spec in LOCATION_FIELDS}

#: ISO codes for the pilot countries, as the source spells them.
COUNTRY_CODES: dict[str, str] = {"indonesia": "ID", "nepal": "NP"}


#: What the source writes where a field does not apply. The renewal columns
#: carry an em dash on the 1,340 policies that were not renewed. That is a
#: deliberate "not applicable", not a corrupt number, and reporting it as one
#: would bury the findings that matter under four thousand that do not.
NOT_APPLICABLE: frozenset[str] = frozenset({"—", "–", "-", "--", "n/a", "na", "none"})


def is_not_applicable(value: object) -> bool:
    """Whether a cell is the source's marker for a field that does not apply."""
    return str(value or "").strip().lower() in NOT_APPLICABLE


def country_code(name: str) -> str:
    """Map a source country name to a validated ISO code.

    Returns an empty string for anything unrecognised rather than guessing. The
    brief maps country to a validated ``CountryCode``, and a wrong code would
    route a location to the wrong national grid.
    """
    return COUNTRY_CODES.get((name or "").strip().lower(), "")
