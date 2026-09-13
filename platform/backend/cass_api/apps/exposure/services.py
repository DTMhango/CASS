"""Exposure import, validation and publication.

This implements the first four gates of the section 8 input-generation
workflow: capture or import, validate, enrich, then publish immutable OED
artifacts. The enrichment step is a separate service; what lives here is the
path from an uploaded file to a frozen exposure version that a run may use.

The rule that shapes the code: raw exposure is immutable. An uploaded file is
registered as an artifact and never rewritten. Validation produces findings
about it; publication freezes the version that points at it.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from django.db import transaction

from apps.artifacts.models import Artifact, ArtifactLink, ArtifactState
from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.storage import bucket, get_store
from cass_core.artifacts import AccessPolicy, RetentionClass
from cass_oed.perspectives import available_perspectives
from cass_oed.reader import read_binary
from cass_oed.schema import OED_SCHEMA_VERSION, FileKind
from cass_oed.validation import PortfolioFiles, validate

from .models import ExposureState, ExposureVersion

#: Which OED file each artifact role carries.
ROLE_BY_KIND: Mapping[FileKind, str] = {
    FileKind.LOCATION: "oed_location",
    FileKind.ACCOUNT: "oed_account",
    FileKind.REINS_INFO: "oed_reins_info",
    FileKind.REINS_SCOPE: "oed_reins_scope",
}
KIND_BY_ROLE = {role: kind for kind, role in ROLE_BY_KIND.items()}


class ExposureError(Exception):
    """Raised when an exposure operation is not permitted in the current state."""


@transaction.atomic
def attach_file(
    version: ExposureVersion,
    kind: FileKind,
    payload: bytes,
    *,
    filename: str,
    actor=None,
    request=None,
) -> Artifact:
    """Register one uploaded OED file against a draft exposure version.

    Section 5 requires Django to record the artifact only after checksum and
    content validation complete, so the bytes are written to the store and
    digested before any database row claims they exist.
    """
    if version.is_frozen:
        raise ExposureError(
            "This exposure version is published. Create a new version to change it."
        )

    kind = FileKind(kind)
    role = ROLE_BY_KIND[kind]
    key = f"{version.artifact_prefix}/{role}.csv"

    # The same content check a direct upload gets. Two routes into the store
    # with two standards would make the weaker one the route that matters.
    from apps.artifacts.intake import HEAD_BYTES, content_verdict

    verdict = content_verdict(payload[:HEAD_BYTES], content_type="text/csv")
    if not verdict.clean:
        raise ExposureError(verdict.finding)

    store = get_store()
    ref = store.put_bytes(
        bucket("portfolio"),
        key,
        payload,
        content_type="text/csv",
        retention=RetentionClass.PORTFOLIO,
        access=AccessPolicy.PROJECT,
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
            "project": version.project,
            "role": role,
            "original_filename": filename[:255],
            "created_by": actor,
            "updated_by": actor,
        },
    )
    ArtifactLink.objects.update_or_create(
        artifact=artifact,
        subject_type="exposure_version",
        subject_id=version.id,
        role=role,
        direction="input",
        defaults={"created_by": actor},
    )

    audit.record(
        action=AuditAction.UPLOAD,
        subject_type="exposure_version",
        subject_id=version.id,
        actor=actor,
        project=version.project,
        subject_label=str(version),
        after={"role": role, "uri": ref.uri, "checksum": ref.checksum},
        request=request,
    )
    return artifact


def load_files(version: ExposureVersion) -> PortfolioFiles:
    """Read the OED artifacts attached to a version.

    Reads through the artifact interface rather than a path, so this behaves
    identically on a workstation and against S3.
    """
    store = get_store()
    links = ArtifactLink.objects.filter(
        subject_type="exposure_version", subject_id=version.id, direction="input"
    ).select_related("artifact")

    parsed: dict[str, object] = {}
    for link in links:
        kind = KIND_BY_ROLE.get(link.role)
        if kind is None or not link.artifact.is_readable:
            continue
        with store.open(link.artifact.uri) as handle:
            parsed[kind.value] = read_binary(kind, handle)

    if FileKind.LOCATION.value not in parsed:
        raise ExposureError(
            "No location file is attached. A location file is required for any analysis."
        )

    return PortfolioFiles(
        location=parsed[FileKind.LOCATION.value],
        account=parsed.get(FileKind.ACCOUNT.value),
        reins_info=parsed.get(FileKind.REINS_INFO.value),
        reins_scope=parsed.get(FileKind.REINS_SCOPE.value),
    )


@transaction.atomic
def run_validation(version: ExposureVersion, *, actor=None, request=None) -> ExposureVersion:
    """Validate the attached files and record the report on the version."""
    if version.is_frozen:
        raise ExposureError("A published exposure version cannot be revalidated in place.")

    files = load_files(version)
    report = validate(files)

    # VALIDATED means validation has run, not that the result was clean.
    # Whether the version may be published is answered by the findings.
    version.state = ExposureState.VALIDATED
    version.oed_schema_version = OED_SCHEMA_VERSION
    version.location_count = report.location_count
    version.account_count = report.account_count
    version.total_tiv = report.total_tiv
    version.tiv_by_coverage = {k: str(v) for k, v in report.tiv_by_coverage.items()}
    version.tiv_by_country = {k: str(v) for k, v in report.tiv_by_country.items()}
    version.tiv_by_currency = {k: str(v) for k, v in report.tiv_by_currency.items()}
    version.unmodelled_subperils = list(report.unmodelled_subperils)
    version.validation_report = report.as_dict()
    version.supported_perspectives = [
        item.as_dict() for item in available_perspectives(files)
    ]
    if len(report.currencies) == 1 and not version.run_currency:
        version.run_currency = report.currencies[0]
    version.updated_by = actor
    version.save()

    audit.record(
        action=AuditAction.UPDATE,
        subject_type="exposure_version",
        subject_id=version.id,
        actor=actor,
        project=version.project,
        subject_label=str(version),
        after={
            "state": version.state,
            "publishable": report.publishable,
            "errors": len(report.findings.errors),
            "warnings": len(report.findings.warnings),
        },
        detail="Validation run",
        request=request,
    )
    return version


@transaction.atomic
def publish(version: ExposureVersion, *, actor=None, request=None) -> ExposureVersion:
    """Freeze the version so runs may use it.

    Publication is refused while a blocking finding stands. Section 8 allows a
    user to correct the business record and publish a new version; it does not
    allow publishing over an error.
    """
    if version.is_frozen:
        return version
    if version.state != ExposureState.VALIDATED:
        raise ExposureError(
            "Validate the exposure version before publishing it."
        )
    if not version.is_publishable:
        errors = (version.validation_report or {}).get("validation", {}).get("error_count", 0)
        raise ExposureError(
            f"{errors} blocking validation finding(s) must be resolved before publication."
        )

    version.state = ExposureState.PUBLISHED
    version.updated_by = actor
    version.save()
    version.freeze()

    audit.record(
        action=AuditAction.PUBLISH,
        subject_type="exposure_version",
        subject_id=version.id,
        actor=actor,
        project=version.project,
        subject_label=str(version),
        after={
            "state": version.state,
            "total_tiv": str(version.total_tiv),
            "location_count": version.location_count,
        },
        request=request,
    )
    return version


def next_version_number(project, name: str) -> int:
    """The next version number for a portfolio name within a project."""
    latest = (
        ExposureVersion.objects.filter(project=project, name=name)
        .order_by("-version")
        .values_list("version", flat=True)
        .first()
    )
    return (latest or 0) + 1


def tiv_total(version: ExposureVersion) -> Decimal:
    return Decimal(version.total_tiv or 0)
