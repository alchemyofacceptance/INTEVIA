"""Committee authority derived from exact LAW and CQ receipts."""

from . import _committee_board_work as board_work

SCHEMA = """
CREATE VIEW public.cmt_mandates AS
 SELECT a.organism_id,a.act_uuid,a.effective_at,a.recorded_at,a.ordinal,a.board_uuid,
        (f->>'circle_id')::bigint AS circle_id,(f->>'binding')::uuid AS binding,
        (f->>'chair_id')::bigint AS chair_id,f AS mandate
 FROM public.core_cqact a CROSS JOIN LATERAL (SELECT a.facts->'domain_plan'->'committee' AS f) x
 WHERE a.operation='IMPLEMENT' AND a.facts->'domain_plan'->>'domain'='LAW' AND f->>'type'='FORMATION';
CREATE INDEX cmt_mandate_history ON public.core_cqact(organism_id,ordinal DESC)
 WHERE operation='IMPLEMENT' AND facts->'domain_plan' ? 'committee';
"""

SQL = r"""
CREATE FUNCTION public.cmt_activation_choice(p_lo bigint,p_at timestamptz,p_known timestamptz)
RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT jsonb_build_object('key','activation.chair_board_member','activation',e.event_uuid,
   'source',CASE WHEN right(e.evidence_reference,2) IN ('BY','BN') THEN 'BIRTHING' ELSE 'ACTIVATE' END,
   'required',right(e.evidence_reference,1)='Y')
 FROM public.core_circle c JOIN public.core_circlestateevent e ON e.circle_id=c.id
 WHERE c.parent_organism_id=p_lo AND c.kind='GOVERNANCE' AND e.action='ACTIVATE' AND e.resulting_state='ACTIVE'
 AND e.effective_at<=p_at AND e.recorded_at<=p_known AND e.received_at<=p_known
 AND e.evidence_reference ~ '[|]cr:B?[YN]$' ORDER BY e.effective_at DESC,e.sequence DESC LIMIT 1
$$;

CREATE FUNCTION public.cmt_choice(p_lo bigint,p_at timestamptz,p_known timestamptz,p_constitution boolean)
RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT coalesce((SELECT jsonb_build_object('binding',l.binding,'version',l.version,'key','committees.chair_board_member',
     'required',doc->'committees'->'chair_board_member','sub_floor',doc->'committees'->'sub_board_member')
 FROM public.core_livingorganism o CROSS JOIN LATERAL public.l_laws_at(o.organism_id,p_at,p_known) l
 JOIN public.core_libraryresourceversion v ON v.id=l.version
 CROSS JOIN LATERAL (SELECT public.elect_document(v.content) AS doc) x
 WHERE o.id=p_lo AND NOT l.inherited AND l.scope LIKE 'LO:%'
 AND jsonb_typeof(doc->'committees'->'chair_board_member')='boolean'
 AND ((p_constitution AND l.class_name IN ('ARTICLE','BYLAW')) OR
      (NOT p_constitution AND l.class_name='POLICY' AND l.decision_route='CQ_GOVERNANCE_CIRCLE'))
 ORDER BY public.l_law_rank(l.class_name),l.effective_from DESC,l.binding LIMIT 1),
   CASE WHEN NOT p_constitution THEN public.cmt_activation_choice(p_lo,p_at,p_known) END)
$$;

CREATE FUNCTION public.cmt_activation_suffix(p_lo bigint,p_proof text,p_explicit boolean,p_at timestamptz,p_known timestamptz)
RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE chosen jsonb;
BEGIN
 IF public.cmt_choice(p_lo,p_at,p_known,true) IS NOT NULL THEN
   IF p_explicit IS NULL THEN RETURN ''; END IF;
   RETURN NULL;
 END IF;
 SELECT s.body->'settings'->'committee_chair_board_member' INTO chosen
 FROM public.core_lobirthingsnapshot s WHERE s.living_organism_id=p_lo
 AND 'birth:'||s.snapshot_uuid::text=split_part(p_proof,'|',1) AND s.recorded_at<=p_known AND s.effective_at<=p_at;
 IF jsonb_typeof(chosen)='boolean' THEN
   IF p_explicit IS NOT NULL AND to_jsonb(p_explicit)<>chosen THEN RETURN NULL; END IF;
   RETURN CASE WHEN chosen='true'::jsonb THEN '|cr:BY' ELSE '|cr:BN' END;
 END IF;
 RETURN CASE p_explicit WHEN true THEN '|cr:Y' WHEN false THEN '|cr:N' END;
END $$;

CREATE FUNCTION public.cmt_current(p_circle bigint,p_at timestamptz,p_known timestamptz)
RETURNS public.cmt_mandates LANGUAGE sql STABLE AS $$
 SELECT m.* FROM public.cmt_mandates m JOIN public.core_organismlawbinding b ON b.binding_uuid=m.binding
 WHERE m.circle_id=p_circle AND m.effective_at<=p_at AND m.recorded_at<=p_known
 AND public.l_visible_window(b,p_at,p_known) ORDER BY m.ordinal DESC LIMIT 1
$$;

CREATE FUNCTION public.cmt_live(p_binding uuid,p_at timestamptz,p_known timestamptz)
RETURNS boolean LANGUAGE sql STABLE AS $$
 WITH RECURSIVE ancestors AS (
  SELECT m.*,ARRAY[m.binding] AS path FROM public.cmt_mandates m WHERE m.binding=p_binding
  UNION ALL SELECT p.*,a.path||p.binding FROM ancestors a JOIN public.cmt_mandates p
     ON p.binding=(a.mandate->>'delegating_parent')::uuid WHERE NOT p.binding=ANY(a.path)
 )
 SELECT count(*)>0 AND bool_and((public.cmt_current(a.circle_id,p_at,p_known)).binding IS NOT DISTINCT FROM a.binding
     AND public.s015_fold_effective_state('core_circle',a.circle_id,p_at,p_known)='ACTIVE'
     AND (a.mandate->>'ends_at' IS NULL OR p_at<(a.mandate->>'ends_at')::timestamptz)
     AND NOT EXISTS(SELECT FROM public.core_cqact i WHERE i.organism_id=a.organism_id AND i.operation='IMPLEMENT'
      AND i.facts->'domain_plan'->'committee'->>'completed'=a.binding::text
      AND i.recorded_at<=p_known AND i.effective_at<=p_at)) FROM ancestors a
$$;

CREATE FUNCTION public.cmt_trustees(p_lo bigint,p_circle bigint,p_at timestamptz,p_known timestamptz)
RETURNS TABLE(member_id bigint) LANGUAGE sql STABLE AS $$
 SELECT DISTINCT p.membership_id FROM public.core_circleparticipation p
 JOIN public.core_organismmembership m ON m.id=p.membership_id AND m.living_organism_id=p_lo
 WHERE p.circle_id=public.cq3_board(p_lo,p_at,p_known)
 AND public.circle_place_at(p.circle_id,p.membership_id,p_at,p_known)
 AND public.circle_place_at(p_circle,p.membership_id,p_at,p_known)
 AND public.elect_term_includes(p_at,p.effective_at,public.elect_place_end(p.id,p_known))
 AND greatest(p.received_at,p.recorded_at)<=p_known
$$;

CREATE FUNCTION public.cmt_chair_rule(p_lo bigint,p_terms jsonb,p_at timestamptz,p_known timestamptz)
RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT coalesce(public.cmt_choice(p_lo,p_at,p_known,true),
    CASE WHEN jsonb_typeof(p_terms->'chair_board_member')='boolean'
       THEN jsonb_build_object('required',p_terms->'chair_board_member','key','terms.chair_board_member') END,
    public.cmt_choice(p_lo,p_at,p_known,false))
$$;

CREATE FUNCTION public.cmt_floor_rule(p_lo bigint,p_at timestamptz,p_known timestamptz)
RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT coalesce(public.cmt_choice(p_lo,p_at,p_known,true),public.cmt_choice(p_lo,p_at,p_known,false))
$$;

CREATE FUNCTION public.cmt_officers(p_lo bigint,p_circle bigint,p_at timestamptz)
RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT jsonb_build_object('count',count(DISTINCT m.identity_id),'required',3,
  'offices',coalesce(jsonb_agg(jsonb_build_object('holder',i.identity_id,'office',a.assignment_uuid,
     'code',d.code) ORDER BY a.assignment_uuid),'[]'::jsonb),'basis','HG:COMMITTEES:20261109:LEVEL_1')
 FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
 JOIN public.core_organismmembership m ON m.id=a.membership_id JOIN public.core_identity i ON i.id=m.identity_id
 WHERE d.living_organism_id=p_lo AND d.kind='BOARD_OFFICE' AND d.scope='LIVING_ORGANISM' AND a.circle_id IS NULL
 AND d.code<>'COORDINATOR' AND NOT starts_with(d.code,'INTEVIA_')
 AND public.elect_office_effective(a.id,p_at,p_at) AND public.circle_place_at(p_circle,m.id,p_at,p_at)
$$;

CREATE FUNCTION public.cmt_form_plan(p_lo bigint,p_board uuid,p_att jsonb,p_at timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE s jsonb:=p_att->'spec'; doc jsonb:=public.elect_document(p_att->>'text');
 cmd jsonb:=doc->'committee'; refs jsonb:=cmd->'terms'; raw_ jsonb; tor public.core_libraryresourceversion;
 circle_ public.core_circle; chair_ public.core_contextualroleassignment; parent_ public.cmt_mandates;
 latest public.cmt_mandates; caller bigint; gov bigint:=public.cq3_board(p_lo,p_at,p_at);
 basis jsonb; chair_basis jsonb; data_ jsonb; evidence jsonb; until_ timestamptz; scope_ uuid;
 terms_effect public.core_organismlawbinding; prior public.core_organismlawbinding;
BEGIN
 IF cmd IS NOT NULL AND (jsonb_typeof(cmd)<>'object' OR cmd-ARRAY['circle','chair','kind','level','terms','ends_at','task','completion_body',
   'appointing_body','parent','continue_until_replaced','stand_in']<>'{}' OR jsonb_typeof(refs) IS DISTINCT FROM 'object')
 THEN PERFORM public.cq_refuse('CMT_FORM'); END IF;
 IF cmd IS NULL THEN RETURN '{}'::jsonb; END IF;
 SELECT circle_id INTO caller FROM public.core_discboard WHERE board_uuid=p_board AND organism_id=p_lo;
 IF s->>'operation'<>'ADOPT' THEN RETURN '{}'::jsonb; END IF;
 IF s->>'law_class' NOT IN ('POLICY','BYLAW') OR gov IS NULL THEN RETURN jsonb_build_object('code','CMT_AUTH','reason','A competent body must adopt the forming law.'); END IF;
 IF cmd->>'kind' IS NULL OR cmd->>'kind' NOT IN ('STANDING','SUB_COMMITTEE','AD_HOC')
 OR cmd->>'level' IS NULL OR cmd->>'level' NOT IN ('1','2','3','4') THEN PERFORM public.cq_refuse('CMT_FORM'); END IF;
 IF EXISTS(WITH RECURSIVE ancestors AS (SELECT m.circle_id,m.mandate->>'parent_binding' AS parent FROM public.cmt_mandates m
   WHERE m.mandate->>'circle'=cmd->>'parent' AND m.binding=(public.cmt_current(m.circle_id,p_at,p_at)).binding
   UNION SELECT m.circle_id,m.mandate->>'parent_binding' FROM ancestors a JOIN public.cmt_mandates m ON m.binding::text=a.parent)
   SELECT FROM ancestors a JOIN public.core_circle c ON c.id=a.circle_id WHERE c.circle_uuid::text=cmd->>'circle')
 THEN PERFORM public.cq_refuse('CMT_PARENT'); END IF;
 SELECT * INTO circle_ FROM public.core_circle WHERE parent_organism_id=p_lo AND circle_uuid=(cmd->>'circle')::uuid;
 SELECT a.* INTO chair_ FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
 WHERE a.circle_id=circle_.id AND a.assignment_uuid=(cmd->>'chair')::uuid AND d.code='COORDINATOR' AND d.scope='CIRCLE'
 AND EXISTS(SELECT FROM public.c2_seat_facts(p_lo,p_at,p_at) f WHERE f.assignment_id=a.id AND f.standing='QUALIFIED');
 IF circle_.id IS NULL OR chair_.id IS NULL OR public.s015_fold_effective_state('core_circle',circle_.id,p_at,p_at) IS DISTINCT FROM 'ACTIVE'
 THEN PERFORM public.cq_refuse('CMT_FORM'); END IF;
 latest:=public.cmt_current(circle_.id,p_at,p_at);
 IF latest.binding::text IS DISTINCT FROM s->>'predecessor' AND latest.binding IS NOT NULL THEN PERFORM public.cq_refuse('CMT_STALE'); END IF;
 IF cmd->>'kind'='SUB_COMMITTEE' THEN
   SELECT m.* INTO parent_ FROM public.core_circle c CROSS JOIN LATERAL public.cmt_current(c.id,p_at,p_at) m
    WHERE c.parent_organism_id=p_lo AND c.circle_uuid=(cmd->>'parent')::uuid;
   IF parent_.binding IS NULL OR parent_.circle_id=circle_.id OR NOT public.cmt_live(parent_.binding,p_at,p_at)
    THEN PERFORM public.cq_refuse('CMT_PARENT'); END IF;
 END IF;
 IF caller IS DISTINCT FROM gov AND NOT(caller IS NULL AND s->>'law_class'='BYLAW') THEN
   IF parent_.circle_id IS DISTINCT FROM caller OR parent_.binding IS NULL OR parent_.mandate->>'level'='4'
    OR NOT coalesce((parent_.mandate->'terms'->>'form_children')::boolean,false)
    OR (cmd->>'level')::integer<(parent_.mandate->>'level')::integer
    OR NOT public.l_subscriber_capability('COMMITTEE_PARENT_FORMATION',(SELECT organism_id FROM public.core_livingorganism WHERE id=p_lo),p_at)
    THEN PERFORM public.cq_refuse('CMT_PARENT'); END IF;
   scope_:=parent_.binding;
 END IF;
 SELECT v.* INTO tor FROM public.core_libraryresourceversion v JOIN public.core_libraryresource r ON r.id=v.resource_id
 WHERE r.resource_id=refs->>'resource' AND v.version_number=(refs->>'version')::integer AND r.state<>'archived'
 AND r.resource_id<>s->>'resource' AND EXISTS(SELECT FROM public.core_libraryresourcetransition t
 WHERE t.resource_id=r.id AND t.version_id=v.id AND t.to_state='published' AND t.occurred_at<=p_at);
 raw_:=public.elect_document(tor.content);
 IF tor.id IS NULL OR refs->>'binding' IS NULL OR refs->>'binding'=s->>'proposal' OR
  jsonb_typeof(raw_->'areas') IS DISTINCT FROM 'object' OR NOT public.l_valid_areas(raw_->'areas')
  OR EXISTS(SELECT FROM public.core_organismlawbinding b WHERE b.binding_uuid=(refs->>'binding')::uuid)
  THEN PERFORM public.cq_refuse('CMT_FORM'); END IF;
 IF scope_ IS NOT NULL AND EXISTS(SELECT FROM jsonb_object_keys(raw_->'areas') k
   WHERE NOT (parent_.mandate->'terms'->'areas' ? k) OR EXISTS(SELECT FROM (VALUES('POLICY'),('PROCEDURE')) cls(name)
    WHERE public.l_tor_right(raw_->'areas',k,cls.name)='ADOPT' AND public.l_tor_right(parent_.mandate->'terms'->'areas',k,cls.name)<>'ADOPT'))
 THEN PERFORM public.cq_refuse('CMT_PARENT'); END IF;
 IF raw_ ? 'chair_board_member' AND (jsonb_typeof(raw_->'chair_board_member')<>'boolean' OR NOT public.l_subscriber_capability(
   'COMMITTEE_CHAIR_RULE',(SELECT organism_id FROM public.core_livingorganism WHERE id=p_lo),p_at)) THEN PERFORM public.cq_refuse('CMT_TIER'); END IF;
 basis:=public.cmt_floor_rule(p_lo,p_at,p_at);
 IF basis IS NULL THEN PERFORM public.cq_refuse('CMT_CHOICE'); END IF;
 chair_basis:=public.cmt_chair_rule(p_lo,raw_,p_at,p_at);
 IF (cmd->>'kind'<>'SUB_COMMITTEE' OR coalesce((basis->>'sub_floor')::boolean,false))
 AND NOT EXISTS(SELECT FROM public.cmt_trustees(p_lo,circle_.id,p_at,p_at)) THEN PERFORM public.cq_refuse('CMT_FLOOR'); END IF;
 IF (chair_basis->>'required')::boolean AND NOT EXISTS(SELECT FROM public.cmt_trustees(p_lo,circle_.id,p_at,p_at) t
     WHERE t.member_id=chair_.membership_id) THEN PERFORM public.cq_refuse('CMT_CHAIR'); END IF;
 IF cmd->>'kind'='AD_HOC' THEN
   IF (cmd->>'ends_at' IS NULL)=(cmd->>'task' IS NULL) THEN PERFORM public.cq_refuse('CMT_END'); END IF;
   IF cmd->>'ends_at' IS NOT NULL THEN
     IF cmd->>'ends_at' !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' THEN PERFORM public.cq_refuse('CMT_END'); END IF;
     until_:=(cmd->>'ends_at')::timestamptz;
     IF NOT isfinite(until_) OR until_<=p_at THEN PERFORM public.cq_refuse('CMT_END'); END IF;
   ELSIF length(btrim(cmd->>'task'))=0 OR cmd->>'completion_body' IS NULL THEN PERFORM public.cq_refuse('CMT_END'); END IF;
 ELSIF cmd ?| ARRAY['task','ends_at','completion_body'] THEN PERFORM public.cq_refuse('CMT_END'); END IF;
 SELECT b.* INTO prior FROM public.core_organismlawbinding b WHERE b.resource_id=tor.resource_id AND b.scope_circle_id=circle_.id
 ORDER BY b.in_force_from DESC LIMIT 1;
 IF prior.binding_uuid::text IS DISTINCT FROM refs->>'predecessor' OR (latest.binding IS NOT NULL
 AND latest.mandate->'terms_effect'->>'binding_uuid' IS DISTINCT FROM prior.binding_uuid::text) THEN PERFORM public.cq_refuse('CMT_STALE'); END IF;
 terms_effect.binding_uuid:=(refs->>'binding')::uuid; terms_effect.resource_id:=tor.resource_id; terms_effect.version_id:=tor.id;
 terms_effect.organism_id:=p_lo; terms_effect.scope_kind:='CIRCLE'; terms_effect.scope_circle_id:=circle_.id;
 terms_effect.law_class:='TERMS_OF_REFERENCE'; terms_effect.consequence_class:='BINDING'; terms_effect.decision_route:='CQ_GOVERNANCE_CIRCLE';
 terms_effect.owner_kind:='GOVERNANCE'; terms_effect.delegated_areas:=raw_->'areas';
 terms_effect.reserves_bylaws:=false; terms_effect.predecessor_id:=prior.binding_uuid;
 data_:=cmd||jsonb_build_object('type','FORMATION','binding',s->'proposal','circle_id',circle_.id,'chair_id',chair_.id,
    'parent_binding',parent_.binding,'delegating_parent',scope_,'chair_basis',chair_basis,'terms',raw_,
    'terms_effect',public.l_adoption_facts(terms_effect),'terms_open_prior',
    prior.binding_uuid IS NOT NULL AND public.l_visible_window(prior,p_at,p_at));
 IF cmd->>'level'='1' THEN
   evidence:=public.cmt_officers(p_lo,circle_.id,p_at);
   data_:=data_||jsonb_build_object('caution','Level 1 allows this committee to act for the Board within its terms.');
   IF (evidence->>'count')::integer<3 THEN data_:=data_||jsonb_build_object('officer_finding',evidence); END IF;
 END IF;
 RETURN jsonb_build_object('committee',data_);
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR invalid_datetime_format OR numeric_value_out_of_range
 THEN PERFORM public.cq_refuse('CMT_FORM'); RETURN NULL;
END $$;
"""

SQL += r"""
CREATE FUNCTION public.cmt_law_grant(p_lo bigint,p_board uuid,p_doc bigint,p_content text,p_cls text,p_at timestamptz,p_known timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE doc jsonb:=public.elect_document(p_content); request_ jsonb:=doc->'committee_adoption';
 m public.cmt_mandates; id_ bigint; lvl text; area_ text:=request_->>'area';
BEGIN
 IF p_cls NOT IN ('POLICY','PROCEDURE') THEN RETURN NULL; END IF;
 SELECT circle_id INTO id_ FROM public.core_discboard WHERE board_uuid=p_board AND organism_id=p_lo;
 IF id_ IS NULL OR id_=public.cq3_board(p_lo,p_at,p_known) THEN RETURN NULL; END IF;
 m:=public.cmt_current(id_,p_at,p_known);
 IF m.binding IS NULL THEN
   IF request_ IS NOT NULL OR doc ? 'committee' THEN RETURN jsonb_build_object('code','CMT_ENDED','reason','The committee mandate is unavailable. Refer the matter to the Board.'); END IF;
   RETURN NULL;
 END IF;
 IF NOT public.cmt_live(m.binding,p_at,p_known) THEN RETURN jsonb_build_object('code','CMT_ENDED','reason','This committee’s mandate has ended. Refer new work to the Board.'); END IF;
 IF doc ? 'committee' AND p_cls='POLICY' THEN
   IF doc->'committee'->>'parent' IS DISTINCT FROM (SELECT circle_uuid::text FROM public.core_circle WHERE id=id_)
    THEN RETURN jsonb_build_object('code','CMT_PARENT','reason','The forming law must name this parent.'); END IF;
   RETURN jsonb_build_object('route','CQ_CIRCLE','formation',true);
 END IF;
 IF public.cq3_board(p_lo,p_at,p_known) IS NULL OR
  NOT EXISTS(SELECT FROM public.core_livingorganism lo WHERE lo.id=p_lo AND NOT lo.platform_root
  AND public.c2_basis_in_force(lo.founding_authority_basis_id,p_at,p_known)
  AND public.s015_fold_effective_state('core_livingorganism',lo.id,p_at,p_known)='ACTIVE') THEN PERFORM public.cq_refuse('CMT_AUTH'); END IF;
 lvl:=m.mandate->>'level';
 IF lvl<>'1' THEN RETURN jsonb_build_object('code','CMT_LEVEL_'||lvl,'reason',
  CASE lvl WHEN '2' THEN 'This committee can recommend; the Board decides.' WHEN '3' THEN 'This committee can report findings. Send them to the deciding body.'
   ELSE 'This committee can investigate. Give the information to the deciding body.' END); END IF;
 IF request_->>'mandate' IS DISTINCT FROM m.binding::text OR
  public.l_tor_right(m.mandate->'terms'->'areas',area_,p_cls) IS DISTINCT FROM 'ADOPT' THEN
  RETURN jsonb_build_object('code','CMT_AREA','reason','These terms do not authorise this act in this area. Refer it to the Board.'); END IF;
 RETURN jsonb_build_object('route','CQ_CIRCLE','proposing_circle_id',id_,'terms_binding_id',
   m.mandate->'terms_effect'->'binding_uuid','area',area_);
END $$;

CREATE FUNCTION public.cmt_work_plan(p_lo bigint,p_board uuid,p_att jsonb,p_at timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE s jsonb:=p_att->'spec'; m public.cmt_mandates; op text:=s->>'act'; body_ bigint; ended_ boolean;
 lvl integer; result_ jsonb; target_ bigint; n public.core_cqact;
BEGIN
 IF NOT (s ?& ARRAY['organism','mandate','act']) OR s-(ARRAY['organism','mandate','act']||CASE op
   WHEN 'RECOMMEND' THEN ARRAY['area','version','fingerprint'] WHEN 'REPORT' THEN ARRAY['area','finding']
   WHEN 'INVESTIGATE' THEN ARRAY['area','finding'] WHEN 'NAME_ADMIN' THEN ARRAY['administrator','contest']
   WHEN 'FILL_CHAIR' THEN ARRAY['person','assignment'] WHEN 'COMPLETE_TASK' THEN ARRAY['task']
   WHEN 'BIND_WORK' THEN ARRAY['activity'] WHEN 'REASSIGN_WORK' THEN ARRAY['activity','office','predecessor']
   ELSE ARRAY[]::text[] END)<>'{}' THEN PERFORM public.cq_refuse('CQ_INPUT'); END IF;
 IF s->>'organism' IS DISTINCT FROM (SELECT organism_id::text FROM public.core_livingorganism WHERE id=p_lo)
 OR public.elect_document(p_att->>'text') IS DISTINCT FROM s THEN PERFORM public.cq_refuse('CQ_ATTACHMENT'); END IF;
 SELECT circle_id INTO body_ FROM public.core_discboard WHERE board_uuid=p_board AND organism_id=p_lo;
 SELECT * INTO m FROM public.cmt_mandates WHERE organism_id=p_lo AND binding::text=s->>'mandate';
 ended_:=NOT public.cmt_live(m.binding,p_at,p_at);
 IF m.binding IS NULL OR (ended_ AND (op IS DISTINCT FROM 'REASSIGN_WORK' OR body_ IS NULL
    OR body_ IS DISTINCT FROM public.cq3_board(p_lo,p_at,p_at))) THEN RETURN jsonb_build_object('code','CMT_ENDED','reason','This committee’s mandate has ended. Refer new work to the Board.'); END IF;
 lvl:=(m.mandate->>'level')::integer;
 IF op IN ('INVESTIGATE','REPORT','RECOMMEND') THEN
   IF body_ IS DISTINCT FROM m.circle_id OR NOT coalesce(m.mandate->'terms'->'areas' ? (s->>'area'),false)
    THEN PERFORM public.cq_refuse('CMT_AREA'); END IF;
   IF (op='REPORT' AND lvl=4) OR (op='RECOMMEND' AND lvl>2) THEN PERFORM public.cq_refuse('CMT_LEVEL_'||lvl); END IF;
   IF op='RECOMMEND' AND NOT EXISTS(SELECT FROM public.core_libraryresourceversion v WHERE v.id=(s->>'version')::bigint
    AND encode(sha256(convert_to(v.content,'UTF8')),'hex')=s->>'fingerprint') THEN PERFORM public.cq_refuse('CMT_FORM'); END IF;
   result_:=jsonb_build_object('recipient',coalesce(m.mandate->>'parent_binding','GOVERNANCE'));
 ELSIF op='COMPLETE_TASK' THEN
   IF s->>'task' IS NULL OR s->>'task' IS DISTINCT FROM m.mandate->>'task' OR body_ IS NULL OR
     (m.mandate->>'completion_body' IS DISTINCT FROM (SELECT c.circle_uuid::text FROM public.core_circle c WHERE c.id=body_)
       AND NOT(m.mandate->>'completion_body'='GOVERNANCE' AND body_=public.cq3_board(p_lo,p_at,p_at)))
    THEN PERFORM public.cq_refuse('CMT_TASK'); END IF;
   result_:=jsonb_build_object('completed',m.binding);
 ELSIF op='NAME_ADMIN' THEN
   IF body_ IS DISTINCT FROM public.cq3_board(p_lo,p_at,p_at) THEN PERFORM public.cq_refuse('CMT_AUTH'); END IF;
   SELECT * INTO n FROM public.core_cqact WHERE organism_id=p_lo AND thread_uuid=(s->>'contest')::uuid AND operation='INITIATE';
   IF n.act_uuid IS NULL OR NOT EXISTS(SELECT FROM public.elect_last_transfer(p_lo,public.elect_run_scope(n.request->'attachment'->'spec'),p_at) g
     WHERE g.transfer->>'holder_circle'=m.circle_id::text) OR NOT EXISTS(SELECT FROM public.cmt_recusals(n.thread_uuid,p_at) r)
    THEN PERFORM public.cq_refuse('CMT_ADMIN'); END IF;
   SELECT i.id INTO target_ FROM public.core_identity i WHERE i.identity_id=(s->>'administrator')::uuid
     AND public.disc_member_state(p_lo,i.id,p_at)='ACTIVE';
   IF target_ IS NULL OR EXISTS(SELECT FROM public.cmt_recusals(n.thread_uuid,p_at) r WHERE r.person=target_)
     THEN PERFORM public.cq_refuse('CMT_RECUSED'); END IF;
   result_:=jsonb_build_object('administrator',target_,'contest',n.thread_uuid);
 ELSE PERFORM public.cq_refuse('CQ_INPUT'); END IF;
 RETURN jsonb_build_object('domain','COMMITTEE','route',CASE WHEN body_=public.cq3_board(p_lo,p_at,p_at) THEN 'CQ_GOVERNANCE_CIRCLE' ELSE 'CQ_CIRCLE' END,
   'committee',s||result_);
END $$;

CREATE FUNCTION public.cmt_recusals(p_thread uuid,p_at timestamptz)
RETURNS TABLE(person bigint,nomination uuid,cause uuid) LANGUAGE sql STABLE AS $$
 SELECT i.id,n.act_uuid,coalesce(consent_.act_uuid,n.act_uuid)
 FROM public.core_cqact n JOIN public.core_identity i ON i.identity_id::text=n.request->>'person'
 LEFT JOIN LATERAL (SELECT a.act_uuid FROM public.core_cqact a WHERE a.thread_uuid=n.thread_uuid
   AND a.operation='ELECT_CONSENT' AND a.actor_id=i.id AND a.request->>'nomination'=n.act_uuid::text
   AND greatest(a.effective_at,a.recorded_at)<=p_at ORDER BY a.ordinal LIMIT 1) consent_ ON true
 WHERE n.thread_uuid=p_thread AND n.operation='ELECT_NOMINATE' AND greatest(n.effective_at,n.recorded_at)<=p_at
 AND (n.actor_id=i.id OR consent_.act_uuid IS NOT NULL)
 AND NOT EXISTS(SELECT FROM public.core_cqact w WHERE w.thread_uuid=p_thread AND w.operation='ELECT_WITHDRAW'
    AND w.request->>'nomination'=n.act_uuid::text AND greatest(w.effective_at,w.recorded_at)<=p_at)
$$;

CREATE FUNCTION public.cmt_admin(p_lo bigint,p_board uuid,p_att jsonb,p_person bigint,p_at timestamptz,p_thread uuid,p_op text)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE g public.elect_transfers; m public.cmt_mandates; normal_ jsonb; named_ bigint; source_ uuid;
 valid_ boolean; person_ uuid; power_ text; recipient_ bigint; grant_doc jsonb;
BEGIN
 normal_:=public.elect_admin_office(p_lo,p_board,p_att,p_person,p_at);
 IF p_att->>'kind' IS DISTINCT FROM 'ELECTION' OR public.elect_board_confirmation(p_att) THEN RETURN normal_; END IF;
 g:=public.elect_last_transfer(p_lo,public.elect_run_scope(p_att->'spec'),p_at);
 recipient_:=(g.transfer->>'holder_circle')::bigint;
 SELECT mm.* INTO m FROM public.cmt_mandates mm WHERE mm.circle_id=recipient_ AND mm.effective_at<=p_at AND mm.recorded_at<=p_at
 ORDER BY mm.ordinal DESC LIMIT 1;
 IF g.transfer->>'committee_binding' IS NULL THEN RETURN normal_; END IF;
 IF m.binding::text IS DISTINCT FROM g.transfer->>'committee_binding' THEN PERFORM public.cq_refuse('CMT_ENDED'); END IF;
 IF NOT public.cmt_live(m.binding,p_at,p_at) THEN PERFORM public.cq_refuse('CMT_ENDED'); END IF;
 SELECT public.elect_document(v.content) INTO grant_doc FROM public.core_organismlawbinding b
 JOIN public.core_libraryresourceversion v ON v.id=b.version_id WHERE b.binding_uuid::text=g.transfer->>'policy_binding';
 power_:=CASE WHEN p_op='INITIATE' THEN 'CALL_NOMINATIONS' WHEN p_op IN ('ELECT_NOMINATE','PREPARED') THEN 'PREPARE_SLATE' ELSE 'RUN_ELECTION' END;
 IF grant_doc ? 'election_powers' AND (NOT(grant_doc->'election_powers' ? power_) OR
    (p_op='INITIATE' AND EXISTS(SELECT FROM public.core_libraryresourceversion v WHERE v.id=(p_att->'spec'->>'version')::bigint
    AND (public.elect_document(v.content)->'elections'->(p_att->'spec'->>'provision')->'term'->>'start')::timestamptz>p_at) AND NOT(grant_doc->'election_powers' ? 'CALL_AHEAD')))
 THEN PERFORM public.cq_refuse('CMT_POWER'); END IF;
 IF NOT EXISTS(SELECT FROM public.cmt_recusals(p_thread,p_at) r
 WHERE public.elect_admin_office(p_lo,p_board,p_att,r.person,p_at) IS NOT NULL)
 THEN RETURN normal_; END IF;
 IF EXISTS(SELECT FROM public.cmt_recusals(p_thread,p_at) r WHERE r.person=p_person) THEN PERFORM public.cq_refuse('CMT_RECUSED'); END IF;
 SELECT i.id INTO named_ FROM public.core_identity i WHERE i.identity_id::text=coalesce(grant_doc->>'stand_in',m.mandate->>'stand_in',m.mandate->'terms'->>'stand_in')
 AND public.disc_member_state(p_lo,i.id,p_at)='ACTIVE'
 AND NOT EXISTS(SELECT FROM public.cmt_recusals(p_thread,p_at) r WHERE r.person=i.id);
 source_:=g.act_uuid;
 IF named_ IS NULL THEN
   SELECT (f->>'administrator')::bigint,a.act_uuid INTO named_,source_ FROM public.core_cqact a
   CROSS JOIN LATERAL(SELECT a.facts->'domain_plan'->'committee' AS f) x
   WHERE a.organism_id=p_lo AND a.operation='IMPLEMENT' AND f->>'act'='NAME_ADMIN' AND f->>'contest'=p_thread::text
   AND f->>'mandate'=m.binding::text AND greatest(a.effective_at,a.recorded_at)<=p_at ORDER BY a.ordinal DESC LIMIT 1;
 END IF;
 valid_:=named_=p_person AND public.disc_member_state(p_lo,p_person,p_at)='ACTIVE';
 IF valid_ THEN RETURN jsonb_build_object('kind','CQ_RESULT','reference',source_,'route','ELECTION_STAND_IN','transfer',g.act_uuid); END IF;
 RETURN NULL;
END $$;
"""

SQL += r"""
CREATE FUNCTION public.cmt_terms_source(p_event bigint,p_lo bigint,p_cut timestamptz,p_effect jsonb)
RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT FROM public.core_livingorganismevent e JOIN public.core_cqact s ON s.act_uuid=e.event_uuid
   JOIN public.core_cqact i ON i.thread_uuid=s.thread_uuid AND i.operation='IMPLEMENT' AND i.request->>'result'=s.act_uuid::text
   WHERE e.id=p_event AND e.living_organism_id=p_lo AND e.effective_at=p_cut AND public.cq_event_matches(e)
   AND s.operation='SETTLE' AND s.organism_id=p_lo AND s.facts->>'disposition'='APPLIED'
   AND s.facts->'domain_plan'->'committee'->'terms_effect'=p_effect
   AND s.facts->'domain_plan'=i.facts->'domain_plan' AND public.cq3_implement_source(i)=i.authority_source)
$$;

CREATE FUNCTION public.cmt_chair_at(p_binding uuid,p_cut timestamptz,p_known timestamptz)
RETURNS bigint LANGUAGE sql STABLE AS $$
 SELECT coalesce((SELECT a.id FROM public.core_cqact act
  JOIN public.core_contextualroleassignment a ON a.assignment_uuid::text=act.facts->'domain_plan'->'committee'->>'assignment'
  WHERE act.operation='IMPLEMENT' AND act.facts->'domain_plan'->'committee'->>'act'='FILL_CHAIR'
  AND act.facts->'domain_plan'->'committee'->>'mandate'=m.binding::text
  AND act.effective_at<=p_cut AND act.recorded_at<=p_known AND EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent seated WHERE seated.assignment_id=a.id AND seated.action='ASSIGN' AND seated.effective_at<=p_cut AND seated.recorded_at<=p_known) ORDER BY act.ordinal DESC LIMIT 1),m.chair_id)
 FROM public.cmt_mandates m WHERE m.binding=p_binding
$$;

-- Enumerate effective boundaries from existing evidence, including dates known before catch-up.
CREATE FUNCTION public.cmt_cuts(p_lo bigint,p_known timestamptz)
RETURNS TABLE(cut timestamptz) LANGUAGE sql STABLE AS $$
 SELECT x.effective_at FROM public.core_circleparticipationend x JOIN public.core_circleparticipation p ON p.id=x.participation_id
 JOIN public.core_circle c ON c.id=p.circle_id WHERE c.parent_organism_id=p_lo AND greatest(x.received_at,x.recorded_at)<=p_known
 UNION SELECT p.effective_at FROM public.core_circleparticipation p JOIN public.core_circle c ON c.id=p.circle_id
 WHERE c.parent_organism_id=p_lo AND greatest(p.received_at,p.recorded_at)<=p_known
 UNION SELECT e.effective_at FROM public.core_livingorganismevent e WHERE e.living_organism_id=p_lo AND e.recorded_at<=p_known
 AND (e.action='ADOPT_LAW' OR EXISTS(SELECT FROM public.core_cqact act WHERE act.act_uuid=e.event_uuid AND act.operation='IMPLEMENT'
 AND (act.facts->'domain_plan'->>'domain'='LAW' OR act.facts->'domain_plan' ? 'committee')))
 UNION SELECT e.effective_at FROM public.core_contextualroleassignmentstateevent e JOIN public.core_contextualroleassignment a ON a.id=e.assignment_id
 JOIN public.core_organismmembership om ON om.id=a.membership_id WHERE om.living_organism_id=p_lo AND e.action IN ('ASSIGN','END') AND e.recorded_at<=p_known
 UNION SELECT e.effective_at FROM public.core_circlestateevent e JOIN public.core_circle c ON c.id=e.circle_id
 WHERE c.parent_organism_id=p_lo AND e.recorded_at<=p_known AND e.resulting_state='CLOSED'
 UNION SELECT (m.mandate->>'ends_at')::timestamptz FROM public.cmt_mandates m WHERE m.organism_id=p_lo
 AND m.recorded_at<=p_known AND m.mandate->>'ends_at' IS NOT NULL
$$;

CREATE FUNCTION public.cmt_chair_end(p_assignment bigint,p_known timestamptz)
RETURNS timestamptz LANGUAGE plpgsql STABLE STRICT AS $$
BEGIN
 -- A Board-place dependency requires a true chair rule at some cut.
 -- Keep superseded and future provisions in this inexpensive presence check.
 IF NOT EXISTS (
   SELECT FROM public.core_contextualroleassignment a
   JOIN public.cmt_mandates m ON m.circle_id=a.circle_id
   WHERE a.id=p_assignment AND m.recorded_at<=p_known
   AND (m.mandate->'terms'->'chair_board_member'='true'::jsonb
     OR (public.cmt_activation_choice(m.organism_id,'infinity',p_known)->>'required')::boolean OR EXISTS (
     SELECT FROM public.core_organismlawbinding b
     JOIN public.core_libraryresourceversion v ON v.id=b.version_id
     WHERE b.organism_id=m.organism_id AND b.law_class IN ('ARTICLE','BYLAW','POLICY')
     AND public.elect_document(v.content)->'committees'->'chair_board_member'='true'::jsonb
   ))
 ) THEN RETURN NULL; END IF;
 RETURN (SELECT min(t.cut) FROM public.core_contextualroleassignment a JOIN public.cmt_mandates m ON m.circle_id=a.circle_id
 CROSS JOIN LATERAL public.cmt_cuts(m.organism_id,p_known) t
 WHERE a.id=p_assignment AND m.recorded_at<=p_known AND t.cut>=m.effective_at
 AND public.cmt_chair_at(m.binding,t.cut,p_known)=a.id AND public.cmt_live(m.binding,t.cut,p_known)
 AND (public.cmt_chair_rule(m.organism_id,m.mandate->'terms',t.cut,p_known)->>'required')::boolean
 AND NOT coalesce((m.mandate->>'continue_until_replaced')::boolean,false)
 AND NOT EXISTS(SELECT FROM public.cmt_trustees(m.organism_id,m.circle_id,t.cut,p_known) trustee
                WHERE trustee.member_id=a.membership_id));
END $$;

CREATE FUNCTION public.cmt_empty(p_circle bigint,p_at timestamptz,p_known timestamptz)
RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT FROM public.cmt_current(p_circle,p_at,p_known) m
 WHERE public.cmt_live(m.binding,p_at,p_known) AND NOT EXISTS(SELECT FROM public.core_contextualroleassignment a
 WHERE a.id=public.cmt_chair_at(m.binding,p_at,p_known) AND public.elect_office_effective(a.id,p_at,p_known)))
$$;

CREATE FUNCTION public.cmt_no_recovery() RETURNS uuid LANGUAGE plpgsql AS $$
BEGIN PERFORM public.cq_refuse('CMT_CHAIR_GAP'); RETURN NULL; END $$;

CREATE FUNCTION public.cmt_appointment(p_circle bigint,p_actor bigint,p_subject bigint,p_assignment uuid,p_cut timestamptz,p_known timestamptz)
RETURNS uuid LANGUAGE sql STABLE AS $$
 SELECT act.act_uuid FROM public.core_cqact act JOIN public.cmt_mandates m
 ON m.binding::text=act.facts->'domain_plan'->'committee'->>'mandate'
 WHERE act.operation='IMPLEMENT' AND act.facts->'domain_plan'->'committee'->>'act'='FILL_CHAIR'
 AND act.facts->'domain_plan'->'committee'->>'assignment'=p_assignment::text
 AND act.facts->'domain_plan'->'committee'->>'person_id'=p_subject::text
 AND m.circle_id=p_circle AND public.cmt_live(m.binding,p_cut,p_known)
 AND NOT EXISTS(SELECT FROM public.core_cqact later WHERE later.organism_id=m.organism_id AND later.operation='IMPLEMENT'
 AND later.effective_at<=p_cut AND later.recorded_at<=p_known AND later.ordinal>act.ordinal
 AND later.facts->'domain_plan'->'committee'->>'act'='FILL_CHAIR' AND later.facts->'domain_plan'->'committee'->>'mandate'=m.binding::text)
 AND act.effective_at<=p_cut AND act.recorded_at<=p_known AND EXISTS(SELECT FROM public.core_organismmembership om
 WHERE om.living_organism_id=m.organism_id AND om.identity_id=p_actor AND public.disc_member_state(m.organism_id,p_actor,p_cut)='ACTIVE')
 ORDER BY act.ordinal DESC LIMIT 1
$$;
"""

FILL = r"""
 ELSIF op='FILL_CHAIR' THEN
   IF NOT public.cmt_empty(m.circle_id,p_at,p_at) OR body_ IS NULL OR
     (m.mandate->>'appointing_body' IS NULL AND body_ IS DISTINCT FROM public.cq3_board(p_lo,p_at,p_at)) OR
     (m.mandate->>'appointing_body' IS NOT NULL AND m.mandate->>'appointing_body' IS DISTINCT FROM
     (SELECT c.circle_uuid::text FROM public.core_circle c WHERE c.id=body_))
     THEN PERFORM public.cq_refuse('CMT_AUTH'); END IF;
   SELECT om.identity_id INTO target_ FROM public.core_organismmembership om JOIN public.core_identity who ON who.id=om.identity_id
   WHERE om.living_organism_id=p_lo AND who.identity_id=(s->>'person')::uuid AND public.circle_place_at(m.circle_id,om.id,p_at,p_at)
   AND public.disc_member_state(p_lo,who.id,p_at)='ACTIVE'
   AND (NOT (public.cmt_chair_rule(p_lo,m.mandate->'terms',p_at,p_at)->>'required')::boolean OR EXISTS(
   SELECT FROM public.cmt_trustees(p_lo,m.circle_id,p_at,p_at) t WHERE t.member_id=om.id));
   IF target_ IS NULL OR (s->>'assignment')::uuid IS NULL OR EXISTS(SELECT FROM public.core_contextualroleassignment a
      WHERE a.assignment_uuid=(s->>'assignment')::uuid) THEN PERFORM public.cq_refuse('CMT_CHAIR'); END IF;
   result_:=jsonb_build_object('person_id',target_);
"""
SQL = SQL.replace(" ELSIF op='NAME_ADMIN' THEN", FILL + " ELSIF op='NAME_ADMIN' THEN")

MESSAGES = {
    "CMT_FORM": "This Circle needs an adopted forming Policy or Bylaw and its exact terms of reference. Put the complete mandate to the competent body.",
    "CMT_AUTH": "Put this act to the body named in the forming law.",
    "CMT_STALE": "The committee’s mandate has changed. Review the current version before putting this change forward.",
    "CMT_PARENT": "The Board can form this sub-committee by Policy. Formation by its parent needs subscriber entitlement and authority in the parent’s terms.",
    "CMT_AREA": "These terms do not authorise this act in this area. Refer it to the Board.",
    "CMT_LEVEL_2": "This committee can recommend; the Board decides.",
    "CMT_LEVEL_3": "This committee can report findings. Send them to the deciding body.",
    "CMT_LEVEL_4": "This committee can investigate. Give the information to the deciding body.",
    "CMT_FLOOR": "A current Board member must hold this committee place. Identify an eligible trustee before formation.",
    "CMT_CHAIR": "These terms require a Board member as chair. Choose a current trustee.",
    "CMT_TIER": "This organisation uses one chair rule for all committees. Use the Board’s recorded rule.",
    "CMT_CHOICE": "The Board must record its chair rule before forming this committee.",
    "CMT_CHAIR_GAP": "The committee chair's place is empty. Ask the Board to fill it under the forming law.",
    "CMT_ENDED": "This committee’s mandate has ended. Refer new work to the Board.",
    "CMT_END": "Identify the dated end or the exact task and its completion authority in the terms.",
    "CMT_TASK": "Only the authority named in the terms can record completion of the stated task. Refer it there.",
    "CMT_POWER": "The grant does not cover this electoral act or place. Refer it to the recorded holder.",
    "CMT_RECUSED": "You are a candidate in this election. Ask another authorised administrator to act.",
    "CMT_ADMIN": "The Board may name an eligible member for this contest after the administrator stands aside.",
    "CMT_WORK": "This work has returned to the chair. Ask the chair to reassign it.",
    "CMT_NO_CHAIR": "There is no effective chair to receive this work. Refer it to the Board.",
    "CMT_WORK_LAW": "No governing work-transfer clause is in force. Ask the Board to identify or adopt one.",
}
SQL += """CREATE FUNCTION public.cmt_message(p_code text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
 SELECT CASE p_code """ + " ".join("WHEN '" + key + "' THEN '" + value.replace("'", "''") + "'" for key, value in MESSAGES.items()) + " END $$;"

SCHEMA += """
-- Transaction worklist, empty after commit. Each source contributes only affected pairs.
CREATE TABLE public.cmt_refresh_batch (
 action_xid xid8 PRIMARY KEY, touches jsonb NOT NULL CHECK (jsonb_typeof(touches)='object')
);
CREATE TABLE public.core_cmtcomposition (
 mandate uuid NOT NULL REFERENCES public.core_organismlawbinding(binding_uuid),
 requirement text NOT NULL, effective_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL,
 unmet boolean NOT NULL, basis jsonb NOT NULL, PRIMARY KEY(mandate,requirement,effective_at,recorded_at)
);
CREATE TABLE public.core_cmtcustody (
 receipt uuid PRIMARY KEY, activity bigint NOT NULL REFERENCES public.core_serviceactivity,
 mandate uuid NOT NULL REFERENCES public.core_organismlawbinding(binding_uuid),
 predecessor uuid UNIQUE REFERENCES public.core_cmtcustody, holder bigint REFERENCES public.core_identity,
 office bigint REFERENCES public.core_contextualroleassignment, clause jsonb NOT NULL,
 cause jsonb NOT NULL, effective_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL,
 board_circle bigint REFERENCES public.core_circle, board_target jsonb, place bigint REFERENCES public.core_circleparticipation,
 CHECK ((board_circle IS NULL)=(board_target IS NULL)), CHECK (place IS NULL OR board_circle IS NOT NULL AND office IS NULL));
CREATE INDEX cmt_work_by_board_target ON public.core_cmtcustody(board_circle,board_target,activity)
 WHERE board_circle IS NOT NULL;
CREATE INDEX cmt_board_work_history ON public.core_cmtcustody(activity,effective_at DESC,recorded_at DESC)
 WHERE board_circle IS NOT NULL;
CREATE INDEX cmt_work_by_place ON public.core_cmtcustody(place) WHERE place IS NOT NULL;
CREATE INDEX cmt_work_by_board_office ON public.core_cmtcustody(office) WHERE board_circle IS NOT NULL AND office IS NOT NULL;
CREATE UNIQUE INDEX cmt_first_work ON public.core_cmtcustody(activity) WHERE predecessor IS NULL;
CREATE INDEX cmt_work_by_mandate ON public.core_cmtcustody(mandate,activity,effective_at)
 WHERE office IS NOT NULL;
"""
SQL += r"""
CREATE FUNCTION public.cmt_composition(p_binding uuid,p_at timestamptz,p_known timestamptz)
RETURNS TABLE(requirement text,unmet boolean,basis jsonb) LANGUAGE sql STABLE AS $$
 WITH facts AS (SELECT m.*,public.cmt_chair_rule(m.organism_id,m.mandate->'terms',p_at,p_known) AS rule_,
   public.cmt_live(m.binding,p_at,p_known) AS live_ FROM public.cmt_mandates m WHERE m.binding=p_binding
   AND m.effective_at<=p_at AND m.recorded_at<=p_known)
 SELECT d.* FROM facts m CROSS JOIN LATERAL (
   SELECT CASE WHEN m.mandate->>'kind'='SUB_COMMITTEE' AND m.rule_->>'key'='terms.chair_board_member'
       THEN public.cmt_floor_rule(m.organism_id,p_at,p_known) ELSE m.rule_ END AS basis
 ) floor_ CROSS JOIN LATERAL (VALUES
 ('MANDATE',NOT m.live_,jsonb_build_object('forming',m.act_uuid,'terms',m.mandate->'terms_effect'->'binding_uuid')),
 ('CHAIR',m.live_ AND public.cmt_empty(m.circle_id,p_at,p_known),m.rule_),
 ('TRUSTEE',m.live_ AND (m.mandate->>'kind'<>'SUB_COMMITTEE' OR coalesce((floor_.basis->>'sub_floor')::boolean,false))
 AND NOT EXISTS(SELECT FROM public.cmt_trustees(m.organism_id,m.circle_id,p_at,p_known)),floor_.basis)) d
$$;

CREATE FUNCTION public.cmt_gap_guard() RETURNS trigger LANGUAGE plpgsql AS $$
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
END $$;

CREATE FUNCTION public.cmt_board_chair(p_lo bigint,p_cut timestamptz,p_known timestamptz)
RETURNS bigint LANGUAGE sql STABLE AS $$
 SELECT min(a.id) FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
 WHERE d.living_organism_id=p_lo AND d.kind='BOARD_OFFICE' AND d.scope='LIVING_ORGANISM' AND d.code='CHAIR'
 AND public.elect_office_effective(a.id,p_cut,p_known) HAVING count(*)=1
$$;

CREATE FUNCTION public.cmt_work_clause(p_lo bigint,p_mandate uuid,p_at timestamptz,p_known timestamptz)
RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT jsonb_build_object('binding',l.binding,'version',l.version,'key','committee_work','clause',doc->'committee_work')
 FROM public.core_livingorganism o CROSS JOIN LATERAL public.l_laws_at(o.organism_id,p_at,p_known) l
 JOIN public.core_libraryresourceversion v ON v.id=l.version
 CROSS JOIN LATERAL (SELECT public.elect_document(v.content) AS doc) x
 WHERE o.id=p_lo AND l.class_name IN ('POLICY','TERMS_OF_REFERENCE')
 AND (l.binding IN (SELECT (m.mandate->'terms_effect'->>'binding_uuid')::uuid FROM public.cmt_mandates m WHERE m.binding=p_mandate)
 OR l.scope='LO:'||o.organism_id OR (l.inherited AND doc->'committee_work'->'defers_to_local'='true'))
 AND doc->'committee_work'->>'return_to'='CHAIR'
 ORDER BY l.inherited,l.class_name='POLICY',l.effective_from DESC,l.binding LIMIT 1
$$;

"""
SQL += board_work.SQL
SQL += r"""
CREATE FUNCTION public.cmt_worker(p_activity bigint,p_at timestamptz,p_known timestamptz)
RETURNS TABLE(holder bigint,office bigint,receipt uuid,clause jsonb) LANGUAGE sql STABLE AS $$
 SELECT CASE WHEN eligible.keep_office THEN w.holder
  WHEN w.office IS NOT NULL AND law.clause IS NOT NULL THEN om.identity_id END,
 CASE WHEN eligible.keep_office THEN w.office WHEN w.office IS NOT NULL AND law.clause IS NOT NULL THEN target.id END,
 w.receipt,law.clause
 FROM (SELECT x.* FROM public.core_cmtcustody x WHERE x.activity=p_activity AND x.effective_at<=p_at AND x.recorded_at<=p_known
 AND x.board_circle IS NULL ORDER BY x.effective_at DESC,x.recorded_at DESC LIMIT 1) w JOIN public.cmt_mandates m ON m.binding=w.mandate
 LEFT JOIN public.core_cqact decision ON decision.act_uuid=w.receipt AND decision.operation='IMPLEMENT'
 CROSS JOIN LATERAL (SELECT public.elect_office_effective(w.office,p_at,p_known)
     AND (decision.facts#>>'{domain_plan,committee,board_recovery}'='true' OR public.cmt_live(m.binding,p_at,p_known)) AS keep_office) eligible
 CROSS JOIN LATERAL (SELECT public.cmt_work_clause(m.organism_id,m.binding,p_at,p_known) AS clause) law
 LEFT JOIN public.core_contextualroleassignment target ON target.id=CASE
 WHEN NOT public.cmt_live(m.binding,p_at,p_known) OR w.office=public.cmt_chair_at(m.binding,p_at,p_known)
 THEN public.cmt_board_chair(m.organism_id,p_at,p_known)
 ELSE public.cmt_chair_at(m.binding,p_at,p_known) END AND public.elect_office_effective(target.id,p_at,p_known)
 LEFT JOIN public.core_organismmembership om ON om.id=target.membership_id
 UNION ALL
 SELECT CASE WHEN dest.ending IS NOT NULL THEN
            CASE WHEN public.elect_office_effective(dest.office,p_at,p_known) THEN dest.holder END
          WHEN w.office IS NOT NULL AND public.elect_office_effective(w.office,p_at,p_known) THEN w.holder
          WHEN w.place IS NOT NULL AND EXISTS (SELECT FROM public.core_circleparticipation p WHERE p.id=w.place
            AND public.s015_fold_effective_state('core_organismmembership',p.membership_id,p_at,p_known)='ACTIVE')
          THEN w.holder END,
   CASE WHEN dest.ending IS NULL THEN w.office ELSE dest.office END,w.receipt,w.clause
 FROM (SELECT x.* FROM public.core_cmtcustody x WHERE x.activity=p_activity AND x.board_circle IS NOT NULL
   AND x.effective_at<=p_at AND x.recorded_at<=p_known ORDER BY x.effective_at DESC,x.recorded_at DESC LIMIT 1) w
 LEFT JOIN LATERAL public.cmt_board_return(w.receipt,p_at,p_known) dest ON true
$$;

CREATE FUNCTION public.cmt_assignment_deadline(p_assignment bigint,p_known timestamptz)
RETURNS timestamptz LANGUAGE sql STABLE STRICT AS $$
 SELECT min(public.elect_office_deadline(e.id,p_known))
 FROM public.core_contextualroleassignmentstateevent e
 WHERE e.assignment_id=p_assignment AND e.action='ASSIGN' AND greatest(e.received_at,e.recorded_at)<=p_known
$$;

CREATE FUNCTION public.cmt_custody_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prior_ public.core_cmtcustody; i public.core_cqact; result_ jsonb; w record;
BEGIN
 PERFORM 1 FROM public.core_serviceactivity WHERE id=NEW.activity FOR UPDATE;
 NEW.recorded_at:=statement_timestamp();
 SELECT * INTO prior_ FROM public.core_cmtcustody WHERE activity=NEW.activity ORDER BY effective_at DESC,recorded_at DESC LIMIT 1;
 IF prior_.receipt IS DISTINCT FROM NEW.predecessor OR NEW.effective_at>NEW.recorded_at THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
 IF prior_.receipt IS NOT NULL AND (NEW.board_circle IS DISTINCT FROM prior_.board_circle
 OR NEW.board_target IS DISTINCT FROM prior_.board_target) THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
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
END $$;

-- One insert per transaction schedules the drain. Subsequent source rows merge
-- their exact mandate/cut pairs. Rollback (including a savepoint) rolls them back.
CREATE FUNCTION public.cmt_batch_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF pg_trigger_depth()<2 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 IF NEW.action_xid<>pg_current_xact_id() THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION public.cmt_changed() RETURNS trigger LANGUAGE plpgsql AS $$
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
END $$;

-- Only dates already established for this mandate are relevant. A date alone
-- never writes. On a later touch preserve the actual end, including the end
-- of an ancestor whose delegated power the child uses.
CREATE FUNCTION public.cmt_observed_end(p_binding uuid,p_at timestamptz,p_known timestamptz)
RETURNS timestamptz LANGUAGE sql STABLE AS $$
 WITH RECURSIVE chain AS (
   SELECT m.binding,m.mandate FROM public.cmt_mandates m WHERE m.binding=p_binding
   UNION SELECT m.binding,m.mandate FROM chain c JOIN public.cmt_mandates m
      ON m.binding::text=c.mandate->>'delegating_parent'
 )
 SELECT min((mandate->>'ends_at')::timestamptz) FROM chain
 WHERE (mandate->>'ends_at')::timestamptz<=p_at
$$;

CREATE FUNCTION public.cmt_custody_due(p_office bigint,p_binding uuid,p_since timestamptz,p_cut timestamptz,p_known timestamptz)
RETURNS timestamptz LANGUAGE sql STABLE AS $$
 SELECT least(public.cmt_assignment_deadline(p_office,p_known),CASE WHEN ending>=p_since THEN ending END)
 FROM (SELECT public.cmt_observed_end(p_binding,p_cut,p_known) AS ending) m
$$;

CREATE FUNCTION public.cmt_pending_cut(p_binding uuid,p_cut timestamptz,p_known timestamptz)
RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT p_cut<=p_known AND EXISTS(SELECT FROM public.cmt_refresh_batch b
 WHERE b.action_xid=pg_current_xact_id() AND b.touches ? jsonb_build_array(p_binding,p_cut)::text)
$$;

-- A newly observed gap may have a dated cause on file. Read only the cause of
-- this requirement. Do not run the refresh again at that date.
CREATE FUNCTION public.cmt_gap_date(p_binding uuid,p_requirement text,p_cut timestamptz,p_known timestamptz)
RETURNS timestamptz LANGUAGE plpgsql STABLE AS $$
DECLARE m public.cmt_mandates; deadline timestamptz;
BEGIN
 SELECT * INTO STRICT m FROM public.cmt_mandates WHERE binding=p_binding;
 CASE p_requirement
 WHEN 'MANDATE' THEN deadline:=public.cmt_observed_end(p_binding,p_cut,p_known);
 WHEN 'CHAIR' THEN
   deadline:=public.cmt_assignment_deadline(public.cmt_chair_at(p_binding,p_cut,p_known),p_known);
 WHEN 'TRUSTEE' THEN
   -- When the floor is lost, the last overlapping Board/committee place ends it.
   SELECT max(span.ending) INTO deadline FROM public.core_circleparticipation p
    JOIN public.core_circleparticipation b ON b.membership_id=p.membership_id
    AND b.circle_id=public.cq3_board(m.organism_id,p_cut,p_known)
    CROSS JOIN LATERAL(SELECT least(public.elect_place_end(p.id,p_known),public.elect_place_end(b.id,p_known)) AS ending) span
    WHERE p.circle_id=m.circle_id AND greatest(p.recorded_at,b.recorded_at,p.received_at,b.received_at)<=p_known
    AND greatest(p.effective_at,b.effective_at,m.effective_at)<span.ending AND span.ending<=p_cut;
 ELSE RETURN p_cut;
 END CASE;
 IF deadline>=m.effective_at AND deadline<p_cut AND NOT EXISTS (
    SELECT FROM public.core_cmtcomposition g WHERE g.mandate=p_binding AND g.requirement=p_requirement
       AND g.effective_at BETWEEN deadline AND p_cut) THEN RETURN deadline; END IF;
 RETURN p_cut;
END $$;

CREATE FUNCTION public.cmt_refresh_one(p_binding uuid,p_cut timestamptz,p_known timestamptz)
RETURNS void LANGUAGE plpgsql VOLATILE AS $$
DECLARE r record; prev boolean; w record; target record; due_target record; due timestamptz; effective timestamptz;
BEGIN
 FOR r IN SELECT * FROM public.cmt_composition(p_binding,p_cut,p_known) LOOP
   SELECT g.unmet INTO prev FROM public.core_cmtcomposition g
     WHERE g.mandate=p_binding AND g.requirement=r.requirement AND g.effective_at<=p_cut
     ORDER BY g.effective_at DESC,g.recorded_at DESC LIMIT 1;
   IF coalesce(prev,false)<>r.unmet THEN
     effective:=p_cut;
     IF r.unmet THEN
       due:=public.cmt_gap_date(p_binding,r.requirement,p_cut,p_known);
       IF due<p_cut AND EXISTS(SELECT FROM public.cmt_composition(p_binding,due,p_known) state
          WHERE state.requirement=r.requirement AND state.unmet AND state.basis IS NOT DISTINCT FROM r.basis)
       THEN effective:=due; END IF;
     END IF;
     INSERT INTO public.core_cmtcomposition VALUES(p_binding,r.requirement,effective,p_known,r.unmet,coalesce(r.basis,'{}'));
   END IF;
 END LOOP;
 -- Follow the actual head only. A late fact cannot fork a later custody receipt.
 FOR w IN SELECT c.* FROM public.core_cmtcustody c JOIN public.core_serviceactivity a ON a.id=c.activity
   WHERE c.board_circle IS NULL AND c.mandate=p_binding AND c.effective_at<=p_cut AND c.office IS NOT NULL
     AND a.state IN ('assigned','in_progress') AND NOT EXISTS(
       SELECT FROM public.core_cmtcustody child WHERE child.predecessor=c.receipt)
   ORDER BY c.activity LOOP
   SELECT * INTO target FROM public.cmt_worker(w.activity,p_cut,p_known);
   IF target.office IS DISTINCT FROM w.office THEN
     effective:=p_cut;
     due:=public.cmt_custody_due(w.office,p_binding,w.effective_at,p_cut,p_known);
     IF due>=w.effective_at AND due<p_cut THEN
       SELECT * INTO due_target FROM public.cmt_worker(w.activity,due,p_known);
       IF due_target.office IS NOT DISTINCT FROM target.office AND due_target.holder IS NOT DISTINCT FROM target.holder
         AND due_target.clause IS NOT DISTINCT FROM target.clause THEN effective:=due; END IF;
     END IF;
     INSERT INTO public.core_cmtcustody VALUES(gen_random_uuid(),w.activity,p_binding,w.receipt,target.holder,
       target.office,coalesce(target.clause,'null'::jsonb),jsonb_build_object('ending_office',w.office),effective,p_known);
   END IF;
 END LOOP;
END $$;

CREATE FUNCTION public.cmt_drain() RETURNS trigger LANGUAGE plpgsql AS $$
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
END $$;

CREATE FUNCTION public.cmt_work_effect() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE f jsonb:=NEW.facts->'domain_plan'->'committee';
BEGIN
 INSERT INTO public.core_cmtcustody VALUES(NEW.act_uuid,(f->>'activity_id')::bigint,(f->>'mandate')::uuid,
  (f->>'predecessor')::uuid,(f->>'holder')::bigint,(f->>'office_id')::bigint,f->'clause',
  jsonb_build_object('implementation',NEW.act_uuid),NEW.effective_at,NEW.recorded_at,
  (f->>'board_id')::bigint,f->'target',(f->>'place_id')::bigint);
 RETURN NULL;
END $$;
"""
WORK = r"""
 ELSIF op IN ('BIND_WORK','REASSIGN_WORK') THEN
   SELECT id INTO target_ FROM public.core_serviceactivity WHERE activity_id=(s->>'activity')::uuid
   AND initiating_domain='service' AND initiating_domain_reference='committee:'||m.mandate->>'circle'
   AND state IN ('assigned','in_progress') FOR UPDATE;
   IF target_ IS NULL THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
   SELECT * INTO custody_ FROM public.cmt_worker(target_,p_at,p_at);
   clause_:=public.cmt_work_clause(p_lo,m.binding,p_at,p_at);
   IF clause_ IS NULL THEN PERFORM public.cq_refuse('CMT_WORK_LAW'); END IF;
   IF op='BIND_WORK' THEN
     IF body_ IS DISTINCT FROM m.circle_id OR custody_.receipt IS NOT NULL THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
     seat_:=public.cmt_chair_at(m.binding,p_at,p_at);
   ELSE
     IF custody_.receipt IS NULL OR custody_.receipt::text IS DISTINCT FROM s->>'predecessor' THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
     IF body_ IS DISTINCT FROM m.circle_id AND body_ IS DISTINCT FROM public.cq3_board(p_lo,p_at,p_at) THEN PERFORM public.cq_refuse('CMT_AUTH'); END IF;
     IF ended_ AND (NOT EXISTS (SELECT FROM public.core_cmtcustody h WHERE h.receipt=custody_.receipt AND h.mandate=m.binding
       AND NOT EXISTS (SELECT FROM public.core_cmtcustody next_ WHERE next_.predecessor=h.receipt))
       OR (custody_.holder IS NOT NULL AND custody_.office IS DISTINCT FROM public.cmt_board_chair(p_lo,p_at,p_at)))
       THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
     IF NOT ended_ AND public.cmt_empty(m.circle_id,p_at,p_at) AND (body_=m.circle_id OR public.cmt_board_chair(p_lo,p_at,p_at) IS NULL AND body_<>public.cq3_board(p_lo,p_at,p_at))
       THEN PERFORM public.cq_refuse('CMT_NO_CHAIR'); END IF;
     SELECT a.id INTO seat_ FROM public.core_contextualroleassignment a JOIN public.core_organismmembership member ON member.id=a.membership_id
      WHERE a.assignment_uuid=(s->>'office')::uuid AND member.living_organism_id=p_lo
      AND (ended_ OR public.circle_place_at(m.circle_id,member.id,p_at,p_at));
   END IF;
   SELECT member.identity_id INTO worker_ FROM public.core_contextualroleassignment a JOIN public.core_organismmembership member ON member.id=a.membership_id
     WHERE a.id=seat_ AND public.elect_office_effective(a.id,p_at,p_at);
   IF worker_ IS NULL OR (op='BIND_WORK' AND NOT EXISTS(SELECT FROM public.core_serviceactivityassignment w
     WHERE w.activity_id=target_ AND w.assignee_id=worker_)) THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
   result_:=jsonb_build_object('activity_id',target_,'office_id',seat_,'holder',worker_,'clause',clause_,'predecessor',custody_.receipt);
   IF ended_ THEN result_:=result_||jsonb_build_object('board_recovery',true); END IF;
"""
SQL = SQL.replace(" ELSIF op='NAME_ADMIN' THEN", WORK + " ELSIF op='NAME_ADMIN' THEN").replace(
    'n public.core_cqact;\nBEGIN', 'n public.core_cqact; custody_ record; clause_ jsonb; seat_ bigint; worker_ bigint;\nBEGIN').replace(
    'public.cmt_work_plan(p_lo', 'public.cmt_work_plan(p_lo', 1).replace(
    "RETURNS jsonb LANGUAGE plpgsql STABLE AS $$\nDECLARE s jsonb:=p_att->'spec'; m public.cmt_mandates;",
    "RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $$\nDECLARE s jsonb:=p_att->'spec'; m public.cmt_mandates;").replace(
    "initiating_domain_reference='committee:'||m.mandate->>'circle'", "initiating_domain_reference='committee:'||(m.mandate->>'circle')")

CHANGES = {
    "core_organismlawbinding": "WHEN (NEW.law_class IN ('ARTICLE','BYLAW','POLICY','TERMS_OF_REFERENCE'))",
    "core_organismlawending": "",
    "core_circleparticipation": "", "core_circleparticipationend": "",
    "core_contextualroleassignmentstateevent": "WHEN (NEW.action IN ('ASSIGN','END') OR NEW.resulting_state='ENDED')",
    "core_electvacancy": "WHEN (NEW.kind='SEAT')",
    "core_circlestateevent": "WHEN (NEW.resulting_state='CLOSED')",
    "core_cqact": "WHEN (NEW.operation='IMPLEMENT' AND NEW.facts->'domain_plan' ? 'committee')",
}
TRIGGERS = "\n".join(f"""CREATE CONSTRAINT TRIGGER zz_cmt_change AFTER INSERT ON public.{table}
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW {condition} EXECUTE FUNCTION public.cmt_changed();""" for table, condition in CHANGES.items())
TRIGGERS += """
CREATE TRIGGER cmt_batch_input BEFORE INSERT OR UPDATE OR DELETE ON public.cmt_refresh_batch
 FOR EACH ROW EXECUTE FUNCTION public.cmt_batch_guard();
CREATE TRIGGER cmt_batch_no_truncate BEFORE TRUNCATE ON public.cmt_refresh_batch
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE CONSTRAINT TRIGGER cmt_flush AFTER INSERT ON public.cmt_refresh_batch
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.cmt_drain();
CREATE TRIGGER zz_cmt_work_effect AFTER INSERT ON public.core_cqact FOR EACH ROW
 WHEN (NEW.operation='IMPLEMENT' AND NEW.facts->'domain_plan'->'committee'->>'act' IN ('BIND_WORK','REASSIGN_WORK','BIND_BOARD_WORK','REASSIGN_BOARD_WORK'))
 EXECUTE FUNCTION public.cmt_work_effect();
"""
for table, guard in (("core_cmtcomposition", "cmt_gap_guard"), ("core_cmtcustody", "cmt_custody_guard")):
    TRIGGERS += f"""
CREATE TRIGGER cmt_input BEFORE INSERT ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.{guard}();
CREATE TRIGGER cmt_immutable BEFORE UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_cmt_no_truncate BEFORE TRUNCATE ON public.{table} FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();
"""
REMOVE_TRIGGERS = "\n".join(f"DROP TRIGGER zz_cmt_change ON public.{table};" for table in CHANGES)
REMOVE_TRIGGERS += "\nDROP TRIGGER zz_cmt_work_effect ON public.core_cqact;"
# Drop table triggers before their functions, then the tables after the row-type readers.
for table in ("core_cmtcomposition","core_cmtcustody"):
    for name in ("cmt_input","cmt_immutable","s015_cmt_no_truncate"):
        REMOVE_TRIGGERS += f"\nDROP TRIGGER {name} ON public.{table};"
for name in ("cmt_batch_input","cmt_batch_no_truncate","cmt_flush"):
    REMOVE_TRIGGERS += f"\nDROP TRIGGER {name} ON public.cmt_refresh_batch;"
UNSCHEMA = "DROP TABLE public.core_cmtcomposition, public.core_cmtcustody, public.cmt_refresh_batch; DROP VIEW public.cmt_mandates; DROP INDEX public.cmt_mandate_history;"
