"""Fixtures for control-plane tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import PlatformRole, User
from apps.common.storage import reset_store_cache
from apps.projects.models import Project, ProjectMembership, ProjectRole

FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "piwind"


@pytest.fixture(autouse=True)
def isolated_artifact_store(settings, tmp_path):
    """Point the artifact store at a per-test directory.

    Tests must not share an artifact root, and none of them should touch the
    development one.
    """
    settings.CASS_ARTIFACT_BACKEND = "filesystem"
    settings.CASS_ARTIFACT_ROOT = str(tmp_path / "artifacts")
    reset_store_cache()
    yield
    reset_store_cache()


def make_user(username: str, role: str = PlatformRole.ANALYST, **extra) -> User:
    return User.objects.create_user(
        username=username,
        password="correct-horse-battery",
        platform_role=role,
        **extra,
    )


@pytest.fixture()
def analyst(db) -> User:
    return make_user("analyst", PlatformRole.ANALYST, first_name="Ada", last_name="Analyst")


@pytest.fixture()
def modeller(db) -> User:
    return make_user("modeller", PlatformRole.MODELLER)


@pytest.fixture()
def reviewer(db) -> User:
    return make_user("reviewer", PlatformRole.REVIEWER)


@pytest.fixture()
def outsider(db) -> User:
    return make_user("outsider", PlatformRole.ANALYST)


@pytest.fixture()
def admin(db) -> User:
    return make_user("platform-admin", PlatformRole.ADMIN)


@pytest.fixture()
def project(db, analyst) -> Project:
    project = Project.objects.create(
        name="Indonesia facultative 2026",
        reference="idn-fac-2026",
        purpose="Pilot earthquake portfolio analysis.",
        created_by=analyst,
    )
    ProjectMembership.objects.create(project=project, user=analyst, role=ProjectRole.OWNER)
    return project


@pytest.fixture()
def client_for():
    """Return a factory that yields an authenticated API client."""

    def _client(user) -> APIClient:
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    return _client


@pytest.fixture()
def api(client_for, analyst) -> APIClient:
    return client_for(analyst)


@pytest.fixture(scope="session")
def piwind_root() -> Path:
    if not FIXTURE_ROOT.is_dir():
        pytest.skip(f"PiWind fixtures are not present at {FIXTURE_ROOT}")
    return FIXTURE_ROOT


@pytest.fixture()
def piwind_location_csv(piwind_root) -> bytes:
    return (piwind_root / "SourceLocOEDPiWind10.csv").read_bytes()


@pytest.fixture()
def piwind_account_csv(piwind_root) -> bytes:
    return (piwind_root / "SourceAccOEDPiWind.csv").read_bytes()


@pytest.fixture()
def earthquake_location_csv() -> bytes:
    """A small Indonesian earthquake portfolio that passes validation."""
    header = (
        "PortNumber,AccNumber,LocNumber,BuildingID,CountryCode,Latitude,Longitude,"
        "OccupancyCode,ConstructionCode,LocPerilsCovered,BuildingTIV,ContentsTIV,LocCurrency"
    )
    rows = [
        "1,ACC-1,LOC-1,1,ID,-6.2088,106.8456,1100,5000,QEQ,4500000,900000,IDR",
        "1,ACC-1,LOC-2,1,ID,-6.9175,107.6191,1200,5000,QEQ,2750000,400000,IDR",
        "1,ACC-1,LOC-3,1,ID,-7.2575,112.7521,1050,3000,QEQ,1200000,150000,IDR",
    ]
    return ("\n".join([header, *rows]) + "\n").encode("utf-8")


API = "/api/v1"
