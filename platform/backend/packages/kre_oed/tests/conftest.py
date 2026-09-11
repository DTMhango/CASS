"""Shared fixtures, including the official PiWind exposure baseline.

Build plan section 12 makes PiWind the end-to-end regression baseline, so the
validator is exercised against the real upstream files rather than against
hand-written rows only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kre_oed.reader import read_bytes, read_path
from kre_oed.schema import FileKind
from kre_oed.validation import PortfolioFiles

FIXTURE_ROOT = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "piwind"


@pytest.fixture(scope="session")
def piwind_root() -> Path:
    if not FIXTURE_ROOT.is_dir():
        pytest.skip(f"PiWind fixtures are not present at {FIXTURE_ROOT}")
    return FIXTURE_ROOT


@pytest.fixture()
def piwind_files(piwind_root: Path) -> PortfolioFiles:
    return PortfolioFiles(
        location=read_path(FileKind.LOCATION, piwind_root / "SourceLocOEDPiWind10.csv"),
        account=read_path(FileKind.ACCOUNT, piwind_root / "SourceAccOEDPiWind.csv"),
        reins_info=read_path(FileKind.REINS_INFO, piwind_root / "SourceReinsInfoOEDPiWind.csv"),
        reins_scope=read_path(FileKind.REINS_SCOPE, piwind_root / "SourceReinsScopeOEDPiWind.csv"),
    )


def location_csv(*rows: str, header: str | None = None) -> bytes:
    """Build a small location file for a focused test."""
    default_header = (
        "PortNumber,AccNumber,LocNumber,BuildingID,CountryCode,Latitude,Longitude,"
        "OccupancyCode,ConstructionCode,LocPerilsCovered,BuildingTIV,ContentsTIV,LocCurrency"
    )
    body = "\n".join([header or default_header, *rows])
    return (body + "\n").encode("utf-8")


def read_location(*rows: str, header: str | None = None):
    return read_bytes(FileKind.LOCATION, location_csv(*rows, header=header))


@pytest.fixture()
def make_location():
    """Factory returning a PortfolioFiles holding only the rows a test needs."""

    def _make(*rows: str, header: str | None = None) -> PortfolioFiles:
        return PortfolioFiles(location=read_location(*rows, header=header))

    return _make
