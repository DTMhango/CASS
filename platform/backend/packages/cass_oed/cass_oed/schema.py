"""The pinned OED schema subset that CASS supports.

Build plan section 8 requires the supported OED schema version to be pinned in
the engine compatibility matrix rather than following a moving development
specification, and section 16 lists the OED/ODS Tools compatibility baseline as
an open decision. This module is where that pin lives: a KRE-owned declaration
of the fields the platform reads, writes and validates, with the business
language the exposure workspace shows to an analyst.

It is deliberately a subset. A field absent here is not silently dropped -- the
validator reports it as an unrecognised column so an analyst can see that CASS
did not interpret it.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Mapping, Sequence

#: The OED specification release this subset was written against. Changing it
#: is a compatibility-matrix decision, not an edit.
OED_SCHEMA_VERSION = "4.0.0"


class FileKind(enum.StrEnum):
    """The four OED source inputs of build plan section 8."""

    LOCATION = "location"
    ACCOUNT = "account"
    REINS_INFO = "reins_info"
    REINS_SCOPE = "reins_scope"

    @property
    def label(self) -> str:
        return {
            FileKind.LOCATION: "Location file",
            FileKind.ACCOUNT: "Account file",
            FileKind.REINS_INFO: "Reinsurance Info file",
            FileKind.REINS_SCOPE: "Reinsurance Scope file",
        }[self]


class DataType(enum.StrEnum):
    TEXT = "text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    MONEY = "money"
    RATE = "rate"
    """A proportion in [0, 1]."""
    LATITUDE = "latitude"
    LONGITUDE = "longitude"
    DATE = "date"
    CURRENCY = "currency"
    PERIL = "peril"
    FLAG = "flag"


@dataclasses.dataclass(frozen=True, slots=True)
class FieldSpec:
    """One OED column as CASS interprets it.

    ``business_label`` and ``business_help`` are what the exposure workspace
    shows; section 3 requires engine terminology to be translated into analyst
    language while keeping the technical detail available.
    """

    name: str
    dtype: DataType
    business_label: str
    required: bool = False
    business_help: str = ""
    allowed: tuple[str, ...] | None = None
    minimum: float | None = None
    maximum: float | None = None
    #: Marks a field that carries insured value and therefore enters TIV
    #: reconciliation.
    is_tiv: bool = False
    #: Marks a financial term. Unsupported terms must be flagged rather than
    #: approximated, so they are enumerated rather than inferred.
    is_financial_term: bool = False


def _money(name: str, label: str, help_text: str = "", *, tiv: bool = False) -> FieldSpec:
    return FieldSpec(
        name=name,
        dtype=DataType.MONEY,
        business_label=label,
        business_help=help_text,
        minimum=0.0,
        is_tiv=tiv,
    )


#: The only deductible or limit basis CASS calculates: a flat monetary amount.
#: OED also defines 1 (percentage of TIV) and 2 (percentage of loss), and a
#: portfolio using either is refused rather than approximated as flat.
FLAT_TERM_BASIS = "0"


def _term_type(name: str, label: str) -> FieldSpec:
    """A deductible or limit basis column.

    OED requires one wherever the matching amount carries a value, so CASS has
    to read it: refusing the column outright made every portfolio with a
    deductible unrunnable -- rejected here if it named the basis, and rejected
    inside the engine if it did not.
    """
    return FieldSpec(
        name=name,
        dtype=DataType.INTEGER,
        business_label=label,
        allowed=(FLAT_TERM_BASIS,),
        business_help=(
            "0 for a flat monetary amount, which is what CASS calculates. "
            "Percentage bases are refused rather than approximated."
        ),
        is_financial_term=True,
    )


def _rate(name: str, label: str, help_text: str = "") -> FieldSpec:
    return FieldSpec(
        name=name,
        dtype=DataType.RATE,
        business_label=label,
        business_help=help_text,
        minimum=0.0,
        maximum=1.0,
        is_financial_term=True,
    )


LOCATION_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("PortNumber", DataType.TEXT, "Portfolio reference", required=True,
              business_help="Groups accounts into the portfolio being analysed."),
    FieldSpec("AccNumber", DataType.TEXT, "Account reference", required=True,
              business_help="Links the location to its account and policy terms."),
    FieldSpec("LocNumber", DataType.TEXT, "Location reference", required=True,
              business_help="Unique within the account; identifies the risk in results."),
    FieldSpec("BuildingID", DataType.INTEGER, "Building number",
              business_help="Distinguishes several buildings sharing one location."),
    FieldSpec("IsTenant", DataType.FLAG, "Tenant interest",
              business_help="Set where the insured occupies rather than owns the building."),
    FieldSpec("CountryCode", DataType.TEXT, "Country", required=True,
              business_help="ISO 3166-1 alpha-2 code; drives model and grid selection."),
    FieldSpec("Latitude", DataType.LATITUDE, "Latitude", required=True,
              minimum=-90.0, maximum=90.0,
              business_help="Decimal degrees. Drives the area-peril cell the risk falls in."),
    FieldSpec("Longitude", DataType.LONGITUDE, "Longitude", required=True,
              minimum=-180.0, maximum=180.0,
              business_help="Decimal degrees, east positive."),
    FieldSpec("StreetAddress", DataType.TEXT, "Street address",
              business_help="Used for geocoding review, never for calculation."),
    FieldSpec("PostalCode", DataType.TEXT, "Postal code"),
    FieldSpec("AreaCode", DataType.TEXT, "Administrative area",
              business_help="The OED area code for the country, not its name -- Indonesia "
                            "uses the BPS province numbers, so DKI Jakarta is 31. Conditions "
                            "enrichment priors, and the engine refuses a pair it does not know."),
    FieldSpec("OccupancyCode", DataType.TEXT, "Occupancy", required=True,
              business_help="OED occupancy code. Classified before construction taxonomy."),
    FieldSpec("ConstructionCode", DataType.TEXT, "Construction",
              business_help="OED construction code. Inferred from priors when absent."),
    FieldSpec("YearBuilt", DataType.INTEGER, "Year built", minimum=1000, maximum=2100,
              business_help="Reported construction year. Never inferred from code level."),
    FieldSpec("NumberOfStoreys", DataType.INTEGER, "Storeys", minimum=0, maximum=200,
              business_help="Supports a derived height class."),
    FieldSpec("LocPerilsCovered", DataType.PERIL, "Perils covered", required=True,
              business_help="Peril codes this location is exposed to, such as QEQ."),
    _money("BuildingTIV", "Building value", "Structural replacement value.", tiv=True),
    _money("OtherTIV", "Other structures value", "Value of other structures.", tiv=True),
    _money("ContentsTIV", "Contents value", "Contents replacement value.", tiv=True),
    _money("BITIV", "Business interruption value",
           "Modelled only where an approved BI model exists.", tiv=True),
    FieldSpec("LocCurrency", DataType.CURRENCY, "Location currency", required=True,
              business_help="Normalised to the run currency before Oasis generation."),
    _money("LocDed6All", "Location deductible", "Combined deductible applied at the location."),
    _money("LocLimit6All", "Location limit", "Combined limit applied at the location."),
    FieldSpec("LocPeril", DataType.PERIL, "Peril the location terms apply to",
              business_help="Which peril the deductible and limit are written against. "
                            "Required by OED wherever a location term carries a value."),
    _term_type("LocDedType6All", "Location deductible basis"),
    _term_type("LocLimitType6All", "Location limit basis"),
    FieldSpec("LocGroup", DataType.TEXT, "Location group",
              business_help="Used by reinsurance scope filters."),
    FieldSpec("OEDVersion", DataType.TEXT, "OED version",
              business_help="Recorded for lineage; CASS pins the supported version."),
)

ACCOUNT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("PortNumber", DataType.TEXT, "Portfolio reference", required=True),
    FieldSpec("AccNumber", DataType.TEXT, "Account reference", required=True),
    FieldSpec("AccCurrency", DataType.CURRENCY, "Account currency", required=True),
    FieldSpec("PolNumber", DataType.TEXT, "Policy reference", required=True),
    FieldSpec("PolPerilsCovered", DataType.PERIL, "Policy perils", required=True),
    FieldSpec("PolInceptionDate", DataType.DATE, "Inception date"),
    FieldSpec("PolExpiryDate", DataType.DATE, "Expiry date"),
    FieldSpec("LayerNumber", DataType.INTEGER, "Layer", minimum=1,
              business_help="Layers are evaluated in order; gaps are reported."),
    _rate("LayerParticipation", "Signed share",
          "The share CASS writes on this layer, as a proportion."),
    _money("LayerLimit", "Layer limit"),
    _money("LayerAttachment", "Layer attachment"),
    _money("PolDed6All", "Policy deductible"),
    _money("PolLimit6All", "Policy limit"),
    FieldSpec("PolPeril", DataType.PERIL, "Peril the policy terms apply to",
              business_help="Which peril the policy deductible and limit are written "
                            "against. Required by OED wherever a policy term carries a value."),
    _term_type("PolDedType6All", "Policy deductible basis"),
    _term_type("PolLimitType6All", "Policy limit basis"),
    FieldSpec("OEDVersion", DataType.TEXT, "OED version"),
)

REINS_INFO_FIELDS: tuple[FieldSpec, ...] = (
    # Integer, not text. OED numbers reinsurance contracts and ODS Tools reads
    # the column as one: a portfolio whose contracts were named "RE001" passed
    # validation here and then failed inside the engine while it parsed the
    # file, which is exactly the discovery this validator exists to prevent.
    FieldSpec("ReinsNumber", DataType.INTEGER, "Contract reference", required=True,
              business_help="OED numbers contracts. A name belongs in the contract name."),
    FieldSpec("ReinsLayerNumber", DataType.INTEGER, "Contract layer", minimum=1),
    FieldSpec("ReinsName", DataType.TEXT, "Contract name"),
    FieldSpec("ReinsPeril", DataType.PERIL, "Contract perils", required=True),
    FieldSpec("ReinsInceptionDate", DataType.DATE, "Inception date"),
    FieldSpec("ReinsExpiryDate", DataType.DATE, "Expiry date"),
    _rate("CededPercent", "Ceded share"),
    _money("RiskLimit", "Risk limit"),
    _money("RiskAttachment", "Risk attachment"),
    _money("OccLimit", "Occurrence limit"),
    _money("OccAttachment", "Occurrence attachment"),
    _rate("PlacedPercent", "Placed share"),
    FieldSpec("ReinsCurrency", DataType.CURRENCY, "Contract currency", required=True),
    FieldSpec("InuringPriority", DataType.INTEGER, "Inuring priority", required=True, minimum=1,
              business_help="Lower numbers inure to the benefit of higher ones."),
    FieldSpec("ReinsType", DataType.TEXT, "Contract type", required=True,
              allowed=("QS", "SS", "FAC", "PR", "CXL", "XL"),
              business_help="Quota share, surplus share, facultative, per risk or catastrophe excess."),
    FieldSpec("RiskLevel", DataType.TEXT, "Risk level",
              allowed=("LOC", "ACC", "POL", "LGR", "SEL"),
              business_help="The level at which risk terms apply."),
    FieldSpec("UseReinsDates", DataType.FLAG, "Apply contract dates"),
    FieldSpec("OEDVersion", DataType.TEXT, "OED version"),
)

REINS_SCOPE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("ReinsNumber", DataType.INTEGER, "Contract reference", required=True,
              business_help="Matches a contract in the Reinsurance Info file."),
    FieldSpec("PortNumber", DataType.TEXT, "Portfolio reference"),
    FieldSpec("AccNumber", DataType.TEXT, "Account reference"),
    FieldSpec("PolNumber", DataType.TEXT, "Policy reference"),
    FieldSpec("LocGroup", DataType.TEXT, "Location group"),
    FieldSpec("LocNumber", DataType.TEXT, "Location reference"),
    FieldSpec("CedantName", DataType.TEXT, "Cedant"),
    FieldSpec("ProducerName", DataType.TEXT, "Producer"),
    FieldSpec("LOB", DataType.TEXT, "Line of business"),
    FieldSpec("CountryCode", DataType.TEXT, "Country"),
    FieldSpec("ReinsTag", DataType.TEXT, "Contract tag"),
    _rate("CededPercent", "Ceded share for this scope row"),
    FieldSpec("OEDVersion", DataType.TEXT, "OED version"),
)

SCHEMA: Mapping[FileKind, tuple[FieldSpec, ...]] = {
    FileKind.LOCATION: LOCATION_FIELDS,
    FileKind.ACCOUNT: ACCOUNT_FIELDS,
    FileKind.REINS_INFO: REINS_INFO_FIELDS,
    FileKind.REINS_SCOPE: REINS_SCOPE_FIELDS,
}

#: Columns that identify a record for error reporting and reconciliation.
KEY_FIELDS: Mapping[FileKind, tuple[str, ...]] = {
    FileKind.LOCATION: ("PortNumber", "AccNumber", "LocNumber", "BuildingID"),
    FileKind.ACCOUNT: ("PortNumber", "AccNumber", "PolNumber", "LayerNumber"),
    FileKind.REINS_INFO: ("ReinsNumber", "ReinsLayerNumber"),
    FileKind.REINS_SCOPE: (),
}

#: TIV columns and the Oasis coverage type each maps to.
COVERAGE_TYPES: Mapping[str, int] = {
    "BuildingTIV": 1,
    "OtherTIV": 2,
    "ContentsTIV": 3,
    "BITIV": 4,
}

#: Earthquake peril codes CASS recognises. Section 6 requires each country
#: release to state whether secondary perils are modelled; codes appearing here
#: are recognised by the parser, not automatically modelled.
EARTHQUAKE_PERILS: Mapping[str, str] = {
    "QEQ": "Earthquake shake",
    "QFF": "Fire following earthquake",
    "QTS": "Tsunami",
    "QSL": "Sprinkler leakage",
    "QLS": "Earthquake landslide",
    "QLF": "Liquefaction",
}

#: OED's grouped peril codes, and the earthquake sub-perils each stands for.
#:
#: ``QQ1`` is OED's code for the whole earthquake group and ``AA1`` for every
#: peril there is; only their earthquake members are listed, because those are
#: the ones CASS can say anything about. A book covering either is covering
#: shake, and reading the code as a sub-peril of its own would report a
#: perfectly ordinary schedule as covering nothing this release models.
PERIL_GROUPS: Mapping[str, tuple[str, ...]] = {
    "QQ1": tuple(EARTHQUAKE_PERILS),
    "AA1": tuple(EARTHQUAKE_PERILS),
}

#: Sub-perils CASS can currently model. Everything else in a covered peril
#: group is reported as out of scope rather than silently included.
MODELLED_SUBPERILS: frozenset[str] = frozenset({"QEQ"})


def fields_for(kind: FileKind) -> tuple[FieldSpec, ...]:
    return SCHEMA[FileKind(kind)]


def field_map(kind: FileKind) -> Mapping[str, FieldSpec]:
    return {spec.name: spec for spec in fields_for(kind)}


def required_columns(kind: FileKind) -> tuple[str, ...]:
    return tuple(spec.name for spec in fields_for(kind) if spec.required)


def tiv_columns() -> tuple[str, ...]:
    return tuple(spec.name for spec in LOCATION_FIELDS if spec.is_tiv)


def financial_term_columns(kind: FileKind) -> tuple[str, ...]:
    return tuple(spec.name for spec in fields_for(kind) if spec.is_financial_term)


def expand_perils(value: str) -> tuple[str, ...]:
    """Split an OED peril list and expand its group codes.

    ``QQ1`` is the whole earthquake group and ``AA1`` is every peril. Expanding
    them here means the coverage report can state plainly which sub-perils a
    policy covers and which of those CASS actually models -- and that a book
    written with a group code is not read as covering nothing.
    """
    codes: list[str] = []
    for token in str(value or "").replace(" ", "").split(";"):
        if not token:
            continue
        group = PERIL_GROUPS.get(token.upper())
        if group is not None:
            codes.extend(group)
        else:
            codes.append(token.upper())
    seen: list[str] = []
    for code in codes:
        if code not in seen:
            seen.append(code)
    return tuple(seen)


def unmodelled_subperils(covered: Sequence[str]) -> tuple[str, ...]:
    """Return covered earthquake sub-perils that CASS does not model.

    Section 9 forbids labelling output simply as earthquake loss where material
    components are excluded, so this list feeds the scope statement published
    beside every result.
    """
    return tuple(
        code
        for code in covered
        if code in EARTHQUAKE_PERILS and code not in MODELLED_SUBPERILS
    )
