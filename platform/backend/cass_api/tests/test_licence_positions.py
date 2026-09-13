"""What CASS records about its entitlement to model data (ADR 7, ADR 15).

There are two positions. What matters is that neither is applied to the other's
data. GEM Foundation's written permission clears the sets built from its public
exposure and vulnerability models. It says nothing about a hazard source model
such as PuSGeN 2024, which stays under the installation's internal-use basis.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as django_apps

from apps.modelregistry import gem
from apps.modelregistry.models import INTERNAL_USE_LICENCE, VulnerabilitySet

pytestmark = pytest.mark.django_db

permission = importlib.import_module("apps.modelregistry.migrations.0009_gem_permission")


def test_a_gem_set_is_cleared_under_gems_permission_by_default():
    statement = gem.LicenceStatement()

    assert statement.cleared is True
    assert statement.as_note() == gem.GEM_PERMISSION


def test_the_permission_says_what_it_covers_and_what_it_asks_in_return():
    assert "Global Exposure and Vulnerability models" in gem.GEM_PERMISSION
    assert "Credit GEM Foundation" in gem.GEM_PERMISSION
    # The licence is kept beside the permission, because attribution and
    # share-alike still apply to anything redistributed.
    assert gem.GEM_LICENCE == "CC BY-NC-SA 4.0"


def test_the_migration_records_the_permission_on_gem_sets_and_nothing_else(modeller):
    from_gem = VulnerabilitySet.objects.create(
        country_code="ID",
        version="gem-test",
        source="GEM Global Vulnerability Model v2026.0.0",
        licence="CC BY-NC-SA 4.0",
        licence_cleared=False,
        licence_note="Awaiting GEM's written position.",
        created_by=modeller,
    )
    elsewhere = VulnerabilitySet.objects.create(
        country_code="ID",
        version="pilot-test",
        source="CASS pilot routing table",
        created_by=modeller,
    )

    permission.record_permission(django_apps, None)

    from_gem.refresh_from_db()
    elsewhere.refresh_from_db()
    assert from_gem.licence_cleared is True
    assert from_gem.licence_note == gem.GEM_PERMISSION
    assert elsewhere.licence_note == INTERNAL_USE_LICENCE


def test_the_migration_carries_the_same_words_as_the_registry():
    """A migration holds its own copy of the text, and the two must not drift."""
    assert permission.GEM_PERMISSION == gem.GEM_PERMISSION
    assert permission.INTERNAL_USE_LICENCE == INTERNAL_USE_LICENCE
