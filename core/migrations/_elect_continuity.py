"""Constitution-bound recognition and holder notices on the existing governed act ledger."""

SQL = r"""
-- Identify the Board place supporting the office start from the facts
-- available at award. Read the ending of that same place at the viewer's cut.
CREATE FUNCTION public.elect_office_support_ending(p_assign bigint,p_known timestamptz)
RETURNS SETOF public.core_circleparticipationend LANGUAGE sql STABLE STRICT AS $$
 SELECT ending.*
 FROM public.core_contextualroleassignmentstateevent award
 JOIN public.core_contextualroleassignment a ON a.id=award.assignment_id
 JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
 CROSS JOIN LATERAL (
   SELECT p.id FROM public.core_circleparticipation p JOIN public.core_circle c ON c.id=p.circle_id
   WHERE c.kind='GOVERNANCE' AND c.parent_organism_id=d.living_organism_id AND p.membership_id=a.membership_id
   AND greatest(p.received_at,p.recorded_at)<=award.recorded_at
   AND public.elect_term_includes(award.effective_at,p.effective_at,public.elect_place_end(p.id,award.recorded_at))
   ORDER BY p.effective_at DESC,p.id DESC LIMIT 1
 ) supporting
 JOIN public.core_circleparticipationend ending ON ending.participation_id=supporting.id
 WHERE award.id=p_assign AND award.action='ASSIGN' AND greatest(award.received_at,award.recorded_at)<=p_known
 AND d.kind='BOARD_OFFICE' AND d.participation_required
 AND greatest(ending.received_at,ending.recorded_at)<=p_known
 ORDER BY ending.effective_at,ending.recorded_at,ending.id LIMIT 1
$$;

-- Only a recorded, adjacent-term election can reserve this future place.
CREATE FUNCTION public.elect_pending_successor(p_event uuid,p_member bigint,p_kind text,
 p_depart timestamptz,p_received timestamptz) RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 SELECT EXISTS(SELECT FROM public.core_cqact i
 WHERE i.act_uuid=p_event AND i.operation='IMPLEMENT' AND i.facts->'domain_plan'->>'domain'='ELECTION'
 AND i.facts->'domain_plan'->>'type'=p_kind AND i.facts->'domain_plan'->>'incumbent' IS NOT NULL
 AND i.facts->'domain_plan'->'winner'->>'member'=p_member::text
 AND i.effective_at<=p_depart AND i.recorded_at<=p_received AND p_depart<=p_received
 AND (i.facts->'domain_plan'->>'start')::timestamptz>p_received)
$$;

-- The assignment and its cancellation have the same future start. The
-- incumbent's separate term and vacancy continue to use their original date.
CREATE FUNCTION public.elect_cancel_pending_office(p_assignment bigint,p_departure uuid)
RETURNS void LANGUAGE plpgsql VOLATILE AS $$
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
END $$;

CREATE FUNCTION public.elect_continuity_clause(p_lo bigint,p_spec jsonb,p_section text,p_cut timestamptz)
RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT public.elect_document(v.content)->p_section->(p_spec->>'provision')
 FROM public.core_livingorganism lo CROSS JOIN LATERAL public.l_laws_at(lo.organism_id,p_cut,p_cut) l
 JOIN public.core_libraryresourceversion v ON v.id=l.version
 WHERE lo.id=p_lo AND NOT l.inherited AND l.class_name IN ('ARTICLE','BYLAW')
 AND l.binding::text=p_spec->>'binding' AND l.version::text=p_spec->>'version'
$$;

CREATE FUNCTION public.elect_recogniser(a public.core_cqact,p_claim jsonb,p_rule jsonb,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE named text:=p_rule->>'authority'; office_ uuid; result_ uuid;
BEGIN
 IF named='FOUNDING_COORDINATOR' THEN
   SELECT assignment_.assignment_uuid INTO office_ FROM public.core_livingorganism lo
    JOIN public.core_livingorganismevent first_ ON first_.living_organism_id=lo.id AND first_.sequence=1 AND first_.action='FOUND'
    JOIN public.core_contextualroleassignmentstateevent e ON e.evidence_reference='founding-batch:'||first_.event_uuid
       AND e.action='ASSIGN' AND e.authority_decision_reference=first_.authority_decision_reference
    JOIN public.core_contextualroleassignment assignment_ ON assignment_.id=e.assignment_id
    JOIN public.core_organismroledefinition d ON d.id=assignment_.role_definition_id
    JOIN public.core_organismmembership member_ ON member_.id=assignment_.membership_id
    WHERE lo.id=a.organism_id AND NOT lo.platform_root AND lo.founder_identity_id=a.actor_id AND first_.actor_id=a.actor_id AND member_.identity_id=a.actor_id
    AND member_.living_organism_id=lo.id AND assignment_.circle_id IS NULL AND d.scope='LIVING_ORGANISM' AND d.code='COORDINATOR'
    AND public.s015_fold_effective_state('core_contextualroleassignment',assignment_.id,p_cut,p_cut)='ACTIVE'
    AND public.c2_basis_in_force(lo.founding_authority_basis_id,p_cut,p_cut)
    AND greatest(e.effective_at,e.received_at,e.recorded_at)<=p_cut
    AND EXISTS(SELECT FROM public.core_livingorganismevent phase WHERE phase.living_organism_id=lo.id AND phase.action='PHASE_ADVANCE'
       AND phase.evidence_reference='formation-phase:GERMINATING->BIRTHING'
       AND greatest(phase.effective_at,phase.received_at,phase.recorded_at)<=p_cut)
    AND NOT EXISTS(SELECT FROM public.core_livingorganismevent b WHERE b.living_organism_id=lo.id AND b.action='BIRTHING'
       AND greatest(b.effective_at,b.received_at,b.recorded_at)<=p_cut);
 ELSIF named='office:LO_COORDINATOR' THEN
   SELECT public.circle_lo_office(lo.id,a.actor_id,p_cut,p_cut) INTO office_
   FROM public.core_livingorganism lo WHERE lo.id=a.organism_id AND NOT lo.platform_root;
 ELSIF starts_with(named,'office:') THEN
   SELECT assignment_.assignment_uuid INTO office_ FROM public.c2_seat_facts(a.organism_id,p_cut,p_cut) s
    JOIN public.core_contextualroleassignment assignment_ ON assignment_.id=s.assignment_id JOIN public.core_organismmembership m ON m.id=s.membership_id
    WHERE m.identity_id=a.actor_id AND s.role_scope='LIVING_ORGANISM' AND s.standing='QUALIFIED'
    AND s.role_code=split_part(named,':',2) ORDER BY assignment_.assignment_uuid LIMIT 1;
 ELSE
   SELECT r.act_uuid INTO result_ FROM public.core_cqact r
    JOIN public.core_discboard b ON b.board_uuid=r.board_uuid AND b.organism_id=r.organism_id
    LEFT JOIN public.core_circle c ON c.id=b.circle_id
    JOIN public.core_discbody v ON v.version_uuid::text=r.facts->>'version'
    WHERE r.act_uuid::text=a.request->>'result' AND r.organism_id=a.organism_id AND r.operation='SETTLE'
    AND public.elect_attachment(r.thread_uuid) IS NULL AND r.facts->>'event_kind'='DECISION' AND r.facts->'count'->>'kind'='QUALIFIED' AND r.recorded_at<=p_cut
    AND ((named='body:LO_MEMBERSHIP' AND b.circle_id IS NULL) OR
      (named='body:GOVERNANCE' AND c.id=public.cq3_board(a.organism_id,p_cut,p_cut))
      OR (named='circle:'||c.circle_uuid::text AND public.s015_fold_effective_state('core_circle',c.id,p_cut,p_cut)='ACTIVE'))
    AND public.cq3_result_visible(r.act_uuid,a.organism_id,a.actor_id,p_cut)
    AND public.elect_document(v.markdown)=jsonb_build_object('recognise',p_claim);
 END IF;
 IF office_ IS NULL AND result_ IS NULL THEN PERFORM public.cq_refuse('ELECT_RECOGNISER'); END IF;
 RETURN jsonb_build_object('kind',(CASE WHEN office_ IS NULL THEN 'CQ_RESULT' ELSE 'OFFICE' END),
    'route',named,'reference',coalesce(office_,result_));
END $$;

CREATE FUNCTION public.elect_recognition_facts(a public.core_cqact,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE claim jsonb:=a.request->'claim'; rule_ jsonb; authority_ jsonb; holder_ bigint; member_ bigint;
 gov public.core_circle; arrangement public.core_governedrule; role_ public.core_organismroledefinition;
 original_ timestamptz; end_ timestamptz; assignment_ bigint; consent_ uuid; owner_ uuid;
BEGIN
 SELECT lo.organism_id INTO owner_ FROM public.core_livingorganism lo WHERE lo.id=a.organism_id AND NOT lo.platform_root;
 SELECT * INTO gov FROM public.core_circle WHERE parent_organism_id=a.organism_id AND kind='GOVERNANCE';
 IF owner_ IS NULL OR gov.id IS NULL OR public.s015_fold_effective_state('core_circle',gov.id,p_cut,p_cut) IS NULL
 OR public.s015_fold_effective_state('core_circle',gov.id,p_cut,p_cut) NOT IN ('DORMANT','ELIGIBLE','ACTIVE')
 THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 rule_:=public.elect_continuity_clause(a.organism_id,claim,'recognitions',p_cut);
 IF jsonb_typeof(rule_) IS DISTINCT FROM 'object' OR rule_-ARRAY['type','place','authority','term']<>'{}'::jsonb
    OR rule_->>'place' IS DISTINCT FROM claim->>'place' OR rule_->>'type' IS DISTINCT FROM claim->>'type'
    OR nullif(btrim(rule_->>'authority'),'') IS NULL OR length(rule_->>'authority')>64 THEN PERFORM public.cq_refuse('ELECT_RECOGNISER'); END IF;
 IF a.operation='RECOGNISE' THEN authority_:=public.elect_recogniser(a,claim,rule_,p_cut); END IF;
 SELECT m.id,i.id INTO member_,holder_ FROM public.core_organismmembership m JOIN public.core_identity i ON i.id=m.identity_id
 JOIN public.auth_user u ON u.id=i.credential_id WHERE m.living_organism_id=a.organism_id AND i.identity_id::text=claim->>'person'
 AND public.disc_member_state(a.organism_id,i.id,p_cut)='ACTIVE' AND i.access_state='active' AND u.is_active;
 IF member_ IS NULL THEN PERFORM public.cq_refuse('ELECT_CANDIDATE'); END IF;
 IF a.operation='RECOGNITION_CONSENT' THEN
    IF a.actor_id<>holder_ THEN PERFORM public.cq_refuse('ELECT_RECOGNITION_CONSENT'); END IF;
    authority_:=jsonb_build_object('kind','HOLDER','route','RECOGNITION_CONSENT','reference',claim->'person');
 END IF;
 IF jsonb_typeof(claim) IS DISTINCT FROM 'object' OR claim-ARRAY['person','place','type','binding','version','provision',
    'original_body','original_date','decision','evidence','term_start','term_end','assignment']<>'{}'::jsonb
    OR EXISTS(SELECT FROM unnest(ARRAY['original_body','original_date','decision','evidence']) k
     WHERE jsonb_typeof(claim->k) IS DISTINCT FROM 'string' OR btrim(claim->>k)='')
 THEN PERFORM public.cq_refuse('ELECT_ORIGINAL'); END IF;
 IF (claim->>'original_date') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}($|T)' THEN PERFORM public.cq_refuse('ELECT_ORIGINAL'); END IF;
 BEGIN
   IF (claim->>'original_date')::timestamptz>p_cut THEN PERFORM public.cq_refuse('ELECT_ORIGINAL'); END IF;
 EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN PERFORM public.cq_refuse('ELECT_ORIGINAL'); END;
 IF coalesce(claim->>'term_start','') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T.*(Z|[+-][0-9]{2}:[0-9]{2})$'
   OR (claim->>'term_end' IS NOT NULL AND claim->>'term_end' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T.*(Z|[+-][0-9]{2}:[0-9]{2})$')
 THEN PERFORM public.cq_refuse('ELECT_REMAINDER'); END IF;
 original_:=(claim->>'term_start')::timestamptz; end_:=(claim->>'term_end')::timestamptz;
 IF original_ IS NULL OR NOT isfinite(original_) OR original_>p_cut OR (end_ IS NOT NULL AND NOT isfinite(end_))
    OR NOT coalesce((rule_->>'term'='APPOINTMENT_RECORD' AND end_ IS NOT NULL)
      OR (rule_->>'term'='OPEN_ENDED' AND claim ? 'term_end' AND end_ IS NULL),false)
 THEN PERFORM public.cq_refuse('ELECT_REMAINDER'); END IF;
 IF end_<=p_cut THEN PERFORM public.cq_refuse('ELECT_RECOGNITION_EXPIRED'); END IF;
 IF claim->>'type'='SEAT' THEN
   SELECT * INTO arrangement FROM public.core_governedrule WHERE scope_kind='LIVING_ORGANISM'
    AND scope_organism_id=a.organism_id AND code='GOVERNANCE_CONSTITUTION' AND effective_from<=p_cut AND recorded_at<=p_cut
    ORDER BY effective_from DESC,recorded_at DESC,version DESC LIMIT 1;
   IF NOT EXISTS(SELECT FROM jsonb_array_elements(arrangement.body->'seats') s WHERE s->>'seat_id'=claim->>'place'
      AND (s->>'effective_from')::timestamptz<=p_cut) THEN PERFORM public.cq_refuse('ELECT_CITATION'); END IF;
 ELSIF claim->>'type'='OFFICE' THEN
   SELECT d.* INTO role_ FROM public.core_organismroledefinition d JOIN public.core_contextualroleassignment o ON o.role_definition_id=d.id
     WHERE o.assignment_uuid::text=claim->>'assignment' AND o.membership_id=member_ AND o.circle_id IS NULL AND d.active
     AND d.living_organism_id=a.organism_id AND d.code=claim->>'place' AND d.kind='BOARD_OFFICE' AND d.scope='LIVING_ORGANISM'
     AND d.code<>'COORDINATOR' AND NOT starts_with(d.code,'INTEVIA_') AND greatest(d.effective_at,d.recorded_at)<=p_cut
     AND NOT EXISTS(SELECT FROM public.core_organismroledefinition n WHERE n.supersedes_id=d.id AND greatest(n.effective_at,n.recorded_at)<=p_cut)
     AND EXISTS(SELECT FROM public.core_organismroledefinitionprovision v WHERE v.definition_id=d.id AND v.relationship='OFFICE_BASIS'
       AND v.provision_reference=claim->>'provision' AND v.provision_version=claim->>'version');
   SELECT o.id INTO assignment_ FROM public.core_contextualroleassignment o WHERE o.assignment_uuid::text=claim->>'assignment'
     AND o.role_definition_id=role_.id AND public.s015_fold_effective_state('core_contextualroleassignment',o.id,p_cut,p_cut)='PROPOSED';
   IF role_.id IS NULL OR assignment_ IS NULL OR (role_.participation_required AND NOT public.circle_place_at(gov.id,member_,p_cut,p_cut))
      THEN PERFORM public.cq_refuse('ELECT_CANDIDATE'); END IF;
 ELSE PERFORM public.cq_refuse('ELECT_CITATION'); END IF;
 IF a.operation='RECOGNISE' THEN
    SELECT prior.act_uuid INTO consent_ FROM public.core_cqact prior WHERE prior.organism_id=a.organism_id
     AND prior.operation='RECOGNITION_CONSENT' AND prior.actor_id=holder_ AND prior.request->'claim'=claim AND prior.recorded_at<=p_cut
     AND prior.act_uuid::text=a.request->>'consent' AND prior.facts->>'member'=member_::text
     AND NOT EXISTS(SELECT FROM public.core_cqact used WHERE used.operation='RECOGNISE' AND used.request->>'consent'=prior.act_uuid::text);
    IF consent_ IS NULL THEN PERFORM public.cq_refuse('ELECT_RECOGNITION_CONSENT'); END IF;
    IF EXISTS(SELECT FROM public.elect_term_slots(gov.id,role_.id,claim->>'place',member_,p_cut) s
      WHERE (s.ends IS NULL OR s.ends>greatest(p_cut,s.starts)) AND (end_ IS NULL OR s.starts<end_))
      THEN PERFORM public.cq_refuse('ELECT_RECOGNITION_OCCUPIED'); END IF;
 END IF;
 RETURN jsonb_build_object('source',authority_,'member',member_,'holder',holder_,'circle',gov.id,'place',claim->'place','type',claim->'type',
    'rule_id',arrangement.id,'seat_rule',arrangement.rule_uuid,'seat_version',arrangement.version,'assignment_id',assignment_,
    'consent',consent_,'start',p_cut,'end',end_);
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN PERFORM public.cq_refuse('ELECT_REMAINDER');
END $$;

CREATE FUNCTION public.elect_resignation_facts(a public.core_cqact,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE target public.core_circleparticipation; member_ public.core_organismmembership; rule_ jsonb;
 due_ timestamptz; notice_ public.core_cqact; src jsonb; g_id bigint;
BEGIN
 SELECT p.* INTO target FROM public.core_circleparticipation p JOIN public.core_circle c ON c.id=p.circle_id
 WHERE c.parent_organism_id=a.organism_id AND c.kind='GOVERNANCE' AND p.participation_uuid::text=a.request->>'participation';
 IF target.id IS NULL THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 SELECT * INTO STRICT member_ FROM public.core_organismmembership WHERE id=target.membership_id;
 IF member_.identity_id<>a.actor_id THEN PERFORM public.cq_refuse('ELECT_RESIGN_HOLDER'); END IF;
 IF NOT public.elect_term_includes(p_cut,target.effective_at,public.elect_place_end(target.id,p_cut))
 THEN PERFORM public.cq_refuse((CASE WHEN p_cut<target.effective_at THEN 'ELECT_HOLDER_EARLY' ELSE 'ELECT_HOLDER_EXPIRED' END)); END IF;
 IF EXISTS(SELECT FROM public.core_circleparticipationend e WHERE e.participation_id=target.id
   AND starts_with(e.composing_rule_reference,'elect-resign:')) THEN PERFORM public.cq_refuse('ELECT_CONT_REQUEST'); END IF;
 IF a.request->>'notice' IS NULL AND EXISTS(SELECT FROM public.core_cqact h WHERE h.organism_id=a.organism_id
    AND h.operation='BOARD_RESIGN' AND h.actor_id=a.actor_id AND h.request->>'participation'=target.participation_uuid::text
    AND h.facts->>'effect'='AWAITING_BASIS') THEN PERFORM public.cq_refuse('ELECT_CONT_REQUEST'); END IF;
 IF a.request->>'notice' IS NOT NULL THEN
   SELECT * INTO notice_ FROM public.core_cqact n WHERE n.act_uuid::text=a.request->>'notice' AND n.organism_id=a.organism_id
    AND n.operation='BOARD_RESIGN' AND n.actor_id=a.actor_id AND n.facts->>'effect'='AWAITING_BASIS'
    AND n.request->>'participation'=target.participation_uuid::text;
   IF notice_.act_uuid IS NULL THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 END IF;
 rule_:=public.elect_continuity_clause(a.organism_id,a.request,'resignations',p_cut);
 IF rule_->>'place'=target.seat_id AND jsonb_typeof(rule_)='object'
    AND rule_-ARRAY['place','effect','effective_at','handover']='{}'::jsonb THEN
   IF rule_->>'effect'='ON_NOTICE' THEN
     IF notice_.act_uuid IS NULL THEN due_:=p_cut;
     ELSIF public.elect_continuity_clause(a.organism_id,a.request,'resignations',notice_.effective_at)=rule_ THEN due_:=notice_.effective_at;
     ELSE PERFORM public.cq_refuse('ELECT_REMAINDER'); END IF;
   ELSIF rule_->>'effect'='DATED' AND rule_->>'effective_at' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T.*(Z|[+-][0-9]{2}:[0-9]{2})$' THEN due_:=(rule_->>'effective_at')::timestamptz; END IF;
 END IF;
 IF due_ IS NOT NULL AND (NOT isfinite(due_) OR due_<target.effective_at OR due_<coalesce(notice_.effective_at,p_cut)
     OR public.elect_place_end(target.id,p_cut)<=due_) THEN PERFORM public.cq_refuse('ELECT_RESIGN_DATE'); END IF;
 SELECT g.id INTO g_id FROM public.core_governedrule g JOIN public.core_circleadmissiondecision d ON d.id=target.decision_id
    WHERE g.scope_organism_id=a.organism_id AND g.rule_uuid=target.seat_rule_uuid AND
    d.settings_basis='rule:'||g.rule_uuid||'@'||g.version||'#'||target.seat_id;
 IF g_id IS NULL THEN SELECT g.id INTO g_id FROM public.core_governedrule g JOIN public.core_circle c ON c.parent_organism_id=g.scope_organism_id
   WHERE c.id=target.circle_id AND g.rule_uuid=target.seat_rule_uuid AND g.recorded_at<=target.recorded_at
   AND g.effective_from<=target.effective_at ORDER BY g.effective_from DESC,g.recorded_at DESC,g.version DESC LIMIT 1; END IF;
 src:=jsonb_build_object('kind','HOLDER','route','BOARD_RESIGN','reference',target.participation_uuid);
 RETURN jsonb_build_object('source',src,'participation_id',target.id,'member',target.membership_id,'place',target.seat_id,
    'rule_id',g_id,'end',due_,'handover',rule_->'handover','effect',(CASE WHEN due_ IS NULL THEN 'AWAITING_BASIS' ELSE 'DATED' END));
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN PERFORM public.cq_refuse('ELECT_RESIGN_DATE');
END $$;
"""

SQL += r"""
CREATE FUNCTION public.elect_recognition_office(p_assignment bigint,p_event uuid,p_actor bigint,p_decision text,
 p_start timestamptz,p_end timestamptz) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT FROM public.core_cqact a
 WHERE a.act_uuid=p_event AND a.operation='RECOGNISE' AND a.execution_xid=pg_current_xact_id()
 AND a.facts->>'type'='OFFICE' AND (a.facts->>'assignment_id')::bigint=p_assignment AND a.actor_id=p_actor
 AND public.disc_decision(a.authority_source,a.fingerprint)=p_decision AND a.effective_at=p_start
 AND (a.facts->>'end')::timestamptz IS NOT DISTINCT FROM p_end)
$$;

-- A SUSPENDED or RESTRICTED holder's own notice may complete its dependent consequences.
-- Ordinary term catch-up still requires an active member of this organisation.
CREATE FUNCTION public.elect_notice_enterer(p_lo bigint,p_assignment bigint,p_actor bigint,p_cut timestamptz)
RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT FROM public.core_cqact n JOIN public.core_circleparticipationend x ON x.composed_from_event_uuid=n.act_uuid
 JOIN public.core_circleparticipation p ON p.id=x.participation_id JOIN public.core_organismmembership m ON m.id=p.membership_id
 JOIN public.core_contextualroleassignment a ON a.membership_id=m.id JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
 WHERE n.organism_id=p_lo AND n.operation='BOARD_RESIGN' AND n.execution_xid=pg_current_xact_id()
 AND n.actor_id=p_actor AND m.identity_id=p_actor AND m.living_organism_id=p_lo AND n.facts->>'participation_id'=p.id::text
 AND d.participation_required AND d.kind='BOARD_OFFICE' AND d.living_organism_id=p_lo AND a.id=p_assignment
 AND x.composing_rule_reference='elect-resign:'||n.act_uuid AND x.effective_at<=p_cut
 AND EXISTS(SELECT FROM public.elect_due_office(a.id,p_cut) due WHERE due.due=x.effective_at))
$$;

CREATE FUNCTION public.elect_resignation_coverage(p_lo bigint,p_act uuid,p_enterer bigint)
RETURNS boolean LANGUAGE plpgsql VOLATILE AS $$
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
END $$;

CREATE FUNCTION public.elect_continuity_effect() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE f jsonb:=NEW.facts; q public.core_circlejoinrequest; d_id bigint; place_ bigint;
 ref text:=public.disc_decision(NEW.authority_source,NEW.fingerprint); head_ public.core_contextualroleassignmentstateevent;
 consent_ public.core_cqact; basis_ bigint;
 office_ record; end_ timestamptz:=(f->>'end')::timestamptz;
BEGIN
 IF NEW.operation='RECOGNITION_CONSENT' THEN RETURN NULL; END IF;
 IF NEW.operation='RECOGNISE' AND f->>'type'='SEAT' THEN
   SELECT * INTO STRICT consent_ FROM public.core_cqact WHERE act_uuid=(f->>'consent')::uuid;
   INSERT INTO public.core_circlejoinrequest(request_uuid,circle_id,membership_id,seat_rule_uuid,seat_id,initiated_by,
     consent_reference,enterer_identity_id,occurred_at,effective_at,received_at)
   VALUES(consent_.act_uuid,(f->>'circle')::bigint,(f->>'member')::bigint,(f->>'seat_rule')::uuid,f->>'place',
     'APPLICANT','recognition-consent:'||consent_.act_uuid,consent_.actor_id,consent_.received_at,NEW.effective_at,NEW.received_at)
   RETURNING * INTO q;
   INSERT INTO public.core_circleadmissiondecision(decision_uuid,request_id,route,constitutional_route,deciding_party,
    deciding_identity_id,self_decision,outcome,authority_decision_reference,settings_basis,seat_rule_uuid,seat_id,
    actor_state,actor_capacity,entry_mode,enterer_identity_id,composing_rule_state,composer_state,occurred_at,effective_at,received_at)
   VALUES(NEW.act_uuid,q.id,'GOVERNANCE_CONSTITUTIONAL','RECOGNITION',NEW.authority_source->>'route',
    NULL,false,'ACCEPT',ref,'rule:'||q.seat_rule_uuid||'@'||(f->>'seat_version')||'#'||q.seat_id,q.seat_rule_uuid,q.seat_id,
    'LIVING_ORGANISM','STANDING','TRANSPORT',NEW.actor_id,'NA_TRANSPORT','NA_TRANSPORT',NEW.received_at,NEW.effective_at,NEW.received_at)
   RETURNING id INTO d_id;
   INSERT INTO public.core_circleparticipation(participation_uuid,circle_id,membership_id,seat_rule_uuid,seat_id,decision_id,
     actor_state,actor_capacity,entry_mode,enterer_identity_id,composing_rule_state,composer_state,occurred_at,effective_at,received_at)
   VALUES(NEW.act_uuid,q.circle_id,q.membership_id,q.seat_rule_uuid,q.seat_id,d_id,'LIVING_ORGANISM','STANDING','TRANSPORT',
     NEW.actor_id,'NA_TRANSPORT','NA_TRANSPORT',NEW.received_at,NEW.effective_at,NEW.received_at) RETURNING id INTO place_;
 ELSIF NEW.operation='RECOGNISE' THEN
   SELECT * INTO STRICT head_ FROM public.core_contextualroleassignmentstateevent WHERE assignment_id=(f->>'assignment_id')::bigint
      ORDER BY sequence DESC LIMIT 1;
   SELECT founding_authority_basis_id INTO STRICT basis_ FROM public.core_livingorganism WHERE id=NEW.organism_id;
   PERFORM public.s015_append_event('core_contextualroleassignment',head_.assignment_id,'ASSIGN','ACTIVE',NEW.effective_at,
     NEW.received_at,NEW.received_at,NEW.actor_id,basis_,ref,'elect-recognised:'||NEW.act_uuid,'recognise:'||NEW.act_uuid,
     NEW.fingerprint,'{}',end_,NEW.act_uuid,head_.id,head_.sequence::integer,NULL,NULL,'recognise:'||NEW.act_uuid);
   PERFORM public.s015_refresh_governed_cache('core_contextualroleassignment',head_.assignment_id,NEW.effective_at,NEW.effective_at);
   RETURN NULL;
 ELSE
   place_:=(f->>'participation_id')::bigint;
 END IF;
 IF end_ IS NOT NULL THEN
   INSERT INTO public.core_circleparticipationend(participation_id,actor_state,actor_capacity,entry_mode,enterer_identity_id,
      composing_rule_state,composing_rule_id,composing_rule_reference,composer_state,composer_identity_id,composed_from_event_uuid,
      occurred_at,effective_at,received_at)
   VALUES(place_,'NONE','NA_NO_PARTY','COMPOSED',NEW.actor_id,'REFERENCE',(f->>'rule_id')::bigint,
      (CASE WHEN NEW.operation='RECOGNISE' THEN 'elect-term:recognition:' ELSE 'elect-resign:' END)||NEW.act_uuid,
      'REFERENCE',NEW.actor_id,NEW.act_uuid,NEW.received_at,end_,NEW.received_at);
 END IF;
 IF NEW.operation='BOARD_RESIGN' AND end_<=NEW.effective_at THEN
   FOR office_ IN SELECT a.id FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d
      ON d.id=a.role_definition_id WHERE a.membership_id=(f->>'member')::bigint AND d.living_organism_id=NEW.organism_id
      AND d.scope='LIVING_ORGANISM' AND d.kind='BOARD_OFFICE' AND d.participation_required
      AND EXISTS(SELECT FROM public.elect_due_office(a.id,NEW.effective_at) pending WHERE pending.due=end_)
      ORDER BY a.id
   LOOP PERFORM public.elect_record_term_end(NEW.organism_id,office_.id,NEW.actor_id); END LOOP;
   PERFORM public.elect_resignation_coverage(NEW.organism_id,NEW.act_uuid,NEW.actor_id);
 END IF;
 RETURN NULL;
END $$;
"""

# The recognised effect is identified by its own act and consent, independently
# of the normal election and Coordinator appointment routes.
REQUEST_GUARD = r"""
    IF starts_with(NEW.consent_reference,'recognition-consent:') THEN
       IF NOT EXISTS(SELECT FROM public.core_cqact r JOIN public.core_cqact h ON h.act_uuid::text=r.facts->>'consent'
         WHERE r.operation='RECOGNISE' AND r.execution_xid=pg_current_xact_id() AND r.facts->>'type'='SEAT'
         AND h.operation='RECOGNITION_CONSENT' AND h.organism_id=r.organism_id AND h.request->'claim'=r.request->'claim'
         AND h.act_uuid=NEW.request_uuid AND NEW.consent_reference='recognition-consent:'||h.act_uuid
         AND NEW.initiated_by='APPLICANT' AND h.actor_id=NEW.enterer_identity_id
         AND NEW.circle_id=(r.facts->>'circle')::bigint AND NEW.membership_id=(r.facts->>'member')::bigint
         AND NEW.seat_rule_uuid::text=r.facts->>'seat_rule' AND NEW.seat_id=r.facts->>'place'
         AND (NEW.occurred_at,NEW.effective_at,NEW.received_at)=(h.received_at,r.effective_at,r.received_at))
       THEN PERFORM public.cq_refuse('ELECT_RECOGNITION_CONSENT'); END IF;
       RETURN NEW;
    END IF;
"""

DECISION_GUARD = r"""
    IF NEW.constitutional_route='RECOGNITION' THEN
       IF NOT EXISTS(SELECT FROM public.core_cqact r JOIN public.core_circlejoinrequest q ON q.id=NEW.request_id
         WHERE r.act_uuid=NEW.decision_uuid AND r.operation='RECOGNISE' AND r.execution_xid=pg_current_xact_id()
         AND r.facts->>'type'='SEAT' AND q.request_uuid::text=r.facts->>'consent'
         AND NEW.route='GOVERNANCE_CONSTITUTIONAL' AND NEW.deciding_party=r.authority_source->>'route'
         AND NEW.deciding_identity_id IS NULL AND NOT NEW.self_decision AND NEW.outcome='ACCEPT'
         AND NEW.authority_decision_reference=public.disc_decision(r.authority_source,r.fingerprint)
         AND NEW.seat_id=q.seat_id AND NEW.seat_rule_uuid=q.seat_rule_uuid
         AND NEW.settings_basis='rule:'||q.seat_rule_uuid||'@'||(r.facts->>'seat_version')||'#'||q.seat_id
         AND NEW.enterer_identity_id=r.actor_id AND NEW.occurred_at=r.received_at AND NEW.effective_at=r.effective_at
         AND NEW.received_at=r.received_at) THEN PERFORM public.cq_refuse('ELECT_RECOGNISER'); END IF;
       RETURN NEW;
    END IF;
"""

END_GUARD = r"""
    IF starts_with(NEW.composing_rule_reference,'elect-term:recognition:') OR starts_with(NEW.composing_rule_reference,'elect-resign:') THEN
       IF NOT EXISTS(SELECT FROM public.core_cqact a JOIN public.core_circleparticipation p ON p.id=NEW.participation_id
         WHERE a.act_uuid=NEW.composed_from_event_uuid AND a.execution_xid=pg_current_xact_id()
         AND (a.operation='RECOGNISE' AND a.act_uuid=p.participation_uuid AND a.facts->>'type'='SEAT'
              AND NEW.composing_rule_reference='elect-term:recognition:'||a.act_uuid
           OR a.operation='BOARD_RESIGN' AND a.facts->>'participation_id'=p.id::text
              AND NEW.composing_rule_reference='elect-resign:'||a.act_uuid)
         AND NEW.actor_state='NONE' AND NEW.actor_id IS NULL AND NEW.actor_access_epoch IS NULL
         AND NEW.composing_rule_id=(a.facts->>'rule_id')::bigint AND NEW.composer_identity_id=a.actor_id AND NEW.enterer_identity_id=a.actor_id
         AND NEW.effective_at=(a.facts->>'end')::timestamptz AND NEW.received_at=a.received_at AND NEW.occurred_at=a.received_at)
       THEN PERFORM public.cq_refuse('ELECT_RECOGNISER'); END IF;
       RETURN NEW;
    END IF;
"""

CANCEL_OFFICE_GUARD = r"""
    IF NEW.composing_rule_reference='ELECT:SUCCESSOR_LEFT' THEN
       IF NEW.action IS DISTINCT FROM 'END' OR NEW.prior_state IS DISTINCT FROM 'ACTIVE'
          OR NEW.resulting_state IS DISTINCT FROM 'ENDED' OR NEW.actor_state IS DISTINCT FROM 'NONE'
          OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL OR NEW.actor_capacity IS DISTINCT FROM 'NA_NO_PARTY'
          OR NEW.entry_mode IS DISTINCT FROM 'COMPOSED' OR NEW.composing_rule_state IS DISTINCT FROM 'REFERENCE'
          OR NEW.composer_state IS DISTINCT FROM 'REFERENCE' OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
          OR NEW.effective_until IS NOT NULL OR NEW.effective_until_state IS DISTINCT FROM 'NONE'
          OR NOT EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent h
            JOIN public.core_contextualroleassignment a ON a.id=h.assignment_id
            JOIN public.core_organismmembershiptransition t ON t.membership_id=a.membership_id
            WHERE h.id=NEW.predecessor_id AND h.assignment_id=NEW.assignment_id AND h.action='ASSIGN' AND t.resulting_state='ENDED'
            AND public.elect_pending_successor(h.event_uuid,t.membership_id,'OFFICE',t.effective_at,t.received_at)
            AND NEW.evidence_reference='elect-departure:'||t.event_uuid||':'||h.event_uuid
            AND NEW.enterer_identity_id=t.enterer_identity_id AND NEW.authority_basis_id=t.authority_basis_id
            AND NEW.authority_decision_reference=t.authority_decision_reference AND NEW.payload_fingerprint=t.payload_fingerprint
            AND NEW.request_reference=NEW.evidence_reference AND NEW.idempotency_key=NEW.evidence_reference
            AND (NEW.effective_at,NEW.occurred_at,NEW.received_at)=(h.effective_at,t.received_at,t.received_at))
       THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
       RETURN NEW;
    END IF;
"""

OFFICE_GUARD = r"""
    IF NEW.action='ASSIGN' AND starts_with(NEW.evidence_reference,'elect-recognised:') THEN
      IF NEW.prior_state IS DISTINCT FROM 'PROPOSED' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'
         OR NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM' OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
         OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT' OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
         OR NOT public.elect_recognition_office(NEW.assignment_id,NEW.event_uuid,NEW.enterer_identity_id,
             NEW.authority_decision_reference,NEW.effective_at,NEW.effective_until)
         OR NOT EXISTS(SELECT FROM public.core_cqact a JOIN public.core_livingorganism lo ON a.organism_id=lo.id
           WHERE a.act_uuid=NEW.event_uuid AND NEW.evidence_reference='elect-recognised:'||a.act_uuid
           AND NEW.request_reference='recognise:'||a.act_uuid AND NEW.idempotency_key=NEW.request_reference
           AND NEW.authority_basis_id=lo.founding_authority_basis_id AND a.fingerprint=NEW.payload_fingerprint
           AND NEW.occurred_at=a.received_at AND NEW.received_at=a.received_at)
      THEN PERFORM public.cq_refuse('ELECT_RECOGNISER'); END IF;
      RETURN NEW;
    END IF;
"""
