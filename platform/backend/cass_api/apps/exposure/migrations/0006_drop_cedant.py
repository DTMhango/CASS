"""A portfolio no longer records a cedant.

Nothing read it. No screen showed it, no run used it and no import set it: the
only writer was the field on the create form, which is now gone. A field a
person fills in that nothing then reads is one that ends up disagreeing with
the files it claims to describe.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("exposure", "0005_sourcerisklocation_storeys_reviewdecision"),
    ]

    operations = [
        migrations.RemoveField(model_name="exposureversion", name="cedant"),
    ]
