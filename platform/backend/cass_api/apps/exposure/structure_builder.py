"""Building a financial structure on the platform, rather than importing one.

Section 3 asks for a financial structure workspace, and until now it could only
show a structure somebody had written into OED files elsewhere: a portfolio that
arrived with locations alone could never gain a policy or a treaty. This writes
them into the draft exposure version's own OED files, so the structure a run
applies is still the portfolio's files, validated the same way, and immutable
once the version is published.

It asks only for what the engine uses, and refuses what the engine would
reject or ignore. The rules per contract type are oasislmf 2.5.7's own: a quota
share cedes the share stated on the contract; a surplus share cedes the share
stated on each scope row, and the engine refuses a surplus share whose scope
rows do not name exactly the risk at its risk level; a catastrophe excess of
loss applies its occurrence attachment and limit to each event across the
portfolio. A contract type CASS does not apply in a run (ADR 10) is refused here
rather than written and then quietly left out of the ceded loss.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import transaction

from apps.artifacts.models import ArtifactLink
from cass_oed import structure as oed_structure
from cass_oed.schema import FLAT_TERM_BASIS, FileKind

from . import editing
from .models import ExposureVersion
from .services import ROLE_BY_KIND, ExposureError, load_files, run_validation

#: The contract types a run applies (ADR 10), and so the ones built here.
BUILDABLE_TYPES = tuple(sorted(oed_structure.PORTFOLIO_LEVEL_TYPES))

#: OED's risk level for terms applied to the portfolio as a whole.
PORTFOLIO_LEVEL = "SEL"

#: The risk levels a per-risk term may be written at, with the scope columns the
#: engine requires a surplus share's scope row to name, and to leave empty.
EXACT_SCOPE: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "ACC": (("AccNumber",), ("PolNumber", "LocNumber")),
    "POL": (("AccNumber", "PolNumber"), ("LocNumber",)),
    "LOC": (("AccNumber", "LocNumber"), ()),
}

LEVEL_NAMES = {"ACC": "account", "POL": "policy", "LOC": "location"}
SCOPE_NAMES = {"AccNumber": "the account", "PolNumber": "the policy", "LocNumber": "the location"}


class BuildError(Exception):
    """A structure that cannot be written as asked, field by field."""

    def __init__(self, problems: Mapping[str, str]) -> None:
        super().__init__("; ".join(problems.values()))
        self.problems = dict(problems)


# -- reading and writing the files ----------------------------------------------

def _require_draft(version: ExposureVersion) -> None:
    if version.is_frozen:
        raise ExposureError(
            f"{version} is published, so its structure cannot change. Correct it in a new "
            "version and build the structure there."
        )


def _rows(version: ExposureVersion, kind: FileKind) -> list[dict[str, str]]:
    if kind not in editing.attached_kinds(version):
        return []
    return editing.read_rows(version, kind)


def _write(version: ExposureVersion, kind: FileKind, rows: Sequence[Mapping[str, str]], *, actor) -> None:
    """Write a file's rows with every column any row uses, or drop the file when none are left."""
    if not rows:
        ArtifactLink.objects.filter(
            subject_type="exposure_version",
            subject_id=version.id,
            role=ROLE_BY_KIND[kind],
            direction="input",
        ).delete()
        return
    names: list[str] = []
    for row in rows:
        for name in row:
            if name not in names:
                names.append(name)
    editing.write_rows(
        version, kind, [{name: row.get(name, "") for name in names} for row in rows], actor=actor
    )


def summary(version: ExposureVersion) -> dict[str, Any]:
    """The structure as the workspace shows it, read back from the files just written."""
    structure = oed_structure.read(load_files(version))
    return {
        "exposure_version": str(version.id),
        "currency": version.run_currency,
        **structure.as_dict(),
    }


# -- values -------------------------------------------------------------------------

def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _plain(value: Decimal | None, default: str = "") -> str:
    return format(value, "f") if value is not None else default


def _amount(
    value: Any, field: str, label: str, problems: dict[str, str], *, share: bool = False
) -> Decimal | None:
    """A money amount or a share, refusing one that is not a usable number."""
    if value is None or str(value).strip() == "":
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        problems[field] = f"{label} must be a number."
        return None
    if not number.is_finite() or number < 0:
        problems[field] = f"{label} cannot be negative."
        return None
    if share and number > 1:
        problems[field] = f"{label} is a proportion between 0 and 1, so 25% is written 0.25."
        return None
    return number


def _checked(kind: FileKind, rows: Sequence[Mapping[str, str]], problems: dict[str, str], prefix: str) -> None:
    """Hold every written row to the schema the file is validated against."""
    for index, row in enumerate(rows, start=1):
        for field, message in editing.check(kind, row).items():
            problems.setdefault(f"{prefix}{index}.{field}" if len(rows) > 1 else field, message)


# -- policies -------------------------------------------------------------------------

@transaction.atomic
def add_policy(
    version: ExposureVersion,
    *,
    account: Any,
    policy: Any,
    perils: Any,
    layers: Sequence[Mapping[str, Any]],
    deductible: Any = None,
    policy_limit: Any = None,
    inception: Any = "",
    expiry: Any = "",
    actor=None,
) -> dict[str, Any]:
    """Write one policy and its layers into the portfolio's account file."""
    _require_draft(version)
    problems: dict[str, str] = {}
    account, policy, perils = _text(account), _text(policy), _text(perils).upper()

    if not account:
        problems["AccNumber"] = "Name the account the policy belongs to."
    if not policy:
        problems["PolNumber"] = "Name the policy."
    if not perils:
        problems["PolPerilsCovered"] = "State the perils the policy covers, such as QEQ."

    locations = [
        row for row in editing.read_rows(version, FileKind.LOCATION)
        if _text(row.get("AccNumber")) == account
    ]
    if account and not locations:
        problems["AccNumber"] = (
            f"No location in this portfolio belongs to account {account}, so a policy on it "
            "would cover nothing."
        )
    existing = _rows(version, FileKind.ACCOUNT)
    if any(
        _text(row.get("AccNumber")) == account and _text(row.get("PolNumber")) == policy
        for row in existing
    ):
        problems["PolNumber"] = (
            f"Policy {policy} on account {account} is already written. Remove it first to "
            "write it again."
        )
    if not layers:
        problems["layers"] = "A policy needs at least one layer."

    deductible_value = _amount(deductible, "PolDed6All", "The policy deductible", problems)
    limit_value = _amount(policy_limit, "PolLimit6All", "The policy limit", problems)
    portfolio = _text(locations[0].get("PortNumber")) if locations else ""
    currency = _text(locations[0].get("LocCurrency")) if locations else ""

    written: list[dict[str, str]] = []
    for number, layer in enumerate(layers or [], start=1):
        prefix = f"layers.{number}."
        attachment = _amount(layer.get("attachment"), prefix + "attachment", f"Layer {number}'s attachment", problems)
        limit = _amount(layer.get("limit"), prefix + "limit", f"Layer {number}'s limit", problems)
        share = _amount(
            layer.get("participation"), prefix + "participation", f"Layer {number}'s signed share",
            problems, share=True,
        )
        written.append(
            {
                "PortNumber": portfolio,
                "AccNumber": account,
                "AccCurrency": currency,
                "PolNumber": policy,
                "PolPerilsCovered": perils,
                "PolInceptionDate": _text(inception),
                "PolExpiryDate": _text(expiry),
                "LayerNumber": str(number),
                "LayerParticipation": _plain(share, default="1"),
                "LayerLimit": _plain(limit),
                "LayerAttachment": _plain(attachment),
                "PolDed6All": _plain(deductible_value),
                "PolLimit6All": _plain(limit_value),
                # OED requires the peril and the basis wherever a policy term
                # carries a value, and CASS calculates only a flat amount.
                "PolPeril": perils if (deductible_value or limit_value) else "",
                "PolDedType6All": FLAT_TERM_BASIS if deductible_value else "",
                "PolLimitType6All": FLAT_TERM_BASIS if limit_value else "",
            }
        )

    if not problems:
        _checked(FileKind.ACCOUNT, written, problems, "layers.")
    if problems:
        raise BuildError(problems)

    _write(version, FileKind.ACCOUNT, [*existing, *written], actor=actor)
    run_validation(version, actor=actor)
    return summary(version)


@transaction.atomic
def remove_policy(version: ExposureVersion, *, account: Any, policy: Any, actor=None) -> dict[str, Any]:
    """Remove every layer of one policy."""
    _require_draft(version)
    account, policy = _text(account), _text(policy)
    rows = _rows(version, FileKind.ACCOUNT)
    kept = [
        row for row in rows
        if not (_text(row.get("AccNumber")) == account and _text(row.get("PolNumber")) == policy)
    ]
    if len(kept) == len(rows):
        raise BuildError({"PolNumber": f"No policy {policy} on account {account} is written."})
    _write(version, FileKind.ACCOUNT, kept, actor=actor)
    run_validation(version, actor=actor)
    return summary(version)


# -- contracts ------------------------------------------------------------------------

def _policy_keys(rows: Sequence[Mapping[str, Any]]) -> set[tuple[str, str]]:
    return {(_text(row.get("AccNumber")), _text(row.get("PolNumber"))) for row in rows}


@transaction.atomic
def add_contract(
    version: ExposureVersion,
    *,
    contract_type: Any,
    perils: Any,
    name: Any = "",
    currency: Any = "",
    inuring_priority: Any = 1,
    ceded_percent: Any = None,
    placed_percent: Any = None,
    occurrence_attachment: Any = None,
    occurrence_limit: Any = None,
    risk_level: Any = "",
    risk_attachment: Any = None,
    risk_limit: Any = None,
    reinstatements: Any = None,
    reinstatement_rate: Any = None,
    reinstatement_premium: Any = None,
    scope: Sequence[Mapping[str, Any]] = (),
    whole_portfolio: bool = False,
    actor=None,
) -> dict[str, Any]:
    """Write one reinsurance contract and the scope it reaches."""
    _require_draft(version)
    info_rows = _rows(version, FileKind.REINS_INFO)
    info, scope_rows = contract_rows(
        contract_type=contract_type,
        perils=perils,
        name=name,
        currency=currency,
        inuring_priority=inuring_priority,
        ceded_percent=ceded_percent,
        placed_percent=placed_percent,
        occurrence_attachment=occurrence_attachment,
        occurrence_limit=occurrence_limit,
        risk_level=risk_level,
        risk_attachment=risk_attachment,
        risk_limit=risk_limit,
        reinstatements=reinstatements,
        reinstatement_rate=reinstatement_rate,
        reinstatement_premium=reinstatement_premium,
        scope=scope,
        whole_portfolio=whole_portfolio,
        number=max([_int(row.get("ReinsNumber")) or 0 for row in info_rows] + [0]) + 1,
        locations=editing.read_rows(version, FileKind.LOCATION),
        policy_keys=_policy_keys(_rows(version, FileKind.ACCOUNT)),
    )
    _write(version, FileKind.REINS_INFO, [*info_rows, info], actor=actor)
    _write(version, FileKind.REINS_SCOPE, [*_rows(version, FileKind.REINS_SCOPE), *scope_rows], actor=actor)
    run_validation(version, actor=actor)
    return summary(version)


def contract_rows(
    *,
    contract_type: Any,
    perils: Any,
    number: int,
    locations: Sequence[Mapping[str, Any]],
    policy_keys: set[tuple[str, str]],
    layer_number: int = 1,
    name: Any = "",
    currency: Any = "",
    inuring_priority: Any = 1,
    ceded_percent: Any = None,
    placed_percent: Any = None,
    occurrence_attachment: Any = None,
    occurrence_limit: Any = None,
    risk_level: Any = "",
    risk_attachment: Any = None,
    risk_limit: Any = None,
    reinstatements: Any = None,
    reinstatement_rate: Any = None,
    reinstatement_premium: Any = None,
    scope: Sequence[Mapping[str, Any]] = (),
    whole_portfolio: bool = False,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """The info row and scope rows one contract layer writes, or why it cannot.

    Reads and writes nothing, so the Financial structure screen and an imported
    workbook are held to one set of rules: a contract the screen would refuse
    is refused from a workbook too, in the same words. Raises ``BuildError``.
    """
    kind = _text(contract_type).upper()
    if not kind:
        raise BuildError({"ReinsType": "Choose the contract type."})
    if kind not in BUILDABLE_TYPES:
        label = oed_structure.CONTRACT_TYPES.get(kind, kind)
        raise BuildError(
            {
                "ReinsType": (
                    f"{label} is not a contract type CASS applies in a run. It applies quota "
                    "share, surplus share and catastrophe excess of loss (ADR 10), and a "
                    "contract built here that the engine then left out would read as ceded "
                    "loss that was never ceded."
                )
            }
        )

    problems: dict[str, str] = {}
    perils = _text(perils).upper()
    if not perils:
        problems["ReinsPeril"] = "State the perils the contract covers, such as QEQ."
    currency = _text(currency).upper() or (_text(locations[0].get("LocCurrency")) if locations else "")

    ceded = _amount(ceded_percent, "CededPercent", "The ceded share", problems, share=True)
    placed = _amount(placed_percent, "PlacedPercent", "The placed share", problems, share=True)
    occurrence_attach = _amount(occurrence_attachment, "OccAttachment", "The occurrence attachment", problems)
    occurrence_cap = _amount(occurrence_limit, "OccLimit", "The occurrence limit", problems)
    risk_attach = _amount(risk_attachment, "RiskAttachment", "The risk attachment", problems)
    risk_cap = _amount(risk_limit, "RiskLimit", "The risk limit", problems)
    level = _text(risk_level).upper()
    reinstated, rate_text, rip_base = _reinstatement_terms(
        kind, reinstatements, reinstatement_rate, reinstatement_premium, problems
    )
    priority = _int(inuring_priority)
    if priority is None or priority < 1:
        problems["InuringPriority"] = "The inuring priority is a whole number from 1."
        priority = 1

    if kind == "CXL":
        if occurrence_cap is None and "OccLimit" not in problems:
            problems["OccLimit"] = (
                "A catastrophe excess of loss needs an occurrence limit: the most it pays for "
                "one event."
            )
        if occurrence_attach is None and "OccAttachment" not in problems:
            problems["OccAttachment"] = (
                "A catastrophe excess of loss needs an occurrence attachment: where its cover "
                "begins for one event."
            )
        if risk_attach is not None or risk_cap is not None:
            problems["RiskLimit"] = (
                "A catastrophe excess of loss applies to each event across the portfolio, not "
                "per risk, so it takes no risk terms."
            )
        level = PORTFOLIO_LEVEL
    elif kind == "QS":
        if ceded is None and "CededPercent" not in problems:
            problems["CededPercent"] = "A quota share needs the share it cedes, as a proportion."
        if occurrence_attach is not None:
            problems["OccAttachment"] = (
                "A quota share takes no occurrence attachment; the engine reads only its "
                "occurrence limit."
            )
        if risk_attach is not None:
            problems["RiskAttachment"] = (
                "A quota share takes no risk attachment; the engine reads only its risk limit."
            )
        if risk_cap is not None:
            if level not in EXACT_SCOPE:
                problems["RiskLevel"] = (
                    "A risk limit applies per risk, so say which: per location, per policy or "
                    "per account."
                )
        else:
            level = PORTFOLIO_LEVEL
    else:  # SS
        if level not in EXACT_SCOPE:
            problems["RiskLevel"] = (
                "A surplus share cedes risk by risk, so say at which level: per location, per "
                "policy or per account."
            )
        if occurrence_attach is not None:
            problems["OccAttachment"] = (
                "A surplus share takes no occurrence attachment; the engine reads only its "
                "occurrence limit."
            )
        if ceded is not None:
            problems["CededPercent"] = (
                "A surplus share's ceded share is stated for each risk in its scope, not once "
                "for the contract."
            )
        if whole_portfolio:
            problems["scope"] = (
                "A surplus share names each risk it cedes; the engine refuses one scoped to "
                "the whole portfolio."
            )

    portfolio_of = {_text(row.get("AccNumber")): _text(row.get("PortNumber")) for row in locations}
    location_keys = {(_text(row.get("AccNumber")), _text(row.get("LocNumber"))) for row in locations}

    scope_rows: list[dict[str, str]] = []
    if whole_portfolio and kind != "SS":
        if scope:
            problems["scope"] = (
                "Scope a contract either to the whole portfolio or to the risks it names, not both."
            )
        for portfolio in sorted(set(portfolio_of.values())):
            scope_rows.append({"ReinsNumber": str(number), "PortNumber": portfolio})
    elif not whole_portfolio:
        if not scope:
            problems["scope"] = (
                "Say what the contract covers: the whole portfolio, or the accounts, policies "
                "or locations it names."
            )
        for index, entry in enumerate(scope, start=1):
            key = f"scope.{index}"
            label = _text(entry.get("label")) or f"Scope row {index}"
            account = _text(entry.get("account"))
            policy = _text(entry.get("policy"))
            location = _text(entry.get("location"))
            if not account:
                problems[key] = f"{label} names no account."
                continue
            if account not in portfolio_of:
                problems[key] = f"{label} names account {account}, which this portfolio does not hold."
                continue
            if location and (account, location) not in location_keys:
                problems[key] = (
                    f"{label} names location {location} on account {account}, which "
                    "this portfolio does not hold."
                )
                continue
            if policy and (account, policy) not in policy_keys:
                problems[key] = (
                    f"{label} names policy {policy} on account {account}, which has no "
                    "policy of that name."
                )
                continue
            row = {
                "ReinsNumber": str(number),
                "PortNumber": portfolio_of[account],
                "AccNumber": account,
                "PolNumber": policy,
                "LocNumber": location,
            }
            if kind == "SS" and level in EXACT_SCOPE:
                required, empty = EXACT_SCOPE[level]
                if any(not row[field] for field in required) or any(row[field] for field in empty):
                    problems[key] = (
                        f"A surplus share written per {LEVEL_NAMES[level]} needs each scope row to "
                        f"name {' and '.join(SCOPE_NAMES[field] for field in required)}"
                        + (
                            f", and no {' or '.join(SCOPE_NAMES[field] for field in empty)}"
                            if empty
                            else ""
                        )
                        + ": the engine matches a surplus share to exactly that risk."
                    )
                share = _amount(
                    entry.get("ceded_percent"), f"{key}.ceded_percent",
                    f"{label}'s ceded share", problems, share=True,
                )
                if share is None and f"{key}.ceded_percent" not in problems:
                    problems[f"{key}.ceded_percent"] = f"{label} needs the share ceded on that risk."
                row["CededPercent"] = _plain(share)
            scope_rows.append(row)

    info = {
        "ReinsNumber": str(number),
        "ReinsLayerNumber": str(layer_number),
        "ReinsName": _text(name),
        "ReinsPeril": perils,
        "CededPercent": _plain(ceded, default="1" if kind == "CXL" else ""),
        "PlacedPercent": _plain(placed, default="1"),
        "RiskLimit": _plain(risk_cap),
        "RiskAttachment": _plain(risk_attach),
        "OccLimit": _plain(occurrence_cap),
        "OccAttachment": _plain(occurrence_attach),
        "ReinsCurrency": currency,
        "InuringPriority": str(priority),
        "ReinsType": kind,
        "RiskLevel": level,
        "Reinstatement": "" if reinstated is None else str(reinstated),
        "ReinstatementCharge": rate_text,
        "ReinsPremium": _plain(rip_base),
    }

    if not problems:
        _checked(FileKind.REINS_INFO, [info], problems, "contract.")
        _checked(FileKind.REINS_SCOPE, scope_rows, problems, "scope.")
    if problems:
        raise BuildError(problems)
    return info, scope_rows


def _reinstatement_terms(
    kind: str, count: Any, rate: Any, premium: Any, problems: dict[str, str]
) -> tuple[int | None, str, Decimal | None]:
    """Reinstatements, their rate and the premium they are charged on.

    Stated only for a catastrophe excess of loss, the one type whose annual
    limit CASS applies. The engine ignores all three, so a term written on any
    other contract would read as cover that is limited when it is not.
    """
    from cass_oed.limited_cover import CoverError, parse_rates

    stated = [value for value in (count, rate, premium) if _text(value)]
    if not stated:
        return None, "", None
    if kind != "CXL":
        problems["Reinstatement"] = (
            "Reinstatements are applied to a catastrophe excess of loss only, whose "
            "limit is per event. Leave them out for this contract type."
        )
        return None, "", None
    reinstated = _int(count) if _text(count) else None
    if _text(count) and (reinstated is None or reinstated < 0):
        problems["Reinstatement"] = "The number of reinstatements is a whole number from 0."
    if reinstated is None and not problems.get("Reinstatement"):
        problems["Reinstatement"] = (
            "State how many reinstatements the layer has, so its rate and premium "
            "have something to apply to. 0 means it pays its limit once a year."
        )
    try:
        rates = parse_rates(rate)
    except CoverError as exc:
        problems["ReinstatementCharge"] = str(exc)
        rates = ()
    base = _amount(premium, "ReinsPremium", "The reinstatement premium", problems)
    if rates and any(rates) and base is None and "ReinsPremium" not in problems:
        problems["ReinsPremium"] = (
            "A reinstatement rate needs the premium it is charged on, usually the "
            "layer's MDP at 100%."
        )
    text = ";".join(format(value, "g") for value in rates)
    return reinstated, text, base


@dataclasses.dataclass(frozen=True, slots=True)
class Programme:
    """A workbook's reinsurance, as the rows it writes and what it could not."""

    info: tuple[dict[str, str], ...]
    scope: tuple[dict[str, str], ...]
    #: Each contract layer that broke a rule: its record and the reasons.
    refused: tuple[tuple[Mapping[str, Any], tuple[str, ...]], ...]
    #: Contract numbers no scope row reaches, so they would cede nothing.
    unscoped: tuple[int, ...]

    @property
    def written(self) -> tuple[int, ...]:
        return tuple(sorted({int(row["ReinsNumber"]) for row in self.info}))


def programme_rows(
    contracts: Sequence[Mapping[str, Any]],
    scope: Sequence[Mapping[str, Any]],
    *,
    locations: Sequence[Mapping[str, Any]],
    policy_keys: set[tuple[str, str]],
    perils: str,
    currency: str,
) -> Programme:
    """Every contract of a workbook, under the rules ``contract_rows`` applies.

    Contract numbers and layers are kept as the workbook states them, so the
    structure a run applies can be read back against the sheet it came from.
    Layers of one contract share its scope, which OED joins on the contract
    number alone, so scope rows are written once per contract.

    Records are canonical, as the intake reader names them: a scope row with no
    Policy ID covers the whole portfolio.
    """
    by_number: dict[int, list[Mapping[str, Any]]] = {}
    for record in contracts:
        number = _int(record.get("contract_number"))
        if number is not None:
            by_number.setdefault(number, []).append(record)
    reach: dict[int, list[Mapping[str, Any]]] = {}
    for record in scope:
        number = _int(record.get("contract_number"))
        if number is not None:
            reach.setdefault(number, []).append(record)

    info: list[dict[str, str]] = []
    scope_rows: list[dict[str, str]] = []
    refused: list[tuple[Mapping[str, Any], tuple[str, ...]]] = []
    unscoped: list[int] = []

    for number in sorted(by_number):
        rows = reach.get(number, [])
        if not rows:
            unscoped.append(number)
            continue
        named = [
            {
                "account": row.get("business_id"),
                "policy": row.get("policy_id"),
                "location": row.get("location_number"),
                "ceded_percent": row.get("ceded_percent"),
                "label": f"Scope row {row.get('row_number')}" if row.get("row_number") else "",
            }
            for row in rows
            if _text(row.get("business_id"))
        ]
        whole = any(not _text(row.get("business_id")) for row in rows)
        scoped = False
        for record in sorted(by_number[number], key=lambda item: _int(item.get("layer_number")) or 1):
            try:
                layer_info, layer_scope = contract_rows(
                    contract_type=record.get("contract_type"),
                    perils=perils,
                    currency=currency,
                    name=record.get("name"),
                    inuring_priority=record.get("inuring_priority"),
                    ceded_percent=record.get("ceded_percent"),
                    placed_percent=record.get("placed_percent"),
                    occurrence_attachment=record.get("occurrence_attachment"),
                    occurrence_limit=record.get("occurrence_limit"),
                    risk_level=record.get("risk_level"),
                    risk_limit=record.get("risk_limit"),
                    reinstatements=record.get("reinstatements"),
                    reinstatement_rate=record.get("reinstatement_rate"),
                    reinstatement_premium=record.get("reinstatement_premium"),
                    scope=named,
                    whole_portfolio=whole,
                    number=number,
                    layer_number=_int(record.get("layer_number")) or 1,
                    locations=locations,
                    policy_keys=policy_keys,
                )
            except BuildError as exc:
                refused.append((record, tuple(dict.fromkeys(exc.problems.values()))))
                continue
            info.append(layer_info)
            if not scoped:
                scope_rows.extend(layer_scope)
                scoped = True

    return Programme(
        info=tuple(info),
        scope=tuple(scope_rows),
        refused=tuple(refused),
        unscoped=tuple(unscoped),
    )


@transaction.atomic
def remove_contract(version: ExposureVersion, *, number: Any, actor=None) -> dict[str, Any]:
    """Remove one contract and every scope row that names it."""
    _require_draft(version)
    wanted = _int(number)
    info_rows = _rows(version, FileKind.REINS_INFO)
    kept_info = [row for row in info_rows if _int(row.get("ReinsNumber")) != wanted]
    if wanted is None or len(kept_info) == len(info_rows):
        raise BuildError({"ReinsNumber": f"No contract {number} is written."})
    kept_scope = [
        row for row in _rows(version, FileKind.REINS_SCOPE) if _int(row.get("ReinsNumber")) != wanted
    ]
    _write(version, FileKind.REINS_INFO, kept_info, actor=actor)
    _write(version, FileKind.REINS_SCOPE, kept_scope, actor=actor)
    run_validation(version, actor=actor)
    return summary(version)
