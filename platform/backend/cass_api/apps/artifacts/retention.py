"""Expiring artifacts under their retention class, and refusing to.

Section 5 makes the retention class decide how long an object survives, and
every artifact has carried an expiry date since the store was built. Nothing
ever acted on one, so "expires after 14 days" meant an object that stayed for
ever with a date in the past written on it -- which is worse than no policy,
because it reads as one.

What sweeps here is the payload, never the record. The row stays, marked
expired, because lineage outlives the bytes: a result approved last year has to
go on naming the keys file it was reconciled against even once that file is
gone, and a lineage with a hole in it is not a lineage.

Three things are refused outright, and they are the point of the module rather
than caveats to it. An artifact a run still in flight is using is not expired
under it. An artifact behind an approved result is not expired at all: the
number rests on it, and section 12 requires a result to stay traceable to what
produced it. And an artifact whose retention class states no lifetime is never
due in the first place -- a permanent class is a decision somebody took.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.storage import get_store
from cass_core.artifacts import ArtifactNotFound
from cass_core.runs import ACTIVE_STATES

from .models import Artifact, ArtifactLink, ArtifactState


@dataclasses.dataclass(frozen=True, slots=True)
class Refusal:
    """Why one due artifact was not expired."""

    artifact: Artifact
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact": str(self.artifact.id),
            "uri": self.artifact.uri,
            "role": self.artifact.role,
            "reason": self.reason,
        }


def due(*, now: dt.datetime | None = None) -> QuerySet[Artifact]:
    """Registered artifacts whose retention class has run out."""
    moment = now or timezone.now()
    return Artifact.objects.filter(
        state=ArtifactState.REGISTERED, expires_at__isnull=False, expires_at__lte=moment
    ).order_by("expires_at")


def _linked_runs(artifact: Artifact) -> list[str]:
    return list(
        ArtifactLink.objects.filter(
            artifact=artifact, subject_type__in=("analysis_run", "hazard_run", "conversion_run")
        ).values_list("subject_id", flat=True)
    )


def protection(artifact: Artifact) -> str:
    """Why this artifact may not be expired, or an empty string where it may.

    Checked against what the artifact is attached to rather than against its
    own fields: an artifact does not know that a reviewer approved the number
    derived from it, and that is exactly the fact that has to stop it going.
    """
    from apps.results.models import ResultSet, ResultState
    from apps.runs.models import Run

    run_ids = _linked_runs(artifact)
    if run_ids:
        active = Run.objects.filter(
            id__in=run_ids, state__in=[str(state) for state in ACTIVE_STATES]
        ).exists()
        if active:
            return (
                "A run using this artifact has not finished. Expiring it under a "
                "run would remove an input the run is still reading."
            )

        approved = ResultSet.objects.filter(
            run_id__in=run_ids, state=ResultState.APPROVED
        ).exists()
        if approved:
            return (
                "An approved result rests on this run. Section 12 requires a result "
                "to stay traceable to what produced it, so its evidence outlives "
                "the retention class."
            )

    if ArtifactLink.objects.filter(
        artifact=artifact, subject_type="exposure_version"
    ).exists():
        return (
            "This is published exposure, which is immutable input to every run "
            "that used it."
        )

    return ""


def expire(artifact: Artifact, *, actor=None) -> bool:
    """Remove the payload and mark the record expired, keeping the lineage.

    A payload already gone is not a failure: the record is marked expired
    anyway, because the state has to describe the store rather than the last
    attempt to change it.
    """
    store = get_store()
    try:
        store.delete(artifact.uri)
    except ArtifactNotFound:
        pass

    artifact.state = ArtifactState.EXPIRED
    artifact.updated_by = actor
    artifact.save(update_fields=["state", "updated_by", "updated_at"])

    audit.record(
        action=AuditAction.DELETE,
        subject_type="artifact",
        subject_id=artifact.id,
        actor=actor,
        project=artifact.project,
        subject_label=str(artifact),
        before={"state": ArtifactState.REGISTERED},
        after={"state": ArtifactState.EXPIRED, "retention": artifact.retention},
    )
    return True


def sweep(
    *,
    now: dt.datetime | None = None,
    actor=None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Expire everything due that nothing protects, and report both.

    The refusals are returned rather than counted: an artifact that keeps being
    due and keeps being kept is either evidence somebody should reclassify or a
    run that never finished, and both are worth seeing by name.
    """
    considered = due(now=now)
    if limit:
        considered = considered[:limit]

    expired: list[dict[str, Any]] = []
    refused: list[Refusal] = []
    released = 0

    for artifact in considered:
        reason = protection(artifact)
        if reason:
            refused.append(Refusal(artifact=artifact, reason=reason))
            continue
        if not dry_run:
            expire(artifact, actor=actor)
        released += artifact.size_bytes or 0
        expired.append(
            {
                "artifact": str(artifact.id),
                "uri": artifact.uri,
                "role": artifact.role,
                "retention": artifact.retention,
                "size_bytes": artifact.size_bytes,
            }
        )

    return {
        "swept_at": (now or timezone.now()).isoformat(),
        "dry_run": dry_run,
        "expired": expired,
        "expired_count": len(expired),
        "bytes_released": released,
        "kept": [item.as_dict() for item in refused],
        "kept_count": len(refused),
    }
