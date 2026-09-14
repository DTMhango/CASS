"""Which GEM release a folder holds, read from the folder alone.

GEM's release is not bundled with CASS, so this check is what stands between a
user's folder and a build: the repositories must be laid out as GEM publishes
them, and the commit each is at is read from git's own files -- a detached
clone, a branch, packed refs, an annotated tag, a worktree -- without running
git, which an installation cannot be assumed to have.
"""

from __future__ import annotations

from cass_converter import gem_release
from cass_converter.gem import LossCategory
from cass_converter.gem_release import VALIDATED_COMMITS, discover, inspect

EXPOSURE, VULNERABILITY = gem_release.REPOSITORIES
TAG = "v2026.0.0"


def make_release(root, *, mapping=True, functions=True):
    """The smallest folder the readers accept: one country, and the world mapping."""
    country = root / VULNERABILITY / "Southeast_Asia" / "Atlantis"
    country.mkdir(parents=True)
    if functions:
        for category in LossCategory:
            (country / f"vulnerability_{category}.xml").write_text("<nrml/>", encoding="utf-8")
    summaries = root / EXPOSURE / "World" / "summaries"
    summaries.mkdir(parents=True)
    if mapping:
        (summaries / "Vulnerability_mapping_country.csv").write_text("REGION,ID_0\n", encoding="utf-8")
    return root


def git_dir(repository):
    folder = repository / ".git"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def detached_at_tag(root):
    for name, commit in VALIDATED_COMMITS.items():
        git = git_dir(root / name)
        (git / "HEAD").write_text(f"{commit}\n", encoding="utf-8")
        (git / "packed-refs").write_text(f"{commit} refs/tags/{TAG}\n", encoding="utf-8")


def test_a_detached_clone_at_a_tag_is_named_by_the_tag(tmp_path):
    root = make_release(tmp_path / "gem")
    detached_at_tag(root)

    inspection = inspect(root)

    assert inspection.usable
    assert inspection.release == TAG
    assert inspection.matches_validated
    assert inspection.countries == 1
    assert inspection.notes == ()


def test_a_branch_checkout_is_resolved_through_its_loose_ref(tmp_path):
    root = make_release(tmp_path / "gem")
    commit = VALIDATED_COMMITS[VULNERABILITY]
    git = git_dir(root / VULNERABILITY)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "refs" / "heads" / "main").write_text(f"{commit}\n", encoding="utf-8")

    assert gem_release._head_commit(root / VULNERABILITY) == commit


def test_a_ref_held_only_in_packed_refs_is_resolved(tmp_path):
    repository = tmp_path / "repo"
    git = git_dir(repository)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "packed-refs").write_text(f"{'a' * 40} refs/heads/main\n", encoding="utf-8")

    assert gem_release._head_commit(repository) == "a" * 40


def test_an_annotated_tag_is_peeled_to_the_commit_it_names(tmp_path):
    repository = tmp_path / "repo"
    git = git_dir(repository)
    commit = "b" * 40
    (git / "HEAD").write_text(f"{commit}\n", encoding="utf-8")
    (git / "packed-refs").write_text(
        f"# pack-refs with: peeled fully-peeled sorted\n{'c' * 40} refs/tags/{TAG}\n^{commit}\n",
        encoding="utf-8",
    )

    assert gem_release._tags_at(repository, commit) == (TAG,)


def test_a_worktree_is_followed_to_its_git_directory(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    elsewhere = tmp_path / "gitdir"
    elsewhere.mkdir()
    (elsewhere / "HEAD").write_text(f"{'d' * 40}\n", encoding="utf-8")
    (repository / ".git").write_text("gitdir: ../gitdir\n", encoding="utf-8")

    assert gem_release._head_commit(repository) == "d" * 40


def test_a_copy_without_git_metadata_is_usable_but_says_its_commit_is_unknown(tmp_path):
    root = make_release(tmp_path / "v2026.0.0")

    inspection = inspect(root)

    assert inspection.usable
    assert inspection.release == "v2026.0.0"
    assert inspection.matches_validated is False
    assert any("commit is unknown" in note for note in inspection.notes)


def test_a_release_without_the_world_mapping_cannot_be_built_from(tmp_path):
    inspection = inspect(make_release(tmp_path / "gem", mapping=False))

    assert not inspection.usable
    assert any("Vulnerability_mapping_country.csv" in problem for problem in inspection.problems)


def test_a_release_that_publishes_no_functions_cannot_be_built_from(tmp_path):
    inspection = inspect(make_release(tmp_path / "gem", functions=False))

    assert not inspection.usable
    assert any("no country's functions" in problem for problem in inspection.problems)


def test_a_path_that_is_not_a_folder_says_where_it_has_to_be(tmp_path):
    inspection = inspect(tmp_path / "nowhere")

    assert not inspection.usable
    assert "mounted from your device" in inspection.problems[0]


def test_releases_are_found_down_to_three_folders_deep_and_no_further(tmp_path):
    near = make_release(tmp_path / "gem" / "v2026.0.0")
    deeper = make_release(tmp_path / "archive" / "old" / "v2025.1.0")
    make_release(tmp_path / "a" / "b" / "c" / "d" / "too-deep")

    found = [item.path for item in discover(tmp_path)]

    assert found == [str(near), str(deeper)]
