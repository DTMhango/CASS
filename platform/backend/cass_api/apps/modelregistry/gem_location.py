"""Which GEM release this installation builds vulnerability sets from.

The release is not bundled with CASS. The person running it keeps GEM's two
repositories on their own device; the compose file mounts that folder into the
containers, and the location is chosen here, on the platform, rather than
edited into an environment file and restarted. A choice is checked before it is
kept, and the installation-wide setting still applies until anybody chooses.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from cass_converter import gem_release

from .models import GemReleaseLocation

#: What an installation with no release says, wherever a build is attempted.
NOT_CONFIGURED = (
    "No GEM release is chosen on this installation, so there is nothing to build a "
    "vulnerability set from. Choose the folder holding the GEM clones on the Build tab."
)


class GemLocationError(Exception):
    """A folder that cannot be built from, with each reason."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(" ".join(problems))
        self.problems = list(problems)


def chosen() -> GemReleaseLocation | None:
    return GemReleaseLocation.objects.order_by("-created_at").first()


def current_root() -> str:
    """The folder builds read from: the latest choice, else the installation setting."""
    record = chosen()
    if record is not None:
        return record.path
    return str(settings.CASS_GEM_ROOT or "")


def choose(path: Any, *, actor=None) -> GemReleaseLocation:
    """Keep a folder as the release, refusing one the readers cannot build from."""
    text = str(path or "").strip()
    if not text:
        raise GemLocationError(["Name the folder that holds the GEM release."])
    inspection = gem_release.inspect(text)
    if not inspection.usable:
        raise GemLocationError(list(inspection.problems))
    return GemReleaseLocation.objects.create(
        path=inspection.path,
        release=inspection.release,
        matches_validated=inspection.matches_validated,
        inspection=inspection.as_dict(),
        created_by=actor,
        updated_by=actor,
    )


def status() -> dict[str, Any]:
    """The release in use, where it came from, and every release the installation can see."""
    record = chosen()
    root = current_root()
    if record is not None:
        source = "chosen"
    elif root:
        source = "installation setting"
    else:
        source = "none"
    return {
        "source": source,
        "path": root,
        "chosen_at": record.created_at.isoformat() if record is not None else None,
        "current": gem_release.inspect(root).as_dict() if root else None,
        "mount": str(settings.CASS_MODELS_ROOT),
        "discovered": [
            item.as_dict() for item in gem_release.discover(settings.CASS_MODELS_ROOT)
        ],
        "validated_release": gem_release.VALIDATED_RELEASE,
    }
