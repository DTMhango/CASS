# 15. CASS is a research tool, and GEM has permitted use of its exposure and vulnerability models

Status: Accepted
Date: 2026-09-13

## Context

Build plan 1.8 described CASS as a governed platform whose results would support
pricing, reserving, capital and underwriting decisions. Much of what is built
follows from that description: platform roles, independent approval gates, the
decision-use run mode, result approval, and a production milestone (M7) with
backup drills, single sign-on, signed releases and separately deployed keys and
converter services.

That was not what CASS is for. It is mainly a research tool, and it is not meant
to support pricing or reserving.

Separately, ADR 7 held all model data under one internal-use basis and left
GEM's written position open. GEM Foundation has now replied to KRE's email of
11 September 2026. It acknowledged that the use KRE described does not violate
its terms of use, and granted explicit permission to use the public Global
Exposure and Vulnerability models as that email described.

## Decision

**CASS is a research tool.** Its results are research outputs and are not a
basis for pricing or reserving.

- What is built stays as built. Roles, approvals, run modes and gates are kept;
  removing them would cost more than keeping them, and they still separate a
  checked result from an unchecked one.
- A decision-use result that a reviewer approves is a reviewed research result.
  Screen text no longer says a result may inform pricing, capital or
  underwriting.
- The remaining work is scoped for research: validating the model against
  reference calculations, sensitivity studies, reading results, and reproducing
  a run.
- Items that serve only a governed production deployment are not pursued:
  - separate keys and converter HTTP services;
  - multi-factor authentication and single sign-on;
  - a restore drill as a release gate;
  - SBOMs and a signed release bundle for approved local installations.
- Three items are kept in a smaller form:
  - backing up the database and the artifact store, so research work is not lost;
  - the CI integration workflow;
  - pinned engine image digests, so a run can be repeated on the same engines.

**GEM's exposure and vulnerability models are used under GEM's permission.**

- `apps.modelregistry.gem.GEM_PERMISSION` records the permission.
- A GEM vulnerability set is cleared under it by default.
- Migration `modelregistry.0009_gem_permission` records it against the sets
  already registered from GEM, including the demonstration seed.
- The licence the data carries, CC BY-NC-SA 4.0, is still recorded beside the
  permission. GEM Foundation must be credited, and a model or derivative that is
  redistributed carries the same licence.

The permission covers those two models and nothing else:

- The PuSGeN 2024 hazard package, and any other hazard source model or
  third-party data, stays under the internal-use basis of ADR 7.
- OpenQuake stays unmodified and under the AGPL. A modified version would have
  to be released under the AGPL.

## Alternatives considered

**Keep building toward a governed decision platform.** It is not what CASS is
for, and the production items would take time from the research the platform
exists to support.

**Remove the governance already built.** Rewriting working roles, approvals and
gates would cost more than keeping them, and they do no harm to research use.

**Record the permission as a new installation-wide basis.** It would clear hazard
source models that GEM's permission does not mention.

## Consequences

- The licence question no longer stands against GEM vulnerability sets, and a
  model version built from them is no longer held back by it.
- The decision-use mode and result approval still work as before.
- An approved result is a reviewed research result. Anybody reading "decision use"
  on a screen should read it that way.
- The delivery tracker's M7 production milestone is not pursued, and backlog
  items 13, 17, 18, 19 and 20 are re-scoped or not pursued.
- Legal review is still needed before model data, or a package derived from it,
  leaves KRE.

## Revisit if

- KRE decides CASS results should support pricing or reserving. The plan's
  governance, production controls and a licence review for that use would all
  return.
- CASS is used in a way the 11 September 2026 email did not describe.
- Model data or a derived package is to leave the company.
- GEM's terms change.
