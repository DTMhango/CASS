"""The range a result spans across the assumption scenarios run for it.

The build plan asks results to "report scenario ranges and sensitivity
attribution alongside central estimates where uncertainty is material", and the
interface to "identify the assumptions that most influence AAL, EP curves and
key return-period losses".

CASS varies one assumption between runs of one book on one model: the
assumption set, which re-weighs the building mixtures a class is blended from
(ADR 14). So a scenario range is the same book, on the same model version,
perspective, currency, run mode and ORD basis, run under each assumption set.
The baseline is the central estimate, and each metric reports its low and its
high, which scenario set each, and the spread against the central number.
Nothing else differs between the results in a range, which is what lets the
spread be attributed to the assumption -- and why a result on another book or
another model is never brought into one.

What is not varied is stated rather than implied. The hazard realisation, the
allocation of value between a business's sites and where a coarse geocode puts
a location are other sources of uncertainty, measured elsewhere, and none of
them is in this range.

The arithmetic is here for the reason the comparison's is: ADR 5 keeps money
arithmetic on the server, in ``Decimal``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.core.exceptions import ObjectDoesNotExist

from .comparison import RATIO_PLACES, _decimal, _relative
from .models import ResultSet, ResultState

#: The scenario key for baseline weights, whether no set was named or a set
#: whose flavour is the baseline was.
BASELINE = "baseline"

#: Sources of uncertainty that do not vary between the results in a range.
NOT_VARIED = (
    "The hazard realisation and event set",
    "How a business's value is allocated between its sites",
    "Where a coarse geocode places a location",
)


def _analysis(result: ResultSet):
    try:
        return result.run.analysis
    except ObjectDoesNotExist:
        return None


def _scenario(result: ResultSet) -> tuple[str, str]:
    """The assumption scenario a result was run under, as a key and a label."""
    analysis = _analysis(result)
    assumption = analysis.assumption_set if analysis is not None else None
    if assumption is None or assumption.flavour == BASELINE:
        return BASELINE, "Baseline weights"
    return (
        str(assumption.flavour),
        f"{assumption.get_flavour_display()} ({assumption.reference})",
    )


def _mode(result: ResultSet) -> str:
    """The mode a result was run under, read from its run where the result predates the copy."""
    if result.run_mode:
        return result.run_mode
    analysis = _analysis(result)
    return str(analysis.mode) if analysis is not None else ""


def scenarios_for(
    result: ResultSet,
) -> tuple[list[tuple[str, str, ResultSet]], dict[str, int]]:
    """One result per assumption scenario for the same book on the same model.

    The requested result stands for its own scenario; every other scenario is
    represented by its latest result. Only results calculated the same way
    apart from the assumption set are brought in, and those left out for that
    reason are counted rather than dropped silently.
    """
    own_key, own_label = _scenario(result)
    found: dict[str, tuple[str, str, ResultSet]] = {own_key: (own_key, own_label, result)}
    left_out = {"calculated_differently": 0, "calculation_not_recorded": 0}

    analysis = _analysis(result)
    if analysis is None or not result.calculation_digest:
        return list(found.values()), left_out

    basis = (result.uncertainty_attribution or {}).get("ord_basis")
    mode = _mode(result)
    candidates = (
        ResultSet.objects.filter(
            project_id=result.project_id,
            perspective=result.perspective,
            currency=result.currency,
            model_version_reference=result.model_version_reference,
            run__analysis__exposure_version_id=analysis.exposure_version_id,
        )
        .exclude(pk=result.pk)
        .exclude(state=ResultState.WITHDRAWN)
        .select_related("run__analysis__assumption_set")
        .order_by("-created_at")
    )
    for item in candidates:
        # Brief section 5.2 keeps modes apart: a run made for another purpose is
        # not another scenario of this one.
        if _mode(item) != mode:
            continue
        # Two numbers on different ORD bases are different quantities, and a
        # range across them would be a range of definitions.
        if (item.uncertainty_attribution or {}).get("ord_basis") != basis:
            continue
        if not item.calculation_digest:
            left_out["calculation_not_recorded"] += 1
            continue
        if item.calculation_digest != result.calculation_digest:
            left_out["calculated_differently"] += 1
            continue
        key, label = _scenario(item)
        found.setdefault(key, (key, label, item))

    return (
        sorted(found.values(), key=lambda entry: (entry[0] != BASELINE, entry[0])),
        left_out,
    )


def scenario_range(result: ResultSet) -> dict[str, Any]:
    """The central estimate, each metric's range, and what moves it most."""
    scenarios, left_out = scenarios_for(result)
    labels = {key: label for key, label, _ in scenarios}
    central = next((entry for entry in scenarios if entry[0] == BASELINE), None)
    central_is_baseline = central is not None
    if central is None:
        central = next(entry for entry in scenarios if entry[2].pk == result.pk)

    document: dict[str, Any] = {
        "varies": "assumption set",
        "not_varied": list(NOT_VARIED),
        "currency": result.currency,
        "central": {
            "scenario": central[0],
            "label": central[1],
            "result": str(central[2].id),
            "is_baseline": central_is_baseline,
        },
        "scenarios": [
            {
                "scenario": key,
                "label": label,
                "result": str(item.id),
                "created_at": item.created_at.isoformat(),
                "average_annual_loss": (
                    str(item.average_annual_loss)
                    if item.average_annual_loss is not None
                    else None
                ),
            }
            for key, label, item in scenarios
        ],
        "metrics": [],
        "influence": [],
        "left_out": left_out,
    }
    if not result.calculation_digest:
        document["note"] = (
            "This result was published before CASS recorded how a run's losses were "
            "calculated, so no other result can be shown to have been calculated the "
            "same way, and there is no range to report."
        )
        return document
    if len(scenarios) < 2:
        excluded = sum(left_out.values())
        document["note"] = (
            "Only one assumption scenario has been calculated this way for this book on "
            "this model, so there is no range to report. Run the book again under "
            "another assumption set, with the same settings, and the range appears here."
            + (
                f" {excluded} other result(s) for this book were calculated under "
                "different settings, or before that was recorded, and are left out."
                if excluded
                else ""
            )
        )
        return document
    if not central_is_baseline:
        document["note"] = (
            "No baseline run exists for this book on this model, so the range is "
            "centred on this result rather than on the baseline."
        )

    metrics: list[tuple[str, str, dict[str, Decimal | None]]] = [
        (
            "average_annual_loss",
            "Average annual loss",
            {key: _decimal(item.average_annual_loss) for key, _, item in scenarios},
        )
    ]
    shared = set.intersection(
        *(set((item.return_period_losses or {}).keys()) for _, _, item in scenarios)
    )
    for period in sorted(shared, key=float):
        metrics.append(
            (
                f"return_period_{period}",
                f"{period}-year return period",
                {
                    key: _decimal((item.return_period_losses or {}).get(period))
                    for key, _, item in scenarios
                },
            )
        )

    influence: dict[str, Decimal] = {}
    for metric, label, values in metrics:
        present = {key: value for key, value in values.items() if value is not None}
        if len(present) < 2:
            continue
        low = min(present, key=lambda key: present[key])
        high = max(present, key=lambda key: present[key])
        spread = present[high] - present[low]
        central_value = present.get(central[0])
        entry: dict[str, Any] = {
            "metric": metric,
            "label": label,
            "central": str(central_value) if central_value is not None else None,
            "low": {"scenario": low, "label": labels[low], "value": str(present[low])},
            "high": {"scenario": high, "label": labels[high], "value": str(present[high])},
            "spread": str(spread),
            "relative_spread": (
                str((spread / central_value).quantize(RATIO_PLACES)) if central_value else None
            ),
        }
        if central_value is not None:
            deviations = {
                key: value - central_value
                for key, value in present.items()
                if key != central[0]
            }
            if deviations:
                strongest = max(deviations, key=lambda key: abs(deviations[key]))
                entry["most_influential"] = {
                    "scenario": strongest,
                    "label": labels[strongest],
                    "change": str(deviations[strongest]),
                    "relative_change": _relative(central_value, present[strongest]),
                }
                for key in deviations:
                    relative = _relative(central_value, present[key])
                    if relative is not None:
                        influence[key] = max(influence.get(key, Decimal("0")), abs(Decimal(relative)))
        document["metrics"].append(entry)

    document["influence"] = [
        {"scenario": key, "label": labels[key], "largest_relative_change": str(value)}
        for key, value in sorted(influence.items(), key=lambda pair: pair[1], reverse=True)
    ]
    return document
