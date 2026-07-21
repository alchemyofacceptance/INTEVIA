import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0012_s007_identity_foundation")]

    operations = [
        migrations.AlterField(
            model_name="identity",
            name="identity_id",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="identity",
            name="canonical_username",
            field=models.CharField(max_length=255, unique=True),
        ),
        migrations.AlterField(
            model_name="identity",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddConstraint(
            model_name="identity",
            constraint=models.CheckConstraint(
                condition=models.Q(("access_epoch__gte", 0)),
                name="identity_access_epoch_nonnegative",
            ),
        ),
        migrations.AddConstraint(
            model_name="identity",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("access_state__in", (
                        "pending",
                        "active",
                        "restricted",
                        "deactivated",
                    ))
                ),
                name="identity_access_state_valid",
            ),
        ),
    ]