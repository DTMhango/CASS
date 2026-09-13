"""What differs between two governed results, and what explains it.

Milestone M6 in section 13 is completing the analyst journey and comparing two
governed runs, and section 9 requires a model-change impact report to compare
benchmark AAL, EP curves and segments against the previous release with the
drivers identified.

The arithmetic is here rather than in the browser, and that is not a matter of
taste. ADR 5 puts money across the API as decimal strings precisely so that no
monetary value is ever a JavaScript float, and it says what follows: arithmetic
on money belongs on the server, where it is ``Decimal`` and where the result is
audited. A difference computed in a browser and posted back would be a number
nobody can reproduce, stored as though it were a fact.

The drivers matter as much as the deltas. A number that moved is a question; a
number that moved because the vulnerability set was replaced is an answer. So
every field a result carries that could explain a change is compared, and where
none of them differ the comparison says that plainly rather than leaving a
reader to assume the model did it.
"""

from __future__ import annotations

from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any

from .models import ResultSet

#: Relative change is a ratio rather than money, but it is derived from money,
#: so it is quantised and serialised as a string on the same principle.
RATIO_PLACES = Decimal("0.000001")


def _decimal(value: Any) -> Decimal | None:
    """Read a stored monetary value, whatever JSON made of it."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _relative(baseline: Decimal | None, candidate: Decimal | None) -> str | None:
    """The proportional change, or nothing where there is no base to take it of.

    A move from zero is not an infinite increase, it is a change of kind, and
    reporting it as a percentage would put a meaningless number beside a real
    one. The absolute change is still reported, which is the honest half.
    """
    if baseline is None or candidate is None or not baseline:
        return None
    try:
        return str(((candidate - baseline) / baseline).quantize(RATIO_PLACES))
    except (InvalidOperation, DivisionByZero):
        return None


def _direction(change: Decimal | None) -> str:
    if change is None:
        return "unknown"
    if change > 0:
        return "increase"
    if change < 0:
        return "decrease"
    return "unchanged"


def _metric(
    key: str, label: str, baseline: Decimal | None, candidate: Decimal | None
) -> dict[str, Any]:
    change = (
        candidate - baseline if baseline is not None and candidate is not None else None
    )
    return {
        "metric": key,
        "label": label,
        "baseline": str(baseline) if baseline is not None else None,
        "candidate": str(candidate) if candidate is not None else None,
        "change": str(change) if change is not None else None,
        "relative_change": _relative(baseline, candidate),
        "direction": _direction(change),
    }


def _return_periods(baseline: ResultSet, candidate: ResultSet) -> dict[str, Any]:
    """The EP curve, compared only where both results report the same period.

    A return period one side did not report is listed rather than treated as
    zero. Zero would draw a cliff on a chart where the truth is that nobody
    asked the question at that period.
    """
    left = baseline.return_period_losses or {}
    right = candidate.return_period_losses or {}

    def sort_key(period: str) -> tuple[int, float | str]:
        try:
            return (0, float(period))
        except ValueError:
            return (1, period)

    shared = sorted(set(left) & set(right), key=sort_key)
    rows = []
    for period in shared:
        base = _decimal(left.get(period))
        cand = _decimal(right.get(period))
        change = cand - base if base is not None and cand is not None else None
        rows.append(
            {
                "return_period": period,
                "baseline": str(base) if base is not None else None,
                "candidate": str(cand) if cand is not None else None,
                "change": str(change) if change is not None else None,
                "relative_change": _relative(base, cand),
                "direction": _direction(change),
            }
        )

    return {
        "shared": rows,
        "only_in_baseline": sorted(set(left) - set(right), key=sort_key),
        "only_in_candidate": sorted(set(right) - set(left), key=sort_key),
    }


def _drivers(baseline: ResultSet, candidate: ResultSet) -> list[dict[str, Any]]:
    """Everything either result records that could account for the change."""
    drivers: list[dict[str, Any]] = []

    def differs(key: str, label: str, left: Any, right: Any, note: str = "") -> None:
        if left != right:
            drivers.append(
                {
                    "driver": key,
                    "label": label,
                    "baseline": left if isinstance(left, str) else str(left or ""),
                    "candidate": right if isinstance(right, str) else str(right or ""),
                    "note": note,
                }
            )

    differs(
        "model_version",
        "Model version",
        baseline.model_version_reference,
        candidate.model_version_reference,
        "A different model version changes hazard, vulnerability or both.",
    )
    differs(
        "assumption_set",
        "Assumption set",
        baseline.assumption_set_reference,
        candidate.assumption_set_reference,
        "A different assumption set fills the same gaps differently.",
    )
    differs(
        "run_mode",
        "Run mode",
        baseline.run_mode,
        candidate.run_mode,
        "The two runs were made for different purposes, so they do not answer "
        "the same question and their difference is not a like-for-like one.",
    )
    differs(
        "valuation_date",
        "Valuation date",
        baseline.valuation_date.isoformat() if baseline.valuation_date else "",
        candidate.valuation_date.isoformat() if candidate.valuation_date else "",
        "The portfolios are stated as at different dates.",
    )

    left_exclusions = set(baseline.material_exclusions or [])
    right_exclusions = set(candidate.material_exclusions or [])
    if left_exclusions != right_exclusions:
        drivers.append(
            {
                "driver": "material_exclusions",
                "label": "Material exclusions",
                "baseline": ", ".join(sorted(left_exclusions)) or "none recorded",
                "candidate": ", ".join(sorted(right_exclusions)) or "none recorded",
                "note": (
                    "The two numbers do not cover the same perils, so part of the "
                    "difference is scope rather than loss."
                ),
            }
        )

    changed_scope = sorted(
        key
        for key in set(baseline.peril_scope or {}) | set(candidate.peril_scope or {})
        if (baseline.peril_scope or {}).get(key) != (candidate.peril_scope or {}).get(key)
    )
    if changed_scope:
        drivers.append(
            {
                "driver": "peril_scope",
                "label": "Peril scope",
                "baseline": ", ".join(changed_scope),
                "candidate": ", ".join(changed_scope),
                "note": (
                    "The treatment of these sub-perils differs between the two "
                    "releases."
                ),
            }
        )

    return drivers


def differences(baseline: ResultSet, candidate: ResultSet) -> dict[str, Any]:
    """The saved body of a comparison.

    Computed once, at creation, and stored. A comparison recomputed on every
    read would quietly change when a result was corrected, and the point of
    saving one is to be able to say what was compared and when.
    """
    base_aal = baseline.average_annual_loss
    cand_aal = candidate.average_annual_loss

    drivers = _drivers(baseline, candidate)
    metrics = [
        _metric("average_annual_loss", "Average annual loss", base_aal, cand_aal),
        _metric(
            "standard_deviation",
            "Standard deviation",
            baseline.standard_deviation,
            candidate.standard_deviation,
        ),
    ]

    return {
        "perspective": baseline.get_perspective_display(),
        "currency": baseline.currency,
        "metrics": metrics,
        "return_periods": _return_periods(baseline, candidate),
        "drivers": drivers,
        # Where nothing recorded on either result differs, the change came from
        # somewhere neither of them names. Saying so is more use than an empty
        # list, which reads as "no cause" rather than "no cause recorded".
        "unexplained": not drivers,
        "unexplained_note": (
            "Both results name the same model version, assumption set, run mode "
            "and valuation date, so the difference lies in the exposure or the "
            "run settings rather than in anything recorded here."
            if not drivers
            else ""
        ),
        # Brief section 5.2: outputs from different modes are never mixed in one
        # comparison without an explicit warning. A geometry-only run against a
        # technical one, or a research number against a decision one, compares
        # two different claims rather than two answers to one question.
        "run_modes": {
            "baseline": baseline.run_mode,
            "candidate": candidate.run_mode,
            "mixed": baseline.run_mode != candidate.run_mode,
            "warning": (
                ""
                if baseline.run_mode == candidate.run_mode
                else (
                    "These results come from runs made for different purposes "
                    f"({baseline.run_mode or 'unrecorded'} against "
                    f"{candidate.run_mode or 'unrecorded'}), so the difference "
                    "between them is not a like-for-like one."
                )
            ),
        },
        "decision_use": {
            "baseline": baseline.usable_for_decisions,
            "candidate": candidate.usable_for_decisions,
            "both_approved": baseline.usable_for_decisions
            and candidate.usable_for_decisions,
            # Section 9 keeps research output operationally distinct, and a
            # comparison is exactly where the two would otherwise be blended:
            # an approved baseline against a research candidate reads as an
            # approved finding unless it says otherwise.
            "warning": (
                ""
                if baseline.usable_for_decisions and candidate.usable_for_decisions
                else (
                    "At least one side of this comparison is not approved for "
                    "decision use, so the difference is not a decision number "
                    "either."
                )
            ),
        },
    }
