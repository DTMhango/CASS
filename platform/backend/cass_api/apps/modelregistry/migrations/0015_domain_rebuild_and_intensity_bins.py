"""Grid domains and specifications; hazard sets stored once and rebuilt; bin fingerprints.

Three things arrive together because they share the records.

A grid keeps the specification it was built from, which now includes the domain
it kept -- land, and places near buildings or residents.

A hazard set records where its calculation's datastore is kept, a fingerprint of
its ground motion, whether OpenQuake's own copy has been removed, the saved
configuration behind it, and the set it was rebuilt from. Sets already
registered are backfilled with their datastore from the hazard run behind them,
found the way migration 0011 found the calculation: the pipeline names a set
after the first eight characters of its run id.

Hazard sets and vulnerability sets record the fingerprint of the intensity-bin
dictionaries they were built against. Every set registered before this migration
was built against the dictionary in force from 12 September 2026 until 17
September 2026 -- the one this change widened -- so a blank fingerprint is filled
with that dictionary's. That is what lets the platform say those sets need
rebuilding, rather than reading them as sets whose bins are unknown and letting
them through.
"""

from django.db import migrations, models
import django.db.models.deletion

#: The fingerprint of the intensity-bin dictionaries in force from 12 to 17
#: September 2026: 50 logarithmic bins per measure from 0.005 g to 6 g (PGA),
#: 13 g (SA(0.3)), 9 g (SA(0.6)) and 8 g (SA(1.0)), bin version 0.1.0-draft.
PREVIOUS_BINS_CHECKSUM = "5216763f41d2f54935b101fca2ea61fc2b9830d60a8125b72bf79a9b03133fbc"


def backfill(apps, schema_editor):
    hazard_set = apps.get_model("modelregistry", "HazardSet")
    vulnerability_set = apps.get_model("modelregistry", "VulnerabilitySet")
    hazard_run = apps.get_model("runs", "HazardRun")
    artifact_link = apps.get_model("artifacts", "ArtifactLink")

    hazard_set.objects.filter(intensity_bins_checksum="").update(
        intensity_bins_checksum=PREVIOUS_BINS_CHECKSUM
    )
    vulnerability_set.objects.filter(intensity_bins_checksum="").update(
        intensity_bins_checksum=PREVIOUS_BINS_CHECKSUM
    )

    runs = {str(run.run_id)[:8]: run.run_id for run in hazard_run.objects.all()}
    for record in hazard_set.objects.filter(datastore_uri=""):
        run_id = runs.get(record.version.rsplit("-", 1)[-1])
        if run_id is None:
            continue
        link = (
            artifact_link.objects.filter(
                subject_type="hazard_run", subject_id=run_id, role="openquake_datastore"
            )
            .select_related("artifact")
            .first()
        )
        if link is not None:
            hazard_set.objects.filter(pk=record.pk).update(datastore_uri=link.artifact.uri)


def forget(apps, schema_editor):
    """The new columns are dropped by reversing the field operations."""


class Migration(migrations.Migration):

    dependencies = [
        ('modelregistry', '0014_hazardset_logic_tree_paths'),
        ('runs', '0008_run_stage_progress'),
        ('artifacts', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='areaperilgrid',
            name='specification',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='hazardset',
            name='datastore_uri',
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name='hazardset',
            name='ground_motion_digest',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='hazardset',
            name='intensity_bins_checksum',
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name='hazardset',
            name='job_spec',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hazard_sets', to='modelregistry.hazardjobspec'),
        ),
        migrations.AddField(
            model_name='hazardset',
            name='openquake_calculation_removed',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='hazardset',
            name='rebuilt_from',
            field=models.ForeignKey(blank=True, help_text='The set this one was rebuilt from: the same calculation, binned again against the intensity bins current when it was rebuilt.', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='rebuilds', to='modelregistry.hazardset'),
        ),
        migrations.AddField(
            model_name='vulnerabilityset',
            name='intensity_bins_checksum',
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.RunPython(backfill, forget),
    ]
