# 21. A hazard run simulates ten thousand years by default, stores only shaking that can cause damage, and bins to wider ceilings

Status: Accepted
Date: 2026-09-17
Amends: [ADR 18](0018-sampled-paths-pooled-as-one-catalogue.md)

## Context

ADR 18 kept a run to a thousand simulated years, drawn along twenty logic-tree
paths. A return-period loss read from a simulated catalogue is an order
statistic: from a thousand years the 1-in-1,000-year loss is the single worst
year, and the delivery notes already recorded the 500- and 1,000-year figures
resting on two events and one.

The hazard models form also sent twenty event sets per path, a default from
before ADR 18, so a run configured there with its defaults simulated twenty
thousand years without saying so.

Longer catalogues and larger regions reach stronger shaking, and motion above
the top intensity bin is clipped, which refuses the set publication.

## Decision

- **A run simulates ten thousand years by default**: fifty-year event sets, ten
  per path, twenty paths. The form sends the same.
- **The configuration states the catalogue before anything runs**: its length,
  the number of simulated years beyond each of the 100-, 250-, 500- and
  1,000-year return periods, and a ceiling on what its hazard and package would
  store. It warns where the hazard could store more than 100 GB.
- **The intensity ceilings are widened by the rule `pilot_bins` already
  recorded** — near double the strongest motion any run has reached — to 12 g
  PGA, 28 g at 0.3 s, 20 g at 0.6 s and 14 g at 1.0 s, with 56 bins rather than
  50 so that no measure's bins are wider than before. The bin version is
  0.2.0-draft.
- **Only shaking of 0.05 g or more is stored.** The floor of every intensity-bin
  dictionary, and the engine's `minimum_intensity`, rise from 0.005 g to 0.05 g,
  which is where every GEM vulnerability function begins. Below a function's
  lowest level CASS and OpenQuake both give no loss, so no loss changes; what
  changes is that the 91% of stored site-events that never reach it are not
  stored.
- **Vulnerability sets record the fingerprint of the bins they were
  discretised against**, as hazard sets now do, and a package refuses a
  vulnerability set or hazard set built against bins other than the current
  ones. Sets registered before the fingerprint are marked with the old
  dictionary's, which they were built with.

## Evidence

Measured on 16 and 17 September 2026 with PuSGeN 2024 on this installation, with
OpenQuake using all 20 cores.

| Run | Engine time | Datastore | Strongest motion |
| --- | --- | --- | --- |
| Jakarta-Bandung, 962 cells, 1,000 years | 121 s | 176 MB | PGA 5.69 g; SA(0.3) 6.59 g |
| Jakarta-Bandung, 962 cells, 5,000 years | 301 s | 891 MB | SA(0.3) 13.29 g |
| Jakarta-Bandung, 962 cells, 10,000 years | 501 s | 1.78 GB | SA(0.3) 14.05 g |
| Java, 4,149 cells, 1,000 years | 161 s | 564 MB | SA(0.3) 13.47 g; SA(0.6) 9.75 g; SA(1.0) 6.84 g |

Binning the 10,000-year datastore into a footprint took 693 s in one process and
wrote 139 million rows, 566 MB compressed. Against the old bins one value was
clipped; the Java run clipped the old 13 g and 9 g ceilings at only 1,000 years.
Against the widened ceilings the same datastore binned with nothing clipped.

Every one of the 73,308 vulnerability functions in GEM v2026.0.0, in 860 files
covering every country and loss type, has its lowest intensity level at exactly
0.05 g. Of the site-events the 0.005 g floor stored, 8.8% (Jakarta-Bandung) and
9.0% (Java) reached 0.05 g on at least one measure, and 3.2% of individual
values did.

Running the same 1,000-year job twice gave the same ground motion to the bit.

With the floor, the same 10,000-year Jakarta-Bandung job run through the platform
on 17 September took 540 seconds from submission to a registered hazard set, and
stored a 233 MB datastore (1.78 GB before), 8.8 million footprint rows (139
million before) in a 38 MB compressed footprint (566 MB before), and a 108 MB
package footprint. Nothing was clipped.

## Alternatives considered

**Keep a thousand years.** Cheapest, and it leaves the tail figures CASS reports
resting on one or two years.

**A hundred thousand years.** About a tenth of the error again, at ten times the
time and storage; at national scale that is terabytes. The form lets a run ask
for it.

**An open-ended top bin.** Would end clipping by construction, but it assumes
every vulnerability function has saturated at the old ceiling, which nothing
checks.

**Hazard computed only where a portfolio's properties are.** Much less storage,
but measured on the Jakarta-Bandung and Java calculations, which share a seed:
the ruptures were identical and the ground motion at the same cell for the same
rupture was not (18 of about 106,000 values matched). A hazard set computed for
one book is therefore a different draw from one computed for another or for the
country, so books could not be added or compared event by event, and every new
book would need its own calculation. The floor keeps national hazard reusable
at about a tenth of the storage.

## Consequences

A hazard run simulates ten times the years. With the floor it stores about a
seventh of what a thousand-year run stored before, and its conversion is far
faster because there is less to bin. The Indonesian seed at ten thousand years has
a ceiling of about 77 GB of hazard and 30 GB of package. A national run should
still be sized on the form before it is saved.

Every hazard set and vulnerability set registered before this change was built
against the old bins. A hazard set's footprint is rebuilt from its stored
calculation (ADR 22); a vulnerability set is built again from its GEM release.
