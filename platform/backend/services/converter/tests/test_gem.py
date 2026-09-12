"""Reading GEM vulnerability files, and refusing files that are not them.

The fixtures are small NRML documents written here rather than copies of GEM's,
for the same reason the extract fixtures are synthetic: what the tests need is
the shapes, and the published files are large. The real files are read by the
integration tests, which are marked and skipped unless the model directory is
present.
"""

from __future__ import annotations

import hashlib
import io

import pytest

from cass_converter.gem import (
    CATEGORY_BY_DECLARED,
    GemError,
    LossCategory,
    OccupancyClass,
    parse_taxonomy,
    read_country,
    read_model,
)


def document(functions: str, *, category: str = "structural") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nrml xmlns="http://openquake.org/xmlns/nrml/0.5">
<vulnerabilityModel id="vulnerability_model" assetCategory="buildings" \
lossCategory="{category}">
<description>Test Vulnerability Model</description>
{functions}
</vulnerabilityModel>
</nrml>
""".encode()


def function(
    identifier: str = "CR/LFINF/CDL+ERM/H:2/COM",
    *,
    imt: str = "PGA",
    imls: str = "0.05 0.1 0.2 0.4",
    means: str = "0.0001 0.01 0.2 0.8",
    covs: str = "3.0 1.5 0.8 0.3",
    dist: str = "BT",
) -> str:
    return (
        f'<vulnerabilityFunction id="{identifier}" dist="{dist}">\n'
        f'<imls imt="{imt}"> {imls} </imls>\n'
        f"<meanLRs>{means}</meanLRs>\n"
        f"<covLRs>{covs}</covLRs>\n"
        f"</vulnerabilityFunction>"
    )


@pytest.fixture()
def model():
    return read_model(
        io.BytesIO(document(function())),
        country_code="id",
        loss_category=LossCategory.STRUCTURAL,
    )


# -- the taxonomy ------------------------------------------------------------------

def test_a_taxonomy_string_splits_into_its_segments():
    taxonomy = parse_taxonomy("CR/LDUAL/CDL+ERM/H:7/COM")
    assert taxonomy.material == "CR"
    assert taxonomy.lateral_system == "LDUAL"
    assert taxonomy.design == "CDL+ERM"
    assert taxonomy.storeys == 7
    assert taxonomy.occupancy_class is OccupancyClass.COMMERCIAL


def test_a_compound_material_keeps_its_qualifier():
    """MUR+CLBRS is fired-clay-brick masonry, and the qualifier is the point."""
    assert parse_taxonomy("MUR+CLBRS/LWAL/CDN+ERN/H:1/RES").material == "MUR+CLBRS"


def test_a_height_band_is_not_read_as_a_storey_count():
    """The exposure summaries band heights; a band is not a building."""
    assert parse_taxonomy("CR/LDUAL/CDL+ERL/H:6-12/COM").storeys is None


def test_a_string_with_the_wrong_number_of_segments_is_not_a_taxonomy():
    with pytest.raises(GemError, match="not a GEM taxonomy string"):
        parse_taxonomy("CR/LFINF/COM")


def test_an_unknown_occupancy_class_is_absent_rather_than_guessed():
    assert parse_taxonomy("CR/LFINF/CDL+ERM/H:2/AGR").occupancy_class is None


# -- reading -----------------------------------------------------------------------

def test_a_function_is_read_whole(model):
    read = model.by_taxonomy["CR/LFINF/CDL+ERM/H:2/COM"]
    assert read.imt == "PGA"
    assert read.intensities == (0.05, 0.1, 0.2, 0.4)
    assert read.mean_loss_ratios == (0.0001, 0.01, 0.2, 0.8)
    assert read.coefficients_of_variation == (3.0, 1.5, 0.8, 0.3)
    assert read.loss_category is LossCategory.STRUCTURAL


def test_the_country_code_is_normalised(model):
    assert model.country_code == "ID"


def test_the_checksum_is_computed_over_the_bytes_read():
    """Not looked up. It is what makes "this build used that file" checkable."""
    payload = document(function())
    model = read_model(io.BytesIO(payload), country_code="ID")
    assert model.checksum == hashlib.sha256(payload).hexdigest()


def test_the_intensity_measures_a_file_spans_are_reported():
    """Usually more than one, which is the multi-IMT problem stated in data."""
    payload = document(
        function("CR/LFINF/CDL+ERM/H:2/COM", imt="PGA")
        + function("CR/LDUAL/CDL+ERM/H:7/COM", imt="SA(1.0)")
    )
    model = read_model(io.BytesIO(payload), country_code="ID")
    assert model.intensity_measures == ("PGA", "SA(1.0)")


def test_taxonomies_can_be_narrowed_to_one_occupancy_class():
    payload = document(
        function("CR/LFINF/CDL+ERM/H:2/COM")
        + function("MUR+CLBRS/LWAL/CDN+ERN/H:1/RES")
    )
    model = read_model(io.BytesIO(payload), country_code="ID")
    residential = model.taxonomies(OccupancyClass.RESIDENTIAL)
    assert [item.text for item in residential] == ["MUR+CLBRS/LWAL/CDN+ERN/H:1/RES"]


def test_each_point_pairs_its_intensity_with_its_mean_and_cov(model):
    read = model.by_taxonomy["CR/LFINF/CDL+ERM/H:2/COM"]
    assert list(read.points())[1] == (0.1, 0.01, 1.5)


# -- what the reader refuses --------------------------------------------------------

def test_something_that_is_not_xml_is_refused():
    with pytest.raises(GemError, match="not well-formed XML"):
        read_model(io.BytesIO(b"taxonomy,mean\nCR,0.1\n"), country_code="ID")


def test_a_document_in_another_namespace_is_refused():
    payload = b'<?xml version="1.0"?><nrml><vulnerabilityModel/></nrml>'
    with pytest.raises(GemError, match="not an NRML 0.5 document"):
        read_model(io.BytesIO(payload), country_code="ID")


def test_a_distribution_the_discretiser_cannot_integrate_is_refused():
    """Reading a lognormal as beta would produce plausible and wrong tails."""
    payload = document(function(dist="LN"))
    with pytest.raises(GemError, match="supports 'BT'"):
        read_model(io.BytesIO(payload), country_code="ID")


def test_parallel_lists_of_different_lengths_are_refused():
    """A short list would silently truncate the part where the losses are."""
    payload = document(function(means="0.0001 0.01 0.2"))
    with pytest.raises(GemError, match="parallel lists"):
        read_model(io.BytesIO(payload), country_code="ID")


def test_intensity_levels_that_do_not_increase_are_refused():
    payload = document(function(imls="0.05 0.2 0.1 0.4"))
    with pytest.raises(GemError, match="do not\\s+strictly increase"):
        read_model(io.BytesIO(payload), country_code="ID")


def test_a_mean_loss_ratio_above_one_is_refused():
    payload = document(function(means="0.0001 0.01 0.2 1.4"))
    with pytest.raises(GemError, match="outside \\[0, 1\\]"):
        read_model(io.BytesIO(payload), country_code="ID")


def test_a_negative_coefficient_of_variation_is_refused():
    payload = document(function(covs="3.0 1.5 0.8 -0.3"))
    with pytest.raises(GemError, match="negative coefficient"):
        read_model(io.BytesIO(payload), country_code="ID")


def test_intensity_levels_with_no_intensity_measure_are_refused():
    payload = document(function(imt=""))
    with pytest.raises(GemError, match="no\\s+intensity measure type"):
        read_model(io.BytesIO(payload), country_code="ID")


def test_a_repeated_taxonomy_is_refused():
    """Which one applied would depend on the order the file was written in."""
    payload = document(function() + function())
    with pytest.raises(GemError, match="more than one function for"):
        read_model(io.BytesIO(payload), country_code="ID")


def test_a_file_with_no_functions_is_refused():
    with pytest.raises(GemError, match="no vulnerability functions"):
        read_model(io.BytesIO(document("")), country_code="ID")


def test_a_function_with_a_single_intensity_level_is_refused():
    payload = document(function(imls="0.05", means="0.1", covs="1.0"))
    with pytest.raises(GemError, match="fewer than two intensity levels"):
        read_model(io.BytesIO(payload), country_code="ID")


# -- loss categories -----------------------------------------------------------------

def test_the_declared_category_is_used_when_none_is_asked_for():
    model = read_model(io.BytesIO(document(function(), category="contents")),
                       country_code="ID")
    assert model.loss_category is LossCategory.CONTENTS


def test_reading_a_contents_file_as_structural_is_refused():
    """It would attach the wrong damage relationship to a building."""
    payload = document(function(), category="contents")
    with pytest.raises(GemError, match="declares loss category contents"):
        read_model(io.BytesIO(payload), country_code="ID",
                   loss_category=LossCategory.STRUCTURAL)


def test_gems_own_spelling_of_the_fatalities_category_is_accepted():
    """The file is named fatalities and declares occupants. Both are GEM's."""
    assert CATEGORY_BY_DECLARED["occupants"] is LossCategory.FATALITIES
    model = read_model(
        io.BytesIO(document(function(), category="occupants")),
        country_code="ID",
        loss_category=LossCategory.FATALITIES,
    )
    assert model.loss_category is LossCategory.FATALITIES


def test_fatalities_is_marked_as_not_a_monetary_ratio():
    """It shares the format and the taxonomy and is a ratio of people.

    Nothing may multiply it by a TIV, so the distinction is on the category
    rather than left to whoever wires up the coverage types.
    """
    assert LossCategory.FATALITIES.is_monetary is False
    assert all(
        item.is_monetary
        for item in LossCategory
        if item is not LossCategory.FATALITIES
    )


def test_an_unrecognised_category_is_refused():
    payload = document(function(), category="reputational")
    with pytest.raises(GemError, match="unrecognised loss category"):
        read_model(io.BytesIO(payload), country_code="ID")


# -- reading a country directory -------------------------------------------------------

def test_a_country_read_needs_every_loss_category(tmp_path):
    """A gap would appear only as unexplained zero loss on one coverage type."""
    (tmp_path / "vulnerability_structural.xml").write_bytes(document(function()))
    with pytest.raises(GemError, match="missing vulnerability_nonstructural.xml"):
        read_country(tmp_path, country_code="ID")


def test_a_country_read_returns_every_category(tmp_path):
    for category in LossCategory:
        (tmp_path / f"vulnerability_{category}.xml").write_bytes(
            document(function(), category=category.declared)
        )
    models = read_country(tmp_path, country_code="NP")
    assert set(models) == set(LossCategory)
    assert models[LossCategory.CONTENTS].country_code == "NP"


def test_a_directory_that_is_not_one_is_refused(tmp_path):
    path = tmp_path / "not-a-directory.xml"
    path.write_bytes(document(function()))
    with pytest.raises(GemError, match="not a directory"):
        read_country(path, country_code="ID")


def test_only_the_categories_asked_for_are_read(tmp_path):
    (tmp_path / "vulnerability_structural.xml").write_bytes(document(function()))
    models = read_country(
        tmp_path, country_code="ID", categories=[LossCategory.STRUCTURAL]
    )
    assert set(models) == {LossCategory.STRUCTURAL}
