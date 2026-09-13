# 7. Model data is held under one internal-use basis

Status: Amended by [0015](0015-research-tool-and-gem-permission.md), which
records GEM Foundation's permission for the data and models it makes publicly
available, and that CASS is a research tool
Date: 2026-09-13

## Context

Build plan 1.7, section 10, took the position that the public GEM exposure and
vulnerability models are CC BY-NC-SA, that Klapton Re's use supports commercial
reinsurance decisions, and that the use should therefore stay a research
activity until GEM confirmed permitted use in writing. The registry followed
that: `licence_cleared` defaulted to false on every vulnerability set, hazard
model and hazard set, every upload asked whoever was uploading to state a
clearance and its reference, and a model version with an uncleared asset could
only publish as a research prototype.

The Indonesian PuSGeN 2024 hazard package carries the same licence.

## Decision

CASS is an internal Klapton Re platform. The model data it carries is used
inside the company, is not redistributed outside it, and is not sold. That
basis is recorded once, as `INTERNAL_USE_LICENCE` in
`apps.modelregistry.models`, and applied by default:

- `VulnerabilitySet`, `HazardModel` and `HazardSet` default to cleared under
  that note, and migration `modelregistry.0006_internal_use_licence` clears the
  records that already existed.
- Uploads and the GEM registration command no longer ask for a clearance. The
  flags remain, so a narrower entitlement can still be stated explicitly.
- Administration states the basis once, instead of every model and result
  screen carrying a caveat.

`licence_cleared` survives as a way to mark data deliberately not usable here.
A hazard run launched from such a model still calculates, and is labelled
research only.

## Alternatives considered

**Keep per-asset clearance.** It asked the same question of every upload, and
the answer was a property of the installation rather than of the file.

**Keep everything research-only until GEM writes.** Every model version would
have stayed blocked from decision use on a question the platform cannot answer.

## Consequences

The licence is no longer a publication blocker, and the section 10 data-rights
gate is answered at installation level rather than per asset.

The legal question is not closed by this record, and should not be read as
closed. CC BY-NC-SA's NonCommercial term is exactly what plan 1.7 thought
needed GEM's written confirmation for internal use that supports commercial
decisions. CASS records the basis KRE operates under; it does not make the
legal determination. Any use outside Klapton Re — including distributing an
approved local Docker package beyond KRE staff, or a derived Oasis package —
still needs the licence analysis section 10 describes.

## Revisit if

KRE legal or GEM advises that internal use supporting pricing or reserving
needs an agreement; data is to leave the company; or a licensed asset arrives
with terms narrower than the installation basis.
