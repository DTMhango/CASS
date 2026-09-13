"""Running the two scientific gates of section 7 against what CASS stored.

The comparisons themselves live in the converter, where the science is. This
is the control-plane half: it finds the approved reference, reads the tables
back out of the artifact store, runs the comparison and keeps the report.

One rule shapes it. Neither gate passes itself. Where no benchmark and no
tolerance set have been approved, both produce their numbers and record that
acceptance is undecided -- which is the state the platform is actually in, and
the state a reviewer needs to see rather than a green tick nobody earned.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from django.utils import timezone

from apps.artifacts.models import ArtifactLink
from apps.common.storage import get_store
from cass_converter import benchmark as benchmark_science
from cass_converter import pilot_bins
from cass_converter.benchmark import BenchmarkError, BenchmarkPoint

from .models import ConversionTolerances, HazardBenchmark, PublicationState


class QualityGateError(Exception):
    """Raised when a gate cannot be run against what is stored."""


class _Row:
    """One footprint row, as the stored table states it."""

    __slots__ = ("event_id", "area_peril_id", "imt", "intensity_bin_id", "probability")

    def __init__(self, event_id, area_peril_id, imt, intensity_bin_id, probability):
        self.event_id = event_id
        self.area_peril_id = area_peril_id
        self.imt = imt
        self.intensity_bin_id = intensity_bin_id
        self.probability = probability


class _Occurrence:
    __slots__ = ("event_id", "period_no")

    def __init__(self, event_id, period_no):
        self.event_id = event_id
        self.period_no = period_no


def approved_tolerances() -> ConversionTolerances | None:
    """The tolerance set conversions are measured against, if one is approved."""
    return (
        ConversionTolerances.objects.filter(
            publication_state__in=(PublicationState.APPROVED, PublicationState.PUBLISHED)
        )
        .order_by("-approved_at", "-created_at")
        .first()
    )


def approved_benchmark(country_code: str) -> HazardBenchmark | None:
    """The benchmark a country's hazard is compared against, if one is approved."""
    return (
        HazardBenchmark.objects.filter(
            country_code=country_code.upper(),
            publication_state__in=(PublicationState.APPROVED, PublicationState.PUBLISHED),
        )
        .order_by("-approved_at", "-created_at")
        .first()
    )


def _artifact(hazard_set, role: str):
    link = (
        ArtifactLink.objects.filter(
            subject_type="hazard_set", subject_id=hazard_set.id, role=role
        )
        .select_related("artifact")
        .first()
    )
    if link is None or not link.artifact.is_readable:
        raise QualityGateError(
            f"{hazard_set.reference} has no readable {role} table stored, so the "
            "comparison has nothing to read."
        )
    with get_store().open(link.artifact.uri) as handle:
        return handle.read()


def _footprint_rows(payload: bytes, imt: str) -> list[_Row]:
    rows: list[_Row] = []
    for row in csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))):
        rows.append(
            _Row(
                event_id=int(row["event_id"]),
                area_peril_id=int(row["areaperil_id"]),
                imt=imt,
                intensity_bin_id=int(row["intensity_bin_id"]),
                probability=float(row["probability"]),
            )
        )
    return rows


def _occurrence_rows(payload: bytes) -> list[_Occurrence]:
    rows: list[_Occurrence] = []
    for row in csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))):
        rows.append(
            _Occurrence(event_id=int(row["event_id"]), period_no=int(row["period_no"]))
        )
    return rows


def _measure_stem(imt: str) -> str:
    """The file stem a hazard set stores one measure's footprint under."""
    return imt.replace("(", "").replace(")", "").replace(".", "p")


def run_benchmark(hazard_set, *, benchmark: HazardBenchmark | None = None) -> dict[str, Any]:
    """Compare one registered hazard set against an approved published curve.

    Reads the footprint back rather than trusting a number recorded at build
    time: the gate exists to check what the engine will actually be given.
    """
    reference = benchmark or approved_benchmark(hazard_set.country_code)
    if reference is None:
        return {
            "compared": False,
            "decided": False,
            "reason": (
                f"No approved hazard benchmark is registered for "
                f"{hazard_set.country_code}, so there is nothing to compare this "
                "hazard against. The gate stands open."
            ),
        }

    points = [
        BenchmarkPoint(
            area_peril_id=int(item["areaperil_id"]),
            imt=str(item["imt"]),
            return_period=float(item["return_period"]),
            intensity=float(item["intensity"]),
        )
        for item in (reference.points or [])
    ]
    if not points:
        raise QualityGateError(
            f"{reference} states no points, so it cannot be compared against."
        )

    # Only the measures both the benchmark and this hazard set carry. A point
    # naming a measure the set does not hold is reported as not comparable by
    # the comparison itself rather than quietly dropped here.
    wanted_imts = [
        imt
        for imt in sorted({point.imt for point in points})
        if imt in (hazard_set.imts or [])
    ]
    bins = pilot_bins.intensity_bins(wanted_imts) if wanted_imts else {}
    footprint: list[_Row] = []
    for imt in wanted_imts:
        footprint.extend(
            _footprint_rows(
                _artifact(hazard_set, f"hazard_footprint_{_measure_stem(imt)}"), imt
            )
        )

    occurrences = _occurrence_rows(_artifact(hazard_set, "hazard_occurrence"))

    try:
        report = benchmark_science.compare(
            footprint,
            occurrences=occurrences,
            effective_time=hazard_set.effective_time,
            intensity_bins=bins,
            points=points,
            tolerance=float(reference.tolerance) if reference.tolerance is not None else None,
        )
    except BenchmarkError as exc:
        raise QualityGateError(str(exc)) from exc

    return {
        "compared": True,
        "benchmark": str(reference),
        "benchmark_id": str(reference.id),
        "source": reference.source,
        "hazard_set": hazard_set.reference,
        "compared_at": timezone.now().isoformat(),
        **report,
    }


def record_benchmark(hazard_set, *, actor=None, benchmark: HazardBenchmark | None = None):
    """Run the benchmark and keep the report with the hazard set."""
    report = run_benchmark(hazard_set, benchmark=benchmark)
    stored = dict(hazard_set.conversion_report or {})
    stored["benchmark"] = report
    hazard_set.conversion_report = stored
    hazard_set.updated_by = actor
    hazard_set.save(update_fields=["conversion_report", "updated_by", "updated_at"])
    return report


def tolerance_values(record: ConversionTolerances | None) -> dict[str, float] | None:
    """An approved tolerance set as the converter's QA module takes it."""
    if record is None or not record.values:
        return None
    return {
        str(key): float(value)
        for key, value in record.values.items()
        if isinstance(value, int | float | str | Decimal)
    }


def decide(measurements: Mapping[str, Any] | None, tolerances: Mapping[str, float] | None):
    """Re-decide recorded measurements against the tolerances approved now.

    The numbers were taken when the hazard was converted; the tolerances may
    have been approved since, or not at all. Re-deciding rather than re-reading
    keeps the gate cheap and keeps the measurement honest: nothing here changes
    a measured value, only what it is judged against.
    """
    recorded = dict(measurements or {})
    checks = [dict(item) for item in recorded.get("checks") or []]
    stated = dict(tolerances or {})

    if not checks:
        return {
            **recorded,
            "decided": False,
            "passed": None,
            "reason": (
                "This hazard set was converted before the acceptance measurements "
                "existed, so there is nothing to judge. Convert it again to measure it."
            ),
        }

    failed: list[str] = []
    for check in checks:
        tolerance = stated.get(check["check"])
        check["tolerance"] = tolerance
        check["within_tolerance"] = (
            None if tolerance is None else float(check["value"]) <= float(tolerance)
        )
        if check["within_tolerance"] is False:
            failed.append(check["check"])

    decided = bool(stated) and all(item["within_tolerance"] is not None for item in checks)
    problems = list(recorded.get("problems") or [])
    return {
        **recorded,
        "checks": checks,
        "failed": failed,
        "decided": decided,
        "passed": (not failed and not problems) if decided else None,
        "reason": (
            ""
            if decided
            else (
                "No conversion tolerances have been approved, so the acceptance "
                "numbers are recorded and the gate stands open."
            )
        ),
    }
