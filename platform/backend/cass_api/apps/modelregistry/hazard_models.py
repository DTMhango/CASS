"""Uploading a published PSHA model, and configuring a run of it on-platform.

Getting a national seismic model into CASS used to mean an operator with a
shell: unzip it somewhere, read the ``job.ini``, work out which of its settings
are wrong for a catastrophe model, edit them by hand, and run the engine. This
is that, as something a person can do from the product.

Three steps, and they are separate on purpose.

**Upload.** The archive goes into the artifact store whole and is read, not
interpreted: every file is listed and checksummed, the ``job.ini`` is parsed
into typed parameters, and the logic trees are counted so the realisation
number is known before anything runs. Nothing is changed.

**Configure.** An operator edits the parameters that are theirs to edit. What
they produce is a *spec* -- their choices laid over the publisher's, never
replacing the file -- which resolves to a complete configuration, validated,
with every problem listed while somebody is still looking at the screen. A
national calculation is hours; discovering there that the mode was wrong is the
expensive way to find out.

**Run.** The spec's files are assembled and handed to the engine, and what
comes back becomes a hazard set.

The reason this is worth building rather than documenting is the PuSGeN 2024
Indonesia model, which is what it was built against. Converting it needed ten
changes, two of which are invisible unless you know to look: the published
configuration writes ``-999`` for basin depths it did not measure, which an
event-based run either refuses outright or -- worse -- reads as a real depth,
inflating ground motion about a thousandfold while still looking like a column
of numbers. A person doing this by hand gets it wrong. The platform gets it
wrong once and then never again.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import pathlib
import re
import tempfile
import zipfile
from collections.abc import Mapping
from typing import Any

from django.db import transaction

from cass_converter import hazard_job, job_config, pilot_bins
from cass_converter.job_config import JobConfig, JobConfigError

from .assets import attach_hazard_model_file
from .models import (
    GEM_PERMISSION,
    INTERNAL_USE_LICENCE,
    AreaPerilGrid,
    HazardJobSpec,
    HazardModel,
    PublicationState,
)

#: Names a published package uses for its job configuration, in the order they
#: are looked for. GEM's mosaic packages ship ``job_clean.ini``; a plain
#: OpenQuake export ships ``job.ini``.
JOB_FILENAMES = ("job.ini", "job_clean.ini")

#: Files that make up a model, by extension. Anything else in the archive is
#: recorded in the manifest and not treated as an input.
MODEL_SUFFIXES = frozenset({".xml", ".csv", ".hdf5", ".ini", ".txt", ".md"})

#: The largest archive the platform will read in one request. A national source
#: model is tens of megabytes of NRML; beyond this it belongs in the artifact
#: store by another route rather than through a browser upload.
MAX_ARCHIVE_BYTES = 500 * 1024 * 1024

#: How deep a path inside the archive may go. Guards against an archive whose
#: entries escape the directory they are extracted into.
MAX_PATH_DEPTH = 8

#: How a publisher is recognised as GEM: the acronym as a word, which is how the
#: mosaic packages credit it -- "PuSGeN, with the GEM Foundation (2026 mosaic)".
GEM_PUBLISHER = re.compile(r"\bGEM\b")


class HazardModelError(Exception):
    """Raised when an uploaded package cannot be read or configured."""


def licence_basis(publisher: str) -> str:
    """The basis a hazard model is held under, read from who published it.

    A model GEM makes publicly available is held under GEM Foundation's written
    permission (ADR 15), and anything else under the installation's internal-use
    basis. It follows from the publisher rather than being asked of whoever
    uploads, because it is a fact about the model.
    """
    return GEM_PERMISSION if GEM_PUBLISHER.search(publisher or "") else INTERNAL_USE_LICENCE


@dataclasses.dataclass(frozen=True, slots=True)
class PackageFile:
    """One file inside an uploaded package."""

    path: str
    size_bytes: int
    checksum: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class Package:
    """An uploaded archive, read but not interpreted."""

    files: tuple[PackageFile, ...]
    contents: dict[str, bytes]
    job_path: str
    checksum: str
    size_bytes: int

    def read(self, path: str) -> bytes:
        try:
            return self.contents[path]
        except KeyError:
            raise HazardModelError(
                f"{path} is not in this package. It holds: "
                + ", ".join(sorted(self.contents)[:10])
                + "."
            ) from None


def _safe(name: str) -> bool:
    """Whether an archive entry may be extracted.

    An archive is somebody else's file. An entry naming an absolute path or
    climbing out with ``..`` would write wherever it liked, and a published
    model is exactly the kind of thing that arrives from outside.
    """
    if not name or name.endswith("/"):
        return False
    path = pathlib.PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or any(part == ".." for part in path.parts):
        return False
    return len(path.parts) <= MAX_PATH_DEPTH


def read_package(payload: bytes) -> Package:
    """Read an uploaded archive: every file, checksummed, plus its job file.

    Nested archives are unwrapped one level, because that is how the GEM mosaic
    packages ship -- a zip holding a ``job.zip`` beside a README and a licence.
    Requiring an operator to unwrap that by hand before uploading would be the
    platform asking them to do the one step it exists to do for them.
    """
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise HazardModelError(
            f"This archive is {len(payload) / 1e6:.0f} MB, above the "
            f"{MAX_ARCHIVE_BYTES / 1e6:.0f} MB an upload carries."
        )

    contents: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for name in archive.namelist():
                if not _safe(name):
                    continue
                data = archive.read(name)
                inner = pathlib.PurePosixPath(name)
                if inner.suffix == ".zip":
                    with zipfile.ZipFile(io.BytesIO(data)) as nested:
                        for entry in nested.namelist():
                            if _safe(entry):
                                contents[entry] = nested.read(entry)
                    continue
                # Drop the single wrapper directory a release usually adds, so
                # paths inside the package match what the job.ini references.
                relative = str(inner if len(inner.parts) == 1 else pathlib.PurePosixPath(*inner.parts[1:]))
                contents[relative] = data
    except zipfile.BadZipFile as exc:
        raise HazardModelError(f"This is not a readable zip archive: {exc}") from exc

    if not contents:
        raise HazardModelError("The archive holds no files this platform can read.")

    job_path = next((name for name in JOB_FILENAMES if name in contents), "")
    if not job_path:
        candidates = [name for name in contents if name.endswith(".ini")]
        if len(candidates) == 1:
            job_path = candidates[0]
    if not job_path:
        raise HazardModelError(
            "The archive holds no job configuration. A published OpenQuake model "
            "carries a job.ini (GEM's mosaic packages call it job_clean.ini) "
            "naming its logic trees and settings."
        )

    files = tuple(
        PackageFile(
            path=name,
            size_bytes=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
        )
        for name, data in sorted(contents.items())
    )
    return Package(
        files=files,
        contents=contents,
        job_path=job_path,
        checksum=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def inspect(package: Package) -> dict[str, Any]:
    """What the package contains and what running it would take.

    Read before anything is stored, so an operator who uploaded the wrong file
    finds out from a screen rather than from a registry record.
    """
    try:
        config = JobConfig.parse(
            package.read(package.job_path), source_name=package.job_path
        )
    except JobConfigError as exc:
        raise HazardModelError(f"{package.job_path} could not be read: {exc}") from exc

    trees = {}
    source_tree = config.get("source_model_logic_tree_file")
    gsim_tree = config.get("gsim_logic_tree_file")
    if source_tree and gsim_tree:
        try:
            trees = job_config.logic_tree_summary(
                package.read(source_tree).decode("utf-8", "replace"),
                package.read(gsim_tree).decode("utf-8", "replace"),
            )
        except HazardModelError:
            # A tree the configuration names but the archive does not hold is
            # reported as a problem rather than raised: the operator needs to
            # see the rest of the package to understand what went wrong.
            trees = {
                "estimated_realizations": 0,
                "note": (
                    f"The configuration names {source_tree} and {gsim_tree}, and "
                    "the archive does not hold both. The package is incomplete."
                ),
            }

    return {
        "job_path": package.job_path,
        "files": [item.as_dict() for item in package.files],
        "file_count": len(package.files),
        "archive_checksum": package.checksum,
        "archive_bytes": package.size_bytes,
        "configuration": job_config.describe(
            config, required_measures=pilot_bins.PILOT_IMTS
        ),
        "logic_trees": trees,
        "site_models": [
            item.path
            for item in package.files
            if item.path.endswith(".csv") and "site" not in item.path.lower()
        ][:5],
    }


@transaction.atomic
def register_model(
    payload: bytes,
    *,
    country_code: str,
    version: str,
    label: str,
    source_organisation: str = "",
    publication_reference: str = "",
    licence: str = "",
    licence_cleared: bool = True,
    licence_note: str = "",
    actor=None,
) -> HazardModel:
    """Store an uploaded package as a registered, configurable hazard model.

    Idempotent by version: re-uploading replaces the stored files and leaves the
    record in place, so the same version never means two different models.
    """
    package = read_package(payload)
    summary = inspect(package)
    configuration = summary["configuration"]



    model, _ = HazardModel.objects.update_or_create(
        country_code=country_code.upper(),
        version=version,
        defaults={
            "label": label,
            "source_organisation": source_organisation,
            "publication_reference": publication_reference,
            "licence": licence,
            "licence_cleared": licence_cleared,
            "licence_note": licence_note or licence_basis(source_organisation),
            "archive_checksum": package.checksum,
            "archive_bytes": package.size_bytes,
            "file_manifest": [item.as_dict() for item in package.files],
            "job_configuration": configuration,
            "published_calculation_mode": configuration["calculation_mode"],
            "intensity_measures": configuration["intensity_measures"],
            "tectonic_regions": summary["logic_trees"].get("tectonic_regions", []),
            "estimated_realizations": summary["logic_trees"].get(
                "estimated_realizations", 0
            ),
            "logic_tree_summary": summary["logic_trees"],
            "publication_state": PublicationState.DRAFT,
            "updated_by": actor,
        },
    )

    for item in package.files:
        attach_hazard_model_file(
            model, item.path, package.read(item.path), actor=actor
        )
    return model


# -- configuring a run ------------------------------------------------------------------

#: Parameters an operator may set through the editor. Deliberately the science
#: and discretisation ones: the model's own logic trees are shown and not
#: offered, because editing one does not configure this model, it makes a
#: different one that nobody published.
EDITABLE = tuple(
    item.name
    for item in job_config.PARAMETERS
    if item.editability
    in (job_config.Editability.SCIENCE, job_config.Editability.DISCRETISATION)
)

#: Defaults a CASS run starts from, over the publisher's configuration. Each is
#: the value the conversion needs rather than a preference, except the event set
#: count, which is a genuine trade of runtime against how well the tail is
#: sampled.
CASS_DEFAULTS: dict[str, Any] = {
    "investigation_time": 50.0,
    # Twenty paths of ten event sets each: ten thousand simulated years, drawn
    # across twenty views of the logic tree (ADR 18) -- and ten sets per path
    # rather than one because a return-period loss read from a catalogue is an
    # order statistic. From a thousand years the 1-in-1,000-year loss is the
    # single worst year; from ten thousand it is the tenth-worst (ADR 21).
    # Measured on this platform over the 962 Jakarta-Bandung cells: 121 seconds
    # and 176 MB for a thousand years, 501 seconds and 1.78 GB for ten thousand.
    "ses_per_logic_tree_path": 10,
    "number_of_logic_tree_samples": 20,
}

#: Above this a configuration warns that its hazard would store a great deal.
#: A warning rather than a refusal: whether the disk is there is the
#: installation's to know, but nobody should find out after the calculation.
STORAGE_WARNING_MB = 100_000


def published_configuration(model: HazardModel) -> JobConfig:
    """The model's own configuration, rebuilt from what was stored."""
    settings = model.job_configuration.get("settings") or []
    text = ""
    section = None
    for item in settings:
        if item["section"] != section:
            section = item["section"]
            text += f"[{section}]\n"
        text += f"{item['name']} = {item['value']}\n"
    if not text:
        raise HazardModelError(
            f"{model} has no stored configuration, so a run cannot be built from it."
        )
    return JobConfig.parse(text, source_name=model.job_configuration.get("source_name", ""))


#: The four bounds a run region is given by, in decimal degrees.
REGION_KEYS = ("min_latitude", "max_latitude", "min_longitude", "max_longitude")


def normalise_region(region: Mapping[str, Any] | None) -> dict[str, float] | None:
    """A run region as four bounds, or ``None`` where the whole grid is meant.

    A region is not an engine parameter and not the model's science. It is how
    much of a national grid one calculation computes, and it exists because a
    national model over every onshore cell is a calculation of many hours: a
    modeller testing a configuration, or a portfolio concentrated in two
    cities, should not have to pay for Papua to find out.
    """
    if not region:
        return None
    try:
        bounds = {key: float(region[key]) for key in REGION_KEYS}
    except (KeyError, TypeError, ValueError):
        raise HazardModelError(
            "A region needs all four bounds -- minimum and maximum latitude and "
            "longitude, in decimal degrees."
        ) from None
    if not -90 <= bounds["min_latitude"] < bounds["max_latitude"] <= 90:
        raise HazardModelError(
            "The region's latitudes are not a range: the minimum must be below "
            "the maximum, and both within -90 to 90."
        )
    if not -180 <= bounds["min_longitude"] < bounds["max_longitude"] <= 180:
        raise HazardModelError(
            "The region's longitudes are not a range: the minimum must be below "
            "the maximum, and both within -180 to 180."
        )
    return bounds


def cells_in_region(cells: Any, region: Mapping[str, float] | None) -> tuple[Any, ...]:
    """The cells whose centre lies in the region, or every cell without one.

    By centre, because the centre is where the calculation point goes: a cell
    straddling the boundary is in the run exactly when its ground motion would
    be computed inside it.
    """
    if region is None:
        return tuple(cells)
    chosen = []
    for cell in cells:
        latitude = float(cell.min_latitude + cell.max_latitude) / 2
        longitude = float(cell.min_longitude + cell.max_longitude) / 2
        if (
            region["min_latitude"] <= latitude <= region["max_latitude"]
            and region["min_longitude"] <= longitude <= region["max_longitude"]
        ):
            chosen.append(cell)
    return tuple(chosen)


def published_site_points(model: HazardModel) -> tuple[Any, ...] | None:
    """The site conditions a published model measured, where it ships them.

    National packages carry a site model beside the sources -- PuSGeN 2024's is
    ``iid-10km.csv``, eleven thousand points with measured Vs30 -- and the
    converted run has to use it. Without it the job names a site model that
    carries coordinates and nothing else, and every ground-motion model refuses
    to start for want of a Vs30 it was never given.
    """
    from apps.modelregistry.assets import hazard_model_file

    published = published_configuration(model)
    for key in ("site_model_file", "sites_csv"):
        name = str(published.get(key) or "").strip().strip(QUOTES)
        if not name or "," in name or "{" in name:
            continue
        payload = hazard_model_file(model, name)
        if payload is None:
            continue
        header = payload.split(NEWLINE, 1)[0].decode("utf-8-sig").lower()
        if "vs30" not in header:
            continue
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / pathlib.PurePosixPath(name).name
            path.write_bytes(payload)
            return hazard_job.read_site_model(path)
    return None


#: Quote characters a job configuration may wrap a file name in.
QUOTES = "'" + '"'

#: The line separator of a CSV header, as bytes.
NEWLINE = bytes([10])


def _build(
    model: HazardModel,
    grid: AreaPerilGrid,
    *,
    overrides: dict[str, Any] | None,
    cells: Any,
    region: Mapping[str, Any] | None,
    site_points: Any,
) -> tuple[dict[str, Any], Any, Any]:
    """The resolved configuration, and the job and site join behind it."""
    chosen = dict(CASS_DEFAULTS)
    unknown = sorted(set(overrides or {}) - set(EDITABLE))
    if unknown:
        raise HazardModelError(
            f"{', '.join(unknown)} cannot be set here. The model's own logic "
            "trees and source files are shown rather than offered: changing one "
            "does not configure this model, it makes a different one."
        )
    chosen.update(overrides or {})
    bounds = normalise_region(region)

    published = published_configuration(model)

    site_report: dict[str, Any] = {}
    coverage: dict[str, Any] = {}
    job = None
    join = None
    extra_problems: list[job_config.Problem] = []
    if cells is not None:
        selected = cells_in_region(cells, bounds)
        job = hazard_job.HazardJob(
            country_code=model.country_code,
            label=f"{model.label} on {grid.reference}",
            sites=hazard_job.sites_from_cells(selected),
            imts=pilot_bins.PILOT_IMTS,
            source_model_logic_tree=published.get("source_model_logic_tree_file") or "",
            gsim_logic_tree=published.get("gsim_logic_tree_file") or "",
            investigation_time=float(chosen["investigation_time"]),
            ses_per_logic_tree_path=int(chosen["ses_per_logic_tree_path"]),
        )
        # Against the whole grid rather than the region, so the report says
        # plainly how much of the country this run leaves out.
        coverage = {**hazard_job.coverage(job, tuple(cells)), "region": bounds}
        if not job.sites:
            extra_problems.append(
                job_config.Problem(
                    "region",
                    "error",
                    "The region holds no onshore cell of this grid, so the "
                    "calculation would have no sites at all.",
                )
            )
        elif site_points is not None:
            reference = published.get("reference_vs30_value") or "760"
            join = hazard_job.join_site_model(
                job,
                site_points,
                fallback={
                    "vs30": reference,
                    "vs30measured": "0",
                    "region": "0",
                    "backarc": "0",
                },
            )
            site_report = join.as_dict()

    conversion = job_config.to_event_based(
        published,
        measures=pilot_bins.PILOT_IMTS,
        investigation_time=float(chosen["investigation_time"]),
        ses_per_logic_tree_path=int(chosen["ses_per_logic_tree_path"]),
        logic_tree_samples=int(chosen["number_of_logic_tree_samples"]),
        minimum_intensity=float(pilot_bins.INTENSITY_RANGE["PGA"][0]),
        # A site model where the published one could be joined, so every cell
        # carries its own Vs30. Otherwise a plain sites file, and the model's
        # reference conditions apply everywhere -- which the site report says.
        # With no cells at all (an editor call before a grid is chosen) the
        # site model is still named, because that is what a real run will use.
        site_model_file="sites.csv" if (join is not None or cells is None) else None,
        sites_file="sites.csv",
    )

    resolved = conversion.config
    for name, value in (overrides or {}).items():
        if name in (
            "investigation_time",
            "ses_per_logic_tree_path",
            "number_of_logic_tree_samples",
        ):
            continue
        resolved = resolved.set(name, value)

    problems = job_config.validate(resolved, required_measures=pilot_bins.PILOT_IMTS)
    problems.extend(extra_problems)
    sampled = int(chosen["number_of_logic_tree_samples"])
    if model.estimated_realizations > 1 and sampled == 1:
        problems.append(
            job_config.Problem(
                "number_of_logic_tree_samples",
                "warning",
                f"This model's logic tree enumerates to about "
                f"{model.estimated_realizations} realisations and the run samples "
                "one of them. The result is one view of the hazard rather than "
                "the model's weighted mean, and measured against that mean a "
                "single path sat between 0.84 and 1.24 of it depending on which "
                "path was drawn. Sampling several costs the same to run "
                "(ADR 18).",
            )
        )

    catalogue = catalogue_summary(
        resolved.effective_time, coverage.get("cells_computed") if coverage else None
    )
    stored = (catalogue or {}).get("storage") or {}
    if stored.get("hazard_set_mb", 0) > STORAGE_WARNING_MB:
        problems.append(
            job_config.Problem(
                "ses_per_logic_tree_path",
                "warning",
                f"This run's hazard could store up to about "
                f"{stored['hazard_set_mb'] / 1000:,.0f} GB -- "
                f"{catalogue['simulated_years']:,.0f} simulated years over "
                f"{coverage.get('cells_computed', 0):,} cells -- plus a model package "
                "of up to "
                f"{stored['package_mb'] / 1000:,.0f} GB once built. Storage grows in "
                "step with the simulated years and the cells, so fewer event sets or "
                "a smaller region reduce it. The figure is a ceiling measured on the "
                "strongest shaking CASS has computed.",
            )
        )

    outcome = {
        "overrides": chosen,
        "region": bounds,
        "configuration": job_config.describe(
            resolved, required_measures=pilot_bins.PILOT_IMTS
        ),
        "conversion": conversion.as_dict(),
        "coverage": coverage,
        "site_join": site_report,
        "problems": [item.as_dict() for item in problems],
        "runnable": not any(item.severity == "error" for item in problems),
        "job_checksum": (
            hazard_job.job_checksum(job) if job is not None else ""
        ),
        "rendered": resolved.render().decode("utf-8"),
        "catalogue": catalogue,
    }
    return outcome, job, join


#: Return periods a catalogue's tail is described at, in years.
TAIL_RETURN_PERIODS = (100, 250, 500, 1000)


def catalogue_summary(simulated_years: float | None, sites: int | None) -> dict[str, Any] | None:
    """How long a run's catalogue is, what its tail rests on, and what it will store.

    A return-period loss read from a simulated catalogue is an order statistic:
    the 1-in-1,000-year loss from 10,000 simulated years is the tenth-worst year,
    and from 1,000 years it is the single worst. So the number of simulated
    years beyond each return period is the plainest measure of how much a tail
    figure rests on, and it is shown before anything is run rather than
    discovered in the results.
    """
    from . import grid_build  # noqa: PLC0415

    if not simulated_years:
        return None
    thousands = simulated_years / 1000
    summary: dict[str, Any] = {
        "simulated_years": simulated_years,
        "tail": [
            {"return_period": period, "years_beyond": simulated_years / period}
            for period in TAIL_RETURN_PERIODS
        ],
    }
    if sites:
        summary["storage"] = {
            "hazard_set_mb": round(
                sites
                * (grid_build.DATASTORE_KB_PER_CELL + grid_build.FOOTPRINT_KB_PER_CELL)
                * thousands
                / 1000
            ),
            "package_mb": round(sites * grid_build.PACKAGE_KB_PER_CELL * thousands / 1000),
            "basis": (
                "Measured on the Jakarta-Bandung calculation, the strongest shaking CASS "
                "has computed, so a ceiling: quieter cells store less."
            ),
        }
    return summary


def resolve(
    model: HazardModel,
    grid: AreaPerilGrid,
    *,
    overrides: dict[str, Any] | None = None,
    cells: Any = None,
    region: Mapping[str, Any] | None = None,
    site_points: Any = None,
) -> dict[str, Any]:
    """Work out what a configured run would actually do, without running it.

    Returns the resolved configuration, the conversion's list of changes, how
    much of the grid the run covers, the site join and every problem found.
    Nothing is stored and nothing executes: this is what the editor renders
    after each edit.
    """
    outcome, _, _ = _build(
        model,
        grid,
        overrides=overrides,
        cells=cells,
        region=region,
        site_points=site_points,
    )
    return outcome


@transaction.atomic
def save_spec(
    model: HazardModel,
    grid: AreaPerilGrid,
    *,
    name: str,
    overrides: dict[str, Any] | None = None,
    cells: Any = None,
    region: Mapping[str, Any] | None = None,
    site_points: Any = None,
    actor=None,
) -> HazardJobSpec:
    """Store one configured run of a model."""
    if grid.country_code.upper() != model.country_code.upper():
        raise HazardModelError(
            f"The grid is for {grid.country_code} and the model for "
            f"{model.country_code}. A run pairing them would compute one "
            "country's hazard at another's cells."
        )
    outcome = resolve(
        model,
        grid,
        overrides=overrides,
        cells=cells,
        region=region,
        site_points=site_points,
    )
    return HazardJobSpec.objects.create(
        model=model,
        grid=grid,
        name=name,
        overrides=outcome["overrides"],
        region=outcome["region"] or {},
        resolved_configuration=outcome["configuration"],
        conversion_report=outcome["conversion"],
        site_join_report={**outcome["site_join"], "coverage": outcome["coverage"]},
        problems=outcome["problems"],
        is_runnable=outcome["runnable"],
        job_checksum=outcome["job_checksum"],
        created_by=actor,
        updated_by=actor,
    )


def job_files(spec: HazardJobSpec) -> dict[str, Any]:
    """Everything OpenQuake needs to run one saved configuration.

    The model's own files go up as they were published, under the paths its
    logic trees reference. Beside them go two files CASS generates: the
    resolved ``job.ini``, which is the configuration the editor showed and
    promised would run, and ``sites.csv`` -- the grid cells in the saved region,
    each carrying the published site conditions nearest it.

    The checksum is recomputed and compared rather than trusted. A grid can be
    republished between a configuration being saved and being run, and a run
    whose job checksum is not the one anybody reviewed is a run nobody can
    reproduce -- so the difference is reported rather than quietly submitted.
    """
    from apps.modelregistry.assets import hazard_model_files, load_grid

    grid = load_grid(spec.grid)
    outcome, job, join = _build(
        spec.model,
        spec.grid,
        overrides=spec.overrides,
        cells=grid.cells,
        region=spec.region or None,
        site_points=published_site_points(spec.model),
    )

    files: dict[str, bytes] = dict(hazard_model_files(spec.model))
    files["job.ini"] = outcome["rendered"].encode("utf-8")
    files["sites.csv"] = (
        hazard_job.site_model_csv(join) if join is not None else hazard_job.sites_csv(job)
    )

    recomputed = outcome["job_checksum"]
    return {
        "files": files,
        "job_checksum": recomputed,
        "saved_checksum": spec.job_checksum,
        "checksum_matches": (not spec.job_checksum) or recomputed == spec.job_checksum,
        "problems": outcome["problems"],
        "site_join": outcome["site_join"],
        "coverage": outcome["coverage"],
    }


def catalogue() -> list[dict[str, Any]]:
    """Every parameter the editor can offer, with what changing it costs."""
    return [
        item
        for item in job_config.parameter_catalogue()
        if item["name"] in EDITABLE or item["editability"] == "model"
    ]
