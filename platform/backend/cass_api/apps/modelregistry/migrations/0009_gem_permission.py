"""Record GEM Foundation's permission against the GEM vulnerability sets.

GEM Foundation has given explicit written permission to use its public Global
Exposure and Vulnerability models for the use KRE described (ADR 15). The sets
already registered from those models were cleared under the installation's
internal-use basis, or, in the demonstration seed, not cleared at all while
exactly this answer was awaited. They now name what clears them.

Hazard models and hazard sets are untouched: the permission does not cover a
hazard source model such as PuSGeN 2024.
"""

from django.db import migrations

GEM_PERMISSION = (
    "GEM Foundation has granted explicit permission to use the public Global "
    "Exposure and Vulnerability models for the use KRE described in its email of "
    "11 September 2026. Credit GEM Foundation as the source; anything "
    "redistributed carries the same licence."
)

INTERNAL_USE_LICENCE = (
    "Internal use within Klapton Re only: no redistribution outside the company "
    "and no commercial exploitation."
)

#: How a set built from GEM names its source, in both the registration command
#: and the demonstration seed.
GEM_SOURCE_PREFIX = "GEM Global"


def record_permission(apps, schema_editor):
    vulnerability_set = apps.get_model("modelregistry", "VulnerabilitySet")
    vulnerability_set.objects.filter(source__startswith=GEM_SOURCE_PREFIX).update(
        licence_cleared=True, licence_note=GEM_PERMISSION
    )


def restore_basis(apps, schema_editor):
    """Reversed to the internal-use note. The permission itself is GEM's, and stands."""
    vulnerability_set = apps.get_model("modelregistry", "VulnerabilitySet")
    vulnerability_set.objects.filter(licence_note=GEM_PERMISSION).update(
        licence_note=INTERNAL_USE_LICENCE
    )


class Migration(migrations.Migration):
    dependencies = [("modelregistry", "0008_conversiontolerances_hazardbenchmark")]

    operations = [migrations.RunPython(record_permission, restore_basis)]
