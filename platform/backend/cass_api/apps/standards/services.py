"""Registering a specification as an immutable artifact CASS can read later."""

from __future__ import annotations

import pathlib

from django.db import transaction

from apps.artifacts.models import Artifact, ArtifactLink, ArtifactState
from apps.common.storage import bucket, get_store
from cass_core.artifacts import AccessPolicy, RetentionClass

from . import registry
from .models import DataStandardVersion, StandardState


class StandardRegistrationError(Exception):
    """Raised when a specification cannot be registered."""


@transaction.atomic
def register(
    payload: bytes,
    *,
    version: str,
    source: str,
    standard: str = "OED",
    notes: str = "",
    actor=None,
) -> DataStandardVersion:
    """Register one published specification.

    Idempotent by (standard, version): registering the same release twice
    replaces the stored file and leaves the record, so two registrations cannot
    produce two histories of the same version.
    """
    specification = registry.read_specification(payload)

    store = get_store()
    ref = store.put_bytes(
        bucket("model"),
        f"data_standard/{standard.lower()}/{version}/specification.json",
        payload,
        content_type="application/json",
        retention=RetentionClass.MODEL_ASSET,
        access=AccessPolicy.MODEL,
    )
    artifact, _ = Artifact.objects.update_or_create(
        uri=ref.uri,
        defaults={
            "checksum": ref.checksum,
            "size_bytes": ref.size_bytes,
            "content_type": ref.content_type,
            "retention": str(ref.retention),
            "access": str(ref.access),
            "state": ArtifactState.REGISTERED,
            "project": None,
            "role": "data_standard_specification",
            "original_filename": f"{standard}_{version}.json",
            "created_by": actor,
            "updated_by": actor,
        },
    )

    record, _ = DataStandardVersion.objects.update_or_create(
        standard=standard,
        version=version,
        defaults={
            "source": source,
            "reference_uri": ref.uri,
            "checksum": ref.checksum,
            "file_kinds": registry.summarise(specification),
            "field_count": sum(len(fields) for fields in specification.values()),
            "notes": notes,
            "updated_by": actor,
        },
    )
    ArtifactLink.objects.update_or_create(
        artifact=artifact,
        subject_type="data_standard_version",
        subject_id=record.id,
        role="specification",
        direction="input",
        defaults={"created_by": actor},
    )
    return record


def specification_of(record: DataStandardVersion):
    """Read back the specification this record was registered from."""
    if not record.reference_uri:
        raise StandardRegistrationError(
            f"{record} has no registered specification, so nothing can be read from it."
        )
    with get_store().open(record.reference_uri) as handle:
        return registry.read_specification(handle.read())


def register_from_path(path: str | pathlib.Path, **kwargs) -> DataStandardVersion:
    """Register a specification file from disk, such as a pinned ODS Tools one."""
    location = pathlib.Path(path)
    if not location.is_file():
        raise StandardRegistrationError(f"{location} is not a file CASS can read.")
    return register(location.read_bytes(), **kwargs)


def adopt(record: DataStandardVersion, *, actor=None) -> DataStandardVersion:
    """Make one version the one exposure is read against.

    Exactly one version of a standard is active at a time. Two would leave the
    question of which one a portfolio was validated under unanswerable, which is
    the question the registry exists to answer.
    """
    from django.utils import timezone

    superseded = DataStandardVersion.objects.filter(
        standard=record.standard, state=StandardState.ACTIVE
    ).exclude(pk=record.pk)
    superseded.update(state=StandardState.SUPERSEDED)

    record.state = StandardState.ACTIVE
    record.adopted_at = timezone.now()
    record.adopted_by = actor
    record.updated_by = actor
    record.save()
    return record
