"""The CASS intake profile: business columns bound to OED fields.

The platform asks for a portfolio one risk at a time, in language a
underwriter or analyst reads without a schema in front of them -- "Building
value", not ``BuildingTIV``; "Signed share", not ``LayerParticipation``. This
module is the table that makes those two the same thing.

It is deliberately data rather than code, and it is the single source of truth
for three consumers: the blank template a user downloads, the reader that
interprets a completed one, and any later loading API that populates CASS
directly from a source system. A mapping that lived in three places would drift
between them, and the drift would be silent -- a column quietly landing in no
OED field at all.

Why one row per risk. The earlier intake shape described a risk across two
sheets, with insured value on the policy and geography on the locations, and no
key the supplier had filled in. Everything expensive about it followed from
that: a join CASS had to infer, and an allocation assumption on every policy
holding more than one site. A risk is a row here, and the account reference
appears on both sheets, so the join is stated rather than reconstructed.

Two sheets, not one, for the same reason OED has four files. A deductible
belongs to a policy and a coordinate belongs to a building, and flattening them
together would repeat policy terms on every risk row and invite them to
disagree. Policy terms are optional: a portfolio without them still runs, at
ground-up loss only, and CASS says so rather than inventing a term.

**No column is classified.** An earlier draft graded these by sensitivity and
role-gated the address, on the reading that a risk address was counterparty
detail. It is not: a Klapton Re portfolio holds no information a Klapton Re
colleague may not see, and a modeller who cannot read an address cannot check
a coordinate against it, which is the review. What remains is a narrower
operational rule that has nothing to do with roles -- whole source rows are not
dumped into logs or support bundles, because those travel further than the
platform does.

**Blank is an answer.** Every optional column declares what CASS does when it
is empty, and the answer is never "treat it as zero". An unknown occupancy
reaches a named assumption; an unstated risk value reaches the allocation
engine; an unstated policy term removes a perspective and says which. The
alternative -- a template that implies knowledge the person filling it in does
not have -- produces confident numbers resting on blanks, which is the failure
this whole profile exists to prevent.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Iterator, Mapping
from typing import Any

from cass_oed.schema import (
    ACCOUNT_FIELDS,
    LOCATION_FIELDS,
    OED_SCHEMA_VERSION,
    DataType,
    FieldSpec,
    FileKind,
    field_map,
)

#: Bumped when a column is added, removed or rebound to a different OED field.
#: A completed template records it, so a file filled in under an earlier profile
#: stays interpretable.
PROFILE_NAME = "CASS portfolio intake"
PROFILE_VERSION = "cass-portfolio-intake/1.0.0"

#: The sheet names a workbook carries.
RISK_SHEET = "Risks"
POLICY_SHEET = "Policies"
GUIDE_SHEET = "How to fill this in"


class Sheet(enum.StrEnum):
    """Which sheet a column belongs to."""

    RISK = RISK_SHEET
    POLICY = POLICY_SHEET


class WhenBlank(enum.StrEnum):
    """What CASS does with an empty cell.

    Never "zero". The distinction between a value of nothing and no value at
    all is the one an exposure platform cannot afford to lose.
    """

    REFUSED = "refused"
    """The row cannot be interpreted. The import reports it and stops."""

    ASSUMED = "assumed"
    """A named, versioned assumption fills it, and the version records which."""

    ALLOCATED = "allocated"
    """Derived from the policy total under a selectable allocation scenario."""

    ABSENT = "absent"
    """Written to OED as genuinely absent. Nothing is inferred."""

    LIMITS_PERSPECTIVE = "limits_perspective"
    """Modelling continues, but a loss perspective becomes unavailable."""


@dataclasses.dataclass(frozen=True, slots=True)
class Column:
    """One template column, in business language, bound to an OED field.

    ``oed_field`` is the binding a loading API would follow. It is optional
    only for the handful of columns that feed a CASS calculation rather than an
    OED file, and those must say what they feed instead -- a column with no
    destination and no purpose is a column nobody can account for.
    """

    name: str
    sheet: Sheet
    oed_field: str | None
    oed_kind: FileKind | None
    required: bool
    help_text: str
    when_blank: WhenBlank
    blank_effect: str = ""
    example: str = ""
    #: Why this column has no OED destination. Required when ``oed_field`` is
    #: None and forbidden otherwise.
    purpose: str = ""
    #: How to read the cell. Taken from the bound OED field where there is one,
    #: so a column can never be validated as a different type from the field it
    #: lands in; stated explicitly only for the CASS-only columns.
    dtype: DataType | None = None

    def __post_init__(self) -> None:
        if self.oed_field is None and not self.purpose:
            raise ProfileError(
                f"Column {self.name!r} names no OED field and no purpose. Every "
                "column either lands somewhere in OED or says what it feeds instead."
            )
        if self.oed_field is not None and self.purpose:
            raise ProfileError(
                f"Column {self.name!r} names both an OED field and a purpose. The "
                "purpose exists for columns OED has no home for."
            )
        if self.required and self.when_blank is not WhenBlank.REFUSED:
            raise ProfileError(
                f"Column {self.name!r} is required but declares a behaviour for being "
                "blank. A required column that has a fallback is optional."
            )
        if not self.required and self.when_blank is WhenBlank.REFUSED:
            raise ProfileError(
                f"Column {self.name!r} is optional but refuses a blank, which no "
                "person filling in the template could act on."
            )
        if self.oed_field is None and self.dtype is None:
            raise ProfileError(
                f"Column {self.name!r} has no OED field to take its type from, so it "
                "must state one."
            )
        if not self.example:
            raise ProfileError(
                f"Column {self.name!r} shows no example. A person filling in a "
                "column reads the example before the explanation, and a column "
                "with none is the one that comes back wrong."
            )

    @property
    def spec(self) -> FieldSpec | None:
        """The OED field this column lands in, where it lands in one."""
        if self.oed_field is None or self.oed_kind is None:
            return None
        return field_map(self.oed_kind).get(self.oed_field)

    @property
    def reads_as(self) -> DataType:
        """How to interpret the cell."""
        if self.dtype is not None:
            return self.dtype
        spec = self.spec
        if spec is None:  # pragma: no cover - validate() refuses this
            raise ProfileError(f"Column {self.name!r} binds to no known OED field.")
        return spec.dtype

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sheet": str(self.sheet),
            "oed_field": self.oed_field,
            "oed_file": str(self.oed_kind) if self.oed_kind else None,
            "required": self.required,
            "help": self.help_text,
            "when_blank": str(self.when_blank),
            "blank_effect": self.blank_effect,
            "example": self.example,
            "purpose": self.purpose,
            "reads_as": str(self.reads_as),
        }


class ProfileError(Exception):
    """Raised when the profile itself is inconsistent."""





def _risk(
    name: str,
    oed_field: str | None,
    *,
    required: bool = False,
    help_text: str,
    when_blank: WhenBlank = WhenBlank.ABSENT,
    blank_effect: str = "",
    example: str = "",
    purpose: str = "",
    dtype: DataType | None = None,
) -> Column:
    return Column(
        name=name,
        sheet=Sheet.RISK,
        oed_field=oed_field,
        oed_kind=FileKind.LOCATION if oed_field else None,
        required=required,
        help_text=help_text,
        when_blank=WhenBlank.REFUSED if required else when_blank,
        blank_effect=blank_effect,
        example=example,
        purpose=purpose,
        dtype=dtype,
    )


def _policy(
    name: str,
    oed_field: str | None,
    *,
    required: bool = False,
    help_text: str,
    when_blank: WhenBlank = WhenBlank.ABSENT,
    blank_effect: str = "",
    example: str = "",
    purpose: str = "",
    dtype: DataType | None = None,
) -> Column:
    return Column(
        name=name,
        sheet=Sheet.POLICY,
        oed_field=oed_field,
        oed_kind=FileKind.ACCOUNT if oed_field else None,
        required=required,
        help_text=help_text,
        when_blank=WhenBlank.REFUSED if required else when_blank,
        blank_effect=blank_effect,
        example=example,
        purpose=purpose,
        dtype=dtype,
    )


RISK_COLUMNS: tuple[Column, ...] = (
    _risk(
        "Policy ID",
        "AccNumber",
        required=True,
        help_text=(
            "The policy this risk sits under, as the premium system writes it: "
            "underwriting year, inception month and business reference, joined by "
            "underscores -- 2026_06_PFAC8716. The business reference on its own is "
            "not enough, because the same business renews into a new year and a "
            "new policy. It is the key that joins a risk to its policies, so it "
            "must match the Policies sheet exactly."
        ),
        example="2026_06_PFAC8716",
    ),
    _risk(
        "Risk reference",
        "LocNumber",
        required=True,
        help_text=(
            "Identifies this property within its policy -- the location number the "
            "risk carries in the premium system, numbered from 1. Unique within "
            "the policy, not across the portfolio, and it is what results are "
            "reported against."
        ),
        example="1",
    ),
    _risk(
        "Risk name",
        None,
        help_text="A description to recognise the site by. Never used in calculation.",
        blank_effect="The risk is identified by its account and risk reference alone.",
        example="Cikarang warehouse",
        dtype=DataType.TEXT,
        purpose=(
            "Shown in the review interface so a person can recognise a row. OED "
            "has no field for it and it never reaches a model."
        ),
    ),
    _risk(
        "Country",
        "CountryCode",
        required=True,
        help_text=(
            "ISO two-letter code. Selects the model and the area-peril grid, so a "
            "wrong code routes the risk to the wrong country's hazard."
        ),
        example="ID",
    ),
    _risk(
        "Latitude",
        "Latitude",
        required=True,
        help_text="Decimal degrees, south negative. Decides the area-peril cell.",
        example="-6.208800",
    ),
    _risk(
        "Longitude",
        "Longitude",
        required=True,
        help_text="Decimal degrees, east positive.",
        example="106.845600",
    ),
    _risk(
        "Address",
        "StreetAddress",
        help_text=(
            "Used for geocoding review, never for calculation. Visible to every "
            "project member: checking a coordinate against its address is the "
            "review, and a modeller who cannot see it cannot do it."
        ),
        blank_effect="Coordinates cannot be checked against an address during review.",
        example="Jl. Jababeka Raya Blok F, Cikarang",
    ),
    _risk(
        "Postal code",
        "PostalCode",
        help_text="Supports geocoding review.",
        blank_effect="No effect on the model.",
        example="17530",
    ),
    _risk(
        "Administrative area",
        "AreaCode",
        help_text="Province or equivalent. Conditions enrichment priors.",
        blank_effect="Enrichment priors fall back to the country level.",
        example="32",
    ),
    _risk(
        "Geocode precision",
        None,
        help_text=(
            "How precisely the coordinate was resolved: parcel, street, embedded, "
            "locality, postcode or admin. Decides whether the risk is eligible for "
            "automated modelling or needs review."
        ),
        blank_effect=(
            "The risk cannot be placed in an eligibility cohort and waits for review."
        ),
        example="street",
        dtype=DataType.TEXT,
        purpose=(
            "Drives the coordinate-eligibility cohorts. It describes the quality of "
            "an OED field rather than being one, so OED has no home for it."
        ),
    ),
    _risk(
        "Needs review",
        None,
        help_text="Yes where a person has flagged this coordinate as uncertain.",
        blank_effect="Read as no. The eligibility cohort then rests on precision alone.",
        example="No",
        dtype=DataType.FLAG,
        purpose=(
            "A reviewer's judgement about the row, which outranks the provider's "
            "own precision. It is CASS workflow state, not exposure data."
        ),
    ),
    _risk(
        "Primary site",
        None,
        help_text=(
            "Yes on the main site of an account that holds several. Only "
            "meaningful where the risk values are left blank: it is what the "
            "primary-concentrated allocation sensitivity concentrates on."
        ),
        blank_effect=(
            "Read as no. An account with no primary site marked cannot run the "
            "primary-concentrated sensitivity, and the run says so rather than "
            "picking one."
        ),
        example="Yes",
        dtype=DataType.FLAG,
        purpose=(
            "Feeds the primary-concentrated allocation sensitivity. It is a "
            "statement about which site matters commercially, not about the "
            "building, so OED has no field for it -- and it is never treated as a "
            "measure of value in its own right."
        ),
    ),
    _risk(
        "Class of business",
        None,
        help_text=(
            "The class this risk is written under, such as Fire, Engineering or "
            "Liability. Decides what is in scope for physical-damage modelling."
        ),
        blank_effect=(
            "The risk is not filtered by class, so it enters any selection its "
            "cohort qualifies for."
        ),
        example="Fire",
        dtype=DataType.TEXT,
        purpose=(
            "A CASS scope filter. Engineering needs different occupancy, duration "
            "and vulnerability treatment, and Liability carries no property at the "
            "coordinate at all, so neither belongs in a physical-damage benchmark. "
            "OED has no field for the class a risk is written under."
        ),
    ),
    _risk(
        "Total insured value",
        None,
        help_text=(
            "The whole value at this risk, where you know the total but not how it "
            "splits between building, contents and the rest. Leave it blank if you "
            "have filled in the individual coverages above."
        ),
        when_blank=WhenBlank.ALLOCATED,
        blank_effect=(
            "Where the coverage columns are also blank, the policy's total is "
            "divided across its risks under a selectable allocation scenario."
        ),
        example="1500000.00",
        dtype=DataType.MONEY,
        purpose=(
            "Feeds the coverage-component split. OED holds value per coverage, not "
            "a risk total, so nothing is written from this column directly: a named "
            "component assumption divides it into the four OED columns and the "
            "version records which one."
        ),
    ),
    _risk(
        "Occupancy",
        "OccupancyCode",
        help_text=(
            "OED occupancy code, such as 1100 for general commercial. Together "
            "with construction it decides the vulnerability function."
        ),
        when_blank=WhenBlank.ASSUMED,
        blank_effect=(
            "The occupancy assumption chosen for the run fills it, and the version "
            "records which one. Leave it blank rather than guessing: an assumption "
            "CASS records is auditable, and a guess typed into a cell is not."
        ),
        example="1100",
    ),
    _risk(
        "Construction",
        "ConstructionCode",
        help_text="OED construction code, such as 5150 for reinforced concrete.",
        when_blank=WhenBlank.ASSUMED,
        blank_effect="Filled by the occupancy assumption, usually as unknown (5000).",
        example="5150",
    ),
    _risk(
        "Year built",
        "YearBuilt",
        help_text="Reported construction year. Never inferred from a code level.",
        blank_effect="No vintage is assumed and none is written.",
        example="2011",
    ),
    _risk(
        "Storeys",
        "NumberOfStoreys",
        help_text="Supports a derived height class.",
        blank_effect="No height class is derived.",
        example="2",
    ),
    _risk(
        "Perils covered",
        "LocPerilsCovered",
        required=True,
        help_text=(
            "Peril codes this risk is exposed to, separated by semicolons. QEQ is "
            "earthquake shake; QQ means the whole earthquake group."
        ),
        example="QEQ",
    ),
    _risk(
        "Currency",
        "LocCurrency",
        required=True,
        help_text="Currency of the values on this row, normalised before the run.",
        example="USD",
    ),
    _risk(
        "Building value",
        "BuildingTIV",
        help_text="Structural replacement value at this risk.",
        when_blank=WhenBlank.ALLOCATED,
        blank_effect=(
            "Where every value on the row is blank, the policy's total is divided "
            "across its risks under a selectable allocation scenario, and the "
            "version records which. Where some are filled and some are not, the "
            "blanks are read as nothing at that coverage."
        ),
        example="1500000.00",
    ),
    _risk(
        "Other structures value",
        "OtherTIV",
        help_text="Value of other structures and machinery.",
        when_blank=WhenBlank.ALLOCATED,
        blank_effect="As for building value.",
        example="120000.00",
    ),
    _risk(
        "Contents value",
        "ContentsTIV",
        help_text="Contents and stock replacement value.",
        when_blank=WhenBlank.ALLOCATED,
        blank_effect="As for building value.",
        example="450000.00",
    ),
    _risk(
        "Business interruption value",
        "BITIV",
        help_text="Modelled only where an approved business-interruption model exists.",
        when_blank=WhenBlank.ALLOCATED,
        blank_effect="As for building value.",
        example="300000.00",
    ),
    _risk(
        "Risk deductible",
        "LocDed6All",
        help_text="Combined deductible applying at this risk.",
        when_blank=WhenBlank.LIMITS_PERSPECTIVE,
        blank_effect="No deductible is applied at the risk. Ground-up loss is unaffected.",
        example="25000.00",
    ),
    _risk(
        "Risk limit",
        "LocLimit6All",
        help_text="Combined limit applying at this risk.",
        when_blank=WhenBlank.LIMITS_PERSPECTIVE,
        blank_effect="No limit is applied at the risk. Ground-up loss is unaffected.",
        example="1200000.00",
    ),
)


POLICY_COLUMNS: tuple[Column, ...] = (
    _policy(
        "Policy ID",
        "AccNumber",
        required=True,
        help_text=(
            "The policy these terms apply to, written exactly as on the Risks "
            "sheet: underwriting year, inception month and business reference. "
            "This is the join CASS no longer has to guess."
        ),
        example="2026_06_PFAC8716",
    ),
    _policy(
        "Policy reference",
        "PolNumber",
        required=True,
        help_text="Identifies the policy or section within the account.",
        example="P-10802-01",
    ),
    _policy(
        "Currency",
        "AccCurrency",
        required=True,
        help_text="Currency of the terms on this row.",
        example="USD",
    ),
    _policy(
        "Perils covered",
        "PolPerilsCovered",
        required=True,
        help_text="Peril codes the policy responds to.",
        example="QEQ",
    ),
    _policy(
        "Total insured value",
        None,
        help_text=(
            "The policy's total insured value, at the share CASS writes. Fill this "
            "in only where the individual risk values are not known: it is what "
            "the allocation scenarios divide."
        ),
        when_blank=WhenBlank.ABSENT,
        blank_effect=(
            "Nothing to allocate, which is the better case: the risk rows carry "
            "their own values and no allocation assumption is made at all."
        ),
        example="3500000.00",
        dtype=DataType.MONEY,
        purpose=(
            "Feeds the location allocation where risk values are blank. OED holds "
            "insured value at the location, so there is no account-level field for "
            "it and none is written."
        ),
    ),
    _policy(
        "Inception date",
        "PolInceptionDate",
        help_text="Start of the period of cover.",
        blank_effect="No period is written; contract dates cannot be applied.",
        example="2026-06-11",
    ),
    _policy(
        "Expiry date",
        "PolExpiryDate",
        help_text="End of the period of cover.",
        blank_effect="No period is written; contract dates cannot be applied.",
        example="2027-06-11",
    ),
    _policy(
        "Layer",
        "LayerNumber",
        help_text="Layer number where the policy is layered. Layers apply in order.",
        blank_effect="Read as a single layer.",
        example="1",
    ),
    _policy(
        "Signed share",
        "LayerParticipation",
        help_text=(
            "The proportion of the layer CASS writes, between 0 and 1. Supply it "
            "only where the values above are at 100% of the risk."
        ),
        when_blank=WhenBlank.ABSENT,
        blank_effect=(
            "No share is applied. Values are taken to be already at the share "
            "CASS writes, which is what the results then represent."
        ),
        example="0.15",
    ),
    _policy(
        "Layer limit",
        "LayerLimit",
        help_text="Limit of this layer.",
        when_blank=WhenBlank.LIMITS_PERSPECTIVE,
        blank_effect="Insured loss cannot be calculated for this policy.",
        example="5000000.00",
    ),
    _policy(
        "Layer attachment",
        "LayerAttachment",
        help_text="Attachment point of this layer.",
        when_blank=WhenBlank.LIMITS_PERSPECTIVE,
        blank_effect="Insured loss cannot be calculated for this policy.",
        example="1000000.00",
    ),
    _policy(
        "Policy deductible",
        "PolDed6All",
        help_text="Combined deductible applying at the policy.",
        when_blank=WhenBlank.LIMITS_PERSPECTIVE,
        blank_effect="No policy deductible is applied.",
        example="250000.00",
    ),
    _policy(
        "Policy limit",
        "PolLimit6All",
        help_text="Combined limit applying at the policy.",
        when_blank=WhenBlank.LIMITS_PERSPECTIVE,
        blank_effect="No policy limit is applied.",
        example="10000000.00",
    ),
)


COLUMNS: tuple[Column, ...] = (*RISK_COLUMNS, *POLICY_COLUMNS)

#: OED fields CASS supplies itself rather than asking for. A template that
#: asked for them would invite a person to disagree with the platform about a
#: fact the platform owns.
DERIVED_FIELDS: Mapping[str, str] = {
    "PortNumber": "The CASS project reference the import belongs to.",
    "OEDVersion": f"The OED release CASS is pinned to ({OED_SCHEMA_VERSION}).",
    "BuildingID": "Written as one building per risk row unless a source states otherwise.",
    # OED requires a basis and a peril beside every financial amount, and the
    # engine refuses a file without them. Neither is a question for the person
    # filling in the template: CASS calculates flat monetary terms for the
    # peril it models, so it writes both rather than asking.
    "LocPeril": "QEQ: the peril CASS models, which the risk's terms are written against.",
    "LocDedType6All": "0, a flat monetary amount -- the only deductible basis CASS calculates.",
    "LocLimitType6All": "0, a flat monetary amount -- the only limit basis CASS calculates.",
    "PolPeril": "QEQ: the peril CASS models, which the policy's terms are written against.",
    "PolDedType6All": "0, a flat monetary amount -- the only deductible basis CASS calculates.",
    "PolLimitType6All": "0, a flat monetary amount -- the only limit basis CASS calculates.",
}


def columns_for(sheet: Sheet) -> tuple[Column, ...]:
    return tuple(item for item in COLUMNS if item.sheet is sheet)


def column(name: str) -> Column:
    """Look up a column by the name a person sees."""
    for item in COLUMNS:
        if item.name.strip().lower() == name.strip().lower():
            return item
    raise KeyError(f"{name!r} is not a CASS intake column.")


def oed_binding(sheet: Sheet) -> dict[str, str]:
    """Template column to OED field, for one sheet.

    This is the mapping a loading API follows. Returned as plain data so it can
    be published in the schema endpoint rather than reimplemented by a caller.
    """
    return {
        item.name: item.oed_field
        for item in columns_for(sheet)
        if item.oed_field is not None
    }


def unmapped_oed_fields(kind: FileKind) -> tuple[FieldSpec, ...]:
    """OED fields of one file the template neither asks for nor derives.

    Reported rather than left to be discovered. A reader who assumes the
    template covers OED would be wrong about deductibles, tenancy and location
    grouping, and the honest place to find that out is here.
    """
    bound = {item.oed_field for item in COLUMNS if item.oed_kind is kind}
    fields = LOCATION_FIELDS if kind is FileKind.LOCATION else ACCOUNT_FIELDS
    return tuple(
        spec
        for spec in fields
        if spec.name not in bound and spec.name not in DERIVED_FIELDS
    )


def validate() -> Iterator[str]:
    """Problems with the profile itself, as messages.

    Run as a test rather than at import: a broken profile should fail a build,
    not a user's upload.
    """
    location_names = {spec.name for spec in LOCATION_FIELDS}
    account_names = {spec.name for spec in ACCOUNT_FIELDS}

    seen: dict[tuple[Sheet, str], int] = {}
    for item in COLUMNS:
        key = (item.sheet, item.name.lower())
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            yield f"{item.sheet} repeats the column {item.name!r}."

        if item.oed_field is None:
            continue
        known = location_names if item.oed_kind is FileKind.LOCATION else account_names
        if item.oed_field not in known:
            yield (
                f"Column {item.name!r} binds to {item.oed_field!r}, which the pinned "
                f"OED {item.oed_kind} schema does not define."
            )
        if item.oed_field in DERIVED_FIELDS:
            yield (
                f"Column {item.name!r} asks for {item.oed_field!r}, which CASS "
                "supplies itself."
            )

    for kind, fields in ((FileKind.LOCATION, LOCATION_FIELDS), (FileKind.ACCOUNT, ACCOUNT_FIELDS)):
        bound = {item.oed_field for item in COLUMNS if item.oed_kind is kind}
        for spec in fields:
            if spec.required and spec.name not in bound and spec.name not in DERIVED_FIELDS:
                yield (
                    f"OED requires {spec.name!r} on the {kind} file, but the template "
                    "neither asks for it nor derives it."
                )


def as_dict() -> dict[str, Any]:
    """The whole profile, for the schema endpoint and the guidance sheet."""
    return {
        "profile_version": PROFILE_VERSION,
        "oed_schema_version": OED_SCHEMA_VERSION,
        "sheets": {
            str(sheet): [item.as_dict() for item in columns_for(sheet)]
            for sheet in Sheet
        },
        "derived_fields": dict(DERIVED_FIELDS),
        "oed_fields_not_requested": {
            str(kind): [spec.name for spec in unmapped_oed_fields(kind)]
            for kind in (FileKind.LOCATION, FileKind.ACCOUNT)
        },
    }
