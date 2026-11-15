"""The INTEVIA Clock, code landing: the automatic time fields of the email records (CK-2i).

Three fields that Django filled from the web server's clock become
``core.clock_fields.ClockDateTimeField``, which fills them from the INTEVIA Clock. The column
is the same column, so this migration changes the recorded state of the models and nothing in
the database.
"""

import core.clock_fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("email_foundation", "0001_initial"),
    ]

    # Every operation changes Django's recorded state of the models and nothing in the database, so they are
    # given as state operations with no database operation beside them: migrating forward or backward runs no
    # statement and does not build the models again for each field.
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="deliveryattempt",
                    name="created_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="localcapture",
                    name="captured_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="outbox",
                    name="created_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
            ],
        ),
    ]
