# 8. Intensity measures are carried as correlated area-peril channels

Status: Accepted
Date: 2026-09-13
Amends: [ADR 4](0004-converter-refuses-unapproved-policy.md)

## Context

The GEM v2026.0.0 vulnerability functions for Indonesia and Nepal respond at
four intensity measures: PGA, SA(0.3), SA(0.6) and SA(1.0). An Oasis footprint
has no intensity-measure dimension — it is event, area peril and intensity bin
— and a vulnerability function reads whatever intensity its area peril names.

Build plan 1.7 narrowed the first converter to the SA family, with PGA deferred,
and listed three candidate multi-IMT representations in section 6: correlated
channels, a custom ground-up component, and an OpenQuake-loss fallback. ADR 4
made the converter refuse to run until a representation was approved.

Two things learned since changed the shape of the question. PGA is not a
minority: the Jakarta–Bandung test book routes most commercial and industrial
classes to it, so an SA-only package leaves much of a real book unmodelled.
And the multi-measure problem is narrower than it looked. Once storeys are
known, most classes resolve to one measure; 56 of the 240 classes per country
in the current build still reach several.

## Decision

The hazard build writes one footprint per measure the hazard set carries, PGA
included. The Oasis package maps each measure to its own channel of a cell,
`area_peril = cell * 10 + channel`, and the lookup routes a vulnerability class
to the channel of the measure its function was built for. Every channel shares
the event identifier, so ground motion across measures stays correlated within
an event exactly as OpenQuake computed it.

This is the correlated-channel representation of section 6, applied only to
classes that resolve to a single measure. A class whose GEM taxonomies respond
at several measures is refused by the lookup, with its value reported, rather
than approximated to whichever channel sorts first.

ADR 4's rule still holds. A package is built only under a converter-candidate
approval decided by someone other than the requester, and the conversion names
its event identity (occurrence per event) and this representation.

## Alternatives considered

**SA only, PGA deferred (plan 1.7).** It leaves PGA-routed exposure with no
function at all, which is most of the value in the test book.

**One common intensity measure.** Still rejected, as section 6 and ADR 4 require.

**A custom ground-up component.** It would cross the Oasis module boundary and
duplicate kernel logic for the minority of classes this does not cover.

## Consequences

`SUPPORTED_IMTS` is all four measures. What limits a model version is its
hazard set: attaching a set that lacks a measure the functions demand is
refused.

Collecting storeys is now the cheapest way to raise modelled value, because
height is what resolves a class to one measure.

The section 6 study is not closed. This is the implemented prototype.

*Measured 13 September 2026.* The OpenQuake reference comparison now exists
(tracker item 9) and has run once, on the Jakarta–Bandung book: the channel
representation produced 0.904 of the engine's own average annual loss on the
same events and the same GEM functions, and between 0.70 and 1.26 at the
reported return periods. One book is not a finding about the representation,
and the candidates have not been measured against each other, which is what
tracker item 21 is for.

The channel arithmetic allows at most ten measures per cell.

## Revisit if

The OpenQuake reference comparison shows the channel representation biased; a
representation for multi-measure classes is approved; or a model needs more than
ten measures.
