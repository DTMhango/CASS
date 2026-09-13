# 10. The Oasis worker image carries a build-time patch

Status: Accepted
Date: 2026-09-13

## Context

Build plan section 10 prefers unmodified upstream engine containers, with CASS
logic kept in separate adapters, and the compose file said the engine
containers were unmodified upstream images.

Running insured and reinsurance analyses of a real-sized portfolio against
`coreoasis/model_worker:2.5.7` found three defects in oasislmf 2.5.7, which is
still the latest release on PyPI as of 2026-09-13:

1. `FMReader.event_read_log` builds a debug message by indexing a node-sized
   array with a compute counter. It raises `IndexError` and kills `fmpy`
   whenever computes outrun nodes, which any multi-layer structure of a few
   hundred items does. The message is built whatever the log level, so
   configuration cannot avoid it.
2. The financial module's compute queue is sized with no headroom. A node
   queued twice in one event writes past the end of the array, which under numba
   is a segmentation fault mid-calculation.
3. Nothing in file generation writes `fmsummaryxref` into each `RI_n`
   directory, so every reinsurance analysis stops on a missing file after
   ground-up and insured have already been calculated.

Ground-up never reaches any of this code, so every failure looked like a
problem with the financial terms.

## Decision

`deploy/images/Dockerfile.oasis-worker` builds `cass/model_worker:2.5.7-patched`
from the official image and applies `patches/oasislmf_2_5_7_fm_event_log.py`:

- the debug message is built only when debug logging is on and the index is in
  range;
- the compute queue is allocated with headroom, which changes no arithmetic;
- where the engine wrote no reinsurance summary index, one is written mapping
  every output of the level to summary 1 of set 1 — the whole portfolio, exactly
  what the engine writes for the insured level beside it.

The patch asserts the exact source it replaces, so an engine upgrade that has
moved the code fails the image build instead of carrying a stale patch.
`OASIS_LOG_LEVEL` defaults to `INFO`.

OpenQuake stays unmodified.

## Alternatives considered

**Ground-up only until upstream fixes it.** The plan's insured and reinsurance
perspectives would have been undeliverable for an unknown period.

**Fork oasislmf.** A fork accumulates exactly the maintenance section 15 warns
about. Three asserted, removable edits do not.

## Consequences

The worker is a CASS-built image, so it needs the same scanning, SBOM and
digest pinning as the CASS images. CI does not build it yet.

Reinsurance results are available only at whole-portfolio summary level. A
reinsurance summary by treaty, country or account needs the upstream fix rather
than this patch.

oasislmf is BSD-licensed, so the modification is permitted. The patch records
what was changed and why.

## Revisit if

An oasislmf release fixes any of the three defects — the build will fail on
that file — or reinsurance summaries below portfolio level are required.
