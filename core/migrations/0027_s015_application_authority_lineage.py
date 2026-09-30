from django.db import migrations, models
from django.db.models import Q


TABLES = ("core_application", "core_applicationwithdrawal")


def require_empty_operational_records(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0027 requires PostgreSQL")
    with schema_editor.connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(f"LOCK TABLE public.{table} IN SHARE ROW EXCLUSIVE MODE")
        for table in TABLES:
            cursor.execute(f"SELECT count(*) FROM public.{table}")
            if cursor.fetchone()[0]:
                raise RuntimeError(f"S015 0027 preflight halt: {table} must be empty; no backfill is authorised")


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("core", "0026_s015_application_admission_time")]

    operations = [
        migrations.RunPython(require_empty_operational_records, migrations.RunPython.noop),
        migrations.AddField(
            model_name="application", name="authority_decision_reference",
            field=models.CharField(max_length=71),
        ),
        migrations.AddField(
            model_name="application", name="authority_reference",
            field=models.CharField(max_length=255),
        ),
        migrations.AddField(
            model_name="application", name="actor_access_epoch",
            field=models.PositiveBigIntegerField(),
        ),
        migrations.AddField(
            model_name="applicationwithdrawal", name="authority_decision_reference",
            field=models.CharField(max_length=71),
        ),
        migrations.AddField(
            model_name="applicationwithdrawal", name="authority_reference",
            field=models.CharField(max_length=255),
        ),
        migrations.AddField(
            model_name="applicationwithdrawal", name="actor_access_epoch",
            field=models.PositiveBigIntegerField(),
        ),
        migrations.AddConstraint(
            model_name="application",
            constraint=models.CheckConstraint(
                condition=Q(authority_decision_reference__regex=r"^s015d1:[0-9a-f]{64}$"),
                name="s3fu_application_decision_shape_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="applicationwithdrawal",
            constraint=models.CheckConstraint(
                condition=Q(authority_decision_reference__regex=r"^s015d1:[0-9a-f]{64}$"),
                name="s3fu_withdrawal_decision_shape_ck",
            ),
        ),
    ]