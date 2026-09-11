"""Manifests: the contracts that make a calculation reproducible.

Build plan section 17 requires schemas for the static model manifest, accepted
keys, generated portfolio files and the run manifest. Section 12 requires that
every result be traceable to immutable exposure, model, engine, converter and
settings versions, and that a calculation be reconstructible from retained
inputs even after an intermediate GMF has expired.

A manifest is therefore the durable record of *what went in*. It holds versions,
settings hashes and artifact references with checksums, never host paths and
never the arrays themselves.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any

from .artifacts import ArtifactRef
from .checksums import canonical_json, hash_bytes, settings_hash

MANIFEST_SCHEMA_VERSION = "1.0.0"


class ManifestError(Exception):
    """Raised when a manifest is incomplete or internally inconsistent."""


@dataclasses.dataclass(frozen=True, slots=True)
class SoftwareVersion:
    """One pinned piece of software in the calculation chain.

    ``image_digest`` is the immutable content digest, not a moving tag. Section
    18 requires pinned digests rather than latest tags.
    """

    component: str
    version: str
    image_digest: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "version": self.version,
            "image_digest": self.image_digest,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class ArtifactEntry:
    """An artifact named by its role in the calculation."""

    role: str
    uri: str
    checksum: str
    size_bytes: int
    content_type: str

    @classmethod
    def from_ref(cls, role: str, ref: ArtifactRef) -> ArtifactEntry:
        return cls(
            role=role,
            uri=ref.uri,
            checksum=ref.checksum,
            size_bytes=ref.size_bytes,
            content_type=ref.content_type,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "uri": self.uri,
            "checksum": self.checksum,
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class Manifest:
    """The common manifest body.

    ``kind`` distinguishes a hazard, conversion, model-package or analysis
    manifest. ``settings`` is hashed rather than trusted by reference so that
    an unchanged calculation always produces the same manifest digest.
    """

    kind: str
    subject_id: str
    created_at: dt.datetime
    software: tuple[SoftwareVersion, ...]
    inputs: tuple[ArtifactEntry, ...]
    outputs: tuple[ArtifactEntry, ...]
    settings: Mapping[str, Any]
    lineage: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    schema_version: str = MANIFEST_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "subject_id": self.subject_id,
            "created_at": self.created_at.isoformat(),
            "software": [item.as_dict() for item in self.software],
            "inputs": [item.as_dict() for item in self.inputs],
            "outputs": [item.as_dict() for item in self.outputs],
            "settings": dict(self.settings),
            "settings_hash": settings_hash(dict(self.settings)),
            "lineage": dict(self.lineage),
        }

    def digest(self) -> str:
        """Digest the manifest itself, excluding its own creation timestamp.

        Two runs of the same calculation with the same inputs and settings must
        produce the same digest, so the timestamp cannot take part.
        """
        body = self.as_dict()
        body.pop("created_at", None)
        return hash_bytes(canonical_json(body))

    def to_json(self) -> bytes:
        return canonical_json(self.as_dict())


def _require(entries: Sequence[ArtifactEntry], roles: Sequence[str], where: str) -> None:
    present = {entry.role for entry in entries}
    missing = [role for role in roles if role not in present]
    if missing:
        raise ManifestError(f"{where} is missing required roles: {', '.join(missing)}")


def build_manifest(
    *,
    kind: str,
    subject_id: str,
    software: Sequence[SoftwareVersion],
    inputs: Sequence[ArtifactEntry],
    outputs: Sequence[ArtifactEntry] = (),
    settings: Mapping[str, Any] | None = None,
    lineage: Mapping[str, Any] | None = None,
    created_at: dt.datetime | None = None,
) -> Manifest:
    """Assemble a manifest, checking that the required roles are present.

    The required roles differ by kind and encode the boundary rules of the
    build plan: a conversion cannot claim to have happened without a GMF and an
    area-peril grid, and an analysis cannot claim to have happened without a
    published OED location file, accepted keys and a model package.
    """
    if not software:
        raise ManifestError("a manifest must pin at least one software version")

    inputs = tuple(inputs)
    outputs = tuple(outputs)

    required_inputs = REQUIRED_INPUT_ROLES.get(kind)
    if required_inputs is None:
        raise ManifestError(f"unknown manifest kind {kind!r}")
    _require(inputs, required_inputs, f"{kind} manifest input set")

    duplicate_roles = _duplicates(entry.role for entry in outputs)
    if duplicate_roles:
        raise ManifestError(
            f"{kind} manifest declares duplicate output roles: {', '.join(duplicate_roles)}"
        )

    return Manifest(
        kind=kind,
        subject_id=subject_id,
        created_at=created_at or dt.datetime.now(dt.UTC),
        software=tuple(software),
        inputs=inputs,
        outputs=outputs,
        settings=dict(settings or {}),
        lineage=dict(lineage or {}),
    )


def _duplicates(values) -> list[str]:
    seen: set[str] = set()
    repeated: list[str] = []
    for value in values:
        if value in seen and value not in repeated:
            repeated.append(value)
        seen.add(value)
    return repeated


#: Input roles each manifest kind must declare.
REQUIRED_INPUT_ROLES: Mapping[str, tuple[str, ...]] = {
    "hazard": ("source_model", "site_model", "job_ini"),
    "conversion": ("gmf_hdf5", "area_peril_grid", "intensity_bin_dict"),
    "model_package": ("footprint", "vulnerability", "events", "occurrence", "damage_bin_dict"),
    "analysis": ("oed_location", "keys", "model_package", "analysis_settings"),
}

#: Output roles a successful run of each kind is expected to produce.
EXPECTED_OUTPUT_ROLES: Mapping[str, tuple[str, ...]] = {
    "hazard": ("gmf_hdf5", "hazard_report"),
    "conversion": ("footprint", "events", "occurrence", "conversion_report"),
    "model_package": ("model_settings", "package_checksums"),
    "analysis": ("gul_results", "run_report"),
}


def check_expected_outputs(manifest: Manifest) -> list[str]:
    """Return the expected output roles a manifest does not declare.

    Used at the end of a run to decide whether the pipeline genuinely produced
    what its kind promises, rather than trusting the exit code of a worker.
    """
    expected = EXPECTED_OUTPUT_ROLES.get(manifest.kind, ())
    present = {entry.role for entry in manifest.outputs}
    return [role for role in expected if role not in present]


@dataclasses.dataclass(frozen=True, slots=True)
class CompatibilityMatrix:
    """The tested combinations of engine, converter and schema versions.

    Section 4 requires every boundary to be crossed through a versioned adapter
    with a tested compatibility matrix, and section 18 requires promotion only
    after contract and regression suites pass. The matrix is data, so a new
    tested combination is a configuration change rather than a code change.
    """

    entries: tuple[Mapping[str, str], ...]

    def supports(self, candidate: Mapping[str, str]) -> bool:
        """Report whether a combination has been tested.

        A candidate matches an entry when every key the entry constrains is
        present in the candidate with the same value.
        """
        for entry in self.entries:
            if all(candidate.get(key) == value for key, value in entry.items()):
                return True
        return False

    def require(self, candidate: Mapping[str, str]) -> None:
        if not self.supports(candidate):
            raise ManifestError(
                "untested engine combination: "
                + ", ".join(f"{key}={value}" for key, value in sorted(candidate.items()))
            )
