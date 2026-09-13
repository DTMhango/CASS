# 12. A published national hazard model is converted to an event-based run

Status: Accepted
Date: 2026-09-13

## Context

Build plan 1.7 section 6 expected GEM earthquake source models loaded into
OpenQuake and run on the fixed grid. The model actually available for
Indonesia is PuSGeN 2024, the national model in GEM's 2026 mosaic. It is a
*classical* calculation: it publishes the probability of exceeding a ground
motion at a site, because it exists to support a building code. Run as
published, it completes successfully and produces nothing a footprint can be
made from.

## Decision

A published hazard package is uploaded and registered as a `HazardModel`, and a
run is configured from it rather than written by hand.

- `cass_converter.job_config` parses the `job.ini` into typed parameters and
  separates what an operator may change — investigation time, stochastic event
  sets, seed, region — from the model's own science. Logic trees, source files,
  maximum distances and distance lookups are shown and never offered, because
  editing them makes a different model under the publisher's name.
- The configuration is converted from `classical` to `event_based`, and every
  change is listed against the published file.
- Where the published site model writes `-999` for basin depths, meaning "not
  measured", the depths are recomputed from Vs30 and marked derived.
- The logic tree (about 1,080 realisations) is sampled along one path until a
  realisation-weighting rule is approved, and the screen says the footprint is
  one view of the hazard, not the weighted mean.
- A run may be limited to a region of the grid. The published site model is
  joined to cells within 15 km, the rest fall back to reference conditions, and
  both counts are reported.
- The worker rebuilds the job from the saved specification and refuses if its
  checksum has moved since the configuration was reviewed.

## Alternatives considered

**Hand-edit an event-based ini.** It took ten changes to convert PuSGeN, and two
of them — the basin depths and the realisation count — produce plausible,
badly wrong numbers when missed.

**Wait for an event-based GEM set.** No such set exists for the pilot countries.

## Consequences

Footprints are one sampled path, so hazard uncertainty is understated until the
weighting rule is decided. The hazard benchmark gate stays open.

A national run over all 52,831 Indonesian cells is hours of compute; only the
858-cell Jakarta–Bandung region has been run.

Nepal has no source model, so it can map keys but cannot produce a loss.

## Revisit if

A realisation-weighting rule is approved; GEM publishes event sets for the
pilot countries; or a Nepal source model is acquired.
