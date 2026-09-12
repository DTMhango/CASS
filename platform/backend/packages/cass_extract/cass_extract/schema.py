"""The Klapton Re geocoded policy extract, as a contract.

The 30 June 2026 extract is a two-sheet workbook of Klapton Reinsurance's own
book, not an OED file. It is described here rather than inferred at read time
so that a change of shape is a refusal with a reason, not a column quietly
arriving as ``None`` and a total quietly coming out wrong.

Three things this module fixes that the rest of the importer depends on.

**Sensitivity is a property of the column.** Insured, cedent and broker names,
the business title and the street addresses identify real counterparties. The
integration brief restricts them to roles that need them and keeps them out of
technical logs, so every field says whether it is confidential and the manifest
builder reads that rather than keeping its own list to fall out of date.

**Value is money.** ``gross_limit`` is the reported TIV at KRE's share in USD,
and it is read as ``Decimal`` from the first moment. A float here would make
the exact reconciliation the brief requires impossible to promise.

**The version is stated.** ``SCHEMA_VERSION`` identifies the shape this parser
was written against, and every import records it beside the source checksum, so
a later extract that moved a column can be told apart from one that did not.
"""

from __future__ import annotations

import dataclasses
import enum

#: The import profile, named as the integration brief names it. CASS is the
#: workspace; the profile is named for the source, which is Klapton Re's own
#: book -- the build plan reserves the company name for exactly that.
PROFILE_NAME = "Klapton Re geocoded policy extract"

#: The extract shape this parser understands. Bump on any column change.
#: Spelled out rather than abbreviated: the build plan forbids new ``kre_*``
#: technical namespaces, and this string is an identifier as well as a label.
SCHEMA_VERSION = "klapton-re-geocoded-policy-extract/2026-06-30.1"

#: The parser itself, recorded alongside the schema in the audit trail so a
#: reread of the same file by a later build is distinguishable.
PARSER_VERSION = "1.0.0"

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


class Sensitivity(enum.StrEnum):
    """Who may see a column, and whether it may be logged."""

    OPEN = "open"
    """Identifiers and codes. Safe in logs and support bundles."""

    RESTRICTED = "restricted"
    """Commercial detail: premium, limit, loss. Project members only."""

    CONFIDENTIAL = "confidential"
    """Names a counterparty or an address. Role-gated, never logged."""


@dataclasses.dataclass(frozen=True, slots=True)
class FieldSpec:
    """One source column as CASS interprets it."""

    name: str
    dtype: DataType
    business_label: str
    required: bool = False
    sensitivity: Sensitivity = Sensitivity.OPEN
    business_help: str = ""

    @property
    def is_confidential(self) -> bool:
        return self.sensitivity is Sensitivity.CONFIDENTIAL


def _f(
    name: str,
    dtype: DataType,
    label: str,
    *,
    required: bool = False,
    sensitivity: Sensitivity = Sensitivity.OPEN,
    help_text: str = "",
) -> FieldSpec:
    return FieldSpec(name, dtype, label, required, sensitivity, help_text)


POLICY_FIELDS: tuple[FieldSpec, ...] = (
    _f("policy_id", DataType.TEXT, "Policy reference", required=True),
    _f("business_id", DataType.TEXT, "Business reference", required=True),
    _f(
        "business_title",
        DataType.TEXT,
        "Business title",
        sensitivity=Sensitivity.CONFIDENTIAL,
    ),
    _f("insured_name", DataType.TEXT, "Insured", sensitivity=Sensitivity.CONFIDENTIAL),
    _f("cedent_name", DataType.TEXT, "Cedent", sensitivity=Sensitivity.CONFIDENTIAL),
    _f("broker_name", DataType.TEXT, "Broker", sensitivity=Sensitivity.CONFIDENTIAL),
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
        sensitivity=Sensitivity.RESTRICTED,
    ),
    _f("net_premium", DataType.MONEY, "Net premium", sensitivity=Sensitivity.RESTRICTED),
    _f(
        "gross_limit",
        DataType.MONEY,
        "Total insured value at KRE share",
        required=True,
        sensitivity=Sensitivity.RESTRICTED,
        help_text=(
            "Confirmed as TIV at KRE's share in USD. The share is not applied "
            "again during loss calculation."
        ),
    ),
    _f("gross_paid", DataType.MONEY, "Gross paid", sensitivity=Sensitivity.RESTRICTED),
    _f(
        "gross_outstanding",
        DataType.MONEY,
        "Gross outstanding",
        sensitivity=Sensitivity.RESTRICTED,
    ),
    _f(
        "gross_incurred",
        DataType.MONEY,
        "Gross incurred",
        sensitivity=Sensitivity.RESTRICTED,
    ),
    _f("loss_ratio_pct", DataType.NUMBER, "Loss ratio %", sensitivity=Sensitivity.RESTRICTED),
    _f("renewed", DataType.TEXT, "Renewed"),
    _f("renewal_policy_id", DataType.TEXT, "Renewal policy reference"),
    _f("prior_share_pct", DataType.NUMBER, "Prior share %", sensitivity=Sensitivity.RESTRICTED),
    _f(
        "renewal_premium",
        DataType.MONEY,
        "Renewal premium",
        sensitivity=Sensitivity.RESTRICTED,
    ),
    _f(
        "premium_growth_pct",
        DataType.NUMBER,
        "Premium growth %",
        sensitivity=Sensitivity.RESTRICTED,
    ),
    _f(
        "renewal_share_pct",
        DataType.NUMBER,
        "Renewal share %",
        sensitivity=Sensitivity.RESTRICTED,
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
        sensitivity=Sensitivity.CONFIDENTIAL,
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
        sensitivity=Sensitivity.CONFIDENTIAL,
    ),
    _f(
        "provider_address",
        DataType.TEXT,
        "Provider address",
        sensitivity=Sensitivity.CONFIDENTIAL,
    ),
    _f("precision", DataType.TEXT, "Geocode precision", required=True),
    _f("method", DataType.TEXT, "Geocode method"),
    # Categorical, not numeric: the source records labels such as "medium",
    # "both_agents_risk" and "explicit_risk_heading". Reading it as a number
    # would fail every row, and the brief forbids using it as a value weight
    # in any case.
    _f("confidence", DataType.TEXT, "Geocode confidence"),
    _f("needs_review", DataType.YES_NO, "Needs review", required=True),
    _f("class_of_business", DataType.TEXT, "Class of business"),
    _f("country", DataType.TEXT, "Country"),
    _f("provider", DataType.TEXT, "Geocode provider"),
    _f("source_id", DataType.TEXT, "Provider source reference"),
    _f("rationale", DataType.TEXT, "Geocode rationale"),
)

POLICY_FIELD_MAP = {spec.name: spec for spec in POLICY_FIELDS}
LOCATION_FIELD_MAP = {spec.name: spec for spec in LOCATION_FIELDS}

#: Columns that must never reach a log, a support bundle or a manifest that has
#: not been explicitly asked to include them.
CONFIDENTIAL_COLUMNS: frozenset[str] = frozenset(
    spec.name
    for spec in (*POLICY_FIELDS, *LOCATION_FIELDS)
    if spec.is_confidential
)

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
