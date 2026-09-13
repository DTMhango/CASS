"""An analysis names the assumption set it weighs unknown attributes under."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("runs", "0003_conversionrun_hazard_run_optional"),
        ("modelregistry", "0007_vulnerabilityset_assumption_variants"),
    ]

    operations = [
        migrations.AddField(
            model_name="analysisrun",
            name="assumption_set",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="analysis_runs",
                to="modelregistry.assumptionset",
            ),
        ),
    ]
