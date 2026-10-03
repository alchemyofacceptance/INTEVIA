from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0037_s2b_office_appointment_consent")]

    operations = [
        migrations.AddField(
            model_name="logovernanceconfigurationversion",
            name="coordinator_nomination_policy",
            field=models.CharField(
                blank=True,
                choices=[
                    ("A", "Member nominations"),
                    ("B", "Coordinator nominations"),
                    ("C", "Volunteering and recruitment"),
                ],
                max_length=1,
                null=True,
            ),
        ),
    ]