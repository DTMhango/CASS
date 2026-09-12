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

import io
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
    import_portfolio,
    transformation_manifest,
)
from apps.exposure.models import (
    ImportBatch,
    ImportState,
    ReviewState,
    SourcePolicyRow,
    SourceRiskLocation,
)
from cass_extract import intake
from cass_extract import profile as intake_profile

from .conftest import API

# The synthetic extract lives with the package whose rules it exercises.
_FIXTURES = (
    Path(__file__).resolve().parents[2] / "packages" / "cass_extract" / "tests"
)
if str(_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_FIXTURES))

from fixtures import as_template, structural_template  # noqa: E402

pytestmark = pytest.mark.django_db


@pytest.fixture()
def workbook() -> bytes:
    return as_template()


@pytest.fixture()
def batch(project, analyst, workbook) -> ImportBatch:
    return import_portfolio(
        project, workbook, filename="extract.xlsx", actor=analyst
    )


# -- the immutable raw artifact ------------------------------------------------

def test_the_source_is_registered_before_it_is_parsed(project, analyst):
    """A workbook that cannot be read is still evidence of what was supplied."""
    result = import_portfolio(
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
    assert batch.risk_row_count == 11
    assert SourcePolicyRow.objects.filter(batch=batch).count() == 10
    assert SourceRiskLocation.objects.filter(batch=batch).count() == 11


def test_a_policy_total_is_staged_as_a_decimal(batch):
    """A float would lose the reconciliation everything downstream rests on."""
    row = SourcePolicyRow.objects.get(batch=batch, policy_id="P-1")
    assert row.policy_tiv == Decimal("1000000.00")
    assert row.values["policy_tiv"] == "1000000.00"


def test_a_risk_that_states_its_value_is_staged_with_it(batch):
    row = SourceRiskLocation.objects.get(batch=batch, business_id="B-SINGLE")
    assert row.total_insured_value == Decimal("1000000.00")


def test_a_risk_that_states_no_value_is_staged_as_stating_none(batch):
    """Null, not zero. The two mean different things and only one is a value."""
    rows = SourceRiskLocation.objects.filter(batch=batch, business_id="B-MULTI")
    assert rows.count() == 3
    assert all(row.total_insured_value is None for row in rows)


def test_a_risk_reference_is_kept_as_text(batch):
    """OED makes it text, and a schedule numbering its sites SITE-A is ordinary."""
    row = SourceRiskLocation.objects.get(batch=batch, business_id="B-SINGLE")
    assert row.location_number == "1"


def test_secondary_locations_are_staged_not_filtered_away(batch):
    """primary_location is an attribute, not a filter that deletes sites."""
    multi = SourceRiskLocation.objects.filter(batch=batch, business_id="B-MULTI")
    assert multi.count() == 3
    assert multi.filter(primary_location=False).count() == 2


def test_the_country_is_staged_as_the_iso_code_the_template_asks_for(batch):
    nepal = SourceRiskLocation.objects.get(batch=batch, business_id="B-NEPAL")
    assert nepal.country_code == "NP"


def test_a_country_with_no_grid_is_staged_and_left_unclassified(project, analyst):
    """Not refused: the row is real, and a person has to see it to fix it.

    What must not happen is a coordinate in a country CASS cannot screen being
    treated as eligible, so the cohort rules place it nowhere.
    """

    risks, policies = structural_template()
    risks[0]["Country"] = "FR"
    result = import_portfolio(
        project, as_template(risks, policies), filename="p.xlsx", actor=analyst
    )
    row = SourceRiskLocation.objects.get(batch=result, business_id="B-SINGLE")
    assert row.country_code == "FR"
    assert row.cohort == str(extract.Cohort.UNCLASSIFIED)
    assert "No country screen" in row.cohort_reason


# -- versions in the audit trail -----------------------------------------------

def test_the_batch_records_every_rule_version_that_decided_it(batch):
    assert batch.schema_version == intake.PROFILE_VERSION
    assert batch.parser_version == intake.PARSER_VERSION
    assert batch.cohort_rule_version == extract.COHORT_RULE_VERSION
    assert batch.profile == intake_profile.PROFILE_NAME


def test_the_checksum_and_parser_version_reach_the_audit_trail(batch):
    event = AuditEvent.objects.filter(
        subject_type="import_batch", subject_id=batch.id, action=AuditAction.CREATE
    ).first()
    assert event is not None
    assert event.after_reference["checksum"] == batch.source_checksum
    assert event.after_reference["parser_version"] == intake.PARSER_VERSION


def test_the_upload_is_audited_without_a_row_of_the_source(project, analyst, workbook):
    import_portfolio(project, workbook, filename="extract.xlsx", actor=analyst)
    event = AuditEvent.objects.filter(
        subject_type="project", action=AuditAction.UPLOAD
    ).first()
    assert set(event.after_reference) == {"role", "uri", "checksum"}


# -- idempotency ----------------------------------------------------------------

def test_reimporting_the_same_bytes_returns_the_same_batch(project, analyst, workbook):
    """Section 8: rerunning the same checksum is idempotent."""
    first = import_portfolio(project, workbook, filename="extract.xlsx", actor=analyst)
    second = import_portfolio(project, workbook, filename="extract.xlsx", actor=analyst)

    assert first.id == second.id
    assert ImportBatch.objects.filter(project=project).count() == 1
    assert SourceRiskLocation.objects.filter(batch=first).count() == 11


def test_a_different_extract_is_a_different_batch(project, analyst, workbook):
    risks, policies = structural_template()
    policies[0]["Total insured value"] = "1100000.00"
    other = as_template(risks, policies)

    import_portfolio(project, workbook, filename="a.xlsx", actor=analyst)
    import_portfolio(project, other, filename="b.xlsx", actor=analyst)
    assert ImportBatch.objects.filter(project=project).count() == 2


# -- the intake report ------------------------------------------------------------

def test_the_intake_report_is_stored_with_the_batch(batch):
    report = batch.intake_report
    assert report["risks"] == 11
    assert report["policies"] == 10
    assert report["accounts"] == 8
    assert report["readable"] is True
    assert report["has_policy_terms"] is True


def test_the_report_says_which_evidence_tier_each_risk_sits_in(batch):
    """The number that says which assumptions are in play at all."""
    evidence = batch.intake_report["coverage_evidence"]
    assert evidence["risks"] == 11
    assert evidence["risk_total_stated"] == 6
    assert evidence["allocated_from_policy"] == 5


def test_two_policies_on_one_account_are_ordinary_now(batch):
    """They were a blocking join finding when the join had to be inferred.

    The account reference is on both sheets because a person put it there, so
    two policies over one schedule are two policies -- layers, sections, a
    renewal -- rather than evidence that a reconstruction went wrong.
    """
    assert SourcePolicyRow.objects.filter(
        batch=batch, business_id="B-TWOPOL"
    ).count() == 2
    assert batch.blocking is False
    assert batch.may_accept is True


def test_an_account_with_policy_terms_and_no_risks_is_reported_not_blocking(batch):
    """Most of a facultative book is not geocoded. That is a state, not a defect."""
    codes = batch.intake_report["findings_by_code"]
    assert codes["policy_without_risks"] == 1
    assert batch.blocking is False


def test_a_file_that_cannot_be_read_blocks(project, analyst):
    from openpyxl import Workbook

    book = Workbook()
    book.active.title = "Sheet1"
    buffer = io.BytesIO()
    book.save(buffer)

    rejected = import_portfolio(
        project, buffer.getvalue(), filename="wrong.xlsx", actor=analyst
    )
    assert rejected.state == ImportState.REJECTED
    assert "not a CASS intake template" in rejected.rejection_reason


def test_a_readable_batch_can_be_accepted(batch, analyst):
    accept(batch, actor=analyst)
    batch.refresh_from_db()
    assert batch.state == ImportState.ACCEPTED
    assert batch.accepted_by_id == analyst.id
    assert batch.accepted_at is not None


def test_a_batch_cannot_be_accepted_twice(batch, analyst):
    accept(batch, actor=analyst)
    with pytest.raises(ExtractImportError, match="cannot be accepted"):
        accept(batch, actor=analyst)


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
        "schema": intake.PROFILE_VERSION,
        "parser": intake.PARSER_VERSION,
        "cohort_rules": extract.COHORT_RULE_VERSION,
    }


def test_the_manifest_is_a_summary_rather_than_a_copy_of_the_book(batch):
    """Not because rows are withheld -- they are on the batch -- but because a
    manifest that reproduced the portfolio would be the portfolio."""
    manifest = transformation_manifest(batch)
    assert manifest["counts"]["risk_rows"] == 11
    assert manifest["counts"]["policy_rows"] == 10
    assert "Example Street" not in str(manifest)


def test_the_manifest_states_the_value_basis(batch):
    """Nobody should have to remember that the share is already applied."""
    basis = transformation_manifest(batch)["value_basis"]
    assert "at the share CASS writes" in basis
    assert "not applied again" in basis


def test_the_manifest_totals_value_only_for_single_cohort_businesses(batch):
    """A business split across cohorts belongs to neither for a value total."""
    manifest = transformation_manifest(batch)
    cohort_tiv = manifest["cohort_tiv_at_kre_share_usd"]

    # B-PARTIAL has one cohort A site and one cohort C site.
    assert "mixed" in cohort_tiv
    assert Decimal(cohort_tiv["mixed"]) == Decimal("2000000.00")


def test_the_manifest_prefers_a_stated_value_over_a_policy_total(batch):
    """Adding both would count the same money twice."""
    cohort_tiv = transformation_manifest(batch)["cohort_tiv_at_kre_share_usd"]
    # Cohort A is a coordinate-eligibility cohort, not a class filter: the
    # Nepali engineering risk and the liability one qualify on their geocode
    # and are counted here even though no physical-damage run would take them.
    assert Decimal(cohort_tiv["A"]) == Decimal("6225000.00")


def test_the_manifest_carries_the_review_queue(batch):
    queue = transformation_manifest(batch)["review_queue"]
    assert queue["pending"] == 1
    assert queue["by_country"]["ID"] == 1


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
    assert response.data["risk_row_count"] == 11
    assert response.data["blocking"] is False


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
    assert "intake template" in response.data["detail"]


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


def test_every_project_member_sees_the_address(client_for, modeller, api, batch):
    """A modeller checks a coordinate against the address it came from.

    An earlier draft hid it from them, on the reading that a risk address was
    counterparty detail. It is not, and hiding it removed the only evidence
    the geocoding review has.
    """
    from apps.projects.models import ProjectMembership, ProjectRole

    shown = api.get(f"{API}/portfolio-imports/{batch.id}/locations/").data
    rows = shown["results"] if "results" in shown else shown
    assert rows[0]["address"]

    ProjectMembership.objects.create(
        project=batch.project, user=modeller, role=ProjectRole.CONTRIBUTOR
    )
    also = client_for(modeller).get(
        f"{API}/portfolio-imports/{batch.id}/locations/"
    ).data
    rows = also["results"] if "results" in also else also
    assert rows[0]["address"]


def test_a_manifest_download_is_audited(api, batch):
    api.get(f"{API}/portfolio-imports/{batch.id}/manifest/")
    assert AuditEvent.objects.filter(
        subject_type="import_batch", subject_id=batch.id, action=AuditAction.DOWNLOAD
    ).exists()


def test_accepting_through_the_api_is_refused_when_the_file_was_unreadable(
    api, project, analyst
):
    from openpyxl import Workbook

    book = Workbook()
    book.active.title = "Sheet1"
    buffer = io.BytesIO()
    book.save(buffer)
    rejected = import_portfolio(
        project, buffer.getvalue(), filename="wrong.xlsx", actor=analyst
    )

    response = api.post(f"{API}/portfolio-imports/{rejected.id}/accept/")
    assert response.status_code == 409


def test_the_batch_is_not_visible_outside_its_project(client_for, outsider, batch):
    response = client_for(outsider).get(f"{API}/portfolio-imports/{batch.id}/")
    assert response.status_code == 404


def test_an_artifact_is_reused_rather_than_duplicated(project, analyst, workbook):
    import_portfolio(project, workbook, filename="extract.xlsx", actor=analyst)
    import_portfolio(project, workbook, filename="extract.xlsx", actor=analyst)
    assert Artifact.objects.filter(role="portfolio_extract_source").count() == 1
