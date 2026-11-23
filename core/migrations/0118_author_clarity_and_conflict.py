"""Derive author clarity from the roster and allow the last response to be a conflict."""

from importlib import import_module

from django.db import migrations

from core.migrations._clock_conversion import swap


BR2 = import_module("core.migrations.0116_clarity_full_participation")
CHAIR = import_module("core.migrations.0117_board_chair_commands")

# The eligible author contributes on the current wording without a response act.
# A conflict is checked against the pre-insert roster. Only a qualifying parent
# reaches the existing follow-up trigger; the ballot re-reads the reduced roster.
EDITS = {
    BR2.PHASE: (
        (
            "CASE WHEN p_op='CONFIRM' AND m.value=v_self THEN 'CONFIRMED'",
            """CASE WHEN (m.value->>'person')::bigint=v_first.author_id THEN 'AUTHOR_CLEAR'
         WHEN p_op='CONFIRM' AND m.value=v_self THEN 'CONFIRMED'""",
        ),
        (
            "WHERE x->>'response'='CONFIRMED';",
            "WHERE x->>'response' IN ('CONFIRMED','AUTHOR_CLEAR');",
        ),
        (
            "   RETURN v_facts||jsonb_build_object('basis',p_args->>'basis');",
            """   v_facts:=v_facts||jsonb_build_object('basis',p_args->>'basis');
   IF v_head.phase<>'CLARITY' THEN RETURN v_facts; END IF;""",
        ),
        (
            " IF p_op NOT IN ('CLOSE','PROCEED','RESPONSE_DUE','CONFIRM') THEN",
            " IF p_op NOT IN ('CLOSE','PROCEED','RESPONSE_DUE','CONFIRM','CONFLICT') THEN",
        ),
        (
            ' v_people:=public.cq_roster(p_board,p_thread,p_cut);',
            """ v_people:=public.cq_roster(p_board,p_thread,p_cut);
 IF p_op='CONFLICT' THEN
   SELECT m.entry INTO v_self FROM jsonb_array_elements(v_people) m(entry)
     WHERE (m.entry->>'person')::bigint=p_actor;
   IF v_self IS NULL THEN RETURN v_facts; END IF;
   IF p_actor<>v_first.author_id AND (
     SELECT a.operation='QUESTION' AND a.facts->>'version'=v_first.version_uuid::text
       FROM public.core_cqact a WHERE a.thread_uuid=p_thread AND a.facts->'member'=v_self
         AND a.operation IN ('CONFIRM','QUESTION') AND a.effective_at<=v_before
       ORDER BY a.ordinal DESC LIMIT 1
   ) THEN RETURN v_facts; END IF;
   SELECT coalesce(jsonb_agg(m.entry ORDER BY m.pos),'[]'::jsonb) INTO v_people
     FROM jsonb_array_elements(v_people) WITH ORDINALITY AS m(entry,pos)
     WHERE (m.entry->>'person')::bigint<>p_actor;
 END IF;""",
        ),
        (
            """ IF p_op='CONFIRM' THEN
   IF v_total<3 OR v_count<>v_total THEN RETURN v_facts; END IF;""",
            """ IF p_op IN ('CONFIRM','CONFLICT') THEN
   IF v_total<3 OR v_count<>v_total THEN RETURN v_facts; END IF;""",
        ),
        (
            """ IF p_op='CONFIRM' THEN
   v_facts:=v_facts||jsonb_build_object('rule_open',true""",
            """ IF p_op IN ('CONFIRM','CONFLICT') THEN
   v_facts:=v_facts||jsonb_build_object('rule_open',true""",
        ),
    ),
    BR2.SOURCE: (
        (
            "operation IN ('PREPARED','PREPARATION_DUE','RESPONSE_DUE','CONFIRM')",
            "operation IN ('PREPARED','PREPARATION_DUE','RESPONSE_DUE','CONFIRM','CONFLICT')",
        ),
        (
            " IF origin.operation='CONFIRM' THEN",
            " IF origin.operation IN ('CONFIRM','CONFLICT') THEN",
        ),
    ),
    BR2.FOLLOWUP: (
        (
            " IF NEW.operation IN ('PREPARED','PREPARATION_DUE','RESPONSE_DUE','CONFIRM') THEN",
            """ IF NEW.operation IN ('PREPARED','PREPARATION_DUE','RESPONSE_DUE','CONFIRM')
    OR (NEW.operation='CONFLICT' AND NEW.facts->>'rule_open'='true') THEN""",
        ),
    ),
    BR2.COMPLETE: (
        (
            " IF v_act.operation='CONFIRM' AND v_act.facts->>'rule_open'='true' AND NOT EXISTS (",
            " IF v_act.operation IN ('CONFIRM','CONFLICT') AND v_act.facts->>'rule_open'='true' AND NOT EXISTS (",
        ),
    ),
    CHAIR.PREPARER.SIGNATURE: (
        (
            " IF p_op='INITIATE' AND v_first.author_id<>p_person THEN",
            """ IF p_op IN ('CONFIRM','QUESTION') AND v_first.author_id=p_person THEN
   PERFORM public.cq_refuse('CQ_AUTHOR_CLEAR');
 END IF;
 IF p_op='INITIATE' AND v_first.author_id<>p_person THEN""",
        ),
    ),
}


def qualified_bodies():
    previous = {signature: after for signature, _, after in BR2.qualified_bodies()}
    _, previous[CHAIR.PREPARER.SIGNATURE] = CHAIR.qualified_bodies()
    bodies = []
    for signature, before in previous.items():
        after = before
        for old, new in EDITS[signature]:
            if after.count(old) != 1:
                raise RuntimeError(f"0118 requires exactly one reviewed SQL fragment in {signature}.")
            after = after.replace(old, new, 1)
        bodies.append((signature, before, after))
    return bodies


def install(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for signature, before, after in qualified_bodies():
            swap(cursor, signature, before, after)


def uninstall(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for signature, before, after in reversed(qualified_bodies()):
            swap(cursor, signature, after, before)


class Migration(migrations.Migration):
    atomic = True
    dependencies = (("core", "0117_board_chair_commands"),)
    operations = (migrations.RunPython(install, uninstall),)
