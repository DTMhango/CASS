# 11. Portfolios arrive through the CASS intake template, joined on Policy ID

Status: Accepted, amended by [ADR 23](0023-the-template-carries-the-financial-structure.md)
Date: 2026-09-13

## Context

The geocoded portfolio brief was written around the 30 June 2026 two-sheet
extract. Policies and risk locations were separate sheets, the join had to be
inferred from `business_id`, and two businesses with more than one policy row
made that inference a standing hazard. The brief also asked that insured,
cedent and broker names be shown only to roles that needed them, and CASS had
extended that gate to risk addresses.

## Decision

Portfolios arrive as a CASS intake template: a Risks sheet and a Policies
sheet, generated from the intake profile, with every column bound to an OED
field and a worked example row that the reader always drops.

- **The join is stated rather than inferred.** Both sheets carry the Policy ID
  as the premium system writes it, underwriting year, inception month and
  business reference: `2026_06_PFAC8716`. A business reference alone does not
  say which year's placement a risk belongs to.
- **Value is read in three tiers**, and the exposure version counts risks in
  each: stated coverages kept as stated; a stated total split under a named
  component assumption; or a policy total divided under a named allocation
  scenario.
- **Storeys are collected**, because height decides which intensity measure a
  class responds at.
- **The 30 June extract was migrated once** through the legacy reader, which
  now serves only that migration.
- **No role-based data classification.** Every CASS user sees portfolio data.
  Sensitivity governs only what leaves the platform: logs and support bundles
  carry identifiers and codes, not portfolio rows.
- **An exposure version no longer records a cedant.** Nothing read the field.

## Alternatives considered

**Keep importing the extract format.** The join inference stays a hazard, and
every new source needs its own reader.

**Keep the role gate.** It stopped the modeller doing geocoding review from
reading the address the coordinate is checked against.

## Consequences

Brief section 4.1 item 5 and the join rules of section 4.3 are superseded for
new imports. They still describe the one-off migration.

Plan text that segments by cedant — keys coverage reports, summary groups — is
superseded. Segment by portfolio, account, policy, country and occupancy
instead.

## Revisit if

KRE introduces users who must not see counterparty data, or a loading API
replaces the workbook.
