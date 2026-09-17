"""Grid specifications CASS ships for the countries it is used on first.

A grid is built from a specification a person writes and argues with, and that
does not change here. What a seed changes is where the writing starts: a whole
country, tiled into named regions, clipped to its land and to where anyone lives
or builds, at a resolution chosen by a stated rule -- rather than an empty form.
Loaded into the grid builder it is edited like anything typed, and what is built
from it belongs to whoever builds it.

Every seed was measured with the builder when it was written, and the counts
are stored beside it. The tests build every seed again and hold the counts to
it, so a change to the builder, the land outlines or the settlement layer that
moves a seed's cells cannot pass unnoticed.

The resolution rule, the same for every seed: the finest of 0.0125 degrees
(about 1.4 km) and 0.025 degrees at which the country's inhabited land fits in
300,000 cells, leaving room under the 500,000-cell limit for refinement; where
only 0.025 fits, the largest cities are refined to 0.0125. Finer than about a
kilometre adds cells without adding information, because global site-condition
data is no finer.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Mapping
from importlib import resources
from typing import Any

#: Keys of a seed file that describe the seed rather than specify a grid.
MEASURED = "measured"


class SeedError(Exception):
    """Raised when no seed exists for a country."""


@functools.cache
def _documents() -> Mapping[str, dict[str, Any]]:
    folder = resources.files("cass_keys") / "seed_grids"
    found = {}
    for entry in folder.iterdir():
        if entry.name.endswith(".json"):
            document = json.loads(entry.read_text(encoding="utf-8"))
            found[document["country_code"].upper()] = document
    return dict(sorted(found.items()))


def countries() -> tuple[str, ...]:
    return tuple(_documents())


def specification(country_code: str) -> dict[str, Any]:
    """The seed's specification document, as the grid builder takes it."""
    document = _document(country_code)
    return json.loads(json.dumps({key: value for key, value in document.items() if key != MEASURED}))


def measured(country_code: str) -> dict[str, Any]:
    """What the builder produced from the seed when it was written."""
    return dict(_document(country_code).get(MEASURED) or {})


def catalogue() -> list[dict[str, Any]]:
    """One line per seed: enough to choose one without reading it."""
    listed = []
    for code, document in _documents().items():
        counts = document.get(MEASURED) or {}
        listed.append(
            {
                "country_code": code,
                "label": document["label"],
                "version": document["version"],
                "base_resolution_deg": document["base_resolution_deg"],
                "tiles": len(document.get("tiles") or []),
                "refinements": len(document.get("refinements") or []),
                "domain": document.get("domain") or {},
                "cells": counts.get("cells"),
            }
        )
    return listed


def _document(country_code: str) -> dict[str, Any]:
    code = (country_code or "").upper()
    try:
        return _documents()[code]
    except KeyError:
        raise SeedError(
            f"CASS ships no seed grid for {country_code!r}. Seeds exist for "
            f"{', '.join(countries())}; any other country's grid starts from an "
            "empty specification."
        ) from None
