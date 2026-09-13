"""Converting a model version into an Oasis model package.

Section 5's conversion pipeline is manifest, events, occurrence, footprint,
vulnerability, package and scientific QA. The first six happen here; the QA gate
is recorded as unperformed, for the same reason the hazard benchmark is: its
acceptance tests and tolerances have not been approved, and a gate that passed
itself would be a gate in name only.

A conversion does not run without an approved policy. Section 16 leaves event
identity and the multi-IMT representation open, and the converter ships no
default for either -- so a conversion names the choices it runs under and the
converter-candidate approval that cleared them, and both are checked here as
well as when the run is created, because an approval can be withdrawn between
the two.
"""

from __future__ import annotations

import json
from typing import Any

from django.db import transaction

from apps.artifacts.models import Artifact, ArtifactLink, ArtifactState
from apps.audit import services as audit
from apps.audit.models import Approval, AuditAction
from apps.common.storage import bucket, get_store
from apps.modelregistry import package as packaging
from apps.modelregistry import quality
from cass_converter.oasis_package import PackageError
from cass_converter.policy import ConversionPolicy, EventIdentity, IMTRepresentation
from cass_core.artifacts import AccessPolicy, RetentionClass
from cass_core.runs import RunState

from .models import ConversionRun

UNPERFORMED_STAGES: dict[str, str] = {
    "qa": (
        "Scientific acceptance tests and their tolerances have not been approved, "
        "so the QA gate stands open rather than being passed automatically."
    ),
}


class ConversionError(Exception):
    """Raised when a conversion cannot proceed in its current shape."""


def policy_for(conversion_run: ConversionRun) -> ConversionPolicy:
    """The conversion policy the run was created under."""
    recorded = (conversion_run.run.manifest or {}).get("policy") or {}
    hazard_set = conversion_run.model_version.hazard_set
    return ConversionPolicy(
        event_identity=EventIdentity(recorded.get("event_identity", "undecided")),
        imt_representation=IMTRepresentation(recorded.get("imt_representation", "undecided")),
        approval_reference=recorded.get("approval_reference", ""),
        imts=tuple(hazard_set.imts) if hazard_set else (),
        investigation_time=hazard_set.investigation_time if hazard_set else None,
        notes=recorded.get("notes", ""),
    )


def execute(conversion_run: ConversionRun, *, actor=None) -> ConversionRun:
    """Build and deploy the package, recording each stage against the run."""
    run = conversion_run.run
    if run.run_state is RunState.DRAFT:
        run.transition(RunState.QUEUED, actor=actor)
    if run.run_state is not RunState.QUEUED:
        raise ConversionError(
            f"A run in state {run.state} cannot be executed. Only a draft or queued "
            "run may start."
        )
    run.transition(RunState.RUNNING, actor=actor)

    manifest = dict(run.manifest or {})
    manifest["stages_not_performed"] = UNPERFORMED_STAGES
    stage = "manifest"
    try:
        policy = policy_for(conversion_run)
        _require_policy(conversion_run, policy)
        inputs = packaging.gather(conversion_run.model_version)
        run.advance(
            "manifest",
            actor=actor,
            message=(
                f"Converting {conversion_run.model_version.reference} under "
                f"{policy.event_identity} events and {policy.imt_representation} "
                f"measures, approved as {policy.approval_reference}."
            ),
        )

        stage = "footprint"
        built = packaging.deploy(
            inputs, root=packaging.model_root(), token=str(run.id)[:8]
        )

        stage = "events"
        run.advance(
            "events",
            actor=actor,
            message=f"{built['events']} events carried as Oasis events one for one.",
        )
        stage = "occurrence"
        preserved = built["occurrences"] == conversion_run.model_version.hazard_set.event_count
        run.advance(
            "occurrence",
            actor=actor,
            message=(
                f"{built['occurrences']} occurrences over {built['periods']} periods; "
                + ("frequency preserved." if preserved else "occurrence count differs from the hazard set.")
            ),
        )
        if not preserved:
            raise ConversionError(
                "The occurrence table does not carry every event of the hazard set, "
                "so annual frequency would not be preserved."
            )
        stage = "footprint"
        run.advance(
            "footprint",
            actor=actor,
            message=(
                f"{built['footprint']['rows']} footprint rows over "
                f"{', '.join(built['measures'])}, as area-peril channels."
            ),
        )
        stage = "vulnerability"
        run.advance(
            "vulnerability",
            actor=actor,
            message=f"{built['damage_bins']} damage bins; functions demand {', '.join(built['measures_demanded'])}.",
        )

        stage = "package"
        artifact = _register_manifest(conversion_run, built, actor)
        run.advance(
            "package",
            actor=actor,
            message="Package deployed to the Oasis model root and its manifest registered.",
            metrics={"manifest_checksum": artifact.checksum},
        )
    except (packaging.PackageBuildError, PackageError, ConversionError, Exception) as exc:
        _fail(run, exc, stage=stage, actor=actor)
        raise

    conversion_run.frequency_preserved = True
    conversion_run.target_checksum = artifact.checksum

    # Section 7's QA gate. The acceptance numbers were measured when the hazard
    # was converted; what happens here is the judgement, against whatever
    # tolerances are approved now. Where none are, the gate stands open with
    # the numbers in front of a reviewer rather than passing itself.
    converted_hazard = conversion_run.model_version.hazard_set
    tolerances = quality.approved_tolerances()
    decision = quality.decide(
        (converted_hazard.conversion_report or {}).get("qa") if converted_hazard else None,
        quality.tolerance_values(tolerances),
    )
    conversion_run.qa_report = {
        **decision,
        "tolerance_set": str(tolerances) if tolerances else "",
        "measures": built["measures"],
        "events": built["events"],
        "footprint_rows": built["footprint"]["rows"],
    }
    if decision.get("decided"):
        conversion_run.qa_state = "passed" if decision.get("passed") else "failed"
        manifest["stages_not_performed"] = {
            key: value
            for key, value in (manifest.get("stages_not_performed") or {}).items()
            if key != "qa"
        }
    conversion_run.save(
        update_fields=[
            "frequency_preserved",
            "target_checksum",
            "qa_report",
            "qa_state",
            "updated_at",
        ]
    )

    manifest["package"] = {
        key: value for key, value in built.items() if key != "files"
    }
    manifest["package"]["file_count"] = len(built["files"])
    run.manifest = manifest
    run.save(update_fields=["manifest", "updated_at"])
    run.transition(RunState.SUCCEEDED, actor=actor, stage="package")

    audit.record(
        action=AuditAction.PUBLISH,
        subject_type="conversion_run",
        subject_id=run.id,
        actor=actor,
        subject_label=str(run),
        after={
            "model_version": conversion_run.model_version.reference,
            "manifest_checksum": artifact.checksum,
        },
    )
    return conversion_run


def _require_policy(conversion_run: ConversionRun, policy: ConversionPolicy) -> None:
    blockers = policy.blockers()
    if blockers:
        raise ConversionError("; ".join(blockers))
    recorded = (conversion_run.run.manifest or {}).get("policy") or {}
    approval = Approval.objects.filter(pk=recorded.get("approval") or None).first()
    if approval is None or approval.gate != Approval.Gate.CONVERTER_CANDIDATE:
        raise ConversionError(
            "The converter-candidate approval this conversion was created under no "
            "longer exists."
        )
    if not approval.is_cleared:
        raise ConversionError(
            f"The converter-candidate approval is {approval.decision}, not approved, so "
            "the policy it would clear is not cleared."
        )


def _register_manifest(conversion_run: ConversionRun, built: dict[str, Any], actor) -> Artifact:
    """Keep the package manifest, with every file's checksum, in the artifact store.

    The package itself lives on the engine's volume, where it is read; the
    manifest lives here, where it is governed, so a later reader can prove which
    package a loss was computed from even after the volume has been rebuilt.
    """
    run = conversion_run.run
    payload = json.dumps(built, indent=2, sort_keys=True).encode("utf-8")
    ref = get_store().put_bytes(
        bucket("model"),
        f"oasis_package/{run.id}/MANIFEST.json",
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
            "role": "oasis_package_manifest",
            "original_filename": "MANIFEST.json",
            "created_by": actor,
            "updated_by": actor,
        },
    )
    ArtifactLink.objects.update_or_create(
        subject_type="conversion_run",
        subject_id=run.id,
        role="oasis_package_manifest",
        defaults={"artifact": artifact, "direction": "output"},
    )
    return artifact


def _fail(run, exc: Exception, *, stage: str, actor) -> None:
    summary = getattr(exc, "summary", None) or str(exc)
    progress = run.progress
    with transaction.atomic():
        run.transition(
            RunState.FAILED,
            actor=actor,
            stage=stage,
            failure_summary=summary[:500],
            failure_detail=getattr(exc, "detail", "") or repr(exc),
            save=False,
        )
        run.progress = progress
        run.save()
