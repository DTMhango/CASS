"""Clear every registered asset under the installation's internal-use basis.

CASS is internal to Klapton Re: the model data it carries is used inside the
company, not redistributed and not sold. That entitlement covers everything
already registered, so the records say so rather than leaving assets marked
uncleared and every screen carrying a caveat about it.
"""

from django.db import migrations, models


INTERNAL_USE_LICENCE = (
    "Internal use within Klapton Re only: no redistribution outside the company "
    "and no commercial exploitation."
)


def clear(apps, schema_editor):
    for name in ("VulnerabilitySet", "HazardModel", "HazardSet"):
        model = apps.get_model("modelregistry", name)
        model.objects.update(licence_cleared=True, licence_note=INTERNAL_USE_LICENCE)


def unclear(apps, schema_editor):
    """Reversed only in shape: the entitlement itself is not ours to withdraw."""
    for name in ("VulnerabilitySet", "HazardModel", "HazardSet"):
        model = apps.get_model("modelregistry", name)
        model.objects.filter(licence_note=INTERNAL_USE_LICENCE).update(
            licence_cleared=False
        )


class Migration(migrations.Migration):
    dependencies = [("modelregistry", "0005_hazardjobspec_region")]

    operations = [
        migrations.AlterField(
            model_name="vulnerabilityset",
            name="licence_cleared",
            field=models.BooleanField(
                default=True,
                help_text="Cleared under the installation's internal-use basis.",
            ),
        ),
        migrations.AlterField(
            model_name="vulnerabilityset",
            name="licence_note",
            field=models.TextField(blank=True, default=INTERNAL_USE_LICENCE),
        ),
        migrations.AlterField(
            model_name="hazardmodel",
            name="licence_cleared",
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name="hazardmodel",
            name="licence_note",
            field=models.TextField(blank=True, default=INTERNAL_USE_LICENCE),
        ),
        migrations.AlterField(
            model_name="hazardset",
            name="licence_cleared",
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name="hazardset",
            name="licence_note",
            field=models.TextField(blank=True, default=INTERNAL_USE_LICENCE),
        ),
        migrations.RunPython(clear, unclear),
    ]
