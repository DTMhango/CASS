"""What CASS records about its entitlement to model data (ADR 7, ADR 15).

GEM Foundation's written permission covers everything GEM makes publicly
available: its Global Exposure and Vulnerability models, and the national hazard
models in its mosaic, such as PuSGeN 2024. Anything else is held under the
installation's internal-use basis. What is held to account here is that each is
applied to the data it belongs to, whichever way that data arrives.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as django_apps

from apps.modelregistry import gem, hazard, hazard_models, pilot
from apps.modelregistry.models import (
    GEM_PERMISSION,
    INTERNAL_USE_LICENCE,
    HazardModel,
    HazardSet,
    VulnerabilitySet,
)

from .test_hazard_models import archive

pytestmark = pytest.mark.django_db

earlier = importlib.import_module("apps.modelregistry.migrations.0009_gem_permission")
public_models = importlib.import_module(
    "apps.modelregistry.migrations.0010_gem_permission_public_models"
)

#: How the PuSGeN 2024 mosaic package credits its publisher.
MOSAIC_PUBLISHER = "PuSGeN, with the GEM Foundation (2026 mosaic)"


def test_the_permission_covers_what_gem_makes_public_and_asks_for_credit():
    assert "publicly available" in GEM_PERMISSION
    assert "Credit the authors and GEM Foundation" in GEM_PERMISSION
    # The licence is kept beside the permission, because attribution and
    # share-alike still apply to anything redistributed.
    assert gem.GEM_LICENCE == "CC BY-NC-SA 4.0"


def test_a_gem_vulnerability_set_is_cleared_under_gems_permission_by_default():
    statement = gem.LicenceStatement()

    assert statement.cleared is True
    assert statement.as_note() == GEM_PERMISSION


def test_a_hazard_model_gem_publishes_is_held_under_its_permission(modeller):
    model = hazard_models.register_model(
        archive(),
        country_code="ID",
        version="mosaic-test",
        label="PuSGeN 2024 Indonesia",
        source_organisation=MOSAIC_PUBLISHER,
        licence="CC BY-NC-SA 4.0",
        actor=modeller,
    )

    assert model.licence_cleared is True
    assert model.licence_note == GEM_PERMISSION


def test_a_hazard_model_from_anyone_else_keeps_the_internal_use_basis(modeller):
    model = hazard_models.register_model(
        archive(),
        country_code="ID",
        version="agency-test",
        label="A national model",
        source_organisation="A national agency",
        actor=modeller,
    )

    assert model.licence_note == INTERNAL_USE_LICENCE


def test_gem_is_recognised_as_a_publisher_and_not_as_part_of_a_word():
    assert hazard_models.licence_basis("GEM Foundation") == GEM_PERMISSION
    assert hazard_models.licence_basis("Gemological Survey") == INTERNAL_USE_LICENCE
    assert hazard_models.licence_basis("") == INTERNAL_USE_LICENCE


def test_a_hazard_set_carries_its_models_basis_as_written():
    """Not wrapped in a sentence the permission does not fit."""
    source = hazard.SourceStatement(model="PuSGeN 2024 Indonesia", reference=GEM_PERMISSION)

    assert source.as_note() == GEM_PERMISSION


def test_the_migrations_record_the_permission_on_everything_gem_publishes(modeller):
    grid = pilot.register_grid("ID", actor=modeller)
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
    mosaic = hazard_models.register_model(
        archive(),
        country_code="ID",
        version="2024.0.0",
        label="PuSGeN 2024 Indonesia",
        source_organisation=MOSAIC_PUBLISHER,
        actor=modeller,
    )
    agency = hazard_models.register_model(
        archive(),
        country_code="ID",
        version="agency",
        label="A national model",
        source_organisation="A national agency",
        actor=modeller,
    )
    # As the records stood before the permission: all under the internal-use basis.
    HazardModel.objects.update(licence_note=INTERNAL_USE_LICENCE)
    from_mosaic = HazardSet.objects.create(
        country_code="ID",
        version="2024.0.0-run",
        label="Mosaic run",
        source_model=f"{mosaic.label} ({mosaic.reference})",
        grid=grid,
        created_by=modeller,
    )
    from_agency = HazardSet.objects.create(
        country_code="ID",
        version="agency-run",
        label="Agency run",
        source_model=f"{agency.label} ({agency.reference})",
        grid=grid,
        created_by=modeller,
    )

    earlier.record_permission(django_apps, None)
    public_models.record_permission(django_apps, None)

    for record in (from_gem, elsewhere, mosaic, agency, from_mosaic, from_agency):
        record.refresh_from_db()
    assert from_gem.licence_cleared is True
    assert from_gem.licence_note == GEM_PERMISSION
    assert mosaic.licence_note == GEM_PERMISSION
    assert from_mosaic.licence_note == GEM_PERMISSION
    assert elsewhere.licence_note == INTERNAL_USE_LICENCE
    assert agency.licence_note == INTERNAL_USE_LICENCE
    assert from_agency.licence_note == INTERNAL_USE_LICENCE


def test_the_migrations_carry_the_same_words_and_rule_as_the_registry():
    """A migration holds its own copy, and the copies must not drift."""
    assert public_models.GEM_PERMISSION == GEM_PERMISSION
    assert public_models.EARLIER_WORDING == earlier.GEM_PERMISSION
    assert public_models.INTERNAL_USE_LICENCE == INTERNAL_USE_LICENCE
    assert public_models.GEM_PUBLISHER.pattern == hazard_models.GEM_PUBLISHER.pattern
