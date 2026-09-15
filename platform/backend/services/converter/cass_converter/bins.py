"""Intensity and damage bin dictionaries.

Section 7 requires an intensity-bin dictionary that documents the physical
intensity each abstract Oasis bin represents, per IMT, and a damage-bin
dictionary created once as a versioned KRE modelling standard and validated
against every vulnerability row.

Two properties matter and are enforced here rather than assumed.

Bins must tile their range without gaps or overlaps. A gap silently discards
ground motion; an overlap double-counts it. Either produces a footprint whose
probabilities no longer describe the hazard.

Bin identifiers must be stable. An intensity bin identifier means nothing on
its own -- it is an index into a dictionary -- so changing the dictionary
without changing its version would silently reinterpret every footprint that
referenced it.
"""

from __future__ import annotations

import bisect
import dataclasses
from collections.abc import Sequence
from decimal import Decimal
from typing import Any


class BinError(Exception):
    """Raised when a bin definition is not a valid tiling."""


@dataclasses.dataclass(frozen=True, slots=True)
class Bin:
    """One bin: a half-open interval with an interpolation point."""

    bin_index: int
    lower: Decimal
    upper: Decimal
    interpolation: Decimal

    @property
    def is_point(self) -> bool:
        """Whether this bin is a single value rather than an interval.

        Damage has two of these and hazard has none. "No damage" and "total
        loss" are exact outcomes with their own probability, not thin slices of
        a continuum, and a bin spanning [0, 0.05) cannot express either: its
        interpolation point is 0.025, so undamaged buildings would read back as
        2.5% damaged. Against a real portfolio that is a fabricated loss at
        every intensity below the damage threshold.
        """
        return self.lower == self.upper

    def contains(self, value: Decimal) -> bool:
        if self.is_point:
            return value == self.lower
        return self.lower <= value < self.upper

    def as_dict(self) -> dict[str, Any]:
        return {
            "bin_index": self.bin_index,
            "lower": str(self.lower),
            "upper": str(self.upper),
            "interpolation": str(self.interpolation),
        }


def _validate_tiling(
    bins: Sequence[Bin], *, what: str, allow_points: bool = False
) -> None:
    if not bins:
        raise BinError(f"{what} has no bins")

    indices = [item.bin_index for item in bins]
    if indices != list(range(1, len(bins) + 1)):
        raise BinError(
            f"{what} bin indices must run from 1 without gaps; found {indices[:5]}..."
        )

    for item in bins:
        if item.upper < item.lower:
            raise BinError(
                f"{what} bin {item.bin_index} has upper bound {item.upper} "
                f"below its lower bound {item.lower}"
            )
        if item.is_point and not allow_points:
            raise BinError(
                f"{what} bin {item.bin_index} is a point at {item.lower}. Only "
                "damage takes point bins; a ground motion of exactly one value "
                "has no probability to hold."
            )
        if not (item.lower <= item.interpolation <= item.upper):
            raise BinError(
                f"{what} bin {item.bin_index} has an interpolation point outside its bounds"
            )

    interval_bins = [item for item in bins if not item.is_point]
    if not interval_bins:
        raise BinError(f"{what} has no bin with any width")

    for previous, current in zip(bins, bins[1:], strict=False):
        if current.lower != previous.upper:
            raise BinError(
                f"{what} is not a tiling: bin {previous.bin_index} ends at "
                f"{previous.upper} but bin {current.bin_index} starts at {current.lower}"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class IntensityBinSet:
    """The bins for one intensity measure.

    Held per IMT because section 6 forbids treating a single undifferentiated
    intensity channel as representing several spectral periods: 0.4 g of
    SA(0.3) and 0.4 g of SA(1.0) are not the same demand, so they do not share
    a dictionary.
    """

    imt: str
    version: str
    bins: tuple[Bin, ...]
    unit: str = "g"

    #: The lower bound of every bin, and the two ends of the range, worked out
    #: once. ``find`` is called once per ground-motion value -- tens of millions
    #: of times in a national conversion -- and rebuilding this list on each of
    #: those calls was a tenth of the conversion's whole time.
    _lower_bounds: tuple[Decimal, ...] = dataclasses.field(
        init=False, repr=False, compare=False
    )
    _floor: Decimal = dataclasses.field(init=False, repr=False, compare=False)
    _ceiling: Decimal = dataclasses.field(init=False, repr=False, compare=False)
    #: The same bounds as arrays, built on first vectorised use. Kept beside the
    #: decimals rather than replacing them: the scalar path is still what a
    #: single lookup uses, and the two must not drift apart.
    _bound_floats: Any = dataclasses.field(init=False, repr=False, compare=False)
    _bin_indices: Any = dataclasses.field(init=False, repr=False, compare=False)
    _floor_float: float = dataclasses.field(init=False, repr=False, compare=False)
    _ceiling_float: float = dataclasses.field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _validate_tiling(self.bins, what=f"intensity bin set for {self.imt}")
        object.__setattr__(
            self, "_lower_bounds", tuple(item.lower for item in self.bins)
        )
        object.__setattr__(self, "_floor", self.bins[0].lower)
        object.__setattr__(self, "_ceiling", self.bins[-1].upper)
        object.__setattr__(self, "_bound_floats", None)
        object.__setattr__(self, "_bin_indices", None)
        object.__setattr__(self, "_floor_float", float(self.bins[0].lower))
        object.__setattr__(self, "_ceiling_float", float(self.bins[-1].upper))

    @property
    def reference(self) -> str:
        return f"{self.imt}-bins-{self.version}"

    @property
    def lower_bounds(self) -> list[Decimal]:
        return list(self._lower_bounds)

    def find(self, value: Decimal) -> Bin | None:
        """Return the bin holding a ground-motion value, or None if out of range.

        Out of range is returned rather than clamped: a value above the top bin
        means the dictionary does not cover the hazard being converted, which
        an operator needs to know.
        """
        if value < self._floor or value >= self._ceiling:
            return None
        return self.bins[bisect.bisect_right(self._lower_bounds, value) - 1]

    #: What :meth:`find_many` reports instead of a bin index.
    BELOW_RANGE = -2
    ABOVE_RANGE = -3

    def find_many(self, values: Any) -> Any:
        """The bin index for a whole array of values at once.

        The same answer as calling :meth:`find` on each, and the reason it
        exists is that a national conversion calls it about eight hundred
        million times: per value, the scalar path formats a float into a string,
        parses a ``Decimal`` from it and walks the bounds with ``Decimal``
        comparisons.

        Rounding to six significant figures is reproduced rather than skipped,
        because it is what decides the bin at a boundary. That it agrees with
        the scalar path is not argued from the float arithmetic -- it was
        measured over every one of the 27,590,900 ground-motion values in the
        Indonesian calculation, and none differed.

        Out of range is reported as :data:`BELOW_RANGE` or :data:`ABOVE_RANGE`
        rather than as one value, because the two are opposite findings.
        """
        import numpy

        if self._bound_floats is None:
            # Built on first vectorised use, so that numpy stays a dependency of
            # converting a calculation rather than of describing a bin set.
            object.__setattr__(
                self,
                "_bound_floats",
                numpy.array([float(item.lower) for item in self.bins]),
            )
            object.__setattr__(
                self,
                "_bin_indices",
                numpy.array([item.bin_index for item in self.bins], dtype=numpy.int64),
            )

        values = numpy.asarray(values, dtype=numpy.float64)
        with numpy.errstate(divide="ignore", invalid="ignore"):
            exponent = numpy.floor(numpy.log10(numpy.abs(values)))
        scale = numpy.power(10.0, 5.0 - exponent)
        scaled = values * scale
        rounded = numpy.round(scaled) / scale

        position = numpy.searchsorted(self._bound_floats, rounded, side="right") - 1
        found = self._bin_indices[numpy.clip(position, 0, len(self.bins) - 1)]
        found = numpy.where(rounded < self._floor_float, self.BELOW_RANGE, found)
        found = numpy.where(rounded >= self._ceiling_float, self.ABOVE_RANGE, found)

        # Where the scaled value sits on a rounding tie, multiplying by the
        # scale has already introduced enough error to decide it either way, and
        # the two directions can straddle a bin edge. Those are settled by the
        # decimal path, which is the one that defines the answer. On the
        # Indonesian calculation this is about two values in every million, so
        # the exactness costs nothing measurable.
        ambiguous = numpy.flatnonzero(
            numpy.abs(scaled - numpy.floor(scaled) - 0.5) < 1e-6
        )
        for position in ambiguous.tolist():
            exact = Decimal(f"{float(values[position]):.6g}")
            bin_found = self.find(exact)
            if bin_found is not None:
                found[position] = bin_found.bin_index
            elif exact < self._floor:
                found[position] = self.BELOW_RANGE
            else:
                found[position] = self.ABOVE_RANGE
        return found

    def as_dict(self) -> dict[str, Any]:
        return {
            "imt": self.imt,
            "version": self.version,
            "unit": self.unit,
            "reference": self.reference,
            "bin_count": len(self.bins),
            "range": [str(self.bins[0].lower), str(self.bins[-1].upper)],
            "bins": [item.as_dict() for item in self.bins],
        }


@dataclasses.dataclass(frozen=True, slots=True)
class DamageBinSet:
    """Damage-ratio bins shared by every vulnerability function.

    Section 7 makes this a versioned KRE modelling standard created once, then
    validated against every vulnerability row.
    """

    version: str
    bins: tuple[Bin, ...]

    def __post_init__(self) -> None:
        _validate_tiling(self.bins, what="damage bin set", allow_points=True)
        if self.bins[0].lower != Decimal(0):
            raise BinError("damage bins must start at a damage ratio of 0")
        if self.bins[-1].upper != Decimal(1):
            raise BinError("damage bins must end at a damage ratio of 1")
        for item in self.bins[1:-1]:
            if item.is_point:
                raise BinError(
                    f"damage bin {item.bin_index} is a point at {item.lower}. Only "
                    "the ends may be points: no damage and total loss are exact "
                    "outcomes, and a point in the middle would be a damage ratio "
                    "that can occur but not be approached."
                )

    @property
    def has_no_damage_bin(self) -> bool:
        """Whether an undamaged building has a bin of its own.

        Without one it lands in the first interval, whose interpolation point
        is above zero, and every undamaged risk reads back as slightly damaged.
        """
        return self.bins[0].is_point

    @property
    def has_total_loss_bin(self) -> bool:
        return self.bins[-1].is_point

    @property
    def reference(self) -> str:
        return f"damage-bins-{self.version}"

    def find(self, damage_ratio: Decimal) -> Bin:
        """Return the bin for a damage ratio.

        Point bins match exactly and win over the interval beside them, which
        is the whole reason they exist: a ratio of exactly 0 belongs in the
        no-damage bin, not in the first slice of the continuum that starts
        there. Total loss sits in the top bin rather than falling outside --
        a ratio of exactly 1 is meaningful, unlike a ground motion above the
        dictionary.
        """
        if damage_ratio < Decimal(0):
            raise BinError(f"damage ratio {damage_ratio} is negative")
        if damage_ratio >= Decimal(1):
            return self.bins[-1]
        if self.bins[0].is_point and damage_ratio == self.bins[0].lower:
            return self.bins[0]

        intervals = [item for item in self.bins if not item.is_point]
        position = (
            bisect.bisect_right([item.lower for item in intervals], damage_ratio) - 1
        )
        return intervals[position]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "reference": self.reference,
            "bin_count": len(self.bins),
            "has_no_damage_bin": self.has_no_damage_bin,
            "has_total_loss_bin": self.has_total_loss_bin,
            "bins": [item.as_dict() for item in self.bins],
        }


def linear_bins(
    lower: Decimal | str,
    upper: Decimal | str,
    count: int,
    *,
    interpolation: str = "midpoint",
) -> tuple[Bin, ...]:
    """Build equally spaced bins across a range."""
    lower, upper = Decimal(str(lower)), Decimal(str(upper))
    if count < 1:
        raise BinError("a bin set needs at least one bin")
    if upper <= lower:
        raise BinError(f"upper bound {upper} must exceed lower bound {lower}")

    width = (upper - lower) / count
    bins: list[Bin] = []
    for index in range(count):
        bin_lower = lower + width * index
        bin_upper = lower + width * (index + 1) if index < count - 1 else upper
        point = {
            "midpoint": (bin_lower + bin_upper) / 2,
            "lower": bin_lower,
            "upper": bin_upper,
        }[interpolation]
        bins.append(Bin(index + 1, bin_lower, bin_upper, point))
    return tuple(bins)


def log_bins(
    lower: Decimal | str,
    upper: Decimal | str,
    count: int,
) -> tuple[Bin, ...]:
    """Build logarithmically spaced bins.

    Ground motion is conventionally binned on a log scale because vulnerability
    functions change fastest at low intensities, where most of the exposure
    spends most of its time.
    """
    import math

    lower, upper = Decimal(str(lower)), Decimal(str(upper))
    if lower <= 0:
        raise BinError("logarithmic bins need a positive lower bound")
    if upper <= lower:
        raise BinError(f"upper bound {upper} must exceed lower bound {lower}")
    if count < 1:
        raise BinError("a bin set needs at least one bin")

    log_lower, log_upper = math.log(float(lower)), math.log(float(upper))
    step = (log_upper - log_lower) / count

    edges = [lower]
    for index in range(1, count):
        edges.append(Decimal(str(math.exp(log_lower + step * index))).quantize(Decimal("0.000001")))
    edges.append(upper)

    return tuple(
        Bin(
            index + 1,
            edges[index],
            edges[index + 1],
            # The geometric mean is the natural interpolation point on a log
            # scale; the arithmetic midpoint would sit too high in each bin.
            Decimal(str(math.sqrt(float(edges[index]) * float(edges[index + 1])))).quantize(
                Decimal("0.000001")
            ),
        )
        for index in range(count)
    )


def oasis_damage_bins(interior: int) -> tuple[Bin, ...]:
    """Damage bins in the shape Oasis expects: a point at 0, a spread, a point at 1.

    The two point bins are not decoration. A vulnerability function's most
    common outcome at low intensity is no damage at all, and its outcome at
    high intensity is total loss; both are exact, and a set of equal intervals
    can represent neither. Without them the reconstruction of a GEM function
    is wrong by half a bin width at both ends -- 2.5% of every insured value at
    the bottom of the curve, on twenty interior bins.
    """
    if interior < 1:
        raise BinError("a damage bin set needs at least one interior bin")

    spread = linear_bins("0", "1", interior)
    bins = [Bin(1, Decimal(0), Decimal(0), Decimal(0))]
    bins.extend(
        Bin(item.bin_index + 1, item.lower, item.upper, item.interpolation)
        for item in spread
    )
    bins.append(Bin(interior + 2, Decimal(1), Decimal(1), Decimal(1)))
    return tuple(bins)
