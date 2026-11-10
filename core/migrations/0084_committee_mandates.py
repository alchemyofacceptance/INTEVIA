"""Committees derive authority from exact local law and its qualified result."""
import re
from importlib import import_module

from django.db import migrations

from . import _committee_mandates as committee

head = import_module("core.migrations.0083_elect_board_continuity")
terms, elect, hard = head.terms, head.elect, head.hard
disc4 = elect.disc4
law = disc4.previous.authority
clarity = law.ballots.clarity
one, function = elect.one, elect.function
CAPABILITY_CODES = ("COMMITTEE_CHAIR_RULE", "COMMITTEE_PARENT_FORMATION")


def predecessor(module, name):
    return next(pair[-1] for pair in module.edits() if elect.p1.name(pair[-1]) == name)


def edits():
    old = predecessor(law, "circle_activation_problem")
    new = one(old, "found_source uuid; act text;", "found_source uuid; proof text; suffix text; act text;")
    gate = "IF evidence IS DISTINCT FROM public.cq3_birth_proof(c.id,enterer,cut,as_known)"
    yield "0076", old, one(new, gate, """proof:=public.cq3_birth_proof(c.id,enterer,cut,as_known);
         suffix:=public.cmt_activation_suffix(c.parent_organism_id,proof,
           CASE right(evidence,5) WHEN '|cr:Y' THEN true WHEN '|cr:N' THEN false END,cut,as_known);
         IF evidence=proof AND suffix IS NULL THEN RETURN 'CMT_ACTIVATION_CHOICE'; END IF;
         IF suffix IS NULL OR evidence IS DISTINCT FROM proof||suffix""")
    se = clarity.boards.se
    old = next(sql for sql in se.FUNCTION_SQL if sql.startswith("CREATE OR REPLACE FUNCTION public.s015_guard_organismentitlement(")) + ";"
    yield "0046", old, one(old, "'CIRCLE_SCOPED_EXPORT','SEALED_EXPORT'", "'CIRCLE_SCOPED_EXPORT','SEALED_EXPORT','COMMITTEE_CHAIR_RULE','COMMITTEE_PARENT_FORMATION'")
    old = function(law.records.FORWARD, "l_subscriber_capability")
    marker = "('RESOLUTION_LABEL',NULL::text)"
    yield "0066", old, one(old, marker, marker + "," + ",".join(f"('{code}','{code}')" for code in CAPABILITY_CODES))
    old = clarity.boards.SE_HEADER + clarity.se_body + "$$;"
    yield "0074", old, one(old, "'CQ_TOTALS_ONLY')", "'CQ_TOTALS_ONLY','COMMITTEE_CHAIR_RULE','COMMITTEE_PARENT_FORMATION')")
    old = function(law.records.FORWARD, "l_prepare_binding")
    yield "0066", old, one(one(old, "NOT public.l_subscriber_capability('ISSUE_CIRCLE_TERMS',lo.organism_id,NEW.in_force_from)",
        "NOT public.cmt_terms_source(NEW.adoption_event_id,lo.id,NEW.in_force_from,public.l_adoption_facts(NEW))"),
        "NOT public.l_subscriber_capability('CIRCLE_DELEGATION',lo.organism_id,NEW.in_force_from)",
        """NOT EXISTS(SELECT FROM public.cmt_mandates m WHERE m.mandate->'terms_effect'->>'binding_uuid'=terms.binding_uuid::text
            AND public.cmt_live(m.binding,NEW.in_force_from,NEW.in_force_from))""")
    old = predecessor(law, "l_require_source")
    marker = " IF kind IN ('ADOPT','RETIRE')"
    yield "0076", old, one(old, marker, " IF kind='ADOPT' AND public.cmt_terms_source(event_id,lo,cut,facts) THEN RETURN; END IF;\n" + marker)
    # Both the personal and the collective assessment inherit the historical held features.
    for name, old in (("l_assess", law.assess_definitions()[1]), ("cq3_law_assess", law.assess_definitions()[2])):
        new = old.replace("Circle delegation of law is not yet available.", "This personal route is not yet available. Use the committee vote and cite the adopted terms and area.")
        new = new.replace("Board-issued Circle terms of reference are not yet available.", "Separate issue is not yet available. Put the exact terms and their forming law to the competent body together.")
        if name == "cq3_law_assess":
            new = one(new, "previous_owner text;", "previous_owner text; grant_ jsonb;")
            marker = " IF route IS NULL OR"
            new = one(new, marker, """ grant_:=public.cmt_law_grant(lo.id,p_board,doc.id,
       (SELECT ver.content FROM public.core_libraryresourceversion ver WHERE ver.id=v.id),cls,cut,known);
 IF grant_ ? 'reason' THEN RETURN grant_; END IF;
 IF grant_ IS NOT NULL AND (pin IS NOT NULL OR cls NOT IN ('POLICY','PROCEDURE') OR scope<>'LIVING_ORGANISM'
   OR lo.platform_root OR NOT public.c2_basis_in_force(lo.founding_authority_basis_id,cut,known)
   OR public.s015_fold_effective_state('core_livingorganism',lo.id,cut,known)<>'ACTIVE') THEN PERFORM public.cq_refuse('CMT_AUTH'); END IF;
 route:=coalesce(grant_->>'route',route);
""" + marker)
            new = one(new, "OR (open_prior AND public.cq3_body_route", """OR (open_prior AND NOT
    (previous.decision_route='CQ_CIRCLE' AND (route='CQ_GOVERNANCE_CIRCLE' OR
     (grant_ IS NOT NULL AND previous.proposing_circle_id=(grant_->>'proposing_circle_id')::bigint))) AND public.cq3_body_route""")
            marker = "   effect:=public.l_adoption_facts(b);"
            new = one(new, marker, """   IF grant_ ? 'terms_binding_id' THEN
     b.owner_kind:='CIRCLE'; b.proposing_circle_id:=(grant_->>'proposing_circle_id')::bigint;
     b.owner_circle_id:=b.proposing_circle_id; b.area:=grant_->>'area'; b.terms_binding_id:=(grant_->>'terms_binding_id')::uuid;
   END IF;
""" + marker)
        yield "0076", old, new
    old = predecessor(elect, "cq3_plan")
    new = one(old, "'CIRCLE_REPORTING','ELECTION')", "'CIRCLE_REPORTING','ELECTION','COMMITTEE')")
    marker = " IF op='ELECTION' THEN"
    new = one(new, marker, " IF op='COMMITTEE' AND spec->>'act' IN ('BIND_BOARD_WORK','REASSIGN_BOARD_WORK') THEN RETURN public.cmt_board_work_plan(p_lo,p_board,p_attachment,p_cut); END IF;\n IF op='COMMITTEE' THEN RETURN public.cmt_work_plan(p_lo,p_board,p_attachment,p_cut); END IF;\n" + marker)
    new = one(new, "OR spec-keys<>'{}'::jsonb","OR spec-keys- 'recommendation'<>'{}'::jsonb")
    marker = "   RETURN plan||jsonb_build_object('domain',op);"
    new = one(new, marker, """   IF spec ? 'recommendation' AND NOT plan ? 'reason' THEN
     IF plan->>'route'<>'CQ_GOVERNANCE_CIRCLE' OR NOT EXISTS(SELECT FROM public.core_cqact r
      WHERE r.organism_id=p_lo AND r.act_uuid::text=spec->>'recommendation' AND r.operation='SETTLE'
      AND r.facts->>'disposition'='APPLIED' AND r.facts->'domain_plan'->>'domain'='COMMITTEE'
      AND r.facts->'domain_plan'->'committee'->>'act'='RECOMMEND'
      AND r.facts->'domain_plan'->'committee'->>'version'=v.id::text
      AND r.facts->'domain_plan'->'committee'->>'fingerprint'=encode(sha256(convert_to(v.content,'UTF8')),'hex'))
     THEN RETURN jsonb_build_object('code','CMT_STALE','reason',public.cmt_message('CMT_STALE')); END IF;
     plan:=plan||jsonb_build_object('recommendation',spec->'recommendation');
   END IF;
   IF NOT plan ? 'reason' THEN plan:=plan||public.cmt_form_plan(p_lo,p_board,p_attachment,p_cut); END IF;
""" + marker)
    marker = "EXCEPTION WHEN invalid_text_representation"
    new = one(new, marker, """EXCEPTION WHEN check_violation THEN
 IF starts_with(SQLERRM,'CMT_') THEN
   RETURN jsonb_build_object('code',SQLERRM,'reason',public.cmt_message(SQLERRM)); END IF;
 RAISE;
WHEN invalid_text_representation""")
    yield "0080", old, new
    old = predecessor(elect.p1, "cq3_apply")
    new = one(old, " IF plan->>'domain'='CIRCLE_REPORTING'", " IF plan->>'domain' IN ('COMMITTEE','CIRCLE_REPORTING')")
    marker = " ELSIF plan->>'domain'='GI' THEN"
    new = one(new, marker, """ IF plan->'committee' ? 'terms_effect' THEN
   SELECT id INTO STRICT event_pk FROM public.core_livingorganismevent WHERE event_uuid=(NEW.request->>'result')::uuid;
   IF (plan->'committee'->>'terms_open_prior')::boolean THEN
     INSERT INTO public.core_organismlawending(binding_id,end_event_id,decision_route,end_kind,successor_id)
     VALUES((plan->'committee'->'terms_effect'->>'predecessor_id')::uuid,event_pk,'CQ_GOVERNANCE_CIRCLE','SUPERSEDED',
       (plan->'committee'->'terms_effect'->>'binding_uuid')::uuid);
   END IF;
   INSERT INTO public.core_organismlawbinding SELECT b.* FROM jsonb_populate_record(NULL::public.core_organismlawbinding,
     (plan->'committee'->'terms_effect')||jsonb_build_object('adoption_event_id',event_pk,'in_force_from',cut)) b;
 END IF;
""" + marker)
    yield "0079", old, new
    old = predecessor(elect, "cq3_check_committed")
    new = one(old, "plan-ARRAY['domain','transfer']", "plan-ARRAY['domain','transfer','committee','recommendation']")
    marker = " IF plan->>'domain'='LAW' THEN"
    new = one(new, marker, """ IF plan->>'domain'='COMMITTEE' THEN
   IF NOT EXISTS(SELECT FROM public.core_cqact s WHERE s.act_uuid=(a.request->>'result')::uuid AND s.operation='SETTLE'
     AND s.facts->'domain_plan'=plan AND public.cq3_implement_source(a)=a.authority_source)
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN;
 END IF;
 IF plan->'committee' ? 'terms_effect' AND NOT EXISTS(SELECT FROM public.core_organismlawbinding b
   WHERE b.binding_uuid=(plan->'committee'->'terms_effect'->>'binding_uuid')::uuid
   AND public.cmt_terms_source(b.adoption_event_id,b.organism_id,b.in_force_from,public.l_adoption_facts(b)))
 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
""" + marker)
    yield "0080", old, new
    for name, module, params, tail in (
        ("cq_input", head, "NEW.organism_id,NEW.board_uuid,NEW.request->'attachment',NEW.actor_id,v_stamp", "NEW.thread_uuid,NEW.operation"),
        ("cq_source", head, "p_lo,p_board,public.elect_attachment(p_thread),p_person,p_cut", "p_thread,p_op"),
        ("elect_actor_basis", elect, "p_act.organism_id,p_act.board_uuid,att,p_act.actor_id,p_cut", "p_act.thread_uuid,p_act.operation"),
        ("elect_nomination", elect, "p_act.organism_id,p_act.board_uuid,att,p_act.actor_id,p_cut", "p_act.thread_uuid,p_act.operation"),
    ):
        old = predecessor(module, name)
        new = one(old, f"public.elect_admin_office({params})", f"public.cmt_admin({params},{tail})")
        if name == "elect_actor_basis":
            # The Board office and the committee office can belong to different people.
            # Only the effective Board officeholder gets the electoral body's refusal here.
            marker = " IF office_ IS NULL THEN"
            new = one(new, marker, """ IF office_ IS NULL AND p_act.operation='INITIATE'
 AND EXISTS (
   SELECT FROM public.core_discboard b
   WHERE b.board_uuid=p_act.board_uuid AND b.organism_id=p_act.organism_id
   AND b.circle_id=public.cq3_board(p_act.organism_id,p_cut,p_cut)
   AND public.disc_office(b.organism_id,b.circle_id,p_act.actor_id,p_cut) IS NOT NULL
   AND (public.elect_last_transfer(b.organism_id,public.elect_run_scope(att->'spec'),p_cut)).transfer
       ->>'committee_binding' IS NOT NULL
 ) THEN
   IF public.elect_plan(p_act.organism_id,p_act.board_uuid,att,p_cut)->>'code'='CQ_BODY'
   THEN PERFORM public.cq_refuse('CQ_BODY'); END IF;
 END IF;
""" + marker)
        yield module.__name__.split(".")[-1][:4], old, new
    old = predecessor(terms, "c2_seat_facts")
    yield "0082", old, one(old, " WHERE lo.id=lo_id AND", """ WHERE lo.id=lo_id AND NOT EXISTS(
     SELECT FROM public.cmt_mandates m WHERE m.circle_id=a.circle_id
     AND public.cmt_chair_end(a.id,known)<=at_time) AND""")
    old = predecessor(head, "elect_office_deadline")
    yield "0083", old, one(old, "SELECT least(e.effective_until,", "SELECT least(e.effective_until,public.cmt_chair_end(e.assignment_id,p_known),")
    old = function(terms.terms.SQL, "elect_due_office")
    start = "d.kind='BOARD_OFFICE' AND d.code<>'COORDINATOR' AND NOT starts_with(d.code,'INTEVIA_')\n AND d.scope='LIVING_ORGANISM' AND a.circle_id IS NULL"
    yield "0082", old, one(old, start, "((" + start + ") OR (d.code='COORDINATOR' AND d.scope='CIRCLE' AND public.cmt_chair_end(a.id,p_known)=b.ending))")
    old = function(import_module("core.migrations.0060_circle_coordinators").SQL, "circle_appointing_office")
    marker = " SELECT CASE WHEN actor=subject"
    yield "0060", old, one(old, marker, """ SELECT CASE WHEN public.cmt_appointment(c,actor,subject,nominee_office,at_time,known) IS NOT NULL
 THEN public.cmt_appointment(c,actor,subject,nominee_office,at_time,known)
 WHEN public.cmt_empty(c,at_time,known) THEN public.cmt_no_recovery()
 WHEN actor=subject""")

    circles = import_module("core.migrations.0063_root_circles")
    for name in ("circle_coordinator_event", "circle_generic_offices"):
        _, header, _, body = next(row for row in circles.bodies() if row[0].startswith(name + "("))
        old = header.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1) + " AS $$" + body + "$$;"
        if name == "circle_coordinator_event":
            new = one(old, " IF NEW.action='END' AND NEW.composing_rule_reference IN", """ IF NEW.action='END' AND NEW.composing_rule_reference='ELECT:TERM_END'
 AND public.cmt_chair_end(NEW.assignment_id,NEW.recorded_at)=NEW.effective_at THEN RETURN NULL; END IF;
 IF NEW.action='END' AND NEW.composing_rule_reference IN""")
            new = one(new, "IF marker IS DISTINCT FROM '|RC1'", "IF marker NOT IN ('|RC1','|CMT') OR marker IS NULL")
            new = one(new, "IF marker NOT IN ('|RC1','|CMT') OR marker IS NULL", "IF (marker NOT IN ('|RC1','|CMT') OR marker IS NULL)")
        else:
            marker = "OR (split_part(e.evidence_reference,'|',7)='RC1'"
            new = one(old, marker, """OR (split_part(e.evidence_reference,'|',7)='CMT' AND
               EXISTS(SELECT FROM public.core_cqact authorised WHERE authorised.act_uuid::text=split_part(e.evidence_reference,'|',2)
               AND authorised.operation='IMPLEMENT' AND authorised.facts->'domain_plan'->'committee'->>'act'='FILL_CHAIR'
               AND authorised.facts->'domain_plan'->'committee'->>'assignment'=a.assignment_uuid::text))
      """ + marker)
            source = "coalesce(issuer.assignment_uuid,place.participation_uuid)"
            if new.count(source) != 2:
                raise RuntimeError("Unexpected generic-office decision and receipt predicates")
            new = new.replace(source, "(CASE WHEN split_part(e.evidence_reference,'|',7)='CMT' THEN split_part(e.evidence_reference,'|',2)::uuid ELSE " + source + " END)")
            new = one(new,"WHEN 'CB1' THEN '|CB1' ELSE", "WHEN 'CB1' THEN '|CB1' WHEN 'CMT' THEN '|CMT' ELSE")
        yield "0063", old, new
    old = function(circles.offices.SQL, "circle_appointment_marker")
    yield "0060", old, one(old, " SELECT CASE WHEN NOT public.circle_vacant", """ SELECT CASE WHEN
 source=public.cmt_appointment(c,actor,subject,incoming,at_time,known) THEN '|CMT'
 WHEN NOT public.circle_vacant""")
    old = function(elect.transfers.SQL,"elect_policy_transfer")
    marker = " RETURN jsonb_build_object('transfer',grant_||"
    new = one(old, marker, """ IF EXISTS(SELECT FROM public.cmt_mandates m WHERE m.circle_id=circle_.id AND m.organism_id=p_lo)
 AND NOT EXISTS(SELECT FROM public.cmt_current(circle_.id,p_cut,p_cut) m WHERE public.cmt_live(m.binding,p_cut,p_cut))
 THEN RETURN jsonb_build_object('code','CMT_ENDED','reason',public.cmt_message('CMT_ENDED')); END IF;
 IF body_ ? 'election_powers' AND (jsonb_typeof(body_->'election_powers') IS DISTINCT FROM 'array' OR
 EXISTS(SELECT FROM jsonb_array_elements_text(body_->'election_powers') power WHERE power NOT IN
 ('CALL_NOMINATIONS','PREPARE_SLATE','RUN_ELECTION','CALL_AHEAD'))) THEN PERFORM public.cq_refuse('CMT_POWER'); END IF;
 RETURN jsonb_build_object('transfer',grant_||jsonb_strip_nulls(jsonb_build_object('committee_binding',CASE
 WHEN circle_.circle_uuid::text=body_->'committee'->>'circle' THEN p_att->'spec'->>'proposal'
 ELSE (public.cmt_current(circle_.id,p_cut,p_cut)).binding::text END))||""")
    yield "0080",old,new


def sig(sql):
    name, args = re.search(r"FUNCTION public\.(\w+)\(([^)]*)\)", sql).groups()
    types = [arg.strip().split()[1] for arg in args.split(",") if arg.strip()]
    return "public." + name + "(" + ",".join(types) + ")"


def functions():
    return [function(committee.SQL, name) for name in re.findall(r"CREATE FUNCTION public\.(\w+)\(", committee.SQL)]


def change(editor, reverse):
    pairs = list(edits())
    helpers = functions()
    with editor.connection.cursor() as cur:
        for sql, expected in [(old, hard.body(new if reverse else old)) for _, old, new in pairs] + [
            (sql, hard.body(sql) if reverse else None) for sql in helpers
        ]:
            if hard.installed(cur, sig(sql)) != expected:
                raise RuntimeError("COMMITTEES stopped before DDL: unexpected definition for " + sig(sql))
        if reverse:
            cur.execute("""SELECT EXISTS(SELECT FROM public.core_circlestateevent WHERE action='ACTIVATE' AND evidence_reference ~ '[|]cr:B?[YN]$')
            OR EXISTS(SELECT FROM public.core_lobirthingsnapshot WHERE body->'settings' ? 'committee_chair_board_member')
            OR EXISTS(SELECT FROM public.core_cqact WHERE request->'attachment'->>'kind'='COMMITTEE'
            OR facts->'domain_plan' ? 'committee' OR request->'attachment'->>'kind'='LAW'
            AND public.elect_document(request->'attachment'->>'text') ?| ARRAY['committees','committee','committee_adoption'])""")
            if cur.fetchone()[0]:
                raise RuntimeError("COMMITTEES retains its recorded acts.")
    if reverse:
        for _, old, _ in reversed(pairs):
            editor.execute(old, params=None)
        editor.execute(committee.REMOVE_TRIGGERS, params=None)
        editor.execute("DROP FUNCTION " + ",".join(sig(sql) for sql in helpers), params=None)
        editor.execute(committee.UNSCHEMA, params=None)
    else:
        editor.execute(committee.SCHEMA, params=None)
        editor.execute(committee.SQL, params=None)
        for _, _, new in pairs:
            editor.execute(new, params=None)
        editor.execute(committee.TRIGGERS, params=None)


class Migration(migrations.Migration):
    dependencies = (("core", "0083_elect_board_continuity"),)
    operations = (migrations.RunPython(lambda apps, editor: change(editor, False),
                                        lambda apps, editor: change(editor, True)),)
