"""Board custody uses the existing immutable work chain and transaction batch."""

SQL = r"""
CREATE FUNCTION public.cmt_board_occupant(p_board bigint,p_target jsonb,p_at timestamptz,p_known timestamptz)
RETURNS TABLE(person bigint,office bigint,place bigint,occupancy uuid) LANGUAGE sql STABLE AS $$
 SELECT om.identity_id,NULL::bigint,p.id,p.participation_uuid
 FROM public.core_circleparticipation p JOIN public.core_organismmembership om ON om.id=p.membership_id
 WHERE p.circle_id=p_board AND p_target=jsonb_build_object('seat',p.seat_id)
 AND greatest(p.received_at,p.recorded_at)<=p_known AND p.effective_at<=p_at
 AND (public.elect_place_end(p.id,p_known) IS NULL OR p_at<public.elect_place_end(p.id,p_known))
 AND public.s015_fold_effective_state('core_organismmembership',om.id,p_at,p_known)='ACTIVE'
 UNION ALL
 SELECT om.identity_id,a.id,NULL::bigint,a.assignment_uuid FROM public.core_circle c
 JOIN public.core_organismroledefinition d ON d.living_organism_id=c.parent_organism_id
 JOIN public.core_contextualroleassignment a ON a.role_definition_id=d.id
 JOIN public.core_organismmembership om ON om.id=a.membership_id
 WHERE c.id=p_board AND d.kind='BOARD_OFFICE' AND d.scope='LIVING_ORGANISM' AND a.circle_id IS NULL
 AND p_target=jsonb_build_object('office',d.code) AND public.elect_office_effective(a.id,p_at,p_known)
$$;

CREATE FUNCTION public.cmt_board_work_end(p_receipt uuid,p_known timestamptz)
RETURNS timestamptz LANGUAGE sql STABLE AS $$
 SELECT least(public.elect_place_end(p_work.place,p_known),
   (SELECT min(least(public.elect_office_deadline(e.id,p_known),
     (SELECT min(x.effective_at) FROM public.core_contextualroleassignmentstateevent x
      WHERE x.assignment_id=e.assignment_id AND x.resulting_state='ENDED' AND greatest(x.received_at,x.recorded_at)<=p_known)))
    FROM public.core_contextualroleassignmentstateevent e WHERE e.assignment_id=p_work.office
    AND e.action='ASSIGN' AND greatest(e.received_at,e.recorded_at)<=p_known),
   (SELECT min(t.effective_at) FROM public.core_organismmembershiptransition t
    WHERE t.membership_id=coalesce((SELECT p.membership_id FROM public.core_circleparticipation p WHERE p.id=p_work.place),
           (SELECT a.membership_id FROM public.core_contextualroleassignment a WHERE a.id=p_work.office))
    AND t.resulting_state='ENDED' AND greatest(t.received_at,t.recorded_at)<=p_known))
 FROM public.core_cmtcustody p_work WHERE p_work.receipt=p_receipt
$$;

CREATE FUNCTION public.cmt_board_return(p_receipt uuid,p_at timestamptz,p_known timestamptz)
RETURNS TABLE(holder bigint,office bigint,ending timestamptz) LANGUAGE sql STABLE AS $$
 WITH due AS (SELECT public.cmt_board_work_end(p_receipt,p_known) AS moment)
 SELECT om.identity_id,a.id,due.moment FROM due JOIN public.core_cmtcustody w ON w.receipt=p_receipt JOIN public.core_circle b ON b.id=w.board_circle
 LEFT JOIN public.core_contextualroleassignment a ON a.id=public.cmt_board_chair(b.parent_organism_id,due.moment,p_known)
 LEFT JOIN public.core_organismmembership om ON om.id=a.membership_id
 WHERE due.moment<=p_at AND due.moment>=w.effective_at
$$;

CREATE FUNCTION public.cmt_board_work_plan(p_lo bigint,p_board uuid,p_att jsonb,p_at timestamptz)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $$
DECLARE s jsonb:=p_att->'spec'; b public.core_circle; a public.core_serviceactivity; prior_ public.core_cmtcustody;
 worker_ bigint; office_ bigint; place_ bigint; clause_ jsonb; keys_ text[]:=ARRAY['organism','act','activity','target'];
BEGIN
 IF s->>'organism' IS DISTINCT FROM (SELECT organism_id::text FROM public.core_livingorganism WHERE id=p_lo)
 OR public.elect_document(p_att->>'text') IS DISTINCT FROM s THEN PERFORM public.cq_refuse('CQ_ATTACHMENT'); END IF;
 SELECT c.* INTO b FROM public.core_circle c JOIN public.core_discboard db ON db.circle_id=c.id
 WHERE db.board_uuid=p_board AND db.organism_id=p_lo AND c.id=public.cq3_board(p_lo,p_at,p_at);
 IF b.id IS NULL THEN PERFORM public.cq_refuse('CMT_AUTH'); END IF;
 IF s->>'act'='REASSIGN_BOARD_WORK' THEN keys_:=keys_||ARRAY['predecessor','assignee_target','occupancy']; END IF;
 IF NOT s ?& keys_ OR s-keys_<>'{}'::jsonb THEN PERFORM public.cq_refuse('CQ_INPUT'); END IF;
 SELECT * INTO a FROM public.core_serviceactivity WHERE activity_id=(s->>'activity')::uuid
 AND initiating_domain='service' AND initiating_domain_reference='board:'||b.circle_uuid::text
 AND state IN ('assigned','in_progress') FOR UPDATE;
 IF a.id IS NULL THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
 SELECT * INTO prior_ FROM public.core_cmtcustody WHERE activity=a.id
 AND NOT EXISTS(SELECT FROM public.core_cmtcustody n WHERE n.predecessor=core_cmtcustody.receipt);
 IF s->>'act'='BIND_BOARD_WORK' THEN
   IF prior_.receipt IS NOT NULL THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
   IF NOT EXISTS(
     SELECT FROM (SELECT g.body FROM public.core_governedrule g WHERE g.scope_organism_id=p_lo
       AND g.scope_kind='LIVING_ORGANISM' AND g.code='GOVERNANCE_CONSTITUTION'
       AND g.effective_from<=p_at AND g.recorded_at<=p_at ORDER BY g.effective_from DESC,g.version DESC LIMIT 1) current_rule
     CROSS JOIN LATERAL jsonb_array_elements(current_rule.body->'seats') seat
     WHERE s->'target'=jsonb_build_object('seat',seat->>'seat_id') AND (seat->>'effective_from')::timestamptz<=p_at)
   AND NOT EXISTS(SELECT FROM public.core_organismroledefinition d WHERE d.living_organism_id=p_lo AND d.active
     AND d.kind='BOARD_OFFICE' AND d.scope='LIVING_ORGANISM' AND d.code<>'COORDINATOR' AND NOT starts_with(d.code,'INTEVIA_')
     AND s->'target'=jsonb_build_object('office',d.code) AND greatest(d.effective_at,d.recorded_at)<=p_at
     AND NOT EXISTS(SELECT FROM public.core_organismroledefinition n WHERE n.supersedes_id=d.id
       AND greatest(n.effective_at,n.recorded_at)<=p_at)) THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
   office_:=public.cmt_board_chair(p_lo,p_at,p_at);
   SELECT om.identity_id INTO worker_ FROM public.core_contextualroleassignment h
    JOIN public.core_organismmembership om ON om.id=h.membership_id WHERE h.id=office_;
   IF worker_ IS NOT NULL AND NOT EXISTS(SELECT FROM public.core_serviceactivityassignment x
     WHERE x.activity_id=a.id AND x.assignee_id=worker_) THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
 ELSE
   IF prior_.receipt IS NULL OR prior_.receipt::text IS DISTINCT FROM s->>'predecessor' OR
    prior_.board_circle IS DISTINCT FROM b.id OR prior_.board_target IS DISTINCT FROM s->'target'
   THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
   SELECT o.person,o.office,o.place INTO worker_,office_,place_ FROM public.cmt_board_occupant(b.id,s->'assignee_target',p_at,p_at) o
    WHERE o.occupancy::text=s->>'occupancy';
   IF worker_ IS NULL THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
 END IF;
 clause_:=public.cmt_work_clause(p_lo,NULL,p_at,p_at);
 IF clause_ IS NULL THEN PERFORM public.cq_refuse('CMT_WORK_LAW'); END IF;
 RETURN jsonb_build_object('domain','COMMITTEE','route','CQ_GOVERNANCE_CIRCLE','committee',s||
   jsonb_build_object('board_id',b.id,'activity_id',a.id,'holder',worker_,'office_id',office_,
    'place_id',place_,'mandate',clause_->'binding','clause',clause_,'predecessor',prior_.receipt));
END $$;

CREATE FUNCTION public.cmt_refresh_board_activity(p_id uuid,p_cut timestamptz,p_known timestamptz)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE head_ public.core_cmtcustody; next_ record;
BEGIN
 PERFORM a.id FROM public.core_serviceactivity a WHERE a.activity_id=p_id FOR UPDATE;
 SELECT w.* INTO head_ FROM public.core_cmtcustody w JOIN public.core_serviceactivity a ON a.id=w.activity
 WHERE a.activity_id=p_id AND a.state IN ('assigned','in_progress') AND w.board_circle IS NOT NULL
 AND NOT EXISTS(SELECT FROM public.core_cmtcustody n WHERE n.predecessor=w.receipt);
 IF head_.receipt IS NULL THEN RETURN; END IF;
 SELECT * INTO next_ FROM public.cmt_board_return(head_.receipt,p_cut,p_known);
 IF FOUND THEN
 INSERT INTO public.core_cmtcustody(receipt,activity,mandate,predecessor,holder,office,clause,cause,effective_at,
   recorded_at,board_circle,board_target)
 VALUES(gen_random_uuid(),head_.activity,head_.mandate,head_.receipt,next_.holder,next_.office,head_.clause,
   jsonb_build_object('ending_place',head_.place,'ending_office',head_.office),next_.ending,p_known,
   head_.board_circle,head_.board_target);
 END IF;
END $$;
"""
