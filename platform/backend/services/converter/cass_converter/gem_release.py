"""Which GEM release a folder holds, read from the folder itself.

CASS does not carry GEM's models: the 2026 release is too large to live in the
codebase, and it is GEM's to publish. Whoever runs CASS keeps a clone of GEM's
exposure and vulnerability repositories on their own device and points CASS at
it. So a folder is checked before it is used -- for the repository layout the
readers expect, and for the commit each repository is at, read from its own git
metadata. That says which release it is, and whether it is the release CASS was
validated against, without anybody having to type either.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
from typing import Any

from .gem import GemError, catalogue

#: The two repositories a GEM release is made of, side by side as GEM publishes them.
REPOSITORIES = ("global_exposure_model", "global_vulnerability_model")

#: The release CASS's acceptance tests were run against, and the commit of each
#: repository in it -- the same commits models/gem/v2026.0.0/MODEL_MANIFEST.md pins.
VALIDATED_RELEASE = "v2026.0.0"
VALIDATED_COMMITS = {
    "global_exposure_model": "c3add51f4e56f9d10477c8f6b5e24fd89fe089a1",
    "global_vulnerability_model": "5974372ac3f4a99f25d0649eb030fbe596f23b36",
}

#: GEM's exposure-to-vulnerability mapping, which every build reads.
MAPPING_FILE = "global_exposure_model/World/summaries/Vulnerability_mapping_country.csv"

_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclasses.dataclass(frozen=True, slots=True)
class Repository:
    """One of the two repositories, and the commit and tags it is at."""

    name: str
    present: bool
    commit: str = ""
    tags: tuple[str, ...] = ()

    @property
    def matches_validated(self) -> bool:
        return bool(self.commit) and self.commit == VALIDATED_COMMITS.get(self.name)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "present": self.present,
            "commit": self.commit,
            "tags": list(self.tags),
            "matches_validated": self.matches_validated,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class ReleaseInspection:
    """What a folder holds, what stops it being used, and what is worth knowing."""

    path: str
    repositories: tuple[Repository, ...]
    countries: int
    problems: tuple[str, ...]
    notes: tuple[str, ...]

    @property
    def usable(self) -> bool:
        return not self.problems

    @property
    def release(self) -> str:
        """The tag both repositories share, or the folder's name where there is none."""
        tagged = [set(item.tags) for item in self.repositories if item.present]
        shared = set.intersection(*tagged) if len(tagged) == len(REPOSITORIES) else set()
        if shared:
            return sorted(shared)[-1]
        return pathlib.PurePath(self.path).name if self.usable else ""

    @property
    def matches_validated(self) -> bool:
        return len(self.repositories) == len(REPOSITORIES) and all(
            item.matches_validated for item in self.repositories
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "usable": self.usable,
            "release": self.release,
            "matches_validated": self.matches_validated,
            "validated_release": VALIDATED_RELEASE,
            "countries": self.countries,
            "repositories": [item.as_dict() for item in self.repositories],
            "problems": list(self.problems),
            "notes": list(self.notes),
        }


def inspect(root: str | pathlib.Path) -> ReleaseInspection:
    """Check a folder is a GEM release the readers can build from, and say which release it is."""
    base = pathlib.Path(root)
    if not base.is_dir():
        return ReleaseInspection(
            path=str(base),
            repositories=(),
            countries=0,
            problems=(
                f"{base} is not a folder this installation can read. On a containerised "
                "installation it has to be inside the models folder mounted from your "
                "device.",
            ),
            notes=(),
        )

    problems: list[str] = []
    notes: list[str] = []
    repositories: list[Repository] = []
    for name in REPOSITORIES:
        folder = base / name
        if not folder.is_dir():
            problems.append(
                f"There is no {name} folder in {base}. A GEM release is the two "
                "repositories side by side, as GEM publishes them: global_exposure_model "
                "and global_vulnerability_model."
            )
            repositories.append(Repository(name, False))
            continue
        commit = _head_commit(folder)
        if not commit:
            notes.append(
                f"{name} carries no git metadata CASS can read, so its commit is unknown. "
                "Cloning it with git lets the release be confirmed."
            )
        repositories.append(Repository(name, True, commit, _tags_at(folder, commit) if commit else ()))

    countries = 0
    if not problems:
        if not (base / MAPPING_FILE).is_file():
            problems.append(
                f"{MAPPING_FILE} is missing, so a building's exposure value cannot be "
                "mapped to GEM's functions."
            )
        try:
            countries = len(catalogue(base))
        except GemError as exc:
            problems.append(str(exc))
        if not problems and countries == 0:
            problems.append("The vulnerability repository publishes no country's functions.")

    if not problems and not all(item.matches_validated for item in repositories):
        notes.append(
            f"The repositories are not at the {VALIDATED_RELEASE} commits CASS was "
            "validated against. They can be built from, but a set built from them is not "
            "covered by that validation."
        )

    return ReleaseInspection(
        path=str(base),
        repositories=tuple(repositories),
        countries=countries,
        problems=tuple(problems),
        notes=tuple(notes),
    )


def discover(mount: str | pathlib.Path, *, depth: int = 3) -> tuple[ReleaseInspection, ...]:
    """Every folder under the mount that holds both repositories, nearest first.

    Stops at a folder that holds a release rather than descending into it, and
    never walks into a repository, so a large clone costs a directory listing
    rather than a crawl.
    """
    base = pathlib.Path(mount)
    if not base.is_dir():
        return ()
    found: list[ReleaseInspection] = []
    frontier: list[tuple[pathlib.Path, int]] = [(base, 0)]
    while frontier:
        folder, level = frontier.pop(0)
        if all((folder / name).is_dir() for name in REPOSITORIES):
            found.append(inspect(folder))
            continue
        if level >= depth:
            continue
        try:
            children = sorted(
                child
                for child in folder.iterdir()
                if child.is_dir() and not child.name.startswith(".") and child.name not in REPOSITORIES
            )
        except OSError:
            continue
        frontier.extend((child, level + 1) for child in children)
    return tuple(found)


# -- reading git metadata without git ------------------------------------------------

def _git_dir(repository: pathlib.Path) -> pathlib.Path | None:
    marker = repository / ".git"
    if marker.is_dir():
        return marker
    if marker.is_file():
        # A worktree or submodule points at its git directory from a file.
        text = marker.read_text(errors="ignore").strip()
        if text.startswith("gitdir:"):
            target = pathlib.Path(text[len("gitdir:"):].strip())
            target = target if target.is_absolute() else repository / target
            return target if target.is_dir() else None
    return None


def _head_commit(repository: pathlib.Path) -> str:
    git = _git_dir(repository)
    if git is None:
        return ""
    try:
        head = (git / "HEAD").read_text(errors="ignore").strip()
    except OSError:
        return ""
    if _SHA.match(head):
        return head
    if head.startswith("ref:"):
        return _resolve(git, head[len("ref:"):].strip())
    return ""


def _resolve(git: pathlib.Path, ref: str) -> str:
    loose = git / ref
    if loose.is_file():
        value = loose.read_text(errors="ignore").strip()
        return value if _SHA.match(value) else ""
    for target, name in _packed(git):
        if name == ref:
            return target
    return ""


def _packed(git: pathlib.Path) -> list[tuple[str, str]]:
    """(commit, ref) pairs from packed-refs, an annotated tag peeled to its commit."""
    path = git / "packed-refs"
    if not path.is_file():
        return []
    entries: list[tuple[str, str]] = []
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("^"):
            peeled = line[1:].strip()
            if entries and _SHA.match(peeled):
                entries[-1] = (peeled, entries[-1][1])
            continue
        commit, _, ref = line.partition(" ")
        if _SHA.match(commit) and ref:
            entries.append((commit, ref.strip()))
    return entries


def _tags_at(repository: pathlib.Path, commit: str) -> tuple[str, ...]:
    git = _git_dir(repository)
    if git is None:
        return ()
    found = {
        ref[len("refs/tags/"):]
        for target, ref in _packed(git)
        if ref.startswith("refs/tags/") and target == commit
    }
    tags = git / "refs" / "tags"
    if tags.is_dir():
        for path in tags.rglob("*"):
            if path.is_file() and path.read_text(errors="ignore").strip() == commit:
                found.add(path.relative_to(tags).as_posix())
    return tuple(sorted(found))
