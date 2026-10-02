from django.db import migrations, models
from django.db.models.deletion import PROTECT


def install_authority_decision_guards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 authority decision guards require PostgreSQL")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            CREATE FUNCTION public.s015_authoritydecision_guard()
            RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE expected_reference text;
            DECLARE expected_target_fingerprint text;
            BEGIN
                IF NEW.recorded_at IS NOT NULL THEN
                    RAISE EXCEPTION 'S015 authority decision recorded_at is database-assigned'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF NEW.decision_payload IS DISTINCT FROM
                    convert_from(NEW.decision_payload_bytes, 'UTF8')::jsonb THEN
                    RAISE EXCEPTION 'S015 authority decision payload bytes do not match JSON'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF NEW.target IS DISTINCT FROM convert_from(NEW.target_bytes, 'UTF8')::jsonb THEN
                    RAISE EXCEPTION 'S015 authority decision target bytes do not match JSON'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                expected_target_fingerprint := encode(
                    sha256(
                        convert_to('INTEVIA:S015:ORGANISM_AUTHORITY_TARGET:v1', 'UTF8')
                        || decode('00', 'hex')
                        || NEW.target_bytes
                    ),
                    'hex'
                );
                IF NEW.target_fingerprint <> expected_target_fingerprint THEN
                    RAISE EXCEPTION 'S015 authority decision target fingerprint does not recompute'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                expected_reference := 's015d1:' || encode(
                    sha256(
                        convert_to('INTEVIA:S015:ORGANISM_AUTHORITY_DECISION:v1', 'UTF8')
                        || decode('00', 'hex')
                        || NEW.decision_payload_bytes
                    ),
                    'hex'
                );
                IF NEW.decision_reference <> expected_reference THEN
                    RAISE EXCEPTION 'S015 authority decision digest does not recompute'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                NEW.recorded_at := statement_timestamp();
                RETURN NEW;
            END;
            $$
        """)
        cursor.execute("""
            CREATE TRIGGER s015_authoritydecision_insert_guard
            BEFORE INSERT ON public.core_authoritydecision
            FOR EACH ROW EXECUTE FUNCTION public.s015_authoritydecision_guard()
        """)
        cursor.execute("""
            CREATE TRIGGER s015_authoritydecision_immutable
            BEFORE UPDATE OR DELETE ON public.core_authoritydecision
            FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()
        """)
        cursor.execute("""
            CREATE TRIGGER s015_authoritydecision_truncate_immutable
            BEFORE TRUNCATE ON public.core_authoritydecision
            FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation()
        """)


def remove_authority_decision_guards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 authority decision guards require PostgreSQL")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS (SELECT 1 FROM public.core_authoritydecision)")
        if cursor.fetchone()[0]:
            raise RuntimeError("S015 authority decisions exist; refusing destructive reverse")
        cursor.execute(
            "DROP TRIGGER s015_authoritydecision_truncate_immutable ON public.core_authoritydecision"
        )
        cursor.execute(
            "DROP TRIGGER s015_authoritydecision_immutable ON public.core_authoritydecision"
        )
        cursor.execute(
            "DROP TRIGGER s015_authoritydecision_insert_guard ON public.core_authoritydecision"
        )
        cursor.execute("DROP FUNCTION public.s015_authoritydecision_guard()")


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", "0033_s015_application_authority")]

    operations = [
        migrations.CreateModel(
            name="AuthorityDecision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("decision_reference", models.CharField(max_length=71, unique=True)),
                ("decision_payload", models.JSONField()),
                ("decision_payload_bytes", models.BinaryField()),
                ("request_kind", models.CharField(max_length=32)),
                ("action", models.CharField(max_length=48)),
                ("command_subtype", models.CharField(max_length=96)),
                ("actor_identity_id", models.UUIDField()),
                ("actor_access_epoch", models.PositiveBigIntegerField()),
                ("actor_credential_pk", models.PositiveBigIntegerField(blank=True, null=True)),
                ("target", models.JSONField()),
                ("target_bytes", models.BinaryField()),
                ("target_fingerprint", models.CharField(max_length=64)),
                ("effective_at", models.DateTimeField()),
                ("evaluated_at", models.DateTimeField()),
                ("authority_reference", models.CharField(blank=True, max_length=255, null=True)),
                ("facts_read", models.JSONField(default=list)),
                ("outcome", models.CharField(max_length=9)),
                ("refusal_code", models.CharField(blank=True, max_length=48, null=True)),
                ("provider_identity", models.CharField(max_length=255)),
                ("provider_version", models.CharField(max_length=64)),
                ("request_reference", models.CharField(max_length=128)),
                ("idempotency_key", models.CharField(max_length=120)),
                ("recorded_at", models.DateTimeField(editable=False, null=True)),
                ("actor", models.ForeignKey(on_delete=PROTECT, related_name="authority_decisions", to="core.identity")),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(decision_reference__regex=r"^s015d1:[0-9a-f]{64}$"),
                        name="s015_authdecision_ref_shape_ck",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(outcome__in=["QUALIFIED", "REFUSED"]),
                        name="s015_authdecision_outcome_ck",
                    ),
                    models.CheckConstraint(
                        condition=(
                            models.Q(outcome="QUALIFIED", refusal_code__isnull=True)
                            | models.Q(outcome="REFUSED", refusal_code__isnull=False)
                        ),
                        name="s015_authdecision_refusal_ck",
                    ),
                ],
            },
        ),
        migrations.RunPython(install_authority_decision_guards, remove_authority_decision_guards),
    ]