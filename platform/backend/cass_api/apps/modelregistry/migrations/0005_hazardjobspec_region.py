from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("modelregistry", "0004_hazardmodel_hazardjobspec_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="hazardjobspec",
            name="region",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Bounds of the cells this run computes; empty means the whole grid.",
            ),
        ),
    ]
