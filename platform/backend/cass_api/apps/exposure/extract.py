"""Importing the Klapton Re geocoded policy extract.

Work package 1 of the integration brief. The order matters and is the order
below: register the source before reading it, parse both sheets independently,
then join-report and assign cohorts over what was parsed.

Registering first is the part that is easy to get backwards. A workbook that
cannot be read is still evidence of what was supplied, and a source registered
only on success means the one import anybody needs to investigate is the one
nothing was kept for.

Nothing here parses. ``cass_extract`` owns the contract, the coercion, the
cohort rules and the join; this module supplies bytes, writes rows and records
versions. That split is what lets the parsing rules be tested without a
database and the storage rules without a workbook.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone

import cass_extract as extract
from apps.artifacts.models import Artifact, ArtifactLink, ArtifactState
from apps.audit import services as audit
from apps.audit.models import AuditAction
from apps.common.storage import bucket, get_store
from cass_core.artifacts import AccessPolicy, RetentionClass

from .models import (
    ImportBatch,
    ImportState,
    ReviewState,
    SourcePolicyRow,
    SourceRiskLocation,
)

#: The role the raw workbook is registered under.
SOURCE_ROLE = "portfolio_extract_source"

#: Namespace for deterministic location identifiers. A fixed UUID, so the same
#: location in the same source resolves to the same identifier on every read,
#: in every project and every installation.
LOCATION_NAMESPACE = uuid.UUID("6f5b3f2c-1a44-4f0e-9a1d-0c9a4d6e5b71")


def location_identity(checksum: str, business_id: str, location_number: int) -> uuid.UUID:
    """The deterministic internal identifier section 4.4 asks for."""
    return uuid.uuid5(LOCATION_NAMESPACE, f"{checksum}:{business_id}:{location_number}")


class ExtractImportError(Exception):
    """Raised when an extract cannot be imported at all."""


@transaction.atomic
def register_source(
    project,
    payload: bytes,
    *,
    filename: str,
    actor=None,
    request=None,
) -> Artifact:
    """Store the workbook as an immutable, restricted source artifact.

    ``PORTFOLIO`` retention and ``PROJECT`` access, because this is a live book
    of business: section 4.1 of the brief keeps it out of Git, out of container
    images and out of browser bundles, and restricts download to project
    members with portfolio-data permission.
    """
    store = get_store()
    ref = store.put_bytes(
        bucket("portfolio"),
        f"{project.artifact_prefix}/extract/{filename}",
        payload,
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
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
            "project": project,
            "role": SOURCE_ROLE,
            "original_filename": filename[:255],
            "created_by": actor,
            "updated_by": actor,
        },
    )
    audit.record(
        action=AuditAction.UPLOAD,
        subject_type="project",
        subject_id=project.id,
        actor=actor,
        project=project,
        subject_label=str(project),
        # The filename and checksum, never a row of it.
        after={"role": SOURCE_ROLE, "uri": ref.uri, "checksum": ref.checksum},
        detail="Registered a confidential portfolio extract.",
        request=request,
    )
    return artifact


def import_extract(
    project,
    payload: bytes,
    *,
    filename: str,
    snapshot_date: dt.date | None = None,
    actor=None,
    request=None,
) -> ImportBatch:
    """Register, parse and profile one extract workbook.

    Re-importing the same bytes into the same project returns the batch that
    already exists. The brief requires a rerun of the same checksum to be
    idempotent, and a second batch with identical counts would be worse than
    useless: a reader would have to work out which of two identical reads the
    downstream work was based on.
    """
    artifact = register_source(
        project, payload, filename=filename, actor=actor, request=request
    )

    existing = ImportBatch.objects.filter(
        project=project, source_checksum=artifact.checksum
    ).first()
    if existing is not None:
        return existing

    try:
        read = extract.read_workbook(_as_stream(payload))
    except extract.ExtractReadError as exc:
        # The source is registered; the batch records that it could not be read.
        return _rejected(project, artifact, filename, snapshot_date, str(exc), actor)

    policies = [row.values for row in read.policies]
    locations = [row.values for row in read.locations]

    report = extract.build_join_report(policies, locations)
    assignments = extract.assign_all(locations)
    cohort_profile = extract.profile(locations, assignments)

    with transaction.atomic():
        batch = ImportBatch.objects.create(
            project=project,
            profile=extract.PROFILE_NAME,
            source_artifact=artifact,
            source_filename=filename[:255],
            source_checksum=artifact.checksum,
            snapshot_date=snapshot_date,
            schema_version=extract.SCHEMA_VERSION,
            parser_version=extract.PARSER_VERSION,
            cohort_rule_version=extract.COHORT_RULE_VERSION,
            join_rule_version=extract.JOIN_RULE_VERSION,
            state=ImportState.PARSED,
            policy_row_count=len(read.policies),
            location_row_count=len(read.locations),
            findings=[finding.as_dict() for finding in read.findings],
            join_report=report.as_dict(),
            cohort_profile=cohort_profile.as_dict(),
            created_by=actor,
            updated_by=actor,
        )
        ArtifactLink.objects.update_or_create(
            artifact=artifact,
            subject_type="import_batch",
            subject_id=batch.id,
            role=SOURCE_ROLE,
            direction="input",
            defaults={"created_by": actor},
        )
        _stage_policies(batch, read.policies)
        _stage_locations(batch, read.locations, assignments)

    audit.record(
        action=AuditAction.CREATE,
        subject_type="import_batch",
        subject_id=batch.id,
        actor=actor,
        project=project,
        subject_label=str(batch),
        after={
            "checksum": batch.source_checksum,
            "schema_version": batch.schema_version,
            "parser_version": batch.parser_version,
            "policy_rows": batch.policy_row_count,
            "location_rows": batch.location_row_count,
            "blocking": batch.blocking,
        },
        request=request,
    )
    return batch


def _rejected(project, artifact, filename, snapshot_date, reason, actor) -> ImportBatch:
    return ImportBatch.objects.create(
        project=project,
        profile=extract.PROFILE_NAME,
        source_artifact=artifact,
        source_filename=filename[:255],
        source_checksum=artifact.checksum,
        snapshot_date=snapshot_date,
        schema_version=extract.SCHEMA_VERSION,
        parser_version=extract.PARSER_VERSION,
        state=ImportState.REJECTED,
        rejection_reason=reason,
        created_by=actor,
        updated_by=actor,
    )


def _stage_policies(batch: ImportBatch, rows) -> None:
    SourcePolicyRow.objects.bulk_create(
        [
            SourcePolicyRow(
                batch=batch,
                row_number=row.row_number,
                policy_id=str(row.get("policy_id") or "")[:64],
                business_id=str(row.get("business_id") or "")[:64],
                gross_limit=row.get("gross_limit"),
                risk_location_count=row.get("risk_location_count"),
                class_of_business=str(row.get("main_class_of_business") or "")[:120],
                insured_country=str(row.get("insured_country") or "")[:120],
                values=_jsonable(row.values),
                raw=dict(row.raw),
                created_by=batch.created_by,
            )
            for row in rows
        ],
        batch_size=500,
    )


def _stage_locations(batch: ImportBatch, rows, assignments) -> None:
    SourceRiskLocation.objects.bulk_create(
        [
            SourceRiskLocation(
                batch=batch,
                row_number=row.row_number,
                business_id=str(row.get("business_id") or "")[:64],
                location_number=int(row.get("location_number") or 0),
                cass_location_id=location_identity(
                    batch.source_checksum,
                    str(row.get("business_id") or ""),
                    int(row.get("location_number") or 0),
                ),
                primary_location=bool(row.get("primary_location")),
                latitude=row.get("latitude"),
                longitude=row.get("longitude"),
                precision=str(row.get("precision") or "")[:32],
                needs_review=bool(row.get("needs_review")),
                class_of_business=str(row.get("class_of_business") or "")[:120],
                country=str(row.get("country") or "")[:120],
                country_code=extract.country_code(str(row.get("country") or "")),
                cohort=str(assignment.cohort),
                cohort_reason=assignment.reason[:200],
                cohort_rule_version=assignment.rule_version,
                # The backlog cohort and anything the rules could not place.
                # Marking every row "pending" would make the queue meaningless,
                # but an unclassified row is precisely work someone owes.
                review_state=(
                    ReviewState.PENDING
                    if assignment.cohort
                    in (extract.Cohort.C, extract.Cohort.UNCLASSIFIED)
                    else ReviewState.NOT_REQUIRED
                ),
                values=_jsonable(row.values),
                raw=dict(row.raw),
                created_by=batch.created_by,
            )
            for row, assignment in zip(rows, assignments, strict=True)
        ],
        batch_size=500,
    )


# -- acceptance ---------------------------------------------------------------

@transaction.atomic
def accept(batch: ImportBatch, *, actor=None, request=None) -> ImportBatch:
    """Record that a person has reviewed the join report and allowed the batch on."""
    if batch.state != ImportState.PARSED:
        raise ExtractImportError(
            f"A batch in state {batch.state} cannot be accepted."
        )
    if batch.blocking:
        raise ExtractImportError(
            "A blocking join finding stands. Correct the source and import again; "
            "the brief does not allow a fan-out or a lost location to be approved away."
        )

    batch.state = ImportState.ACCEPTED
    batch.accepted_at = timezone.now()
    batch.accepted_by = actor
    batch.updated_by = actor
    batch.save(
        update_fields=["state", "accepted_at", "accepted_by", "updated_by", "updated_at"]
    )

    audit.record(
        action=AuditAction.APPROVE,
        subject_type="import_batch",
        subject_id=batch.id,
        actor=actor,
        project=batch.project,
        subject_label=str(batch),
        after={"state": batch.state},
        request=request,
    )
    return batch


# -- the transformation manifest ----------------------------------------------

def transformation_manifest(
    batch: ImportBatch, *, include_confidential: bool = False
) -> dict[str, Any]:
    """What was read, under which rules, and what it produced.

    Downloadable, and without insured names by default. The default is the
    important half: a manifest is the artefact most likely to be attached to a
    ticket or an email, and section 10 restricts counterparty names to the
    roles that need them. Asking for them is a decision someone makes; getting
    them is not something that should happen by omission.
    """
    locations = list(batch.location_rows.all())
    policies_by_business: dict[str, list[SourcePolicyRow]] = {}
    for policy in batch.policy_rows.all():
        policies_by_business.setdefault(policy.business_id, []).append(policy)

    cohort_tiv: dict[str, str] = {}
    for cohort, businesses in _businesses_by_cohort(locations).items():
        total = Decimal(0)
        for business_id in businesses:
            for policy in policies_by_business.get(business_id, ()):
                total += policy.gross_limit or Decimal(0)
        cohort_tiv[cohort] = str(total)

    manifest: dict[str, Any] = {
        "profile": batch.profile,
        "state": batch.state,
        "source": {
            "filename": batch.source_filename,
            "checksum": batch.source_checksum,
            "snapshot_date": (
                batch.snapshot_date.isoformat() if batch.snapshot_date else None
            ),
            "uri": batch.source_artifact.uri if batch.source_artifact_id else None,
        },
        "versions": {
            "schema": batch.schema_version,
            "parser": batch.parser_version,
            "cohort_rules": batch.cohort_rule_version,
            "join_rules": batch.join_rule_version,
        },
        "counts": {
            "policy_rows": batch.policy_row_count,
            "location_rows": batch.location_row_count,
            "parse_findings": len(batch.findings or []),
        },
        "join_report": batch.join_report,
        "cohorts": batch.cohort_profile,
        "cohort_tiv_at_kre_share_usd": cohort_tiv,
        "review_queue": _review_queue(locations),
        "value_basis": (
            "gross_limit is reported TIV at KRE's share, in USD. The share is not "
            "applied again during loss calculation, so a physical-damage result is "
            "KRE-share gross damage rather than 100%-of-risk ground-up loss."
        ),
        "confidential_columns_included": include_confidential,
    }

    if not include_confidential:
        manifest["confidential_columns_withheld"] = sorted(
            extract.CONFIDENTIAL_COLUMNS
        )
    return manifest


def _businesses_by_cohort(locations) -> dict[str, set[str]]:
    """Businesses whose *whole* schedule sits in one cohort.

    A business with sites in two cohorts belongs to neither for the purpose of
    a value total: attributing its policy TIV to one of them would count value
    that the other cohort's sites also carry.
    """
    schedules: dict[str, set[str]] = {}
    for location in locations:
        schedules.setdefault(location.business_id, set()).add(location.cohort)
    grouped: dict[str, set[str]] = {}
    for business_id, cohorts in schedules.items():
        if len(cohorts) == 1:
            grouped.setdefault(next(iter(cohorts)), set()).add(business_id)
        else:
            grouped.setdefault("mixed", set()).add(business_id)
    return grouped


def _review_queue(locations) -> dict[str, Any]:
    pending = [item for item in locations if item.review_state == ReviewState.PENDING]
    by_country: dict[str, int] = {}
    for item in pending:
        by_country[item.country or "unknown"] = by_country.get(item.country or "unknown", 0) + 1
    return {
        "pending": len(pending),
        "by_country": by_country,
        "reason": (
            "Locations a reviewer has flagged. They stay out of the automated "
            "benchmark until reviewed or explicitly approved for a stated "
            "sensitivity run."
        ),
    }


# -- helpers -------------------------------------------------------------------

def _as_stream(payload: bytes):
    import io

    return io.BytesIO(payload)


def _jsonable(values: Mapping[str, Any]) -> dict[str, Any]:
    """Make a parsed row storable as JSON without losing decimal exactness."""
    out: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, Decimal):
            out[key] = format(value, "f")
        elif isinstance(value, dt.date):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out
