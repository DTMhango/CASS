# 19. A footprint is stored as the ktools binary, with compression measured and held in reserve

Status: Accepted
Date: 2026-09-14

## Context

A footprint is the table of shaking for every event, cell and intensity bin. For
the Jakarta–Bandung region that is 12.8 million rows; for Indonesia's 52,831
cells it is about 790 million. The engine reads it throughout a loss run, so the
format decides disk, build time and run time. CASS writes the ktools binary
(`footprint.bin` with its index), which oasislmf 2.5.7 reads alongside a
compressed binary, Parquet and CSV. Item 23 is the measurement that chooses.

## Decision

The runtime format stays the ktools binary CASS writes today.

Compressed binary is the reserve, and the trade is measured rather than
guessed: it is four times smaller, reads at the same speed, and costs about a
minute more to build at national scale. CASS switches to it when a package's
size is actually binding — a second national package on one volume, or a
deployment whose model store is small — and that switch needs no further study.

Parquet is rejected: larger than the compressed binary, slower to read, and a
directory of one file per event.

## Evidence

Measured on 14 September 2026 on the deployed package, with the same
Jakarta–Bandung book run through each format and the same losses required of
all three. National scale is the regional footprint's rows repeated across
52,831 cells, labelled synthetic: the same shaking, the size of a country.

| Scale | Format | Size | Build | Loss run | Losses |
| --- | --- | --- | --- | --- | --- |
| Region (858 cells) | binary | 154 MB | — | 16s | identical |
| | compressed | 40 MB | 7s | 16s | identical |
| | Parquet | 107 MB | 50s | 30s | identical |
| A third of Indonesia (17,160 cells) | binary | 3,063 MB | 18s | 14s | identical |
| | compressed | 765 MB | 283s | 21s | identical |
| | Parquet | 866 MB | 170s | 23s | identical |
| Indonesia (52,831 cells) | binary | 9,494 MB | 44s | 17s | identical |
| | compressed | 2,366 MB | 710s | 18s | identical |
| | Parquet | 2,566 MB | 296s | 22s | identical |

Every format returned the same loss to the cent — 570,652.25 analytically and
566,728.13 as the sample mean — which is what makes this a storage choice rather
than a modelling one, and it is the figure the live run of the same book
produced.

The build times above compress at zlib level 9. Measured over 400 national-scale
events, level 1 reaches 27% of the binary and level 6 reaches 24.5%, and level 6
is about half the time of level 9: a national footprint compresses in roughly a
minute at level 1 and three and a half at level 6, rather than twelve.

## Alternatives considered

**Compressed binary now.** Four times smaller at no read cost, and the reason it
is not adopted today is that disk is not binding: one package is served at a
time (ADR 9), a national package is 9.5 GB, and writing a second binary format
is code and risk for space CASS is not short of.

**Parquet.** Its appeal was columnar reads, and the engine's reader takes one
file per event: 27,313 files, 8% larger than the compressed binary and 29%
slower to read at national scale.

**CSV.** Read by the engine, and at national scale it would be tens of
gigabytes of text.

## Consequences

A national package is about 9.5 GB of footprint. The model volume has to hold
that, and the deployment swap holds two for the moment of the swap.

Loss run time is not sensitive to the format at these scales: 17 to 22 seconds
for a 64-location book over 27,313 events, whichever of the three is used. What
the format decides is disk and build time.

Building a national package writes 9.5 GB, which is 44 seconds of the build.

## Revisit if

A deployment cannot hold a national package, or holds several; the engine's
Parquet reader stops writing a file per event; or a footprint grows enough that
reading it, rather than computing losses from it, dominates a run.
