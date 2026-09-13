"""Converting a model version into an Oasis model package, through the API.

The path this holds is the one that was missing entirely: a hazard set and a
vulnerability set both registered, and nothing that could turn them into the
directory an Oasis worker loads. What these check is that the conversion runs
only under a policy somebody other than the requester approved, that the package
lands where the engine reads it, and that it names what it was built from.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from apps.audit.models import Approval
from apps.modelregistry import hazard as hazard_registry
from apps.modelregistry.assets import attach_damage_bins, attach_vulnerability_functions
from apps.runs.models import ConversionRun, Run, RunKind
from cass_core.runs import RunState

from . import fixture_model
from .conftest import API

pytestmark = pytest.mark.django_db

HEADER = (
    "#,,,,,\"generated_by='OpenQuake engine 3.23.4', "
    "start_date='2026-09-12T16:30:50', checksum=556311143\""
)
RUPTURE_HEADER = (
    "#,,,,,,,,,,\"generated_by='OpenQuake engine 3.23.4', "
    "start_date='2026-09-12T16:30:50', checksum=556311143, "
    "investigation_time=50.0, ses_per_logic_tree_path=20\""
)

EXPORTS = {
    "gmf-data_1.csv": (
        f"{HEADER}\nevent_id,gmv_SA(0.3),gmv_SA(0.6),custom_site_id\n"
        "0,4.00000E-01,2.00000E-01,101\n"
        "0,5.00000E-01,3.00000E-01,102\n"
        "1,2.00000E-01,1.00000E-01,101\n"
    ),
    "events_1.csv": f"{HEADER}\nevent_id,rup_id,rlz_id,year,ses_id\n0,2,0,90,14\n1,7,0,774,9\n",
    "ruptures_1.csv": (
        f"{RUPTURE_HEADER}\n"
        "rup_id,source_id,multiplicity,mag,centroid_lon,centroid_lat,centroid_depth,trt,strike,dip,rake\n"
        "2,1,1,5.1,106.5,-6.2,10.0,Active Shallow Crust,270.0,45.0,90.0\n"
        "7,1,1,6.4,106.9,-6.8,20.0,Active Shallow Crust,90.0,45.0,90.0\n"
    ),
    "realizations_1.csv": f"{HEADER}\nrlz_id,branch_path,weight\n0,A~A,1.0\n",
}

VULNERABILITY = "vulnerability_id,intensity_bin_id,damage_bin_id,probability\n" + "".join(
    f"{vid},{ib},2,1.0\n" for vid in range(1, 5) for ib in range(1, 51)
)

DAMAGE_BINS = (
    "bin_index,bin_from,bin_to,interpolation,interval_type\n"
    "1,0,0,0,1201\n2,0,1,0.5,1201\n"
)


@pytest.fixture()
def model_root(settings, tmp_path) -> pathlib.Path:
    root = tmp_path / "oasis-model"
    settings.CASS_OASIS_MODEL_ROOT = str(root)
    return root


@pytest.fixture()
def model_version(db, modeller, tmp_path):
    version = fixture_model.register("ID", actor=modeller)
    attach_vulnerability_functions(
        version.vulnerability_set, VULNERABILITY.encode(), actor=modeller
    )
    attach_damage_bins(version.vulnerability_set, DAMAGE_BINS.encode(), actor=modeller)
    return version


@pytest.fixture()
def hazard_set(model_version, modeller, tmp_path):
    directory = tmp_path / "exports"
    directory.mkdir()
    for name, text in EXPORTS.items():
        (directory / name).write_text(text, encoding="utf-8")
    registered, _ = hazard_registry.register(
        directory,
        country_code="ID",
        version="test-1",
        source=hazard_registry.SourceStatement(model="A test source model"),
        grid=model_version.grid,
        actor=modeller,
    )
    return registered


@pytest.fixture()
def approval(model_version, client_for, modeller, reviewer) -> Approval:
    """Requested by the modeller whose work it governs, decided by a reviewer."""
    requested = client_for(modeller).post(
        f"{API}/approvals/",
        {
            "gate": "converter_candidate",
            "subject_type": "model_version",
            "subject_id": str(model_version.id),
            "rationale": "Occurrence per event; measures as area-peril channels.",
        },
        format="json",
    )
    assert requested.status_code == 201, requested.data
    decided = client_for(reviewer).post(
        f"{API}/approvals/{requested.data['id']}/decide/",
        {"decision": "approved", "rationale": "Test policy for the platform run."},
        format="json",
    )
    assert decided.status_code == 200, decided.data
    return Approval.objects.get(id=requested.data["id"])


def attach(client, model_version, hazard_set):
    return client.post(
        f"{API}/model-versions/{model_version.id}/attach-hazard/",
        {"hazard_set": str(hazard_set.id)},
        format="json",
    )


def build(client, model_version, approval, monkeypatch):
    from apps.runs import tasks

    monkeypatch.setattr(
        "apps.runs.tasks.execute_conversion.delay",
        lambda conversion_id: tasks.execute_conversion(conversion_id),
    )
    return client.post(
        f"{API}/model-versions/{model_version.id}/build-package/",
        {"approval": str(approval.id)},
        format="json",
    )


# -- attaching --------------------------------------------------------------

def test_a_hazard_set_carrying_every_demanded_measure_attaches(
    client_for, modeller, model_version, hazard_set
):
    response = attach(client_for(modeller), model_version, hazard_set)

    assert response.status_code == 200, response.data
    model_version.refresh_from_db()
    assert model_version.hazard_set_id == hazard_set.id


def test_hazard_sets_are_listed_for_the_model_build_screen(client_for, modeller, hazard_set):
    listed = client_for(modeller).get(f"{API}/hazard-sets/")

    assert listed.status_code == 200
    assert [item["id"] for item in listed.data["results"]] == [str(hazard_set.id)]


# -- gates ------------------------------------------------------------------

def test_a_modeller_may_request_the_gate_that_governs_their_work(
    client_for, modeller, model_version
):
    response = client_for(modeller).post(
        f"{API}/approvals/",
        {
            "gate": "converter_candidate",
            "subject_type": "model_version",
            "subject_id": str(model_version.id),
        },
        format="json",
    )
    assert response.status_code == 201, response.data


def test_a_modeller_may_not_decide_a_gate(client_for, modeller, reviewer, model_version):
    requested = client_for(reviewer).post(
        f"{API}/approvals/",
        {
            "gate": "converter_candidate",
            "subject_type": "model_version",
            "subject_id": str(model_version.id),
        },
        format="json",
    )
    decided = client_for(modeller).post(
        f"{API}/approvals/{requested.data['id']}/decide/",
        {"decision": "approved", "rationale": "self-serve"},
        format="json",
    )
    assert decided.status_code == 403


# -- building ---------------------------------------------------------------

def test_a_package_is_built_and_deployed_where_the_engine_reads_it(
    client_for, modeller, model_version, hazard_set, approval, model_root, monkeypatch
):
    attach(client_for(modeller), model_version, hazard_set)

    response = build(client_for(modeller), model_version, approval, monkeypatch)

    assert response.status_code == 202, response.data
    run = Run.objects.get(id=response.data["run"])
    assert run.kind == RunKind.CONVERSION
    assert run.state == RunState.SUCCEEDED, run.failure_summary
    for path in ("oasislmf.json", "keys_data/lookup.py", "model_data/footprint.bin",
                 "keys_data/vendor/cass_keys/assets.py", "MANIFEST.json"):
        assert (model_root / path).is_file(), path


def test_the_package_names_what_it_was_built_from(
    client_for, modeller, model_version, hazard_set, approval, model_root, monkeypatch
):
    attach(client_for(modeller), model_version, hazard_set)
    build(client_for(modeller), model_version, approval, monkeypatch)

    manifest = json.loads((model_root / "MANIFEST.json").read_text())
    assert manifest["provenance"]["hazard_set"] == hazard_set.reference
    assert manifest["provenance"]["model_version"] == model_version.reference
    assert manifest["identity"]["model_id"] == "EQ"


def test_the_manifest_is_kept_in_the_artifact_store(
    client_for, modeller, model_version, hazard_set, approval, model_root, monkeypatch
):
    """The package lives on the engine's volume; its manifest lives where it is governed."""
    from apps.artifacts.models import ArtifactLink

    attach(client_for(modeller), model_version, hazard_set)
    response = build(client_for(modeller), model_version, approval, monkeypatch)

    link = ArtifactLink.objects.get(subject_type="conversion_run", subject_id=response.data["run"])
    assert link.role == "oasis_package_manifest"
    assert ConversionRun.objects.get(run_id=response.data["run"]).frequency_preserved is True


def test_no_package_is_built_without_a_decided_approval(
    client_for, modeller, model_version, hazard_set, model_root, monkeypatch
):
    attach(client_for(modeller), model_version, hazard_set)
    open_gate = Approval.objects.create(
        gate=Approval.Gate.CONVERTER_CANDIDATE,
        subject_type="model_version",
        subject_id=model_version.id,
        requested_by=modeller,
    )

    response = build(client_for(modeller), model_version, open_gate, monkeypatch)

    assert response.status_code == 409
    assert "has been decided" in response.data["detail"]
    assert not (model_root / "oasislmf.json").exists()


def test_no_package_is_built_without_hazard(
    client_for, modeller, model_version, approval, model_root, monkeypatch
):
    response = build(client_for(modeller), model_version, approval, monkeypatch)

    assert response.status_code == 409
    assert "Attach a hazard set" in response.data["detail"]


def test_an_analyst_cannot_build_a_package(
    client_for, analyst, model_version, hazard_set, approval, monkeypatch
):
    response = build(client_for(analyst), model_version, approval, monkeypatch)
    assert response.status_code == 403
