"""Temporal facts used by election admission, office competence and late recording."""

SQL = r"""
CREATE FUNCTION public.elect_term_includes(p_at timestamptz,p_start timestamptz,p_end timestamptz)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
 SELECT p_at >= p_start AND (p_end IS NULL OR p_at < p_end) -- ELECT_EXCLUSIVE_END
$$;

CREATE FUNCTION public.elect_place_end(p_id bigint,p_known timestamptz)
RETURNS timestamptz LANGUAGE sql STABLE STRICT AS $$
 SELECT min(e.effective_at) FROM public.core_circleparticipationend e
 WHERE e.participation_id=p_id AND e.received_at<=p_known AND e.recorded_at<=p_known
$$;

-- The office term belongs to its ASSIGN event. A dependent office can also
-- end with the particular Board place supporting it when that office started.
CREATE FUNCTION public.elect_office_deadline(p_assign bigint,p_known timestamptz)
RETURNS timestamptz LANGUAGE sql STABLE STRICT AS $$
 SELECT least(e.effective_until,
   (SELECT public.elect_place_end(p.id,p_known) FROM public.core_circleparticipation p
    JOIN public.core_circle c ON c.id=p.circle_id
    WHERE d.kind='BOARD_OFFICE' AND d.participation_required AND c.kind='GOVERNANCE'
    AND c.parent_organism_id=d.living_organism_id AND p.membership_id=a.membership_id
    AND p.received_at<=p_known AND p.recorded_at<=p_known
    AND public.elect_term_includes(e.effective_at,p.effective_at,public.elect_place_end(p.id,p_known))
    ORDER BY p.effective_at DESC,p.id DESC LIMIT 1))
 FROM public.core_contextualroleassignmentstateevent e
 JOIN public.core_contextualroleassignment a ON a.id=e.assignment_id
 JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
 WHERE e.id=p_assign AND e.action='ASSIGN' AND e.recorded_at<=p_known AND e.received_at<=p_known
$$;

CREATE FUNCTION public.elect_office_effective(p_id bigint,p_at timestamptz,p_known timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 SELECT EXISTS(SELECT FROM public.core_contextualroleassignment a
 JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
 JOIN public.core_contextualroleassignmentstateevent e ON e.assignment_id=a.id AND e.action='ASSIGN'
 WHERE a.id=p_id AND greatest(e.received_at,e.recorded_at)<=p_known
 AND public.elect_term_includes(p_at,e.effective_at,public.elect_office_deadline(e.id,p_known))
 AND public.s015_fold_effective_state('core_contextualroleassignment',a.id,p_at,p_known)='ACTIVE'
 AND public.s015_fold_effective_state('core_organismmembership',a.membership_id,p_at,p_known)='ACTIVE'
 AND (d.kind IS DISTINCT FROM 'BOARD_OFFICE' OR d.participation_required IS NOT TRUE OR EXISTS(
    SELECT FROM public.core_circle c JOIN public.core_circleparticipation p ON p.circle_id=c.id
    WHERE c.parent_organism_id=d.living_organism_id AND c.kind='GOVERNANCE' AND p.membership_id=a.membership_id
    AND greatest(p.received_at,p.recorded_at)<=p_known AND p.effective_at<=e.effective_at
    AND public.elect_term_includes(p_at,p.effective_at,public.elect_place_end(p.id,p_known)))))
$$;

-- Enumerate the recorded intervals competing with this particular mandate.
-- A proposal alone reserves nothing. Suspended offices still occupy a place.
CREATE FUNCTION public.elect_term_slots(p_circle bigint,p_role bigint,p_place text,p_member bigint,p_known timestamptz)
RETURNS TABLE(occupancy uuid,starts timestamptz,ends timestamptz) LANGUAGE sql STABLE AS $$
 SELECT p.participation_uuid,p.effective_at,least(public.elect_place_end(p.id,p_known),depart.effective_at)
 FROM public.core_circleparticipation p
 LEFT JOIN LATERAL (SELECT min(t.effective_at) AS effective_at FROM public.core_organismmembershiptransition t
   WHERE t.membership_id=p.membership_id AND t.resulting_state='ENDED'
   AND greatest(t.received_at,t.recorded_at)<=p_known) depart ON true
 WHERE p_role IS NULL AND p.circle_id=p_circle AND greatest(p.received_at,p.recorded_at)<=p_known
 AND (p.seat_id=p_place OR p.membership_id=p_member)
 UNION ALL
 SELECT a.assignment_uuid,e.effective_at,least(public.elect_office_deadline(e.id,p_known),ended.effective_at,depart.effective_at)
 FROM public.core_organismroledefinition target JOIN public.core_organismroledefinition d
   ON d.code=target.code AND d.scope=target.scope AND d.living_organism_id=target.living_organism_id
 JOIN public.core_contextualroleassignment a ON a.role_definition_id=d.id
 JOIN public.core_contextualroleassignmentstateevent e ON e.assignment_id=a.id AND e.action='ASSIGN'
 LEFT JOIN LATERAL (SELECT min(h.effective_at) AS effective_at FROM public.core_contextualroleassignmentstateevent h
    WHERE h.assignment_id=a.id AND h.resulting_state='ENDED' AND greatest(h.received_at,h.recorded_at)<=p_known) ended ON true
 LEFT JOIN LATERAL (SELECT min(t.effective_at) AS effective_at FROM public.core_organismmembershiptransition t
    WHERE t.membership_id=a.membership_id AND t.resulting_state='ENDED'
    AND greatest(t.received_at,t.recorded_at)<=p_known) depart ON true
 WHERE target.id=p_role AND greatest(e.received_at,e.recorded_at)<=p_known
$$;

CREATE FUNCTION public.elect_due_office(p_id bigint,p_known timestamptz)
RETURNS TABLE(source_id bigint,due timestamptz) LANGUAGE sql STABLE STRICT AS $$
 SELECT e.id,b.ending FROM public.core_contextualroleassignment a
 JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
 JOIN public.core_contextualroleassignmentstateevent e ON e.assignment_id=a.id AND e.action='ASSIGN'
 CROSS JOIN LATERAL (SELECT public.elect_office_deadline(e.id,p_known) AS ending) b
 WHERE a.id=p_id AND d.kind='BOARD_OFFICE' AND d.code<>'COORDINATOR' AND NOT starts_with(d.code,'INTEVIA_')
 AND d.scope='LIVING_ORGANISM' AND a.circle_id IS NULL
 AND e.effective_at<b.ending AND b.ending<=p_known
 AND NOT EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent h
  WHERE h.assignment_id=a.id AND h.resulting_state='ENDED' AND greatest(h.received_at,h.recorded_at)<=p_known)
 AND public.s015_fold_effective_state('core_contextualroleassignment',a.id,b.ending,p_known) IN ('ACTIVE','SUSPENDED')
$$;

CREATE FUNCTION public.elect_record_term_end(p_lo bigint,p_id bigint,p_enterer bigint)
RETURNS boolean LANGUAGE plpgsql VOLATILE AS $$
DECLARE src public.core_contextualroleassignmentstateevent; head_ public.core_contextualroleassignmentstateevent;
 boundary_ timestamptz; cut_ timestamptz:=statement_timestamp(); key_ text;
BEGIN
 PERFORM 1 FROM public.core_livingorganism WHERE id=p_lo FOR UPDATE;
 IF public.disc_member_state(p_lo,p_enterer,cut_) IS DISTINCT FROM 'ACTIVE' OR NOT EXISTS(
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
END $$;
"""
