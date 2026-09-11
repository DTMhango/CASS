"""Artifact store behaviour, with emphasis on the boundary rules."""

from __future__ import annotations

import io

import pytest

from cass_core.artifacts import (
    AccessPolicy,
    ArtifactNotFound,
    FilesystemArtifactStore,
    InvalidArtifactURI,
    RetentionClass,
    build_uri,
    parse_uri,
)
from cass_core.checksums import hash_bytes


@pytest.fixture()
def store(tmp_path):
    return FilesystemArtifactStore(tmp_path / "artifacts")


def test_put_bytes_records_checksum_size_and_retention(store):
    payload = b"LocNumber,AccNumber\n1,A\n"
    ref = store.put_bytes(
        "cass-portfolio",
        "project/1/exposure/v1/location.csv",
        payload,
        content_type="text/csv",
        retention=RetentionClass.PORTFOLIO,
    )

    assert ref.checksum == hash_bytes(payload)
    assert ref.size_bytes == len(payload)
    assert ref.retention is RetentionClass.PORTFOLIO
    assert ref.access is AccessPolicy.PROJECT
    assert ref.uri == "cass://cass-portfolio/project/1/exposure/v1/location.csv"


def test_round_trip_through_uri(store):
    ref = store.put_bytes(
        "cass-model", "eq/idn/v1/footprint.parquet", b"binary",
        content_type="application/vnd.apache.parquet",
        retention=RetentionClass.MODEL_ASSET,
    )
    with store.open(ref.uri) as handle:
        assert handle.read() == b"binary"
    assert store.exists(ref.uri)

    store.delete(ref.uri)
    assert not store.exists(ref.uri)
    with pytest.raises(ArtifactNotFound):
        store.open(ref.uri)


def test_download_writes_to_destination(store, tmp_path):
    ref = store.put_bytes(
        "cass-model", "eq/npl/v1/events.bin", b"\x01\x02",
        content_type="application/octet-stream",
        retention=RetentionClass.MODEL_ASSET,
    )
    target = store.download(ref.uri, tmp_path / "nested" / "events.bin")
    assert target.read_bytes() == b"\x01\x02"


@pytest.mark.parametrize(
    "key",
    [
        "../escape.csv",
        "project/../../etc/passwd",
        "C:/Users/Daniel/exposure.csv",
        "project\\1\\location.csv",
        "",
    ],
)
def test_host_paths_and_traversal_are_rejected(store, key):
    """Section 5: host paths must never appear in a calculation contract."""
    with pytest.raises(InvalidArtifactURI):
        store.put_bytes(
            "cass-portfolio", key, b"x",
            content_type="text/csv",
            retention=RetentionClass.PORTFOLIO,
        )


def test_parse_uri_rejects_foreign_schemes():
    with pytest.raises(InvalidArtifactURI):
        parse_uri("s3://cass-portfolio/location.csv")
    with pytest.raises(InvalidArtifactURI):
        parse_uri("file:///C:/exposure.csv")


def test_build_uri_round_trips():
    uri = build_uri("cass-model", "eq/idn/v1/footprint.parquet")
    assert parse_uri(uri) == ("cass-model", "eq/idn/v1/footprint.parquet")


def test_list_keys_is_prefix_filtered_and_sorted(store):
    for key in ["a/2.csv", "a/1.csv", "b/1.csv"]:
        store.put_bytes(
            "cass-portfolio", key, b"x",
            content_type="text/csv",
            retention=RetentionClass.DIAGNOSTIC,
        )
    assert list(store.list_keys("cass-portfolio", "a/")) == ["a/1.csv", "a/2.csv"]


def test_upload_session_then_register(store):
    session = store.create_upload_session(
        "cass-portfolio", "project/1/upload.csv", content_type="text/csv"
    )
    assert session.method == "PUT"
    assert session.uri == "cass://cass-portfolio/project/1/upload.csv"

    ref = store.register_upload(
        session.uri,
        io.BytesIO(b"LocNumber\n1\n"),
        content_type="text/csv",
        retention=RetentionClass.PORTFOLIO,
    )
    assert ref.checksum == hash_bytes(b"LocNumber\n1\n")
    assert store.exists(ref.uri)


def test_ref_serialises_without_host_paths(store):
    ref = store.put_bytes(
        "cass-result", "run/9/aal.parquet", b"x",
        content_type="application/vnd.apache.parquet",
        retention=RetentionClass.RESULT,
    )
    body = ref.as_dict()
    assert body["uri"].startswith("cass://")
    assert str(store.root) not in repr(body)
