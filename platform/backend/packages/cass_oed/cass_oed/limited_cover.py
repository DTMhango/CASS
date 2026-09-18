"""Reinsurance cover limited by contract terms: reinstatements and their premiums.

The Oasis financial module applies a catastrophe excess of loss to each event
on its own. Nothing carries from one event to the next, so a layer pays in full
on every event of a year, however many there are: unlimited reinstatements,
free of charge. Oasis's own list of the terms it supports marks
``Reinstatement``, ``ReinstatementCharge``, ``ReinsPremium`` and the aggregate
terms as unsupported, and oasislmf 2.5.7 reads none of them.

This module is the other reading. It takes the engine's year-by-year insured
loss for the risks each contract covers and applies every layer in event order
within each simulated year:

- a layer pays ``min(max(loss - attachment, 0), limit)`` for an event, as the
  engine does, until the year's aggregate is used up: the limit once, plus once
  more for each reinstatement;
- each amount paid is reinstated while reinstatements remain, at the rate the
  contract states for that reinstatement (``1`` for 100%, ``1.25`` for 125%),
  pro rata to amount on the premium stated for the layer, and never pro rata to
  time;
- the reinstatement premium is deducted from the recovery it restores, so the
  net recoverable for the event is the recovery less that premium, and the loss
  kept is the insured loss less the net recoverable;
- the ceded share and the placed share scale the recovery and its premium alike.

Everything else is the engine's, deliberately. The same arithmetic runs once
more with the aggregate and the premium removed, and that figure must match the
engine's own net-of-reinsurance result: it is the check that this module read
the engine's losses and curves the way the engine did. Curves are the mean
sample basis ``ept`` reports, computed as ``oasislmf.pytools.lec`` computes
them: per year and sample, the aggregate is the year's total and the occurrence
loss its largest event; each is averaged over samples, ranked, and read at the
return periods by the same linear interpolation.

Within a year, events are applied in event-number order. CASS's occurrence
table puts every event on the first of January of its year, so there is no
date to order by, and the order is recorded rather than implied. It can move a
year's largest single loss once an aggregate runs out; it never moves the
year's total.
"""

from __future__ import annotations

import dataclasses
import io
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np

#: The only contract type this calculation applies. A quota share or surplus
#: share inuring before a catastrophe layer changes the loss the layer sees, and
#: a per-risk contract needs each risk's loss rather than the scope's total.
SUPPORTED_TYPE = "CXL"

#: The cover class of a location no contract covers.
UNCOVERED = "none"


class CoverError(Exception):
    """Raised when a programme cannot be applied with limited cover."""


# -- the contracts ------------------------------------------------------------------

def parse_rates(text: Any) -> tuple[float, ...]:
    """Reinstatement rates as OED writes them: ``1``, ``1.25`` or ``0;0.5;1``."""
    raw = "" if text is None else str(text).strip()
    if not raw:
        return ()
    rates: list[float] = []
    for part in raw.replace(",", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        try:
            value = float(part)
        except ValueError as exc:
            raise CoverError(
                f"Reinstatement rate {raw!r} is not a number or a list of numbers "
                "separated by semicolons, such as 1.25 or 0;0.5;1."
            ) from exc
        if not math.isfinite(value) or value < 0:
            raise CoverError(f"Reinstatement rate {raw!r} cannot be negative.")
        rates.append(value)
    return tuple(rates)


@dataclasses.dataclass(frozen=True, slots=True)
class LayerTerms:
    """One layer of one catastrophe excess of loss, as the calculation applies it."""

    contract: int
    layer: int
    name: str
    priority: int
    attachment: float
    limit: float
    ceded: float
    placed: float
    #: ``None`` where the contract states no number of reinstatements. Such a
    #: layer is applied as the engine applies it, and the result says so.
    reinstatements: int | None
    rates: tuple[float, ...]
    premium: float | None
    classes: frozenset[str]

    @property
    def aggregate(self) -> float:
        if self.reinstatements is None:
            return math.inf
        return self.limit * (self.reinstatements + 1)

    def rate(self, reinstatement: int) -> float:
        """The rate of the k-th reinstatement, counting from 1."""
        if not self.rates:
            return 0.0
        if len(self.rates) == 1:
            return self.rates[0]
        return self.rates[min(reinstatement, len(self.rates)) - 1]

    @property
    def charges_premium(self) -> bool:
        return bool(self.reinstatements) and bool(self.premium) and any(self.rates)

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> LayerTerms:
        """A layer recorded by ``as_dict``, as a run stores it between stages."""
        return cls(
            contract=int(record["contract"]),
            layer=int(record["layer"]),
            name=str(record.get("name") or ""),
            priority=int(record.get("priority") or 1),
            attachment=float(record["attachment"]),
            limit=float(record["limit"]),
            ceded=float(record.get("ceded", 1.0)),
            placed=float(record.get("placed", 1.0)),
            reinstatements=(
                None if record.get("reinstatements") is None else int(record["reinstatements"])
            ),
            rates=tuple(float(value) for value in record.get("rates") or ()),
            premium=None if record.get("premium") is None else float(record["premium"]),
            classes=frozenset(record.get("classes") or ()),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "layer": self.layer,
            "name": self.name,
            "priority": self.priority,
            "attachment": self.attachment,
            "limit": self.limit,
            "ceded": self.ceded,
            "placed": self.placed,
            "reinstatements": self.reinstatements,
            "rates": list(self.rates),
            "premium": self.premium,
            "classes": sorted(self.classes),
        }


def _number(row: Mapping[str, Any], name: str, default: float | None = None) -> float | None:
    raw = row.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except ValueError as exc:
        raise CoverError(f"{name} {raw!r} is not a number.") from exc


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def location_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (_text(row.get("PortNumber")), _text(row.get("AccNumber")), _text(row.get("LocNumber")))


def cover_classes(
    info_rows: Sequence[Mapping[str, Any]],
    scope_rows: Sequence[Mapping[str, Any]],
    locations: Sequence[Mapping[str, Any]],
) -> tuple[list[LayerTerms], dict[tuple[str, str, str], str]]:
    """Every layer, and each location's cover class.

    A cover class is the set of contracts whose scope reaches a location,
    written as their numbers joined by ``+``, or ``none``. The engine reports
    insured loss per class, and a contract's loss for an event is the sum of
    the classes that name it. Refuses what this calculation cannot apply
    faithfully, with the reason, rather than applying something else.
    """
    contracts: dict[int, list[Mapping[str, Any]]] = {}
    for row in info_rows:
        try:
            number = int(float(_text(row.get("ReinsNumber"))))
        except ValueError as exc:
            raise CoverError(f"Contract number {row.get('ReinsNumber')!r} is not a whole number.") from exc
        kind = _text(row.get("ReinsType")).upper()
        if kind != SUPPORTED_TYPE:
            raise CoverError(
                f"Contract {number} is {kind or 'of no stated type'}. Limited cover applies "
                "catastrophe excess of loss contracts only: a quota share or surplus share "
                "changes the loss a later layer sees, and a per-risk contract needs each "
                "risk's own loss. Run this programme with the engine's cover."
            )
        contracts.setdefault(number, []).append(row)

    reach: dict[int, list[Mapping[str, Any]]] = {}
    for row in scope_rows:
        try:
            number = int(float(_text(row.get("ReinsNumber"))))
        except ValueError:
            continue
        if _text(row.get("PolNumber")):
            raise CoverError(
                f"Contract {number}'s scope names a policy. Limited cover reads insured "
                "loss per location, so a scope must name accounts, locations or the whole "
                "portfolio."
            )
        reach.setdefault(number, []).append(row)

    def covers(row: Mapping[str, Any], key: tuple[str, str, str]) -> bool:
        for field, value in zip(("PortNumber", "AccNumber", "LocNumber"), key, strict=True):
            stated = _text(row.get(field))
            if stated and stated != value:
                return False
        return True

    classes: dict[tuple[str, str, str], str] = {}
    for location in locations:
        key = location_key(location)
        numbers = sorted(
            number
            for number, rows in reach.items()
            if number in contracts and any(covers(row, key) for row in rows)
        )
        classes[key] = "+".join(str(number) for number in numbers) or UNCOVERED

    by_priority: dict[int, set[int]] = {}
    layers: list[LayerTerms] = []
    for number, rows in sorted(contracts.items()):
        priority = int(_number(rows[0], "InuringPriority", 1) or 1)
        by_priority.setdefault(priority, set()).add(number)
        reached = frozenset(
            code for code in set(classes.values()) if str(number) in code.split("+")
        )
        for row in sorted(rows, key=lambda item: _number(item, "ReinsLayerNumber", 1) or 1):
            count = _number(row, "Reinstatement")
            limit = _number(row, "OccLimit")
            if not limit:
                raise CoverError(f"Contract {number} states no occurrence limit.")
            layers.append(
                LayerTerms(
                    contract=number,
                    layer=int(_number(row, "ReinsLayerNumber", 1) or 1),
                    name=_text(row.get("ReinsName")),
                    priority=priority,
                    attachment=_number(row, "OccAttachment", 0.0) or 0.0,
                    limit=limit,
                    ceded=_number(row, "CededPercent", 1.0) or 1.0,
                    placed=_number(row, "PlacedPercent", 1.0) or 1.0,
                    reinstatements=None if count is None else int(count),
                    rates=parse_rates(row.get("ReinstatementCharge")),
                    premium=_number(row, "ReinsPremium"),
                    classes=reached,
                )
            )

    # Contracts at a later priority see what the earlier ones left. Where their
    # scopes overlap, that is a loss net of limited recoveries, which this
    # version does not carry forward; disjoint scopes need no carrying.
    priorities = sorted(by_priority)
    for earlier_index, earlier in enumerate(priorities):
        for later in priorities[earlier_index + 1:]:
            earlier_classes = {code for layer in layers if layer.priority == earlier for code in layer.classes}
            later_classes = {code for layer in layers if layer.priority == later for code in layer.classes}
            if earlier_classes & later_classes:
                raise CoverError(
                    f"Contracts at inuring priorities {earlier} and {later} cover some of the "
                    "same risks. Limited cover does not yet carry one priority's recoveries "
                    "into the next; run this programme with the engine's cover."
                )
    return layers, classes


# -- the losses ---------------------------------------------------------------------

@dataclasses.dataclass(slots=True)
class EventLosses:
    """Insured loss per event occurrence, sample and cover class, sorted for applying.

    Rows are ordered by sample, then year, then event, so a year's events are
    contiguous and in the order the layers meet them.
    """

    sample: np.ndarray
    period: np.ndarray
    event: np.ndarray
    losses: np.ndarray  # shape (rows, classes)
    classes: tuple[str, ...]
    periods: int
    samples: int

    @classmethod
    def from_arrays(
        cls,
        sample: np.ndarray,
        period: np.ndarray,
        event: np.ndarray,
        class_index: np.ndarray,
        loss: np.ndarray,
        *,
        classes: Sequence[str],
        periods: int,
        samples: int,
    ) -> EventLosses:
        """From one entry per (sample, year, event, cover class), in any order.

        Only real samples are kept: the engine's sample index uses zero and
        below for the mean and other statistics, which are not draws.
        """
        keep = (sample > 0) & (loss != 0)
        s, p, e, c, value = (
            np.asarray(array)[keep] for array in (sample, period, event, class_index, loss)
        )
        order = np.lexsort((e, p, s))
        s, p, e, c, value = s[order], p[order], e[order], c[order], value[order]
        fresh = np.ones(len(s), dtype=bool)
        fresh[1:] = (s[1:] != s[:-1]) | (p[1:] != p[:-1]) | (e[1:] != e[:-1])
        row = np.cumsum(fresh) - 1
        losses = np.zeros((int(row[-1]) + 1 if len(row) else 0, max(len(classes), 1)))
        if len(row):
            np.add.at(losses, (row, c.astype(np.int64)), value)
        first = np.flatnonzero(fresh)
        return cls(
            sample=s[first].astype(np.int64),
            period=p[first].astype(np.int64),
            event=e[first].astype(np.int64),
            losses=losses,
            classes=tuple(classes),
            periods=periods,
            samples=samples,
        )

    @classmethod
    def from_rows(
        cls,
        rows: Iterable[tuple[int, int, int, str, float]],
        *,
        periods: int,
        samples: int,
        classes: Sequence[str] | None = None,
    ) -> EventLosses:
        """From ``(sample, period, event, cover class, loss)`` rows, in any order."""
        records = list(rows)
        names = tuple(sorted(set(classes or ()) | {row[3] for row in records}))
        index = {name: position for position, name in enumerate(names)}
        columns = list(zip(*records, strict=True)) if records else [(), (), (), (), ()]
        return cls.from_arrays(
            np.array(columns[0], dtype=np.int64),
            np.array(columns[1], dtype=np.int64),
            np.array(columns[2], dtype=np.int64),
            np.array([index[name] for name in columns[3]], dtype=np.int64),
            np.array(columns[4], dtype=float),
            classes=names,
            periods=periods,
            samples=samples,
        )

    @classmethod
    def from_splt(
        cls,
        data: bytes,
        *,
        summary_classes: Mapping[int, str],
        samples: int,
    ) -> EventLosses:
        """From the engine's sample period loss table, one summary per cover class.

        ``summary_classes`` names the class of each summary id, read from the
        level's summary-info table. The number of simulated years is read from
        the table's own period weight, which the engine writes as one over it.
        ``samples`` of zero reads the sample count from the table itself.
        """
        header = data[: data.find(b"\n")].decode("utf-8-sig").strip().split(",")
        wanted = ("Period", "PeriodWeight", "EventId", "SummaryId", "SampleId", "Loss")
        missing = [name for name in wanted if name not in header]
        if missing:
            raise CoverError(
                "The sample period loss table has no " + ", ".join(missing) + " column."
            )
        body = data[data.find(b"\n") + 1:]
        if not body.strip():
            names = tuple(sorted(set(summary_classes.values())))
            empty = np.zeros(0, dtype=np.int64)
            return cls.from_arrays(
                empty, empty, empty, empty, np.zeros(0),
                classes=names, periods=1, samples=samples,
            )
        table = np.loadtxt(
            io.BytesIO(body),
            delimiter=",",
            usecols=[header.index(name) for name in wanted],
            ndmin=2,
        )
        weight = float(table[0, 1])
        if weight <= 0:
            raise CoverError("The period loss table states no period weight.")
        periods = int(round(1.0 / weight))
        if samples <= 0:
            samples = int(table[:, 4].max())
        names = tuple(sorted(set(summary_classes.values())))
        position = {name: index for index, name in enumerate(names)}
        summary_ids = table[:, 3].astype(np.int64)
        unknown = set(np.unique(summary_ids).tolist()) - set(summary_classes)
        if unknown:
            raise CoverError(
                f"Summary ids {sorted(unknown)[:5]} have no cover class in the summary-info table."
            )
        lookup = np.zeros(int(summary_ids.max()) + 1, dtype=np.int64)
        for summary_id, name in summary_classes.items():
            if summary_id < len(lookup):
                lookup[summary_id] = position[name]
        return cls.from_arrays(
            table[:, 4].astype(np.int64),
            table[:, 0].astype(np.int64),
            table[:, 2].astype(np.int64),
            lookup[summary_ids],
            table[:, 5],
            classes=names,
            periods=periods,
            samples=samples,
        )

    @property
    def insured(self) -> np.ndarray:
        return self.losses.sum(axis=1)

    def entering(self, layer: LayerTerms) -> np.ndarray:
        columns = [position for position, name in enumerate(self.classes) if name in layer.classes]
        if not columns:
            return np.zeros(len(self.event))
        return self.losses[:, columns].sum(axis=1)

    def year_starts(self) -> np.ndarray:
        """Row index where each (sample, year) group starts, one entry per row."""
        if len(self.event) == 0:
            return np.zeros(0, dtype=np.int64)
        boundary = np.ones(len(self.event), dtype=bool)
        boundary[1:] = (self.sample[1:] != self.sample[:-1]) | (self.period[1:] != self.period[:-1])
        starts = np.flatnonzero(boundary)
        return np.repeat(starts, np.diff(np.append(starts, len(self.event))))


def _within_year_cumsum(values: np.ndarray, starts: np.ndarray) -> np.ndarray:
    """Cumulative sum that restarts at each year's first row."""
    total = np.cumsum(values)
    before = np.concatenate(([0.0], total))[starts]
    return total - before


def _premium_curve(amount: np.ndarray, layer: LayerTerms) -> np.ndarray:
    """Reinstatement premium due for a cumulative amount reinstated in a year."""
    if not layer.charges_premium:
        return np.zeros_like(amount)
    count = layer.reinstatements or 0
    due = np.zeros_like(amount)
    for k in range(1, count + 1):
        start = (k - 1) * layer.limit
        within = np.clip(amount - start, 0.0, layer.limit)
        due += within / layer.limit * layer.rate(k) * layer.premium
    return due


@dataclasses.dataclass(frozen=True, slots=True)
class LayerOutcome:
    """What one layer recovered and cost, per event row."""

    layer: LayerTerms
    recovered: np.ndarray
    premium: np.ndarray
    exhausted_years: int

    @property
    def net(self) -> np.ndarray:
        return self.recovered - self.premium


def apply_layer(losses: EventLosses, layer: LayerTerms, *, limited: bool) -> LayerOutcome:
    """One layer over every event, year by year, in event order."""
    entering = losses.entering(layer)
    per_event = np.clip(entering - layer.attachment, 0.0, layer.limit)
    share = layer.ceded * layer.placed
    if not limited or math.isinf(layer.aggregate):
        return LayerOutcome(layer, per_event * share, np.zeros_like(per_event), 0)

    starts = losses.year_starts()
    asked = _within_year_cumsum(per_event, starts)
    paid_to_date = np.minimum(asked, layer.aggregate)
    before = np.concatenate(([0.0], paid_to_date[:-1]))
    before[starts == np.arange(len(starts))] = 0.0
    recovered = paid_to_date - before

    reinstatable = layer.limit * (layer.reinstatements or 0)
    reinstated_to_date = np.minimum(paid_to_date, reinstatable)
    premium_to_date = _premium_curve(reinstated_to_date, layer)
    premium_before = np.concatenate(([0.0], premium_to_date[:-1]))
    premium_before[starts == np.arange(len(starts))] = 0.0
    premium = premium_to_date - premium_before

    year_last = np.append(np.flatnonzero(np.diff(starts) != 0), len(starts) - 1) if len(starts) else []
    exhausted = int(np.sum(paid_to_date[year_last] >= layer.aggregate - 1e-6)) if len(starts) else 0
    return LayerOutcome(layer, recovered * share, premium * share, exhausted)


# -- the curves ---------------------------------------------------------------------

def _interpolated(next_rp: float, last_rp: float, last_loss: float, rp: float, loss: float) -> float:
    """``oasislmf.pytools.lec.aggreports.write_tables.get_loss``, exactly."""
    if rp == 0 or loss == 0:
        return 0.0
    if rp == next_rp:
        return loss
    if rp < next_rp:
        return (next_rp - rp) * (last_loss - loss) / (last_rp - rp) + loss
    return -1.0


def exceedance(values: np.ndarray, *, periods: int, return_periods: Sequence[float]) -> dict[float, float]:
    """Losses at the return periods, as ``ept`` computes them with a return period file.

    ``values`` holds one sample-mean loss per year that had any loss; years
    without one are left out, as the engine leaves them out.
    """
    wanted = sorted((float(rp) for rp in return_periods), reverse=True)
    curve: dict[float, float] = {}
    position = 0
    last_rp = 0.0
    last_loss = 0.0

    def emit(rp: float, loss: float) -> None:
        nonlocal position, last_rp, last_loss
        while position < len(wanted):
            target = wanted[position]
            if rp > target:
                break
            if periods < target:
                position += 1
                continue
            curve[target] = _interpolated(target, last_rp, last_loss, rp, loss)
            position += 1
        if rp > 0:
            last_rp, last_loss = rp, loss

    rank = 1
    for value in sorted((float(v) for v in values if v > 0), reverse=True):
        if position >= len(wanted):
            break
        emit(periods / rank, value)
        rank += 1
    while position < len(wanted):
        emit(periods / rank if wanted[position] > 0 else 0.0, 0.0)
        rank += 1
    return curve


@dataclasses.dataclass(frozen=True, slots=True)
class CoverMetrics:
    """A result on the mean sample basis, the way the engine's tables state one."""

    average_annual_loss: float
    standard_deviation: float
    aep: dict[float, float]
    oep: dict[float, float]


def metrics(
    losses: EventLosses, net: np.ndarray, *, return_periods: Sequence[float]
) -> CoverMetrics:
    """Average annual loss, its deviation, and both curves, from per-event losses."""
    periods, samples = losses.periods, losses.samples
    annual = np.zeros((samples, periods))
    largest = np.zeros((samples, periods))
    if len(net):
        np.add.at(annual, (losses.sample - 1, losses.period - 1), net)
        np.maximum.at(largest, (losses.sample - 1, losses.period - 1), net)
    count = periods * samples
    total = annual.sum()
    squares = float((annual * annual).sum())
    mean = total / count
    deviation = math.sqrt(max(squares - total * total / count, 0.0) / (count - 1)) if count > 1 else 0.0
    return CoverMetrics(
        average_annual_loss=mean,
        standard_deviation=deviation,
        aep=exceedance(annual.mean(axis=0), periods=periods, return_periods=return_periods),
        oep=exceedance(largest.mean(axis=0), periods=periods, return_periods=return_periods),
    )


# -- the whole calculation ------------------------------------------------------------

@dataclasses.dataclass(frozen=True, slots=True)
class CoverResult:
    """Net of reinsurance under limited cover, with the engine-basis check beside it."""

    limited: CoverMetrics
    unlimited: CoverMetrics
    insured: CoverMetrics
    layers: tuple[dict[str, Any], ...]
    periods: int
    samples: int
    event_order: str = "event number within each year"

    def as_dict(self) -> dict[str, Any]:
        def curve(values: Mapping[float, float]) -> dict[str, float]:
            return {
                (str(int(rp)) if float(rp).is_integer() else str(rp)): round(loss, 2)
                for rp, loss in sorted(values.items())
            }

        def block(value: CoverMetrics) -> dict[str, Any]:
            return {
                "average_annual_loss": round(value.average_annual_loss, 2),
                "standard_deviation": round(value.standard_deviation, 2),
                "aep": curve(value.aep),
                "oep": curve(value.oep),
            }

        return {
            "limited": block(self.limited),
            "unlimited": block(self.unlimited),
            "insured": block(self.insured),
            "layers": list(self.layers),
            "periods": self.periods,
            "samples": self.samples,
            "event_order": self.event_order,
        }


def calculate(
    losses: EventLosses, layers: Sequence[LayerTerms], *, return_periods: Sequence[float]
) -> CoverResult:
    """Apply every layer both ways, and read all three results on the engine's basis."""
    insured = losses.insured
    limited_net = insured.copy()
    unlimited_net = insured.copy()
    summaries: list[dict[str, Any]] = []
    denominator = losses.periods * losses.samples
    for layer in layers:
        limited = apply_layer(losses, layer, limited=True)
        unlimited = apply_layer(losses, layer, limited=False)
        limited_net -= limited.net
        unlimited_net -= unlimited.net
        summaries.append(
            {
                **layer.as_dict(),
                "recovered_aal": round(float(limited.recovered.sum()) / denominator, 2),
                "premium_aal": round(float(limited.premium.sum()) / denominator, 2),
                "unlimited_recovered_aal": round(float(unlimited.recovered.sum()) / denominator, 2),
                "exhausted_share": round(limited.exhausted_years / denominator, 6),
                "applied_as_engine": layer.reinstatements is None,
            }
        )
    return CoverResult(
        limited=metrics(losses, limited_net, return_periods=return_periods),
        unlimited=metrics(losses, unlimited_net, return_periods=return_periods),
        insured=metrics(losses, insured, return_periods=return_periods),
        layers=tuple(summaries),
        periods=losses.periods,
        samples=losses.samples,
    )
