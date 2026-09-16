"""Driving an analysis run across the Oasis boundary.

Milestone M2 of section 13 is the target: CASS publishes the OED, runs Oasis
file generation, then submits and monitors the loss job, with artifacts,
errors, reconciliation and lineage visible -- and without anyone opening the
native Oasis interface. This module is the orchestration half of that. The
protocol half is the adapter; nothing here speaks HTTP.

All eleven stages of the analysis pipeline are performed here:

``validate_exposure``
    Validate the published files again, inside the run, and confirm they still
    match the version record, carry one currency and support the requested
    perspectives.

``enrich``
    Apply the run's assumption set by choosing the engine's vulnerability set,
    and record which attributes each location stated, derived or left to the
    assumption, as an enrichment run.

``publish_oed``
    Create the Oasis portfolio and upload the frozen OED artifacts.

``keys``
    Map the published exposure to the model through the CASS keys service.
    Section 5 gives CASS keys this job; OasisLMF builds the kernel files from
    what it produces.

``reconcile_keys``
    The section 8 gate. Value that does not add up fails the run; value that
    adds up but could not be mapped holds it for an approval.

``generate_inputs``
    Create the analysis, post its settings, and let the pinned OasisLMF build
    the kernel and financial files.

``validate_inputs``
    Read back what Oasis's own lookup did and check every published location is
    accounted for, comparing it with the CASS keys result.

``smoke``
    Run a handful of the served package's largest events through every
    requested perspective before admitting the full event set.

``losses``
    Run the requested perspectives.

``collect``
    Pull the ORD outputs back into the artifact store and complete the run
    manifest.

``review``
    Check the published results are losses at all -- not negative, rising with
    return period, within the insured value -- and hold them at the gate when
    they are not.

``smoke`` is the one stage that can decline to run. Where no footprint index is
readable it is recorded in the manifest as not performed rather than skipped
silently, because a manifest that omits a stage reads as though it passed.

Two rules shape everything below. A run that stops must say why in terms an
analyst can act on, and must never leave a published partial result: every
engine failure is caught, recorded against the stage that raised it with the
engine's own traceback in the detail, and the run is failed rather than left
holding a queue slot. And a run held at a gate is not a run that failed --
blocking has its own state, its own fields and its own resume path, so a
waiting analysis is never reported as broken.
"""

from __future__ import annotations

import copy
import csv
import io
import json
import pathlib
import time
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.db import transaction

from apps.artifacts.models import Artifact, ArtifactLink, ArtifactState
from apps.audit import services as audit
from apps.audit.models import Approval, AuditAction
from apps.common.engines import oasis_adapter, oasis_model_triple
from apps.common.storage import bucket, get_store
from apps.exposure import services as exposure_services
from apps.exposure.models import CurrencyRate, EnrichmentRun
from apps.modelregistry.assets import (
    VULNERABILITY_DICTIONARY_ROLE,
    ModelAssetError,
    asset_bytes,
    load_grid,
    load_vulnerability,
)
from apps.modelregistry.models import PublicationState
from cass_adapters.base import AdapterError, EngineState
from cass_adapters.oasis import OasisPhase, PortfolioFileKind
from cass_converter import pilot_enrichment
from cass_converter.enrichment import (
    Enrichment,
    EnrichmentError,
    assumption_variant,
    enrichment_from,
    exposure_lineage,
)
from cass_converter.model_build import BASELINE
from cass_converter.oasis_package import (
    PERIL_SCOPE,
    PackageError,
    read_footprint_index,
    widen_peril_scope,
)
from cass_core.artifacts import AccessPolicy, RetentionClass
from cass_core.policy import MULTI_CHANNEL_REPRESENTATIONS, IMTRepresentation
from cass_core.runs import RunState
from cass_keys.lookup import lookup as keys_lookup
from cass_oed import ord as ord_results
from cass_oed.currency import ConversionRate, CurrencyError, convert_portfolio
from cass_oed.perspectives import Perspective, available_perspectives
from cass_oed.validation import validate as validate_portfolio

from . import admission
from .models import AnalysisRun

#: Which Oasis portfolio endpoint each CASS artifact role belongs to. The order
#: matters: Oasis validates a reinsurance scope against the contracts it
#: references, and contracts against the accounts they attach to.
OASIS_FILE_BY_ROLE: dict[str, PortfolioFileKind] = {
    "oed_location": PortfolioFileKind.LOCATION,
    "oed_account": PortfolioFileKind.ACCOUNTS,
    "oed_reins_info": PortfolioFileKind.REINSURANCE_INFO,
    "oed_reins_scope": PortfolioFileKind.REINSURANCE_SCOPE,
}

#: The stages a geometry-only run performs. It maps coordinates to the grid and
#: reports which risks the model can answer for, and stops: brief section 5.2
#: makes it a run that calculates no portfolio loss, so nothing is submitted to
#: the engine and no result set is published.
GEOMETRY_ONLY_STAGES = ("validate_exposure", "enrich", "keys", "reconcile_keys")

#: The stages that reach the engine. Named rather than taken as the span from
#: the first to the last, because the keys reconciliation sits between two of
#: them and uses no engine at all: a geometry-only run that stops there must
#: not ask Oasis for its version to do it.
ENGINE_CALLING_STAGES = frozenset(
    {"publish_oed", "generate_inputs", "validate_inputs", "smoke", "losses", "collect"}
)

#: Stages this service performs, in pipeline order.
ENGINE_STAGES = (
    "validate_exposure",
    "enrich",
    "publish_oed",
    "keys",
    "reconcile_keys",
    "generate_inputs",
    "validate_inputs",
    "smoke",
    "losses",
    "collect",
    "review",
)

#: Stages the analysis pipeline declares that this service does not perform,
#: with the reason each is outstanding. Recorded in the manifest so a reader
#: can tell "not done" from "done and clean". Every stage is performed now;
#: ``smoke`` joins these on a run where it could not choose an event set, with
#: the reason it could not.
UNPERFORMED_STAGES: dict[str, str] = {}

#: How many events the smoke check runs. Enough to touch most of a regional
#: book, few enough that a national event set of tens of thousands is checked
#: in the time it takes to generate its inputs.
SMOKE_EVENT_COUNT = 25


class AnalysisExecutionError(Exception):
    """Raised when a run cannot be executed in its current shape."""


class RunBlocked(Exception):
    """Raised when a governance gate stops a run pending an approval.

    Deliberately not an ``AnalysisExecutionError``. A blocked run has not
    failed: nothing is wrong with it, a person simply has to decide something
    before it goes on. Sharing a base class with failure is how the two end up
    being handled together and a waiting run gets reported as broken.
    """

    def __init__(self, summary: str, *, detail: str = "") -> None:
        super().__init__(summary)
        self.summary = summary
        self.detail = detail


# -- settings ---------------------------------------------------------------

#: The ORD outputs CASS asks for by default.
#:
#: Deliberately not every output Oasis can produce. Section 11 records that the
#: PiWind run reached roughly 21.6 GB at peak under an all-output configuration
#: and requires compute profiles to be declared rather than inherited from
#: maximum parallelism. These four answer the questions a portfolio result has
#: to answer -- what each event costs, what the exceedance curves look like, and
#: what the average annual loss is -- and a run that needs more says so
#: explicitly in its own settings document.
DEFAULT_ORD_OUTPUT: dict[str, bool] = {
    "elt_moment": True,
    "alt_period": True,
    "ept_mean_sample_aep": True,
    "ept_mean_sample_oep": True,
    "return_period_file": True,
}

#: Which settings keys carry each perspective.
_PERSPECTIVE_KEYS: dict[str, tuple[str, str]] = {
    str(Perspective.GROUND_UP): ("gul_output", "gul_summaries"),
    str(Perspective.INSURED): ("il_output", "il_summaries"),
    str(Perspective.REINSURANCE): ("ri_output", "ri_summaries"),
}

#: The summary set that says where a loss is. The portfolio summary answers how
#: much; this one groups by location so each location's average annual loss can
#: be placed in the cell its keys mapped it to. Only the period average loss is
#: asked for at this level: an event table per location is the output that
#: drives a national run's size, and a map needs none of it.
LOCATION_SUMMARY_ID = 2
LOCATION_FIELDS: tuple[str, ...] = ("AccNumber", "LocNumber")
LOCATION_ORD_OUTPUT: dict[str, bool] = {"alt_period": True}


def build_analysis_settings(analysis_run: AnalysisRun, model=None) -> dict:
    """The analysis settings document Oasis is given.

    An explicitly configured document wins. Otherwise one is derived from the
    requested perspectives, because section 8 forbids generating a placeholder
    financial file to imply a perspective the source data does not support: if
    insured loss was not requested, the settings must not ask for it.

    Each requested perspective needs both its output flag and a summary block.
    A flag on its own is accepted by Oasis and produces nothing, which is worse
    than a refusal: the run succeeds and the result package is empty.

    ``model_supplier_id``, ``model_name_id`` and ``model_settings`` are required
    by the server's settings schema. The first two name the resolved Oasis
    model, so the document cannot drift from the model the analysis is bound
    to. ``model_settings`` names the vulnerability set where the package carries
    more than one (ADR 14) and nothing else: event set and occurrence choices
    belong to the model's own defaults, and filling them in here would put a
    CASS guess inside a model's configuration.
    """
    vulnerability_set = vulnerability_set_for(analysis_run)
    if analysis_run.analysis_settings:
        document = dict(analysis_run.analysis_settings)
        chosen = dict(document.get("model_settings") or {})
        if vulnerability_set and not chosen.get("vulnerability_set"):
            # A package built with assumption sets carries no plain table, so a
            # document naming none would reach the engine with no damage
            # relationships at all. The run's own set is added rather than the
            # document refused.
            document["model_settings"] = {**chosen, "vulnerability_set": vulnerability_set}
        return document

    requested = set(settings_perspectives(analysis_run))

    supplier, model_name, _ = oasis_model_triple()
    settings_document: dict = {
        "version": "3",
        "analysis_tag": str(analysis_run.run_id),
        "model_version_id": str(analysis_run.model_version_id),
        "model_supplier_id": model.supplier_id if model is not None else supplier,
        "model_name_id": model.model_id if model is not None else model_name,
        "model_settings": (
            {"vulnerability_set": vulnerability_set} if vulnerability_set else {}
        ),
    }
    for perspective, (flag, summaries) in _PERSPECTIVE_KEYS.items():
        wanted = perspective in requested
        settings_document[flag] = wanted
        if wanted:
            settings_document[summaries] = [
                {"id": 1, "ord_output": dict(DEFAULT_ORD_OUTPUT)},
                {
                    "id": LOCATION_SUMMARY_ID,
                    "oed_fields": list(LOCATION_FIELDS),
                    "ord_output": dict(LOCATION_ORD_OUTPUT),
                },
            ]
    return settings_document


def vulnerability_set_for(analysis_run: AnalysisRun) -> str | None:
    """The vulnerability set the engine is asked for, or ``None`` for one plain table.

    A package built with assumption sets carries no plain table (ADR 14), so an
    analysis against it always names one: the set the run chose, or the
    baseline where it chose none.
    """
    variants = analysis_run.model_version.vulnerability_set.assumption_variants or {}
    if not variants:
        return None
    if analysis_run.assumption_set_id:
        return analysis_run.assumption_set.flavour
    return BASELINE


def settings_perspectives(analysis_run: AnalysisRun) -> tuple[str, ...]:
    """The perspectives the engine is asked to calculate, in pipeline order.

    Not always the ones requested. Reinsurance is computed from the insured
    position: the engine applies the contracts to the insured stream and needs
    it calculated and summarised to write the reinsurance summary files at all.
    Asked for reinsurance alone, it builds the reinsurance structures and then
    stops on a missing insured summary index. So the insured stream is asked
    for too, which is the comparison a reinsurance analyst wants in front of
    them anyway.
    """
    requested = {str(item) for item in (analysis_run.perspectives or [])}
    if not requested:
        requested = {str(Perspective.GROUND_UP)}
    if str(Perspective.REINSURANCE) in requested:
        requested.add(str(Perspective.INSURED))
    return tuple(str(item) for item in Perspective if str(item) in requested)


def settings_digest(document: dict) -> str:
    """A stable digest of the settings, for the run's ``settings_hash``."""
    from cass_core.checksums import hash_bytes

    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hash_bytes(canonical)


#: Settings that say what a run reports, or which run it is, rather than how its
#: losses are calculated.
_NOT_CALCULATION = frozenset(
    {"analysis_tag", "gul_output", "il_output", "ri_output",
     "gul_summaries", "il_summaries", "ri_summaries"}
)


def calculation_digest(document: Mapping[str, Any]) -> str:
    """A digest of how a run's losses were calculated, apart from its assumption set.

    Two runs of one book on one model differ by design in the vulnerability set
    their assumption set names, in their tag and in the outputs they ask for.
    Everything else in the settings -- samples, events, thresholds, the model's
    own settings -- decides the numbers, so results are comparable across
    assumption sets only where this digest agrees.
    """
    trimmed = {key: value for key, value in document.items() if key not in _NOT_CALCULATION}
    model_settings = dict(trimmed.get("model_settings") or {})
    model_settings.pop("vulnerability_set", None)
    trimmed["model_settings"] = model_settings
    return settings_digest(trimmed)


# -- reading the published OED ----------------------------------------------

def oed_payloads(analysis_run: AnalysisRun) -> list[tuple[str, str, bytes]]:
    """The published OED files as ``(role, filename, bytes)``, in upload order.

    Read through the artifact interface rather than a path, so this behaves the
    same on a workstation and against S3, and so the engine is handed bytes
    rather than a location it could not reach anyway.
    """
    version = analysis_run.exposure_version
    if not version.is_frozen:
        raise AnalysisExecutionError(
            "The exposure version is not published. Publish it before running an analysis, "
            "so the run points at immutable input."
        )

    store = get_store()
    links = {
        link.role: link
        for link in ArtifactLink.objects.filter(
            subject_type="exposure_version", subject_id=version.id, direction="input"
        ).select_related("artifact")
    }

    payloads: list[tuple[str, str, bytes]] = []
    for role in OASIS_FILE_BY_ROLE:
        link = links.get(role)
        if link is None or not link.artifact.is_readable:
            continue
        with store.open(link.artifact.uri) as handle:
            payloads.append((role, link.artifact.original_filename or f"{role}.csv", handle.read()))

    if not any(role == "oed_location" for role, _, _ in payloads):
        raise AnalysisExecutionError(
            "The exposure version has no readable location file, so there is nothing to analyse."
        )
    return payloads


# -- before anything reaches the engine -------------------------------------

def _validate_exposure(analysis_run, actor) -> dict:
    """Validate the published exposure again, inside the run.

    It was validated when it was published, and it is validated again here for
    two reasons. A run may start weeks after publication, under a validator
    that has learned something since. And three things the engine cannot
    recover from are cheap to check now and expensive to discover after input
    generation: files that no longer match the version record, more than one
    currency reaching the financial module, and a perspective the files cannot
    support.

    Nothing here writes to the version. A published version is immutable, so a
    problem found now is a reason to correct it into the next version, not to
    change this one.
    """
    run = analysis_run.run
    version = analysis_run.exposure_version
    if not version.is_frozen:
        raise AnalysisExecutionError(
            "The exposure version is not published. Publish it before running an analysis, "
            "so the run points at immutable input."
        )
    try:
        files = exposure_services.load_files(version)
    except exposure_services.ExposureError as exc:
        raise AnalysisExecutionError(str(exc)) from exc

    report = validate_portfolio(files)
    problems: list[str] = []

    errors = report.findings.errors
    if errors:
        examples = "; ".join(item.message for item in errors[:3])
        problems.append(
            f"{len(errors)} blocking validation finding(s) now stand against the "
            f"published files, for example: {examples}"
        )

    recorded_tiv = Decimal(version.total_tiv or 0)
    if report.total_tiv != recorded_tiv:
        problems.append(
            f"The files hold {report.total_tiv} of insured value but the version records "
            f"{recorded_tiv}, so the artifact and the record have come apart."
        )
    if report.location_count != version.location_count:
        problems.append(
            f"The files hold {report.location_count} locations but the version records "
            f"{version.location_count}."
        )

    currencies = [item for item in report.currencies if item]
    run_currency = analysis_run.run_currency or version.run_currency
    if len(currencies) > 1:
        problems.append(
            f"The files mix {', '.join(currencies)}. The Oasis financial module does not "
            "convert between currencies, so values must be normalised into one before a run."
        )
    elif currencies and run_currency and currencies[0] != run_currency:
        # A book stated in another currency is not a defect: section 8 has CASS
        # convert it before generation, because the Oasis financial module
        # cannot. What would be a defect is converting it at no stated rate, so
        # the evidence is required here -- before the engine is touched --
        # rather than discovered when the portfolio is published to it.
        try:
            conversion_rate_for(analysis_run)
        except AnalysisExecutionError as exc:
            problems.append(str(exc))

    availability = {str(item.perspective): item for item in available_perspectives(files)}
    for perspective in analysis_run.perspectives or []:
        entry = availability.get(str(perspective))
        if entry is None or not entry.available:
            reason = entry.reason if entry is not None else "CASS does not produce it."
            problems.append(
                f"{perspective} is not supported by the published files. {reason}".strip()
            )

    if problems:
        raise AnalysisExecutionError(
            "The published exposure does not pass the checks a run requires:\n- "
            + "\n- ".join(problems)
        )

    summary = {
        "oed_schema_version": version.oed_schema_version,
        "locations": report.location_count,
        "accounts": report.account_count,
        "total_tiv": str(report.total_tiv),
        "currency": currencies[0] if currencies else "",
        "warnings": len(report.findings.warnings),
        "inputs": _input_checksums(version),
    }
    run.advance(
        "validate_exposure",
        actor=actor,
        message=(
            f"The published exposure validates: {report.location_count} locations, "
            f"{report.total_tiv} {summary['currency']} of insured value."
        ),
        metrics=summary,
    )
    return summary


def _input_checksums(version) -> dict[str, str]:
    """The checksum of every published file, by role, for the manifest."""
    return {
        link.role: link.artifact.checksum
        for link in ArtifactLink.objects.filter(
            subject_type="exposure_version", subject_id=version.id, direction="input"
        ).select_related("artifact")
    }


def _served_package(analysis_run) -> dict:
    """Which model package the Oasis worker serves, and whether it is this one.

    ADR 9: a worker serves one package at a time, and building a package for a
    model version replaces what it serves. A run against another version's
    package would complete, and publish that version's losses under this one's
    name, with nothing downstream able to tell. So wherever CASS can read the
    served package it confirms the name before anything is submitted.

    Where it cannot -- a model version CASS did not build a package for, or a
    control plane that does not share the worker's volume -- that is recorded as
    unconfirmed rather than passed.
    """
    model_version = analysis_run.model_version
    if model_version.hazard_set_id is None:
        return {
            "checked": False,
            "reason": (
                f"{model_version.reference} has no CASS hazard set, so the engine serves "
                "a package CASS did not build and has no manifest to compare."
            ),
        }

    root = pathlib.Path(settings.CASS_OASIS_MODEL_ROOT)
    try:
        manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "checked": False,
            "reason": (
                f"No model package is readable at {root} from the control plane, so "
                "which package the worker serves could not be confirmed."
            ),
        }
    except (OSError, ValueError) as exc:
        return {
            "checked": False,
            "reason": f"The package manifest at {root} could not be read: {exc}",
        }

    provenance = manifest.get("provenance") or {}
    served = str(provenance.get("model_version") or "")
    if served != model_version.reference:
        raise AnalysisExecutionError(
            f"The Oasis worker is serving the package built for "
            f"{served or 'an unnamed model version'}, not {model_version.reference}. "
            f"Build the package for {model_version.reference} on the Build tab and run "
            "again: this run would otherwise publish the other version's losses under "
            "this one's name."
        )

    wanted = vulnerability_set_for(analysis_run)
    carried = [str(item) for item in manifest.get("vulnerability_sets") or []]
    if wanted and wanted not in carried:
        described = (
            f"the {', '.join(carried)} vulnerability sets" if carried
            else "one plain vulnerability table"
        )
        raise AnalysisExecutionError(
            f"The served package for {model_version.reference} carries {described}, and "
            f"this run asks for {wanted}. Build the package again so it carries the "
            "model version's assumption sets."
        )
    return {
        "checked": True,
        "model_version": served,
        "package_version": manifest.get("package_version", ""),
        "hazard_set": provenance.get("hazard_set", ""),
        "events": manifest.get("events"),
        "vulnerability_sets": carried,
    }


def _recorded_enrichment(vulnerability, key: str) -> Enrichment | None:
    """The enrichment a vulnerability set's functions were built under, as it recorded it.

    A set built on the platform is built under an enrichment somebody wrote, so
    there is no constant to look it up by country: the set's dictionary is the
    only place it survives. Reading it from there is right for the pilot
    countries too, since a compiled-in enrichment can move after a set was built
    and the lineage has to describe the functions the engine will use.

    ``None`` for a set whose dictionary predates the record, which is then
    described as before. A record that is present but unreadable stops the run
    instead of being passed over, because the lineage would otherwise describe a
    different model without saying so.
    """
    try:
        payload = asset_bytes(
            vulnerability, "vulnerability_set", VULNERABILITY_DICTIONARY_ROLE, str(vulnerability)
        )
    except ModelAssetError:
        return None
    try:
        recorded = json.loads(payload).get("assumption_sets") or {}
    except (ValueError, AttributeError) as exc:
        raise AnalysisExecutionError(
            f"The provenance dictionary of {vulnerability} cannot be read ({exc}), so the "
            "enrichment its functions were built under cannot be established."
        ) from exc
    document = recorded.get(key) if isinstance(recorded, dict) else None
    if document is None:
        return None
    try:
        return enrichment_from(document)
    except EnrichmentError as exc:
        raise AnalysisExecutionError(
            f"{vulnerability} records the enrichment behind its {key} functions, and the "
            f"record cannot be read: {exc}"
        ) from exc


def _enrich(analysis_run, actor) -> dict:
    """Apply the run's assumption set, and record what the run rests on.

    Section 8 keeps reported data first and assumptions only for what is
    missing, and section 5 asks every enrichment run to say how much of each
    was which. An assumption set in CASS is a weighting of the mixtures the
    model's classes are blended from (ADR 14). It changes the damage
    relationship an unknown attribute is answered by, and never a value or a
    stated attribute. So this stage settles which vulnerability set the engine
    uses and records the lineage -- which attributes each location stated,
    which were derived, which the mixture carries -- as an ``EnrichmentRun``,
    with its table stored as an artifact.

    The lineage is recorded under the rules the functions were built with,
    which the vulnerability set holds. An assumption set whose record has
    changed since would otherwise describe weights the functions do not have.
    """
    run = analysis_run.run
    version = analysis_run.exposure_version
    model_version = analysis_run.model_version
    vulnerability = model_version.vulnerability_set
    chosen = analysis_run.assumption_set
    variants = vulnerability.assumption_variants or {}
    key = chosen.flavour if chosen is not None else BASELINE

    if chosen is not None:
        if chosen.country_code.upper() != model_version.country_code.upper():
            raise AnalysisExecutionError(
                f"The {chosen} assumption set is for {chosen.country_code}, and "
                f"{model_version.reference} models {model_version.country_code}."
            )
        if key not in variants:
            raise AnalysisExecutionError(
                f"{vulnerability} carries no functions for the "
                f"{chosen.get_flavour_display().lower()} assumption set. Register the "
                "vulnerability set again so it is built under every assumption set, "
                "then build the package."
            )
        if chosen.rules and dict(chosen.rules) != variants[key]:
            raise AnalysisExecutionError(
                f"The {chosen} assumption set's rules have changed since {vulnerability} "
                "was built, so the functions a run would use are not the ones the set "
                "now describes. Register the vulnerability set again."
            )

    rules = variants.get(key, {})
    enrichment = _recorded_enrichment(vulnerability, key)
    if enrichment is None:
        # A set registered before its dictionary recorded the enrichment.
        try:
            base = pilot_enrichment.enrichment(model_version.country_code)
        except KeyError:
            # A country with no pilot enrichment still has lineage: what the
            # schedule states, with nothing derived from a year no era covers.
            base = Enrichment(
                name=f"{model_version.country_code.lower()}_no_pilot_enrichment",
                version="0",
                country_code=model_version.country_code.upper(),
            )
        try:
            enrichment = (
                base
                if key == BASELINE and not rules
                else assumption_variant(base, key=key, rules=rules)
            )
        except EnrichmentError as exc:
            raise AnalysisExecutionError(str(exc)) from exc

    try:
        files = exposure_services.load_files(version)
    except exposure_services.ExposureError as exc:
        raise AnalysisExecutionError(str(exc)) from exc
    lineage = exposure_lineage([dict(row.raw) for row in files.location.rows], enrichment)

    source_tiv = Decimal(version.total_tiv or 0)
    if lineage.total_tiv != source_tiv:
        raise AnalysisExecutionError(
            f"The enrichment read {lineage.total_tiv} of insured value from the published "
            f"files, and the version records {source_tiv}. An assumption set moves no "
            "value, so a difference is a defect in the reading rather than a result."
        )

    uri = _store_keys_file(
        run, "enrichment_lineage", lineage.as_csv(), actor, retention=RetentionClass.RESULT
    )
    artifact = Artifact.objects.get(uri=uri)
    counts = lineage.counts()
    exceptions = lineage.exceptions()
    engine_set = key if variants else ""

    with transaction.atomic():
        enrichment_run = EnrichmentRun.objects.create(
            exposure_version=version,
            assumption_set=chosen,
            reported_count=counts["reported"],
            derived_count=counts["derived"],
            imputed_count=counts["imputed"],
            mean_confidence=lineage.mean_confidence(),
            missingness_profile=lineage.missingness(),
            attribute_lineage={
                "by_attribute": lineage.by_attribute(),
                "unresolved": counts["unresolved"],
                "enrichment": enrichment.as_dict(),
                "vulnerability_set": engine_set,
            },
            reconciliation={
                "source_tiv": str(source_tiv),
                "enriched_tiv": str(lineage.total_tiv),
                "difference": str(lineage.total_tiv - source_tiv),
                "method": (
                    "An assumption set re-weighs the mixture a class is blended from. "
                    "It moves no value between locations or coverages, so the enriched "
                    "value is the published value."
                ),
            },
            reconciled=True,
            exceptions=exceptions,
            output_checksum=artifact.checksum,
            created_by=actor,
            updated_by=actor,
        )
        ArtifactLink.objects.update_or_create(
            artifact=artifact,
            subject_type="enrichment_run",
            subject_id=enrichment_run.id,
            role="enrichment_lineage",
            direction="output",
            defaults={"created_by": actor},
        )
        analysis_run.enrichment_run = enrichment_run
        analysis_run.save(update_fields=["enrichment_run", "updated_at"])

    label = chosen.label if chosen is not None else "The model's baseline weights"
    run.advance(
        "enrich",
        actor=actor,
        message=(
            f"{label}: {enrichment_run.assumption_share:.0%} of attribute values rest on "
            "the assumption rather than the schedule"
            + (
                f"; {len(exceptions)} location(s) have an occupancy no assumption can place."
                if exceptions
                else "."
            )
        ),
        metrics={
            "enrichment_run": str(enrichment_run.id),
            "vulnerability_set": engine_set,
            **counts,
        },
    )
    return {
        "enrichment_run": str(enrichment_run.id),
        "assumption_set": chosen.reference if chosen is not None else "",
        "vulnerability_set": engine_set,
        "enrichment": enrichment.reference,
        "counts": counts,
        "mean_confidence": enrichment_run.mean_confidence,
        "lineage": uri,
        "checksum": artifact.checksum,
    }


def source_currency(analysis_run: AnalysisRun) -> str:
    """The currency the published book is stated in."""
    version = analysis_run.exposure_version
    stated = [code for code in (version.tiv_by_currency or {}) if code and code != "unknown"]
    if len(stated) == 1:
        return stated[0].upper()
    return (version.run_currency or "").upper()


def conversion_rate_for(analysis_run: AnalysisRun) -> ConversionRate | None:
    """The governed rate this run needs, or ``None`` where it needs none.

    Section 8 requires the evidence before generation and section 15 explains
    why: the Oasis Financial Module cannot calculate multi-currency terms, so a
    book that is not already in the run currency is converted here or not run.
    An unapproved rate is not evidence, so it is refused in the same way a
    missing one is -- with the difference said plainly, because the fix differs.
    """
    source = source_currency(analysis_run)
    target = (analysis_run.run_currency or source).upper()
    if not source or not target or source == target:
        return None

    version = analysis_run.exposure_version
    candidates = CurrencyRate.objects.filter(
        from_currency=source, to_currency=target
    ).order_by("-valuation_date")
    # A rate is evidence at a date. One quoted after the valuation date of the
    # book describes a different day's money, so it is not used unless nothing
    # earlier exists to use instead.
    if version.valuation_date:
        dated = candidates.filter(valuation_date__lte=version.valuation_date)
        candidates = dated if dated.exists() else candidates

    approved = candidates.filter(approved_at__isnull=False).first()
    if approved is None:
        unapproved = candidates.first()
        if unapproved is not None:
            raise AnalysisExecutionError(
                f"The {source} to {target} rate of {unapproved.valuation_date.isoformat()} "
                f"({unapproved.source}) has not been approved, so it is not evidence. "
                "Have it approved before running, or the numbers rest on a rate "
                "nobody stood behind."
            )
        raise AnalysisExecutionError(
            f"{version.name} v{version.version} is stated in {source} and this run is "
            f"in {target}, and no approved {source} to {target} rate is recorded. "
            "The Oasis Financial Module does not calculate multi-currency terms, so "
            "the conversion happens in CASS and needs a rate with its source and "
            "valuation date."
        )

    return ConversionRate(
        from_currency=approved.from_currency,
        to_currency=approved.to_currency,
        rate=approved.rate,
        valuation_date=approved.valuation_date,
        source=approved.source,
        reference=approved.reference or f"currency_rate:{approved.id}",
    )


# -- the stages -------------------------------------------------------------

def _publish_oed(analysis_run, engine, actor) -> dict:
    """Create the Oasis portfolio and upload the OED.

    Where the book is not already in the run currency it is converted first,
    and what reaches the engine is the converted copy. The published exposure
    is untouched: section 8 forbids a transformation silently replacing a
    reported field, so the reported value and the converted one both stand,
    with the rate between them recorded on the run.
    """
    run = analysis_run.run
    payloads = oed_payloads(analysis_run)
    rate = conversion_rate_for(analysis_run)
    conversion: dict = {}

    if rate is not None:
        try:
            converted, conversion = convert_portfolio(
                {
                    role: (exposure_services.KIND_BY_ROLE[role], payload)
                    for role, _, payload in payloads
                },
                rate,
            )
        except CurrencyError as exc:
            raise AnalysisExecutionError(str(exc)) from exc

        payloads = [
            (role, filename, converted[role]) for role, filename, _ in payloads
        ]
        # Stored as well as sent: what the engine was given has to be
        # retrievable, not merely described.
        conversion["artifacts"] = {
            role: _store_keys_file(run, f"{role}_converted", payload, actor)
            for role, _, payload in payloads
        }
        analysis_run.currency_conversion = conversion
        analysis_run.save(update_fields=["currency_conversion", "updated_at"])

    peril_scope: dict = {}
    if carries_sub_peril_channels(analysis_run):
        # A class spanning measures reaches the engine as one item per measure,
        # each under its own earthquake sub-peril, and a term only applies to
        # the perils it names. The engine's copy is scoped to every earthquake
        # peril so each term covers all of a building's channels; the published
        # files still say what was reported.
        scoped = [
            (role, filename, *widen_peril_scope(payload)) for role, filename, payload in payloads
        ]
        payloads = [(role, filename, payload) for role, filename, payload, _ in scoped]
        changed = {role: count for role, _, _, count in scoped}
        peril_scope = {
            "scope": PERIL_SCOPE,
            "values_changed": changed,
            "artifacts": {
                role: _store_keys_file(run, f"{role}_peril_scope", payload, actor)
                for role, _, payload in payloads
                if changed[role]
            },
        }

    portfolio_id = engine.create_portfolio(f"cass-{run.id}")
    uploaded = {}
    for role, filename, payload in payloads:
        engine.upload_portfolio_file(
            portfolio_id, OASIS_FILE_BY_ROLE[role], filename, payload
        )
        uploaded[role] = len(payload)

    analysis_run.oasis_portfolio_id = str(portfolio_id)
    analysis_run.save(update_fields=["oasis_portfolio_id", "updated_at"])

    run.advance(
        "publish_oed",
        actor=actor,
        message=(
            f"Published {len(uploaded)} OED files to Oasis portfolio {portfolio_id}."
            + (f" Converted at {rate.description}." if rate is not None else "")
            + (
                f" Terms scoped to {PERIL_SCOPE} for the model's sub-peril channels."
                if peril_scope
                else ""
            )
        ),
        metrics={
            "oasis_portfolio_id": portfolio_id,
            "bytes_by_role": uploaded,
            **({"peril_scope": peril_scope} if peril_scope else {}),
        },
    )
    return {
        "oasis_portfolio_id": portfolio_id,
        "files": uploaded,
        "currency_conversion": conversion,
        "peril_scope": peril_scope,
    }


def carries_sub_peril_channels(analysis_run: AnalysisRun) -> bool:
    """Whether the run's model splits a class spanning measures into sub-peril items.

    Read from the vulnerability set's record, which says what the set was built
    under and how many of its classes span measures, rather than from the package
    the worker happens to serve: the run already refuses a package that is not
    this model version's.
    """
    vulnerability = analysis_run.model_version.vulnerability_set
    try:
        representation = IMTRepresentation(
            vulnerability.imt_representation or IMTRepresentation.UNDECIDED
        )
    except ValueError:
        return False
    return (
        representation in MULTI_CHANNEL_REPRESENTATIONS
        and vulnerability.multi_channel_class_count > 0
    )


def _generate_inputs(analysis_run, engine, actor, *, poll_interval, timeout) -> dict:
    """Create the analysis, post settings and build the kernel files."""
    run = analysis_run.run
    model = engine.find_model(*oasis_model_triple())

    analysis_id = engine.create_analysis(
        f"cass-{run.id}", int(analysis_run.oasis_portfolio_id), model.id
    )
    document = build_analysis_settings(analysis_run, model)
    engine.upload_settings(analysis_id, document)

    analysis_run.oasis_analysis_id = str(analysis_id)
    analysis_run.save(update_fields=["oasis_analysis_id", "updated_at"])
    run.settings_hash = settings_digest(document)
    run.save(update_fields=["settings_hash", "updated_at"])

    engine.generate_inputs(analysis_id)
    job = engine.poll(
        analysis_id,
        OasisPhase.INPUTS,
        interval=poll_interval,
        timeout=timeout,
        on_update=lambda observed: _note(run, "generate_inputs", observed, actor),
    )
    _require_success(job, engine, analysis_id, OasisPhase.INPUTS, "generate the Oasis input files")

    run.advance(
        "generate_inputs",
        actor=actor,
        message="Oasis generated the kernel and financial files.",
        metrics={"oasis_analysis_id": analysis_id, "oasis_model": model.as_dict()},
    )
    return {"oasis_analysis_id": analysis_id, "oasis_model": model.as_dict()}


def _keys(analysis_run, actor) -> dict:
    """Map the published exposure to the model through the CASS keys service.

    Section 5 gives CASS keys the mapping: CASS owns the business records and
    the immutable OED, CASS keys maps exposure to the model, and OasisLMF
    builds the kernel files from that. Running our own lookup before submission
    is also what makes ``validate_inputs`` meaningful later -- two independent
    lookups over the same exposure ought to agree, and a disagreement is worth
    knowing about.
    """
    run = analysis_run.run
    model_version = analysis_run.model_version

    grid = load_grid(model_version.grid)
    # What can answer a function is what the attached hazard set carries. A
    # function demanding a measure this hazard did not compute is reported as
    # unsupported, with the value behind it, rather than answered from a
    # measure it was not built for.
    hazard_set = model_version.hazard_set
    vulnerability = load_vulnerability(
        model_version.vulnerability_set,
        supported_imts=frozenset(hazard_set.imts) if hazard_set else None,
    )

    files = exposure_services.load_files(analysis_run.exposure_version)
    # The raw text, not the coerced values: section 5 keeps reported exposure
    # immutable, and the lookup does its own decimal conversion.
    locations = [dict(row.raw) for row in files.location.rows]

    result = keys_lookup(locations, grid=grid, vulnerability=vulnerability)
    report = result.report.as_dict()

    keys_csv = _keys_csv(result.records)
    errors_csv = _keys_csv(result.failures)
    stored = {
        "keys": _store_keys_file(run, "cass_keys", keys_csv, actor),
        "errors": _store_keys_file(run, "cass_keys_errors", errors_csv, actor),
    }

    summary = {
        "source": "cass_keys",
        "grid": result.grid_reference,
        "vulnerability": result.vulnerability_reference,
        "locations": len(locations),
        # Distinct locations, not records: a location produces one record per
        # coverage and sub-peril, so a record count answers a different
        # question and is the wrong thing to compare Oasis against.
        "mapped_locations": len({item.location_id for item in result.successes}),
        **report,
    }
    analysis_run.keys_summary = summary
    analysis_run.keys_reconciled = result.report.reconciled
    analysis_run.save(update_fields=["keys_summary", "keys_reconciled", "updated_at"])

    run.advance(
        "keys",
        actor=actor,
        message=(
            f"CASS keys mapped {report['mapped_tiv']} of {report['source_tiv']} TIV "
            f"against {result.grid_reference}."
        ),
        metrics={
            "grid": result.grid_reference,
            "vulnerability": result.vulnerability_reference,
            "record_count": len(result.records),
            "failure_count": len(result.failures),
        },
    )
    return {**summary, "artifacts": stored}


def _reconcile_keys(analysis_run, actor) -> dict:
    """The section 8 gate: no run proceeds on exposure nobody has accounted for.

    Two different things can be wrong here and they are not the same, so they
    do not get the same answer.

    Value that does not add up -- successful plus not-at-risk plus failed TIV
    not equal to the published source -- means the lookup lost some. Every
    combination is supposed to produce exactly one record, so that is a defect
    in CASS rather than a fact about the portfolio, and the run fails.

    Value that adds up but could not be mapped is a fact about the portfolio,
    and section 8 requires a person to decide whether the analysis is still
    worth running. The run blocks for an approval rather than failing, which is
    what ``RunState.BLOCKED`` exists for, and proceeds once a ``run_exception``
    approval is attached and granted.
    """
    run = analysis_run.run
    summary = analysis_run.keys_summary or {}
    source_tiv = Decimal(summary.get("source_tiv", "0"))
    failed_tiv = analysis_run.unmapped_tiv
    not_at_risk_tiv = Decimal(summary.get("not_at_risk_tiv", "0"))

    if not analysis_run.keys_reconciled:
        raise AnalysisExecutionError(
            "Keys TIV does not reconcile to the published exposure: "
            f"{summary.get('accounted_tiv')} accounted against {summary.get('source_tiv')} "
            f"published, a difference of {summary.get('difference')}. Every location, "
            "coverage and sub-peril is supposed to produce exactly one response, so "
            "value that has gone missing is a defect rather than a portfolio fact."
        )

    approval = analysis_run.exception_approval
    approved = approval is not None and approval.is_cleared

    # A geometry-only run is the one that exists to find this out. It makes no
    # financial claim, so value the model cannot map is its finding rather than
    # a gate: holding it for an approval would ask somebody to accept an
    # unmapped share of a loss nobody is going to calculate.
    reports_only = not analysis_run.calculates_loss

    # One definition of the gate, shared with the API through the model, so a
    # screen cannot show a run as clear while the service holds it.
    if not reports_only and not analysis_run.may_proceed_past_keys:
        raise RunBlocked(
            f"{failed_tiv} of {source_tiv} TIV could not be mapped to the model.",
            detail=_unmapped_detail(summary),
        )

    run.advance(
        "reconcile_keys",
        actor=actor,
        message=(
            f"Keys reconcile: {summary.get('mapped_tiv')} mapped, "
            f"{not_at_risk_tiv} not at risk, {failed_tiv} failed."
            + (" Proceeding under an approved run exception." if approved else "")
            + (
                " Reported rather than held: this run calculates no loss."
                if reports_only and failed_tiv
                else ""
            )
        ),
        metrics={
            "reconciled": True,
            "failed_tiv": str(failed_tiv),
            "approved_exception": str(approval.id) if approved else "",
        },
    )
    return {
        "reconciled": True,
        "failed_tiv": str(failed_tiv),
        "not_at_risk_tiv": str(not_at_risk_tiv),
        "approved_exception": str(approval.id) if approved else None,
        "unmapped_reported_not_gated": bool(reports_only and failed_tiv),
    }


def _unmapped_detail(summary: Mapping[str, Any]) -> str:
    """Why the exposure could not be mapped, in the analyst's terms."""
    reasons = summary.get("tiv_by_reason") or {}
    if not reasons:
        return ""
    lines = [f"  {value} TIV: {reason}" for reason, value in sorted(reasons.items())]
    return "Unmapped value by reason:\n" + "\n".join(lines)


def _validate_inputs(analysis_run, engine, actor) -> dict:
    """Check what Oasis's own lookup did against the published exposure.

    Oasis runs its own lookup while generating inputs, so by this point two
    independent mappings of the same exposure exist: the CASS keys result from
    the ``keys`` stage, and this one. Both are checked here.

    Every published location must be accounted for -- mapped or reported as
    failed. One that is neither has been lost between the OED and the model,
    which is a defect rather than a business exception, so it fails rather than
    blocking. The approval gate belongs to ``reconcile_keys``, which runs
    before anything is submitted.

    Where the two lookups disagree about which locations map, the difference is
    recorded rather than raised. They are different implementations over
    different model data -- CASS keys against the CASS grid, Oasis against the
    model package's own keys server -- and a disagreement is a finding for the
    model owner, not necessarily a reason to stop.
    """
    run = analysis_run.run
    analysis_id = int(analysis_run.oasis_analysis_id)
    report = engine.keys_report(analysis_id)

    mapped = _keys_locations(report["success"], "successful")
    failed = _keys_locations(report["errors"], "failed")
    accounted = mapped | failed
    located = _location_count(analysis_run)

    summary = {
        "source": "oasis_lookup",
        "mapped_locations": len(mapped),
        "failed_locations": len(failed),
        "accounted_locations": len(accounted),
        "published_locations": located,
        # Row counts are one per location per peril per coverage type. They are
        # kept as evidence of how much the model matched, but they are not the
        # reconciliation: comparing them to a location count is what a keys
        # file that maps two perils per site would fail for no reason.
        "key_rows": _csv_rows(report["success"]),
        "error_rows": _csv_rows(report["errors"]),
        "validation_rows": _csv_rows(report["validation"]),
        "cass_keys_comparison": _cass_keys_comparison(analysis_run, mapped),
    }
    reconciled = located == 0 or len(accounted) == located

    if not reconciled:
        raise AnalysisExecutionError(
            "Oasis's lookup accounted for "
            f"{len(accounted)} of {located} published locations. "
            "A location that is neither mapped nor reported as failed has been lost "
            "between the published OED and the model, so the run cannot continue."
        )

    run.advance(
        "validate_inputs",
        actor=actor,
        message=(
            f"Oasis mapped {len(mapped)} locations and reported "
            f"{len(failed)} as unmappable."
        ),
        metrics=summary,
    )
    return summary


def _cass_keys_comparison(analysis_run, oasis_mapped: set[str]) -> dict:
    """How Oasis's lookup compares with the CASS keys result.

    Recorded rather than enforced. The two are different implementations over
    different model data -- CASS keys against the CASS grid, Oasis against the
    model package's own keys server -- so a disagreement is a finding for the
    model owner rather than automatically a reason to stop. It is stated
    explicitly when there was nothing to compare, because a silently absent
    comparison reads like one that passed.
    """
    summary = analysis_run.keys_summary or {}
    if summary.get("source") != "cass_keys":
        return {"compared": False, "reason": "The CASS keys stage did not run."}
    cass_mapped = int(summary.get("mapped_locations", 0))
    return {
        "compared": True,
        "cass_mapped_locations": cass_mapped,
        "oasis_mapped_locations": len(oasis_mapped),
        "agree": cass_mapped == len(oasis_mapped),
    }


def smoke_event_ids(root: pathlib.Path, count: int = SMOKE_EVENT_COUNT) -> list[int]:
    """The events with the largest footprints in the served package, by identifier.

    Largest by bytes of footprint, which is the number of cells and bins an
    event reaches: the events most likely to touch a portfolio wherever it
    sits. Ties go to the lower identifier, so one package always gives one
    selection.
    """
    entries = read_footprint_index((root / "model_data" / "footprint.idx").read_bytes())
    ranked = sorted(
        (item for item in entries if item.size > 0),
        key=lambda item: (-item.size, item.event_id),
    )
    return sorted(item.event_id for item in ranked[:count])


def smoke_settings(document: Mapping[str, Any], event_ids: Sequence[int]) -> dict:
    """The full settings, limited to a handful of events and one table each.

    Only the moment event loss table is asked for. An exceedance curve from
    twenty-five events describes nothing, and the question the smoke check asks
    -- does every perspective run, and are its losses bounded -- is answered
    event by event.
    """
    reduced = copy.deepcopy(dict(document))
    reduced["event_ids"] = [int(item) for item in event_ids]
    for _, summaries in _PERSPECTIVE_KEYS.values():
        for summary in reduced.get(summaries) or []:
            if isinstance(summary, dict):
                summary["ord_output"] = {"elt_moment": True}
    return reduced


def smoke_findings(
    payload: bytes, *, perspectives: Sequence[str], insured_value: Decimal
) -> dict:
    """What the smoke run's event losses say, and anything that cannot be right.

    A package that cannot be read is not evaluated rather than failed, for the
    reason the collect stage gives: the engine ran, and a table not being where
    CASS expected it is not evidence about the losses. A perspective that wrote
    no event losses at all, a loss that is not a number or is negative, and an
    event loss above the whole portfolio's insured value are all problems: each
    is a model or financial structure that would produce the same defect across
    the full event set.
    """
    try:
        package = ord_results.open_package(payload)
    except ord_results.OrdError as exc:
        return {"evaluated": False, "reason": str(exc), "problems": [], "perspectives": {}}

    problems: list[str] = []
    seen: dict[str, dict[str, int]] = {}
    for perspective in perspectives:
        label = Perspective(perspective).label.lower()
        rows = package.rows(perspective, 1, "melt")
        if not rows:
            problems.append(
                f"The smoke run wrote no {label} event losses, so that perspective "
                "produced nothing."
            )
            continue
        with_loss: set[str] = set()
        for row in rows:
            event = row.get("EventId", "?")
            mean = _loss_value(row.get("MeanLoss"))
            maximum = _loss_value(row.get("MaxLoss"))
            if mean is None or not mean.is_finite() or mean < 0:
                problems.append(
                    f"Event {event} has a {label} mean of {row.get('MeanLoss')!r}, which "
                    "is not a loss."
                )
                continue
            if mean > 0:
                with_loss.add(event)
            if (
                insured_value > 0
                and maximum is not None
                and maximum.is_finite()
                and maximum > insured_value
            ):
                problems.append(
                    f"Event {event} reaches a {label} of {maximum}, above the "
                    f"{insured_value} insured value of the whole portfolio."
                )
        seen[perspective] = {"rows": len(rows), "events_with_loss": len(with_loss)}

    return {
        "evaluated": True,
        # A defect usually repeats on every event, and twenty identical lines say
        # no more than three.
        "problems": problems[:20],
        "problem_count": len(problems),
        "perspectives": seen,
    }


def _loss_value(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return None


def _smoke(analysis_run, engine, actor, *, poll_interval, timeout) -> dict:
    """Run a handful of events through every requested perspective first.

    Section 8 asks for pre-loss smoke checks before a portfolio is admitted to
    the full event set, and the reason is cost. The defects that stop an insured
    or reinsurance run surface in the first events it calculates, and finding
    one after hours of a national event set wastes the hours. So the analysis
    runs first on the served package's largest events, their losses are
    checked, and only then are the full settings put back.

    Where no footprint index is readable -- a package CASS did not build, or a
    control plane that does not share the worker's volume -- no event set can
    be chosen, and the stage is recorded as not performed rather than passed.
    """
    run = analysis_run.run
    analysis_id = int(analysis_run.oasis_analysis_id)
    root = pathlib.Path(settings.CASS_OASIS_MODEL_ROOT)

    try:
        event_ids = smoke_event_ids(root)
    except OSError:
        return _smoke_not_performed(
            run,
            actor,
            f"No footprint index is readable at {root} from the control plane, so no "
            "reduced event set could be chosen.",
        )
    except PackageError as exc:
        raise AnalysisExecutionError(
            f"The served model package's footprint index cannot be read: {exc}"
        ) from exc
    if not event_ids:
        return _smoke_not_performed(
            run,
            actor,
            "The served package's footprint index lists no event with any footprint, "
            "so there is nothing to run.",
        )

    model = engine.find_model(*oasis_model_triple())
    full = build_analysis_settings(analysis_run, model)
    engine.upload_settings(analysis_id, smoke_settings(full, event_ids))
    engine.run(analysis_id)
    job = engine.poll(
        analysis_id,
        OasisPhase.LOSSES,
        interval=poll_interval,
        timeout=timeout,
        on_update=lambda observed: _note(run, "smoke", observed, actor),
    )
    _require_success(
        job, engine, analysis_id, OasisPhase.LOSSES, "complete the reduced-event smoke check"
    )

    sink = io.BytesIO()
    engine.download_outputs(analysis_id, sink)
    evaluation = smoke_findings(
        sink.getvalue(),
        perspectives=settings_perspectives(analysis_run),
        insured_value=Decimal(analysis_run.exposure_version.total_tiv or 0),
    )
    if evaluation["problems"]:
        raise AnalysisExecutionError(
            "The reduced-event smoke check produced losses that cannot be right, so the "
            "full event set was not run:\n- " + "\n- ".join(evaluation["problems"])
        )

    # The full document, exactly as it was hashed when the inputs were
    # generated, goes back before the loss stage: the analysis that runs has to
    # be the one the manifest records.
    if settings_digest(full) != run.settings_hash:
        raise AnalysisExecutionError(
            "The analysis settings changed between input generation and the smoke check, "
            "so the run could not say which settings produced its losses."
        )
    engine.upload_settings(analysis_id, full)

    reached = sorted(
        {counts["events_with_loss"] for counts in evaluation["perspectives"].values()}
    )
    run.advance(
        "smoke",
        actor=actor,
        message=(
            f"{len(event_ids)} of the package's largest events ran through every requested "
            "perspective"
            + (
                f"; {reached[-1]} produced a loss."
                if evaluation["evaluated"] and reached
                else "."
            )
        ),
        metrics={"event_count": len(event_ids), "evaluated": evaluation["evaluated"]},
    )
    return {"performed": True, "event_ids": event_ids, **evaluation}


def _smoke_not_performed(run, actor, reason: str) -> dict:
    run.advance(
        "smoke", actor=actor, message=f"Not performed. {reason}", metrics={"performed": False}
    )
    return {"performed": False, "reason": reason}


def _losses(analysis_run, engine, actor, *, poll_interval, timeout) -> dict:
    """Run the loss calculation."""
    run = analysis_run.run
    analysis_id = int(analysis_run.oasis_analysis_id)

    engine.run(analysis_id)
    job = engine.poll(
        analysis_id,
        OasisPhase.LOSSES,
        interval=poll_interval,
        timeout=timeout,
        on_update=lambda observed: _note(run, "losses", observed, actor),
    )
    _require_success(job, engine, analysis_id, OasisPhase.LOSSES, "calculate losses")

    run.advance(
        "losses",
        actor=actor,
        message="Oasis completed the loss calculation.",
        metrics={"raw_state": job.raw_state},
    )
    return {"raw_state": job.raw_state}


def _collect(analysis_run, engine, actor) -> dict:
    """Bring the outputs back and register them as an artifact."""
    run = analysis_run.run
    analysis_id = int(analysis_run.oasis_analysis_id)

    sink = io.BytesIO()
    engine.download_outputs(analysis_id, sink)
    payload = sink.getvalue()
    if not payload:
        raise AnalysisExecutionError(
            "Oasis reported the run complete but returned no output package."
        )

    store = get_store()
    ref = store.put_bytes(
        bucket("result"),
        f"analysis/{run.id}/oasis_output.tar.gz",
        payload,
        content_type="application/gzip",
        retention=RetentionClass.RESULT,
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
            "project": run.project,
            "role": "oasis_output",
            "original_filename": "oasis_output.tar.gz",
            "created_by": actor,
            "updated_by": actor,
        },
    )
    ArtifactLink.objects.update_or_create(
        artifact=artifact,
        subject_type="analysis_run",
        subject_id=run.id,
        role="oasis_output",
        direction="output",
        defaults={"created_by": actor},
    )

    ingested = _ingest_results(analysis_run, payload, actor)

    run.advance(
        "collect",
        actor=actor,
        message=(
            "Collected the Oasis output package and published "
            f"{len(ingested['published'])} result set(s)."
        ),
        metrics={"uri": ref.uri, "size_bytes": ref.size_bytes, "checksum": ref.checksum},
    )
    # Returned rather than written to the run here: ``execute`` assembles the
    # manifest from what each stage returns and saves it at the end, so a write
    # made directly to ``run.manifest`` at this point would be overwritten.
    return {
        "uri": ref.uri,
        "checksum": ref.checksum,
        "size_bytes": ref.size_bytes,
        **ingested,
    }


def _ingest_results(analysis_run, payload: bytes, actor) -> dict:
    """Read the ORD tables and publish one result set per perspective.

    Without this the analysis stops at a tarball: the run succeeds, the
    artifact is registered, and the results workspace stays empty forever
    because nothing ever turned the package into numbers.

    A result arrives as a draft, never approved. Section 9 requires approved
    decision output to be operationally distinct from a research run, and a
    pipeline that published its own numbers as decision-grade would make the
    reviewer role a formality. A research-prototype model version produces a
    result marked research, which approval cannot lift.

    Failure to read the package is deliberately not failure of the run. The
    calculation happened, the output is stored and checksummed, and discarding
    hours of engine time because a table was not where CASS expected it would
    be the wrong trade -- so the reason is recorded on the run and the artifact
    stays available for somebody to read by hand.
    """
    from apps.results.models import ResultSet, ResultState

    run = analysis_run.run
    model_version = analysis_run.model_version

    try:
        package = ord_results.open_package(payload)
    except ord_results.OrdError as exc:
        return {"published": [], "results_not_published": str(exc)}

    published: list[dict] = []
    requested = [str(item) for item in (analysis_run.perspectives or [])]

    # How the losses were calculated, apart from the assumption set. Recorded
    # only where the settings rebuilt here are exactly the ones the run hashed
    # when its inputs were generated; a digest of anything else would claim a
    # match nobody could check.
    document = build_analysis_settings(analysis_run)
    calculation = (
        calculation_digest(document)
        if run.settings_hash and settings_digest(document) == run.settings_hash
        else ""
    )

    for perspective in requested:
        try:
            metrics = ord_results.metrics_for(package, perspective=perspective)
        except ord_results.OrdError as exc:
            published.append({"perspective": perspective, "not_published": str(exc)})
            continue

        label = (
            f"{analysis_run.exposure_version.name} "
            f"v{analysis_run.exposure_version.version} — "
            f"{perspective.replace('_', '-')}"
        )
        result, _ = ResultSet.objects.update_or_create(
            run=run,
            perspective=perspective,
            defaults={
                "project": run.project,
                "label": label,
                # Research output stays research whatever a reviewer does: a
                # prototype's number must not become a decision number by
                # approval alone. Nor may a number from a run nobody made for
                # decisions: brief section 5.2 blocks decision-use approval for
                # the technical and research modes, and that is a property of
                # what the run was for rather than of how it went.
                "state": (
                    ResultState.RESEARCH
                    if model_version.is_research_prototype
                    or _assumption_is_unapproved(analysis_run)
                    or not analysis_run.may_produce_decision_output
                    else ResultState.DRAFT
                ),
                "run_mode": analysis_run.mode,
                "calculation_digest": calculation,
                "average_annual_loss": metrics.average_annual_loss,
                "standard_deviation": metrics.standard_deviation,
                "currency": analysis_run.run_currency
                or analysis_run.exposure_version.run_currency,
                "return_period_losses": metrics.return_period_losses,
                "model_version_reference": model_version.reference,
                "assumption_set_reference": _assumption_reference(analysis_run),
                "valuation_date": analysis_run.exposure_version.valuation_date,
                "exposure_quality": _exposure_quality(analysis_run),
                "peril_scope": model_version.peril_scope,
                "material_exclusions": _material_exclusions(analysis_run, model_version),
                # The basis travels with the number, because a package carries
                # several and an analyst has to know which one they hold.
                "uncertainty_attribution": {"ord_basis": metrics.basis},
                "created_by": actor,
                "updated_by": actor,
            },
        )
        events = _store_event_losses(run, result, package, perspective, actor)
        geography = _store_geographic_summary(
            run, result, package, perspective, analysis_run, actor
        )
        published.append(
            {
                "perspective": perspective,
                "result_set": str(result.id),
                "basis": metrics.basis,
                "events_with_loss": events,
                "geographic_summary": geography,
            }
        )

        audit.record(
            action=AuditAction.CREATE,
            subject_type="result_set",
            subject_id=result.id,
            actor=actor,
            project=run.project,
            subject_label=str(result),
            after={"state": result.state, "perspective": perspective},
        )

    return {"published": published}


def _store_event_losses(run, result, package, perspective: str, actor) -> int:
    """Keep the event loss table beside the result, and say how long it is.

    Section 3 asks the results workspace to show which events drive a number.
    The table is one row per event that produced a loss -- tens of thousands of
    them on a national event set -- so it lives in the artifact store and is
    read a page at a time, rather than in a column of the results table.
    """
    rows = ord_results.event_losses(package, perspective=perspective)
    if not rows:
        return 0

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=list(rows[0].as_dict()), lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row.as_dict())

    store = get_store()
    ref = store.put_bytes(
        bucket("result"),
        f"analysis/{run.id}/{perspective}_event_losses.csv",
        buffer.getvalue().encode("utf-8"),
        content_type="text/csv",
        retention=RetentionClass.RESULT,
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
            "project": run.project,
            "role": "event_losses",
            "original_filename": f"{perspective}_event_losses.csv",
            "created_by": actor,
            "updated_by": actor,
        },
    )
    ArtifactLink.objects.update_or_create(
        artifact=artifact,
        subject_type="result_set",
        subject_id=result.id,
        role="event_losses",
        direction="output",
        defaults={"created_by": actor},
    )
    return len(rows)


def _store_geographic_summary(
    run, result, package, perspective: str, analysis_run, actor
) -> dict:
    """Where the loss is: average annual loss by area-peril cell, beside the result.

    Section 3 asks the results workspace for geographic summaries and a map. The
    engine reports each location's average annual loss at the location summary
    level; each is placed in the cell the run's own keys mapped that location to,
    and the cells are summed. The keys rather than the coordinates decide the
    cell, so the map shows the hazard each dollar was calculated against.

    Not having one is recorded rather than failing the publication: a run from
    before the location summary was asked for, or a package without it, still
    has a portfolio number worth keeping.
    """
    try:
        losses = ord_results.location_losses(
            package,
            perspective=perspective,
            summary_level=LOCATION_SUMMARY_ID,
            fields=LOCATION_FIELDS,
        )
    except ord_results.OrdError as exc:
        return {"available": False, "reason": str(exc)}

    keys_link = (
        ArtifactLink.objects.filter(
            subject_type="analysis_run",
            subject_id__in=[run.id, analysis_run.id],
            role="cass_keys",
        )
        .select_related("artifact")
        .order_by("-created_at")
        .first()
    )
    if keys_link is None or not keys_link.artifact.is_readable:
        return {
            "available": False,
            "reason": "The run's keys are not stored, so no location's loss can be placed in a cell.",
        }
    with get_store().open(keys_link.artifact.uri) as handle:
        keys = list(csv.DictReader(io.StringIO(handle.read().decode("utf-8-sig"))))
    cell_of: dict[tuple[str, str], int] = {}
    for row in keys:
        try:
            area_peril = int(str(row.get("AreaPerilID") or "").strip())
        except ValueError:
            continue
        location = (
            str(row.get("AccNumber") or "").strip(),
            str(row.get("LocNumber") or "").strip(),
        )
        cell_of.setdefault(location, area_peril)

    try:
        grid = load_grid(analysis_run.model_version.grid)
    except ModelAssetError as exc:
        return {"available": False, "reason": str(exc)}
    bounds = {cell.area_peril_id: cell for cell in grid.cells}

    cells: dict[int, dict[str, Any]] = {}
    unplaced, unplaced_loss = 0, Decimal("0")
    for item in losses:
        area_peril = cell_of.get((item.fields["AccNumber"], item.fields["LocNumber"]))
        if area_peril is None or area_peril not in bounds:
            unplaced += 1
            unplaced_loss += item.average_annual_loss
            continue
        entry = cells.setdefault(
            area_peril,
            {"locations": 0, "average_annual_loss": Decimal("0"), "tiv": Decimal("0")},
        )
        entry["locations"] += 1
        entry["average_annual_loss"] += item.average_annual_loss
        entry["tiv"] += item.tiv or Decimal("0")

    location_total = sum((item.average_annual_loss for item in losses), Decimal("0"))
    portfolio = result.average_annual_loss
    document = {
        "perspective": perspective,
        "grid": grid.reference,
        "currency": result.currency,
        "basis": {
            "average_loss": "sample",
            "summary_level": LOCATION_SUMMARY_ID,
            "grouped_by": list(LOCATION_FIELDS),
            "placed_by": "the run's keys",
        },
        "cells": [
            {
                "area_peril_id": area_peril,
                "min_latitude": str(bounds[area_peril].min_latitude),
                "max_latitude": str(bounds[area_peril].max_latitude),
                "min_longitude": str(bounds[area_peril].min_longitude),
                "max_longitude": str(bounds[area_peril].max_longitude),
                "locations": entry["locations"],
                "average_annual_loss": str(entry["average_annual_loss"]),
                "tiv": str(entry["tiv"]),
            }
            for area_peril, entry in sorted(
                cells.items(), key=lambda pair: pair[1]["average_annual_loss"], reverse=True
            )
        ],
        "locations_with_loss": len(losses),
        "unplaced_locations": unplaced,
        "unplaced_loss": str(unplaced_loss),
        "location_total": str(location_total),
        "portfolio_average_annual_loss": str(portfolio) if portfolio is not None else None,
        # The location losses are the portfolio's split by place, so they should
        # add back to it; the table rounds each row, so a few cents is the table
        # and anything more is worth somebody's attention.
        "difference": str(location_total - portfolio) if portfolio is not None else None,
    }

    ref = get_store().put_bytes(
        bucket("result"),
        f"analysis/{run.id}/{perspective}_geographic_summary.json",
        json.dumps(document, indent=2).encode("utf-8"),
        content_type="application/json",
        retention=RetentionClass.RESULT,
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
            "project": run.project,
            "role": "geographic_summary",
            "original_filename": f"{perspective}_geographic_summary.json",
            "created_by": actor,
            "updated_by": actor,
        },
    )
    ArtifactLink.objects.update_or_create(
        artifact=artifact,
        subject_type="result_set",
        subject_id=result.id,
        role="geographic_summary",
        direction="output",
        defaults={"created_by": actor},
    )
    return {
        "available": True,
        "cells": len(cells),
        "locations_with_loss": len(losses),
        "unplaced_locations": unplaced,
    }


def _assumption_is_unapproved(analysis_run) -> bool:
    """Whether the run rests on an assumption set nobody has approved.

    A result under a draft assumption set is research output whatever a
    reviewer later does, for the reason a prototype model's is: approving the
    number would approve the assumption behind it without anyone having
    reviewed the assumption.
    """
    chosen = analysis_run.assumption_set
    return chosen is not None and chosen.publication_state not in (
        PublicationState.APPROVED,
        PublicationState.PUBLISHED,
    )


def _assumption_reference(analysis_run) -> str:
    """What the result's unknown attributes were weighted under."""
    if analysis_run.assumption_set_id:
        return analysis_run.assumption_set.reference
    if analysis_run.enrichment_run_id:
        return "baseline weights"
    return ""


def _exposure_quality(analysis_run) -> dict:
    """What the keys lookup said about how much of the book was modelled."""
    summary = analysis_run.keys_summary or {}
    return {
        "location_count": analysis_run.exposure_version.location_count,
        "source_tiv": str(analysis_run.exposure_version.total_tiv or ""),
        "successful_tiv": summary.get("successful_tiv"),
        "not_at_risk_tiv": summary.get("not_at_risk_tiv"),
        "unmapped_tiv": summary.get("failed_tiv"),
        "keys_reconciled": analysis_run.keys_reconciled,
        # Section 8: the rate a number rests on travels with the number, not
        # only with the run that produced it.
        "currency_conversion": analysis_run.currency_conversion or {},
    }


def _material_exclusions(analysis_run, model_version) -> list[str]:
    """What this number does not include, from the exposure and the model.

    Two sources, and both matter. A sub-peril the portfolio covers but the
    release does not model is missing loss; a sub-peril the model version
    declares excluded is missing loss as well. Reporting only one of them would
    understate what the number leaves out.
    """
    exclusions: list[str] = list(analysis_run.exposure_version.unmodelled_subperils or [])
    for peril, treatment in (model_version.peril_scope or {}).items():
        if not isinstance(treatment, Mapping):
            continue
        if str(treatment.get("treatment", "")).lower() == "excluded":
            exclusions.append(peril)
    return sorted(set(exclusions))


# -- review -----------------------------------------------------------------

def stage_approval(analysis_run, stage: str):
    """The latest run-exception request for one gate of this run, if any.

    Tagged with the stage it was asked at, because a run can reach more than one
    gate and clearing unmapped value at the keys gate says nothing about a loss
    curve that falls with return period.
    """
    return (
        Approval.objects.filter(
            gate=Approval.Gate.RUN_EXCEPTION,
            subject_type="analysis_run",
            subject_id=analysis_run.id,
            evidence__stage=stage,
        )
        .order_by("-created_at")
        .first()
    )


def review_checks(
    results: Mapping[str, Any],
    *,
    requested: Sequence[str],
    insured_value: Decimal,
    keys_reconciled: bool | None,
) -> list[dict]:
    """The operational checks a result must pass before anyone relies on it.

    Each check is ``passed`` true or false, or ``None`` where there was nothing
    to evaluate -- a perspective whose package could not be read published no
    result, which the collect stage has already recorded, and inventing a
    failure for it would hold a run over a table rather than a number.

    None of these is a scientific judgement. They are the conditions under
    which a number cannot be a loss at all, and a result that fails one is
    wrong whatever the model.
    """
    checks: list[dict] = []

    def add(check: str, passed: bool | None, detail: str) -> None:
        checks.append({"check": check, "passed": passed, "detail": detail})

    add(
        "Keys reconcile to the published source",
        keys_reconciled is True,
        "Every location, coverage and sub-peril produced one response."
        if keys_reconciled
        else "The keys accounting did not balance.",
    )

    for perspective in requested:
        label = Perspective(perspective).label
        result = results.get(perspective)
        if result is None:
            add(
                f"{label} was published",
                None,
                "No result set was published, because the output package could not be "
                "read; the reason is on the collect stage. There is nothing to review.",
            )
            continue

        average = result.average_annual_loss
        add(
            f"{label}: the average annual loss is not negative",
            average is not None and average >= 0,
            f"{average}" if average is not None else "No average annual loss was reported.",
        )
        deviation = result.standard_deviation
        if deviation is not None:
            add(
                f"{label}: the standard deviation is not negative",
                deviation >= 0,
                f"{deviation}",
            )

        curve = _curve(result.return_period_losses)
        negative = [(period, loss) for period, loss in curve if loss < 0]
        add(
            f"{label}: return-period losses are not negative",
            not negative,
            "; ".join(f"{period} years: {loss}" for period, loss in negative)
            or f"{len(curve)} return periods.",
        )
        falling = [
            (low, low_loss, high, high_loss)
            for (low, low_loss), (high, high_loss) in zip(curve, curve[1:], strict=False)
            if high_loss < low_loss
        ]
        add(
            f"{label}: losses rise with return period",
            not falling,
            "; ".join(
                f"{high} years ({high_loss}) is below {low} years ({low_loss})"
                for low, low_loss, high, high_loss in falling
            )
            or "The curve does not fall anywhere.",
        )
        if insured_value > 0:
            largest = max([average or Decimal(0)] + [loss for _, loss in curve])
            add(
                f"{label}: no loss exceeds the portfolio's insured value",
                largest <= insured_value,
                f"The largest is {largest} against {insured_value} insured.",
            )

    for gross, net in (
        (str(Perspective.GROUND_UP), str(Perspective.INSURED)),
        (str(Perspective.INSURED), str(Perspective.REINSURANCE)),
    ):
        upper, lower = results.get(gross), results.get(net)
        if (
            upper is None
            or lower is None
            or upper.average_annual_loss is None
            or lower.average_annual_loss is None
        ):
            continue
        add(
            f"{Perspective(net).label} does not exceed {Perspective(gross).label.lower()}",
            lower.average_annual_loss <= upper.average_annual_loss,
            f"{lower.average_annual_loss} against {upper.average_annual_loss}.",
        )
    return checks


def _curve(losses: Mapping[str, Any] | None) -> list[tuple[Decimal, Decimal]]:
    """A stored return-period curve as ordered decimal pairs."""
    pairs = []
    for period, loss in (losses or {}).items():
        try:
            pairs.append((Decimal(str(period)), Decimal(str(loss))))
        except InvalidOperation:
            continue
    return sorted(pairs)


def _review(analysis_run, actor) -> dict:
    """Check the published results, and hold them when they cannot be losses.

    Section 8's review gate holds results until operational and scientific
    checks pass. The operational half is here: the checks in
    :func:`review_checks`. A result failing one keeps the run at the gate, with
    what failed, until a reviewer who did not ask records a run exception --
    and while it is held, the result cannot be approved for decisions, because
    only a successful run may release one.

    The scientific half is not a check CASS can compute. What the model version
    is -- a research prototype or an approved release -- is recorded beside the
    checks so the reviewer reads both together.
    """
    from apps.results.models import ResultSet

    run = analysis_run.run
    model_version = analysis_run.model_version
    results = {item.perspective: item for item in ResultSet.objects.filter(run=run)}
    checks = review_checks(
        results,
        requested=[str(item) for item in (analysis_run.perspectives or [])],
        insured_value=Decimal(analysis_run.exposure_version.total_tiv or 0),
        keys_reconciled=analysis_run.keys_reconciled,
    )
    failed = [item for item in checks if item["passed"] is False]
    record: dict[str, Any] = {
        "checks": checks,
        "failed": len(failed),
        "model_version": {
            "reference": model_version.reference,
            "publication_state": model_version.publication_state,
            "research_prototype": model_version.is_research_prototype,
        },
    }

    if failed:
        approval = stage_approval(analysis_run, "review")
        if approval is None or not approval.is_cleared:
            raise RunBlocked(
                f"{len(failed)} result check(s) did not pass, so the results are held "
                "until a reviewer decides.",
                detail="\n".join(f"  {item['check']}: {item['detail']}" for item in failed),
            )
        record["approved_exception"] = str(approval.id)

    run.advance(
        "review",
        actor=actor,
        message=(
            f"{len(checks) - len(failed)} of {len(checks)} result checks passed"
            + (" and the rest were released by an approved exception." if failed else ".")
        ),
        metrics={"checks": len(checks), "failed": len(failed)},
    )
    return record


# -- the driver -------------------------------------------------------------

def execute(
    analysis_run: AnalysisRun,
    *,
    adapter=None,
    actor=None,
    poll_interval: float = 5.0,
    timeout: float | None = None,
) -> AnalysisRun:
    """Run one analysis through Oasis, from published OED to collected output.

    Returns the run with its manifest complete. Raises on failure, having first
    recorded the failure on the run so the monitor can explain it.
    """
    run = analysis_run.run
    engine = adapter if adapter is not None else oasis_adapter()

    # Section 11's declared envelope. The profile's limit becomes the engine
    # polls' timeout, so a calculation that hangs is abandoned rather than
    # holding a worker for ever, and it is checked between stages so a run that
    # creeps past it stops at a boundary with a reason rather than inside one.
    allowed = admission.time_limit(run)
    if timeout is None:
        timeout = allowed
    deadline = time.monotonic() + allowed if allowed else None

    #: Where a resumed run picks up. A run blocked at a gate has already done
    #: everything before it, and redoing that would republish the OED and leave
    #: an orphan portfolio on the engine -- and ``advance`` would refuse the
    #: backwards move anyway.
    resume_at = run.stage if run.run_state is RunState.BLOCKED else ""

    if run.run_state is RunState.DRAFT:
        run.transition(RunState.QUEUED, actor=actor)
    if run.run_state is RunState.BLOCKED:
        run.transition(RunState.QUEUED, actor=actor, save=False)
        # The gate no longer applies. Its history stays in the stage events and
        # the audit trail; leaving it on the run would show a resumed analysis
        # as though it were still waiting.
        run.gate_summary = ""
        run.gate_detail = ""
        run.save()
    if run.run_state is not RunState.QUEUED:
        raise AnalysisExecutionError(
            f"A run in state {run.state} cannot be executed. "
            "Only a draft, queued or approved-and-resumed run may start."
        )
    run.transition(RunState.RUNNING, actor=actor)

    manifest = dict(run.manifest or {})
    # Merged rather than replaced: a resumed run keeps what its first attempt
    # recorded, including a smoke check that could not run.
    not_performed = dict(manifest.get("stages_not_performed") or {})
    not_performed.update(UNPERFORMED_STAGES)
    manifest["stages_not_performed"] = not_performed

    #: Each step is named by the stage it performs rather than by the stage the
    #: run last completed. A failure has to be located where it happened: a
    #: loss calculation that dies reported against ``validate_inputs``, because
    #: that was the last stage to finish, sends the analyst to the wrong place.
    steps = (
        (
            "validate_exposure",
            "exposure_validation",
            lambda: _validate_exposure(analysis_run, actor),
        ),
        ("enrich", "enrichment", lambda: _enrich(analysis_run, actor)),
        ("publish_oed", "portfolio", lambda: _publish_oed(analysis_run, engine, actor)),
        ("keys", "keys", lambda: _keys(analysis_run, actor)),
        ("reconcile_keys", "reconciliation", lambda: _reconcile_keys(analysis_run, actor)),
        (
            "generate_inputs",
            "inputs",
            lambda: _generate_inputs(
                analysis_run, engine, actor, poll_interval=poll_interval, timeout=timeout
            ),
        ),
        (
            "validate_inputs",
            "oasis_keys",
            lambda: _validate_inputs(analysis_run, engine, actor),
        ),
        (
            "smoke",
            "smoke",
            lambda: _smoke(
                analysis_run, engine, actor, poll_interval=poll_interval, timeout=timeout
            ),
        ),
        (
            "losses",
            "losses",
            lambda: _losses(
                analysis_run, engine, actor, poll_interval=poll_interval, timeout=timeout
            ),
        ),
        ("collect", "output", lambda: _collect(analysis_run, engine, actor)),
        ("review", "review", lambda: _review(analysis_run, actor)),
    )

    pipeline = run.pipeline

    #: Brief section 5.2: a geometry-only run maps the book to the model and
    #: calculates no portfolio loss, so it performs the stages up to the keys
    #: reconciliation and stops. The rest are recorded as not performed with
    #: the reason, because a manifest that simply lacked them would read as a
    #: run that failed to do them.
    manifest["mode"] = analysis_run.mode
    if not analysis_run.calculates_loss:
        steps = tuple(item for item in steps if item[0] in GEOMETRY_ONLY_STAGES)
        for stage_key in pipeline.keys():
            if stage_key not in GEOMETRY_ONLY_STAGES:
                not_performed[stage_key] = (
                    "Geometry-only run: it maps exposure to the model and makes no "
                    "financial claim, so nothing is submitted to the engine."
                )

    start = pipeline.index_of(resume_at) if resume_at else -1

    stage = ENGINE_STAGES[0]
    checked_engine = False
    try:
        for next_stage, key, step in steps:
            position = pipeline.index_of(next_stage)
            if position < start:
                continue
            # Carried out of the loop so the failure handler can name the stage
            # that raised rather than the last one that finished.
            stage = next_stage

            if deadline is not None and time.monotonic() > deadline:
                raise AnalysisExecutionError(
                    f"This run has used the {run.execution_profile} profile's whole "
                    f"time limit of {int(allowed)} seconds and stopped before "
                    f"{next_stage}. Run it on a profile that allows longer, or "
                    "reduce what it is asked to do."
                )

            # A run resumed at the review gate reaches no engine stage, so it is
            # not asked about its version or its package again: a package
            # rebuilt since the losses ran would otherwise hold a finished run.
            if not checked_engine and next_stage in ENGINE_CALLING_STAGES:
                # Before anything is submitted: section 18 refuses an untested
                # engine rather than producing a result nobody can defend, and a
                # worker serving another version's package would produce one.
                manifest["engine"] = engine.check_compatible().as_dict()
                manifest["package"] = _served_package(analysis_run)
                checked_engine = True

            outcome = step()
            manifest[key] = outcome
            if next_stage == "smoke":
                if outcome.get("performed"):
                    not_performed.pop("smoke", None)
                else:
                    not_performed["smoke"] = outcome.get("reason", "")

        manifest["exposure_version"] = str(analysis_run.exposure_version_id)
        manifest["model_version"] = str(analysis_run.model_version_id)
        manifest["settings_hash"] = run.settings_hash
    except RunBlocked as exc:
        # The evidence so far is kept on the run. A resumed run starts from the
        # gate, and a manifest dropped here would lose every stage before it.
        run.manifest = manifest
        _block(run, exc, stage=stage, actor=actor)
        raise
    except (AdapterError, AnalysisExecutionError, ModelAssetError) as exc:
        run.manifest = manifest
        _fail(run, exc, stage=stage, actor=actor)
        raise

    run.manifest = manifest
    run.save(update_fields=["manifest", "updated_at"])
    # The stage a run finishes at is the last one it performed. A geometry-only
    # run that claimed to finish at ``review`` would claim results were
    # reviewed, and it published none.
    run.transition(RunState.SUCCEEDED, actor=actor, stage=steps[-1][0])

    audit.record(
        action=AuditAction.SUBMIT,
        subject_type="analysis_run",
        subject_id=run.id,
        actor=actor,
        project=run.project,
        subject_label=str(run),
        after={
            "state": run.state,
            "oasis_analysis_id": analysis_run.oasis_analysis_id,
            "settings_hash": run.settings_hash,
        },
    )
    return analysis_run


def cancel(analysis_run: AnalysisRun, *, adapter=None, actor=None) -> AnalysisRun:
    """Stop the run on the engine as well as in CASS.

    Section 11 requires cancellation to free the resource envelope. Marking the
    CASS record cancelled while an Oasis worker keeps going would leave the
    queue accounting wrong and the compute still spent.
    """
    run = analysis_run.run
    if run.run_state is not RunState.CANCELLING:
        run.transition(RunState.CANCELLING, actor=actor)

    if analysis_run.oasis_analysis_id:
        engine = adapter if adapter is not None else oasis_adapter()
        phase = (
            OasisPhase.LOSSES
            if run.stage in ("losses", "collect")
            else OasisPhase.INPUTS
        )
        try:
            engine.cancel(int(analysis_run.oasis_analysis_id), phase)
        except AdapterError as exc:
            # The engine could not be told. Say so rather than reporting a
            # clean cancellation that did not reach the worker.
            run.transition(
                RunState.FAILED,
                actor=actor,
                failure_summary="Cancellation could not be delivered to Oasis.",
                failure_detail=exc.detail or exc.summary,
            )
            raise

    run.transition(RunState.CANCELLED, actor=actor)
    audit.record(
        action=AuditAction.CANCEL,
        subject_type="analysis_run",
        subject_id=run.id,
        actor=actor,
        project=run.project,
        subject_label=str(run),
        after={"state": run.state},
    )
    return analysis_run


# -- helpers ----------------------------------------------------------------

def _note(run, stage: str, job, actor) -> None:
    """Record one engine observation against the run, for the monitor.

    Only meaningful changes are written. Polling a ten-minute calculation every
    five seconds would otherwise bury the stage history in a hundred identical
    rows.

    The fraction is the exception, and is why it is kept on the run rather than
    in the history: sub-tasks complete while the engine's state word does not
    change, which is precisely the stretch an analyst is watching. It is
    written first so it survives the return below.
    """
    from .models import RunStageEvent

    run.record_stage_progress(
        job.progress, "sub-tasks" if job.progress is not None else ""
    )

    latest = (
        RunStageEvent.objects.filter(run=run, stage=stage).order_by("-created_at").first()
    )
    if latest is not None and latest.metrics.get("raw_state") == job.raw_state:
        return
    RunStageEvent.objects.create(
        run=run,
        stage=stage,
        state=run.state,
        message=job.message[:500],
        metrics={"raw_state": job.raw_state, "progress": job.progress},
        created_by=actor,
    )


def _require_success(job, engine, analysis_id, phase, what: str) -> None:
    """Turn a non-successful engine job into an intelligible failure."""
    if job.state is EngineState.SUCCEEDED:
        return
    if job.state is EngineState.CANCELLED:
        raise AnalysisExecutionError(f"Oasis cancelled the request to {what}.")
    detail = engine.failure_log(analysis_id, phase)
    raise AnalysisExecutionError(
        f"Oasis could not {what}. {job.message}"
        + (f"\n\nEngine log:\n{detail}" if detail else "")
    )


def _block(run, exc: RunBlocked, *, stage: str, actor) -> None:
    """Hold the run at a gate and record what a person has to decide.

    A blocked run keeps its progress fraction and its evidence. It has not
    failed and it is not retried: it resumes from this stage once the approval
    exists, which is why nothing before this point is undone.
    """
    progress = run.progress
    with transaction.atomic():
        run.transition(
            RunState.BLOCKED,
            actor=actor,
            stage=stage,
            # Carried into the stage event as the message; the gate fields
            # below are what the run record itself keeps.
            failure_summary=exc.summary,
            save=False,
        )
        run.progress = progress
        run.gate_summary = exc.summary[:500]
        run.gate_detail = exc.detail
        run.save()

    audit.record(
        action=AuditAction.UPDATE,
        subject_type="analysis_run",
        subject_id=run.id,
        actor=actor,
        project=run.project,
        subject_label=str(run),
        after={"state": run.state, "stage": stage},
        detail=exc.summary,
    )


def _fail(run, exc: Exception, *, stage: str, actor) -> None:
    """Record a failure against the stage that raised it.

    ``transition`` sets the progress fraction from the stage it is given, and
    a failed stage did not complete, so the fraction is restored afterwards:
    a run that died part-way through the losses must not read as though the
    losses finished.
    """
    summary = getattr(exc, "summary", None) or str(exc)
    detail = getattr(exc, "detail", "") or ""
    progress = run.progress
    with transaction.atomic():
        run.transition(
            RunState.FAILED,
            actor=actor,
            stage=stage,
            failure_summary=summary,
            failure_detail=detail or str(exc),
            save=False,
        )
        run.progress = progress
        run.save()


def _keys_csv(records) -> bytes:
    """The keys file, in the shape section 8 asks for."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "LocID",
            "AccNumber",
            "LocNumber",
            "PerilID",
            "CoverageTypeID",
            "AreaPerilID",
            "VulnerabilityID",
            # A class spanning intensity measures produces one row per measure,
            # each carrying its share. Every other row carries 1, so the column
            # is meaningful without having to know which case a row is.
            "ChannelWeight",
            "Status",
            "Message",
        ],
        lineterminator="\n",
    )
    writer.writeheader()
    for record in records:
        writer.writerow(record.as_row())
    return buffer.getvalue().encode("utf-8")


def _store_keys_file(
    run, role: str, payload: bytes, actor, *, retention=RetentionClass.DIAGNOSTIC
) -> str:
    """Register one keys output against the run and return its URI.

    Kept as an artifact rather than a column because section 8 requires the
    keys and error files themselves to be retrievable, not merely counted: an
    analyst asking which locations failed needs the rows, not a total.
    """
    store = get_store()
    ref = store.put_bytes(
        bucket("portfolio"),
        f"analysis/{run.id}/{role}.csv",
        payload,
        content_type="text/csv",
        retention=retention,
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
            "project": run.project,
            "role": role,
            "original_filename": f"{role}.csv",
            "created_by": actor,
            "updated_by": actor,
        },
    )
    ArtifactLink.objects.update_or_create(
        artifact=artifact,
        subject_type="analysis_run",
        subject_id=run.id,
        role=role,
        direction="output",
        defaults={"created_by": actor},
    )
    return ref.uri


def _csv_rows(text: str) -> int:
    """Count data rows in a CSV payload, ignoring the header and blank lines."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return max(0, len(lines) - 1) if lines else 0


#: Columns an Oasis keys file may use for the location identity, best first.
#: ``LocNumber`` is the OED identifier and the one the published exposure
#: shares; ``loc_id`` is Oasis's own sequential index and only a fallback.
#:
#: A location number is qualified by its account wherever the file carries one.
#: OED makes the number unique within an account rather than within a
#: portfolio, so a book where several businesses each schedule a location 1
#: would otherwise reconcile three real locations down to one and report a
#: complete mapping as a shortfall.
_LOCATION_COLUMNS = ("locnumber", "loc_id", "locid")
_ACCOUNT_COLUMN = "accnumber"


def _keys_locations(text: str, what: str) -> set[str]:
    """The distinct locations a keys file accounts for.

    Oasis writes one row per location per peril per coverage type, so ten
    locations covering two perils produce twenty rows. Counting rows and
    comparing them to a location count fails a perfectly good run, which is
    exactly what the first live PiWind run did.

    A file whose header carries no recognisable location column cannot be
    reconciled at all, and section 8 does not allow proceeding on an
    unreconciled mapping, so that is raised rather than treated as empty.
    """
    if not (text or "").strip():
        return set()

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return set()

    columns = {name.strip().lower(): position for position, name in enumerate(header)}
    for candidate in _LOCATION_COLUMNS:
        if candidate in columns:
            index = columns[candidate]
            break
    else:
        raise AnalysisExecutionError(
            f"Oasis's {what} keys output has no location identifier column, so the "
            "mapping cannot be reconciled against the published exposure. "
            f"Columns present: {', '.join(header)}."
        )

    # Only when the identifier is the OED number. Oasis's own ``loc_id`` is
    # already unique across the portfolio, and qualifying it would invent a
    # distinction the file does not make.
    account = columns.get(_ACCOUNT_COLUMN) if candidate == "locnumber" else None

    identities: set[str] = set()
    for row in reader:
        if len(row) <= index or not row[index].strip():
            continue
        number = row[index].strip()
        if account is not None and len(row) > account and row[account].strip():
            identities.add(f"{row[account].strip()}/{number}")
        else:
            identities.add(number)
    return identities


def _location_count(analysis_run) -> int:
    """How many locations the published exposure version holds.

    Taken from the count validation recorded on the version, which is the same
    number the exposure workspace showed the analyst, so the two cannot
    disagree about what was published.
    """
    return int(analysis_run.exposure_version.location_count or 0)
