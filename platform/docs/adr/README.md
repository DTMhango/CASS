# Decision records

Section 14 of the build plan asks for architecture, model and data decisions to
be recorded in short version-controlled decision records. These are they.

A record states what was decided, what it rules out, and what would make us
revisit it. A decision that cannot name its alternatives was not a decision.

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-separate-backend-and-frontend-trees.md) | Separate backend and frontend trees | Accepted |
| [0002](0002-artifact-store-boundary.md) | Large arrays live behind an artifact store interface | Accepted |
| [0003](0003-one-run-table-with-pipelines.md) | One run table, with pipelines selected by kind | Accepted |
| [0004](0004-converter-refuses-unapproved-policy.md) | The converter refuses to run under an unapproved policy | Accepted |
| [0005](0005-decimal-money-across-the-boundary.md) | Money crosses the API as decimal strings | Accepted |
| [0006](0006-evidence-in-the-design-system.md) | Evidence class is a design system primitive | Accepted |

## Status values

- **Proposed** — written, not yet agreed.
- **Accepted** — in force. The code reflects it.
- **Superseded** — replaced by a later record, which is named in this one.
