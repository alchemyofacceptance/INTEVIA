"""A qualified stand-down spends its result once across every affected ledger."""
import re
from importlib import import_module

from django.db import migrations

from . import _board_stand_down_effect as effect

p2 = import_module("core.migrations.0086_board_continuity")
p1 = p2.p1
cmt = p1.head
cont = cmt.head
elect = cmt.elect
law = cmt.law
one, previous, function = p1.one, p1.previous, cmt.function


def helpers():
    return tuple(re.findall(r"CREATE FUNCTION public\..*?\$\$;", effect.SQL, re.DOTALL))


def edits():
    # A same-state work event is not a new Board tenure.
    old = function(p2.rules.AUTHORITY, "c108_active_lifecycle")
    yield "0086", old, one(old, p1.hard.body(old), """
 SELECT e.event_uuid FROM public.core_circlestateevent e WHERE e.circle_id=p_circle
 AND e.action='ACTIVATE' AND e.resulting_state='ACTIVE'
 AND public.s015_fold_effective_state('core_circle',p_circle,p_at,p_known)='ACTIVE'
 AND e.effective_at<=p_at AND greatest(e.received_at,e.recorded_at)<=p_known
 ORDER BY e.effective_at DESC,e.sequence DESC LIMIT 1
""")
    old = previous(cmt, "cq3_plan")
    new = one(old, "'ELECTION','COMMITTEE')", "'ELECTION','COMMITTEE','BOARD_STAND_DOWN')")
    resolver = "public.cq3_body_route(lo,p_board,'LO_COORDINATOR','POLICY','LIVING_ORGANISM',false,p_cut,p_cut)"
    new = one(new, resolver + "\n   IS DISTINCT FROM 'CQ_GOVERNANCE_CIRCLE'", "coalesce(" + resolver + ",'') NOT IN ('CQ_GOVERNANCE_CIRCLE','CQ_MEMBERSHIP')")
    new = one(new, "'resource',doc.id,'route','CQ_GOVERNANCE_CIRCLE'", "'resource',doc.id,'route'," + resolver)
    marker = " IF op='COMMITTEE' AND"
    yield "0084", old, one(new, marker, """ IF op='BOARD_STAND_DOWN' THEN RETURN public.c108_plan(p_lo,p_board,p_attachment,p_cut); END IF;
""" + marker)

    old = previous(elect, "cq3_attachment_facts")
    marker = " IF a.operation<>'SETTLE' THEN"
    new = one(old, marker, """ IF att->>'kind'='BOARD_STAND_DOWN' AND a.operation IN ('OPEN_VOTE','REOPEN_VOTE','PROCEED') THEN
   plan:=public.c108_plan(a.organism_id,a.board_uuid,att,p_cut);
   IF plan ? 'reason' THEN PERFORM public.cq_refuse(plan->>'code'); END IF;
 END IF;
""" + marker)
    marker = " IF att->>'kind'='SETTINGS' THEN\n   plan:=public.elect_settings_plan"
    new = one(new, marker, """ IF att->>'kind'='SETTINGS' AND EXISTS(
   SELECT FROM public.core_cqact ended WHERE ended.organism_id=a.organism_id AND ended.operation='IMPLEMENT'
   AND ended.facts#>>'{domain_plan,domain}'='BOARD_STAND_DOWN'
   AND ended.facts#>'{domain_plan,targets,powers}' ? (ready.facts#>>'{settings_mandate,mandate}')
   AND ended.effective_at<=p_cut AND ended.recorded_at<=p_cut) THEN
   RETURN extra||jsonb_build_object('disposition','NO_EFFECT','reason','The Board stood down and the voting-settings power returned to the members.');
 END IF;
""" + marker)
    marker = " RETURN extra||jsonb_build_object('disposition','APPLIED'"
    yield "0080", old, one(new, marker, """ IF att->>'kind'='BOARD_STAND_DOWN' THEN
   IF att->'spec'->>'effective_at' IS DISTINCT FROM 'AT_RESULT' AND att->'spec'->'effective_at' IS DISTINCT FROM to_jsonb(p_cut) THEN
     RETURN extra||jsonb_build_object('disposition','NO_EFFECT','reason','The stated effective instant differs from this result. No Board has stood down.');
   END IF;
   plan:=plan||jsonb_build_object('targets',public.c108_targets(a.organism_id,(plan->>'circle_id')::bigint,p_cut));
 END IF;
""" + marker)
    old = previous(cmt, "cq3_apply")
    marker = " IF plan->>'domain'='ELECTION' THEN"
    yield "0084", old, one(old, marker, " IF plan->>'domain'='BOARD_STAND_DOWN' THEN PERFORM public.c108_apply(NEW.act_uuid); RETURN NULL; END IF;\n" + marker)

    old = previous(cont, "s015_append_event")
    new = one(old, "'ACTIVATE','DISSOLVE_CIRCLE')", "'ACTIVATE','DISSOLVE_CIRCLE','STAND_DOWN')")
    marker = " ELSIF token='core_contextualroleassignment' AND command_rule='ELECT:SUCCESSOR_LEFT'"
    yield "0083", old, one(new, marker, """ ELSIF token='core_contextualroleassignment' AND starts_with(command_rule,'c108-stand-down:') THEN
    IF NOT is_command OR action_name IS DISTINCT FROM 'END' OR outcome IS DISTINCT FROM 'ENDED'
    OR evidence IS DISTINCT FROM command_rule OR NOT public.c108_result_child(evidence,'offices',anchor)
    THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
""" + marker)
    m = import_module("core.migrations.0061_circle_qualified_activation")
    old = function(m.SQL, "circle_guard_active_event")
    marker = " IF (NEW.action='DISSOLVE_CIRCLE'"
    yield "0061", old, one(old, marker, """ IF NEW.action='STAND_DOWN' THEN
   IF NEW.prior_state IS DISTINCT FROM 'ACTIVE' OR NEW.resulting_state IS DISTINCT FROM 'DORMANT'
     OR NEW.event_uuid IS DISTINCT FROM public.c108_executing_result(NEW.evidence_reference)
     OR NEW.evidence_reference IS DISTINCT FROM 'c108-stand-down:'||NEW.event_uuid
     OR NEW.composing_rule_reference IS DISTINCT FROM NEW.evidence_reference
     OR NEW.actor_state IS DISTINCT FROM 'NONE' OR NEW.entry_mode IS DISTINCT FROM 'COMPOSED'
     OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
     OR NOT EXISTS(SELECT FROM public.core_cqact i JOIN public.core_livingorganism lo ON lo.id=i.organism_id
       WHERE i.operation='IMPLEMENT' AND i.execution_xid=pg_current_xact_id() AND i.request->>'result'=NEW.event_uuid::text
       AND i.facts#>>'{domain_plan,circle_id}'=NEW.circle_id::text
       AND i.actor_id=NEW.enterer_identity_id AND i.fingerprint=NEW.payload_fingerprint
       AND NEW.authority_decision_reference=public.disc_decision(i.authority_source,i.fingerprint)
       AND NEW.authority_basis_id=lo.founding_authority_basis_id AND NEW.effective_until IS NULL
       AND (NEW.effective_at,NEW.occurred_at,NEW.received_at)=(i.effective_at,i.received_at,i.received_at)
       AND NEW.request_reference=NEW.evidence_reference AND NEW.idempotency_key=NEW.evidence_reference)
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN NEW;
 END IF;
""" + marker)
    old = previous(cont, "sbg_guard_circleparticipationend")
    marker = "    IF starts_with(NEW.composing_rule_reference,'elect-term:recognition:')"
    yield "0083", old, one(old, marker, """    IF starts_with(NEW.composing_rule_reference,'c108-stand-down:') THEN
      IF NOT public.c108_result_child(NEW.composing_rule_reference,'places',NEW.participation_id)
        OR NEW.actor_state IS DISTINCT FROM 'NONE' OR NEW.entry_mode IS DISTINCT FROM 'COMPOSED'
        OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL OR NEW.actor_capacity IS DISTINCT FROM 'NA_NO_PARTY'
        OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id OR NEW.composer_state IS DISTINCT FROM 'REFERENCE'
        OR NEW.composing_rule_state IS DISTINCT FROM 'REFERENCE' OR NOT EXISTS(
         SELECT FROM public.core_circleparticipation p JOIN public.core_governedrule g ON g.rule_uuid=p.seat_rule_uuid
         JOIN public.core_circlestateevent e ON e.circle_id=p.circle_id AND e.action='STAND_DOWN'
         WHERE p.id=NEW.participation_id AND g.id=NEW.composing_rule_id AND e.event_uuid=NEW.composed_from_event_uuid
         AND NEW.composing_rule_reference=e.evidence_reference AND NEW.enterer_identity_id=e.enterer_identity_id
         AND (NEW.effective_at,NEW.occurred_at,NEW.received_at)=(greatest(p.effective_at,e.effective_at),e.occurred_at,e.received_at))
      THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
      RETURN NEW;
    END IF;
""" + marker)
    old = function(p2.rules.CAUSE, "c108_check_ending")
    yield "0086", old, one(old, "life.effective_at IS DISTINCT FROM NEW.effective_at", """NEW.effective_at IS DISTINCT FROM
 (CASE WHEN TG_TABLE_NAME='core_circleparticipationend' THEN greatest(life.effective_at,
   (SELECT effective_at FROM public.core_circleparticipation WHERE id=(to_jsonb(NEW)->>'participation_id')::bigint))
 ELSE greatest(life.effective_at,(SELECT max(effective_at) FROM public.core_contextualroleassignmentstateevent
   WHERE assignment_id=(to_jsonb(NEW)->>'assignment_id')::bigint AND action='ASSIGN')) END)""")
    old = previous(cont, "s2b_guard_office_attribution")
    marker = "    IF NEW.composing_rule_reference='ELECT:TERM_END' THEN"
    yield "0083", old, one(old, marker, """    IF starts_with(NEW.evidence_reference,'c108-stand-down:') THEN
      IF NOT public.c108_result_child(NEW.evidence_reference,'offices',NEW.assignment_id) OR
        NEW.composing_rule_reference IS DISTINCT FROM NEW.evidence_reference OR NEW.action IS DISTINCT FROM 'END'
        OR NEW.prior_state NOT IN ('ACTIVE','SUSPENDED') OR NEW.prior_state IS NULL OR NEW.resulting_state IS DISTINCT FROM 'ENDED'
        OR NEW.actor_state IS DISTINCT FROM 'NONE' OR NEW.entry_mode IS DISTINCT FROM 'COMPOSED' OR NEW.actor_id IS NOT NULL
        OR NEW.actor_access_epoch IS NOT NULL OR NEW.actor_capacity IS DISTINCT FROM 'NA_NO_PARTY'
        OR NEW.effective_until IS NOT NULL OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
        OR NOT EXISTS(SELECT FROM public.core_circlestateevent e WHERE e.evidence_reference=NEW.evidence_reference
          AND e.action='STAND_DOWN' AND (e.authority_basis_id,e.authority_decision_reference,e.enterer_identity_id,
            e.occurred_at,e.received_at,e.payload_fingerprint)=(NEW.authority_basis_id,NEW.authority_decision_reference,
            NEW.enterer_identity_id,NEW.occurred_at,NEW.received_at,NEW.payload_fingerprint)
          AND NEW.request_reference=e.evidence_reference||':office:'||NEW.assignment_id AND NEW.idempotency_key=NEW.request_reference)
      THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
      RETURN NEW;
    END IF;
""" + marker)
    old = previous(cmt, "circle_coordinator_event")
    marker = " IF NEW.action='END' AND NEW.composing_rule_reference='ELECT:TERM_END'"
    yield "0084", old, one(old, marker, """ IF NEW.action='END' AND public.c108_result_child(NEW.evidence_reference,'offices',NEW.assignment_id)
 AND NEW.composing_rule_reference=NEW.evidence_reference THEN RETURN NULL; END IF;
""" + marker)

    yield from mandate_edits()
    yield from reversion_edits()

    old = previous(cmt, "cq3_check_committed")
    marker = " IF plan->>'domain'='ELECTION' THEN"
    new = one(old, "b.circle_id=public.cq3_board(a.organism_id,stamp,stamp))", """(b.circle_id=public.cq3_board(a.organism_id,stamp,stamp) OR
        (b.circle_id IS NULL AND b.organism_id=a.organism_id AND plan->>'route'='CQ_MEMBERSHIP'
         AND public.c108_reverted(a.organism_id,stamp,stamp))))""")
    yield "0084", old, one(new, marker, """ IF plan->>'domain'='BOARD_STAND_DOWN' THEN
   PERFORM public.c108_complete(a);
   RETURN;
 END IF;
""" + marker)


def mandate_edits():
    # Preserve the forming law. A later Board tenure cannot revive its expired mandate.
    old = function(cmt.committee.SQL, "cmt_live")
    yield "0084", old, one(old, "AND (a.mandate->>'ends_at' IS NULL", """AND NOT EXISTS(SELECT FROM public.core_cqact i
     WHERE i.organism_id=a.organism_id AND i.operation='IMPLEMENT' AND i.facts#>>'{domain_plan,domain}'='BOARD_STAND_DOWN'
     AND EXISTS(SELECT FROM public.core_circlestateevent e WHERE e.action='STAND_DOWN' AND e.event_uuid::text=i.request->>'result' AND e.effective_at<=p_at AND e.recorded_at<=p_known)
     AND i.facts#>'{domain_plan,targets,mandates}' ? a.binding::text AND i.effective_at<=p_at AND i.recorded_at<=p_known)
     AND (a.mandate->>'ends_at' IS NULL""")

    old = function(cmt.committee.SQL, "cmt_custody_guard")
    marker = " IF NEW.cause ? 'implementation' THEN"
    yield "0084", old, one(old, marker, """ IF NEW.cause ? 'stand_down' THEN
   IF prior_.receipt IS NULL OR NEW.holder IS NOT NULL OR NEW.office IS NOT NULL OR NEW.place IS NOT NULL
    OR NEW.clause IS DISTINCT FROM prior_.clause OR NEW.mandate IS DISTINCT FROM prior_.mandate OR NOT EXISTS(
    SELECT FROM public.core_cqact a WHERE a.operation='IMPLEMENT' AND a.execution_xid=pg_current_xact_id()
    AND a.facts#>>'{domain_plan,domain}'='BOARD_STAND_DOWN' AND public.cq3_implement_source(a)=a.authority_source
    AND a.facts#>'{domain_plan,targets,work}' ? prior_.receipt::text AND a.effective_at=NEW.effective_at
    AND NEW.cause=jsonb_build_object('stand_down',a.request->>'result','custodian','LO_COORDINATORS','organism',a.organism_id))
    THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
   RETURN NEW;
 END IF;
 IF prior_.cause ? 'stand_down' THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
""" + marker)
    old = function(cmt.committee.SQL, "cmt_worker")
    body = p1.hard.body(old)
    new = """ WITH last_row AS (
    SELECT w.* FROM public.core_cmtcustody w WHERE w.activity=p_activity AND w.effective_at<=p_at AND w.recorded_at<=p_known
    ORDER BY w.effective_at DESC,w.recorded_at DESC LIMIT 1)
 SELECT ordinary.* FROM (""" + body + """) ordinary
 WHERE NOT EXISTS(SELECT FROM last_row w WHERE w.cause ? 'stand_down')
 UNION ALL
 SELECT co.person,co.id,w.receipt,w.clause FROM last_row w LEFT JOIN LATERAL (
   SELECT m.identity_id AS person,a.id FROM public.core_contextualroleassignment a
   JOIN public.core_organismmembership m ON m.id=a.membership_id
   WHERE m.living_organism_id=(w.cause->>'organism')::bigint
   AND a.assignment_uuid=public.circle_lo_office(m.living_organism_id,m.identity_id,p_at,p_known)
 ) co ON true WHERE w.cause ? 'stand_down'
"""
    yield "0084", old, one(old, body, new)
    old = function(cmt.committee.SQL, "cmt_board_return")
    yield "0084", old, one(old, " WHERE due.moment<=p_at", " WHERE NOT (w.cause ? 'stand_down') AND due.moment<=p_at")
    old = function(cmt.committee.SQL, "cmt_work_plan")
    marker = "   IF custody_.receipt IS NULL OR"
    yield "0084", old, one(old, marker, """   IF EXISTS(SELECT FROM public.core_cmtcustody w WHERE w.receipt=custody_.receipt AND w.cause ? 'stand_down')
     THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
""" + marker)
    old = function(cmt.committee.SQL, "cmt_board_work_plan")
    marker = "   IF prior_.receipt IS NULL OR"
    yield "0084", old, one(old, marker, "   IF prior_.cause ? 'stand_down' THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;\n" + marker)


def reversion_edits():
    old = function(law.HELPERS, "cq3_policy_problem")
    new = one(old, " WHEN EXISTS(SELECT FROM", " WHEN public.cq3_board(p_lo,p_cut,p_known) IS NOT NULL AND EXISTS(SELECT FROM")
    yield "0076", old, one(new, " WHEN lo.platform_root THEN NULL",""" WHEN lo.platform_root THEN NULL
 WHEN public.c108_reverted(p_lo,p_cut,p_known) AND public.cq_settings_values_at(p_lo,p_cut,p_known)->>'enabled'='true'
 THEN 'Voting is on. Put this Policy to the members.'""")
    old = previous(law, "l_owner_at")
    new = one(old, "WHEN board.event_uuid IS NOT NULL THEN 'GOVERNANCE'", """WHEN reverted.event_uuid IS NOT NULL THEN CASE
      WHEN public.cq_settings_values_at(b.organism_id,cut,known)->>'enabled'='true' THEN 'MEMBERS' ELSE 'LO_COORDINATOR' END
             WHEN board.event_uuid IS NOT NULL THEN 'GOVERNANCE'""")
    new = one(new, "coalesce(closing.event_uuid, board.event_uuid", "coalesce(closing.event_uuid, reverted.event_uuid, board.event_uuid")
    marker = " LEFT JOIN public.core_organismlawbinding article"
    yield "0076", old, one(new, marker, """ LEFT JOIN LATERAL (SELECT e.event_uuid FROM public.core_circle c
   JOIN public.core_circlestateevent e ON e.circle_id=c.id WHERE c.parent_organism_id=b.organism_id
   AND c.kind='GOVERNANCE' AND e.action='STAND_DOWN' AND e.effective_at<=cut AND greatest(e.received_at,e.recorded_at)<=known
   ORDER BY e.effective_at DESC,e.sequence DESC LIMIT 1) reverted
   ON public.c108_reverted(b.organism_id,cut,known) AND b.scope_kind='LIVING_ORGANISM'
   AND b.law_class IN ('POLICY','PROCEDURE','BYLAW') AND b.owner_kind IN ('LO_COORDINATOR','GOVERNANCE')
   AND (b.law_class<>'BYLAW' OR reservation.binding IS NULL)
""" + marker)
    old = function(law.HELPERS, "cq3_body_route")
    marker = "(p_cls='BYLAW' AND p_owner='GOVERNANCE' AND gov.id IS NULL)"
    yield "0076", old, one(old, marker, marker + """ OR
    (gov.id IS NULL AND p_cls IN ('POLICY','PROCEDURE','BYLAW') AND p_owner IN ('GOVERNANCE','LO_COORDINATOR')
     AND public.c108_reverted(p_lo.id,p_cut,p_known)
     AND public.cq_settings_values_at(p_lo.id,p_cut,p_known)->>'enabled'='true')""")
    old = previous(cmt, "cq3_law_assess")
    marker = "OR (route<>'CQ_GOVERNANCE_CIRCLE' AND EXISTS"
    yield "0084", old, one(old, marker, "OR (route<>'CQ_GOVERNANCE_CIRCLE' AND public.cq3_board(lo.id,cut,known) IS NOT NULL AND EXISTS")
    old = function(import_module("core.migrations.0070_law_route_parameter_qualification").FORWARD, "l_route")
    marker = " ELSIF class_name IN ('ARTICLE','MEMBERS_RESOLUTION','BYLAW') THEN"
    yield "0070", old, one(old, marker, """ ELSIF NOT lo.platform_root AND scope='LIVING_ORGANISM' AND NOT charter
   AND class_name IN ('POLICY','PROCEDURE','BYLAW') AND owner IN ('LO_COORDINATOR','GOVERNANCE','MEMBERS')
   AND (class_name<>'BYLAW' OR public.l_reservation(lo.id,cut,known) IS NULL)
   AND public.c108_reverted(lo.id,cut,known) THEN
   IF public.cq_settings_values_at(lo.id,cut,known)->>'enabled'='true' THEN
     RETURN jsonb_build_object('reason','Voting is on. Put this change to the members.'); END IF;
   route:='COORDINATOR_ROUTE'; -- The original office and requested-route checks below still apply.
""" + marker)


def transfer_views():
    prior = elect.transfers.SQL.split('CREATE VIEW public.elect_transfers AS',1)[1].split(';',1)[0]
    # A separate row per exact power. The single result authorises them together.
    return prior, prior + """
 UNION ALL SELECT i.organism_id,i.act_uuid,i.board_uuid,i.ordinal,i.effective_at,i.recorded_at,
  i.request->>'result',old.facts#>'{domain_plan,transfer}' || jsonb_build_object(
   'from',old.facts#>>'{domain_plan,transfer,to}','to','LO_MEMBERSHIP','holder_circle',NULL,
   'committee_binding',NULL,'predecessor',old.act_uuid,'stand_down',i.request->>'result')
 FROM public.core_cqact i CROSS JOIN LATERAL jsonb_array_elements_text(i.facts#>'{domain_plan,targets,powers}') k
 JOIN public.core_cqact old ON old.act_uuid=k::uuid AND old.operation='IMPLEMENT' AND old.facts->'domain_plan' ? 'transfer'
 WHERE i.operation='IMPLEMENT' AND i.facts#>>'{domain_plan,domain}'='BOARD_STAND_DOWN' AND old.organism_id=i.organism_id
 AND EXISTS(SELECT FROM public.core_circlestateevent e WHERE e.action='STAND_DOWN' AND e.event_uuid::text=i.request->>'result')
"""


def sig(sql):
    if elect.p1.name(sql) == "s015_append_event":
        return cont.signature(sql)
    return cmt.sig(sql)


def change(editor, reverse):
    pairs=list(edits())
    before,after=transfer_views()
    with editor.connection.cursor() as cur:
        cur.execute("LOCK TABLE public.core_cqact,public.core_circlestateevent IN SHARE ROW EXCLUSIVE MODE")
        for sql, wanted in [(o,n if reverse else o) for _,o,n in pairs] + [(s,s if reverse else None) for s in helpers()]:
            cur.execute("""SELECT p.prosrc,p.proisstrict,p.provolatile::text,l.lanname::text FROM pg_proc p
            JOIN pg_language l ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)""",[sig(sql)])
            if cur.fetchone() != ((p1.hard.body(wanted),*p1.attributes(wanted)) if wanted is not None else None):
                raise RuntimeError("C1-108 stopped before replacement: unexpected " + sig(sql))
        cur.execute("CREATE TEMP VIEW c108_transfer_check AS " + (after if reverse else before))
        cur.execute("""SELECT pg_get_viewdef('pg_temp.c108_transfer_check'::regclass)=pg_get_viewdef('public.elect_transfers'::regclass)""")
        if cur.fetchone()!=(True,):
            raise RuntimeError("Unexpected installed elect_transfers definition.")
        cur.execute("DROP VIEW pg_temp.c108_transfer_check")
        if reverse:
            cur.execute("""SELECT EXISTS(SELECT FROM public.core_cqact WHERE request#>>'{attachment,kind}'='BOARD_STAND_DOWN')""")
            if cur.fetchone()[0]:
                raise RuntimeError("C1-108 retains its Board stand-down history.")
    if not reverse:
        for sql in helpers():
            editor.execute(sql,params=None)
    editor.execute("CREATE OR REPLACE VIEW public.elect_transfers AS " + (before if reverse else after),params=None)
    for _,old,new in (reversed(pairs) if reverse else pairs):
        editor.execute(p1.replace_sql(old if reverse else new),params=None)
    if reverse:
        for sql in reversed(helpers()):
            editor.execute("DROP FUNCTION " + sig(sql))


def install(apps, editor):
    change(editor, False)


def uninstall(apps, editor):
    change(editor, True)


class Migration(migrations.Migration):
    dependencies=(("core","0086_board_continuity"),)
    operations=(migrations.RunPython(install,uninstall),)
