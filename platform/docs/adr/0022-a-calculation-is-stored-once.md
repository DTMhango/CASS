# 22. A calculation is stored once, streamed rather than held, and rebuilt rather than rerun

Status: Accepted
Date: 2026-09-17

## Context

A hazard run kept its calculation four times. OpenQuake kept its datastore; CASS
copied the datastore into its store; the footprint was stored as CSV text beside
it; and the deployed model package held it again as a ktools binary. For the
962-cell Jakarta-Bandung calculation at 1,000 years that was 176 MB, 176 MB,
335 MB and about 154 MB.

The run also pulled the whole datastore and a CSV export of the ground motion
into memory before converting it, and a package build read every footprint into
memory. A national calculation is gigabytes, and the worker is capped at 3.5 GB:
either step would have failed after the calculation had already run for hours.

And a footprint counted into intensity bins that later change has had no way
back short of running the calculation again.

## Decision

- **The datastore is streamed to disk**, uploaded from the file, checked against
  its size once stored, and read a slice at a time to build the hazard set. The
  CSV exports are no longer asked for. The hazard set records the datastore's
  address, OpenQuake's engine version and checksum from the datastore itself, and
  a fingerprint of its ground motion.
- **OpenQuake's copy is removed once the hazard set is registered**, unless the
  installation sets `CASS_OPENQUAKE_KEEP_CALCULATIONS`. It is kept whenever
  anything before registration fails. A removal the engine refuses does not fail
  the run; the run says the calculation is stored twice.
- **A comparison chained onto a removed calculation runs it again** from the saved
  configuration, checks the new datastore's ground-motion fingerprint and input
  checksum against the stored ones, refuses and removes it if they differ, and
  removes it again afterwards.
- **Footprints are stored compressed**, as gzip written without a time or name in
  its header, so the same rows write the same bytes. Tables stored before read as
  they were.
- **A package is built from footprints downloaded to a workspace** and read a row
  at a time; the workspace is removed as soon as the package is written.
- **A footprint is rebuilt from the stored datastore** when the intensity bins
  change, as a run from the hazard set's row. The result is a new hazard set that
  names the one it replaces and shares its datastore. The replaced set's
  footprints are expired once no model version uses it and it is not published;
  its record and its datastore stay.

## Evidence

Measured on 16 and 17 September 2026.

| Measurement | Result |
| --- | --- |
| Footprint of the 1,000-year Jakarta-Bandung calculation | 335 MB as CSV, 55 MB compressed at gzip level 1 in 1.8 s, 49 MB at level 6 in 8.3 s |
| Building that footprint from the datastore | 13,588,639 rows, the count the registered set had |
| The same 1,000-year job run twice | identical ground motion, events and input checksum |
| Ground-motion fingerprint | 0.7 s for 6.9 million rows, 6.1 s for 70 million; unchanged by shuffling the rows |
| OpenQuake's remove endpoint on engine 3.23.4 | the calculation is gone afterwards |
| The live platform, 10,000 years over Jakarta-Bandung with motion from 0.05 g | run streamed and registered in 540 s; OpenQuake's copy gone; footprint files stored as `.gz`; rebuilt from the stored datastore in 60 s, replacing four footprint tables, which were expired; a package footprint of 108 MB written from the compressed files in 17 s |

## Alternatives considered

**Keep no footprint in the registry and bin at package time.** One fewer copy,
and every package build would bin again, each time a model version is switched:
693 s for the 10,000-year regional calculation before ADR 21's floor, and far
longer nationally. With the floor the compressed footprint is small -- 38 MB for
that calculation -- so keeping it costs little.

**Keep OpenQuake's copy for comparisons.** Doubles the storage of every run for a
check made occasionally, when running the calculation again reproduces it
exactly.

**Store the registry footprint as the ktools binary.** The package merges measures
into channels, so it would still be rewritten at every build.

## Consequences

What stays stored for one hazard set is the datastore, for its 90-day retention,
and the compressed footprint; the deployed package is the engine's working copy
of one set at a time. After the datastore expires a footprint can no longer be
rebuilt, and the set's page says so.

A comparison against OpenQuake on a removed calculation costs a second run of it.
