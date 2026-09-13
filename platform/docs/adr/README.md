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
| [0004](0004-converter-refuses-unapproved-policy.md) | The converter refuses to run under an unapproved policy | Accepted, amended by 0008 |
| [0005](0005-decimal-money-across-the-boundary.md) | Money crosses the API as decimal strings | Accepted |
| [0006](0006-evidence-in-the-design-system.md) | Evidence class is a design system primitive | Accepted |
| [0007](0007-internal-use-licence-basis.md) | Model data is held under one internal-use basis | Accepted |
| [0008](0008-intensity-measures-as-area-peril-channels.md) | Intensity measures are carried as correlated area-peril channels | Accepted |
| [0009](0009-cass-writes-the-oasis-package.md) | CASS writes the Oasis model package and ships its own lookup inside it | Accepted |
| [0010](0010-patched-oasis-worker.md) | The Oasis worker image carries a build-time patch | Accepted |
| [0011](0011-intake-template-and-policy-id.md) | Portfolios arrive through the CASS intake template, joined on Policy ID | Accepted |
| [0012](0012-national-classical-model-run-event-based.md) | A published national hazard model is converted to an event-based run | Accepted |
| [0013](0013-product-areas-hold-tabs.md) | A product area holds its screens as tabs | Accepted |

Records 0007 to 0013 are where delivery moved the design away from build plan
1.7. Plan 1.8 marks each affected statement *Changed in 1.8* and links here.

## Status values

- **Proposed** — written, not yet agreed.
- **Accepted** — in force. The code reflects it.
- **Amended** — still in force, with a later record changing part of it.
- **Superseded** — replaced by a later record, which is named in this one.
