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

**Classes and channels.** The unit this resolves to is a *class* -- a
combination of occupancy, construction and height band that a schedule can
distinguish -- and not a single row. Most classes are answered by one function.
A class reaching structures that respond at different spectral periods is
answered by several, one per measure, and each carries the share of the class
those structures hold. Returning the whole class rather than the first matching
row is what lets such a risk be answered at all; whether it *may* be depends on
the multi-IMT representation the set was built under, and a class that spans
measures under an undecided one is refused rather than approximated.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Iterable, Iterator, Mapping, Sequence
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from cass_core.policy import MULTI_CHANNEL_REPRESENTATIONS, IMTRepresentation
from cass_oed.schema import COVERAGE_TYPES, MODELLED_SUBPERILS, expand_perils

KEYS_SCHEMA_VERSION = "1.0.0"

#: The band name meaning "the schedule stated no storey count". Distinct from
#: an entry carrying no band at all: that one answers whatever a location says,
#: this one answers only silence.
UNSTATED_BAND = "unstated"

#: How far one class's channel weights may sum from 1 before the mapping is
#: refused. A class whose channels summed to 0.8 would drop a fifth of the
#: risk's damage with nothing reporting it.
CHANNEL_WEIGHT_TOLERANCE = 1e-04


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


class MappingError(LookupError_):
    """Raised when a vulnerability mapping is not usable as a routing table.

    Separate from a location that fails to route. This is the file being wrong
    -- a class whose channels do not sum to one, two classes that answer the
    same risk equally well -- and it is refused when the mapping is built, not
    when a risk happens to reach it.
    """


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
    """One row of the taxonomy mapping: one function, for one class of risk.

    A *class* is what a schedule can distinguish -- occupancy, construction and
    height band -- and it is the unit the lookup resolves to. Most classes are
    one function, and then a class and an entry are the same thing. Where a
    class reaches structures responding at different spectral periods it is
    several, and each is a **channel**: one intensity measure, carrying the
    share of the class's weight the structures demanding it hold.
    """

    vulnerability_id: int
    coverage_type: int
    #: The intensity measure this function demands. Section 6 forbids routing a
    #: function to an IMT it was not built for.
    required_imt: str
    occupancy_codes: frozenset[str] = frozenset()
    construction_codes: frozenset[str] = frozenset()
    label: str = ""
    #: The height band this entry answers. Empty means the mapping does not
    #: distinguish height at all, so the entry answers whatever a location says.
    storey_band: str = ""
    #: The band's extent in storeys, inclusive. ``None`` at either end is
    #: unbounded, so a top band of ``8`` to ``None`` is "eight or more".
    min_storeys: int | None = None
    max_storeys: int | None = None
    #: This channel's share of its class; 1 where the class is one function.
    channel_weight: float = 1.0

    @property
    def class_key(self) -> tuple[int, frozenset[str], frozenset[str], str]:
        """What this entry answers *for*, ignoring which channel of it this is."""
        return (
            self.coverage_type,
            self.occupancy_codes,
            self.construction_codes,
            self.storey_band,
        )

    @property
    def specificity(self) -> int:
        """How narrowly this entry is stated.

        Occupancy outranks construction, which outranks height, so an entry
        naming more attributes always beats one naming fewer and the existing
        preference for occupancy is unchanged. Height is the finest tiebreak
        rather than the coarsest because a mapping stating occupancy and
        construction says more about a building than one knowing only how tall
        it is.

        The weights are powers of two on purpose. That makes the score a
        faithful record of *which* attributes were stated rather than merely
        how many, so two entries scoring the same necessarily state the same
        set of them -- which is what lets the ambiguity check below compare
        like with like.
        """
        return (
            (4 if self.occupancy_codes else 0)
            + (2 if self.construction_codes else 0)
            + (1 if self.storey_band else 0)
        )

    def matches_storeys(self, storeys: int | None) -> bool:
        """Whether this entry answers a location of this height.

        Three cases, and conflating any two loses information. No band means
        the mapping does not distinguish height, so the entry answers every
        location. The ``unstated`` band answers only a location that gave no
        storey count -- falling back to it for one that did would discard a
        field the schedule took the trouble to fill in, and the whole reason
        for collecting storeys is that it narrows the mixture. Any other band
        answers the heights inside it.
        """
        if not self.storey_band:
            return True
        if self.storey_band == UNSTATED_BAND:
            return storeys is None
        if storeys is None:
            return False
        if self.min_storeys is not None and storeys < self.min_storeys:
            return False
        return self.max_storeys is None or storeys <= self.max_storeys


@dataclasses.dataclass(frozen=True, slots=True)
class VulnerabilityClass:
    """One class of risk and every channel that answers it."""

    coverage_type: int
    occupancy_codes: frozenset[str]
    construction_codes: frozenset[str]
    storey_band: str
    specificity: int
    channels: tuple[VulnerabilityEntry, ...]

    @property
    def is_multi_channel(self) -> bool:
        return len(self.channels) > 1

    @property
    def intensity_measures(self) -> tuple[str, ...]:
        return tuple(item.required_imt for item in self.channels)

    def matches_storeys(self, storeys: int | None) -> bool:
        return self.channels[0].matches_storeys(storeys)


@dataclasses.dataclass(frozen=True, slots=True)
class VulnerabilityMapping:
    """The taxonomy-to-vulnerability mapping for one model version."""

    country_code: str
    version: str
    entries: tuple[VulnerabilityEntry, ...]
    #: IMTs the converter can currently produce. A function demanding anything
    #: else is reported as unsupported rather than silently rerouted.
    supported_imts: frozenset[str] = frozenset({"SA(0.3)", "SA(0.6)", "SA(1.0)"})
    #: Which multi-IMT representation this set was built under.
    #:
    #: ``UNDECIDED`` is the honest default and it is not a blanket failure: a
    #: class with one channel needs no representation and routes normally. A
    #: class with several cannot be answered until somebody chooses, so it is
    #: refused rather than approximated -- and the coverage report says how
    #: much value that costs, which is the number the decision needs.
    imt_representation: IMTRepresentation = IMTRepresentation.UNDECIDED

    #: Entries grouped into the classes they answer, built once on construction
    #: because that is also where the mapping is validated.
    _classes: tuple[VulnerabilityClass, ...] = dataclasses.field(
        default=(), init=False, repr=False, compare=False, hash=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_classes", _group_into_classes(self))
        _refuse_ambiguous_classes(self.country_code, self._classes)

    @property
    def classes(self) -> tuple[VulnerabilityClass, ...]:
        return self._classes

    @property
    def multi_channel_classes(self) -> tuple[VulnerabilityClass, ...]:
        """Classes that cannot be answered without the section 6 decision."""
        return tuple(item for item in self._classes if item.is_multi_channel)

    @property
    def resolves_multi_channel(self) -> bool:
        """Whether this mapping's representation can answer such a class."""
        return self.imt_representation in MULTI_CHANNEL_REPRESENTATIONS

    def find_channels(
        self,
        occupancy: str,
        construction: str,
        coverage_type: int,
        storeys: int | None = None,
    ) -> tuple[VulnerabilityEntry, ...]:
        """Every function this taxonomy reaches, as the channels of one class.

        Resolution is to a class, not to a row. Where both occupancy and
        construction match, that class wins over an occupancy-only one, so a
        more specific mapping is preferred without the caller having to order
        the table. Returning the whole class is the change that matters: a risk
        reaching several intensity measures is answered with all of them,
        rather than with whichever row happened to sort first.
        """
        best: VulnerabilityClass | None = None
        for item in self._classes:
            if item.coverage_type != coverage_type:
                continue
            if item.occupancy_codes and occupancy not in item.occupancy_codes:
                continue
            if item.construction_codes and construction not in item.construction_codes:
                continue
            if not item.matches_storeys(storeys):
                continue
            if best is None or item.specificity > best.specificity:
                best = item
        return best.channels if best is not None else ()


def _group_into_classes(
    mapping: VulnerabilityMapping,
) -> tuple[VulnerabilityClass, ...]:
    """Gather entries into classes, refusing the ones that cannot be one.

    Two things are checked here rather than at lookup time, because a mapping
    that fails either is wrong for every risk and not just for the one that
    happened to reach it: a class must not name the same intensity measure
    twice, and its channel weights must account for the whole class.
    """
    grouped: dict[tuple[Any, ...], list[VulnerabilityEntry]] = {}
    for entry in mapping.entries:
        grouped.setdefault(entry.class_key, []).append(entry)

    classes: list[VulnerabilityClass] = []
    for (coverage_type, occupancy, construction, band), members in grouped.items():
        measures = [item.required_imt for item in members]
        if len(set(measures)) != len(measures):
            raise MappingError(
                f"In the {mapping.country_code} mapping, the class for coverage "
                f"{coverage_type} occupancy {sorted(occupancy) or 'any'} names "
                f"{sorted(measures)} -- the same intensity measure twice. Channels "
                "are distinguished by their measure, so which function a risk "
                "reached would depend on row order."
            )
        total = sum(item.channel_weight for item in members)
        if abs(total - 1.0) > CHANNEL_WEIGHT_TOLERANCE:
            raise MappingError(
                f"In the {mapping.country_code} mapping, the channel weights for "
                f"coverage {coverage_type} occupancy {sorted(occupancy) or 'any'} "
                f"sum to {total} rather than 1. The difference is damage that would "
                "be dropped or double-counted with nothing reporting it."
            )
        classes.append(
            VulnerabilityClass(
                coverage_type=coverage_type,
                occupancy_codes=occupancy,
                construction_codes=construction,
                storey_band=band,
                specificity=members[0].specificity,
                channels=tuple(
                    sorted(members, key=lambda item: item.vulnerability_id)
                ),
            )
        )
    return tuple(classes)


def _refuse_ambiguous_classes(
    country_code: str, classes: Sequence[VulnerabilityClass]
) -> None:
    """Refuse two classes that could answer the same risk equally well.

    The counterpart of the check the specification builder runs, applied to the
    mapping itself so that a file produced by the converter -- which never goes
    through a specification -- is held to the same rule. Equal specificity is
    the condition that matters: a generic class and a specific one both
    matching is fine and intended, because the specific one wins. Two at the
    same score is not, because the answer would depend on row order.
    """
    for position, first in enumerate(classes):
        for second in classes[position + 1 :]:
            if first.coverage_type != second.coverage_type:
                continue
            if first.specificity != second.specificity:
                continue
            if not _codes_overlap(first.occupancy_codes, second.occupancy_codes):
                continue
            if not _codes_overlap(
                first.construction_codes, second.construction_codes
            ):
                continue
            if not _bands_overlap(first, second):
                continue
            raise MappingError(
                f"In the {country_code} mapping, two classes answer the same risk "
                f"for coverage {first.coverage_type} at the same specificity: "
                f"{first.channels[0].label!r} and {second.channels[0].label!r}. "
                "Which one a location reached would depend on table order, so the "
                "loss would not be reproducible. Narrow one of them."
            )


def _codes_overlap(first: frozenset[str], second: frozenset[str]) -> bool:
    """Whether two code sets can match the same value; empty means any."""
    return not first or not second or bool(first & second)


def _bands_overlap(first: VulnerabilityClass, second: VulnerabilityClass) -> bool:
    """Whether two height bands can both answer the same location.

    Only reached for classes of equal specificity, and because the specificity
    weights are powers of two that means either both state a band or neither
    does -- so there is no case here where a banded class meets an unbanded one.
    """
    if not first.storey_band or not second.storey_band:
        return True
    if first.storey_band == second.storey_band:
        return True
    if UNSTATED_BAND in (first.storey_band, second.storey_band):
        # One answers silence and the other answers a stated height. No single
        # location is both.
        return False
    return _ranges_overlap(first.channels[0], second.channels[0])


def _ranges_overlap(first: VulnerabilityEntry, second: VulnerabilityEntry) -> bool:
    low = max(first.min_storeys or 0, second.min_storeys or 0)
    highs = [
        item
        for item in (first.max_storeys, second.max_storeys)
        if item is not None
    ]
    return not highs or low <= min(highs)


@dataclasses.dataclass(frozen=True, slots=True)
class KeyRecord:
    """One response row. Every expected combination produces exactly one."""

    location_id: str
    """The OED location identity: account and location number together.

    Not ``LocNumber`` alone. OED makes a location number unique within an
    account, not within a portfolio, so a portfolio of four businesses that
    each schedule a location 1 has four rows sharing that number -- at four
    different coordinates. Keying on it would give three of them another's
    area peril.
    """

    peril_id: str
    coverage_type: int
    status: KeyStatus
    area_peril_id: int | None = None
    vulnerability_id: int | None = None
    imt: str | None = None
    message: str = ""
    tiv: Decimal = Decimal(0)
    #: The two halves of the identity, kept so the keys and errors files stay
    #: readable against the source OED rather than needing the composite split.
    account_id: str = ""
    location_number: str = ""
    #: Which channel of its class this row is, and how many there are. A class
    #: answered by one function produces ``1`` of ``1``; one spanning intensity
    #: measures produces a row per measure, and the weight is the share of the
    #: class each carries.
    channel_index: int = 1
    channel_count: int = 1
    channel_weight: float = 1.0

    @property
    def carries_value(self) -> bool:
        """Whether this row is the one the coverage's TIV is counted on.

        A coverage produces a row per sub-peril and, where the class spans
        intensity measures, a row per channel as well. All of them describe the
        same money, so exactly one is counted or the reconciliation would
        report several times the value the schedule holds.
        """
        return self.channel_index == 1

    def as_row(self) -> dict[str, Any]:
        """The shape written to keys.csv."""
        return {
            "LocID": self.location_id,
            "AccNumber": self.account_id,
            "LocNumber": self.location_number,
            "PerilID": self.peril_id,
            "CoverageTypeID": self.coverage_type,
            "AreaPerilID": self.area_peril_id if self.area_peril_id is not None else "",
            "VulnerabilityID": (
                self.vulnerability_id if self.vulnerability_id is not None else ""
            ),
            "ChannelWeight": f"{self.channel_weight:.6f}",
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

    #: How this run's mapping represents a class spanning intensity measures.
    #: Recorded on the result so a keys file can be read years later without
    #: having to find the registry record that produced it.
    imt_representation: str = str(IMTRepresentation.UNDECIDED)

    @property
    def failures(self) -> list[KeyRecord]:
        """The errors file of section 8."""
        return [item for item in self.records if item.status.is_failure]

    @property
    def successes(self) -> list[KeyRecord]:
        return [item for item in self.records if item.status is KeyStatus.SUCCESS]

    @property
    def multi_channel_successes(self) -> list[KeyRecord]:
        """Successful rows belonging to a class that spans intensity measures."""
        return [item for item in self.successes if item.channel_count > 1]

    def as_dict(self) -> dict[str, Any]:
        return {
            "grid": self.grid_reference,
            "vulnerability": self.vulnerability_reference,
            "imt_representation": self.imt_representation,
            "record_count": len(self.records),
            "success_count": len(self.successes),
            "failure_count": len(self.failures),
            "multi_channel_success_count": len(self.multi_channel_successes),
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


def _location_identity(row: Mapping[str, Any]) -> tuple[str, str, str]:
    """The account, the location number, and the identity they form together.

    OED's location key is the portfolio, the account and the location number.
    The portfolio is constant within one lookup, so account and location number
    are what distinguish a row -- and a promoted Klapton Re cohort is the case
    that proves the number alone will not: four businesses, each with a
    location 1, at four different coordinates.
    """
    number = str(row.get("LocNumber") or row.get("LocID") or "").strip()
    account = str(row.get("AccNumber") or "").strip()
    if not number:
        return account, "", ""
    return account, number, f"{account}/{number}" if account else number


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def read_storeys(value: Any) -> int | None:
    """The reported storey count, or None where the schedule did not say.

    A value that will not read as a whole number is treated as not stated.
    ``NumberOfStoreys`` is validated as an integer on the way into OED, so
    anything reaching here that is not one came from a source that bypassed
    that -- and the safe reading of an uninterpretable height is that the
    height is unknown, which widens the mixture rather than narrowing it to
    the wrong band.

    Zero is not stated either. It is OED's default for the field, and oasislmf
    fills a blank with it before the engine's lookup sees the row, so a schedule
    CASS wrote blank arrives in the engine as 0. Read as a height, it is one no
    band covers, and every risk without a storey count fails there while CASS's
    own keys answer it.
    """
    if value is None or value == "":
        return None
    try:
        storeys = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return None if storeys == 0 else storeys


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

    A combination can now produce *more* than one record, where the class it
    resolves to spans intensity measures and the mapping's representation can
    carry that. The reconciliation is unaffected because value is attributed on
    the first channel of the first sub-peril only -- the other rows describe the
    same money seen through a different measure, not additional exposure.
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
        account_id, location_number, location_id = _location_identity(row)
        latitude = _decimal(row.get("Latitude"))
        longitude = _decimal(row.get("Longitude"))
        occupancy = str(row.get("OccupancyCode") or "").strip()
        construction = str(row.get("ConstructionCode") or "").strip()
        storeys = read_storeys(row.get("NumberOfStoreys"))
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
                resolved = _resolve(
                    location_id=location_id,
                    account_id=account_id,
                    location_number=location_number,
                    peril=peril,
                    coverage_type=coverage_type,
                    tiv=tiv,
                    covered=covered,
                    latitude=latitude,
                    longitude=longitude,
                    cell=cell,
                    occupancy=occupancy,
                    construction=construction,
                    storeys=storeys,
                    vulnerability=vulnerability,
                )
                records.extend(resolved)
                # Value is attributed once per coverage, on the first sub-peril
                # and the first channel, so the reconciliation totals cannot
                # double-count a location covered for several sub-perils or a
                # class answered through several intensity measures.
                if peril == subperils[0]:
                    report.add(resolved[0])

    return LookupResult(
        records=records,
        report=report,
        grid_reference=grid.reference,
        vulnerability_reference=f"{vulnerability.country_code.lower()}-vuln-{vulnerability.version}",
        imt_representation=str(vulnerability.imt_representation),
    )


def _resolve(
    *,
    location_id: str,
    account_id: str,
    location_number: str,
    peril: str,
    coverage_type: int,
    tiv: Decimal,
    covered: set[str],
    latitude: Decimal | None,
    longitude: Decimal | None,
    cell: GridCell | None,
    occupancy: str,
    construction: str,
    storeys: int | None,
    vulnerability: VulnerabilityMapping,
) -> list[KeyRecord]:
    """Decide one location, coverage and sub-peril combination.

    Returns a list because a class spanning intensity measures is answered by
    one row per measure. Every path returns at least one record, and the first
    one returned is the one the coverage's value is counted on.
    """

    def record(status: KeyStatus, message: str = "", **extra: Any) -> KeyRecord:
        return KeyRecord(
            location_id=location_id,
            account_id=account_id,
            location_number=location_number,
            peril_id=peril,
            coverage_type=coverage_type,
            status=status,
            tiv=tiv,
            message=message,
            **extra,
        )

    if not location_id:
        return [record(KeyStatus.FAIL, "The location has no identifier.")]

    # No value means no loss. This is a deliberate exclusion, not a failure,
    # and is reported separately so the two are never conflated.
    if tiv <= 0:
        return [record(KeyStatus.NOTATRISK, "The coverage carries no insured value.")]

    if peril not in covered:
        return [
            record(
                KeyStatus.NOTATRISK,
                f"The location is not covered for {peril}.",
            )
        ]

    if latitude is None or longitude is None:
        return [record(KeyStatus.FAIL_AP, "The location has no usable coordinates.")]

    if cell is None:
        return [
            record(
                KeyStatus.FAIL_AP,
                "The location falls outside the area-peril grid domain.",
            )
        ]

    if cell.offshore:
        return [
            record(
                KeyStatus.FAIL_AP,
                "The location falls in an offshore grid cell; confirm the coordinates.",
                area_peril_id=cell.area_peril_id,
            )
        ]

    channels = vulnerability.find_channels(
        occupancy, construction, coverage_type, storeys
    )
    if not channels:
        height = "unstated" if storeys is None else f"{storeys} storeys"
        return [
            record(
                KeyStatus.FAIL_V,
                (
                    f"No vulnerability function covers occupancy "
                    f"{occupancy or 'unknown'}, construction "
                    f"{construction or 'unknown'} and height {height} for this "
                    "coverage."
                ),
                area_peril_id=cell.area_peril_id,
            )
        ]

    # Section 6: a function must be routed to the IMT it demands. Producing a
    # key against an IMT the converter cannot supply would hide the gap the
    # SA-only prototype is supposed to report.
    unsupported = sorted(
        {
            item.required_imt
            for item in channels
            if item.required_imt not in vulnerability.supported_imts
        }
    )
    if unsupported:
        return [
            record(
                KeyStatus.FAIL_V,
                (
                    f"This class requires {', '.join(unsupported)}, which this model "
                    "release does not produce."
                ),
                area_peril_id=cell.area_peril_id,
                vulnerability_id=channels[0].vulnerability_id,
                imt=channels[0].required_imt,
            )
        ]

    # A class reaching one intensity measure needs no representation and is
    # answered whatever the policy says. One reaching several cannot be, and
    # the refusal is the point: choosing silently is the failure section 15
    # names, and the value that lands here is what makes the decision urgent.
    if len(channels) > 1 and not vulnerability.resolves_multi_channel:
        measures = ", ".join(item.required_imt for item in channels)
        reason = (
            "no multi-IMT representation has been approved"
            if vulnerability.imt_representation is IMTRepresentation.UNDECIDED
            else (
                f"the approved representation ({vulnerability.imt_representation}) "
                "resolves such a class outside the keys contract"
            )
        )
        return [
            record(
                KeyStatus.FAIL_V,
                (
                    f"This class responds at {measures} and cannot be one function; "
                    f"{reason}."
                ),
                area_peril_id=cell.area_peril_id,
            )
        ]

    return [
        record(
            KeyStatus.SUCCESS,
            "",
            area_peril_id=cell.area_peril_id,
            vulnerability_id=item.vulnerability_id,
            imt=item.required_imt,
            channel_index=position,
            channel_count=len(channels),
            channel_weight=item.channel_weight,
        )
        for position, item in enumerate(channels, start=1)
    ]
