"""An enrichment run may rest on the baseline weights rather than a named set.

A run that names no assumption set still has its lineage recorded; it applied
the weights its model was built with, and there is no assumption set record to
point at.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("exposure", "0006_drop_cedant"),
        ("modelregistry", "0007_vulnerabilityset_assumption_variants"),
    ]

    operations = [
        migrations.AlterField(
            model_name="enrichmentrun",
            name="assumption_set",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="enrichment_runs",
                to="modelregistry.assumptionset",
            ),
        ),
    ]
