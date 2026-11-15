"""The INTEVIA Clock, code landing: the automatic time fields of the core models (CK-2i).

Thirty fields that Django filled from the web server's clock (``auto_now_add``, ``auto_now``)
become ``core.clock_fields.ClockDateTimeField``, which fills them from the INTEVIA Clock. The
column is the same column, so this migration changes the recorded state of the models and
nothing in the database: PostgreSQL is sent no statement for any of these operations, forward
or backward.

It also records one change that was already in the model and not yet in any migration:
``RootValueDecision.predecessor``. Migration 0065 wrote the field's target as ``'self'``;
Django records it by the model's own name, and has asked for this operation ever since. That
too is a change of recorded state only (owed row C1-121, items 7 and 41).
"""

import core.clock_fields
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0103_clock_storage_column_defaults"),
    ]

    # Every operation changes Django's recorded state of the models and nothing in the database, so they are
    # given as state operations with no database operation beside them: migrating forward or backward runs no
    # statement and does not build the models again for each field.
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="authoritybasis",
                    name="recorded_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="authorityderivationedge",
                    name="recorded_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="authorityprincipal",
                    name="recorded_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="authorityprincipalaliasreview",
                    name="recorded_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="circle",
                    name="created_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="contextualroleassignment",
                    name="created_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="contribution",
                    name="created_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="contribution",
                    name="updated_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now=True),
                ),
                migrations.AlterField(
                    model_name="contributionversion",
                    name="created_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="emergencyauthorityenvelope",
                    name="recorded_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="event",
                    name="updated_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now=True),
                ),
                migrations.AlterField(
                    model_name="eventattendance",
                    name="created_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="eventattendance",
                    name="updated_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now=True),
                ),
                migrations.AlterField(
                    model_name="eventattendanceeligibilityreceipt",
                    name="created_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="eventattendancetransition",
                    name="created_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="eventresourcerelationshipevidence",
                    name="recorded_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="evidencereference",
                    name="created_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="governeddetermination",
                    name="recorded_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="governedvisibilitygrant",
                    name="recorded_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="identity",
                    name="created_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="identity",
                    name="updated_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now=True),
                ),
                migrations.AlterField(
                    model_name="libraryresource",
                    name="updated_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now=True),
                ),
                migrations.AlterField(
                    model_name="livingorganism",
                    name="created_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="organismcommandreceipt",
                    name="completed_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="organismmembership",
                    name="created_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="organismroledefinition",
                    name="created_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="profilerole",
                    name="assigned_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="role",
                    name="created_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now_add=True),
                ),
                migrations.AlterField(
                    model_name="rootvaluedecision",
                    name="predecessor",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="core.rootvaluedecision",
                    ),
                ),
                migrations.AlterField(
                    model_name="service",
                    name="updated_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now=True),
                ),
                migrations.AlterField(
                    model_name="serviceactivity",
                    name="updated_at",
                    field=core.clock_fields.ClockDateTimeField(auto_now=True),
                ),
            ],
        ),
    ]
