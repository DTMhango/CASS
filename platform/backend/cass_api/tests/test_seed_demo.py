"""The demonstration seed.

The seed is how a reviewer first sees the product, so it is tested like a
feature. The assertions that matter are the governance ones: a model version
whose hazard and IMT coverage are outstanding must be seeded as a research
prototype, not quietly as an approved model.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.accounts.models import User
from apps.artifacts.models import ArtifactLink
from apps.exposure.models import ExposureState, ExposureVersion
from apps.modelregistry.models import AssumptionSet, ModelVersion
from apps.projects.models import Project

pytestmark = pytest.mark.django_db


@pytest.fixture()
def seeded(capsys):
    call_command("seed_demo", verbosity=0)
    capsys.readouterr()


def test_seed_creates_the_three_working_roles(seeded):
    assert set(User.objects.values_list("username", flat=True)) == {
        "analyst",
        "modeller",
        "reviewer",
    }


def test_seed_creates_a_project_the_analyst_owns(seeded):
    project = Project.objects.get(reference="idn-fac-2026")
    analyst = User.objects.get(username="analyst")
    assert project.may_administer(analyst)


def test_seeded_model_is_a_research_prototype_not_an_approved_model(seeded):
    """Section 9: research output must be operationally distinct."""
    model = ModelVersion.objects.get(version="0.1.0-sa")
    assert model.is_research_prototype is True
    assert model.usable_for_decisions is False


def test_seeded_model_names_what_blocks_full_publication(seeded):
    model = ModelVersion.objects.get(version="0.1.0-sa")
    blockers = " ".join(model.publication_blockers())

    assert "hazard set" in blockers
    # The GEM licence is no longer one of them: GEM Foundation has given its
    # written permission (ADR 15).
    assert "licence" not in blockers


def test_the_seeded_gem_set_records_gems_permission_beside_its_licence(seeded):
    from apps.modelregistry.gem import GEM_LICENCE, GEM_PERMISSION

    vulnerability = ModelVersion.objects.get(version="0.1.0-sa").vulnerability_set

    assert vulnerability.licence_cleared is True
    assert vulnerability.licence_note == GEM_PERMISSION
    assert vulnerability.licence == GEM_LICENCE


def test_seeded_model_declares_only_the_sa_family(seeded):
    """Section 16: SA-first, with PGA deferred from the prototype.

    The deferral is in what the seed declares, not in what the converter can
    do. The converter writes a footprint for every measure the hazard set
    carries, so a version reaches PGA functions as soon as a hazard set
    carrying PGA is attached -- and until one is, the missing measure is
    reported against that hazard set rather than against the converter.
    """
    model = ModelVersion.objects.get(version="0.1.0-sa")
    assert set(model.imts) == {"SA(0.3)", "SA(0.6)", "SA(1.0)"}
    assert "PGA" in model.vulnerability_set.imts_used
    assert model.vulnerability_set.unsupported_imts == []


def test_seeded_model_carries_a_peril_scope_statement(seeded):
    """Section 9: output may not be labelled earthquake loss silently."""
    model = ModelVersion.objects.get(version="0.1.0-sa")
    scope = model.peril_scope

    assert scope["QEQ"]["treatment"] == "included"
    for excluded in ("QTS", "QLF", "QLS", "QFF"):
        assert scope[excluded]["treatment"] == "excluded"
        assert scope[excluded]["expected_bias"]


def test_seed_creates_the_three_assumption_sets(seeded):
    """Section 8 requires Baseline, More Robust and More Vulnerable."""
    assert set(AssumptionSet.objects.values_list("flavour", flat=True)) == {
        "baseline",
        "more_robust",
        "more_vulnerable",
    }


def test_seeded_portfolio_is_validated_and_publishable(seeded):
    exposure = ExposureVersion.objects.get(name="Indonesia pilot portfolio")
    assert exposure.state == ExposureState.VALIDATED
    assert exposure.location_count == 4
    assert exposure.is_publishable is True


def test_seeded_portfolio_discloses_unmodelled_subperils(seeded):
    """One seeded location is covered for the whole earthquake group."""
    exposure = ExposureVersion.objects.get(name="Indonesia pilot portfolio")
    assert set(exposure.unmodelled_subperils) == {"QFF", "QTS", "QSL", "QLS"}


def test_piwind_baseline_is_seeded_from_the_real_fixtures(seeded):
    """Section 12 makes PiWind the end-to-end regression baseline."""
    exposure = ExposureVersion.objects.get(name="PiWind baseline")
    assert exposure.location_count == 10
    assert exposure.account_count == 2

    roles = set(
        ArtifactLink.objects.filter(
            subject_type="exposure_version", subject_id=exposure.id
        ).values_list("role", flat=True)
    )
    assert roles == {
        "oed_location",
        "oed_account",
        "oed_reins_info",
        "oed_reins_scope",
    }


def test_piwind_baseline_supports_every_perspective(seeded):
    exposure = ExposureVersion.objects.get(name="PiWind baseline")
    available = {
        item["perspective"]
        for item in exposure.supported_perspectives
        if item["available"]
    }
    assert available == {"ground_up", "insured", "reinsurance"}


def test_reseeding_restores_a_missing_regression_baseline(seeded, capsys):
    """A re-run must attempt every portfolio, not stop at the first one present.

    PiWind seeding used to be nested inside the demonstration-portfolio branch,
    so a second run that found the demonstration portfolio returned early and
    never created the regression baseline.
    """
    ExposureVersion.objects.filter(name="PiWind baseline").delete()
    assert not ExposureVersion.objects.filter(name="PiWind baseline").exists()

    call_command("seed_demo", verbosity=0)
    capsys.readouterr()

    assert ExposureVersion.objects.filter(name="PiWind baseline").exists()


def test_seed_is_idempotent(seeded, capsys):
    call_command("seed_demo", verbosity=0)
    capsys.readouterr()

    assert Project.objects.filter(reference="idn-fac-2026").count() == 1
    assert ModelVersion.objects.filter(version="0.1.0-sa").count() == 1
    assert ExposureVersion.objects.filter(name="Indonesia pilot portfolio").count() == 1
    assert ExposureVersion.objects.filter(name="PiWind baseline").count() == 1
    assert User.objects.count() == 3
