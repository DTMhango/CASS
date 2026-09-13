# 13. A product area holds its screens as tabs

Status: Accepted
Date: 2026-09-13

## Context

Build plan section 3 lists nine core screens. The first interface gave most of
them a sidebar entry of their own: model catalogue, hazard models, model build,
exposure workspace and import review sat in one list with the analysis builder,
run monitor and results.

A sidebar reads as a sequence. A modeller moves between running hazard,
building a package and reading the catalogue all day, and an analyst moves
between fixing a portfolio and reviewing an import until the data is right.
Neither is a step before or after the other, and giving each its own entry put
a modeller's workbench in the middle of an analyst's path.

## Decision

- The sidebar keeps the order work happens in: dashboard, exposure, analysis,
  runs, results, models, administration.
- **Exposure** holds Portfolios and Import review as tabs.
- **Models** holds Catalogue, Hazard and Build as tabs. Hazard and Build appear
  only for people who may publish models.
- The chosen tab is in the address, so a link opens on it and the back button
  steps between tabs. The old addresses redirect to their tabs.

## Consequences

Every section 3 screen still exists; some are tabs. The financial structure
workspace belongs in the Exposure area when it is built.

## Revisit if

User acceptance testing shows people cannot find a tab.
