"""Record GEM Foundation's permission against every model GEM publishes.

GEM's reply to KRE covers what the Data and Models section of its terms covers:
everything GEM makes publicly available. Migration 0009 read it as naming only
the Global Exposure and Vulnerability models, and left the PuSGeN 2024 hazard
model, which GEM publishes in its mosaic, under the internal-use basis. This
records the permission against GEM-published hazard models and the hazard sets
computed from them, and gives the vulnerability sets 0009 touched the wording
that says what the permission covers (ADR 15).

A hazard model is GEM-published when its publisher names GEM, which is how the
mosaic packages credit it and the rule a new upload follows.
"""

import re

from django.db import migrations

GEM_PERMISSION = (
    "GEM Foundation has granted explicit permission to use the data and models it "
    "makes publicly available, for the use KRE described in its email of "
    "11 September 2026. Credit the authors and GEM Foundation as the source; "
    "anything redistributed carries the same licence."
)

#: What 0009 wrote, before the permission's reach was read correctly.
EARLIER_WORDING = (
    "GEM Foundation has granted explicit permission to use the public Global "
    "Exposure and Vulnerability models for the use KRE described in its email of "
    "11 September 2026. Credit GEM Foundation as the source; anything "
    "redistributed carries the same licence."
)

INTERNAL_USE_LICENCE = (
    "Internal use within Klapton Re only: no redistribution outside the company "
    "and no commercial exploitation."
)

GEM_PUBLISHER = re.compile(r"\bGEM\b")


def record_permission(apps, schema_editor):
    vulnerability_set = apps.get_model("modelregistry", "VulnerabilitySet")
    hazard_model = apps.get_model("modelregistry", "HazardModel")
    hazard_set = apps.get_model("modelregistry", "HazardSet")

    vulnerability_set.objects.filter(licence_note=EARLIER_WORDING).update(
        licence_note=GEM_PERMISSION
    )
    for model in hazard_model.objects.all():
        if not GEM_PUBLISHER.search(model.source_organisation or ""):
            continue
        hazard_model.objects.filter(pk=model.pk).update(
            licence_cleared=True, licence_note=GEM_PERMISSION
        )
        # A hazard set names the model it was computed from by its reference.
        reference = f"{model.country_code.lower()}-hazmodel-{model.version}"
        hazard_set.objects.filter(source_model__endswith=f"({reference})").update(
            licence_cleared=True, licence_note=GEM_PERMISSION
        )


def restore_earlier(apps, schema_editor):
    """Reversed to the wording and bases before this. The permission itself stands."""
    for name in ("HazardModel", "HazardSet"):
        apps.get_model("modelregistry", name).objects.filter(
            licence_note=GEM_PERMISSION
        ).update(licence_note=INTERNAL_USE_LICENCE)
    apps.get_model("modelregistry", "VulnerabilitySet").objects.filter(
        licence_note=GEM_PERMISSION
    ).update(licence_note=EARLIER_WORDING)


class Migration(migrations.Migration):
    dependencies = [("modelregistry", "0009_gem_permission")]

    operations = [migrations.RunPython(record_permission, restore_earlier)]
