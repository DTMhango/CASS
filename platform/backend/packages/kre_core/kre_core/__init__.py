"""Shared primitives for the KRE catastrophe modelling platform.

This package holds the boundary rules that section 4 of the build plan makes
non-negotiable, so that every service enforces them the same way:

* large scientific arrays live in the artifact store, never in PostgreSQL;
* every artifact carries a content checksum, retention class and access policy;
* every job is idempotent and records input and output checksums;
* host paths never appear in calculation contracts -- only artifact URIs.
"""

__version__ = "0.1.0"
