"""Let the opening author prepare a Suggestion on the authority of membership (AZ2, BR1)."""

from hashlib import sha256
from importlib import import_module

from django.db import migrations

from core.migrations._clock_conversion import swap


SIGNATURE = "public.cq_source(bigint,uuid,uuid,text,bigint,timestamptz)"
OFFICE_CHECK = " IF p_op IN ('SETTINGS','PREPARED','EXTEND','CLOSE','PROCEED','OPEN_VOTE') THEN"
SUGGESTION_CHECK = """ IF p_op='PREPARED' AND EXISTS (
   SELECT 1 FROM public.discp_threads t
   JOIN public.core_cqact i ON i.thread_uuid=t.thread_uuid
     AND i.board_uuid=t.board_uuid AND i.organism_id=p_lo AND i.operation='INITIATE'
   WHERE t.thread_uuid=p_thread AND t.board_uuid=p_board AND t.topic_type='SUGGESTION'
     AND COALESCE(i.request->'attachment'->>'kind','') NOT IN ('ELECTION','COMMITTEE')
 ) THEN
   IF v_first.author_id=p_person THEN
     RETURN jsonb_build_object('kind','MEMBER','reference',v_caller,'route',p_op);
   END IF;
   IF v_office IS NOT NULL AND EXISTS (
     SELECT 1 FROM public.core_circle c WHERE c.id=v_board.circle_id
       AND c.parent_organism_id=p_lo AND c.kind='GOVERNANCE'
   ) THEN RETURN v_office; END IF;
   PERFORM public.cq_refuse('CQ_PREPARE_SUGGESTION');
 END IF;
"""
PRIOR_PIN = (4582, "f92eef4fe708e9f97984aee177b13f0cb576a3432d85b1e6a971fe8acf3cfe5b")
REVISED_PIN = (5381, "2dcb881609dd82fe12e3efcbb0de86e0232f410e247ef18b1a11e4c0b9e9e4bc")


def qualified_bodies():
    """Read the last definition of this signature, preserving PostgreSQL's body whitespace."""
    committees = import_module("core.migrations.0084_committee_mandates")
    original, = (committees.hard.body(ddl) for _, _, ddl in committees.edits()
                 if committees.sig(ddl) == SIGNATURE)
    if original.count(OFFICE_CHECK) != 1:
        raise RuntimeError("Expected exactly one preparation office check in the predecessor.")
    revised = original.replace(OFFICE_CHECK, SUGGESTION_CHECK + OFFICE_CHECK)
    for body, expected in ((original, PRIOR_PIN), (revised, REVISED_PIN)):
        data = body.encode("utf-8")
        if (len(data), sha256(data).hexdigest()) != expected:
            raise RuntimeError("Unexpected preparation authority body. Migration 0115 stopped.")
    return original, revised


def install(apps, schema_editor):
    original, revised = qualified_bodies()
    with schema_editor.connection.cursor() as cursor:
        swap(cursor, SIGNATURE, original, revised)


def uninstall(apps, schema_editor):
    original, revised = qualified_bodies()
    with schema_editor.connection.cursor() as cursor:
        swap(cursor, SIGNATURE, revised, original)


class Migration(migrations.Migration):
    atomic = True
    dependencies = (("core", "0114_alpha_treasurer_board_clause"),)
    operations = (migrations.RunPython(install, uninstall),)
