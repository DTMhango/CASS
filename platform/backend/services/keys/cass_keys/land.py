"""Which grid cells touch a country's land.

Tiles are rectangles, and a country is not. A rectangle drawn around an
archipelago holds far more sea than land -- the Indonesian prototype's eight
tiles cover 52,831 cells at 0.1 degrees, and only about 18,000 of them touch
land -- and every cell a grid carries is a site in every hazard calculation run
on it. Drawing enough rectangles to follow the coast instead was measured and
rejected: following Indonesia's coast at 0.05 degrees takes 2,075 of them, which
no reviewer could read.

So the tiles stay a short list of named regions, and a grid can ask to keep only
the cells that touch the country's land. That is this module: it rasterises the
country's outline onto the same lattice the cells are generated on and says,
for each cell, whether any part of it touches land.

Four decisions, each measured rather than assumed.

**The outline is Natural Earth's.** The 1:10m Admin 0 countries, version 5.1.1,
are public domain and 4.9 MB, and they ship with CASS so the clip needs nothing
downloaded. They show boundaries as they are held on the ground rather than as
they are claimed, so a disputed area belongs to whoever administers it; where an
area is administered by nobody recognised, Natural Earth gives it no country
code and no grid can clip to it.

**A cell is kept when any part of it touches land, not only its centre.** Testing
the centre alone lost 9% of Indonesia's cells at 0.05 degrees, every one of them
on a coast, and left the Maldives three cells. Coasts are where exposure is.

**The coast is widened before the test.** A map drawn for 1:10,000,000 places a
coastline to within about 5 km -- half a millimetre at that scale -- and a
geocoded address can sit just offshore of a coast drawn that way. A cell within
the buffer of land is kept, so neither loses a location that is really ashore.

**It is computed when asked.** Rasterising Indonesia's whole outline takes 0.15
seconds at 1.4 km cells, so nothing is precomputed or stored.

What it cannot see is land the map does not draw. Natural Earth draws 176 of the
Maldives' roughly 1,190 islands, 110 km2 of its 298; a clip there would drop real
islands, and the Maldivian seed does not clip for that reason.

The same outlines screen a portfolio. Each coordinate is checked against the
country its row names by the rule a grid keeps its cells by, so a location that
passes is one that country's grid can hold. Every officially assigned ISO 3166-1
code has an outline -- eleven of them inside another country's, such as Réunion
inside France -- so the screen never has to say it does not know a country. A
Maldivian coordinate is checked against the extent of the islands drawn, for the
reason the seed does not clip. That is ``within``, at the end of this module.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import io
import math
import struct
import zipfile
from collections.abc import Mapping
from decimal import Decimal
from importlib import resources
from typing import Any

#: The outline source, as it is recorded on every grid clipped to it.
SOURCE = "Natural Earth 1:10m Admin 0 Countries 5.1.1 (public domain)"

#: Where the archive ships inside this package, and the checksum it must have.
ARCHIVE = "ne_10m_admin_0_countries_5.1.1.zip"
ARCHIVE_SHA256 = "ce1ac7036499a0edd641fbc093cd209a98f96a49d2eca8480aaacad35138a7f6"

#: Kilometres per degree of latitude, and of longitude at the equator.
KM_PER_DEGREE = 111.32

#: The default coastal buffer: the positional accuracy of a 1:10m map.
DEFAULT_COAST_BUFFER_KM = Decimal("5")

#: How finely each coastline edge is walked, as a fraction of a cell. An edge
#: clipping a cell's corner by less than this may be missed; the buffer, which is
#: at least a cell, covers it.
_EDGE_SAMPLES_PER_CELL = 8

#: Coastline samples walked at once. Two million points is about a hundred
#: megabytes of working arrays.
_SAMPLE_BATCH = 2_000_000


class LandError(Exception):
    """Raised when a country's land cannot be read or clipped to."""


@dataclasses.dataclass(frozen=True, slots=True)
class Country:
    """One country's outline: every ring of every part Natural Earth draws for it."""

    code: str
    name: str
    parts: tuple[str, ...]
    rings: tuple[Any, ...]
    min_longitude: float
    max_longitude: float
    min_latitude: float
    max_latitude: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "parts": list(self.parts),
            "bounds": {
                "min_latitude": round(self.min_latitude, 4),
                "max_latitude": round(self.max_latitude, 4),
                "min_longitude": round(self.min_longitude, 4),
                "max_longitude": round(self.max_longitude, 4),
            },
        }


@dataclasses.dataclass(frozen=True, slots=True)
class LatticeMask:
    """Which cells of one resolution's lattice are kept, over a window of it.

    Cell ``(row, column)`` spans latitude ``row * resolution`` to
    ``(row + 1) * resolution`` and longitude likewise -- the lattice every grid
    generates its cells on, measured from (0, 0).
    """

    resolution: Decimal
    first_row: int
    first_column: int
    kept: Any  # a boolean numpy array, rows by columns

    def contains(self, rows, columns):
        """For each cell named by ``rows`` and ``columns``, whether it is kept."""
        numpy = _numpy()
        rows = numpy.asarray(rows, dtype=numpy.int64) - self.first_row
        columns = numpy.asarray(columns, dtype=numpy.int64) - self.first_column
        height, width = self.kept.shape
        inside = (rows >= 0) & (rows < height) & (columns >= 0) & (columns < width)
        result = numpy.zeros(rows.shape, dtype=bool)
        result[inside] = self.kept[rows[inside], columns[inside]]
        return result


def _numpy():
    try:
        import numpy
    except ImportError as exc:  # pragma: no cover - installed with the geodata extra
        raise LandError(
            "Clipping a grid to land needs numpy. Install the keys service with its "
            "geodata extra: pip install 'cass-keys[geodata]'."
        ) from exc
    return numpy


# -- reading the archive -------------------------------------------------------

@functools.cache
def _archive() -> dict[str, bytes]:
    payload = (resources.files("cass_keys") / "data" / "natural_earth" / ARCHIVE).read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != ARCHIVE_SHA256:
        raise LandError(
            f"The Natural Earth archive has checksum {digest[:12]}, not the "
            f"{ARCHIVE_SHA256[:12]} this code was written against, so the outlines "
            "it holds are not the ones any grid records."
        )
    with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
        return {
            name.rsplit(".", 1)[-1]: bundle.read(name)
            for name in bundle.namelist()
            if name.endswith((".shp", ".dbf"))
        }


def _records(dbf: bytes) -> list[dict[str, str]]:
    """The attribute table. Text fields are padded with spaces or NULs, so both go."""
    count, header_length, record_length = struct.unpack("<IHH", dbf[4:12])
    fields: list[tuple[str, int]] = []
    offset = 32
    while dbf[offset] != 0x0D:
        name = dbf[offset : offset + 11].split(b"\x00")[0].decode("ascii")
        fields.append((name, dbf[offset + 16]))
        offset += 32
    records = []
    for index in range(count):
        base = header_length + index * record_length + 1
        record, position = {}, 0
        for name, length in fields:
            raw = dbf[base + position : base + position + length]
            record[name] = raw.decode("utf-8", "replace").replace("\x00", "").strip()
            position += length
        records.append(record)
    return records


def _shapes(shp: bytes) -> list[list[Any]]:
    """Every record's rings, as arrays of longitude and latitude."""
    numpy = _numpy()
    shapes, offset = [], 100
    while offset < len(shp):
        _, content_words = struct.unpack(">ii", shp[offset : offset + 8])
        offset += 8
        body = shp[offset : offset + content_words * 2]
        offset += content_words * 2
        if struct.unpack("<i", body[:4])[0] == 0:
            shapes.append([])
            continue
        part_count, point_count = struct.unpack("<ii", body[36:44])
        starts = list(struct.unpack(f"<{part_count}i", body[44 : 44 + 4 * part_count]))
        begin = 44 + 4 * part_count
        points = numpy.frombuffer(
            body[begin : begin + 16 * point_count], dtype="<f8"
        ).reshape(-1, 2)
        ends = [*starts[1:], point_count]
        shapes.append([points[start:end] for start, end in zip(starts, ends, strict=True)])
    return shapes


@functools.cache
def _countries() -> dict[str, Country]:
    archive = _archive()
    records = _records(archive["dbf"])
    shapes = _shapes(archive["shp"])
    grouped: dict[str, list[tuple[dict[str, str], list[Any]]]] = {}
    for record, rings in zip(records, shapes, strict=True):
        # ``ISO_A2_EH`` fills the codes ``ISO_A2`` leaves as -99 for political
        # reasons -- France and Norway among them. Records sharing a code are
        # one country: Australia with its external territories, Kazakhstan with
        # the Baikonur lease.
        code = record.get("ISO_A2_EH") or ""
        if len(code) != 2 or not code.isalpha() or not rings:
            continue
        grouped.setdefault(code.upper(), []).append((record, rings))

    found = {}
    for code, members in grouped.items():
        # The sovereign record names the country; its dependencies follow. The
        # type is tested first because ``ISO_A2`` alone cannot tell them apart
        # where it is -99 for both: France and Clipperton Island.
        members.sort(
            key=lambda item: (
                item[0].get("TYPE") == "Dependency",
                item[0].get("ISO_A2") != code,
                item[0].get("NAME", ""),
            )
        )
        rings = tuple(ring for _, parts in members for ring in parts)
        longitudes = [ring[:, 0] for ring in rings]
        latitudes = [ring[:, 1] for ring in rings]
        found[code] = Country(
            code=code,
            name=members[0][0].get("NAME", code),
            parts=tuple(record.get("NAME", "") for record, _ in members),
            rings=rings,
            min_longitude=float(min(item.min() for item in longitudes)),
            max_longitude=float(max(item.max() for item in longitudes)),
            min_latitude=float(min(item.min() for item in latitudes)),
            max_latitude=float(max(item.max() for item in latitudes)),
        )
    return found


def countries() -> Mapping[str, Country]:
    """Every country Natural Earth draws with a two-letter code, by that code."""
    return _countries()


def country(code: str) -> Country:
    found = _countries().get((code or "").upper())
    if found is None:
        raise LandError(
            f"Natural Earth draws no country with the code {code!r}, so there is no "
            "land to clip the grid to. Areas administered by no recognised country "
            "carry no code; clip such a grid to its tiles instead."
        )
    return found


# -- the lattice ---------------------------------------------------------------

def buffer_cells(buffer_km: Decimal | float, resolution: Decimal, latitude: float) -> tuple[int, int]:
    """How many cells a buffer spans, along latitude and along longitude.

    A cell narrows towards the poles, so the longitude count is taken at the
    widest latitude the window reaches, where cells are narrowest: a buffer
    that is wide enough there is wide enough everywhere.
    """
    height_km = float(resolution) * KM_PER_DEGREE
    width_km = height_km * max(math.cos(math.radians(min(abs(latitude), 89.0))), 1e-6)
    km = float(buffer_km)
    if km <= 0:
        return 0, 0
    return math.ceil(km / height_km), math.ceil(km / width_km)


def _dilate(mask, rows: int, columns: int):
    """Grow a boolean mask by a rectangle of cells, with running sums rather than loops."""
    numpy = _numpy()

    def along(array, reach: int, axis: int):
        if reach <= 0:
            return array
        padded = numpy.pad(
            array.astype(numpy.int32),
            [(reach + 1, reach) if a == axis else (0, 0) for a in range(array.ndim)],
        )
        running = numpy.cumsum(padded, axis=axis)
        size = array.shape[axis]
        upper = numpy.take(running, numpy.arange(2 * reach + 1, 2 * reach + 1 + size), axis=axis)
        lower = numpy.take(running, numpy.arange(0, size), axis=axis)
        return (upper - lower) > 0

    return along(along(mask, rows, 0), columns, 1)


def touching(
    rings,
    resolution: Decimal,
    *,
    first_row: int,
    last_row: int,
    first_column: int,
    last_column: int,
):
    """Cells of a window that any ring touches, inside or on its edge.

    Two passes. A scan through each row's centre fills the cells whose centre
    is inside the outline, by the even-odd rule, so holes stay holes. Then every
    edge is walked in steps finer than a cell, marking every cell it passes
    through -- which catches the coastal cells whose centres are at sea and the
    islands smaller than a cell. Together that is every cell the outline
    touches, to within an eighth of a cell.
    """
    numpy = _numpy()
    step = float(resolution)
    height = last_row - first_row + 1
    width = last_column - first_column + 1
    kept = numpy.zeros((height, width), dtype=bool)
    if height <= 0 or width <= 0 or not rings:
        return kept

    edges = numpy.vstack([numpy.hstack([ring[:-1], ring[1:]]) for ring in rings if len(ring) > 1])
    x1, y1, x2, y2 = (edges[:, index] for index in range(4))
    low = numpy.minimum(y1, y2)
    high = numpy.maximum(y1, y2)

    # Only edges that reach the window's rows can cross them. Every such edge
    # is kept for the scan, wherever it lies east or west: whether a point is
    # inside is decided by counting all the crossings on its row, and dropping
    # the ones west of the window would turn land into sea.
    south = first_row * step
    north = (last_row + 1) * step
    relevant = (high >= south) & (low <= north)
    x1, y1, x2, y2, low, high = (item[relevant] for item in (x1, y1, x2, y2, low, high))
    order = numpy.argsort(low)
    x1, y1, x2, y2, low, high = (item[order] for item in (x1, y1, x2, y2, low, high))

    for row in range(height):
        centre = (first_row + row + 0.5) * step
        reach = numpy.searchsorted(low, centre, side="right")
        crossing = high[:reach] > centre
        if not crossing.any():
            continue
        ax, ay, bx, by = x1[:reach][crossing], y1[:reach][crossing], x2[:reach][crossing], y2[:reach][crossing]
        xs = numpy.sort(ax + (centre - ay) * (bx - ax) / (by - ay))
        for start, end in zip(xs[0::2], xs[1::2], strict=False):
            begin = math.ceil(start / step - 0.5) - first_column
            finish = math.ceil(end / step - 0.5) - first_column
            if finish > begin:
                kept[row, max(begin, 0) : min(finish, width)] = True

    # Walking the edges only marks the cells an edge passes through, so here the
    # edges can be narrowed to the window's columns as well.
    west = first_column * step
    east = (last_column + 1) * step
    nearby = (numpy.maximum(x1, x2) >= west) & (numpy.minimum(x1, x2) <= east)
    x1, y1, x2, y2 = (item[nearby] for item in (x1, y1, x2, y2))
    length = numpy.hypot(x2 - x1, y2 - y1)
    samples = numpy.maximum(
        numpy.ceil(length / (step / _EDGE_SAMPLES_PER_CELL)).astype(numpy.int64), 1
    ) + 1
    # Walked in batches, so a long coastline at a fine resolution costs a batch
    # of memory rather than all of its samples at once.
    batch_start = 0
    while batch_start < len(samples):
        running = numpy.cumsum(samples[batch_start:])
        batch_end = batch_start + max(1, int(numpy.searchsorted(running, _SAMPLE_BATCH)))
        counts = samples[batch_start:batch_end]
        owner = numpy.repeat(numpy.arange(batch_start, batch_end), counts)
        offsets = numpy.arange(int(counts.sum())) - numpy.repeat(numpy.cumsum(counts) - counts, counts)
        fraction = offsets / numpy.repeat(counts - 1, counts)
        px = x1[owner] + fraction * (x2[owner] - x1[owner])
        py = y1[owner] + fraction * (y2[owner] - y1[owner])
        rows = numpy.floor(py / step).astype(numpy.int64) - first_row
        columns = numpy.floor(px / step).astype(numpy.int64) - first_column
        inside = (rows >= 0) & (rows < height) & (columns >= 0) & (columns < width)
        kept[rows[inside], columns[inside]] = True
        batch_start = batch_end
    return kept


@functools.lru_cache(maxsize=32)
def land_mask(
    code: str,
    resolution: Decimal,
    buffer_km: Decimal,
    first_row: int,
    last_row: int,
    first_column: int,
    last_column: int,
) -> LatticeMask:
    """The cells of a window that touch the country's land, widened by the buffer.

    The window is the part of the lattice a grid actually asks about, so memory
    follows the grid rather than the country: a grid of one island of a large
    country rasterises one island's worth of lattice.
    """
    outline = country(code)
    widest = max(abs(first_row * float(resolution)), abs((last_row + 1) * float(resolution)))
    reach_rows, reach_columns = buffer_cells(buffer_km, resolution, widest)
    kept = touching(
        outline.rings,
        resolution,
        first_row=first_row - reach_rows,
        last_row=last_row + reach_rows,
        first_column=first_column - reach_columns,
        last_column=last_column + reach_columns,
    )
    grown = _dilate(kept, reach_rows, reach_columns)
    trimmed = grown[
        reach_rows : grown.shape[0] - reach_rows,
        reach_columns : grown.shape[1] - reach_columns,
    ]
    return LatticeMask(
        resolution=resolution,
        first_row=first_row,
        first_column=first_column,
        kept=trimmed,
    )


# -- whether a coordinate is in the country its row names ----------------------

#: ISO 3166-1 codes Natural Earth draws inside another country's outline rather
#: than under a code of their own, with the name a message should use. Checked
#: against the 1:10m Admin 0 archive this module ships: every other officially
#: assigned code is drawn under itself, so with these every code has an outline.
DRAWN_WITHIN: Mapping[str, tuple[str, str]] = {
    "BQ": ("NL", "Bonaire, Sint Eustatius and Saba"),
    "BV": ("NO", "Bouvet Island"),
    "CC": ("AU", "Cocos (Keeling) Islands"),
    "CX": ("AU", "Christmas Island"),
    "GF": ("FR", "French Guiana"),
    "GP": ("FR", "Guadeloupe"),
    "MQ": ("FR", "Martinique"),
    "RE": ("FR", "Réunion"),
    "SJ": ("NO", "Svalbard and Jan Mayen"),
    "TK": ("NZ", "Tokelau"),
    "YT": ("FR", "Mayotte"),
}

#: Countries whose outline leaves out too much land to screen a coordinate
#: against, so the screen uses the outline's extent instead: the box around
#: every island it does draw, widened by the buffer. Natural Earth draws 176 of
#: the Maldives' roughly 1,190 islands, and a resort on an undrawn one can sit
#: well beyond any buffer of a drawn coast. The extent still catches what the
#: screen exists for, a coordinate in the wrong country.
EXTENT_ONLY = frozenset({"MV"})

#: The lattice the screen rasterises on: 0.0125 degrees, about 1.4 km, the finest
#: base resolution a seed grid uses. A coordinate passes when its cell touches
#: the country's land widened by the buffer -- the rule a grid clipped to land
#: keeps its cells by, so a location the screen passes is one such a grid can
#: hold.
SCREEN_RESOLUTION = Decimal("0.0125")

#: The screen works one degree square at a time, 80 cells a side at that
#: resolution, and only where there are coordinates to test. Memory then
#: follows the portfolio rather than the country: France's outline reaches from
#: the Caribbean to the Indian Ocean, and a book in Paris rasterises Paris.
_SCREEN_TILE = 80


def outline_code(code: str) -> str | None:
    """The code the outline holding a country is drawn under, or ``None``.

    ``None`` means the code is not a country Natural Earth draws, under itself
    or within another: in practice, not an ISO 3166-1 code at all.
    """
    code = (code or "").strip().upper()
    if code in _countries():
        return code
    if code in DRAWN_WITHIN:
        return DRAWN_WITHIN[code][0]
    return None


def country_name(code: str) -> str | None:
    """The name to show for a code, or ``None`` where no outline holds it."""
    code = (code or "").strip().upper()
    if code in DRAWN_WITHIN:
        return DRAWN_WITHIN[code][1]
    found = _countries().get(code)
    return found.name if found is not None else None


def within(
    code: str,
    points,
    *,
    buffer_km: Decimal = DEFAULT_COAST_BUFFER_KM,
) -> list[bool] | None:
    """For each ``(latitude, longitude)``, whether it is on the country's land.

    On land means inside the outline or within ``buffer_km`` of it, measured
    the way a grid clipped to land measures it. ``None`` where no outline holds
    the code, so a caller can tell a coordinate outside a country from a code
    that names none.
    """
    drawn = outline_code(code)
    if drawn is None:
        return None
    numpy = _numpy()
    points = [(float(latitude), float(longitude)) for latitude, longitude in points]
    if not points:
        return []
    outline = _countries()[drawn]
    latitudes = numpy.array([point[0] for point in points])
    longitudes = numpy.array([point[1] for point in points])

    # Anything beyond the outline's extent, widened by the buffer, is outside
    # without rasterising. For an extent-only country that is the whole test.
    reach = float(buffer_km) / KM_PER_DEGREE
    widening = reach / numpy.maximum(numpy.cos(numpy.radians(numpy.minimum(numpy.abs(latitudes), 89.0))), 1e-6)
    near = (
        (latitudes >= outline.min_latitude - reach)
        & (latitudes <= outline.max_latitude + reach)
        & (longitudes >= outline.min_longitude - widening)
        & (longitudes <= outline.max_longitude + widening)
    )
    if (code or "").strip().upper() in EXTENT_ONLY or drawn in EXTENT_ONLY:
        return [bool(item) for item in near]

    step = float(SCREEN_RESOLUTION)
    rows = numpy.floor(latitudes / step).astype(numpy.int64)
    columns = numpy.floor(longitudes / step).astype(numpy.int64)
    result = numpy.zeros(len(points), dtype=bool)
    tiles: dict[tuple[int, int], list[int]] = {}
    for index in numpy.flatnonzero(near):
        key = (int(rows[index]) // _SCREEN_TILE, int(columns[index]) // _SCREEN_TILE)
        tiles.setdefault(key, []).append(int(index))
    for (tile_row, tile_column), members in tiles.items():
        first_row, first_column = tile_row * _SCREEN_TILE, tile_column * _SCREEN_TILE
        mask = land_mask(
            drawn,
            SCREEN_RESOLUTION,
            Decimal(buffer_km),
            first_row,
            first_row + _SCREEN_TILE - 1,
            first_column,
            first_column + _SCREEN_TILE - 1,
        )
        result[members] = mask.contains(rows[members], columns[members])
    return [bool(item) for item in result]


@dataclasses.dataclass(frozen=True, slots=True)
class CountryScreen:
    """Checks a coordinate against the country its row names, on these outlines.

    What the cohort rules are handed, so the rules need not know where an
    outline comes from and the outlines need not know what a cohort is.
    """

    buffer_km: Decimal = DEFAULT_COAST_BUFFER_KM
    source: str = SOURCE

    def within(self, code: str, points) -> list[bool] | None:
        return within(code, points, buffer_km=self.buffer_km)

    def name(self, code: str) -> str | None:
        return country_name(code)
