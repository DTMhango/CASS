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

    def __post_init__(self) -> None:
        _validate_tiling(self.bins, what=f"intensity bin set for {self.imt}")

    @property
    def reference(self) -> str:
        return f"{self.imt}-bins-{self.version}"

    @property
    def lower_bounds(self) -> list[Decimal]:
        return [item.lower for item in self.bins]

    def find(self, value: Decimal) -> Bin | None:
        """Return the bin holding a ground-motion value, or None if out of range.

        Out of range is returned rather than clamped: a value above the top bin
        means the dictionary does not cover the hazard being converted, which
        an operator needs to know.
        """
        if value < self.bins[0].lower or value >= self.bins[-1].upper:
            return None
        position = bisect.bisect_right(self.lower_bounds, value) - 1
        return self.bins[position]

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
