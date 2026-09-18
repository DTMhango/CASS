# 23. The intake template carries the financial structure, and promotion writes it

Status: Accepted, amended by [ADR 24](0024-limited-cover-computed-beside-the-engine.md)
Date: 2026-09-18

## Context

ADR 11 made the intake template the way a portfolio arrives. Its Policies sheet
asked for layers, signed shares, deductibles and limits, and its Risks sheet for
a risk's own deductible and limit. The reader parsed and staged all of them.
Promotion then wrote the OED location file and nothing else, so a portfolio
brought in through the template could only ever be modelled at ground-up loss,
however completely its policy terms were filled in. Nothing said so. To get an
insured loss, a user had to correct the published version and type every term
again into the Financial structure screen.

The template had no place for reinsurance at all, and no example of a layered
programme.

## Decision

- **Four sheets.** Risks and Policies as before, plus Reinsurance contracts (one
  row per contract layer) and Reinsurance scope (what each contract covers,
  once per contract, joined on the contract number as OED joins it). Their
  example rows show a quota share on one policy and a two-layer catastrophe
  excess of loss over the whole portfolio. The profile is 1.1.0; a 1.0.0
  workbook still reads, as a portfolio without reinsurance.
- **Promotion writes what the workbook states.** An OED account file with one
  row per policy layer, the risks' own deductibles and limits in the location
  file, and the two reinsurance files. A term the workbook leaves blank is never
  filled in, including one stated on another layer of the same policy.
- **Every account is written, and a missing limit is flagged rather than
  dropped.** A blank layer limit is written blank, which OED reads as no limit,
  and a blank attachment as 0; an account the Policies sheet omits gets one row
  of no terms. Such a policy's insured loss is its ground-up loss, less any
  deductible it states. Every one is listed on the version as possibly
  overstated, with the insured value behind it, and the list travels with every
  insured and net-of-reinsurance result. The only other choice is to leave the
  terms out altogether, for a ground-up version.
- **Contracts are held to the Financial structure screen's rules**, through one
  function both use. They are checked when the workbook is imported, so a broken
  contract is a finding before anyone promotes, and again at promotion, which
  refuses rather than writes a programme different from the workbook's.
  Contract numbers and layer numbers are kept as the workbook states them.
- **A layered policy's value is counted once.** Its total insured value may sit
  on the first layer's row or on every row; two different figures are refused.
- **Reinstatement columns, and no aggregate columns.** The Oasis financial
  module applies none of OED's reinstatement or aggregate terms. The
  Reinstatements, Reinstatement rate and Reinstatement premium columns are
  written to OED all the same, because CASS applies them in a run that asks for
  limited cover (ADR 24); each column's help says the engine ignores it. There
  are no aggregate columns, since nothing applies them.

## Alternatives considered

**A separate reinsurance workbook.** Two uploads that must agree on Policy IDs
is the join ADR 11 removed.

**Keep the Financial structure screen as the only route.** It requires a
published version to be corrected and every term typed a second time, and it
has no way to take a hundred policies at once.

**Refuse or drop an account without complete terms.** The first version of
this record did: by default one incomplete account kept the whole version at
ground-up, or the caller could leave incomplete accounts out. Both understate
the book a PML is meant to measure. Writing the blank as OED reads it, and
saying so beside every number it touches, errs the other way, visibly.

## Consequences

A portfolio brought in through the template can be modelled at ground-up,
insured and net-of-reinsurance loss, and its lineage records which accounts
may be overstated for want of a stated term, and why.

Currency and peril are still fixed at USD and QEQ on this route, for the terms
as for the values.

Per-risk and facultative contracts stay refused on this route, as on the screen,
although the engine applies both. Reinsurance results remain whole-portfolio
only (ADR 10).

## Revisit if

The engine gains aggregate or reinstatement terms; per-risk or facultative
contracts are wanted; or a loading API replaces the workbook.
