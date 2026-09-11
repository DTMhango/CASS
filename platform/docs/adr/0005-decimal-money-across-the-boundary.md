# 5. Money crosses the API as decimal strings

Status: Accepted
Date: 2026-09-11

## Context

Section 8 requires allocated TIV to reconcile exactly to the source record, and
section 15 names weighted exposure splitting that duplicates or loses value as
a material loss misstatement.

A large facultative portfolio can carry values well beyond the range where a
64-bit float represents integers exactly. JSON has one number type, and every
JSON parser in a browser produces a float. So a total insured value serialised
as a JSON number and parsed in the browser has already lost precision before
anything is displayed.

## Decision

Monetary values are `Decimal` on the server, are serialised as strings, and
stay strings in the TypeScript types. They are converted to a number only at
the moment of display, by a formatting function.

Allocation arithmetic runs in `Decimal` with explicit quantisation, and the
residue from rounding is placed on the largest share so the parts always sum to
the whole.

## Alternatives considered

**Integer minor units.** Exact, and the usual answer for currency. It does not
fit here: the platform handles many currencies with different minor-unit
conventions, and model outputs are not naturally denominated in minor units.

**JSON numbers with a documented precision limit.** Defers the problem to
whoever first analyses a large portfolio, and they will not know it happened.

**A JSON number parser that produces decimals in the browser.** Solves it, at
the cost of a non-standard parse on every response.

## Consequences

TIV reconciliation is exact, and the tests assert equality rather than
approximate equality.

The cost is that the frontend cannot do arithmetic on a monetary value without
deliberate conversion. That is the intended friction: arithmetic on money
belongs on the server, where it is `Decimal` and where the result is audited.

## Revisit if

The interface needs client-side aggregation over monetary values. A decimal
library in the browser is preferable to relaxing this.
