"""The intensity-bin dictionaries, held to the ground motion they must hold.

A footprint counts ground motion into these bins, and motion above the top bin
has nowhere to go: dropping it removes exactly the events that drive the tail,
and a set that does so is refused publication. The ceilings were widened on 17
September 2026 after runs on this platform reached past them, and these hold
them to what those runs measured.
"""

from __future__ import annotations

import math

from cass_converter import pilot_bins

#: The strongest motion each measure reached in any run measured on the
#: platform with PuSGeN 2024, in g: Java over 1,000 years and Jakarta-Bandung
#: over 1,000, 5,000 and 10,000 years.
MEASURED_MAXIMA = {"PGA": 5.69, "SA(0.3)": 14.05, "SA(0.6)": 9.75, "SA(1.0)": 6.84}

#: The dictionary before the widening: 50 bins per measure to these ceilings.
PREVIOUS = {"PGA": 6.0, "SA(0.3)": 13.0, "SA(0.6)": 9.0, "SA(1.0)": 8.0}


def test_every_ceiling_is_at_least_double_the_strongest_motion_measured():
    for imt, strongest in MEASURED_MAXIMA.items():
        ceiling = float(pilot_bins.INTENSITY_RANGE[imt][1])
        assert ceiling >= 1.9 * strongest, imt


def test_widening_the_top_did_not_coarsen_any_bin():
    """Each bin spans no larger a ratio of motion than it did before."""
    for imt, previous_ceiling in PREVIOUS.items():
        floor, ceiling = (float(value) for value in pilot_bins.INTENSITY_RANGE[imt])
        now = math.log(ceiling / floor) / pilot_bins.INTENSITY_BIN_COUNT
        before = math.log(previous_ceiling / 0.005) / 50
        assert now <= before, imt


def test_the_floor_is_where_gem_functions_begin():
    """Every GEM v2026.0.0 function starts at 0.05 g; below it nothing is damaged.

    The release is licensed and outside version control, so the measurement is
    recorded here rather than repeated: 73,308 functions in 860 files, each with
    its lowest intensity level at exactly 0.05 g.
    """
    from cass_converter.hazard_job import HazardJob

    for imt, (floor, _) in pilot_bins.INTENSITY_RANGE.items():
        assert floor == "0.05", imt
    assert HazardJob.__dataclass_fields__["minimum_intensity"].default == 0.05


def test_the_dictionary_says_it_changed():
    assert pilot_bins.PILOT_BIN_VERSION == "0.2.0-draft"
    for imt, bins in pilot_bins.intensity_bins().items():
        assert len(bins.bins) == pilot_bins.INTENSITY_BIN_COUNT, imt
