"""Administration: changing what people may do, and the support bundle.

Two sets of refusals, each guarding something a screen cannot undo by itself.

Role changes refuse the lock-out. An administrator may not remove their own
administration or deactivate themselves, and no change may leave the
installation with no active administrator -- the same failure reached two ways,
and after either one there is nobody left to reverse it.

The support bundle refuses to carry what section 11 says it must not: secrets
and portfolio contents. Settings go in by allowlist, so a secret added next
quarter is left out by default rather than leaked by default, and failures go in
by where they happened rather than what they said, because a failure summary
written for an analyst names the portfolio.
"""

from __future__ import annotations

import json

import pytest

from apps.accounts.models import PlatformRole, User
from apps.common import support

from .conftest import API

pytestmark = pytest.mark.django_db


# -- role changes -------------------------------------------------------------

def test_an_administrator_changes_what_somebody_may_do(client_for, admin, analyst):
    changed = client_for(admin).patch(
        f"{API}/users/{analyst.id}/",
        {"platform_role": PlatformRole.REVIEWER, "job_title": "Cat modelling lead"},
        format="json",
    )

    assert changed.status_code == 200, changed.data
    assert changed.data["platform_role"] == PlatformRole.REVIEWER
    assert changed.data["capabilities"]["approve_gates"] is True


def test_the_change_is_written_to_the_audit_trail_with_what_it_was(client_for, admin, analyst):
    from apps.audit.models import AuditEvent

    client_for(admin).patch(
        f"{API}/users/{analyst.id}/", {"platform_role": PlatformRole.MODELLER}, format="json"
    )

    event = AuditEvent.objects.get(subject_type="user", subject_id=str(analyst.id))
    assert event.before_reference == {"platform_role": PlatformRole.ANALYST}
    assert event.after_reference == {"platform_role": PlatformRole.MODELLER}


def test_only_an_administrator_may_change_a_role(api, reviewer):
    refused = api.patch(
        f"{API}/users/{reviewer.id}/", {"platform_role": PlatformRole.ADMIN}, format="json"
    )

    assert refused.status_code == 403


def test_nobody_may_raise_their_own_role(api, analyst):
    refused = api.patch(
        f"{API}/users/{analyst.id}/", {"platform_role": PlatformRole.ADMIN}, format="json"
    )

    assert refused.status_code == 403
    analyst.refresh_from_db()
    assert analyst.platform_role == PlatformRole.ANALYST


def test_an_administrator_may_not_remove_their_own_administration(client_for, admin):
    refused = client_for(admin).patch(
        f"{API}/users/{admin.id}/", {"platform_role": PlatformRole.ANALYST}, format="json"
    )

    assert refused.status_code == 409
    assert "Ask another administrator" in refused.data["detail"]


def test_an_administrator_may_not_deactivate_themselves(client_for, admin):
    refused = client_for(admin).patch(
        f"{API}/users/{admin.id}/", {"is_active": False}, format="json"
    )

    assert refused.status_code == 409


def test_the_last_active_administrator_cannot_be_removed_by_another(client_for, admin):
    """Reaching the lock-out through somebody else is the same lock-out."""
    other = User.objects.create_user(
        username="second-admin", password="correct-horse-battery", platform_role=PlatformRole.ADMIN
    )
    # Demoting the other administrator is fine while one remains.
    allowed = client_for(admin).patch(
        f"{API}/users/{other.id}/", {"platform_role": PlatformRole.ANALYST}, format="json"
    )
    assert allowed.status_code == 200

    # Now only ``admin`` is left, and ``other`` is no longer an administrator,
    # so ``other`` cannot remove it -- and ``admin`` cannot remove itself.
    refused = client_for(admin).patch(
        f"{API}/users/{admin.id}/", {"is_active": False}, format="json"
    )
    assert refused.status_code == 409


def test_an_administrator_sees_inactive_people_so_they_can_be_reactivated(
    client_for, admin, analyst
):
    analyst.is_active = False
    analyst.save()

    listing = client_for(admin).get(f"{API}/users/")

    usernames = {row["username"] for row in listing.data["results"]}
    assert analyst.username in usernames


def test_identity_is_not_editable_here(client_for, admin, analyst):
    """Who somebody is belongs to the identity provider, not a platform screen."""
    client_for(admin).patch(
        f"{API}/users/{analyst.id}/",
        {"username": "renamed", "email": "someone@else.example"},
        format="json",
    )

    analyst.refresh_from_db()
    assert analyst.username == "analyst"


# -- the support bundle ------------------------------------------------------

def test_the_bundle_carries_versions_capacity_and_health(project, analyst):
    carried = support.bundle(probe_engines=False)

    assert carried["installation"]["oed_schema_version"]
    assert carried["installation"]["packages"]["django"] != "not installed"
    assert carried["capacity"]
    assert carried["engines"] == {"probe_skipped": True}


def test_no_secret_leaves_in_the_bundle(settings):
    """By allowlist: a secret added later is left out by default."""
    settings.SECRET_KEY = "a-secret-key-value"
    settings.CASS_METRICS_TOKEN = "a-scrape-token-value"
    settings.CASS_OASIS_PASSWORD = "an-engine-password"
    settings.CASS_SOMETHING_NEW_AND_SECRET = "added-next-quarter"

    rendered = json.dumps(support.bundle(probe_engines=False), default=str)

    for secret in (
        "a-secret-key-value",
        "a-scrape-token-value",
        "an-engine-password",
        "added-next-quarter",
    ):
        assert secret not in rendered
    # Whether a secret is set is the fact an operator needs, and that is kept.
    assert support.bundle(probe_engines=False)["configured"]["CASS_METRICS_TOKEN"] is True


def test_a_failure_travels_by_where_it_happened_not_what_it_said(project, analyst):
    """A failure summary names the portfolio; the bundle must not."""
    from django.utils import timezone

    from apps.runs.models import Run, RunKind

    Run.objects.create(
        kind=RunKind.ANALYSIS,
        project=project,
        state="failed",
        failure_stage="reconcile_keys",
        failure_summary="USD 180,000,000 of Jakarta Tower TIV could not be mapped",
        failure_detail="LOC-JKT-001 at -6.2, 106.8",
        correlation_id="abc123",
        finished_at=timezone.now(),
        created_by=analyst,
    )

    carried = support.bundle(probe_engines=False)
    rendered = json.dumps(carried, default=str)

    failure = carried["recent_failures"][0]
    assert failure["stage"] == "reconcile_keys"
    assert failure["correlation_id"] == "abc123"
    assert "Jakarta Tower" not in rendered
    assert "LOC-JKT-001" not in rendered


def test_a_broken_engine_still_yields_a_bundle(monkeypatch):
    """The engines are often the reason somebody needs the bundle."""

    def unreachable():
        raise RuntimeError("connection refused")

    monkeypatch.setattr("apps.common.engines.describe_engines", unreachable)

    carried = support.bundle(probe_engines=True)

    assert "connection refused" in carried["engines"]["probe_failed"]


# -- downloading the bundle ---------------------------------------------------

@pytest.fixture()
def quiet_engines(monkeypatch):
    """The endpoint probes the engines; a test has none to probe."""
    monkeypatch.setattr(
        "apps.common.engines.describe_engines", lambda: {"oasis": {"reachable": False}}
    )


def test_an_administrator_downloads_the_bundle_as_a_file(client_for, admin, quiet_engines):
    downloaded = client_for(admin).get(f"{API}/support-bundle/?download=1")

    assert downloaded.status_code == 200
    assert downloaded["Content-Disposition"].startswith(
        'attachment; filename="cass-support-bundle-'
    )
    assert downloaded.data["engines"] == {"oasis": {"reachable": False}}


def test_the_bundle_is_not_for_anybody_signed_in(api, quiet_engines):
    """What leaves the installation is an administrator's decision."""
    refused = api.get(f"{API}/support-bundle/")

    assert refused.status_code == 403


def test_downloading_the_bundle_is_audited(client_for, admin, quiet_engines):
    from apps.audit.models import AuditEvent

    client_for(admin).get(f"{API}/support-bundle/?download=1")

    assert AuditEvent.objects.filter(
        subject_type="support_bundle", actor=admin, action="download"
    ).exists()


def test_looking_at_the_bundle_is_a_read_not_a_download(client_for, admin, quiet_engines):
    """A hundred visits to the administration screen are not a hundred downloads."""
    from apps.audit.models import AuditEvent

    viewed = client_for(admin).get(f"{API}/support-bundle/")

    assert viewed.status_code == 200
    assert "Content-Disposition" not in viewed
    assert AuditEvent.objects.filter(subject_type="support_bundle", action="read").exists()
    assert not AuditEvent.objects.filter(subject_type="support_bundle", action="download").exists()
