"""Authority to restore, individual ELECT awards and the second recorded moment."""

TABLE = """
CREATE TABLE public.core_c108restoration (
 activation uuid PRIMARY KEY REFERENCES public.core_circlestateevent(event_uuid),
 result uuid NOT NULL UNIQUE REFERENCES public.core_cqact(act_uuid),
 stand_down uuid NOT NULL UNIQUE REFERENCES public.core_circlestateevent(event_uuid),
 facts jsonb NOT NULL
)
"""

SQL = """
CREATE FUNCTION public.c108_last_down(p_circle bigint,p_cut timestamptz,p_known timestamptz)
RETURNS uuid LANGUAGE sql STABLE STRICT AS $$
 SELECT e.event_uuid FROM public.core_circlestateevent e
 WHERE e.circle_id=p_circle AND e.action='STAND_DOWN' AND e.effective_at<=p_cut
 AND greatest(e.recorded_at,e.received_at)<=p_known ORDER BY e.sequence DESC LIMIT 1
$$;

CREATE FUNCTION public.c108_restore_grant(p_circle bigint,p_cut timestamptz,p_known timestamptz)
RETURNS public.core_cqact LANGUAGE sql STABLE STRICT AS $$
 SELECT g.* FROM public.core_circle c JOIN LATERAL (
   SELECT i.* FROM public.core_cqact i WHERE i.organism_id=c.parent_organism_id AND i.operation='IMPLEMENT'
   AND i.facts#>>'{domain_plan,domain}'='BOARD_RESTORE' AND i.facts#>>'{domain_plan,circle_id}'=c.id::text
   AND i.facts#>>'{domain_plan,stand_down}'=public.c108_last_down(c.id,p_cut,p_known)::text
   AND greatest(i.received_at,i.recorded_at)<=p_known AND i.effective_at<=p_cut ORDER BY i.ordinal DESC LIMIT 1
 ) g ON true JOIN public.core_cqact r ON r.act_uuid::text=g.request->>'result' AND r.organism_id=g.organism_id
 AND r.board_uuid=g.board_uuid AND r.thread_uuid=g.thread_uuid AND r.operation='SETTLE'
 AND r.facts->>'disposition'='APPLIED' AND public.cq3_qualified(r.facts#>>'{count,kind}')
 AND r.facts#>'{count,cqt_applied}'='true'::jsonb AND g.authority_source=r.facts->'result_authority'
 AND g.facts->'domain_plan'=r.facts->'domain_plan'
 WHERE c.id=p_circle AND public.c108_restore_body(g.organism_id,g.board_uuid,g.facts#>'{domain_plan,citation}',p_cut,p_known)
$$;

CREATE FUNCTION public.c108_restore_plan(p_lo bigint,p_board uuid,p_att jsonb,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE spec jsonb:=p_att->'spec'; c public.core_circle; lo public.core_livingorganism; down_ uuid;
BEGIN
 SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE id=p_lo;
 SELECT * INTO c FROM public.core_circle WHERE parent_organism_id=p_lo AND kind='GOVERNANCE';
 IF lo.platform_root OR c.id IS NULL OR c.circle_uuid::text IS DISTINCT FROM spec->>'circle'
 THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 IF NOT public.c108_restore_body(p_lo,p_board,spec->'citation',p_cut,p_cut)
 THEN RETURN jsonb_build_object('code','CQ_BODY','reason','Put restoration to the members or the body named by the constitution.'); END IF;
 IF public.cq_settings_values_at(p_lo,p_cut,p_cut)->>'enabled' IS DISTINCT FROM 'true'
 THEN RETURN jsonb_build_object('code','CQ_RESTORE_OFF','reason','Voting must be on to restore this Board.'); END IF;
 down_:=public.c108_last_down(c.id,p_cut,p_cut);
 IF down_ IS NULL OR down_::text IS DISTINCT FROM spec->>'stand_down' OR
   public.s015_fold_effective_state('core_circle',c.id,p_cut,p_cut) NOT IN ('DORMANT','ELIGIBLE')
   OR EXISTS(SELECT FROM public.core_c108restoration WHERE stand_down=down_)
 THEN RETURN jsonb_build_object('code','CQ_RESTORE_TENURE','reason','This motion does not identify the current stood-down Board.'); END IF;
 IF public.elect_document(p_att->>'text') IS DISTINCT FROM spec OR spec IS DISTINCT FROM jsonb_build_object(
 'act','RESTORE','organism',lo.organism_id,'circle',c.circle_uuid,'body',p_board,'stand_down',down_,'citation',spec->'citation')
 THEN PERFORM public.cq_refuse('CQ_ATTACHMENT'); END IF;
 IF (public.c108_restore_grant(c.id,p_cut,p_cut)).act_uuid IS NOT NULL
 THEN RETURN jsonb_build_object('code','CQ_RESTORE_PENDING','reason','A restoration decision already covers this Board.'); END IF;
 RETURN jsonb_build_object('domain','BOARD_RESTORE','circle_id',c.id,'circle',c.circle_uuid,'stand_down',down_,
 'body',p_board,'citation',spec->'citation','route',CASE WHEN EXISTS(SELECT FROM public.core_discboard b
 WHERE b.board_uuid=p_board AND b.circle_id IS NULL) THEN 'CQ_MEMBERSHIP' ELSE 'CQ_CIRCLE' END);
END $$;

CREATE FUNCTION public.c108_restore_details(p_circle bigint,p_result uuid,p_cut timestamptz,p_known timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE g public.core_cqact; makeup jsonb; seats jsonb; people integer; places integer; total integer;
BEGIN
 g:=public.c108_restore_grant(p_circle,p_cut,p_known);
 IF g.act_uuid IS NULL OR g.request->>'result' IS DISTINCT FROM p_result::text
 THEN RETURN jsonb_build_object('problem','CQ_RESTORE_TENURE'); END IF;
 IF public.cq_settings_values_at(g.organism_id,p_cut,p_known)->>'enabled' IS DISTINCT FROM 'true'
 THEN RETURN jsonb_build_object('problem','CQ_RESTORE_OFF'); END IF;
 makeup:=public.c108_board_makeup(g.organism_id,p_cut,p_known);
 IF makeup->>'problem' IS NOT NULL THEN RETURN jsonb_build_object('problem',makeup->>'problem'); END IF;
 IF EXISTS(SELECT FROM public.core_circleparticipation p WHERE p.circle_id=p_circle AND p.seat_id IS NOT NULL
   AND greatest(p.received_at,p.recorded_at)<=p_known
   AND coalesce(public.elect_place_end(p.id,p_known),'infinity'::timestamptz)>greatest(p_cut,p.effective_at)
   AND NOT EXISTS(SELECT FROM public.core_cqact a WHERE a.act_uuid=p.participation_uuid AND a.organism_id=g.organism_id
     AND a.ordinal>g.ordinal AND ((a.operation='IMPLEMENT' AND a.facts#>>'{domain_plan,restoration_result}'=p_result::text)
       OR (a.operation='RECOGNISE' AND a.facts->>'restoration_result'=p_result::text))))
 THEN RETURN jsonb_build_object('problem','CQ_RESTORE_ROSTER'); END IF;
 SELECT coalesce(jsonb_agg(jsonb_build_object(
    'place',p.seat_id,'person',ident.identity_id,'participation',p.participation_uuid,
    'admission',d.constitutional_route,'award',a.act_uuid,'decision',coalesce(nullif(a.request->'result','null'::jsonb),to_jsonb(a.act_uuid)),
    'seat_rule',p.seat_rule_uuid,'binding',coalesce(a.facts#>'{domain_plan,binding}',a.request#>'{claim,binding}'),
    'version',coalesce(a.facts#>'{domain_plan,law_version}',a.request#>'{claim,version}'),
    'provision',coalesce(a.facts#>'{domain_plan,provision}',a.request#>'{claim,provision}'),
    'start',p.effective_at,'end',public.elect_place_end(p.id,p_known),
    'original_start',coalesce(a.facts#>'{domain_plan,term,start}',a.request#>'{claim,term_start}',to_jsonb(p.effective_at)),
    'original_end',coalesce(a.facts#>'{domain_plan,end}',a.facts->'end')
 ) ORDER BY p.seat_id,p.participation_uuid),'[]'), count(DISTINCT m.identity_id),count(DISTINCT p.seat_id),count(*)
 INTO seats,people,places,total
 FROM public.core_circleparticipation p JOIN public.core_circleadmissiondecision d ON d.id=p.decision_id
 JOIN public.core_organismmembership m ON m.id=p.membership_id AND m.living_organism_id=g.organism_id
 JOIN public.core_identity ident ON ident.id=m.identity_id JOIN public.auth_user u ON u.id=ident.credential_id
 JOIN public.core_cqact a ON a.act_uuid=p.participation_uuid AND a.organism_id=g.organism_id AND a.ordinal>g.ordinal
 WHERE p.circle_id=p_circle AND d.outcome='ACCEPT' AND d.seat_id=p.seat_id AND d.seat_rule_uuid=p.seat_rule_uuid
 AND ((a.operation='IMPLEMENT' AND d.constitutional_route='ELECTION' AND a.facts#>>'{domain_plan,type}'='SEAT'
     AND a.facts#>>'{domain_plan,domain}'='ELECTION' AND a.facts#>>'{domain_plan,restoration_result}'=p_result::text)
   OR (a.operation='RECOGNISE' AND d.constitutional_route='RECOGNITION' AND a.facts->>'type'='SEAT'
       AND a.facts->>'restoration_result'=p_result::text AND a.authority_source->>'route' IS DISTINCT FROM 'FOUNDING_COORDINATOR'))
 AND p.recorded_at<=p_known AND p.received_at<=p_known AND a.recorded_at<=p_known AND a.effective_at<=p_cut
 AND public.elect_term_includes(p_cut,p.effective_at,public.elect_place_end(p.id,p_known))
 AND ident.access_state='active' AND u.is_active AND public.circle_place_at(p_circle,m.id,p_cut,p_known)
 AND public.disc_member_state(g.organism_id,m.identity_id,p_cut)='ACTIVE'
 AND EXISTS(SELECT FROM jsonb_array_elements((SELECT rule.body->'seats' FROM public.core_governedrule rule
   WHERE rule.scope_organism_id=g.organism_id AND rule.code='GOVERNANCE_CONSTITUTION'
   AND rule.effective_from<=p_cut AND rule.recorded_at<=p_known
   ORDER BY rule.effective_from DESC,rule.recorded_at DESC,rule.version DESC LIMIT 1)) seat
   WHERE seat->>'seat_id'=p.seat_id AND (seat->>'effective_from')::timestamptz<=p_cut);
 IF people<>total OR places<>total OR people<(makeup->>'minimum')::numeric OR people>(makeup->>'maximum')::numeric
 THEN RETURN jsonb_build_object('problem','CIRCLE_G4_GOVERNANCE_SEATS'); END IF;
 RETURN jsonb_build_object('result',p_result,'implementation',g.act_uuid,'stand_down',g.facts#>'{domain_plan,stand_down}',
 'circle',g.facts#>'{domain_plan,circle}','body',g.board_uuid,'citation',g.facts#>'{domain_plan,citation}',
 'makeup',makeup,'seats',seats);
END $$;
"""

SQL += """
CREATE FUNCTION public.c108_restore_ref(p_facts jsonb) RETURNS text LANGUAGE sql STABLE STRICT AS $$
 SELECT 'restore:'||(p_facts->>'result')||'|down:'||(p_facts->>'stand_down')||'|proof:'||public.c2_target(p_facts::text)
$$;

CREATE FUNCTION public.c108_restore_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
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
END $$;

CREATE FUNCTION public.c108_restore_seat_guard() RETURNS trigger LANGUAGE plpgsql AS $$
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
END $$;
"""

TRIGGERS = """
CREATE TRIGGER c108_restore_snapshot AFTER INSERT ON public.core_circlestateevent FOR EACH ROW
 WHEN (NEW.action='ACTIVATE' AND starts_with(NEW.evidence_reference,'restore:'))
 EXECUTE FUNCTION public.c108_restore_receipt();
CREATE TRIGGER c108_restore_input BEFORE INSERT ON public.core_c108restoration
 FOR EACH ROW EXECUTE FUNCTION public.c108_restore_receipt();
CREATE TRIGGER c108_restore_immutable BEFORE UPDATE OR DELETE ON public.core_c108restoration
 FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER c108_restore_no_truncate BEFORE TRUNCATE ON public.core_c108restoration
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER c108_restore_seat BEFORE INSERT ON public.core_circleparticipation
 FOR EACH ROW WHEN (NEW.seat_id IS NOT NULL) EXECUTE FUNCTION public.c108_restore_seat_guard();
"""
