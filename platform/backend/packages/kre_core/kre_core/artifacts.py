"""The artifact store boundary.

Section 4 of the build plan states that Django holds references to large
artifacts rather than the arrays themselves, and section 5 states that workers
read and write through the artifact interface so Windows host paths never
appear in a calculation contract. This module is that interface.

Two backends are provided. ``FilesystemArtifactStore`` is the first-workstation
deployment; ``S3ArtifactStore`` is the managed deployment. They expose the same
operations so moving between them is a configuration change, not an
architectural one.

Every stored object is addressed by a ``kre://`` URI and carries a content
checksum, a byte count, a content type and a retention class.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import io
import os
import re
import shutil
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol

from .checksums import hash_file, hash_stream

URI_SCHEME = "kre"
_URI_PATTERN = re.compile(r"^kre://(?P<bucket>[a-z0-9][a-z0-9.\-]{1,62})/(?P<key>.+)$")
# Keys are deliberately restrictive: no drive letters, no backslashes, no
# traversal. This is what stops a host path leaking into a calculation contract.
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.\-=]{0,1022}$")


class RetentionClass(enum.StrEnum):
    """Artifact lifecycle classes from build plan section 5.

    The class, not a manual delete, decides how long an object survives.
    """

    PERMANENT = "permanent"
    """Source inputs and manifests for published model versions."""

    MODEL_ASSET = "model_asset"
    """Accepted Oasis model packages; immutable versioned releases."""

    HAZARD_INTERMEDIATE = "hazard_intermediate"
    """OpenQuake HDF5 GMF; expires after footprint acceptance unless governed otherwise."""

    PORTFOLIO = "portfolio"
    """Portfolio input versions under business retention policy."""

    RESULT = "result"
    """Loss result packages under business and regulatory policy."""

    DIAGNOSTIC = "diagnostic"
    """Short-lived CSV and inspection output."""

    OPERATIONAL = "operational"
    """Logs and metrics, tiered by usefulness."""


class AccessPolicy(enum.StrEnum):
    """Who may retrieve an object once they hold its URI.

    Section 10 requires per-project authorization on artifact retrieval: a
    guessed object key must never grant access. Every read therefore passes
    through the project authorization check in the API; this field records the
    intended audience so that check has something to enforce.
    """

    PROJECT = "project"
    """Readable only by members of the owning project."""

    MODEL = "model"
    """Readable by any user entitled to the owning model version."""

    PLATFORM = "platform"
    """Readable by platform administrators only."""


class ArtifactStoreError(Exception):
    """Base class for artifact store failures."""


class ArtifactNotFound(ArtifactStoreError):
    """Raised when a URI does not resolve to a stored object."""


class InvalidArtifactURI(ArtifactStoreError):
    """Raised when a URI is malformed or would escape the store root."""


@dataclasses.dataclass(frozen=True, slots=True)
class ArtifactRef:
    """A reference to one stored object.

    This is the value that crosses service boundaries and is persisted in the
    control plane. It never contains a host path.
    """

    uri: str
    checksum: str
    size_bytes: int
    content_type: str
    retention: RetentionClass
    access: AccessPolicy
    created_at: dt.datetime

    @property
    def bucket(self) -> str:
        return parse_uri(self.uri)[0]

    @property
    def key(self) -> str:
        return parse_uri(self.uri)[1]

    def as_dict(self) -> dict[str, object]:
        return {
            "uri": self.uri,
            "checksum": self.checksum,
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
            "retention": str(self.retention),
            "access": str(self.access),
            "created_at": self.created_at.isoformat(),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class UploadSession:
    """A short-lived direct-to-store upload grant.

    Section 5 requires the browser to upload large files straight to object
    storage, with Django registering the artifact only after the checksum and
    content validation complete.
    """

    uri: str
    url: str
    method: str
    headers: dict[str, str]
    expires_at: dt.datetime


def build_uri(bucket: str, key: str) -> str:
    """Compose a validated ``kre://`` URI."""
    if not _KEY_PATTERN.match(key):
        raise InvalidArtifactURI(f"unsafe artifact key: {key!r}")
    if ".." in PurePosixPath(key).parts:
        raise InvalidArtifactURI(f"artifact key may not traverse: {key!r}")
    uri = f"{URI_SCHEME}://{bucket}/{key}"
    parse_uri(uri)
    return uri


def parse_uri(uri: str) -> tuple[str, str]:
    """Split a ``kre://`` URI into bucket and key, rejecting anything unsafe."""
    match = _URI_PATTERN.match(uri)
    if not match:
        raise InvalidArtifactURI(f"not a KRE artifact URI: {uri!r}")
    key = match.group("key")
    if not _KEY_PATTERN.match(key) or ".." in PurePosixPath(key).parts:
        raise InvalidArtifactURI(f"unsafe artifact key in URI: {uri!r}")
    return match.group("bucket"), key


class ArtifactStore(Protocol):
    """The operations every backend must provide."""

    def put_file(
        self,
        bucket: str,
        key: str,
        source: str | Path,
        *,
        content_type: str,
        retention: RetentionClass,
        access: AccessPolicy = AccessPolicy.PROJECT,
    ) -> ArtifactRef: ...

    def put_bytes(
        self,
        bucket: str,
        key: str,
        payload: bytes,
        *,
        content_type: str,
        retention: RetentionClass,
        access: AccessPolicy = AccessPolicy.PROJECT,
    ) -> ArtifactRef: ...

    def open(self, uri: str) -> BinaryIO: ...

    def download(self, uri: str, destination: str | Path) -> Path: ...

    def exists(self, uri: str) -> bool: ...

    def delete(self, uri: str) -> None: ...

    def list_keys(self, bucket: str, prefix: str = "") -> Iterator[str]: ...

    def create_upload_session(
        self, bucket: str, key: str, *, content_type: str, expires_in: int = 900
    ) -> UploadSession: ...


class FilesystemArtifactStore:
    """Filesystem-backed store for development and local installations.

    The object interface is preserved exactly, so a local deployment and a
    KRE-server deployment exercise the same code paths.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # -- internals ---------------------------------------------------------
    def _path(self, bucket: str, key: str) -> Path:
        build_uri(bucket, key)  # validation only
        bucket_root = (self.root / bucket).resolve()
        resolved = (bucket_root / key).resolve()
        if not resolved.is_relative_to(bucket_root):
            raise InvalidArtifactURI(f"key escapes bucket root: {key!r}")
        return resolved

    def _path_for_uri(self, uri: str) -> Path:
        bucket, key = parse_uri(uri)
        return self._path(bucket, key)

    def _ref(
        self,
        bucket: str,
        key: str,
        path: Path,
        content_type: str,
        retention: RetentionClass,
        access: AccessPolicy,
    ) -> ArtifactRef:
        return ArtifactRef(
            uri=build_uri(bucket, key),
            checksum=hash_file(path),
            size_bytes=path.stat().st_size,
            content_type=content_type,
            retention=retention,
            access=access,
            created_at=dt.datetime.now(dt.UTC),
        )

    # -- interface ---------------------------------------------------------
    def put_file(
        self,
        bucket: str,
        key: str,
        source: str | Path,
        *,
        content_type: str,
        retention: RetentionClass,
        access: AccessPolicy = AccessPolicy.PROJECT,
    ) -> ArtifactRef:
        target = self._path(bucket, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return self._ref(bucket, key, target, content_type, retention, access)

    def put_bytes(
        self,
        bucket: str,
        key: str,
        payload: bytes,
        *,
        content_type: str,
        retention: RetentionClass,
        access: AccessPolicy = AccessPolicy.PROJECT,
    ) -> ArtifactRef:
        target = self._path(bucket, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return self._ref(bucket, key, target, content_type, retention, access)

    def open(self, uri: str) -> BinaryIO:
        path = self._path_for_uri(uri)
        if not path.is_file():
            raise ArtifactNotFound(uri)
        return open(path, "rb")

    def download(self, uri: str, destination: str | Path) -> Path:
        path = self._path_for_uri(uri)
        if not path.is_file():
            raise ArtifactNotFound(uri)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        return destination

    def exists(self, uri: str) -> bool:
        return self._path_for_uri(uri).is_file()

    def delete(self, uri: str) -> None:
        path = self._path_for_uri(uri)
        if path.is_file():
            path.unlink()

    def list_keys(self, bucket: str, prefix: str = "") -> Iterator[str]:
        base = (self.root / bucket).resolve()
        if not base.is_dir():
            return
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            key = path.relative_to(base).as_posix()
            if key.startswith(prefix):
                yield key

    def create_upload_session(
        self, bucket: str, key: str, *, content_type: str, expires_in: int = 900
    ) -> UploadSession:
        """Return a local-mode upload grant.

        There is no signing authority on a filesystem store, so the API
        receives the body itself and registers the artifact afterwards. The
        session shape is identical to the S3 one so browser code does not have
        to branch on deployment mode.
        """
        uri = build_uri(bucket, key)
        return UploadSession(
            uri=uri,
            url=f"/api/v1/artifacts/upload/{bucket}/{key}",
            method="PUT",
            headers={"Content-Type": content_type},
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=expires_in),
        )

    def register_upload(
        self,
        uri: str,
        stream: BinaryIO,
        *,
        content_type: str,
        retention: RetentionClass,
        access: AccessPolicy = AccessPolicy.PROJECT,
    ) -> ArtifactRef:
        """Persist the body of a local-mode upload and return its reference."""
        bucket, key = parse_uri(uri)
        target = self._path(bucket, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as handle:
            shutil.copyfileobj(stream, handle, length=1024 * 1024)
        return self._ref(bucket, key, target, content_type, retention, access)


class S3ArtifactStore:
    """S3-compatible store for MinIO and managed deployments."""

    def __init__(self, client, *, url_expiry: int = 900) -> None:
        self._client = client
        self._url_expiry = url_expiry

    def _ref_from_head(
        self,
        bucket: str,
        key: str,
        checksum: str,
        content_type: str,
        retention: RetentionClass,
        access: AccessPolicy,
    ) -> ArtifactRef:
        head = self._client.head_object(Bucket=bucket, Key=key)
        return ArtifactRef(
            uri=build_uri(bucket, key),
            checksum=checksum,
            size_bytes=int(head["ContentLength"]),
            content_type=content_type,
            retention=retention,
            access=access,
            created_at=dt.datetime.now(dt.UTC),
        )

    def put_file(
        self,
        bucket: str,
        key: str,
        source: str | Path,
        *,
        content_type: str,
        retention: RetentionClass,
        access: AccessPolicy = AccessPolicy.PROJECT,
    ) -> ArtifactRef:
        build_uri(bucket, key)
        checksum = hash_file(source)
        with open(source, "rb") as handle:
            self._client.upload_fileobj(
                handle,
                bucket,
                key,
                ExtraArgs={
                    "ContentType": content_type,
                    "Metadata": {
                        "kre-checksum": checksum,
                        "kre-retention": str(retention),
                        "kre-access": str(access),
                    },
                },
            )
        return self._ref_from_head(bucket, key, checksum, content_type, retention, access)

    def put_bytes(
        self,
        bucket: str,
        key: str,
        payload: bytes,
        *,
        content_type: str,
        retention: RetentionClass,
        access: AccessPolicy = AccessPolicy.PROJECT,
    ) -> ArtifactRef:
        build_uri(bucket, key)
        checksum = hash_stream(io.BytesIO(payload))
        self._client.put_object(
            Bucket=bucket,
            Key=key,
            Body=payload,
            ContentType=content_type,
            Metadata={
                "kre-checksum": checksum,
                "kre-retention": str(retention),
                "kre-access": str(access),
            },
        )
        return ArtifactRef(
            uri=build_uri(bucket, key),
            checksum=checksum,
            size_bytes=len(payload),
            content_type=content_type,
            retention=retention,
            access=access,
            created_at=dt.datetime.now(dt.UTC),
        )

    def open(self, uri: str) -> BinaryIO:
        bucket, key = parse_uri(uri)
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
        except Exception as exc:  # pragma: no cover - backend specific
            raise ArtifactNotFound(uri) from exc
        return response["Body"]

    def download(self, uri: str, destination: str | Path) -> Path:
        bucket, key = parse_uri(uri)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(bucket, key, str(destination))
        return destination

    def exists(self, uri: str) -> bool:
        bucket, key = parse_uri(uri)
        try:
            self._client.head_object(Bucket=bucket, Key=key)
        except Exception:
            return False
        return True

    def delete(self, uri: str) -> None:
        bucket, key = parse_uri(uri)
        self._client.delete_object(Bucket=bucket, Key=key)

    def list_keys(self, bucket: str, prefix: str = "") -> Iterator[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                yield item["Key"]

    def create_upload_session(
        self, bucket: str, key: str, *, content_type: str, expires_in: int | None = None
    ) -> UploadSession:
        build_uri(bucket, key)
        expiry = expires_in or self._url_expiry
        url = self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expiry,
        )
        return UploadSession(
            uri=build_uri(bucket, key),
            url=url,
            method="PUT",
            headers={"Content-Type": content_type},
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=expiry),
        )


def store_from_env() -> ArtifactStore:
    """Build the store described by the environment.

    ``KRE_ARTIFACT_BACKEND`` selects ``filesystem`` (default) or ``s3``.
    """
    backend = os.environ.get("KRE_ARTIFACT_BACKEND", "filesystem").lower()
    if backend == "filesystem":
        return FilesystemArtifactStore(os.environ.get("KRE_ARTIFACT_ROOT", ".artifacts"))
    if backend == "s3":
        import boto3  # imported lazily so local installs need no AWS SDK

        client = boto3.client(
            "s3",
            endpoint_url=os.environ.get("KRE_S3_ENDPOINT") or None,
            region_name=os.environ.get("KRE_S3_REGION", "us-east-1"),
        )
        return S3ArtifactStore(client)
    raise ArtifactStoreError(f"unknown artifact backend: {backend!r}")
