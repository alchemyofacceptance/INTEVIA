"""A members' decision can authorise a later, separately evidenced Board activation."""
import re
from importlib import import_module

from django.db import migrations

from . import _board_restoration as restore

makeup = import_module("core.migrations.0088_constitutional_board_makeup")
p3 = makeup.stand_down
p2, voting = p3.p2, p3.p1
one, previous, function = p3.one, p3.previous, p3.function


def helpers():
    body = function(p2.rules.AUTHORITY, "c108_body_matches")
    yield one(one(body, "public.c108_body_matches(", "public.c108_restore_body("),
              "->'board_stand_down'", "->'board_restoration'").replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION")
    yield from re.findall(r"CREATE FUNCTION public\..*?\$\$;", restore.SQL, re.DOTALL)


def edits():
    old = previous(p3, "cq3_plan")
    new = one(old, "'COMMITTEE','BOARD_STAND_DOWN')", "'COMMITTEE','BOARD_STAND_DOWN','BOARD_RESTORE')")
    marker = " IF op='BOARD_STAND_DOWN' THEN"
    yield "0087", old, one(new, marker, """ IF op='BOARD_RESTORE' THEN RETURN public.c108_restore_plan(p_lo,p_board,p_attachment,p_cut); END IF;
""" + marker)
    old = previous(p3, "cq3_attachment_facts")
    marker = " IF att->>'kind'='BOARD_STAND_DOWN' AND a.operation IN"
    yield "0087", old, one(old, marker, """ IF att->>'kind'='BOARD_RESTORE' AND a.operation IN ('OPEN_VOTE','REOPEN_VOTE','PROCEED') THEN
   plan:=public.c108_restore_plan(a.organism_id,a.board_uuid,att,p_cut);
   IF plan ? 'reason' THEN PERFORM public.cq_refuse(plan->>'code'); END IF;
 END IF;
""" + marker)
    old = previous(p3, "cq3_apply")
    marker = " IF plan->>'domain'='BOARD_STAND_DOWN' THEN"
    yield "0087", old, one(old, marker, " IF plan->>'domain'='BOARD_RESTORE' THEN RETURN NULL; END IF;\n" + marker)
    old = previous(p3, "cq3_check_committed")
    yield "0087", old, one(old, marker, """ IF plan->>'domain'='BOARD_RESTORE' THEN
   IF NOT EXISTS(SELECT FROM public.core_cqact r JOIN public.core_cqact i ON i.thread_uuid=r.thread_uuid
      AND i.board_uuid=r.board_uuid AND i.organism_id=r.organism_id AND i.operation='INITIATE'
      JOIN public.core_cqact o ON o.act_uuid::text=r.request->>'ballot' AND o.thread_uuid=r.thread_uuid
      AND o.board_uuid=r.board_uuid AND o.operation IN ('OPEN_VOTE','PROCEED','REOPEN_VOTE')
     WHERE r.act_uuid::text=a.request->>'result' AND r.operation='SETTLE' AND r.organism_id=a.organism_id
     AND public.cq3_qualified(r.facts#>>'{count,kind}') AND r.facts#>'{count,cqt_applied}'='true'::jsonb
     AND r.facts->'domain_plan'=plan AND i.request#>>'{attachment,kind}'='BOARD_RESTORE'
     AND i.request#>'{attachment,spec,stand_down}'=plan->'stand_down'
     AND i.request#>'{attachment,spec,citation}'=plan->'citation' AND r.board_uuid::text=plan->>'body'
     AND r.facts->'attachment_fingerprint'=o.facts->'attachment_fingerprint'
     AND r.facts->'attachment_fingerprint'=i.facts->'attachment_fingerprint'
     AND r.facts->'wording_fingerprint'=o.facts->'wording_fingerprint'
     AND public.c108_restore_body(a.organism_id,r.board_uuid,plan->'citation',a.effective_at,a.recorded_at))
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN;
 END IF;
""" + marker)
    old = previous(p2, "cq_input")
    marker = " IF NEW.operation IN ('INITIATE','ELECT_NOMINATE') THEN"
    yield "0086", old, one(old, marker, """ IF NEW.operation='INITIATE' AND NEW.request#>>'{attachment,kind}'='BOARD_RESTORE'
 AND public.cq_settings_values_at(NEW.organism_id,v_stamp,v_stamp)->>'enabled' IS DISTINCT FROM 'true'
 THEN PERFORM public.cq_refuse('CQ_RESTORE_OFF'); END IF;
""" + marker)

    old = previous(p3.cont.terms, "elect_plan")
    marker = "extra:=jsonb_build_object('seat_rule',arrangement.rule_uuid,'seat_version',arrangement.version);"
    yield "0082", old, one(old, marker, marker + """
   IF public.c108_reverted(p_lo,p_cut,p_cut) THEN
     IF jsonb_typeof(rule->'term') IS DISTINCT FROM 'object' THEN RETURN jsonb_build_object('code','CQ_RESTORE_TERM','reason','The cited seat needs its constitutional term.'); END IF;
     extra:=extra||jsonb_build_object('restoration_result',(public.c108_restore_grant(gov.id,p_cut,p_cut)).request->'result');
     IF extra->>'restoration_result' IS NULL THEN RETURN jsonb_build_object('code','CQ_RESTORE_SEATING',
       'reason','Record the members’ restoration decision before electing this Board seat.'); END IF;
   ELSE
     -- A ballot opened during seating keeps its mandate across the activation
     -- of that same tenure. Later AGM elections still use their normal rules.
     extra:=extra||coalesce((SELECT jsonb_build_object('restoration_result',rr.result)
       FROM public.core_c108restoration rr WHERE rr.activation=public.c108_active_lifecycle(gov.id,p_cut,p_cut)), '{}'::jsonb);
   END IF;""")
    old = function(p3.cont.continuity.SQL, "elect_recognition_facts")
    marker = " RETURN jsonb_build_object('source',authority_,'member',member_"
    new = one(old, marker, """ IF claim->>'type'='SEAT' AND public.c108_reverted(a.organism_id,p_cut,p_cut) THEN
   IF (public.c108_restore_grant(gov.id,p_cut,p_cut)).act_uuid IS NULL OR rule_->>'authority'='FOUNDING_COORDINATOR'
   OR public.cq_settings_values_at(a.organism_id,p_cut,p_cut)->>'enabled' IS DISTINCT FROM 'true'
   THEN PERFORM public.cq_refuse('CQ_RESTORE_SEATING'); END IF;
 END IF;
""" + marker)
    yield "0083", old, one(new, "'consent',consent_,'start',p_cut,'end',end_);", """'consent',consent_,'start',p_cut,'end',end_)
 ||CASE WHEN claim->>'type'='SEAT' AND public.c108_reverted(a.organism_id,p_cut,p_cut)
 THEN jsonb_build_object('restoration_result',(public.c108_restore_grant(gov.id,p_cut,p_cut)).request->'result') ELSE '{}'::jsonb END;""")
    yield from activation_edits()
    yield from protection_edits()


def activation_edits():
    old = previous(makeup, "circle_activation_problem")
    new = one(old, "found_source uuid; proof text;", "found_source uuid; result_ uuid; details jsonb; proof text;")
    marker = " IF c.kind='GOVERNANCE' THEN"
    yield "0088", old, one(new, marker, """ IF c.kind='GOVERNANCE' AND starts_with(evidence,'restore:') THEN
   IF split_part(substr(evidence,9),'|',1) !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
   THEN RETURN 'CQ_RESTORE_TENURE'; END IF;
   result_:=split_part(substr(evidence,9),'|',1)::uuid;
   FOR cut,as_known IN SELECT * FROM (VALUES(effective,recorded),(executed,executed)) cuts(t,k) LOOP
     IF public.circle_lo_office(c.parent_organism_id,enterer,cut,as_known) IS NULL THEN RETURN 'CIRCLE_G4_AUTHORITY'; END IF;
     IF public.s015_fold_effective_state('core_circle',c.id,cut,as_known) NOT IN ('DORMANT','ELIGIBLE')
       OR public.s015_fold_effective_state('core_livingorganism',c.parent_organism_id,cut,as_known) IS DISTINCT FROM 'ACTIVE'
       OR EXISTS(SELECT FROM public.core_circlestateevent x WHERE x.circle_id=c.id AND x.effective_at>effective)
       OR EXISTS(SELECT FROM public.core_c108restoration x WHERE x.result=result_ OR x.stand_down=public.c108_last_down(c.id,cut,as_known))
     THEN RETURN 'CQ_RESTORE_TENURE'; END IF;
     details:=public.c108_restore_details(c.id,result_,cut,as_known);
     IF details->>'problem' IS NOT NULL THEN RETURN details->>'problem'; END IF;
     proof:=public.c108_restore_ref(details);
     suffix:=public.cmt_activation_suffix(c.parent_organism_id,proof,
       CASE right(evidence,5) WHEN '|cr:Y' THEN true WHEN '|cr:N' THEN false END,cut,as_known);
     IF suffix IS NULL THEN RETURN 'CMT_ACTIVATION_CHOICE'; END IF;
     IF evidence IS DISTINCT FROM proof||suffix OR decision IS DISTINCT FROM
       's015d1:'||public.c2_target(c.circle_uuid||':'||request_id||':'||evidence)
     THEN RETURN 'CIRCLE_G4_AUTHORITY'; END IF;
   END LOOP;
   RETURN NULL;
 END IF;
""" + marker)


def protection_edits():
    old = previous(p3, "cq3_policy_problem")
    marker = "AND b.decision_route='CQ_GOVERNANCE_CIRCLE'"
    yield "0087", old, one(old, marker, marker + """ AND NOT EXISTS(SELECT FROM public.core_circle past_circle JOIN public.core_circlestateevent ended_tenure ON ended_tenure.circle_id=past_circle.id
   WHERE past_circle.parent_organism_id=p_lo AND past_circle.kind='GOVERNANCE' AND ended_tenure.action='STAND_DOWN'
   AND ended_tenure.effective_at>=b.in_force_from AND ended_tenure.effective_at<=p_cut AND greatest(ended_tenure.received_at,ended_tenure.recorded_at)<=p_known)""")
    old = previous(p3, "cq3_law_assess")
    marker = "AND voted.decision_route='CQ_GOVERNANCE_CIRCLE'"
    yield "0087", old, one(old, marker, marker + """ AND NOT EXISTS(SELECT FROM public.core_circle past_circle JOIN public.core_circlestateevent ended_tenure ON ended_tenure.circle_id=past_circle.id
   WHERE past_circle.parent_organism_id=lo.id AND past_circle.kind='GOVERNANCE' AND ended_tenure.action='STAND_DOWN'
   AND ended_tenure.effective_at>=voted.in_force_from AND ended_tenure.effective_at<=cut AND greatest(ended_tenure.received_at,ended_tenure.recorded_at)<=known)""")
    old = function(p3.cmt.committee.SQL, "cmt_activation_choice")
    marker = " AND e.evidence_reference ~"
    yield "0084", old, one(old, marker, """ AND NOT EXISTS(SELECT FROM public.core_circlestateevent x WHERE x.circle_id=c.id
 AND x.action='STAND_DOWN' AND x.sequence>e.sequence AND x.effective_at<=p_at AND greatest(x.received_at,x.recorded_at)<=p_known)
""" + marker)


def change(editor, reverse):
    pairs = tuple(edits())
    additions = tuple(helpers())
    with editor.connection.cursor() as cur:
        cur.execute("LOCK TABLE public.core_cqact,public.core_circlestateevent IN SHARE ROW EXCLUSIVE MODE")
        checks = [(old, new if reverse else old) for _, old, new in pairs]
        checks += [(s, s if reverse else None) for s in additions]
        for source, expected in checks:
            signature = p3.sig(source)
            cur.execute("""SELECT p.prosrc,p.proisstrict,p.provolatile::text,l.lanname::text
                FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)""", [signature])
            actual = cur.fetchone()
            wanted = (voting.hard.body(expected), *voting.attributes(expected)) if expected is not None else None
            if actual != wanted:
                raise RuntimeError("Unexpected installed restoration function: " + signature)
        cur.execute("SELECT to_regclass('public.core_c108restoration') IS NOT NULL")
        if cur.fetchone()[0] != reverse:
            raise RuntimeError("Unexpected Board restoration receipt table.")
        if reverse:
            cur.execute("""SELECT EXISTS(SELECT FROM public.core_cqact WHERE request#>>'{attachment,kind}'='BOARD_RESTORE')
                OR EXISTS(SELECT FROM public.core_c108restoration)""")
            if cur.fetchone()[0]:
                raise RuntimeError("C1-108 retains its recorded Board restoration journey.")
    if not reverse:
        editor.execute(restore.TABLE, params=None)
        for sql in additions:
            editor.execute(sql, params=None)
    else:
        editor.execute("DROP TRIGGER c108_restore_snapshot ON public.core_circlestateevent")
        editor.execute("DROP TRIGGER c108_restore_seat ON public.core_circleparticipation")
        editor.execute("DROP TABLE public.core_c108restoration")
    for _, old, new in (reversed(pairs) if reverse else pairs):
        editor.execute(voting.replace_sql(old if reverse else new), params=None)
    if not reverse:
        editor.execute(restore.TRIGGERS, params=None)
    else:
        for sql in reversed(additions):
            editor.execute("DROP FUNCTION " + p3.sig(sql))


def install(apps, editor):
    change(editor, False)


def uninstall(apps, editor):
    change(editor, True)


class Migration(migrations.Migration):
    dependencies = (("core", "0088_constitutional_board_makeup"),)
    operations = (migrations.RunPython(install, uninstall),)
