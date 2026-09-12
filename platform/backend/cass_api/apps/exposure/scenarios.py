"""Comparing multi-location allocation scenarios at the model.

The extract reports value at the policy and the model needs it at the location,
so every policy scheduling more than one site carries an assumption about how
it divides. ``cass_extract.allocation`` makes each scenario exact and
reconciled. What it cannot say is whether the choice *matters*, and that is the
question an analyst actually has.

It matters only where a business's sites fall in different area-peril cells.
Two warehouses 300 metres apart in the same Jakarta cell see the same hazard
and reach the same vulnerability function, so moving value between them changes
the loss by nothing at all -- the split is arithmetic. Two sites in different
cells is a different portfolio. So the comparison is run against a real grid,
and the headline number is the share of value for which the assumption is
economically live.

Three things this deliberately does not do.

It does not promote. Each scenario is prepared, mapped and reported in memory,
so an analyst can try six of them without leaving six published versions
behind. Promotion is the separate, audited act of choosing one.

It does not rank the scenarios. Equal-location is the maximum-ignorance
baseline and the others are sensitivities around it; none is more likely than
another, because the source says nothing about site value. Reporting a
"recommended" allocation would invent evidence.

It does not turn a spread into a loss. Until a hazard set exists, the movement
reported here is movement of exposure between cells. That is the input to a
loss difference, not the difference itself, and calling it one would overstate
what the platform currently knows.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import cass_extract as extract
from apps.modelregistry.assets import load_grid, load_vulnerability
from cass_keys.lookup import KeyStatus, lookup

from . import promotion
from .models import ImportBatch

#: Bumped when the comparison's materiality rule or reported shape changes.
SCENARIO_RULE_VERSION = "1.0.0"

#: The scenarios a comparison runs when the caller names none. The baseline
#: first: section 5.3 makes equal-location the maximum-ignorance allocation and
#: the others sensitivities around it, so the order is not cosmetic.
DEFAULT_METHODS: tuple[extract.AllocationMethod, ...] = (
    extract.AllocationMethod.EQUAL_LOCATION,
    extract.AllocationMethod.PRIMARY_CONCENTRATED,
)


#: Allocations that describe a whole portfolio. ``concentration_v1`` is not one
#: of them: it places a policy's whole value at *one nominated site*, so it is
#: a per-business envelope variant rather than a portfolio-wide choice, and
#: asking for it here would need a nominated site for every policy at once.
PORTFOLIO_METHODS: frozenset[extract.AllocationMethod] = frozenset(
    {
        extract.AllocationMethod.EQUAL_LOCATION,
        extract.AllocationMethod.PRIMARY_CONCENTRATED,
    }
)


class ScenarioError(Exception):
    """Raised when a comparison cannot be produced."""


@dataclasses.dataclass(frozen=True, slots=True)
class ScenarioResult:
    """One allocation scenario, mapped to the model."""

    method: extract.AllocationMethod
    baseline: bool
    total_tiv: Decimal
    mapped_tiv: Decimal
    failed_tiv: Decimal
    reconciles: bool
    #: TIV by area-peril cell. The thing that actually differs between
    #: scenarios, and the thing a hazard set would be applied to.
    by_area_peril: dict[int, Decimal]
    location_count: int
    methods_used: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": str(self.method),
            "baseline": self.baseline,
            "total_tiv": str(self.total_tiv),
            "mapped_tiv": str(self.mapped_tiv),
            "failed_tiv": str(self.failed_tiv),
            "reconciles": self.reconciles,
            "location_count": self.location_count,
            "area_peril_count": len(self.by_area_peril),
            "tiv_by_area_peril": {
                str(key): str(value) for key, value in sorted(self.by_area_peril.items())
            },
            "methods_used": self.methods_used,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class BusinessMateriality:
    """Whether the allocation assumption is live for one business."""

    business_id: str
    location_count: int
    area_peril_count: int
    total_tiv: Decimal

    @property
    def material(self) -> bool:
        """Whether moving value between this business's sites changes anything.

        Two conditions, and both are needed. One site means there is nothing to
        allocate. Several sites in one cell means the allocation moves value
        between places the model cannot tell apart.
        """
        return self.location_count > 1 and self.area_peril_count > 1

    @property
    def reason(self) -> str:
        if self.location_count <= 1:
            return "One scheduled site, so no allocation assumption applies."
        if self.area_peril_count <= 1:
            return (
                f"{self.location_count} sites, all in one area-peril cell. The "
                "allocation moves value between places this grid cannot "
                "distinguish, so it cannot change the loss."
            )
        return (
            f"{self.location_count} sites across {self.area_peril_count} area-peril "
            "cells, so the allocation assumption changes which hazard the value sees."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "business_id": self.business_id,
            "location_count": self.location_count,
            "area_peril_count": self.area_peril_count,
            "total_tiv": str(self.total_tiv),
            "material": self.material,
            "reason": self.reason,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class ScenarioComparison:
    """Every scenario, what differs between them, and where it matters."""

    rule_version: str
    grid_reference: str
    vulnerability_reference: str
    scenarios: list[ScenarioResult]
    materiality: list[BusinessMateriality]
    #: Per multi-location business, every area-peril cell its whole value could
    #: be concentrated into. The brief's concentration envelope, stated in the
    #: terms the model works in: a statement about what is not known, and
    #: deliberately not a choice among them.
    envelope: dict[str, list[int]]

    @property
    def baseline(self) -> ScenarioResult:
        return self.scenarios[0]

    @property
    def total_holds(self) -> bool:
        """Whether every scenario carries the same money.

        The property that makes a comparison a sensitivity rather than a set of
        different portfolios. A scenario that changed the total would be
        answering a different question.
        """
        return len({item.total_tiv for item in self.scenarios}) == 1

    @property
    def material_tiv(self) -> Decimal:
        return sum(
            (item.total_tiv for item in self.materiality if item.material),
            Decimal("0.00"),
        )

    @property
    def total_tiv(self) -> Decimal:
        return sum((item.total_tiv for item in self.materiality), Decimal("0.00"))

    def movement(self, scenario: ScenarioResult) -> dict[int, Decimal]:
        """How much TIV a scenario moves into or out of each cell.

        Against the baseline, and signed: a cell that gains and one that loses
        are different findings, and an absolute value would hide which is which.
        """
        cells = set(self.baseline.by_area_peril) | set(scenario.by_area_peril)
        return {
            cell: scenario.by_area_peril.get(cell, Decimal("0.00"))
            - self.baseline.by_area_peril.get(cell, Decimal("0.00"))
            for cell in sorted(cells)
            if scenario.by_area_peril.get(cell, Decimal("0.00"))
            != self.baseline.by_area_peril.get(cell, Decimal("0.00"))
        }

    def as_dict(self) -> dict[str, Any]:
        material = [item for item in self.materiality if item.material]
        total = self.total_tiv
        return {
            "rule_version": self.rule_version,
            "allocation_rule_version": extract.ALLOCATION_RULE_VERSION,
            "grid": self.grid_reference,
            "vulnerability": self.vulnerability_reference,
            "total_holds_across_scenarios": self.total_holds,
            "scenarios": [item.as_dict() for item in self.scenarios],
            "movement_from_baseline": {
                str(item.method): {
                    str(cell): str(amount)
                    for cell, amount in self.movement(item).items()
                }
                for item in self.scenarios[1:]
            },
            "materiality": {
                "businesses": len(self.materiality),
                "businesses_where_allocation_is_material": len(material),
                "material_tiv": str(self.material_tiv),
                "total_tiv": str(total),
                "material_share": (
                    float(self.material_tiv / total) if total else 0.0
                ),
                "detail": [
                    item.as_dict()
                    for item in sorted(
                        self.materiality,
                        key=lambda entry: (not entry.material, entry.business_id),
                    )
                ],
            },
            "envelope": {
                business_id: {
                    "candidate_area_perils": cells,
                    "count": len(cells),
                    "total_tiv": str(
                        next(
                            (
                                item.total_tiv
                                for item in self.materiality
                                if item.business_id == business_id
                            ),
                            Decimal("0.00"),
                        )
                    ),
                }
                for business_id, cells in sorted(self.envelope.items())
            },
            "interpretation": (
                "Movement is exposure moving between area-peril cells, not a loss "
                "difference. No scenario is more likely than another: the source "
                "reports no site-level value, so equal-location is the "
                "maximum-ignorance baseline and the rest are sensitivities around it."
            ),
        }


def compare(
    batch: ImportBatch,
    *,
    model_version,
    methods: Sequence[extract.AllocationMethod] = DEFAULT_METHODS,
    cohort: extract.Cohort = extract.Cohort.A,
    class_of_business: str | None = extract.cohorts.PHYSICAL_DAMAGE_CLASS,
    country: str | None = None,
    component_split: extract.ComponentSplit | None = None,
    occupancy: extract.OccupancyAssumption | None = None,
) -> ScenarioComparison:
    """Prepare one selection under several allocations and map each to the model.

    Everything except the allocation is held fixed. A comparison that also
    varied the coverage split or the occupancy would not be a comparison of
    allocations, and the reader could not attribute a difference to anything.
    """
    ordered = list(dict.fromkeys(methods))
    if not ordered:
        raise ScenarioError("A comparison needs at least one allocation scenario.")
    unsupported = [item for item in ordered if item not in PORTFOLIO_METHODS]
    if unsupported:
        raise ScenarioError(
            ", ".join(sorted(str(item) for item in unsupported))
            + " places a policy's whole value at one nominated site, so it "
            "describes a single business rather than a portfolio. The envelope in "
            "this report already bounds that: it names every cell each "
            "multi-location business's value could concentrate into. Portfolio "
            "scenarios available: "
            + ", ".join(sorted(str(item) for item in PORTFOLIO_METHODS))
            + "."
        )

    grid = load_grid(model_version.grid)
    vulnerability = load_vulnerability(model_version.vulnerability_set)

    results: list[ScenarioResult] = []
    #: Built from the baseline only. Which cell a site sits in is a property of
    #: the grid, not of an allocation, so reading it from one scenario and
    #: applying it to all of them is correct rather than a shortcut.
    sites: dict[str, set[int]] = {}
    cells: dict[str, set[int]] = {}
    value: dict[str, Decimal] = {}

    for position, method in enumerate(ordered):
        prepared = promotion.prepare(
            batch,
            cohort=cohort,
            class_of_business=class_of_business,
            country=country,
            allocation_method=method,
            component_split=component_split,
            occupancy=occupancy,
        )
        result = lookup(prepared.rows, grid=grid, vulnerability=vulnerability)

        by_area_peril: dict[int, Decimal] = {}
        for record in result.records:
            if record.status is not KeyStatus.SUCCESS or record.area_peril_id is None:
                continue
            by_area_peril[record.area_peril_id] = (
                by_area_peril.get(record.area_peril_id, Decimal("0.00")) + record.tiv
            )
            if position == 0:
                account = record.account_id
                sites.setdefault(account, set()).add(int(record.location_number or 0))
                cells.setdefault(account, set()).add(record.area_peril_id)
                value[account] = value.get(account, Decimal("0.00")) + record.tiv

        results.append(
            ScenarioResult(
                method=method,
                baseline=position == 0,
                total_tiv=prepared.total_tiv,
                mapped_tiv=result.report.mapped_tiv,
                failed_tiv=result.report.failed_tiv,
                reconciles=result.report.reconciled,
                by_area_peril=by_area_peril,
                location_count=len(prepared.rows),
                methods_used=sorted(
                    {str(item.method) for item in prepared.allocation.allocations}
                ),
            )
        )

    return ScenarioComparison(
        rule_version=SCENARIO_RULE_VERSION,
        grid_reference=grid.reference,
        vulnerability_reference=(
            f"{vulnerability.country_code.lower()}-vuln-{vulnerability.version}"
        ),
        scenarios=results,
        envelope={
            business_id: sorted(business_cells)
            for business_id, business_cells in sorted(cells.items())
            if len(sites.get(business_id, set())) > 1
        },
        materiality=[
            BusinessMateriality(
                business_id=business_id,
                location_count=len(sites.get(business_id, set())),
                area_peril_count=len(business_cells),
                total_tiv=value.get(business_id, Decimal("0.00")),
            )
            for business_id, business_cells in sorted(cells.items())
        ],
    )
