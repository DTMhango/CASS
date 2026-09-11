"""Manifest construction, determinism and compatibility rules."""

from __future__ import annotations

import datetime as dt

import pytest

from cass_core.checksums import settings_hash
from cass_core.manifests import (
    ArtifactEntry,
    CompatibilityMatrix,
    ManifestError,
    SoftwareVersion,
    build_manifest,
    check_expected_outputs,
)


def entry(role: str) -> ArtifactEntry:
    return ArtifactEntry(
        role=role,
        uri=f"cass://cass-model/eq/idn/v1/{role}",
        checksum="sha256:" + "0" * 64,
        size_bytes=1024,
        content_type="application/octet-stream",
    )


def software() -> list[SoftwareVersion]:
    return [
        SoftwareVersion("openquake", "3.23.1", "sha256:aaa"),
        SoftwareVersion("cass-converter", "0.1.0", "sha256:bbb"),
    ]


def conversion_inputs():
    return [entry("gmf_hdf5"), entry("area_peril_grid"), entry("intensity_bin_dict")]


def test_manifest_requires_its_declared_input_roles():
    with pytest.raises(ManifestError) as excinfo:
        build_manifest(
            kind="conversion",
            subject_id="conv-1",
            software=software(),
            inputs=[entry("gmf_hdf5")],
        )
    assert "area_peril_grid" in str(excinfo.value)


def test_manifest_rejects_an_unknown_kind():
    with pytest.raises(ManifestError):
        build_manifest(kind="tsunami", subject_id="x", software=software(), inputs=[])


def test_manifest_requires_pinned_software():
    with pytest.raises(ManifestError):
        build_manifest(
            kind="conversion",
            subject_id="conv-1",
            software=[],
            inputs=conversion_inputs(),
        )


def test_manifest_rejects_duplicate_output_roles():
    with pytest.raises(ManifestError):
        build_manifest(
            kind="conversion",
            subject_id="conv-1",
            software=software(),
            inputs=conversion_inputs(),
            outputs=[entry("footprint"), entry("footprint")],
        )


def test_digest_ignores_creation_time_but_not_settings():
    """Section 7: unchanged inputs, versions and settings must be byte-stable."""
    common = {
        "kind": "conversion",
        "subject_id": "conv-1",
        "software": software(),
        "inputs": conversion_inputs(),
        "settings": {"imt": "SA(0.3)", "bins": 32},
    }
    first = build_manifest(**common, created_at=dt.datetime(2026, 9, 11, tzinfo=dt.UTC))
    second = build_manifest(**common, created_at=dt.datetime(2026, 12, 25, tzinfo=dt.UTC))
    assert first.digest() == second.digest()

    changed = build_manifest(
        **{**common, "settings": {"imt": "SA(0.6)", "bins": 32}},
        created_at=dt.datetime(2026, 9, 11, tzinfo=dt.UTC),
    )
    assert changed.digest() != first.digest()


def test_settings_hash_is_insensitive_to_key_order():
    assert settings_hash({"a": 1, "b": 2}) == settings_hash({"b": 2, "a": 1})


def test_manifest_body_carries_a_settings_hash():
    manifest = build_manifest(
        kind="conversion",
        subject_id="conv-1",
        software=software(),
        inputs=conversion_inputs(),
        settings={"imt": "SA(0.3)"},
    )
    body = manifest.as_dict()
    assert body["settings_hash"] == settings_hash({"imt": "SA(0.3)"})
    assert body["schema_version"] == "1.0.0"


def test_expected_outputs_are_reported_rather_than_assumed():
    manifest = build_manifest(
        kind="conversion",
        subject_id="conv-1",
        software=software(),
        inputs=conversion_inputs(),
        outputs=[entry("footprint"), entry("events")],
    )
    missing = check_expected_outputs(manifest)
    assert missing == ["occurrence", "conversion_report"]


def test_compatibility_matrix_blocks_untested_combinations():
    matrix = CompatibilityMatrix(
        entries=(
            {"oasis": "2.5.7", "oed": "4.0.0"},
            {"oasis": "2.5.8", "oed": "4.0.0"},
        )
    )
    assert matrix.supports({"oasis": "2.5.7", "oed": "4.0.0", "openquake": "3.23.1"})
    assert not matrix.supports({"oasis": "2.6.0", "oed": "4.0.0"})
    with pytest.raises(ManifestError):
        matrix.require({"oasis": "2.6.0", "oed": "4.0.0"})
