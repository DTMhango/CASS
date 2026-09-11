# PiWind exposure fixtures

These four CSVs are copied verbatim from the official Oasis PiWind reference
model. Section 12 of the build plan makes PiWind the end-to-end regression
baseline, and section 17 asks for the successful PiWind command-line test to
become an automated integration test.

| File | Purpose |
| --- | --- |
| `SourceLocOEDPiWind10.csv` | 10 locations; the ground-up loss source |
| `SourceAccOEDPiWind.csv` | 2 policy layers; required for insured loss |
| `SourceReinsInfoOEDPiWind.csv` | One surplus-share contract |
| `SourceReinsScopeOEDPiWind.csv` | Which locations that contract covers |

## Provenance

- Source: <https://github.com/OasisLMF/OasisPiWind>, `exposure_data/`
- Licence: BSD 3-Clause, as published by OasisLMF
- Copied: 2026-09-11

The upstream clone used to obtain them sits at `.piwind_e2e_main/` in the
working tree. It is **not** part of this repository: it carries its own `.git`
and is excluded by `.gitignore`, so nothing from it is tracked here except
these four files.

## Why they are copied rather than referenced

A test that depends on a sibling clone being present fails on a clean checkout
and in CI. These files are two kilobytes in total, so vendoring them costs
nothing and makes the regression baseline reproducible anywhere.

## Why they are wind, not earthquake

They are deliberately a wind portfolio. Running them through the earthquake
validator proves the platform reports "no modelled peril applies" for all ten
locations rather than silently producing zero loss, which is the section 15
failure mode of hidden not-at-risk exposure.
