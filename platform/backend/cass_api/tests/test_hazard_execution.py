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
import tempfile
import zipfile
from pathlib import Path

import pytest

from apps.modelregistry import hazard_models, pilot
from apps.modelregistry.models import HazardModel
from apps.runs import hazard as hazard_service
from apps.runs.models import HazardRun, Run, RunKind
from cass_adapters.base import EngineJob, EngineRejected, EngineState, IncompatibleEngine
from cass_adapters.openquake import OpenQuakeAdapter
from cass_core.runs import RunState

from .conftest import API
from .test_hazard_models import archive

pytestmark = pytest.mark.django_db


# -- a scripted OpenQuake server --------------------------------------------

def small_datastore(*, weights=(1.0,), engine_version="3.23.4") -> bytes:
    """A datastore in the shape engine 3.23.4 writes, small enough to read by eye.

    Two cells carrying their area perils as custom site ids, two events in the
    years the engine assigned them, and the provenance a real datastore keeps on
    its root: the engine version and the calculation's own checksum. The rows
    are out of event order, as the engine's workers write them.
    """
    import h5py
    import numpy

    rows = ((1, 0, 0.1, 0.2), (0, 0, 0.2, 0.4), (0, 1, 0.3, 0.5))
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "calc_77.hdf5"
        with h5py.File(target, "w") as store:
            store.attrs["engine_version"] = engine_version
            store.attrs["checksum32"] = numpy.int64(556311143)
            store.attrs["date"] = "2026-09-12T16:30:50"
            group = store.create_group("gmf_data")
            group.create_dataset("eid", data=numpy.array([r[0] for r in rows], dtype="u4"))
            group.create_dataset("sid", data=numpy.array([r[1] for r in rows], dtype="u4"))
            group.create_dataset("gmv_0", data=numpy.array([r[2] for r in rows], dtype="f4"))
            group.create_dataset("gmv_1", data=numpy.array([r[3] for r in rows], dtype="f4"))
            group.attrs["imts"] = "PGA SA(0.3)"
            group.attrs["investigation_time"] = 50.0
            group.attrs["effective_time"] = 1000.0
            group.attrs["num_events"] = 2
            sites = store.create_group("sitecol")
            sites.create_dataset("sids", data=numpy.arange(2, dtype="u4"))
            sites.create_dataset(
                "custom_site_id", data=numpy.array([b"101", b"102"], dtype="S8")
            )
            store.create_dataset(
                "events",
                data=numpy.array([(0, 90), (1, 774)], dtype=[("id", "u4"), ("year", "u4")]),
            )
            if weights is not None:
                store.create_dataset("weights", data=numpy.array(weights, dtype="f8"))
        return target.read_bytes()


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
        datastore=None,
        publishes_realizations=True,
        remove_status=200,
        log=(["t", "INFO", "job", "computing gmfs"],),
    ):
        self.version = version
        self.statuses = list(statuses)
        self.submit_status = submit_status
        self.datastore = small_datastore() if datastore is None else datastore
        self.remove_status = remove_status
        self.removed: list[str] = []
        self.log = [list(line) for line in log]
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
            # The engine takes a line offset, and a caller following a running
            # calculation asks only for what it has not read.
            start, _, _stop = url.rstrip("/").rsplit("/", 1)[-1].partition(":")
            return FakeResponse(200, self.log[int(start or 0):])
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
        if url.endswith("/remove"):
            if self.remove_status != 200:
                return FakeResponse(self.remove_status, text="cannot remove")
            self.removed.append(url)
            return FakeResponse(200, {"success": True})
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


def test_a_completed_run_registers_a_hazard_set_from_its_datastore(hazard_run):
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


def test_a_calculation_with_no_realisation_weights_still_registers_its_hazard(
    hazard_run,
):
    """One branch, no logic tree to describe, and no weights recorded for it.

    The engine records weights only where there are realisations to weigh.
    Treating their absence as a failure stopped the first Indonesia run after
    the ground motion had already been computed.
    """
    from apps.modelregistry.models import HazardSet

    run_it(hazard_run, FakeEngine(datastore=small_datastore(weights=None)))

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


# -- a calculation held once, and never in memory ---------------------------
#
# A national datastore is gigabytes and the worker holding it is not. The
# export used to fetch the ground motion a second time as CSV text and hold both
# in memory; it now streams the datastore to disk, builds the hazard set from it
# a slice at a time, and removes the engine's own copy once CASS holds one.

def test_the_export_never_asks_the_engine_for_csv_copies(hazard_run):
    engine = FakeEngine()

    run_it(hazard_run, engine)

    assert not any("calc/result/" in url for _, url in engine.calls)


def test_the_datastore_is_streamed_to_a_file_rather_than_held_in_memory(
    hazard_run, monkeypatch
):
    sinks: list = []
    original = OpenQuakeAdapter.download_datastore

    def recording(self, calculation_id, sink, **kwargs):
        sinks.append(sink)
        return original(self, calculation_id, sink, **kwargs)

    monkeypatch.setattr(OpenQuakeAdapter, "download_datastore", recording)

    run_it(hazard_run, FakeEngine())

    assert sinks
    assert not isinstance(sinks[0], io.BytesIO)
    assert hasattr(sinks[0], "fileno")


def test_the_hazard_set_carries_the_provenance_the_datastore_records(hazard_run, spec):
    """Nothing the CSV header used to carry is lost by reading the datastore."""
    from apps.modelregistry import hazard as hazard_registry
    from apps.modelregistry.models import HazardSet

    run_it(hazard_run, FakeEngine())

    hazard_set = HazardSet.objects.get()
    assert hazard_set.engine_version == "OpenQuake engine 3.23.4"
    assert hazard_set.calculation_checksum == "556311143"
    assert hazard_set.datastore_uri.endswith(f"hazard/{hazard_run.run_id}/datastore.hdf5")
    assert hazard_set.intensity_bins_checksum == (
        hazard_registry.current_intensity_bins_checksum()
    )
    assert hazard_set.job_spec_id == spec.id
    assert hazard_set.job_checksum == hazard_run.job_settings["job_checksum"]
    assert hazard_set.effective_time == 1000.0


def test_the_engine_copy_is_removed_once_cass_holds_the_calculation(hazard_run):
    """A calculation stored on the engine and in CASS is a calculation stored twice."""
    from apps.modelregistry.models import HazardSet

    engine = FakeEngine()

    run_it(hazard_run, engine)

    assert engine.removed and engine.removed[0].endswith("/calc/77/remove")
    assert HazardSet.objects.get().openquake_calculation_removed is True
    hazard_run.run.refresh_from_db()
    assert hazard_run.run.manifest["hazard"]["engine_copy"]["removed"] is True
    messages = hazard_run.run.events.filter(stage="export").values_list("message", flat=True)
    assert any("only copy" in message for message in messages)


def test_an_installation_can_keep_calculations_on_the_engine(hazard_run, settings):
    from apps.modelregistry.models import HazardSet

    settings.CASS_OPENQUAKE_KEEP_CALCULATIONS = True
    engine = FakeEngine()

    run_it(hazard_run, engine)

    assert not engine.removed
    assert HazardSet.objects.get().openquake_calculation_removed is False


def test_a_removal_the_engine_refuses_does_not_cost_the_result(hazard_run):
    """Disk, not a hazard set: the run succeeds and says the copy is still there."""
    from apps.modelregistry.models import HazardSet

    run_it(hazard_run, FakeEngine(remove_status=500))

    hazard_run.run.refresh_from_db()
    assert hazard_run.run.state == RunState.SUCCEEDED
    assert HazardSet.objects.get().openquake_calculation_removed is False
    messages = hazard_run.run.events.filter(stage="export").values_list("message", flat=True)
    assert any("stored twice" in message for message in messages)


def test_a_calculation_that_cannot_be_converted_keeps_its_engine_copy(hazard_run):
    """A failed run stays readable where it ran (section 12)."""
    engine = FakeEngine(datastore=b"not a datastore at all")

    with pytest.raises(hazard_service.HazardExecutionError):
        run_it(hazard_run, engine)

    hazard_run.run.refresh_from_db()
    assert hazard_run.run.failure_stage == "export"
    assert not engine.removed


# -- running a removed calculation again, checked -----------------------------

def registered_and_removed(hazard_run):
    from apps.modelregistry.models import HazardSet

    run_it(hazard_run, FakeEngine())
    hazard_set = HazardSet.objects.get()
    assert hazard_set.openquake_calculation_removed
    return hazard_set


def test_a_removed_calculation_is_run_again_and_shown_to_be_the_same(hazard_run):
    hazard_set = registered_and_removed(hazard_run)
    engine = FakeEngine()

    calculation = hazard_service.restore_calculation(
        hazard_set, adapter_for(engine), poll_interval=0
    )

    assert calculation == 77
    assert "job.ini" in engine.submitted
    # Reproduced, so it is left on the engine for the caller to chain onto.
    assert not engine.removed


def test_a_calculation_that_does_not_reproduce_is_refused_and_removed(hazard_run):
    """Different ground motion chained onto would look exactly like a comparison."""
    import h5py

    hazard_set = registered_and_removed(hazard_run)
    changed = small_datastore()
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "changed.hdf5"
        target.write_bytes(changed)
        with h5py.File(target, "a") as store:
            store["gmf_data"]["gmv_0"][0] = 0.9
        changed = target.read_bytes()
    engine = FakeEngine(datastore=changed)

    with pytest.raises(hazard_service.HazardExecutionError, match="its ground motion"):
        hazard_service.restore_calculation(hazard_set, adapter_for(engine), poll_interval=0)

    assert engine.removed


def test_a_set_without_its_configuration_cannot_be_run_again(hazard_run):
    hazard_set = registered_and_removed(hazard_run)
    hazard_set.job_spec = None

    with pytest.raises(hazard_service.HazardExecutionError, match="does not record"):
        hazard_service.restore_calculation(
            hazard_set, adapter_for(FakeEngine()), poll_interval=0
        )


def test_a_configuration_that_now_resolves_differently_is_not_run_again(hazard_run):
    hazard_set = registered_and_removed(hazard_run)
    hazard_set.job_checksum = "b" * 64
    engine = FakeEngine()

    with pytest.raises(hazard_service.HazardExecutionError, match="different job"):
        hazard_service.restore_calculation(hazard_set, adapter_for(engine), poll_interval=0)

    assert not any(url.endswith("calc/run") for _, url in engine.calls)


# -- rebuilding a footprint from the stored calculation ---------------------------------
#
# A footprint is ground motion counted into intensity bins. When the bins change,
# the ground motion has not: CASS keeps the datastore, and a rebuild reads it again
# instead of asking OpenQuake for hours of new motion.

@pytest.fixture()
def computed(hazard_run):
    """A hazard set computed on the platform, with its datastore stored."""
    from apps.modelregistry.models import HazardSet

    run_it(hazard_run, FakeEngine())
    return HazardSet.objects.get()


@pytest.fixture()
def changed_bins(monkeypatch):
    """The platform's intensity bins, changed since the set was computed."""
    from cass_converter import pilot_bins

    monkeypatch.setattr(pilot_bins, "INTENSITY_BIN_COUNT", 40)


def rebuild_run(hazard_set, actor):
    run = Run.objects.create(
        kind=RunKind.HAZARD, label="Rebuild", execution_profile="model_build", created_by=actor
    )
    return HazardRun.objects.create(
        run=run,
        grid=hazard_set.grid,
        rebuild_of=hazard_set,
        openquake_calculation_id=hazard_set.openquake_calculation_id,
        created_by=actor,
    )


def footprint_states(hazard_set):
    from apps.artifacts.models import ArtifactLink

    return sorted(
        ArtifactLink.objects.filter(
            subject_type="hazard_set",
            subject_id=hazard_set.id,
            role__startswith="hazard_footprint_",
        ).values_list("artifact__state", flat=True)
    )


def test_a_set_binned_against_the_current_bins_is_not_rebuilt(computed, client_for, modeller):
    from apps.modelregistry import hazard as hazard_registry

    assert hazard_registry.rebuild_status(computed)["intensity_bins_current"] is True

    refused = client_for(modeller).post(f"{API}/hazard-sets/{computed.id}/rebuild/")

    assert refused.status_code == 409
    assert "same footprint" in refused.data["detail"]


def test_a_changed_bin_dictionary_shows_on_the_set(computed, changed_bins, client_for, modeller):
    listed = client_for(modeller).get(f"{API}/hazard-sets/{computed.id}/")

    status_ = listed.data["rebuild"]
    assert status_["intensity_bins_current"] is False
    assert status_["datastore_available"] is True
    assert status_["datastore_expires_at"]


def test_rebuilding_is_launched_as_a_run(computed, changed_bins, client_for, modeller, monkeypatch):
    queued: list[str] = []
    monkeypatch.setattr("apps.runs.tasks.rebuild_hazard.delay", lambda run_id: queued.append(run_id))

    launched = client_for(modeller).post(f"{API}/hazard-sets/{computed.id}/rebuild/")

    assert launched.status_code == 202, launched.data
    hazard_run = HazardRun.objects.get(id=queued[0])
    assert hazard_run.rebuild_of == computed
    assert hazard_run.run.label == f"Rebuild footprint of {computed.reference}"


def test_a_footprint_is_rebuilt_from_the_stored_calculation(computed, changed_bins, modeller):
    from apps.modelregistry import hazard as hazard_registry
    from apps.modelregistry.models import HazardSet

    hazard_run = rebuild_run(computed, modeller)

    hazard_service.rebuild(hazard_run, actor=modeller)

    rebuilt = HazardSet.objects.exclude(pk=computed.pk).get()
    assert rebuilt.rebuilt_from == computed
    assert rebuilt.version.endswith(
        "-b" + hazard_registry.current_intensity_bins_checksum()[:8]
    )
    # The same calculation, stored once, and the same facts about it.
    assert rebuilt.datastore_uri == computed.datastore_uri
    assert rebuilt.calculation_checksum == computed.calculation_checksum
    assert rebuilt.openquake_calculation_removed == computed.openquake_calculation_removed
    assert rebuilt.event_count == computed.event_count
    assert rebuilt.footprint_row_count > 0
    assert hazard_registry.rebuild_status(rebuilt)["intensity_bins_current"] is True

    hazard_run.run.refresh_from_db()
    assert hazard_run.run.state == RunState.SUCCEEDED
    stages = set(hazard_run.run.events.values_list("stage", flat=True))
    assert {"prepare", "export"} <= stages
    assert "submit" not in stages


def test_the_replaced_footprint_is_removed_once_nothing_uses_it(computed, changed_bins, modeller):
    """The largest table a set stores, kept twice, is what the rebuild must not leave."""
    from apps.modelregistry import hazard as hazard_registry

    hazard_service.rebuild(rebuild_run(computed, modeller), actor=modeller)

    assert set(footprint_states(computed)) == {"expired"}
    assert hazard_registry.rebuild_status(computed)["rebuilt_as"]
    # Only the footprints: the record, its occurrence table and the calculation stay.
    from apps.artifacts.models import Artifact, ArtifactLink

    occurrence = ArtifactLink.objects.get(
        subject_type="hazard_set", subject_id=computed.id, role="hazard_occurrence"
    )
    assert occurrence.artifact.state == "registered"
    assert Artifact.objects.get(uri=computed.datastore_uri).state == "registered"


def test_a_footprint_a_model_version_uses_is_kept_until_the_rebuilt_set_replaces_it(
    computed, changed_bins, modeller
):
    from apps.modelregistry import hazard as hazard_registry
    from apps.modelregistry.models import HazardSet

    from . import fixture_model

    model = fixture_model.register("ID", actor=modeller)
    model.hazard_set = computed
    model.save()

    hazard_service.rebuild(rebuild_run(computed, modeller), actor=modeller)

    assert set(footprint_states(computed)) == {"registered"}
    messages = HazardRun.objects.get(rebuild_of=computed).run.events.values_list("message", flat=True)
    assert any("still use it" in message for message in messages)

    rebuilt = HazardSet.objects.exclude(pk=computed.pk).get()
    model.refresh_from_db()
    # The fixture's functions demand four measures; this calculation carries two.
    model.vulnerability_set.imts_used = ["PGA", "SA(0.3)"]
    model.vulnerability_set.save(update_fields=["imts_used"])
    hazard_registry.attach(model, rebuilt, actor=modeller)

    assert set(footprint_states(computed)) == {"expired"}


def test_a_set_whose_calculation_has_expired_cannot_be_rebuilt(computed, changed_bins, client_for, modeller):
    from apps.artifacts.models import Artifact

    Artifact.objects.filter(uri=computed.datastore_uri).update(state="expired")

    refused = client_for(modeller).post(f"{API}/hazard-sets/{computed.id}/rebuild/")

    assert refused.status_code == 409
    assert "no longer stored" in refused.data["detail"]


def test_a_set_registered_from_exports_has_nothing_to_rebuild_from(computed, changed_bins, client_for, modeller):
    computed.datastore_uri = ""
    computed.save()

    refused = client_for(modeller).post(f"{API}/hazard-sets/{computed.id}/rebuild/")

    assert refused.status_code == 409
    assert "registered from exported tables" in refused.data["detail"]


def test_a_set_rebuilt_already_is_not_rebuilt_twice(computed, changed_bins, client_for, modeller):
    hazard_service.rebuild(rebuild_run(computed, modeller), actor=modeller)

    refused = client_for(modeller).post(f"{API}/hazard-sets/{computed.id}/rebuild/")

    assert refused.status_code == 409
    assert "already been rebuilt" in refused.data["detail"]


def test_an_analyst_cannot_rebuild_a_footprint(computed, changed_bins, api):
    assert api.post(f"{API}/hazard-sets/{computed.id}/rebuild/").status_code == 403


def test_a_package_is_not_built_from_a_footprint_binned_against_old_bins(computed, changed_bins, modeller):
    from apps.modelregistry import package
    from apps.modelregistry.package import PackageBuildError

    from . import fixture_model

    model = fixture_model.register("ID", actor=modeller)
    model.hazard_set = computed
    model.save()

    with pytest.raises(PackageBuildError, match="since changed"):
        package.gather(model)


# -- the other half of a package: functions discretised against the same bins ---------

def test_a_package_is_not_built_from_functions_discretised_against_old_bins(computed, modeller):
    """A damage table and a footprint read against each other bin by bin must share bins."""
    from apps.modelregistry import package
    from apps.modelregistry.package import PackageBuildError

    from . import fixture_model

    model = fixture_model.register("ID", actor=modeller)
    model.hazard_set = computed
    model.save()
    model.vulnerability_set.intensity_bins_checksum = "f" * 64
    model.vulnerability_set.save(update_fields=["intensity_bins_checksum"])

    with pytest.raises(PackageBuildError, match="Build the vulnerability set again"):
        package.gather(model)


def test_a_vulnerability_set_says_whether_its_bins_are_current(modeller, client_for):
    from . import fixture_model

    model = fixture_model.register("ID", actor=modeller)
    vulnerability = model.vulnerability_set
    listed = client_for(modeller).get(f"{API}/vulnerability-sets/{vulnerability.id}/")
    assert listed.data["intensity_bins_current"] in (True, None)

    vulnerability.intensity_bins_checksum = "f" * 64
    vulnerability.save(update_fields=["intensity_bins_checksum"])
    listed = client_for(modeller).get(f"{API}/vulnerability-sets/{vulnerability.id}/")
    assert listed.data["intensity_bins_current"] is False


def test_sets_registered_before_fingerprints_are_marked_with_the_bins_they_used(
    computed, modeller
):
    """Migration 0015: everything registered earlier used the dictionary this change widened."""
    import importlib

    from django.apps import apps as django_apps

    from apps.modelregistry import hazard as hazard_registry
    from apps.modelregistry.models import HazardSet

    from . import fixture_model

    model = fixture_model.register("ID", actor=modeller)
    HazardSet.objects.update(intensity_bins_checksum="", datastore_uri="")
    type(model.vulnerability_set).objects.update(intensity_bins_checksum="")

    migration = importlib.import_module(
        "apps.modelregistry.migrations.0015_domain_rebuild_and_intensity_bins"
    )
    migration.backfill(django_apps, None)

    backfilled = HazardSet.objects.get()
    assert backfilled.intensity_bins_checksum == migration.PREVIOUS_BINS_CHECKSUM
    assert backfilled.datastore_uri.endswith("/datastore.hdf5")
    model.vulnerability_set.refresh_from_db()
    assert model.vulnerability_set.intensity_bins_checksum == migration.PREVIOUS_BINS_CHECKSUM
    # And the platform then knows both need rebuilding.
    assert hazard_registry.rebuild_status(backfilled)["intensity_bins_current"] is False


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


# -- following a calculation that is still going ----------------------------
#
# A national calculation is hours inside a single pipeline stage, so the stage
# list alone tells an analyst nothing for most of the run. OpenQuake's status
# endpoint answers with a state and a description and no counts; its log is
# where the engine says how far it has got, and these hold that CASS reads it
# without ever letting that reading disturb the calculation.

def watch(hazard_run, monkeypatch) -> list[tuple]:
    """What the run said about its position each time the monitor waited."""
    seen: list[tuple] = []
    monkeypatch.setattr(
        hazard_service.time,
        "sleep",
        lambda _: seen.append(
            Run.objects.values_list("stage_progress", "stage_progress_label").get(
                pk=hazard_run.run_id
            )
        ),
    )
    return seen


def test_the_monitor_records_how_far_into_the_calculation_the_engine_is(
    hazard_run, monkeypatch
):
    engine = FakeEngine(
        statuses=("executing", "executing", "complete"),
        log=[
            ["t", "INFO", "job", "classical  25% [128 submitted, 96 queued]"],
            ["t", "INFO", "job", "classical  50% [128 submitted, 64 queued]"],
        ],
    )
    seen = watch(hazard_run, monkeypatch)

    run_it(hazard_run, engine)

    # The latest line, not the first: the log is read forward to where the
    # calculation actually is.
    assert seen[0] == (0.5, "classical")


def test_the_phase_is_kept_with_the_percentage(hazard_run, monkeypatch):
    """OpenQuake's percentage restarts for each phase, so the bare number lies."""
    engine = FakeEngine(
        statuses=("executing", "executing", "complete"),
        log=[
            ["t", "INFO", "job", "classical 100% [128 submitted, 0 queued]"],
            ["t", "INFO", "job", "computing gmfs   8% [64 submitted, 59 queued]"],
        ],
    )
    seen = watch(hazard_run, monkeypatch)

    run_it(hazard_run, engine)

    assert seen[0] == (0.08, "computing gmfs")


def test_a_line_that_merely_mentions_a_percentage_is_not_read_as_progress(hazard_run):
    """Progress is the engine's own task line, not any number with a per cent sign."""
    run = hazard_run.run
    job = EngineJob(engine_job_id="77", state=EngineState.RUNNING)

    class OnlyALog:
        def log_entries(self, calculation_id, *, start=0, stop=0):
            entries = [["t", "INFO", "job", "Sent 90% of the model to the workers"]]
            return entries[start:]

    read = hazard_service._follow(run, OnlyALog(), 77, job, 0)

    assert read == 1
    assert run.stage_progress is None


class RefusesItsLog(FakeEngine):
    """An engine that serves the calculation but not the log of it."""

    def request(self, method, url, **kwargs):
        if "/log/" in url:
            return FakeResponse(500, text="log unavailable")
        return super().request(method, url, **kwargs)


def test_a_log_the_engine_will_not_serve_does_not_stop_the_calculation(hazard_run):
    """Progress is a courtesy. Losing it must not cost a ten-hour calculation."""
    run_it(hazard_run, RefusesItsLog(statuses=("executing", "complete")))

    hazard_run.run.refresh_from_db()
    assert hazard_run.run.state == RunState.SUCCEEDED
    assert hazard_run.run.stage_progress is None


def test_a_finished_run_carries_no_fraction_from_a_stage_it_has_left(hazard_run):
    engine = FakeEngine(
        statuses=("executing", "complete"),
        log=[["t", "INFO", "job", "classical  25% [128 submitted, 96 queued]"]],
    )

    run_it(hazard_run, engine)

    hazard_run.run.refresh_from_db()
    assert hazard_run.run.stage_progress is None
    assert hazard_run.run.stage_progress_label == ""


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
