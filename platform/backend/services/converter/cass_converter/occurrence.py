"""Occurrence: mapping events to simulation periods, preserving frequency.

Section 7 makes this a scientific acceptance test in its own right: "Check
annual event frequency and period weighting before and after conversion." and
"Prove that total occurrence rates and effective time are preserved after
conversion."

Section 15 states the consequence of getting it wrong: invalid annual
frequency. An AAL computed from an occurrence table whose effective time does
not match the hazard is wrong by exactly the ratio of the two, and nothing
downstream will notice.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Sequence
from typing import Any

#: Frequency is compared at this relative tolerance. Conversion is a
#: reorganisation of the same events, so agreement should be near-exact; the
#: tolerance covers floating-point accumulation only.
FREQUENCY_TOLERANCE = 1e-9


class OccurrenceError(Exception):
    """Raised when an occurrence set would misstate frequency."""


@dataclasses.dataclass(frozen=True, slots=True)
class OccurrenceRow:
    """One occurrence of one event in one simulation period."""

    event_id: int
    period_no: int
    occ_year: int | None = None
    occ_month: int | None = None
    occ_day: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "period_no": self.period_no,
            "occ_year": self.occ_year,
            "occ_month": self.occ_month,
            "occ_day": self.occ_day,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class FrequencyCheck:
    """Annual frequency before and after conversion."""

    source_event_count: int
    source_investigation_time: float
    converted_occurrence_count: int
    period_count: int

    @property
    def source_annual_rate(self) -> float:
        """Events per year in the OpenQuake output."""
        if self.source_investigation_time <= 0:
            raise OccurrenceError("investigation time must be positive")
        return self.source_event_count / self.source_investigation_time

    @property
    def converted_annual_rate(self) -> float:
        """Events per year implied by the occurrence table."""
        if self.period_count <= 0:
            raise OccurrenceError("period count must be positive")
        return self.converted_occurrence_count / self.period_count

    @property
    def relative_difference(self) -> float:
        source = self.source_annual_rate
        if source == 0:
            return 0.0 if self.converted_annual_rate == 0 else float("inf")
        return abs(self.converted_annual_rate - source) / source

    @property
    def preserved(self) -> bool:
        return self.relative_difference <= FREQUENCY_TOLERANCE

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_event_count": self.source_event_count,
            "source_investigation_time": self.source_investigation_time,
            "source_annual_rate": self.source_annual_rate,
            "converted_occurrence_count": self.converted_occurrence_count,
            "period_count": self.period_count,
            "converted_annual_rate": self.converted_annual_rate,
            "relative_difference": self.relative_difference,
            "preserved": self.preserved,
        }

    def require_preserved(self) -> None:
        if not self.preserved:
            raise OccurrenceError(
                "annual frequency was not preserved by conversion: source "
                f"{self.source_annual_rate:.6f}/year against converted "
                f"{self.converted_annual_rate:.6f}/year "
                f"({self.relative_difference:.2%} apart)"
            )


def check_frequency(
    *,
    source_event_count: int,
    source_investigation_time: float,
    occurrences: Sequence[OccurrenceRow],
    period_count: int,
) -> FrequencyCheck:
    """Compare annual frequency before and after conversion."""
    return FrequencyCheck(
        source_event_count=source_event_count,
        source_investigation_time=source_investigation_time,
        converted_occurrence_count=len(occurrences),
        period_count=period_count,
    )


def validate_occurrences(
    occurrences: Iterable[OccurrenceRow],
    *,
    period_count: int,
    known_event_ids: Iterable[int] | None = None,
) -> list[str]:
    """Check an occurrence table for the errors that misstate frequency."""
    problems: list[str] = []
    rows = list(occurrences)

    if not rows:
        problems.append("the occurrence table is empty, so no event can contribute loss")
        return problems

    out_of_range = [row for row in rows if not 1 <= row.period_no <= period_count]
    if out_of_range:
        problems.append(
            f"{len(out_of_range)} occurrence(s) name a period outside 1 to "
            f"{period_count}, starting with period {out_of_range[0].period_no}"
        )

    seen: set[tuple[int, int]] = set()
    duplicates = 0
    for row in rows:
        key = (row.event_id, row.period_no)
        if key in seen:
            duplicates += 1
        seen.add(key)
    if duplicates:
        problems.append(
            f"{duplicates} event/period pair(s) appear more than once, which "
            "double-counts their contribution to the exceedance probability curve"
        )

    if known_event_ids is not None:
        known = set(known_event_ids)
        orphans = sorted({row.event_id for row in rows} - known)
        if orphans:
            problems.append(
                f"{len(orphans)} occurrence event(s) are not in the event set, "
                f"starting with {orphans[:5]}"
            )
        silent = sorted(known - {row.event_id for row in rows})
        if silent:
            problems.append(
                f"{len(silent)} event(s) never occur in any period, starting with "
                f"{silent[:5]}"
            )

    return problems


def empty_period_share(
    occurrences: Iterable[OccurrenceRow], period_count: int
) -> float:
    """The share of simulation periods in which nothing happens.

    Reported rather than treated as an error: for a low-seismicity region most
    years genuinely are quiet. It becomes evidence in the conversion report,
    where an unexpected value is the signal that the period count and the
    investigation time disagree.
    """
    if period_count <= 0:
        raise OccurrenceError("period count must be positive")
    occupied = {row.period_no for row in occurrences}
    return (period_count - len(occupied)) / period_count
