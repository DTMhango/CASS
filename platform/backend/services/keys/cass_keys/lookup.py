"""The CASS keys service: the controlled bridge from OED exposure to the model.

Build plan section 8 sets the contract precisely, and it is unusually strict:

* every location, coverage type and applicable earthquake sub-peril or IMT
  route must receive a response -- a missing response is an error, not an
  absence;
* coordinates outside the grid, offshore, near a border or of low confidence
  are reported rather than silently snapped;
* successful, not-at-risk and failed TIV must reconcile to the published OED
  source before an analyst may proceed.

Section 15 names the failure this prevents: keys failures or not-at-risk values
being hidden, so exposure is silently omitted from loss.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Iterable, Iterator, Mapping, Sequence
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from cass_oed.schema import COVERAGE_TYPES, MODELLED_SUBPERILS, expand_perils

KEYS_SCHEMA_VERSION = "1.0.0"


class KeyStatus(enum.StrEnum):
    """The response statuses the Oasis keys contract defines."""

    SUCCESS = "success"
    """Mapped to an area peril and a vulnerability function."""

    FAIL_AP = "fail_ap"
    """No area peril: the location falls outside the grid domain."""

    FAIL_V = "fail_v"
    """No vulnerability: the taxonomy maps to no supported function."""

    FAIL = "fail"
    """Neither could be resolved, or the record could not be interpreted."""

    NOTATRISK = "notatrisk"
    """Deliberately not modelled: the coverage carries no value, or the
    sub-peril is out of scope for this model release. Distinct from a failure,
    and reported separately, because the two mean different things to an
    analyst."""

    @property
    def is_failure(self) -> bool:
        return self in (KeyStatus.FAIL, KeyStatus.FAIL_AP, KeyStatus.FAIL_V)


class LookupError_(Exception):
    """Raised when the lookup cannot run at all."""


@dataclasses.dataclass(frozen=True, slots=True)
class GridCell:
    """One cell of a versioned area-peril grid."""

    area_peril_id: int
    min_latitude: Decimal
    max_latitude: Decimal
    min_longitude: Decimal
    max_longitude: Decimal
    country_code: str
    #: Whether the cell sits offshore. Section 6 asks the Indonesian grid to
    #: avoid unnecessary calculation points over ocean; a location that lands
    #: on one is reported rather than accepted quietly.
    offshore: bool = False
    vs30: float | None = None

    def contains(self, latitude: Decimal, longitude: Decimal) -> bool:
        return (
            self.min_latitude <= latitude < self.max_latitude
            and self.min_longitude <= longitude < self.max_longitude
        )


@dataclasses.dataclass(frozen=True, slots=True)
class AreaPerilGrid:
    """A published, versioned grid.

    Area-peril identifiers are stable within a version. Section 6 forbids
    changing what an identifier means, so a grid carries its version and the
    lookup records which version produced each key.
    """

    country_code: str
    version: str
    cells: tuple[GridCell, ...]
    #: Distance beyond which a near-miss is reported rather than snapped.
    tolerance_km: Decimal = Decimal("0")

    #: Cells bucketed by whole degree, built once on first use. Section 6's
    #: work package asks for a spatial index rather than a linear scan, and at
    #: national scale the difference is real: Indonesia's prototype grid holds
    #: 52,000 cells, so scanning it for every location turns a portfolio
    #: mapping into millions of comparisons for no reason.
    #:
    #: A cell is registered in every bucket it touches rather than only the one
    #: its corner sits in, so the index stays correct for a grid whose cells are
    #: larger than a degree or do not align to one.
    _index: dict[tuple[int, int], tuple[GridCell, ...]] | None = dataclasses.field(
        default=None, init=False, repr=False, compare=False, hash=False
    )

    @property
    def reference(self) -> str:
        return f"{self.country_code.lower()}-grid-{self.version}"

    def _bucketed(self) -> dict[tuple[int, int], tuple[GridCell, ...]]:
        if self._index is None:
            buckets: dict[tuple[int, int], list[GridCell]] = {}
            for cell in self.cells:
                for key in _buckets_touched(cell):
                    buckets.setdefault(key, []).append(cell)
            object.__setattr__(
                self, "_index", {key: tuple(value) for key, value in buckets.items()}
            )
        return self._index

    def find(self, latitude: Decimal, longitude: Decimal) -> GridCell | None:
        """The cell a coordinate falls in, or None if the grid does not cover it."""
        for cell in self._bucketed().get(_bucket(latitude, longitude), ()):
            if cell.contains(latitude, longitude):
                return cell
        return None


@dataclasses.dataclass(frozen=True, slots=True)
class VulnerabilityEntry:
    """One row of the taxonomy mapping."""

    vulnerability_id: int
    coverage_type: int
    #: The intensity measure this function demands. Section 6 forbids routing a
    #: function to an IMT it was not built for.
    required_imt: str
    occupancy_codes: frozenset[str] = frozenset()
    construction_codes: frozenset[str] = frozenset()
    label: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class VulnerabilityMapping:
    """The taxonomy-to-vulnerability mapping for one model version."""

    country_code: str
    version: str
    entries: tuple[VulnerabilityEntry, ...]
    #: IMTs the converter can currently produce. A function demanding anything
    #: else is reported as unsupported rather than silently rerouted.
    supported_imts: frozenset[str] = frozenset({"SA(0.3)", "SA(0.6)", "SA(1.0)"})

    def find(
        self, occupancy: str, construction: str, coverage_type: int
    ) -> VulnerabilityEntry | None:
        """Resolve a taxonomy to exactly one function.

        Where both occupancy and construction match an entry it wins over an
        occupancy-only match, so a more specific mapping is preferred without
        the caller having to order the table.
        """
        best: VulnerabilityEntry | None = None
        best_score = -1
        for entry in self.entries:
            if entry.coverage_type != coverage_type:
                continue
            score = 0
            if entry.occupancy_codes:
                if occupancy not in entry.occupancy_codes:
                    continue
                score += 2
            if entry.construction_codes:
                if construction not in entry.construction_codes:
                    continue
                score += 1
            if score > best_score:
                best, best_score = entry, score
        return best


@dataclasses.dataclass(frozen=True, slots=True)
class KeyRecord:
    """One response row. Every expected combination produces exactly one."""

    location_id: str
    peril_id: str
    coverage_type: int
    status: KeyStatus
    area_peril_id: int | None = None
    vulnerability_id: int | None = None
    imt: str | None = None
    message: str = ""
    tiv: Decimal = Decimal(0)

    def as_row(self) -> dict[str, Any]:
        """The shape written to keys.csv."""
        return {
            "LocID": self.location_id,
            "PerilID": self.peril_id,
            "CoverageTypeID": self.coverage_type,
            "AreaPerilID": self.area_peril_id if self.area_peril_id is not None else "",
            "VulnerabilityID": (
                self.vulnerability_id if self.vulnerability_id is not None else ""
            ),
            "Status": str(self.status),
            "Message": self.message,
        }


@dataclasses.dataclass(slots=True)
class CoverageReport:
    """Mapped and unmapped counts and TIV, by reason.

    Section 8 requires this alongside the keys file, and section 15 requires it
    to be reconciled before a run may be approved.
    """

    source_tiv: Decimal = Decimal(0)
    by_status: dict[str, Decimal] = dataclasses.field(default_factory=dict)
    counts_by_status: dict[str, int] = dataclasses.field(default_factory=dict)
    by_reason: dict[str, Decimal] = dataclasses.field(default_factory=dict)
    counts_by_reason: dict[str, int] = dataclasses.field(default_factory=dict)

    def add(self, record: KeyRecord) -> None:
        status = str(record.status)
        self.by_status[status] = self.by_status.get(status, Decimal(0)) + record.tiv
        self.counts_by_status[status] = self.counts_by_status.get(status, 0) + 1
        if record.status is not KeyStatus.SUCCESS and record.message:
            self.by_reason[record.message] = (
                self.by_reason.get(record.message, Decimal(0)) + record.tiv
            )
            self.counts_by_reason[record.message] = (
                self.counts_by_reason.get(record.message, 0) + 1
            )

    @property
    def mapped_tiv(self) -> Decimal:
        return self.by_status.get(str(KeyStatus.SUCCESS), Decimal(0))

    @property
    def not_at_risk_tiv(self) -> Decimal:
        return self.by_status.get(str(KeyStatus.NOTATRISK), Decimal(0))

    @property
    def failed_tiv(self) -> Decimal:
        return sum(
            (
                value
                for status, value in self.by_status.items()
                if KeyStatus(status).is_failure
            ),
            Decimal(0),
        )

    @property
    def accounted_tiv(self) -> Decimal:
        return self.mapped_tiv + self.not_at_risk_tiv + self.failed_tiv

    @property
    def reconciled(self) -> bool:
        """Every unit of source value must land in exactly one bucket."""
        return self.accounted_tiv == self.source_tiv

    @property
    def difference(self) -> Decimal:
        return self.accounted_tiv - self.source_tiv

    @property
    def mapped_share(self) -> float:
        if self.source_tiv == 0:
            return 0.0
        return float(self.mapped_tiv / self.source_tiv)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": KEYS_SCHEMA_VERSION,
            "source_tiv": str(self.source_tiv),
            "mapped_tiv": str(self.mapped_tiv),
            "not_at_risk_tiv": str(self.not_at_risk_tiv),
            "failed_tiv": str(self.failed_tiv),
            "accounted_tiv": str(self.accounted_tiv),
            "difference": str(self.difference),
            "reconciled": self.reconciled,
            "mapped_share": self.mapped_share,
            "tiv_by_status": {k: str(v) for k, v in sorted(self.by_status.items())},
            "counts_by_status": dict(sorted(self.counts_by_status.items())),
            "tiv_by_reason": {k: str(v) for k, v in sorted(self.by_reason.items())},
            "counts_by_reason": dict(sorted(self.counts_by_reason.items())),
        }


@dataclasses.dataclass(slots=True)
class LookupResult:
    """The complete response for one portfolio."""

    records: list[KeyRecord]
    report: CoverageReport
    grid_reference: str
    vulnerability_reference: str

    @property
    def failures(self) -> list[KeyRecord]:
        """The errors file of section 8."""
        return [item for item in self.records if item.status.is_failure]

    @property
    def successes(self) -> list[KeyRecord]:
        return [item for item in self.records if item.status is KeyStatus.SUCCESS]

    def as_dict(self) -> dict[str, Any]:
        return {
            "grid": self.grid_reference,
            "vulnerability": self.vulnerability_reference,
            "record_count": len(self.records),
            "success_count": len(self.successes),
            "failure_count": len(self.failures),
            "coverage_report": self.report.as_dict(),
        }


def _floor(value: Decimal) -> int:
    """Floor, not truncation.

    ``Decimal // 1`` rounds toward zero, so -6.2 becomes -6 rather than -7.
    Most of Indonesia is south of the equator, so getting this wrong would put
    a location in a bucket its cell is not in and report the whole country as
    outside the grid.
    """
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def _bucket(latitude: Decimal, longitude: Decimal) -> tuple[int, int]:
    """The whole-degree square a coordinate belongs to."""
    return (_floor(latitude), _floor(longitude))


def _buckets_touched(cell: GridCell) -> Iterator[tuple[int, int]]:
    """Every whole-degree square a cell overlaps, however large the cell is."""
    latitude = _floor(cell.min_latitude)
    while latitude < cell.max_latitude:
        longitude = _floor(cell.min_longitude)
        while longitude < cell.max_longitude:
            yield (latitude, longitude)
            longitude += 1
        latitude += 1


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def lookup(
    locations: Iterable[Mapping[str, Any]],
    *,
    grid: AreaPerilGrid,
    vulnerability: VulnerabilityMapping,
    coverage_types: Mapping[str, int] = COVERAGE_TYPES,
    modelled_subperils: Sequence[str] | None = None,
) -> LookupResult:
    """Map every location and coverage to area-peril and vulnerability IDs.

    The loop below is written so that every expected combination reaches a
    ``KeyRecord``. There is no path that skips one: a record with no value is
    ``notatrisk``, an uninterpretable record is ``fail``, and anything mapped
    is ``success``. That is what makes the TIV reconciliation meaningful.
    """
    # An omitted list means "use the release default"; an empty one means the
    # caller believes nothing is modelled, which is a configuration error
    # rather than something to paper over with a default.
    if modelled_subperils is None:
        subperils = tuple(sorted(MODELLED_SUBPERILS))
    else:
        subperils = tuple(modelled_subperils)
    if not subperils:
        raise LookupError_("no modelled sub-perils were supplied")

    records: list[KeyRecord] = []
    report = CoverageReport()

    for row in locations:
        location_id = str(row.get("LocNumber") or row.get("LocID") or "").strip()
        latitude = _decimal(row.get("Latitude"))
        longitude = _decimal(row.get("Longitude"))
        occupancy = str(row.get("OccupancyCode") or "").strip()
        construction = str(row.get("ConstructionCode") or "").strip()
        covered = set(expand_perils(str(row.get("LocPerilsCovered") or "")))

        cell = (
            grid.find(latitude, longitude)
            if latitude is not None and longitude is not None
            else None
        )

        for column, coverage_type in coverage_types.items():
            tiv = _decimal(row.get(column)) or Decimal(0)
            report.source_tiv += tiv

            for peril in subperils:
                record = _resolve(
                    location_id=location_id,
                    peril=peril,
                    coverage_type=coverage_type,
                    tiv=tiv,
                    covered=covered,
                    latitude=latitude,
                    longitude=longitude,
                    cell=cell,
                    occupancy=occupancy,
                    construction=construction,
                    vulnerability=vulnerability,
                )
                records.append(record)
                # Value is attributed once per coverage, on the sub-peril that
                # carries it, so the reconciliation totals cannot double-count
                # a location covered for several sub-perils.
                if peril == subperils[0]:
                    report.add(record)

    return LookupResult(
        records=records,
        report=report,
        grid_reference=grid.reference,
        vulnerability_reference=f"{vulnerability.country_code.lower()}-vuln-{vulnerability.version}",
    )


def _resolve(
    *,
    location_id: str,
    peril: str,
    coverage_type: int,
    tiv: Decimal,
    covered: set[str],
    latitude: Decimal | None,
    longitude: Decimal | None,
    cell: GridCell | None,
    occupancy: str,
    construction: str,
    vulnerability: VulnerabilityMapping,
) -> KeyRecord:
    """Decide one location, coverage and sub-peril combination."""

    def record(status: KeyStatus, message: str = "", **extra: Any) -> KeyRecord:
        return KeyRecord(
            location_id=location_id,
            peril_id=peril,
            coverage_type=coverage_type,
            status=status,
            tiv=tiv,
            message=message,
            **extra,
        )

    if not location_id:
        return record(KeyStatus.FAIL, "The location has no identifier.")

    # No value means no loss. This is a deliberate exclusion, not a failure,
    # and is reported separately so the two are never conflated.
    if tiv <= 0:
        return record(KeyStatus.NOTATRISK, "The coverage carries no insured value.")

    if peril not in covered:
        return record(
            KeyStatus.NOTATRISK,
            f"The location is not covered for {peril}.",
        )

    if latitude is None or longitude is None:
        return record(KeyStatus.FAIL_AP, "The location has no usable coordinates.")

    if cell is None:
        return record(
            KeyStatus.FAIL_AP,
            "The location falls outside the area-peril grid domain.",
        )

    if cell.offshore:
        return record(
            KeyStatus.FAIL_AP,
            "The location falls in an offshore grid cell; confirm the coordinates.",
            area_peril_id=cell.area_peril_id,
        )

    entry = vulnerability.find(occupancy, construction, coverage_type)
    if entry is None:
        return record(
            KeyStatus.FAIL_V,
            (
                f"No vulnerability function covers occupancy {occupancy or 'unknown'} "
                f"and construction {construction or 'unknown'} for this coverage."
            ),
            area_peril_id=cell.area_peril_id,
        )

    # Section 6: a function must be routed to the IMT it demands. Producing a
    # key against an IMT the converter cannot supply would hide the gap the
    # SA-only prototype is supposed to report.
    if entry.required_imt not in vulnerability.supported_imts:
        return record(
            KeyStatus.FAIL_V,
            (
                f"Vulnerability {entry.vulnerability_id} requires {entry.required_imt}, "
                "which this model release does not produce."
            ),
            area_peril_id=cell.area_peril_id,
            vulnerability_id=entry.vulnerability_id,
            imt=entry.required_imt,
        )

    return record(
        KeyStatus.SUCCESS,
        "",
        area_peril_id=cell.area_peril_id,
        vulnerability_id=entry.vulnerability_id,
        imt=entry.required_imt,
    )
