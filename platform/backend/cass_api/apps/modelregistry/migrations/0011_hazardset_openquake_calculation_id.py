"""Record which OpenQuake calculation a hazard set was computed from.

A hazard set already carries the calculation's checksum, which says whether two
sets came from the same calculation. It did not carry the calculation's id,
which is what lets a later job be chained onto the same ground-motion fields --
and that is the whole basis of the OpenQuake reference comparison work package 4
asks for: the same events on both sides, rather than two calculations that ought
to agree.

Sets already registered are backfilled from the hazard run behind them. The
pipeline names a set after the model version and the first eight characters of
its run id, which is enough to find the run and the calculation it ran.
"""

from django.db import migrations, models


def backfill(apps, schema_editor):
    hazard_set = apps.get_model("modelregistry", "HazardSet")
    hazard_run = apps.get_model("runs", "HazardRun")

    calculations = {
        str(run.run_id)[:8]: run.openquake_calculation_id
        for run in hazard_run.objects.exclude(openquake_calculation_id="")
    }
    for record in hazard_set.objects.filter(openquake_calculation_id=""):
        suffix = record.version.rsplit("-", 1)[-1]
        calculation = calculations.get(suffix)
        if calculation:
            hazard_set.objects.filter(pk=record.pk).update(
                openquake_calculation_id=calculation
            )


def forget(apps, schema_editor):
    apps.get_model("modelregistry", "HazardSet").objects.update(
        openquake_calculation_id=""
    )


class Migration(migrations.Migration):
    dependencies = [
        ("modelregistry", "0010_gem_permission_public_models"),
        ("runs", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="hazardset",
            name="openquake_calculation_id",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.RunPython(backfill, forget),
    ]
