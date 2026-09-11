"""The adapter compatibility gate.

Section 15 names two risks this addresses: an OpenQuake or Oasis upgrade
breaking integration, and custom engine forks accumulating. The gate is what
turns the first from a silent behaviour change into a refusal an operator can
read.
"""

from __future__ import annotations

import pytest

from cass_adapters.base import (
    EngineAdapter,
    EngineState,
    EngineVersion,
    IncompatibleEngine,
)


class FakeAdapter(EngineAdapter):
    """A stand-in engine, so the gate can be tested without an engine."""

    engine_name = "fake-engine"
    supported_versions = ("2.5.7", "3.23.1")

    def __init__(self, version: str = "2.5.7", *, reachable: bool = True) -> None:
        self._version = version
        self._reachable = reachable

    def version(self) -> EngineVersion:
        if not self._reachable:
            from cass_adapters.base import EngineUnavailable

            raise EngineUnavailable(f"{self.engine_name} did not respond.")
        return EngineVersion(self.engine_name, self._version, "sha256:abc")

    def healthy(self) -> bool:
        return self._reachable


# -- the gate ---------------------------------------------------------------

def test_a_tested_version_is_accepted():
    assert FakeAdapter("2.5.7").check_compatible().version == "2.5.7"


def test_a_patch_release_within_a_tested_series_is_accepted():
    """Patch releases do not move export formats or API shapes."""
    assert FakeAdapter("2.5.9").check_compatible().version == "2.5.9"


@pytest.mark.parametrize("version", ["2.6.0", "3.0.0", "1.9.9", "3.24.0"])
def test_an_untested_minor_or_major_is_refused(version):
    with pytest.raises(IncompatibleEngine) as excinfo:
        FakeAdapter(version).check_compatible()
    assert version in str(excinfo.value)


def test_the_refusal_names_the_tested_versions():
    with pytest.raises(IncompatibleEngine) as excinfo:
        FakeAdapter("9.9.9").check_compatible()
    assert "2.5.7" in excinfo.value.detail
    assert "compatibility environment" in excinfo.value.detail


def test_an_adapter_declaring_no_tested_versions_is_refused():
    """An adapter with an empty list has not been tested against anything."""

    class Untested(FakeAdapter):
        supported_versions = ()

    with pytest.raises(IncompatibleEngine, match="declares no tested versions"):
        Untested().check_compatible()


# -- failure reporting ------------------------------------------------------

def test_an_unreachable_engine_is_retryable():
    """A network failure is worth retrying; a rejection is not."""
    from cass_adapters.base import EngineRejected, EngineUnavailable

    assert EngineUnavailable("down").retryable is True
    assert EngineRejected("bad request").retryable is False


def test_describe_reports_an_unreachable_engine_without_raising():
    """The administration screen must render even when an engine is down."""
    described = FakeAdapter(reachable=False).describe()
    assert described["reachable"] is False
    assert described["compatible"] is False
    assert "did not respond" in described["error"]


def test_describe_reports_a_reachable_incompatible_engine():
    described = FakeAdapter("9.9.9").describe()
    assert described["reachable"] is True
    assert described["compatible"] is False
    assert described["version"] == "9.9.9"


def test_describe_reports_a_healthy_compatible_engine():
    described = FakeAdapter("2.5.7").describe()
    assert described == {
        "engine": "fake-engine",
        "reachable": True,
        "version": "2.5.7",
        "image_digest": "sha256:abc",
        "supported_versions": ["2.5.7", "3.23.1"],
        "compatible": True,
    }


# -- normalised state -------------------------------------------------------

def test_terminal_states_are_identified():
    assert EngineState.SUCCEEDED.is_terminal
    assert EngineState.FAILED.is_terminal
    assert EngineState.CANCELLED.is_terminal
    assert not EngineState.RUNNING.is_terminal
    assert not EngineState.PENDING.is_terminal


def test_unknown_is_not_treated_as_terminal():
    """An engine whose state cannot be read has not necessarily finished."""
    assert not EngineState.UNKNOWN.is_terminal
