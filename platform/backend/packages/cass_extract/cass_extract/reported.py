"""Coverage values supplied per location, as OED actually works.

A percentage split is a convenience for the case where nobody knows the
breakdown. It is not how OED works, and it must not become the only door.
``BuildingTIV``, ``OtherTIV``, ``ContentsTIV`` and ``BITIV`` are four columns
and a user puts whatever numbers belong in them, row by row -- a schedule with
a real contents figure on one site and none on the next is ordinary, and no
single ratio describes it.

So this is the other path: populate a template, supply it, run it. The
integration brief ranks it above any assumption anyway. Its evidence hierarchy
is reported location values first, a supported value driver second, and the
equal split only as the maximum-ignorance fallback -- so where a user has real
numbers, the derived split should get out of the way entirely.

Two things this module refuses to do quietly.

It will not rescale supplied values to make them fit. If a schedule adds up to
something other than the policy's reported TIV, that is a discrepancy between
two pieces of reported information and an analyst has to see it, not have it
smoothed away. The difference is reported per location and in total, and the
caller decides whether the supplied evidence restates the portfolio or the file
needs correcting.

It will not treat a blank as a zero without saying so. An empty contents column
may mean "no contents" or "not yet known", and the two are different; a blank
is read as zero and counted, so the count is visible.

The same template carries ``OccupancyCode`` and ``ConstructionCode``, for the
same reason. The source reports neither, so a promotion applies a named
occupancy assumption -- but a schedule that states the real thing should not
have to accept one, and a schedule that states it for half its rows should not
have to choose all or nothing. A stated code is used; a blank falls through to
the assumption.
"""

from __future__ import annotations

import csv
import dataclasses
import io
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from .allocation import AllocationError, AllocationEvidence
from .components import COVERAGE_ORDER

#: Bumped when the template's shape changes.
TEMPLATE_VERSION = "1.1.0"

#: Columns the template carries. The first four identify the row and are not
#: edited; the reference total is there so someone filling it in can see what
#: the allocation would have produced; the last four are the ones to complete.
IDENTITY_COLUMNS = ("AccNumber", "LocNumber", "CountryCode", "LocationName")
REFERENCE_COLUMN = "AllocatedTIV"
COMPONENT_COLUMNS = tuple(str(coverage) for coverage in COVERAGE_ORDER)

#: Vulnerability attributes a schedule may state per row. Optional: a file that
#: leaves them blank falls through to whichever occupancy assumption the
#: promotion selected, which is the common case for this source.
TAXONOMY_COLUMNS = ("OccupancyCode", "ConstructionCode")

TEMPLATE_COLUMNS = (
    *IDENTITY_COLUMNS,
    REFERENCE_COLUMN,
    *COMPONENT_COLUMNS,
    *TAXONOMY_COLUMNS,
)

LocationKey = tuple[str, int]


@dataclasses.dataclass(frozen=True, slots=True)
class ReportedComponents:
    """Per-location coverage values a user supplied.

    ``evidence`` is ``REPORTED`` rather than ``ASSUMED``: these are numbers
    somebody stated, not weights the platform invented, and a result built on
    them should not be labelled as though it rests on a prior.
    """

    name: str
    values: Mapping[LocationKey, Mapping[str, Decimal]]
    blank_cells: int = 0
    evidence: AllocationEvidence = AllocationEvidence.REPORTED
    approved: bool = True
    template_version: str = TEMPLATE_VERSION
    #: Occupancy and construction where the file stated them. Only rows that
    #: carry a code appear, so "supplied nothing" and "supplied a blank" are
    #: the same thing here and both fall through to the assumption.
    taxonomy: Mapping[LocationKey, Mapping[str, str]] = dataclasses.field(
        default_factory=dict
    )

    def __len__(self) -> int:
        return len(self.values)

    def covers(self, keys: Iterable[LocationKey]) -> tuple[LocationKey, ...]:
        """Locations that were asked for but not supplied."""
        return tuple(sorted(key for key in keys if key not in self.values))

    def total_for(self, key: LocationKey) -> Decimal:
        return sum(self.values[key].values(), Decimal("0.00"))

    def apply(self, key: LocationKey) -> dict[str, Decimal]:
        try:
            supplied = self.values[key]
        except KeyError:
            raise AllocationError(
                f"No coverage values were supplied for location {key[0]}/{key[1]}."
            ) from None
        return {column: supplied.get(column, Decimal("0.00")) for column in COMPONENT_COLUMNS}

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": "reported_location_values",
            "evidence": str(self.evidence),
            "approved": self.approved,
            "template_version": self.template_version,
            "location_count": len(self.values),
            "blank_cells_read_as_zero": self.blank_cells,
            "locations_with_stated_taxonomy": len(self.taxonomy),
            "total": str(
                sum(
                    (self.total_for(key) for key in self.values),
                    Decimal("0.00"),
                )
            ),
        }


def template(rows: Iterable[Mapping[str, Any]]) -> bytes:
    """A CSV of the selected locations, ready to have real values typed in.

    The allocated total travels with each row as a reference. It is deliberately
    not one of the coverage columns: it says what the platform would have done,
    so that someone overriding it can see what they are overriding.
    """
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(TEMPLATE_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "AccNumber": row.get("business_id", ""),
                "LocNumber": row.get("location_number", ""),
                "CountryCode": row.get("country_code", ""),
                "LocationName": row.get("label", ""),
                REFERENCE_COLUMN: _text(row.get("allocated_tiv")),
                **{column: "" for column in COMPONENT_COLUMNS},
                **{column: "" for column in TAXONOMY_COLUMNS},
            }
        )
    return buffer.getvalue().encode("utf-8")


def read(payload: bytes | str, *, name: str = "supplied_location_values") -> ReportedComponents:
    """Read a completed template.

    Every coverage column is optional -- a schedule with only a building figure
    is perfectly ordinary -- but at least one must be present, or the file says
    nothing the platform did not already know.
    """
    text = payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload
    reader = csv.DictReader(io.StringIO(text))
    columns = {item.strip() for item in (reader.fieldnames or [])}

    missing = [item for item in ("AccNumber", "LocNumber") if item not in columns]
    if missing:
        raise AllocationError(
            "The coverage file is missing required columns: " + ", ".join(missing) + "."
        )
    present = [column for column in COMPONENT_COLUMNS if column in columns]
    if not present:
        raise AllocationError(
            "The coverage file carries no coverage column. Supply at least one of: "
            + ", ".join(COMPONENT_COLUMNS)
            + "."
        )

    stated_taxonomy = [column for column in TAXONOMY_COLUMNS if column in columns]
    values: dict[LocationKey, dict[str, Decimal]] = {}
    taxonomy: dict[LocationKey, dict[str, str]] = {}
    blanks = 0

    for line, row in enumerate(reader, start=2):
        business = str(row.get("AccNumber") or "").strip()
        raw_number = str(row.get("LocNumber") or "").strip()
        if not business and not raw_number:
            continue
        try:
            number = int(raw_number)
        except ValueError:
            raise AllocationError(
                f"Line {line} of the coverage file has an unreadable location number: "
                f"{raw_number!r}."
            ) from None

        key = (business, number)
        if key in values:
            raise AllocationError(
                f"Line {line} repeats location {business}/{number}. Each location "
                "may carry one set of coverage values."
            )

        supplied: dict[str, Decimal] = {}
        for column in present:
            cell = str(row.get(column) or "").strip()
            if not cell:
                blanks += 1
                supplied[column] = Decimal("0.00")
                continue
            try:
                supplied[column] = Decimal(cell.replace(",", "")).quantize(Decimal("0.01"))
            except (InvalidOperation, ValueError):
                raise AllocationError(
                    f"Line {line} has an unreadable {column}: {cell!r}."
                ) from None
        if any(amount < 0 for amount in supplied.values()):
            raise AllocationError(
                f"Line {line} carries a negative coverage value. A negative insured "
                "value is not something this platform can interpret."
            )
        values[key] = supplied

        # A blank taxonomy cell is not an assertion, so it is left out rather
        # than stored empty. Storing it would make "the schedule says nothing"
        # indistinguishable from "the schedule says unknown", and only the
        # second should override an assumption.
        codes = {
            column: str(row.get(column) or "").strip()
            for column in stated_taxonomy
            if str(row.get(column) or "").strip()
        }
        if codes.get("OccupancyCode"):
            taxonomy[key] = codes
        elif codes:
            raise AllocationError(
                f"Line {line} states a construction code with no occupancy code. OED "
                "requires an occupancy, and a construction alone cannot reach a "
                "vulnerability function."
            )

    if not values:
        raise AllocationError("The coverage file has no rows.")
    return ReportedComponents(
        name=name, values=values, blank_cells=blanks, taxonomy=taxonomy
    )


def reconcile(
    reported: ReportedComponents, expected: Mapping[LocationKey, Decimal]
) -> dict[str, Any]:
    """Compare what was supplied against what the allocation derived.

    A difference is not automatically an error. Reported location values
    outrank a derived split in the brief's evidence hierarchy, so a schedule
    that disagrees with the policy total may be the better number -- or the
    file may be wrong. Both readings are possible, so the numbers are reported
    and the decision is left to a person.
    """
    missing = reported.covers(expected.keys())
    unexpected = tuple(sorted(set(reported.values) - set(expected)))

    differences = []
    for key, derived in sorted(expected.items()):
        if key not in reported.values:
            continue
        supplied = reported.total_for(key)
        if supplied != derived:
            differences.append(
                {
                    "location": f"{key[0]}/{key[1]}",
                    "allocated": str(derived),
                    "supplied": str(supplied),
                    "difference": str(supplied - derived),
                }
            )

    derived_total = sum(expected.values(), Decimal("0.00"))
    supplied_total = sum(
        (reported.total_for(key) for key in reported.values if key in expected),
        Decimal("0.00"),
    )
    return {
        "location_count": len(expected),
        "supplied_count": len(reported),
        "missing_locations": [f"{key[0]}/{key[1]}" for key in missing],
        "unexpected_locations": [f"{key[0]}/{key[1]}" for key in unexpected],
        "allocated_total": str(derived_total),
        "supplied_total": str(supplied_total),
        "difference": str(supplied_total - derived_total),
        "matches_allocation": not differences and not missing,
        "restates_total": supplied_total != derived_total,
        "differences": differences[:200],
        "difference_count": len(differences),
        "blank_cells_read_as_zero": reported.blank_cells,
    }


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)
