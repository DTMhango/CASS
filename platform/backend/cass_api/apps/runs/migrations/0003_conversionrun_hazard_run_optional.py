import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("runs", "0002_run_gate_detail_run_gate_summary"),
    ]

    operations = [
        migrations.AlterField(
            model_name="conversionrun",
            name="hazard_run",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="conversions",
                to="runs.hazardrun",
            ),
        ),
    ]
