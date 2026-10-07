import uuid

from django.db import migrations, models
import django.db.models.deletion


def _execute(schema_editor, sql):
    schema_editor.execute(sql)


def install_p1(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    statements = (
        """
        CREATE OR REPLACE FUNCTION public.p1_assign_registration_commitment()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE preimage text;
        BEGIN
            IF NEW.registration_commitment IS NOT NULL THEN
                RAISE EXCEPTION 'P1 registration commitment is database-assigned'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            -- PostgreSQL orders same-event triggers by name; commitment runs before record_time.
            preimage := '{"actor_access_epoch":' ||
                    public.s015_canonical_value('bigint'::regtype, to_jsonb(NEW.actor_access_epoch)) ||
                ',"authority_reference":' ||
                    public.s015_canonical_value('character varying'::regtype, to_jsonb(NEW.authority_reference)) ||
                ',"effective_at":' ||
                    public.s015_canonical_value('timestamp with time zone'::regtype, to_jsonb(NEW.effective_at)) ||
                ',"enterer_executor":' ||
                    public.s015_canonical_value('character varying'::regtype, to_jsonb(NEW.enterer_executor)) ||
                ',"entry_mode":' ||
                    public.s015_canonical_value('character varying'::regtype, to_jsonb(NEW.entry_mode)) ||
                ',"evidence_reference":' ||
                    public.s015_canonical_value('character varying'::regtype, to_jsonb(NEW.evidence_reference)) ||
                ',"identity_id":' ||
                    public.s015_canonical_value('bigint'::regtype, to_jsonb(NEW.identity_id)) ||
                ',"received_at":' ||
                    public.s015_canonical_value('timestamp with time zone'::regtype, to_jsonb(NEW.received_at)) ||
                ',"recorded_at":' ||
                    public.s015_canonical_value(
                        'timestamp with time zone'::regtype,
                        to_jsonb(statement_timestamp())
                    ) ||
                ',"registration_receipt_id":' ||
                    public.s015_canonical_value(
                        'bigint'::regtype,
                        to_jsonb(NEW.registration_receipt_id)
                    ) ||
                ',"registration_uuid":' ||
                    public.s015_canonical_value('uuid'::regtype, to_jsonb(NEW.registration_uuid)) || '}';
            NEW.registration_commitment := public.s015_0022_digest('s015r1', preimage);
            RETURN NEW;
        END $$;
        """,
        """
        CREATE OR REPLACE FUNCTION public.p1_assign_registrationreceipt()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.received_at IS NOT NULL OR NEW.transaction_id IS NOT NULL THEN
                RAISE EXCEPTION 'P1 registration receipt fields are database-assigned'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            NEW.received_at := clock_timestamp();
            NEW.transaction_id := txid_current();
            RETURN NEW;
        END $$;
        """,
        """
        CREATE OR REPLACE FUNCTION public.p1_guard_learnerregistration_terms()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                  FROM public.core_platformtermsacceptance a
                  JOIN public.core_platformtermsversion v ON v.id = a.terms_version_id
                  JOIN public.core_registrationreceipt receipt
                    ON receipt.id = NEW.registration_receipt_id
                 WHERE NEW.evidence_reference =
                           'platform-terms-acceptance:' || a.acceptance_uuid::text
                   AND receipt.transaction_id = txid_current()
                   AND NEW.received_at = receipt.received_at
                   AND NEW.effective_at = receipt.received_at
                   AND a.acceptance_kind = 'REGISTRATION'
                   AND a.subject_reference =
                           'learner-registration:' || NEW.registration_uuid::text
                   AND a.accepted_by_identity_id = NEW.identity_id
                   AND a.accepted_by_access_epoch = NEW.actor_access_epoch
                   AND a.recorded_at <= NEW.recorded_at
                   AND a.accepted_at = receipt.received_at
                   AND a.received_at = receipt.received_at
                   AND v.id = (
                        SELECT latest.id
                          FROM public.core_platformtermsversion latest
                         WHERE latest.effective_from <= receipt.received_at
                           AND (latest.effective_until IS NULL
                                OR receipt.received_at < latest.effective_until)
                           AND latest.recorded_at <= receipt.received_at
                           AND latest.id NOT IN (
                                SELECT successor.supersedes_id
                                  FROM public.core_platformtermsversion successor
                                 WHERE successor.supersedes_id IS NOT NULL
                                   AND successor.effective_from <= receipt.received_at
                                   AND (successor.effective_until IS NULL
                                        OR receipt.received_at < successor.effective_until)
                                   AND successor.recorded_at <= receipt.received_at
                           )
                         ORDER BY latest.effective_from DESC,
                                  latest.recorded_at DESC,
                                  latest.id DESC
                         LIMIT 1
                   )
            ) THEN
                RAISE EXCEPTION 'P1 registration requires acceptance of the latest terms version at its database receipt'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$;
        """,
        """
        CREATE OR REPLACE FUNCTION public.p1_guard_identityresolution_ig2()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.canonical_username IS DISTINCT FROM OLD.canonical_username THEN
                RAISE EXCEPTION 'IG-2: canonical_username is immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NEW.credential_link IS DISTINCT FROM OLD.credential_link THEN
                RAISE EXCEPTION 'IG-2: credential_link is immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$;
        """,
        """
        CREATE OR REPLACE FUNCTION public.p1_guard_identity_username_equal()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM public.core_identityresolution r
                 WHERE r.identity_id = NEW.id
                   AND r.canonical_username IS DISTINCT FROM NEW.canonical_username
            ) THEN
                RAISE EXCEPTION 'P1 identity canonical_username differs from its resolution row'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$;
        """,
        """
        CREATE OR REPLACE FUNCTION public.s015_0022_guard_identity_resolution()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                  FROM public.core_identityresolution r
                 WHERE r.identity_id = NEW.id
            ) THEN
                RAISE EXCEPTION 'A-1: identity requires an identity-resolution row at commit'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$;
        """,
        """
        CREATE TRIGGER p1_learnerregistration_commitment
        BEFORE INSERT ON public.core_learnerregistration
        FOR EACH ROW EXECUTE FUNCTION public.p1_assign_registration_commitment()
        """,
        """
        CREATE TRIGGER p1_learnerregistration_record_time
        BEFORE INSERT ON public.core_learnerregistration
        FOR EACH ROW EXECUTE FUNCTION public.s3_assign_record_time()
        """,
        """
        CREATE TRIGGER p1_learnerregistration_terms
        BEFORE INSERT ON public.core_learnerregistration
        FOR EACH ROW EXECUTE FUNCTION public.p1_guard_learnerregistration_terms()
        """,
        """
        CREATE TRIGGER p1_learnerregistration_immutable
        BEFORE UPDATE OR DELETE ON public.core_learnerregistration
        FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()
        """,
        """
        CREATE TRIGGER p1_registrationreceipt_assign
        BEFORE INSERT ON public.core_registrationreceipt
        FOR EACH ROW EXECUTE FUNCTION public.p1_assign_registrationreceipt()
        """,
        """
        CREATE TRIGGER p1_registrationreceipt_immutable
        BEFORE UPDATE OR DELETE ON public.core_registrationreceipt
        FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()
        """,
        """
        CREATE TRIGGER s015_registrationreceipt_truncate_immutable
        BEFORE TRUNCATE ON public.core_registrationreceipt
        FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation()
        """,
        """
        CREATE TRIGGER s015_learnerregistration_truncate_immutable
        BEFORE TRUNCATE ON public.core_learnerregistration
        FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation()
        """,
        """
        CREATE TRIGGER p1_identitytransition_record_time
        BEFORE INSERT ON public.core_identitytransition
        FOR EACH ROW EXECUTE FUNCTION public.s3_assign_record_time()
        """,
        """
        CREATE TRIGGER p1_identityresolution_ig2
        BEFORE UPDATE ON public.core_identityresolution
        FOR EACH ROW EXECUTE FUNCTION public.p1_guard_identityresolution_ig2()
        """,
    )
    for statement in statements:
        _execute(schema_editor, statement)

    _execute(
        schema_editor,
        "DROP TRIGGER s015_0022_guard_identity_resolution ON public.core_identity",
    )
    _execute(
        schema_editor,
        """
        CREATE CONSTRAINT TRIGGER s015_0022_guard_identity_resolution
        AFTER INSERT ON public.core_identity
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.s015_0022_guard_identity_resolution()
        """,
    )
    _execute(
        schema_editor,
        """
        CREATE CONSTRAINT TRIGGER p1_identity_username_equal
        AFTER INSERT OR UPDATE OF canonical_username ON public.core_identity
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.p1_guard_identity_username_equal()
        """,
    )
    for signature in (
        "public.p1_assign_registration_commitment()",
        "public.p1_assign_registrationreceipt()",
        "public.p1_guard_learnerregistration_terms()",
        "public.p1_guard_identityresolution_ig2()",
        "public.p1_guard_identity_username_equal()",
    ):
        _execute(schema_editor, f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")


def remove_p1(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM public.core_learnerregistration")
        if cursor.fetchone()[0]:
            raise RuntimeError("P1 migration reverse refused while learner registrations exist")
        cursor.execute("SELECT count(*) FROM public.core_registrationreceipt")
        if cursor.fetchone()[0]:
            raise RuntimeError("P1 migration reverse refused while registration receipts exist")

    for trigger, table in (
        ("s015_registrationreceipt_truncate_immutable", "public.core_registrationreceipt"),
        ("p1_registrationreceipt_immutable", "public.core_registrationreceipt"),
        ("p1_registrationreceipt_assign", "public.core_registrationreceipt"),
        ("p1_identity_username_equal", "public.core_identity"),
        ("s015_0022_guard_identity_resolution", "public.core_identity"),
        ("p1_identityresolution_ig2", "public.core_identityresolution"),
        ("p1_identitytransition_record_time", "public.core_identitytransition"),
        ("p1_learnerregistration_commitment", "public.core_learnerregistration"),
        ("p1_learnerregistration_record_time", "public.core_learnerregistration"),
        ("p1_learnerregistration_terms", "public.core_learnerregistration"),
        ("p1_learnerregistration_immutable", "public.core_learnerregistration"),
        ("s015_learnerregistration_truncate_immutable", "public.core_learnerregistration"),
    ):
        _execute(schema_editor, f"DROP TRIGGER IF EXISTS {trigger} ON {table}")

    _execute(
        schema_editor,
        """
        CREATE OR REPLACE FUNCTION public.s015_0022_guard_identity_resolution()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RETURN NEW;
        END $$;
        """,
    )
    _execute(
        schema_editor,
        """
        CREATE TRIGGER s015_0022_guard_identity_resolution
        AFTER INSERT ON public.core_identity
        FOR EACH ROW EXECUTE FUNCTION public.s015_0022_guard_identity_resolution()
        """,
    )
    for signature in (
        "public.p1_assign_registration_commitment()",
        "public.p1_assign_registrationreceipt()",
        "public.p1_guard_learnerregistration_terms()",
        "public.p1_guard_identityresolution_ig2()",
        "public.p1_guard_identity_username_equal()",
    ):
        _execute(schema_editor, f"DROP FUNCTION IF EXISTS {signature}")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0042_sb1_founding_terms_phases"),
    ]

    operations = [
        migrations.CreateModel(
            name="RegistrationReceipt",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("received_at", models.DateTimeField(editable=False)),
                ("transaction_id", models.PositiveBigIntegerField(editable=False)),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="LearnerRegistration",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "registration_uuid",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("actor_access_epoch", models.PositiveBigIntegerField()),
                (
                    "entry_mode",
                    models.CharField(
                        choices=[("SELF", "Self"), ("SUPPORTED", "Supported")],
                        max_length=9,
                    ),
                ),
                ("enterer_executor", models.CharField(max_length=120)),
                ("authority_reference", models.CharField(max_length=255)),
                ("evidence_reference", models.CharField(max_length=255)),
                ("effective_at", models.DateTimeField()),
                ("received_at", models.DateTimeField()),
                ("recorded_at", models.DateTimeField(editable=False, null=True)),
                (
                    "registration_commitment",
                    models.CharField(editable=False, max_length=71, null=True),
                ),
                (
                    "identity",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="learner_registration",
                        to="core.identity",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.AddField(
            model_name="learnerregistration",
            name="registration_receipt",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="learner_registration",
                to="core.registrationreceipt",
            ),
        ),
        migrations.AddField(
            model_name="identitytransition",
            name="effective_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="identitytransition",
            name="received_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="identitytransition",
            name="recorded_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(install_p1, remove_p1),
    ]
