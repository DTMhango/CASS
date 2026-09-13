"""Admission control and time limits, from the declared execution profiles.

Section 11 asks for a declared envelope rather than an inherited maximum, and
names unbounded concurrency as a material risk: the PiWind baseline peaked at
21.6 GB under a highly parallel configuration, and a host that lets four of
those start at once fails all four. The profiles were declared and validated
when a run was created, and then nothing used them.

Two things are enforced here.

A profile admits only as many runs at once as it declares. A run that arrives
when its profile is full is refused with what is running and when to come back,
rather than queued behind a broker that will start it anyway or failed with an
out-of-memory error twenty minutes later.

And a run may not exceed its profile's time limit. The limit reaches the engine
polls as their timeout, so a calculation that hangs is abandoned rather than
holding a worker for ever, and it is checked between stages, so a run that
creeps past it stops at a stage boundary with an intelligible reason instead of
somewhere inside one.

Neither is a queue. Section 11's queue and the broker's are the same thing, and
adding a second one here would put two things in charge of what runs next.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from django.conf import settings

from cass_core.runs import ACTIVE_STATES

from .models import Run


class AdmissionRefused(Exception):
    """Raised when a profile has no room for another run."""

    def __init__(self, profile: str, running: int, limit: int) -> None:
        self.profile = profile
        self.running = running
        self.limit = limit
        super().__init__(
            f"The {profile} profile allows {limit} run(s) at once and {running} "
            "are already active. Wait for one to finish, or choose a profile with "
            "room. Starting it anyway is what section 11 forbids: the measured "
            "peak of a parallel run is large enough that two of them fail both."
        )


@dataclasses.dataclass(frozen=True, slots=True)
class Capacity:
    """What one profile declares and what is using it now."""

    profile: str
    cpu: int
    memory_gb: int
    timeout_seconds: int
    max_concurrent: int
    running: int

    @property
    def available(self) -> int:
        return max(self.max_concurrent - self.running, 0)

    @property
    def is_full(self) -> bool:
        return self.running >= self.max_concurrent

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "cpu": self.cpu,
            "memory_gb": self.memory_gb,
            "timeout_seconds": self.timeout_seconds,
            "max_concurrent": self.max_concurrent,
            "running": self.running,
            "available": self.available,
            "is_full": self.is_full,
        }


def declared(profile: str) -> dict[str, Any]:
    """One profile's envelope, or a refusal naming the ones that exist."""
    try:
        return dict(settings.CASS_EXECUTION_PROFILES[profile])
    except KeyError:
        raise AdmissionRefused(profile, running=0, limit=0) from None


def running_in(profile: str, *, exclude: Any = None) -> int:
    """How many runs are active on this profile."""
    queryset = Run.objects.filter(
        execution_profile=profile, state__in=[str(state) for state in ACTIVE_STATES]
    )
    if exclude is not None:
        queryset = queryset.exclude(pk=exclude)
    return queryset.count()


def capacity() -> list[Capacity]:
    """Every declared profile with what is running on it."""
    found: list[Capacity] = []
    for name, envelope in sorted(settings.CASS_EXECUTION_PROFILES.items()):
        found.append(
            Capacity(
                profile=name,
                cpu=int(envelope.get("cpu", 0)),
                memory_gb=int(envelope.get("memory_gb", 0)),
                timeout_seconds=int(envelope.get("timeout_seconds", 0)),
                max_concurrent=int(envelope.get("max_concurrent", 1)),
                running=running_in(name),
            )
        )
    return found


def admit(run: Run) -> None:
    """Refuse a run its profile has no room for.

    The run itself is excluded from the count: a run being resumed at a gate is
    already active on its own profile and must not be refused for occupying the
    place it is asking to go on occupying.
    """
    envelope = settings.CASS_EXECUTION_PROFILES.get(run.execution_profile)
    if envelope is None:
        raise AdmissionRefused(run.execution_profile, running=0, limit=0)

    limit = int(envelope.get("max_concurrent", 1))
    running = running_in(run.execution_profile, exclude=run.pk)
    if running >= limit:
        raise AdmissionRefused(run.execution_profile, running=running, limit=limit)


def time_limit(run: Run) -> float | None:
    """The seconds this run's profile allows it, or nothing where it declares none."""
    envelope = settings.CASS_EXECUTION_PROFILES.get(run.execution_profile) or {}
    stated = envelope.get("timeout_seconds")
    return float(stated) if stated else None
