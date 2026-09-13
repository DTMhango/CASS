"""The control plane's route to an engine.

Section 4 forbids the React application from calling an engine API directly and
puts every engine behind a versioned adapter, which makes the factory in
``apps.common.engines`` the only place deployment configuration turns into a
live client. These tests hold that boundary in place and cover the status
endpoint an operator reads before submitting a run.
"""

from __future__ import annotations

import pytest

from apps.common.engines import (
    describe_engines,
    oasis_adapter,
    oasis_model_triple,
    openquake_adapter,
)

from .conftest import API


class StubResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}
        self.text = str(body)

    def json(self):
        return self._body


class StubSession:
    """Answers the handful of calls ``describe`` and a sign-in make."""

    def __init__(self, version="2.5.7", *, reachable=True):
        self.version = version
        self.reachable = reachable
        self.urls = []
        self.calls = []

    def request(self, method, url, **kwargs):
        self.urls.append(url)
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.reachable:
            raise OSError("connection refused")
        if url.endswith("/access_token/"):
            return StubResponse(200, {"access_token": "tkn-a", "refresh_token": "tkn-r"})
        return StubResponse(
            200, {"version": self.version, "config": {"API_AUTH_TYPE": "simple"}}
        )


class StubTextResponse:
    """OpenQuake answers ``engine_version`` with a bare string, not a document."""

    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text

    def json(self):
        raise ValueError("not JSON")


class StubOpenQuakeSession:
    def __init__(self, version="3.23.0", *, reachable=True):
        self.version = version
        self.reachable = reachable
        self.urls = []

    def request(self, method, url, **kwargs):
        self.urls.append(url)
        if not self.reachable:
            raise OSError("connection refused")
        if url.endswith("engine_version"):
            return StubTextResponse(200, self.version)
        return StubTextResponse(404, "not found")


@pytest.fixture()
def openquake_at(settings, monkeypatch):
    """Point the factory at a stubbed OpenQuake and return the stub."""

    def _configure(version="3.23.0", *, reachable=True):
        settings.CASS_OPENQUAKE_URL = "http://openquake:8800"
        stub = StubOpenQuakeSession(version, reachable=reachable)
        monkeypatch.setattr(
            "apps.common.engines.openquake_adapter",
            lambda **kwargs: openquake_adapter(
                session=stub, retries=1, retry_delay=0, sleep=lambda _: None, **kwargs
            ),
        )
        return stub

    return _configure


@pytest.fixture()
def oasis_at(settings, monkeypatch):
    """Point the factory at a stubbed Oasis and return the stub."""

    def _configure(version="2.5.7", *, reachable=True):
        settings.CASS_OASIS_API_URL = "http://oasis-api:8000"
        stub = StubSession(version, reachable=reachable)
        monkeypatch.setattr(
            "apps.common.engines.oasis_adapter",
            lambda **kwargs: oasis_adapter(
                session=stub, retries=1, retry_delay=0, sleep=lambda _: None, **kwargs
            ),
        )
        return stub

    return _configure


# -- the factory ------------------------------------------------------------

def test_the_adapter_is_built_from_the_deployment_configuration(settings):
    settings.CASS_OASIS_API_URL = "http://oasis-api:8000"
    settings.CASS_OASIS_USERNAME = "cass-service"
    settings.CASS_OASIS_PASSWORD = "secret"

    stub = StubSession()
    engine = oasis_adapter(session=stub)
    engine.authenticate()

    sign_in = next(call for call in stub.calls if call["url"].endswith("/access_token/"))
    assert sign_in["url"] == "http://oasis-api:8000/access_token/"
    assert sign_in["json"] == {"username": "cass-service", "password": "secret"}


def test_the_model_triple_comes_from_configuration_rather_than_a_stored_id(settings):
    """A rebuilt Oasis server must not be able to hand a run a different model."""
    settings.CASS_OASIS_MODEL_SUPPLIER_ID = "KRE"
    settings.CASS_OASIS_MODEL_ID = "EQ"
    settings.CASS_OASIS_MODEL_VERSION_ID = 3
    assert oasis_model_triple() == ("KRE", "EQ", "3")


def test_a_reachable_engine_on_a_tested_version_is_reported_compatible(oasis_at):
    oasis_at("2.5.7")
    oasis = describe_engines()["oasis"]
    assert oasis["reachable"] is True
    assert oasis["compatible"] is True


def test_a_reachable_engine_on_an_untested_version_is_reported_incompatible(oasis_at):
    """Section 18: an operator must see this before submitting, not after."""
    oasis_at("2.6.0")
    oasis = describe_engines()["oasis"]
    assert oasis["reachable"] is True
    assert oasis["compatible"] is False


def test_an_unreachable_engine_is_reported_rather_than_raising(oasis_at):
    oasis_at(reachable=False)
    oasis = describe_engines()["oasis"]
    assert oasis["reachable"] is False
    assert "did not respond" in oasis["error"]


def test_every_engine_the_deployment_runs_is_named(oasis_at, openquake_at):
    """A missing row reads as nothing to see, which is the wrong thing to say."""
    oasis_at()
    openquake_at()
    described = describe_engines()
    assert set(described) == {"oasis", "openquake"}
    # The configured endpoint travels with each answer, so a support bundle
    # records what was probed rather than only what replied.
    assert described["openquake"]["url"] == "http://openquake:8800"


def test_openquake_reports_its_version_and_compatibility(oasis_at, openquake_at):
    oasis_at()
    openquake_at("3.23.0")
    openquake = describe_engines()["openquake"]
    assert openquake["reachable"] is True
    assert openquake["version"] == "3.23.0"
    assert openquake["compatible"] is True


def test_an_untested_openquake_is_reported_before_a_run_is_submitted(
    oasis_at, openquake_at
):
    """Section 18: a result from an untested engine cannot be defended."""
    oasis_at()
    openquake_at("3.19.0")
    openquake = describe_engines()["openquake"]
    assert openquake["reachable"] is True
    assert openquake["compatible"] is False


def test_an_unreachable_openquake_does_not_hide_a_healthy_oasis(
    oasis_at, openquake_at
):
    """One engine being down must not cost the operator the other's status."""
    oasis_at()
    openquake_at(reachable=False)
    described = describe_engines()
    assert described["oasis"]["reachable"] is True
    assert described["openquake"]["reachable"] is False


# -- the endpoint -----------------------------------------------------------

def test_engine_status_requires_a_signed_in_user(db, oasis_at):
    from rest_framework.test import APIClient

    oasis_at()
    assert APIClient().get(f"{API}/engines/").status_code in (401, 403)


def test_engine_status_reports_each_engine(api, oasis_at):
    oasis_at("2.5.7")
    response = api.get(f"{API}/engines/")
    assert response.status_code == 200
    engines = response.json()["engines"]
    assert engines["oasis"]["compatible"] is True
    assert set(engines) == {"oasis", "openquake"}


def test_engine_status_still_answers_when_an_engine_is_down(api, oasis_at):
    """A screen must be able to say the engine is down, not fail to load."""
    oasis_at(reachable=False)
    response = api.get(f"{API}/engines/")
    assert response.status_code == 200
    assert response.json()["engines"]["oasis"]["reachable"] is False


def test_platform_metadata_does_not_call_an_engine(api, oasis_at):
    """Metadata is a cheap read; a page load must not wait on an Oasis server."""
    stub = oasis_at()
    assert api.get(f"{API}/platform/").status_code == 200
    assert stub.urls == []
