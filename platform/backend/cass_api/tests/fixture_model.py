"""A registered model version for tests that need one, and nothing more.

Most of the control-plane tests are about the platform rather than the model:
that value reconciles, that a gate holds, that a run is reproducible, that a
manifest names what it used. They need *a* vulnerability set to route against,
and it does not matter what damage relationships stand behind it -- there are
none here, and none are claimed.

This used to come from a specification shipped in ``cass_keys``. It has been
retired. A hand-written routing table living in the library could be registered
by accident and would then look, in the registry, exactly like a model; the
real one is built from the published GEM data by ``modelregistry.gem``, which
needs a licensed clone on disk and is therefore not something a unit test can
call. So the fiction lives here, where only a test can reach it.

The four functions match the taxonomies the CASS occupancy assumptions emit.
**OED unknown occupancy (1000) reaches nothing**, deliberately: a portfolio
promoted without a stated occupancy returns ``fail_v`` and holds at the section
8 gate, and several tests depend on that gate meaning something.
"""

from __future__ import annotations

from apps.modelregistry import assembly, grid_build, pilot
from apps.modelregistry.assets import attach_vulnerability_mapping
from apps.modelregistry.models import ModelVersion, PublicationState, VulnerabilitySet
from cass_keys import vulnerability as vulnerability_specs
from cass_keys.lookup import VulnerabilityMapping

#: Every OED coverage type. A function covering only building would leave
#: contents unrouted, and unrouted value is ``fail_v``, not a quiet omission.
ALL_COVERAGES = (1, 2, 3, 4)

VERSION = "0.1.0-fixture"

FUNCTIONS = (
    vulnerability_specs.Function(
        label="Commercial, general",
        coverage_types=ALL_COVERAGES,
        required_imt="SA(0.3)",
        occupancy_codes=("1100",),
        reason="The fallback where construction is not known.",
    ),
    vulnerability_specs.Function(
        label="Commercial, reinforced concrete",
        coverage_types=ALL_COVERAGES,
        required_imt="SA(0.6)",
        occupancy_codes=("1100",),
        construction_codes=("5150",),
        reason="More specific than the general commercial function, so it wins.",
    ),
    vulnerability_specs.Function(
        label="Industrial, steel",
        coverage_types=ALL_COVERAGES,
        required_imt="SA(0.6)",
        occupancy_codes=("1150",),
        construction_codes=("5200",),
        reason="Industrial occupancy in steel frame.",
    ),
    vulnerability_specs.Function(
        label="Residential, masonry",
        coverage_types=ALL_COVERAGES,
        required_imt="SA(0.3)",
        occupancy_codes=("1050",),
        construction_codes=("5100",),
        reason="Masonry dominates residential stock in both pilot countries.",
    ),
)

NOTES = (
    "Test fixture. No damage relationship stands behind any of these "
    "identifiers: they route a taxonomy to a function, and nothing computes a "
    "loss from one. Not a vulnerability set, not a draft of one, and not "
    "usable for anything but exercising the platform."
)


def specification(country_code: str) -> vulnerability_specs.VulnerabilitySpecification:
    return vulnerability_specs.VulnerabilitySpecification(
        country_code=country_code.upper(),
        version=VERSION,
        label=f"{country_code.upper()} routing fixture",
        source="CASS test fixture. No damage relationship stands behind these.",
        functions=FUNCTIONS,
        open_questions=(
            "No damage relationship stands behind any of these identifiers.",
        ),
        notes=NOTES,
    )


def mapping(country_code: str) -> VulnerabilityMapping:
    return vulnerability_specs.build(specification(country_code))


def register_vulnerability(country_code: str, *, actor=None) -> VulnerabilitySet:
    """Register the fixture routing table as a vulnerability set."""
    code = country_code.upper()
    built = mapping(code)
    vulnerability_set, _ = VulnerabilitySet.objects.update_or_create(
        country_code=code,
        version=VERSION,
        defaults={
            "source": "CASS test fixture.",
            "taxonomy_generation": "OED 4.0.0 occupancy and construction codes",
            "licence": "",
            "licence_cleared": False,
            "licence_note": (
                "No licensed vulnerability source is attached. These identifiers "
                "route a taxonomy to a function; no damage relationship stands "
                "behind them."
            ),
            "function_count": len(built.entries),
            "imts_used": sorted({item.required_imt for item in built.entries}),
            "multi_channel_class_count": len(built.multi_channel_classes),
            "coverage_components": sorted(
                {str(item.coverage_type) for item in built.entries}
            ),
            "publication_state": PublicationState.DRAFT,
            "updated_by": actor,
        },
    )
    attach_vulnerability_mapping(
        vulnerability_set,
        vulnerability_specs.to_csv(built),
        filename=f"{code.lower()}-vuln-{VERSION}.csv",
        actor=actor,
    )
    return vulnerability_set


def register(country_code: str, *, actor=None) -> ModelVersion:
    """The grid, the fixture routing table and a model version joining them."""
    return pilot.register_model_version(
        country_code,
        vulnerability_set=register_vulnerability(country_code, actor=actor),
        vulnerability_limitations=f"- {NOTES}",
        actor=actor,
    )


#: Nepal's grid, written the way a country is now given one. CASS no longer
#: ships a Nepal prototype -- it was test data -- but the 30 June book carries
#: Nepali business, so the tests that map it build Nepal from a specification.
NEPAL_GRID = {
    "country_code": "NP",
    "version": "0.1.0-written",
    "label": "Nepal, written for the tests",
    "base_resolution_deg": "0.1",
    "mapping_tolerance_km": "0",
    "tiles": [
        {
            "name": "Nepal",
            "reason": "The national domain; the Main Himalayan Thrust runs its length.",
            "min_latitude": "26.3",
            "max_latitude": "30.5",
            "min_longitude": "80.0",
            "max_longitude": "88.3",
        }
    ],
    "refinements": [
        {
            "name": "Kathmandu valley",
            "reason": "The valley concentrates exposure and its sediments amplify strongly.",
            "resolution_deg": "0.025",
            # On the 0.1-degree lattice. It was drawn at 27.85 and 85.55, which put
            # a sliver of the valley in two cells; the builder now refuses that.
            "min_latitude": "27.6",
            "max_latitude": "27.9",
            "min_longitude": "85.2",
            "max_longitude": "85.6",
        },
        {
            "name": "Pokhara",
            "reason": "Material exposure.",
            "resolution_deg": "0.025",
            "min_latitude": "28.1",
            "max_latitude": "28.3",
            "min_longitude": "83.9",
            "max_longitude": "84.1",
        },
    ],
    "open_questions": ["Written for the tests; nobody has reviewed it."],
    "notes": "Test grid. Not a model asset.",
}


def register_written(grid_document, *, actor=None) -> ModelVersion:
    """A country with no compiled-in grid, built the way the platform builds one."""
    grid, _ = grid_build.build(grid_document, actor=actor)
    return assembly.assemble(
        grid=grid,
        vulnerability_set=register_vulnerability(grid.country_code, actor=actor),
        version=VERSION,
        actor=actor,
    )
