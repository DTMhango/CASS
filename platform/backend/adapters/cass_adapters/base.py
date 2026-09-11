"""The shared shape of an engine adapter.

Section 4 of the build plan makes every boundary a versioned adapter over a
supported API, and section 15 names what adapters exist to prevent: custom
engine forks accumulating, and an engine upgrade breaking integration.

Three rules follow, and they are enforced here rather than restated in each
adapter.

An adapter declares the engine versions it has been tested against. A version
outside that range is refused rather than attempted, because an adapter that
half-works against an untested engine produces results nobody can defend.

An adapter never writes to an engine's internal store. It calls the documented
API. Where an engine needs files on a shared volume, the adapter stages them
from the artifact store rather than handing the engine a CASS path.

An adapter surfaces failure in terms a run monitor can display. An HTTP 500
from an engine is not an intelligible state; "the calculation was rejected
because the site model is missing" is.
"""

from __future__ import annotations

import abc
import dataclasses
import enum
from collections.abc import Mapping
from typing import Any


class EngineState(enum.StrEnum):
    """Engine-reported job state, normalised across engines.

    Each adapter maps its engine's vocabulary onto this, so the workflow layer
    does not need to know that OpenQuake says "complete" and Oasis says
    "RUN_COMPLETED".
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

    @property
    def is_terminal(self) -> bool:
        return self in (
            EngineState.SUCCEEDED,
            EngineState.FAILED,
            EngineState.CANCELLED,
        )


class AdapterError(Exception):
    """Base class for adapter failures.

    Carries an analyst-facing summary alongside the technical detail, because
    section 12 requires an intelligible state rather than a stack trace.
    """

    def __init__(self, summary: str, *, detail: str = "", retryable: bool = False) -> None:
        super().__init__(summary)
        self.summary = summary
        self.detail = detail
        self.retryable = retryable


class EngineUnavailable(AdapterError):
    """The engine could not be reached. Usually worth retrying."""

    def __init__(self, summary: str, *, detail: str = "") -> None:
        super().__init__(summary, detail=detail, retryable=True)


class EngineRejected(AdapterError):
    """The engine refused the request. Retrying will not help."""


class IncompatibleEngine(AdapterError):
    """The engine version is outside the tested compatibility range."""


@dataclasses.dataclass(frozen=True, slots=True)
class EngineJob:
    """A job as the engine reports it."""

    engine_job_id: str
    state: EngineState
    progress: float | None = None
    message: str = ""
    raw_state: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine_job_id": self.engine_job_id,
            "state": str(self.state),
            "raw_state": self.raw_state,
            "progress": self.progress,
            "message": self.message,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class EngineVersion:
    """What an engine reports about itself."""

    name: str
    version: str
    image_digest: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "image_digest": self.image_digest,
        }


class EngineAdapter(abc.ABC):
    """The operations every engine adapter provides."""

    #: Engine versions this adapter has contract tests against. Section 18
    #: requires promotion only after those tests pass, so the list is the
    #: record of what has actually been tested.
    supported_versions: tuple[str, ...] = ()

    #: Human name, used in failure messages and the support bundle.
    engine_name: str = "engine"

    @abc.abstractmethod
    def version(self) -> EngineVersion:
        """Ask the engine what it is."""

    @abc.abstractmethod
    def healthy(self) -> bool:
        """Whether the engine is reachable and ready."""

    def check_compatible(self) -> EngineVersion:
        """Refuse to proceed against an untested engine version.

        A patch-level difference is accepted; a minor or major difference is
        not, because that is where export formats and API shapes move.
        """
        reported = self.version()
        if not self.supported_versions:
            raise IncompatibleEngine(
                f"The {self.engine_name} adapter declares no tested versions.",
                detail="Add the tested version to supported_versions before use.",
            )
        if any(_series(reported.version) == _series(item) for item in self.supported_versions):
            return reported
        raise IncompatibleEngine(
            f"{self.engine_name} reports version {reported.version}, which this "
            "adapter has not been tested against.",
            detail=(
                f"Tested versions: {', '.join(self.supported_versions)}. Run the "
                "contract and regression suites in a compatibility environment "
                "before promoting a new engine version."
            ),
        )

    def describe(self) -> Mapping[str, Any]:
        """Version and health, for the administration screen and support bundle."""
        try:
            reported = self.version()
            return {
                "engine": self.engine_name,
                "reachable": True,
                "version": reported.version,
                "image_digest": reported.image_digest,
                "supported_versions": list(self.supported_versions),
                "compatible": any(
                    _series(reported.version) == _series(item)
                    for item in self.supported_versions
                ),
            }
        except AdapterError as exc:
            return {
                "engine": self.engine_name,
                "reachable": False,
                "error": exc.summary,
                "supported_versions": list(self.supported_versions),
                "compatible": False,
            }


def _series(version: str) -> tuple[int, ...]:
    """Return the major and minor parts of a version, for comparison.

    Patch releases within a tested minor series are accepted; anything else is
    a new compatibility question.
    """
    parts: list[int] = []
    for chunk in version.split(".")[:2]:
        digits = "".join(character for character in chunk if character.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 2:
        parts.append(0)
    return tuple(parts)
