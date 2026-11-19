from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("core", "0111_care_office_record")]

    operations = [
        migrations.AddField(
            model_name="identity",
            name="colour_mode",
            field=models.CharField(
                choices=[
                    ("device", "Use device setting"),
                    ("light", "Light"),
                    ("dark", "Dark"),
                ],
                max_length=6,
                default="device",
                db_default="device",
            ),
        ),
        migrations.AddConstraint(
            model_name="identity",
            constraint=models.CheckConstraint(
                condition=models.Q(colour_mode__in=("device", "light", "dark")),
                name="identity_colour_mode_valid",
            ),
        ),
    ]
