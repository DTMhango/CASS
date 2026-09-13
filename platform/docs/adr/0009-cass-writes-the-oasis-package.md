# 9. CASS writes the Oasis model package and ships its own lookup inside it

Status: Accepted
Date: 2026-09-13

## Context

Build plan 1.7 section 7 had the model-build job compile runtime binaries with
the Oasis-supported converters (`csvtobin` and friends), and section 8 gave
exposure-to-model mapping to the CASS keys service while pinned OasisLMF
generates the kernel files.

Oasis also runs a lookup of its own while generating inputs, from the
`keys_data` of the model package it serves. If that lookup were written
separately — as an OasisLMF built-in lookup configuration — CASS would reconcile
a portfolio against one mapping before submission, and the engine would build
items from a different one.

## Decision

`cass_converter.oasis_package` writes the package itself: the ktools binaries
under `model_data` (events, occurrence, footprint and index, vulnerability,
damage-bin dictionary, return periods), `meta-data/model_settings.json`, and
`oasislmf.json`. Every binary layout is held byte-for-byte against the official
PiWind model's binaries in the test suite.

`keys_data` vendors `cass_keys`, `cass_oed` and the policy vocabulary from
`cass_core`, plus a thin class adapting OasisLMF's location frame to it. The
lookup Oasis runs is the lookup CASS runs, and both read the grid and mapping
through `cass_keys.assets`.

`apps.modelregistry.package` deploys the package onto the `oasis-model` volume,
which the worker mounts read-only as its model root. It writes to a staging
directory and renames each entry into place, so a worker reading during a
rebuild reads the previous package whole.

## Alternatives considered

**`csvtobin` inside the worker.** The control plane could then neither build
nor test a package without shelling into the engine.

**An OasisLMF built-in lookup.** It gives two lookups that can disagree about a
storey band or a cell edge while each looks correct.

## Consequences

`validate_inputs` now compares two runs of one lookup, so a disagreement points
to a packaging defect rather than a legitimate model difference.

The binary layouts are CASS's to keep correct, and must be re-verified against
the reference model on every Oasis upgrade.

The worker serves one model package at a time. Building a package for a model
version replaces what the worker serves, so two model versions cannot be run
concurrently on one installation.

## Revisit if

Oasis changes a binary format; the storage-format decision selects Parquet
footprints; or two model versions must be served at once.
