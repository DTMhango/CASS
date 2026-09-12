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

A coarse country screen runs before either field, because mapping the real
extract to the pilot grids found five rows whose coordinates are not in the
country they name -- one in Beirut and one on the Adriatic coast, both at
street or better precision with no review flag. A geocoder can be confident and
wrong, so neither the provider's precision nor the absence of a human flag is
sufficient on its own. Those rows are unclassified: not eligible for anything,
and in the queue for a person.

The cohorts are not a quality ranking of the geocoder. They are a statement
about what each row is fit for: A for automated mapping, B for mapping whose
sensitivity has to be reported separately, C for work a person still owes, and
unclassified for a row the rules could not place at all.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

#: Bumped whenever an eligibility rule changes. Stored with each assignment.
COHORT_RULE_VERSION = "1.1.0"


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


#: Generous national bounds for the pilot countries, as a coarse screen only.
#:
#: Not a boundary polygon and not a substitute for one: they are wide enough to
#: include water and neighbouring territory, and they exist to catch a geocode
#: that landed on the wrong continent rather than one that landed a mile over a
#: border. The 30 June 2026 extract carries five such rows, two of them at
#: street or better precision with no review flag -- a geocoder can be confident
#: and wrong, which is why precision alone cannot decide eligibility.
NATIONAL_BOUNDS: Mapping[str, tuple[Decimal, Decimal, Decimal, Decimal]] = {
    "indonesia": (Decimal("-11.5"), Decimal("6.5"), Decimal("94.5"), Decimal("141.5")),
    "nepal": (Decimal("26.0"), Decimal("30.7"), Decimal("79.9"), Decimal("88.4")),
}


def within_stated_country(location: Mapping[str, Any]) -> bool | None:
    """Whether a coordinate is plausibly inside the country the row names.

    ``None`` where the country is not one the screen knows, so an unrecognised
    country is reported as unscreened rather than quietly passed or failed.
    """
    bounds = NATIONAL_BOUNDS.get(str(location.get("country") or "").strip().lower())
    if bounds is None:
        return None
    latitude, longitude = location.get("latitude"), location.get("longitude")
    if latitude is None or longitude is None:
        return None
    minimum_latitude, maximum_latitude, minimum_longitude, maximum_longitude = bounds
    return (
        minimum_latitude <= Decimal(str(latitude)) <= maximum_latitude
        and minimum_longitude <= Decimal(str(longitude)) <= maximum_longitude
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "business_id": self.business_id,
            "location_number": self.location_number,
            "cohort": str(self.cohort),
            "reason": self.reason,
            "rule_version": self.rule_version,
        }


def assign(location: Mapping[str, Any]) -> Assignment:
    """Assign one location row to a cohort.

    ``needs_review`` is tested first and wins outright. A row a reviewer has
    flagged does not become eligible because its precision happens to look
    good: the flag is a person's judgement about the row, and precision is the
    provider's about the match.
    """
    business_id = str(location.get("business_id") or "").strip()
    try:
        location_number = int(location.get("location_number") or 0)
    except (TypeError, ValueError):
        location_number = 0

    def made(cohort: Cohort, reason: str) -> Assignment:
        return Assignment(business_id, location_number, cohort, reason)

    latitude = location.get("latitude")
    longitude = location.get("longitude")
    if latitude is None or longitude is None:
        return made(Cohort.UNCLASSIFIED, "The row has no usable coordinate pair.")
    if Decimal(str(latitude)) == 0 and Decimal(str(longitude)) == 0:
        return made(
            Cohort.UNCLASSIFIED,
            "The coordinate is (0, 0), which is a failed geocode rather than a place.",
        )

    # Checked before precision, and before the review flag, because a geocode
    # in the wrong country is wrong however confident the provider was and
    # whatever nobody flagged. Two rows in the 30 June 2026 extract reach
    # street or better precision, carry no review flag, and sit on another
    # continent.
    consistent = within_stated_country(location)
    if consistent is False:
        return made(
            Cohort.UNCLASSIFIED,
            f"The coordinate is outside {location.get('country')}, so it does not "
            "describe the risk the row names.",
        )

    if _is_yes(location.get("needs_review")):
        return made(Cohort.C, "A reviewer has flagged this location for review.")

    if consistent is None:
        return made(
            Cohort.UNCLASSIFIED,
            f"No country screen exists for {location.get('country') or 'an unnamed country'}, "
            "so the coordinate could not be checked against it.",
        )

    precision = str(location.get("precision") or "").strip().lower()
    if precision in PRECISE:
        return made(
            Cohort.A,
            f"No review needed and the geocode resolves to {precision} precision.",
        )
    if precision in COARSE:
        return made(
            Cohort.B,
            f"No review needed, but the geocode resolves only to {precision} precision.",
        )
    return made(
        Cohort.UNCLASSIFIED,
        f"Geocode precision {precision or 'unknown'!r} is not a recognised level.",
    )


def assign_all(locations: Iterable[Mapping[str, Any]]) -> list[Assignment]:
    return [assign(location) for location in locations]


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


def profile(
    locations: Iterable[Mapping[str, Any]], assignments: Iterable[Assignment]
) -> CohortProfile:
    """Summarise an assignment set the way the review screen shows it."""
    counts: dict[str, int] = {}
    by_country: dict[str, dict[str, int]] = {}
    by_class: dict[str, dict[str, int]] = {}

    for location, assignment in zip(locations, assignments, strict=True):
        cohort = str(assignment.cohort)
        counts[cohort] = counts.get(cohort, 0) + 1

        country = str(location.get("country") or "unknown").strip()
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
) -> set[str]:
    """Businesses where *every* scheduled location qualifies.

    This is the rule that makes the first earthquake benchmark defensible. Take
    a business's qualifying locations and leave its others behind, and the
    policy's TIV has to go somewhere: either it silently moves onto the
    included sites, overstating them, or it silently disappears. Requiring the
    whole schedule to qualify means neither happens, at the cost of a smaller
    benchmark -- which is the right trade.
    """
    schedules: dict[str, list[bool]] = {}
    for location, assignment in zip(locations, assignments, strict=True):
        business_id = str(location.get("business_id") or "").strip()
        qualifies = assignment.cohort is cohort
        if class_of_business is not None:
            qualifies = qualifies and (
                str(location.get("class_of_business") or "").strip() == class_of_business
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
