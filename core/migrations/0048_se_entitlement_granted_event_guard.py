"""Enforce the PKT-SE-K3 first-GRANTED-event invariant at transaction completion.

The Human Governor adopted the rule in
HG_DECISION_PKT_SE_K2_DESIGN_ADOPTION_20261015 and the installation preflight
in HG_ISSUE_SEK34_COMMISSION_V0_2_20261018_CAPTURE. The deferred database
trigger checks every entitlement inserted after installation; the forward
preflight checks existing rows while both tables are locked. The function has
no runtime switch. Keeping the application role from disabling or removing
the guard remains K3-PROD, a separate delivery gate.
"""

from django.db import migrations


MIGRATION = "0048_se_entitlement_granted_event_guard"
PREVIOUS_MIGRATION = "0047_sek1_platform_rule_statement_start"

LOCK_SQL = (
    "LOCK TABLE public.core_organismentitlement, "
    "public.core_organismentitlementevent IN SHARE ROW EXCLUSIVE MODE"
)
PREFLIGHT_SQL = """
SELECT id, count(*) OVER ()
  FROM (
        SELECT entitlement.id
          FROM public.core_organismentitlement entitlement
         WHERE (
               SELECT count(*)
                 FROM public.core_organismentitlementevent event
                WHERE event.entitlement_id = entitlement.id
                  AND event.sequence = 1
                  AND event.event_type = 'GRANTED'
         ) <> 1
       ) violating
 ORDER BY id
 LIMIT 10
"""
FUNCTION_SQL = """
CREATE FUNCTION public.s015_guard_entitlement_granted()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF (
        SELECT count(*)
          FROM public.core_organismentitlementevent event
         WHERE event.entitlement_id = NEW.id
           AND event.sequence = 1
           AND event.event_type = 'GRANTED'
    ) <> 1 THEN
        RAISE EXCEPTION 'SE_ENTITLEMENT_WITHOUT_GRANT'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END
$$
"""
TRIGGER_SQL = """
CREATE CONSTRAINT TRIGGER s015_organismentitlement_granted_guard
AFTER INSERT ON public.core_organismentitlement
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION public.s015_guard_entitlement_granted()
"""


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(LOCK_SQL)
        cursor.execute(PREFLIGHT_SQL)
        violations = cursor.fetchall()
        if violations:
            count = violations[0][1]
            identifiers = [row[0] for row in violations]
            raise RuntimeError(
                f"{MIGRATION} forward refused: {count} entitlement rows do not "
                "have exactly one first GRANTED event; up to ten row ids: "
                f"{identifiers}"
            )
        cursor.execute(FUNCTION_SQL)
        cursor.execute(TRIGGER_SQL)


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM public.core_organismentitlement) "
            "OR EXISTS (SELECT 1 FROM public.core_organismentitlementevent) "
            "OR EXISTS (SELECT 1 FROM public.core_organismentitlementpolicytext)"
        )
        if cursor.fetchone()[0]:
            raise RuntimeError(
                f"{MIGRATION} reverse refused while SE entitlement data exists"
            )
        cursor.execute(
            "DROP TRIGGER s015_organismentitlement_granted_guard "
            "ON public.core_organismentitlement"
        )
        cursor.execute(
            "DROP FUNCTION public.s015_guard_entitlement_granted()"
        )


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", PREVIOUS_MIGRATION)]

    operations = [migrations.RunPython(forwards, backwards)]
