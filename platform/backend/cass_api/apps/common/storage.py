"""Access to the artifact store from the control plane."""

from __future__ import annotations

from functools import lru_cache

from django.conf import settings

from cass_core.artifacts import (
    ArtifactStore,
    FilesystemArtifactStore,
    S3ArtifactStore,
)


@lru_cache(maxsize=1)
def get_store() -> ArtifactStore:
    """Return the configured artifact store.

    Cached because the store holds a client, not state: the same instance is
    safe to share across requests and tasks.
    """
    backend = settings.CASS_ARTIFACT_BACKEND.lower()
    if backend == "filesystem":
        return FilesystemArtifactStore(settings.CASS_ARTIFACT_ROOT)
    if backend == "s3":
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url=settings.CASS_S3_ENDPOINT or None,
            region_name=settings.CASS_S3_REGION,
        )
        return S3ArtifactStore(client)
    raise ValueError(f"unknown artifact backend {backend!r}")


def bucket(purpose: str) -> str:
    """Resolve a bucket by purpose, such as portfolio or model."""
    try:
        return settings.CASS_BUCKETS[purpose]
    except KeyError as exc:
        raise KeyError(f"no bucket configured for {purpose!r}") from exc


def reset_store_cache() -> None:
    """Clear the cached store. Used by tests that repoint the root."""
    get_store.cache_clear()
