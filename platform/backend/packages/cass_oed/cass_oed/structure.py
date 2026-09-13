"""The financial structure a portfolio carries, read from its own files.

Section 3 asks for a workspace showing accounts and layers, contracts, the
scope each contract reaches, the inuring order and the reconciliation between
them. This module is the reading half of that: it takes the published OED and
says what the structure is, what it covers and where it does not add up.

It states rather than repairs. A layer nobody wrote an attachment for, a
contract whose scope reaches no location, a signed share above 100% -- each is
reported with the record that carries it, because a financial structure that
quietly fixed itself would produce a loss nobody could tie back to the treaty
it came from. Section 8's rule about reported data is the same rule here.

Money is ``Decimal`` throughout, and every figure crosses the API as a string.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from .reader import ReadResult, Row
from .schema import expand_perils
from .validation import PortfolioFiles

#: Contract types Oasis applies at the portfolio level, which is where the
#: patched worker runs reinsurance (ADR 10). A per-risk contract is read,
#: reported and reconciled here; what it is not is silently applied.
PORTFOLIO_LEVEL_TYPES = frozenset({"QS", "SS", "CXL"})

#: How OED names each contract type, for a reader who does not think in codes.
CONTRACT_TYPES: Mapping[str, str] = {
    "QS": "Quota share",
    "SS": "Surplus share",
    "FAC": "Facultative",
    "PR": "Per risk",
    "CXL": "Catastrophe excess of loss",
    "XL": "Excess of loss",
}


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - a cell that will not parse is simply absent
        return None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


@dataclasses.dataclass(frozen=True, slots=True)
class Layer:
    """One layer of one policy, as the account file states it."""

    account: str
    policy: str
    layer_number: int | None
    participation: Decimal | None
    limit: Decimal | None
    attachment: Decimal | None
    deductible: Decimal | None
    policy_limit: Decimal | None
    perils: tuple[str, ...]
    inception: str
    expiry: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "policy": self.policy,
            "layer_number": self.layer_number,
            "participation": _str(self.participation),
            "limit": _str(self.limit),
            "attachment": _str(self.attachment),
            "deductible": _str(self.deductible),
            "policy_limit": _str(self.policy_limit),
            "perils": list(self.perils),
            "inception": self.inception,
            "expiry": self.expiry,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class Contract:
    """One reinsurance contract, with the scope it reaches."""

    number: int | None
    layer_number: int | None
    name: str
    contract_type: str
    perils: tuple[str, ...]
    inuring_priority: int | None
    ceded_percent: Decimal | None
    placed_percent: Decimal | None
    risk_limit: Decimal | None
    risk_attachment: Decimal | None
    occurrence_limit: Decimal | None
    occurrence_attachment: Decimal | None
    currency: str
    scope_rows: int
    scope_tiv: Decimal
    locations_reached: int
    applied_by_the_engine: bool
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "layer_number": self.layer_number,
            "name": self.name,
            "type": self.contract_type,
            "type_label": CONTRACT_TYPES.get(self.contract_type, self.contract_type),
            "perils": list(self.perils),
            "inuring_priority": self.inuring_priority,
            "ceded_percent": _str(self.ceded_percent),
            "placed_percent": _str(self.placed_percent),
            "risk_limit": _str(self.risk_limit),
            "risk_attachment": _str(self.risk_attachment),
            "occurrence_limit": _str(self.occurrence_limit),
            "occurrence_attachment": _str(self.occurrence_attachment),
            "currency": self.currency,
            "scope_rows": self.scope_rows,
            "scope_tiv": str(self.scope_tiv),
            "locations_reached": self.locations_reached,
            "applied_by_the_engine": self.applied_by_the_engine,
            "notes": list(self.notes),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class StructureFinding:
    """Something about the structure a person has to decide about."""

    code: str
    subject: str
    message: str
    blocking: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "subject": self.subject,
            "message": self.message,
            "blocking": self.blocking,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class FinancialStructure:
    """Everything the financial structure workspace shows."""

    total_tiv: Decimal
    location_count: int
    layers: tuple[Layer, ...]
    contracts: tuple[Contract, ...]
    inuring_order: tuple[tuple[int, tuple[int, ...]], ...]
    uncovered_locations: int
    uncovered_tiv: Decimal
    findings: tuple[StructureFinding, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_tiv": str(self.total_tiv),
            "location_count": self.location_count,
            "layers": [item.as_dict() for item in self.layers],
            "contracts": [item.as_dict() for item in self.contracts],
            "inuring_order": [
                {"priority": priority, "contracts": list(numbers)}
                for priority, numbers in self.inuring_order
            ],
            "uncovered_locations": self.uncovered_locations,
            "uncovered_tiv": str(self.uncovered_tiv),
            "findings": [item.as_dict() for item in self.findings],
            "has_accounts": bool(self.layers),
            "has_contracts": bool(self.contracts),
        }


def _str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _location_tiv(rows: Iterable[Row]) -> dict[str, Decimal]:
    """Insured value by location reference, as the file states it."""
    from .schema import COVERAGE_TYPES

    totals: dict[str, Decimal] = {}
    for row in rows:
        key = row.text("LocNumber")
        total = Decimal(0)
        for column in COVERAGE_TYPES:
            amount = _decimal(row.get(column))
            if amount is not None:
                total += amount
        totals[key] = totals.get(key, Decimal(0)) + total
    return totals


def _layers(account: ReadResult | None) -> tuple[list[Layer], list[StructureFinding]]:
    if account is None:
        return [], []

    layers: list[Layer] = []
    findings: list[StructureFinding] = []
    for row in account.rows:
        layer = Layer(
            account=row.text("AccNumber"),
            policy=row.text("PolNumber"),
            layer_number=row.get("LayerNumber"),
            participation=_decimal(row.get("LayerParticipation")),
            limit=_decimal(row.get("LayerLimit")),
            attachment=_decimal(row.get("LayerAttachment")),
            deductible=_decimal(row.get("PolDed6All")),
            policy_limit=_decimal(row.get("PolLimit6All")),
            perils=expand_perils(row.text("PolPerilsCovered")),
            inception=row.text("PolInceptionDate"),
            expiry=row.text("PolExpiryDate"),
        )
        layers.append(layer)

        subject = f"{layer.account}/{layer.policy} layer {layer.layer_number or '—'}"
        if layer.participation is not None and layer.participation > 1:
            findings.append(
                StructureFinding(
                    "participation_above_one",
                    subject,
                    f"The signed share is {layer.participation}, which is more than the "
                    "whole layer. OED states participation as a proportion.",
                    blocking=True,
                )
            )
        if layer.limit is None and layer.policy_limit is None:
            findings.append(
                StructureFinding(
                    "no_limit",
                    subject,
                    "Neither a layer limit nor a policy limit is stated, so this layer "
                    "is unlimited as written. Confirm that is intended.",
                )
            )

    # Layers are evaluated in order, and a gap means a band of loss nobody has
    # written a term for. Reported rather than closed up.
    by_policy: dict[tuple[str, str], list[int]] = {}
    for layer in layers:
        if layer.layer_number is not None:
            by_policy.setdefault((layer.account, layer.policy), []).append(
                layer.layer_number
            )
    for (account_ref, policy), numbers in sorted(by_policy.items()):
        ordered = sorted(numbers)
        expected = list(range(1, len(ordered) + 1))
        if ordered != expected:
            findings.append(
                StructureFinding(
                    "layer_gap",
                    f"{account_ref}/{policy}",
                    f"Layers are numbered {', '.join(str(item) for item in ordered)}. "
                    "Layers are evaluated in order, so a gap is a band of loss with no "
                    "term written against it.",
                )
            )

    return layers, findings


def _scope_index(
    reins_scope: ReadResult | None, location_tiv: Mapping[str, Decimal]
) -> dict[int, tuple[int, Decimal, set[str]]]:
    """Rows, value and locations each contract's scope reaches."""
    index: dict[int, tuple[int, Decimal, set[str]]] = {}
    if reins_scope is None:
        return index

    every_location = set(location_tiv)
    for row in reins_scope.rows:
        number = row.get("ReinsNumber")
        if number is None:
            continue
        rows_seen, value, reached = index.get(number, (0, Decimal(0), set()))
        location = row.text("LocNumber")
        if location:
            matched = {location} & every_location
        else:
            # A scope row naming no location covers everything the rest of its
            # columns select. Account and policy selection is what OED uses
            # most, and a row naming neither reaches the whole portfolio.
            matched = every_location
        index[number] = (
            rows_seen + 1,
            value + sum((location_tiv[item] for item in matched), Decimal(0)),
            reached | matched,
        )
    return index


def read(files: PortfolioFiles) -> FinancialStructure:
    """Read the financial structure out of a published portfolio."""
    location_tiv = _location_tiv(files.location.rows)
    total_tiv = sum(location_tiv.values(), Decimal(0))

    layers, findings = _layers(files.account)
    scope = _scope_index(files.reins_scope, location_tiv)

    contracts: list[Contract] = []
    covered: set[str] = set()
    for row in (files.reins_info.rows if files.reins_info is not None else []):
        number = row.get("ReinsNumber")
        rows_seen, scope_tiv, reached = scope.get(number, (0, Decimal(0), set()))
        covered |= reached
        contract_type = row.text("ReinsType").upper()
        notes: list[str] = []
        if contract_type and contract_type not in PORTFOLIO_LEVEL_TYPES:
            notes.append(
                "CASS runs reinsurance at portfolio level on the patched worker "
                "(ADR 10), so a per-risk contract is read and reconciled here but "
                "not applied in a run."
            )
        contracts.append(
            Contract(
                number=number,
                layer_number=row.get("ReinsLayerNumber"),
                name=row.text("ReinsName"),
                contract_type=contract_type,
                perils=expand_perils(row.text("ReinsPeril")),
                inuring_priority=row.get("InuringPriority"),
                ceded_percent=_decimal(row.get("CededPercent")),
                placed_percent=_decimal(row.get("PlacedPercent")),
                risk_limit=_decimal(row.get("RiskLimit")),
                risk_attachment=_decimal(row.get("RiskAttachment")),
                occurrence_limit=_decimal(row.get("OccLimit")),
                occurrence_attachment=_decimal(row.get("OccAttachment")),
                currency=row.text("ReinsCurrency"),
                scope_rows=rows_seen,
                scope_tiv=scope_tiv,
                locations_reached=len(reached),
                applied_by_the_engine=contract_type in PORTFOLIO_LEVEL_TYPES,
                notes=tuple(notes),
            )
        )

        subject = f"Contract {number}" + (f" ({row.text('ReinsName')})" if row.text("ReinsName") else "")
        if rows_seen == 0:
            findings.append(
                StructureFinding(
                    "contract_without_scope",
                    subject,
                    "No scope row names this contract, so it reaches nothing. A "
                    "contract that covers nothing cedes nothing.",
                    blocking=True,
                )
            )
        elif not reached:
            findings.append(
                StructureFinding(
                    "scope_matches_no_location",
                    subject,
                    f"{rows_seen} scope row(s) name locations this portfolio does not "
                    "hold, so the contract reaches none of it.",
                    blocking=True,
                )
            )

    # Inuring priority decides the order treaties benefit each other. Two
    # contracts at one priority run together, which is a statement worth making
    # explicit rather than leaving in a column.
    priorities: dict[int, list[int]] = {}
    for contract in contracts:
        if contract.inuring_priority is not None and contract.number is not None:
            priorities.setdefault(contract.inuring_priority, []).append(contract.number)
    inuring_order = tuple(
        (priority, tuple(sorted(numbers))) for priority, numbers in sorted(priorities.items())
    )

    uncovered = set(location_tiv) - covered if contracts else set()
    uncovered_tiv = sum((location_tiv[item] for item in uncovered), Decimal(0))
    if contracts and uncovered:
        findings.append(
            StructureFinding(
                "locations_outside_every_contract",
                "Reinsurance scope",
                f"{len(uncovered)} location(s) holding {uncovered_tiv} of insured value "
                "are named by no contract's scope. Their loss is retained in full.",
            )
        )

    return FinancialStructure(
        total_tiv=total_tiv,
        location_count=len(location_tiv),
        layers=tuple(layers),
        contracts=tuple(contracts),
        inuring_order=inuring_order,
        uncovered_locations=len(uncovered),
        uncovered_tiv=uncovered_tiv,
        findings=tuple(findings),
    )
