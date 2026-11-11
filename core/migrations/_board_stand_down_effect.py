"""Stand-down: freeze the affected objects under the owner lock at settlement."""

SQL = """
CREATE FUNCTION public.c108_reverted(p_lo bigint,p_at timestamptz,p_known timestamptz)
RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT public.cq3_board(p_lo,p_at,p_known) IS NULL AND EXISTS(
 SELECT FROM public.core_circle c JOIN public.core_circlestateevent e ON e.circle_id=c.id
 WHERE c.parent_organism_id=p_lo AND c.kind='GOVERNANCE' AND e.action='STAND_DOWN'
 AND e.effective_at<=p_at AND greatest(e.received_at,e.recorded_at)<=p_known)
$$;

CREATE FUNCTION public.c108_targets(p_lo bigint,p_circle bigint,p_cut timestamptz)
RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT jsonb_build_object(
 'places',coalesce((SELECT jsonb_agg(p.id ORDER BY p.id) FROM public.core_circleparticipation p
 WHERE p.circle_id=p_circle AND p.seat_id IS NOT NULL AND greatest(p.recorded_at,p.received_at)<=p_cut
 AND coalesce(public.elect_place_end(p.id,p_cut),'infinity')>greatest(p_cut,p.effective_at)),'[]'),
 'offices',coalesce((SELECT jsonb_agg(a.id ORDER BY a.id) FROM public.core_contextualroleassignment a
 JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
 JOIN public.core_organismmembership m ON m.id=a.membership_id AND m.living_organism_id=p_lo
 WHERE ((a.circle_id=p_circle AND d.scope='CIRCLE') OR (a.circle_id IS NULL AND d.living_organism_id=p_lo
 AND d.scope='LIVING_ORGANISM' AND d.kind='BOARD_OFFICE' AND d.code<>'COORDINATOR' AND NOT starts_with(d.code,'INTEVIA_')))
 AND EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent h WHERE h.assignment_id=a.id AND h.action='ASSIGN'
 AND greatest(h.recorded_at,h.received_at)<=p_cut
 AND coalesce(public.elect_office_deadline(h.id,p_cut),'infinity')>greatest(p_cut,h.effective_at))
 AND NOT EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent x WHERE x.assignment_id=a.id
 AND x.resulting_state='ENDED' AND x.effective_at<=p_cut AND greatest(x.recorded_at,x.received_at)<=p_cut)),'[]'),
 'mandates',coalesce((SELECT jsonb_agg(m.binding ORDER BY m.binding) FROM public.cmt_mandates m WHERE m.organism_id=p_lo
 AND m.binding=(public.cmt_current(m.circle_id,p_cut,p_cut)).binding
 AND (m.mandate->>'ends_at' IS NULL OR p_cut<(m.mandate->>'ends_at')::timestamptz)
 AND NOT EXISTS(SELECT FROM public.core_cqact e WHERE e.organism_id=p_lo AND e.operation='IMPLEMENT'
 AND e.facts#>>'{domain_plan,committee,completed}'=m.binding::text AND greatest(e.effective_at,e.recorded_at)<=p_cut)
 AND NOT EXISTS(SELECT FROM public.core_cqact prior JOIN public.core_circlestateevent edge
 ON edge.action='STAND_DOWN' AND edge.event_uuid::text=prior.request->>'result'
 WHERE prior.organism_id=p_lo AND prior.operation='IMPLEMENT' AND prior.facts#>>'{domain_plan,domain}'='BOARD_STAND_DOWN'
 AND prior.facts#>'{domain_plan,targets,mandates}' ? m.binding::text AND greatest(edge.effective_at,edge.recorded_at)<=p_cut)),'[]'),
 'powers',coalesce((SELECT jsonb_agg(t.act_uuid ORDER BY t.act_uuid) FROM public.elect_transfers t
 WHERE t.organism_id=p_lo AND t.transfer->>'to' IS DISTINCT FROM 'LO_MEMBERSHIP'
 AND t.act_uuid=(public.elect_last_transfer(p_lo,t.transfer->'scope',p_cut)).act_uuid),'[]'),
 'work',coalesce((SELECT jsonb_agg(w.receipt ORDER BY w.activity) FROM public.core_cmtcustody w
 JOIN public.core_serviceactivity a ON a.id=w.activity AND a.state IN ('assigned','in_progress')
 WHERE w.effective_at<=p_cut AND w.recorded_at<=p_cut AND NOT (w.cause ? 'stand_down')
 AND (w.board_circle=p_circle OR EXISTS(SELECT FROM public.cmt_mandates m WHERE m.organism_id=p_lo AND m.binding=w.mandate))
 AND NOT EXISTS(SELECT FROM public.core_cmtcustody later WHERE later.predecessor=w.receipt)),'[]'))
$$;

CREATE FUNCTION public.c108_plan(p_lo bigint,p_board uuid,p_att jsonb,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE s jsonb:=p_att->'spec'; b public.core_circle; life uuid;
BEGIN
 SELECT * INTO b FROM public.core_circle WHERE id=public.cq3_board(p_lo,p_cut,p_cut);
 IF b.id IS NULL OR s->>'circle' IS DISTINCT FROM b.circle_uuid::text
 THEN RETURN jsonb_build_object('code','CQ_ATTACHMENT','reason','The named Board is no longer active.'); END IF;
 IF NOT public.c108_body_matches(p_lo,p_board,s->'citation',p_cut,p_cut)
 THEN RETURN jsonb_build_object('code','CQ_BODY','reason','Only the members or the body named by the constitution may stand down this Board.'); END IF;
 life:=public.c108_active_lifecycle(b.id,p_cut,p_cut);
 IF life IS NULL OR s->>'lifecycle' IS DISTINCT FROM life::text
 THEN RETURN jsonb_build_object('code','CQ_ATTACHMENT','reason','The Board lifecycle has changed. Review the motion.'); END IF;
 IF public.elect_document(p_att->>'text') IS DISTINCT FROM s OR NOT (s ?& ARRAY['citation','effective_at'])
 OR s IS DISTINCT FROM jsonb_build_object('act','STAND_DOWN','organism',s->'organism','circle',b.circle_uuid,
 'body',p_board,'lifecycle',life,'effective_at',s->'effective_at','citation',s->'citation')
 THEN PERFORM public.cq_refuse('CQ_ATTACHMENT'); END IF;
 IF s->>'effective_at' IS DISTINCT FROM 'AT_RESULT' THEN
   RETURN jsonb_build_object('code','CQ_STAND_DOWN_DATE','reason','Choose stand-down at the result. A separately dated stand-down is not yet available.');
 END IF;
 RETURN jsonb_build_object('domain','BOARD_STAND_DOWN','circle_id',b.id,'lifecycle',life,
   'route',CASE WHEN EXISTS(SELECT FROM public.core_discboard WHERE board_uuid=p_board AND circle_id IS NULL)
   THEN 'CQ_MEMBERSHIP' ELSE 'CQ_CIRCLE' END);
END $$;

CREATE FUNCTION public.c108_result_child(p_marker text,p_kind text,p_id bigint)
RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT FROM public.core_cqact i WHERE i.operation='IMPLEMENT' AND i.execution_xid=pg_current_xact_id()
 AND public.cq3_implement_source(i)=i.authority_source AND i.facts->'domain_plan'->>'domain'='BOARD_STAND_DOWN'
 AND p_marker='c108-stand-down:'||(i.request->>'result')
 AND i.facts->'domain_plan'->'targets'->p_kind @> jsonb_build_array(p_id))
$$;

CREATE FUNCTION public.c108_apply(p_act uuid) RETURNS void LANGUAGE plpgsql VOLATILE AS $$
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
 SELECT * INTO STRICT h FROM public.core_contextualroleassignmentstateevent WHERE assignment_id=id_ ORDER BY sequence DESC LIMIT 1;
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
END $$;
"""

SQL += """
CREATE FUNCTION public.c108_complete(a public.core_cqact) RETURNS void LANGUAGE plpgsql STABLE AS $$
DECLARE targets jsonb:=a.facts#>'{domain_plan,targets}'; result_ uuid:=(a.request->>'result')::uuid;
 marker text:='c108-stand-down:'||result_;
BEGIN
 IF NOT EXISTS(SELECT FROM public.core_circlestateevent e WHERE e.circle_id=(a.facts#>>'{domain_plan,circle_id}')::bigint
 AND e.evidence_reference=marker AND e.action='STAND_DOWN' AND e.resulting_state='DORMANT' AND e.effective_at=a.effective_at)
 OR (SELECT coalesce(jsonb_agg(x.participation_id ORDER BY x.participation_id),'[]') FROM public.core_circleparticipationend x
 WHERE x.composing_rule_reference=marker AND x.composed_from_event_uuid=result_) IS DISTINCT FROM targets->'places'
 OR (SELECT coalesce(jsonb_agg(x.assignment_id ORDER BY x.assignment_id),'[]') FROM public.core_contextualroleassignmentstateevent x
 WHERE x.evidence_reference=marker AND x.resulting_state='ENDED') IS DISTINCT FROM targets->'offices'
 OR EXISTS(SELECT FROM jsonb_array_elements_text(targets->'mandates') k WHERE public.cmt_live(k::uuid,a.effective_at,a.recorded_at))
 OR EXISTS(SELECT FROM jsonb_array_elements_text(targets->'work') k WHERE NOT EXISTS(SELECT FROM public.core_cmtcustody c
 WHERE c.predecessor=k::uuid AND c.cause->>'stand_down'=result_::text AND c.effective_at=a.effective_at))
 OR EXISTS(SELECT FROM jsonb_array_elements_text(targets->'powers') k WHERE NOT EXISTS(SELECT FROM public.elect_transfers t
 WHERE t.act_uuid=a.act_uuid AND t.transfer->>'predecessor'=k AND t.transfer->>'to'='LO_MEMBERSHIP'))
 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
END $$;
"""
