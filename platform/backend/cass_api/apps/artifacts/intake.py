"""Direct-to-store uploads: issued to a project, completed, checksummed, scanned.

Section 5 has large files go straight from the browser to object storage, with
Django registering the artifact only once the bytes are present, checksummed
and validated. Half of that existed. A session could be issued -- for any key a
caller named, in any bucket, with no project behind it, which on S3 is a
presigned PUT that can overwrite somebody else's object -- and then nothing
ever completed it. The artifact stayed pending for ever and nothing had looked
at what arrived.

So three rules, each of which closes one of those.

The key is chosen here, under the owning project's prefix, and the session is
issued only to someone who may write to that project. A caller names a file,
not a location.

Completion is a separate act that reads back what actually arrived. The digest
and the size are computed from the stored object, compared with what the
uploader said they sent, and a mismatch is quarantined rather than registered:
an object that arrived different from what left the browser is not the file
anybody meant.

And every completed upload is scanned before anything can read it. The content
check needs no outside service -- an executable declared as a spreadsheet is
refused on its first bytes -- and an antivirus engine is used where the
installation names one. Where none is configured the artifact says so in its
own validation note, rather than a registered state implying a scan that did
not happen. Section 10's quarantine exists for the rest.
"""

from __future__ import annotations

import dataclasses
import re
import socket
import struct
import uuid
from collections.abc import Callable, Iterable
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.storage import bucket, get_store
from cass_core.artifacts import AccessPolicy, RetentionClass

from .models import Artifact, ArtifactState

#: How much of an object the content check reads. Enough to see a signature and
#: to tell text from binary, without reading a national file to do it.
HEAD_BYTES = 64 * 1024

#: Streamed reads for the digest and the antivirus engine.
CHUNK_BYTES = 1024 * 1024

#: Leading bytes of the executable formats a portfolio upload has no business
#: being. Checked whatever the declared type, because a declared type is a
#: claim the uploader makes.
EXECUTABLE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"MZ", "a Windows executable"),
    (b"\x7fELF", "a Linux executable"),
    (b"\xcf\xfa\xed\xfe", "a macOS executable"),
    (b"\xfe\xed\xfa\xcf", "a macOS executable"),
    (b"\xce\xfa\xed\xfe", "a macOS executable"),
    (b"\xca\xfe\xba\xbe", "a macOS universal binary or Java class"),
)

#: Declared types that must be text, so a NUL byte in them is binary content
#: travelling under a text label.
TEXT_TYPES = frozenset({"text/csv", "text/plain", "application/json", "application/xml"})

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._\-]+")


class IntakeError(Exception):
    """Raised when an upload cannot be issued or completed as asked."""


class ScannerUnavailable(IntakeError):
    """Raised when a configured scanner cannot be reached.

    Deliberately not a quarantine. The object has not been found wanting; it
    has not been looked at, and the right state for that is still pending, so
    completion can be retried once the engine is back.
    """


@dataclasses.dataclass(frozen=True, slots=True)
class Verdict:
    """What one scanner concluded about one object."""

    scanner: str
    clean: bool
    finding: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"scanner": self.scanner, "clean": self.clean, "finding": self.finding}


def content_verdict(head: bytes, *, content_type: str) -> Verdict:
    """Refuse content that is not what a portfolio upload can be.

    Needs no outside service, so it runs on every installation.
    """
    for signature, description in EXECUTABLE_SIGNATURES:
        if head.startswith(signature):
            return Verdict(
                scanner="content_signature",
                clean=False,
                finding=(
                    f"The file begins with the signature of {description}, which no "
                    "exposure, model or hazard file is."
                ),
            )
    if content_type.split(";")[0].strip().lower() in TEXT_TYPES and b"\x00" in head:
        return Verdict(
            scanner="content_signature",
            clean=False,
            finding=(
                f"The file was declared as {content_type} and contains binary content. "
                "A spreadsheet saved in its native format rather than exported as CSV "
                "does this too; export it and upload again."
            ),
        )
    return Verdict(scanner="content_signature", clean=True)


class ClamdScanner:
    """An antivirus engine reached over clamd's INSTREAM protocol.

    The connection is injectable so the protocol can be tested without an
    engine; in an installation it is a TCP socket to the configured host.
    """

    name = "clamav"

    def __init__(
        self,
        host: str,
        port: int = 3310,
        *,
        timeout: float = 30.0,
        connect: Callable[[], Any] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._connect = connect or (
            lambda: socket.create_connection((self.host, self.port), timeout=self.timeout)
        )

    def scan(self, chunks: Iterable[bytes]) -> Verdict:
        try:
            connection = self._connect()
        except OSError as exc:
            raise ScannerUnavailable(
                f"The antivirus engine at {self.host}:{self.port} could not be reached "
                f"({exc}). The upload stays pending and can be completed again once "
                "the engine is back; it has not been released unscanned."
            ) from exc

        try:
            connection.sendall(b"zINSTREAM\0")
            for chunk in chunks:
                if not chunk:
                    continue
                connection.sendall(struct.pack(">I", len(chunk)) + chunk)
            connection.sendall(struct.pack(">I", 0))
            reply = b""
            while not reply.endswith(b"\0"):
                received = connection.recv(4096)
                if not received:
                    break
                reply += received
        except OSError as exc:
            raise ScannerUnavailable(
                f"The antivirus engine stopped answering part-way through ({exc}). "
                "The upload stays pending."
            ) from exc
        finally:
            try:
                connection.close()
            except OSError:
                pass

        text = reply.rstrip(b"\0").decode("utf-8", errors="replace").strip()
        if text.endswith("OK"):
            return Verdict(scanner=self.name, clean=True)
        if text.endswith("FOUND"):
            signature = text.split(":", 1)[-1].rsplit("FOUND", 1)[0].strip()
            return Verdict(
                scanner=self.name,
                clean=False,
                finding=f"The antivirus engine identified {signature}.",
            )
        raise ScannerUnavailable(
            f"The antivirus engine gave an answer CASS does not recognise ({text!r}), "
            "so the upload stays pending rather than being released on it."
        )


def configured_scanner() -> ClamdScanner | None:
    """The antivirus engine this installation names, or nothing."""
    host = getattr(settings, "CASS_CLAMD_HOST", "")
    if not host:
        return None
    return ClamdScanner(host, int(getattr(settings, "CASS_CLAMD_PORT", 3310)))


def _safe_name(filename: str) -> str:
    stem = _UNSAFE_NAME.sub("_", (filename or "upload").strip()).strip("._-")
    return (stem or "upload")[:120]


def issue_session(project, *, filename: str, content_type: str, actor):
    """Open an upload into a key this service chooses, under the project's prefix.

    Returns the pending artifact and the session. The session's expiry is kept
    on the artifact, so an upload nobody completes can be expired rather than
    left pending for ever.
    """
    if not project.may_write(actor):
        raise IntakeError("You may not upload into this project.")

    key = f"{project.artifact_prefix}/uploads/{uuid.uuid4().hex}/{_safe_name(filename)}"
    store = get_store()
    session = store.create_upload_session(
        bucket("upload"),
        key,
        content_type=content_type,
        expires_in=int(getattr(settings, "CASS_UPLOAD_SESSION_SECONDS", 900)),
    )
    artifact = Artifact.objects.create(
        uri=session.uri,
        content_type=content_type,
        retention=str(RetentionClass.PORTFOLIO),
        access=str(AccessPolicy.PROJECT),
        state=ArtifactState.PENDING,
        project=project,
        role="upload",
        original_filename=(filename or "")[:255],
        expires_at=session.expires_at,
        created_by=actor,
        updated_by=actor,
    )
    return artifact, session


def _read_back(uri: str) -> tuple[str, int, bytes]:
    """Digest, size and the first bytes of what actually arrived."""
    import hashlib

    from cass_core.checksums import DIGEST_ALGORITHM

    digest = hashlib.sha256()
    size = 0
    head = b""
    with get_store().open(uri) as handle:
        while True:
            chunk = handle.read(CHUNK_BYTES)
            if not chunk:
                break
            if len(head) < HEAD_BYTES:
                head += chunk[: HEAD_BYTES - len(head)]
            digest.update(chunk)
            size += len(chunk)
    return f"{DIGEST_ALGORITHM}:{digest.hexdigest()}", size, head


def _chunks(uri: str) -> Iterable[bytes]:
    with get_store().open(uri) as handle:
        while True:
            chunk = handle.read(CHUNK_BYTES)
            if not chunk:
                return
            yield chunk


def complete(
    artifact: Artifact,
    *,
    declared_checksum: str = "",
    declared_size: int | None = None,
    actor=None,
    scanner: ClamdScanner | None = None,
) -> Artifact:
    """Register what arrived, or quarantine it, and say which and why.

    ``scanner`` defaults to the installation's configured engine; passing one
    is how a test reaches the protocol without an engine.
    """
    if artifact.state != ArtifactState.PENDING:
        raise IntakeError(
            f"This upload is {artifact.get_state_display().lower()}, so there is "
            "nothing to complete."
        )
    if artifact.expires_at is not None and artifact.expires_at < timezone.now():
        raise IntakeError(
            "The upload session expired before it was completed. Start a new upload; "
            "what arrived under the old session will be removed."
        )

    store = get_store()
    if not store.exists(artifact.uri):
        raise IntakeError(
            "Nothing has arrived for this upload yet. Send the file to the session "
            "URL first, then complete it."
        )

    checksum, size, head = _read_back(artifact.uri)
    verdicts: list[Verdict] = []
    refusal = ""

    limit = int(getattr(settings, "CASS_MAX_UPLOAD_BYTES", 0) or 0)
    if limit and size > limit:
        refusal = f"The file is {size} bytes, above this installation's {limit} byte limit."
    elif declared_checksum and declared_checksum != checksum:
        refusal = (
            f"The file arrived as {checksum} and the uploader sent {declared_checksum}. "
            "What arrived is not the file that left, so it is not registered."
        )
    elif declared_size is not None and int(declared_size) != size:
        refusal = (
            f"The file arrived as {size} bytes and the uploader sent {declared_size}. "
            "A truncated upload is not the file anybody meant."
        )
    else:
        verdicts.append(content_verdict(head, content_type=artifact.content_type))
        engine = scanner if scanner is not None else configured_scanner()
        if engine is not None and verdicts[-1].clean:
            verdicts.append(engine.scan(_chunks(artifact.uri)))
        failed = [item for item in verdicts if not item.clean]
        if failed:
            refusal = " ".join(item.finding for item in failed)

    scanned_by = [item.scanner for item in verdicts]
    antivirus = any(item.scanner == ClamdScanner.name for item in verdicts)
    artifact.checksum = checksum
    artifact.size_bytes = size
    artifact.updated_by = actor

    if refusal:
        artifact.state = ArtifactState.QUARANTINED
        artifact.validation_note = refusal
        artifact.save()
        audit.record(
            action=AuditAction.REJECT,
            subject_type="artifact",
            subject_id=artifact.id,
            actor=actor,
            project=artifact.project,
            subject_label=str(artifact),
            after={"state": artifact.state, "checksum": checksum, "reason": refusal[:200]},
        )
        return artifact

    artifact.state = ArtifactState.REGISTERED
    # The session's deadline stops mattering once the object is registered; the
    # retention class decides from here, and save() fills that in.
    artifact.expires_at = None
    artifact.validation_note = (
        f"Checksummed on arrival and scanned by {', '.join(scanned_by)}."
        + (
            ""
            if antivirus
            else " No antivirus engine is configured for this installation, so no "
            "antivirus scan was made."
        )
    )
    artifact.save()
    audit.record(
        action=AuditAction.UPLOAD,
        subject_type="artifact",
        subject_id=artifact.id,
        actor=actor,
        project=artifact.project,
        subject_label=str(artifact),
        after={"state": artifact.state, "checksum": checksum, "scanned_by": scanned_by},
    )
    return artifact
