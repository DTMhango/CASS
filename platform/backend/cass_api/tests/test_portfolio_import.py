"""Work package 1: the secure importer.

The acceptance checks in the integration brief are counts against the real
workbook, which is confidential and is not in this repository. What is checked
here is the behaviour those counts depend on: an immutable raw artifact, both
sheets staged without joining, the schema and parser versions in the audit
trail, cohorts assigned with their rule version, a review queue, and a
transformation manifest that does not carry insured names unless someone with
the right role asks for them.

The fixtures are the synthetic structural extract from the ``cass_extract``
package, which carries every awkward shape the real one has.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

import cass_extract as extract
from apps.artifacts.models import Artifact, ArtifactLink
from apps.audit.models import AuditAction, AuditEvent
from apps.exposure.extract import (
    ExtractImportError,
    accept,
    import_extract,
    transformation_manifest,
)
from apps.exposure.models import (
    ImportBatch,
    ImportState,
    ReviewState,
    SourcePolicyRow,
    SourceRiskLocation,
)

from .conftest import API, make_user

# The synthetic extract lives with the package whose rules it exercises.
_FIXTURES = (
    Path(__file__).resolve().parents[2] / "packages" / "cass_extract" / "tests"
)
if str(_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_FIXTURES))

from fixtures import as_workbook, structural_extract  # noqa: E402

pytestmark = pytest.mark.django_db


@pytest.fixture()
def workbook() -> bytes:
    policies, locations = structural_extract()
    return as_workbook(policies, locations).getvalue()


@pytest.fixture()
def batch(project, analyst, workbook) -> ImportBatch:
    return import_extract(
        project, workbook, filename="extract.xlsx", actor=analyst
    )


# -- the immutable raw artifact ------------------------------------------------

def test_the_source_is_registered_before_it_is_parsed(project, analyst):
    """A workbook that cannot be read is still evidence of what was supplied."""
    result = import_extract(
        project, b"not a workbook", filename="broken.xlsx", actor=analyst
    )

    assert result.state == ImportState.REJECTED
    assert "could not be opened" in result.rejection_reason
    assert result.source_artifact is not None
    assert result.source_artifact.checksum


def test_the_raw_workbook_is_stored_with_a_checksum(batch, workbook):
    artifact = batch.source_artifact
    assert artifact.checksum.startswith("sha256:")
    assert artifact.size_bytes == len(workbook)
    assert artifact.original_filename == "extract.xlsx"


def test_the_source_is_project_data_under_portfolio_retention(batch, project):
    """Section 4.1: restricted to project members, kept out of Git and images."""
    artifact = batch.source_artifact
    assert artifact.project_id == project.id
    assert artifact.retention == "portfolio"
    assert artifact.access == "project"


def test_the_source_is_linked_to_the_batch_that_read_it(batch):
    assert ArtifactLink.objects.filter(
        subject_type="import_batch", subject_id=batch.id, direction="input"
    ).exists()


# -- staging -------------------------------------------------------------------

def test_both_sheets_are_staged(batch):
    assert batch.policy_row_count == 10
    assert batch.location_row_count == 11
    assert SourcePolicyRow.objects.filter(batch=batch).count() == 10
    assert SourceRiskLocation.objects.filter(batch=batch).count() == 11


def test_reported_value_is_staged_as_decimal_at_kre_share(batch):
    row = SourcePolicyRow.objects.get(batch=batch, policy_id="P-1")
    assert row.gross_limit == Decimal("1000000.00")
    assert row.values["gross_limit"] == "1000000.00"


def test_the_source_text_survives_staging(batch):
    """Section 5: no transformation silently replaces a reported field."""
    row = SourcePolicyRow.objects.get(batch=batch, policy_id="P-1")
    assert row.raw["gross_limit"] == "1000000.00"


def test_secondary_locations_are_staged_not_filtered_away(batch):
    """primary_location is an attribute, not a filter that deletes sites."""
    multi = SourceRiskLocation.objects.filter(batch=batch, business_id="B-MULTI")
    assert multi.count() == 3
    assert multi.filter(primary_location=False).count() == 2


def test_a_country_is_mapped_to_a_validated_iso_code(batch):
    nepal = SourceRiskLocation.objects.get(batch=batch, business_id="B-NEPAL")
    assert nepal.country == "Nepal"
    assert nepal.country_code == "NP"


def test_an_unrecognised_country_leaves_the_code_empty(project, analyst):
    """A guess here would route a location to the wrong national grid."""
    policies, locations = structural_extract()
    locations[0]["country"] = "Atlantis"
    result = import_extract(
        project, as_workbook(policies, locations).getvalue(),
        filename="extract.xlsx", actor=analyst,
    )
    row = SourceRiskLocation.objects.get(batch=result, business_id="B-SINGLE")
    assert row.country_code == ""


# -- versions in the audit trail -----------------------------------------------

def test_the_batch_records_every_rule_version_that_decided_it(batch):
    assert batch.schema_version == extract.SCHEMA_VERSION
    assert batch.parser_version == extract.PARSER_VERSION
    assert batch.cohort_rule_version == extract.COHORT_RULE_VERSION
    assert batch.join_rule_version == extract.JOIN_RULE_VERSION
    assert batch.profile == "Klapton Re geocoded policy extract"


def test_the_checksum_and_parser_version_reach_the_audit_trail(batch):
    event = AuditEvent.objects.filter(
        subject_type="import_batch", subject_id=batch.id, action=AuditAction.CREATE
    ).first()
    assert event is not None
    assert event.after_reference["checksum"] == batch.source_checksum
    assert event.after_reference["parser_version"] == extract.PARSER_VERSION


def test_the_upload_is_audited_without_a_row_of_the_source(project, analyst, workbook):
    import_extract(project, workbook, filename="extract.xlsx", actor=analyst)
    event = AuditEvent.objects.filter(
        subject_type="project", action=AuditAction.UPLOAD
    ).first()
    assert set(event.after_reference) == {"role", "uri", "checksum"}


# -- idempotency ----------------------------------------------------------------

def test_reimporting_the_same_bytes_returns_the_same_batch(project, analyst, workbook):
    """Section 8: rerunning the same checksum is idempotent."""
    first = import_extract(project, workbook, filename="extract.xlsx", actor=analyst)
    second = import_extract(project, workbook, filename="extract.xlsx", actor=analyst)

    assert first.id == second.id
    assert ImportBatch.objects.filter(project=project).count() == 1
    assert SourceRiskLocation.objects.filter(batch=first).count() == 11


def test_a_different_extract_is_a_different_batch(project, analyst, workbook):
    policies, locations = structural_extract()
    policies[0]["gross_limit"] = "1100000.00"
    other = as_workbook(policies, locations).getvalue()

    import_extract(project, workbook, filename="a.xlsx", actor=analyst)
    import_extract(project, other, filename="b.xlsx", actor=analyst)
    assert ImportBatch.objects.filter(project=project).count() == 2


# -- the join report ------------------------------------------------------------

def test_the_join_report_is_stored_with_the_batch(batch):
    report = batch.join_report
    assert report["policy_rows"] == 10
    assert report["location_rows"] == 11
    assert report["unique_location_keys"] == 11
    assert report["primary_locations"] == 8
    assert report["secondary_locations"] == 3


def test_a_business_on_two_policy_rows_blocks_acceptance(batch, analyst):
    """Joining on the business reference alone would repeat its locations."""
    assert batch.blocking is True
    assert batch.may_accept is False

    with pytest.raises(ExtractImportError, match="blocking join finding"):
        accept(batch, actor=analyst)


def test_a_clean_extract_can_be_accepted(project, analyst):
    policies, locations = structural_extract()
    policies = [p for p in policies if p["policy_id"] != "P-4b"]
    clean = import_extract(
        project, as_workbook(policies, locations).getvalue(),
        filename="clean.xlsx", actor=analyst,
    )
    assert clean.blocking is False

    accept(clean, actor=analyst)
    clean.refresh_from_db()
    assert clean.state == ImportState.ACCEPTED
    assert clean.accepted_by_id == analyst.id
    assert clean.accepted_at is not None


def test_a_batch_cannot_be_accepted_twice(project, analyst):
    policies, locations = structural_extract()
    policies = [p for p in policies if p["policy_id"] != "P-4b"]
    clean = import_extract(
        project, as_workbook(policies, locations).getvalue(),
        filename="clean.xlsx", actor=analyst,
    )
    accept(clean, actor=analyst)
    with pytest.raises(ExtractImportError, match="cannot be accepted"):
        accept(clean, actor=analyst)


# -- cohorts and the review queue -------------------------------------------------

def test_every_location_carries_a_cohort_and_the_rule_that_assigned_it(batch):
    rows = SourceRiskLocation.objects.filter(batch=batch)
    assert rows.filter(cohort="").count() == 0
    assert {row.cohort_rule_version for row in rows} == {extract.COHORT_RULE_VERSION}
    assert all(row.cohort_reason for row in rows)


def test_the_cohort_profile_is_stored_for_the_review_screen(batch):
    counts = batch.cohort_profile["counts"]
    assert counts["A"] == 9
    assert counts["B"] == 1
    assert counts["C"] == 1


def test_only_the_backlog_cohort_carries_work_someone_owes(batch):
    """Marking every row pending would make the queue meaningless."""
    pending = SourceRiskLocation.objects.filter(
        batch=batch, review_state=ReviewState.PENDING
    )
    assert pending.count() == 1
    assert pending.first().cohort == "C"

    assert (
        SourceRiskLocation.objects.filter(
            batch=batch, review_state=ReviewState.NOT_REQUIRED
        ).count()
        == 10
    )


# -- the transformation manifest ---------------------------------------------------

def test_the_manifest_states_the_source_and_every_rule_version(batch):
    manifest = transformation_manifest(batch)
    assert manifest["source"]["checksum"] == batch.source_checksum
    assert manifest["versions"] == {
        "schema": extract.SCHEMA_VERSION,
        "parser": extract.PARSER_VERSION,
        "cohort_rules": extract.COHORT_RULE_VERSION,
        "join_rules": extract.JOIN_RULE_VERSION,
    }


def test_the_manifest_withholds_counterparty_names_by_default(batch):
    """A manifest is what gets attached to a ticket. The default must be safe."""
    manifest = transformation_manifest(batch)
    assert manifest["confidential_columns_included"] is False
    assert "insured_name" in manifest["confidential_columns_withheld"]
    assert "Example Insured" not in str(manifest)


def test_the_manifest_states_the_value_basis(batch):
    """Nobody should have to remember that the share is already applied."""
    basis = transformation_manifest(batch)["value_basis"]
    assert "KRE's share" in basis
    assert "not applied again" in basis


def test_the_manifest_totals_value_only_for_single_cohort_businesses(batch):
    """A business split across cohorts belongs to neither for a value total."""
    manifest = transformation_manifest(batch)
    cohort_tiv = manifest["cohort_tiv_at_kre_share_usd"]

    # B-PARTIAL has one cohort A site and one cohort C site.
    assert "mixed" in cohort_tiv
    assert Decimal(cohort_tiv["mixed"]) == Decimal("2000000.00")


def test_the_manifest_carries_the_review_queue(batch):
    queue = transformation_manifest(batch)["review_queue"]
    assert queue["pending"] == 1
    assert queue["by_country"]["Indonesia"] == 1


# -- the API -----------------------------------------------------------------------

def test_an_analyst_can_upload_an_extract(api, project, workbook):
    response = api.post(
        f"{API}/portfolio-imports/upload/",
        {
            "project": str(project.id),
            "file": SimpleUploadedFile("extract.xlsx", workbook),
            "snapshot_date": "2026-06-30",
        },
        format="multipart",
    )
    assert response.status_code == 201, response.data
    assert response.data["policy_row_count"] == 10
    assert response.data["location_row_count"] == 11
    assert response.data["blocking"] is True


def test_an_upload_needs_a_project_the_caller_belongs_to(client_for, outsider, project, workbook):
    response = client_for(outsider).post(
        f"{API}/portfolio-imports/upload/",
        {"project": str(project.id), "file": SimpleUploadedFile("e.xlsx", workbook)},
        format="multipart",
    )
    assert response.status_code == 400
    assert ImportBatch.objects.count() == 0


def test_an_upload_without_a_file_says_so(api, project):
    response = api.post(
        f"{API}/portfolio-imports/upload/", {"project": str(project.id)},
        format="multipart",
    )
    assert response.status_code == 400
    assert "workbook" in response.data["detail"]


def test_the_review_queue_is_filterable(api, batch):
    response = api.get(
        f"{API}/portfolio-imports/{batch.id}/locations/?review_state=pending"
    )
    assert response.status_code == 200
    rows = response.data["results"] if "results" in response.data else response.data
    assert len(rows) == 1
    assert rows[0]["cohort"] == "C"


def test_the_cohort_a_set_is_what_the_benchmark_may_use(api, batch):
    response = api.get(f"{API}/portfolio-imports/{batch.id}/locations/?cohort=A")
    rows = response.data["results"] if "results" in response.data else response.data
    assert len(rows) == 9
    assert all(row["cohort"] == "A" for row in rows)


def test_an_analyst_sees_the_address_but_a_modeller_does_not(client_for, modeller, api, batch):
    """A modeller builds from coordinates and value, not from whose building it is."""
    shown = api.get(f"{API}/portfolio-imports/{batch.id}/locations/").data
    rows = shown["results"] if "results" in shown else shown
    assert rows[0]["address"]

    # A modeller is not a member of this project, so grant membership first.
    from apps.projects.models import ProjectMembership, ProjectRole

    ProjectMembership.objects.create(
        project=batch.project, user=modeller, role=ProjectRole.CONTRIBUTOR
    )
    hidden = client_for(modeller).get(
        f"{API}/portfolio-imports/{batch.id}/locations/"
    ).data
    rows = hidden["results"] if "results" in hidden else hidden
    assert rows[0]["address"] is None


def test_the_manifest_endpoint_withholds_names_by_default(api, batch):
    response = api.get(f"{API}/portfolio-imports/{batch.id}/manifest/")
    assert response.status_code == 200
    assert response.data["confidential_columns_included"] is False


def test_a_manifest_download_is_audited(api, batch):
    api.get(f"{API}/portfolio-imports/{batch.id}/manifest/")
    assert AuditEvent.objects.filter(
        subject_type="import_batch", subject_id=batch.id, action=AuditAction.DOWNLOAD
    ).exists()


def test_a_role_without_permission_cannot_download_names(client_for, db, batch):
    from apps.accounts.models import PlatformRole
    from apps.projects.models import ProjectMembership, ProjectRole

    reviewer = make_user("a-modeller", PlatformRole.MODELLER)
    ProjectMembership.objects.create(
        project=batch.project, user=reviewer, role=ProjectRole.CONTRIBUTOR
    )
    response = client_for(reviewer).get(
        f"{API}/portfolio-imports/{batch.id}/manifest/?include_confidential=true"
    )
    assert response.status_code == 403


def test_accepting_through_the_api_is_refused_while_a_finding_blocks(api, batch):
    response = api.post(f"{API}/portfolio-imports/{batch.id}/accept/")
    assert response.status_code == 409
    assert "blocking join finding" in response.data["detail"]


def test_the_batch_is_not_visible_outside_its_project(client_for, outsider, batch):
    response = client_for(outsider).get(f"{API}/portfolio-imports/{batch.id}/")
    assert response.status_code == 404


def test_an_artifact_is_reused_rather_than_duplicated(project, analyst, workbook):
    import_extract(project, workbook, filename="extract.xlsx", actor=analyst)
    import_extract(project, workbook, filename="extract.xlsx", actor=analyst)
    assert Artifact.objects.filter(role="portfolio_extract_source").count() == 1
