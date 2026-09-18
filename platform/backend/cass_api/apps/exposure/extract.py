"""Importing a completed CASS intake template.

The order matters and is the order below: register the source before reading
it, read both sheets, cross-check the join the file states, then assign cohorts
over what was read.

Registering first is the part that is easy to get backwards. A workbook that
cannot be read is still evidence of what was supplied, and a source registered
only on success means the one import anybody needs to investigate is the one
nothing was kept for.

Nothing here parses. ``cass_extract`` owns the profile, the coercion and the
cohort rules; this module supplies bytes, writes rows and records versions.
That split is what lets the reading rules be tested without a database and the
storage rules without a workbook.

Rows are staged under canonical names rather than the template's own column
headings. A staged row is what the cohorts, the allocation and the promotion
read, and none of them should have to know what a spreadsheet column was
called -- which is also what lets a loading API populate these same tables
without a file existing at all.
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
from cass_extract import intake, profile
from cass_keys.land import CountryScreen

from .models import (
    ImportBatch,
    ImportState,
    ReviewState,
    SourcePolicyRow,
    SourceRiskLocation,
)

#: The role the raw workbook is registered under.
SOURCE_ROLE = "portfolio_extract_source"

#: What every coordinate is checked against: the country outlines CASS ships,
#: with the coastal allowance a grid clipped to land uses. See ADR 0025.
COUNTRY_SCREEN = CountryScreen()

#: The cohort rules whose answers become import findings, and the column each
#: points a person to.
SCREEN_FINDINGS = {
    "outside_country": ("coordinate_outside_country", "Latitude"),
    "unknown_country": ("unknown_country_code", "Country"),
}

#: Row numbers a screen finding lists before counting the rest.
SCREEN_ROWS_SHOWN = 10

#: Namespace for deterministic location identifiers. A fixed UUID, so the same
#: location in the same source resolves to the same identifier on every read,
#: in every project and every installation.
LOCATION_NAMESPACE = uuid.UUID("6f5b3f2c-1a44-4f0e-9a1d-0c9a4d6e5b71")


def location_identity(checksum: str, business_id: str, location_number: str) -> uuid.UUID:
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


def import_portfolio(
    project,
    payload: bytes,
    *,
    filename: str,
    snapshot_date: dt.date | None = None,
    actor=None,
    request=None,
) -> ImportBatch:
    """Register, read and profile one completed intake template.

    Re-importing the same bytes into the same project returns the batch that
    already exists, if the same rules read it. A rerun of the same checksum has
    to be idempotent, and a second batch with identical counts would be worse
    than useless: a reader would have to work out which of two identical reads
    the downstream work was based on. Under newer rules the read is not
    identical -- a country the old screen could not check is checked now -- so
    the file is read again into a new batch, and the old one stays as the record
    of what the old rules made of it.
    """
    artifact = register_source(
        project, payload, filename=filename, actor=actor, request=request
    )

    existing = ImportBatch.objects.filter(
        project=project,
        source_checksum=artifact.checksum,
        parser_version=intake.PARSER_VERSION,
        cohort_rule_version=extract.COHORT_RULE_VERSION,
    ).first()
    if existing is not None:
        return existing

    try:
        read = intake.read_workbook(_as_stream(payload))
    except extract.ExtractReadError as exc:
        # The source is registered; the batch records that it could not be read.
        return _rejected(project, artifact, filename, snapshot_date, str(exc), actor)

    risks, policies = intake.records(read)
    contracts, scope = intake.reinsurance_records(read)
    assignments = extract.assign_all(risks, screen=COUNTRY_SCREEN)
    cohort_profile = extract.cohort_profile(risks, assignments)
    programme = _programme(project, risks, policies, contracts, scope)
    findings = [
        *(finding.as_dict() for finding in read.findings),
        *_screen_findings(risks, assignments),
        *_programme_findings(programme),
    ]

    with transaction.atomic():
        batch = ImportBatch.objects.create(
            project=project,
            profile=profile.PROFILE_NAME,
            source_artifact=artifact,
            source_filename=filename[:255],
            source_checksum=artifact.checksum,
            snapshot_date=snapshot_date,
            schema_version=intake.PROFILE_VERSION,
            parser_version=intake.PARSER_VERSION,
            cohort_rule_version=extract.COHORT_RULE_VERSION,
            state=ImportState.PARSED,
            policy_row_count=len(read.policies.rows),
            risk_row_count=len(read.risks.rows),
            findings=findings,
            intake_report=_intake_report(read, findings, risks, policies, contracts, programme),
            reinsurance={
                "contracts": [_jsonable(record) for record in contracts],
                "scope": [_jsonable(record) for record in scope],
            },
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
        _stage_policies(batch, policies)
        _stage_risks(batch, risks, assignments)

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
            "risk_rows": batch.risk_row_count,
            "blocking": batch.blocking,
        },
        request=request,
    )
    return batch


#: The perils and currency the template route writes every term in. Promotion
#: records the same for every location and policy; the contracts follow them.
TEMPLATE_PERIL = "QEQ"
TEMPLATE_CURRENCY = "USD"


def _programme(project, risks, policies, contracts, scope):
    """The workbook's reinsurance under the Financial structure screen's rules.

    Checked against every risk and policy the workbook holds. A promotion
    checks it again against the selection it promotes, which can only be
    smaller, so anything refused here is refused there too.
    """
    from . import structure_builder

    locations = [
        {
            "PortNumber": project.reference,
            "AccNumber": str(record.get("business_id") or "").strip(),
            "LocNumber": str(record.get("location_number") or "").strip(),
            "LocCurrency": TEMPLATE_CURRENCY,
        }
        for record in risks
    ]
    policy_keys = {
        (str(record.get("business_id") or "").strip(), str(record.get("policy_id") or "").strip())
        for record in policies
    }
    return structure_builder.programme_rows(
        contracts,
        scope,
        locations=locations,
        policy_keys=policy_keys,
        perils=TEMPLATE_PERIL,
        currency=TEMPLATE_CURRENCY,
    )


def _screen_findings(risks, assignments) -> list[dict[str, Any]]:
    """One finding per country code whose risks the country screen could not place.

    Each row is staged either way, unclassified and in the review queue with
    its own reason. The finding is what puts them in front of a person at
    import, with both ways out: correct the workbook, or -- where a coordinate
    is right, as an offshore platform's is -- confirm that row into a cohort in
    review with the reason. Grouped by code and counted, as the intake groups
    its findings, so a sheet whose latitudes have all lost their sign says so
    once a country rather than once a row.
    """
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for record, assignment in zip(risks, assignments, strict=True):
        if assignment.rule in SCREEN_FINDINGS:
            country = str(record.get("country_code") or "").strip().upper()
            grouped.setdefault((assignment.rule, country), []).append(record)

    found = []
    for (rule, country), records in sorted(grouped.items()):
        code, field = SCREEN_FINDINGS[rule]
        rows = sorted(record.get("row_number") or 0 for record in records)
        shown = ", ".join(str(row) for row in rows[:SCREEN_ROWS_SHOWN]) + (
            f" and {len(rows) - SCREEN_ROWS_SHOWN} more" if len(rows) > SCREEN_ROWS_SHOWN else ""
        )
        risks_word = "risk" if len(rows) == 1 else "risks"
        if rule == "outside_country":
            name = COUNTRY_SCREEN.name(country) or country
            message = (
                f"{len(rows)} {risks_word} coded {country} "
                f"{'has a coordinate' if len(rows) == 1 else 'have coordinates'} more "
                f"than {COUNTRY_SCREEN.buffer_km:f} km outside {name} (Risks rows "
                f"{shown}). Correct the coordinate or the Country code and import "
                "the file again. Where a coordinate is right -- an offshore platform, "
                "say -- confirm that row into a cohort in the review queue and record "
                "why."
            )
        else:
            message = (
                f"{len(rows)} {risks_word} (Risks rows {shown}): "
                f"{extract.cohorts.unknown_country_reason(country)} Then import the "
                "file again."
            )
        found.append(
            {
                "sheet": profile.RISK_SHEET,
                "row_number": rows[0],
                "field": field,
                "code": code,
                "message": message,
                "value": country,
            }
        )
    return found


def _programme_findings(programme) -> list[dict[str, Any]]:
    """One finding per reason a contract layer cannot be written."""
    return [
        {
            "sheet": profile.CONTRACT_SHEET,
            "row_number": record.get("row_number"),
            "field": "Contract type",
            "code": "contract_refused",
            "message": (
                f"Contract {record.get('contract_number')} layer "
                f"{record.get('layer_number') or 1}: {reason}"
            ),
            "value": "",
        }
        for record, reasons in programme.refused
        for reason in reasons
    ]


def _intake_report(read, findings, risks, policies, contracts, programme) -> dict[str, Any]:
    """What the read found across every sheet.

    ``blocking`` is deliberately narrow. A file CASS cannot interpret at all
    stops the import; a file describing a book that is only partly geocoded
    does not, because that is the ordinary state of a facultative portfolio and
    refusing it would leave an analyst nothing to work with.
    """
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding["code"]] = counts.get(finding["code"], 0) + 1
    accounts = {str(record.get("business_id") or "").strip() for record in risks} - {""}
    with_terms = intake.complete_accounts(policies) & accounts
    return {
        "profile_version": read.profile_version,
        "parser_version": read.parser_version,
        "readable": read.is_readable,
        "has_policy_terms": read.has_policy_terms,
        "risks": len(read.risks.rows),
        "policies": len(read.policies.rows),
        "accounts": len(read.accounts()),
        "coverage_evidence": dict(intake.coverage_evidence(read)),
        "findings_by_code": dict(sorted(counts.items())),
        "unrecognised_columns": {
            "risks": list(read.risks.unrecognised_columns),
            "policies": list(read.policies.unrecognised_columns),
        },
        "blocking": not read.is_readable,
        # What promotion can write beyond the locations. Counted per Policy ID,
        # because a policy's terms are written for all of its rows or none.
        "financial_structure": {
            "policy_ids": len(accounts),
            "policy_ids_with_terms": len(with_terms),
            "policy_ids_without_terms": len(accounts - with_terms),
            "contracts": len({record.get("contract_number") for record in contracts} - {None}),
            "contract_layers": len(contracts),
            "contract_layers_refused": len(programme.refused),
            "contracts_without_scope": list(programme.unscoped),
        },
    }


def _rejected(project, artifact, filename, snapshot_date, reason, actor) -> ImportBatch:
    return ImportBatch.objects.create(
        project=project,
        profile=profile.PROFILE_NAME,
        source_artifact=artifact,
        source_filename=filename[:255],
        source_checksum=artifact.checksum,
        snapshot_date=snapshot_date,
        schema_version=intake.PROFILE_VERSION,
        parser_version=intake.PARSER_VERSION,
        # The rules that would have applied, so importing the same unreadable
        # file again finds this batch rather than colliding with it.
        cohort_rule_version=extract.COHORT_RULE_VERSION,
        state=ImportState.REJECTED,
        rejection_reason=reason,
        created_by=actor,
        updated_by=actor,
    )


def _stage_policies(batch: ImportBatch, records) -> None:
    SourcePolicyRow.objects.bulk_create(
        [
            SourcePolicyRow(
                batch=batch,
                row_number=record["row_number"],
                policy_id=str(record.get("policy_id") or "")[:64],
                business_id=str(record.get("business_id") or "")[:64],
                policy_tiv=record.get("policy_tiv"),
                currency=str(record.get("currency") or "")[:8],
                layer_number=record.get("layer_number"),
                values=_jsonable(record),
                raw={},
                created_by=batch.created_by,
            )
            for record in records
        ],
        batch_size=500,
    )


def _stage_risks(batch: ImportBatch, records, assignments) -> None:
    SourceRiskLocation.objects.bulk_create(
        [
            SourceRiskLocation(
                batch=batch,
                row_number=record["row_number"],
                business_id=str(record.get("business_id") or "")[:64],
                location_number=str(record.get("location_number") or "")[:64],
                cass_location_id=location_identity(
                    batch.source_checksum,
                    str(record.get("business_id") or ""),
                    str(record.get("location_number") or ""),
                ),
                primary_location=bool(record.get("primary_location")),
                latitude=record.get("latitude"),
                longitude=record.get("longitude"),
                total_insured_value=record.get("location_tiv"),
                currency=str(record.get("currency") or "")[:8],
                storeys=_storeys(record.get("storeys")),
                precision=str(record.get("precision") or "")[:32],
                needs_review=bool(record.get("needs_review")),
                class_of_business=str(record.get("class_of_business") or "")[:120],
                country_code=str(record.get("country_code") or "")[:2],
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
                values=_jsonable(record),
                raw={},
                created_by=batch.created_by,
            )
            for record, assignment in zip(records, assignments, strict=True)
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

def transformation_manifest(batch: ImportBatch) -> dict[str, Any]:
    """What was read, under which rules, and what it produced.

    It carries counts, versions and cohort totals rather than the portfolio
    itself -- not because the rows are withheld from anybody, but because a
    manifest is a summary and one that reproduced the whole book would be a
    copy of it. The rows are on the batch, for anyone who wants them.
    """
    risks = list(batch.location_rows.all())
    policies_by_business: dict[str, list[SourcePolicyRow]] = {}
    for policy in batch.policy_rows.all():
        policies_by_business.setdefault(policy.business_id, []).append(policy)

    cohort_tiv: dict[str, str] = {}
    for cohort, businesses in _businesses_by_cohort(risks).items():
        total = Decimal(0)
        for business_id in businesses:
            # A risk that states its own value is the better number; the policy
            # total is the fallback, and adding both would count it twice.
            stated = sum(
                (row.total_insured_value or Decimal(0))
                for row in risks
                if row.business_id == business_id
            )
            if stated:
                total += stated
            else:
                for policy in policies_by_business.get(business_id, ()):
                    total += policy.policy_tiv or Decimal(0)
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
        },
        "counts": {
            "policy_rows": batch.policy_row_count,
            "risk_rows": batch.risk_row_count,
            "parse_findings": len(batch.findings or []),
        },
        "intake_report": batch.intake_report,
        "cohorts": batch.cohort_profile,
        "cohort_tiv_at_kre_share_usd": cohort_tiv,
        "review_queue": _review_queue(risks),
        "value_basis": (
            "Insured values are stated at the share CASS writes. The share is not "
            "applied again during loss calculation, so a physical-damage result is "
            "gross damage at that share rather than 100%-of-risk ground-up loss."
        ),
    }
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
        code = item.country_code or "unknown"
        by_country[code] = by_country.get(code, 0) + 1
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


def _storeys(value) -> int | None:
    """The stated storey count, or nothing where the schedule left it blank.

    An unreadable value reads as unstated rather than as an error. The intake
    validator has already reported it as a finding, and refusing the whole
    import over one malformed height would hold up the other 223 risks -- while
    treating the bad value as real would put a risk in the wrong height band,
    which is worse than putting it in none.
    """
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


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
