"""Admit the existing institutional Circle creation envelope to the shared writer.

This change covers dormant creation. It does not alter a lifecycle predicate.
"""
from importlib import import_module

from django.db import migrations


shared = import_module("core.migrations.0057_shared_event_append")
SIGNATURE = f"public.s015_append_event({shared.SIGNATURE},uuid,bigint,integer,bigint,text,text)"
HEADER, original_sql = shared.SQL.strip().split(" AS $$", 1)
ORIGINAL = original_sql.removesuffix("$$;")
# The existing command and trigger branches retain their seals, actor shapes
# and lineage. A Circle creation is a transported LO act, entered by a human.
CIRCLE_COMMAND = """
 IF token = 'core_circle' THEN
    IF action_name IS DISTINCT FROM 'CREATE_DORMANT' OR outcome IS DISTINCT FROM 'DORMANT'
       OR supplied_head IS NOT NULL OR supplied_sequence IS NOT NULL OR command_rule IS NOT NULL
       OR access_epoch IS NOT NULL OR deadline IS NOT NULL OR actor IS NULL THEN
       RAISE EXCEPTION 'Invalid institutional Circle creation envelope';
    END IF;
    SELECT encode(sha256(convert_to(format(
      '{"action":"CREATE_DORMANT_CIRCLE","event_uuid":"%s","fingerprint":"%s","subject_uuid":"%s"}',
      event,payload,c.circle_uuid),'UTF8')),'hex') INTO STRICT lineage
    FROM public.core_circle c WHERE c.id=anchor AND c.kind='GOVERNANCE';
 END IF;
"""
CIRCLE_PARTY = """
 IF token = 'core_circle' THEN
    values_ := values_ || jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL);
 END IF;
"""


def new_body():
    body = ORIGINAL
    for before, after in (
        ("(is_command AND token IN ('core_livingorganism'", "(is_command AND token IN ('core_circle','core_livingorganism'"),
        (" values_:=jsonb_build_object", CIRCLE_COMMAND + " values_:=jsonb_build_object"),
        (" SELECT string_agg", CIRCLE_PARTY + " SELECT string_agg"),
    ):
        if body.count(before) != 1:
            raise RuntimeError("The qualified shared writer source has changed.")
        body = body.replace(before, after)
    return body


def switch(editor, *, reverse):
    extended = new_body()
    expected, replacement = (extended, ORIGINAL) if reverse else (ORIGINAL, extended)
    with editor.connection.cursor() as cursor:
        if reverse:
            # Old code cannot reconstruct a purpose-bound intake from the
            # earlier command shape. Refuse rollback across such history.
            cursor.execute("""SELECT EXISTS (SELECT FROM public.core_circlestateevent
                WHERE action='CREATE_DORMANT' AND evidence_reference LIKE 'circle-purpose:%')""")
            if cursor.fetchone()[0]:
                raise RuntimeError("A purpose-bound Circle creation is recorded. Keep 0058 installed.")
        cursor.execute("SELECT prosrc FROM pg_proc WHERE oid=%s::regprocedure", [SIGNATURE])
        if cursor.fetchone() != (expected,):
            raise RuntimeError("The installed shared append body differs. No function was replaced.")
    editor.execute(HEADER.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
                   + " AS $$" + replacement + "$$;", params=None)


def install(apps, editor):
    switch(editor, reverse=False)


def uninstall(apps, editor):
    switch(editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [("core", "0057_shared_event_append")]
    operations = [migrations.RunPython(install, uninstall)]
