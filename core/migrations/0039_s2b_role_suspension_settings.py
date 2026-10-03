from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0038_s2b_coordinator_nomination_policy")]

    operations = [
        migrations.AddField(
            model_name="logovernanceconfigurationversion",
            name="role_suspension_enabled",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="logovernanceconfigurationversion",
            name="suspension_tolls_term",
            field=models.CharField(
                blank=True,
                choices=[("YES", "Yes"), ("NO", "No")],
                max_length=3,
                null=True,
            ),
        ),
    ]