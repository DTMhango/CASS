"""Derive the settlement mask CASS ships, from the published GHSL rasters.

CASS can skip grid cells where nobody lives and nothing is built. What it reads
to decide is one bit per 30 arc-second pixel of the world -- set where the
Global Human Settlement Layer records any built-up surface or any resident --
stored in latitude bands so a grid reads only the bands it covers. This script
is how that file is made, so the file can always be made again from the
sources and checked against the checksum the keys service holds.

Run it once per GHSL release, outside the platform. It needs tifffile and
imagecodecs to decode the LZW-compressed GeoTIFFs, which CASS itself does not::

    python -m venv geo && geo/bin/pip install numpy tifffile imagecodecs
    geo/bin/python derive_settlement_mask.py \\
        --built GHS_BUILT_S_E2020_GLOBE_R2023A_4326_30ss_V1_0.tif \\
        --population GHS_POP_E2020_GLOBE_R2023A_4326_30ss_V1_0.tif \\
        --output ../cass_keys/data/settlement/ghsl_r2023a_e2020_30ss_settled.bin

The sources, from https://human-settlement.emergency.copernicus.eu/download.php:

* GHS_BUILT_S_E2020_GLOBE_R2023A_4326_30ss_V1_0.zip, SHA-256
  1bb109f506bd66605eee9f2d2dca21ed937e089f5adf5d1d388cbebe0614fba8
* GHS_POP_E2020_GLOBE_R2023A_4326_30ss_V1_0.zip, SHA-256
  579fb7477b33d9be61e9562b170ea108a670b85ef6fe23b61a22d17200929636

Why both layers. Buildings alone miss people GHSL places without detecting a
building under them; residents alone miss factories, ports and warehouses, which
are exactly what a commercial book insures. Measured over the ten seeded
countries at their seed resolutions, each layer has a few hundred cells the
other lacks, so the union costs almost nothing and misses neither.

The two rasters are not on the same pixel grid: the population raster starts
0.8 of a pixel further west. Each population pixel is added to every building
pixel it overlaps, which can only widen the mask, never narrow it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import zlib
from pathlib import Path

import numpy
import tifffile

MAGIC = b"CASSSET1"
BAND_ROWS = 240  # two degrees of latitude


def read_positive(path: Path):
    """A boolean raster of the pixels holding any positive value, and its georeference."""
    with tifffile.TiffFile(path) as tif:
        page = tif.pages[0]
        height, width = page.shape
        scale = page.tags["ModelPixelScaleTag"].value
        tie = page.tags["ModelTiepointTag"].value
        positive = numpy.zeros((height, width), dtype=bool)
        tile_height, tile_width = page.tilelength, page.tilewidth
        for data, index, shape in page.segments():
            top, left = index[-3], index[-2]
            tile = data.reshape(shape[1], shape[2])
            rows = slice(top, min(top + tile_height, height))
            columns = slice(left, min(left + tile_width, width))
            positive[rows, columns] = (tile > 0)[: rows.stop - rows.start, : columns.stop - columns.start]
    return positive, {
        "origin_longitude": float(tie[3]),
        "origin_latitude": float(tie[4]),
        "pixel_degrees": float(scale[0]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--built", type=Path, required=True)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    built, georeference = read_positive(arguments.built)
    residents, population_georeference = read_positive(arguments.population)
    pixel = georeference["pixel_degrees"]
    if abs(population_georeference["origin_latitude"] - georeference["origin_latitude"]) > pixel / 100:
        raise SystemExit("The rasters do not share a latitude origin; this script assumes they do.")

    # Where each population column falls on the building raster's columns.
    offset = (population_georeference["origin_longitude"] - georeference["origin_longitude"]) / pixel
    settled = built.copy()
    height, width = settled.shape
    first = math.floor(offset)
    last = math.ceil(offset + 1) - 1
    for shift in range(first, last + 1):
        target_start = max(0, shift)
        source_start = target_start - shift
        length = min(width - target_start, residents.shape[1] - source_start)
        settled[:, target_start : target_start + length] |= residents[
            : height, source_start : source_start + length
        ]

    bands, payloads, position = [], [], 0
    for start in range(0, height, BAND_ROWS):
        packed = numpy.packbits(settled[start : start + BAND_ROWS], axis=1)
        payload = zlib.compress(packed.tobytes(), 9)
        bands.append([position, len(payload)])
        payloads.append(payload)
        position += len(payload)

    header = json.dumps(
        {
            "width": width,
            "height": height,
            **georeference,
            "band_rows": BAND_ROWS,
            "bands": bands,
            "source": (
                "GHSL R2023A, epoch 2020, 30 arc-seconds: GHS-BUILT-S and GHS-POP. "
                "European Commission, Joint Research Centre."
            ),
            "rule": "Set where any built-up surface or any resident is recorded.",
            "settled_pixels": int(settled.sum()),
        },
        sort_keys=True,
    ).encode("utf-8")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with arguments.output.open("wb") as handle:
        handle.write(MAGIC)
        handle.write(struct.pack("<I", len(header)))
        handle.write(header)
        for payload in payloads:
            handle.write(payload)
    digest = hashlib.sha256(arguments.output.read_bytes()).hexdigest()
    print(f"{arguments.output}: {arguments.output.stat().st_size:,} bytes, SHA-256 {digest}")
    print(f"settled pixels {int(settled.sum()):,} (buildings {int(built.sum()):,})")


if __name__ == "__main__":
    main()
