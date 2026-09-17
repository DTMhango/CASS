"""Who may create a project, and what creating one grants.

A project is the folder every other screen scopes by, so an installation with
none is an installation nobody can work in. Creating one is therefore open to
any signed-in user whatever their platform role -- a reviewer who needs a
workspace of their own is not made to ask an analyst for one -- and the person
who creates it becomes its owner, because a project nobody can reach is the
same as no project at all.

The user guide states both rules in the words a reader acts on, and a rule
stated in prose and enforced nowhere drifts. These tests are where the two are
held together: a permission check added to project creation later fails here,
beside the guide it would contradict.
"""

from __future__ import annotations

import pytest

from apps.accounts.models import PlatformRole
from apps.projects.models import Project

from .conftest import API, make_user

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "role",
    [
        PlatformRole.ANALYST,
        PlatformRole.MODELLER,
        PlatformRole.REVIEWER,
        PlatformRole.ADMIN,
    ],
)
def test_any_role_may_create_a_project(client_for, role):
    user = make_user(f"maker-{role}", role)
    response = client_for(user).post(
        f"{API}/projects/",
        {
            "name": "Nepal treaty 2026",
            "reference": f"npl-{role}",
            "purpose": "Checking that the role does not gate the workspace.",
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    assert response.data["my_role"] == "owner"


def test_the_creator_can_reach_what_they_made(client_for, reviewer):
    client = client_for(reviewer)
    created = client.post(
        f"{API}/projects/",
        {"name": "Reviewer's own", "reference": "rev-own", "purpose": ""},
        format="json",
    )
    assert created.status_code == 201, created.data

    # Membership is granted on creation, so the project is visible immediately
    # rather than after somebody else adds its author to it.
    listed = client.get(f"{API}/projects/")
    assert [item["reference"] for item in listed.data["results"]] == ["rev-own"]


def test_a_project_someone_is_not_in_is_not_listed(client_for, outsider, project):
    listed = client_for(outsider).get(f"{API}/projects/")

    assert listed.data["results"] == []
    assert Project.objects.filter(id=project.id).exists()
