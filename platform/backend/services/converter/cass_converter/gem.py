"""Reading the GEM global vulnerability model, exactly as published.

GEM distributes each country's vulnerability as NRML 0.5 XML: one file per loss
category, one ``vulnerabilityFunction`` per taxonomy, each carrying the
intensity levels it is defined at, the mean loss ratio at each, and the
coefficient of variation of that loss ratio.

This module reads them and does nothing else. No taxonomy is mapped, no
distribution is discretised, no function is chosen. That separation is the
point: what GEM published is a fact about a file with a checksum, and
everything CASS does afterwards is an assumption about how it applies to a
Klapton Re risk. Mixing the two in one module would make the boundary between
them a matter of reading the code carefully.

Three things the reader refuses rather than accommodates.

**A distribution family it cannot discretise.** Every pilot-country function is
``dist="BT"``. A future release using a lognormal would need a different
discretisation, and quietly treating it as beta would produce plausible,
wrong tails.

**Arrays of unequal length.** The intensity levels, means and CoVs are three
parallel lists, and NRML does not enforce that they match. A short list would
otherwise silently truncate the function's upper range -- the part where the
losses are.

**A repeated taxonomy.** Two functions for one taxonomy would be resolved by
file order, so the answer would depend on how the XML happened to be written.

The checksum is computed over the bytes read, not looked up. A model release
records it, so the assertion "this build used the published Indonesia
structural functions" is checkable against the GEM manifest rather than
believed.
"""

from __future__ import annotations

import csv
import dataclasses
import enum
import hashlib
import pathlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, BinaryIO

from . import iso3166

#: Bumped when the reader's interpretation of the file changes.
READER_VERSION = "1.0.0"

NRML_NAMESPACE = "http://openquake.org/xmlns/nrml/0.5"
_NS = {"n": NRML_NAMESPACE}

#: The only distribution this reader will accept, because it is the only one
#: the discretiser knows how to integrate. Named rather than assumed.
SUPPORTED_DISTRIBUTION = "BT"

#: The fewest segments a GEM taxonomy string has: material, lateral system,
#: design, height and the occupancy class. More is normal -- GEM states further
#: attributes between the height and the occupancy for a good part of the world
#: -- so this is a minimum rather than a count, and the occupancy is read as the
#: last segment rather than the fifth.
TAXONOMY_SEGMENTS = 5


class GemError(Exception):
    """Raised when a GEM file is not the thing it claims to be."""


class LossCategory(enum.StrEnum):
    """The loss categories GEM publishes, named as its files name them."""

    STRUCTURAL = "structural"
    NONSTRUCTURAL = "nonstructural"
    CONTENTS = "contents"
    FATALITIES = "fatalities"

    @property
    def declared(self) -> str:
        """The spelling GEM writes in the file's ``lossCategory`` attribute.

        It is not always the filename. ``vulnerability_fatalities.xml`` declares
        ``occupants`` in both pilot countries -- the file is named for what it
        predicts and the attribute for what it counts. Both spellings are
        accepted, and the mismatch is recorded here rather than smoothed over,
        because a future release that renamed one of them should reach the
        reconciliation check below rather than pass silently.
        """
        return DECLARED_CATEGORIES.get(self, str(self))

    @property
    def is_monetary(self) -> bool:
        """Whether this category is a loss ratio against a value.

        Fatalities is not. It shares the file format and the taxonomy, and it
        is a ratio of occupants rather than of money, so it must never reach a
        vulnerability table that Oasis will multiply by a TIV.
        """
        return self is not LossCategory.FATALITIES


#: Where GEM's ``lossCategory`` attribute differs from its filename. Only one
#: does, and it does so in every published country file.
DECLARED_CATEGORIES: dict[LossCategory, str] = {LossCategory.FATALITIES: "occupants"}

#: The reverse, for reading a file that states its category.
CATEGORY_BY_DECLARED: dict[str, LossCategory] = {
    **{str(item): item for item in LossCategory},
    **{value: key for key, value in DECLARED_CATEGORIES.items()},
}


class OccupancyClass(enum.StrEnum):
    """The occupancy classes the GEM taxonomy's final segment uses."""

    COMMERCIAL = "COM"
    RESIDENTIAL = "RES"
    INDUSTRIAL = "IND"


@dataclasses.dataclass(frozen=True, slots=True)
class Taxonomy:
    """A GEM taxonomy string, parsed far enough to route on.

    Only the parts CASS needs to decide anything are pulled out. The whole
    string is kept, because it is the identifier GEM published and the one a
    reviewer will search for.
    """

    text: str
    material: str
    lateral_system: str
    design: str
    height: str
    occupancy: str
    #: Attributes GEM states between the height and the occupancy, where it
    #: states any: a wooden roof (``RWO``) on an adobe house, a soft storey
    #: (``IRI(SOS)``) in a concrete frame. CASS routes on none of them -- no OED
    #: field says whether a building has a soft storey -- so they are carried
    #: whole, and the taxonomies that hold them stay distinct candidates in a
    #: mixture rather than being merged with the ones that do not.
    attributes: tuple[str, ...] = ()

    @property
    def storeys(self) -> int | None:
        """Storeys, where the height segment states a single number.

        The vulnerability model writes ``H:7``. The exposure summaries write
        bands like ``H:6-12``, which is why this can be absent: a band is not a
        storey count and guessing a midpoint would invent a building.
        """
        if not self.height.startswith("H:"):
            return None
        value = self.height[2:]
        return int(value) if value.isdigit() else None

    @property
    def occupancy_class(self) -> OccupancyClass | None:
        try:
            return OccupancyClass(self.occupancy)
        except ValueError:
            return None

    def __str__(self) -> str:
        return self.text


def parse_taxonomy(text: str) -> Taxonomy:
    """Split a GEM taxonomy string into the parts CASS routes on.

    The first four segments and the last are fixed -- material, lateral system,
    design, height, and the occupancy class at the end. Between them GEM may
    state further attributes, and in v2026.0.0 it does for 75 countries: a
    wooden roof, a soft storey. Reading the occupancy positionally rather than
    as the last segment is what made those countries unreadable, and the failure
    was not visible as a bad taxonomy -- it read the roof as the occupancy,
    which matches no occupancy class, so every one of their risks reached no
    function at all.
    """
    segments = text.split("/")
    if len(segments) < TAXONOMY_SEGMENTS:
        raise GemError(
            f"{text!r} is not a GEM taxonomy string: expected at least "
            f"{TAXONOMY_SEGMENTS} segments separated by '/', found {len(segments)}."
            + (
                " It is a HAZUS class -- model building type, code level and "
                "occupancy -- which is the alphabet GEM publishes the United "
                "States, Canada and their territories in. CASS reads the GEM "
                "building taxonomy, and mapping an OED schedule onto HAZUS "
                "classes is a second set of assumptions nobody has written."
                if len(segments) == 3
                else ""
            )
        )
    material, lateral, design, height, *rest = segments
    return Taxonomy(
        text=text,
        material=material,
        lateral_system=lateral,
        design=design,
        height=height,
        occupancy=rest[-1],
        attributes=tuple(rest[:-1]),
    )


@dataclasses.dataclass(frozen=True, slots=True)
class VulnerabilityFunction:
    """One GEM function: a loss-ratio distribution against intensity."""

    taxonomy: Taxonomy
    loss_category: LossCategory
    imt: str
    #: Intensity levels, strictly increasing, in the IMT's own units.
    intensities: tuple[float, ...]
    mean_loss_ratios: tuple[float, ...]
    coefficients_of_variation: tuple[float, ...]
    distribution: str = SUPPORTED_DISTRIBUTION

    def __len__(self) -> int:
        return len(self.intensities)

    def points(self) -> Iterator[tuple[float, float, float]]:
        """Each intensity with its mean loss ratio and CoV."""
        return zip(
            self.intensities,
            self.mean_loss_ratios,
            self.coefficients_of_variation,
            strict=True,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "taxonomy": self.taxonomy.text,
            "loss_category": str(self.loss_category),
            "imt": self.imt,
            "distribution": self.distribution,
            "intensity_count": len(self.intensities),
            "intensity_range": [self.intensities[0], self.intensities[-1]],
            "max_mean_loss_ratio": max(self.mean_loss_ratios),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class VulnerabilityModel:
    """One published file: every function of one loss category for one country."""

    country_code: str
    loss_category: LossCategory
    functions: tuple[VulnerabilityFunction, ...]
    source_name: str
    checksum: str
    model_id: str = ""
    description: str = ""
    reader_version: str = READER_VERSION

    def __len__(self) -> int:
        return len(self.functions)

    def __iter__(self) -> Iterator[VulnerabilityFunction]:
        return iter(self.functions)

    @property
    def by_taxonomy(self) -> Mapping[str, VulnerabilityFunction]:
        return {item.taxonomy.text: item for item in self.functions}

    @property
    def intensity_measures(self) -> tuple[str, ...]:
        """Every IMT this file's functions demand, in sorted order.

        Usually more than one. That is the multi-IMT problem stated in data:
        the Indonesia structural file alone spans PGA, SA(0.3), SA(0.6) and
        SA(1.0), because GEM picks the period each structure responds at.
        """
        return tuple(sorted({item.imt for item in self.functions}))

    def taxonomies(self, occupancy: OccupancyClass | None = None) -> tuple[Taxonomy, ...]:
        """The taxonomies covered, optionally narrowed to one occupancy class."""
        return tuple(
            item.taxonomy
            for item in self.functions
            if occupancy is None or item.taxonomy.occupancy_class is occupancy
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "country_code": self.country_code,
            "loss_category": str(self.loss_category),
            "source_name": self.source_name,
            "checksum": self.checksum,
            "model_id": self.model_id,
            "description": self.description,
            "reader_version": self.reader_version,
            "function_count": len(self.functions),
            "intensity_measures": list(self.intensity_measures),
            "taxonomy_count": len({item.taxonomy.text for item in self.functions}),
        }


def read_model(
    source: BinaryIO | str | pathlib.Path,
    *,
    country_code: str,
    loss_category: LossCategory | str | None = None,
) -> VulnerabilityModel:
    """Read one GEM vulnerability XML file."""
    payload, name = _read_bytes(source)
    checksum = hashlib.sha256(payload).hexdigest()

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise GemError(f"{name} is not well-formed XML: {exc}") from exc

    if root.tag != f"{{{NRML_NAMESPACE}}}nrml":
        raise GemError(
            f"{name} is not an NRML 0.5 document. Its root element is {root.tag!r}, "
            f"and this reader expects the GEM vulnerability namespace "
            f"{NRML_NAMESPACE!r}."
        )

    models = root.findall("n:vulnerabilityModel", _NS)
    if len(models) != 1:
        raise GemError(
            f"{name} carries {len(models)} vulnerability models. This reader "
            "expects exactly one, as every published GEM country file has."
        )
    model = models[0]

    stated = (model.get("lossCategory") or "").strip()
    category = _resolve_category(loss_category, stated, name)

    functions = tuple(
        _read_function(element, category, name)
        for element in model.findall("n:vulnerabilityFunction", _NS)
    )
    if not functions:
        raise GemError(f"{name} carries no vulnerability functions.")

    seen: dict[str, int] = {}
    for item in functions:
        seen[item.taxonomy.text] = seen.get(item.taxonomy.text, 0) + 1
    repeated = sorted(key for key, count in seen.items() if count > 1)
    if repeated:
        raise GemError(
            f"{name} defines more than one function for: {', '.join(repeated)}. "
            "Which one applied would depend on the order the file was written in."
        )

    return VulnerabilityModel(
        country_code=country_code.upper(),
        loss_category=category,
        functions=functions,
        source_name=name,
        checksum=checksum,
        model_id=(model.get("id") or "").strip(),
        description=(model.findtext("n:description", "", _NS) or "").strip(),
    )


def read_country(
    root: str | pathlib.Path,
    *,
    country_code: str,
    categories: Sequence[LossCategory] | None = None,
) -> dict[LossCategory, VulnerabilityModel]:
    """Read every loss category for one country from a directory of GEM files.

    The directory is the country folder of the published repository, holding
    ``vulnerability_structural.xml`` and its siblings.
    """
    directory = pathlib.Path(root)
    if not directory.is_dir():
        raise GemError(f"{directory} is not a directory of GEM vulnerability files.")

    wanted = tuple(categories) if categories is not None else tuple(LossCategory)
    models: dict[LossCategory, VulnerabilityModel] = {}
    missing: list[str] = []
    for category in wanted:
        path = directory / f"vulnerability_{category}.xml"
        if not path.is_file():
            missing.append(path.name)
            continue
        models[category] = read_model(
            path, country_code=country_code, loss_category=category
        )

    if missing:
        raise GemError(
            f"{directory} is missing {', '.join(missing)}. A partial country read "
            "would produce a model covering some coverage types and not others, "
            "and the gap would only appear as unexplained zero loss."
        )
    return models


#: Countries the two GEM repositories name differently. The vulnerability model
#: keeps the older English name and the exposure model has moved to the
#: country's own -- and neither is derivable from the other, so the pairs are
#: stated. Only these two differ in v2026.0.0, and
#: ``test_every_country_in_the_release_pairs_with_its_exposure`` fails if a
#: later release adds a third rather than leaving that country unbuildable.
EXPOSURE_FOLDERS: Mapping[str, str] = {
    "Cape_Verde": "Cabo_Verde",
    "Turkey": "Turkiye",
}


def exposure_folder(root: str | pathlib.Path, *, region: str, country: str) -> str:
    """The exposure repository's folder for a vulnerability repository's country."""
    base = pathlib.Path(root) / "global_exposure_model" / region
    if (base / country).is_dir():
        return country
    named = EXPOSURE_FOLDERS.get(country, "")
    return named if named and (base / named).is_dir() else country


def stock_summary_path(
    root: str | pathlib.Path, *, region: str, country: str
) -> pathlib.Path:
    """Where GEM publishes a country's building stock by taxonomy."""
    return (
        pathlib.Path(root)
        / "global_exposure_model"
        / region
        / exposure_folder(root, region=region, country=country)
        / "summaries"
        / "Exposure_Summary_Taxonomy.csv"
    )


def national_summary_path(
    root: str | pathlib.Path, *, region: str, country: str
) -> pathlib.Path:
    """Where GEM publishes a country's totals by occupancy, for checking against."""
    return stock_summary_path(root, region=region, country=country).with_name(
        "Exposure_Summary_Adm0.csv"
    )


@dataclasses.dataclass(frozen=True, slots=True)
class CountryIdentity:
    """Which country a folder in the release is, by code."""

    #: ISO 3166-1 alpha-3, as GEM writes it. Its mapping file is keyed by this.
    iso3: str
    #: ISO 3166-1 alpha-2, which the rest of CASS is keyed by.
    country_code: str


def country_identity(
    root: str | pathlib.Path, *, region: str, country: str
) -> CountryIdentity:
    """The codes of the country a release folder holds, as GEM states them.

    Read from ``ID_0`` in the stock summary the build weights the country's
    functions with, so the codes and the stock cannot describe two different
    countries. Only the first row is read here; the build reads every row and
    refuses a summary that changes country part-way.
    """
    path = stock_summary_path(root, region=region, country=country)
    place = f"{country.replace('_', ' ')} ({region.replace('_', ' ')})"
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            first = next(csv.DictReader(handle), None)
    except OSError:
        raise GemError(
            f"GEM's exposure model holds no stock summary for {place}, so which country "
            "it is, and what its buildings are, could not be read."
        ) from None

    iso3 = str((first or {}).get("ID_0") or "").strip().upper()
    if not iso3:
        raise GemError(
            f"The stock summary for {place} states no ID_0, so which country it "
            "describes could not be read."
        )
    code = iso3166.alpha_2(iso3)
    if not code:
        raise GemError(
            f"GEM names {place} {iso3!r}, which is not an ISO 3166-1 alpha-3 code, so "
            "there is no alpha-2 code to key its vulnerability set by."
        )
    return CountryIdentity(iso3=iso3, country_code=code)


def check_taxonomy_alphabet(
    root: str | pathlib.Path, *, region: str, country: str
) -> None:
    """Refuse a country whose functions are not named in the GEM taxonomy.

    GEM publishes the United States, Canada and their territories in HAZUS
    classes instead. They are read here, from the first function of the country's
    structural file, so a country CASS cannot map is said to be unbuildable while
    somebody is choosing one -- rather than part way through a build, as an
    unreadable taxonomy string.

    The reason is a sentence rather than the parser's full account of it,
    because this one is read in a list of two hundred countries. A build states
    the whole thing.
    """
    path = (
        pathlib.Path(root)
        / "global_vulnerability_model"
        / region
        / country
        / f"vulnerability_{LossCategory.STRUCTURAL}.xml"
    )
    try:
        head = path.read_bytes()[:8192].decode("utf-8", errors="replace")
    except OSError as exc:
        raise GemError(f"{path.name} could not be read for {country}: {exc}") from None
    found = re.search(r'<vulnerabilityFunction\s+id="([^"]+)"', head)
    if not found:
        return
    identifier = found.group(1)
    try:
        parse_taxonomy(identifier)
    except GemError:
        raise GemError(
            f"GEM publishes this country's functions as HAZUS classes ({identifier}), "
            "not in its own building taxonomy, and CASS cannot map an OED schedule "
            "onto those."
            if len(identifier.split("/")) == 3
            else f"CASS cannot read this country's functions: {identifier!r} is not a "
            "GEM taxonomy string."
        ) from None


def catalogue(
    root: str | pathlib.Path, *, identify: bool = False
) -> tuple[dict[str, Any], ...]:
    """Every country a GEM release publishes vulnerability functions for.

    Read from the release rather than from a table inside CASS. Which countries
    GEM covers is a property of the release somebody downloaded, and a list
    compiled into the platform would be wrong the first time GEM published
    another -- which is exactly when somebody would be looking for it.

    ``identify`` adds each country's codes, read from its stock summary. It
    costs a file per country, so a caller that only counts countries leaves it
    off. A country whose codes cannot be read is still listed, with the reason
    in ``problem``: GEM publishes its functions, and saying why it cannot be
    built is more use than leaving it out.
    """
    base = pathlib.Path(root) / "global_vulnerability_model"
    if not base.is_dir():
        raise GemError(
            f"{base} is not a directory. It should be the global_vulnerability_model "
            "of a GEM release."
        )

    found: list[dict[str, Any]] = []
    for region in sorted(item for item in base.iterdir() if item.is_dir()):
        for country in sorted(item for item in region.iterdir() if item.is_dir()):
            categories = [
                str(category)
                for category in LossCategory
                if (country / f"vulnerability_{category}.xml").is_file()
            ]
            if not categories:
                continue
            entry: dict[str, Any] = {
                "region": region.name,
                "country": country.name,
                "loss_categories": categories,
            }
            if identify:
                identity: CountryIdentity | None = None
                problem = ""
                try:
                    identity = country_identity(
                        root, region=region.name, country=country.name
                    )
                    # Read after the codes, and reported beside them: a country
                    # CASS knows the name of and cannot read the functions of is
                    # a different thing from one it cannot identify at all.
                    check_taxonomy_alphabet(
                        root, region=region.name, country=country.name
                    )
                except GemError as exc:
                    problem = str(exc)
                entry.update(
                    iso3=identity.iso3 if identity else "",
                    country_code=identity.country_code if identity else "",
                    problem=problem,
                )
            found.append(entry)
    return tuple(found)


# -- reading one function ----------------------------------------------------------

def _read_function(
    element: ET.Element, category: LossCategory, name: str
) -> VulnerabilityFunction:
    identifier = (element.get("id") or "").strip()
    if not identifier:
        raise GemError(f"{name} has a vulnerability function with no id.")

    distribution = (element.get("dist") or "").strip()
    if distribution != SUPPORTED_DISTRIBUTION:
        raise GemError(
            f"{name}: function {identifier!r} is distributed as {distribution!r}. "
            f"This reader supports {SUPPORTED_DISTRIBUTION!r} (beta) only, because "
            "that is the family the damage-bin discretisation integrates. Reading "
            "another family as beta would produce plausible and wrong tails."
        )

    imls = element.find("n:imls", _NS)
    if imls is None:
        raise GemError(f"{name}: function {identifier!r} states no intensity levels.")
    imt = (imls.get("imt") or "").strip()
    if not imt:
        raise GemError(
            f"{name}: function {identifier!r} states intensity levels with no "
            "intensity measure type. Which demand they represent would be a guess."
        )

    intensities = _floats(imls.text, identifier, "imls", name)
    means = _floats(element.findtext("n:meanLRs", "", _NS), identifier, "meanLRs", name)
    covs = _floats(element.findtext("n:covLRs", "", _NS), identifier, "covLRs", name)

    if not (len(intensities) == len(means) == len(covs)):
        raise GemError(
            f"{name}: function {identifier!r} has {len(intensities)} intensity "
            f"levels, {len(means)} mean loss ratios and {len(covs)} coefficients of "
            "variation. They are parallel lists and must be the same length."
        )
    if len(intensities) < 2:
        raise GemError(
            f"{name}: function {identifier!r} has fewer than two intensity levels, "
            "so there is nothing to interpolate between."
        )

    for previous, current in zip(intensities, intensities[1:], strict=False):
        if current <= previous:
            raise GemError(
                f"{name}: function {identifier!r} has intensity levels that do not "
                f"strictly increase ({previous} then {current})."
            )
    for value in means:
        if not 0.0 <= value <= 1.0:
            raise GemError(
                f"{name}: function {identifier!r} has a mean loss ratio of {value}, "
                "which is outside [0, 1]."
            )
    for value in covs:
        if value < 0.0:
            raise GemError(
                f"{name}: function {identifier!r} has a negative coefficient of "
                f"variation ({value})."
            )

    return VulnerabilityFunction(
        taxonomy=parse_taxonomy(identifier),
        loss_category=category,
        imt=imt,
        intensities=intensities,
        mean_loss_ratios=means,
        coefficients_of_variation=covs,
        distribution=distribution,
    )


def _floats(text: str | None, identifier: str, field: str, name: str) -> tuple[float, ...]:
    try:
        return tuple(float(item) for item in (text or "").split())
    except ValueError as exc:
        raise GemError(
            f"{name}: function {identifier!r} has an unreadable number in {field}: {exc}"
        ) from exc


def _resolve_category(
    requested: LossCategory | str | None, stated: str, name: str
) -> LossCategory:
    """Reconcile the category asked for with the one the file declares."""
    if stated and stated not in CATEGORY_BY_DECLARED:
        raise GemError(
            f"{name} declares an unrecognised loss category {stated!r}. Known "
            "categories: " + ", ".join(sorted(CATEGORY_BY_DECLARED)) + "."
        )
    declared = CATEGORY_BY_DECLARED.get(stated) if stated else None

    if requested is None:
        if declared is None:
            raise GemError(
                f"{name} declares no loss category and none was supplied, so which "
                "coverage its functions apply to would be a guess."
            )
        return declared

    asked = LossCategory(requested)
    if declared is not None and declared is not asked:
        raise GemError(
            f"{name} declares loss category {declared!s} but was read as {asked!s}. "
            "Reading a contents file as structural would attach the wrong damage "
            "relationship to a building."
        )
    return asked


def _read_bytes(source: BinaryIO | str | pathlib.Path) -> tuple[bytes, str]:
    if isinstance(source, str | pathlib.Path):
        path = pathlib.Path(source)
        try:
            return path.read_bytes(), path.name
        except OSError as exc:
            raise GemError(f"{path} could not be read: {exc}") from exc
    payload = source.read()
    return payload, getattr(source, "name", "<stream>")
