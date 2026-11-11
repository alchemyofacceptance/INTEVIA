"""Private stand-down evidence and the rule fired by GOVERNANCE activation."""

# The discussion board UUID identifies the electorate. For a constitutional
# exception, read the designation from a live local Article or Bylaw, never
# from a claim supplied by the proposer. Ambiguous designations fail closed.
AUTHORITY = """
CREATE FUNCTION public.c108_body_matches(p_lo bigint,p_board uuid,p_citation jsonb,
    p_effective timestamptz,p_known timestamptz) RETURNS boolean LANGUAGE sql STABLE AS $$
 WITH articles AS (
   SELECT law.binding,law.version,public.elect_document(v.content)->'board_stand_down' AS entries
   FROM public.core_livingorganism lo
   CROSS JOIN LATERAL public.l_laws_at(lo.organism_id,p_effective,p_known) law
   JOIN public.core_libraryresourceversion v ON v.id=law.version
   WHERE lo.id=p_lo AND NOT law.inherited AND law.class_name IN ('ARTICLE','BYLAW')
 ), provisions AS (
   SELECT a.binding,a.version,clause.key,clause.value FROM articles a
   CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(a.entries)='object' THEN a.entries ELSE '{}' END) clause
 )
 SELECT EXISTS(SELECT FROM public.core_discboard b WHERE b.board_uuid=p_board AND b.organism_id=p_lo
   AND NOT EXISTS(SELECT FROM articles WHERE entries IS NOT NULL AND jsonb_typeof(entries)<>'object')
   AND CASE WHEN NOT EXISTS(SELECT FROM provisions) THEN
      b.circle_id IS NULL AND p_citation='null'::jsonb
   ELSE (SELECT count(*)=1 AND bool_and(p.value->>'body'=b.board_uuid::text
      AND p_citation=jsonb_build_object('binding',p.binding,'version',p.version,'provision',p.key))
      FROM provisions p) END)
$$;

CREATE FUNCTION public.c108_active_lifecycle(p_circle bigint,p_at timestamptz,p_known timestamptz)
RETURNS uuid LANGUAGE sql STABLE AS $$
 SELECT CASE WHEN e.resulting_state='ACTIVE' THEN e.event_uuid END
 FROM unnest(public.s015_eligible_event_ids('core_circle',p_circle,p_at,p_known)) WITH ORDINALITY cut(id,n)
 JOIN public.core_circlestateevent e ON e.id=cut.id ORDER BY cut.n DESC LIMIT 1
$$;

CREATE FUNCTION public.c108_stand_down_authorised(p_result uuid,p_circle bigint,p_effective timestamptz,
    p_known timestamptz) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(
   SELECT FROM public.core_circle c JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id
   JOIN public.core_cqact r ON r.organism_id=lo.id AND r.act_uuid=p_result AND r.operation='SETTLE'
   JOIN public.core_cqact i ON i.organism_id=lo.id AND i.board_uuid=r.board_uuid
      AND i.thread_uuid=r.thread_uuid AND i.operation='INITIATE'
   JOIN public.core_cqact o ON o.organism_id=lo.id AND o.board_uuid=r.board_uuid AND o.thread_uuid=r.thread_uuid
      AND o.act_uuid::text=r.request->>'ballot' AND o.operation IN ('PROCEED','OPEN_VOTE','REOPEN_VOTE')
   CROSS JOIN LATERAL (SELECT i.request->'attachment' AS att) a
   CROSS JOIN LATERAL (SELECT a.att->'spec' AS spec,public.disc_fingerprint(a.att) AS fp) f
   CROSS JOIN LATERAL (SELECT public.c108_active_lifecycle(c.id,p_effective,p_known) AS event_uuid) life
   WHERE c.id=p_circle AND c.kind='GOVERNANCE' AND NOT lo.platform_root
     AND p_effective<=p_known AND r.recorded_at<=p_known AND r.effective_at<=p_effective
     AND life.event_uuid IS NOT NULL
     AND life.event_uuid=public.c108_active_lifecycle(c.id,i.effective_at,i.recorded_at)
     AND life.event_uuid=public.c108_active_lifecycle(c.id,p_known,p_known)
     AND public.cq3_qualified(r.facts->'count'->>'kind') AND r.facts->'count'->'cqt_applied'='true'::jsonb
     AND r.facts->'ballot'=to_jsonb(o.act_uuid) AND r.facts->'version'=o.facts->'version'
     AND r.facts->'wording_fingerprint'=o.facts->'wording_fingerprint'
     AND r.facts->'domain_plan'->>'domain'='BOARD_STAND_DOWN'
     AND r.facts->'result_authority'->>'reference'=r.act_uuid::text
     AND r.facts->>'attachment_fingerprint'=f.fp AND o.facts->>'attachment_fingerprint'=f.fp
     AND i.facts->>'attachment_fingerprint'=f.fp
     AND a.att->>'kind'='BOARD_STAND_DOWN'
     AND f.spec=jsonb_build_object('organism',lo.organism_id,'circle',c.circle_uuid,'act','STAND_DOWN',
       'lifecycle',life.event_uuid,'body',r.board_uuid,'effective_at',f.spec->'effective_at','citation',f.spec->'citation')
     AND CASE WHEN f.spec->>'effective_at'='AT_RESULT' THEN p_effective=r.effective_at
              ELSE f.spec->'effective_at'=to_jsonb(p_effective) END
     AND public.c108_body_matches(lo.id,r.board_uuid,f.spec->'citation',i.effective_at,i.recorded_at)
     AND public.c108_body_matches(lo.id,r.board_uuid,f.spec->'citation',o.effective_at,o.recorded_at)
     AND public.c108_body_matches(lo.id,r.board_uuid,f.spec->'citation',r.effective_at,r.recorded_at)
     AND public.c108_body_matches(lo.id,r.board_uuid,f.spec->'citation',p_effective,p_known)
     AND public.c108_body_matches(lo.id,r.board_uuid,f.spec->'citation',p_known,p_known)
     AND r.facts->>'disposition'='APPLIED')
$$;
"""

# The existing append-only evidence fields are part of the S015 fingerprint.
# Reuse them so adding a column never changes the preimage of a historic act.
INDEX = """
CREATE UNIQUE INDEX c108_one_stand_down_per_result
ON public.core_circlestateevent(evidence_reference)
WHERE starts_with(evidence_reference,'c108-stand-down:');
"""

CAUSE = """
CREATE FUNCTION public.c108_executing_result(p_marker text) RETURNS uuid LANGUAGE sql STABLE AS $$
 SELECT r.act_uuid FROM public.core_cqact i JOIN public.core_cqact r
   ON i.request->>'result'=r.act_uuid::text AND i.organism_id=r.organism_id AND i.thread_uuid=r.thread_uuid
 WHERE i.operation='IMPLEMENT' AND i.execution_xid=pg_current_xact_id()
   AND i.facts->'domain_plan'->>'domain'='BOARD_STAND_DOWN' AND r.operation='SETTLE'
   AND p_marker='c108-stand-down:'||r.act_uuid
$$;

CREATE FUNCTION public.c108_check_stand_down() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE result_ uuid;
BEGIN
 result_:=public.c108_executing_result(NEW.evidence_reference);
 IF NEW.evidence_reference IS DISTINCT FROM 'c108-stand-down:'||result_ OR NEW.prior_state IS DISTINCT FROM 'ACTIVE' OR NEW.resulting_state IS DISTINCT FROM 'DORMANT'
   OR NOT public.c108_stand_down_authorised(result_,NEW.circle_id,
        NEW.effective_at,statement_timestamp()) THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.c108_check_ending() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE life public.core_circlestateevent; marker text;
BEGIN
 IF TG_TABLE_NAME='core_circleparticipationend' THEN marker:=NEW.composing_rule_reference;
 ELSE marker:=NEW.evidence_reference; END IF;
 SELECT * INTO life FROM public.core_circlestateevent WHERE evidence_reference=marker;
 IF life.id IS NULL OR public.c108_executing_result(marker) IS NULL OR life.effective_at IS DISTINCT FROM NEW.effective_at THEN
   PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF TG_TABLE_NAME='core_circleparticipationend' THEN
   IF marker IS DISTINCT FROM 'c108-stand-down:'||NEW.composed_from_event_uuid
      OR NOT EXISTS(SELECT FROM public.core_circleparticipation p WHERE p.id=NEW.participation_id
       AND p.circle_id=life.circle_id AND p.seat_id IS NOT NULL) THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 ELSE
   IF NEW.resulting_state IS DISTINCT FROM 'ENDED' OR NOT EXISTS(
       SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
       JOIN public.core_organismmembership m ON m.id=a.membership_id
       JOIN public.core_circle c ON c.id=life.circle_id AND c.parent_organism_id=m.living_organism_id
       WHERE a.id=NEW.assignment_id AND ((a.circle_id=c.id AND d.scope='CIRCLE') OR
         (a.circle_id IS NULL AND d.living_organism_id=c.parent_organism_id AND d.scope='LIVING_ORGANISM' AND d.kind='BOARD_OFFICE' AND d.code<>'COORDINATOR' AND NOT starts_with(d.code,'INTEVIA_'))))
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 END IF;
 RETURN NEW;
END $$;
"""

CAUSE_TRIGGERS = """
CREATE TRIGGER c108_stand_down BEFORE INSERT ON public.core_circlestateevent
 FOR EACH ROW WHEN (starts_with(NEW.evidence_reference,'c108-stand-down:')) EXECUTE FUNCTION public.c108_check_stand_down();
CREATE TRIGGER c108_seat_cause BEFORE INSERT ON public.core_circleparticipationend
 FOR EACH ROW WHEN (starts_with(NEW.composing_rule_reference,'c108-stand-down:')) EXECUTE FUNCTION public.c108_check_ending();
CREATE TRIGGER c108_office_cause BEFORE INSERT ON public.core_contextualroleassignmentstateevent
 FOR EACH ROW WHEN (starts_with(NEW.evidence_reference,'c108-stand-down:')) EXECUTE FUNCTION public.c108_check_ending();
"""

OPEN_MOTIONS = """
CREATE FUNCTION public.c108_open_off(p_lo bigint)
RETURNS TABLE(thread_uuid uuid,board_uuid uuid,phase_act uuid) LANGUAGE sql STABLE AS $$
 SELECT h.thread_uuid,i.board_uuid,h.act_uuid
 FROM public.core_cqact i JOIN public.cq_phases h ON h.thread_uuid=i.thread_uuid
 WHERE i.organism_id=p_lo AND i.operation='INITIATE' AND h.phase IN ('PREPARATION','CLARITY','VOTING')
   AND i.request->'attachment'->>'kind'='SETTINGS'
   AND i.request->'attachment'->'spec'->'changes'->'enabled'='false'::jsonb
$$;

CREATE FUNCTION public.c108_close_for_activation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner_ public.core_livingorganism; epoch_ bigint; motion record;
BEGIN
 SELECT lo.* INTO owner_ FROM public.core_livingorganism lo JOIN public.core_circle c ON c.parent_organism_id=lo.id
 WHERE c.id=NEW.circle_id AND c.kind='GOVERNANCE' AND NOT lo.platform_root;
 IF NOT FOUND THEN RETURN NULL; END IF;
 PERFORM public.s015_lock_organisms(ARRAY[owner_.organism_id]);
 SELECT access_epoch INTO STRICT epoch_ FROM public.core_identity WHERE id=NEW.enterer_identity_id;
 FOR motion IN SELECT * FROM public.c108_open_off(owner_.id) ORDER BY thread_uuid LOOP
   INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,
       actor_id,actor_epoch,received_at,request)
   VALUES(gen_random_uuid(),owner_.id,motion.board_uuid,motion.thread_uuid,'CLOSE',
       NEW.enterer_identity_id,epoch_,statement_timestamp(),
       jsonb_build_object('activation',NEW.event_uuid,'previous_phase',motion.phase_act));
 END LOOP;
 RETURN NULL;
END $$;

CREATE FUNCTION public.c108_close_source(a public.core_cqact,p_stamp timestamptz) RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT jsonb_build_object('kind','RULE','route','GOVERNANCE_ACTIVATION','reference',e.event_uuid)
 FROM public.core_circlestateevent e JOIN public.core_circle c ON c.id=e.circle_id
 JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id
 WHERE a.request->>'activation'=e.event_uuid::text AND a.organism_id=lo.id AND NOT lo.platform_root
   AND c.kind='GOVERNANCE' AND e.prior_state IS DISTINCT FROM 'ACTIVE' AND e.resulting_state='ACTIVE'
   AND a.actor_id=e.enterer_identity_id AND e.recorded_at=p_stamp AND a.received_at=p_stamp
$$;

CREATE FUNCTION public.c108_close_facts(a public.core_cqact,p_stamp timestamptz) RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE h public.core_cqact; wording public.discp_posts;
BEGIN
 SELECT phase.* INTO h FROM public.c108_open_off(a.organism_id) m
 JOIN public.core_cqact phase ON phase.act_uuid=m.phase_act
 WHERE m.thread_uuid=a.thread_uuid AND m.board_uuid=a.board_uuid AND m.phase_act::text=a.request->>'previous_phase';
 IF NOT FOUND THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 SELECT * INTO STRICT wording FROM public.discp_posts WHERE thread_uuid=a.thread_uuid AND post_number=1;
 RETURN jsonb_build_object('phase','CLOSED','event_kind','DECISION','reason',
   'A Board became active; voting stays on while a Board is active',
   'activation',a.request->'activation','previous_phase',h.act_uuid,
   'ballot',CASE WHEN h.facts->>'phase'='VOTING' THEN h.act_uuid END,
   'version',wording.version_uuid,'post_number',public.cq_next_post(a.thread_uuid),
   'closed_at',p_stamp,'settled_at',p_stamp,'restart',false,'retry_requested',false,
   'reporting_rule',coalesce(h.facts->'reporting_rule',public.cq4_board_reporting(a.organism_id,a.board_uuid,a.thread_uuid,p_stamp)),
   'count',jsonb_build_object('kind','NO_DECISION_BOARD_ACTIVATED','cqt_applied',false));
END $$;
"""

ACTIVATION_TRIGGER = """
CREATE TRIGGER c108_close_for_activation AFTER INSERT ON public.core_circlestateevent
 FOR EACH ROW WHEN (NEW.resulting_state='ACTIVE' AND NEW.prior_state IS DISTINCT FROM 'ACTIVE')
 EXECUTE FUNCTION public.c108_close_for_activation();
"""
