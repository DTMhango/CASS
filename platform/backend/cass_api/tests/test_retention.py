"""Expiring artifacts under their retention class.

Every artifact has carried an expiry date since the store was built and nothing
ever acted on one, which is worse than having no policy: a fourteen-day class
that keeps its objects for ever still reads as a fourteen-day class.

What these hold to account is the three refusals, because they are the reason
the sweep is safe to run unattended. An artifact a run is still reading is not
taken from under it. An artifact behind an approved result is never expired at
all, whatever its class says, because section 12 requires the number to stay
traceable to what produced it. And the record always survives the payload: a
lineage with a hole in it is not a lineage.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.artifacts import retention
from apps.artifacts.models import Artifact, ArtifactLink, ArtifactState
from apps.common.storage import bucket, get_store
from cass_core.artifacts import AccessPolicy, RetentionClass

pytestmark = pytest.mark.django_db


def store_artifact(project, analyst, *, role="cass_keys", retention_class=RetentionClass.DIAGNOSTIC):
    """One real object in the store, with a record that points at it."""
    store = get_store()
    ref = store.put_bytes(
        bucket("portfolio"),
        f"test/{role}-{timezone.now().timestamp()}.csv",
        b"LocNumber,AreaPerilID\n1,7\n",
        content_type="text/csv",
        retention=retention_class,
        access=AccessPolicy.PROJECT,
    )
    return Artifact.objects.create(
        uri=ref.uri,
        checksum=ref.checksum,
        size_bytes=ref.size_bytes,
        content_type=ref.content_type,
        retention=str(retention_class),
        access=str(AccessPolicy.PROJECT),
        state=ArtifactState.REGISTERED,
        project=project,
        role=role,
        created_by=analyst,
    )


def make_due(artifact):
    artifact.expires_at = timezone.now() - dt.timedelta(days=1)
    artifact.save(update_fields=["expires_at"])
    return artifact


def test_a_retention_class_with_no_lifetime_never_becomes_due(project, analyst):
    """A permanent class is a decision somebody took, not a long timer."""
    kept = store_artifact(project, analyst, retention_class=RetentionClass.RESULT)

    assert kept.expires_at is None
    assert retention.due().count() == 0


def test_a_diagnostic_artifact_becomes_due_and_is_expired(project, analyst):
    artifact = make_due(store_artifact(project, analyst))

    report = retention.sweep()

    artifact.refresh_from_db()
    assert report["expired_count"] == 1
    assert artifact.state == ArtifactState.EXPIRED
    assert get_store().exists(artifact.uri) is False


def test_the_record_outlives_the_bytes(project, analyst):
    """A result has to go on naming the file it was reconciled against."""
    artifact = make_due(store_artifact(project, analyst))

    retention.sweep()

    artifact.refresh_from_db()
    assert Artifact.objects.filter(id=artifact.id).exists()
    assert artifact.checksum
    assert artifact.uri
    assert artifact.is_readable is False


def test_a_dry_run_changes_nothing(project, analyst):
    artifact = make_due(store_artifact(project, analyst))

    report = retention.sweep(dry_run=True)

    artifact.refresh_from_db()
    assert report["expired_count"] == 1
    assert artifact.state == ArtifactState.REGISTERED
    assert get_store().exists(artifact.uri) is True


def test_an_artifact_a_running_run_is_using_is_not_taken_from_under_it(
    project, analyst
):
    from apps.runs.models import Run, RunKind

    artifact = make_due(store_artifact(project, analyst))
    run = Run.objects.create(
        kind=RunKind.ANALYSIS, project=project, state="running", created_by=analyst
    )
    ArtifactLink.objects.create(
        artifact=artifact,
        subject_type="analysis_run",
        subject_id=run.id,
        role="cass_keys",
        direction="output",
        created_by=analyst,
    )

    report = retention.sweep()

    artifact.refresh_from_db()
    assert report["expired_count"] == 0
    assert "has not finished" in report["kept"][0]["reason"]
    assert artifact.state == ArtifactState.REGISTERED


def test_evidence_behind_an_approved_result_outlives_its_retention_class(
    project, analyst
):
    """Section 12: the number stays traceable to what produced it."""
    from apps.results.models import ResultSet, ResultState
    from apps.runs.models import Run, RunKind

    artifact = make_due(store_artifact(project, analyst))
    run = Run.objects.create(
        kind=RunKind.ANALYSIS, project=project, state="succeeded", created_by=analyst
    )
    ArtifactLink.objects.create(
        artifact=artifact,
        subject_type="analysis_run",
        subject_id=run.id,
        role="cass_keys",
        direction="output",
        created_by=analyst,
    )
    ResultSet.objects.create(
        run=run,
        project=project,
        label="Approved ground-up",
        perspective="ground_up",
        state=ResultState.APPROVED,
        created_by=analyst,
    )

    report = retention.sweep()

    artifact.refresh_from_db()
    assert report["expired_count"] == 0
    assert "approved result" in report["kept"][0]["reason"]
    assert artifact.state == ArtifactState.REGISTERED


def test_a_finished_run_without_an_approved_result_does_not_hold_its_diagnostics(
    project, analyst
):
    from apps.runs.models import Run, RunKind

    artifact = make_due(store_artifact(project, analyst))
    run = Run.objects.create(
        kind=RunKind.ANALYSIS, project=project, state="succeeded", created_by=analyst
    )
    ArtifactLink.objects.create(
        artifact=artifact,
        subject_type="analysis_run",
        subject_id=run.id,
        role="cass_keys",
        direction="output",
        created_by=analyst,
    )

    report = retention.sweep()

    artifact.refresh_from_db()
    assert report["expired_count"] == 1
    assert artifact.state == ArtifactState.EXPIRED


def test_published_exposure_is_never_expired(project, analyst):
    artifact = make_due(store_artifact(project, analyst, role="oed_location"))
    ArtifactLink.objects.create(
        artifact=artifact,
        subject_type="exposure_version",
        subject_id=artifact.id,
        role="oed_location",
        direction="input",
        created_by=analyst,
    )

    report = retention.sweep()

    assert report["expired_count"] == 0
    assert "published exposure" in report["kept"][0]["reason"]


def test_the_sweep_is_written_to_the_audit_trail(project, analyst):
    from apps.audit.models import AuditEvent

    artifact = make_due(store_artifact(project, analyst))

    retention.sweep()

    event = AuditEvent.objects.get(subject_type="artifact", subject_id=str(artifact.id))
    assert event.after_reference["state"] == ArtifactState.EXPIRED
    assert event.after_reference["retention"] == str(RetentionClass.DIAGNOSTIC)


def test_the_command_reports_what_it_would_do(project, analyst, capsys):
    from django.core.management import call_command

    make_due(store_artifact(project, analyst))

    call_command("expire_artifacts", "--dry-run")

    printed = capsys.readouterr().out
    assert "would expire" in printed
    assert "1 expired" in printed


def test_the_platform_schedules_both_policies_it_enforces_on_a_clock(settings):
    """A policy nothing runs is not a policy.

    Both of these were commands an operator had to remember: retention expiry
    and closing runs whose worker died. The schedule is what makes them the
    platform's behaviour rather than somebody's habit.
    """
    scheduled = {
        item["task"] for item in settings.CELERY_BEAT_SCHEDULE.values()
    }

    assert "cass.artifacts.expire_due" in scheduled
    assert "cass.runs.abandon_stale" in scheduled


def test_the_scheduled_sweep_reports_what_it_did(project, analyst):
    from apps.artifacts.tasks import expire_due

    make_due(store_artifact(project, analyst))

    report = expire_due()

    assert report["expired_count"] == 1
    assert report["bytes_released"] > 0
