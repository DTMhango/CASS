"""The PiWind baseline against a live Oasis Platform.

Section 17 asks for the successful PiWind command-line test to become an
automated integration test triggered through a minimal CASS workflow, and
section 12 makes PiWind the end-to-end regression baseline. Every other test in
this repository talks to a scripted Oasis; this one talks to a real 2.5.7
server and is the only place the two halves of the boundary -- what CASS sends
and what Oasis actually accepts -- are checked against each other.

It is marked ``integration`` and deselected by default, so an ordinary test run
still needs no engines. To run it:

    docker compose -f deploy/docker-compose.yml \\
                   -f deploy/docker-compose.integration.yml \\
                   --env-file .env up -d oasis-api oasis-worker
    CASS_OASIS_LIVE_URL=http://localhost:8100/api \\
    CASS_OASIS_LIVE_PASSWORD=... \\
        pytest -m integration cass_api/tests/test_piwind_live.py

The worker must be started with the PiWind model root mounted and identified as
OasisLMF/PiWind/1; ``CASS_MODEL_DATA_PATH`` points at it.

PiWind is a wind portfolio deliberately. Running it through CASS's earthquake
validator is part of the point: the platform should say "no modelled peril
applies" for all ten locations rather than silently return zero loss, and that
finding is a warning rather than an error, so the version still publishes and
the engine boundary can be exercised on real data.
"""

from __future__ import annotations

import os

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.artifacts.models import ArtifactLink
from apps.exposure.models import ExposureVersion
from apps.modelregistry.models import AreaPerilGrid, ModelVersion, VulnerabilitySet
from apps.runs.models import AnalysisRun, Run, RunKind
from apps.runs.services import execute
from cass_adapters.oasis import OasisAdapter
from cass_core.runs import RunState

from .conftest import API

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

LIVE_URL = os.environ.get("CASS_OASIS_LIVE_URL", "")
LIVE_USER = os.environ.get("CASS_OASIS_LIVE_USERNAME", "admin")
LIVE_PASSWORD = os.environ.get("CASS_OASIS_LIVE_PASSWORD", "")

#: Generating inputs and running 1,447 events takes minutes, not seconds.
POLL_INTERVAL = float(os.environ.get("CASS_OASIS_LIVE_POLL", "5"))
POLL_TIMEOUT = float(os.environ.get("CASS_OASIS_LIVE_TIMEOUT", "1800"))

live = pytest.mark.skipif(
    not LIVE_URL, reason="set CASS_OASIS_LIVE_URL to run against a live Oasis Platform"
)


@pytest.fixture()
def engine() -> OasisAdapter:
    return OasisAdapter(LIVE_URL, username=LIVE_USER, password=LIVE_PASSWORD)


@pytest.fixture()
def piwind_exposure(api, project, piwind_root) -> ExposureVersion:
    """The official PiWind OED, published through the CASS exposure workspace."""
    created = api.post(
        f"{API}/exposure-versions/",
        {
            "project": str(project.id),
            "name": "PiWind baseline",
            "cedant": "Oasis PiWind reference model",
        },
        format="json",
    )
    assert created.status_code == 201, created.data
    exposure_id = created.data["id"]

    for kind, filename in (
        ("location", "SourceLocOEDPiWind10.csv"),
        ("account", "SourceAccOEDPiWind.csv"),
        ("reins_info", "SourceReinsInfoOEDPiWind.csv"),
        ("reins_scope", "SourceReinsScopeOEDPiWind.csv"),
    ):
        payload = (piwind_root / filename).read_bytes()
        uploaded = api.post(
            f"{API}/exposure-versions/{exposure_id}/files/",
            {"kind": kind, "file": SimpleUploadedFile(filename, payload, "text/csv")},
            format="multipart",
        )
        assert uploaded.status_code == 201, uploaded.data

    validated = api.post(f"{API}/exposure-versions/{exposure_id}/validate/")
    assert validated.status_code == 200, validated.data
    published = api.post(f"{API}/exposure-versions/{exposure_id}/publish/")
    assert published.status_code == 200, published.data
    return ExposureVersion.objects.get(id=exposure_id)


@pytest.fixture()
def piwind_model_version(db, modeller, settings) -> ModelVersion:
    settings.CASS_OASIS_MODEL_SUPPLIER_ID = "OasisLMF"
    settings.CASS_OASIS_MODEL_ID = "PiWind"
    settings.CASS_OASIS_MODEL_VERSION_ID = "1"

    grid = AreaPerilGrid.objects.create(
        country_code="GB",
        version="piwind-1",
        label="PiWind reference grid",
        base_resolution_deg="0.100000",
        refined_resolution_deg="0.100000",
        cell_count=0,
        created_by=modeller,
    )
    vulnerability = VulnerabilitySet.objects.create(
        country_code="GB",
        version="piwind-1",
        source="Oasis PiWind reference model",
        function_count=1,
        created_by=modeller,
    )
    return ModelVersion.objects.create(
        country_code="GB",
        version="piwind-1",
        label="Oasis PiWind reference model",
        grid=grid,
        vulnerability_set=vulnerability,
        oasis_version="2.5.7",
        created_by=modeller,
    )


# -- the engine is what it claims to be -------------------------------------

@live
def test_the_live_server_reports_a_tested_version(engine):
    assert engine.healthy() is True
    assert engine.check_compatible().version == "2.5.7"


@live
def test_the_piwind_model_is_registered(engine):
    """The run cannot resolve a model the server does not have."""
    model = engine.find_model("OasisLMF", "PiWind", "1")
    assert model.id > 0


# -- the milestone, for real -------------------------------------------------

@live
def test_piwind_runs_end_to_end_through_cass(
    piwind_exposure, piwind_model_version, analyst, project, engine
):
    """Milestone 2 against a real engine: OED in, ORD package out."""
    run = Run.objects.create(
        kind=RunKind.ANALYSIS,
        project=project,
        label="PiWind live baseline",
        created_by=analyst,
    )
    analysis_run = AnalysisRun.objects.create(
        run=run,
        exposure_version=piwind_exposure,
        model_version=piwind_model_version,
        perspectives=["ground_up"],
        created_by=analyst,
    )

    execute(
        analysis_run,
        adapter=engine,
        actor=analyst,
        poll_interval=POLL_INTERVAL,
        timeout=POLL_TIMEOUT,
    )

    run.refresh_from_db()
    analysis_run.refresh_from_db()

    assert run.state == RunState.SUCCEEDED, run.failure_summary + "\n" + run.failure_detail
    assert analysis_run.oasis_portfolio_id
    assert analysis_run.oasis_analysis_id

    link = ArtifactLink.objects.get(
        subject_type="analysis_run", subject_id=run.id, role="oasis_output"
    )
    assert link.artifact.size_bytes > 0
    assert link.artifact.checksum

    manifest = run.manifest
    assert manifest["engine"]["version"] == "2.5.7"
    assert manifest["inputs"]["oasis_model"]["model_id"] == "PiWind"
    assert manifest["output"]["size_bytes"] == link.artifact.size_bytes


@live
def test_the_wind_portfolio_is_reported_as_unmodelled_rather_than_zero(piwind_exposure):
    """Section 15's hidden-not-at-risk failure mode, on real data.

    PiWind is a wind portfolio. The earthquake release must say so on every
    location rather than quietly producing a zero loss that reads as a result.
    """
    report = piwind_exposure.validation_report
    codes = {finding["code"] for finding in report["validation"]["findings"]}
    assert "no_modelled_peril" in codes
    assert report["publishable"] is True
