"""The OpenQuake reference calculation: the same portfolio, the engine's own way.

Section 7 and work package 4 step 9 ask for one comparison before the Oasis
representation is accepted: the same portfolio, the same ground motion and the
same GEM vulnerability functions, run by OpenQuake itself, against what CASS
computed through Oasis. It is the only measurement that can say whether the
conversion changes the answer -- intensity binned into a footprint, damage
discretised into bins, and intensity measures carried as correlated area-peril
channels (ADR 8).

Three things make this a comparison rather than two calculations.

**The same events.** The risk job is chained onto the hazard calculation the
footprint was built from, so both sides read one set of ground-motion fields.
Nothing is recomputed and no seed has to match.

**The same mixture.** A CASS vulnerability identifier is a blend of GEM
taxonomies. The exposure written here splits each coverage's value across those
taxonomies at the weights the blend used, so OpenQuake reads the same buildings
CASS modelled -- through GEM's own continuous functions rather than through a
blended, discretised table.

**The same money.** Every asset's value comes from the published coverage TIV,
and the weights sum to one, so both sides insure the same amount. The
reconciliation below is arithmetic, and a comparison that does not reconcile is
refused rather than reported: a difference in value would look exactly like a
difference in method.

What it deliberately does not hold constant is the point. OpenQuake reads exact
ground motion and continuous loss ratios; CASS reads binned intensity and
discretised damage. What is left between them is the conversion's own error.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import math
from collections.abc import Iterable, Mapping, Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any
from xml.sax.saxutils import escape

from cass_core.evidence import EvidenceError, allocate_value

REFERENCE_VERSION = "1.0.0"

#: NRML namespace, as the engine's exposure reader expects it.
NRML_NAMESPACE = "http://openquake.org/xmlns/nrml/0.5"

#: GEM's loss categories as OpenQuake names them in an exposure and a job.
#: ``fatalities`` is not here: it is a ratio of occupants rather than of money
#: and must never reach a column the engine multiplies by a value.
LOSS_TYPES: tuple[str, ...] = ("structural", "nonstructural", "contents")

#: The OED column each coverage type carries its value in.
COVERAGE_COLUMNS: Mapping[int, str] = {
    1: "BuildingTIV",
    2: "OtherTIV",
    3: "ContentsTIV",
    4: "BITIV",
}

#: How far the assets written may fall from the coverage value they came from
#: before the comparison is refused. One cent per portfolio: the split is
#: rounded to the cent per asset, and nothing else may move.
RECONCILIATION_TOLERANCE = Decimal("0.01")

#: The keys statuses whose value is modelled. Everything else is not-at-risk or
#: failed, and belongs in neither calculation.
MAPPED_STATUS = "success"


class ReferenceError(Exception):
    """Raised when a reference calculation cannot be assembled or read."""


@dataclasses.dataclass(frozen=True, slots=True)
class Asset:
    """One row of the exposure OpenQuake reads: a taxonomy at a coordinate."""

    asset_id: str
    location_id: str
    longitude: float
    latitude: float
    taxonomy: str
    values: Mapping[str, Decimal]

    def value(self, loss_type: str) -> Decimal:
        return self.values.get(loss_type, Decimal(0))


@dataclasses.dataclass(frozen=True, slots=True)
class ReferenceExposure:
    """The portfolio as OpenQuake will read it, with what it was built from."""

    assets: tuple[Asset, ...]
    loss_types: tuple[str, ...]
    #: Value by loss type, as the keys file accounted for it.
    source_value: Mapping[str, Decimal]
    #: Value by loss type, as the assets carry it.
    written_value: Mapping[str, Decimal]
    #: Keys rows that reached no dictionary entry, by vulnerability identifier.
    unmatched: tuple[str, ...]
    #: How each asset was placed: at the centroid of the cell CASS mapped it to,
    #: or at the location's own coordinate.
    placement: Mapping[str, int] = dataclasses.field(default_factory=dict)
    reference_version: str = REFERENCE_VERSION

    @property
    def difference(self) -> Decimal:
        return sum(
            (
                self.written_value.get(name, Decimal(0))
                - self.source_value.get(name, Decimal(0))
                for name in self.loss_types
            ),
            Decimal(0),
        )

    @property
    def reconciles(self) -> bool:
        """Whether both sides insure the same money, to the cent."""
        return not self.unmatched and abs(self.difference) <= RECONCILIATION_TOLERANCE

    def as_dict(self) -> dict[str, Any]:
        return {
            "reference_version": self.reference_version,
            "assets": len(self.assets),
            "locations": len({item.location_id for item in self.assets}),
            "taxonomies": len({item.taxonomy for item in self.assets}),
            "loss_types": list(self.loss_types),
            "source_value": {k: str(v) for k, v in sorted(self.source_value.items())},
            "written_value": {k: str(v) for k, v in sorted(self.written_value.items())},
            "difference": str(self.difference),
            "reconciles": self.reconciles,
            "unmatched": list(self.unmatched),
            "placement": dict(sorted(self.placement.items())),
        }


def build_exposure(
    keys_rows: Iterable[Mapping[str, Any]],
    *,
    locations: Mapping[str, Mapping[str, Any]],
    dictionary: Mapping[str, Any],
    cell_centroids: Mapping[int, tuple[float, float]] | None = None,
) -> ReferenceExposure:
    """Write the portfolio as taxonomies, from the keys the run actually used.

    ``keys_rows`` is the run's ``keys.csv``: one row per location, coverage and
    channel, carrying the vulnerability identifier that answered it and the
    share of its class that channel holds. ``dictionary`` is the vulnerability
    set's provenance dictionary, which says which GEM taxonomies each identifier
    blends and at what weight.

    Value moves through both weights and nowhere else: coverage value, times the
    channel's share of its class, times the taxonomy's share of the blend. A
    location mapped to one measure and one taxonomy therefore carries its whole
    coverage value, which is what makes the reconciliation meaningful.

    ``cell_centroids`` places each asset at the centre of the area-peril cell
    CASS mapped it to, and it is what makes this a comparison of method rather
    than of geography. OpenQuake associates an asset to its nearest hazard site;
    CASS maps a location to the cell that contains it. Near a cell edge those
    are different cells, and then the two sides would read different ground
    motion for the same building -- a spatial difference that would arrive
    looking like a conversion error. Without it, assets sit at their own
    coordinates and the report says so.
    """
    entries = _entries_by_id(dictionary)
    source: dict[str, Decimal] = {}
    unmatched: set[str] = set()
    #: One entry per location and coverage: the money, and every channel that
    #: answers it. Grouped before anything is split, because the split has to
    #: add back to the coverage's value and cannot if each row rounds alone.
    groups: dict[tuple[str, int], dict[str, Any]] = {}

    for row in keys_rows:
        if str(row.get("Status") or "").strip().lower() != MAPPED_STATUS:
            continue
        identifier = str(row.get("VulnerabilityID") or "").strip()
        entry = entries.get(identifier)
        if entry is None:
            unmatched.add(identifier or "(none)")
            continue

        location_id = str(row.get("LocID") or "").strip()
        location = locations.get(location_id)
        if location is None:
            raise ReferenceError(
                f"The keys file maps {location_id!r}, which is not in the exposure "
                "the comparison was given. Both must come from the same run."
            )

        coverage_type = int(row.get("CoverageTypeID") or 0)
        loss_type = str(entry.get("loss_category") or "").strip()
        if loss_type not in LOSS_TYPES:
            raise ReferenceError(
                f"Vulnerability {identifier} is recorded against {loss_type!r}, which "
                "is not a loss type OpenQuake reads as money. Known: "
                + ", ".join(LOSS_TYPES)
                + "."
            )

        group = groups.get((location_id, coverage_type))
        if group is None:
            value = _coverage_value(location, coverage_type)
            # Value is accounted once per location and coverage, however many
            # channels answer it: the channels describe one sum of money.
            source[loss_type] = source.get(loss_type, Decimal(0)) + value
            group = groups[(location_id, coverage_type)] = {
                "location_id": location_id,
                "location": location,
                "loss_type": loss_type,
                "value": value,
                "channels": [],
            }
        group["channels"].append((row, _weight(row.get("ChannelWeight"), default=Decimal(1)), entry, identifier))

    assets, written, placement = _split(groups, centroids=cell_centroids)

    loss_types = tuple(name for name in LOSS_TYPES if name in source or name in written)
    return ReferenceExposure(
        assets=assets,
        loss_types=loss_types,
        source_value=source,
        written_value=written,
        unmatched=tuple(sorted(unmatched)),
        placement=placement,
    )


def _split(
    groups: Mapping[tuple[str, int], Mapping[str, Any]],
    *,
    centroids: Mapping[int, tuple[float, float]] | None,
) -> tuple[tuple[Asset, ...], dict[str, Decimal], dict[str, int]]:
    """Split each coverage's value across its channels and their taxonomies.

    Both splits go through the platform's own allocation rule: shares rounded to
    the cent with the residue placed on the largest, so the parts add back to the
    whole exactly (ADR 5, and the reconciliation section 15 requires of every
    enrichment run). Rounding each asset on its own leaves a portfolio a few
    cents from the value it came from, and a comparison that cannot say the two
    sides insure the same money is measuring the wrong thing.
    """
    assets: list[Asset] = []
    written: dict[str, Decimal] = {}
    placement: dict[str, int] = {}

    for group in groups.values():
        value = group["value"]
        if value <= 0:
            continue
        channels = group["channels"]
        loss_type = group["loss_type"]
        try:
            allocated = allocate_value(
                value, [(index, weight) for index, (_r, weight, _e, _i) in enumerate(channels)]
            )
        except EvidenceError as exc:
            raise ReferenceError(
                f"The channels answering {group['location_id']} coverage "
                f"{loss_type} carry no usable weight: {exc}."
            ) from None

        for index, share in allocated:
            if share <= 0:
                continue
            row, _weight_used, entry, identifier = channels[index]
            longitude, latitude, placed = _placement(
                row,
                location=group["location"],
                location_id=group["location_id"],
                centroids=centroids,
            )
            placement[placed] = placement.get(placed, 0) + 1

            components = [
                (str(item.get("taxonomy") or "").strip(), _weight(item.get("weight"), default=Decimal(0)))
                for item in entry.get("blended_from") or ()
            ]
            components = [(name, weight) for name, weight in components if name and weight > 0]
            if not components:
                raise ReferenceError(
                    f"Vulnerability {identifier} names no taxonomy behind it, so the "
                    "value it answers for cannot be given to any GEM function."
                )

            for taxonomy, carried in allocate_value(share, components):
                if carried <= 0:
                    continue
                assets.append(
                    Asset(
                        asset_id=_asset_id(len(assets)),
                        location_id=group["location_id"],
                        longitude=longitude,
                        latitude=latitude,
                        taxonomy=taxonomy,
                        values={loss_type: carried},
                    )
                )
                written[loss_type] = written.get(loss_type, Decimal(0)) + carried

    return tuple(assets), written, placement


def exposure_csv(exposure: ReferenceExposure) -> bytes:
    """The asset table, in the columns the engine's exposure reader takes."""
    buffer = io.StringIO(newline="")
    columns = ["id", "lon", "lat", "taxonomy", "number", *exposure.loss_types]
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for asset in exposure.assets:
        writer.writerow(
            {
                "id": asset.asset_id,
                "lon": f"{asset.longitude:.6f}",
                "lat": f"{asset.latitude:.6f}",
                "taxonomy": asset.taxonomy,
                "number": 1,
                **{name: str(asset.value(name)) for name in exposure.loss_types},
            }
        )
    return buffer.getvalue().encode("utf-8")


def exposure_xml(
    exposure: ReferenceExposure,
    *,
    identifier: str = "cass_reference",
    description: str = "CASS reference comparison",
    currency: str = "USD",
    assets_file: str = "exposure.csv",
    taxonomy_source: str = "GEM building taxonomy",
) -> bytes:
    """The exposure model that points at the asset table."""
    if not exposure.loss_types:
        raise ReferenceError(
            "The portfolio mapped no value to any loss type, so there is nothing "
            "for a reference calculation to compute."
        )
    cost_types = "\n".join(
        f'          <costType name="{name}" type="aggregated" unit="{escape(currency)}"/>'
        for name in exposure.loss_types
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<nrml xmlns="{NRML_NAMESPACE}">\n'
        f'  <exposureModel id="{escape(identifier)}" category="buildings" '
        f'taxonomySource="{escape(taxonomy_source)}">\n'
        f"    <description>{escape(description)}</description>\n"
        "    <conversions>\n"
        "      <costTypes>\n"
        f"{cost_types}\n"
        "      </costTypes>\n"
        "    </conversions>\n"
        f"    <assets>{escape(assets_file)}</assets>\n"
        "  </exposureModel>\n"
        "</nrml>\n"
    ).encode()


def job_ini(
    exposure: ReferenceExposure,
    *,
    description: str,
    vulnerability_files: Mapping[str, str],
    exposure_file: str = "exposure.xml",
    asset_hazard_distance: float = 5.0,
    master_seed: int = 42,
    ignore_covs: bool = False,
    minimum_asset_loss: float = 0.0,
) -> bytes:
    """The risk job, to be submitted against the hazard calculation's id.

    The hazard is not named here. It is passed to the engine as the job to chain
    onto, which is what makes both sides read the same events; a job naming its
    own sources would compute new ones.

    ``ignore_covs`` is the choice between comparing expectations and comparing
    distributions. Left false, OpenQuake samples each asset's loss ratio from the
    GEM beta distribution as Oasis samples from the discretised damage bins, and
    the two tails are comparable. Set it true and both sides reduce to means,
    which is the cleaner test of the discretisation alone.
    """
    missing = [name for name in exposure.loss_types if name not in vulnerability_files]
    if missing:
        raise ReferenceError(
            "The portfolio carries "
            + ", ".join(missing)
            + " value and no vulnerability file was given for it. OpenQuake would "
            "report zero loss for that value rather than refuse."
        )
    lines = [
        "[general]",
        f"description = {description}",
        "calculation_mode = event_based_risk",
        "",
        "[exposure]",
        f"exposure_file = {exposure_file}",
        "",
        "[vulnerability]",
    ]
    lines += [
        f"{name}_vulnerability_file = {vulnerability_files[name]}"
        for name in exposure.loss_types
    ]
    lines += [
        "",
        "[risk_calculation]",
        f"asset_hazard_distance = {asset_hazard_distance:g}",
        f"master_seed = {master_seed:d}",
        f"ignore_covs = {'true' if ignore_covs else 'false'}",
        f"minimum_asset_loss = {minimum_asset_loss:g}",
        "avg_losses = true",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def files(
    exposure: ReferenceExposure,
    *,
    vulnerability: Mapping[str, bytes],
    description: str,
    **options: Any,
) -> dict[str, bytes]:
    """Everything the engine needs for the reference job, keyed by filename."""
    names = {name: f"vulnerability_{name}.xml" for name in exposure.loss_types}
    missing = [name for name in exposure.loss_types if name not in vulnerability]
    if missing:
        raise ReferenceError(
            "No vulnerability file was given for: " + ", ".join(missing) + "."
        )
    produced = {
        "job.ini": job_ini(
            exposure,
            description=description,
            vulnerability_files=names,
            **options,
        ),
        "exposure.xml": exposure_xml(exposure),
        "exposure.csv": exposure_csv(exposure),
    }
    produced.update({names[name]: vulnerability[name] for name in exposure.loss_types})
    return produced


# -- reading what the engine computed ---------------------------------------------

@dataclasses.dataclass(frozen=True, slots=True)
class ReferenceLosses:
    """OpenQuake's own answer for the portfolio, event by event."""

    by_event: Mapping[int, Decimal]
    effective_time: float
    by_asset: Mapping[str, Decimal] = dataclasses.field(default_factory=dict)
    reference_version: str = REFERENCE_VERSION

    @property
    def total(self) -> Decimal:
        return sum(self.by_event.values(), Decimal(0))

    @property
    def average_annual_loss(self) -> Decimal:
        """Total loss over the time the event set represents."""
        if self.effective_time <= 0:
            raise ReferenceError(
                "The reference calculation has no effective time, so no annual "
                "rate can be derived from its losses."
            )
        return self.total / Decimal(str(self.effective_time))

    def exceedance(self, return_periods: Sequence[int]) -> dict[str, Decimal]:
        """Occurrence exceedance probability losses, by return period.

        The same basis as the Oasis side: events ordered by loss, and the loss at
        a return period is the largest loss whose rank rate is at least the
        wanted rate. A return period longer than the event set can support is
        reported as absent rather than extrapolated.
        """
        losses = sorted(self.by_event.values(), reverse=True)
        found: dict[str, Decimal] = {}
        for period in return_periods:
            rank = self.effective_time / period
            index = int(math.floor(rank)) - 1
            if index < 0 or index >= len(losses):
                continue
            found[str(period)] = losses[index]
        return found

    def as_dict(self) -> dict[str, Any]:
        return {
            "reference_version": self.reference_version,
            "events_with_loss": len(self.by_event),
            "effective_time": self.effective_time,
            "total_loss": str(self.total),
            "average_annual_loss": str(self.average_annual_loss),
            "assets": len(self.by_asset),
        }


#: Column names the engine has used for the event and the loss in
#: ``risk_by_event``. Matched by name rather than position, because the export's
#: column order has moved between engine versions and a positional reader would
#: produce plausible nonsense.
_EVENT_COLUMNS = ("event_id", "eid", "event")
_LOSS_COLUMNS = ("loss", "loss_value", "total_loss")
_ASSET_COLUMNS = ("asset_id", "asset", "id")


def read_losses(
    exports: Mapping[str, bytes], *, effective_time: float
) -> ReferenceLosses:
    """Read the engine's ``risk_by_event`` export into one loss per event.

    Losses are summed across loss types and aggregation rows, which is the
    portfolio ground-up loss the Oasis side reports. Reading the export rather
    than the datastore keeps this on the engine's supported boundary.
    """
    table = _find_export(exports, "risk_by_event")
    rows = list(_rows(table))
    if not rows:
        raise ReferenceError(
            "The reference calculation produced no event losses. Its exposure "
            "may have fallen outside the hazard sites."
        )

    event_column = _column(rows[0], _EVENT_COLUMNS, "event")
    loss_column = _column(rows[0], _LOSS_COLUMNS, "loss")

    by_event: dict[int, Decimal] = {}
    for row in rows:
        try:
            event = int(float(row[event_column]))
            loss = Decimal(str(row[loss_column] or "0"))
        except (KeyError, ValueError, ArithmeticError) as exc:
            raise ReferenceError(
                f"The risk_by_event export has a row this reader cannot use: {exc}"
            ) from None
        if loss <= 0:
            continue
        by_event[event] = by_event.get(event, Decimal(0)) + loss

    by_asset: dict[str, Decimal] = {}
    average = _find_export(exports, "avg_losses", required=False)
    if average is not None:
        for row in _rows(average):
            asset = row.get(_column(row, _ASSET_COLUMNS, "asset", required=False) or "")
            if not asset:
                continue
            total = sum(
                (
                    Decimal(str(row[name] or "0"))
                    for name in LOSS_TYPES
                    if name in row and _is_number(row[name])
                ),
                Decimal(0),
            )
            by_asset[str(asset)] = by_asset.get(str(asset), Decimal(0)) + total

    return ReferenceLosses(
        by_event=by_event, effective_time=effective_time, by_asset=by_asset
    )


# -- the comparison ----------------------------------------------------------------

def compare(
    *,
    exposure: ReferenceExposure,
    reference: ReferenceLosses,
    oasis_average_annual_loss: Decimal,
    oasis_return_period_losses: Mapping[str, Decimal] | None = None,
    tolerances: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Measure one against the other, and decide only where a tolerance exists.

    Ratios rather than differences, because the question is whether the Oasis
    representation is biased, not by how many dollars a particular book differs.
    A ratio of one is agreement; below one, CASS understates what the engine's
    own calculation produced.

    Nothing here passes itself. With no approved tolerance the measurement is
    reported as undecided, the same way the conversion QA gate reports
    (section 7): a comparison that graded itself would be a formality.
    """
    if not exposure.reconciles:
        raise ReferenceError(
            "The reference exposure does not carry the same value as the keys it "
            f"was built from: {exposure.difference} difference"
            + (
                ", and " + str(len(exposure.unmatched)) + " identifiers reached no "
                "dictionary entry"
                if exposure.unmatched
                else ""
            )
            + ". The two sides would be insuring different portfolios."
        )

    stated = dict(tolerances or {})
    reference_aal = reference.average_annual_loss
    measurements = [
        _measurement(
            key="average_annual_loss",
            what="Average annual loss against the OpenQuake reference",
            cass=oasis_average_annual_loss,
            engine=reference_aal,
            tolerance=stated.get("average_annual_loss"),
        )
    ]

    wanted = sorted(
        (int(period) for period in (oasis_return_period_losses or {})), reverse=False
    )
    engine_periods = reference.exceedance(wanted) if wanted else {}
    for period in wanted:
        engine_loss = engine_periods.get(str(period))
        if engine_loss is None:
            continue
        measurements.append(
            _measurement(
                key=f"return_period_{period}",
                what=f"{period}-year occurrence exceedance loss",
                cass=Decimal(str((oasis_return_period_losses or {})[str(period)])),
                engine=engine_loss,
                tolerance=stated.get("return_period"),
            )
        )

    decided = [item for item in measurements if item["decided"]]
    return {
        "reference_version": REFERENCE_VERSION,
        "exposure": exposure.as_dict(),
        "reference": reference.as_dict(),
        "measurements": measurements,
        "decided": bool(decided),
        "within_tolerance": all(item["within_tolerance"] for item in decided)
        if decided
        else None,
        "note": (
            "Measured against OpenQuake's own event-based risk calculation on the "
            "same events and the same GEM functions. No tolerance is approved, so "
            "nothing here passes or fails."
        )
        if not decided
        else "",
    }


def _measurement(
    *, key: str, what: str, cass: Decimal, engine: Decimal, tolerance: float | None
) -> dict[str, Any]:
    ratio = float(cass / engine) if engine else None
    within = None
    if tolerance is not None and ratio is not None:
        within = abs(ratio - 1.0) <= float(tolerance)
    return {
        "key": key,
        "what": what,
        "cass": str(cass),
        "openquake": str(engine),
        "ratio": ratio,
        "tolerance": tolerance,
        "decided": tolerance is not None and ratio is not None,
        "within_tolerance": bool(within) if within is not None else None,
    }


# -- reading the inputs -------------------------------------------------------------

def _entries_by_id(dictionary: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    entries = dictionary.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ReferenceError(
            "The vulnerability dictionary lists no entries, so no identifier can be "
            "traced back to the taxonomies behind it."
        )
    return {str(item.get("vulnerability_id")): item for item in entries}


def _coverage_value(location: Mapping[str, Any], coverage_type: int) -> Decimal:
    column = COVERAGE_COLUMNS.get(coverage_type)
    if column is None:
        raise ReferenceError(
            f"Coverage type {coverage_type} has no OED value column, so the value "
            "behind it cannot be found."
        )
    raw = location.get(column)
    if raw in (None, ""):
        return Decimal(0)
    try:
        # To the cent, which is the precision the split allocates in and the
        # precision money is reconciled at.
        return Decimal(str(raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    except ArithmeticError:
        raise ReferenceError(
            f"{column} on location {location.get('LocNumber', '?')} is not a number: "
            f"{raw!r}."
        ) from None


def _placement(
    row: Mapping[str, Any],
    *,
    location: Mapping[str, Any],
    location_id: str,
    centroids: Mapping[int, tuple[float, float]] | None,
) -> tuple[float, float, str]:
    """Where the asset sits, and which of the two rules put it there."""
    if centroids:
        try:
            area_peril = int(row.get("AreaPerilID") or 0)
        except (TypeError, ValueError):
            area_peril = 0
        centroid = centroids.get(area_peril)
        if centroid is not None:
            return centroid[0], centroid[1], "cell_centroid"
    longitude, latitude = _coordinates(location, location_id)
    return longitude, latitude, "location_coordinate"


def _coordinates(location: Mapping[str, Any], location_id: str) -> tuple[float, float]:
    try:
        return float(location["Longitude"]), float(location["Latitude"])
    except (KeyError, TypeError, ValueError):
        raise ReferenceError(
            f"Location {location_id} has no usable coordinate pair, and OpenQuake "
            "associates an asset to hazard by position."
        ) from None


def _weight(value: Any, *, default: Decimal) -> Decimal:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except ArithmeticError:
        raise ReferenceError(f"{value!r} is not a usable weight.") from None


def _asset_id(index: int) -> str:
    return f"a{index + 1:06d}"


def _rows(payload: bytes) -> Iterable[dict[str, str]]:
    """Rows of an engine export, past the provenance comment it starts with."""
    text = payload.decode("utf-8-sig")
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def _find_export(
    exports: Mapping[str, bytes], stem: str, *, required: bool = True
) -> bytes | None:
    for name, payload in sorted(exports.items()):
        if stem in name:
            return payload
    if required:
        raise ReferenceError(
            f"The reference calculation exported no {stem}. It has: "
            + (", ".join(sorted(exports)) or "nothing")
            + "."
        )
    return None


def _column(
    row: Mapping[str, Any],
    candidates: Sequence[str],
    what: str,
    *,
    required: bool = True,
) -> str | None:
    for name in candidates:
        if name in row:
            return name
    if required:
        raise ReferenceError(
            f"The export has no {what} column. It has: "
            + ", ".join(sorted(str(key) for key in row))
            + "."
        )
    return None


def _is_number(value: Any) -> bool:
    try:
        Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return False
    return True
