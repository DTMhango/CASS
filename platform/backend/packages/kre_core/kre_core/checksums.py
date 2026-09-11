"""Content checksums and deterministic hashing.

Build plan section 5 requires a content checksum on every artifact, and
section 7 requires byte-stable converter output when inputs, versions and
settings are unchanged. Both needs are served by the helpers here: a single
canonical digest algorithm, and a canonical JSON encoding so that a settings
hash does not change because a dictionary was built in a different order.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, BinaryIO

DIGEST_ALGORITHM = "sha256"
_CHUNK_BYTES = 1024 * 1024


def hash_bytes(payload: bytes) -> str:
    """Return the canonical digest of a byte string, prefixed with its algorithm."""
    return f"{DIGEST_ALGORITHM}:{hashlib.sha256(payload).hexdigest()}"


def hash_stream(stream: BinaryIO) -> str:
    """Digest a binary stream in chunks, never loading the whole object."""
    digest = hashlib.sha256()
    while True:
        chunk = stream.read(_CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
    return f"{DIGEST_ALGORITHM}:{digest.hexdigest()}"


def hash_file(path: str | Path) -> str:
    """Digest a file on disk in chunks."""
    with open(path, "rb") as handle:
        return hash_stream(handle)


def canonical_json(value: Any) -> bytes:
    """Encode a value so that equal settings always produce equal bytes.

    Keys are sorted, separators are fixed and non-ASCII characters are escaped,
    which makes the encoding stable across platforms and Python versions.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def settings_hash(value: Any) -> str:
    """Digest a settings object through its canonical JSON encoding."""
    return hash_bytes(canonical_json(value))


def combine(digests: Iterable[str]) -> str:
    """Fold several digests into one order-independent digest.

    Used where a manifest depends on a set of inputs whose enumeration order is
    an implementation detail, such as the files inside a model package.
    """
    ordered = sorted(digests)
    return hash_bytes("\n".join(ordered).encode("ascii"))


def verify(path: str | Path, expected: str) -> None:
    """Raise if a file no longer matches the digest recorded for it."""
    actual = hash_file(path)
    if actual != expected:
        raise ChecksumMismatch(f"expected {expected}, found {actual} for {path}")


class ChecksumMismatch(Exception):
    """Raised when stored content does not match its recorded checksum."""
