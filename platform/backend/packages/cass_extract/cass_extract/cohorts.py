"""Governed coordinate cohorts.

The rule the integration brief insists on: coordinate presence is not
coordinate eligibility. Every row in this extract has a valid latitude and
longitude, and 115 of the 224 still need review. Filtering on "has coordinates"
would put all of them into an automated benchmark and read geocoding noise as
concentration.

So eligibility is derived from the recorded ``precision`` and ``needs_review``
fields into three cohorts, every source row is preserved, and the rule version
that made the assignment is recorded with it. A later rule change produces a
new assignment rather than silently reinterpreting an old run.

A country screen runs before either field, because mapping the real extract
to the pilot grids found five rows whose coordinates are not in the country
they name -- one in Beirut and one on the Adriatic coast, both at street or
better precision with no review flag. A geocoder can be confident and wrong, so
neither the provider's precision nor the absence of a human flag is sufficient
on its own. Those rows are unclassified: not eligible for anything, and in the
queue for a person, who can correct the workbook or, where the coordinate is
right after all, confirm the row into a cohort with a reason.

The screen is handed in rather than held here. The rules say what to do with
its answer; where the country outlines come from, and how near a coast counts
as ashore, belong to the code that owns the outlines. Every call has to name
one, so no path can assign cohorts without screening.

The cohorts are not a quality ranking of the geocoder. They are a statement
about what each row is fit for: A for automated mapping, B for mapping whose
sensitivity has to be reported separately, C for work a person still owes, and
unclassified for a row the rules could not place at all.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any, Protocol

#: Bumped whenever an eligibility rule changes. Stored with each assignment.
#: 1.2.0 screens every country against its outline, where 1.1.0 screened
#: Indonesia and Nepal against a box each and left every other country
#: unclassified.
COHORT_RULE_VERSION = "1.2.0"


class Cohort(enum.StrEnum):
    """What a located row is fit for."""

    A = "A"
    """Automated test cohort: no review needed, and a precise geocode."""

    B = "B"
    """Geocoding-sensitivity cohort: no review needed, but a coarse geocode."""

    C = "C"
    """Analyst-review backlog: flagged for review, whatever its precision."""

    UNCLASSIFIED = "unclassified"
    """The rules did not reach a decision. Never treated as eligible."""

    @property
    def label(self) -> str:
        return {
            Cohort.A: "Automated test cohort",
            Cohort.B: "Geocoding-sensitivity cohort",
            Cohort.C: "Analyst-review backlog",
            Cohort.UNCLASSIFIED: "Unclassified",
        }[self]


class CountryScreen(Protocol):
    """Checks coordinates against the country their rows name.

    ``within`` answers, for each ``(latitude, longitude)``, whether it lies in
    the country the code names, allowing ``buffer_km`` for a coast or border
    drawn at map scale. It answers ``None`` for a code that names no country.
    Keyed by ISO code rather than country name: the intake template asks for
    the code, because a name has spellings and a code does not.
    """

    buffer_km: Decimal
    source: str

    def within(self, code: str, points: Sequence[tuple[Any, Any]]) -> Sequence[bool] | None: ...

    def name(self, code: str) -> str | None: ...


#: Codes people write for a country that ISO 3166-1 spells otherwise, so a
#: message can say which was meant rather than only that this one is wrong.
COMMON_MISCODES: Mapping[str, tuple[str, str]] = {
    "UK": ("GB", "the United Kingdom"),
    "EL": ("GR", "Greece"),
}


class Placement(enum.Enum):
    """What the screen said about one row's coordinate."""

    INSIDE = "inside"
    OUTSIDE = "outside"
    UNKNOWN_COUNTRY = "unknown_country"
    UNCHECKED = "unchecked"
    """No usable coordinate, so there was nothing to check."""


def place(locations: Sequence[Mapping[str, Any]], screen: CountryScreen) -> list[Placement]:
    """Screen every row at once, a country at a time."""
    placements = [Placement.UNCHECKED] * len(locations)
    by_country: dict[str, list[int]] = {}
    for index, location in enumerate(locations):
        if _usable(location):
            code = str(location.get("country_code") or "").strip().upper()
            by_country.setdefault(code, []).append(index)
    for code, members in by_country.items():
        points = [(locations[index]["latitude"], locations[index]["longitude"]) for index in members]
        answers = screen.within(code, points) if code else None
        for position, index in enumerate(members):
            if answers is None:
                placements[index] = Placement.UNKNOWN_COUNTRY
            elif answers[position]:
                placements[index] = Placement.INSIDE
            else:
                placements[index] = Placement.OUTSIDE
    return placements


def _usable(location: Mapping[str, Any]) -> bool:
    latitude, longitude = location.get("latitude"), location.get("longitude")
    if latitude is None or longitude is None:
        return False
    return not (Decimal(str(latitude)) == 0 and Decimal(str(longitude)) == 0)


def _plain(value: Decimal) -> str:
    text = format(Decimal(value), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _outside_reason(code: str, screen: CountryScreen) -> str:
    # Kept under the 200 characters a staged row stores, so it is never cut.
    return (
        f"The coordinate is more than {_plain(screen.buffer_km)} km outside "
        f"{screen.name(code) or code} ({code}), the country the row names. Correct "
        "it or the Country code, or confirm the row in review if it is right."
    )


def unknown_country_reason(code: str) -> str:
    """Why a row's country code stops its coordinate being checked, and what to do."""
    if not code:
        return (
            "The row names no country, so its coordinate cannot be checked against "
            "one. Fill in the Country column."
        )
    hint = COMMON_MISCODES.get(code)
    meant = f" ({hint[1]} is {hint[0]})" if hint else ""
    return (
        f"{code!r} is not an ISO 3166-1 country code{meant}, so the coordinate "
        "cannot be checked against a country. Correct the Country column."
    )


#: Precisions that place a no-review row in cohort A. These resolve to a
#: building or a street, so a grid cell assignment means what it says.
PRECISE = frozenset({"parcel", "street", "embedded"})

#: Precisions that place a no-review row in cohort B. These resolve to a
#: settlement or an administrative area, so the coordinate may be finer than
#: the evidence behind it and the mapping needs its own sensitivity report.
COARSE = frozenset({"locality", "postcode", "admin"})

#: Classes excluded from physical-damage testing. Liability carries no property
#: at the coordinate; Engineering is a separate workstream because construction
#: and operational plant need different occupancy, duration and vulnerability
#: treatment.
PHYSICAL_DAMAGE_CLASS = "Fire"


@dataclasses.dataclass(frozen=True, slots=True)
class Assignment:
    """One row's cohort, with the reason and the rule version behind it."""

    business_id: str
    location_number: int
    cohort: Cohort
    reason: str
    rule_version: str = COHORT_RULE_VERSION
    #: Which rule decided, as a stable code, so a caller can act on the kind of
    #: reason -- report every row outside its country, say -- without parsing
    #: the sentence.
    rule: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "business_id": self.business_id,
            "location_number": self.location_number,
            "cohort": str(self.cohort),
            "reason": self.reason,
            "rule_version": self.rule_version,
            "rule": self.rule,
        }


def assign(location: Mapping[str, Any], *, screen: CountryScreen) -> Assignment:
    """Assign one location row to a cohort; ``assign_all`` for more than one."""
    return assign_all([location], screen=screen)[0]


def assign_all(
    locations: Iterable[Mapping[str, Any]], *, screen: CountryScreen
) -> list[Assignment]:
    """Assign every row, screening the coordinates a country at a time."""
    locations = list(locations)
    placements = place(locations, screen)
    return [
        _assign(location, placement, screen)
        for location, placement in zip(locations, placements, strict=True)
    ]


def _assign(
    location: Mapping[str, Any], placement: Placement, screen: CountryScreen
) -> Assignment:
    """One row's cohort, given what the screen said about its coordinate.

    ``needs_review`` wins over precision outright. A row a reviewer has flagged
    does not become eligible because its precision happens to look good: the
    flag is a person's judgement about the row, and precision is the
    provider's about the match.
    """
    business_id = str(location.get("business_id") or "").strip()
    try:
        location_number = int(location.get("location_number") or 0)
    except (TypeError, ValueError):
        location_number = 0
    code = str(location.get("country_code") or "").strip().upper()

    def made(cohort: Cohort, rule: str, reason: str) -> Assignment:
        return Assignment(business_id, location_number, cohort, reason, rule=rule)

    latitude = location.get("latitude")
    longitude = location.get("longitude")
    if latitude is None or longitude is None:
        return made(
            Cohort.UNCLASSIFIED, "no_coordinates", "The row has no usable coordinate pair."
        )
    if Decimal(str(latitude)) == 0 and Decimal(str(longitude)) == 0:
        return made(
            Cohort.UNCLASSIFIED,
            "null_island",
            "The coordinate is (0, 0), which is a failed geocode rather than a place.",
        )

    # Checked before precision, and before the review flag, because a geocode
    # in the wrong country is wrong however confident the provider was and
    # whatever nobody flagged. Two rows in the 30 June 2026 extract reach
    # street or better precision, carry no review flag, and sit on another
    # continent. A code that names no country is the same kind of mistake:
    # nothing can be said about where the risk is until it is corrected.
    if placement is Placement.OUTSIDE:
        return made(Cohort.UNCLASSIFIED, "outside_country", _outside_reason(code, screen))
    if placement is Placement.UNKNOWN_COUNTRY:
        return made(Cohort.UNCLASSIFIED, "unknown_country", unknown_country_reason(code))

    if _is_yes(location.get("needs_review")):
        return made(Cohort.C, "review_flag", "A reviewer has flagged this location for review.")

    precision = str(location.get("precision") or "").strip().lower()
    if precision in PRECISE:
        return made(
            Cohort.A,
            "precise",
            f"No review needed and the geocode resolves to {precision} precision.",
        )
    if precision in COARSE:
        return made(
            Cohort.B,
            "coarse",
            f"No review needed, but the geocode resolves only to {precision} precision.",
        )
    return made(
        Cohort.UNCLASSIFIED,
        "unknown_precision",
        f"Geocode precision {precision or 'unknown'!r} is not a recognised level.",
    )


@dataclasses.dataclass(slots=True)
class CohortProfile:
    """Counts by cohort, country and class, for the review interface."""

    rule_version: str
    counts: dict[str, int]
    by_country: dict[str, dict[str, int]]
    by_class: dict[str, dict[str, int]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_version": self.rule_version,
            "counts": self.counts,
            "by_country": self.by_country,
            "by_class": self.by_class,
        }


def cohort_profile(
    locations: Iterable[Mapping[str, Any]], assignments: Iterable[Assignment]
) -> CohortProfile:
    """Summarise an assignment set the way the review screen shows it."""
    counts: dict[str, int] = {}
    by_country: dict[str, dict[str, int]] = {}
    by_class: dict[str, dict[str, int]] = {}

    for location, assignment in zip(locations, assignments, strict=True):
        cohort = str(assignment.cohort)
        counts[cohort] = counts.get(cohort, 0) + 1

        country = str(location.get("country_code") or "unknown").strip()
        by_country.setdefault(cohort, {})
        by_country[cohort][country] = by_country[cohort].get(country, 0) + 1

        class_of_business = str(location.get("class_of_business") or "unknown").strip()
        by_class.setdefault(cohort, {})
        by_class[cohort][class_of_business] = (
            by_class[cohort].get(class_of_business, 0) + 1
        )

    return CohortProfile(COHORT_RULE_VERSION, counts, by_country, by_class)


def business_complete(
    locations: Iterable[Mapping[str, Any]],
    assignments: Iterable[Assignment],
    *,
    cohort: Cohort = Cohort.A,
    class_of_business: str | None = PHYSICAL_DAMAGE_CLASS,
    country: str | None = None,
) -> set[str]:
    """Businesses where *every* scheduled location qualifies.

    This is the rule that makes the first earthquake benchmark defensible. Take
    a business's qualifying locations and leave its others behind, and the
    policy's TIV has to go somewhere: either it silently moves onto the
    included sites, overstating them, or it silently disappears. Requiring the
    whole schedule to qualify means neither happens, at the cost of a smaller
    benchmark -- which is the right trade.

    ``country`` narrows the same way, and for a concrete reason: a model
    version covers one country, so a portfolio spanning two cannot be run
    against either. Selecting by country is how a two-country book becomes two
    runs, and the whole-schedule rule means a business with sites in both is
    excluded from both rather than split across them. It takes the ISO code the
    intake template asks for, matched without regard to case.
    """
    schedules: dict[str, list[bool]] = {}
    for location, assignment in zip(locations, assignments, strict=True):
        business_id = str(location.get("business_id") or "").strip()
        qualifies = assignment.cohort is cohort
        if class_of_business is not None:
            qualifies = qualifies and (
                str(location.get("class_of_business") or "").strip() == class_of_business
            )
        if country is not None:
            qualifies = qualifies and (
                str(location.get("country_code") or "").strip().upper()
                == country.strip().upper()
            )
        schedules.setdefault(business_id, []).append(qualifies)

    return {
        business_id
        for business_id, flags in schedules.items()
        if business_id and flags and all(flags)
    }


def _is_yes(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("yes", "y", "true", "1")
