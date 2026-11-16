"""Reserve the chair of a current committee without granting it any power to act."""
from importlib import import_module

from django.db import migrations

committee = import_module("core.migrations.0107_committee_circle_dormancy")
reseat = import_module("core.migrations.0108_circle_reseat")
once = committee.replace_once
definition = committee.definition

RESERVED = "public.cmt_chair_reserved(uuid,timestamptz,timestamptz)"
RESERVED_SQL = once(
    once(committee.LIVE_NEW, "FUNCTION public.cmt_live(", "FUNCTION public.cmt_chair_reserved("),
    "     AND public.circle_effective_state(a.circle_id,p_at,p_known)='ACTIVE'\n", "",
)

EMPTY = "public.cmt_empty(bigint,timestamptz,timestamptz)"
EMPTY_SQL = once(committee.EMPTY_NEW, "public.cmt_live_apart(m.binding,p_at,p_known)",
                 "public.cmt_chair_reserved(m.binding,p_at,p_known)")
RESEATABLE_SQL = once(reseat.RESEATABLE_SQL,
                      "public.cmt_live_apart_before_assignment(m.binding,at_time,known,incoming)",
                      "public.cmt_chair_reserved(m.binding,at_time,known)")

# Specialise only the already-refused FILL. Keep the actual stored board,
# binding, empty place and dormant delegating ancestor as separate conditions.
PARENT_REASON = "The sub-committee chair could not be filled. This place is reserved while the committee that delegated the work is dormant. The chair is still empty. Ask the Board to check the forming law and arrange for the dormant committee to be reseated first. After reseating, the Board or the body named in the sub-committee's forming law handles the appointment."
ENDED_RETURN = "RETURN jsonb_build_object('code','CMT_ENDED','reason','This committee’s mandate has ended. Refer new work to the Board.');"
FILL_REASON = """IF op='FILL_CHAIR' AND body_ IS NOT NULL
       AND public.cmt_chair_reserved(m.binding,p_at,p_at) AND public.cmt_empty(m.circle_id,p_at,p_at)
       AND ((m.mandate->>'appointing_body' IS NULL AND body_=public.cq3_board(p_lo,p_at,p_at))
         OR m.mandate->>'appointing_body'=(SELECT c.circle_uuid::text FROM public.core_circle c WHERE c.id=body_))
       AND EXISTS (
         WITH RECURSIVE ancestors AS (
           SELECT p.binding,p.circle_id,p.mandate FROM public.cmt_mandates p
            WHERE p.binding=(m.mandate->>'delegating_parent')::uuid
           UNION SELECT p.binding,p.circle_id,p.mandate FROM ancestors a JOIN public.cmt_mandates p
            ON p.binding=(a.mandate->>'delegating_parent')::uuid)
         SELECT FROM ancestors a WHERE a.binding<>m.binding
           AND public.circle_effective_state(a.circle_id,p_at,p_at)='DORMANT')
     THEN RETURN jsonb_build_object('code','CMT_PARENT_DORMANT','reason','%s'); END IF;
     """ % PARENT_REASON.replace("'", "''")
WORK_PLAN = "public.cmt_work_plan(bigint,uuid,jsonb,timestamptz)"
WORK_PLAN_SQL = once(committee.WORK_PLAN_NEW, ENDED_RETURN, FILL_REASON + ENDED_RETURN)

# body_ is the actual discussion board's Circle in this LO. Read its capacity
# at the same effective and known cut that the plan uses for the target.
DECIDER_CHECK = "     public.circle_effective_state(body_,p_at,p_at) IS DISTINCT FROM 'ACTIVE' OR\n"
FILL_TEST = "   IF NOT public.cmt_empty(m.circle_id,p_at,p_at) OR body_ IS NULL OR\n"
COMPLETE_TEST = "   IF s->>'task' IS NULL OR s->>'task' IS DISTINCT FROM m.mandate->>'task' OR body_ IS NULL OR\n"
WORK_PLAN_SQL = once(WORK_PLAN_SQL, FILL_TEST, FILL_TEST + DECIDER_CHECK)
WORK_PLAN_SQL = once(WORK_PLAN_SQL, COMPLETE_TEST, COMPLETE_TEST + DECIDER_CHECK)

REPLACEMENTS = (
    (EMPTY, committee.EMPTY_NEW, EMPTY_SQL),
    (reseat.RESEATABLE, reseat.RESEATABLE_SQL, RESEATABLE_SQL),
    (WORK_PLAN, committee.WORK_PLAN_NEW, WORK_PLAN_SQL),
)


def expect(cursor, installed):
    expected = [(RESERVED, RESERVED_SQL if installed else None)]
    expected.extend((sig, new if installed else old) for sig, old, new in REPLACEMENTS)
    for sig, body in expected:
        if definition(cursor, sig) != body:
            raise RuntimeError("0109 installed definition differs: " + sig)


def install(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        expect(cursor, False)
        cursor.execute(RESERVED_SQL)
        for _, _, new in REPLACEMENTS:
            cursor.execute(new)
        expect(cursor, True)


def uninstall(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        expect(cursor, True)
        for _, old, _ in reversed(REPLACEMENTS):
            cursor.execute(old)
        cursor.execute("DROP FUNCTION " + RESERVED)
        expect(cursor, False)


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("core", "0108_circle_reseat")]
    operations = [migrations.RunPython(install, uninstall)]
