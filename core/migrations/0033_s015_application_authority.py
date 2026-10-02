import importlib

from django.db import migrations, models


LEGACY_BASIS = "DEFAULT (no settings store): cq_active=No, application_sole_participant_delegation=No"
BASIS_PATTERN = (
    r"^governance-configuration-version:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12};application-settings-version:[0-9a-f]{8}-"
    r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def require_postgresql(schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0033 requires PostgreSQL")


def lock_cutover(apps, schema_editor):
    require_postgresql(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "LOCK TABLE public.core_applicationengagement, public.core_applicationdecision "
            "IN SHARE ROW EXCLUSIVE MODE"
        )

        def trigger_binding(table, trigger_name):
            cursor.execute(
                "SELECT p.proname, pg_get_triggerdef(t.oid), t.tgenabled, t.tgtype "
                "FROM pg_catalog.pg_trigger t "
                "JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid "
                "WHERE t.tgrelid = %s::regclass AND t.tgname = %s AND NOT t.tgisinternal",
                [f"public.{table}", trigger_name],
            )
            return cursor.fetchone()

        cursor.execute(
            "SELECT p.prosrc FROM pg_catalog.pg_proc p "
            "WHERE p.oid = 'public.s3_decision_guard()'::regprocedure"
        )
        installed_decision_body = cursor.fetchone()
        routes = importlib.import_module("core.migrations.0025_s015_application_routes")
        decision_sql, = (
            sql for sql in routes.INSTALL_SQL
            if sql.lstrip().startswith("CREATE FUNCTION public.s3_decision_guard()")
        )
        expected_decision_body = decision_sql.split("AS $$", 1)[1].rsplit("$$;", 1)[0]
        if installed_decision_body is None or installed_decision_body[0] != expected_decision_body:
            raise RuntimeError("S015 0033 preflight halt: decision guard body differs from qualified 0025")

        time_guards = importlib.import_module("core.migrations.0026_s015_application_admission_time")
        engagement_sql, = (
            sql for sql in time_guards.guard_bodies(post_lock_time=True)
            if sql.lstrip().startswith("CREATE OR REPLACE FUNCTION public.s3_engagement_guard()")
        )
        cursor.execute(
            "SELECT p.prosrc FROM pg_catalog.pg_proc p "
            "WHERE p.oid = 'public.s3_engagement_guard()'::regprocedure"
        )
        installed_engagement_body = cursor.fetchone()
        expected_engagement_body = engagement_sql.split("AS $$", 1)[1].rsplit("$$;", 1)[0]
        if installed_engagement_body is None or installed_engagement_body[0] != expected_engagement_body:
            raise RuntimeError("S015 0033 preflight halt: engagement guard body differs from qualified 0026")

        for table, name, function, operator in (
            ("core_applicationengagement", "s3_10_s3_engagement_guard", "s3_engagement_guard", "<>"),
            ("core_applicationengagement", "s3_11_auto_engagement", "s3fu_guard_auto_engagement", "="),
            ("core_applicationdecision", "s3_20_s3_decision_guard", "s3_decision_guard", "<>"),
            ("core_applicationdecision", "s3_21_auto_decision", "s3fu_guard_auto_decision", "="),
        ):
            binding = trigger_binding(table, name)
            if (
                binding is None or binding[0] != function or binding[2] not in ("O", "A")
                or "BEFORE INSERT" not in binding[1] or "WHEN" not in binding[1]
                or f"{operator} 'LO_AUTOMATIC'" not in binding[1]
            ):
                raise RuntimeError(f"S015 0033 preflight halt: {name} is not at its qualified 0030 binding")

        immutable_trigger_sql, = (
            sql for sql in routes.INSTALL_SQL
            if sql.lstrip().startswith("CREATE TRIGGER s3_row_immutable ")
            and "ON public.core_applicationengagement " in sql
        )
        immutable_trigger_name = immutable_trigger_sql.split()[2]
        immutable = trigger_binding("core_applicationengagement", immutable_trigger_name)
        immutable_event_bits = 1 | 2 | 8 | 16
        if (
            immutable is None or immutable[0] != "s015_refuse_row_mutation" or immutable[2] not in ("O", "A")
            or immutable[3] & immutable_event_bits != immutable_event_bits
        ):
            raise RuntimeError(
                f"S015 0033 preflight halt: engagement append-only guard {immutable_trigger_name} is missing or misbound"
            )


def _decision_guard_sql():
    previous = importlib.import_module("core.migrations.0025_s015_application_routes")
    source, = (
        sql for sql in previous.INSTALL_SQL
        if sql.lstrip().startswith("CREATE FUNCTION public.s3_decision_guard()")
    )
    original_name = "CREATE FUNCTION public.s3_decision_guard()"
    if source.count(original_name) != 1:
        raise RuntimeError("S015 0033 expected exactly one base decision guard definition")
    sql = source.replace(original_name, "CREATE FUNCTION public.s3_decision_guard_a26()", 1)
    old_basis_test = (
        "         OR engagement.settings_basis <> '" + LEGACY_BASIS + "'\n"
        "         OR NEW.assignment_reference_id IS NULL"
    )
    new_basis_test = (
        "         OR NOT public.s3fu_application_settings_basis_valid("
        "engagement.settings_basis, engagement.deciding_party_id, engagement.effective_at, engagement.recorded_at)\n"
        "         OR NEW.assignment_reference_id IS NULL"
    )
    if sql.count(old_basis_test) != 1:
        raise RuntimeError("S015 0033 expected exactly one legacy-basis test in the qualified decision guard")
    return sql.replace(old_basis_test, new_basis_test, 1)


SETTINGS_BASIS_VALID_SQL = """
CREATE FUNCTION public.s3fu_application_settings_basis_valid(
    p_basis text, p_living_organism_id bigint, p_effective_at timestamptz, p_known_at timestamptz
) RETURNS boolean LANGUAGE plpgsql STABLE AS $$
DECLARE configuration_uuid uuid; settings_uuid uuid; current_reference text;
BEGIN
    IF p_basis = 'DEFAULT (no settings store): cq_active=No, application_sole_participant_delegation=No' THEN
        RETURN TRUE;
    END IF;
    IF p_basis IS NULL OR p_basis !~ '^governance-configuration-version:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12};application-settings-version:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        RETURN FALSE;
    END IF;
    configuration_uuid := substring(p_basis from 34 for 36)::uuid;
    settings_uuid := substring(p_basis from position(';application-settings-version:' in p_basis) + 30 for 36)::uuid;
    IF NOT EXISTS (
        SELECT 1 FROM public.core_logovernanceconfigurationversion v
        WHERE v.version_uuid = configuration_uuid AND v.living_organism_id = p_living_organism_id
    ) OR NOT EXISTS (
        SELECT 1 FROM public.core_loapplicationsettingsversion v
        WHERE v.version_uuid = settings_uuid AND v.living_organism_id = p_living_organism_id
    ) THEN
        RETURN FALSE;
    END IF;
    SELECT e.evidence_reference INTO current_reference
    FROM public.core_livingorganismevent e
    JOIN public.core_logovernanceconfigurationversion v
      ON e.evidence_reference = 'governance-configuration-version:' || v.version_uuid::text
    WHERE e.living_organism_id = p_living_organism_id
      AND e.action = 'EFFECT_GOVERNANCE_CONFIGURATION'
      AND e.effective_at <= p_effective_at AND e.received_at <= p_known_at AND e.recorded_at <= p_known_at
    ORDER BY e.effective_at DESC, e.occurred_at DESC, e.received_at DESC,
             e.recorded_at DESC, e.sequence DESC, e.event_uuid DESC
    LIMIT 1;
    IF current_reference IS DISTINCT FROM 'governance-configuration-version:' || configuration_uuid::text THEN
        RETURN FALSE;
    END IF;
    SELECT e.evidence_reference INTO current_reference
    FROM public.core_livingorganismevent e
    JOIN public.core_loapplicationsettingsversion v
      ON e.evidence_reference = 'application-settings-version:' || v.version_uuid::text
    WHERE e.living_organism_id = p_living_organism_id
      AND e.action = 'EFFECT_APPLICATION_SETTINGS'
      AND e.effective_at <= p_effective_at AND e.received_at <= p_known_at AND e.recorded_at <= p_known_at
    ORDER BY e.effective_at DESC, e.occurred_at DESC, e.received_at DESC,
             e.recorded_at DESC, e.sequence DESC, e.event_uuid DESC
    LIMIT 1;
    RETURN current_reference IS NOT DISTINCT FROM 'application-settings-version:' || settings_uuid::text;
END $$;
"""


ENGAGEMENT_BASIS_GUARD_SQL = """
CREATE FUNCTION public.s3fu_guard_application_engagement_basis() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.settings_basis = 'DEFAULT (no settings store): cq_active=No, application_sole_participant_delegation=No'
           OR (NEW.route <> 'LO_AUTOMATIC' AND NOT public.s3fu_application_settings_basis_valid(
               NEW.settings_basis, NEW.deciding_party_id, NEW.effective_at, NEW.recorded_at
           )) THEN
            RAISE EXCEPTION 'S3FU engagement settings basis refused' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NEW.settings_basis = 'DEFAULT (no settings store): cq_active=No, application_sole_participant_delegation=No'
          AND OLD.settings_basis IS DISTINCT FROM NEW.settings_basis THEN
        RAISE EXCEPTION 'S3FU engagement legacy settings basis cannot be introduced' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""


def install(apps, schema_editor):
    require_postgresql(schema_editor)
    ex = lambda sql: schema_editor.execute(sql, params=None)
    ex(SETTINGS_BASIS_VALID_SQL)
    ex(ENGAGEMENT_BASIS_GUARD_SQL)
    ex(_decision_guard_sql())
    ex(
        "CREATE TRIGGER s3_05_engagement_settings_basis_update BEFORE UPDATE ON "
        "public.core_applicationengagement FOR EACH ROW "
        "EXECUTE FUNCTION public.s3fu_guard_application_engagement_basis()"
    )
    ex(
        "CREATE TRIGGER s3_12_engagement_settings_basis_insert BEFORE INSERT ON "
        "public.core_applicationengagement FOR EACH ROW "
        "EXECUTE FUNCTION public.s3fu_guard_application_engagement_basis()"
    )
    ex("DROP TRIGGER s3_20_s3_decision_guard ON public.core_applicationdecision")
    ex(
        "CREATE TRIGGER s3_20_s3_decision_guard BEFORE INSERT ON public.core_applicationdecision "
        "FOR EACH ROW WHEN (NEW.route <> 'LO_AUTOMATIC') "
        "EXECUTE FUNCTION public.s3_decision_guard_a26()"
    )


def reverse(apps, schema_editor):
    require_postgresql(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "LOCK TABLE public.core_applicationengagement, public.core_applicationdecision "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
        cursor.execute(
            "SELECT count(*) FROM public.core_applicationengagement "
            "WHERE route <> 'LO_AUTOMATIC' AND settings_basis ~ %s",
            [BASIS_PATTERN],
        )
        if cursor.fetchone()[0]:
            raise RuntimeError("S015 0033 reversal refused: non-automatic versioned engagements require the A2.6 guard")
        cursor.execute(
            "SELECT count(*) FROM public.core_applicationdecision WHERE authority_reference IS NOT NULL"
        )
        if cursor.fetchone()[0]:
            raise RuntimeError("S015 0033 reversal refused: application decision authority evidence would be lost")
    ex = lambda sql: schema_editor.execute(sql, params=None)
    ex("DROP TRIGGER s3_05_engagement_settings_basis_update ON public.core_applicationengagement")
    ex("DROP TRIGGER s3_12_engagement_settings_basis_insert ON public.core_applicationengagement")
    ex("DROP TRIGGER s3_20_s3_decision_guard ON public.core_applicationdecision")
    ex(
        "CREATE TRIGGER s3_20_s3_decision_guard BEFORE INSERT ON public.core_applicationdecision "
        "FOR EACH ROW WHEN (NEW.route <> 'LO_AUTOMATIC') EXECUTE FUNCTION public.s3_decision_guard()"
    )
    ex("DROP FUNCTION public.s3_decision_guard_a26()")
    ex("DROP FUNCTION public.s3fu_guard_application_engagement_basis()")
    ex("DROP FUNCTION public.s3fu_application_settings_basis_valid(text, bigint, timestamptz, timestamptz)")


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("core", "0032_s015_register_cleanup_nonblocking")]
    operations = [
        migrations.RunPython(lock_cutover, migrations.RunPython.noop),
        migrations.AddField(
            model_name="applicationdecision",
            name="authority_reference",
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
        migrations.RunPython(install, reverse),
    ]