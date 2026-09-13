"""Direct-to-store uploads: issued, received, completed, checksummed, scanned.

Three failures these exist to prevent, and each was possible before.

A session pointed at somebody else's object. The caller used to name the key
and the bucket, which on S3 is a presigned PUT that overwrites whatever is
there. The key is chosen by the platform now, under the owning project's
prefix, for somebody who may write to that project.

An upload nobody ever finished. A pending artifact stayed pending for ever and
nothing had read what arrived. Completion is a separate act that reads the
object back, and an abandoned session is expired by the retention sweep.

And an object registered on the uploader's word. What arrived is digested and
measured, compared with what the uploader said they sent, and scanned; a
mismatch or a finding is quarantined, and an installation with no antivirus
engine says so in the artifact's own note rather than implying a scan.
"""

from __future__ import annotations

import datetime as dt
import hashlib

import pytest
from django.utils import timezone

from apps.artifacts import intake, retention
from apps.artifacts.models import Artifact, ArtifactState
from apps.common.storage import get_store
from apps.projects.models import ProjectMembership, ProjectRole

from .conftest import API

pytestmark = pytest.mark.django_db

CSV = b"PortNumber,AccNumber,LocNumber\n1,ACC-1,LOC-1\n"


def digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def open_session(api, project, filename="book.csv", content_type="text/csv"):
    return api.post(
        f"{API}/artifacts/upload-session/",
        {"project": str(project.id), "filename": filename, "content_type": content_type},
        format="json",
    )


def send(api, session, payload=CSV, content_type="text/csv"):
    return api.put(session.data["url"], data=payload, content_type=content_type)


class FakeClamd:
    """A clamd connection that answers once, as the real engine does."""

    def __init__(self, reply: bytes):
        self.reply = reply
        self.sent = b""
        self._answered = False

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, size: int) -> bytes:
        if self._answered:
            return b""
        self._answered = True
        return self.reply

    def close(self) -> None:
        pass


# -- issuing a session -------------------------------------------------------

def test_a_session_names_a_project_and_a_file_rather_than_a_key(api, project):
    refused = api.post(
        f"{API}/artifacts/upload-session/",
        {"key": "anything/I/like.csv", "purpose": "model"},
        format="json",
    )

    assert refused.status_code == 400
    assert "Name the project" in refused.data["detail"]


def test_the_platform_chooses_the_key_under_the_projects_own_prefix(api, project):
    """A session can never be pointed at somebody else's object."""
    session = open_session(api, project, filename="../../other-project/keys.csv")

    assert session.status_code == 201, session.data
    assert f"/{project.artifact_prefix}/uploads/" in session.data["uri"]
    assert "other-project" not in session.data["uri"].split("/uploads/")[0]
    artifact = Artifact.objects.get(id=session.data["artifact"])
    assert artifact.state == ArtifactState.PENDING
    assert artifact.project == project
    assert artifact.expires_at is not None


def test_a_project_the_caller_cannot_see_is_not_found(client_for, outsider, project):
    refused = open_session(client_for(outsider), project)

    assert refused.status_code == 404


def test_a_member_who_may_only_read_may_not_upload(client_for, outsider, project):
    ProjectMembership.objects.create(project=project, user=outsider, role=ProjectRole.VIEWER)

    refused = open_session(client_for(outsider), project)

    assert refused.status_code == 403
    assert "may not upload" in refused.data["detail"]


# -- receiving the body ------------------------------------------------------

def test_the_body_is_received_for_the_person_who_opened_the_session(api, project):
    session = open_session(api, project)

    received = send(api, session)

    assert received.status_code == 204
    assert get_store().exists(session.data["uri"])
    # Received is not registered: completion is a separate act.
    assert Artifact.objects.get(id=session.data["artifact"]).state == ArtifactState.PENDING


def test_nobody_else_can_send_into_a_session(api, client_for, outsider, project):
    session = open_session(api, project)

    refused = send(client_for(outsider), session)

    assert refused.status_code == 404


def test_an_expired_session_accepts_nothing(api, project):
    session = open_session(api, project)
    Artifact.objects.filter(id=session.data["artifact"]).update(
        expires_at=timezone.now() - dt.timedelta(minutes=1)
    )

    refused = send(api, session)

    assert refused.status_code == 410


# -- completing it -----------------------------------------------------------

def test_a_completed_upload_is_registered_with_what_actually_arrived(api, project):
    session = open_session(api, project)
    send(api, session)

    completed = api.post(
        f"{API}/artifacts/{session.data['artifact']}/complete/",
        {"checksum": digest(CSV), "size_bytes": len(CSV)},
        format="json",
    )

    assert completed.status_code == 200, completed.data
    assert completed.data["state"] == ArtifactState.REGISTERED
    assert completed.data["checksum"] == digest(CSV)
    assert completed.data["size_bytes"] == len(CSV)
    # The session deadline gives way to the retention class.
    artifact = Artifact.objects.get(id=session.data["artifact"])
    assert artifact.expires_at is None


def test_a_registered_upload_says_no_antivirus_scan_was_made_where_none_is_configured(
    api, project, settings
):
    """A registered state must not imply a scan that did not happen."""
    settings.CASS_CLAMD_HOST = ""
    session = open_session(api, project)
    send(api, session)

    completed = api.post(f"{API}/artifacts/{session.data['artifact']}/complete/")

    assert "No antivirus engine is configured" in completed.data["validation_note"]
    assert "content_signature" in completed.data["validation_note"]


def test_an_upload_that_arrived_different_from_what_was_sent_is_quarantined(api, project):
    session = open_session(api, project)
    send(api, session)

    completed = api.post(
        f"{API}/artifacts/{session.data['artifact']}/complete/",
        {"checksum": digest(b"something else entirely")},
        format="json",
    )

    assert completed.status_code == 422
    assert completed.data["state"] == ArtifactState.QUARANTINED
    assert "not the file that left" in completed.data["validation_note"]
    assert completed.data["readable"] is False


def test_a_truncated_upload_is_quarantined(api, project):
    session = open_session(api, project)
    send(api, session)

    completed = api.post(
        f"{API}/artifacts/{session.data['artifact']}/complete/",
        {"size_bytes": len(CSV) + 1000},
        format="json",
    )

    assert completed.data["state"] == ArtifactState.QUARANTINED
    assert "truncated" in completed.data["validation_note"]


def test_an_executable_declared_as_a_spreadsheet_is_quarantined(api, project):
    session = open_session(api, project, filename="book.csv")
    send(api, session, payload=b"MZ\x90\x00\x03\x00\x00\x00 not a spreadsheet")

    completed = api.post(f"{API}/artifacts/{session.data['artifact']}/complete/")

    assert completed.data["state"] == ArtifactState.QUARANTINED
    assert "Windows executable" in completed.data["validation_note"]


def test_a_native_workbook_uploaded_as_csv_is_quarantined_with_the_fix(api, project):
    session = open_session(api, project, filename="book.csv")
    send(api, session, payload=b"PK\x03\x04\x00\x00binary workbook\x00content")

    completed = api.post(f"{API}/artifacts/{session.data['artifact']}/complete/")

    assert completed.data["state"] == ArtifactState.QUARANTINED
    assert "export it and upload again" in completed.data["validation_note"]


def test_completing_before_anything_arrived_says_to_send_the_file_first(api, project):
    session = open_session(api, project)

    refused = api.post(f"{API}/artifacts/{session.data['artifact']}/complete/")

    assert refused.status_code == 409
    assert "Nothing has arrived" in refused.data["detail"]


def test_only_whoever_opened_an_upload_may_complete_it(
    api, client_for, analyst, project, reviewer
):
    ProjectMembership.objects.create(
        project=project, user=reviewer, role=ProjectRole.CONTRIBUTOR
    )
    session = open_session(api, project)
    send(api, session)

    refused = client_for(reviewer).post(
        f"{API}/artifacts/{session.data['artifact']}/complete/"
    )

    assert refused.status_code == 403


# -- the antivirus engine ----------------------------------------------------

def test_the_engine_is_spoken_to_in_its_own_protocol_and_a_clean_reply_registers(
    api, project, analyst
):
    session = open_session(api, project)
    send(api, session)
    connection = FakeClamd(b"stream: OK\0")
    scanner = intake.ClamdScanner("clamd", connect=lambda: connection)

    artifact = intake.complete(
        Artifact.objects.get(id=session.data["artifact"]), actor=analyst, scanner=scanner
    )

    assert artifact.state == ArtifactState.REGISTERED
    assert connection.sent.startswith(b"zINSTREAM\0")
    # The stream ends with a zero-length chunk, which is how clamd knows.
    assert connection.sent.endswith(b"\x00\x00\x00\x00")
    assert "clamav" in artifact.validation_note
    assert "No antivirus engine" not in artifact.validation_note


def test_a_signature_the_engine_finds_quarantines_the_upload(api, project, analyst):
    session = open_session(api, project)
    send(api, session)
    scanner = intake.ClamdScanner(
        "clamd", connect=lambda: FakeClamd(b"stream: Eicar-Test-Signature FOUND\0")
    )

    artifact = intake.complete(
        Artifact.objects.get(id=session.data["artifact"]), actor=analyst, scanner=scanner
    )

    assert artifact.state == ArtifactState.QUARANTINED
    assert "Eicar-Test-Signature" in artifact.validation_note


def test_an_unreachable_engine_leaves_the_upload_pending_rather_than_unscanned(
    api, project, analyst
):
    """Not looked at is not the same as found wanting, and not the same as clean."""
    session = open_session(api, project)
    send(api, session)

    def unreachable():
        raise OSError("connection refused")

    scanner = intake.ClamdScanner("clamd", connect=unreachable)

    with pytest.raises(intake.ScannerUnavailable, match="could not be reached"):
        intake.complete(
            Artifact.objects.get(id=session.data["artifact"]), actor=analyst, scanner=scanner
        )

    assert Artifact.objects.get(id=session.data["artifact"]).state == ArtifactState.PENDING


def test_the_api_reports_an_unreachable_engine_as_retryable(api, project, settings):
    settings.CASS_CLAMD_HOST = "127.0.0.1"
    settings.CASS_CLAMD_PORT = 1  # nothing listens here
    session = open_session(api, project)
    send(api, session)

    refused = api.post(f"{API}/artifacts/{session.data['artifact']}/complete/")

    assert refused.status_code == 503


# -- abandoned sessions and the other route in -------------------------------

def test_an_upload_nobody_completed_is_expired_by_the_retention_sweep(api, project):
    session = open_session(api, project)
    send(api, session)
    Artifact.objects.filter(id=session.data["artifact"]).update(
        expires_at=timezone.now() - dt.timedelta(hours=1)
    )

    report = retention.sweep()

    artifact = Artifact.objects.get(id=session.data["artifact"])
    assert report["abandoned_upload_count"] == 1
    assert artifact.state == ArtifactState.EXPIRED
    assert get_store().exists(session.data["uri"]) is False


def test_an_executable_through_the_ordinary_upload_route_is_refused_too(
    api, project
):
    """Two routes into the store with two standards makes the weaker one matter."""
    from apps.exposure.models import ExposureVersion
    from apps.exposure.services import ExposureError, attach_file
    from cass_oed.schema import FileKind

    version = ExposureVersion.objects.create(project=project, name="Pilot")

    with pytest.raises(ExposureError, match="Linux executable"):
        attach_file(version, FileKind.LOCATION, b"\x7fELF\x02\x01\x01 payload", filename="book.csv")
