"""Pointing CASS at the GEM release on the user's own device.

The release is not bundled, so these hold the check that stands in for it: a
folder is a release only if it has GEM's two repositories in their published
layout, the release it holds is read from the repositories' own git metadata,
and a choice made on the platform is what the builds then read -- with no
environment setting and no restart.
"""

from __future__ import annotations

import pytest

from apps.modelregistry.models import GemReleaseLocation
from cass_converter.gem_release import VALIDATED_COMMITS

from .conftest import API
from .test_vulnerability_build import COUNTRY, release  # noqa: F401

pytestmark = pytest.mark.django_db

OTHER_COMMITS = {name: "0" * 40 for name in VALIDATED_COMMITS}


def with_git(root, commits, tag="v2026.0.0"):
    """Give each repository the git metadata a detached clone at a tag carries."""
    for name, commit in commits.items():
        git = root / name / ".git"
        git.mkdir(parents=True, exist_ok=True)
        (git / "HEAD").write_text(f"{commit}\n", encoding="utf-8")
        (git / "packed-refs").write_text(
            f"# pack-refs with: peeled fully-peeled sorted\n{commit} refs/tags/{tag}\n",
            encoding="utf-8",
        )
    return root


def choose(client, path):
    return client.post(f"{API}/vulnerability-sets/gem-release/choose/", {"path": str(path)}, format="json")


def test_a_release_chosen_on_the_platform_is_built_from_without_any_setting(
    client_for, modeller, release, settings  # noqa: F811
):
    settings.CASS_GEM_ROOT = ""
    with_git(release, VALIDATED_COMMITS)

    chosen = choose(client_for(modeller), release)
    countries = client_for(modeller).get(f"{API}/vulnerability-sets/gem-countries/")

    assert chosen.status_code == 200, chosen.data
    assert chosen.data["source"] == "chosen"
    assert chosen.data["current"]["release"] == "v2026.0.0"
    assert chosen.data["current"]["matches_validated"] is True
    assert countries.status_code == 200
    assert {item["country"] for item in countries.data["countries"]} == {COUNTRY}


def test_a_folder_that_is_not_a_gem_release_is_refused_with_what_is_missing(
    client_for, modeller, tmp_path
):
    (tmp_path / "global_exposure_model").mkdir()

    refused = choose(client_for(modeller), tmp_path)

    assert refused.status_code == 400
    assert any("global_vulnerability_model" in item for item in refused.data["problems"])
    assert not GemReleaseLocation.objects.exists()


def test_a_release_at_other_commits_is_usable_and_says_it_is_not_the_validated_one(
    client_for, modeller, release, settings  # noqa: F811
):
    settings.CASS_GEM_ROOT = ""
    with_git(release, OTHER_COMMITS, tag="v2027.0.0")

    chosen = choose(client_for(modeller), release)

    assert chosen.status_code == 200
    assert chosen.data["current"]["release"] == "v2027.0.0"
    assert chosen.data["current"]["matches_validated"] is False
    assert any("not covered by that validation" in item for item in chosen.data["current"]["notes"])


def test_releases_under_the_mounted_folder_are_found_for_the_user(
    client_for, modeller, release, settings, tmp_path  # noqa: F811
):
    settings.CASS_MODELS_ROOT = str(tmp_path)
    with_git(release, VALIDATED_COMMITS)

    status = client_for(modeller).get(f"{API}/vulnerability-sets/gem-release/")

    assert status.status_code == 200
    assert [item["path"] for item in status.data["discovered"]] == [str(release)]
    assert status.data["discovered"][0]["countries"] == 1


def test_until_somebody_chooses_the_installation_setting_still_applies(
    client_for, modeller, release  # noqa: F811
):
    status = client_for(modeller).get(f"{API}/vulnerability-sets/gem-release/")

    assert status.data["source"] == "installation setting"
    assert status.data["path"] == str(release)


def test_each_choice_is_kept_and_the_latest_is_used(client_for, modeller, release, settings):  # noqa: F811
    settings.CASS_GEM_ROOT = ""
    with_git(release, VALIDATED_COMMITS)

    choose(client_for(modeller), release)
    choose(client_for(modeller), release)

    assert GemReleaseLocation.objects.count() == 2
    assert client_for(modeller).get(f"{API}/vulnerability-sets/gem-release/").data["path"] == str(release)


def test_somebody_who_may_not_publish_models_cannot_choose_the_release(
    client_for, analyst, release  # noqa: F811
):
    refused = choose(client_for(analyst), release)

    assert refused.status_code == 403
