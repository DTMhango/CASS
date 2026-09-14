"""Reading Oasis ORD results.

These hold the one property that makes the reader safe to build a result set
on: it never substitutes. A package carries several average losses and several
exceedance curves, each a different number, and a reader that quietly returned
the nearest thing it had would produce a loss labelled as something it is not.

The last test reads the real PiWind output package when it is on disk. Fixtures
shaped by hand agree with whatever the person writing them believed the format
to be, which is exactly the belief worth checking.
"""

from __future__ import annotations

import io
import pathlib
import tarfile
import zipfile
from decimal import Decimal

import pytest

from cass_oed import ord as ord_results

PIWIND_OUTPUT = (
    pathlib.Path(__file__).resolve().parents[5]
    / ".piwind_e2e_main"
    / "runs"
    / "losses-20260910172736"
    / "output"
)

EPT = """SummaryId,EPCalc,EPType,ReturnPeriod,Loss
1,4,3,250.000000,9000000.000000
1,4,3,100.000000,5000000.000000
1,4,3,10.000000,633300.000000
1,2,3,250.000000,1.000000
1,4,1,250.000000,2.000000
"""

PALT = """SummaryId,SampleType,MeanLoss,SDLoss
1,1,999999.00,111111.00
1,2,232122.90,623738.00
"""


def package(members: dict[str, str] | None = None, *, as_zip: bool = False) -> bytes:
    members = members if members is not None else {
        "output/gul_S1_ept.csv": EPT,
        "output/gul_S1_palt.csv": PALT,
    }
    buffer = io.BytesIO()
    if as_zip:
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, text in members.items():
                archive.writestr(name, text)
        return buffer.getvalue()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, text in members.items():
            payload = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


# -- a level grouped by location ----------------------------------------------

SUMMARY_INFO = """summary_id,AccNumber,LocNumber,tiv
1,ACC-1,LOC-1,5400000
2,ACC-1,LOC-2,3150000
"""

LOCATION_PALT = """SummaryId,SampleType,MeanLoss,SDLoss
1,1,900.00,10.00
1,2,1200.50,30.00
2,1,50.00,5.00
2,2,4800.25,90.00
"""


def located(**overrides: str) -> ord_results.OrdPackage:
    members = {
        "output/gul_S1_palt.csv": PALT,
        "output/gul_S2_summary-info.csv": SUMMARY_INFO,
        "output/gul_S2_palt.csv": LOCATION_PALT,
        **overrides,
    }
    return ord_results.open_package(package(members))


def test_each_location_loss_is_named_by_the_fields_it_was_grouped_on():
    losses = ord_results.location_losses(
        located(), perspective="ground_up", summary_level=2, fields=("AccNumber", "LocNumber")
    )

    assert [item.fields["LocNumber"] for item in losses] == ["LOC-2", "LOC-1"]
    assert losses[0].average_annual_loss == Decimal("4800.25")
    assert losses[0].tiv == Decimal("3150000")
    # The sample basis the headline number is quoted on, not the analytical one.
    assert losses[1].average_annual_loss == Decimal("1200.50")


def test_a_level_without_summary_info_cannot_name_its_ids():
    packaged = ord_results.open_package(
        package({"output/gul_S2_palt.csv": LOCATION_PALT})
    )
    with pytest.raises(ord_results.OrdError, match="summary-info"):
        ord_results.location_losses(
            packaged, perspective="ground_up", summary_level=2, fields=("LocNumber",)
        )


def test_a_level_grouped_by_something_else_is_refused():
    """The portfolio level's ids name nothing, and must not be read as locations."""
    with pytest.raises(ord_results.OrdError, match="not grouped by LocNumber"):
        ord_results.location_losses(
            located(**{"output/gul_S2_summary-info.csv": "summary_id,_not_set_,tiv\n1,,5\n"}),
            perspective="ground_up",
            summary_level=2,
            fields=("LocNumber",),
        )


def test_a_loss_whose_id_summary_info_does_not_know_is_refused():
    with pytest.raises(ord_results.OrdError, match="cannot be placed"):
        ord_results.location_losses(
            located(**{"output/gul_S2_summary-info.csv": "summary_id,AccNumber,LocNumber,tiv\n1,A,L,1\n"}),
            perspective="ground_up",
            summary_level=2,
            fields=("AccNumber", "LocNumber"),
        )


# -- containers -------------------------------------------------------------

def test_a_gzipped_tar_is_read():
    assert ord_results.open_package(package()).perspectives() == ("ground_up",)


def test_a_zip_is_read_too():
    """Oasis serves a tar; a manual re-upload is nearly always a zip."""
    assert ord_results.open_package(package(as_zip=True)).perspectives() == (
        "ground_up",
    )


def test_something_that_is_neither_says_so():
    with pytest.raises(ord_results.OrdError, match="neither a gzipped tar nor a zip"):
        ord_results.open_package(b"just some bytes")


def test_an_entry_that_would_escape_the_directory_is_ignored():
    """The package comes from an engine, but it arrives over the network."""
    found = ord_results.open_package(
        package({"../evil.csv": "x", "output/gul_S1_palt.csv": PALT})
    )
    assert not any(".." in name for name in found.names)


# -- selecting the number ---------------------------------------------------

def test_the_average_loss_is_read_on_the_requested_sample_type():
    metrics = ord_results.metrics_for(
        ord_results.open_package(package()), perspective="ground_up"
    )
    # Sample, not analytical: the table holds both and they differ.
    assert metrics.average_annual_loss == Decimal("232122.90")
    assert metrics.standard_deviation == Decimal("623738.00")


def test_the_analytical_mean_can_be_asked_for_instead():
    metrics = ord_results.metrics_for(
        ord_results.open_package(package()), perspective="ground_up", sample_type=1
    )
    assert metrics.average_annual_loss == Decimal("999999.00")
    assert metrics.basis["average_loss"] == "analytical"


def test_the_curve_takes_only_the_requested_calculation_and_type():
    """An EPT holds several in one file; mixing them draws no real curve."""
    metrics = ord_results.metrics_for(
        ord_results.open_package(package()), perspective="ground_up"
    )
    assert metrics.return_period_losses == {
        "10": "633300.000000",
        "100": "5000000.000000",
        "250": "9000000.000000",
    }


def test_the_basis_is_recorded_in_words_rather_than_codes():
    """A bare 4 and 3 on a result is a number nobody downstream can interpret."""
    metrics = ord_results.metrics_for(
        ord_results.open_package(package()), perspective="ground_up"
    )
    assert metrics.basis["ep_calculation"] == "mean sample"
    assert metrics.basis["ep_type"] == "AEP"


def test_an_oep_curve_is_a_different_curve():
    metrics = ord_results.metrics_for(
        ord_results.open_package(package()), perspective="ground_up", ep_type=1
    )
    assert metrics.return_period_losses == {"250": "2.000000"}
    assert metrics.basis["ep_type"] == "OEP"


def test_return_periods_are_whole_years_where_they_are_whole():
    metrics = ord_results.metrics_for(
        ord_results.open_package(package()), perspective="ground_up"
    )
    assert "100" in metrics.return_period_losses
    assert "100.000000" not in metrics.return_period_losses


# -- refusing rather than substituting --------------------------------------

def test_a_perspective_the_package_lacks_is_refused():
    """Not the nearest thing it has: that would be a mislabelled loss."""
    with pytest.raises(ord_results.OrdError, match="no insured results"):
        ord_results.metrics_for(
            ord_results.open_package(package()), perspective="insured"
        )


def test_a_perspective_oasis_does_not_report_is_refused():
    with pytest.raises(ord_results.OrdError, match="not a perspective"):
        ord_results.metrics_for(
            ord_results.open_package(package()), perspective="net_of_tax"
        )


def test_a_missing_average_loss_table_is_nothing_rather_than_zero():
    """Zero is a loss. Absent is not, and the two must not be confused."""
    metrics = ord_results.metrics_for(
        ord_results.open_package(package({"output/gul_S1_ept.csv": EPT})),
        perspective="ground_up",
    )
    assert metrics.average_annual_loss is None
    assert metrics.return_period_losses


def test_an_empty_package_says_so():
    with pytest.raises(ord_results.OrdError, match="empty"):
        ord_results.open_package(package({}))


# -- against the real thing -------------------------------------------------

@pytest.mark.skipif(
    not PIWIND_OUTPUT.is_dir(), reason="the PiWind output package is not on disk"
)
def test_the_real_piwind_output_reads_as_expected():
    """A hand-written fixture only agrees with what its author believed."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(PIWIND_OUTPUT.glob("*.csv")):
            archive.add(path, arcname=f"output/{path.name}")

    found = ord_results.open_package(buffer.getvalue())
    assert set(found.perspectives()) == {"ground_up", "insured", "reinsurance"}

    metrics = ord_results.metrics_for(found, perspective="ground_up")
    assert metrics.average_annual_loss == Decimal("232122.906250")
    assert metrics.standard_deviation == Decimal("623738.000000")
    # A real curve, in ascending return period, with no duplicated keys.
    periods = [int(item) for item in metrics.return_period_losses]
    assert periods == sorted(periods)
    assert len(periods) == len(set(periods))
