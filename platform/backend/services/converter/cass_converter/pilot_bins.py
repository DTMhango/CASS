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

The damage bins remain a draft. The intensity ranges are no longer: they were
re-derived from an event-based run of the published PuSGeN 2024 Indonesia model
after the first one clipped, and the derivation is recorded against
``INTENSITY_RANGE`` below.
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
#:
#: Raised from 40 when the ranges below were widened, so widening the top did
#: not coarsen the resolution where the functions are steep.
INTENSITY_BIN_COUNT = 50

#: Lowest and highest ground motion each measure is binned over, in g.
#:
#: The floor is below any motion that causes reportable damage. The ceiling is
#: the asymmetric one: motion above the top bin has nowhere to go, and dropping
#: it removes precisely the events that drive the loss.
#:
#: These are now derived from a footprint rather than guessed. An event-based
#: run of the PuSGeN 2024 Indonesia model over the Jakarta-Bandung cells --
#: 27,413 events across 1,000 years, 858 cells, subduction and crustal sources
#: -- produced maxima of 2.30 g PGA, 6.34 g at 0.3 s, 4.61 g at 0.6 s and
#: 4.05 g at 1.0 s. Three of those four exceeded the ranges that stood before,
#: which were set against a prototype crustal source model that could not
#: produce a megathrust.
#:
#: Each ceiling is set near double the observed maximum. The observation is one
#: realisation of one model over one region, so it is a floor on what is
#: needed rather than a bound on it, and doubling is what keeps a larger event
#: set from clipping. The clipping check reports any that still do.
#:
#: The short-period measures keep the higher ceilings because that is the shape
#: of a response spectrum -- a near-field record reaches further above 1 g at
#: 0.3 s than at 1.0 s.
INTENSITY_RANGE: Mapping[str, tuple[str, str]] = {
    "PGA": ("0.005", "6.0"),
    "SA(0.3)": ("0.005", "13.0"),
    "SA(0.6)": ("0.005", "9.0"),
    "SA(1.0)": ("0.005", "8.0"),
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
