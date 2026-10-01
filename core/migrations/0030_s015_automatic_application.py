import importlib

from django.db import migrations, models
from django.db.models.deletion import PROTECT


def source(number, name):
    return importlib.import_module(f"core.migrations.{number}_{name}")


def replace_once(text, anchor, replacement):
    if text.count(anchor) != 1:
        raise RuntimeError(f"S015 0030 expected exactly one anchor: {anchor!r}")
    return text.replace(anchor, replacement, 1)


AUTO_ENGAGEMENT_SQL = """
CREATE FUNCTION public.s3fu_guard_auto_engagement() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE app public.core_application%ROWTYPE;
BEGIN
    PERFORM public.s3_lock_application(NEW.application_id);
    NEW.recorded_at := clock_timestamp();
    SELECT * INTO app FROM public.core_application WHERE id = NEW.application_id;
    IF NEW.engagement_ordinal <> 1 OR NEW.actor_id IS NOT NULL OR NEW.enterer_id IS NULL
       OR NEW.deciding_party_id IS DISTINCT FROM app.living_organism_id
       OR NEW.settings_basis IS DISTINCT FROM 'application-settings-version:' || (
           SELECT version_uuid::text FROM public.core_loapplicationsettingsversion WHERE id = app.settings_version_id)
       OR NEW.engagement_reference IS DISTINCT FROM NEW.settings_basis OR NEW.basis_reference IS NOT NULL
       OR EXISTS (SELECT 1 FROM public.core_applicationengagement WHERE application_id = app.id)
       OR EXISTS (SELECT 1 FROM public.core_applicationwithdrawal WHERE application_id = app.id)
       OR EXISTS (SELECT 1 FROM public.core_applicationdecision WHERE application_id = app.id)
       OR NOT EXISTS (SELECT 1 FROM public.core_loapplicationsettingsversion v
                      WHERE v.id = app.settings_version_id AND v.living_organism_id = app.living_organism_id
                        AND v.applications_open AND v.automatic_acceptance) THEN
        RAISE EXCEPTION 'S3FU automatic engagement refused: recorded open automatic setting required'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""


AUTO_DECISION_SQL = """
CREATE FUNCTION public.s3fu_guard_auto_decision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE app public.core_application%ROWTYPE; engagement public.core_applicationengagement%ROWTYPE;
BEGIN
    PERFORM public.s3_lock_application(NEW.application_id);
    NEW.recorded_at := clock_timestamp();
    SELECT * INTO app FROM public.core_application WHERE id = NEW.application_id;
    SELECT * INTO engagement FROM public.core_applicationengagement WHERE application_id = app.id
        AND effective_at <= NEW.effective_at AND recorded_at <= NEW.recorded_at
        ORDER BY engagement_ordinal DESC LIMIT 1;
    IF NEW.outcome IS DISTINCT FROM 'ACCEPT' OR NEW.deciding_party_id IS DISTINCT FROM app.living_organism_id
       OR NEW.assignment_reference_id IS NOT NULL OR NEW.supporting_result_reference IS NOT NULL
       OR NEW.responsible_coordinator_id IS NOT NULL OR NEW.responsible_capacity IS NOT NULL
       OR NEW.qualifying_office_id IS NOT NULL OR NEW.enterer_id IS DISTINCT FROM NEW.executor_id
       OR engagement.route IS DISTINCT FROM 'LO_AUTOMATIC'
       OR engagement.settings_basis IS DISTINCT FROM 'application-settings-version:' || (
           SELECT version_uuid::text FROM public.core_loapplicationsettingsversion WHERE id = app.settings_version_id)
       OR NOT EXISTS (SELECT 1 FROM public.core_loapplicationsettingsversion v
                      WHERE v.id = app.settings_version_id AND v.living_organism_id = app.living_organism_id
                        AND v.applications_open AND v.automatic_acceptance) THEN
        RAISE EXCEPTION 'S3FU automatic decision refused: recorded open setting and LO ACCEPT required'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$;
"""


MEMBERSHIP_COMPOSED = """
    IF NEW.entry_mode = 'COMPOSED' THEN
        IF NEW.prior_state IS DISTINCT FROM 'PROPOSED' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'
           OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
           OR NEW.actor_capacity <> 'STANDING' OR NEW.enterer_identity_id IS NULL
           OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
           OR NEW.composer_state <> 'REFERENCE' OR NEW.composing_rule_state <> 'REFERENCE'
           OR NEW.evidence_reference !~ '^application-decision:[0-9a-f-]{36}$'
           OR NOT EXISTS (
               SELECT 1 FROM public.core_applicationdecision d
               JOIN public.core_application app ON app.id = d.application_id
               JOIN public.core_organismmembership m ON m.id = NEW.membership_id
               JOIN public.core_loapplicationsettingsversion v ON v.id = app.settings_version_id
               WHERE NEW.evidence_reference = 'application-decision:' || d.decision_uuid::text
                 AND d.outcome = 'ACCEPT' AND d.route = 'LO_AUTOMATIC'
                 AND d.enterer_id = NEW.enterer_identity_id
                 AND app.living_organism_id = m.living_organism_id AND app.applicant_id = m.identity_id
                 AND v.living_organism_id = app.living_organism_id
                 AND v.applications_open AND v.automatic_acceptance
                 AND NEW.composing_rule_reference = 'application-settings-version:' || v.version_uuid::text
           ) THEN
            RAISE EXCEPTION 'S3FU composed LO admission refused: recorded automatic setting and CARE entry required'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
"""


LINEAGE_COMPOSED = """
    IF NEW.entry_mode = 'COMPOSED' THEN
        SELECT d.* INTO decision_row FROM public.core_applicationdecision d
        WHERE d.application_id = application_row.id
          AND NEW.evidence_reference = 'application-decision:' || d.decision_uuid::text;
        IF NEW.action <> 'DECIDE_APPLICATION' OR NEW.sequence <= 1 OR decision_row.id IS NULL
           OR decision_row.route <> 'LO_AUTOMATIC' OR decision_row.outcome <> 'ACCEPT'
           OR NEW.resulting_state <> 'ACCEPTED' OR NEW.actor_state <> 'LIVING_ORGANISM'
           OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
           OR NEW.actor_capacity <> 'STANDING' OR NEW.l2_commitment IS NOT NULL
           OR NEW.composing_rule_state <> 'REFERENCE' OR NEW.composer_state <> 'REFERENCE'
           OR NEW.enterer_identity_id IS DISTINCT FROM decision_row.enterer_id
           OR NEW.composer_identity_id IS DISTINCT FROM decision_row.enterer_id
           OR NEW.authority_decision_reference IS DISTINCT FROM decision_row.authority_decision_reference
           OR NOT EXISTS (SELECT 1 FROM public.core_loapplicationsettingsversion v
                          WHERE v.id = application_row.settings_version_id
                            AND v.living_organism_id = application_row.living_organism_id
                            AND v.applications_open AND v.automatic_acceptance
                            AND NEW.composing_rule_reference = 'application-settings-version:' || v.version_uuid::text) THEN
            RAISE EXCEPTION 'S3FU composed application acceptance refused: recorded automatic setting required'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
"""


def member_sql():
    original, = (sql for sql in source("0025", "s015_application_routes").INSTALL_SQL
                 if sql.lstrip().startswith("CREATE FUNCTION public.s3_guard_lo_actor()"))
    original = replace_once(original, "NEW.actor_capacity <> 'NA_NO_PARTY'", "NEW.actor_capacity <> 'STANDING'")
    original = replace_once(original, "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")
    original = replace_once(original,
        "    IF NEW.prior_state IS DISTINCT FROM 'PROPOSED'",
        MEMBERSHIP_COMPOSED + "    IF NEW.prior_state IS DISTINCT FROM 'PROPOSED'")
    return replace_once(original,
        "AND decision.outcome = 'ACCEPT' AND application.living_organism_id",
        "AND decision.outcome = 'ACCEPT' AND decision.route <> 'LO_AUTOMATIC' AND application.living_organism_id")


def lineage_sql():
    original = source("0028", "s015_application_lineage_chain").ACTOR_SQL
    original = replace_once(original, "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")
    original = replace_once(original,
        "    IF application_row.id IS NULL OR NEW.enterer_identity_id IS NULL",
        LINEAGE_COMPOSED + "    IF application_row.id IS NULL OR NEW.enterer_identity_id IS NULL")
    return replace_once(original, "IF decision_row.id IS NULL OR NEW.actor_state <> 'LIVING_ORGANISM'",
                        "IF decision_row.id IS NULL OR decision_row.route = 'LO_AUTOMATIC' OR NEW.actor_state <> 'LIVING_ORGANISM'")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0030 requires PostgreSQL")
    ex = lambda sql: schema_editor.execute(sql, params=None)
    ex("ALTER TABLE public.core_applicationengagement ADD CONSTRAINT s3fu_auto_engagement_actor_ck "
       "CHECK ((route = 'LO_AUTOMATIC') = (actor_id IS NULL))")
    ex(AUTO_ENGAGEMENT_SQL)
    ex(AUTO_DECISION_SQL)
    for table, old_name, function, new_name in (
        ("core_applicationengagement", "s3_10_s3_engagement_guard", "s3_engagement_guard", "s3_11_auto_engagement"),
        ("core_applicationdecision", "s3_20_s3_decision_guard", "s3_decision_guard", "s3_21_auto_decision"),
    ):
        ex(f"DROP TRIGGER {old_name} ON public.{table}")
        ex(f"CREATE TRIGGER {old_name} BEFORE INSERT ON public.{table} FOR EACH ROW "
           f"WHEN (NEW.route <> 'LO_AUTOMATIC') EXECUTE FUNCTION public.{function}()")
        auto_function = "s3fu_guard_auto_engagement" if table == "core_applicationengagement" else "s3fu_guard_auto_decision"
        ex(f"CREATE TRIGGER {new_name} BEFORE INSERT ON public.{table} FOR EACH ROW "
           f"WHEN (NEW.route = 'LO_AUTOMATIC') EXECUTE FUNCTION public.{auto_function}()")
    ex(member_sql())
    ex(lineage_sql())


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        raise RuntimeError("S015 0030 requires PostgreSQL")
    with schema_editor.connection.cursor() as cursor:
        for table in ("core_applicationengagement", "core_applicationdecision"):
            cursor.execute(f"SELECT 1 FROM public.{table} WHERE route = 'LO_AUTOMATIC' LIMIT 1")
            if cursor.fetchone():
                raise RuntimeError("S015 0030 reversal refused: LO_AUTOMATIC rows require forward correction")
        for table in ("core_organismmembershiptransition", "core_applicationlineageevent"):
            cursor.execute(f"SELECT 1 FROM public.{table} WHERE actor_state = 'LIVING_ORGANISM' "
                           "AND entry_mode = 'COMPOSED' LIMIT 1")
            if cursor.fetchone():
                raise RuntimeError("S015 0030 reversal refused: composed admission rows require forward correction")
    ex = lambda sql: schema_editor.execute(sql, params=None)
    for table, old_name, function, new_name in (
        ("core_applicationengagement", "s3_10_s3_engagement_guard", "s3_engagement_guard", "s3_11_auto_engagement"),
        ("core_applicationdecision", "s3_20_s3_decision_guard", "s3_decision_guard", "s3_21_auto_decision"),
    ):
        ex(f"DROP TRIGGER {new_name} ON public.{table}")
        ex(f"DROP TRIGGER {old_name} ON public.{table}")
        ex(f"CREATE TRIGGER {old_name} BEFORE INSERT ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.{function}()")
    ex("DROP FUNCTION public.s3fu_guard_auto_engagement()")
    ex("DROP FUNCTION public.s3fu_guard_auto_decision()")
    ex("ALTER TABLE public.core_applicationengagement DROP CONSTRAINT s3fu_auto_engagement_actor_ck")
    old_member, = (sql for sql in source("0025", "s015_application_routes").INSTALL_SQL
                   if sql.lstrip().startswith("CREATE FUNCTION public.s3_guard_lo_actor()"))
    old_member = replace_once(old_member, "NEW.actor_capacity <> 'NA_NO_PARTY'", "NEW.actor_capacity <> 'STANDING'")
    ex(replace_once(old_member, "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"))
    ex(replace_once(source("0028", "s015_application_lineage_chain").ACTOR_SQL,
                    "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"))


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("core", "0029_s015_application_gateway")]
    routes = [(route, route) for route in ("LO_CQ", "LO_CSA", "LO_AUTOMATIC", "CIRCLE_CQ", "CIRCLE_CSA")]
    operations = [
        migrations.AlterField(model_name="applicationengagement", name="route", field=models.CharField(max_length=16, choices=routes)),
        migrations.AlterField(model_name="applicationdecision", name="route", field=models.CharField(max_length=16, choices=routes)),
        migrations.AlterField(model_name="applicationengagement", name="actor", field=models.ForeignKey(
            to="core.identity", on_delete=PROTECT, related_name="engaged_organism_applications", null=True, blank=True)),
        migrations.RunPython(install, reverse),
    ]