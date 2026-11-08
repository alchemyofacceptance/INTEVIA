"""ELECT · evidence that binds a winning ballot to one governed consequence."""

SQL = r"""
CREATE FUNCTION public.elect_secret() RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
 SELECT '{"origin":"ELECTION","scope":"BALLOT","value":"TOTALS_ONLY","basis":"HG:2026-11-02:secret-election"}'::jsonb
$$;

CREATE FUNCTION public.elect_withdrawn(p_open public.core_cqact,p_cut timestamptz) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT FROM jsonb_array_elements(p_open.facts->'slate') s
   JOIN public.core_cqact w ON w.thread_uuid=p_open.thread_uuid AND w.operation='ELECT_WITHDRAW'
     AND w.request->>'nomination'=s->>'nomination' WHERE w.effective_at<=p_cut)
 OR EXISTS(SELECT FROM jsonb_array_elements(p_open.facts->'slate') s JOIN public.core_contextualroleassignment ra
   ON ra.assignment_uuid::text=s->>'assignment' JOIN public.core_officeappointmentconsent c ON c.assignment_id=ra.id
   WHERE c.action IN ('DECLINE','WITHDRAW') AND greatest(c.effective_at,c.recorded_at)<=p_cut)
$$;

CREATE FUNCTION public.elect_vacant(p_plan jsonb,p_person bigint,p_cut timestamptz) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT CASE WHEN p_plan->>'type'='SEAT' THEN NOT EXISTS(
  SELECT FROM public.core_circleparticipation p
  WHERE p.circle_id=(p_plan->>'circle')::bigint AND (p.seat_id=p_plan->>'place' OR p.membership_id=p_person)
   AND NOT EXISTS(SELECT FROM public.core_circleparticipationend x WHERE x.participation_id=p.id AND x.effective_at<=p_cut))
 ELSE NOT EXISTS(SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
  JOIN public.core_organismroledefinition target ON target.id=(p_plan->>'role_id')::bigint
  JOIN public.core_contextualroleassignmentstateevent h ON h.assignment_id=a.id AND h.action='ASSIGN'
  WHERE d.living_organism_id=target.living_organism_id AND d.code=target.code AND d.scope=target.scope
   AND (h.effective_at>p_cut OR public.s015_fold_effective_state('core_contextualroleassignment',a.id,p_cut,p_cut)='ACTIVE')
   AND (h.effective_until IS NULL OR h.effective_until>p_cut)) END
$$;

CREATE FUNCTION public.elect_settlement(p_act public.core_cqact,p_open public.core_cqact,p_att jsonb,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE plan jsonb; candidate jsonb; winner_ text:=p_act.facts->'count'->>'winner';
BEGIN
 IF NOT public.cq3_qualified(p_act.facts->'count'->>'kind') THEN RETURN '{"disposition":"NO_AUTHORITY"}'; END IF;
 plan:=public.elect_plan(p_act.organism_id,p_act.board_uuid,p_att,p_cut);
 SELECT s INTO candidate FROM jsonb_array_elements(p_open.facts->'slate') s WHERE s->>'person'=winner_;
 IF plan IS DISTINCT FROM p_open.facts->'mandate' OR candidate IS NULL OR NOT EXISTS(
    SELECT FROM jsonb_array_elements(public.elect_slate(p_act.thread_uuid,plan,p_cut)) s WHERE s=candidate)
    OR NOT public.elect_vacant(plan,(candidate->>'member')::bigint,p_cut)
    OR (plan->'term'->>'end')::timestamptz<=p_cut THEN
    RETURN jsonb_build_object('disposition','NO_EFFECT','reason','The target, term, provision or consent changed. Call a fresh election.'); END IF;
 RETURN jsonb_build_object('disposition','APPLIED','domain_plan',plan||jsonb_build_object('winner',candidate,
   'ballot',p_open.act_uuid,'start',greatest((plan->'term'->>'start')::timestamptz,p_cut),
   'end',plan->'term'->'end','result',p_act.act_uuid),
   'result_authority',jsonb_build_object('kind','CQ_RESULT','route',plan->>'route','reference',p_act.act_uuid));
END $$;

-- Single testable predicate. A qualified result binds the person, place and term.
CREATE FUNCTION public.elect_authorises(p_result uuid,p_owner bigint,p_person uuid,p_place text,p_start timestamptz,p_end timestamptz)
RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT FROM public.core_cqact r JOIN public.core_cqact o ON o.act_uuid=(r.request->>'ballot')::uuid
  CROSS JOIN LATERAL (SELECT r.facts->'domain_plan' AS plan) d
  WHERE r.act_uuid=p_result AND r.organism_id=p_owner AND r.operation='SETTLE'
   AND r.facts->>'event_kind'='ELECTION' AND r.facts->>'disposition'='APPLIED' AND r.facts->'count'->>'kind'='QUALIFIED'
   AND o.organism_id=r.organism_id AND o.board_uuid=r.board_uuid AND o.thread_uuid=r.thread_uuid AND o.facts->>'event_kind'='ELECTION'
   AND d.plan->>'domain'='ELECTION' AND d.plan->>'result'=r.act_uuid::text AND d.plan->>'ballot'=o.act_uuid::text
   AND (d.plan->>'start')::timestamptz=p_start AND (d.plan->>'end')::timestamptz IS NOT DISTINCT FROM p_end
   AND d.plan->'winner'->>'person'=p_person::text AND d.plan->>'place'=p_place -- ELECT_EXACT_PERSON_PLACE
   AND r.facts->'count'->>'winner'=d.plan->'winner'->>'person' AND o.facts->'slate' @> jsonb_build_array(d.plan->'winner'))
$$;

CREATE FUNCTION public.elect_office_source(p_assignment bigint,p_event uuid,p_enterer bigint,p_decision text,
 p_start timestamptz,p_end timestamptz) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT FROM public.core_cqact i JOIN public.core_contextualroleassignment a ON a.id=p_assignment
  JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
  JOIN public.core_organismmembership m ON m.id=a.membership_id JOIN public.core_identity person ON person.id=m.identity_id
  CROSS JOIN LATERAL (SELECT i.facts->'domain_plan' AS plan) domain_
  WHERE i.operation='IMPLEMENT' AND i.act_uuid=p_event AND i.organism_id=d.living_organism_id AND i.actor_id=p_enterer
   AND i.execution_xid=pg_current_xact_id() AND i.authority_source=public.cq3_implement_source(i)
   AND public.disc_decision(i.authority_source,i.fingerprint)=p_decision AND plan->>'type'='OFFICE'
   AND plan->'winner'->>'assignment'=a.assignment_uuid::text
   AND public.elect_authorises((i.request->>'result')::uuid,i.organism_id,person.identity_id,d.code,p_start,p_end))
$$;

CREATE FUNCTION public.elect_apply(p_act public.core_cqact) RETURNS void LANGUAGE plpgsql VOLATILE AS $$
DECLARE plan jsonb:=p_act.facts->'domain_plan'; nom jsonb:=plan->'winner'; q public.core_circlejoinrequest;
 d_id bigint; start_ timestamptz:=(plan->>'start')::timestamptz; end_ timestamptz:=(plan->>'end')::timestamptz;
 ref text:=public.disc_decision(p_act.authority_source,p_act.fingerprint);
 assignment_ public.core_contextualroleassignment; head_ public.core_contextualroleassignmentstateevent; basis_ bigint;
BEGIN
 IF NOT public.elect_authorises((p_act.request->>'result')::uuid,p_act.organism_id,(nom->>'person')::uuid,
    plan->>'place',start_,end_) THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
 IF plan->>'type'='SEAT' THEN
   SELECT * INTO STRICT q FROM public.core_circlejoinrequest WHERE request_uuid=(nom->>'consent')::uuid;
   INSERT INTO public.core_circleadmissiondecision(decision_uuid,request_id,route,constitutional_route,deciding_party,
    deciding_identity_id,self_decision,outcome,authority_decision_reference,settings_basis,seat_rule_uuid,seat_id,
    actor_state,actor_capacity,entry_mode,enterer_identity_id,composing_rule_state,composer_state,occurred_at,effective_at,received_at)
   VALUES(p_act.act_uuid,q.id,'GOVERNANCE_CONSTITUTIONAL','ELECTION','body:LO_MEMBERSHIP',NULL,false,'ACCEPT',ref,
    'rule:'||q.seat_rule_uuid||'@'||(plan->>'seat_version')||'#'||q.seat_id,q.seat_rule_uuid,q.seat_id,
    'LIVING_ORGANISM','STANDING','TRANSPORT',p_act.actor_id,'NA_TRANSPORT','NA_TRANSPORT',p_act.effective_at,start_,p_act.received_at)
   RETURNING id INTO d_id;
   INSERT INTO public.core_circleparticipation(participation_uuid,circle_id,membership_id,seat_rule_uuid,seat_id,decision_id,
    actor_state,actor_capacity,entry_mode,enterer_identity_id,composing_rule_state,composer_state,occurred_at,effective_at,received_at)
   VALUES(p_act.act_uuid,q.circle_id,q.membership_id,q.seat_rule_uuid,q.seat_id,d_id,
    'LIVING_ORGANISM','STANDING','TRANSPORT',p_act.actor_id,'NA_TRANSPORT','NA_TRANSPORT',p_act.effective_at,start_,p_act.received_at);
   IF end_ IS NOT NULL THEN
     INSERT INTO public.core_circleparticipationend(participation_id,actor_state,actor_capacity,entry_mode,enterer_identity_id,
       composing_rule_state,composing_rule_id,composing_rule_reference,composer_state,composer_identity_id,composed_from_event_uuid,
       occurred_at,effective_at,received_at)
     SELECT p.id,'NONE','NA_NO_PARTY','COMPOSED',p_act.actor_id,'REFERENCE',g.id,'elect-term:'||(plan->>'result'),
       'REFERENCE',p_act.actor_id,p_act.act_uuid,p_act.effective_at,end_,p_act.received_at
     FROM public.core_circleparticipation p JOIN public.core_governedrule g ON g.rule_uuid=p.seat_rule_uuid
       AND g.version=(plan->>'seat_version')::int AND g.scope_organism_id=p_act.organism_id
     WHERE p.participation_uuid=p_act.act_uuid;
   END IF;
 ELSE
   SELECT * INTO STRICT assignment_ FROM public.core_contextualroleassignment WHERE assignment_uuid=(nom->>'assignment')::uuid FOR UPDATE;
   SELECT * INTO STRICT head_ FROM public.core_contextualroleassignmentstateevent WHERE assignment_id=assignment_.id ORDER BY sequence DESC LIMIT 1;
   SELECT founding_authority_basis_id INTO STRICT basis_ FROM public.core_livingorganism WHERE id=p_act.organism_id;
   PERFORM public.s015_append_event('core_contextualroleassignment',assignment_.id,'ASSIGN','ACTIVE',start_,
     p_act.effective_at,p_act.received_at,p_act.actor_id,basis_,ref,'elect-result:'||(plan->>'result'),'elect:'||p_act.act_uuid,
     p_act.fingerprint,'{}',end_,p_act.act_uuid,head_.id,head_.sequence::integer,NULL::bigint,NULL::text,'elect:'||p_act.act_uuid);
   PERFORM public.s015_refresh_governed_cache('core_contextualroleassignment',assignment_.id,p_act.effective_at,p_act.effective_at);
 END IF;
END $$;

CREATE FUNCTION public.elect_nomination_followup() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE plan jsonb; nom public.core_cqact; o public.core_cqact;
BEGIN
 IF NEW.operation='ELECT_WITHDRAW' THEN
   SELECT a.* INTO o FROM public.cq_phases h JOIN public.core_cqact a USING(act_uuid)
     WHERE h.thread_uuid=NEW.thread_uuid AND h.phase='VOTING';
   IF o.act_uuid IS NOT NULL THEN
    INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
    VALUES(gen_random_uuid(),NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,'SETTLE',NEW.actor_id,NEW.actor_epoch,
       statement_timestamp(),jsonb_build_object('ballot',o.act_uuid)); END IF;
   RETURN NULL;
 END IF;
 IF NEW.operation='ELECT_CONSENT' THEN
   SELECT * INTO STRICT nom FROM public.core_cqact WHERE act_uuid=(NEW.request->>'nomination')::uuid;
 ELSIF (NEW.facts->>'self_consent')::boolean THEN nom:=NEW;
 ELSE RETURN NULL; END IF;
 plan:=public.elect_plan(NEW.organism_id,NEW.board_uuid,public.elect_attachment(NEW.thread_uuid),NEW.effective_at);
 IF plan->>'type'='SEAT' THEN
   INSERT INTO public.core_circlejoinrequest(request_uuid,circle_id,membership_id,seat_rule_uuid,seat_id,initiated_by,
    consent_reference,enterer_identity_id,occurred_at,effective_at,received_at)
   VALUES(NEW.act_uuid,(plan->>'circle')::bigint,(nom.facts->>'member')::bigint,(plan->>'seat_rule')::uuid,
     plan->>'place','APPLICANT','elect-consent:'||NEW.act_uuid,NEW.actor_id,NEW.effective_at,NEW.effective_at,NEW.received_at);
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER elect_nomination_followup AFTER INSERT ON public.core_cqact FOR EACH ROW
 WHEN (NEW.operation IN ('ELECT_NOMINATE','ELECT_CONSENT','ELECT_WITHDRAW')) EXECUTE FUNCTION public.elect_nomination_followup();
"""

SQL += r"""
CREATE FUNCTION public.elect_actor_basis(p_act public.core_cqact,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE office_ jsonb; att jsonb; policy_ text;
BEGIN
 IF p_act.operation='INITIATE' THEN
    IF p_act.request->'attachment'->>'kind' IS DISTINCT FROM 'ELECTION' THEN RETURN NULL; END IF;
 ELSIF p_act.operation='ELECT_NOMINATE' THEN
    att:=public.elect_attachment(p_act.thread_uuid);
    policy_:=public.elect_plan(p_act.organism_id,p_act.board_uuid,att,p_cut)->>'nomination';
    IF policy_ IS DISTINCT FROM 'B' AND (policy_ IS DISTINCT FROM 'C' OR p_act.request->>'person'=
       (SELECT identity_id::text FROM public.core_identity WHERE id=p_act.actor_id)) THEN RETURN NULL; END IF;
 ELSE RETURN NULL; END IF;
 SELECT public.disc_office(p_act.organism_id,b.circle_id,p_act.actor_id,p_cut) INTO office_
   FROM public.core_discboard b WHERE b.board_uuid=p_act.board_uuid AND b.organism_id=p_act.organism_id;
 IF office_ IS NULL THEN PERFORM public.cq_refuse(CASE WHEN p_act.operation='INITIATE' THEN 'ELECT_CALL' ELSE 'ELECT_NOMINATION' END); END IF;
 RETURN office_;
END $$;

CREATE FUNCTION public.elect_office_withdrawn() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item record; epoch_ bigint;
BEGIN
 IF greatest(NEW.effective_at,NEW.received_at)>statement_timestamp() THEN RETURN NULL; END IF;
 SELECT access_epoch INTO epoch_ FROM public.core_identity WHERE id=NEW.actor_id;
 FOR item IN SELECT o.* FROM public.cq_phases h JOIN public.core_cqact o USING(act_uuid)
 JOIN public.core_contextualroleassignment a ON a.id=NEW.assignment_id
 WHERE h.phase='VOTING' AND h.deadline>statement_timestamp() AND EXISTS(
   SELECT FROM jsonb_array_elements(o.facts->'slate') s WHERE s->>'assignment'=a.assignment_uuid::text)
 ORDER BY o.organism_id,o.ordinal LOOP
    INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
    VALUES(gen_random_uuid(),item.organism_id,item.board_uuid,item.thread_uuid,'SETTLE',NEW.actor_id,epoch_,
       statement_timestamp(),jsonb_build_object('ballot',item.act_uuid));
 END LOOP;
 RETURN NULL;
END $$;
CREATE TRIGGER elect_office_withdrawn AFTER INSERT ON public.core_officeappointmentconsent FOR EACH ROW
 WHEN (NEW.action IN ('WITHDRAW','DECLINE')) EXECUTE FUNCTION public.elect_office_withdrawn();
"""
