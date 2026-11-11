"""Prepare stand-down evidence, exclude its vacancies and close Off motions."""

import re
from importlib import import_module

from django.db import migrations

from . import _board_stand_down as rules

p1 = import_module("core.migrations.0085_board_voting")
one = p1.one


def vacancy_views():
    # Keep the original view type and columns: existing instructions, raw guards,
    # triggers, recovery and catch-up all refer to this exact relation.
    original = p1.elect.vacancies.SQL.split(
        "CREATE VIEW public.elect_vacancy_sources AS", 1
    )[1].split(";", 1)[0]
    changed = one(
        original,
        "WHERE e.resulting_state='ENDED'",
        "WHERE NOT starts_with(coalesce(e.evidence_reference,''),'c108-stand-down:') AND e.resulting_state='ENDED'",
    )
    changed = one(
        changed,
        "e.recorded_at,e.enterer_identity_id FROM",
        "e.recorded_at,e.enterer_identity_id,e.composing_rule_reference FROM",
    )
    # Select the causal (first) ending before filtering. Filtering inside the
    # lateral query would incorrectly promote a later scheduled term ending.
    changed = one(
        changed,
        "WHERE c.kind='GOVERNANCE'",
        "WHERE NOT starts_with(coalesce(x.composing_rule_reference,''),'c108-stand-down:') AND c.kind='GOVERNANCE'",
    )
    return original, changed


def verify_view(cur, expected):
    cur.execute("CREATE TEMP VIEW c108_expected_vacancies AS " + expected)
    cur.execute("""SELECT pg_get_viewdef('pg_temp.c108_expected_vacancies'::regclass)
                       = pg_get_viewdef('public.elect_vacancy_sources'::regclass)""")
    if cur.fetchone() != (True,):
        raise RuntimeError("Unexpected installed elect_vacancy_sources view.")
    cur.execute("DROP VIEW pg_temp.c108_expected_vacancies")


def edits():
    old = p1.previous(p1, "cq_input")
    anchor = " IF NEW.operation='ELECT_VACANCY' THEN"
    new = one(
        old,
        anchor,
        """ IF NEW.operation='CLOSE' AND NEW.request ? 'activation' THEN
   v_source:=public.c108_close_source(NEW,v_stamp);
   IF pg_trigger_depth()<2 OR v_source IS NULL THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 ELSIF NEW.operation='ELECT_VACANCY' THEN""",
    )
    new = one(
        new,
        "WHEN 'CONFIRM' THEN ARRAY['version']",
        """WHEN 'CLOSE' THEN
     CASE WHEN NEW.request ? 'activation' THEN ARRAY['activation','previous_phase'] ELSE ARRAY[]::text[] END
   WHEN 'CONFIRM' THEN ARRAY['version']""",
    )
    anchor = " ELSIF NEW.operation='CLOSE' THEN\n   NEW.facts:="
    new = one(
        new,
        anchor,
        """ ELSIF NEW.operation='CLOSE' AND NEW.request ? 'activation' THEN
   NEW.facts:=public.c108_close_facts(NEW,v_stamp);
"""
        + anchor,
    )
    yield old, new
    old = p1.previous(p1, "cq_complete")
    anchor = " IF v_act.operation='REFUSE_VOTING_OFF' THEN"
    new = one(
        old,
        anchor,
        """ IF v_act.operation='CLOSE' AND v_act.request ? 'activation' THEN
   IF v_act.authority_source IS DISTINCT FROM public.c108_close_source(v_act,v_act.recorded_at)
      OR v_act.facts->'activation' IS DISTINCT FROM v_act.request->'activation'
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN NULL;
 END IF;
"""
        + anchor,
    )
    yield old, new


def helpers():
    blocks = (rules.AUTHORITY, rules.CAUSE, rules.OPEN_MOTIONS)
    for block in blocks:
        for match in re.finditer(r"CREATE FUNCTION public\..*?\$\$;", block, re.DOTALL):
            yield match.group()


def check(cur, sql, expected):
    signature = p1.head.sig(sql)
    cur.execute(
        """SELECT p.prosrc,p.proisstrict,p.provolatile::text,l.lanname::text
        FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)""",
        [signature],
    )
    wanted = (
        (p1.hard.body(expected), *p1.attributes(expected))
        if expected is not None
        else None
    )
    if cur.fetchone() != wanted:
        raise RuntimeError("Unexpected installed function " + signature)


def change(editor, reverse):
    pairs = list(edits())
    additions = list(helpers())
    prior_view, next_view = vacancy_views()
    with editor.connection.cursor() as cur:
        for old, new in pairs:
            check(cur, old, new if reverse else old)
        for sql in additions:
            check(cur, sql, sql if reverse else None)
        verify_view(cur, next_view if reverse else prior_view)
        cur.execute("""LOCK TABLE public.core_circlestateevent,public.core_cqact,
            public.core_circleparticipationend,public.core_contextualroleassignmentstateevent
            IN SHARE ROW EXCLUSIVE MODE""")
        if not reverse:
            # The marker namespace was formerly free text. Never reinterpret an
            # old event or overwrite an existing filling instruction on upgrade.
            cur.execute("""SELECT EXISTS(SELECT FROM public.core_circlestateevent WHERE starts_with(evidence_reference,'c108-stand-down:'))
                OR EXISTS(SELECT FROM public.core_circleparticipationend WHERE starts_with(composing_rule_reference,'c108-stand-down:'))
                OR EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent WHERE starts_with(evidence_reference,'c108-stand-down:'))""")
            if cur.fetchone()[0]:
                raise RuntimeError(
                    "Existing evidence uses the reserved stand-down reference. Preserve it and resolve the collision before upgrade."
                )
        if reverse:
            cur.execute("""SELECT EXISTS(SELECT FROM public.core_circlestateevent WHERE starts_with(evidence_reference,'c108-stand-down:'))
                OR EXISTS(SELECT FROM public.core_cqact WHERE operation='CLOSE' AND request ? 'activation')""")
            if cur.fetchone()[0]:
                raise RuntimeError(
                    "C1-108 retains its stand-down and activation closure evidence."
                )
    if not reverse:
        editor.execute(rules.INDEX, params=None)
        for sql in additions:
            editor.execute(sql, params=None)
        # An older installation might already have crossed an activation with
        # an Off motion still open. A migration cannot invent a contemporary
        # enterer or backdate the closing act to that historic transaction.
        with editor.connection.cursor() as cur:
            cur.execute("""SELECT EXISTS(SELECT FROM public.core_livingorganism lo
                WHERE NOT lo.platform_root AND public.cq3_board(lo.id,statement_timestamp(),statement_timestamp()) IS NOT NULL
                AND EXISTS(SELECT FROM public.c108_open_off(lo.id)))""")
            if cur.fetchone()[0]:
                raise RuntimeError(
                    "A voting-off motion is already open under an active Board. Resolve the existing journey before upgrade."
                )
        editor.execute(rules.CAUSE_TRIGGERS, params=None)
    for old, new in pairs:
        editor.execute(p1.replace_sql(old if reverse else new), params=None)
    editor.execute(
        "CREATE OR REPLACE VIEW public.elect_vacancy_sources AS "
        + (prior_view if reverse else next_view),
        params=None,
    )
    if not reverse:
        editor.execute(rules.ACTIVATION_TRIGGER, params=None)
        return
    for table, trigger in (
        ("core_circlestateevent", "c108_close_for_activation"),
        ("core_circleparticipationend", "c108_seat_cause"),
        ("core_contextualroleassignmentstateevent", "c108_office_cause"),
        ("core_circlestateevent", "c108_stand_down"),
    ):
        editor.execute(f"DROP TRIGGER {trigger} ON public.{table}")
    for sql in reversed(additions):
        editor.execute("DROP FUNCTION " + p1.head.sig(sql))
    editor.execute("DROP INDEX public.c108_one_stand_down_per_result")


def install(apps, editor):
    change(editor, False)


def uninstall(apps, editor):
    change(editor, True)


class Migration(migrations.Migration):
    dependencies = (("core", "0085_board_voting"),)
    operations = (migrations.RunPython(install, uninstall),)
