import importlib
import uuid

from django.db import migrations, models
from django.db.models.deletion import PROTECT


SPECS = (
    ("core_loapplicationsettingsversion", "EFFECT_APPLICATION_SETTINGS", "application-settings-version:"),
    ("core_loapplicationquestionversion", "EFFECT_APPLICATION_QUESTION", "application-question-version:"),
    ("core_logovernanceconfigurationversion", "EFFECT_GOVERNANCE_CONFIGURATION", "governance-configuration-version:"),
)


def replace_once(source, anchor, replacement):
    if source.count(anchor) != 1:
        raise RuntimeError(f"S015 0029 derivation anchor occurs {source.count(anchor)} times: {anchor!r}")
    return source.replace(anchor, replacement, 1)


def capacity_sql(previous):
    sql = replace_once(previous, "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")
    sql = replace_once(sql,
        "    IF TG_TABLE_NAME = 'core_applicationlineageevent' THEN",
        "    IF TG_TABLE_NAME = 'core_livingorganismevent' THEN\n"
        "        lo_id := NEW.living_organism_id;\n"
        "    ELSIF TG_TABLE_NAME = 'core_applicationlineageevent' THEN",
    )
    sql = replace_once(sql,
        "       ) OR EXISTS (\n          SELECT 1 FROM public.core_livingorganismevent closed",
        "       ) THEN\n"
        "        RAISE EXCEPTION 'S3FU LO capacity refused: no founding record' USING ERRCODE = 'integrity_constraint_violation';\n"
        "    END IF;\n"
        "    IF EXISTS (\n          SELECT 1 FROM public.core_livingorganismevent closed",
    )
    sql = replace_once(sql, "closed.effective_at <= NEW.effective_at", "closed.effective_at < NEW.effective_at")
    return replace_once(sql, "RAISE EXCEPTION 'S3FU LO capacity refused'", "RAISE EXCEPTION 'S3FU LO capacity refused: dissolved'")


def permission_sql(previous):
    sql = replace_once(previous, "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")
    return replace_once(sql,
        "(TG_TABLE_NAME = 'core_applicationlineageevent' AND NEW.resulting_state <> 'ACCEPTED')",
        "(TG_TABLE_NAME = 'core_applicationlineageevent' AND NEW.resulting_state <> 'ACCEPTED') OR\n"
        "       (TG_TABLE_NAME = 'core_organismmembershiptransition' AND\n"
        "        (NEW.prior_state IS DISTINCT FROM 'PROPOSED' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'))",
    )


EFFECT_SQL = """
CREATE FUNCTION public.s3fu_guard_gateway_effect() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE version_row jsonb; version_id bigint; previous_id bigint; version_table text; prefix text;
        options jsonb; option_count integer;
BEGIN
    IF NEW.action NOT IN ('EFFECT_APPLICATION_SETTINGS','EFFECT_APPLICATION_QUESTION','EFFECT_GOVERNANCE_CONFIGURATION') THEN
        IF NEW.prior_state IS NOT DISTINCT FROM NEW.resulting_state THEN
            RAISE EXCEPTION 'S3FU gateway effect: same-state event without effect action' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.prior_state IS DISTINCT FROM NEW.resulting_state OR NEW.sequence = 1
       OR NEW.actor_state <> 'LIVING_ORGANISM' OR NEW.actor_id IS NOT NULL
       OR NEW.actor_access_epoch IS NOT NULL OR NEW.actor_capacity <> 'STANDING'
       OR NEW.entry_mode <> 'TRANSPORT' OR NEW.enterer_identity_id IS NULL
       OR NEW.composing_rule_state <> 'NA_TRANSPORT' OR NEW.composer_state <> 'NA_TRANSPORT' THEN
        RAISE EXCEPTION 'S3FU gateway effect: same-state LO transport required' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    version_table := CASE NEW.action
        WHEN 'EFFECT_APPLICATION_SETTINGS' THEN 'core_loapplicationsettingsversion'
        WHEN 'EFFECT_APPLICATION_QUESTION' THEN 'core_loapplicationquestionversion'
        ELSE 'core_logovernanceconfigurationversion' END;
    prefix := CASE NEW.action
        WHEN 'EFFECT_APPLICATION_SETTINGS' THEN 'application-settings-version:'
        WHEN 'EFFECT_APPLICATION_QUESTION' THEN 'application-question-version:'
        ELSE 'governance-configuration-version:' END;
    IF NEW.evidence_reference !~ ('^' || prefix || '[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$') THEN
        RAISE EXCEPTION 'S3FU gateway effect: evidence reference malformed' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    EXECUTE format('SELECT id, to_jsonb(v) FROM public.%I v WHERE version_uuid = $1::uuid', version_table)
        INTO version_id, version_row USING substring(NEW.evidence_reference from length(prefix) + 1);
    IF version_id IS NULL THEN
        RAISE EXCEPTION 'S3FU gateway effect: version not found' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (version_row->>'living_organism_id')::bigint IS DISTINCT FROM NEW.living_organism_id THEN
        RAISE EXCEPTION 'S3FU gateway effect: version of another organism' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action = 'EFFECT_APPLICATION_SETTINGS' THEN
        IF version_row->>'applications_open' IS NULL OR version_row->>'automatic_acceptance' IS NULL
           OR version_row->>'processing_responsibility' IS NULL
           OR version_row->>'processing_responsibility' NOT IN ('LO','APPLICATION_CIRCLE') THEN
            RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NEW.action = 'EFFECT_APPLICATION_QUESTION' THEN
        options := version_row->'options';
        option_count := CASE WHEN jsonb_typeof(options) = 'array' THEN jsonb_array_length(options) ELSE 0 END;
        IF (version_row->>'question_text') LIKE '%[LO name]%' THEN
            RAISE EXCEPTION 'S3FU gateway effect: unresolved LO name placeholder' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
          IF nullif(btrim(version_row->>'question_text'), '') IS NULL
              OR version_row->>'answer_format' IS NULL
           OR version_row->>'answer_format' NOT IN ('SHORT_ANSWER','LONG_ANSWER','YES_NO','MULTIPLE_CHOICE','SCALE_COMPLEX')
           OR (version_row->>'answer_format' IN ('MULTIPLE_CHOICE','SCALE_COMPLEX') AND (option_count < 2 OR option_count > 7))
           OR (version_row->>'answer_format' NOT IN ('MULTIPLE_CHOICE','SCALE_COMPLEX') AND option_count <> 0) THEN
            RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF version_row->>'cq_active' IS NULL OR version_row->>'inherit_portal_cq_settings' IS NULL
          OR version_row->>'require_cq_to_change_lo_settings' IS NULL
          OR version_row->>'require_cq_to_change_application_question' IS NULL THEN
        RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    EXECUTE format('SELECT 1 FROM public.core_livingorganismevent e JOIN public.%I v ON e.evidence_reference = $1 || v.version_uuid::text WHERE v.id = $2 LIMIT 1', version_table)
        INTO previous_id USING prefix, version_id;
    IF previous_id IS NOT NULL THEN
        RAISE EXCEPTION 'S3FU gateway effect: already effective' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    previous_id := NULL;
    EXECUTE format('SELECT v.id FROM public.core_livingorganismevent e JOIN public.%I v ON e.evidence_reference = $1 || v.version_uuid::text WHERE e.living_organism_id = $2 AND e.action = $3 AND e.effective_at <= $4 AND e.received_at <= $5 AND e.recorded_at <= $5 ORDER BY e.effective_at DESC, e.occurred_at DESC, e.received_at DESC, e.recorded_at DESC, e.sequence DESC, e.event_uuid DESC LIMIT 1', version_table)
        INTO previous_id USING prefix, NEW.living_organism_id, NEW.action, NEW.effective_at, NEW.recorded_at;
    IF (version_row->>'supersedes_id')::bigint IS DISTINCT FROM previous_id THEN
        RAISE EXCEPTION 'S3FU gateway effect: continuity' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""


VERSION_GUARD_SQL = """
CREATE FUNCTION public.s3fu_guard_gateway_version() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prefix text; affected uuid;
BEGIN
    prefix := CASE TG_TABLE_NAME
        WHEN 'core_loapplicationsettingsversion' THEN 'application-settings-version:'
        WHEN 'core_loapplicationquestionversion' THEN 'application-question-version:'
        ELSE 'governance-configuration-version:' END;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.version_uuid IS DISTINCT FROM OLD.version_uuid OR NEW.living_organism_id IS DISTINCT FROM OLD.living_organism_id
           OR NEW.supersedes_id IS DISTINCT FROM OLD.supersedes_id OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
           OR NEW.enterer_id IS DISTINCT FROM OLD.enterer_id OR NEW.recorded_at IS DISTINCT FROM OLD.recorded_at THEN
            RAISE EXCEPTION 'S3FU gateway version identity immutable' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    affected := OLD.version_uuid;
    IF EXISTS (SELECT 1 FROM public.core_livingorganismevent e WHERE e.evidence_reference = prefix || affected::text) THEN
        RAISE EXCEPTION 'S3FU gateway version effective and immutable' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0029 requires PostgreSQL")
    ex = lambda sql: schema_editor.execute(sql, params=None)
    ex(EFFECT_SQL)
    ex(VERSION_GUARD_SQL)
    for table, _, _ in SPECS:
        token = table.removeprefix("core_")
        ex(f"CREATE TRIGGER s3fu_{token}_record_time BEFORE INSERT ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.s3_assign_record_time()")
        for operation in ("UPDATE", "DELETE"):
            ex(f"CREATE TRIGGER s3fu_{token}_{operation.lower()} BEFORE {operation} ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.s3fu_guard_gateway_version()")
    ex("CREATE TRIGGER s3fu_gateway_update BEFORE UPDATE ON public.core_loapplicationgateway FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()")
    ex("CREATE TRIGGER s3fu_gateway_delete BEFORE DELETE ON public.core_loapplicationgateway FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation()")
    ex("ALTER TABLE public.core_livingorganismevent DROP CONSTRAINT s015_0022_livingorganismevent_actor_state_vocab_ck")
    ex("ALTER TABLE public.core_livingorganismevent ADD CONSTRAINT s015_0022_livingorganismevent_actor_state_vocab_ck CHECK (actor_state IN ('PARTY','NONE','NOT_ESTABLISHED','LIVING_ORGANISM'))")
    ex("ALTER TABLE public.core_livingorganismevent DROP CONSTRAINT s015_0022_livingorganismevent_actor_capacity_state_ck")
    ex("ALTER TABLE public.core_livingorganismevent ADD CONSTRAINT s015_0022_livingorganismevent_actor_capacity_state_ck CHECK ((actor_state IN ('PARTY','LIVING_ORGANISM')) = (actor_capacity = 'STANDING'))")
    previous = importlib.import_module("core.migrations.0028_s015_application_lineage_chain")
    ex(capacity_sql(previous.CAPACITY_SQL))
    ex(permission_sql(previous.PERMISSION_SQL))
    ex("CREATE TRIGGER s3fu_00_capacity BEFORE INSERT ON public.core_livingorganismevent FOR EACH ROW EXECUTE FUNCTION public.s3fu_guard_lo_capacity()")
    ex("CREATE TRIGGER s3fu_10_gateway_effect BEFORE INSERT ON public.core_livingorganismevent FOR EACH ROW EXECUTE FUNCTION public.s3fu_guard_gateway_effect()")


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0029 requires PostgreSQL")
    ex = lambda sql: schema_editor.execute(sql, params=None)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM public.core_application")
        if cursor.fetchone()[0]:
            raise RuntimeError("S015 0029 reversal refused: applications need forward correction")
        for table, _, _ in SPECS:
            cursor.execute(f"SELECT count(*) FROM public.{table}")
            if cursor.fetchone()[0]:
                raise RuntimeError("S015 0029 reversal refused: versions need forward correction")
    ex("DROP TRIGGER s3fu_10_gateway_effect ON public.core_livingorganismevent")
    ex("DROP TRIGGER s3fu_00_capacity ON public.core_livingorganismevent")
    ex("ALTER TABLE public.core_livingorganismevent DROP CONSTRAINT s015_0022_livingorganismevent_actor_state_vocab_ck")
    ex("ALTER TABLE public.core_livingorganismevent ADD CONSTRAINT s015_0022_livingorganismevent_actor_state_vocab_ck CHECK (actor_state IN ('PARTY','NONE','NOT_ESTABLISHED'))")
    ex("ALTER TABLE public.core_livingorganismevent DROP CONSTRAINT s015_0022_livingorganismevent_actor_capacity_state_ck")
    ex("ALTER TABLE public.core_livingorganismevent ADD CONSTRAINT s015_0022_livingorganismevent_actor_capacity_state_ck CHECK ((actor_state = 'PARTY') = (actor_capacity = 'STANDING'))")
    previous = importlib.import_module("core.migrations.0028_s015_application_lineage_chain")
    ex(replace_once(previous.CAPACITY_SQL, "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"))
    ex(replace_once(previous.PERMISSION_SQL, "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"))
    for table, _, _ in SPECS:
        token = table.removeprefix("core_")
        for operation in ("INSERT", "UPDATE", "DELETE"):
            name = "record_time" if operation == "INSERT" else operation.lower()
            ex(f"DROP TRIGGER s3fu_{token}_{name} ON public.{table}")
    for operation in ("update", "delete"):
        ex(f"DROP TRIGGER s3fu_gateway_{operation} ON public.core_loapplicationgateway")
    ex("DROP FUNCTION public.s3fu_guard_gateway_effect()")
    ex("DROP FUNCTION public.s3fu_guard_gateway_version()")


def version_fields(extra, supersedes_target):
    return [
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("version_uuid", models.UUIDField(default=uuid.uuid4, unique=True, editable=False)),
        ("living_organism", models.ForeignKey(to="core.livingorganism", on_delete=PROTECT, related_name="+")),
        ("supersedes", models.ForeignKey(to=supersedes_target, on_delete=PROTECT, related_name="+", null=True, blank=True)),
        ("actor", models.ForeignKey(to="core.identity", on_delete=PROTECT, related_name="+")),
        ("enterer", models.ForeignKey(to="core.identity", on_delete=PROTECT, related_name="+")),
        ("occurred_at", models.DateTimeField()),
        ("effective_at", models.DateTimeField()),
        ("received_at", models.DateTimeField()),
        ("recorded_at", models.DateTimeField(null=True, editable=False)),
        *extra,
    ]


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("core", "0028_s015_application_lineage_chain")]
    operations = [
        migrations.CreateModel(name="LOApplicationGateway", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("living_organism", models.OneToOneField(to="core.livingorganism", on_delete=PROTECT, related_name="application_gateway")),
        ]),
        migrations.CreateModel(name="LOApplicationSettingsVersion", fields=version_fields([
            ("applications_open", models.BooleanField(null=True, blank=True)),
            ("automatic_acceptance", models.BooleanField(null=True, blank=True)),
            ("processing_responsibility", models.CharField(max_length=18, null=True, blank=True)),
        ], "core.loapplicationsettingsversion")),
        migrations.CreateModel(name="LOApplicationQuestionVersion", fields=version_fields([
            ("question_text", models.CharField(max_length=255)),
            ("answer_format", models.CharField(max_length=18, null=True, blank=True)),
            ("options", models.JSONField(null=True, blank=True)),
        ], "core.loapplicationquestionversion")),
        migrations.CreateModel(name="LOGovernanceConfigurationVersion", fields=version_fields([
            ("cq_active", models.BooleanField(null=True, blank=True)),
            ("inherit_portal_cq_settings", models.BooleanField(null=True, blank=True)),
            ("require_cq_to_change_lo_settings", models.BooleanField(null=True, blank=True)),
            ("require_cq_to_change_application_question", models.BooleanField(null=True, blank=True)),
        ], "core.logovernanceconfigurationversion")),
        migrations.RunPython(lambda apps, editor: _preflight(apps, editor), migrations.RunPython.noop),
        migrations.AddField(model_name="application", name="settings_version", field=models.ForeignKey(to="core.loapplicationsettingsversion", on_delete=PROTECT, related_name="applications")),
        migrations.AddField(model_name="application", name="question_version", field=models.ForeignKey(to="core.loapplicationquestionversion", on_delete=PROTECT, related_name="applications")),
        migrations.AddField(model_name="application", name="answer", field=models.TextField()),
        migrations.RunPython(install, reverse),
    ]


def _preflight(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0029 requires PostgreSQL")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("LOCK TABLE public.core_application IN SHARE ROW EXCLUSIVE MODE")
        cursor.execute("SELECT count(*) FROM public.core_application")
        if cursor.fetchone()[0]:
            raise RuntimeError("S015 0029 preflight halt: existing applications require authorised backfill")