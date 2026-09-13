"""Registering an OpenQuake calculation as a hazard set the platform can run.

The last missing leg. Exposure has been real since the intake template, the
damage relationships since the GEM build, and this is the ground motion they
are read against -- the piece without which the platform could hold a portfolio
and never produce a number.

What gets stored is the whole hazard set, not a summary of it: one footprint
per intensity measure, the occurrence table, an intensity-bin dictionary per
measure, and the job that produced them. The job matters as much as the output.
A footprint is a large table of numbers that looks the same whatever it came
from, and the only way to know whether two runs are comparable is to compare
the calculations behind them -- so the job configuration, its checksum, the
engine version and the calculation's own checksum are all recorded.

Two refusals, and both are about a footprint that would look complete.

**Clipped hazard blocks publication rather than warning.** Ground motion above
the top intensity bin is discarded by the accumulator, and those values are the
strongest the calculation produced. A set that clips is not slightly wrong at
the mean; it is wrong exactly where a reinsurance loss lives.

**A hazard set is not attached to a model version unless it carries every
measure the vulnerability functions demand.** Half the measures produces a
model that answers half its own vulnerability set and reports zero for the
rest, which is indistinguishable from an event that did no damage.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Any

from django.db import transaction

from cass_converter import hazard_build, pilot_bins
from cass_converter.hazard_build import HazardBuildError
from cass_converter.hazard_build import HazardSet as ConvertedHazard
from cass_converter.hazard_job import HazardJob

from .assets import attach_hazard_asset
from .models import (
    INTERNAL_USE_LICENCE,
    AreaPerilGrid,
    HazardSet,
    ModelVersion,
    PublicationState,
)


class HazardRegistrationError(Exception):
    """Raised when a calculation cannot be registered as a hazard set."""


@dataclasses.dataclass(frozen=True, slots=True)
class SourceStatement:
    """What the operator asserts about the seismic sources behind a calculation.

    The source model is the single largest determinant of the answer and it is
    not derivable from the export -- OpenQuake records that a source model was
    used, not whose it is or what may be done with it. So it is stated, with
    the same shape as the vulnerability licence: a clearance needs a reference,
    because an unevidenced one in a governance record is worse than an honest
    absence.
    """

    model: str
    licence: str = ""
    cleared: bool = True
    reference: str = INTERNAL_USE_LICENCE
    ground_motion_models: tuple[str, ...] = ()
    checksum: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise HazardRegistrationError(
                "A hazard set must name its seismic source model. It is the largest "
                "single determinant of the answer and nothing in the export records "
                "which one was used."
            )
        if self.cleared and not self.reference.strip():
            raise HazardRegistrationError(
                "A source licence clearance needs a reference -- the basis or "
                "approval that grants it."
            )

    def as_note(self) -> str:
        if not self.cleared:
            return (
                f"Use of {self.model} has not been cleared. This hazard set may be "
                "used for research and platform development and not for a pricing "
                "or reserving decision."
            ) + (f" {self.note}" if self.note else "")
        return f"Cleared under {self.reference}." + (
            f" {self.note}" if self.note else ""
        )


def build(
    directory: str | pathlib.Path,
    *,
    country_code: str,
    label: str = "",
    job: HazardJob | None = None,
) -> ConvertedHazard:
    """Read an exported calculation into the converter's hazard set."""
    try:
        return hazard_build.build_hazard(
            directory,
            country_code=country_code,
            intensity_bins=pilot_bins.intensity_bins(),
            label=label,
            job=job,
        )
    except HazardBuildError as exc:
        raise HazardRegistrationError(str(exc)) from exc


@transaction.atomic
def register(
    directory: str | pathlib.Path,
    *,
    country_code: str,
    version: str,
    source: SourceStatement,
    grid: AreaPerilGrid | None = None,
    label: str = "",
    job: HazardJob | None = None,
    actor=None,
) -> tuple[HazardSet, ConvertedHazard]:
    """Register one calculation as a versioned hazard set with its tables.

    Idempotent by version. Re-registering replaces the stored files and leaves
    the registry record in place, so running this twice does not produce two
    sets whose event identifiers mean different things.
    """
    converted = build(directory, country_code=country_code, label=label, job=job)
    code = converted.country_code

    if grid is None:
        grid = (
            AreaPerilGrid.objects.filter(country_code=code)
            .order_by("-created_at")
            .first()
        )
    if grid is None:
        raise HazardRegistrationError(
            f"No area-peril grid is registered for {code}, so the cells this "
            "footprint names would refer to nothing. Register the grid first."
        )

    cells = {row.area_peril_id for row in converted.footprint}
    hazard_set, _ = HazardSet.objects.update_or_create(
        country_code=code,
        version=version,
        defaults={
            "label": converted.label,
            "source_model": source.model,
            "source_model_checksum": source.checksum[:64],
            "ground_motion_models": list(source.ground_motion_models),
            "licence": source.licence,
            "licence_cleared": source.cleared,
            "licence_note": source.as_note(),
            "grid": grid,
            "engine_version": converted.metadata.engine_version[:32],
            "calculation_checksum": converted.metadata.checksum[:64],
            "job_checksum": (
                hazard_build.hazard_job.job_checksum(job) if job is not None else ""
            ),
            "investigation_time": converted.metadata.investigation_time or 0.0,
            "stochastic_event_sets": converted.metadata.ses_per_logic_tree_path or 0,
            "event_count": len(converted.events),
            "cell_count": len(cells),
            "footprint_row_count": len(converted.footprint),
            "imts": list(converted.imts),
            "samples_above_range": converted.metrics.samples_above_range,
            "conversion_report": hazard_build.hazard_report(converted),
            "publication_state": PublicationState.DRAFT,
            "notes": _notes(converted, source),
            "updated_by": actor,
        },
    )

    for name, payload in hazard_build.tables(converted).items():
        attach_hazard_asset(hazard_set, name, payload, actor=actor)
    if job is not None:
        for name, payload in hazard_build.hazard_job.files(job).items():
            attach_hazard_asset(hazard_set, f"job_{name}", payload, actor=actor)
    attach_hazard_asset(
        hazard_set,
        "conversion_report.json",
        json.dumps(
            hazard_build.hazard_report(converted), indent=2, sort_keys=True
        ).encode("utf-8"),
        actor=actor,
    )
    return hazard_set, converted


@transaction.atomic
def attach(model_version: ModelVersion, hazard_set: HazardSet, *, actor=None) -> ModelVersion:
    """Point a model version at a hazard set, if the two can work together."""
    if model_version.country_code.upper() != hazard_set.country_code.upper():
        raise HazardRegistrationError(
            f"The hazard set is for {hazard_set.country_code} and the model version "
            f"for {model_version.country_code}. Attaching them would apply one "
            "country's ground motion to another's buildings."
        )
    if model_version.grid_id != hazard_set.grid_id:
        raise HazardRegistrationError(
            f"The hazard set was computed on {hazard_set.grid} and the model version "
            f"uses {model_version.grid}. Area-peril identifiers are only stable "
            "within a grid version, so the footprint would name the wrong cells."
        )

    demanded = set(model_version.vulnerability_set.imts_used)
    missing = sorted(demanded - set(hazard_set.imts))
    if missing:
        raise HazardRegistrationError(
            f"{hazard_set} carries {', '.join(hazard_set.imts) or 'no measures'}, "
            f"and this version's vulnerability functions demand {', '.join(missing)} "
            "as well. Attaching it would leave those functions answered by nothing "
            "and reporting zero, which is indistinguishable from no damage."
        )

    model_version.hazard_set = hazard_set
    model_version.hazard_source_model = hazard_set.source_model
    model_version.hazard_source_licence = hazard_set.licence
    model_version.openquake_version = hazard_set.engine_version
    model_version.converter_version = hazard_build.HAZARD_BUILD_VERSION
    model_version.imts = sorted(set(model_version.imts) | set(hazard_set.imts))
    model_version.updated_by = actor
    model_version.save()
    return model_version


def _notes(converted: ConvertedHazard, source: SourceStatement) -> str:
    lines = [
        f"Event-based calculation on {converted.metadata.engine_version}, "
        f"{len(converted.events)} events over "
        f"{converted.metadata.effective_time:.0f} years "
        f"({len(converted.events) / converted.metadata.effective_time:.4f}/year).",
        f"Sources: {source.model}.",
    ]
    if converted.metrics.samples_below_range:
        lines.append(
            f"{converted.metrics.samples_below_range} ground-motion values fell "
            "below the lowest intensity bin and produce no footprint row. This is "
            "shaking too weak to damage anything and omitting it is what keeps the "
            "footprint a manageable size."
        )
    if converted.problems:
        lines.append("Problems found in conversion:")
        lines.extend(f"- {item}" for item in converted.problems)
    return "\n".join(lines)


def report(hazard_set: HazardSet) -> dict[str, Any]:
    """What was registered, for the operator who ran it."""
    return {
        "reference": hazard_set.reference,
        "source_model": hazard_set.source_model,
        "engine_version": hazard_set.engine_version,
        "events": hazard_set.event_count,
        "effective_time": hazard_set.effective_time,
        "annual_event_rate": hazard_set.annual_event_rate,
        "cells": hazard_set.cell_count,
        "footprint_rows": hazard_set.footprint_row_count,
        "imts": list(hazard_set.imts),
        "clips_the_hazard": hazard_set.clips_the_hazard,
        "publication_blockers": hazard_set.publication_blockers(),
    }
