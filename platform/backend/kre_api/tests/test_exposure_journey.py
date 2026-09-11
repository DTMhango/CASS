"""Milestone M1: the analyst journey from sign-in to a published exposure version.

Section 13 defines M1 as signing in to KRE, creating or importing a small
location portfolio, previewing valid OED and observing a durable background
task through the web interface. These tests drive that journey through the API
the React application uses, with no engine interface involved.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.artifacts.models import Artifact, ArtifactState
from apps.audit.models import AuditAction, AuditEvent
from apps.common.models import ImmutableError
from apps.exposure.models import ExposureState, ExposureVersion

from .conftest import API

pytestmark = pytest.mark.django_db


def upload(api, exposure_id, kind, payload, filename="source.csv"):
    return api.post(
        f"{API}/exposure-versions/{exposure_id}/files/",
        {"kind": kind, "file": SimpleUploadedFile(filename, payload, "text/csv")},
        format="multipart",
    )


def create_version(api, project, name="Pilot portfolio"):
    response = api.post(
        f"{API}/exposure-versions/",
        {"project": str(project.id), "name": name, "cedant": "Test Cedant"},
        format="json",
    )
    assert response.status_code == 201, response.data
    return response.data["id"]


# -- the journey ------------------------------------------------------------

def test_analyst_can_complete_the_m1_journey(api, project, earthquake_location_csv):
    exposure_id = create_version(api, project)

    assert upload(api, exposure_id, "location", earthquake_location_csv).status_code == 201

    preview = api.get(f"{API}/exposure-versions/{exposure_id}/preview/")
    assert preview.status_code == 200
    assert preview.data["files"]["location"]["row_count"] == 3

    validated = api.post(f"{API}/exposure-versions/{exposure_id}/validate/")
    assert validated.status_code == 200
    assert validated.data["state"] == ExposureState.VALIDATED
    assert validated.data["is_publishable"] is True
    assert Decimal(validated.data["total_tiv"]) == Decimal("9900000")

    published = api.post(f"{API}/exposure-versions/{exposure_id}/publish/")
    assert published.status_code == 200
    assert published.data["state"] == ExposureState.PUBLISHED
    assert published.data["is_frozen"] is True
    assert published.data["is_usable_by_runs"] is True


def test_validation_returns_business_language_findings(api, project):
    """An analyst sees what is wrong and what to do, not a parser error."""
    broken = (
        b"PortNumber,AccNumber,LocNumber,BuildingID,CountryCode,Latitude,Longitude,"
        b"OccupancyCode,LocPerilsCovered,BuildingTIV,LocCurrency\n"
        b"1,ACC-1,LOC-1,1,ID,,106.8456,1100,QEQ,4500000,IDR\n"
    )
    exposure_id = create_version(api, project)
    upload(api, exposure_id, "location", broken)
    api.post(f"{API}/exposure-versions/{exposure_id}/validate/")

    findings = api.get(f"{API}/exposure-versions/{exposure_id}/findings/")
    assert findings.status_code == 200
    assert findings.data["blocking"] is True

    missing = next(f for f in findings.data["findings"] if f["code"] == "missing_value")
    assert missing["field"] == "Latitude"
    assert missing["message"] == "Latitude is required and was not supplied."
    assert missing["remediation"]
    assert "LocNumber=LOC-1" in missing["record_key"]


def test_publication_is_refused_while_a_blocking_finding_stands(api, project):
    broken = (
        b"PortNumber,AccNumber,LocNumber,BuildingID,CountryCode,Latitude,Longitude,"
        b"OccupancyCode,LocPerilsCovered,BuildingTIV,LocCurrency\n"
        b"1,ACC-1,LOC-1,1,ID,0,0,1100,QEQ,4500000,IDR\n"
    )
    exposure_id = create_version(api, project)
    upload(api, exposure_id, "location", broken)
    api.post(f"{API}/exposure-versions/{exposure_id}/validate/")

    response = api.post(f"{API}/exposure-versions/{exposure_id}/publish/")
    assert response.status_code == 409
    assert "blocking validation finding" in response.data["detail"]


def test_publishing_without_validating_is_refused(api, project, earthquake_location_csv):
    exposure_id = create_version(api, project)
    upload(api, exposure_id, "location", earthquake_location_csv)

    response = api.post(f"{API}/exposure-versions/{exposure_id}/publish/")
    assert response.status_code == 409
    assert "Validate the exposure version" in response.data["detail"]


def test_a_published_version_cannot_be_changed(api, project, earthquake_location_csv):
    """Section 5: raw exposure is immutable; a correction is a new version."""
    exposure_id = create_version(api, project)
    upload(api, exposure_id, "location", earthquake_location_csv)
    api.post(f"{API}/exposure-versions/{exposure_id}/validate/")
    api.post(f"{API}/exposure-versions/{exposure_id}/publish/")

    response = upload(api, exposure_id, "location", earthquake_location_csv)
    assert response.status_code == 409
    assert "Create a new version" in response.data["detail"]

    version = ExposureVersion.objects.get(id=exposure_id)
    with pytest.raises(ImmutableError):
        version.cedant = "Someone else"
        version.save()


def test_a_correction_creates_a_new_version_number(api, project, earthquake_location_csv):
    first = create_version(api, project, name="Same portfolio")
    second = create_version(api, project, name="Same portfolio")
    assert ExposureVersion.objects.get(id=first).version == 1
    assert ExposureVersion.objects.get(id=second).version == 2


# -- perspectives -----------------------------------------------------------

def test_location_only_portfolio_reports_ground_up_alone(
    api, project, earthquake_location_csv
):
    """Section 8: no placeholder file may imply an unsupported perspective."""
    exposure_id = create_version(api, project)
    upload(api, exposure_id, "location", earthquake_location_csv)
    response = api.post(f"{API}/exposure-versions/{exposure_id}/validate/")

    availability = {
        item["perspective"]: item for item in response.data["supported_perspectives"]
    }
    assert availability["ground_up"]["available"] is True
    assert availability["insured"]["available"] is False
    assert "account file is required" in availability["insured"]["reason"]


def test_adding_an_account_file_enables_the_insured_perspective(
    api, project, piwind_location_csv, piwind_account_csv
):
    exposure_id = create_version(api, project, name="PiWind baseline")
    upload(api, exposure_id, "location", piwind_location_csv)
    upload(api, exposure_id, "account", piwind_account_csv)
    response = api.post(f"{API}/exposure-versions/{exposure_id}/validate/")

    availability = {
        item["perspective"]: item for item in response.data["supported_perspectives"]
    }
    assert availability["insured"]["available"] is True
    assert availability["reinsurance"]["available"] is False


# -- artifacts and lineage --------------------------------------------------

def test_upload_registers_a_checksummed_artifact(api, project, earthquake_location_csv):
    exposure_id = create_version(api, project)
    response = upload(api, exposure_id, "location", earthquake_location_csv, "loc.csv")

    assert response.data["checksum"].startswith("sha256:")
    assert response.data["uri"].startswith("kre://")

    artifact = Artifact.objects.get(uri=response.data["uri"])
    assert artifact.state == ArtifactState.REGISTERED
    assert artifact.role == "oed_location"
    assert artifact.project == project
    assert artifact.original_filename == "loc.csv"
    # Portfolio data is retained under business policy, so it has no expiry.
    assert artifact.expires_at is None


def test_attached_files_are_listed_with_their_checksums(
    api, project, earthquake_location_csv
):
    exposure_id = create_version(api, project)
    upload(api, exposure_id, "location", earthquake_location_csv)
    response = api.get(f"{API}/exposure-versions/{exposure_id}/")

    attached = response.data["attached_files"]
    assert len(attached) == 1
    assert attached[0]["role"] == "oed_location"
    assert attached[0]["checksum"].startswith("sha256:")


def test_an_unknown_file_kind_is_rejected_with_the_accepted_list(api, project):
    exposure_id = create_version(api, project)
    response = api.post(
        f"{API}/exposure-versions/{exposure_id}/files/",
        {"kind": "hurricane", "file": SimpleUploadedFile("x.csv", b"a,b\n1,2\n", "text/csv")},
        format="multipart",
    )
    assert response.status_code == 400
    assert set(response.data["accepted"]) == {
        "location", "account", "reins_info", "reins_scope"
    }


def test_validation_without_a_location_file_explains_why(api, project, piwind_account_csv):
    exposure_id = create_version(api, project)
    upload(api, exposure_id, "account", piwind_account_csv)
    response = api.post(f"{API}/exposure-versions/{exposure_id}/validate/")
    assert response.status_code == 409
    assert "location file is required" in response.data["detail"]


# -- audit ------------------------------------------------------------------

def test_the_journey_is_written_to_the_audit_trail(
    api, project, analyst, earthquake_location_csv
):
    exposure_id = create_version(api, project)
    upload(api, exposure_id, "location", earthquake_location_csv)
    api.post(f"{API}/exposure-versions/{exposure_id}/validate/")
    api.post(f"{API}/exposure-versions/{exposure_id}/publish/")

    events = AuditEvent.objects.filter(
        subject_type="exposure_version", subject_id=str(exposure_id)
    )
    actions = set(events.values_list("action", flat=True))
    assert {AuditAction.UPLOAD, AuditAction.UPDATE, AuditAction.PUBLISH} <= actions

    publication = events.get(action=AuditAction.PUBLISH)
    assert publication.actor == analyst
    assert publication.project == project
    assert publication.after_reference["state"] == ExposureState.PUBLISHED
    assert publication.correlation_id
