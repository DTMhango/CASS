"""The two open scientific decisions of build plan section 16, as vocabulary.

These live in ``cass_core`` rather than in the converter because more than one
service has to name them. The converter *makes* the choice and refuses to run
without one; the keys service has to *read* it, because whether a risk that
reaches several intensity measures can be answered at all depends on which
representation was approved. A term two packages both depend on, defined in
one of them, drifts.

Only the vocabulary is here. ``ConversionPolicy`` -- the approval gate, the
blockers, the refusal to run -- stays in ``cass_converter.policy``, because
that is behaviour rather than shared language.
"""

from __future__ import annotations

import enum


class EventIdentity(enum.StrEnum):
    """Candidate mappings from OpenQuake output to an Oasis event.

    Section 6 describes two defensible designs and requires a formal
    specification with worked examples before code depends on one.
    """

    OCCURRENCE_PER_EVENT = "occurrence_per_event"
    """Each simulated occurrence becomes a separate Oasis event."""

    RUPTURE_BINNED = "rupture_binned"
    """Repeated ground-motion samples are aggregated into intensity-bin
    probabilities for a rupture-level event."""

    UNDECIDED = "undecided"
    """No policy has been approved. This is the default state, and it is not
    runnable."""


class IMTRepresentation(enum.StrEnum):
    """Candidate multi-IMT representations, from the section 6 gate.

    A vulnerability class that reaches GEM taxonomies responding at different
    spectral periods cannot be one Oasis function, and there is no choice of
    damage bins that makes it one. These are the ways it could be carried, and
    the point of naming them is that a build has to say which it used.
    """

    CORRELATED_CHANNELS = "correlated_channels"
    """Correlated Oasis sub-peril or intensity-measure channels that retain a
    common event identity and route each vulnerability class to its IMT."""

    CUSTOM_GUL = "custom_gul"
    """A CASS ground-up-loss component consuming multi-IMT OpenQuake output
    directly, preserving the Oasis financial-module boundary."""

    OQ_LOSS_HANDOFF = "oq_loss_handoff"
    """OpenQuake damage or ground-up loss, then an event-loss interface to the
    Oasis financial calculations. Retained as a fallback."""

    COMMON_IMT = "common_imt"
    """Convert every function to one intensity measure.

    Explicitly not an accepted default. Section 6: this "would require a
    separate scientific derivation, validation and approval". It is listed so
    that choosing it is a recorded decision rather than an accident.
    """

    UNDECIDED = "undecided"
    """No representation has been approved.

    A single-channel class is unaffected -- there is nothing to represent --
    so a mapping in this state still routes every risk it can answer without
    the decision. A multi-channel class is refused, because answering it would
    mean picking one of the options above silently.
    """


#: Representations under which a class spanning several intensity measures can
#: be answered. ``CORRELATED_CHANNELS`` carries each measure as its own channel
#: and is the only one that resolves to several functions; the others resolve a
#: class elsewhere -- outside the keys contract -- and so are not listed here.
MULTI_CHANNEL_REPRESENTATIONS: frozenset[IMTRepresentation] = frozenset(
    {IMTRepresentation.CORRELATED_CHANNELS}
)

#: How far a channel's pre-weighted twin sits from the channel's own identifier.
#:
#: Under correlated channels a class spanning several measures reaches the
#: engine as one item per measure, and the engine prices every item at its
#: coverage's whole value. Each item's function therefore carries its measure's
#: share of the class in its damage -- a different function from the channel's
#: own blend, which is kept because it is what the dictionary and the reference
#: comparison trace back to GEM. The build writes the twin here and the lookup
#: inside the engine answers with it, so neither may keep its own copy of the
#: rule.
WEIGHTED_CHANNEL_OFFSET = 1_000_000
