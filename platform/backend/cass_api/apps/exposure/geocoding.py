"""Cohort B, tried against a grid: does the geocode support the cell it was given?

The brief sets Cohort B aside for exactly this. Its rows need no review and sit in
the right country, but their geocodes resolve only to a locality, a postcode or
an administrative area -- so a grid fine enough to separate Jakarta's districts
can give such a row a cell the geocode cannot vouch for. The rule is that Cohort B
is never merged into a benchmark "without reporting its sensitivity separately",
and this is that report.

It reads the staged rows as a person has left them. Coordinates are not
reviewable, so they are the source's; the cohort is, so a row a reviewer moved
into or out of Cohort B is counted where the reviewer put it.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from cass_keys import sensitivity

from . import review
from .models import ImportBatch

#: The cohort this report is for.
COHORT = "B"

#: What the value on each location is, so the report cannot be read as more.
VALUE_BASIS = (
    "The insured value each location states. A location whose value awaits an "
    "allocation carries none here and is counted separately, so the value at "
    "stake is a floor rather than the book's."
)


def cohort_b_sensitivity(
    batch: ImportBatch,
    *,
    grid,
    buffers_km: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assess every Cohort B location in the grid's country under its uncertainty buffer."""
    locations = list(batch.location_rows.prefetch_related("decisions"))
    applied = review.overlays(locations)
    country = grid.country_code.upper()

    rows: list[dict[str, Any]] = []
    other_countries: dict[str, int] = {}
    without_value = 0
    for location in locations:
        if applied[location.id].cohort != COHORT:
            continue
        code = (location.country_code or "").strip().upper()
        if code != country:
            other_countries[code or "unknown"] = other_countries.get(code or "unknown", 0) + 1
            continue
        if location.total_insured_value is None:
            without_value += 1
        rows.append(
            {
                "location": f"{location.business_id}/{location.location_number}",
                "latitude": location.latitude,
                "longitude": location.longitude,
                "precision": location.precision,
                "tiv": location.total_insured_value or Decimal("0"),
            }
        )

    document = sensitivity.report(grid, rows, buffers_km=buffers_km).as_dict()
    document["cohort"] = COHORT
    document["country"] = country
    document["other_countries"] = dict(sorted(other_countries.items()))
    document["value_basis"] = VALUE_BASIS
    document["summary"]["without_stated_value"] = without_value
    return document
