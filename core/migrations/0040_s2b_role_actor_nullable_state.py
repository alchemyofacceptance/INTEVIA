from django.db import migrations, models
from django.db.models.deletion import PROTECT


class Migration(migrations.Migration):
    dependencies = [("core", "0039_s2b_role_suspension_settings")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="contextualroleassignmentstateevent",
                    name="actor",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=PROTECT,
                        related_name="+",
                        to="core.identity",
                    ),
                ),
                migrations.AlterField(
                    model_name="contextualroleassignmentstateevent",
                    name="actor_access_epoch",
                    field=models.PositiveBigIntegerField(blank=True, null=True),
                ),
            ],
        ),
    ]