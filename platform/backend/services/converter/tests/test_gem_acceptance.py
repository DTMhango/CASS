"""The vulnerability builder against the published GEM v2026.0.0 files.

The synthetic tests prove the reader and the discretiser behave. This proves
they behave on the actual pilot-country model, which is the only place the
things that matter show up: four intensity measures in one file, coefficients
of variation above forty, distributions that collapse to a point at both ends
of every curve, and 57 Nepali taxonomies where the prototype routing had four.

The GEM repositories are large and licensed, so they are not vendored into
this one. Point the suite at a clone:

    CASS_GEM_PATH=/path/to/models/gem/v2026.0.0 \\
        pytest -m integration services/converter/tests/test_gem_acceptance.py

The checksums asserted here are the ones in that release's ``MODEL_MANIFEST``,
recomputed from the bytes the reader actually read. That is the check worth
having: it makes "this build used the published Indonesia structural
functions" verifiable rather than asserted.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cass_converter.bins import DamageBinSet, IntensityBinSet, log_bins, oasis_damage_bins
from cass_converter.gem import LossCategory, OccupancyClass, read_country
from cass_converter.policy import ConversionPolicy, EventIdentity, IMTRepresentation
from cass_converter.vulnerability import (
    Placement,
    build_table,
    discretise,
    reconstruction_failures,
    table_report,
    to_csv,
)

pytestmark = pytest.mark.integration

GEM_PATH = os.environ.get("CASS_GEM_PATH", "")

needs_gem = pytest.mark.skipif(
    not (GEM_PATH and Path(GEM_PATH).is_dir()),
    reason="set CASS_GEM_PATH to a GEM v2026.0.0 checkout",
)

COUNTRIES = {
    "ID": ("Southeast_Asia", "Indonesia"),
    "NP": ("South_Asia", "Nepal"),
}

#: From the release manifest, lower-cased. Recomputed, not copied forward.
PUBLISHED_CHECKSUMS = {
    ("ID", LossCategory.STRUCTURAL):
        "00d68afbd382f15b7a61918f1ffb0b0577dfdfa41d5577275f3d6da0ddb14410",
    ("ID", LossCategory.NONSTRUCTURAL):
        "5562e205b382b9dfa14fc58a735eef8280346d18824e33547d0190640f2abf6b",
    ("ID", LossCategory.CONTENTS):
        "47e4a5c8081da3e5a83326ea3d59160e89a81ec04b42be1c7aa9fe0efa471e39",
    ("ID", LossCategory.FATALITIES):
        "4f4b7ad1afb521a93b94536f51bbfb2103f253251b27fda4612e17043318257e",
    ("NP", LossCategory.STRUCTURAL):
        "6a44a6230143be39135a19a91b0f05cafe0d486e7ac5d6e617205243f39295df",
    ("NP", LossCategory.NONSTRUCTURAL):
        "07d9916e3c417f34205b12c496da40f0f1ab3c607c4ac22a9ec0ceb7c75719ee",
    ("NP", LossCategory.CONTENTS):
        "7948349ee09ea960419cb59def4cc6cb9d094b8e7aa4ccdaa0d91c9058862285",
    ("NP", LossCategory.FATALITIES):
        "deb9471503b76ef237dbc6fbb9f7241f045948e0848696d456cbf02280c352c5",
}


def country(code: str):
    region, name = COUNTRIES[code]
    return read_country(
        Path(GEM_PATH) / "global_vulnerability_model" / region / name,
        country_code=code,
    )


@pytest.fixture(scope="module")
def indonesia():
    return country("ID")


@pytest.fixture(scope="module")
def nepal():
    return country("NP")


@pytest.fixture()
def damage():
    """Fifty interior bins, plus a point at each end.

    Chosen against these functions rather than by convention: at fifty bins the
    worst reconstructed mean over both countries and all four loss categories
    is under 1e-07, and the coefficient of variation is within 0.01 wherever
    the mean is large enough to affect a loss.
    """
    return DamageBinSet(version="1.0.0", bins=oasis_damage_bins(50))


@pytest.fixture()
def intensity():
    return {
        imt: IntensityBinSet(
            imt=imt, version="1.0.0", bins=log_bins("0.05", "10.0", 40)
        )
        for imt in ("PGA", "SA(0.3)", "SA(0.6)", "SA(1.0)")
    }


@pytest.fixture()
def policy():
    """A policy that would not be approved. Named so the test cannot be mistaken.

    Section 6 leaves the multi-IMT representation open, so no real conversion
    may run yet. What this suite establishes is that the arithmetic is right
    when it is; it does not establish that the choice has been made.
    """
    return ConversionPolicy(
        event_identity=EventIdentity.RUPTURE_BINNED,
        imt_representation=IMTRepresentation.CORRELATED_CHANNELS,
        approval_reference="NOT-APPROVED-TEST-ONLY",
        imts=("PGA", "SA(0.3)", "SA(0.6)", "SA(1.0)"),
        investigation_time=1.0,
    )


# -- what the published files contain -------------------------------------------------

@needs_gem
@pytest.mark.parametrize("code", ["ID", "NP"])
def test_every_published_file_matches_its_manifest_checksum(code):
    for category, model in country(code).items():
        assert model.checksum == PUBLISHED_CHECKSUMS[(code, category)], (
            f"{code} {category} does not match the release manifest"
        )


@needs_gem
def test_indonesia_publishes_thirty_two_taxonomies_in_every_category(indonesia):
    assert {len(model) for model in indonesia.values()} == {32}


@needs_gem
def test_nepal_publishes_fifty_seven(nepal):
    """Against the four the prototype routing table carried."""
    assert {len(model) for model in nepal.values()} == {57}


@needs_gem
def test_the_occupancy_mix_differs_between_the_pilot_countries(indonesia, nepal):
    """The prototype gave both countries the same functions, and flagged it.

    They are not the same. Nepal's published stock is 45 residential taxonomies
    against 9 commercial; Indonesia's is 12 against 15. A mapping shared
    between them by coincidence rather than decision was the right thing to
    call a placeholder.
    """
    structural = indonesia[LossCategory.STRUCTURAL]
    assert len(structural.taxonomies(OccupancyClass.COMMERCIAL)) == 15
    assert len(structural.taxonomies(OccupancyClass.RESIDENTIAL)) == 12
    assert len(structural.taxonomies(OccupancyClass.INDUSTRIAL)) == 5

    nepali = nepal[LossCategory.STRUCTURAL]
    assert len(nepali.taxonomies(OccupancyClass.COMMERCIAL)) == 9
    assert len(nepali.taxonomies(OccupancyClass.RESIDENTIAL)) == 45
    assert len(nepali.taxonomies(OccupancyClass.INDUSTRIAL)) == 3


@needs_gem
@pytest.mark.parametrize("code", ["ID", "NP"])
def test_one_file_spans_four_intensity_measures(code):
    """The multi-IMT question is not hypothetical: it is in every file.

    GEM picks the spectral period each structure responds at, so a single
    country's structural model demands PGA for its low-rise and SA(1.0) for
    its tall frames. A one-channel conversion has to reinterpret three of the
    four, which is the substitution section 6 refuses to accept as a default.
    """
    for model in country(code).values():
        assert model.intensity_measures == ("PGA", "SA(0.3)", "SA(0.6)", "SA(1.0)")


@needs_gem
def test_every_function_is_beta_distributed(indonesia, nepal):
    for models in (indonesia, nepal):
        for model in models.values():
            assert {item.distribution for item in model} == {"BT"}


# -- the discretisation ----------------------------------------------------------------

@needs_gem
@pytest.mark.parametrize("code", ["ID", "NP"])
def test_the_whole_country_discretises_within_tolerance(code, damage, intensity, policy):
    """The acceptance test the build plan asks for, on the real functions.

    ``build_table`` runs ``check_reconstruction`` itself, so this passing is
    the assertion: every intensity bin of every function in all four loss
    categories reproduces the mean and CoV GEM published.
    """
    for category, model in country(code).items():
        functions = dict(enumerate(model.functions, start=1))
        table = build_table(
            functions,
            intensity_bins=intensity,
            damage_bins=damage,
            policy=policy,
        )
        assert len(table) == len(model)
        report = table_report(table)
        assert report["worst_absolute_mean_error"]["error"] < 1e-06, (
            f"{code} {category}: {report}"
        )


@needs_gem
def test_reading_the_bins_at_their_midpoints_would_not_pass(indonesia, damage, intensity):
    """The choice that this default exists to avoid, measured on real data.

    Every one of these functions is concentrated near zero at the intensities
    an ordinary year produces, so midpoint placement reports damage where GEM
    reports effectively none -- and it fails on the mean, not on some subtlety
    of the tail.
    """
    model = indonesia[LossCategory.STRUCTURAL]
    tables = [
        discretise(
            item,
            vulnerability_id=index,
            intensity_bins=intensity[item.imt],
            damage_bins=damage,
            placement=Placement.MIDPOINT,
        )
        for index, item in enumerate(model.functions, start=1)
    ]
    failures = reconstruction_failures(tables)
    assert len(failures) > 500
    assert any(item.measure == "mean_loss_ratio" for item in failures)


@needs_gem
def test_mean_preserving_placement_is_exact_where_midpoint_is_out_by_a_bin(
    indonesia, damage, intensity
):
    """The worked example from the module docstring, on the published file."""
    model = indonesia[LossCategory.STRUCTURAL]
    function = model.by_taxonomy["CR/LFINF/CDL+ERM/H:6/RES"]

    preserved, midpoint = (
        discretise(
            function,
            vulnerability_id=1,
            intensity_bins=intensity[function.imt],
            damage_bins=damage,
            placement=rule,
        )
        for rule in (Placement.MEAN_PRESERVING, Placement.MIDPOINT)
    )
    assert preserved.worst_mean_error < 1e-07
    assert midpoint.worst_mean_error > 1e-03
    assert midpoint.worst_mean_error > 10_000 * preserved.worst_mean_error


@needs_gem
def test_a_country_table_is_a_plausible_size(indonesia, damage, intensity, policy):
    """A table is checked for being finite as well as correct.

    The probability floor drops the far tail, where a beta contributes an
    unbounded number of bins at 1e-30. Without it a country's structural table
    alone would carry every bin of every intensity.
    """
    model = indonesia[LossCategory.STRUCTURAL]
    table = build_table(
        dict(enumerate(model.functions, start=1)),
        intensity_bins=intensity,
        damage_bins=damage,
        policy=policy,
    )
    payload = to_csv(table)
    assert 10_000 < len(table[0].rows) * len(table) < 5_000_000
    assert payload.startswith(b"vulnerability_id,intensity_bin_id,damage_bin_id,")
    assert max(item.worst_dropped_mass for item in table) < 1e-06


@needs_gem
def test_fatalities_are_read_but_marked_as_not_monetary(indonesia):
    """GEM ships them in the same shape as the loss ratios and they are not one.

    Reading them is right -- a life-safety view is a legitimate output. Letting
    them reach a table Oasis multiplies by a TIV is not, so the category says
    which it is rather than leaving it to whoever wires up the coverage types.
    """
    model = indonesia[LossCategory.FATALITIES]
    assert len(model) == 32
    assert model.loss_category.is_monetary is False
