"""What GEM building is a Klapton Re risk? An assumption, written down.

A schedule says "commercial, reinforced concrete, six storeys, Jakarta". GEM
says ``CR/LFINF/CDM+ERM/H:6/RES`` and forty other things, distinguished by
seismic design level, lateral system and occupancy detail the schedule does not
carry and never will. Something has to bridge that, and the bridge is not a
fact -- it is a prior, and this module is where it is stated so it can be
argued with.

**A risk reaches a mixture, not a function.** This is the design decision the
rest follows from. Where OED pins the building down, the mixture has one member
and nothing is assumed. Where it does not, the candidates consistent with what
the schedule *does* say are carried together, weighted by the share of value
each represents in that country's building stock, and blended into one Oasis
function whose spread is wider than any single candidate's. That width is the
honest representation of not knowing which building it is. Picking the most
common candidate would state something nobody knows; picking the worst would be
prudent and wrong, and would compound across a portfolio into a number no one
could defend.

**The weights come from a published file, not from this module.** GEM's
exposure summaries carry replacement cost by occupancy and material for each
country, and they are not similar between the pilot countries: Nepali
commercial value is 66% non-ductile concrete and 23% unreinforced masonry,
Indonesian commercial is 42% and 6% with a quarter in ductile concrete that
Nepal's commercial stock barely has. A mapping that gave both countries the
same prior would be wrong in a way that a single number could not reveal.

**And the weights describe the wrong population.** GEM's exposure is national
building stock. A facultative reinsurance book is none of that -- it is large,
engineered, urban, and selected by an underwriter. The plan is explicit that
GEM must be a conditional prior calibrated for facultative selection rather
than a description of the insured portfolio, and CASS cannot do that
calibration from anything it currently holds. What it can do is refuse to
pretend otherwise: the default weighting is by replacement cost rather than by
building count, which moves the prior towards larger buildings and is stated
rather than silent; ``minimum_storeys`` and ``weight_overrides`` exist for a
model owner who has better information; and every specification carries the
bias as an open question that survives into the model release. The prior is a
starting point that expects to be replaced by survey and claims experience,
which is what the plan says it is for.

**Unknown occupancy reaches nothing.** OED 1000 is not mapped, deliberately.
A portfolio promoted without a stated or assumed occupancy still fails the
lookup and still holds at the gate, which is the honest answer for exposure
whose use nobody knows. Mapping it to a generic function would make the signal
disappear and the gate stop meaning anything.
"""

from __future__ import annotations

import csv
import dataclasses
import enum
import hashlib
import io
import math
import pathlib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from .gem import GemError, OccupancyClass, Taxonomy, VulnerabilityModel

#: Bumped when the resolution rules change. A mixture resolved under a
#: different version of this code is a different assumption about the building.
ENRICHMENT_VERSION = "1.0.0"


class EnrichmentError(Exception):
    """Raised when an enrichment cannot be formed or applied."""


class Weighting(enum.StrEnum):
    """What the stock prior counts."""

    VALUE = "replacement_cost"
    """Share of replacement cost. The default.

    A facultative book is biased towards large buildings, and value weighting
    is the one correction available from the published summaries alone -- a
    single reinforced-concrete office carries the value of several hundred
    masonry dwellings, and counting buildings would drown it. It does not make
    the prior a facultative prior. It makes it less wrong in the direction the
    bias runs.
    """

    BUILDINGS = "building_count"
    """Share of building count. Describes the stock, not the money in it."""


class Evidence(enum.StrEnum):
    """Where each part of a resolved taxonomy came from."""

    STATED = "stated"
    """The schedule said so."""

    PRIOR = "prior"
    """The schedule did not say, and the candidates were weighted by the
    country's building stock."""

    UNCONSTRAINED = "unconstrained"
    """The schedule did not say and no prior distinguishes the candidates, so
    they are carried with equal weight."""


# -- OED to GEM ------------------------------------------------------------------------

#: OED occupancy code to GEM occupancy class. 1000 (unknown) is absent on
#: purpose; see the module docstring.
OED_OCCUPANCY: Mapping[str, OccupancyClass] = {
    "1050": OccupancyClass.RESIDENTIAL,
    "1100": OccupancyClass.COMMERCIAL,
    "1150": OccupancyClass.INDUSTRIAL,
}

#: OED construction code to the GEM materials it admits. One code often admits
#: several: OED "Masonry" does not distinguish the unreinforced masonry that
#: fails in a moderate shake from the confined masonry that does not, and GEM
#: does. That gap is a mixture rather than a choice.
OED_CONSTRUCTION: Mapping[str, tuple[str, ...]] = {
    "5050": ("W",),
    "5100": ("MUR", "MCF"),
    "5150": ("CR",),
    "5200": ("S",),
}

#: 5000 is unknown construction and is deliberately not in the table above: it
#: constrains nothing, so every material the occupancy admits stays a candidate.
UNKNOWN_CONSTRUCTION = "5000"

#: GEM's macro-taxonomy labels, as its exposure summaries spell them, to the
#: vulnerability-model materials they cover. The split between ``CR-`` and
#: ``CR+`` is ductility rather than material, which is why the design level has
#: to be read to place a concrete taxonomy in one or the other.
MACRO_MATERIALS: Mapping[str, tuple[str, ...]] = {
    "CR-": ("CR",),
    "CR+": ("CR",),
    "MR|MCF": ("MCF",),
    "MUR": ("MUR",),
    "ADO|ST|E": ("MUR",),
    "S": ("S",),
    "W": ("W",),
    "OT": (),
}

#: Ductility classes GEM treats as engineered for seismic demand. A concrete
#: taxonomy carrying one of these belongs to the ``CR+`` macro class, and one
#: that does not belongs to ``CR-``.
DUCTILE_DESIGN = ("CDM", "CDH")

#: GEM's seismic design levels, least engineered first: no code, then low,
#: moderate and high code. What an assumption set may tilt a mixture towards.
DESIGN_LEVELS = ("CDN", "CDL", "CDM", "CDH")


def design_level(taxonomy: Taxonomy) -> str:
    """The seismic design level a taxonomy states, or ``""`` where it states none."""
    code = taxonomy.design.split("+")[0]
    return code if code in DESIGN_LEVELS else ""

#: Masonry qualifiers the ``ADO|ST|E`` macro class covers: adobe, dressed and
#: rubble stone. GEM's vulnerability model writes them as MUR qualifiers, and
#: its exposure summaries give them a macro class of their own, so the two have
#: to be reconciled here rather than by string prefix.
EARTHEN_QUALIFIERS = ("ADO", "STDRE", "STRUB")


def macro_class(taxonomy: Taxonomy) -> str:
    """The exposure summary's macro class for a vulnerability taxonomy.

    The fallback, used when GEM's own mapping is not to hand. The two files
    describe the same buildings in different alphabets: the vulnerability model
    names a full taxonomy, the exposure summaries group them into a handful of
    macro classes, and this reconstructs the grouping by reading the taxonomy
    string.

    It is coarse in a specific way. A macro class holding four vulnerability
    functions has its value divided equally between them, which is a statement
    about nothing. GEM's ``Vulnerability_mapping_ISO3.csv`` says which function
    each exposure taxonomy actually uses; where that file has been supplied,
    ``apply_vulnerability_mapping`` produces exact weights and this function is
    not consulted at all.
    """
    material = taxonomy.material
    base, _, qualifier = material.partition("+")

    if base == "CR":
        ductile = any(
            item in taxonomy.design.split("+")[0] for item in DUCTILE_DESIGN
        )
        return "CR+" if ductile else "CR-"
    if base == "MUR":
        return "ADO|ST|E" if qualifier in EARTHEN_QUALIFIERS else "MUR"
    if base == "MCF":
        return "MR|MCF"
    if base in ("S", "W"):
        return base
    return "OT"


# -- the country's building stock, as GEM measured it -------------------------------------

@dataclasses.dataclass(frozen=True, slots=True)
class StockPrior:
    """Value and count by occupancy and macro class, from a published summary.

    Two levels of precision, and which one is in use is worth knowing.

    The macro level always works: the public summaries group every taxonomy
    into a handful of classes -- non-ductile concrete, confined masonry and so
    on -- and a vulnerability taxonomy can be placed in one of those by reading
    its own string. It is coarse. A macro class holding four vulnerability
    functions splits its share equally between them, which is a statement about
    nothing.

    The exact level needs GEM's ``Vulnerability_mapping_ISO3.csv``, which says
    which vulnerability function each exposure taxonomy uses. With it, every
    function gets precisely the value that maps to it, and the macro grouping
    is not consulted at all. That file ships with the licensed spatial exposure
    download rather than the public repository, so ``by_taxonomy`` is empty
    until someone fetches it -- and ``uses_exact_weights`` says which
    calculation produced any given prior, because two model releases weighted
    differently are different models.
    """

    country_code: str
    shares: Mapping[tuple[str, str], tuple[float, float]]
    source_name: str
    checksum: str
    weighting: Weighting = Weighting.VALUE
    #: Value and count per exposure taxonomy, kept so a mapping can be applied
    #: later without re-reading the summary.
    by_exposure_taxonomy: Mapping[tuple[str, str], tuple[float, float]] = (
        dataclasses.field(default_factory=dict)
    )
    #: Exact share per *vulnerability* taxonomy, once GEM's mapping has been
    #: applied. Empty until then.
    by_taxonomy: Mapping[tuple[str, str], float] = dataclasses.field(
        default_factory=dict
    )
    mapping_source: str = ""
    mapping_checksum: str = ""

    @property
    def uses_exact_weights(self) -> bool:
        return bool(self.by_taxonomy)

    def taxonomy_weight(self, occupancy: OccupancyClass, taxonomy: str) -> float | None:
        """The exact share this vulnerability function holds, if it is known."""
        if not self.by_taxonomy:
            return None
        return self.by_taxonomy.get((str(occupancy), taxonomy))

    def weight(self, occupancy: OccupancyClass, macro: str) -> float:
        """The share of this occupancy's stock the macro class holds."""
        total = sum(
            self._amount(value)
            for (occ, _), value in self.shares.items()
            if occ == str(occupancy)
        )
        if total <= 0.0:
            return 0.0
        found = self.shares.get((str(occupancy), macro))
        return self._amount(found) / total if found else 0.0

    def _amount(self, value: tuple[float, float]) -> float:
        buildings, cost = value
        return cost if self.weighting is Weighting.VALUE else buildings

    def profile(self, occupancy: OccupancyClass) -> dict[str, float]:
        """Every macro class this occupancy holds, with its share."""
        return {
            macro: share
            for macro in sorted(
                {key[1] for key in self.shares if key[0] == str(occupancy)}
            )
            if (share := self.weight(occupancy, macro)) > 0.0
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "country_code": self.country_code,
            "source_name": self.source_name,
            "checksum": self.checksum,
            "weighting": str(self.weighting),
            "uses_exact_weights": self.uses_exact_weights,
            "mapping_source": self.mapping_source,
            "mapping_checksum": self.mapping_checksum,
            "occupancies": {
                str(item): self.profile(item)
                for item in OccupancyClass
                if self.profile(item)
            },
        }


def read_stock_prior(
    source: str | pathlib.Path,
    *,
    country_code: str,
    weighting: Weighting = Weighting.VALUE,
) -> StockPrior:
    """Read a GEM ``Exposure_Summary_Taxonomy.csv``.

    Only the occupancy, macro class, building count and replacement cost are
    read. The summary's own taxonomy strings are not: they band heights and
    carry occupancy sub-classes the vulnerability model does not use, so
    matching them to a vulnerability taxonomy would be a second assumption on
    top of the one in ``macro_class``, with nothing gained.
    """
    path = pathlib.Path(source)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise EnrichmentError(f"{path} could not be read: {exc}") from exc

    rows = list(csv.DictReader(payload.decode("utf-8-sig").splitlines()))
    if not rows:
        raise EnrichmentError(f"{path.name} has no rows.")

    required = {"OCCUPANCY", "MACRO_TAXONOMY", "BUILDINGS", "BLDG_REPL_COST_USD"}
    missing = sorted(required - set(rows[0]))
    if missing:
        raise EnrichmentError(
            f"{path.name} is missing columns: {', '.join(missing)}."
        )

    # A taxonomy is reported either as one TOTAL row or split into settlement
    # classes, never both. Summing regardless is correct today and would
    # double-count silently if a later release started publishing both, so the
    # two shapes are counted and the overlap refused.
    settlements: dict[tuple[str, str], set[str]] = {}
    shares: dict[tuple[str, str], list[float]] = {}
    per_taxonomy: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        occupancy = (row.get("OCCUPANCY") or "").strip()
        macro = (row.get("MACRO_TAXONOMY") or "").strip()
        taxonomy = (row.get("TAXONOMY") or "").strip()
        if not occupancy or not macro:
            continue

        seen = settlements.setdefault((occupancy, taxonomy), set())
        seen.add((row.get("SETTLEMENT") or "").strip().upper())
        if "TOTAL" in seen and len(seen) > 1:
            raise EnrichmentError(
                f"{path.name} reports {taxonomy!r} both as a total and split by "
                "settlement. Summing both would count the same buildings twice."
            )

        buildings = _number(row.get("BUILDINGS"))
        cost = _number(row.get("BLDG_REPL_COST_USD"))

        entry = shares.setdefault((occupancy, macro), [0.0, 0.0])
        entry[0] += buildings
        entry[1] += cost

        if taxonomy:
            detail = per_taxonomy.setdefault((occupancy, taxonomy), [0.0, 0.0])
            detail[0] += buildings
            detail[1] += cost

    return StockPrior(
        country_code=country_code.upper(),
        shares={key: (value[0], value[1]) for key, value in shares.items()},
        source_name=path.name,
        checksum=hashlib.sha256(payload).hexdigest(),
        weighting=weighting,
        by_exposure_taxonomy={
            key: (value[0], value[1]) for key, value in per_taxonomy.items()
        },
    )


#: The columns of GEM's published mapping file.
MAPPING_COLUMNS = ("ID_0", "TAXONOMY", "VUL_MAPPING", "WEIGHT")

#: How far a taxonomy's weights may sum from 1 before the file is refused.
WEIGHT_TOLERANCE = 1e-06


@dataclasses.dataclass(frozen=True, slots=True)
class TaxonomyMapping:
    """GEM's own mapping from exposure taxonomy to vulnerability function.

    Published as ``World/summaries/Vulnerability_mapping_country.csv`` in the
    exposure repository -- one row per country, exposure taxonomy and target
    function, with a weight. This is the file that makes ``macro_class``
    unnecessary: rather than grouping a vulnerability taxonomy by reading its
    string and dividing a macro class's value equally among its members, each
    function receives exactly the exposure value GEM says maps to it.

    The weight is not decoration. An exposure taxonomy banded as ``H:3-4``
    reaches two published functions, and GEM states the split -- 70% to the
    three-storey function and 30% to the four-storey one for Nepali confined
    masonry. That is the same mixture idea CASS applies to a Klapton Re risk,
    published one level up.
    """

    country_code: str
    entries: Mapping[str, tuple[tuple[str, float], ...]]
    source_name: str
    checksum: str

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def targets(self) -> frozenset[str]:
        """Every vulnerability function this mapping can reach."""
        return frozenset(
            function for values in self.entries.values() for function, _ in values
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "country_code": self.country_code,
            "source_name": self.source_name,
            "checksum": self.checksum,
            "exposure_taxonomies": len(self.entries),
            "vulnerability_functions": len(self.targets),
            "weighted_taxonomies": sum(
                1 for values in self.entries.values() if len(values) > 1
            ),
        }


def read_taxonomy_mapping(
    source: str | pathlib.Path, *, iso3: str
) -> TaxonomyMapping:
    """Read one country out of GEM's vulnerability mapping file.

    Two shapes of repeated row appear in the published file and they mean
    different things, so they are handled differently rather than both being
    treated as a mixture.

    An identical row repeated -- same taxonomy, same target, same weight --
    is a duplicate. Indonesia has six, and they are exactly the six taxonomies
    its exposure summary splits into urban and rural, so the mapping carries a
    row per settlement class. Summing them would double the weight.

    Different targets with weights summing to one is a real mixture. Nepal has
    five, where an exposure taxonomy banded across two storey counts is split
    between the two published functions.
    """
    path = pathlib.Path(source)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise EnrichmentError(f"{path} could not be read: {exc}") from exc

    rows = list(csv.DictReader(payload.decode("utf-8-sig").splitlines()))
    if not rows:
        raise EnrichmentError(f"{path.name} has no rows.")

    missing = sorted(set(MAPPING_COLUMNS) - set(rows[0]))
    if missing:
        raise EnrichmentError(
            f"{path.name} is missing columns: {', '.join(missing)}. It should be "
            "GEM's Vulnerability_mapping_country.csv."
        )

    wanted = iso3.strip().upper()
    seen: dict[str, dict[str, float]] = {}
    for row in rows:
        if (row.get("ID_0") or "").strip().upper() != wanted:
            continue
        taxonomy = (row.get("TAXONOMY") or "").strip()
        function = (row.get("VUL_MAPPING") or "").strip()
        if not taxonomy or not function:
            continue
        try:
            weight = float((row.get("WEIGHT") or "").strip())
        except ValueError:
            raise EnrichmentError(
                f"{path.name}: {taxonomy!r} has an unreadable weight "
                f"{row.get('WEIGHT')!r}."
            ) from None
        if weight <= 0.0:
            continue

        targets = seen.setdefault(taxonomy, {})
        existing = targets.get(function)
        if existing is None:
            targets[function] = weight
        elif abs(existing - weight) > WEIGHT_TOLERANCE:
            raise EnrichmentError(
                f"{path.name}: {taxonomy!r} maps to {function!r} at two different "
                f"weights ({existing} and {weight}). Which applied would depend on "
                "row order."
            )
        # An identical repeat is a duplicate row, not a second share of the
        # mixture, so the weight is kept rather than added.

    if not seen:
        raise EnrichmentError(
            f"{path.name} carries no rows for {wanted!r}. It is keyed by ISO 3166 "
            "alpha-3, so Indonesia is IDN and Nepal is NPL."
        )

    entries: dict[str, tuple[tuple[str, float], ...]] = {}
    for taxonomy, targets in seen.items():
        total = sum(targets.values())
        if abs(total - 1.0) > WEIGHT_TOLERANCE:
            raise EnrichmentError(
                f"{path.name}: the weights for {taxonomy!r} sum to {total} rather "
                "than 1, so the value mapped through it would be scaled."
            )
        entries[taxonomy] = tuple(sorted(targets.items()))

    return TaxonomyMapping(
        country_code=wanted,
        entries=entries,
        source_name=path.name,
        checksum=hashlib.sha256(payload).hexdigest(),
    )


def apply_taxonomy_mapping(prior: StockPrior, mapping: TaxonomyMapping) -> StockPrior:
    """A prior that weights vulnerability functions directly, not macro classes.

    Each exposure taxonomy's value is attributed to the functions GEM says it
    maps to, in the weights GEM states, and the shares are normalised within
    each occupancy. An exposure taxonomy the mapping does not cover is left out
    rather than spread over the ones it does -- that would move value onto
    buildings it does not belong to -- and ``mapping_coverage`` reports how much
    was left out.
    """
    if not prior.by_exposure_taxonomy:
        raise EnrichmentError(
            "This prior was built without per-taxonomy detail, so a mapping "
            "cannot be applied to it."
        )

    totals: dict[tuple[str, str], float] = {}
    for (occupancy, exposure), (buildings, cost) in prior.by_exposure_taxonomy.items():
        targets = mapping.entries.get(exposure)
        if not targets:
            continue
        amount = cost if prior.weighting is Weighting.VALUE else buildings
        for function, weight in targets:
            key = (occupancy, function)
            totals[key] = totals.get(key, 0.0) + amount * weight

    by_occupancy: dict[str, float] = {}
    for (occupancy, _), amount in totals.items():
        by_occupancy[occupancy] = by_occupancy.get(occupancy, 0.0) + amount

    shares = {
        key: amount / by_occupancy[key[0]]
        for key, amount in totals.items()
        if by_occupancy.get(key[0], 0.0) > 0.0
    }
    if not shares:
        raise EnrichmentError(
            "The mapping covered none of this summary's taxonomies, so it would "
            "produce a prior with no weights at all."
        )

    return dataclasses.replace(
        prior,
        by_taxonomy=shares,
        mapping_source=mapping.source_name,
        mapping_checksum=mapping.checksum,
    )


def mapping_coverage(prior: StockPrior, mapping: TaxonomyMapping) -> dict[str, Any]:
    """How much of the country's value the mapping actually places.

    A mapping covering 99% of value is fine and one covering 60% is a finding,
    and the difference is invisible unless somebody measures it. Both pilot
    countries currently come out at 100%.
    """
    covered = uncovered = 0.0
    missing: list[str] = []
    for (_, exposure), (buildings, cost) in prior.by_exposure_taxonomy.items():
        amount = cost if prior.weighting is Weighting.VALUE else buildings
        if exposure in mapping.entries:
            covered += amount
        else:
            uncovered += amount
            missing.append(exposure)

    total = covered + uncovered
    return {
        "country_code": prior.country_code,
        "mapping_source": mapping.source_name,
        "weighting": str(prior.weighting),
        "covered_share": covered / total if total else 0.0,
        "uncovered_taxonomies": sorted(set(missing))[:20],
        "uncovered_count": len(set(missing)),
    }


def _number(value: str | None) -> float:
    try:
        return float(str(value or "").strip() or 0.0)
    except ValueError:
        return 0.0


# -- what CASS knows about one risk ------------------------------------------------------

@dataclasses.dataclass(frozen=True, slots=True)
class Attributes:
    """The OED fields the intake template collects that bear on vulnerability."""

    occupancy_code: str
    construction_code: str = ""
    storeys: int | None = None
    year_built: int | None = None

    @property
    def states_construction(self) -> bool:
        code = self.construction_code.strip()
        return bool(code) and code != UNKNOWN_CONSTRUCTION

    def as_dict(self) -> dict[str, Any]:
        return {
            "occupancy_code": self.occupancy_code,
            "construction_code": self.construction_code,
            "storeys": self.storeys,
            "year_built": self.year_built,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class DesignEra:
    """A period of construction and the seismic design levels it implies.

    Country-specific and squarely a matter of local expertise: it depends on
    when a code was adopted, whether it was enforced, and on what was built
    before either. It is a table rather than a formula so that a model owner
    can correct one row without touching the resolution rules, and every
    specification carries its provenance in ``reason``.
    """

    to_year: int | None
    design_levels: tuple[str, ...]
    reason: str

    def covers(self, year: int) -> bool:
        return self.to_year is None or year <= self.to_year


# -- the resolution ----------------------------------------------------------------------

@dataclasses.dataclass(frozen=True, slots=True)
class Candidate:
    """One GEM taxonomy a risk might be, and how much of the mixture it is."""

    taxonomy: str
    imt: str
    weight: float
    macro: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "taxonomy": self.taxonomy,
            "imt": self.imt,
            "weight": self.weight,
            "macro_class": self.macro,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class Mixture:
    """What a risk resolved to, and how much of it was assumed."""

    attributes: Attributes
    candidates: tuple[Candidate, ...]
    occupancy: Evidence
    construction: Evidence
    height: Evidence
    design: Evidence
    notes: tuple[str, ...] = ()
    enrichment: str = ""
    version: str = ENRICHMENT_VERSION

    def __len__(self) -> int:
        return len(self.candidates)

    def __iter__(self) -> Iterator[Candidate]:
        return iter(self.candidates)

    @property
    def resolved(self) -> bool:
        return bool(self.candidates)

    @property
    def is_certain(self) -> bool:
        """Whether the schedule determined the building without a prior."""
        return len(self.candidates) == 1 and all(
            item is Evidence.STATED
            for item in (self.occupancy, self.construction, self.height, self.design)
        )

    @property
    def intensity_measures(self) -> tuple[str, ...]:
        return tuple(sorted({item.imt for item in self.candidates}))

    @property
    def spans_intensity_measures(self) -> bool:
        """Whether this mixture cannot be one Oasis function at any resolution.

        The multi-IMT question, arriving one risk at a time. GEM assigns each
        taxonomy the spectral period its structures respond at, so a mixture
        that spans construction types usually spans measures too, and no
        damage-bin dictionary makes that go away.
        """
        return len(self.intensity_measures) > 1

    def by_imt(self) -> dict[str, tuple[Candidate, ...]]:
        groups: dict[str, list[Candidate]] = {}
        for item in self.candidates:
            groups.setdefault(item.imt, []).append(item)
        return {imt: tuple(items) for imt, items in sorted(groups.items())}

    def as_dict(self) -> dict[str, Any]:
        return {
            "attributes": self.attributes.as_dict(),
            "enrichment": self.enrichment,
            "version": self.version,
            "resolved": self.resolved,
            "certain": self.is_certain,
            "candidate_count": len(self.candidates),
            "candidates": [item.as_dict() for item in self.candidates],
            "evidence": {
                "occupancy": str(self.occupancy),
                "construction": str(self.construction),
                "height": str(self.height),
                "design": str(self.design),
            },
            "intensity_measures": list(self.intensity_measures),
            "spans_intensity_measures": self.spans_intensity_measures,
            "notes": list(self.notes),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class Enrichment:
    """A named, versioned assumption about what GEM building a risk is.

    Selectable in the same way an occupancy assumption or a component split is:
    a run states which one it used, the version records it, and comparing two
    is how the sensitivity of a result to this assumption gets measured rather
    than asserted.
    """

    name: str
    version: str
    country_code: str
    #: ISO 3166 alpha-3, because GEM's mapping file is keyed by it and the rest
    #: of CASS is keyed by alpha-2. Stated rather than derived: there is no rule
    #: that turns ID into IDN, only a table, and a wrong one would silently read
    #: another country's mapping.
    iso3: str = ""
    design_eras: tuple[DesignEra, ...] = ()
    weighting: Weighting = Weighting.VALUE
    #: Candidates below this height are dropped. Unset by default. A model
    #: owner who knows the book is mid-rise commercial can use it to move the
    #: prior off national stock; CASS will not guess a value.
    minimum_storeys: int | None = None
    #: Macro-class weights that replace the published ones, for a book whose
    #: composition is known better than the national stock describes it.
    weight_overrides: Mapping[str, float] = dataclasses.field(default_factory=dict)
    #: Factors multiplying each candidate's weight by its seismic design level
    #: before the mixture is normalised. Empty means the published stock stands.
    #: An assumption set that leans towards or away from engineered construction
    #: says so here, as numbers a reviewer can argue with, rather than as a
    #: different model.
    design_weight_factors: Mapping[str, float] = dataclasses.field(default_factory=dict)
    open_questions: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise EnrichmentError("An enrichment needs a name.")
        if not self.version:
            raise EnrichmentError(f"Enrichment {self.name!r} needs a version.")
        for macro in self.weight_overrides:
            if macro not in MACRO_MATERIALS:
                raise EnrichmentError(
                    f"Enrichment {self.name!r} overrides the weight of {macro!r}, "
                    "which is not a GEM macro class. Known: "
                    + ", ".join(sorted(MACRO_MATERIALS))
                    + "."
                )
        for level, factor in self.design_weight_factors.items():
            if level not in DESIGN_LEVELS:
                raise EnrichmentError(
                    f"Enrichment {self.name!r} tilts design level {level!r}, which is "
                    f"not one GEM uses. Known: {', '.join(DESIGN_LEVELS)}."
                )
            if not (isinstance(factor, int | float) and math.isfinite(factor) and factor > 0):
                raise EnrichmentError(
                    f"Enrichment {self.name!r} gives design level {level} a factor of "
                    f"{factor!r}. A tilt must be a positive number: zero would remove a "
                    "building type from the country rather than weigh it less, and a "
                    "mixture that loses a candidate changes shape rather than balance."
                )

    @property
    def reference(self) -> str:
        return f"{self.name}/{self.version}"

    def design_levels(self, year: int) -> tuple[str, ...]:
        for era in self.design_eras:
            if era.covers(year):
                return era.design_levels
        return ()

    def resolve(
        self,
        attributes: Attributes,
        model: VulnerabilityModel,
        prior: StockPrior | None = None,
    ) -> Mixture:
        """The GEM taxonomies a risk with these attributes might be."""
        if model.country_code != self.country_code:
            raise EnrichmentError(
                f"Enrichment {self.reference} is for {self.country_code} and was "
                f"given a {model.country_code} vulnerability model."
            )

        notes: list[str] = []
        occupancy = OED_OCCUPANCY.get(attributes.occupancy_code.strip())
        if occupancy is None:
            return Mixture(
                attributes=attributes,
                candidates=(),
                occupancy=Evidence.UNCONSTRAINED,
                construction=Evidence.UNCONSTRAINED,
                height=Evidence.UNCONSTRAINED,
                design=Evidence.UNCONSTRAINED,
                notes=(
                    f"OED occupancy {attributes.occupancy_code!r} reaches no GEM "
                    "occupancy class, so no vulnerability function applies. Unknown "
                    "occupancy is deliberately unmapped: a risk whose use nobody "
                    "knows should fail the lookup rather than receive a generic "
                    "curve that hides the gap.",
                ),
                enrichment=self.reference,
            )

        candidates = [
            item for item in model.functions if item.taxonomy.occupancy_class is occupancy
        ]

        construction = Evidence.PRIOR
        if attributes.states_construction:
            materials = OED_CONSTRUCTION.get(attributes.construction_code.strip())
            if materials is None:
                notes.append(
                    f"OED construction {attributes.construction_code!r} is not in "
                    "the mapping, so it constrained nothing and the country's stock "
                    "prior decided the material."
                )
            else:
                narrowed = [
                    item
                    for item in candidates
                    if item.taxonomy.material.partition("+")[0] in materials
                ]
                if narrowed:
                    candidates, construction = narrowed, Evidence.STATED
                else:
                    notes.append(
                        f"No {occupancy!s} taxonomy in this model is built of "
                        f"{'/'.join(materials)}, so the stated construction could "
                        "not be honoured and the prior decided the material."
                    )

        height = Evidence.PRIOR
        if attributes.storeys is not None:
            nearest = _nearest_height(candidates, attributes.storeys)
            if nearest:
                candidates, height = nearest, Evidence.STATED
            else:
                notes.append(
                    "No candidate states a storey count, so the reported height "
                    "could not narrow the mixture."
                )

        design = Evidence.PRIOR
        if attributes.year_built is not None:
            levels = self.design_levels(attributes.year_built)
            if levels:
                narrowed = [
                    item
                    for item in candidates
                    if item.taxonomy.design.split("+")[0] in levels
                ]
                if narrowed:
                    candidates, design = narrowed, Evidence.STATED
                else:
                    notes.append(
                        f"No candidate carries a design level in {'/'.join(levels)}, "
                        f"which is what this enrichment expects of a building of "
                        f"{attributes.year_built}, so the prior decided it."
                    )
            else:
                notes.append(
                    f"This enrichment states no design era covering "
                    f"{attributes.year_built}, so the year did not narrow anything."
                )

        if self.minimum_storeys is not None:
            tall = [
                item
                for item in candidates
                if (item.taxonomy.storeys or 0) >= self.minimum_storeys
            ]
            if tall:
                candidates = tall
                notes.append(
                    f"Candidates below {self.minimum_storeys} storeys were dropped, "
                    "as this enrichment states the book does not contain them."
                )

        weighted, weighting_evidence = self._weigh(candidates, occupancy, prior)
        if weighting_evidence is Evidence.UNCONSTRAINED:
            if construction is Evidence.PRIOR:
                construction = Evidence.UNCONSTRAINED
            if height is Evidence.PRIOR:
                height = Evidence.UNCONSTRAINED
            if design is Evidence.PRIOR:
                design = Evidence.UNCONSTRAINED

        return Mixture(
            attributes=attributes,
            candidates=weighted,
            occupancy=Evidence.STATED,
            construction=construction,
            height=height,
            design=design,
            notes=tuple(notes),
            enrichment=self.reference,
        )

    def _weigh(
        self,
        candidates: Sequence[Any],
        occupancy: OccupancyClass,
        prior: StockPrior | None,
    ) -> tuple[tuple[Candidate, ...], Evidence]:
        """Share the mixture out over its candidates."""
        if not candidates:
            return (), Evidence.UNCONSTRAINED

        macros = [macro_class(item.taxonomy) for item in candidates]
        if prior is None:
            # Equal shares, then any design-level tilt: with no tilt stated
            # every factor is one and this is exactly an equal split.
            tilted = [self._tilt(item) for item in candidates]
            spread = sum(tilted)
            return (
                tuple(
                    Candidate(
                        taxonomy=item.taxonomy.text,
                        imt=item.imt,
                        weight=value / spread,
                        macro=macro,
                    )
                    for item, macro, value in zip(candidates, macros, tilted, strict=True)
                ),
                Evidence.UNCONSTRAINED,
            )

        counts: dict[str, int] = {}
        for macro in macros:
            counts[macro] = counts.get(macro, 0) + 1

        raw: list[float] = []
        for item, macro in zip(candidates, macros, strict=True):
            weight = self.weight_overrides.get(macro)
            if weight is None:
                # GEM's own mapping, where it has been supplied: the value that
                # belongs to this function rather than to the group it sits in.
                exact = prior.taxonomy_weight(occupancy, item.taxonomy.text)
                if exact is not None:
                    raw.append((exact or _NOMINAL_SHARE) * self._tilt(item))
                    continue
                weight = prior.weight(occupancy, macro)
            # A macro class the summary does not report for this occupancy gets
            # nothing from the prior. Its taxonomies stay in the mixture at a
            # nominal share rather than vanishing: GEM published a function for
            # them, so the stock has some, and a zero here would silently
            # remove a building type from the country.
            raw.append((weight or _NOMINAL_SHARE) / counts[macro] * self._tilt(item))

        total = sum(raw)
        if total <= 0.0:
            share = 1.0 / len(candidates)
            raw = [share] * len(candidates)
            total = 1.0

        return (
            tuple(
                Candidate(
                    taxonomy=item.taxonomy.text,
                    imt=item.imt,
                    weight=value / total,
                    macro=macro,
                )
                for item, macro, value in zip(candidates, macros, raw, strict=True)
            ),
            Evidence.PRIOR,
        )

    def _tilt(self, candidate: Any) -> float:
        """This candidate's design-level factor, or one where none is stated."""
        return float(self.design_weight_factors.get(design_level(candidate.taxonomy), 1.0))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "reference": self.reference,
            "country_code": self.country_code,
            "iso3": self.iso3,
            "weighting": str(self.weighting),
            "minimum_storeys": self.minimum_storeys,
            "weight_overrides": dict(self.weight_overrides),
            "design_weight_factors": dict(self.design_weight_factors),
            "design_eras": [
                {
                    "to_year": era.to_year,
                    "design_levels": list(era.design_levels),
                    "reason": era.reason,
                }
                for era in self.design_eras
            ],
            "open_questions": list(self.open_questions),
            "notes": self.notes,
            "enrichment_version": ENRICHMENT_VERSION,
        }


#: The share a macro class gets when the exposure summary reports none for that
#: occupancy. Small enough not to matter against a real share and large enough
#: that the building type does not disappear.
_NOMINAL_SHARE = 1e-04

#: The rules an assumption set may state. Both re-weigh a mixture; neither can
#: remove a candidate from it.
ASSUMPTION_RULES = ("design_level_factors", "macro_weights")


def assumption_variant(
    base: Enrichment, *, key: str, rules: Mapping[str, Any] | None = None
) -> Enrichment:
    """The base enrichment under one assumption set's rules.

    Only rules that re-weigh the mixture are accepted. A rule that removed
    candidates -- a minimum height, say -- would change which classes exist and
    which intensity measures they reach, and an assumption set has to be a
    different weighting of the same model rather than a different model. That is
    what lets one Oasis package carry every set under one set of vulnerability
    identifiers, with the keys and their reconciliation unchanged whichever set
    a run chooses.
    """
    stated = dict(rules or {})
    unknown = sorted(set(stated) - set(ASSUMPTION_RULES))
    if unknown:
        raise EnrichmentError(
            f"Assumption set {key!r} states {', '.join(unknown)}, which an assumption "
            f"set may not. It may state {', '.join(ASSUMPTION_RULES)}: rules that "
            "re-weigh a mixture rather than change which buildings are in it."
        )
    return dataclasses.replace(
        base,
        name=f"{base.name}+{key}",
        design_weight_factors={
            **base.design_weight_factors,
            **{str(k): float(v) for k, v in (stated.get("design_level_factors") or {}).items()},
        },
        weight_overrides={
            **base.weight_overrides,
            **{str(k): float(v) for k, v in (stated.get("macro_weights") or {}).items()},
        },
    )


def _nearest_height(candidates: Sequence[Any], storeys: int) -> list[Any]:
    """Candidates at the storey count closest to the one reported.

    GEM publishes functions at particular heights rather than for bands, so a
    six-storey building reaches the six-storey function if there is one and the
    nearest otherwise. Interpolating between two heights would invent a
    function GEM did not publish; taking all heights would ignore what the
    schedule actually said.
    """
    heights = {
        item.taxonomy.storeys for item in candidates if item.taxonomy.storeys is not None
    }
    if not heights:
        return []
    closest = min(heights, key=lambda value: (abs(value - storeys), value))
    return [item for item in candidates if item.taxonomy.storeys == closest]


def coverage(
    enrichment: Enrichment, model: VulnerabilityModel
) -> dict[str, Any]:
    """Which OED codes this enrichment can place in this model, and which it cannot.

    The report that says what a portfolio will actually resolve to before one
    is loaded, so a gap is found while it can still be filled rather than as an
    unexplained batch of lookup failures.
    """
    reachable: dict[str, int] = {}
    unreachable: list[str] = []
    for code, occupancy in sorted(OED_OCCUPANCY.items()):
        count = len(model.taxonomies(occupancy))
        if count:
            reachable[code] = count
        else:
            unreachable.append(code)

    materials = {
        item.taxonomy.material.partition("+")[0] for item in model.functions
    }
    unmapped_materials = sorted(
        materials
        - {value for values in OED_CONSTRUCTION.values() for value in values}
    )
    return {
        "enrichment": enrichment.reference,
        "country_code": model.country_code,
        "loss_category": str(model.loss_category),
        "occupancy_codes_reaching_functions": reachable,
        "occupancy_codes_reaching_nothing": unreachable,
        "unknown_occupancy_is_unmapped": "1000" not in OED_OCCUPANCY,
        "gem_materials_no_oed_code_selects": unmapped_materials,
        "intensity_measures": list(model.intensity_measures),
    }


def resolve_all(
    enrichment: Enrichment,
    attributes: Iterable[Attributes],
    model: VulnerabilityModel,
    prior: StockPrior | None = None,
) -> tuple[Mixture, ...]:
    """Resolve many risks, in the order given."""
    return tuple(
        enrichment.resolve(item, model, prior) for item in attributes
    )


def mixture_report(mixtures: Sequence[Mixture]) -> dict[str, Any]:
    """How much of a portfolio's vulnerability is assumed rather than stated."""
    if not mixtures:
        return {"risks": 0}

    resolved = [item for item in mixtures if item.resolved]
    return {
        "risks": len(mixtures),
        "resolved": len(resolved),
        "unresolved": len(mixtures) - len(resolved),
        "certain": sum(1 for item in resolved if item.is_certain),
        "spanning_intensity_measures": sum(
            1 for item in resolved if item.spans_intensity_measures
        ),
        "mean_candidates": (
            sum(len(item) for item in resolved) / len(resolved) if resolved else 0
        ),
        "evidence": {
            dimension: {
                str(value): sum(
                    1 for item in resolved if getattr(item, dimension) is value
                )
                for value in Evidence
            }
            for dimension in ("construction", "height", "design")
        },
    }


# -- what an enrichment run records about a portfolio -----------------------------------

#: The OED value columns a location's evidence is weighed by.
TIV_COLUMNS = ("BuildingTIV", "OtherTIV", "ContentsTIV", "BITIV")

#: The attributes an enrichment decides, in the order a mixture narrows them.
ENRICHED_ATTRIBUTES = ("occupancy", "construction", "height", "design")


class Provenance(enum.StrEnum):
    """Where one attribute of one location came from, in section 8's hierarchy."""

    REPORTED = "reported"
    """The schedule stated it, and CASS uses it as stated."""

    DERIVED = "derived"
    """Reliably derived from a stated field: a height band from a storey count,
    a seismic design level from a year of construction."""

    IMPUTED = "imputed"
    """Not stated, or stated in a form that constrains nothing, so the
    assumption set's mixture carries it."""

    UNRESOLVED = "unresolved"
    """Stated, and reaching nothing: an occupancy no GEM class answers. Neither
    evidence nor assumption, and the lookup fails the location."""


@dataclasses.dataclass(frozen=True, slots=True)
class LocationEvidence:
    """What an enrichment knew and assumed about one location."""

    account: str
    location: str
    tiv: Decimal
    evidence: Mapping[str, Provenance]

    @property
    def confidence(self) -> float:
        """The share of its attributes that were stated or derived rather than assumed."""
        known = sum(
            1
            for value in self.evidence.values()
            if value in (Provenance.REPORTED, Provenance.DERIVED)
        )
        return known / len(ENRICHED_ATTRIBUTES)

    def as_row(self) -> dict[str, str]:
        return {
            "AccNumber": self.account,
            "LocNumber": self.location,
            "TIV": str(self.tiv),
            **{name: str(self.evidence[name]) for name in ENRICHED_ATTRIBUTES},
            "Confidence": f"{self.confidence:.2f}",
        }


@dataclasses.dataclass(frozen=True, slots=True)
class ExposureLineage:
    """Attribute-level lineage for a whole portfolio under one enrichment.

    The control-plane summary section 5 asks an enrichment run to hold -- counts
    by evidence class, missingness by count and by value, and exceptions -- and
    the per-location table behind it, which is kept as an artifact rather than
    expanded into database rows.
    """

    enrichment: str
    locations: tuple[LocationEvidence, ...]

    @property
    def total_tiv(self) -> Decimal:
        return sum((item.tiv for item in self.locations), Decimal(0))

    def counts(self) -> dict[str, int]:
        """Attribute values by provenance, across every location."""
        found = {str(item): 0 for item in Provenance}
        for location in self.locations:
            for value in location.evidence.values():
                found[str(value)] += 1
        return found

    def by_attribute(self) -> dict[str, dict[str, int]]:
        found = {
            name: {str(item): 0 for item in Provenance} for name in ENRICHED_ATTRIBUTES
        }
        for location in self.locations:
            for name, value in location.evidence.items():
                found[name][str(value)] += 1
        return found

    def missingness(self) -> dict[str, dict[str, Any]]:
        """For each attribute, how many locations and how much value lacked it."""
        profile: dict[str, dict[str, Any]] = {}
        for name in ENRICHED_ATTRIBUTES:
            missing = [
                item
                for item in self.locations
                if item.evidence[name] in (Provenance.IMPUTED, Provenance.UNRESOLVED)
            ]
            value = sum((item.tiv for item in missing), Decimal(0))
            profile[name] = {
                "locations": len(missing),
                "tiv": str(value),
                "share_of_tiv": (
                    str((value / self.total_tiv).quantize(Decimal("0.0001")))
                    if self.total_tiv
                    else "0"
                ),
            }
        return profile

    def exceptions(self) -> list[dict[str, str]]:
        """Locations no assumption can place, with the value that rests on them."""
        return [
            {
                "AccNumber": item.account,
                "LocNumber": item.location,
                "TIV": str(item.tiv),
                "reason": (
                    "The occupancy reaches no GEM class, so no assumption set places "
                    "this location and the lookup fails it."
                ),
            }
            for item in self.locations
            if item.evidence["occupancy"] is Provenance.UNRESOLVED
        ]

    def mean_confidence(self) -> float:
        if not self.locations:
            return 0.0
        return sum(item.confidence for item in self.locations) / len(self.locations)

    def as_csv(self) -> bytes:
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(
            buffer,
            fieldnames=["AccNumber", "LocNumber", "TIV", *ENRICHED_ATTRIBUTES, "Confidence"],
            lineterminator="\n",
        )
        writer.writeheader()
        for item in self.locations:
            writer.writerow(item.as_row())
        return buffer.getvalue().encode("utf-8")


def exposure_lineage(
    rows: Iterable[Mapping[str, Any]], enrichment: Enrichment
) -> ExposureLineage:
    """Which of each location's attributes were stated, derived or assumed.

    Read from the published OED rows, as reported. Nothing here changes a value:
    an assumption set re-weighs the mixture a class is blended from, not the
    attributes a schedule states, so the lineage is a statement about the rows
    rather than a rewrite of them.
    """
    found: list[LocationEvidence] = []
    for row in rows:
        occupancy = str(row.get("OccupancyCode") or "").strip()
        construction = str(row.get("ConstructionCode") or "").strip()
        storeys = _whole(row.get("NumberOfStoreys"))
        year = _whole(row.get("YearBuilt"))

        evidence = {
            "occupancy": (
                Provenance.REPORTED if occupancy in OED_OCCUPANCY else Provenance.UNRESOLVED
            ),
            "construction": (
                Provenance.REPORTED if construction in OED_CONSTRUCTION else Provenance.IMPUTED
            ),
            "height": (
                Provenance.DERIVED if storeys is not None and storeys >= 1 else Provenance.IMPUTED
            ),
            "design": (
                Provenance.DERIVED
                if year is not None and enrichment.design_levels(year)
                else Provenance.IMPUTED
            ),
        }
        found.append(
            LocationEvidence(
                account=str(row.get("AccNumber") or "").strip(),
                location=str(row.get("LocNumber") or "").strip(),
                tiv=sum((_money(row.get(column)) for column in TIV_COLUMNS), Decimal(0)),
                evidence=evidence,
            )
        )
    return ExposureLineage(enrichment=enrichment.reference, locations=tuple(found))


def _whole(value: Any) -> int | None:
    try:
        text = str(value).strip()
        return int(float(text)) if text else None
    except (TypeError, ValueError):
        return None


def _money(value: Any) -> Decimal:
    try:
        text = str(value).strip() if value is not None else ""
        return Decimal(text) if text else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def require_model(model: VulnerabilityModel) -> None:
    """Refuse a model whose loss category cannot carry monetary vulnerability."""
    if not model.loss_category.is_monetary:
        raise GemError(
            f"{model.loss_category} is a ratio of occupants rather than of value. "
            "It must not reach a vulnerability table Oasis will multiply by a TIV."
        )
