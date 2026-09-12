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
import pathlib
from collections.abc import Iterable, Mapping, Sequence
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

#: Masonry qualifiers the ``ADO|ST|E`` macro class covers: adobe, dressed and
#: rubble stone. GEM's vulnerability model writes them as MUR qualifiers, and
#: its exposure summaries give them a macro class of their own, so the two have
#: to be reconciled here rather than by string prefix.
EARTHEN_QUALIFIERS = ("ADO", "STDRE", "STRUB")


def macro_class(taxonomy: Taxonomy) -> str:
    """The exposure summary's macro class for a vulnerability taxonomy.

    The two files describe the same buildings in different alphabets: the
    vulnerability model names a full taxonomy, the exposure summaries group
    them. GEM publishes a mapping between them, but it is one of the licensed
    assets the release manifest records as outstanding, so this reconstructs
    the grouping from the taxonomy string. It is an assumption, and a shallow
    one; it is here rather than inline so that replacing it with the licensed
    mapping is a change to one function.
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
    """Value and count by occupancy and macro class, from a published summary."""

    country_code: str
    shares: Mapping[tuple[str, str], tuple[float, float]]
    source_name: str
    checksum: str
    weighting: Weighting = Weighting.VALUE

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

        entry = shares.setdefault((occupancy, macro), [0.0, 0.0])
        entry[0] += _number(row.get("BUILDINGS"))
        entry[1] += _number(row.get("BLDG_REPL_COST_USD"))

    return StockPrior(
        country_code=country_code.upper(),
        shares={key: (value[0], value[1]) for key, value in shares.items()},
        source_name=path.name,
        checksum=hashlib.sha256(payload).hexdigest(),
        weighting=weighting,
    )


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
    design_eras: tuple[DesignEra, ...] = ()
    weighting: Weighting = Weighting.VALUE
    #: Candidates below this height are dropped. Unset by default. A model
    #: owner who knows the book is mid-rise commercial can use it to move the
    #: prior off national stock; CASS will not guess a value.
    minimum_storeys: int | None = None
    #: Macro-class weights that replace the published ones, for a book whose
    #: composition is known better than the national stock describes it.
    weight_overrides: Mapping[str, float] = dataclasses.field(default_factory=dict)
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
            share = 1.0 / len(candidates)
            return (
                tuple(
                    Candidate(
                        taxonomy=item.taxonomy.text,
                        imt=item.imt,
                        weight=share,
                        macro=macro,
                    )
                    for item, macro in zip(candidates, macros, strict=True)
                ),
                Evidence.UNCONSTRAINED,
            )

        counts: dict[str, int] = {}
        for macro in macros:
            counts[macro] = counts.get(macro, 0) + 1

        raw: list[float] = []
        for macro in macros:
            weight = self.weight_overrides.get(macro)
            if weight is None:
                weight = prior.weight(occupancy, macro)
            # A macro class the summary does not report for this occupancy gets
            # nothing from the prior. Its taxonomies stay in the mixture at a
            # nominal share rather than vanishing: GEM published a function for
            # them, so the stock has some, and a zero here would silently
            # remove a building type from the country.
            raw.append((weight or _NOMINAL_SHARE) / counts[macro])

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

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "reference": self.reference,
            "country_code": self.country_code,
            "weighting": str(self.weighting),
            "minimum_storeys": self.minimum_storeys,
            "weight_overrides": dict(self.weight_overrides),
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


def require_model(model: VulnerabilityModel) -> None:
    """Refuse a model whose loss category cannot carry monetary vulnerability."""
    if not model.loss_category.is_monetary:
        raise GemError(
            f"{model.loss_category} is a ratio of occupants rather than of value. "
            "It must not reach a vulnerability table Oasis will multiply by a TIV."
        )
