"""Preserve pre-recorded endings when an earlier members' stand-down settles."""
from importlib import import_module

from django.db import migrations, models

restoration = import_module("core.migrations.0089_members_restore_board")
stand_down = restoration.p3
makeup = restoration.makeup
voting = stand_down.p1
one = voting.one


def ending_edits():
    # Store the actual ending records that the authorised transaction supersedes.
    # The caller already freezes and rechecks this entire object under the LO lock.
    old = stand_down.function(stand_down.effect.SQL, "c108_targets")
    new = one(old, " SELECT jsonb_build_object(", " WITH selected(target) AS MATERIALIZED (SELECT jsonb_build_object(")
    marker = "$$;"
    yield "0087", old, one(new, marker, """)
 SELECT target || jsonb_build_object(
   'superseded_place_endings',coalesce((SELECT jsonb_agg(x.id ORDER BY x.id)
     FROM jsonb_array_elements_text(target->'places') place
     JOIN public.core_circleparticipation p ON p.id=place::bigint
     JOIN public.core_circleparticipationend x ON x.participation_id=p.id
     WHERE x.effective_at>greatest(p_cut,p.effective_at)
       AND greatest(x.recorded_at,x.received_at)<=p_cut),'[]'),
   'superseded_office_endings',coalesce((SELECT jsonb_agg(x.event_uuid ORDER BY x.event_uuid)
     FROM jsonb_array_elements_text(target->'offices') office
     JOIN public.core_contextualroleassignmentstateevent x ON x.assignment_id=office::bigint
     WHERE x.resulting_state='ENDED' AND x.effective_at>greatest(p_cut,
       (SELECT max(award.effective_at) FROM public.core_contextualroleassignmentstateevent award
        WHERE award.assignment_id=x.assignment_id AND award.action='ASSIGN'))
       AND greatest(x.recorded_at,x.received_at)<=p_cut),'[]'))
 FROM selected
$$;""")
    # Keep the recorded sequence head for the L1 chain. In the superseding
    # branch calculate prior_state using the very predecessor set used by U21g.
    old = stand_down.previous(stand_down, "s015_append_event")
    marker = " ELSIF token='core_contextualroleassignment' AND command_rule='ELECT:SUCCESSOR_LEFT' THEN"
    yield "0087", old, one(old, marker, """
    IF EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent h
      WHERE h.id=supplied_head AND h.assignment_id=anchor AND h.effective_at>at_time AND h.resulting_state='ENDED') THEN
      SELECT public.s015_reduce_projection(token,coalesce(array_agg(e.id ORDER BY e.effective_at,e.occurred_at,
        e.received_at,e.recorded_at,e.sequence,e.event_uuid),'{}'::bigint[])) INTO prior
      FROM public.core_contextualroleassignmentstateevent e
      WHERE e.assignment_id=anchor AND e.sequence<=supplied_sequence
        AND (e.effective_at,e.occurred_at,e.received_at)<=(at_time,occurred,received);
    END IF;
""" + marker)


    # Read the aggregate's committed head, just as the ordinary writer does.
    # This is a recording predecessor and can be a future-effective END.
    old = stand_down.function(stand_down.effect.SQL, "c108_apply")
    head = "SELECT * INTO STRICT h FROM public.core_contextualroleassignmentstateevent WHERE assignment_id=id_ ORDER BY sequence DESC LIMIT 1;"
    yield "0087", old, one(old, head, """SELECT e.* INTO STRICT h FROM public.core_contextualroleassignment ra
 JOIN public.core_contextualroleassignmentstateevent e ON e.id=ra.head_state_event_id AND e.assignment_id=ra.id
 WHERE ra.id=id_;""")

    # The frozen rows and earlier causal ends must coexist at COMMIT. Existing
    # exact-set checks below enforce one child per targeted place/office.
    old = stand_down.function(stand_down.effect.SQL, "c108_complete")
    marker = " OR EXISTS(SELECT FROM jsonb_array_elements_text(targets->'mandates')"
    yield "0087", old, one(old, marker, """
 OR EXISTS(SELECT FROM jsonb_array_elements_text(targets->'superseded_place_endings') item
    WHERE NOT EXISTS(SELECT FROM public.core_circleparticipationend dated
      JOIN public.core_circleparticipationend down ON down.participation_id=dated.participation_id
      WHERE dated.id=item::bigint AND down.composing_rule_reference=marker
        AND down.composed_from_event_uuid=result_ AND down.effective_at<dated.effective_at
        AND public.elect_place_end(down.participation_id,greatest(down.recorded_at,down.received_at))=down.effective_at))
 OR EXISTS(SELECT FROM jsonb_array_elements_text(targets->'superseded_office_endings') item
    WHERE NOT EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent dated
      JOIN public.core_contextualroleassignmentstateevent down ON down.assignment_id=dated.assignment_id
      WHERE dated.event_uuid=item::uuid AND down.evidence_reference=marker
        AND down.sequence>dated.sequence AND down.effective_at<dated.effective_at))
""" + marker)


def vacancy_views():
    old = stand_down.p2.vacancy_views()[1]
    new = one(old, "AND e.resulting_state='ENDED'", """AND NOT EXISTS(
     SELECT FROM public.core_contextualroleassignmentstateevent sd
     WHERE sd.assignment_id=e.assignment_id AND starts_with(sd.evidence_reference,'c108-stand-down:')
       AND sd.action='END' AND sd.effective_at<=e.effective_at)
   AND e.resulting_state='ENDED'""")
    return old, one(new, "ORDER BY e.effective_at,e.recorded_at,e.id",
                    "ORDER BY e.effective_at,starts_with(coalesce(e.composing_rule_reference,''),'c108-stand-down:') DESC,e.recorded_at,e.id")


# The landed worker resolves custody at the supplied cuts. Its receipt also
# identifies the exact cause, including when several LO Coordinators can act.
STAND_DOWN_WORK = (
    "This work passed to the LO Coordinator(s) when the Board stood down. "
    "This request cannot change it. Its custody record is unchanged. "
    "Ask an LO Coordinator to pick it up or arrange reassignment."
)


def message_edits():
    old = stand_down.function(stand_down.cmt.committee.SQL, "cmt_message")
    marker = " WHEN 'CMT_WORK' THEN"
    yield "0084", old, one(old, marker, " WHEN 'CMT_WORK_STAND_DOWN' THEN '" + STAND_DOWN_WORK + "'" + marker)

    # The notice is visible only on the proper organisation and board, for
    # the specified mandate/Board target and its actual activity.
    lookup = """
     SELECT FROM public.core_serviceactivity act
     CROSS JOIN LATERAL public.cmt_worker(act.id,p_at,p_at) worker
     JOIN public.core_cmtcustody held ON held.receipt=worker.receipt
     JOIN public.core_discboard db ON db.board_uuid=p_board AND db.organism_id=p_lo
     WHERE act.activity_id=(s->>'activity')::uuid AND held.cause ? 'stand_down'
       AND held.cause->>'organism'=p_lo::text AND held.cause->>'custodian'='LO_COORDINATORS'
"""
    old = stand_down.previous(stand_down, "cmt_work_plan")
    marker = " ended_:=NOT public.cmt_live(m.binding,p_at,p_at);"
    new = one(old, marker, """ IF op='REASSIGN_WORK' AND m.binding IS NOT NULL AND EXISTS(""" + lookup + """
       AND held.mandate=m.binding AND held.board_circle IS NULL AND (db.circle_id=m.circle_id
         OR EXISTS(SELECT FROM public.core_circle g WHERE g.id=db.circle_id AND g.kind='GOVERNANCE'
              AND g.parent_organism_id=p_lo)))
     THEN PERFORM public.cq_refuse('CMT_WORK_STAND_DOWN'); END IF;
""" + marker)
    yield "0087", old, new
    old = stand_down.previous(stand_down, "cmt_board_work_plan")
    marker = " IF b.id IS NULL THEN"
    yield "0087", old, one(old, marker, """ IF s->>'act'='REASSIGN_BOARD_WORK' AND EXISTS(""" + lookup + """
       AND held.board_circle IS NOT NULL AND held.board_target=s->'target'
       AND db.circle_id=held.board_circle)
     THEN PERFORM public.cq_refuse('CMT_WORK_STAND_DOWN'); END IF;
""" + marker)

    old = stand_down.previous(stand_down, "cmt_custody_guard")
    marker = " IF prior_.cause ? 'stand_down' THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;"
    yield "0087", old, one(old, marker, """ IF prior_.cause ? 'stand_down' THEN
   PERFORM public.cq_refuse('CMT_WORK_STAND_DOWN'); END IF;""")


def edits():
    yield from ending_edits()
    yield from message_edits()


def installed(cur, signature):
    cur.execute("""SELECT p.prosrc,p.proisstrict,p.provolatile::text,l.lanname::text
        FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)""", [signature])
    return cur.fetchone()


def identity(sql):
    strict, volatility, language = voting.attributes(sql)
    if " IMMUTABLE " in sql.partition(" AS $$")[0] + " ":
        volatility = "i"
    return voting.hard.body(sql), strict, volatility, language


def change(editor, reverse):
    pairs = list(edits())
    before, after = vacancy_views()
    corrected = makeup.MAKEUP.strip()
    prior_candidate = one(corrected, "n>=1", "n>=3")
    with editor.connection.cursor() as cur:
        cur.execute("LOCK TABLE public.core_cqact,public.core_circleparticipationend IN SHARE ROW EXCLUSIVE MODE")
        for _, old, new in pairs:
            signature = stand_down.sig(old)
            if installed(cur, signature) != identity(new if reverse else old):
                raise RuntimeError("Unexpected installed body: " + signature)
        stand_down.p2.verify_view(cur, after if reverse else before)
        actual = installed(cur, stand_down.sig(corrected))
        allowed = [identity(corrected)] if reverse else [identity(corrected), identity(prior_candidate)]
        if actual not in allowed:
            raise RuntimeError("Unexpected installed constitutional Board makeup reader.")
        if reverse:
            cur.execute("""SELECT EXISTS(SELECT FROM public.core_cqact
              WHERE operation='IMPLEMENT' AND facts#>>'{domain_plan,domain}'='BOARD_STAND_DOWN'
                AND (jsonb_array_length(facts#>'{domain_plan,targets,superseded_place_endings}')>0
                  OR jsonb_array_length(facts#>'{domain_plan,targets,superseded_office_endings}')>0))""")
            if cur.fetchone()[0]:
                raise RuntimeError("C1-108 retains stand-down supersession evidence.")
    if not reverse and actual == identity(prior_candidate):
        editor.execute(voting.replace_sql(corrected), params=None)
    for _, old, new in (reversed(pairs) if reverse else pairs):
        editor.execute(voting.replace_sql(old if reverse else new), params=None)
    editor.execute("CREATE OR REPLACE VIEW public.elect_vacancy_sources AS " + (before if reverse else after), params=None)


def install(apps, editor):
    change(editor, False)


def uninstall(apps, editor):
    change(editor, True)


class Migration(migrations.Migration):
    dependencies = (("core", "0089_members_restore_board"),)
    operations = (
        migrations.RemoveConstraint("circleparticipationend", "elect_one_other_end"),
        migrations.AddConstraint("circleparticipationend", models.UniqueConstraint(
            fields=("participation",), name="elect_one_other_end",
            condition=~models.Q(composing_rule_reference__startswith="elect-term:")
                      & ~models.Q(composing_rule_reference__startswith="elect-resign:")
                      & ~models.Q(composing_rule_reference__startswith="c108-stand-down:"))),
        migrations.AddConstraint("circleparticipationend", models.UniqueConstraint(
            fields=("participation",), name="c108_one_standdown_place",
            condition=models.Q(composing_rule_reference__startswith="c108-stand-down:"))),
        migrations.RunPython(install, uninstall),
    )
