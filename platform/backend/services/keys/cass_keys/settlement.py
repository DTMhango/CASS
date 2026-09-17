"""Which grid cells are near anywhere somebody lives or something is built.

Land is not the same as exposure. A grid clipped to Indonesia's coast still
carries the interior of Papua and Kalimantan, Oman's Empty Quarter and Nepal's
high Himalaya -- cells where no building stands and no one lives, and where no
insured risk can be. Every one is a site in every hazard calculation. A grid can
ask to skip them, and this module answers which they are.

What it reads is the Global Human Settlement Layer's 2020 estimate at 30
arc-seconds, about a kilometre: one bit per pixel of the world, set where GHSL
records any built-up surface or any resident. The union rather than either
layer, because measured over the ten seeded countries each has a few hundred
cells the other lacks -- factories and ports with no residents, people with no
building GHSL detected -- and missing a factory is the expensive mistake. The
bits ship with CASS, 8.9 MB, derived by ``tools/derive_settlement_mask.py`` from
the published rasters, and are read a latitude band at a time.

**A cell is kept when it lies within a buffer of anywhere settled**, and the
default buffer is 5 km. That was measured against the geocoded locations of
KRE's 30 June 2026 book in the two countries it covers: at 2 km two of 224
locations fell in skipped cells, both geocoded only to a postcode centroid; at
5 km none did. A location in a skipped cell is reported as outside the grid
rather than lost silently, but it cannot be modelled until the grid and its
hazard are built again, so the buffer errs wide.

What this cannot see is a building GHSL did not detect: remote infrastructure --
a mine, a dam, a pipeline station -- far from any settlement. Such risks are why
skipping is a choice a grid makes, not a default, and why the buffer is stated
on every grid that uses it.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import math
import struct
import zlib
from decimal import Decimal
from importlib import resources
from typing import Any

from .land import KM_PER_DEGREE, LatticeMask, _dilate, _numpy

#: The data source, as it is recorded on every grid that skips unsettled cells.
SOURCE = (
    "GHSL R2023A, epoch 2020, 30 arc-seconds: GHS-BUILT-S and GHS-POP "
    "(European Commission, Joint Research Centre)"
)

FILE = "ghsl_r2023a_e2020_30ss_settled.bin"
FILE_SHA256 = "32a06c18a8249d20e004af74218e2757b2bbbd891e241b1f8dfc387346b17203"
MAGIC = b"CASSSET1"

#: Measured against KRE's geocoded book: the narrowest buffer that kept every
#: location.
DEFAULT_SETTLEMENT_BUFFER_KM = Decimal("5")

#: Slack on pixel boundaries, so a cell edge that meets a pixel edge exactly
#: counts that pixel -- the direction that keeps a cell rather than drops it.
_EDGE = 1e-9


class SettlementError(Exception):
    """Raised when the settlement layer cannot be read."""


@dataclasses.dataclass(frozen=True, slots=True)
class _Layer:
    width: int
    height: int
    origin_longitude: float
    origin_latitude: float
    pixel_degrees: float
    band_rows: int
    bands: tuple[tuple[int, int], ...]
    data_offset: int
    payload: bytes


@functools.cache
def _layer() -> _Layer:
    payload = (resources.files("cass_keys") / "data" / "settlement" / FILE).read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != FILE_SHA256:
        raise SettlementError(
            f"The settlement layer has checksum {digest[:12]}, not the "
            f"{FILE_SHA256[:12]} this code was written against, so it is not the "
            "layer any grid records."
        )
    if payload[: len(MAGIC)] != MAGIC:
        raise SettlementError("The settlement layer is not in the format CASS writes.")
    (length,) = struct.unpack("<I", payload[len(MAGIC) : len(MAGIC) + 4])
    start = len(MAGIC) + 4
    header = json.loads(payload[start : start + length])
    return _Layer(
        width=int(header["width"]),
        height=int(header["height"]),
        origin_longitude=float(header["origin_longitude"]),
        origin_latitude=float(header["origin_latitude"]),
        pixel_degrees=float(header["pixel_degrees"]),
        band_rows=int(header["band_rows"]),
        bands=tuple((int(offset), int(size)) for offset, size in header["bands"]),
        data_offset=start + length,
        payload=payload,
    )


@functools.lru_cache(maxsize=8)
def _band(index: int):
    """One latitude band, unpacked to one boolean per pixel."""
    numpy = _numpy()
    layer = _layer()
    offset, size = layer.bands[index]
    begin = layer.data_offset + offset
    packed = numpy.frombuffer(zlib.decompress(layer.payload[begin : begin + size]), dtype=numpy.uint8)
    rows = min(layer.band_rows, layer.height - index * layer.band_rows)
    return numpy.unpackbits(packed.reshape(rows, -1), axis=1)[:, : layer.width].astype(bool)


def _pixels(first_row: int, last_row: int, first_column: int, last_column: int):
    """A window of the layer, with anything beyond its edges read as unsettled."""
    numpy = _numpy()
    layer = _layer()
    window = numpy.zeros((last_row - first_row + 1, last_column - first_column + 1), dtype=bool)
    rows = range(max(first_row, 0), min(last_row, layer.height - 1) + 1)
    columns = slice(max(first_column, 0), min(last_column, layer.width - 1) + 1)
    if not rows or columns.start >= columns.stop:
        return window
    for band in range(rows.start // layer.band_rows, (rows.stop - 1) // layer.band_rows + 1):
        band_start = band * layer.band_rows
        top = max(rows.start, band_start)
        bottom = min(rows.stop, band_start + layer.band_rows)
        window[top - first_row : bottom - first_row, columns.start - first_column : columns.stop - first_column] = (
            _band(band)[top - band_start : bottom - band_start, columns]
        )
    return window


def describe() -> dict[str, Any]:
    layer = _layer()
    return {
        "source": SOURCE,
        "pixel_degrees": layer.pixel_degrees,
        "default_buffer_km": str(DEFAULT_SETTLEMENT_BUFFER_KM),
    }


@functools.lru_cache(maxsize=32)
def settlement_mask(
    resolution: Decimal,
    buffer_km: Decimal,
    first_row: int,
    last_row: int,
    first_column: int,
    last_column: int,
) -> LatticeMask:
    """The cells of a window within ``buffer_km`` of any settled pixel.

    The buffer grows the settled pixels, not the cells, so it is a distance from
    a building or a resident rather than from the edge of a grid cell: a buffer
    of 5 km means 5 km whatever resolution the grid is.
    """
    numpy = _numpy()
    layer = _layer()
    step = float(resolution)
    pixel = layer.pixel_degrees

    widest = max(abs(first_row * step), abs((last_row + 1) * step))
    km = float(buffer_km)
    reach_rows = math.ceil(km / (pixel * KM_PER_DEGREE)) if km > 0 else 0
    narrowing = max(math.cos(math.radians(min(widest, 89.0))), 1e-6)
    reach_columns = math.ceil(km / (pixel * KM_PER_DEGREE * narrowing)) if km > 0 else 0

    rows = numpy.arange(first_row, last_row + 1)
    columns = numpy.arange(first_column, last_column + 1)
    # The pixels each cell overlaps. Pixel rows count down from the layer's
    # northern edge; cell rows count up from the equator.
    top = numpy.floor((layer.origin_latitude - (rows + 1) * step) / pixel + _EDGE).astype(numpy.int64)
    bottom = numpy.ceil((layer.origin_latitude - rows * step) / pixel - _EDGE).astype(numpy.int64) - 1
    left = numpy.floor((columns * step - layer.origin_longitude) / pixel + _EDGE).astype(numpy.int64)
    right = numpy.ceil(((columns + 1) * step - layer.origin_longitude) / pixel - _EDGE).astype(numpy.int64) - 1

    pixel_top = int(top.min()) - reach_rows
    pixel_bottom = int(bottom.max()) + reach_rows
    pixel_left = int(left.min()) - reach_columns
    pixel_right = int(right.max()) + reach_columns

    window = _dilate(
        _pixels(pixel_top, pixel_bottom, pixel_left, pixel_right), reach_rows, reach_columns
    )
    totals = numpy.zeros((window.shape[0] + 1, window.shape[1] + 1), dtype=numpy.int32)
    totals[1:, 1:] = window.astype(numpy.int32).cumsum(axis=0).cumsum(axis=1)

    top = numpy.clip(top - pixel_top, 0, window.shape[0])
    bottom = numpy.clip(bottom - pixel_top, -1, window.shape[0] - 1)
    left = numpy.clip(left - pixel_left, 0, window.shape[1])
    right = numpy.clip(right - pixel_left, -1, window.shape[1] - 1)
    counted = (
        totals[bottom[:, None] + 1, right[None, :] + 1]
        - totals[top[:, None], right[None, :] + 1]
        - totals[bottom[:, None] + 1, left[None, :]]
        + totals[top[:, None], left[None, :]]
    )
    return LatticeMask(
        resolution=resolution,
        first_row=first_row,
        first_column=first_column,
        kept=counted > 0,
    )
