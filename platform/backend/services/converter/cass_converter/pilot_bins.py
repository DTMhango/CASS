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
PILOT_BIN_VERSION = "0.2.0-draft"

#: Interior damage bins between the two point bins, giving 22 in total.
#: Twenty divides the unit interval at a resolution finer than the differences
#: between GEM's own damage states, so the discretisation is not the limiting
#: approximation.
DAMAGE_BIN_INTERIOR = 20

#: Intensity bins per measure, logarithmically spaced. Ground motion spans
#: orders of magnitude and vulnerability functions are steepest at the low end,
#: so equal ratios carry more information than equal differences.
#:
#: Raised from 40 when the ranges below were first widened, and from 50 when they
#: were widened again, so widening the top has never coarsened the resolution
#: where the functions are steep. Measured over the range from 0.005 g, 55 would
#: have left SA(0.6)'s bins 0.6% wider than before, so 56; with the floor now at
#: 0.05 g, every measure's bins are about a third narrower than they were.
INTENSITY_BIN_COUNT = 56

#: Lowest and highest ground motion each measure is binned over, in g.
#:
#: The floor is where damage begins. Every one of the 73,308 vulnerability
#: functions in GEM v2026.0.0 -- 860 files, every country and loss type -- starts
#: at 0.05 g, and below a function's lowest level both CASS and OpenQuake give no
#: loss. Motion beneath the floor is dropped by the engine before it is stored, so
#: nothing that could produce a loss is lost. It was 0.005 g, a tenth of that, and
#: the difference was most of what a hazard set stored: on the Jakarta-Bandung and
#: Java calculations, only 9% of site-events reached 0.05 g on any measure.
#:
#: The ceiling is the asymmetric one: motion above the top bin has nowhere to go,
#: and dropping it removes precisely the events that drive the loss.
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
#:
#: Widened again on 17 September 2026, by the same rule, when longer catalogues
#: and larger regions reached past those ceilings. Measured on this platform with
#: the same model: over the 4,149 cells of Java, 1,000 years reached 13.47 g at
#: 0.3 s and 9.75 g at 0.6 s; over Jakarta-Bandung, 10,000 years reached 14.05 g
#: at 0.3 s; and the largest PGA seen was 5.69 g, at 1,000 years. The first two
#: clipped the old 13 g and 9 g ceilings, so a national run, or the new default
#: of ten thousand years, would have been refused publication. The strongest
#: value each measure has reached in any run is doubled again, as before: PGA
#: 5.69 to 12, SA(0.3) 14.05 to 28, SA(0.6) 9.75 to 20, SA(1.0) 6.84 to 14.
INTENSITY_RANGE: Mapping[str, tuple[str, str]] = {
    "PGA": ("0.05", "12.0"),
    "SA(0.3)": ("0.05", "28.0"),
    "SA(0.6)": ("0.05", "20.0"),
    "SA(1.0)": ("0.05", "14.0"),
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
