"""Reading Oasis ORD results.

OED is the input standard and ORD is its output counterpart, which is why this
sits beside the reader rather than in the control plane: turning an engine's
output package into loss metrics is a format concern, and a format concern that
lives in a Django app is one nothing else can test or reuse.

What this module is for is narrow and worth stating. An analysis produces a
tarball of ORD tables; a result set needs an average annual loss, a standard
deviation and an exceedance curve. Between those two is a set of choices that
are *not* the reader's to make silently -- which EP calculation, which EP type,
analytical or sample statistics -- because each names a different number and an
analyst quoting one has to know which they hold.

So every selection is explicit, recorded on the result, and refused rather than
substituted. A package that does not carry the requested basis reports that it
does not, instead of quietly returning the nearest thing it has. A loss labelled
as something it is not is the failure this whole platform exists to prevent.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import re
import tarfile
import zipfile
from collections.abc import Iterator, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

__all__ = [
    "EP_CALC",
    "EP_TYPE",
    "OrdError",
    "OrdPackage",
    "ResultMetrics",
    "SAMPLE_TYPE",
    "metrics_for",
    "open_package",
]

#: ORD exceedance-probability calculation bases. These are the standard's own
#: codes; naming them here means a result can say which one it quotes rather
#: than carrying a bare integer nobody downstream can interpret.
EP_CALC: Mapping[int, str] = {
    1: "analytical mean",
    2: "full uncertainty",
    3: "per sample mean",
    4: "mean sample",
}

#: ORD exceedance-probability types. OEP is the largest single occurrence in a
#: year; AEP is the aggregate of all of them. They are different questions and
#: a curve labelled with the wrong one is a materially wrong answer.
EP_TYPE: Mapping[int, str] = {
    1: "OEP",
    2: "OEP TVaR",
    3: "AEP",
    4: "AEP TVaR",
}

#: ORD average-loss sample types.
SAMPLE_TYPE: Mapping[int, str] = {
    1: "analytical",
    2: "sample",
}

#: The perspective each ORD filename prefix belongs to. Oasis names its output
#: by perspective and summary level, and the prefix is the only place the
#: perspective appears -- the rows themselves do not say.
PERSPECTIVE_PREFIX: Mapping[str, str] = {
    "gul": "ground_up",
    "il": "insured",
    "ri": "reinsurance",
}

#: ``gul_S1_ept.csv`` and friends. The summary level is captured because a
#: portfolio-level result and a location-level one are different tables with
#: the same shape, and reading the wrong one silently would produce a number
#: that is right for a question nobody asked.
_FILENAME = re.compile(
    r"^(?P<perspective>gul|il|ri)_S(?P<summary>\d+)_(?P<table>[a-z-]+)\.csv$"
)


class OrdError(Exception):
    """Raised when an output package cannot be read as ORD."""


@dataclasses.dataclass(frozen=True, slots=True)
class ResultMetrics:
    """The loss metrics behind one result set, and what basis they are on."""

    perspective: str
    summary_level: int
    average_annual_loss: Decimal | None
    standard_deviation: Decimal | None
    return_period_losses: dict[str, str]
    #: Which of the several numbers in the package these are. Carried onto the
    #: result set so section 9's caveat block can state it.
    basis: dict[str, str]
    #: Everything the package held, so a reader can see what was not used.
    tables_present: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "perspective": self.perspective,
            "summary_level": self.summary_level,
            "average_annual_loss": (
                str(self.average_annual_loss)
                if self.average_annual_loss is not None
                else None
            ),
            "standard_deviation": (
                str(self.standard_deviation)
                if self.standard_deviation is not None
                else None
            ),
            "return_period_losses": dict(self.return_period_losses),
            "basis": dict(self.basis),
            "tables_present": list(self.tables_present),
        }


class OrdPackage:
    """One Oasis output package, addressed by perspective and table."""

    def __init__(self, members: Mapping[str, bytes]) -> None:
        self._members = dict(members)
        self._index: dict[tuple[str, int, str], str] = {}
        for name in self._members:
            match = _FILENAME.match(name.rsplit("/", 1)[-1])
            if match is None:
                continue
            key = (
                match.group("perspective"),
                int(match.group("summary")),
                match.group("table"),
            )
            # First wins. A package holding the same table twice is malformed,
            # and picking the later one arbitrarily would hide that.
            self._index.setdefault(key, name)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._members))

    def perspectives(self) -> tuple[str, ...]:
        """Which perspectives the package actually carries results for."""
        found = {PERSPECTIVE_PREFIX[key[0]] for key in self._index}
        return tuple(sorted(found))

    def summary_levels(self, perspective: str) -> tuple[int, ...]:
        prefix = _prefix_for(perspective)
        return tuple(sorted({key[1] for key in self._index if key[0] == prefix}))

    def tables(self, perspective: str, summary_level: int) -> tuple[str, ...]:
        prefix = _prefix_for(perspective)
        return tuple(
            sorted(
                key[2]
                for key in self._index
                if key[0] == prefix and key[1] == summary_level
            )
        )

    def rows(
        self, perspective: str, summary_level: int, table: str
    ) -> list[dict[str, str]]:
        """Every row of one ORD table, or nothing where the package lacks it."""
        prefix = _prefix_for(perspective)
        name = self._index.get((prefix, summary_level, table))
        if name is None:
            return []
        text = self._members[name].decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(text)))


def open_package(payload: bytes) -> OrdPackage:
    """Read an Oasis output package, whichever container it arrived in.

    Oasis serves outputs as a gzipped tar; some deployments and every manual
    re-upload produce a zip. Accepting both costs one branch and saves an
    operator discovering that a package they can open is one CASS cannot.
    """
    members = _tar_members(payload)
    if members is None:
        members = _zip_members(payload)
    if members is None:
        raise OrdError(
            "The output package is neither a gzipped tar nor a zip archive, so "
            "CASS cannot read the results out of it."
        )
    if not members:
        raise OrdError("The output package is empty.")
    return OrdPackage(members)


def metrics_for(
    package: OrdPackage,
    *,
    perspective: str,
    summary_level: int = 1,
    ep_calc: int = 4,
    ep_type: int = 3,
    sample_type: int = 2,
) -> ResultMetrics:
    """The headline metrics for one perspective, on a stated basis.

    The defaults are the mean-sample AEP curve and the sample mean loss, which
    is what a stochastic run is normally quoted on. They are defaults rather
    than assumptions: every one is recorded in ``basis`` and travels onto the
    result, so a reader can see which of the package's several numbers they
    are looking at.
    """
    if perspective not in PERSPECTIVE_PREFIX.values():
        raise OrdError(
            f"{perspective} is not a perspective Oasis reports. "
            f"Expected one of {', '.join(sorted(set(PERSPECTIVE_PREFIX.values())))}."
        )

    available = package.tables(perspective, summary_level)
    if not available:
        raise OrdError(
            f"The output package holds no {perspective.replace('_', '-')} results "
            f"at summary level {summary_level}. It carries: "
            f"{', '.join(package.perspectives()) or 'nothing readable'}."
        )

    mean, deviation = _average_loss(package, perspective, summary_level, sample_type)
    curve = _exceedance(package, perspective, summary_level, ep_calc, ep_type)

    return ResultMetrics(
        perspective=perspective,
        summary_level=summary_level,
        average_annual_loss=mean,
        standard_deviation=deviation,
        return_period_losses=curve,
        basis={
            "average_loss": SAMPLE_TYPE.get(sample_type, str(sample_type)),
            "ep_calculation": EP_CALC.get(ep_calc, str(ep_calc)),
            "ep_type": EP_TYPE.get(ep_type, str(ep_type)),
            "summary_level": str(summary_level),
        },
        tables_present=available,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class EventLoss:
    """One event's loss to the portfolio, as the moment table reports it."""

    event_id: int
    mean_loss: Decimal
    standard_deviation: Decimal | None
    maximum_loss: Decimal | None
    event_rate: Decimal | None
    chance_of_loss: Decimal | None
    impacted_exposure: Decimal | None

    def as_dict(self) -> dict[str, Any]:
        def text(value: Decimal | None) -> str | None:
            return str(value) if value is not None else None

        return {
            "event_id": self.event_id,
            "mean_loss": str(self.mean_loss),
            "standard_deviation": text(self.standard_deviation),
            "maximum_loss": text(self.maximum_loss),
            "event_rate": text(self.event_rate),
            "chance_of_loss": text(self.chance_of_loss),
            "impacted_exposure": text(self.impacted_exposure),
        }


def event_losses(
    package: OrdPackage,
    *,
    perspective: str,
    summary_level: int = 1,
    sample_type: int = 2,
    limit: int | None = None,
) -> list[EventLoss]:
    """The event loss table, largest loss first.

    Section 3 asks the results workspace to show which events drive a number,
    and this is where that comes from: the moment event loss table, one row per
    event, read on the same sample basis as the headline metrics so the table
    and the number above it describe the same calculation.

    Events that produced no loss are left out. A table padded with zeroes would
    be tens of thousands of rows long and say nothing.
    """
    found: list[EventLoss] = []
    for row in package.rows(perspective, summary_level, "melt"):
        if _int(row.get("SampleType")) != sample_type:
            continue
        event_id = _int(row.get("EventId"))
        mean = _decimal(row.get("MeanLoss"))
        if event_id is None or mean is None or mean <= 0:
            continue
        found.append(
            EventLoss(
                event_id=event_id,
                mean_loss=mean,
                standard_deviation=_decimal(row.get("SDLoss")),
                maximum_loss=_decimal(row.get("MaxLoss")),
                event_rate=_decimal(row.get("EventRate")),
                chance_of_loss=_decimal(row.get("ChanceOfLoss")),
                impacted_exposure=_decimal(row.get("MeanImpactedExposure")),
            )
        )

    found.sort(key=lambda item: item.mean_loss, reverse=True)
    return found[:limit] if limit else found


# -- the tables -------------------------------------------------------------

def _average_loss(
    package: OrdPackage, perspective: str, summary_level: int, sample_type: int
) -> tuple[Decimal | None, Decimal | None]:
    """Average annual loss and its standard deviation, from the period ALT.

    ``palt`` is the period average loss table: one row per sample type, and
    the mean over periods is the AAL. Nothing is computed here beyond reading
    it -- an average of the event table would be a different number, and one
    this platform has no business inventing.
    """
    for table in ("palt", "alt"):
        for row in package.rows(perspective, summary_level, table):
            if _int(row.get("SampleType")) != sample_type:
                continue
            return _decimal(row.get("MeanLoss")), _decimal(row.get("SDLoss"))
    return None, None


def _exceedance(
    package: OrdPackage,
    perspective: str,
    summary_level: int,
    ep_calc: int,
    ep_type: int,
) -> dict[str, str]:
    """The exceedance curve, as return period to loss.

    Only rows on the requested basis are taken. An EPT carries several
    calculations and both OEP and AEP in one file, and mixing them would draw a
    curve that is not any of them.
    """
    curve: dict[str, str] = {}
    for row in package.rows(perspective, summary_level, "ept"):
        if _int(row.get("EPCalc")) != ep_calc or _int(row.get("EPType")) != ep_type:
            continue
        period = _decimal(row.get("ReturnPeriod"))
        loss = _decimal(row.get("Loss"))
        if period is None or loss is None:
            continue
        # Return periods are whole years in every reporting set CASS uses, and
        # a key of "100" reads better than "100.000000" everywhere it appears.
        key = str(int(period)) if period == period.to_integral_value() else str(period)
        curve[key] = str(loss)
    return dict(sorted(curve.items(), key=lambda item: float(item[0])))


# -- containers -------------------------------------------------------------

def _tar_members(payload: bytes) -> dict[str, bytes] | None:
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
            return dict(_safe_tar_entries(archive))
    except tarfile.TarError:
        return None


def _safe_tar_entries(archive: tarfile.TarFile) -> Iterator[tuple[str, bytes]]:
    for member in archive.getmembers():
        if not member.isfile() or not _safe_path(member.name):
            continue
        handle = archive.extractfile(member)
        if handle is None:
            continue
        yield member.name, handle.read()


def _zip_members(payload: bytes) -> dict[str, bytes] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            return {
                name: archive.read(name)
                for name in archive.namelist()
                if not name.endswith("/") and _safe_path(name)
            }
    except zipfile.BadZipFile:
        return None


def _safe_path(name: str) -> bool:
    """Refuse an entry that would escape the directory it is read into.

    The package comes from an engine rather than a user, but it arrives over
    the network and is written to disk by whatever reads it, and an archive
    naming ``../`` is the oldest trick there is.
    """
    if name.startswith("/") or name.startswith("\\"):
        return False
    return ".." not in name.replace("\\", "/").split("/")


# -- parsing ----------------------------------------------------------------

def _prefix_for(perspective: str) -> str:
    for prefix, name in PERSPECTIVE_PREFIX.items():
        if name == perspective:
            return prefix
    raise OrdError(f"{perspective} is not a perspective Oasis reports.")


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return None


def describe(package: OrdPackage) -> dict[str, Any]:
    """What the package holds, for a manifest."""
    return {
        "perspectives": list(package.perspectives()),
        "files": list(package.names),
        "tables": {
            perspective: {
                str(level): list(package.tables(perspective, level))
                for level in package.summary_levels(perspective)
            }
            for perspective in package.perspectives()
        },
    }
