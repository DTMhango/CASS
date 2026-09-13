"""Running a saved hazard configuration on OpenQuake, end to end.

Section 5's hazard pipeline is prepare, validate, submit, monitor, export and
benchmark. These drive the first five through the real adapter against a
scripted engine, so the service and the protocol boundary are exercised
together rather than one being stubbed out from under the other.

The refusals matter as much as the happy path. A national calculation is hours,
and section 12 requires a failure to produce an intelligible state rather than
a stack trace -- so a job the engine rejects, a calculation that dies mid-run
and a configuration whose grid moved underneath it all have to arrive at the
run monitor as sentences a modeller can act on.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from apps.modelregistry import hazard_models, pilot
from apps.modelregistry.models import HazardModel
from apps.runs import hazard as hazard_service
from apps.runs.models import HazardRun, Run, RunKind
from cass_adapters.base import EngineRejected, IncompatibleEngine
from cass_adapters.openquake import OpenQuakeAdapter
from cass_core.runs import RunState

from .conftest import API
from .test_hazard_models import archive

pytestmark = pytest.mark.django_db


# -- a scripted OpenQuake server --------------------------------------------

class FakeResponse:
    def __init__(self, status_code=200, body=None, text=None, chunks=None):
        self.status_code = status_code
        self._body = body
        self._text = text
        self._chunks = chunks or []

    @property
    def text(self) -> str:
        if self._text is not None:
            return self._text
        return "" if self._body is None else str(self._body)

    def json(self):
        if self._body is None:
            raise ValueError("no JSON body")
        return self._body

    def iter_content(self, chunk_size: int = 1):
        yield from self._chunks


class FakeEngine:
    """An OpenQuake server that runs one calculation the way we script it."""

    def __init__(
        self,
        *,
        version="3.23.0",
        statuses=("executing", "complete"),
        submit_status=200,
        datastore=b"HDF5 datastore",
        publishes_realizations=True,
    ):
        self.version = version
        self.statuses = list(statuses)
        self.submit_status = submit_status
        self.datastore = datastore
        self.calls: list[tuple[str, str]] = []
        self.submitted: dict[str, bytes] = {}
        # A calculation on a single logic-tree branch exposes no realizations
        # output at all, which is what the Indonesia run does.
        self.OUTPUTS = dict(FakeEngine.OUTPUTS)
        if not publishes_realizations:
            del self.OUTPUTS[6]

    #: Engine 3.23.4 exports, in the exact shape the converter reads: the
    #: provenance comment, ``gmv_`` measure columns, ``custom_site_id`` carrying
    #: the area peril, and the timing on the ruptures header.
    HEADER = (
        "#,,,,,\"generated_by='OpenQuake engine 3.23.4', "
        "start_date='2026-09-12T16:30:50', checksum=556311143\""
    )
    RUPTURE_HEADER = (
        "#,,,,,,,,,,\"generated_by='OpenQuake engine 3.23.4', "
        "start_date='2026-09-12T16:30:50', checksum=556311143, "
        "investigation_time=50.0, ses_per_logic_tree_path=20\""
    )
    OUTPUTS = {3: "gmf_data", 4: "events", 5: "ruptures", 6: "realizations"}

    def exports(self) -> dict[str, dict[str, str]]:
        return {
            "gmf_data": {
                "gmf-data_77.csv": (
                    f"{self.HEADER}\nevent_id,gmv_PGA,gmv_SA(0.3),custom_site_id\n"
                    "0,2.00000E-01,4.00000E-01,101\n"
                    "0,3.00000E-01,5.00000E-01,102\n"
                    "1,1.00000E-01,2.00000E-01,101\n"
                ),
                "sitemesh_77.csv": (
                    f"{self.HEADER}\ncustom_site_id,lon,lat\n"
                    "101,106.45000,-7.15000\n102,106.55000,-7.15000\n"
                ),
            },
            "events": {
                "events_77.csv": (
                    f"{self.HEADER}\nevent_id,rup_id,rlz_id,year,ses_id\n"
                    "0,2,0,90,14\n1,7,0,774,9\n"
                )
            },
            "ruptures": {
                "ruptures_77.csv": (
                    f"{self.RUPTURE_HEADER}\n"
                    "rup_id,source_id,multiplicity,mag,centroid_lon,centroid_lat,"
                    "centroid_depth,trt,strike,dip,rake\n"
                    "2,1,1,5.1,106.5,-6.2,10.0,Active Shallow Crust,270.0,45.0,90.0\n"
                    "7,1,1,6.4,106.9,-6.8,20.0,Active Shallow Crust,90.0,45.0,90.0\n"
                )
            },
            "realizations": {
                "realizations_77.csv": (
                    f"{self.HEADER}\nrlz_id,branch_path,weight\n0,A~A,1.0\n"
                )
            },
        }

    @staticmethod
    def download(payload: bytes, filename: str) -> FakeResponse:
        response = FakeResponse(200, chunks=[payload])
        response.headers = {"Content-Disposition": f"attachment; filename={filename}"}
        return response

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))

        if url.endswith("engine_version"):
            return FakeResponse(200, text=self.version)
        if url.endswith("engine_info"):
            return FakeResponse(404, text="not found")
        if url.endswith("calc/run"):
            if self.submit_status != 200:
                return FakeResponse(self.submit_status, text="Invalid job.ini")
            for _field, (_name, payload, _type) in kwargs.get("files", []):
                with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
                    for member in bundle.namelist():
                        self.submitted[member] = bundle.read(member)
            return FakeResponse(200, {"job_id": 77})
        if "calc/result/" in url:
            result_id = int(url.rstrip("/").rsplit("/", 1)[-1])
            files = self.exports()[self.OUTPUTS[result_id]]
            if len(files) == 1:
                [(name, text)] = files.items()
                return self.download(text.encode(), f"output-{result_id}-{name}")
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as bundle:
                for name, text in files.items():
                    bundle.writestr(name, text)
            return self.download(archive.getvalue(), f"output-{result_id}-gmf_data-csv.zip")
        if url.endswith("/status"):
            status = (
                self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            )
            return FakeResponse(200, {"status": status})
        if url.endswith("/datastore"):
            return FakeResponse(200, chunks=[self.datastore])
        if url.endswith("/traceback"):
            return FakeResponse(200, ["ValueError: the site model is empty"])
        if "/log/" in url:
            return FakeResponse(200, [["t", "INFO", "job", "computing gmfs"]])
        if url.endswith("/results"):
            return FakeResponse(
                200,
                [
                    {"id": result_id, "name": output, "type": output,
                     "outtypes": ["csv", "hdf5"]}
                    for result_id, output in self.OUTPUTS.items()
                ],
            )
        if url.endswith("/abort"):
            return FakeResponse(200, {"status": "aborted"})
        raise AssertionError(f"unscripted call {method} {url}")


def adapter_for(engine) -> OpenQuakeAdapter:
    return OpenQuakeAdapter(
        "http://openquake:8800",
        session=engine,
        retries=1,
        retry_delay=0,
        sleep=lambda _: None,
    )


# -- fixtures ---------------------------------------------------------------

@pytest.fixture()
def model(db, modeller) -> HazardModel:
    registered = hazard_models.register_model(
        archive(),
        country_code="ID",
        version="test-2024",
        label="A national model",
        licence="CC BY-NC-SA 4.0",
        actor=modeller,
    )
    # The data-rights gate is cleared so these tests are about execution; the
    # gate itself has its own test below.
    registered.licence_cleared = True
    registered.licence_note = "Cleared for platform testing."
    registered.save()
    return registered


@pytest.fixture()
def grid(db, modeller):
    return pilot.register_grid("ID", actor=modeller)


@pytest.fixture()
def spec(model, grid, modeller):
    return hazard_models.save_spec(
        model, grid, name="National baseline", overrides={}, actor=modeller
    )


@pytest.fixture()
def hazard_run(spec, modeller) -> HazardRun:
    assembled = hazard_models.job_files(spec)
    run = Run.objects.create(
        kind=RunKind.HAZARD,
        label="National baseline",
        execution_profile="model_build",
        created_by=modeller,
    )
    return HazardRun.objects.create(
        run=run,
        grid=spec.grid,
        job_settings={
            "spec": str(spec.id),
            "job_checksum": assembled["job_checksum"],
        },
        created_by=modeller,
    )


def run_it(hazard_run, engine, **kwargs):
    return hazard_service.execute(
        hazard_run,
        adapter=adapter_for(engine),
        actor=hazard_run.created_by,
        poll_interval=0,
        **kwargs,
    )


# -- the happy path ---------------------------------------------------------

def test_a_hazard_run_completes_and_registers_its_datastore(hazard_run):
    engine = FakeEngine()

    run_it(hazard_run, engine)

    hazard_run.refresh_from_db()
    run = hazard_run.run
    run.refresh_from_db()
    assert run.state == RunState.SUCCEEDED
    assert run.stage == "export"
    assert hazard_run.openquake_calculation_id == "77"
    assert hazard_run.gmf_bytes == len(engine.datastore)


def test_the_engine_version_is_recorded_against_the_run(hazard_run):
    """Section 12: a result traceable to an immutable engine version."""
    run_it(hazard_run, FakeEngine(version="3.23.0"))

    hazard_run.refresh_from_db()
    assert hazard_run.openquake_version == "3.23.0"
    assert hazard_run.run.manifest["engine"]["version"] == "3.23.0"


def test_the_datastore_is_registered_as_an_artifact_with_a_checksum(hazard_run):
    from apps.artifacts.models import ArtifactLink

    run_it(hazard_run, FakeEngine())

    link = ArtifactLink.objects.get(
        subject_type="hazard_run", subject_id=hazard_run.run_id
    )
    assert link.role == "openquake_datastore"
    assert link.direction == "output"
    assert link.artifact.checksum


def test_the_job_that_was_submitted_carries_the_model_and_the_generated_files(
    hazard_run,
):
    """The published package goes up as it was, beside what CASS generated."""
    engine = FakeEngine()

    run_it(hazard_run, engine)

    # The resolved job.ini goes up under the name the engine expects.
    assert "job.ini" in engine.submitted
    assert b"event_based" in engine.submitted["job.ini"]
    # And the model's own source files, under the paths its trees reference.
    assert "ssm/src.xml" in engine.submitted


def test_every_stage_of_the_pipeline_is_recorded(hazard_run):
    run_it(hazard_run, FakeEngine())

    stages = list(hazard_run.run.events.values_list("stage", flat=True))
    assert {"prepare", "validate_settings", "submit", "monitor", "export"} <= set(stages)


def test_a_completed_run_registers_a_hazard_set_from_its_exports(hazard_run):
    """A hazard run whose output nothing can consume was the gap this closes."""
    from apps.modelregistry.models import HazardSet

    run_it(hazard_run, FakeEngine())

    hazard_set = HazardSet.objects.get()
    assert hazard_set.event_count == 2
    assert hazard_set.cell_count == 2
    assert set(hazard_set.imts) == {"PGA", "SA(0.3)"}
    hazard_run.run.refresh_from_db()
    assert hazard_run.run.manifest["hazard"]["hazard_set"]["id"] == str(hazard_set.id)


def test_the_hazard_set_records_the_calculation_it_was_computed_from(hazard_run):
    """A reference comparison is chained onto that calculation, so the id survives.

    The checksum already said whether two sets came from the same calculation.
    Only the id says which one, and without it the comparison would have to
    compute its own ground motion -- which is a different calculation, however
    carefully it was configured.
    """
    from apps.modelregistry.models import HazardSet

    run_it(hazard_run, FakeEngine())

    assert HazardSet.objects.get().openquake_calculation_id == "77"


def test_a_set_registered_before_the_id_was_kept_is_backfilled_from_its_run(hazard_run):
    """Migration 0011: the set names the run in its version, so the run is findable."""
    import importlib

    from django.apps import apps as django_apps

    from apps.modelregistry.models import HazardSet

    run_it(hazard_run, FakeEngine())
    HazardSet.objects.update(openquake_calculation_id="")

    migration = importlib.import_module(
        "apps.modelregistry.migrations.0011_hazardset_openquake_calculation_id"
    )
    migration.backfill(django_apps, None)

    hazard_set = HazardSet.objects.get()
    assert hazard_set.version.endswith(str(hazard_run.run_id)[:8])
    assert hazard_set.openquake_calculation_id == "77"


def test_a_calculation_with_no_realizations_output_still_registers_its_hazard(
    hazard_run,
):
    """One branch, no logic tree to describe, and no such export to fetch.

    The engine publishes a realizations output only where there are several
    realisations. Treating its absence as a failed export stopped the first
    Indonesia run after the ground motion had already been computed.
    """
    from apps.modelregistry.models import HazardSet

    run_it(hazard_run, FakeEngine(publishes_realizations=False))

    hazard_run.run.refresh_from_db()
    assert hazard_run.run.state == RunState.SUCCEEDED, hazard_run.run.failure_summary
    assert HazardSet.objects.get().event_count == 2


def test_the_benchmark_gate_is_recorded_as_not_performed(hazard_run):
    """A gate that silently passed would be a gate in name only."""
    run_it(hazard_run, FakeEngine())

    hazard_run.run.refresh_from_db()
    outstanding = hazard_run.run.manifest["stages_not_performed"]
    assert "benchmark" in outstanding
    assert "has not been approved" in outstanding["benchmark"]


# -- refusals and failures --------------------------------------------------

def test_an_untested_engine_is_refused_before_anything_is_submitted(hazard_run):
    """Section 18: hazard from an untested engine cannot be defended."""
    engine = FakeEngine(version="3.19.0")

    with pytest.raises(IncompatibleEngine, match="3.19.0"):
        run_it(hazard_run, engine)

    hazard_run.run.refresh_from_db()
    assert hazard_run.run.state == RunState.FAILED
    assert not any(url.endswith("calc/run") for _, url in engine.calls)


def test_a_job_the_engine_rejects_says_so_in_the_monitor(hazard_run):
    engine = FakeEngine(submit_status=400)

    with pytest.raises(EngineRejected):
        run_it(hazard_run, engine)

    hazard_run.run.refresh_from_db()
    assert hazard_run.run.state == RunState.FAILED
    assert hazard_run.run.failure_stage == "submit"
    assert "rejected the job configuration" in hazard_run.run.failure_summary


def test_a_calculation_that_fails_carries_the_engine_traceback(hazard_run):
    """Not "the calculation failed": what broke, and what it was doing."""
    engine = FakeEngine(statuses=("executing", "failed"))

    with pytest.raises(hazard_service.HazardExecutionError):
        run_it(hazard_run, engine)

    hazard_run.run.refresh_from_db()
    assert hazard_run.run.failure_stage == "monitor"
    assert "the site model is empty" in hazard_run.run.failure_detail


def test_a_configuration_whose_grid_moved_is_refused_rather_than_run(hazard_run):
    """A run whose checksum nobody reviewed is a run nobody can reproduce."""
    hazard_run.job_settings = {
        **hazard_run.job_settings,
        "job_checksum": "a" * 64,
    }
    hazard_run.save()
    engine = FakeEngine()

    with pytest.raises(hazard_service.HazardExecutionError, match="no longer resolves"):
        run_it(hazard_run, engine)

    hazard_run.run.refresh_from_db()
    assert hazard_run.run.failure_stage == "prepare"
    assert not any(url.endswith("calc/run") for _, url in engine.calls)


def test_a_run_that_names_no_specification_stops_with_a_reason(hazard_run):
    hazard_run.job_settings = {"spec": "", "job_checksum": ""}
    hazard_run.save()

    with pytest.raises(hazard_service.HazardExecutionError, match="no longer exists"):
        run_it(hazard_run, FakeEngine())


def test_a_failed_run_does_not_read_as_though_the_export_finished(hazard_run):
    """The progress fraction belongs to the stage that completed, not the one that died."""
    engine = FakeEngine(statuses=("failed",))

    with pytest.raises(hazard_service.HazardExecutionError):
        run_it(hazard_run, engine)

    hazard_run.run.refresh_from_db()
    assert hazard_run.run.progress < 1.0


# -- cancellation -----------------------------------------------------------

def test_cancelling_reaches_the_engine(hazard_run, modeller):
    engine = FakeEngine(statuses=("executing",))
    hazard_run.openquake_calculation_id = "77"
    hazard_run.save()
    hazard_run.run.transition(RunState.QUEUED, actor=modeller)
    hazard_run.run.transition(RunState.RUNNING, actor=modeller)

    hazard_service.cancel(hazard_run, adapter=adapter_for(engine), actor=modeller)

    hazard_run.run.refresh_from_db()
    assert hazard_run.run.state == RunState.CANCELLED
    assert any(url.endswith("/abort") for _, url in engine.calls)


# -- launching through the API ----------------------------------------------

def test_launching_a_saved_configuration_queues_a_run(
    client_for, modeller, spec, monkeypatch
):
    """The response is the queued run, not the hazard."""
    started: list[str] = []
    monkeypatch.setattr(
        "apps.runs.tasks.execute_hazard.delay", lambda run_id: started.append(run_id)
    )

    response = client_for(modeller).post(
        f"{API}/hazard-models/{spec.model_id}/specs/{spec.id}/launch/"
    )

    assert response.status_code == 202, response.data
    assert started
    run = Run.objects.get(id=response.data["run"])
    assert run.kind == RunKind.HAZARD
    assert run.execution_profile == "model_build"


def test_a_model_whose_licence_is_not_cleared_runs_as_research(
    client_for, modeller, spec, monkeypatch
):
    """CC BY-NC-SA permits research, and the rest of CASS runs uncleared data as such.

    What an uncleared licence withholds is a decision, so the run is labelled
    research and says why, rather than refused.
    """
    monkeypatch.setattr("apps.runs.tasks.execute_hazard.delay", lambda run_id: None)
    spec.model.licence_cleared = False
    spec.model.save()

    response = client_for(modeller).post(
        f"{API}/hazard-models/{spec.model_id}/specs/{spec.id}/launch/"
    )

    assert response.status_code == 202, response.data
    assert response.data["research_only"] is True
    assert response.data["licence_note"]
    run = Run.objects.get(id=response.data["run"])
    assert "research only" in run.label


def test_a_run_of_an_uncleared_model_registers_an_uncleared_hazard_set(
    hazard_run, spec
):
    """The licence travels onto the hazard set, which is what gates publication."""
    from apps.modelregistry.models import HazardSet

    spec.model.licence_cleared = False
    spec.model.save()
    run_it(hazard_run, FakeEngine())

    hazard_set = HazardSet.objects.get()
    assert hazard_set.licence_cleared is False


def test_a_configuration_from_another_model_is_not_launchable(
    client_for, spec, modeller
):
    other = hazard_models.register_model(
        archive(), country_code="ID", version="other", label="Another", actor=modeller
    )

    response = client_for(modeller).post(
        f"{API}/hazard-models/{other.id}/specs/{spec.id}/launch/"
    )

    assert response.status_code == 404


def test_launching_records_who_started_the_calculation(
    client_for, modeller, spec, monkeypatch
):
    from apps.audit.models import AuditEvent

    monkeypatch.setattr("apps.runs.tasks.execute_hazard.delay", lambda run_id: None)
    client_for(modeller).post(
        f"{API}/hazard-models/{spec.model_id}/specs/{spec.id}/launch/"
    )

    assert AuditEvent.objects.filter(
        subject_type="hazard_run", action="submit"
    ).exists()


def test_an_analyst_cannot_start_a_national_calculation(api, spec):
    """Hours of compute on a governed model is a modeller's action."""
    response = api.post(f"{API}/hazard-models/{spec.model_id}/specs/{spec.id}/launch/")

    assert response.status_code == 403
    assert not Run.objects.filter(kind=RunKind.HAZARD).exists()
