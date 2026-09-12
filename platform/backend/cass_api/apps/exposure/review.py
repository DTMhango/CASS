"""What a reviewer decided, laid over what the source said.

The integration brief's rule for work package 2 is short and it settles the
design: an analyst may change a cohort decision "only by recording a rationale",
and the change "creates a new derived exposure version; it does not edit the
source artifact".

So nothing here writes to a staged row. A decision is a separate, append-only
record, and the staged rows keep saying what the workbook said for as long as
the batch exists. Promotion reads the overlay, and the resulting exposure
version records which of its attributes came from the source and which from a
person -- because a reviewed storey count and a reported one are worth
different amounts, and a version that could not tell them apart would let the
difference disappear into a loss number.

Three fields are reviewable and the list is deliberately short.

**Cohort.** The eligibility decision. A geocode the rules put in the review
backlog may be confirmed into the modellable cohort, or a coordinate the rules
accepted may be pushed out of it.

**Review state.** Whether the person has looked, and what they concluded.

**Storeys.** The one attribute worth collecting at the same time, and the
reason is arithmetic rather than tidiness. Height decides which spectral period
a structure responds at. A risk with no storey count reaches GEM candidates
across four intensity measures and cannot become one Oasis function; the same
risk at a stated height reaches one. A reviewer already opening each risk to
check where it is can answer that in the same pass, and on this book that is
the difference between a class that can be modelled and one that is refused
until a scientific decision nobody has made yet.

Coordinates are **not** reviewable here. Moving a risk on the map is a
different act with different evidence behind it, and folding it in beside a
storey count would make the two look equally routine.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from django.db import transaction

import cass_extract as extract

from .models import (
    DecisionField,
    ImportBatch,
    ReviewDecision,
    ReviewState,
    SourceRiskLocation,
)

#: Bumped when the overlay changes how a decision is applied. Recorded on any
#: exposure version promoted through it, so a version always names the rules
#: that produced it.
OVERLAY_VERSION = "1.0.0"

#: The shortest rationale that can carry a reason. Not a formality: "ok" and
#: "checked" are what a required field collects when nothing enforces that it
#: say something, and neither answers the question an auditor will ask.
MINIMUM_RATIONALE = 12

#: Storeys a schedule can plausibly state. The OED field allows 0 to 200; a
#: reviewer typing a height is more likely to slip than a system writing one,
#: so the lower bound is 1 -- a building with no storeys is not a building.
MIN_STOREYS = 1
MAX_STOREYS = 200


class ReviewError(Exception):
    """Raised when a decision cannot be recorded."""


@dataclasses.dataclass(frozen=True, slots=True)
class Overlay:
    """The decided state of one location: source values with decisions applied."""

    location: SourceRiskLocation
    cohort: str
    review_state: str
    storeys: int | None
    #: Which fields a person changed, and what each was before.
    changed: Mapping[str, tuple[str, str]] = dataclasses.field(default_factory=dict)

    @property
    def storeys_are_reviewed(self) -> bool:
        return "storeys" in self.changed

    @property
    def cohort_is_reviewed(self) -> bool:
        return "cohort" in self.changed

    def as_dict(self) -> dict[str, Any]:
        return {
            "business_id": self.location.business_id,
            "location_number": self.location.location_number,
            "cohort": self.cohort,
            "review_state": self.review_state,
            "storeys": self.storeys,
            "storeys_are_reviewed": self.storeys_are_reviewed,
            "cohort_is_reviewed": self.cohort_is_reviewed,
            "changed": {
                field: {"from": before, "to": after}
                for field, (before, after) in sorted(self.changed.items())
            },
        }


def _latest(decisions: Iterable[ReviewDecision]) -> dict[str, ReviewDecision]:
    """The standing decision per field: the last one recorded wins.

    Earlier ones are kept rather than replaced. A reviewer who changed their
    mind twice is part of the record, and an audit that could only see the
    final answer could not tell a considered decision from a hasty one.
    """
    standing: dict[str, ReviewDecision] = {}
    for item in sorted(decisions, key=lambda entry: entry.created_at):
        standing[item.field] = item
    return standing


def overlay(location: SourceRiskLocation) -> Overlay:
    """One location as it stands after every decision made about it."""
    standing = _latest(location.decisions.all())
    changed: dict[str, tuple[str, str]] = {}

    cohort = location.cohort
    review_state = location.review_state
    storeys = location.storeys

    if DecisionField.COHORT in standing:
        decision = standing[DecisionField.COHORT]
        changed["cohort"] = (decision.previous_value, decision.new_value)
        cohort = decision.new_value
    if DecisionField.REVIEW_STATE in standing:
        decision = standing[DecisionField.REVIEW_STATE]
        changed["review_state"] = (decision.previous_value, decision.new_value)
        review_state = decision.new_value
    if DecisionField.STOREYS in standing:
        decision = standing[DecisionField.STOREYS]
        changed["storeys"] = (decision.previous_value, decision.new_value)
        storeys = int(decision.new_value) if decision.new_value else None

    return Overlay(
        location=location,
        cohort=cohort,
        review_state=review_state,
        storeys=storeys,
        changed=changed,
    )


def overlays(locations: Iterable[SourceRiskLocation]) -> dict[int, Overlay]:
    """Overlays for many locations, keyed by primary key."""
    return {item.id: overlay(item) for item in locations}


def _validated(field: str, value: Any) -> str:
    """The stored form of a decided value, refusing one that means nothing."""
    if field == DecisionField.COHORT:
        text = str(value or "").strip().upper()
        allowed = {str(item) for item in extract.Cohort}
        if text not in allowed:
            raise ReviewError(
                f"{text!r} is not a cohort. The rules assign "
                + ", ".join(sorted(allowed))
                + "."
            )
        return text

    if field == DecisionField.REVIEW_STATE:
        text = str(value or "").strip().lower()
        allowed = {str(item) for item in ReviewState}
        if text not in allowed:
            raise ReviewError(
                f"{text!r} is not a review outcome. Available: "
                + ", ".join(sorted(allowed))
                + "."
            )
        return text

    if field == DecisionField.STOREYS:
        if value in (None, ""):
            # Clearing a storey count is allowed and is not the same as never
            # having one: it says a reviewer looked and could not establish it,
            # which is a finding rather than an absence.
            return ""
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ReviewError(
                f"{value!r} is not a storey count. It has to be a whole number, "
                "because the height band a risk falls in decides which "
                "vulnerability function answers it."
            ) from None
        if not MIN_STOREYS <= number <= MAX_STOREYS:
            raise ReviewError(
                f"{number} storeys is outside {MIN_STOREYS} to {MAX_STOREYS}. A "
                "building with no storeys is not a building, and OED will not "
                "carry a number above 200."
            )
        return str(number)

    raise ReviewError(f"{field!r} is not a reviewable field.")


def _current(location: SourceRiskLocation, field: str) -> str:
    standing = overlay(location)
    if field == DecisionField.COHORT:
        return standing.cohort
    if field == DecisionField.REVIEW_STATE:
        return standing.review_state
    return "" if standing.storeys is None else str(standing.storeys)


@transaction.atomic
def decide(
    location: SourceRiskLocation,
    *,
    field: str,
    value: Any,
    rationale: str,
    actor=None,
) -> ReviewDecision:
    """Record one decision about one location, with the reason for it.

    The staged row is not touched. What comes back is the record of the
    decision, and ``overlay`` is what reads it.
    """
    if field not in {str(item) for item in DecisionField}:
        raise ReviewError(
            f"{field!r} is not reviewable. A person may decide "
            + ", ".join(sorted(str(item) for item in DecisionField))
            + ". Moving a risk on the map is a different act with different "
            "evidence behind it and is not done here."
        )

    reason = (rationale or "").strip()
    if len(reason) < MINIMUM_RATIONALE:
        raise ReviewError(
            "A decision needs a rationale of at least "
            f"{MINIMUM_RATIONALE} characters saying what was established and how. "
            "It is the only thing that separates a corrected attribute from a "
            "number somebody preferred, and it is what an auditor reads when the "
            "loss is questioned."
        )

    stored = _validated(field, value)
    before = _current(location, field)
    if stored == before:
        raise ReviewError(
            f"The {field} is already {stored!r}, so this decision changes nothing. "
            "Recording it would put a rationale in the audit trail for a change "
            "that never happened."
        )

    return ReviewDecision.objects.create(
        location=location,
        field=field,
        previous_value=before,
        new_value=stored,
        rationale=reason,
        decided_by=actor,
        created_by=actor,
        updated_by=actor,
    )


def history(location: SourceRiskLocation) -> list[dict[str, Any]]:
    """Every decision made about one location, oldest first."""
    return [
        {
            "field": item.field,
            "from": item.previous_value,
            "to": item.new_value,
            "rationale": item.rationale,
            "decided_by": getattr(item.decided_by, "email", ""),
            "decided_at": item.created_at.isoformat(),
        }
        for item in location.decisions.all().order_by("created_at")
    ]


# -- what the review is worth ------------------------------------------------------------

def storey_coverage(
    locations: Sequence[SourceRiskLocation],
    resolved: Mapping[int, Overlay] | None = None,
) -> dict[str, Any]:
    """How much of the book states a height, and how much value rides on it.

    The number this exists to make visible: value on risks with no storey count
    is value that reaches vulnerability classes spanning several intensity
    measures, and those cannot be answered until the multi-IMT representation
    is approved. Counting the risks understates it, because the large ones are
    the ones with the schedules -- so value is reported alongside.
    """
    applied = resolved if resolved is not None else overlays(locations)

    stated = reviewed = unstated = 0
    stated_value = reviewed_value = unstated_value = 0.0
    for item in locations:
        standing = applied[item.id]
        value = float(item.total_insured_value or 0)
        if standing.storeys is None:
            unstated += 1
            unstated_value += value
        elif standing.storeys_are_reviewed:
            reviewed += 1
            reviewed_value += value
        else:
            stated += 1
            stated_value += value

    total = stated + reviewed + unstated
    total_value = stated_value + reviewed_value + unstated_value
    return {
        "locations": total,
        "stated_in_source": stated,
        "established_in_review": reviewed,
        "unstated": unstated,
        "stated_share": (stated + reviewed) / total if total else 0.0,
        "value_stated_in_source": stated_value,
        "value_established_in_review": reviewed_value,
        "value_unstated": unstated_value,
        "value_stated_share": (
            (stated_value + reviewed_value) / total_value if total_value else 0.0
        ),
        "note": (
            "A risk with no storey count reaches vulnerability candidates across "
            "several intensity measures and cannot become one Oasis function. "
            "Until the multi-IMT representation is approved, that value is "
            "refused rather than approximated -- so this share is the cheapest "
            "lever on how much of the book can be modelled at all."
        ),
    }


def review_summary(batch: ImportBatch) -> dict[str, Any]:
    """The state of the review backlog, and what deciding it has changed."""
    locations = list(batch.location_rows.all().prefetch_related("decisions"))
    applied = overlays(locations)

    by_cohort: dict[str, int] = {}
    by_state: dict[str, int] = {}
    moved: list[dict[str, Any]] = []
    for item in locations:
        standing = applied[item.id]
        by_cohort[standing.cohort] = by_cohort.get(standing.cohort, 0) + 1
        by_state[standing.review_state] = by_state.get(standing.review_state, 0) + 1
        if standing.changed:
            moved.append(standing.as_dict())

    return {
        "overlay_version": OVERLAY_VERSION,
        "cohort_rule_version": batch.cohort_rule_version,
        "locations": len(locations),
        "by_cohort": dict(sorted(by_cohort.items())),
        "by_review_state": dict(sorted(by_state.items())),
        "outstanding": by_state.get(str(ReviewState.PENDING), 0),
        "decided": len(moved),
        "decisions": moved[:50],
        "storeys": storey_coverage(locations, applied),
    }


# -- what the import-results screen shows ------------------------------------------------

#: The four modes a result may be used in, hardest last. Shown on the screen
#: because the difference between them is the difference between an interesting
#: number and a number somebody prices on, and it is not visible in the number.
USE_MODES: tuple[tuple[str, str], ...] = (
    (
        "geometry",
        "Coordinates mapped to the grid and to vulnerability eligibility. No "
        "loss is calculated at all.",
    ),
    (
        "technical_test",
        "A loss is calculated to prove the engine path works. The numbers "
        "describe the pipeline, not the portfolio.",
    ),
    (
        "research",
        "A loss on real KRE-share values, for understanding the book. Carries "
        "every assumption on this page and is not a priced view.",
    ),
    (
        "decision_use",
        "A result that may inform pricing or reserving. Requires actuarial, "
        "underwriting and catastrophe-model review first.",
    ),
)


def queue(batch: ImportBatch) -> dict[str, Any]:
    """The locations a person still owes a decision on, and why each is here.

    Ordered by value. A reviewer with an afternoon should spend it on the risks
    that carry the money, and a queue in row order spends it on whichever
    business happens to sort first.
    """
    locations = list(
        batch.location_rows.filter(review_state=ReviewState.PENDING).prefetch_related(
            "decisions"
        )
    )
    applied = overlays(locations)
    outstanding = [
        item
        for item in locations
        if applied[item.id].review_state == ReviewState.PENDING
    ]
    outstanding.sort(key=lambda item: -(item.total_insured_value or 0))

    return {
        "batch": str(batch.id),
        "outstanding": len(outstanding),
        "outstanding_value": float(
            sum(item.total_insured_value or 0 for item in outstanding)
        ),
        "locations": [
            {
                "id": str(item.id),
                "business_id": item.business_id,
                "location_number": item.location_number,
                "primary_location": item.primary_location,
                "class_of_business": item.class_of_business,
                "country_code": item.country_code,
                "coordinate": item.coordinate,
                "precision": item.precision,
                "needs_review": item.needs_review,
                "total_insured_value": (
                    float(item.total_insured_value)
                    if item.total_insured_value is not None
                    else None
                ),
                "cohort": applied[item.id].cohort,
                "cohort_reason": item.cohort_reason,
                "storeys": applied[item.id].storeys,
                "storeys_are_reviewed": applied[item.id].storeys_are_reviewed,
                "history": history(item),
            }
            for item in outstanding
        ],
    }


def _repeated_coordinates(
    locations: Sequence[SourceRiskLocation],
) -> list[dict[str, Any]]:
    """Locations sharing a coordinate.

    Not necessarily wrong -- two units of one business at one address are
    ordinary -- but a geocoder that fell back to a city centroid produces the
    same thing, and the two are indistinguishable without looking.
    """
    seen: dict[str, list[SourceRiskLocation]] = {}
    for item in locations:
        if item.coordinate:
            seen.setdefault(item.coordinate, []).append(item)
    return [
        {
            "coordinate": coordinate,
            "count": len(rows),
            "businesses": sorted({row.business_id for row in rows}),
            "value": float(sum(row.total_insured_value or 0 for row in rows)),
        }
        for coordinate, rows in sorted(seen.items())
        if len(rows) > 1
    ]


def _multi_location(locations: Sequence[SourceRiskLocation]) -> list[dict[str, Any]]:
    """Businesses with more than one site, which is where allocation bites."""
    by_business: dict[str, list[SourceRiskLocation]] = {}
    for item in locations:
        by_business.setdefault(item.business_id, []).append(item)
    return [
        {
            "business_id": business,
            "locations": len(rows),
            "value": float(sum(row.total_insured_value or 0 for row in rows)),
            "states_own_values": all(
                row.total_insured_value is not None for row in rows
            ),
        }
        for business, rows in sorted(by_business.items())
        if len(rows) > 1
    ]


def _by_country_and_class(
    locations: Sequence[SourceRiskLocation], applied: Mapping[int, Overlay]
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in locations:
        key = (
            item.country_code or "??",
            item.class_of_business or "unstated",
            applied[item.id].cohort,
        )
        entry = grouped.setdefault(
            key,
            {
                "country_code": key[0],
                "class_of_business": key[1],
                "cohort": key[2],
                "locations": 0,
                "value": 0.0,
            },
        )
        entry["locations"] += 1
        entry["value"] += float(item.total_insured_value or 0)
    return [grouped[key] for key in sorted(grouped)]


#: What a risk missing each field costs, in the words the failure will appear in.
MISSING_CONSEQUENCE: Mapping[str, str] = {
    "coordinates": "fail_ap: the risk reaches no grid cell and carries no loss.",
    "country": "The risk cannot be routed to a national grid or model version.",
    "value": (
        "The risk is allocated a share of its policy total rather than carrying "
        "its own."
    ),
    "occupancy": (
        "fail_v: OED unknown occupancy reaches no vulnerability function, by "
        "design."
    ),
    "construction": (
        "The risk reaches a wider mixture of building types than one that states "
        "its material."
    ),
    "storeys": (
        "The risk reaches vulnerability candidates across several intensity "
        "measures and cannot become one Oasis function."
    ),
}


def _missing_model_inputs(
    locations: Sequence[SourceRiskLocation], applied: Mapping[int, Overlay]
) -> list[dict[str, Any]]:
    """What each risk would need before a model could answer it properly.

    Counted by value as well as by risk, because these are not evenly spread:
    the risks that state the least are not the small ones.
    """
    checks = {
        "coordinates": lambda row: not row.coordinate,
        "country": lambda row: not row.country_code,
        "value": lambda row: row.total_insured_value is None,
        "occupancy": lambda row: not (row.values or {}).get("occupancy_code"),
        "construction": lambda row: not (row.values or {}).get("construction_code"),
        "storeys": lambda row: applied[row.id].storeys is None,
    }
    found = []
    for field, test in checks.items():
        rows = [item for item in locations if test(item)]
        if not rows:
            continue
        found.append(
            {
                "field": field,
                "locations": len(rows),
                "value": float(sum(row.total_insured_value or 0 for row in rows)),
                "consequence": MISSING_CONSEQUENCE[field],
            }
        )
    return found


def import_results(batch: ImportBatch) -> dict[str, Any]:
    """The work package 2 payload: the whole import as one picture."""
    locations = list(batch.location_rows.all().prefetch_related("decisions"))
    applied = overlays(locations)
    total_value = float(sum(item.total_insured_value or 0 for item in locations))

    return {
        "batch": {
            "id": str(batch.id),
            "filename": batch.source_filename,
            "source_checksum": batch.source_checksum,
            "parser_version": batch.parser_version,
            "cohort_rule_version": batch.cohort_rule_version,
            "overlay_version": OVERLAY_VERSION,
            "policy_row_count": batch.policy_row_count,
            "risk_row_count": batch.risk_row_count,
            "state": batch.state,
        },
        "included": _by_country_and_class(locations, applied),
        "total_value": total_value,
        "review": review_summary(batch),
        "missing_model_inputs": _missing_model_inputs(locations, applied),
        "multi_location_businesses": _multi_location(locations),
        "repeated_coordinates": _repeated_coordinates(locations),
        "findings": batch.findings,
        "intake_report": batch.intake_report,
        "cohort_profile": batch.cohort_profile,
        "use_modes": [
            {"mode": mode, "meaning": meaning} for mode, meaning in USE_MODES
        ],
        "allocation_note": (
            "A business with one location carries its own value and no allocation "
            "assumption applies to it. Where a business has several and the "
            "schedule does not value them separately, the baseline divides the "
            "policy total equally; the primary-concentrated and "
            "concentration-envelope scenarios are the required sensitivities, and "
            "every one of them reconciles to the policy total exactly."
        ),
    }
