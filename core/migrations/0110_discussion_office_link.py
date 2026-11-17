"""Pin the Board office definition chosen when an ordinary Circle Topic opens.

Reverse only when the database contains no linked Topic. Roll back the dependent
application code first, then reverse this migration. The office argument on
start_topic and the reads in read_thread and topics_about_office depend on 0110.
Removing its column while those reads are deployed breaks even unlinked Topic reads.
"""

from importlib import import_module

from django.db import migrations

from core.migrations._clock_conversion import swap

posts = import_module("core.migrations.0100_clock_storage_0073_0080")
replace_once = import_module("core.migrations.0107_committee_circle_dormancy").replace_once

SCHEMA = """
ALTER TABLE public.core_discthread ADD COLUMN office_definition_uuid uuid
    CONSTRAINT discp_thread_office_fk REFERENCES public.core_organismroledefinition(role_uuid)
    ON UPDATE NO ACTION ON DELETE NO ACTION;
CREATE INDEX discp_thread_office_idx ON public.core_discthread (office_definition_uuid)
    WHERE office_definition_uuid IS NOT NULL;
"""

OLD_INSERT = """INSERT INTO public.core_discthread VALUES(v_thread,(p_payload->>'category')::uuid,v_key,p_payload->>'type',p_payload->>'title','ORDINARY');"""
NEW_INSERT = """INSERT INTO public.core_discthread
      (thread_uuid, category_uuid, creation_act, topic_type, title, record_kind, office_definition_uuid)
   VALUES (v_thread,(p_payload->>'category')::uuid,v_key,p_payload->>'type',p_payload->>'title',
           'ORDINARY',(p_payload->>'office_definition_uuid')::uuid);"""

EXECUTE = "public.discp_execute(jsonb,text,bigint,bigint,timestamptz)"
INPUT = "public.discp_input()"


# Run on the new act under its owner lock. The executor has already compared
# any replay, and both the raw-act and executor paths have qualified authority.
VALIDATE_POST = "   PERFORM public.discp_validate(NEW.payload,NEW.actor_id,v_stamp);"
VALIDATE_OFFICE = """
   IF NEW.payload ? 'office_definition_uuid' THEN
     IF NEW.operation = 'START' THEN
       IF v_board.circle_id IS NULL OR NEW.payload->>'type' IS DISTINCT FROM 'TOPIC' THEN
         RAISE EXCEPTION 'DISC_POST_INPUT' USING ERRCODE='23514';
       END IF;
     ELSE
       RAISE EXCEPTION 'DISC_POST_INPUT' USING ERRCODE='23514';
     END IF;
     IF jsonb_typeof(NEW.payload->'office_definition_uuid') IS DISTINCT FROM 'string'
        OR (NEW.payload->>'office_definition_uuid') COLLATE "C" !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
       RAISE EXCEPTION 'DISC_POST_INPUT' USING ERRCODE='23514';
     END IF;
     IF NOT EXISTS (
       SELECT FROM public.core_organismroledefinition d
       WHERE d.role_uuid=(NEW.payload->>'office_definition_uuid')::uuid
         AND d.living_organism_id=v_board.organism_id
         AND d.scope='LIVING_ORGANISM' AND d.kind='BOARD_OFFICE' AND d.active
         AND d.effective_at<=NEW.received_at AND d.received_at<=NEW.received_at
         AND d.recorded_at<=NEW.received_at
     ) THEN
       RAISE EXCEPTION 'DISC_OFFICE_SELECTION' USING ERRCODE='23514';
     END IF;
   END IF;"""
HEADER_KIND = "OR NEW.record_kind<>'ORDINARY' THEN"
HEADER_LINK = "OR NEW.office_definition_uuid::text IS DISTINCT FROM v_act.payload->>'office_definition_uuid'"
NEW_INPUT = replace_once(posts.DISCP_INPUT_CLOCK, VALIDATE_POST, VALIDATE_POST + VALIDATE_OFFICE)
NEW_INPUT = replace_once(NEW_INPUT, HEADER_KIND, HEADER_LINK + "\n     " + HEADER_KIND)

REPLACEMENTS = (
    (EXECUTE, posts.DISCP_EXECUTE_CLOCK, replace_once(posts.DISCP_EXECUTE_CLOCK, OLD_INSERT, NEW_INSERT)),
    (INPUT, posts.DISCP_INPUT_CLOCK, NEW_INPUT),
)


def install(apps, schema_editor):
    """Add the nullable office pin and its index, then install the guarded START and input functions."""
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(SCHEMA)
        for signature, prior, successor in REPLACEMENTS:
            swap(cursor, signature, prior, successor)


def uninstall(apps, schema_editor):
    """After rolling back dependent code, restore the prior schema only if no Topic records an office link."""
    with schema_editor.connection.cursor() as cursor:
        # Close the race with a concurrent linked START before testing for history.
        cursor.execute("LOCK TABLE public.core_discthread IN ACCESS EXCLUSIVE MODE")
        cursor.execute("SELECT EXISTS (SELECT FROM public.core_discthread WHERE office_definition_uuid IS NOT NULL)")
        if cursor.fetchone()[0]:
            raise RuntimeError("0110 keeps the Board office chosen for a recorded Topic. Leave 0110 applied to preserve that link.")
        for signature, prior, successor in reversed(REPLACEMENTS):
            swap(cursor, signature, successor, prior)
        cursor.execute("DROP INDEX public.discp_thread_office_idx")
        cursor.execute("ALTER TABLE public.core_discthread DROP COLUMN office_definition_uuid")


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("core", "0109_committee_chair_reservation")]
    operations = [migrations.RunPython(install, uninstall)]
