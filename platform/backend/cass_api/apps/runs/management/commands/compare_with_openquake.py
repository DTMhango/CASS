"""Compare an analysis run against OpenQuake's own calculation of it.

Work package 4 step 9, and the measurement ADR 8 said was outstanding: run the
same portfolio, on the same events, through GEM's own vulnerability functions in
OpenQuake, and see what the Oasis representation did to the answer.

Run against a finished analysis run::

    python manage.py compare_with_openquake --run <run id> --gem-root /models/gem/v2026.0.0

Everything it needs is already on the run: the keys it mapped, the exposure it
published, the vulnerability dictionary behind the identifiers, and the hazard
set's calculation, which the risk job is chained onto so both sides read one set
of ground-motion fields. The GEM functions are the only outside input, because
the licensed release is not in version control.

It decides nothing. The report it stores carries the ratios and says they are
undecided unless a tolerance is passed, in the same way the conversion QA gate
reports (section 7): a comparison that graded itself would be a formality.
"""

from __future__ import annotations

import csv
import io
import json
import pathlib
import time
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.artifacts.models import Artifact, ArtifactLink, ArtifactState
from apps.common.engines import openquake_adapter
from apps.common.storage import bucket, get_store
from apps.exposure import services as exposure_services
from apps.modelregistry.assets import (
    VULNERABILITY_DICTIONARY_ROLE,
    ModelAssetError,
    load_grid,
)
from apps.results.models import ResultSet
from apps.runs import hazard as hazard_service
from apps.runs.models import AnalysisRun
from cass_adapters.base import AdapterError, EngineState
from cass_converter import reference
from cass_converter.pilot_enrichment import GEM_LAYOUT
from cass_core.artifacts import AccessPolicy, RetentionClass

#: Where the report is linked, so it sits beside the run it measures.
REPORT_ROLE = "openquake_reference"


class Command(BaseCommand):
    help = "Compare a finished analysis run against an OpenQuake reference calculation."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--run", required=True, help="The analysis run's id, or the start of it."
        )
        parser.add_argument(
            "--gem-root",
            required=True,
            help="GEM release root, holding global_vulnerability_model.",
        )
        parser.add_argument(
            "--perspective",
            default="ground_up",
            help="Which result to compare. Ground-up is the only one OpenQuake "
            "computes here: it applies no financial structure.",
        )
        parser.add_argument(
            "--tolerance-aal",
            type=float,
            default=None,
            help="Approved tolerance on the average annual loss ratio. Without "
            "one the comparison is reported and not decided.",
        )
        parser.add_argument(
            "--tolerance-return-period",
            type=float,
            default=None,
            help="Approved tolerance on each return-period loss ratio.",
        )
        parser.add_argument(
            "--ignore-covs",
            action="store_true",
            help="Compare expectations rather than distributions: OpenQuake uses "
            "mean loss ratios, which isolates the discretisation.",
        )
        parser.add_argument("--poll-interval", type=float, default=10.0)
        parser.add_argument("--timeout", type=float, default=7200.0)

    #: The reference job's calculation, once submitted.
    reference_calculation: int | None = None

    def handle(self, *args, **options) -> None:
        analysis_run = self._find_run(options["run"])
        run = analysis_run.run
        model_version = analysis_run.model_version
        hazard_set = model_version.hazard_set if model_version else None
        if hazard_set is None or not hazard_set.openquake_calculation_id:
            raise CommandError(
                "This run's model version has no hazard set naming the calculation "
                "it was computed from, so there is nothing to chain a reference "
                "calculation onto. Only a hazard set built on this platform carries "
                "that."
            )

        result = ResultSet.objects.filter(
            run=run, perspective=options["perspective"]
        ).first()
        if result is None:
            raise CommandError(
                f"The run published no {options['perspective']} result, so there is "
                "nothing to compare."
            )

        dictionary = self._dictionary(analysis_run)
        exposure = self._exposure(analysis_run, dictionary)
        self.stdout.write(
            f"{len(exposure.assets)} assets over {len({a.location_id for a in exposure.assets})} "
            f"locations, carrying {exposure.written_value}"
        )
        if not exposure.reconciles:
            raise CommandError(
                "The reference exposure does not carry the same value the keys "
                f"accounted for: {exposure.difference} difference, "
                f"{len(exposure.unmatched)} identifiers unexplained."
            )

        files = reference.files(
            exposure,
            vulnerability=self._vulnerability(
                pathlib.Path(options["gem_root"]).expanduser(),
                model_version.country_code,
                exposure.loss_types,
                dictionary,
            ),
            description=f"CASS reference comparison for run {run.id}",
            ignore_covs=options["ignore_covs"],
        )

        engine = openquake_adapter()
        hazard_calculation = int(hazard_set.openquake_calculation_id)
        restored = None
        if hazard_set.openquake_calculation_removed:
            # CASS holds the only copy of the calculation, and a chained job
            # needs one on the engine. Run it again, checked against the stored
            # fingerprint, and remove it again afterwards.
            self.stdout.write(
                f"OpenQuake's copy of calculation {hazard_calculation} was removed "
                "once CASS held it. Running it again and checking it reproduces the "
                "stored ground motion."
            )
            try:
                restored = hazard_service.restore_calculation(
                    hazard_set,
                    engine,
                    poll_interval=options["poll_interval"],
                    timeout=options["timeout"],
                )
            except hazard_service.HazardExecutionError as exc:
                raise CommandError(str(exc)) from None
            hazard_calculation = restored
            self.stdout.write(
                f"Calculation {restored} reproduces the stored ground motion."
            )

        try:
            losses = self._calculate(
                files,
                engine=engine,
                hazard_calculation=hazard_calculation,
                effective_time=hazard_set.effective_time,
                poll_interval=options["poll_interval"],
                timeout=options["timeout"],
            )
        finally:
            if restored is not None:
                # The reference job was chained onto the calculation run again,
                # so it goes first: the engine will not remove a calculation
                # another still reads from. Its losses are in the stored report.
                if self.reference_calculation is not None:
                    hazard_service._remove_quietly(engine, self.reference_calculation)
                removed = hazard_service._remove_quietly(engine, restored)
                self.stdout.write(
                    f"Calculation {restored} removed again."
                    if removed
                    else f"Calculation {restored} could not be removed and is still "
                    "on the engine."
                )

        report = reference.compare(
            exposure=exposure,
            reference=losses,
            oasis_average_annual_loss=result.average_annual_loss or 0,
            oasis_return_period_losses=result.return_period_losses or {},
            tolerances=self._tolerances(options),
        )
        report["run"] = str(run.id)
        report["perspective"] = result.perspective
        report["hazard_set"] = hazard_set.reference
        report["hazard_calculation"] = str(hazard_calculation)
        report["hazard_calculation_rerun"] = restored is not None

        self._store(run, report)
        self._report(report)

    # -- gathering ---------------------------------------------------------

    def _find_run(self, wanted: str) -> AnalysisRun:
        found = [
            item
            for item in AnalysisRun.objects.select_related(
                "run", "exposure_version", "model_version__vulnerability_set"
            )
            if str(item.run_id).startswith(wanted)
        ]
        if not found:
            raise CommandError(f"No analysis run starts with {wanted!r}.")
        if len(found) > 1:
            raise CommandError(
                f"{wanted!r} matches {len(found)} runs. Give more of the id."
            )
        return found[0]

    def _dictionary(self, analysis_run: AnalysisRun) -> dict[str, Any]:
        """The vulnerability dictionary behind the run's identifiers."""
        return json.loads(
            self._artifact(
                "vulnerability_set",
                [analysis_run.model_version.vulnerability_set_id],
                VULNERABILITY_DICTIONARY_ROLE,
            )
        )

    def _exposure(
        self, analysis_run: AnalysisRun, dictionary: dict[str, Any]
    ) -> reference.ReferenceExposure:
        keys = self._artifact(
            "analysis_run", [analysis_run.run_id, analysis_run.id], "cass_keys"
        )
        rows = list(csv.DictReader(io.StringIO(keys.decode("utf-8-sig"))))
        if not rows:
            raise CommandError("The run's keys file is empty.")

        files = exposure_services.load_files(analysis_run.exposure_version)
        locations: dict[str, dict[str, Any]] = {}
        for row in files.location.rows:
            raw = dict(row.raw)
            account = str(raw.get("AccNumber") or "").strip()
            number = str(raw.get("LocNumber") or raw.get("LocID") or "").strip()
            locations[f"{account}/{number}" if account else number] = raw

        try:
            return reference.build_exposure(
                rows,
                locations=locations,
                dictionary=dictionary,
                cell_centroids=self._centroids(analysis_run.model_version.grid),
            )
        except reference.ReferenceError as exc:
            raise CommandError(str(exc)) from None

    def _centroids(self, grid) -> dict[int, tuple[float, float]]:
        """The centre of each cell, so an asset reads the site CASS mapped it to."""
        try:
            cells = load_grid(grid).cells
        except ModelAssetError as exc:
            raise CommandError(str(exc)) from None
        return {
            cell.area_peril_id: (
                float((cell.min_longitude + cell.max_longitude) / 2),
                float((cell.min_latitude + cell.max_latitude) / 2),
            )
            for cell in cells
        }

    def _artifact(self, subject_type: str, subject_ids: list[Any], role: str) -> bytes:
        link = (
            ArtifactLink.objects.filter(
                subject_type=subject_type, subject_id__in=subject_ids, role=role
            )
            .select_related("artifact")
            .order_by("-created_at")
            .first()
        )
        if link is None or not link.artifact.is_readable:
            raise CommandError(
                f"No readable {role} is registered against this run's {subject_type}."
            )
        with get_store().open(link.artifact.uri) as handle:
            return handle.read()

    def _vulnerability(
        self,
        root: pathlib.Path,
        country_code: str,
        loss_types: tuple[str, ...],
        dictionary: dict[str, Any],
    ) -> dict[str, bytes]:
        """GEM's own functions for the country, from where the set says it read them.

        A set built on the platform records its place in the release, because the
        country need not be one CASS was compiled with. Older sets predate that
        record and are found through the pilot table.
        """
        recorded = dictionary.get("gem") or {}
        region = str(recorded.get("region") or "")
        name = str(recorded.get("country") or "")
        if not (region and name):
            try:
                region, name = GEM_LAYOUT[country_code.upper()]
            except KeyError:
                raise CommandError(
                    "The vulnerability set does not record where GEM publishes "
                    f"{country_code}, and it is not a pilot country. Register the set "
                    "again so its dictionary records its place in the release."
                ) from None
        directory = root / "global_vulnerability_model" / region / name
        found: dict[str, bytes] = {}
        for loss_type in loss_types:
            path = directory / f"vulnerability_{loss_type}.xml"
            if not path.is_file():
                raise CommandError(
                    f"{path} does not exist. The portfolio carries {loss_type} value, "
                    "and OpenQuake would report zero loss for it rather than refuse."
                )
            found[loss_type] = path.read_bytes()
        return found

    # -- running -----------------------------------------------------------

    def _calculate(
        self,
        files: dict[str, bytes],
        *,
        engine,
        hazard_calculation: int,
        effective_time: float,
        poll_interval: float,
        timeout: float,
    ) -> reference.ReferenceLosses:
        try:
            calculation = engine.submit(files, hazard_job_id=hazard_calculation)
            self.reference_calculation = calculation
        except AdapterError as exc:
            raise CommandError(
                f"OpenQuake refused the reference job: {exc.summary}"
            ) from None
        self.stdout.write(
            f"Calculation {calculation} submitted on hazard {hazard_calculation}."
        )

        deadline = time.monotonic() + timeout
        while True:
            job = engine.status(calculation)
            if job.state is EngineState.SUCCEEDED:
                break
            if job.state in (EngineState.FAILED, EngineState.CANCELLED):
                raise CommandError(
                    f"The reference calculation {job.raw_state}: "
                    + engine.failure_detail(calculation)
                )
            if time.monotonic() > deadline:
                raise CommandError(
                    f"The reference calculation was still {job.raw_state} after "
                    f"{timeout:.0f} seconds."
                )
            time.sleep(poll_interval)

        exports: dict[str, bytes] = {}
        for output in ("risk_by_event", "avg_losses-rlzs"):
            try:
                exports.update(engine.export(calculation, output))
            except AdapterError:
                # ``avg_losses-rlzs`` is per-asset detail the comparison can do
                # without; ``risk_by_event`` is not, and its absence is reported
                # by the reader below rather than guessed at here.
                continue
        try:
            return reference.read_losses(exports, effective_time=effective_time)
        except reference.ReferenceError as exc:
            raise CommandError(str(exc)) from None

    # -- recording ---------------------------------------------------------

    def _store(self, run, report: dict[str, Any]) -> None:
        payload = json.dumps(report, indent=2, sort_keys=True, default=str).encode()
        ref = get_store().put_bytes(
            bucket("result"),
            f"analysis/{run.id}/openquake_reference.json",
            payload,
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
                "role": REPORT_ROLE,
                "original_filename": "openquake_reference.json",
            },
        )
        ArtifactLink.objects.update_or_create(
            artifact=artifact,
            subject_type="analysis_run",
            subject_id=run.id,
            role=REPORT_ROLE,
            direction="output",
            defaults={},
        )
        self.stdout.write(f"Report stored at {ref.uri}")

    def _report(self, report: dict[str, Any]) -> None:
        for measurement in report["measurements"]:
            ratio = measurement["ratio"]
            verdict = (
                "undecided"
                if not measurement["decided"]
                else ("within tolerance" if measurement["within_tolerance"] else "outside tolerance")
            )
            self.stdout.write(
                f"  {measurement['what']}: CASS {measurement['cass']} against "
                f"OpenQuake {measurement['openquake']}"
                + (f", ratio {ratio:.4f}" if ratio is not None else "")
                + f" ({verdict})"
            )
        if not report["decided"]:
            self.stdout.write(
                self.style.WARNING(
                    "No tolerance is approved, so nothing here passes or fails."
                )
            )

    def _tolerances(self, options) -> dict[str, float]:
        stated = {}
        if options["tolerance_aal"] is not None:
            stated["average_annual_loss"] = options["tolerance_aal"]
        if options["tolerance_return_period"] is not None:
            stated["return_period"] = options["tolerance_return_period"]
        return stated
