"""The INTEVIA Clock, storage 6 of 7: the installed functions of migrations 0088 to 0095 (CK-2e).

21 installed functions read the time through PostgreSQL's own calls. This migration replaces
each under G2-4 with a successor that differs from it only where a time call becomes the
INTEVIA Clock's face with the same meaning (35 calls: 31 statement start, 4 instant,
0 transaction start):

    statement_timestamp()    public.intevia_clock_statement()
    clock_timestamp()        public.intevia_clock_instant()
    transaction_timestamp()  public.intevia_clock_transaction()

In production each face is exactly its built-in (migration 0096), so nothing changes there.
In an owned test database the faces are the ones a test may move.

Each function is held here twice, as landed and as it reads the clock. Forward: the installed
text must equal the landed text; it is replaced; the function's every other attribute must be
unchanged. Reverse: the same, the other way. ``core.migrations._clock_conversion`` has the step.
The two texts of a function are compared with the database character for character, so
neither is ever edited.
"""

from importlib import import_module

from django.db import migrations

clock = import_module("core.migrations._clock_conversion")

# public.c108_restore_receipt()
# Its landed text: 0089_members_restore_board.py (found there verbatim). Time calls moved: 1 statement.
C108_RESTORE_RECEIPT_LANDED = r"""
DECLARE result_ uuid; details jsonb; e public.core_circlestateevent;
BEGIN
 IF TG_TABLE_NAME='core_circlestateevent' THEN
   result_:=split_part(substr(NEW.evidence_reference,9),'|',1)::uuid;
   details:=public.c108_restore_details(NEW.circle_id,result_,NEW.effective_at,NEW.recorded_at);
   INSERT INTO public.core_c108restoration(activation,result,stand_down,facts)
   VALUES(NEW.event_uuid,result_,(details->>'stand_down')::uuid,details);
   RETURN NULL;
 END IF;
 SELECT * INTO STRICT e FROM public.core_circlestateevent WHERE event_uuid=NEW.activation AND action='ACTIVATE'
 AND resulting_state='ACTIVE' AND prior_state IN ('DORMANT','ELIGIBLE') AND recorded_at=statement_timestamp();
 IF pg_trigger_depth()<2 OR NEW.facts->>'result' IS DISTINCT FROM NEW.result::text
 OR NEW.facts->>'stand_down' IS DISTINCT FROM NEW.stand_down::text
 OR e.evidence_reference IS DISTINCT FROM public.c108_restore_ref(NEW.facts)
    ||coalesce(substring(e.evidence_reference from '[|]cr:[YN]$'),'')
 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN NEW;
END """
C108_RESTORE_RECEIPT_CLOCK = r"""
DECLARE result_ uuid; details jsonb; e public.core_circlestateevent;
BEGIN
 IF TG_TABLE_NAME='core_circlestateevent' THEN
   result_:=split_part(substr(NEW.evidence_reference,9),'|',1)::uuid;
   details:=public.c108_restore_details(NEW.circle_id,result_,NEW.effective_at,NEW.recorded_at);
   INSERT INTO public.core_c108restoration(activation,result,stand_down,facts)
   VALUES(NEW.event_uuid,result_,(details->>'stand_down')::uuid,details);
   RETURN NULL;
 END IF;
 SELECT * INTO STRICT e FROM public.core_circlestateevent WHERE event_uuid=NEW.activation AND action='ACTIVATE'
 AND resulting_state='ACTIVE' AND prior_state IN ('DORMANT','ELIGIBLE') AND recorded_at=public.intevia_clock_statement();
 IF pg_trigger_depth()<2 OR NEW.facts->>'result' IS DISTINCT FROM NEW.result::text
 OR NEW.facts->>'stand_down' IS DISTINCT FROM NEW.stand_down::text
 OR e.evidence_reference IS DISTINCT FROM public.c108_restore_ref(NEW.facts)
    ||coalesce(substring(e.evidence_reference from '[|]cr:[YN]$'),'')
 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN NEW;
END """

# public.c108_restore_seat_guard()
# Its landed text: 0089_members_restore_board.py (found there verbatim). Time calls moved: 8 statement.
C108_RESTORE_SEAT_GUARD_LANDED = r"""
DECLARE g public.core_cqact; route_ text;
BEGIN
 IF public.c108_last_down(NEW.circle_id,statement_timestamp(),statement_timestamp()) IS NULL OR
    public.s015_fold_effective_state('core_circle',NEW.circle_id,statement_timestamp(),statement_timestamp())='ACTIVE'
 THEN RETURN NEW; END IF;
 SELECT constitutional_route INTO route_ FROM public.core_circleadmissiondecision WHERE id=NEW.decision_id;
 g:=public.c108_restore_grant(NEW.circle_id,statement_timestamp(),statement_timestamp());
 IF g.act_uuid IS NULL OR public.cq_settings_values_at(g.organism_id,statement_timestamp(),statement_timestamp())->>'enabled' IS DISTINCT FROM 'true'
 OR NOT EXISTS(SELECT FROM public.core_cqact a WHERE a.act_uuid=NEW.participation_uuid AND a.organism_id=g.organism_id
   AND a.execution_xid=pg_current_xact_id() AND a.ordinal>g.ordinal
   AND ((route_='ELECTION' AND a.operation='IMPLEMENT' AND a.facts#>>'{domain_plan,restoration_result}'=g.request->>'result')
    OR (route_='RECOGNITION' AND a.operation='RECOGNISE' AND a.facts->>'restoration_result'=g.request->>'result')))
 THEN PERFORM public.cq_refuse('CQ_RESTORE_SEATING'); END IF;
 RETURN NEW;
END """
C108_RESTORE_SEAT_GUARD_CLOCK = r"""
DECLARE g public.core_cqact; route_ text;
BEGIN
 IF public.c108_last_down(NEW.circle_id,public.intevia_clock_statement(),public.intevia_clock_statement()) IS NULL OR
    public.s015_fold_effective_state('core_circle',NEW.circle_id,public.intevia_clock_statement(),public.intevia_clock_statement())='ACTIVE'
 THEN RETURN NEW; END IF;
 SELECT constitutional_route INTO route_ FROM public.core_circleadmissiondecision WHERE id=NEW.decision_id;
 g:=public.c108_restore_grant(NEW.circle_id,public.intevia_clock_statement(),public.intevia_clock_statement());
 IF g.act_uuid IS NULL OR public.cq_settings_values_at(g.organism_id,public.intevia_clock_statement(),public.intevia_clock_statement())->>'enabled' IS DISTINCT FROM 'true'
 OR NOT EXISTS(SELECT FROM public.core_cqact a WHERE a.act_uuid=NEW.participation_uuid AND a.organism_id=g.organism_id
   AND a.execution_xid=pg_current_xact_id() AND a.ordinal>g.ordinal
   AND ((route_='ELECTION' AND a.operation='IMPLEMENT' AND a.facts#>>'{domain_plan,restoration_result}'=g.request->>'result')
    OR (route_='RECOGNITION' AND a.operation='RECOGNISE' AND a.facts->>'restoration_result'=g.request->>'result')))
 THEN PERFORM public.cq_refuse('CQ_RESTORE_SEATING'); END IF;
 RETURN NEW;
END """

# public.cq3_check_committed(core_cqact)
# Its landed text: 0089_members_restore_board.py (assembled by that migration). Time calls moved: 1 statement.
CQ3_CHECK_COMMITTED_LANDED = r"""
DECLARE plan jsonb:=a.facts->'domain_plan'; spec jsonb; e bigint; check_plan jsonb; stamp timestamptz:=statement_timestamp();
BEGIN
 SELECT id INTO STRICT e FROM public.core_livingorganismevent WHERE event_uuid=a.act_uuid;

 IF plan->>'domain'='BOARD_RESTORE' THEN
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
 IF plan->>'domain'='BOARD_STAND_DOWN' THEN
   PERFORM public.c108_complete(a);
   RETURN;
 END IF;
 IF plan->>'domain'='ELECTION' THEN
   IF NOT public.elect_authorises((a.request->>'result')::uuid,a.organism_id,(plan->'winner'->>'person')::uuid,
      plan->>'place',(plan->>'start')::timestamptz,(plan->>'end')::timestamptz)
     OR (plan->>'type'='SEAT' AND NOT EXISTS(SELECT FROM public.core_circleparticipation p
       JOIN public.core_circleadmissiondecision d ON d.id=p.decision_id
       WHERE d.decision_uuid=a.act_uuid AND d.constitutional_route=coalesce(plan->>'admission_route','ELECTION') AND p.participation_uuid=a.act_uuid
       AND p.membership_id=(plan->'winner'->>'member')::bigint AND p.circle_id=(plan->>'circle')::bigint AND p.seat_id=plan->>'place'))
     OR (plan->>'type'='SEAT' AND plan->>'end' IS NOT NULL AND NOT EXISTS(
        SELECT FROM public.core_circleparticipationend x JOIN public.core_circleparticipation p ON p.id=x.participation_id
        WHERE p.participation_uuid=a.act_uuid AND x.composed_from_event_uuid=a.act_uuid AND x.effective_at=(plan->>'end')::timestamptz
        AND x.composing_rule_reference='elect-term:'||(a.request->>'result')))
     OR (plan->>'type'='OFFICE' AND NOT EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent h
       WHERE h.event_uuid=a.act_uuid AND h.action='ASSIGN' AND public.elect_office_source(h.assignment_id,h.event_uuid,
       h.enterer_identity_id,h.authority_decision_reference,h.effective_at,h.effective_until)))
     THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
   RETURN;
 END IF;
 IF plan->>'domain'='CIRCLE_REPORTING' THEN
   IF NOT EXISTS(SELECT FROM public.core_cqact r JOIN public.core_discboard b USING(board_uuid)
     JOIN public.core_circle c ON c.id=b.circle_id AND c.parent_organism_id=b.organism_id
     WHERE r.act_uuid::text=a.request->>'result' AND r.facts->'domain_plan'=plan
       AND plan->>'circle'=c.circle_uuid::text AND public.cq3_implement_source(a)=a.authority_source)
     THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN;
 END IF;
 IF plan->>'domain'='SETTINGS' THEN
   IF NOT EXISTS(SELECT FROM public.core_cqact s WHERE s.organism_id=a.organism_id AND s.operation='SETTINGS'
      AND s.execution_xid=a.execution_xid AND s.request=((plan->'patch')||jsonb_build_object('result',a.request->'result'))
      AND s.authority_source=a.authority_source AND s.facts->'decision'=a.request->'result'
      AND public.cq4_voted_source(s)=a.authority_source)
     THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN;
 END IF;
 IF plan->>'domain'='COMMITTEE' THEN
   IF NOT EXISTS(SELECT FROM public.core_cqact s WHERE s.act_uuid=(a.request->>'result')::uuid AND s.operation='SETTLE'
     AND s.facts->'domain_plan'=plan AND public.cq3_implement_source(a)=a.authority_source)
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN;
 END IF;
 IF plan->'committee' ? 'terms_effect' AND NOT EXISTS(SELECT FROM public.core_organismlawbinding b
   WHERE b.binding_uuid=(plan->'committee'->'terms_effect'->>'binding_uuid')::uuid
   AND public.cmt_terms_source(b.adoption_event_id,b.organism_id,b.in_force_from,public.l_adoption_facts(b)))
 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF plan->>'domain'='LAW' THEN
   SELECT request->'attachment'->'spec' INTO STRICT spec FROM public.core_cqact
    WHERE thread_uuid=a.thread_uuid AND operation='INITIATE';
   check_plan:=public.cq3_law_assess(spec,a.actor_id,a.board_uuid,stamp,stamp,e);
   IF check_plan IS DISTINCT FROM plan-ARRAY['domain','transfer','committee','recommendation'] OR
     (plan->>'kind'='ADOPT' AND NOT EXISTS(SELECT FROM public.core_organismlawbinding WHERE adoption_event_id=e)) OR
     (plan->>'kind'='RETIRE' AND NOT EXISTS(SELECT FROM public.core_organismlawending WHERE end_event_id=e))
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 ELSIF plan->>'domain'='GI' THEN
   IF NOT EXISTS(SELECT FROM public.core_giact g WHERE g.act_uuid=(plan->'payload'->>'request')::uuid
     AND g.execution_xid=pg_current_xact_id() AND g.payload=plan->'payload'
     AND g.authority_source=a.authority_source AND g.actor_id=a.actor_id AND g.effective_at=a.effective_at)
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 ELSE
   IF NOT EXISTS(SELECT FROM public.core_discboard b WHERE b.board_uuid=a.board_uuid AND
      (b.circle_id=public.cq3_board(a.organism_id,stamp,stamp) OR
        (b.circle_id IS NULL AND b.organism_id=a.organism_id AND plan->>'route'='CQ_MEMBERSHIP'
         AND public.c108_reverted(a.organism_id,stamp,stamp)))) OR
     NOT EXISTS(SELECT FROM public.core_libreleasepermission p WHERE p.version_id=(plan->>'version')::bigint
       AND (plan->>'domain'='PUBLISH_POLICY' OR p.authority_source=a.facts->'result_authority')) OR
     (plan->>'domain'='PUBLISH_POLICY' AND NOT EXISTS(SELECT FROM public.core_libpublicationfact f WHERE
       f.operation_uuid::text=a.request->>'result' AND f.kind='PUBLICATION' AND f.authority_source=a.facts->'result_authority'))
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 END IF;
END """
CQ3_CHECK_COMMITTED_CLOCK = r"""
DECLARE plan jsonb:=a.facts->'domain_plan'; spec jsonb; e bigint; check_plan jsonb; stamp timestamptz:=public.intevia_clock_statement();
BEGIN
 SELECT id INTO STRICT e FROM public.core_livingorganismevent WHERE event_uuid=a.act_uuid;

 IF plan->>'domain'='BOARD_RESTORE' THEN
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
 IF plan->>'domain'='BOARD_STAND_DOWN' THEN
   PERFORM public.c108_complete(a);
   RETURN;
 END IF;
 IF plan->>'domain'='ELECTION' THEN
   IF NOT public.elect_authorises((a.request->>'result')::uuid,a.organism_id,(plan->'winner'->>'person')::uuid,
      plan->>'place',(plan->>'start')::timestamptz,(plan->>'end')::timestamptz)
     OR (plan->>'type'='SEAT' AND NOT EXISTS(SELECT FROM public.core_circleparticipation p
       JOIN public.core_circleadmissiondecision d ON d.id=p.decision_id
       WHERE d.decision_uuid=a.act_uuid AND d.constitutional_route=coalesce(plan->>'admission_route','ELECTION') AND p.participation_uuid=a.act_uuid
       AND p.membership_id=(plan->'winner'->>'member')::bigint AND p.circle_id=(plan->>'circle')::bigint AND p.seat_id=plan->>'place'))
     OR (plan->>'type'='SEAT' AND plan->>'end' IS NOT NULL AND NOT EXISTS(
        SELECT FROM public.core_circleparticipationend x JOIN public.core_circleparticipation p ON p.id=x.participation_id
        WHERE p.participation_uuid=a.act_uuid AND x.composed_from_event_uuid=a.act_uuid AND x.effective_at=(plan->>'end')::timestamptz
        AND x.composing_rule_reference='elect-term:'||(a.request->>'result')))
     OR (plan->>'type'='OFFICE' AND NOT EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent h
       WHERE h.event_uuid=a.act_uuid AND h.action='ASSIGN' AND public.elect_office_source(h.assignment_id,h.event_uuid,
       h.enterer_identity_id,h.authority_decision_reference,h.effective_at,h.effective_until)))
     THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
   RETURN;
 END IF;
 IF plan->>'domain'='CIRCLE_REPORTING' THEN
   IF NOT EXISTS(SELECT FROM public.core_cqact r JOIN public.core_discboard b USING(board_uuid)
     JOIN public.core_circle c ON c.id=b.circle_id AND c.parent_organism_id=b.organism_id
     WHERE r.act_uuid::text=a.request->>'result' AND r.facts->'domain_plan'=plan
       AND plan->>'circle'=c.circle_uuid::text AND public.cq3_implement_source(a)=a.authority_source)
     THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN;
 END IF;
 IF plan->>'domain'='SETTINGS' THEN
   IF NOT EXISTS(SELECT FROM public.core_cqact s WHERE s.organism_id=a.organism_id AND s.operation='SETTINGS'
      AND s.execution_xid=a.execution_xid AND s.request=((plan->'patch')||jsonb_build_object('result',a.request->'result'))
      AND s.authority_source=a.authority_source AND s.facts->'decision'=a.request->'result'
      AND public.cq4_voted_source(s)=a.authority_source)
     THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN;
 END IF;
 IF plan->>'domain'='COMMITTEE' THEN
   IF NOT EXISTS(SELECT FROM public.core_cqact s WHERE s.act_uuid=(a.request->>'result')::uuid AND s.operation='SETTLE'
     AND s.facts->'domain_plan'=plan AND public.cq3_implement_source(a)=a.authority_source)
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN;
 END IF;
 IF plan->'committee' ? 'terms_effect' AND NOT EXISTS(SELECT FROM public.core_organismlawbinding b
   WHERE b.binding_uuid=(plan->'committee'->'terms_effect'->>'binding_uuid')::uuid
   AND public.cmt_terms_source(b.adoption_event_id,b.organism_id,b.in_force_from,public.l_adoption_facts(b)))
 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF plan->>'domain'='LAW' THEN
   SELECT request->'attachment'->'spec' INTO STRICT spec FROM public.core_cqact
    WHERE thread_uuid=a.thread_uuid AND operation='INITIATE';
   check_plan:=public.cq3_law_assess(spec,a.actor_id,a.board_uuid,stamp,stamp,e);
   IF check_plan IS DISTINCT FROM plan-ARRAY['domain','transfer','committee','recommendation'] OR
     (plan->>'kind'='ADOPT' AND NOT EXISTS(SELECT FROM public.core_organismlawbinding WHERE adoption_event_id=e)) OR
     (plan->>'kind'='RETIRE' AND NOT EXISTS(SELECT FROM public.core_organismlawending WHERE end_event_id=e))
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 ELSIF plan->>'domain'='GI' THEN
   IF NOT EXISTS(SELECT FROM public.core_giact g WHERE g.act_uuid=(plan->'payload'->>'request')::uuid
     AND g.execution_xid=pg_current_xact_id() AND g.payload=plan->'payload'
     AND g.authority_source=a.authority_source AND g.actor_id=a.actor_id AND g.effective_at=a.effective_at)
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 ELSE
   IF NOT EXISTS(SELECT FROM public.core_discboard b WHERE b.board_uuid=a.board_uuid AND
      (b.circle_id=public.cq3_board(a.organism_id,stamp,stamp) OR
        (b.circle_id IS NULL AND b.organism_id=a.organism_id AND plan->>'route'='CQ_MEMBERSHIP'
         AND public.c108_reverted(a.organism_id,stamp,stamp)))) OR
     NOT EXISTS(SELECT FROM public.core_libreleasepermission p WHERE p.version_id=(plan->>'version')::bigint
       AND (plan->>'domain'='PUBLISH_POLICY' OR p.authority_source=a.facts->'result_authority')) OR
     (plan->>'domain'='PUBLISH_POLICY' AND NOT EXISTS(SELECT FROM public.core_libpublicationfact f WHERE
       f.operation_uuid::text=a.request->>'result' AND f.kind='PUBLICATION' AND f.authority_source=a.facts->'result_authority'))
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 END IF;
END """

# public.cq_input()
# Its landed text: 0089_members_restore_board.py (assembled by that migration). Time calls moved: 1 statement.
CQ_INPUT_LANDED = r"""
DECLARE v_owner uuid; v_stamp timestamptz:=statement_timestamp(); v_source jsonb; v_allowed text[]; denial text; specific text;
 attempted text:=NEW.operation; nested_operation text;
BEGIN
 SELECT organism_id INTO v_owner FROM public.core_livingorganism WHERE id=NEW.organism_id;
 PERFORM public.s015_lock_organisms(ARRAY[v_owner]);
 IF NEW.received_at IS NULL OR NEW.received_at>v_stamp OR NEW.execution_xid IS NOT NULL
   OR NEW.facts IS NOT NULL OR NEW.fingerprint IS NOT NULL OR NEW.authority_source IS NOT NULL
   OR NEW.ordinal IS NOT NULL OR NEW.effective_at IS NOT NULL OR NEW.recorded_at IS NOT NULL
   OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch)
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;


 IF NEW.operation='REFUSE_VOTING_OFF' THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF NEW.operation='CLOSE' AND NEW.request ? 'activation' THEN
   v_source:=public.c108_close_source(NEW,v_stamp);
   IF pg_trigger_depth()<2 OR v_source IS NULL THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 ELSIF NEW.operation='ELECT_VACANCY' THEN
   SELECT jsonb_build_object('kind','RULE','route','ELECT_VACANCY','reference',v.ending) INTO v_source
   FROM public.core_electvacancy v WHERE v.ending::text=NEW.request->>'ending' AND v.route_act=NEW.act_uuid
    AND v.organism_id=NEW.organism_id AND v.enterer=NEW.actor_id AND NEW.received_at=v.recorded_at;
   IF pg_trigger_depth()<2 OR v_source IS NULL THEN PERFORM public.cq_refuse('ELECT_VACANCY_SOURCE'); END IF;
 ELSIF NEW.operation='SETTINGS' AND NEW.request ? 'result' THEN
   v_source:=public.cq4_voted_source(NEW);
   IF pg_trigger_depth()<2 OR NEW.received_at<>v_stamp OR v_source IS NULL
     THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 ELSIF NEW.operation='IMPLEMENT' THEN
   v_source:=public.cq3_implement_source(NEW);
   IF pg_trigger_depth()<2 OR NEW.received_at<>v_stamp OR v_source IS NULL THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 ELSIF NEW.operation='RULE_OPEN' THEN
   IF pg_trigger_depth()<2 OR NEW.received_at<>v_stamp THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   v_source:=public.cq_open_source(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.actor_id,NEW.actor_epoch,NEW.request);
 ELSIF NEW.operation='NO_BALLOT' THEN PERFORM public.cq_refuse('CQ_COMPLETE');
 ELSIF NEW.operation IN ('SETTLE','REOPEN_VOTE') AND NEW.request ? 'cause' THEN
   IF pg_trigger_depth()<2 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   v_source:=public.cq_loss_source(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.actor_id,NEW.request);
 ELSE
 v_source:=public.cq_source(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.operation,NEW.actor_id,v_stamp);
 IF public.cq_source(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.operation,NEW.actor_id,NEW.received_at)
   IS DISTINCT FROM v_source THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 END IF;
 IF NEW.operation='INITIATE' AND NEW.request#>>'{attachment,kind}'='BOARD_RESTORE'
 AND public.cq_settings_values_at(NEW.organism_id,v_stamp,v_stamp)->>'enabled' IS DISTINCT FROM 'true'
 THEN PERFORM public.cq_refuse('CQ_RESTORE_OFF'); END IF;
 IF NEW.operation IN ('INITIATE','ELECT_NOMINATE') THEN
   v_source:=coalesce(public.elect_actor_basis(NEW,v_stamp),v_source);
   IF public.elect_actor_basis(NEW,NEW.received_at) IS DISTINCT FROM public.elect_actor_basis(NEW,v_stamp)
     THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 END IF;
 v_allowed:=CASE NEW.operation WHEN 'SETTINGS' THEN ARRAY['predecessor','changes','reporting_effective_from','result','below_three_result']
   WHEN 'INITIATE' THEN ARRAY['version','previous_thread','attachment','referral_result'] WHEN 'CLOSE' THEN
     CASE WHEN NEW.request ? 'activation' THEN ARRAY['activation','previous_phase'] ELSE ARRAY[]::text[] END
   WHEN 'CONFIRM' THEN ARRAY['version']
   WHEN 'ELECT_NOMINATE' THEN ARRAY['person','assignment','statement']
   WHEN 'ELECT_CONSENT' THEN ARRAY['nomination'] WHEN 'ELECT_WITHDRAW' THEN ARRAY['nomination']
   WHEN 'RECOGNITION_CONSENT' THEN ARRAY['claim']
    WHEN 'RECOGNISE' THEN ARRAY['claim','consent','result']
    WHEN 'BOARD_RESIGN' THEN ARRAY['participation','binding','version','provision','notice'] WHEN 'ELECT_VACANCY' THEN ARRAY['ending'] WHEN 'IMPLEMENT' THEN ARRAY['result'] WHEN 'RULE_OPEN' THEN ARRAY['trigger_act'] WHEN 'ANSWER' THEN ARRAY['choice','ballot'] WHEN 'SETTLE' THEN ARRAY['ballot','cause'] WHEN 'REOPEN_VOTE' THEN ARRAY['result','cause'] WHEN 'QUESTION' THEN ARRAY['version'] WHEN 'RESPONSE_DUE' THEN ARRAY['window'] WHEN 'CONFLICT' THEN ARRAY['basis'] ELSE ARRAY[]::text[] END;
 IF jsonb_typeof(NEW.request) IS DISTINCT FROM 'object' OR NEW.request-v_allowed<>'{}'::jsonb
 THEN PERFORM public.cq_refuse('CQ_INPUT'); END IF;

 IF NEW.operation IN ('RECOGNITION_CONSENT','RECOGNISE','BOARD_RESIGN') THEN
   IF NEW.operation='BOARD_RESIGN' THEN NEW.facts:=public.elect_resignation_facts(NEW,v_stamp);
   ELSE
     NEW.facts:=public.elect_recognition_facts(NEW,v_stamp);
     IF (public.elect_recognition_facts(NEW,NEW.received_at)-'start') IS DISTINCT FROM (NEW.facts-'start')
       THEN PERFORM public.cq_refuse('ELECT_RECOGNISER'); END IF;
   END IF;
   v_source:=NEW.facts->'source';
 ELSIF NEW.operation='ELECT_VACANCY' THEN
   SELECT jsonb_build_object('instruction',v.instruction,'kind',v.kind,'place',v.place,'vacant_from',v.effective_at) INTO STRICT NEW.facts
   FROM public.core_electvacancy v WHERE v.route_act=NEW.act_uuid;
 ELSIF NEW.operation='IMPLEMENT' THEN
   SELECT jsonb_build_object('result_authority',v_source,'disposition','APPLIED','domain_plan',r.facts->'domain_plan',
       'attachment_fingerprint',r.facts->'attachment_fingerprint','wording_version',r.facts->'version') INTO STRICT NEW.facts
     FROM public.core_cqact r WHERE r.act_uuid::text=NEW.request->>'result';

 ELSIF NEW.operation IN ('ELECT_NOMINATE','ELECT_CONSENT','ELECT_WITHDRAW') THEN
   NEW.facts:=public.elect_nomination(NEW,v_stamp);
 ELSIF NEW.operation='SETTINGS' THEN
   BEGIN
   IF NEW.request ? 'result' THEN
     NEW.facts:=public.cq4_checked(NEW.organism_id,NEW.act_uuid,NEW.request-'result',v_stamp)
       ||jsonb_build_object('decision',NEW.request->'result','basis','CQ_RESULT');
   ELSE
     NEW.facts:=public.cq4_personal_basis(NEW.organism_id,NEW.request,NEW.actor_id,v_stamp);
     IF public.cq4_personal_basis(NEW.organism_id,NEW.request,NEW.actor_id,NEW.received_at) IS DISTINCT FROM NEW.facts
       THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
     NEW.facts:=public.cq4_checked(NEW.organism_id,NEW.act_uuid,NEW.request,v_stamp)||NEW.facts;
   END IF;

   EXCEPTION WHEN check_violation THEN
     specific:=SQLERRM;
     IF NEW.request ? 'result' THEN RAISE; END IF;
     IF specific='CQ_BOARD_ACTIVE' THEN denial:=specific;
     ELSIF specific='CQ_SETTINGS_VOTE' AND NEW.request->'changes'->'enabled'='false'::jsonb THEN
       -- Preserve admission precedence. Only record the Board rule where it
       -- independently applies to an otherwise well-formed settings patch.
       BEGIN
         PERFORM public.cq4_checked(NEW.organism_id,NEW.act_uuid,NEW.request,v_stamp);
       EXCEPTION WHEN check_violation THEN
         IF SQLERRM='CQ_BOARD_ACTIVE' THEN denial:=specific; END IF;
       END;
       IF denial IS NULL THEN RAISE; END IF;
     ELSE RAISE;
     END IF;
   END;
 ELSIF NEW.operation IN ('PROCEED','RULE_OPEN','OPEN_VOTE','ANSWER','SETTLE','REOPEN_VOTE')
   OR (NEW.operation='CONFLICT' AND EXISTS(SELECT FROM public.cq_phases h
       WHERE h.thread_uuid=NEW.thread_uuid AND h.phase IN ('VOTING','RESULT'))) THEN
   NEW.facts:=public.cq_ballot_facts(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.operation,
       NEW.act_uuid,NEW.request,NEW.actor_id,v_stamp);
   IF NEW.operation='RULE_OPEN' THEN
     NEW.operation:=CASE WHEN NEW.facts->>'phase'='RESULT' THEN 'NO_BALLOT' ELSE 'OPEN_VOTE' END;
   END IF;
   IF NEW.operation IN ('ANSWER','SETTLE') THEN
     NEW.facts:=NEW.facts||jsonb_build_object('commitment_nonce',gen_random_uuid());
   END IF;
 ELSIF NEW.operation='CLOSE' AND NEW.request ? 'activation' THEN
   NEW.facts:=public.c108_close_facts(NEW,v_stamp);
 ELSIF NEW.operation='CLOSE' THEN
   NEW.facts:=public.cq_phase_facts(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,'CLOSE',NEW.request,NEW.actor_id,v_stamp)
     || jsonb_build_object('event_kind','DECISION','ballot',NULL,'post_number',public.cq_next_post(NEW.thread_uuid),
       'closed_at',v_stamp,'settled_at',v_stamp,'reporting_rule',public.cq4_board_reporting(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,v_stamp),
       'restart',false,'retry_requested',false,'count',jsonb_build_object('kind','NO_DECISION_COORDINATOR_CLOSED','cqt_applied',false));
 ELSE
   NEW.facts:=public.cq_phase_facts(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.operation,NEW.request,NEW.actor_id,v_stamp);
 END IF;


 IF NEW.operation='INITIATE' AND NEW.request->'attachment'->>'kind'='ELECTION' THEN
   IF public.cmt_admin(NEW.organism_id,NEW.board_uuid,NEW.request->'attachment',NEW.actor_id,v_stamp,NEW.thread_uuid,NEW.operation) IS NULL THEN PERFORM public.cq_refuse('ELECT_CALL'); END IF;

   IF NEW.request->'attachment'->'spec' ? 'vacancy' AND EXISTS(SELECT FROM public.core_cqact i
       WHERE i.organism_id=NEW.organism_id AND i.operation='INITIATE'
       AND i.request->'attachment'->'spec'->>'vacancy'=NEW.request->'attachment'->'spec'->>'vacancy') THEN
      PERFORM public.cq_refuse('ELECT_VACANCY_USED'); END IF;
 ELSIF public.elect_attachment(NEW.thread_uuid) IS NOT NULL THEN
   IF NEW.operation='ANSWER' AND NOT public.elect_board_confirmation(public.elect_attachment(NEW.thread_uuid)) THEN
     INSERT INTO public.core_electchoice(receipt,choice) VALUES(NEW.act_uuid,NEW.request->>'choice');
     NEW.request:=NEW.request-'choice';
   ELSIF NEW.operation IN ('NO_BALLOT','CLOSE')
      AND NOT public.elect_board_confirmation(public.elect_attachment(NEW.thread_uuid)) THEN
     NEW.facts:=NEW.facts||jsonb_build_object('event_kind','ELECTION','reporting_rule',public.elect_secret());
   ELSIF NEW.operation IN ('PREPARED','PREPARATION_DUE') THEN
     NEW.facts:=NEW.facts||jsonb_build_object('prepared_slate',public.elect_slate(NEW.thread_uuid,
       public.elect_plan(NEW.organism_id,NEW.board_uuid,public.elect_attachment(NEW.thread_uuid),v_stamp),v_stamp));
   END IF;
 END IF;
 IF NEW.operation='INITIATE' AND NEW.request ? 'referral_result' THEN
   NEW.facts:=NEW.facts||public.cq3_accept_referral(NEW,v_stamp);
 END IF;
 IF NEW.operation IN ('INITIATE','PROCEED','OPEN_VOTE','REOPEN_VOTE','SETTLE') THEN
   BEGIN
   NEW.facts:=NEW.facts||public.cq3_attachment_facts(NEW,v_stamp);
   EXCEPTION WHEN check_violation THEN
     IF SQLERRM<>'CQ_BOARD_ACTIVE' THEN RAISE; END IF;
     denial:='CQ_BOARD_ACTIVE';
   END;
 END IF;
 IF NEW.operation IN ('SETTLE','NO_BALLOT') AND NEW.facts->>'event_kind' IS DISTINCT FROM 'ELECTION' AND NEW.facts->'count'->>'kind'='NO_DECISION_FEWER_THAN_THREE' THEN
   NEW.facts:=NEW.facts||jsonb_build_object('referral',public.cq3_referral(NEW,v_stamp));
 END IF;

 IF denial IS NOT NULL THEN
   IF pg_trigger_depth()=1 THEN
     -- Reject the original INSERT (zero affected rows). A nested insert of
     -- its original inputs independently derives the immutable receipt.
     INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,
       actor_id,actor_epoch,received_at,request)
     VALUES(NEW.act_uuid,NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,attempted,
       NEW.actor_id,NEW.actor_epoch,NEW.received_at,NEW.request)
     RETURNING operation INTO nested_operation;
     IF nested_operation IS DISTINCT FROM 'REFUSE_VOTING_OFF' THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
     RETURN NULL;
   END IF;
   NEW.request:=jsonb_build_object('operation',attempted,'board',NEW.board_uuid,'thread',NEW.thread_uuid,
      'request_fingerprint',public.disc_fingerprint(NEW.request));
   NEW.operation:='REFUSE_VOTING_OFF'; NEW.board_uuid:=NULL; NEW.thread_uuid:=NULL;
   NEW.facts:=jsonb_build_object('code',denial,'rule','CQ_BOARD_ACTIVE','reason',
     CASE WHEN denial='CQ_SETTINGS_VOTE' THEN 'The members decide these settings by vote. Put the change forward as a Suggestion.' ELSE 'Switching voting off was refused. Voting stays on while the Board is active. Nothing changed. Put Board stand-down to the members or the body named in the constitution. Your LO Coordinator(s) can help.' END);
 END IF;
 SELECT coalesce(max(a.ordinal),0)+1 INTO NEW.ordinal FROM public.core_cqact a WHERE a.organism_id=NEW.organism_id;
 NEW.fingerprint:=public.disc_fingerprint(jsonb_build_object('domain','DISC2','operation',NEW.operation,'request',NEW.request,
   'facts',NEW.facts,'owner',v_owner,'thread',NEW.thread_uuid,'board',NEW.board_uuid,'act',NEW.act_uuid,'source',v_source));
 NEW.authority_source:=v_source; NEW.execution_xid:=pg_current_xact_id(); NEW.effective_at:=v_stamp; NEW.recorded_at:=v_stamp;
 RETURN NEW;
END """
CQ_INPUT_CLOCK = r"""
DECLARE v_owner uuid; v_stamp timestamptz:=public.intevia_clock_statement(); v_source jsonb; v_allowed text[]; denial text; specific text;
 attempted text:=NEW.operation; nested_operation text;
BEGIN
 SELECT organism_id INTO v_owner FROM public.core_livingorganism WHERE id=NEW.organism_id;
 PERFORM public.s015_lock_organisms(ARRAY[v_owner]);
 IF NEW.received_at IS NULL OR NEW.received_at>v_stamp OR NEW.execution_xid IS NOT NULL
   OR NEW.facts IS NOT NULL OR NEW.fingerprint IS NOT NULL OR NEW.authority_source IS NOT NULL
   OR NEW.ordinal IS NOT NULL OR NEW.effective_at IS NOT NULL OR NEW.recorded_at IS NOT NULL
   OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch)
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;


 IF NEW.operation='REFUSE_VOTING_OFF' THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF NEW.operation='CLOSE' AND NEW.request ? 'activation' THEN
   v_source:=public.c108_close_source(NEW,v_stamp);
   IF pg_trigger_depth()<2 OR v_source IS NULL THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 ELSIF NEW.operation='ELECT_VACANCY' THEN
   SELECT jsonb_build_object('kind','RULE','route','ELECT_VACANCY','reference',v.ending) INTO v_source
   FROM public.core_electvacancy v WHERE v.ending::text=NEW.request->>'ending' AND v.route_act=NEW.act_uuid
    AND v.organism_id=NEW.organism_id AND v.enterer=NEW.actor_id AND NEW.received_at=v.recorded_at;
   IF pg_trigger_depth()<2 OR v_source IS NULL THEN PERFORM public.cq_refuse('ELECT_VACANCY_SOURCE'); END IF;
 ELSIF NEW.operation='SETTINGS' AND NEW.request ? 'result' THEN
   v_source:=public.cq4_voted_source(NEW);
   IF pg_trigger_depth()<2 OR NEW.received_at<>v_stamp OR v_source IS NULL
     THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 ELSIF NEW.operation='IMPLEMENT' THEN
   v_source:=public.cq3_implement_source(NEW);
   IF pg_trigger_depth()<2 OR NEW.received_at<>v_stamp OR v_source IS NULL THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 ELSIF NEW.operation='RULE_OPEN' THEN
   IF pg_trigger_depth()<2 OR NEW.received_at<>v_stamp THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   v_source:=public.cq_open_source(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.actor_id,NEW.actor_epoch,NEW.request);
 ELSIF NEW.operation='NO_BALLOT' THEN PERFORM public.cq_refuse('CQ_COMPLETE');
 ELSIF NEW.operation IN ('SETTLE','REOPEN_VOTE') AND NEW.request ? 'cause' THEN
   IF pg_trigger_depth()<2 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   v_source:=public.cq_loss_source(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.actor_id,NEW.request);
 ELSE
 v_source:=public.cq_source(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.operation,NEW.actor_id,v_stamp);
 IF public.cq_source(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.operation,NEW.actor_id,NEW.received_at)
   IS DISTINCT FROM v_source THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 END IF;
 IF NEW.operation='INITIATE' AND NEW.request#>>'{attachment,kind}'='BOARD_RESTORE'
 AND public.cq_settings_values_at(NEW.organism_id,v_stamp,v_stamp)->>'enabled' IS DISTINCT FROM 'true'
 THEN PERFORM public.cq_refuse('CQ_RESTORE_OFF'); END IF;
 IF NEW.operation IN ('INITIATE','ELECT_NOMINATE') THEN
   v_source:=coalesce(public.elect_actor_basis(NEW,v_stamp),v_source);
   IF public.elect_actor_basis(NEW,NEW.received_at) IS DISTINCT FROM public.elect_actor_basis(NEW,v_stamp)
     THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 END IF;
 v_allowed:=CASE NEW.operation WHEN 'SETTINGS' THEN ARRAY['predecessor','changes','reporting_effective_from','result','below_three_result']
   WHEN 'INITIATE' THEN ARRAY['version','previous_thread','attachment','referral_result'] WHEN 'CLOSE' THEN
     CASE WHEN NEW.request ? 'activation' THEN ARRAY['activation','previous_phase'] ELSE ARRAY[]::text[] END
   WHEN 'CONFIRM' THEN ARRAY['version']
   WHEN 'ELECT_NOMINATE' THEN ARRAY['person','assignment','statement']
   WHEN 'ELECT_CONSENT' THEN ARRAY['nomination'] WHEN 'ELECT_WITHDRAW' THEN ARRAY['nomination']
   WHEN 'RECOGNITION_CONSENT' THEN ARRAY['claim']
    WHEN 'RECOGNISE' THEN ARRAY['claim','consent','result']
    WHEN 'BOARD_RESIGN' THEN ARRAY['participation','binding','version','provision','notice'] WHEN 'ELECT_VACANCY' THEN ARRAY['ending'] WHEN 'IMPLEMENT' THEN ARRAY['result'] WHEN 'RULE_OPEN' THEN ARRAY['trigger_act'] WHEN 'ANSWER' THEN ARRAY['choice','ballot'] WHEN 'SETTLE' THEN ARRAY['ballot','cause'] WHEN 'REOPEN_VOTE' THEN ARRAY['result','cause'] WHEN 'QUESTION' THEN ARRAY['version'] WHEN 'RESPONSE_DUE' THEN ARRAY['window'] WHEN 'CONFLICT' THEN ARRAY['basis'] ELSE ARRAY[]::text[] END;
 IF jsonb_typeof(NEW.request) IS DISTINCT FROM 'object' OR NEW.request-v_allowed<>'{}'::jsonb
 THEN PERFORM public.cq_refuse('CQ_INPUT'); END IF;

 IF NEW.operation IN ('RECOGNITION_CONSENT','RECOGNISE','BOARD_RESIGN') THEN
   IF NEW.operation='BOARD_RESIGN' THEN NEW.facts:=public.elect_resignation_facts(NEW,v_stamp);
   ELSE
     NEW.facts:=public.elect_recognition_facts(NEW,v_stamp);
     IF (public.elect_recognition_facts(NEW,NEW.received_at)-'start') IS DISTINCT FROM (NEW.facts-'start')
       THEN PERFORM public.cq_refuse('ELECT_RECOGNISER'); END IF;
   END IF;
   v_source:=NEW.facts->'source';
 ELSIF NEW.operation='ELECT_VACANCY' THEN
   SELECT jsonb_build_object('instruction',v.instruction,'kind',v.kind,'place',v.place,'vacant_from',v.effective_at) INTO STRICT NEW.facts
   FROM public.core_electvacancy v WHERE v.route_act=NEW.act_uuid;
 ELSIF NEW.operation='IMPLEMENT' THEN
   SELECT jsonb_build_object('result_authority',v_source,'disposition','APPLIED','domain_plan',r.facts->'domain_plan',
       'attachment_fingerprint',r.facts->'attachment_fingerprint','wording_version',r.facts->'version') INTO STRICT NEW.facts
     FROM public.core_cqact r WHERE r.act_uuid::text=NEW.request->>'result';

 ELSIF NEW.operation IN ('ELECT_NOMINATE','ELECT_CONSENT','ELECT_WITHDRAW') THEN
   NEW.facts:=public.elect_nomination(NEW,v_stamp);
 ELSIF NEW.operation='SETTINGS' THEN
   BEGIN
   IF NEW.request ? 'result' THEN
     NEW.facts:=public.cq4_checked(NEW.organism_id,NEW.act_uuid,NEW.request-'result',v_stamp)
       ||jsonb_build_object('decision',NEW.request->'result','basis','CQ_RESULT');
   ELSE
     NEW.facts:=public.cq4_personal_basis(NEW.organism_id,NEW.request,NEW.actor_id,v_stamp);
     IF public.cq4_personal_basis(NEW.organism_id,NEW.request,NEW.actor_id,NEW.received_at) IS DISTINCT FROM NEW.facts
       THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
     NEW.facts:=public.cq4_checked(NEW.organism_id,NEW.act_uuid,NEW.request,v_stamp)||NEW.facts;
   END IF;

   EXCEPTION WHEN check_violation THEN
     specific:=SQLERRM;
     IF NEW.request ? 'result' THEN RAISE; END IF;
     IF specific='CQ_BOARD_ACTIVE' THEN denial:=specific;
     ELSIF specific='CQ_SETTINGS_VOTE' AND NEW.request->'changes'->'enabled'='false'::jsonb THEN
       -- Preserve admission precedence. Only record the Board rule where it
       -- independently applies to an otherwise well-formed settings patch.
       BEGIN
         PERFORM public.cq4_checked(NEW.organism_id,NEW.act_uuid,NEW.request,v_stamp);
       EXCEPTION WHEN check_violation THEN
         IF SQLERRM='CQ_BOARD_ACTIVE' THEN denial:=specific; END IF;
       END;
       IF denial IS NULL THEN RAISE; END IF;
     ELSE RAISE;
     END IF;
   END;
 ELSIF NEW.operation IN ('PROCEED','RULE_OPEN','OPEN_VOTE','ANSWER','SETTLE','REOPEN_VOTE')
   OR (NEW.operation='CONFLICT' AND EXISTS(SELECT FROM public.cq_phases h
       WHERE h.thread_uuid=NEW.thread_uuid AND h.phase IN ('VOTING','RESULT'))) THEN
   NEW.facts:=public.cq_ballot_facts(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.operation,
       NEW.act_uuid,NEW.request,NEW.actor_id,v_stamp);
   IF NEW.operation='RULE_OPEN' THEN
     NEW.operation:=CASE WHEN NEW.facts->>'phase'='RESULT' THEN 'NO_BALLOT' ELSE 'OPEN_VOTE' END;
   END IF;
   IF NEW.operation IN ('ANSWER','SETTLE') THEN
     NEW.facts:=NEW.facts||jsonb_build_object('commitment_nonce',gen_random_uuid());
   END IF;
 ELSIF NEW.operation='CLOSE' AND NEW.request ? 'activation' THEN
   NEW.facts:=public.c108_close_facts(NEW,v_stamp);
 ELSIF NEW.operation='CLOSE' THEN
   NEW.facts:=public.cq_phase_facts(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,'CLOSE',NEW.request,NEW.actor_id,v_stamp)
     || jsonb_build_object('event_kind','DECISION','ballot',NULL,'post_number',public.cq_next_post(NEW.thread_uuid),
       'closed_at',v_stamp,'settled_at',v_stamp,'reporting_rule',public.cq4_board_reporting(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,v_stamp),
       'restart',false,'retry_requested',false,'count',jsonb_build_object('kind','NO_DECISION_COORDINATOR_CLOSED','cqt_applied',false));
 ELSE
   NEW.facts:=public.cq_phase_facts(NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,NEW.operation,NEW.request,NEW.actor_id,v_stamp);
 END IF;


 IF NEW.operation='INITIATE' AND NEW.request->'attachment'->>'kind'='ELECTION' THEN
   IF public.cmt_admin(NEW.organism_id,NEW.board_uuid,NEW.request->'attachment',NEW.actor_id,v_stamp,NEW.thread_uuid,NEW.operation) IS NULL THEN PERFORM public.cq_refuse('ELECT_CALL'); END IF;

   IF NEW.request->'attachment'->'spec' ? 'vacancy' AND EXISTS(SELECT FROM public.core_cqact i
       WHERE i.organism_id=NEW.organism_id AND i.operation='INITIATE'
       AND i.request->'attachment'->'spec'->>'vacancy'=NEW.request->'attachment'->'spec'->>'vacancy') THEN
      PERFORM public.cq_refuse('ELECT_VACANCY_USED'); END IF;
 ELSIF public.elect_attachment(NEW.thread_uuid) IS NOT NULL THEN
   IF NEW.operation='ANSWER' AND NOT public.elect_board_confirmation(public.elect_attachment(NEW.thread_uuid)) THEN
     INSERT INTO public.core_electchoice(receipt,choice) VALUES(NEW.act_uuid,NEW.request->>'choice');
     NEW.request:=NEW.request-'choice';
   ELSIF NEW.operation IN ('NO_BALLOT','CLOSE')
      AND NOT public.elect_board_confirmation(public.elect_attachment(NEW.thread_uuid)) THEN
     NEW.facts:=NEW.facts||jsonb_build_object('event_kind','ELECTION','reporting_rule',public.elect_secret());
   ELSIF NEW.operation IN ('PREPARED','PREPARATION_DUE') THEN
     NEW.facts:=NEW.facts||jsonb_build_object('prepared_slate',public.elect_slate(NEW.thread_uuid,
       public.elect_plan(NEW.organism_id,NEW.board_uuid,public.elect_attachment(NEW.thread_uuid),v_stamp),v_stamp));
   END IF;
 END IF;
 IF NEW.operation='INITIATE' AND NEW.request ? 'referral_result' THEN
   NEW.facts:=NEW.facts||public.cq3_accept_referral(NEW,v_stamp);
 END IF;
 IF NEW.operation IN ('INITIATE','PROCEED','OPEN_VOTE','REOPEN_VOTE','SETTLE') THEN
   BEGIN
   NEW.facts:=NEW.facts||public.cq3_attachment_facts(NEW,v_stamp);
   EXCEPTION WHEN check_violation THEN
     IF SQLERRM<>'CQ_BOARD_ACTIVE' THEN RAISE; END IF;
     denial:='CQ_BOARD_ACTIVE';
   END;
 END IF;
 IF NEW.operation IN ('SETTLE','NO_BALLOT') AND NEW.facts->>'event_kind' IS DISTINCT FROM 'ELECTION' AND NEW.facts->'count'->>'kind'='NO_DECISION_FEWER_THAN_THREE' THEN
   NEW.facts:=NEW.facts||jsonb_build_object('referral',public.cq3_referral(NEW,v_stamp));
 END IF;

 IF denial IS NOT NULL THEN
   IF pg_trigger_depth()=1 THEN
     -- Reject the original INSERT (zero affected rows). A nested insert of
     -- its original inputs independently derives the immutable receipt.
     INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,
       actor_id,actor_epoch,received_at,request)
     VALUES(NEW.act_uuid,NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,attempted,
       NEW.actor_id,NEW.actor_epoch,NEW.received_at,NEW.request)
     RETURNING operation INTO nested_operation;
     IF nested_operation IS DISTINCT FROM 'REFUSE_VOTING_OFF' THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
     RETURN NULL;
   END IF;
   NEW.request:=jsonb_build_object('operation',attempted,'board',NEW.board_uuid,'thread',NEW.thread_uuid,
      'request_fingerprint',public.disc_fingerprint(NEW.request));
   NEW.operation:='REFUSE_VOTING_OFF'; NEW.board_uuid:=NULL; NEW.thread_uuid:=NULL;
   NEW.facts:=jsonb_build_object('code',denial,'rule','CQ_BOARD_ACTIVE','reason',
     CASE WHEN denial='CQ_SETTINGS_VOTE' THEN 'The members decide these settings by vote. Put the change forward as a Suggestion.' ELSE 'Switching voting off was refused. Voting stays on while the Board is active. Nothing changed. Put Board stand-down to the members or the body named in the constitution. Your LO Coordinator(s) can help.' END);
 END IF;
 SELECT coalesce(max(a.ordinal),0)+1 INTO NEW.ordinal FROM public.core_cqact a WHERE a.organism_id=NEW.organism_id;
 NEW.fingerprint:=public.disc_fingerprint(jsonb_build_object('domain','DISC2','operation',NEW.operation,'request',NEW.request,
   'facts',NEW.facts,'owner',v_owner,'thread',NEW.thread_uuid,'board',NEW.board_uuid,'act',NEW.act_uuid,'source',v_source));
 NEW.authority_source:=v_source; NEW.execution_xid:=pg_current_xact_id(); NEW.effective_at:=v_stamp; NEW.recorded_at:=v_stamp;
 RETURN NEW;
END """

# public.c108_apply(uuid)
# Its landed text: 0090_stand_down_supersession.py (assembled by that migration). Time calls moved: 1 statement.
C108_APPLY_LANDED = r"""
DECLARE a public.core_cqact; lo public.core_livingorganism; circle_ bigint; cut timestamptz; stamp timestamptz:=statement_timestamp();
 marker text; ref text; target jsonb; x record; head_ public.core_circlestateevent; h public.core_contextualroleassignmentstateevent;
 end_at timestamptz; rid uuid; id_ bigint; w public.core_cmtcustody;
BEGIN
 SELECT * INTO STRICT a FROM public.core_cqact WHERE act_uuid=p_act AND operation='IMPLEMENT'
 AND execution_xid=pg_current_xact_id() AND public.cq3_implement_source(core_cqact)=authority_source
 AND facts->'domain_plan'->>'domain'='BOARD_STAND_DOWN';
 SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE id=a.organism_id AND NOT platform_root FOR UPDATE;
 PERFORM public.s015_lock_organisms(ARRAY[lo.organism_id]);
 rid:=(a.request->>'result')::uuid; marker:='c108-stand-down:'||rid; cut:=a.effective_at;
 circle_:=(a.facts->'domain_plan'->>'circle_id')::bigint; target:=a.facts->'domain_plan'->'targets';
 IF pg_trigger_depth()<1 OR NOT public.c108_stand_down_authorised(rid,circle_,cut,stamp)
 OR target IS DISTINCT FROM public.c108_targets(lo.id,circle_,cut) THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 PERFORM id FROM public.core_circle WHERE id=circle_ FOR UPDATE;
 PERFORM id FROM public.core_circleparticipation WHERE target->'places' @> jsonb_build_array(id) ORDER BY id FOR UPDATE;
 PERFORM id FROM public.core_contextualroleassignment WHERE target->'offices' @> jsonb_build_array(id) ORDER BY id FOR UPDATE;
 PERFORM act.id FROM public.core_serviceactivity act JOIN public.core_cmtcustody c ON c.activity=act.id
 WHERE target->'work' ? c.receipt::text ORDER BY act.id FOR UPDATE OF act;
 SELECT * INTO STRICT head_ FROM public.core_circlestateevent WHERE circle_id=circle_ ORDER BY sequence DESC LIMIT 1;
 ref:=public.disc_decision(a.authority_source,a.fingerprint);
 PERFORM public.s015_append_event('core_circle',circle_,'STAND_DOWN','DORMANT',cut,a.received_at,a.received_at,a.actor_id,
 lo.founding_authority_basis_id,ref,marker,marker,a.fingerprint,'{}',NULL,rid,head_.id,head_.sequence::integer,NULL,marker,marker);
 -- Future awards have an empty term at their own start. Their cancellation is recorded now.
 FOR x IN SELECT p.*,g.id AS rule_id FROM public.core_circleparticipation p
 JOIN public.core_governedrule g ON g.rule_uuid=p.seat_rule_uuid
 WHERE target->'places' @> jsonb_build_array(p.id) ORDER BY p.id LOOP
 INSERT INTO public.core_circleparticipationend(participation_id,actor_state,actor_capacity,entry_mode,enterer_identity_id,
 composing_rule_state,composing_rule_id,composing_rule_reference,composer_state,composer_identity_id,composed_from_event_uuid,
 occurred_at,effective_at,received_at)
 VALUES(x.id,'NONE','NA_NO_PARTY','COMPOSED',a.actor_id,'REFERENCE',x.rule_id,marker,'REFERENCE',a.actor_id,rid,
 a.received_at,greatest(cut,x.effective_at),a.received_at);
 END LOOP;
 FOR id_ IN SELECT value::bigint FROM jsonb_array_elements_text(target->'offices') ORDER BY value::bigint LOOP
 SELECT e.* INTO STRICT h FROM public.core_contextualroleassignment ra
 JOIN public.core_contextualroleassignmentstateevent e ON e.id=ra.head_state_event_id AND e.assignment_id=ra.id
 WHERE ra.id=id_;
 SELECT greatest(cut,max(effective_at)) INTO end_at FROM public.core_contextualroleassignmentstateevent
 WHERE assignment_id=id_ AND action='ASSIGN';
 PERFORM public.s015_append_event('core_contextualroleassignment',id_,'END','ENDED',end_at,a.received_at,a.received_at,a.actor_id,
 lo.founding_authority_basis_id,ref,marker,marker||':office:'||id_,a.fingerprint,'{}',NULL,gen_random_uuid(),
 h.id,h.sequence::integer,NULL,marker,marker||':office:'||id_);
 PERFORM public.s015_refresh_governed_cache('core_contextualroleassignment',id_,cut,stamp);
 END LOOP;
 FOR w IN SELECT * FROM public.core_cmtcustody WHERE target->'work' ? receipt::text ORDER BY activity LOOP
 INSERT INTO public.core_cmtcustody(receipt,activity,mandate,predecessor,clause,cause,effective_at,recorded_at,board_circle,board_target)
 VALUES(gen_random_uuid(),w.activity,w.mandate,w.receipt,w.clause,jsonb_build_object('stand_down',rid,
 'custodian','LO_COORDINATORS','organism',lo.id),cut,stamp,w.board_circle,w.board_target);
 END LOOP;
 PERFORM public.s015_refresh_governed_cache('core_circle',circle_,cut,stamp);
END """
C108_APPLY_CLOCK = r"""
DECLARE a public.core_cqact; lo public.core_livingorganism; circle_ bigint; cut timestamptz; stamp timestamptz:=public.intevia_clock_statement();
 marker text; ref text; target jsonb; x record; head_ public.core_circlestateevent; h public.core_contextualroleassignmentstateevent;
 end_at timestamptz; rid uuid; id_ bigint; w public.core_cmtcustody;
BEGIN
 SELECT * INTO STRICT a FROM public.core_cqact WHERE act_uuid=p_act AND operation='IMPLEMENT'
 AND execution_xid=pg_current_xact_id() AND public.cq3_implement_source(core_cqact)=authority_source
 AND facts->'domain_plan'->>'domain'='BOARD_STAND_DOWN';
 SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE id=a.organism_id AND NOT platform_root FOR UPDATE;
 PERFORM public.s015_lock_organisms(ARRAY[lo.organism_id]);
 rid:=(a.request->>'result')::uuid; marker:='c108-stand-down:'||rid; cut:=a.effective_at;
 circle_:=(a.facts->'domain_plan'->>'circle_id')::bigint; target:=a.facts->'domain_plan'->'targets';
 IF pg_trigger_depth()<1 OR NOT public.c108_stand_down_authorised(rid,circle_,cut,stamp)
 OR target IS DISTINCT FROM public.c108_targets(lo.id,circle_,cut) THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 PERFORM id FROM public.core_circle WHERE id=circle_ FOR UPDATE;
 PERFORM id FROM public.core_circleparticipation WHERE target->'places' @> jsonb_build_array(id) ORDER BY id FOR UPDATE;
 PERFORM id FROM public.core_contextualroleassignment WHERE target->'offices' @> jsonb_build_array(id) ORDER BY id FOR UPDATE;
 PERFORM act.id FROM public.core_serviceactivity act JOIN public.core_cmtcustody c ON c.activity=act.id
 WHERE target->'work' ? c.receipt::text ORDER BY act.id FOR UPDATE OF act;
 SELECT * INTO STRICT head_ FROM public.core_circlestateevent WHERE circle_id=circle_ ORDER BY sequence DESC LIMIT 1;
 ref:=public.disc_decision(a.authority_source,a.fingerprint);
 PERFORM public.s015_append_event('core_circle',circle_,'STAND_DOWN','DORMANT',cut,a.received_at,a.received_at,a.actor_id,
 lo.founding_authority_basis_id,ref,marker,marker,a.fingerprint,'{}',NULL,rid,head_.id,head_.sequence::integer,NULL,marker,marker);
 -- Future awards have an empty term at their own start. Their cancellation is recorded now.
 FOR x IN SELECT p.*,g.id AS rule_id FROM public.core_circleparticipation p
 JOIN public.core_governedrule g ON g.rule_uuid=p.seat_rule_uuid
 WHERE target->'places' @> jsonb_build_array(p.id) ORDER BY p.id LOOP
 INSERT INTO public.core_circleparticipationend(participation_id,actor_state,actor_capacity,entry_mode,enterer_identity_id,
 composing_rule_state,composing_rule_id,composing_rule_reference,composer_state,composer_identity_id,composed_from_event_uuid,
 occurred_at,effective_at,received_at)
 VALUES(x.id,'NONE','NA_NO_PARTY','COMPOSED',a.actor_id,'REFERENCE',x.rule_id,marker,'REFERENCE',a.actor_id,rid,
 a.received_at,greatest(cut,x.effective_at),a.received_at);
 END LOOP;
 FOR id_ IN SELECT value::bigint FROM jsonb_array_elements_text(target->'offices') ORDER BY value::bigint LOOP
 SELECT e.* INTO STRICT h FROM public.core_contextualroleassignment ra
 JOIN public.core_contextualroleassignmentstateevent e ON e.id=ra.head_state_event_id AND e.assignment_id=ra.id
 WHERE ra.id=id_;
 SELECT greatest(cut,max(effective_at)) INTO end_at FROM public.core_contextualroleassignmentstateevent
 WHERE assignment_id=id_ AND action='ASSIGN';
 PERFORM public.s015_append_event('core_contextualroleassignment',id_,'END','ENDED',end_at,a.received_at,a.received_at,a.actor_id,
 lo.founding_authority_basis_id,ref,marker,marker||':office:'||id_,a.fingerprint,'{}',NULL,gen_random_uuid(),
 h.id,h.sequence::integer,NULL,marker,marker||':office:'||id_);
 PERFORM public.s015_refresh_governed_cache('core_contextualroleassignment',id_,cut,stamp);
 END LOOP;
 FOR w IN SELECT * FROM public.core_cmtcustody WHERE target->'work' ? receipt::text ORDER BY activity LOOP
 INSERT INTO public.core_cmtcustody(receipt,activity,mandate,predecessor,clause,cause,effective_at,recorded_at,board_circle,board_target)
 VALUES(gen_random_uuid(),w.activity,w.mandate,w.receipt,w.clause,jsonb_build_object('stand_down',rid,
 'custodian','LO_COORDINATORS','organism',lo.id),cut,stamp,w.board_circle,w.board_target);
 END LOOP;
 PERFORM public.s015_refresh_governed_cache('core_circle',circle_,cut,stamp);
END """

# public.te_limits_at(timestamp with time zone)
# Its landed text: 0091_te_engine_storage.py (assembled by that migration). Time calls moved: 1 statement.
TE_LIMITS_AT_LANDED = r"""
 SELECT r.body FROM public.core_governedrule r
 WHERE r.rule_uuid='7e11a1e0-0091-5c10-a105-0091e0000001'::uuid AND r.code='TIMED_EVENTS_LIMITS' AND r.scope_kind='PLATFORM'
   AND r.source='PLATFORM_RULING' AND r.effective_from<=p_at AND r.recorded_at<=statement_timestamp()
   AND r.body='{"consumers_per_request":1,"items_per_request":1,"minimum_probe_gap":null,"statement_budget":null}'::jsonb
 ORDER BY r.effective_from DESC,r.recorded_at DESC,r.version DESC,r.id DESC LIMIT 1
"""
TE_LIMITS_AT_CLOCK = r"""
 SELECT r.body FROM public.core_governedrule r
 WHERE r.rule_uuid='7e11a1e0-0091-5c10-a105-0091e0000001'::uuid AND r.code='TIMED_EVENTS_LIMITS' AND r.scope_kind='PLATFORM'
   AND r.source='PLATFORM_RULING' AND r.effective_from<=p_at AND r.recorded_at<=public.intevia_clock_statement()
   AND r.body='{"consumers_per_request":1,"items_per_request":1,"minimum_probe_gap":null,"statement_budget":null}'::jsonb
 ORDER BY r.effective_from DESC,r.recorded_at DESC,r.version DESC,r.id DESC LIMIT 1
"""

# public.te_receipt_guard()
# Its landed text: 0091_te_engine_storage.py (found there verbatim). Time calls moved: 1 wall.
TE_RECEIPT_GUARD_LANDED = r"""
DECLARE reg record; proof record; stamp timestamptz:=clock_timestamp();
BEGIN
 IF NEW.discovered_at IS NOT NULL THEN
    RAISE EXCEPTION 'TE_RECEIPT_TIME' USING ERRCODE='23514';
 END IF;
 SELECT r.* INTO reg FROM public.te_consumers() r
 WHERE r.consumer=NEW.consumer AND r.purpose=NEW.purpose;
 IF NOT FOUND THEN
    RAISE EXCEPTION 'TE_RECEIPT_CONSUMER' USING ERRCODE='23514';
 END IF;
 -- The rule is the actor. It is the registered rule of this purpose, never asserted.
 IF NEW.rule_ref IS DISTINCT FROM reg.rule THEN
    RAISE EXCEPTION 'TE_RECEIPT_RULE' USING ERRCODE='23514';
 END IF;
 -- The consumer's own validator proves that the cited effect is this source's
 -- effect in this LO, and supplies both times. A receipt cannot assert them.
 EXECUTE format('SELECT v.due_at,v.effect_at,v.enterer_id,v.made_here FROM public.%I($1,$2,$3,$4,$5) v',reg.validator)
   INTO proof USING NEW.organism_id,NEW.purpose,NEW.source_ref,NEW.occurrence,NEW.effect_ref;
 IF proof.due_at IS NULL OR proof.effect_at IS NULL
    OR proof.due_at IS DISTINCT FROM NEW.due_at OR proof.effect_at IS DISTINCT FROM NEW.effect_at THEN
    RAISE EXCEPTION 'TE_RECEIPT_EFFECT' USING ERRCODE='23514';
 END IF;
 -- OPENED means this transaction made the effect, and the receipt names the effect's own
 -- enterer. FOUND_DONE means an earlier transaction made it; its discoverer may differ.
 IF (NEW.outcome='OPENED') IS DISTINCT FROM proof.made_here THEN
    RAISE EXCEPTION 'TE_RECEIPT_ORIGIN' USING ERRCODE='23514';
 END IF;
 IF NEW.outcome='OPENED' AND NEW.enterer_id IS DISTINCT FROM proof.enterer_id THEN
    RAISE EXCEPTION 'TE_RECEIPT_ENTERER' USING ERRCODE='23514';
 END IF;
 -- The receipt is always written after its effect, so the database clock at this
 -- point is never earlier than the effect's own recorded time.
 IF NEW.effect_at>stamp THEN
    RAISE EXCEPTION 'TE_RECEIPT_TIME' USING ERRCODE='23514';
 END IF;
 NEW.discovered_at:=stamp;
 RETURN NEW;
END """
TE_RECEIPT_GUARD_CLOCK = r"""
DECLARE reg record; proof record; stamp timestamptz:=public.intevia_clock_instant();
BEGIN
 IF NEW.discovered_at IS NOT NULL THEN
    RAISE EXCEPTION 'TE_RECEIPT_TIME' USING ERRCODE='23514';
 END IF;
 SELECT r.* INTO reg FROM public.te_consumers() r
 WHERE r.consumer=NEW.consumer AND r.purpose=NEW.purpose;
 IF NOT FOUND THEN
    RAISE EXCEPTION 'TE_RECEIPT_CONSUMER' USING ERRCODE='23514';
 END IF;
 -- The rule is the actor. It is the registered rule of this purpose, never asserted.
 IF NEW.rule_ref IS DISTINCT FROM reg.rule THEN
    RAISE EXCEPTION 'TE_RECEIPT_RULE' USING ERRCODE='23514';
 END IF;
 -- The consumer's own validator proves that the cited effect is this source's
 -- effect in this LO, and supplies both times. A receipt cannot assert them.
 EXECUTE format('SELECT v.due_at,v.effect_at,v.enterer_id,v.made_here FROM public.%I($1,$2,$3,$4,$5) v',reg.validator)
   INTO proof USING NEW.organism_id,NEW.purpose,NEW.source_ref,NEW.occurrence,NEW.effect_ref;
 IF proof.due_at IS NULL OR proof.effect_at IS NULL
    OR proof.due_at IS DISTINCT FROM NEW.due_at OR proof.effect_at IS DISTINCT FROM NEW.effect_at THEN
    RAISE EXCEPTION 'TE_RECEIPT_EFFECT' USING ERRCODE='23514';
 END IF;
 -- OPENED means this transaction made the effect, and the receipt names the effect's own
 -- enterer. FOUND_DONE means an earlier transaction made it; its discoverer may differ.
 IF (NEW.outcome='OPENED') IS DISTINCT FROM proof.made_here THEN
    RAISE EXCEPTION 'TE_RECEIPT_ORIGIN' USING ERRCODE='23514';
 END IF;
 IF NEW.outcome='OPENED' AND NEW.enterer_id IS DISTINCT FROM proof.enterer_id THEN
    RAISE EXCEPTION 'TE_RECEIPT_ENTERER' USING ERRCODE='23514';
 END IF;
 -- The receipt is always written after its effect, so the database clock at this
 -- point is never earlier than the effect's own recorded time.
 IF NEW.effect_at>stamp THEN
    RAISE EXCEPTION 'TE_RECEIPT_TIME' USING ERRCODE='23514';
 END IF;
 NEW.discovered_at:=stamp;
 RETURN NEW;
END """

# public.c2_source_change()
# Its landed text: 0092_te_elect_joins.py (assembled by that migration). Time calls moved: 1 statement.
C2_SOURCE_CHANGE_LANDED = r"""
DECLARE lo_id bigint; lo public.core_livingorganism; c public.core_essentialcoveragecase;
 v integer; cut timestamptz; known timestamptz:=statement_timestamp(); origin text; outcome text;
 current_lo text; assessment bigint; already boolean; vacant boolean; party bigint; office text;
BEGIN
 IF TG_TABLE_NAME='core_organismmembershiptransition' THEN
    SELECT living_organism_id INTO lo_id FROM public.core_organismmembership WHERE id=NEW.membership_id;
    -- A proposal alone cannot change the number of active members holding offices.
    IF NEW.resulting_state='PROPOSED' AND NEW.prior_state IS DISTINCT FROM 'ACTIVE' THEN RETURN NULL; END IF;
    origin:='membership-ending:'||NEW.event_uuid;
    IF NEW.resulting_state<>'ENDED' THEN origin:='membership:'||NEW.event_uuid; END IF;
 ELSE
    IF NEW.action='PROPOSE' THEN RETURN NULL; END IF;
    SELECT m.living_organism_id,r.code INTO lo_id,office FROM public.core_contextualroleassignment a
    JOIN public.core_organismmembership m ON m.id=a.membership_id JOIN public.core_organismroledefinition r
    ON r.id=a.role_definition_id WHERE a.id=NEW.assignment_id;
    origin:='office:'||NEW.event_uuid;
 END IF;
 SELECT * INTO lo FROM public.core_livingorganism WHERE id=lo_id;
 IF lo.platform_root THEN RETURN NULL; END IF;
 IF lo.founding_insert_xid=pg_current_xact_id() THEN
    IF TG_TABLE_NAME='core_contextualroleassignmentstateevent' THEN PERFORM public.c2_future_cuts(lo_id); END IF;
    RETURN NULL; END IF;
 SELECT * INTO c FROM public.core_essentialcoveragecase WHERE living_organism_id=lo_id;
 IF c.id IS NULL THEN RAISE EXCEPTION 'C2 missing founded coverage case' USING ERRCODE='23514'; END IF;
 cut:=NEW.effective_at; v:=public.c2_roster_version(lo_id,cut,known);
 IF office IS NOT NULL AND NOT EXISTS(SELECT FROM public.c2_rules(lo_id,v,cut,known) r WHERE r.qualifying_role_code=office)
 THEN RETURN NULL; END IF;
 current_lo:=public.s015_fold_effective_state('core_livingorganism',lo_id,cut,known);
 outcome:=public.c2_result(lo_id,v,cut,known);
 SELECT EXISTS(SELECT FROM public.core_coverageassessment a WHERE a.case_id=c.id AND a.xmin=pg_current_xact_id()::xid
    AND a.effective_at=cut AND a.result=outcome AND a.evaluated_known_at>=NEW.recorded_at
    AND public.c2_result(lo_id,v,cut,known,true)=public.c2_result(lo_id,a.roster_version,cut,a.evaluated_known_at,true)) INTO already;
 IF NOT already THEN
    assessment:=public.c2_publish(lo_id,cut,NEW.occurred_at,NEW.received_at,NEW.enterer_identity_id,
    NEW.authority_basis_id,NEW.authority_decision_reference,origin,'c2:'||NEW.event_uuid,NEW.payload_fingerprint);
 END IF;
 PERFORM public.c2_future_cuts(lo_id);
 IF current_lo IN ('FOUNDING_PENDING','CLOSED') OR outcome='SATISFIED' THEN RETURN NULL; END IF;
 vacant:=public.c2_is_coordinator_vacancy(lo_id,v,cut,known);
 IF cut<=known AND current_lo='ACTIVE' AND vacant AND EXISTS(SELECT FROM public.core_organismmembership m WHERE m.living_organism_id=lo_id
     AND public.s015_fold_effective_state('core_organismmembership',m.id,cut,known) IN ('ACTIVE','PROBATIONARY')) THEN
    -- Only an actual disappearance of the last incumbent starts this timer. Routine
    -- membership traffic cannot create or restart an episode on a legacy vacancy.
    IF NOT EXISTS(SELECT FROM public.c2_occupants(lo_id,cut,NEW.recorded_at-interval '1 microsecond') o
       WHERE role_scope='LIVING_ORGANISM' AND role_code='COORDINATOR') THEN RETURN NULL; END IF;
    PERFORM public.s015_append_event('core_livingorganism',lo_id,'WITHOUT_COORDINATOR','RESTRICTED_CONTINUITY',cut,
      NEW.occurred_at,NEW.received_at,NEW.enterer_identity_id,NEW.authority_basis_id,NEW.authority_decision_reference,
      origin,'c2:'||NEW.event_uuid,NEW.payload_fingerprint,'{}',cut+interval '720 hours');
 END IF;
 RETURN NULL;
END """
C2_SOURCE_CHANGE_CLOCK = r"""
DECLARE lo_id bigint; lo public.core_livingorganism; c public.core_essentialcoveragecase;
 v integer; cut timestamptz; known timestamptz:=public.intevia_clock_statement(); origin text; outcome text;
 current_lo text; assessment bigint; already boolean; vacant boolean; party bigint; office text;
BEGIN
 IF TG_TABLE_NAME='core_organismmembershiptransition' THEN
    SELECT living_organism_id INTO lo_id FROM public.core_organismmembership WHERE id=NEW.membership_id;
    -- A proposal alone cannot change the number of active members holding offices.
    IF NEW.resulting_state='PROPOSED' AND NEW.prior_state IS DISTINCT FROM 'ACTIVE' THEN RETURN NULL; END IF;
    origin:='membership-ending:'||NEW.event_uuid;
    IF NEW.resulting_state<>'ENDED' THEN origin:='membership:'||NEW.event_uuid; END IF;
 ELSE
    IF NEW.action='PROPOSE' THEN RETURN NULL; END IF;
    SELECT m.living_organism_id,r.code INTO lo_id,office FROM public.core_contextualroleassignment a
    JOIN public.core_organismmembership m ON m.id=a.membership_id JOIN public.core_organismroledefinition r
    ON r.id=a.role_definition_id WHERE a.id=NEW.assignment_id;
    origin:='office:'||NEW.event_uuid;
 END IF;
 SELECT * INTO lo FROM public.core_livingorganism WHERE id=lo_id;
 IF lo.platform_root THEN RETURN NULL; END IF;
 IF lo.founding_insert_xid=pg_current_xact_id() THEN
    IF TG_TABLE_NAME='core_contextualroleassignmentstateevent' THEN PERFORM public.c2_future_cuts(lo_id); END IF;
    RETURN NULL; END IF;
 SELECT * INTO c FROM public.core_essentialcoveragecase WHERE living_organism_id=lo_id;
 IF c.id IS NULL THEN RAISE EXCEPTION 'C2 missing founded coverage case' USING ERRCODE='23514'; END IF;
 cut:=NEW.effective_at; v:=public.c2_roster_version(lo_id,cut,known);
 IF office IS NOT NULL AND NOT EXISTS(SELECT FROM public.c2_rules(lo_id,v,cut,known) r WHERE r.qualifying_role_code=office)
 THEN RETURN NULL; END IF;
 current_lo:=public.s015_fold_effective_state('core_livingorganism',lo_id,cut,known);
 outcome:=public.c2_result(lo_id,v,cut,known);
 SELECT EXISTS(SELECT FROM public.core_coverageassessment a WHERE a.case_id=c.id AND a.xmin=pg_current_xact_id()::xid
    AND a.effective_at=cut AND a.result=outcome AND a.evaluated_known_at>=NEW.recorded_at
    AND public.c2_result(lo_id,v,cut,known,true)=public.c2_result(lo_id,a.roster_version,cut,a.evaluated_known_at,true)) INTO already;
 IF NOT already THEN
    assessment:=public.c2_publish(lo_id,cut,NEW.occurred_at,NEW.received_at,NEW.enterer_identity_id,
    NEW.authority_basis_id,NEW.authority_decision_reference,origin,'c2:'||NEW.event_uuid,NEW.payload_fingerprint);
 END IF;
 PERFORM public.c2_future_cuts(lo_id);
 IF current_lo IN ('FOUNDING_PENDING','CLOSED') OR outcome='SATISFIED' THEN RETURN NULL; END IF;
 vacant:=public.c2_is_coordinator_vacancy(lo_id,v,cut,known);
 IF cut<=known AND current_lo='ACTIVE' AND vacant AND EXISTS(SELECT FROM public.core_organismmembership m WHERE m.living_organism_id=lo_id
     AND public.s015_fold_effective_state('core_organismmembership',m.id,cut,known) IN ('ACTIVE','PROBATIONARY')) THEN
    -- Only an actual disappearance of the last incumbent starts this timer. Routine
    -- membership traffic cannot create or restart an episode on a legacy vacancy.
    IF NOT EXISTS(SELECT FROM public.c2_occupants(lo_id,cut,NEW.recorded_at-interval '1 microsecond') o
       WHERE role_scope='LIVING_ORGANISM' AND role_code='COORDINATOR') THEN RETURN NULL; END IF;
    PERFORM public.s015_append_event('core_livingorganism',lo_id,'WITHOUT_COORDINATOR','RESTRICTED_CONTINUITY',cut,
      NEW.occurred_at,NEW.received_at,NEW.enterer_identity_id,NEW.authority_basis_id,NEW.authority_decision_reference,
      origin,'c2:'||NEW.event_uuid,NEW.payload_fingerprint,'{}',cut+interval '720 hours');
 END IF;
 RETURN NULL;
END """

# public.elect_cancel_pending_office(bigint, uuid)
# Its landed text: 0092_te_elect_joins.py (found there verbatim). Time calls moved: 1 statement.
ELECT_CANCEL_PENDING_OFFICE_LANDED = r"""
DECLARE t public.core_organismmembershiptransition; h public.core_contextualroleassignmentstateevent;
 key_ text;
BEGIN
 SELECT t_.* INTO STRICT t FROM public.core_organismmembershiptransition t_
 JOIN public.core_contextualroleassignment a ON a.membership_id=t_.membership_id
 WHERE a.id=p_assignment AND t_.event_uuid=p_departure AND t_.resulting_state='ENDED';
 SELECT * INTO STRICT h FROM public.core_contextualroleassignmentstateevent
 WHERE assignment_id=p_assignment ORDER BY sequence DESC LIMIT 1;
 IF h.action<>'ASSIGN' OR NOT public.elect_pending_successor(h.event_uuid,t.membership_id,'OFFICE',t.effective_at,t.received_at)
 THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
 key_:='elect-departure:'||t.event_uuid||':'||h.event_uuid;
 PERFORM public.s015_append_event('core_contextualroleassignment',p_assignment,'END','ENDED',h.effective_at,
    t.received_at,t.received_at,t.enterer_identity_id,t.authority_basis_id,t.authority_decision_reference,key_,key_,
    t.payload_fingerprint,'{}',NULL,gen_random_uuid(),h.id,h.sequence::integer,NULL,'ELECT:SUCCESSOR_LEFT',key_);
 PERFORM public.s015_refresh_governed_cache('core_contextualroleassignment',p_assignment,t.effective_at,statement_timestamp());
END """
ELECT_CANCEL_PENDING_OFFICE_CLOCK = r"""
DECLARE t public.core_organismmembershiptransition; h public.core_contextualroleassignmentstateevent;
 key_ text;
BEGIN
 SELECT t_.* INTO STRICT t FROM public.core_organismmembershiptransition t_
 JOIN public.core_contextualroleassignment a ON a.membership_id=t_.membership_id
 WHERE a.id=p_assignment AND t_.event_uuid=p_departure AND t_.resulting_state='ENDED';
 SELECT * INTO STRICT h FROM public.core_contextualroleassignmentstateevent
 WHERE assignment_id=p_assignment ORDER BY sequence DESC LIMIT 1;
 IF h.action<>'ASSIGN' OR NOT public.elect_pending_successor(h.event_uuid,t.membership_id,'OFFICE',t.effective_at,t.received_at)
 THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
 key_:='elect-departure:'||t.event_uuid||':'||h.event_uuid;
 PERFORM public.s015_append_event('core_contextualroleassignment',p_assignment,'END','ENDED',h.effective_at,
    t.received_at,t.received_at,t.enterer_identity_id,t.authority_basis_id,t.authority_decision_reference,key_,key_,
    t.payload_fingerprint,'{}',NULL,gen_random_uuid(),h.id,h.sequence::integer,NULL,'ELECT:SUCCESSOR_LEFT',key_);
 PERFORM public.s015_refresh_governed_cache('core_contextualroleassignment',p_assignment,t.effective_at,public.intevia_clock_statement());
END """

# public.elect_record_term_end(bigint, bigint, bigint)
# Its landed text: 0092_te_elect_joins.py (assembled by that migration). Time calls moved: 1 statement.
ELECT_RECORD_TERM_END_LANDED = r"""
DECLARE src public.core_contextualroleassignmentstateevent; head_ public.core_contextualroleassignmentstateevent;
 boundary_ timestamptz; cut_ timestamptz:=statement_timestamp(); key_ text;
BEGIN
 PERFORM 1 FROM public.core_livingorganism WHERE id=p_lo FOR UPDATE;
 IF (public.disc_member_state(p_lo,p_enterer,cut_) IS DISTINCT FROM 'ACTIVE'
    AND NOT public.elect_notice_enterer(p_lo,p_id,p_enterer,cut_)) OR NOT EXISTS(
    SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=p_enterer AND i.access_state='active' AND u.is_active)
 THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 PERFORM 1 FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
 WHERE a.id=p_id AND d.living_organism_id=p_lo FOR UPDATE OF a;
 IF NOT FOUND THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 SELECT e.* INTO src FROM public.elect_due_office(p_id,cut_) q
 JOIN public.core_contextualroleassignmentstateevent e ON e.id=q.source_id;
 IF NOT FOUND THEN RETURN false; END IF;
 boundary_:=public.elect_office_deadline(src.id,cut_); key_:='elect-expiry:'||src.event_uuid;
 SELECT * INTO STRICT head_ FROM public.core_contextualroleassignmentstateevent
 WHERE assignment_id=p_id ORDER BY sequence DESC LIMIT 1;
 PERFORM public.s015_append_event('core_contextualroleassignment',p_id,'END','ENDED',boundary_,boundary_,cut_,
   p_enterer,src.authority_basis_id,src.authority_decision_reference,key_,key_,public.c2_target(key_),'{}',
   NULL,gen_random_uuid(),head_.id,head_.sequence::integer,NULL,'ELECT:TERM_END',key_);
 PERFORM public.s015_refresh_governed_cache('core_contextualroleassignment',p_id,cut_,cut_);
 RETURN true;
END """
ELECT_RECORD_TERM_END_CLOCK = r"""
DECLARE src public.core_contextualroleassignmentstateevent; head_ public.core_contextualroleassignmentstateevent;
 boundary_ timestamptz; cut_ timestamptz:=public.intevia_clock_statement(); key_ text;
BEGIN
 PERFORM 1 FROM public.core_livingorganism WHERE id=p_lo FOR UPDATE;
 IF (public.disc_member_state(p_lo,p_enterer,cut_) IS DISTINCT FROM 'ACTIVE'
    AND NOT public.elect_notice_enterer(p_lo,p_id,p_enterer,cut_)) OR NOT EXISTS(
    SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=p_enterer AND i.access_state='active' AND u.is_active)
 THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 PERFORM 1 FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
 WHERE a.id=p_id AND d.living_organism_id=p_lo FOR UPDATE OF a;
 IF NOT FOUND THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 SELECT e.* INTO src FROM public.elect_due_office(p_id,cut_) q
 JOIN public.core_contextualroleassignmentstateevent e ON e.id=q.source_id;
 IF NOT FOUND THEN RETURN false; END IF;
 boundary_:=public.elect_office_deadline(src.id,cut_); key_:='elect-expiry:'||src.event_uuid;
 SELECT * INTO STRICT head_ FROM public.core_contextualroleassignmentstateevent
 WHERE assignment_id=p_id ORDER BY sequence DESC LIMIT 1;
 PERFORM public.s015_append_event('core_contextualroleassignment',p_id,'END','ENDED',boundary_,boundary_,cut_,
   p_enterer,src.authority_basis_id,src.authority_decision_reference,key_,key_,public.c2_target(key_),'{}',
   NULL,gen_random_uuid(),head_.id,head_.sequence::integer,NULL,'ELECT:TERM_END',key_);
 PERFORM public.s015_refresh_governed_cache('core_contextualroleassignment',p_id,cut_,cut_);
 RETURN true;
END """

# public.elect_resignation_coverage(bigint, uuid, bigint)
# Its landed text: 0092_te_elect_joins.py (found there verbatim). Time calls moved: 1 statement.
ELECT_RESIGNATION_COVERAGE_LANDED = r"""
DECLARE receipt public.core_cqact; due_ timestamptz; cut_ timestamptz:=statement_timestamp(); key_ text;
 basis_ bigint;
BEGIN
 SELECT lo.founding_authority_basis_id INTO basis_ FROM public.core_livingorganism lo WHERE lo.id=p_lo AND NOT lo.platform_root FOR UPDATE;
 IF basis_ IS NULL OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=p_enterer AND i.access_state='active' AND u.is_active) THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 SELECT a.* INTO receipt FROM public.core_cqact a JOIN public.core_circleparticipationend x ON x.composed_from_event_uuid=a.act_uuid
   WHERE a.act_uuid=p_act AND a.operation='BOARD_RESIGN' AND a.organism_id=p_lo
   AND x.composing_rule_reference='elect-resign:'||a.act_uuid AND x.effective_at<=cut_;
 IF NOT FOUND THEN RETURN false; END IF;
 IF public.disc_member_state(p_lo,p_enterer,cut_) IS DISTINCT FROM 'ACTIVE' AND NOT
   (receipt.execution_xid=pg_current_xact_id() AND receipt.actor_id=p_enterer)
 THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 due_:=(receipt.facts->>'end')::timestamptz; key_:='elect-resignation:'||p_act;
 IF EXISTS(SELECT FROM public.core_coverageassessment e JOIN public.core_essentialcoveragecase c ON c.id=e.case_id
    WHERE c.living_organism_id=p_lo AND e.evidence_reference=key_) THEN RETURN false; END IF;
 PERFORM public.c2_publish(p_lo,due_,due_,cut_,p_enterer,basis_,public.disc_decision(receipt.authority_source,receipt.fingerprint),
    key_,key_,receipt.fingerprint);
 RETURN true;
END """
ELECT_RESIGNATION_COVERAGE_CLOCK = r"""
DECLARE receipt public.core_cqact; due_ timestamptz; cut_ timestamptz:=public.intevia_clock_statement(); key_ text;
 basis_ bigint;
BEGIN
 SELECT lo.founding_authority_basis_id INTO basis_ FROM public.core_livingorganism lo WHERE lo.id=p_lo AND NOT lo.platform_root FOR UPDATE;
 IF basis_ IS NULL OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=p_enterer AND i.access_state='active' AND u.is_active) THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 SELECT a.* INTO receipt FROM public.core_cqact a JOIN public.core_circleparticipationend x ON x.composed_from_event_uuid=a.act_uuid
   WHERE a.act_uuid=p_act AND a.operation='BOARD_RESIGN' AND a.organism_id=p_lo
   AND x.composing_rule_reference='elect-resign:'||a.act_uuid AND x.effective_at<=cut_;
 IF NOT FOUND THEN RETURN false; END IF;
 IF public.disc_member_state(p_lo,p_enterer,cut_) IS DISTINCT FROM 'ACTIVE' AND NOT
   (receipt.execution_xid=pg_current_xact_id() AND receipt.actor_id=p_enterer)
 THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 due_:=(receipt.facts->>'end')::timestamptz; key_:='elect-resignation:'||p_act;
 IF EXISTS(SELECT FROM public.core_coverageassessment e JOIN public.core_essentialcoveragecase c ON c.id=e.case_id
    WHERE c.living_organism_id=p_lo AND e.evidence_reference=key_) THEN RETURN false; END IF;
 PERFORM public.c2_publish(p_lo,due_,due_,cut_,p_enterer,basis_,public.disc_decision(receipt.authority_source,receipt.fingerprint),
    key_,key_,receipt.fingerprint);
 RETURN true;
END """

# public.s015_append_event(text, bigint, text, text, timestamp with time zone, timestamp with time zone, timestamp with time zone, bigint, bigint, text, text, text, text, jsonb, timestamp with time zone, uuid, bigint, integer, bigint, text, text)
# Its landed text: 0092_te_elect_joins.py (assembled by that migration). Time calls moved: 6 statement, 2 wall.
S015_APPEND_EVENT_LANDED = r"""
DECLARE
 is_command boolean := command_event IS NOT NULL;
 event uuid := coalesce(command_event,gen_random_uuid());
 table_name text; fk text; col text; previous_id bigint; previous_seq integer; previous_l1 text; prior text;
 rule_ref text; lineage text; values_ jsonb; cols text; id_ bigint;
BEGIN
 SELECT m.event_table,m.fk_col,m.state_col INTO STRICT table_name,fk,col
 FROM public.s015_anchor_map() m WHERE m.anchor_token=token AND (
    (is_command AND token IN ('core_organismmembership','core_essentialcoveragecase','core_circle','core_contextualroleassignment','core_livingorganism','core_membershipcondition','core_authoritybasis','core_determinationcontestcase'))
 OR (NOT is_command AND token IN ('core_livingorganism','core_essentialcoveragecase','core_restrictedcontinuitycase')));

 IF is_command THEN
    -- Keep the caller's checked predecessor, even for a stale/empty head.
    -- The installed chain guards still decide whether that continuation is lawful.
    previous_id:=supplied_head; previous_seq:=supplied_sequence;
    IF supplied_head IS NOT NULL THEN
       EXECUTE format('SELECT l1_commitment FROM public.%I WHERE id=$1',table_name)
       INTO STRICT previous_l1 USING supplied_head;
    END IF;
    rule_ref:=command_rule;
    lineage:=public.c2_target(format('{"event":"%s","request":%s}',event,to_json(payload)));
    IF EXISTS (SELECT FROM jsonb_object_keys(metadata) k
               WHERE NOT (
       (token='core_authoritybasis' AND k='kind') OR
       (token='core_organismmembership' AND k='reason_class') OR
       (token='core_essentialcoveragecase' AND k IN ('roster_version','evaluated_state_at','evaluated_known_at','determiner_capacity_reference')))) THEN
       RAISE EXCEPTION 'Unexpected command chain metadata'; END IF;
 ELSE
    EXECUTE format('SELECT id,sequence,l1_commitment FROM public.%I WHERE %I=$1 ORDER BY sequence DESC LIMIT 1',table_name,fk)
    INTO previous_id,previous_seq,previous_l1 USING anchor;
    rule_ref:='C0:G2:C2';
    lineage:=encode(sha256(convert_to(event::text||':'||payload,'UTF8')),'hex');
    IF EXISTS(SELECT FROM jsonb_object_keys(metadata) k WHERE k NOT IN
       ('roster_version','evaluated_state_at','evaluated_known_at','determiner_capacity_reference',
        'permitted_measures_reference','prohibited_effects_reference','triggering_assessment_id','accountable_actor_id')) THEN
       RAISE EXCEPTION 'C2 chain metadata is invalid'; END IF;
 END IF;
 IF NOT is_command OR supplied_head IS NOT NULL THEN
    EXECUTE format('SELECT %I FROM public.%I WHERE %I=$1 AND (effective_at,occurred_at,received_at)<=($2,$3,$4)
        ORDER BY effective_at DESC,occurred_at DESC,received_at DESC,recorded_at DESC,sequence DESC,event_uuid DESC LIMIT 1',
        col,table_name,fk) INTO prior USING anchor,at_time,occurred,received;
 END IF;


 IF token = 'core_circle' AND action_name='CREATE_DORMANT' THEN
    IF action_name IS DISTINCT FROM 'CREATE_DORMANT' OR outcome IS DISTINCT FROM 'DORMANT'
       OR supplied_head IS NOT NULL OR supplied_sequence IS NOT NULL OR command_rule IS NOT NULL
       OR access_epoch IS NOT NULL OR deadline IS NOT NULL OR actor IS NULL THEN
       RAISE EXCEPTION 'Invalid institutional Circle creation envelope';
    END IF;
    SELECT encode(sha256(convert_to(format(
      '{"action":"CREATE_DORMANT_CIRCLE","event_uuid":"%s","fingerprint":"%s","subject_uuid":"%s"}',
      event,payload,c.circle_uuid),'UTF8')),'hex') INTO STRICT lineage
    FROM public.core_circle c WHERE c.id=anchor;
 END IF;


 IF is_command AND token IN ('core_organismmembership','core_essentialcoveragecase') AND (
    command_rule IS NOT NULL OR NOT EXISTS(SELECT FROM public.core_rootgenesis g JOIN public.core_livingorganism lo
     ON lo.organism_id=g.organism_uuid WHERE g.insert_xid=pg_current_xact_id() AND g.decision_reference=decision
     AND g.founder_id=actor AND g.basis_id=basis AND g.request_fingerprint=payload AND g.effective_at=at_time
     AND ((token='core_organismmembership' AND EXISTS(SELECT FROM public.core_organismmembership m
            WHERE m.id=anchor AND m.living_organism_id=lo.id AND m.identity_id=actor)
            AND action_name IN ('OPEN','ACTIVATE') AND metadata->>'reason_class'='FOUNDING')
       OR (token='core_essentialcoveragecase' AND EXISTS(SELECT FROM public.core_essentialcoveragecase c
            WHERE c.id=anchor AND c.living_organism_id=lo.id) AND action_name='ASSESS' AND outcome='SATISFIED')))) THEN
    RAISE EXCEPTION 'R_GENESIS_WRITER_SOURCE'; END IF;

 IF token='core_contextualroleassignment' AND action_name='ASSIGN'
    AND public.elect_recognition_office(anchor,command_event,actor,decision,at_time,deadline) THEN
    IF command_rule IS NOT NULL OR outcome IS DISTINCT FROM 'ACTIVE' OR evidence IS DISTINCT FROM 'elect-recognised:'||event
      THEN PERFORM public.cq_refuse('ELECT_RECOGNISER'); END IF;
 ELSIF token='core_contextualroleassignment' AND action_name='ASSIGN'
    AND public.elect_office_source(anchor,command_event,actor,decision,at_time,deadline) THEN
    IF command_rule IS NOT NULL OR outcome<>'ACTIVE' OR evidence NOT LIKE 'elect-result:%' THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
 ELSIF token='core_contextualroleassignment' AND action_name='ASSIGN' AND EXISTS(
    SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
    WHERE a.id=anchor AND a.circle_id IS NULL AND d.scope='LIVING_ORGANISM'
    AND public.r_genesis_context(d.living_organism_id,decision,actor,at_time)) THEN
    IF command_rule IS NOT NULL OR access_epoch IS NULL OR deadline IS NOT NULL THEN
       RAISE EXCEPTION 'R_GENESIS_WRITER_OFFICE'; END IF;
 ELSIF token='core_contextualroleassignment' AND action_name IN ('PROPOSE','ASSIGN') THEN
    IF command_rule IS NOT NULL OR NOT EXISTS (
       SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d
       ON d.id=a.role_definition_id WHERE a.id=anchor AND a.circle_id IS NOT NULL
       AND d.scope='CIRCLE' AND d.code='COORDINATOR') THEN
       RAISE EXCEPTION 'Invalid Circle Coordinator envelope';
    END IF;
 ELSIF token='core_contextualroleassignment' AND starts_with(command_rule,'c108-stand-down:') THEN
    IF NOT is_command OR action_name IS DISTINCT FROM 'END' OR outcome IS DISTINCT FROM 'ENDED'
    OR evidence IS DISTINCT FROM command_rule OR NOT public.c108_result_child(evidence,'offices',anchor)
    THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;

    IF EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent h
      WHERE h.id=supplied_head AND h.assignment_id=anchor AND h.effective_at>at_time AND h.resulting_state='ENDED') THEN
      SELECT public.s015_reduce_projection(token,coalesce(array_agg(e.id ORDER BY e.effective_at,e.occurred_at,
        e.received_at,e.recorded_at,e.sequence,e.event_uuid),'{}'::bigint[])) INTO prior
      FROM public.core_contextualroleassignmentstateevent e
      WHERE e.assignment_id=anchor AND e.sequence<=supplied_sequence
        AND (e.effective_at,e.occurred_at,e.received_at)<=(at_time,occurred,received);
    END IF;
 ELSIF token='core_contextualroleassignment' AND command_rule='ELECT:SUCCESSOR_LEFT' THEN
    -- The assignment input guard independently binds the complete row to the
    -- exact departure and predecessor. Other uses of this envelope refuse.
    IF NOT is_command OR action_name IS DISTINCT FROM 'END' OR outcome IS DISTINCT FROM 'ENDED'
      THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
 ELSIF token='core_contextualroleassignment' AND command_rule='ELECT:TERM_END' THEN
    IF NOT is_command OR action_name IS DISTINCT FROM 'END' OR outcome IS DISTINCT FROM 'ENDED'
       OR NOT EXISTS(SELECT FROM public.elect_due_office(anchor,statement_timestamp()) q WHERE q.due=at_time)
    THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
 ELSIF token='core_contextualroleassignment' AND (
     action_name IS DISTINCT FROM 'END' OR outcome IS DISTINCT FROM 'ENDED' OR command_rule IS NULL
     OR command_rule NOT IN ('CIRCLES:G2:U3','CIRCLES:G2:SELF_LEAVE')) THEN
    RAISE EXCEPTION 'Unsupported scoped office envelope';
 END IF;
 IF token='core_circle' AND action_name NOT IN ('CREATE_DORMANT','LEAVE_CIRCLE','OPEN_WORK','CLAIM_WORK','ACTIVATE','DISSOLVE_CIRCLE','STAND_DOWN') THEN
    RAISE EXCEPTION 'Unsupported Circle envelope';
 END IF;
 values_:=jsonb_build_object(fk,anchor,'event_uuid',event,'sequence',coalesce(previous_seq,0)+1,
    'predecessor_id',previous_id,'predecessor_sequence',previous_seq,'action',action_name,'prior_state',prior,col,outcome,
    'actor_state',CASE WHEN rule_ref IS NULL THEN 'PARTY' ELSE 'NONE' END,
    'actor_id',CASE WHEN rule_ref IS NULL THEN actor END,
    'actor_access_epoch',CASE WHEN rule_ref IS NULL THEN access_epoch END,
    'actor_capacity',CASE WHEN rule_ref IS NULL THEN 'STANDING' ELSE 'NA_NO_PARTY' END,
    'enterer_identity_id',actor,'entry_mode',CASE WHEN rule_ref IS NULL THEN 'TRANSPORT' ELSE 'COMPOSED' END,
    'composing_rule_state',CASE WHEN rule_ref IS NULL THEN 'NA_TRANSPORT' ELSE 'REFERENCE' END,
    'composing_rule_reference',rule_ref,'composer_state',CASE WHEN rule_ref IS NULL THEN 'NA_TRANSPORT' ELSE 'REFERENCE' END,
    'composer_identity_id',CASE WHEN rule_ref IS NOT NULL THEN actor END,
    'effective_until_state',CASE WHEN deadline IS NULL THEN 'NONE' ELSE 'FIXED' END,'effective_until',deadline,
    'authority_basis_id',basis,'authority_decision_reference',decision,'evidence_reference',evidence,
    'request_reference',request_key,'idempotency_key',CASE WHEN is_command THEN command_key ELSE request_key END,
    'payload_fingerprint',payload,'lineage_reference','s015l1:'||lineage,
    'temporal_basis_kind',CASE WHEN at_time<received THEN 'RETROSPECTIVE' WHEN at_time>received THEN 'PROSPECTIVE' END,
    'temporal_basis_reference',CASE WHEN at_time<>received THEN 's015tb1:'||payload END,
    'effective_at',at_time,'occurred_at',occurred,'received_at',received,'predecessor_l1_commitment',previous_l1,
    'parts_commitment',public.s015_0022_digest('s015p1',public.s015_0022_parts_preimage(table_name,event)))||metadata;

 IF token = 'core_circle' AND action_name='CREATE_DORMANT' THEN
    values_ := values_ || jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL);
 END IF;
 IF token='core_contextualroleassignment' THEN
    IF action_name='ASSIGN' AND EXISTS(SELECT FROM public.core_contextualroleassignment a WHERE a.id=anchor AND a.circle_id IS NOT NULL) THEN
       values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
    ELSIF action_name='PROPOSE' THEN
       values_:=values_||jsonb_build_object('effective_until_state','NOT_ESTABLISHED');
    END IF;
 END IF;

 IF token='core_circle' AND action_name='ACTIVATE' THEN
    IF command_rule IS NOT NULL OR access_epoch IS NOT NULL OR deadline IS NOT NULL THEN
       RAISE EXCEPTION 'Invalid institutional activation envelope'; END IF;
    values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF is_command AND token='core_livingorganism' AND action_name='ADOPT_LAW' THEN
   IF command_rule IS NOT NULL OR deadline IS NOT NULL OR at_time IS DISTINCT FROM statement_timestamp()
     OR basis IS NULL OR actor IS NULL OR prior IS DISTINCT FROM outcome THEN
     RAISE EXCEPTION 'LAW requires same-state institutional transport at execution' USING ERRCODE='23514';
   END IF;
   values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF is_command AND token='core_livingorganism' AND action_name='RECORD_GUIDING_INTENTION' THEN
   IF command_rule IS NOT NULL OR deadline IS NOT NULL OR at_time IS DISTINCT FROM statement_timestamp()
     OR basis IS NULL OR actor IS NULL OR prior IS DISTINCT FROM outcome THEN
     RAISE EXCEPTION 'GI requires same-state institutional transport at execution' USING ERRCODE='23514';
   END IF;
   values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF is_command AND token='core_livingorganism' AND action_name='RECORD_DISCUSSION' THEN
   IF command_rule IS NOT NULL OR deadline IS NOT NULL OR at_time IS DISTINCT FROM statement_timestamp()
     OR basis IS NULL OR actor IS NULL OR prior IS DISTINCT FROM outcome THEN
     RAISE EXCEPTION 'DISC requires same-state institutional transport at execution' USING ERRCODE='23514';
   END IF;
   values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF is_command AND token='core_livingorganism' AND action_name='RECORD_DISCUSSION_POST' THEN
   IF command_rule IS NOT NULL OR deadline IS NOT NULL OR at_time IS DISTINCT FROM statement_timestamp()
     OR basis IS NULL OR actor IS NULL OR prior IS DISTINCT FROM outcome THEN
     RAISE EXCEPTION 'DISC requires same-state institutional transport at execution' USING ERRCODE='23514';
   END IF;
   values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF is_command AND token='core_livingorganism' AND action_name='RECORD_CQ' THEN
   IF command_rule IS NOT NULL OR deadline IS NOT NULL OR at_time IS DISTINCT FROM statement_timestamp()
     OR basis IS NULL OR actor IS NULL OR prior IS DISTINCT FROM outcome THEN
     RAISE EXCEPTION 'DISC requires same-state institutional transport at execution' USING ERRCODE='23514';
   END IF;
   values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF token='core_contextualroleassignment' AND action_name='ASSIGN' AND (public.elect_office_source(anchor,command_event,actor,decision,at_time,deadline)
   OR public.elect_recognition_office(anchor,command_event,actor,decision,at_time,deadline)) THEN
   values_:=values_||'{"actor_state":"LIVING_ORGANISM","actor_id":null,"actor_access_epoch":null}'::jsonb;
 END IF;
 SELECT string_agg(quote_ident(k),',' ORDER BY k) INTO cols FROM jsonb_object_keys(values_) AS k;
 EXECUTE format('INSERT INTO public.%I (%s) SELECT %s FROM jsonb_populate_record(NULL::public.%I,$1) RETURNING id',
                table_name,cols,cols,table_name) INTO id_ USING values_;
 IF token='core_livingorganism' THEN
    PERFORM public.s015_refresh_governed_cache(token,anchor,greatest(
        (SELECT current_state_effective_at FROM public.core_livingorganism WHERE id=anchor),
        least(at_time,clock_timestamp())),clock_timestamp());
 END IF;
 RETURN id_;
END """
S015_APPEND_EVENT_CLOCK = r"""
DECLARE
 is_command boolean := command_event IS NOT NULL;
 event uuid := coalesce(command_event,gen_random_uuid());
 table_name text; fk text; col text; previous_id bigint; previous_seq integer; previous_l1 text; prior text;
 rule_ref text; lineage text; values_ jsonb; cols text; id_ bigint;
BEGIN
 SELECT m.event_table,m.fk_col,m.state_col INTO STRICT table_name,fk,col
 FROM public.s015_anchor_map() m WHERE m.anchor_token=token AND (
    (is_command AND token IN ('core_organismmembership','core_essentialcoveragecase','core_circle','core_contextualroleassignment','core_livingorganism','core_membershipcondition','core_authoritybasis','core_determinationcontestcase'))
 OR (NOT is_command AND token IN ('core_livingorganism','core_essentialcoveragecase','core_restrictedcontinuitycase')));

 IF is_command THEN
    -- Keep the caller's checked predecessor, even for a stale/empty head.
    -- The installed chain guards still decide whether that continuation is lawful.
    previous_id:=supplied_head; previous_seq:=supplied_sequence;
    IF supplied_head IS NOT NULL THEN
       EXECUTE format('SELECT l1_commitment FROM public.%I WHERE id=$1',table_name)
       INTO STRICT previous_l1 USING supplied_head;
    END IF;
    rule_ref:=command_rule;
    lineage:=public.c2_target(format('{"event":"%s","request":%s}',event,to_json(payload)));
    IF EXISTS (SELECT FROM jsonb_object_keys(metadata) k
               WHERE NOT (
       (token='core_authoritybasis' AND k='kind') OR
       (token='core_organismmembership' AND k='reason_class') OR
       (token='core_essentialcoveragecase' AND k IN ('roster_version','evaluated_state_at','evaluated_known_at','determiner_capacity_reference')))) THEN
       RAISE EXCEPTION 'Unexpected command chain metadata'; END IF;
 ELSE
    EXECUTE format('SELECT id,sequence,l1_commitment FROM public.%I WHERE %I=$1 ORDER BY sequence DESC LIMIT 1',table_name,fk)
    INTO previous_id,previous_seq,previous_l1 USING anchor;
    rule_ref:='C0:G2:C2';
    lineage:=encode(sha256(convert_to(event::text||':'||payload,'UTF8')),'hex');
    IF EXISTS(SELECT FROM jsonb_object_keys(metadata) k WHERE k NOT IN
       ('roster_version','evaluated_state_at','evaluated_known_at','determiner_capacity_reference',
        'permitted_measures_reference','prohibited_effects_reference','triggering_assessment_id','accountable_actor_id')) THEN
       RAISE EXCEPTION 'C2 chain metadata is invalid'; END IF;
 END IF;
 IF NOT is_command OR supplied_head IS NOT NULL THEN
    EXECUTE format('SELECT %I FROM public.%I WHERE %I=$1 AND (effective_at,occurred_at,received_at)<=($2,$3,$4)
        ORDER BY effective_at DESC,occurred_at DESC,received_at DESC,recorded_at DESC,sequence DESC,event_uuid DESC LIMIT 1',
        col,table_name,fk) INTO prior USING anchor,at_time,occurred,received;
 END IF;


 IF token = 'core_circle' AND action_name='CREATE_DORMANT' THEN
    IF action_name IS DISTINCT FROM 'CREATE_DORMANT' OR outcome IS DISTINCT FROM 'DORMANT'
       OR supplied_head IS NOT NULL OR supplied_sequence IS NOT NULL OR command_rule IS NOT NULL
       OR access_epoch IS NOT NULL OR deadline IS NOT NULL OR actor IS NULL THEN
       RAISE EXCEPTION 'Invalid institutional Circle creation envelope';
    END IF;
    SELECT encode(sha256(convert_to(format(
      '{"action":"CREATE_DORMANT_CIRCLE","event_uuid":"%s","fingerprint":"%s","subject_uuid":"%s"}',
      event,payload,c.circle_uuid),'UTF8')),'hex') INTO STRICT lineage
    FROM public.core_circle c WHERE c.id=anchor;
 END IF;


 IF is_command AND token IN ('core_organismmembership','core_essentialcoveragecase') AND (
    command_rule IS NOT NULL OR NOT EXISTS(SELECT FROM public.core_rootgenesis g JOIN public.core_livingorganism lo
     ON lo.organism_id=g.organism_uuid WHERE g.insert_xid=pg_current_xact_id() AND g.decision_reference=decision
     AND g.founder_id=actor AND g.basis_id=basis AND g.request_fingerprint=payload AND g.effective_at=at_time
     AND ((token='core_organismmembership' AND EXISTS(SELECT FROM public.core_organismmembership m
            WHERE m.id=anchor AND m.living_organism_id=lo.id AND m.identity_id=actor)
            AND action_name IN ('OPEN','ACTIVATE') AND metadata->>'reason_class'='FOUNDING')
       OR (token='core_essentialcoveragecase' AND EXISTS(SELECT FROM public.core_essentialcoveragecase c
            WHERE c.id=anchor AND c.living_organism_id=lo.id) AND action_name='ASSESS' AND outcome='SATISFIED')))) THEN
    RAISE EXCEPTION 'R_GENESIS_WRITER_SOURCE'; END IF;

 IF token='core_contextualroleassignment' AND action_name='ASSIGN'
    AND public.elect_recognition_office(anchor,command_event,actor,decision,at_time,deadline) THEN
    IF command_rule IS NOT NULL OR outcome IS DISTINCT FROM 'ACTIVE' OR evidence IS DISTINCT FROM 'elect-recognised:'||event
      THEN PERFORM public.cq_refuse('ELECT_RECOGNISER'); END IF;
 ELSIF token='core_contextualroleassignment' AND action_name='ASSIGN'
    AND public.elect_office_source(anchor,command_event,actor,decision,at_time,deadline) THEN
    IF command_rule IS NOT NULL OR outcome<>'ACTIVE' OR evidence NOT LIKE 'elect-result:%' THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
 ELSIF token='core_contextualroleassignment' AND action_name='ASSIGN' AND EXISTS(
    SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
    WHERE a.id=anchor AND a.circle_id IS NULL AND d.scope='LIVING_ORGANISM'
    AND public.r_genesis_context(d.living_organism_id,decision,actor,at_time)) THEN
    IF command_rule IS NOT NULL OR access_epoch IS NULL OR deadline IS NOT NULL THEN
       RAISE EXCEPTION 'R_GENESIS_WRITER_OFFICE'; END IF;
 ELSIF token='core_contextualroleassignment' AND action_name IN ('PROPOSE','ASSIGN') THEN
    IF command_rule IS NOT NULL OR NOT EXISTS (
       SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d
       ON d.id=a.role_definition_id WHERE a.id=anchor AND a.circle_id IS NOT NULL
       AND d.scope='CIRCLE' AND d.code='COORDINATOR') THEN
       RAISE EXCEPTION 'Invalid Circle Coordinator envelope';
    END IF;
 ELSIF token='core_contextualroleassignment' AND starts_with(command_rule,'c108-stand-down:') THEN
    IF NOT is_command OR action_name IS DISTINCT FROM 'END' OR outcome IS DISTINCT FROM 'ENDED'
    OR evidence IS DISTINCT FROM command_rule OR NOT public.c108_result_child(evidence,'offices',anchor)
    THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;

    IF EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent h
      WHERE h.id=supplied_head AND h.assignment_id=anchor AND h.effective_at>at_time AND h.resulting_state='ENDED') THEN
      SELECT public.s015_reduce_projection(token,coalesce(array_agg(e.id ORDER BY e.effective_at,e.occurred_at,
        e.received_at,e.recorded_at,e.sequence,e.event_uuid),'{}'::bigint[])) INTO prior
      FROM public.core_contextualroleassignmentstateevent e
      WHERE e.assignment_id=anchor AND e.sequence<=supplied_sequence
        AND (e.effective_at,e.occurred_at,e.received_at)<=(at_time,occurred,received);
    END IF;
 ELSIF token='core_contextualroleassignment' AND command_rule='ELECT:SUCCESSOR_LEFT' THEN
    -- The assignment input guard independently binds the complete row to the
    -- exact departure and predecessor. Other uses of this envelope refuse.
    IF NOT is_command OR action_name IS DISTINCT FROM 'END' OR outcome IS DISTINCT FROM 'ENDED'
      THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
 ELSIF token='core_contextualroleassignment' AND command_rule='ELECT:TERM_END' THEN
    IF NOT is_command OR action_name IS DISTINCT FROM 'END' OR outcome IS DISTINCT FROM 'ENDED'
       OR NOT EXISTS(SELECT FROM public.elect_due_office(anchor,public.intevia_clock_statement()) q WHERE q.due=at_time)
    THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
 ELSIF token='core_contextualroleassignment' AND (
     action_name IS DISTINCT FROM 'END' OR outcome IS DISTINCT FROM 'ENDED' OR command_rule IS NULL
     OR command_rule NOT IN ('CIRCLES:G2:U3','CIRCLES:G2:SELF_LEAVE')) THEN
    RAISE EXCEPTION 'Unsupported scoped office envelope';
 END IF;
 IF token='core_circle' AND action_name NOT IN ('CREATE_DORMANT','LEAVE_CIRCLE','OPEN_WORK','CLAIM_WORK','ACTIVATE','DISSOLVE_CIRCLE','STAND_DOWN') THEN
    RAISE EXCEPTION 'Unsupported Circle envelope';
 END IF;
 values_:=jsonb_build_object(fk,anchor,'event_uuid',event,'sequence',coalesce(previous_seq,0)+1,
    'predecessor_id',previous_id,'predecessor_sequence',previous_seq,'action',action_name,'prior_state',prior,col,outcome,
    'actor_state',CASE WHEN rule_ref IS NULL THEN 'PARTY' ELSE 'NONE' END,
    'actor_id',CASE WHEN rule_ref IS NULL THEN actor END,
    'actor_access_epoch',CASE WHEN rule_ref IS NULL THEN access_epoch END,
    'actor_capacity',CASE WHEN rule_ref IS NULL THEN 'STANDING' ELSE 'NA_NO_PARTY' END,
    'enterer_identity_id',actor,'entry_mode',CASE WHEN rule_ref IS NULL THEN 'TRANSPORT' ELSE 'COMPOSED' END,
    'composing_rule_state',CASE WHEN rule_ref IS NULL THEN 'NA_TRANSPORT' ELSE 'REFERENCE' END,
    'composing_rule_reference',rule_ref,'composer_state',CASE WHEN rule_ref IS NULL THEN 'NA_TRANSPORT' ELSE 'REFERENCE' END,
    'composer_identity_id',CASE WHEN rule_ref IS NOT NULL THEN actor END,
    'effective_until_state',CASE WHEN deadline IS NULL THEN 'NONE' ELSE 'FIXED' END,'effective_until',deadline,
    'authority_basis_id',basis,'authority_decision_reference',decision,'evidence_reference',evidence,
    'request_reference',request_key,'idempotency_key',CASE WHEN is_command THEN command_key ELSE request_key END,
    'payload_fingerprint',payload,'lineage_reference','s015l1:'||lineage,
    'temporal_basis_kind',CASE WHEN at_time<received THEN 'RETROSPECTIVE' WHEN at_time>received THEN 'PROSPECTIVE' END,
    'temporal_basis_reference',CASE WHEN at_time<>received THEN 's015tb1:'||payload END,
    'effective_at',at_time,'occurred_at',occurred,'received_at',received,'predecessor_l1_commitment',previous_l1,
    'parts_commitment',public.s015_0022_digest('s015p1',public.s015_0022_parts_preimage(table_name,event)))||metadata;

 IF token = 'core_circle' AND action_name='CREATE_DORMANT' THEN
    values_ := values_ || jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL);
 END IF;
 IF token='core_contextualroleassignment' THEN
    IF action_name='ASSIGN' AND EXISTS(SELECT FROM public.core_contextualroleassignment a WHERE a.id=anchor AND a.circle_id IS NOT NULL) THEN
       values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
    ELSIF action_name='PROPOSE' THEN
       values_:=values_||jsonb_build_object('effective_until_state','NOT_ESTABLISHED');
    END IF;
 END IF;

 IF token='core_circle' AND action_name='ACTIVATE' THEN
    IF command_rule IS NOT NULL OR access_epoch IS NOT NULL OR deadline IS NOT NULL THEN
       RAISE EXCEPTION 'Invalid institutional activation envelope'; END IF;
    values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF is_command AND token='core_livingorganism' AND action_name='ADOPT_LAW' THEN
   IF command_rule IS NOT NULL OR deadline IS NOT NULL OR at_time IS DISTINCT FROM public.intevia_clock_statement()
     OR basis IS NULL OR actor IS NULL OR prior IS DISTINCT FROM outcome THEN
     RAISE EXCEPTION 'LAW requires same-state institutional transport at execution' USING ERRCODE='23514';
   END IF;
   values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF is_command AND token='core_livingorganism' AND action_name='RECORD_GUIDING_INTENTION' THEN
   IF command_rule IS NOT NULL OR deadline IS NOT NULL OR at_time IS DISTINCT FROM public.intevia_clock_statement()
     OR basis IS NULL OR actor IS NULL OR prior IS DISTINCT FROM outcome THEN
     RAISE EXCEPTION 'GI requires same-state institutional transport at execution' USING ERRCODE='23514';
   END IF;
   values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF is_command AND token='core_livingorganism' AND action_name='RECORD_DISCUSSION' THEN
   IF command_rule IS NOT NULL OR deadline IS NOT NULL OR at_time IS DISTINCT FROM public.intevia_clock_statement()
     OR basis IS NULL OR actor IS NULL OR prior IS DISTINCT FROM outcome THEN
     RAISE EXCEPTION 'DISC requires same-state institutional transport at execution' USING ERRCODE='23514';
   END IF;
   values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF is_command AND token='core_livingorganism' AND action_name='RECORD_DISCUSSION_POST' THEN
   IF command_rule IS NOT NULL OR deadline IS NOT NULL OR at_time IS DISTINCT FROM public.intevia_clock_statement()
     OR basis IS NULL OR actor IS NULL OR prior IS DISTINCT FROM outcome THEN
     RAISE EXCEPTION 'DISC requires same-state institutional transport at execution' USING ERRCODE='23514';
   END IF;
   values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF is_command AND token='core_livingorganism' AND action_name='RECORD_CQ' THEN
   IF command_rule IS NOT NULL OR deadline IS NOT NULL OR at_time IS DISTINCT FROM public.intevia_clock_statement()
     OR basis IS NULL OR actor IS NULL OR prior IS DISTINCT FROM outcome THEN
     RAISE EXCEPTION 'DISC requires same-state institutional transport at execution' USING ERRCODE='23514';
   END IF;
   values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF token='core_contextualroleassignment' AND action_name='ASSIGN' AND (public.elect_office_source(anchor,command_event,actor,decision,at_time,deadline)
   OR public.elect_recognition_office(anchor,command_event,actor,decision,at_time,deadline)) THEN
   values_:=values_||'{"actor_state":"LIVING_ORGANISM","actor_id":null,"actor_access_epoch":null}'::jsonb;
 END IF;
 SELECT string_agg(quote_ident(k),',' ORDER BY k) INTO cols FROM jsonb_object_keys(values_) AS k;
 EXECUTE format('INSERT INTO public.%I (%s) SELECT %s FROM jsonb_populate_record(NULL::public.%I,$1) RETURNING id',
                table_name,cols,cols,table_name) INTO id_ USING values_;
 IF token='core_livingorganism' THEN
    PERFORM public.s015_refresh_governed_cache(token,anchor,greatest(
        (SELECT current_state_effective_at FROM public.core_livingorganism WHERE id=anchor),
        least(at_time,public.intevia_clock_instant())),public.intevia_clock_instant());
 END IF;
 RETURN id_;
END """

# public.cmt_changed()
# Its landed text: 0094_te_committee.py (assembled by that migration). Time calls moved: 1 statement.
CMT_CHANGED_LANDED = r"""
DECLARE lo bigint; circ bigint; member_ bigint; office_ bigint; cut timestamptz; gov boolean:=false;
 chair_ boolean:=false; rules_ boolean:=false; work_ boolean:=false; inherited_ boolean:=false;
 cascade_ boolean:=false; law_ public.core_organismlawbinding; forming uuid; terms uuid;
 f jsonb; doc jsonb; touches jsonb; targets uuid[]; stamp timestamptz:=statement_timestamp();
BEGIN
 IF TG_TABLE_NAME IN ('core_organismlawbinding','core_organismlawending') THEN
   IF TG_TABLE_NAME='core_organismlawbinding' THEN
     law_:=NEW; cut:=law_.in_force_from;
   ELSE
     SELECT * INTO STRICT law_ FROM public.core_organismlawbinding WHERE binding_uuid=NEW.binding_id;
     SELECT effective_at INTO STRICT cut FROM public.core_livingorganismevent WHERE id=NEW.end_event_id;
   END IF;
   lo:=law_.organism_id; forming:=law_.binding_uuid; terms:=law_.binding_uuid;
   SELECT public.elect_document(content) INTO doc FROM public.core_libraryresourceversion WHERE id=law_.version_id;
   rules_:=law_.scope_kind='LIVING_ORGANISM' AND (law_.law_class IN ('ARTICLE','BYLAW') OR
     (law_.law_class='POLICY' AND law_.decision_route='CQ_GOVERNANCE_CIRCLE'))
     AND jsonb_typeof(doc->'committees'->'chair_board_member')='boolean';
   inherited_:=law_.scope_kind='PLATFORM' AND doc->'committee_work'->>'defers_to_local'='true'
     AND law_.law_class='POLICY' AND doc->'committee_work'->>'return_to'='CHAIR';
   work_:=law_.law_class='POLICY' AND doc->'committee_work'->>'return_to'='CHAIR'
     AND (law_.scope_kind='LIVING_ORGANISM' OR inherited_);
   cascade_:=true;
 ELSIF TG_TABLE_NAME='core_cqact' THEN
   lo:=NEW.organism_id; cut:=NEW.effective_at; f:=NEW.facts->'domain_plan'->'committee';
   IF f ? 'board_id' THEN RETURN NULL; END IF;
   forming:=coalesce(f->>'binding',f->>'mandate')::uuid; cascade_:=f ? 'completed';
 ELSIF TG_TABLE_NAME='core_contextualroleassignmentstateevent' THEN
   office_:=NEW.assignment_id; cut:=NEW.effective_at;
   SELECT om.living_organism_id,a.circle_id,a.membership_id,
       d.kind='BOARD_OFFICE' AND d.scope='LIVING_ORGANISM' AND d.code='CHAIR'
    INTO STRICT lo,circ,member_,chair_ FROM public.core_contextualroleassignment a
    JOIN public.core_organismmembership om ON om.id=a.membership_id
    JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id WHERE a.id=office_;
 ELSIF TG_TABLE_NAME='core_circlestateevent' THEN
   circ:=NEW.circle_id; cut:=NEW.effective_at; cascade_:=true;
   SELECT parent_organism_id,kind='GOVERNANCE' INTO STRICT lo,gov FROM public.core_circle WHERE id=circ;
 ELSIF TG_TABLE_NAME='core_cmtmandateend' THEN
   -- TIMED EVENTS: the mandate reached its stated end date. Refresh it and its children at that date.
   lo:=NEW.organism_id; cut:=NEW.effective_at; forming:=NEW.mandate; cascade_:=true;
 ELSIF TG_TABLE_NAME='core_electvacancy' THEN
   -- A fixed seat end can have been recorded before it was due.
   SELECT p.circle_id,p.membership_id INTO circ,member_ FROM public.elect_vacancy_sources s
   JOIN public.core_circleparticipation p ON p.participation_uuid=s.occupancy
   WHERE s.ending=NEW.ending AND s.organism_id=NEW.organism_id AND s.kind='SEAT';
   IF NOT FOUND THEN RETURN NULL; END IF;
   lo:=NEW.organism_id; gov:=true; cut:=NEW.effective_at;
 ELSE
   IF TG_TABLE_NAME='core_circleparticipationend' THEN
     SELECT p.circle_id,p.membership_id INTO STRICT circ,member_ FROM public.core_circleparticipation p
       WHERE p.id=NEW.participation_id;
   ELSE circ:=NEW.circle_id; member_:=NEW.membership_id; END IF;
   cut:=NEW.effective_at;
   SELECT parent_organism_id,kind='GOVERNANCE' INTO STRICT lo,gov FROM public.core_circle WHERE id=circ;
 END IF;
 IF cut<=stamp AND (office_ IS NOT NULL OR gov AND member_ IS NOT NULL) THEN
   SELECT jsonb_object_agg(jsonb_build_array(a.activity_id,cut,'BOARD_WORK')::text,true) INTO touches
   FROM public.core_cmtcustody w JOIN public.core_serviceactivity a ON a.id=w.activity
   LEFT JOIN public.core_circleparticipation p ON p.id=w.place
   LEFT JOIN public.core_contextualroleassignment o ON o.id=w.office
   WHERE w.board_circle IS NOT NULL AND w.effective_at<=cut AND a.state IN ('assigned','in_progress')
    AND ((office_ IS NOT NULL AND w.office=office_) OR (gov AND w.board_circle=circ
      AND (p.membership_id=member_ OR o.membership_id=member_)))
    AND NOT EXISTS(SELECT FROM public.core_cmtcustody child WHERE child.predecessor=w.receipt);
   IF touches IS NOT NULL THEN
     INSERT INTO public.cmt_refresh_batch AS batch VALUES(pg_current_xact_id(),touches)
     ON CONFLICT(action_xid) DO UPDATE SET touches=batch.touches||EXCLUDED.touches WHERE NOT batch.touches @> EXCLUDED.touches;
   END IF;
 END IF;
 IF cut>stamp OR NOT EXISTS(SELECT FROM public.cmt_mandates m WHERE m.organism_id=lo OR inherited_) THEN
   RETURN NULL;
 END IF;
 -- A participating trustee affects only committees the person belongs to.
 -- The Board Chair is also a receiving office for committee-linked open work.
 SELECT array_agg(m.binding) INTO targets FROM public.cmt_mandates m
 WHERE (m.organism_id=lo OR inherited_) AND m.effective_at<=cut AND m.recorded_at<=stamp
 AND (m.binding=forming OR m.mandate->'terms_effect'->>'binding_uuid'=terms::text OR m.circle_id=circ
 OR rules_ OR (gov AND member_ IS NULL)
 OR ((gov OR office_ IS NOT NULL) AND member_ IS NOT NULL AND EXISTS (
   SELECT FROM public.core_circleparticipation p WHERE p.circle_id=m.circle_id AND p.membership_id=member_
     AND p.effective_at<=cut AND NOT EXISTS(SELECT FROM public.core_circleparticipationend e
       WHERE e.participation_id=p.id AND e.effective_at<cut AND e.recorded_at<=stamp)))
 OR ((work_ OR chair_ OR office_ IS NOT NULL OR (gov AND member_ IS NOT NULL)) AND EXISTS (
   SELECT FROM public.core_cmtcustody w JOIN public.core_serviceactivity a ON a.id=w.activity
   WHERE w.board_circle IS NULL AND w.mandate=m.binding AND w.office IS NOT NULL AND a.state IN ('assigned','in_progress') AND w.effective_at<=cut
   AND (work_ OR chair_ OR w.office=office_ OR (gov AND member_=(SELECT a.membership_id
        FROM public.core_contextualroleassignment a WHERE a.id=w.office))) AND NOT EXISTS(
     SELECT FROM public.core_cmtcustody next_ WHERE next_.predecessor=w.receipt AND next_.effective_at<=cut))))
 AND (m.binding=forming OR NOT EXISTS(SELECT FROM public.core_cmtcomposition g
      WHERE g.mandate=m.binding AND g.requirement='MANDATE' AND g.unmet AND g.effective_at<cut)
 OR EXISTS (SELECT FROM public.core_cmtcustody w JOIN public.core_serviceactivity a ON a.id=w.activity
   WHERE w.mandate=m.binding AND w.board_circle IS NULL AND w.office IS NOT NULL AND w.effective_at<=cut
     AND a.state IN ('assigned','in_progress') AND NOT EXISTS (
       SELECT FROM public.core_cmtcustody next_ WHERE next_.predecessor=w.receipt)));
 IF targets IS NULL THEN RETURN NULL; END IF;
 WITH RECURSIVE affected(binding) AS (
   SELECT unnest(targets)
   UNION
   SELECT child.binding FROM affected parent JOIN public.cmt_mandates child
     ON child.mandate->>'delegating_parent'=parent.binding::text
   WHERE cascade_ AND child.effective_at<=cut AND child.recorded_at<=stamp
 ) SELECT jsonb_object_agg(jsonb_build_array(binding,cut)::text,true) INTO touches FROM affected;
 INSERT INTO public.cmt_refresh_batch AS batch(action_xid,touches) VALUES(pg_current_xact_id(),touches)
 ON CONFLICT(action_xid) DO UPDATE SET touches=batch.touches||EXCLUDED.touches
 WHERE NOT batch.touches @> EXCLUDED.touches;
 RETURN NULL;
END """
CMT_CHANGED_CLOCK = r"""
DECLARE lo bigint; circ bigint; member_ bigint; office_ bigint; cut timestamptz; gov boolean:=false;
 chair_ boolean:=false; rules_ boolean:=false; work_ boolean:=false; inherited_ boolean:=false;
 cascade_ boolean:=false; law_ public.core_organismlawbinding; forming uuid; terms uuid;
 f jsonb; doc jsonb; touches jsonb; targets uuid[]; stamp timestamptz:=public.intevia_clock_statement();
BEGIN
 IF TG_TABLE_NAME IN ('core_organismlawbinding','core_organismlawending') THEN
   IF TG_TABLE_NAME='core_organismlawbinding' THEN
     law_:=NEW; cut:=law_.in_force_from;
   ELSE
     SELECT * INTO STRICT law_ FROM public.core_organismlawbinding WHERE binding_uuid=NEW.binding_id;
     SELECT effective_at INTO STRICT cut FROM public.core_livingorganismevent WHERE id=NEW.end_event_id;
   END IF;
   lo:=law_.organism_id; forming:=law_.binding_uuid; terms:=law_.binding_uuid;
   SELECT public.elect_document(content) INTO doc FROM public.core_libraryresourceversion WHERE id=law_.version_id;
   rules_:=law_.scope_kind='LIVING_ORGANISM' AND (law_.law_class IN ('ARTICLE','BYLAW') OR
     (law_.law_class='POLICY' AND law_.decision_route='CQ_GOVERNANCE_CIRCLE'))
     AND jsonb_typeof(doc->'committees'->'chair_board_member')='boolean';
   inherited_:=law_.scope_kind='PLATFORM' AND doc->'committee_work'->>'defers_to_local'='true'
     AND law_.law_class='POLICY' AND doc->'committee_work'->>'return_to'='CHAIR';
   work_:=law_.law_class='POLICY' AND doc->'committee_work'->>'return_to'='CHAIR'
     AND (law_.scope_kind='LIVING_ORGANISM' OR inherited_);
   cascade_:=true;
 ELSIF TG_TABLE_NAME='core_cqact' THEN
   lo:=NEW.organism_id; cut:=NEW.effective_at; f:=NEW.facts->'domain_plan'->'committee';
   IF f ? 'board_id' THEN RETURN NULL; END IF;
   forming:=coalesce(f->>'binding',f->>'mandate')::uuid; cascade_:=f ? 'completed';
 ELSIF TG_TABLE_NAME='core_contextualroleassignmentstateevent' THEN
   office_:=NEW.assignment_id; cut:=NEW.effective_at;
   SELECT om.living_organism_id,a.circle_id,a.membership_id,
       d.kind='BOARD_OFFICE' AND d.scope='LIVING_ORGANISM' AND d.code='CHAIR'
    INTO STRICT lo,circ,member_,chair_ FROM public.core_contextualroleassignment a
    JOIN public.core_organismmembership om ON om.id=a.membership_id
    JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id WHERE a.id=office_;
 ELSIF TG_TABLE_NAME='core_circlestateevent' THEN
   circ:=NEW.circle_id; cut:=NEW.effective_at; cascade_:=true;
   SELECT parent_organism_id,kind='GOVERNANCE' INTO STRICT lo,gov FROM public.core_circle WHERE id=circ;
 ELSIF TG_TABLE_NAME='core_cmtmandateend' THEN
   -- TIMED EVENTS: the mandate reached its stated end date. Refresh it and its children at that date.
   lo:=NEW.organism_id; cut:=NEW.effective_at; forming:=NEW.mandate; cascade_:=true;
 ELSIF TG_TABLE_NAME='core_electvacancy' THEN
   -- A fixed seat end can have been recorded before it was due.
   SELECT p.circle_id,p.membership_id INTO circ,member_ FROM public.elect_vacancy_sources s
   JOIN public.core_circleparticipation p ON p.participation_uuid=s.occupancy
   WHERE s.ending=NEW.ending AND s.organism_id=NEW.organism_id AND s.kind='SEAT';
   IF NOT FOUND THEN RETURN NULL; END IF;
   lo:=NEW.organism_id; gov:=true; cut:=NEW.effective_at;
 ELSE
   IF TG_TABLE_NAME='core_circleparticipationend' THEN
     SELECT p.circle_id,p.membership_id INTO STRICT circ,member_ FROM public.core_circleparticipation p
       WHERE p.id=NEW.participation_id;
   ELSE circ:=NEW.circle_id; member_:=NEW.membership_id; END IF;
   cut:=NEW.effective_at;
   SELECT parent_organism_id,kind='GOVERNANCE' INTO STRICT lo,gov FROM public.core_circle WHERE id=circ;
 END IF;
 IF cut<=stamp AND (office_ IS NOT NULL OR gov AND member_ IS NOT NULL) THEN
   SELECT jsonb_object_agg(jsonb_build_array(a.activity_id,cut,'BOARD_WORK')::text,true) INTO touches
   FROM public.core_cmtcustody w JOIN public.core_serviceactivity a ON a.id=w.activity
   LEFT JOIN public.core_circleparticipation p ON p.id=w.place
   LEFT JOIN public.core_contextualroleassignment o ON o.id=w.office
   WHERE w.board_circle IS NOT NULL AND w.effective_at<=cut AND a.state IN ('assigned','in_progress')
    AND ((office_ IS NOT NULL AND w.office=office_) OR (gov AND w.board_circle=circ
      AND (p.membership_id=member_ OR o.membership_id=member_)))
    AND NOT EXISTS(SELECT FROM public.core_cmtcustody child WHERE child.predecessor=w.receipt);
   IF touches IS NOT NULL THEN
     INSERT INTO public.cmt_refresh_batch AS batch VALUES(pg_current_xact_id(),touches)
     ON CONFLICT(action_xid) DO UPDATE SET touches=batch.touches||EXCLUDED.touches WHERE NOT batch.touches @> EXCLUDED.touches;
   END IF;
 END IF;
 IF cut>stamp OR NOT EXISTS(SELECT FROM public.cmt_mandates m WHERE m.organism_id=lo OR inherited_) THEN
   RETURN NULL;
 END IF;
 -- A participating trustee affects only committees the person belongs to.
 -- The Board Chair is also a receiving office for committee-linked open work.
 SELECT array_agg(m.binding) INTO targets FROM public.cmt_mandates m
 WHERE (m.organism_id=lo OR inherited_) AND m.effective_at<=cut AND m.recorded_at<=stamp
 AND (m.binding=forming OR m.mandate->'terms_effect'->>'binding_uuid'=terms::text OR m.circle_id=circ
 OR rules_ OR (gov AND member_ IS NULL)
 OR ((gov OR office_ IS NOT NULL) AND member_ IS NOT NULL AND EXISTS (
   SELECT FROM public.core_circleparticipation p WHERE p.circle_id=m.circle_id AND p.membership_id=member_
     AND p.effective_at<=cut AND NOT EXISTS(SELECT FROM public.core_circleparticipationend e
       WHERE e.participation_id=p.id AND e.effective_at<cut AND e.recorded_at<=stamp)))
 OR ((work_ OR chair_ OR office_ IS NOT NULL OR (gov AND member_ IS NOT NULL)) AND EXISTS (
   SELECT FROM public.core_cmtcustody w JOIN public.core_serviceactivity a ON a.id=w.activity
   WHERE w.board_circle IS NULL AND w.mandate=m.binding AND w.office IS NOT NULL AND a.state IN ('assigned','in_progress') AND w.effective_at<=cut
   AND (work_ OR chair_ OR w.office=office_ OR (gov AND member_=(SELECT a.membership_id
        FROM public.core_contextualroleassignment a WHERE a.id=w.office))) AND NOT EXISTS(
     SELECT FROM public.core_cmtcustody next_ WHERE next_.predecessor=w.receipt AND next_.effective_at<=cut))))
 AND (m.binding=forming OR NOT EXISTS(SELECT FROM public.core_cmtcomposition g
      WHERE g.mandate=m.binding AND g.requirement='MANDATE' AND g.unmet AND g.effective_at<cut)
 OR EXISTS (SELECT FROM public.core_cmtcustody w JOIN public.core_serviceactivity a ON a.id=w.activity
   WHERE w.mandate=m.binding AND w.board_circle IS NULL AND w.office IS NOT NULL AND w.effective_at<=cut
     AND a.state IN ('assigned','in_progress') AND NOT EXISTS (
       SELECT FROM public.core_cmtcustody next_ WHERE next_.predecessor=w.receipt)));
 IF targets IS NULL THEN RETURN NULL; END IF;
 WITH RECURSIVE affected(binding) AS (
   SELECT unnest(targets)
   UNION
   SELECT child.binding FROM affected parent JOIN public.cmt_mandates child
     ON child.mandate->>'delegating_parent'=parent.binding::text
   WHERE cascade_ AND child.effective_at<=cut AND child.recorded_at<=stamp
 ) SELECT jsonb_object_agg(jsonb_build_array(binding,cut)::text,true) INTO touches FROM affected;
 INSERT INTO public.cmt_refresh_batch AS batch(action_xid,touches) VALUES(pg_current_xact_id(),touches)
 ON CONFLICT(action_xid) DO UPDATE SET touches=batch.touches||EXCLUDED.touches
 WHERE NOT batch.touches @> EXCLUDED.touches;
 RETURN NULL;
END """

# public.cmt_custody_guard()
# Its landed text: 0094_te_committee.py (assembled by that migration). Time calls moved: 1 statement.
CMT_CUSTODY_GUARD_LANDED = r"""
DECLARE prior_ public.core_cmtcustody; i public.core_cqact; result_ jsonb; w record;
BEGIN
 PERFORM 1 FROM public.core_serviceactivity WHERE id=NEW.activity FOR UPDATE;
 NEW.recorded_at:=statement_timestamp();
 SELECT * INTO prior_ FROM public.core_cmtcustody WHERE activity=NEW.activity ORDER BY effective_at DESC,recorded_at DESC LIMIT 1;
 IF prior_.receipt IS DISTINCT FROM NEW.predecessor OR NEW.effective_at>NEW.recorded_at THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
 IF prior_.receipt IS NOT NULL AND (NEW.board_circle IS DISTINCT FROM prior_.board_circle
 OR NEW.board_target IS DISTINCT FROM prior_.board_target) THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
 IF NEW.cause ? 'stand_down' THEN
   IF prior_.receipt IS NULL OR NEW.holder IS NOT NULL OR NEW.office IS NOT NULL OR NEW.place IS NOT NULL
    OR NEW.clause IS DISTINCT FROM prior_.clause OR NEW.mandate IS DISTINCT FROM prior_.mandate OR NOT EXISTS(
    SELECT FROM public.core_cqact a WHERE a.operation='IMPLEMENT' AND a.execution_xid=pg_current_xact_id()
    AND a.facts#>>'{domain_plan,domain}'='BOARD_STAND_DOWN' AND public.cq3_implement_source(a)=a.authority_source
    AND a.facts#>'{domain_plan,targets,work}' ? prior_.receipt::text AND a.effective_at=NEW.effective_at
    AND NEW.cause=jsonb_build_object('stand_down',a.request->>'result','custodian','LO_COORDINATORS','organism',a.organism_id))
    THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
   RETURN NEW;
 END IF;
 IF prior_.cause ? 'stand_down' THEN
   PERFORM public.cq_refuse('CMT_WORK_STAND_DOWN'); END IF;
 IF NEW.cause ? 'implementation' THEN
   SELECT * INTO i FROM public.core_cqact WHERE act_uuid=NEW.receipt AND operation='IMPLEMENT' AND public.cq3_implement_source(core_cqact)=authority_source;
   result_:=i.facts->'domain_plan'->'committee';
   IF i.act_uuid IS NULL OR NEW.cause<>jsonb_build_object('implementation',i.act_uuid) OR i.effective_at<>NEW.effective_at
    OR coalesce(result_->>'act','') NOT IN ('BIND_WORK','REASSIGN_WORK','BIND_BOARD_WORK','REASSIGN_BOARD_WORK')
    OR NEW.board_circle IS DISTINCT FROM (result_->>'board_id')::bigint
    OR NEW.board_target IS DISTINCT FROM result_->'target' OR NEW.place IS DISTINCT FROM (result_->>'place_id')::bigint
    OR NEW.predecessor IS DISTINCT FROM (result_->>'predecessor')::uuid OR NEW.mandate::text IS DISTINCT FROM result_->>'mandate'
    OR NEW.activity IS DISTINCT FROM (result_->>'activity_id')::bigint OR NEW.office IS DISTINCT FROM (result_->>'office_id')::bigint
    OR NEW.holder IS DISTINCT FROM (result_->>'holder')::bigint OR NEW.clause IS DISTINCT FROM result_->'clause'
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 ELSIF NEW.board_circle IS NOT NULL THEN
   SELECT * INTO w FROM public.cmt_board_return(prior_.receipt,NEW.effective_at,NEW.recorded_at);
   IF NOT FOUND OR pg_trigger_depth()<2 OR NOT EXISTS(SELECT FROM public.cmt_refresh_batch b
     CROSS JOIN LATERAL jsonb_object_keys(b.touches) k JOIN public.core_serviceactivity a ON a.activity_id=(k::jsonb->>0)::uuid
     WHERE b.action_xid=pg_current_xact_id() AND k::jsonb->>2='BOARD_WORK' AND a.id=NEW.activity
       AND (k::jsonb->>1)::timestamptz>=NEW.effective_at)
   OR NEW.place IS NOT NULL OR NEW.effective_at IS DISTINCT FROM w.ending OR NEW.holder IS DISTINCT FROM w.holder
   OR NEW.office IS DISTINCT FROM w.office OR NEW.mandate IS DISTINCT FROM prior_.mandate OR NEW.clause IS DISTINCT FROM prior_.clause
   OR NEW.cause IS DISTINCT FROM jsonb_build_object('ending_place',prior_.place,'ending_office',prior_.office)
   THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
 ELSE
   IF pg_trigger_depth()<2 OR NOT (public.cmt_pending_cut(NEW.mandate,NEW.effective_at,NEW.recorded_at)
      OR EXISTS(SELECT FROM public.cmt_refresh_batch b CROSS JOIN LATERAL jsonb_object_keys(b.touches) k
          WHERE b.action_xid=pg_current_xact_id() AND (k::jsonb->>0)::uuid=NEW.mandate
          AND (k::jsonb->>1)::timestamptz>=NEW.effective_at
          AND NEW.effective_at=public.cmt_custody_due(prior_.office,NEW.mandate,prior_.effective_at,
             (k::jsonb->>1)::timestamptz,NEW.recorded_at)))
     THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
   SELECT * INTO w FROM public.cmt_worker(NEW.activity,NEW.effective_at,NEW.recorded_at);
   IF prior_.receipt IS NULL OR NEW.mandate<>prior_.mandate OR NEW.cause IS DISTINCT FROM jsonb_build_object('ending_office',prior_.office)
   OR prior_.office IS NULL OR w.office IS NOT DISTINCT FROM prior_.office OR NEW.office IS DISTINCT FROM w.office
   OR NEW.holder IS DISTINCT FROM w.holder OR NEW.clause IS DISTINCT FROM coalesce(w.clause,'null'::jsonb)
   THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
 END IF;
 RETURN NEW;
END """
CMT_CUSTODY_GUARD_CLOCK = r"""
DECLARE prior_ public.core_cmtcustody; i public.core_cqact; result_ jsonb; w record;
BEGIN
 PERFORM 1 FROM public.core_serviceactivity WHERE id=NEW.activity FOR UPDATE;
 NEW.recorded_at:=public.intevia_clock_statement();
 SELECT * INTO prior_ FROM public.core_cmtcustody WHERE activity=NEW.activity ORDER BY effective_at DESC,recorded_at DESC LIMIT 1;
 IF prior_.receipt IS DISTINCT FROM NEW.predecessor OR NEW.effective_at>NEW.recorded_at THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
 IF prior_.receipt IS NOT NULL AND (NEW.board_circle IS DISTINCT FROM prior_.board_circle
 OR NEW.board_target IS DISTINCT FROM prior_.board_target) THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
 IF NEW.cause ? 'stand_down' THEN
   IF prior_.receipt IS NULL OR NEW.holder IS NOT NULL OR NEW.office IS NOT NULL OR NEW.place IS NOT NULL
    OR NEW.clause IS DISTINCT FROM prior_.clause OR NEW.mandate IS DISTINCT FROM prior_.mandate OR NOT EXISTS(
    SELECT FROM public.core_cqact a WHERE a.operation='IMPLEMENT' AND a.execution_xid=pg_current_xact_id()
    AND a.facts#>>'{domain_plan,domain}'='BOARD_STAND_DOWN' AND public.cq3_implement_source(a)=a.authority_source
    AND a.facts#>'{domain_plan,targets,work}' ? prior_.receipt::text AND a.effective_at=NEW.effective_at
    AND NEW.cause=jsonb_build_object('stand_down',a.request->>'result','custodian','LO_COORDINATORS','organism',a.organism_id))
    THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
   RETURN NEW;
 END IF;
 IF prior_.cause ? 'stand_down' THEN
   PERFORM public.cq_refuse('CMT_WORK_STAND_DOWN'); END IF;
 IF NEW.cause ? 'implementation' THEN
   SELECT * INTO i FROM public.core_cqact WHERE act_uuid=NEW.receipt AND operation='IMPLEMENT' AND public.cq3_implement_source(core_cqact)=authority_source;
   result_:=i.facts->'domain_plan'->'committee';
   IF i.act_uuid IS NULL OR NEW.cause<>jsonb_build_object('implementation',i.act_uuid) OR i.effective_at<>NEW.effective_at
    OR coalesce(result_->>'act','') NOT IN ('BIND_WORK','REASSIGN_WORK','BIND_BOARD_WORK','REASSIGN_BOARD_WORK')
    OR NEW.board_circle IS DISTINCT FROM (result_->>'board_id')::bigint
    OR NEW.board_target IS DISTINCT FROM result_->'target' OR NEW.place IS DISTINCT FROM (result_->>'place_id')::bigint
    OR NEW.predecessor IS DISTINCT FROM (result_->>'predecessor')::uuid OR NEW.mandate::text IS DISTINCT FROM result_->>'mandate'
    OR NEW.activity IS DISTINCT FROM (result_->>'activity_id')::bigint OR NEW.office IS DISTINCT FROM (result_->>'office_id')::bigint
    OR NEW.holder IS DISTINCT FROM (result_->>'holder')::bigint OR NEW.clause IS DISTINCT FROM result_->'clause'
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 ELSIF NEW.board_circle IS NOT NULL THEN
   SELECT * INTO w FROM public.cmt_board_return(prior_.receipt,NEW.effective_at,NEW.recorded_at);
   IF NOT FOUND OR pg_trigger_depth()<2 OR NOT EXISTS(SELECT FROM public.cmt_refresh_batch b
     CROSS JOIN LATERAL jsonb_object_keys(b.touches) k JOIN public.core_serviceactivity a ON a.activity_id=(k::jsonb->>0)::uuid
     WHERE b.action_xid=pg_current_xact_id() AND k::jsonb->>2='BOARD_WORK' AND a.id=NEW.activity
       AND (k::jsonb->>1)::timestamptz>=NEW.effective_at)
   OR NEW.place IS NOT NULL OR NEW.effective_at IS DISTINCT FROM w.ending OR NEW.holder IS DISTINCT FROM w.holder
   OR NEW.office IS DISTINCT FROM w.office OR NEW.mandate IS DISTINCT FROM prior_.mandate OR NEW.clause IS DISTINCT FROM prior_.clause
   OR NEW.cause IS DISTINCT FROM jsonb_build_object('ending_place',prior_.place,'ending_office',prior_.office)
   THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
 ELSE
   IF pg_trigger_depth()<2 OR NOT (public.cmt_pending_cut(NEW.mandate,NEW.effective_at,NEW.recorded_at)
      OR EXISTS(SELECT FROM public.cmt_refresh_batch b CROSS JOIN LATERAL jsonb_object_keys(b.touches) k
          WHERE b.action_xid=pg_current_xact_id() AND (k::jsonb->>0)::uuid=NEW.mandate
          AND (k::jsonb->>1)::timestamptz>=NEW.effective_at
          AND NEW.effective_at=public.cmt_custody_due(prior_.office,NEW.mandate,prior_.effective_at,
             (k::jsonb->>1)::timestamptz,NEW.recorded_at)))
     THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
   SELECT * INTO w FROM public.cmt_worker(NEW.activity,NEW.effective_at,NEW.recorded_at);
   IF prior_.receipt IS NULL OR NEW.mandate<>prior_.mandate OR NEW.cause IS DISTINCT FROM jsonb_build_object('ending_office',prior_.office)
   OR prior_.office IS NULL OR w.office IS NOT DISTINCT FROM prior_.office OR NEW.office IS DISTINCT FROM w.office
   OR NEW.holder IS DISTINCT FROM w.holder OR NEW.clause IS DISTINCT FROM coalesce(w.clause,'null'::jsonb)
   THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
 END IF;
 RETURN NEW;
END """

# public.cmt_drain()
# Its landed text: 0094_te_committee.py (found there verbatim). Time calls moved: 1 statement.
CMT_DRAIN_LANDED = r"""
DECLARE batch jsonb; r record; stamp timestamptz:=statement_timestamp();
BEGIN
 SELECT touches INTO STRICT batch FROM public.cmt_refresh_batch WHERE action_xid=NEW.action_xid;
 -- Lock affected owners in stable order, once, before testing their ledger heads.
 PERFORM lo.id FROM public.core_livingorganism lo WHERE lo.id IN (
   SELECT m.organism_id FROM jsonb_object_keys(batch) k JOIN public.cmt_mandates m
     ON m.binding=(k::jsonb->>0)::uuid WHERE jsonb_array_length(k::jsonb)=2
   UNION ALL SELECT c.parent_organism_id FROM jsonb_object_keys(batch) k
    JOIN public.core_serviceactivity a ON a.activity_id=(k::jsonb->>0)::uuid
    JOIN public.core_cmtcustody w ON w.activity=a.id AND w.predecessor IS NULL
    JOIN public.core_circle c ON c.id=w.board_circle WHERE k::jsonb->>2='BOARD_WORK') ORDER BY lo.id FOR UPDATE;
 FOR r IN SELECT (k::jsonb->>0)::uuid AS binding,(k::jsonb->>1)::timestamptz AS cut
          FROM jsonb_object_keys(batch) k WHERE jsonb_array_length(k::jsonb)=2 ORDER BY cut,binding LOOP
   PERFORM public.cmt_refresh_one(r.binding,r.cut,stamp);
 END LOOP;
 FOR r IN SELECT (k::jsonb->>0)::uuid AS activity,(k::jsonb->>1)::timestamptz AS cut FROM jsonb_object_keys(batch) k
   WHERE k::jsonb->>2='BOARD_WORK' ORDER BY cut,activity LOOP
   PERFORM public.cmt_refresh_board_activity(r.activity,r.cut,stamp);
 END LOOP;
 DELETE FROM public.cmt_refresh_batch WHERE action_xid=NEW.action_xid;
 RETURN NULL;
END """
CMT_DRAIN_CLOCK = r"""
DECLARE batch jsonb; r record; stamp timestamptz:=public.intevia_clock_statement();
BEGIN
 SELECT touches INTO STRICT batch FROM public.cmt_refresh_batch WHERE action_xid=NEW.action_xid;
 -- Lock affected owners in stable order, once, before testing their ledger heads.
 PERFORM lo.id FROM public.core_livingorganism lo WHERE lo.id IN (
   SELECT m.organism_id FROM jsonb_object_keys(batch) k JOIN public.cmt_mandates m
     ON m.binding=(k::jsonb->>0)::uuid WHERE jsonb_array_length(k::jsonb)=2
   UNION ALL SELECT c.parent_organism_id FROM jsonb_object_keys(batch) k
    JOIN public.core_serviceactivity a ON a.activity_id=(k::jsonb->>0)::uuid
    JOIN public.core_cmtcustody w ON w.activity=a.id AND w.predecessor IS NULL
    JOIN public.core_circle c ON c.id=w.board_circle WHERE k::jsonb->>2='BOARD_WORK') ORDER BY lo.id FOR UPDATE;
 FOR r IN SELECT (k::jsonb->>0)::uuid AS binding,(k::jsonb->>1)::timestamptz AS cut
          FROM jsonb_object_keys(batch) k WHERE jsonb_array_length(k::jsonb)=2 ORDER BY cut,binding LOOP
   PERFORM public.cmt_refresh_one(r.binding,r.cut,stamp);
 END LOOP;
 FOR r IN SELECT (k::jsonb->>0)::uuid AS activity,(k::jsonb->>1)::timestamptz AS cut FROM jsonb_object_keys(batch) k
   WHERE k::jsonb->>2='BOARD_WORK' ORDER BY cut,activity LOOP
   PERFORM public.cmt_refresh_board_activity(r.activity,r.cut,stamp);
 END LOOP;
 DELETE FROM public.cmt_refresh_batch WHERE action_xid=NEW.action_xid;
 RETURN NULL;
END """

# public.cmt_end_guard()
# Its landed text: 0094_te_committee.py (found there verbatim). Time calls moved: 1 statement.
CMT_END_GUARD_LANDED = r"""
DECLARE m public.cmt_mandates;
BEGIN
 NEW.recorded_at:=statement_timestamp();
 SELECT * INTO m FROM public.cmt_mandates x WHERE x.binding=NEW.mandate AND x.organism_id=NEW.organism_id;
 IF m.binding IS NULL OR m.mandate->>'ends_at' IS NULL
    OR NEW.effective_at IS DISTINCT FROM (m.mandate->>'ends_at')::timestamptz
    OR NEW.effective_at>NEW.recorded_at
    OR NOT public.cmt_live(NEW.mandate,NEW.effective_at-interval '1 microsecond',NEW.recorded_at)
 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN NEW;
END """
CMT_END_GUARD_CLOCK = r"""
DECLARE m public.cmt_mandates;
BEGIN
 NEW.recorded_at:=public.intevia_clock_statement();
 SELECT * INTO m FROM public.cmt_mandates x WHERE x.binding=NEW.mandate AND x.organism_id=NEW.organism_id;
 IF m.binding IS NULL OR m.mandate->>'ends_at' IS NULL
    OR NEW.effective_at IS DISTINCT FROM (m.mandate->>'ends_at')::timestamptz
    OR NEW.effective_at>NEW.recorded_at
    OR NOT public.cmt_live(NEW.mandate,NEW.effective_at-interval '1 microsecond',NEW.recorded_at)
 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN NEW;
END """

# public.cmt_gap_guard()
# Its landed text: 0094_te_committee.py (found there verbatim). Time calls moved: 1 statement.
CMT_GAP_GUARD_LANDED = r"""
DECLARE m public.cmt_mandates; last_ boolean;
BEGIN
 SELECT * INTO STRICT m FROM public.cmt_mandates WHERE binding=NEW.mandate;
 PERFORM 1 FROM public.core_livingorganism WHERE id=m.organism_id FOR UPDATE;
 NEW.recorded_at:=statement_timestamp();
 IF NEW.effective_at>NEW.recorded_at OR pg_trigger_depth()<2 OR NOT (
     public.cmt_pending_cut(NEW.mandate,NEW.effective_at,NEW.recorded_at) OR (NEW.unmet AND EXISTS (
       SELECT FROM public.cmt_refresh_batch b CROSS JOIN LATERAL jsonb_object_keys(b.touches) k
       WHERE b.action_xid=pg_current_xact_id() AND (k::jsonb->>0)::uuid=NEW.mandate
         AND public.cmt_gap_date(NEW.mandate,NEW.requirement,(k::jsonb->>1)::timestamptz,NEW.recorded_at)=NEW.effective_at)))
 OR NOT EXISTS(SELECT FROM public.cmt_composition(m.binding,NEW.effective_at,NEW.recorded_at) c WHERE c.requirement=NEW.requirement
    AND c.unmet=NEW.unmet AND coalesce(c.basis,'{}'::jsonb)=NEW.basis) THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 SELECT x.unmet INTO last_ FROM public.core_cmtcomposition x WHERE x.mandate=m.binding AND x.requirement=NEW.requirement
 AND x.effective_at<=NEW.effective_at ORDER BY x.effective_at DESC,x.recorded_at DESC LIMIT 1;
 IF coalesce(last_,false)=NEW.unmet THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN NEW;
END """
CMT_GAP_GUARD_CLOCK = r"""
DECLARE m public.cmt_mandates; last_ boolean;
BEGIN
 SELECT * INTO STRICT m FROM public.cmt_mandates WHERE binding=NEW.mandate;
 PERFORM 1 FROM public.core_livingorganism WHERE id=m.organism_id FOR UPDATE;
 NEW.recorded_at:=public.intevia_clock_statement();
 IF NEW.effective_at>NEW.recorded_at OR pg_trigger_depth()<2 OR NOT (
     public.cmt_pending_cut(NEW.mandate,NEW.effective_at,NEW.recorded_at) OR (NEW.unmet AND EXISTS (
       SELECT FROM public.cmt_refresh_batch b CROSS JOIN LATERAL jsonb_object_keys(b.touches) k
       WHERE b.action_xid=pg_current_xact_id() AND (k::jsonb->>0)::uuid=NEW.mandate
         AND public.cmt_gap_date(NEW.mandate,NEW.requirement,(k::jsonb->>1)::timestamptz,NEW.recorded_at)=NEW.effective_at)))
 OR NOT EXISTS(SELECT FROM public.cmt_composition(m.binding,NEW.effective_at,NEW.recorded_at) c WHERE c.requirement=NEW.requirement
    AND c.unmet=NEW.unmet AND coalesce(c.basis,'{}'::jsonb)=NEW.basis) THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 SELECT x.unmet INTO last_ FROM public.core_cmtcomposition x WHERE x.mandate=m.binding AND x.requirement=NEW.requirement
 AND x.effective_at<=NEW.effective_at ORDER BY x.effective_at DESC,x.recorded_at DESC LIMIT 1;
 IF coalesce(last_,false)=NEW.unmet THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN NEW;
END """

# public.cmt_record_dated_end(bigint, uuid, bigint)
# Its landed text: 0094_te_committee.py (found there verbatim). Time calls moved: 1 statement.
CMT_RECORD_DATED_END_LANDED = r"""
DECLARE cut_ timestamptz:=statement_timestamp(); writes integer;
BEGIN
 PERFORM 1 FROM public.core_livingorganism WHERE id=p_lo FOR UPDATE;
 IF public.disc_member_state(p_lo,p_enterer,cut_) IS DISTINCT FROM 'ACTIVE' OR NOT EXISTS(
    SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=p_enterer AND i.access_state='active' AND u.is_active)
 THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 INSERT INTO public.core_cmtmandateend(mandate,organism_id,effective_at,recorded_at,enterer)
 SELECT d.source_ref::uuid,p_lo,d.due_at,cut_,p_enterer FROM public.te_cmt_end_due(p_lo,cut_) d
 WHERE d.source_ref=p_binding::text
 ON CONFLICT(mandate) DO NOTHING;
 GET DIAGNOSTICS writes=ROW_COUNT; RETURN writes=1;
END """
CMT_RECORD_DATED_END_CLOCK = r"""
DECLARE cut_ timestamptz:=public.intevia_clock_statement(); writes integer;
BEGIN
 PERFORM 1 FROM public.core_livingorganism WHERE id=p_lo FOR UPDATE;
 IF public.disc_member_state(p_lo,p_enterer,cut_) IS DISTINCT FROM 'ACTIVE' OR NOT EXISTS(
    SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=p_enterer AND i.access_state='active' AND u.is_active)
 THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 INSERT INTO public.core_cmtmandateend(mandate,organism_id,effective_at,recorded_at,enterer)
 SELECT d.source_ref::uuid,p_lo,d.due_at,cut_,p_enterer FROM public.te_cmt_end_due(p_lo,cut_) d
 WHERE d.source_ref=p_binding::text
 ON CONFLICT(mandate) DO NOTHING;
 GET DIAGNOSTICS writes=ROW_COUNT; RETURN writes=1;
END """

# public.c2_guard_recovery()
# Its landed text: 0095_te_c2_join.py (assembled by that migration). Time calls moved: 1 wall.
C2_GUARD_RECOVERY_LANDED = r"""
DECLARE parent public.core_livingorganismevent; lo public.core_livingorganism; known timestamptz:=clock_timestamp();
        v integer; linked boolean:=false; a public.core_coverageassessment;
BEGIN
 SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE id=NEW.living_organism_id;
 SELECT * INTO parent FROM public.core_livingorganismevent WHERE id=NEW.predecessor_id;
 IF NEW.action='K2_COORDINATOR_SEATED' THEN
    RAISE EXCEPTION 'A Coordinator recovery appointment must restore the organisation' USING ERRCODE='23514';
 END IF;
 IF NEW.action NOT IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE','K2_DISSOLVE','K2_RESTORED') THEN
    IF parent.action IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE','K2_DISSOLVE') AND
       NOT (NEW.action='DISSOLVE' AND NEW.resulting_state='CLOSED' AND EXISTS(
         SELECT FROM public.core_organismmembershiptransition m JOIN public.core_organismmembership x ON x.id=m.membership_id
         WHERE x.living_organism_id=lo.id AND NEW.evidence_reference='membership-ending:'||m.event_uuid
         AND m.resulting_state='ENDED' AND m.effective_at=NEW.effective_at)
         AND NOT EXISTS(SELECT FROM public.core_organismmembership x WHERE x.living_organism_id=lo.id AND
          public.s015_fold_effective_state('core_organismmembership',x.id,NEW.effective_at,known) IN ('ACTIVE','PROBATIONARY'))) THEN
       RAISE EXCEPTION 'The recovery episode cannot be replaced by an ordinary organisation event' USING ERRCODE='23514'; END IF;
    RETURN NULL;
 END IF;
 IF lo.platform_root OR NEW.entry_mode<>'COMPOSED' OR NEW.actor_state<>'NONE' THEN
    RAISE EXCEPTION 'K-2 requires a composed ordinary organisation event' USING ERRCODE='23514'; END IF;
 v:=public.c2_roster_version(lo.id,NEW.effective_at,known);
 IF NEW.action='WITHOUT_COORDINATOR' THEN
    SELECT EXISTS(SELECT FROM public.core_coverageassessment x JOIN public.core_essentialcoveragecase c ON c.id=x.case_id
      JOIN public.core_restrictedcontinuityevent rc ON rc.triggering_assessment_id=x.id
      WHERE c.living_organism_id=lo.id AND public.te_made_here('core_coverageassessment',x.event_uuid::text) AND public.c2_result(lo.id,x.roster_version,x.evaluated_state_at,x.evaluated_known_at,true)='UNSATISFIED'
      AND x.effective_at=NEW.effective_at AND rc.resulting_state='RESTRICTED'
      AND (x.evidence_reference=NEW.evidence_reference OR x.authority_decision_reference=NEW.authority_decision_reference))
      INTO linked;
    linked:=linked AND NEW.prior_state='ACTIVE'
      AND NEW.resulting_state='RESTRICTED_CONTINUITY'
      AND public.c2_is_coordinator_vacancy(lo.id,v,NEW.effective_at,known)
      AND NEW.effective_until=NEW.effective_at+interval '720 hours'
      AND EXISTS(SELECT FROM public.core_organismmembership x WHERE x.living_organism_id=lo.id AND
          public.s015_fold_effective_state('core_organismmembership',x.id,NEW.effective_at,known) IN ('ACTIVE','PROBATIONARY'));
    linked:=linked AND (
      EXISTS(SELECT FROM public.core_organismmembershiptransition e JOIN public.core_organismmembership m ON m.id=e.membership_id
        JOIN public.core_authoritydecision d ON d.decision_reference=e.authority_decision_reference
        WHERE m.living_organism_id=lo.id AND NEW.evidence_reference='membership-ending:'||e.event_uuid
        AND e.effective_at=NEW.effective_at AND d.target->'confirmed'='true'::jsonb AND e.resulting_state='ENDED'
        AND d.outcome='QUALIFIED' AND e.xmin=pg_current_xact_id()::xid AND d.decision_reference=NEW.authority_decision_reference)
      OR EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent e
        JOIN public.core_contextualroleassignment ra ON ra.id=e.assignment_id
        JOIN public.core_organismmembership m ON m.id=ra.membership_id
        JOIN public.core_organismroledefinition r ON r.id=ra.role_definition_id
        WHERE m.living_organism_id=lo.id AND r.code='COORDINATOR' AND r.scope='LIVING_ORGANISM'
        AND NEW.evidence_reference='office:'||e.event_uuid AND e.effective_at=NEW.effective_at
        AND e.prior_state='ACTIVE' AND e.resulting_state<>'ACTIVE' AND e.xmin=pg_current_xact_id()::xid
        AND e.authority_decision_reference=NEW.authority_decision_reference)
      OR EXISTS(SELECT FROM public.core_organismmembershiptransition e JOIN public.core_organismmembership m ON m.id=e.membership_id
        WHERE m.living_organism_id=lo.id AND NEW.evidence_reference='membership:'||e.event_uuid
        AND e.effective_at=NEW.effective_at AND e.prior_state='ACTIVE' AND e.resulting_state<>'ACTIVE'
        AND e.xmin=pg_current_xact_id()::xid AND e.authority_decision_reference=NEW.authority_decision_reference)
      OR EXISTS(SELECT FROM public.c2_timed_input i WHERE i.lo_id=lo.id AND NEW.evidence_reference='c2-boundary:'||i.source
        AND i.due=NEW.effective_at AND EXISTS(SELECT FROM public.c2_occupants(lo.id,i.due-interval '1 microsecond',known)
            WHERE role_scope='LIVING_ORGANISM' AND role_code='COORDINATOR')));
 ELSIF NEW.action='K2_RESTORED' THEN
    SELECT x.* INTO a FROM public.core_coverageassessment x JOIN public.core_essentialcoveragecase c ON c.id=x.case_id
    WHERE c.living_organism_id=lo.id AND NEW.evidence_reference='coverage-assessment:'||x.event_uuid
      AND x.xmin=pg_current_xact_id()::xid AND x.effective_at=NEW.effective_at;
    linked:=a.id IS NOT NULL AND NEW.effective_until IS NULL
      AND a.roster_version=v AND NEW.resulting_state='ACTIVE'
      AND public.c2_result(lo.id,v,a.evaluated_state_at,a.evaluated_known_at,true)='SATISFIED'
      AND public.c2_result(lo.id,v,NEW.effective_at,known,true)='SATISFIED'
      AND public.c2_result(lo.id,public.c2_roster_version(lo.id,known,known),known,known,true)='SATISFIED'
      AND EXISTS(
       SELECT FROM public.core_contextualroleassignmentstateevent e JOIN public.core_authoritydecision d ON d.decision_reference=e.authority_decision_reference
       JOIN public.core_contextualroleassignment h ON h.id=e.assignment_id JOIN public.core_organismroledefinition r ON r.id=h.role_definition_id
       JOIN public.core_organismmembership m ON m.id=h.membership_id
       WHERE a.evidence_reference='office:'||e.event_uuid AND d.outcome='QUALIFIED'
       AND m.living_organism_id=lo.id AND e.action='ASSIGN' AND r.scope='LIVING_ORGANISM'
       AND e.xmin=pg_current_xact_id()::xid AND e.effective_at=NEW.effective_at AND e.authority_decision_reference=NEW.authority_decision_reference
       AND parent.action IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE')
         AND NEW.effective_at<parent.effective_until AND NEW.recorded_at<parent.effective_until
         AND d.target->>'k2_episode'=CASE WHEN parent.action='WITHOUT_COORDINATOR' THEN parent.event_uuid::text
               ELSE substring(parent.evidence_reference FROM 4) END
         AND r.code='COORDINATOR' AND m.identity_id=d.actor_id
         AND EXISTS(SELECT FROM public.c2_occupants(lo.id,NEW.effective_at,a.evaluated_known_at) o WHERE o.membership_id=m.id
                    AND o.role_scope='LIVING_ORGANISM' AND o.role_code='COORDINATOR')
         AND EXISTS(SELECT FROM public.c2_occupants(lo.id,known,known) o WHERE o.membership_id=m.id
                    AND o.role_scope='LIVING_ORGANISM' AND o.role_code='COORDINATOR'));
 ELSE
    linked:=NEW.evidence_reference='k2:'||parent.event_uuid AND NEW.authority_decision_reference=parent.authority_decision_reference
      AND NEW.authority_basis_id=parent.authority_basis_id AND NEW.enterer_identity_id=parent.enterer_identity_id AND (
      (NEW.action='K2_DEACTIVATE' AND NEW.resulting_state='DORMANT' AND NEW.effective_until=NEW.effective_at+interval '720 hours'
       AND parent.action='WITHOUT_COORDINATOR' AND NEW.effective_at=parent.effective_until)
      OR (NEW.action='K2_DISSOLVE' AND parent.action='K2_DEACTIVATE' AND NEW.resulting_state='CLOSED'
          AND NEW.effective_at=parent.effective_until AND NEW.effective_until IS NULL));
 END IF;
 IF linked IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'C2 recovery evidence, coverage or countdown does not match the requested transition' USING ERRCODE='23514';
 END IF; RETURN NULL;
END """
C2_GUARD_RECOVERY_CLOCK = r"""
DECLARE parent public.core_livingorganismevent; lo public.core_livingorganism; known timestamptz:=public.intevia_clock_instant();
        v integer; linked boolean:=false; a public.core_coverageassessment;
BEGIN
 SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE id=NEW.living_organism_id;
 SELECT * INTO parent FROM public.core_livingorganismevent WHERE id=NEW.predecessor_id;
 IF NEW.action='K2_COORDINATOR_SEATED' THEN
    RAISE EXCEPTION 'A Coordinator recovery appointment must restore the organisation' USING ERRCODE='23514';
 END IF;
 IF NEW.action NOT IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE','K2_DISSOLVE','K2_RESTORED') THEN
    IF parent.action IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE','K2_DISSOLVE') AND
       NOT (NEW.action='DISSOLVE' AND NEW.resulting_state='CLOSED' AND EXISTS(
         SELECT FROM public.core_organismmembershiptransition m JOIN public.core_organismmembership x ON x.id=m.membership_id
         WHERE x.living_organism_id=lo.id AND NEW.evidence_reference='membership-ending:'||m.event_uuid
         AND m.resulting_state='ENDED' AND m.effective_at=NEW.effective_at)
         AND NOT EXISTS(SELECT FROM public.core_organismmembership x WHERE x.living_organism_id=lo.id AND
          public.s015_fold_effective_state('core_organismmembership',x.id,NEW.effective_at,known) IN ('ACTIVE','PROBATIONARY'))) THEN
       RAISE EXCEPTION 'The recovery episode cannot be replaced by an ordinary organisation event' USING ERRCODE='23514'; END IF;
    RETURN NULL;
 END IF;
 IF lo.platform_root OR NEW.entry_mode<>'COMPOSED' OR NEW.actor_state<>'NONE' THEN
    RAISE EXCEPTION 'K-2 requires a composed ordinary organisation event' USING ERRCODE='23514'; END IF;
 v:=public.c2_roster_version(lo.id,NEW.effective_at,known);
 IF NEW.action='WITHOUT_COORDINATOR' THEN
    SELECT EXISTS(SELECT FROM public.core_coverageassessment x JOIN public.core_essentialcoveragecase c ON c.id=x.case_id
      JOIN public.core_restrictedcontinuityevent rc ON rc.triggering_assessment_id=x.id
      WHERE c.living_organism_id=lo.id AND public.te_made_here('core_coverageassessment',x.event_uuid::text) AND public.c2_result(lo.id,x.roster_version,x.evaluated_state_at,x.evaluated_known_at,true)='UNSATISFIED'
      AND x.effective_at=NEW.effective_at AND rc.resulting_state='RESTRICTED'
      AND (x.evidence_reference=NEW.evidence_reference OR x.authority_decision_reference=NEW.authority_decision_reference))
      INTO linked;
    linked:=linked AND NEW.prior_state='ACTIVE'
      AND NEW.resulting_state='RESTRICTED_CONTINUITY'
      AND public.c2_is_coordinator_vacancy(lo.id,v,NEW.effective_at,known)
      AND NEW.effective_until=NEW.effective_at+interval '720 hours'
      AND EXISTS(SELECT FROM public.core_organismmembership x WHERE x.living_organism_id=lo.id AND
          public.s015_fold_effective_state('core_organismmembership',x.id,NEW.effective_at,known) IN ('ACTIVE','PROBATIONARY'));
    linked:=linked AND (
      EXISTS(SELECT FROM public.core_organismmembershiptransition e JOIN public.core_organismmembership m ON m.id=e.membership_id
        JOIN public.core_authoritydecision d ON d.decision_reference=e.authority_decision_reference
        WHERE m.living_organism_id=lo.id AND NEW.evidence_reference='membership-ending:'||e.event_uuid
        AND e.effective_at=NEW.effective_at AND d.target->'confirmed'='true'::jsonb AND e.resulting_state='ENDED'
        AND d.outcome='QUALIFIED' AND e.xmin=pg_current_xact_id()::xid AND d.decision_reference=NEW.authority_decision_reference)
      OR EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent e
        JOIN public.core_contextualroleassignment ra ON ra.id=e.assignment_id
        JOIN public.core_organismmembership m ON m.id=ra.membership_id
        JOIN public.core_organismroledefinition r ON r.id=ra.role_definition_id
        WHERE m.living_organism_id=lo.id AND r.code='COORDINATOR' AND r.scope='LIVING_ORGANISM'
        AND NEW.evidence_reference='office:'||e.event_uuid AND e.effective_at=NEW.effective_at
        AND e.prior_state='ACTIVE' AND e.resulting_state<>'ACTIVE' AND e.xmin=pg_current_xact_id()::xid
        AND e.authority_decision_reference=NEW.authority_decision_reference)
      OR EXISTS(SELECT FROM public.core_organismmembershiptransition e JOIN public.core_organismmembership m ON m.id=e.membership_id
        WHERE m.living_organism_id=lo.id AND NEW.evidence_reference='membership:'||e.event_uuid
        AND e.effective_at=NEW.effective_at AND e.prior_state='ACTIVE' AND e.resulting_state<>'ACTIVE'
        AND e.xmin=pg_current_xact_id()::xid AND e.authority_decision_reference=NEW.authority_decision_reference)
      OR EXISTS(SELECT FROM public.c2_timed_input i WHERE i.lo_id=lo.id AND NEW.evidence_reference='c2-boundary:'||i.source
        AND i.due=NEW.effective_at AND EXISTS(SELECT FROM public.c2_occupants(lo.id,i.due-interval '1 microsecond',known)
            WHERE role_scope='LIVING_ORGANISM' AND role_code='COORDINATOR')));
 ELSIF NEW.action='K2_RESTORED' THEN
    SELECT x.* INTO a FROM public.core_coverageassessment x JOIN public.core_essentialcoveragecase c ON c.id=x.case_id
    WHERE c.living_organism_id=lo.id AND NEW.evidence_reference='coverage-assessment:'||x.event_uuid
      AND x.xmin=pg_current_xact_id()::xid AND x.effective_at=NEW.effective_at;
    linked:=a.id IS NOT NULL AND NEW.effective_until IS NULL
      AND a.roster_version=v AND NEW.resulting_state='ACTIVE'
      AND public.c2_result(lo.id,v,a.evaluated_state_at,a.evaluated_known_at,true)='SATISFIED'
      AND public.c2_result(lo.id,v,NEW.effective_at,known,true)='SATISFIED'
      AND public.c2_result(lo.id,public.c2_roster_version(lo.id,known,known),known,known,true)='SATISFIED'
      AND EXISTS(
       SELECT FROM public.core_contextualroleassignmentstateevent e JOIN public.core_authoritydecision d ON d.decision_reference=e.authority_decision_reference
       JOIN public.core_contextualroleassignment h ON h.id=e.assignment_id JOIN public.core_organismroledefinition r ON r.id=h.role_definition_id
       JOIN public.core_organismmembership m ON m.id=h.membership_id
       WHERE a.evidence_reference='office:'||e.event_uuid AND d.outcome='QUALIFIED'
       AND m.living_organism_id=lo.id AND e.action='ASSIGN' AND r.scope='LIVING_ORGANISM'
       AND e.xmin=pg_current_xact_id()::xid AND e.effective_at=NEW.effective_at AND e.authority_decision_reference=NEW.authority_decision_reference
       AND parent.action IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE')
         AND NEW.effective_at<parent.effective_until AND NEW.recorded_at<parent.effective_until
         AND d.target->>'k2_episode'=CASE WHEN parent.action='WITHOUT_COORDINATOR' THEN parent.event_uuid::text
               ELSE substring(parent.evidence_reference FROM 4) END
         AND r.code='COORDINATOR' AND m.identity_id=d.actor_id
         AND EXISTS(SELECT FROM public.c2_occupants(lo.id,NEW.effective_at,a.evaluated_known_at) o WHERE o.membership_id=m.id
                    AND o.role_scope='LIVING_ORGANISM' AND o.role_code='COORDINATOR')
         AND EXISTS(SELECT FROM public.c2_occupants(lo.id,known,known) o WHERE o.membership_id=m.id
                    AND o.role_scope='LIVING_ORGANISM' AND o.role_code='COORDINATOR'));
 ELSE
    linked:=NEW.evidence_reference='k2:'||parent.event_uuid AND NEW.authority_decision_reference=parent.authority_decision_reference
      AND NEW.authority_basis_id=parent.authority_basis_id AND NEW.enterer_identity_id=parent.enterer_identity_id AND (
      (NEW.action='K2_DEACTIVATE' AND NEW.resulting_state='DORMANT' AND NEW.effective_until=NEW.effective_at+interval '720 hours'
       AND parent.action='WITHOUT_COORDINATOR' AND NEW.effective_at=parent.effective_until)
      OR (NEW.action='K2_DISSOLVE' AND parent.action='K2_DEACTIVATE' AND NEW.resulting_state='CLOSED'
          AND NEW.effective_at=parent.effective_until AND NEW.effective_until IS NULL));
 END IF;
 IF linked IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'C2 recovery evidence, coverage or countdown does not match the requested transition' USING ERRCODE='23514';
 END IF; RETURN NULL;
END """

# public.c2_notice_input(bigint, text)
# Its landed text: 0095_te_c2_join.py (assembled by that migration). Time calls moved: 1 statement.
C2_NOTICE_INPUT_LANDED = r"""
DECLARE input record; lo public.core_livingorganism; founding public.core_livingorganismevent;
        v integer; key_ text; payload text; current_lo text; known timestamptz;
BEGIN
 SELECT * INTO lo FROM public.core_livingorganism WHERE id=lo_id_;
 IF lo.id IS NULL OR lo.platform_root THEN RETURN false; END IF;
 PERFORM public.s015_register_organism_lock(lo.organism_id);
 known:=statement_timestamp();
 SELECT * INTO input FROM public.c2_timed_input i WHERE i.lo_id=lo_id_ AND i.source=source_ AND i.due<=known LIMIT 1;
 IF NOT FOUND THEN RETURN false; END IF;
 key_:='c2-boundary:'||source_;
 SELECT * INTO founding FROM public.core_livingorganismevent WHERE living_organism_id=lo_id_ AND sequence=1;
 IF founding.id IS NULL OR input.due<founding.effective_at THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM public.core_coverageassessment a JOIN public.core_essentialcoveragecase c ON c.id=a.case_id
   WHERE c.living_organism_id=lo_id_ AND a.evidence_reference=key_ AND a.effective_at=input.due) THEN RETURN false; END IF;
 v:=public.c2_roster_version(lo_id_,input.due,known);
 IF v IS NULL THEN RETURN false; END IF;
 payload:=encode(sha256(convert_to(lo.organism_id::text||':'||key_,'UTF8')),'hex');
 PERFORM public.c2_publish(lo_id_,input.due,input.due,known,founding.enterer_identity_id,founding.authority_basis_id,
   founding.authority_decision_reference,key_,key_,payload);
 current_lo:=public.s015_fold_effective_state('core_livingorganism',lo_id_,input.due,known);
 IF current_lo='ACTIVE' AND public.c2_is_coordinator_vacancy(lo_id_,v,input.due,known)
   AND EXISTS(SELECT FROM public.c2_occupants(lo_id_,input.due-interval '1 microsecond',known)
              WHERE role_scope='LIVING_ORGANISM' AND role_code='COORDINATOR')
   AND EXISTS(SELECT FROM public.core_organismmembership m WHERE m.living_organism_id=lo_id_ AND
        public.s015_fold_effective_state('core_organismmembership',m.id,input.due,known) IN ('ACTIVE','PROBATIONARY')) THEN
     PERFORM public.s015_append_event('core_livingorganism',lo_id_,'WITHOUT_COORDINATOR','RESTRICTED_CONTINUITY',
       input.due,input.due,known,founding.enterer_identity_id,founding.authority_basis_id,founding.authority_decision_reference,
       key_,key_,payload,'{}',input.due+interval '720 hours');
 END IF;
 RETURN true;
END """
C2_NOTICE_INPUT_CLOCK = r"""
DECLARE input record; lo public.core_livingorganism; founding public.core_livingorganismevent;
        v integer; key_ text; payload text; current_lo text; known timestamptz;
BEGIN
 SELECT * INTO lo FROM public.core_livingorganism WHERE id=lo_id_;
 IF lo.id IS NULL OR lo.platform_root THEN RETURN false; END IF;
 PERFORM public.s015_register_organism_lock(lo.organism_id);
 known:=public.intevia_clock_statement();
 SELECT * INTO input FROM public.c2_timed_input i WHERE i.lo_id=lo_id_ AND i.source=source_ AND i.due<=known LIMIT 1;
 IF NOT FOUND THEN RETURN false; END IF;
 key_:='c2-boundary:'||source_;
 SELECT * INTO founding FROM public.core_livingorganismevent WHERE living_organism_id=lo_id_ AND sequence=1;
 IF founding.id IS NULL OR input.due<founding.effective_at THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM public.core_coverageassessment a JOIN public.core_essentialcoveragecase c ON c.id=a.case_id
   WHERE c.living_organism_id=lo_id_ AND a.evidence_reference=key_ AND a.effective_at=input.due) THEN RETURN false; END IF;
 v:=public.c2_roster_version(lo_id_,input.due,known);
 IF v IS NULL THEN RETURN false; END IF;
 payload:=encode(sha256(convert_to(lo.organism_id::text||':'||key_,'UTF8')),'hex');
 PERFORM public.c2_publish(lo_id_,input.due,input.due,known,founding.enterer_identity_id,founding.authority_basis_id,
   founding.authority_decision_reference,key_,key_,payload);
 current_lo:=public.s015_fold_effective_state('core_livingorganism',lo_id_,input.due,known);
 IF current_lo='ACTIVE' AND public.c2_is_coordinator_vacancy(lo_id_,v,input.due,known)
   AND EXISTS(SELECT FROM public.c2_occupants(lo_id_,input.due-interval '1 microsecond',known)
              WHERE role_scope='LIVING_ORGANISM' AND role_code='COORDINATOR')
   AND EXISTS(SELECT FROM public.core_organismmembership m WHERE m.living_organism_id=lo_id_ AND
        public.s015_fold_effective_state('core_organismmembership',m.id,input.due,known) IN ('ACTIVE','PROBATIONARY')) THEN
     PERFORM public.s015_append_event('core_livingorganism',lo_id_,'WITHOUT_COORDINATOR','RESTRICTED_CONTINUITY',
       input.due,input.due,known,founding.enterer_identity_id,founding.authority_basis_id,founding.authority_decision_reference,
       key_,key_,payload,'{}',input.due+interval '720 hours');
 END IF;
 RETURN true;
END """

# public.c2_publish(bigint, timestamp with time zone, timestamp with time zone, timestamp with time zone, bigint, bigint, text, text, text, text)
# Its landed text: 0095_te_c2_join.py (assembled by that migration). Time calls moved: 1 statement.
C2_PUBLISH_LANDED = r"""
DECLARE v integer; case_id_ bigint; cont bigint; result_ text; covered boolean; a bigint; known timestamptz:=statement_timestamp();
BEGIN
 SELECT id INTO STRICT case_id_ FROM public.core_essentialcoveragecase WHERE living_organism_id=lo_id FOR UPDATE;
 v:=public.c2_roster_version(lo_id,at_time,known);
 IF v IS NULL THEN RAISE EXCEPTION 'Coverage could not be recorded. Check the founding assessment and effective date.'
 USING ERRCODE='23514'; END IF;
 result_:=public.c2_result(lo_id,v,at_time,known);
 covered:=public.c2_result(lo_id,v,at_time,known,true)='SATISFIED';
 a:=public.s015_append_event('core_essentialcoveragecase',case_id_,'ASSESS',result_,at_time,occurred,received,actor,basis,decision,
    evidence,request_key,payload,jsonb_build_object('roster_version',v,'evaluated_state_at',at_time,'evaluated_known_at',known,
    'determiner_capacity_reference',decision));
 SELECT id INTO cont FROM public.core_restrictedcontinuitycase WHERE living_organism_id=lo_id;
 IF cont IS NULL AND NOT covered THEN
    INSERT INTO public.core_restrictedcontinuitycase(case_uuid,living_organism_id) VALUES(gen_random_uuid(),lo_id) RETURNING id INTO cont;
 END IF;
 IF cont IS NOT NULL THEN
    PERFORM public.s015_append_event('core_restrictedcontinuitycase',cont,'REASSESS',CASE WHEN covered
      THEN 'NOT_RESTRICTED' ELSE 'RESTRICTED' END,at_time,occurred,received,actor,basis,decision,evidence,request_key,payload,
      jsonb_build_object('permitted_measures_reference',CASE WHEN covered THEN 'C2:ordinary-governed-routes'
       ELSE 'C2:preserve-leave-and-qualified-recovery' END,'prohibited_effects_reference',CASE WHEN covered
       THEN 'C2:no-extra-authority' ELSE 'C2:no-fallback-or-ordinary-execution' END,
       'triggering_assessment_id',a,'accountable_actor_id',actor));
 END IF;
 RETURN a;
END """
C2_PUBLISH_CLOCK = r"""
DECLARE v integer; case_id_ bigint; cont bigint; result_ text; covered boolean; a bigint; known timestamptz:=public.intevia_clock_statement();
BEGIN
 SELECT id INTO STRICT case_id_ FROM public.core_essentialcoveragecase WHERE living_organism_id=lo_id FOR UPDATE;
 v:=public.c2_roster_version(lo_id,at_time,known);
 IF v IS NULL THEN RAISE EXCEPTION 'Coverage could not be recorded. Check the founding assessment and effective date.'
 USING ERRCODE='23514'; END IF;
 result_:=public.c2_result(lo_id,v,at_time,known);
 covered:=public.c2_result(lo_id,v,at_time,known,true)='SATISFIED';
 a:=public.s015_append_event('core_essentialcoveragecase',case_id_,'ASSESS',result_,at_time,occurred,received,actor,basis,decision,
    evidence,request_key,payload,jsonb_build_object('roster_version',v,'evaluated_state_at',at_time,'evaluated_known_at',known,
    'determiner_capacity_reference',decision));
 SELECT id INTO cont FROM public.core_restrictedcontinuitycase WHERE living_organism_id=lo_id;
 IF cont IS NULL AND NOT covered THEN
    INSERT INTO public.core_restrictedcontinuitycase(case_uuid,living_organism_id) VALUES(gen_random_uuid(),lo_id) RETURNING id INTO cont;
 END IF;
 IF cont IS NOT NULL THEN
    PERFORM public.s015_append_event('core_restrictedcontinuitycase',cont,'REASSESS',CASE WHEN covered
      THEN 'NOT_RESTRICTED' ELSE 'RESTRICTED' END,at_time,occurred,received,actor,basis,decision,evidence,request_key,payload,
      jsonb_build_object('permitted_measures_reference',CASE WHEN covered THEN 'C2:ordinary-governed-routes'
       ELSE 'C2:preserve-leave-and-qualified-recovery' END,'prohibited_effects_reference',CASE WHEN covered
       THEN 'C2:no-extra-authority' ELSE 'C2:no-fallback-or-ordinary-execution' END,
       'triggering_assessment_id',a,'accountable_actor_id',actor));
 END IF;
 RETURN a;
END """

FUNCTIONS = (
    ("public.c108_restore_receipt()", C108_RESTORE_RECEIPT_LANDED, C108_RESTORE_RECEIPT_CLOCK),
    ("public.c108_restore_seat_guard()", C108_RESTORE_SEAT_GUARD_LANDED, C108_RESTORE_SEAT_GUARD_CLOCK),
    ("public.cq3_check_committed(core_cqact)", CQ3_CHECK_COMMITTED_LANDED, CQ3_CHECK_COMMITTED_CLOCK),
    ("public.cq_input()", CQ_INPUT_LANDED, CQ_INPUT_CLOCK),
    ("public.c108_apply(uuid)", C108_APPLY_LANDED, C108_APPLY_CLOCK),
    ("public.te_limits_at(timestamp with time zone)", TE_LIMITS_AT_LANDED, TE_LIMITS_AT_CLOCK),
    ("public.te_receipt_guard()", TE_RECEIPT_GUARD_LANDED, TE_RECEIPT_GUARD_CLOCK),
    ("public.c2_source_change()", C2_SOURCE_CHANGE_LANDED, C2_SOURCE_CHANGE_CLOCK),
    ("public.elect_cancel_pending_office(bigint, uuid)", ELECT_CANCEL_PENDING_OFFICE_LANDED, ELECT_CANCEL_PENDING_OFFICE_CLOCK),
    ("public.elect_record_term_end(bigint, bigint, bigint)", ELECT_RECORD_TERM_END_LANDED, ELECT_RECORD_TERM_END_CLOCK),
    ("public.elect_resignation_coverage(bigint, uuid, bigint)", ELECT_RESIGNATION_COVERAGE_LANDED, ELECT_RESIGNATION_COVERAGE_CLOCK),
    ("public.s015_append_event(text, bigint, text, text, timestamp with time zone, timestamp with time zone, timestamp with time zone, bigint, bigint, text, text, text, text, jsonb, timestamp with time zone, uuid, bigint, integer, bigint, text, text)", S015_APPEND_EVENT_LANDED, S015_APPEND_EVENT_CLOCK),
    ("public.cmt_changed()", CMT_CHANGED_LANDED, CMT_CHANGED_CLOCK),
    ("public.cmt_custody_guard()", CMT_CUSTODY_GUARD_LANDED, CMT_CUSTODY_GUARD_CLOCK),
    ("public.cmt_drain()", CMT_DRAIN_LANDED, CMT_DRAIN_CLOCK),
    ("public.cmt_end_guard()", CMT_END_GUARD_LANDED, CMT_END_GUARD_CLOCK),
    ("public.cmt_gap_guard()", CMT_GAP_GUARD_LANDED, CMT_GAP_GUARD_CLOCK),
    ("public.cmt_record_dated_end(bigint, uuid, bigint)", CMT_RECORD_DATED_END_LANDED, CMT_RECORD_DATED_END_CLOCK),
    ("public.c2_guard_recovery()", C2_GUARD_RECOVERY_LANDED, C2_GUARD_RECOVERY_CLOCK),
    ("public.c2_notice_input(bigint, text)", C2_NOTICE_INPUT_LANDED, C2_NOTICE_INPUT_CLOCK),
    ("public.c2_publish(bigint, timestamp with time zone, timestamp with time zone, timestamp with time zone, bigint, bigint, text, text, text, text)", C2_PUBLISH_LANDED, C2_PUBLISH_CLOCK),
)


def install(apps, schema_editor):
    """Move these functions onto the INTEVIA Clock."""
    clock.convert(schema_editor, FUNCTIONS)


def uninstall(apps, schema_editor):
    """Put the landed functions back."""
    clock.restore(schema_editor, FUNCTIONS)


class Migration(migrations.Migration):
    dependencies = (("core", "0101_clock_storage_0081_0087"),)
    operations = (migrations.RunPython(install, uninstall),)
