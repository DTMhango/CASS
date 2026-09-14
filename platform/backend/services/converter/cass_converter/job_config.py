"""An OpenQuake job configuration, as something a person can edit safely.

A published PSHA model arrives as a directory of NRML and a ``job.ini``, and
the file is the interface. That is fine for a seismologist at a terminal and
poor for everyone else: the settings that decide whether a result is usable sit
in the same flat list as the ones that decide how long it takes, nothing says
which is which, and a typo in a key name is silently ignored rather than
refused.

So this parses the file into typed parameters that know three things the file
does not.

**What a parameter is for.** Every one carries a label and an explanation in
the terms of the decision it affects, not in the terms of the engine.

**What changing it costs.** ``ps_grid_spacing`` makes a national calculation
finish in an afternoon and coarsens the near-field; ``truncation_level``
decides whether the tail of the ground-motion distribution exists at all.
Neither of those is visible in a number, and both are recorded here.

**Whether it is the operator's to change.** The parameters divide into three
kinds and conflating them is how a model gets quietly altered:

* the *model's own science* -- the logic trees, the source files, the
  equivalent-distance lookups. Editing these does not configure the model, it
  replaces it, so they are shown and not offered;
* *scientific choices the run makes* -- investigation time, event sets,
  truncation, maximum distance. Genuinely the operator's, and each states its
  consequence;
* *discretisation* -- mesh spacings and grid collapsing, which trade runtime
  against accuracy and nothing else.

The other job here is the conversion CASS actually needs. A published model is
almost always ``classical``: it produces hazard curves, which are the right
answer for a building code and the wrong one for a catastrophe model. Oasis
needs a footprint, and a footprint needs stochastic event sets and ground
motion fields -- ``event_based``. ``to_event_based`` performs that conversion
and returns what it changed, so the difference between the published
configuration and the one that ran is a list a reviewer can read rather than a
diff of two ini files.
"""

from __future__ import annotations

import ast
import dataclasses
import enum
import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

#: Sections in the order OpenQuake conventionally writes them. A section not
#: listed keeps its position after these.
SECTION_ORDER = (
    "general",
    "geometry",
    "logic_tree",
    "erf",
    "site_params",
    "calculation",
    "output",
)

#: Calculation modes CASS understands. Others are read and preserved, but the
#: platform cannot make a footprint from them and says so.
CLASSICAL = "classical"
EVENT_BASED = "event_based"

#: What an Oasis footprint needs, whatever else the configuration says.
FOOTPRINT_REQUIREMENTS = (
    "calculation_mode must be event_based",
    "ground_motion_fields must be true",
    "the intensity measures the vulnerability set demands must be produced",
)


class JobConfigError(Exception):
    """Raised when a configuration cannot be read or would not run."""


class Editability(enum.StrEnum):
    """Who a parameter belongs to."""

    MODEL = "model"
    """The model's own science. Shown, never offered for editing: changing it
    does not configure this model, it makes a different one."""

    SCIENCE = "science"
    """A scientific choice the run makes. The operator's, and consequential."""

    DISCRETISATION = "discretisation"
    """Trades runtime against accuracy, and nothing else."""

    OUTPUT = "output"
    """What the calculation writes. Cheap to change, cheap to get wrong."""


class Kind(enum.StrEnum):
    """How a value is typed for an editor."""

    TEXT = "text"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    CHOICE = "choice"
    MEASURE_LIST = "measure_list"
    MAPPING = "mapping"


@dataclasses.dataclass(frozen=True, slots=True)
class Parameter:
    """One setting, with what it means and what changing it costs."""

    name: str
    section: str
    kind: Kind
    label: str
    editability: Editability
    help_text: str = ""
    #: What moves in the answer when this moves. Empty only where nothing does.
    consequence: str = ""
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""
    #: Set where a footprint cannot be produced without this having a
    #: particular value, so an editor can show the requirement rather than
    #: letting a run fail four hours in.
    required_for_footprint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "section": self.section,
            "kind": str(self.kind),
            "label": self.label,
            "editability": str(self.editability),
            "help_text": self.help_text,
            "consequence": self.consequence,
            "choices": list(self.choices),
            "minimum": self.minimum,
            "maximum": self.maximum,
            "unit": self.unit,
            "required_for_footprint": self.required_for_footprint,
        }


def _p(name: str, section: str, kind: Kind, label: str, editability: Editability, **kw):
    return Parameter(
        name=name, section=section, kind=kind, label=label, editability=editability, **kw
    )


#: The parameters the platform understands. A key not listed here is preserved
#: verbatim and shown as unrecognised -- never dropped, because a published
#: model's configuration may legitimately carry settings this does not model,
#: and silently discarding one would change the science without saying so.
PARAMETERS: tuple[Parameter, ...] = (
    _p(
        "description", "general", Kind.TEXT, "Description", Editability.OUTPUT,
        help_text="Free text carried onto every output of this calculation.",
    ),
    _p(
        "calculation_mode", "general", Kind.CHOICE, "Calculation mode",
        Editability.SCIENCE,
        choices=(CLASSICAL, EVENT_BASED),
        help_text=(
            "Classical produces hazard curves -- the probability of exceeding a "
            "ground motion at a site. Event-based produces a set of simulated "
            "earthquakes and the shaking each caused."
        ),
        consequence=(
            "A catastrophe model needs events, not curves: an Oasis footprint is "
            "a table of what each event did at each cell, and a hazard curve "
            "carries no events at all. Published national models are almost "
            "always classical, because they exist to support a building code."
        ),
        required_for_footprint=EVENT_BASED,
    ),
    _p(
        "random_seed", "general", Kind.INTEGER, "Random seed", Editability.SCIENCE,
        help_text="Fixes the sampling, so the same configuration gives the same events.",
        consequence=(
            "Two runs with different seeds produce different event sets and "
            "different losses. Recording it is what makes a result reproducible."
        ),
    ),
    _p(
        "sites_csv", "geometry", Kind.TEXT, "Sites file", Editability.SCIENCE,
        help_text="Where ground motion is calculated.",
        consequence=(
            "A model ships with its own site grid, usually a regular national "
            "mesh. CASS replaces it with the area-peril grid, because a "
            "footprint has to name cells the keys service can map a risk to."
        ),
    ),
    _p(
        "site_model_file", "geometry", Kind.TEXT, "Site model", Editability.SCIENCE,
        help_text=(
            "Measured site conditions -- Vs30 and basin depths -- at the model's "
            "own points. Each calculation site takes the values of the nearest."
        ),
        consequence=(
            "This is how a footprint stops assuming uniform rock. A published "
            "national model usually ships one, and using it is the difference "
            "between a Jakarta site modelled on its actual soft-soil Vs30 and "
            "one modelled at the reference rock value the ground-motion models "
            "were calibrated against."
        ),
    ),
    _p(
        "number_of_logic_tree_samples", "logic_tree", Kind.INTEGER,
        "Logic-tree samples", Editability.SCIENCE,
        minimum=0,
        help_text=(
            "Zero enumerates every branch of the logic tree. Any other number "
            "samples that many paths at random, weighted."
        ),
        consequence=(
            "Sampled paths are drawn in proportion to their weights, so a "
            "catalogue pooling several of them carries the model's own "
            "weighting and covers that many times the simulated years. One path "
            "is one view: measured against the model's weighted mean it sat "
            "between 0.84 and 1.24 of it depending on the draw, and twenty paths "
            "cost the same to run. Full enumeration is refused, because its "
            "branches carry unequal weights (ADR 18)."
        ),
        required_for_footprint="one or more sampled paths, never enumeration",
    ),
    _p(
        "investigation_time", "calculation", Kind.NUMBER, "Investigation time",
        Editability.SCIENCE, unit="years", minimum=0,
        help_text="The period one stochastic event set represents.",
        consequence=(
            "Multiplied by the event set count, this is the effective time and "
            "therefore the number of Oasis periods. Every annual rate and every "
            "AAL is divided by it, so an error here scales the whole answer."
        ),
    ),
    _p(
        "ses_per_logic_tree_path", "calculation", Kind.INTEGER,
        "Stochastic event sets", Editability.SCIENCE, minimum=1,
        help_text="How many independent simulations of the investigation time to run.",
        consequence=(
            "Investigation time times this is the years the event set covers. "
            "More gives a better-sampled tail and costs proportionally more "
            "time; too few and the rare events that drive a reinsurance loss "
            "may simply not occur."
        ),
    ),
    _p(
        "truncation_level", "calculation", Kind.NUMBER, "Truncation level",
        Editability.SCIENCE, unit="σ", minimum=0,
        help_text=(
            "How many standard deviations of ground-motion variability are kept."
        ),
        consequence=(
            "This is the tail. Truncating at 3σ removes the strongest shaking a "
            "ground-motion model predicts, which for a reinsurer is the part "
            "that matters; published models often use 5."
        ),
    ),
    _p(
        "maximum_distance", "calculation", Kind.MAPPING, "Maximum distance",
        Editability.SCIENCE, unit="km",
        help_text=(
            "How far from a site a rupture is still considered. May be one "
            "number, or one per tectonic region."
        ),
        consequence=(
            "Cutting it short drops distant ruptures entirely. Subduction "
            "interface events are damaging at far greater distances than "
            "crustal ones, which is why a national model usually sets a much "
            "larger value for them -- lowering it to save time removes the "
            "megathrust."
        ),
    ),
    _p(
        "intensity_measure_types", "calculation", Kind.MEASURE_LIST,
        "Intensity measures", Editability.SCIENCE,
        help_text="Which ground-motion measures the calculation produces.",
        consequence=(
            "A vulnerability function built for one spectral period cannot be "
            "answered by another. A measure left out here is a set of functions "
            "that will report no loss."
        ),
        required_for_footprint="every measure the vulnerability set demands",
    ),
    _p(
        "intensity_measure_types_and_levels", "calculation", Kind.MAPPING,
        "Intensity measures and levels", Editability.SCIENCE,
        help_text=(
            "Classical calculations need the levels at which to evaluate each "
            "hazard curve. Event-based does not, and ignores them."
        ),
        consequence=(
            "Carried over from the published classical configuration. Converting "
            "to event-based replaces this with the plain measure list."
        ),
    ),
    _p(
        "minimum_intensity", "calculation", Kind.MAPPING, "Minimum intensity",
        Editability.SCIENCE,
        help_text="Ground motion below this is not written out.",
        consequence=(
            "Shaking too weak to damage anything, discarded at the engine rather "
            "than carried through the conversion and dropped there. Set it above "
            "the floor of the intensity-bin dictionary and real hazard goes "
            "missing; set it far below and the export grows without bound."
        ),
    ),
    _p(
        "reference_vs30_value", "site_params", Kind.NUMBER, "Reference Vs30",
        Editability.SCIENCE, unit="m/s", minimum=0,
        help_text="Site stiffness used where the site file does not give one.",
        consequence=(
            "Ground-motion models are calibrated against it. A national model's "
            "reference is usually rock, around 760 to 800; soft soil amplifies "
            "shaking substantially, and using a rock reference over a "
            "sedimentary basin understates the loss there."
        ),
    ),
    _p(
        "reference_depth_to_1pt0km_per_sec", "site_params", Kind.NUMBER,
        "Depth to Vs 1.0 km/s", Editability.SCIENCE, unit="m",
        help_text="Basin depth term some ground-motion models take.",
        consequence=(
            "Deep sedimentary basins amplify long-period shaking, which is what "
            "tall buildings respond to. Published models often write -999 to mean "
            "'not specified' and let each ground-motion model infer it."
        ),
    ),
    _p(
        "reference_depth_to_2pt5km_per_sec", "site_params", Kind.NUMBER,
        "Depth to Vs 2.5 km/s", Editability.SCIENCE, unit="km",
        help_text="The deeper basin term, used by a different family of models.",
        consequence="As above, for the models that take the 2.5 km/s depth.",
    ),
    _p(
        "reference_vs30_type", "site_params", Kind.CHOICE, "Vs30 basis",
        Editability.SCIENCE, choices=("measured", "inferred"),
        help_text="Whether the reference value is measured or inferred.",
        consequence="Some ground-motion models carry a different variance for each.",
    ),
    _p(
        "rupture_mesh_spacing", "erf", Kind.NUMBER, "Rupture mesh spacing",
        Editability.DISCRETISATION, unit="km", minimum=0,
        help_text="How finely a fault surface is discretised.",
        consequence="Finer is more accurate near the fault and costs more time.",
    ),
    _p(
        "area_source_discretization", "erf", Kind.NUMBER,
        "Area source discretisation", Editability.DISCRETISATION, unit="km",
        minimum=0,
        help_text="How finely an area source is turned into point sources.",
        consequence="Finer is more accurate close to the source and costs more time.",
    ),
    _p(
        "complex_fault_mesh_spacing", "erf", Kind.NUMBER,
        "Complex fault mesh spacing", Editability.DISCRETISATION, unit="km",
        minimum=0,
        help_text="Mesh spacing for subduction interface geometries.",
        consequence="Finer resolves the megathrust surface better and costs more time.",
    ),
    _p(
        "width_of_mfd_bin", "erf", Kind.NUMBER, "Magnitude bin width",
        Editability.DISCRETISATION, minimum=0,
        help_text="How finely the magnitude-frequency distribution is discretised.",
        consequence=(
            "Coarser bins group magnitudes together, which matters most at the "
            "top of the distribution where few events sit."
        ),
    ),
    _p(
        "ps_grid_spacing", "calculation", Kind.NUMBER, "Point-source collapsing",
        Editability.DISCRETISATION, unit="km", minimum=0,
        help_text="Collapses nearby point sources onto a coarser grid.",
        consequence=(
            "The largest runtime lever a national model has, and it coarsens the "
            "near field: sites close to gridded seismicity get the collapsed "
            "source's distance rather than their own."
        ),
    ),
    _p(
        "source_model_logic_tree_file", "calculation", Kind.TEXT,
        "Source model logic tree", Editability.MODEL,
        help_text="The seismic sources and their alternatives.",
        consequence="This is the model. Replacing it is not configuration.",
    ),
    _p(
        "gsim_logic_tree_file", "calculation", Kind.TEXT,
        "Ground-motion logic tree", Editability.MODEL,
        help_text="Which ground-motion models apply to each tectonic region.",
        consequence="This is the model. Replacing it is not configuration.",
    ),
    _p(
        "reqv_file", "calculation", Kind.MAPPING, "Equivalent-distance lookups",
        Editability.MODEL,
        help_text="Precomputed distances for collapsed gridded seismicity.",
        consequence="Part of how the model represents its gridded sources.",
    ),
    _p(
        "ground_motion_fields", "output", Kind.BOOLEAN, "Write ground motion fields",
        Editability.OUTPUT,
        help_text="Whether the shaking at each site for each event is exported.",
        consequence=(
            "The footprint is built from these. Without them an event-based run "
            "completes and produces nothing CASS can convert."
        ),
        required_for_footprint="true",
    ),
    _p(
        "export_dir", "output", Kind.TEXT, "Export directory", Editability.OUTPUT,
        help_text="Where the engine writes its CSV exports.",
        consequence="A job naming none runs and exports nothing.",
    ),
)

PARAMETERS_BY_NAME: Mapping[str, Parameter] = {item.name: item for item in PARAMETERS}

#: The value published models use to mean "not specified". Classical accepts
#: it; event-based validates the same fields as positive and refuses. The
#: honest conversion is to remove the key, because absence is what the sentinel
#: was standing in for -- writing zero instead would assert a basin depth of
#: nothing, which is a statement about the site rather than about our ignorance.
NOT_SPECIFIED_SENTINELS = ("-999", "-999.0", "-999.00")

#: Fields where that sentinel appears.
SENTINEL_FIELDS = (
    "reference_depth_to_1pt0km_per_sec",
    "reference_depth_to_2pt5km_per_sec",
)

#: Classical-only settings, removed when a configuration is converted. Kept as
#: a named list so the conversion can say what it dropped rather than leaving a
#: reader to diff two files.
CLASSICAL_ONLY = (
    "intensity_measure_types_and_levels",
    "poes",
    "mean_hazard_curves",
    "quantile_hazard_curves",
    "hazard_maps",
    "uniform_hazard_spectra",
    "individual_curves",
    "hazard_curves_from_gmfs",
)


@dataclasses.dataclass(frozen=True, slots=True)
class Setting:
    """One key as it stands in the file, with the parameter that describes it."""

    name: str
    section: str
    value: str
    parameter: Parameter | None = None

    @property
    def recognised(self) -> bool:
        return self.parameter is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "section": self.section,
            "value": self.value,
            "recognised": self.recognised,
            "parameter": self.parameter.as_dict() if self.parameter else None,
        }


def _parse_ini(text: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """Read an ini into ordered sections of ordered key-value pairs.

    Hand-rolled rather than ``configparser`` for one reason: OpenQuake values
    contain ``%`` and ``:`` and span lines, and configparser's interpolation
    and delimiter handling mangle them. Order is preserved so a rendered file
    reads like the one that was uploaded.
    """
    sections: list[tuple[str, list[tuple[str, str]]]] = []
    current: list[tuple[str, str]] | None = None
    pending: str | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped or stripped.startswith(("#", ";")):
            continue

        if stripped.startswith("[") and stripped.endswith("]"):
            current = []
            sections.append((stripped[1:-1].strip(), current))
            pending = None
            continue

        if current is None:
            raise JobConfigError(
                f"{stripped[:40]!r} appears before any [section]. An OpenQuake "
                "job file has to open with one, usually [general]."
            )

        # A continuation line: indented, and no '=' of its own.
        if pending is not None and (raw[:1] in " \t") and "=" not in stripped:
            current[-1] = (pending, current[-1][1] + " " + stripped)
            continue

        if "=" not in stripped:
            raise JobConfigError(
                f"{stripped[:40]!r} is neither a section nor a key = value line."
            )

        key, _, value = stripped.partition("=")
        key = key.strip()
        pending = key
        current.append((key, value.strip()))

    if not sections:
        raise JobConfigError("This file defines no sections, so it is not a job.ini.")
    return sections


@dataclasses.dataclass(frozen=True, slots=True)
class JobConfig:
    """A parsed job configuration: every key, in order, with what it means."""

    settings: tuple[Setting, ...]
    source_name: str = ""
    checksum: str = ""

    # -- reading ---------------------------------------------------------------

    @classmethod
    def parse(cls, text: str | bytes, *, source_name: str = "job.ini") -> JobConfig:
        payload = text.encode("utf-8") if isinstance(text, str) else text
        decoded = payload.decode("utf-8-sig")

        settings: list[Setting] = []
        for section, pairs in _parse_ini(decoded):
            for key, value in pairs:
                settings.append(
                    Setting(
                        name=key,
                        section=section,
                        value=value,
                        parameter=PARAMETERS_BY_NAME.get(key),
                    )
                )
        if not settings:
            raise JobConfigError("This job file sets nothing.")

        return cls(
            settings=tuple(settings),
            source_name=source_name,
            checksum=hashlib.sha256(payload).hexdigest(),
        )

    def get(self, name: str) -> str | None:
        for item in self.settings:
            if item.name == name:
                return item.value
        return None

    def __contains__(self, name: str) -> bool:
        return self.get(name) is not None

    @property
    def mode(self) -> str:
        return (self.get("calculation_mode") or "").strip()

    @property
    def is_event_based(self) -> bool:
        return self.mode == EVENT_BASED

    @property
    def unrecognised(self) -> tuple[str, ...]:
        """Keys the platform does not model. Preserved, never dropped."""
        return tuple(item.name for item in self.settings if not item.recognised)

    @property
    def effective_time(self) -> float | None:
        """Years the event set covers, where the factors are set.

        Three factors, not two: sampled paths are pooled into one catalogue
        whose years run across all of them (ADR 18), so twenty paths of one
        fifty-year set span a thousand years. Zero samples means enumeration,
        which is refused elsewhere and counted as one path here rather than
        collapsing the span to nothing.
        """
        investigation = _number(self.get("investigation_time"))
        sets = _number(self.get("ses_per_logic_tree_path"))
        if investigation is None or sets is None:
            return None
        samples = _number(self.get("number_of_logic_tree_samples"))
        paths = samples if samples is not None and samples >= 1 else 1
        return investigation * sets * paths

    def measures(self) -> tuple[str, ...]:
        """The intensity measures this configuration produces, whichever way
        it states them."""
        plain = self.get("intensity_measure_types")
        if plain:
            return tuple(
                item.strip() for item in plain.split(",") if item.strip()
            )
        levels = self.get("intensity_measure_types_and_levels")
        if levels:
            try:
                return tuple(ast.literal_eval(levels).keys())
            except (ValueError, SyntaxError):
                return ()
        return ()

    # -- editing ---------------------------------------------------------------

    def set(self, name: str, value: Any, *, section: str | None = None) -> JobConfig:
        """A copy with one setting changed or added.

        Editing an unknown key is allowed -- a published model may carry
        settings this does not model, and the operator may need to change one --
        but adding a key the platform does not recognise needs the section
        named, because guessing it would put a calculation parameter in the
        output block where the engine would ignore it.
        """
        rendered = _render_value(value)
        settings = list(self.settings)
        for index, item in enumerate(settings):
            if item.name == name:
                settings[index] = dataclasses.replace(item, value=rendered)
                return dataclasses.replace(self, settings=tuple(settings))

        parameter = PARAMETERS_BY_NAME.get(name)
        target = section or (parameter.section if parameter else None)
        if target is None:
            raise JobConfigError(
                f"{name!r} is not a parameter the platform models and no section "
                "was given for it. Name the section it belongs to."
            )
        settings.append(
            Setting(name=name, section=target, value=rendered, parameter=parameter)
        )
        return dataclasses.replace(self, settings=tuple(settings))

    def update(self, values: Mapping[str, Any]) -> JobConfig:
        config = self
        for name, value in values.items():
            config = config.set(name, value)
        return config

    def remove(self, *names: str) -> JobConfig:
        drop = set(names)
        return dataclasses.replace(
            self, settings=tuple(i for i in self.settings if i.name not in drop)
        )

    # -- writing ---------------------------------------------------------------

    def render(self) -> bytes:
        """The job.ini this configuration describes."""
        grouped: dict[str, list[Setting]] = {}
        for item in self.settings:
            grouped.setdefault(item.section, []).append(item)

        ordered = [name for name in SECTION_ORDER if name in grouped]
        ordered.extend(name for name in grouped if name not in ordered)

        lines: list[str] = []
        for section in ordered:
            lines.append(f"[{section}]")
            lines.extend(f"{item.name} = {item.value}" for item in grouped[section])
            lines.append("")
        return "\n".join(lines).encode("utf-8")

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "checksum": self.checksum,
            "calculation_mode": self.mode,
            "is_event_based": self.is_event_based,
            "intensity_measures": list(self.measures()),
            "effective_time": self.effective_time,
            "unrecognised": list(self.unrecognised),
            "settings": [item.as_dict() for item in self.settings],
        }


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return None


def _render_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        return ", ".join(str(item) for item in value)
    if isinstance(value, Mapping):
        return "{" + ", ".join(f"{k!r}: {v}" for k, v in value.items()) + "}"
    return str(value)


# -- validation ----------------------------------------------------------------------

@dataclasses.dataclass(frozen=True, slots=True)
class Problem:
    """One reason a configuration would not do what the operator expects."""

    parameter: str
    severity: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "parameter": self.parameter,
            "severity": self.severity,
            "message": self.message,
        }


def validate(config: JobConfig, *, required_measures: Sequence[str] = ()) -> list[Problem]:
    """Everything wrong with a configuration, before it costs anybody hours.

    Returns problems rather than raising, so an editor can show all of them at
    once. ``error`` means the run cannot produce a usable footprint; ``warning``
    means it can, and somebody should know.
    """
    problems: list[Problem] = []

    if not config.mode:
        problems.append(
            Problem("calculation_mode", "error", "No calculation mode is set.")
        )
    elif not config.is_event_based:
        problems.append(
            Problem(
                "calculation_mode",
                "error",
                f"This is a {config.mode} calculation, which produces hazard "
                "curves rather than events. An Oasis footprint needs event_based.",
            )
        )

    samples = config.get("number_of_logic_tree_samples")
    if samples is not None:
        stated = samples.strip()
        if stated == "0":
            problems.append(
                Problem(
                    "number_of_logic_tree_samples",
                    "error",
                    "Full enumeration produces every branch of the logic tree, "
                    "each carrying its own weight. Pooling them into one event "
                    "set would treat a low-weight branch as the model's mean "
                    "(ADR 18). Sample paths instead: they are drawn in "
                    "proportion to their weights, so the catalogue carries the "
                    "weighting already.",
                )
            )
        elif not stated.isdigit():
            problems.append(
                Problem(
                    "number_of_logic_tree_samples",
                    "error",
                    f"{stated!r} is not a number of paths to sample.",
                )
            )
        elif stated == "1":
            problems.append(
                Problem(
                    "number_of_logic_tree_samples",
                    "warning",
                    "One sampled path is one view of the logic tree. Measured "
                    "against the model's own weighted mean, a single path's "
                    "hazard sat between 0.84 and 1.24 of it depending on which "
                    "path was drawn, and sampling twenty cost the same to run "
                    "(ADR 18).",
                )
            )

    if config.is_event_based:
        fields = (config.get("ground_motion_fields") or "").strip().lower()
        if fields not in ("true", "1", "yes"):
            problems.append(
                Problem(
                    "ground_motion_fields",
                    "error",
                    "Ground motion fields are not being written, so this run "
                    "would complete and produce nothing to convert.",
                )
            )
        if config.get("ses_per_logic_tree_path") is None:
            problems.append(
                Problem(
                    "ses_per_logic_tree_path",
                    "error",
                    "No stochastic event set count is set, so the effective time "
                    "the events represent is undefined.",
                )
            )

    investigation = _number(config.get("investigation_time"))
    if investigation is None or investigation <= 0:
        problems.append(
            Problem(
                "investigation_time",
                "error",
                "Investigation time must be positive; it is the denominator of "
                "every annual rate derived from this run.",
            )
        )

    effective = config.effective_time
    if effective is not None and effective < 1000:
        problems.append(
            Problem(
                "ses_per_logic_tree_path",
                "warning",
                f"The event set covers {effective:.0f} years. A return period "
                "cannot be read beyond the span simulated, and a reinsurance "
                "view usually needs several thousand.",
            )
        )

    truncation = _number(config.get("truncation_level"))
    if truncation is not None and 0 < truncation < 3:
        problems.append(
            Problem(
                "truncation_level",
                "warning",
                f"Truncating ground-motion variability at {truncation:g} sigma "
                "removes the strongest shaking the models predict, which is the "
                "part a reinsurance loss is made of.",
            )
        )

    produced = set(config.measures())
    missing = sorted(set(required_measures) - produced)
    if missing:
        problems.append(
            Problem(
                "intensity_measure_types",
                "error",
                f"The vulnerability set demands {', '.join(missing)}, which this "
                "calculation does not produce. Those functions would be answered "
                "by nothing and report no loss.",
            )
        )

    # Three ways to say where to calculate, and a site model is the one CASS
    # uses -- it carries the coordinates and the conditions together.
    if not any(config.get(name) for name in ("site_model_file", "sites_csv", "region")):
        problems.append(
            Problem(
                "sites_csv",
                "error",
                "No sites are defined, so the calculation has nowhere to compute "
                "ground motion. Give it a site model, a sites file or a region.",
            )
        )
    if config.get("site_model_file") and config.get("sites_csv"):
        problems.append(
            Problem(
                "sites_csv",
                "error",
                "Both a site model and a sites file are given. They are two "
                "possibly disagreeing lists of where to calculate, and the engine "
                "refuses rather than choosing between them.",
            )
        )

    return problems


# -- the conversion CASS needs ---------------------------------------------------------

@dataclasses.dataclass(frozen=True, slots=True)
class Conversion:
    """A converted configuration, and every change made to get there."""

    config: JobConfig
    changes: tuple[tuple[str, str, str], ...]
    removed: tuple[str, ...]
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "changes": [
                {"parameter": name, "from": before, "to": after}
                for name, before, after in self.changes
            ],
            "removed": list(self.removed),
            "notes": list(self.notes),
            "config": self.config.as_dict(),
        }


def to_event_based(
    config: JobConfig,
    *,
    measures: Sequence[str],
    investigation_time: float = 50.0,
    # Twenty paths of one event set each: a thousand simulated years drawn
    # across twenty views of the logic tree rather than one (ADR 18).
    ses_per_logic_tree_path: int = 1,
    logic_tree_samples: int = 20,
    minimum_intensity: float | None = None,
    sites_file: str = "sites.csv",
    site_model_file: str | None = None,
    random_seed: int | None = None,
    export_dir: str = "out",
    keep_truncation: bool = True,
) -> Conversion:
    """Turn a published classical configuration into one CASS can convert.

    Everything the model itself decides is left exactly as it was -- the logic
    trees, the source files, the equivalent-distance lookups, the maximum
    distances per tectonic region, the discretisation. What changes is the kind
    of answer asked for and the sites it is asked at, and every change is
    returned so a reviewer reads a list rather than diffing two files.
    """
    changes: list[tuple[str, str, str]] = []
    notes: list[str] = []

    def apply(target: JobConfig, name: str, value: Any) -> JobConfig:
        before = target.get(name)
        rendered = _render_value(value)
        if before == rendered:
            return target
        changes.append((name, before if before is not None else "", rendered))
        return target.set(name, value)

    working = config
    working = apply(working, "calculation_mode", EVENT_BASED)
    working = apply(working, "number_of_logic_tree_samples", logic_tree_samples)
    working = apply(working, "investigation_time", investigation_time)
    working = apply(working, "ses_per_logic_tree_path", ses_per_logic_tree_path)
    if site_model_file:
        # A site model carries its own coordinates, so a separate sites file
        # would be a second, possibly disagreeing, list of where to calculate.
        # Engine 3.23 refuses both rather than choosing, which is right.
        working = apply(working, "site_model_file", site_model_file)
        if "sites_csv" in working:
            changes.append(("sites_csv", working.get("sites_csv") or "", ""))
            working = working.remove("sites_csv")
        notes.append(
            f"Sites come from {site_model_file}, which carries both the "
            "coordinates and the site conditions at each. A separate sites file "
            "would be a second list of where to calculate."
        )
    else:
        working = apply(working, "sites_csv", sites_file)
    working = apply(working, "ground_motion_fields", True)
    working = apply(working, "export_dir", export_dir)
    working = apply(working, "intensity_measure_types", list(measures))
    if random_seed is not None:
        working = apply(working, "random_seed", random_seed)
    if minimum_intensity is not None:
        working = apply(working, "minimum_intensity", minimum_intensity)

    present = [name for name in CLASSICAL_ONLY if name in working]

    # A published model writes -999 for a site parameter it does not specify.
    # Classical takes it; event-based validates the field as positive and
    # refuses to start. Removing the key is not enough on its own -- the
    # ground-motion models then have no basin depth at all and the run refuses
    # for the opposite reason -- so a site model has to supply them.
    sentinels = [
        name
        for name in SENTINEL_FIELDS
        if (working.get(name) or "").strip() in NOT_SPECIFIED_SENTINELS
    ]
    if sentinels:
        present.extend(sentinels)
        if site_model_file:
            notes.append(
                "Removed "
                + ", ".join(sentinels)
                + ", which the model set to -999 meaning 'not specified'. An "
                "event-based run validates these as positive and refuses to "
                f"start. {site_model_file} supplies them per site instead, which "
                "is what the published calculation was relying on."
            )
        else:
            notes.append(
                "Removed "
                + ", ".join(sentinels)
                + " -- the model's 'not specified' sentinel, which an "
                "event-based run rejects. No site model was supplied to replace "
                "them, so every site falls back to the reference values and the "
                "run may refuse if a ground-motion model needs a basin depth."
            )

    working = working.remove(*present)

    notes.append(
        "The source and ground-motion logic trees, the source files, the maximum "
        "distances and the discretisation are unchanged. What changed is the kind "
        "of answer asked for and the sites it is asked at."
    )
    if present:
        notes.append(
            "Removed as meaningless in an event-based run: "
            + ", ".join(present)
            + "."
        )
    notes.append(
        "One logic-tree path is sampled, so this is one realisation out of the "
        "model's full tree rather than its weighted mean. That is a limitation "
        "of the event set, not a defect of the run, and it is why the multi-IMT "
        "and realisation-weighting decisions matter."
    )
    if keep_truncation and config.get("truncation_level"):
        notes.append(
            f"Truncation stays at the model's own {config.get('truncation_level')} "
            "sigma rather than being lowered to a common default."
        )

    return Conversion(
        config=working,
        changes=tuple(changes),
        removed=tuple(present),
        notes=tuple(notes),
    )


# -- what the logic trees will cost ------------------------------------------------------

_BRANCH = re.compile(r"<logicTreeBranch\b")
_BRANCHSET = re.compile(r"<logicTreeBranchSet\b[^>]*>")
_TRT = re.compile(r'applyToTectonicRegionType="([^"]*)"')


def logic_tree_summary(
    source_model_tree: str, gsim_tree: str
) -> dict[str, Any]:
    """How many realisations a full enumeration of these trees would produce.

    Counted before the run, because the number decides whether a configuration
    can produce a footprint at all: full enumeration of a national model is
    hundreds of alternative views of the hazard, and a footprint is one event
    set. Finding that out from a failed conversion after several hours is the
    expensive way.

    Read by counting branches per branch set rather than by building the tree,
    which is approximate for trees using conditional branch sets -- so it is
    reported as an estimate and named as one.
    """
    def sets(text: str) -> list[tuple[str, int]]:
        found: list[tuple[str, int]] = []
        parts = _BRANCHSET.split(text)
        headers = _BRANCHSET.findall(text)
        for header, body in zip(headers, parts[1:], strict=False):
            trt = _TRT.search(header)
            found.append((trt.group(1) if trt else "", len(_BRANCH.findall(body))))
        return found

    source_sets = sets(source_model_tree)
    gsim_sets = sets(gsim_tree)

    total = 1
    for _, count in [*source_sets, *gsim_sets]:
        total *= max(count, 1)

    return {
        "source_branch_sets": [
            {"tectonic_region": trt, "branches": count} for trt, count in source_sets
        ],
        "gsim_branch_sets": [
            {"tectonic_region": trt, "branches": count} for trt, count in gsim_sets
        ],
        "tectonic_regions": sorted(
            {trt for trt, _ in gsim_sets if trt}
        ),
        "estimated_realizations": total,
        "note": (
            "An estimate: branches are multiplied across branch sets, which is "
            "exact for an independent tree and an upper bound for one using "
            "conditional branch sets. A footprint needs one realisation, so any "
            "number above one means the configuration has to sample."
        ),
    }


def describe(config: JobConfig, *, required_measures: Sequence[str] = ()) -> dict[str, Any]:
    """A configuration, its problems and what it would produce."""
    problems = validate(config, required_measures=required_measures)
    return {
        **config.as_dict(),
        "problems": [item.as_dict() for item in problems],
        "runnable": not any(item.severity == "error" for item in problems),
        "footprint_requirements": list(FOOTPRINT_REQUIREMENTS),
        "editable": {
            str(kind): [
                item.name
                for item in config.settings
                if item.parameter and item.parameter.editability is kind
            ]
            for kind in Editability
        },
    }


def parameter_catalogue() -> list[dict[str, Any]]:
    """Every parameter the platform models, for an editor to render."""
    return [item.as_dict() for item in PARAMETERS]


def unknown_names(config: JobConfig, names: Iterable[str]) -> list[str]:
    """Names an editor tried to set that are neither known nor already present."""
    return sorted(
        name
        for name in names
        if name not in PARAMETERS_BY_NAME and name not in config
    )
