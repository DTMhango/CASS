"""Deterministic identifier assignment.

Section 7 requires the converter to generate deterministic identifiers and
byte-stable outputs when inputs, versions and settings are unchanged, and
section 12 requires a result to be traceable back to the OpenQuake output that
produced it.

Those two pull in opposite directions if identifiers are assigned by insertion
order: a chunked conversion that processes events in a different order would
produce different Oasis event IDs for the same hazard. So identifiers here are
a pure function of the source key and a sort order, never of arrival order.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from typing import Any

from kre_core.checksums import canonical_json, hash_bytes


class IdentifierError(Exception):
    """Raised when an identifier assignment would be ambiguous or unstable."""


@dataclasses.dataclass(frozen=True, slots=True)
class EventLineage:
    """What an Oasis event ID was derived from.

    Section 6 requires event and realization lineage to be recorded so any
    material loss event can be traced back to OpenQuake output. This is that
    record, and it is what makes an integer event ID answerable.
    """

    oasis_event_id: int
    source_rupture_id: str
    realization: int | None = None
    occurrence_index: int | None = None
    sample: int | None = None

    def source_key(self) -> tuple:
        return (self.source_rupture_id, self.realization, self.occurrence_index, self.sample)

    def as_dict(self) -> dict[str, Any]:
        return {
            "oasis_event_id": self.oasis_event_id,
            "source_rupture_id": self.source_rupture_id,
            "realization": self.realization,
            "occurrence_index": self.occurrence_index,
            "sample": self.sample,
        }


class DeterministicIdMap:
    """Assigns stable integer identifiers to sorted source keys.

    Oasis identifiers are dense positive integers, which is a storage decision
    rather than a scientific one. Sorting the source keys before numbering
    means the mapping depends only on the set of keys, so two runs over the
    same hazard agree even if they processed it in different chunk orders.
    """

    def __init__(self, keys: Iterable[Any], *, start: int = 1) -> None:
        unique = sorted({self._normalise(key) for key in keys})
        if not unique:
            raise IdentifierError("cannot build an identifier map from no keys")
        self._forward: dict[Any, int] = {
            key: index for index, key in enumerate(unique, start=start)
        }
        self._reverse: dict[int, Any] = {
            value: key for key, value in self._forward.items()
        }

    @staticmethod
    def _normalise(key: Any) -> Any:
        # Tuples sort naturally; anything else is compared as text so that a
        # mixed-type key set still has a total order.
        if isinstance(key, tuple):
            return tuple(str(part) for part in key)
        return str(key)

    def __len__(self) -> int:
        return len(self._forward)

    def __contains__(self, key: Any) -> bool:
        return self._normalise(key) in self._forward

    def to_id(self, key: Any) -> int:
        try:
            return self._forward[self._normalise(key)]
        except KeyError as exc:
            raise IdentifierError(f"{key!r} was not present when the map was built") from exc

    def to_key(self, identifier: int) -> Any:
        try:
            return self._reverse[identifier]
        except KeyError as exc:
            raise IdentifierError(f"no source key holds identifier {identifier}") from exc

    def items(self) -> list[tuple[Any, int]]:
        return sorted(self._forward.items(), key=lambda pair: pair[1])

    def digest(self) -> str:
        """A digest of the whole mapping.

        Two conversions producing the same digest assigned the same identifiers
        to the same source keys, which is the cheapest possible check that a
        rebuilt model package is still comparable to its predecessor.
        """
        return hash_bytes(
            canonical_json([[list(key) if isinstance(key, tuple) else key, value]
                            for key, value in self.items()])
        )


def assign_event_ids(
    lineages: Iterable[Mapping[str, Any]],
    *,
    start: int = 1,
) -> tuple[list[EventLineage], DeterministicIdMap]:
    """Number a set of source events deterministically.

    Each mapping must carry ``source_rupture_id`` and may carry ``realization``,
    ``occurrence_index`` and ``sample``. Which of those are present is an
    outcome of the approved event identity policy, not a choice made here.
    """
    rows = list(lineages)
    if not rows:
        raise IdentifierError("no source events were supplied")

    keys = []
    for row in rows:
        rupture = row.get("source_rupture_id")
        if rupture in (None, ""):
            raise IdentifierError("every source event must name its rupture")
        keys.append(
            (
                str(rupture),
                _part(row.get("realization")),
                _part(row.get("occurrence_index")),
                _part(row.get("sample")),
            )
        )

    if len(set(keys)) != len(keys):
        duplicates = {key for key in keys if keys.count(key) > 1}
        raise IdentifierError(
            f"source events are not unique: {sorted(duplicates)[:3]}"
        )

    id_map = DeterministicIdMap(keys, start=start)

    lineage = [
        EventLineage(
            oasis_event_id=id_map.to_id(key),
            source_rupture_id=key[0],
            realization=_unpart(key[1]),
            occurrence_index=_unpart(key[2]),
            sample=_unpart(key[3]),
        )
        for key in keys
    ]
    lineage.sort(key=lambda item: item.oasis_event_id)
    return lineage, id_map


def _part(value: Any) -> str:
    """Encode an optional key part so sorting is total and stable."""
    if value is None:
        return ""
    # Zero-padded so 10 sorts after 9 rather than after 1.
    if isinstance(value, int):
        return f"{value:012d}"
    return str(value)


def _unpart(value: str) -> int | None:
    if value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def area_peril_ids(cell_keys: Iterable[Any], *, start: int = 1) -> DeterministicIdMap:
    """Number grid cells.

    Section 6 forbids silently changing what an existing area-peril identifier
    means, so this is only ever called when publishing a new grid version. The
    map digest is stored with the grid so a rebuild can be checked against it.
    """
    return DeterministicIdMap(cell_keys, start=start)
