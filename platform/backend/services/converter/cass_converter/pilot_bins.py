"""Draft damage and intensity bin dictionaries for the pilot vulnerability sets.

Two discretisations, and they fail in different directions, which is why they
are stated separately rather than derived from one "resolution" number.

**Damage bins** discretise the loss ratio. The count buys accuracy in the mean
and, more importantly, in the shape: Oasis samples a damage ratio from the bin
probabilities, so a coarse set turns a smooth beta into a staircase and the
tail of a secondary-uncertainty distribution is exactly where a reinsurer's
money is. The two point bins at 0 and 1 are not negotiable -- without them an
undamaged building reads back as a few per cent of TIV at every intensity below
its damage threshold, which across a national portfolio is a large fictitious
loss.

**Intensity bins** discretise the ground motion. Their range has to cover what
the hazard actually produces, and the failure is asymmetric: motion above the
top bin has nowhere to go, and clipping it removes precisely the events that
drive the loss. So the top is set well above what a footprint is expected to
carry rather than at it.

Both are drafts. The counts below are chosen to be fine enough that the
reconstruction check in ``vulnerability`` passes comfortably on the GEM
functions, and they have not been tuned against a real footprint -- there is
no footprint yet. When one exists, the intensity range should be re-derived
from it rather than left at these limits.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .bins import DamageBinSet, IntensityBinSet, log_bins, oasis_damage_bins

#: Bumped with any change to either dictionary. A loss computed against one set
#: of bins is not comparable with a loss computed against another, so the bin
#: version travels with the vulnerability set that used it.
PILOT_BIN_VERSION = "0.1.0-draft"

#: Interior damage bins between the two point bins, giving 22 in total.
#: Twenty divides the unit interval at a resolution finer than the differences
#: between GEM's own damage states, so the discretisation is not the limiting
#: approximation.
DAMAGE_BIN_INTERIOR = 20

#: Intensity bins per measure, logarithmically spaced. Ground motion spans
#: orders of magnitude and vulnerability functions are steepest at the low end,
#: so equal ratios carry more information than equal differences.
INTENSITY_BIN_COUNT = 40

#: Lowest and highest ground motion each measure is binned over, in g.
#:
#: The floor is below any motion that causes reportable damage; the ceiling is
#: above the largest value expected in either pilot country. Short-period
#: measures are given a higher ceiling than long-period ones because that is
#: how a response spectrum is shaped -- a near-field record can exceed 2 g at
#: 0.3 s and will not approach it at 1.0 s.
INTENSITY_RANGE: Mapping[str, tuple[str, str]] = {
    "PGA": ("0.005", "4.0"),
    "SA(0.3)": ("0.005", "6.0"),
    "SA(0.6)": ("0.005", "4.0"),
    "SA(1.0)": ("0.005", "3.0"),
}

#: The measures GEM's pilot-country functions demand. Not a choice: it is what
#: reading the published models produced, and the SA-first sequencing in
#: section 16 does not remove PGA from the functions that were built for it.
PILOT_IMTS: tuple[str, ...] = ("PGA", "SA(0.3)", "SA(0.6)", "SA(1.0)")


def damage_bins() -> DamageBinSet:
    """The pilot damage-bin dictionary: a point at 0, a spread, a point at 1."""
    return DamageBinSet(
        version=PILOT_BIN_VERSION, bins=oasis_damage_bins(DAMAGE_BIN_INTERIOR)
    )


def intensity_bins(imts: Sequence[str] = PILOT_IMTS) -> dict[str, IntensityBinSet]:
    """One intensity-bin dictionary per measure.

    Separate dictionaries rather than one shared set, because the measures do
    not share a range and a single set wide enough for all of them would waste
    most of its resolution on values the narrower measures never reach.
    """
    missing = sorted(set(imts) - set(INTENSITY_RANGE))
    if missing:
        raise KeyError(
            f"No pilot intensity range is defined for {', '.join(missing)}. Add one "
            "rather than reusing another measure's: the ranges differ because the "
            "spectra do."
        )
    return {
        imt: IntensityBinSet(
            imt=imt,
            version=PILOT_BIN_VERSION,
            bins=log_bins(*INTENSITY_RANGE[imt], INTENSITY_BIN_COUNT),
        )
        for imt in imts
    }
