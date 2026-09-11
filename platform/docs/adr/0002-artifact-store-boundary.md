# 2. Large arrays live behind an artifact store interface

Status: Accepted
Date: 2026-09-11

## Context

Section 4 of the build plan states that Django stores references to large
artifacts rather than scientific arrays, and section 5 states that PostgreSQL
holds no event-site-IMT observation table. Section 15 names the failure this
avoids: large files passing through Django or CSV, causing poor performance,
memory pressure and fragile workflows.

There is a second problem the plan raises repeatedly. The first deployment runs
on a Windows workstation through Docker Desktop, and the target is a managed
Linux container environment. Windows host paths appearing in a calculation
contract would make those two environments behave differently, and would make a
run manifest unreproducible on any other machine.

## Decision

Every artifact is addressed by a `kre://bucket/key` URI and reached through one
interface, with a filesystem backend and an S3 backend that expose identical
operations.

Key validation rejects backslashes, drive letters and traversal, so a host path
cannot enter a calculation contract even by accident. Every stored object
carries a content checksum, a byte count, a content type, a retention class and
an access policy.

Django records an artifact only after the bytes are present and digested. The
browser uploads large files directly to object storage through a short-lived
session, with the same session shape in both backends so the interface does not
branch on deployment mode.

## Alternatives considered

**Store scientific arrays in PostgreSQL.** Rejected by the plan itself, and the
volumes make it untenable: a country-scale footprint is far larger than a
control-plane database should ever hold.

**Pass filesystem paths between services.** Simplest on one machine, and the
reason the plan warns about it. It ties the calculation contract to a host
layout and makes the Windows and Linux deployments genuinely different systems.

**Use the S3 SDK directly everywhere.** Would work, but it makes the local
installation of section 11 require an S3-compatible service even when a
filesystem would do, and it scatters bucket and retention decisions across the
codebase.

## Consequences

Moving from a workstation to a managed environment is a configuration change.
Retention is a property of an artifact class rather than a manual cleanup task.
A run manifest is portable, because it names URIs and checksums rather than
paths.

The cost is one more interface to implement per backend, and the discipline of
never reaching around it. A `open(path)` on a scientific artifact anywhere
outside the store implementation is a defect.

## Revisit if

A calculation engine cannot be driven without a shared filesystem mount. Both
Oasis workers and OpenQuake currently expect one, so the adapters will bridge
between the store and an engine-owned volume; if that bridge becomes the
dominant cost, the boundary may need to move rather than the rule to change.
