# 20. A grid keeps only cells on land and near settlement, and CASS ships seeds for ten countries

Status: Accepted
Date: 2026-09-17

## Context

Every cell of a grid is a site in every hazard calculation run on it and a set of
rows in every footprint stored for it. Tiles are rectangles and a country is not:
the eight tiles of the Indonesian prototype held 52,831 cells at 0.1 degrees, and
only about 18,000 of them touched land. Following the coast with rectangles
instead was measured and rejected: at 0.05 degrees Indonesia's coast takes 2,075
of them, which no reviewer could read.

Land is not exposure either. The interiors of Papua and Kalimantan, Oman's Empty
Quarter and Nepal's high Himalaya are land where nothing insurable stands.

A grid was also written from nothing for every country, and a refinement off the
base lattice silently broke the builder's promise that every place is in exactly
one cell: measured, a 0.025-degree refinement edged at 0.06 over a 0.1 base put a
quarter of its points in two cells, and one edged at 0.05 left three fifths of
its area in none.

## Decision

- **A grid specification states a domain.** `clip_to_land` keeps only cells that
  touch the country's land, widened by `coast_buffer_km`; `skip_unsettled` keeps
  only cells within `settlement_buffer_km` of any building or resident. Both are
  off in the API, so a specification that says nothing builds as it always did,
  and both are on, at 5 km, in the grid builder's form.
- **The outline is Natural Earth's 1:10m Admin 0 countries, version 5.1.1**,
  public domain, shipped inside `cass_keys` (4.9 MB) and checked against a pinned
  checksum. Records sharing an ISO code are one country.
- **A cell is kept when any part of it touches land**, not only its centre.
- **The settlement layer is GHSL R2023A, epoch 2020, at 30 arc-seconds: built-up
  surface or population, whichever is present.** It ships as a banded bitmask
  (8.9 MB) derived by `services/keys/tools/derive_settlement_mask.py` from the
  published rasters, whose checksums the script records.
- **Both are computed when asked.** Nothing is precomputed or stored per grid.
- **A refinement must sit on the base lattice**: its resolution must divide the
  base a whole number of times and its edges must be multiples of the base. The
  build refuses one that does not and names the box that would; the estimate
  says so while the specification is written. `check_grids` reports registered
  grids whose cells overlap.
- **The builder's count is exact** once a specification is whole: refinements
  replace base cells and the domain removes its cells before the count is made,
  so the estimate and the build cannot disagree. It also reports land no tile
  covers.
- **The grid limit is 500,000 cells.** The first limit, 250,000, was a guard
  against a typo and never a measurement.
- **CASS ships seed specifications** for Bangladesh, Bhutan, Indonesia, Kuwait,
  the Maldives, Nepal, Oman, the Philippines, Qatar and Türkiye. The base
  resolution is the finest of 0.0125 and 0.025 degrees at which the country's
  inhabited land fits in 300,000 cells; where only 0.025 fits, the largest cities
  are refined to 0.0125. Tiles name regions; the domain removes the sea and empty
  land. Each seed is stored with the counts the builder produced, and the tests
  build every seed and hold it to them.
- **The builder is version 1.1.0.** A specification with no domain and aligned
  refinements builds exactly the cells, in exactly the order, 1.0.0 did: the
  Indonesian prototype's cell file is byte for byte the same.

## Evidence

Measured on 16 and 17 September 2026.

| Test | Result |
| --- | --- |
| Rasterising Indonesia's outline | 0.15 s at 0.0125 degrees |
| Centre-only test instead of touching, Indonesia at 0.05 degrees | 9% of cells lost, all on coasts; the Maldives left 3 cells |
| Every Natural Earth country with a code, clipped at 1 degree | 240 of 240 keep land |
| Natural Earth's Maldives | 176 of about 1,190 islands, 110 of 298 km² |
| Buildings versus residents, ten countries, 2 km | a few hundred cells each way per country, so the union costs little |
| KRE's 30 June geocoded book, 224 locations in Indonesia and Nepal | land clip at 5 km dropped none that lay in the country (five were geocoded thousands of kilometres outside it); settlement buffer dropped 4 at 0 km, 2 at 2 km (both postcode centroids), 0 at 5 km |
| Grid of 230,400 cells | built in 1.1 s, loaded in 1.4 s, 5,000 locations found in 0.1 to 1.7 s |
| A 64-location book against 858- and 52,831-cell footprints (ADR 19) | 16 s and 17 s: loss runs do not slow with grid size |

The seeds, with land clipped at 5 km and settlement at 5 km:

| Seed | Base | Cells | Removed as sea | Removed as empty land |
| --- | --- | --- | --- | --- |
| Indonesia | 0.025 + 10 cities at 0.0125 | 270,769 | 631,216 | 35,211 |
| Philippines | 0.0125 | 220,318 | 686,713 | 2,601 |
| Türkiye | 0.025 + 10 cities at 0.0125 | 144,678 | 57,455 | 1,187 |
| Oman | 0.0125 | 130,825 | 162,184 | 58,991 |
| Bangladesh | 0.0125 | 94,166 | 91,560 | 770 |
| Nepal | 0.0125 | 92,132 | 123,432 | 6,196 |
| Bhutan | 0.0125 | 24,583 | 10,469 | 3,588 |
| Maldives | 0.0125, not clipped to land | 16,185 | — | 40,007 |
| Kuwait | 0.0125 | 13,840 | 7,023 | 257 |
| Qatar | 0.0125 | 8,752 | 2,424 | 24 |

## Alternatives considered

**Tiles generated from the coastline.** Exact, and unreadable: thousands of
rectangles per archipelago.

**Clipping on the cell's centre.** Loses coastal cells, which is where exposure
concentrates.

**Population alone, or buildings alone.** Each misses what the other sees: a
factory has buildings and no residents.

**A 2 km settlement buffer.** Two real locations fell outside it.

**The Maldives clipped to land.** The outline lacks most of the islands; its
seed keeps cells near buildings instead.

## Consequences

A location in a cell the domain removed is reported as outside the grid, as a
location outside every tile always was. Remote infrastructure GHSL does not
detect, far from any settlement, can fall outside; every grid that skips unsettled
land records that as an open question, and the Bhutan, Kuwait and Qatar seeds name
hydropower and oil and gas facilities.

Natural Earth draws boundaries as they are held on the ground, so a grid clipped
to a country with a disputed boundary follows the de facto line.

Changing any of a grid's domain, tiles, resolution or refinements is a new grid
version, and a new grid version needs its hazard computed again.
