"""C1-33. Committee authority and custody at the effective Circle deadline."""
from importlib import import_module

from django.db import migrations

records = import_module("core.migrations.0106_circle_vacancy_records")
definition = records.episode.definition


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError("0107 requires exactly one source fragment")
    return source.replace(old, new)


LIVE_OLD = """CREATE OR REPLACE FUNCTION public.cmt_live(p_binding uuid, p_at timestamp with time zone, p_known timestamp with time zone)
 RETURNS boolean
 LANGUAGE sql
 STABLE
AS $function$
 WITH RECURSIVE ancestors AS (
  SELECT m.*,ARRAY[m.binding] AS path FROM public.cmt_mandates m WHERE m.binding=p_binding
  UNION ALL SELECT p.*,a.path||p.binding FROM ancestors a JOIN public.cmt_mandates p
     ON p.binding=(a.mandate->>'delegating_parent')::uuid WHERE NOT p.binding=ANY(a.path)
 )
 SELECT count(*)>0 AND bool_and((public.cmt_current(a.circle_id,p_at,p_known)).binding IS NOT DISTINCT FROM a.binding
     AND public.s015_fold_effective_state('core_circle',a.circle_id,p_at,p_known)='ACTIVE'
     AND NOT EXISTS(SELECT FROM public.core_cqact i
     WHERE i.organism_id=a.organism_id AND i.operation='IMPLEMENT' AND i.facts#>>'{domain_plan,domain}'='BOARD_STAND_DOWN'
     AND EXISTS(SELECT FROM public.core_circlestateevent e WHERE e.action='STAND_DOWN' AND e.event_uuid::text=i.request->>'result' AND e.effective_at<=p_at AND e.recorded_at<=p_known)
     AND i.facts#>'{domain_plan,targets,mandates}' ? a.binding::text AND i.effective_at<=p_at AND i.recorded_at<=p_known)
     AND (a.mandate->>'ends_at' IS NULL OR p_at<(a.mandate->>'ends_at')::timestamptz)
     AND NOT EXISTS(SELECT FROM public.core_cqact i WHERE i.organism_id=a.organism_id AND i.operation='IMPLEMENT'
      AND i.facts->'domain_plan'->'committee'->>'completed'=a.binding::text
      AND i.recorded_at<=p_known AND i.effective_at<=p_at)) FROM ancestors a
$function$
"""

LIVE_NEW = replace_once(LIVE_OLD,
    "public.s015_fold_effective_state('core_circle',a.circle_id,p_at,p_known)='ACTIVE'",
    "public.circle_effective_state(a.circle_id,p_at,p_known)='ACTIVE'",
)
APART_SQL = replace_once(
    LIVE_NEW.replace('FUNCTION public.cmt_live(', 'FUNCTION public.cmt_live_apart('),
    "public.circle_effective_state(a.circle_id,p_at,p_known)='ACTIVE'",
    """(public.circle_effective_state(a.circle_id,p_at,p_known)='ACTIVE'
       OR (a.binding=p_binding AND EXISTS (
         SELECT FROM public.circle_episode(a.circle_id,p_at,p_known) e WHERE e.status='EXPIRED')))""",
)
APART = "public.cmt_live_apart(uuid,timestamptz,timestamptz)"

EMPTY_OLD = """CREATE OR REPLACE FUNCTION public.cmt_empty(p_circle bigint, p_at timestamp with time zone, p_known timestamp with time zone)
 RETURNS boolean
 LANGUAGE sql
 STABLE
AS $function$
 SELECT EXISTS(SELECT FROM public.cmt_current(p_circle,p_at,p_known) m
 WHERE public.cmt_live(m.binding,p_at,p_known) AND NOT EXISTS(SELECT FROM public.core_contextualroleassignment a
 WHERE a.id=public.cmt_chair_at(m.binding,p_at,p_known) AND public.elect_office_effective(a.id,p_at,p_known)))
$function$
"""

EMPTY_NEW = replace_once(
    EMPTY_OLD,
    """ WHERE public.cmt_live(m.binding,p_at,p_known) AND NOT EXISTS(SELECT FROM public.core_contextualroleassignment a
""",
    """ WHERE public.cmt_live_apart(m.binding,p_at,p_known) AND NOT EXISTS(SELECT FROM public.core_contextualroleassignment a
""",
)

APPOINTMENT_OLD = """CREATE OR REPLACE FUNCTION public.cmt_appointment(p_circle bigint, p_actor bigint, p_subject bigint, p_assignment uuid, p_cut timestamp with time zone, p_known timestamp with time zone)
 RETURNS uuid
 LANGUAGE sql
 STABLE
AS $function$
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
$function$
"""

APPOINTMENT_NEW = replace_once(
    APPOINTMENT_OLD,
    """ AND m.circle_id=p_circle AND public.cmt_live(m.binding,p_cut,p_known)
""",
    """ AND m.circle_id=p_circle AND public.cmt_live_apart(m.binding,p_cut,p_known)
""",
)

WORK_PLAN_OLD = """CREATE OR REPLACE FUNCTION public.cmt_work_plan(p_lo bigint, p_board uuid, p_att jsonb, p_at timestamp with time zone)
 RETURNS jsonb
 LANGUAGE plpgsql
AS $function$
DECLARE s jsonb:=p_att->'spec'; m public.cmt_mandates; op text:=s->>'act'; body_ bigint; ended_ boolean;
 lvl integer; result_ jsonb; target_ bigint; n public.core_cqact; custody_ record; clause_ jsonb; seat_ bigint; worker_ bigint;
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
 IF op='REASSIGN_WORK' AND m.binding IS NOT NULL AND EXISTS(
     SELECT FROM public.core_serviceactivity act
     CROSS JOIN LATERAL public.cmt_worker(act.id,p_at,p_at) worker
     JOIN public.core_cmtcustody held ON held.receipt=worker.receipt
     JOIN public.core_discboard db ON db.board_uuid=p_board AND db.organism_id=p_lo
     WHERE act.activity_id=(s->>'activity')::uuid AND held.cause ? 'stand_down'
       AND held.cause->>'organism'=p_lo::text AND held.cause->>'custodian'='LO_COORDINATORS'

       AND held.mandate=m.binding AND held.board_circle IS NULL AND (db.circle_id=m.circle_id
         OR EXISTS(SELECT FROM public.core_circle g WHERE g.id=db.circle_id AND g.kind='GOVERNANCE'
              AND g.parent_organism_id=p_lo)))
     THEN PERFORM public.cq_refuse('CMT_WORK_STAND_DOWN'); END IF;
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

 ELSIF op IN ('BIND_WORK','REASSIGN_WORK') THEN
   SELECT id INTO target_ FROM public.core_serviceactivity WHERE activity_id=(s->>'activity')::uuid
   AND initiating_domain='service' AND initiating_domain_reference='committee:'||(m.mandate->>'circle')
   AND state IN ('assigned','in_progress') FOR UPDATE;
   IF target_ IS NULL THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
   SELECT * INTO custody_ FROM public.cmt_worker(target_,p_at,p_at);
   clause_:=public.cmt_work_clause(p_lo,m.binding,p_at,p_at);
   IF clause_ IS NULL THEN PERFORM public.cq_refuse('CMT_WORK_LAW'); END IF;
   IF op='BIND_WORK' THEN
     IF body_ IS DISTINCT FROM m.circle_id OR custody_.receipt IS NOT NULL THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
     seat_:=public.cmt_chair_at(m.binding,p_at,p_at);
   ELSE
     IF EXISTS(SELECT FROM public.core_cmtcustody w WHERE w.receipt=custody_.receipt AND w.cause ? 'stand_down')
     THEN PERFORM public.cq_refuse('CMT_WORK'); END IF;
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
END $function$
"""

WORK_PLAN_NEW = replace_once(
    WORK_PLAN_OLD,
    """ IF m.binding IS NULL OR (ended_ AND (op IS DISTINCT FROM 'REASSIGN_WORK' OR body_ IS NULL
""",
    """ IF m.binding IS NULL OR (ended_
    AND NOT (op IS NOT DISTINCT FROM 'FILL_CHAIR' AND public.cmt_live_apart(m.binding,p_at,p_at))
    AND (op IS DISTINCT FROM 'REASSIGN_WORK' OR body_ IS NULL
""",
)

FORM_PLAN_OLD = """CREATE OR REPLACE FUNCTION public.cmt_form_plan(p_lo bigint, p_board uuid, p_att jsonb, p_at timestamp with time zone)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE
AS $function$
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
END $function$
"""

FORM_PLAN_NEW = replace_once(
    FORM_PLAN_OLD,
    """ IF circle_.id IS NULL OR chair_.id IS NULL OR public.s015_fold_effective_state('core_circle',circle_.id,p_at,p_at) IS DISTINCT FROM 'ACTIVE'
""",
    """ IF circle_.id IS NULL OR chair_.id IS NULL OR public.circle_effective_state(circle_.id,p_at,p_at) IS DISTINCT FROM 'ACTIVE'
""",
)

CUTS_OLD = """CREATE OR REPLACE FUNCTION public.cmt_cuts(p_lo bigint, p_known timestamp with time zone)
 RETURNS TABLE(cut timestamp with time zone)
 LANGUAGE sql
 STABLE
AS $function$
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
$function$
"""

CUTS_NEW = replace_once(
    CUTS_OLD,
    """ WHERE c.parent_organism_id=p_lo AND e.recorded_at<=p_known AND e.resulting_state='CLOSED'
""",
    """ WHERE c.parent_organism_id=p_lo AND e.recorded_at<=p_known AND (e.resulting_state='CLOSED' OR e.action IN ('BECOME_DORMANT','RESEAT'))
""",
)

OBSERVED_END_OLD = """CREATE OR REPLACE FUNCTION public.cmt_observed_end(p_binding uuid, p_at timestamp with time zone, p_known timestamp with time zone)
 RETURNS timestamp with time zone
 LANGUAGE sql
 STABLE
AS $function$
 WITH RECURSIVE chain AS (
   SELECT m.binding,m.mandate FROM public.cmt_mandates m WHERE m.binding=p_binding
   UNION SELECT m.binding,m.mandate FROM chain c JOIN public.cmt_mandates m
      ON m.binding::text=c.mandate->>'delegating_parent'
 )
 SELECT min((mandate->>'ends_at')::timestamptz) FROM chain
 WHERE (mandate->>'ends_at')::timestamptz<=p_at
$function$
"""

OBSERVED_END_STEP_1 = replace_once(
    OBSERVED_END_OLD,
    """   SELECT m.binding,m.mandate FROM public.cmt_mandates m WHERE m.binding=p_binding
   UNION SELECT m.binding,m.mandate FROM chain c JOIN public.cmt_mandates m
""",
    """   SELECT m.binding,m.mandate,m.circle_id FROM public.cmt_mandates m WHERE m.binding=p_binding
   UNION SELECT m.binding,m.mandate,m.circle_id FROM chain c JOIN public.cmt_mandates m
""",
)
OBSERVED_END_NEW = replace_once(
    OBSERVED_END_STEP_1,
    """ SELECT min((mandate->>'ends_at')::timestamptz) FROM chain
 WHERE (mandate->>'ends_at')::timestamptz<=p_at
""",
    """ SELECT min(ending) FROM (
   SELECT (mandate->>'ends_at')::timestamptz AS ending FROM chain
    WHERE (mandate->>'ends_at')::timestamptz<=p_at
   UNION ALL SELECT episode.deadline FROM chain
    CROSS JOIN LATERAL public.circle_episode(chain.circle_id,p_at,p_known) episode
    WHERE episode.status='EXPIRED'
 ) endings
""",
)

CHANGED_OLD = """CREATE OR REPLACE FUNCTION public.cmt_changed()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
END $function$
"""

CHANGED_STEP_1 = replace_once(
    CHANGED_OLD,
    """ cascade_ boolean:=false; law_ public.core_organismlawbinding; forming uuid; terms uuid;
""",
    """ reseat_ boolean:=false; cascade_ boolean:=false; law_ public.core_organismlawbinding; forming uuid; terms uuid;
""",
)
CHANGED_STEP_2 = replace_once(
    CHANGED_STEP_1,
    """   circ:=NEW.circle_id; cut:=NEW.effective_at; cascade_:=true;
""",
    """   circ:=NEW.circle_id; cut:=NEW.effective_at; cascade_:=true; reseat_:=NEW.action='RESEAT';
""",
)
CHANGED_NEW = replace_once(
    CHANGED_STEP_2,
    """ AND (m.binding=forming OR NOT EXISTS(SELECT FROM public.core_cmtcomposition g
      WHERE g.mandate=m.binding AND g.requirement='MANDATE' AND g.unmet AND g.effective_at<cut)
""",
    """ AND ((reseat_ AND m.circle_id=circ) OR m.binding=forming OR NOT coalesce((SELECT g.unmet FROM public.core_cmtcomposition g
      WHERE g.mandate=m.binding AND g.requirement='MANDATE' AND g.effective_at<cut
      ORDER BY g.effective_at DESC,g.recorded_at DESC LIMIT 1),false)
""",
)

DISC_OFFICE_OLD = """CREATE OR REPLACE FUNCTION public.disc_office(p_lo bigint, p_circle bigint, p_person bigint, p_cut timestamp with time zone)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE
AS $function$
DECLARE v_assignment uuid; v_organism uuid; v_circle uuid;
BEGIN
 SELECT organism_id INTO v_organism FROM public.core_livingorganism WHERE id=p_lo;
 IF p_circle IS NULL THEN
   v_assignment:=public.circle_lo_office(p_lo,p_person,p_cut,p_cut);
 ELSE
   SELECT a.assignment_uuid,c.circle_uuid INTO v_assignment,v_circle
   FROM public.core_circle c JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id
   CROSS JOIN LATERAL public.circle_holders(c.id,p_cut,p_cut) h
   JOIN public.core_organismmembership m ON m.identity_id=h.person AND m.living_organism_id=p_lo
   JOIN public.core_contextualroleassignment a ON a.membership_id=m.id AND a.circle_id=c.id
   JOIN public.c2_seat_facts(p_lo,p_cut,p_cut) f ON f.assignment_id=a.id
   WHERE c.id=p_circle AND c.parent_organism_id=p_lo AND h.person=p_person
     AND f.role_code='COORDINATOR' AND f.role_scope='CIRCLE' AND f.standing='QUALIFIED'
     AND public.s015_fold_effective_state('core_circle',c.id,p_cut,p_cut)='ACTIVE'
     AND public.s015_fold_effective_state('core_livingorganism',p_lo,p_cut,p_cut)='ACTIVE'
     AND public.c2_basis_in_force(lo.founding_authority_basis_id,p_cut,p_cut)
     AND (NOT lo.platform_root OR public.r_circle_root_qualified(p_lo,p_cut,p_cut))
   ORDER BY a.id LIMIT 1;
 END IF;
 IF v_assignment IS NULL OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
   WHERE i.id=p_person AND i.access_state='active' AND u.is_active) THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('kind','OFFICE','reference',v_assignment,'organism',v_organism,'circle',v_circle,
   'route',CASE WHEN p_circle IS NULL THEN 'COORDINATOR_ROUTE' ELSE 'CIRCLE_COORDINATOR' END);
END $function$
"""
DISC_OFFICE_NEW = replace_once(
    DISC_OFFICE_OLD,
    """public.s015_fold_effective_state('core_circle',c.id,p_cut,p_cut)""",
    """public.circle_effective_state(c.id,p_cut,p_cut)""",
)

CQ4_CIRCLE_PLAN_OLD = """CREATE OR REPLACE FUNCTION public.cq4_circle_plan(p_lo bigint, p_board uuid, p_actor bigint, p_att jsonb, p_cut timestamp with time zone)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE
AS $function$
DECLARE spec jsonb:=p_att->'spec'; choice text:=spec->>'reporting'; local_act public.core_cqact;
 target public.core_circle; owner_uuid uuid; old_rule jsonb; law jsonb;
BEGIN
 SELECT * INTO target FROM public.core_circle c WHERE c.parent_organism_id=p_lo AND c.circle_uuid::text=spec->>'circle';
 IF target.id IS NULL THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 IF NOT EXISTS(SELECT FROM public.core_discboard b WHERE b.board_uuid=p_board
     AND b.organism_id=p_lo AND b.circle_id=target.id) THEN
   IF NOT coalesce(public.disc_audience(p_lo,target.id,p_actor,'MEMBERS',p_cut),false) THEN
     PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
   RETURN jsonb_build_object('code','CQ_BODY','reason','This board cannot decide this act. Put it forward in the body that owns it.');
 END IF;
 BEGIN
   IF (p_att->>'text')::jsonb IS DISTINCT FROM spec THEN PERFORM public.cq_refuse('CQ_SETTINGS_WORDING'); END IF;
 EXCEPTION WHEN invalid_text_representation THEN PERFORM public.cq_refuse('CQ_SETTINGS_WORDING'); END;
 IF NOT(spec ?& ARRAY['organism','circle','predecessor','reporting']) OR spec-ARRAY['organism','circle','predecessor','reporting']<>'{}'::jsonb
    OR jsonb_typeof(spec->'predecessor') NOT IN ('string','null') OR choice IS NULL
    OR choice NOT IN ('NAMED','TOTALS_ONLY','INHERIT') THEN PERFORM public.cq_refuse('CQ_INPUT'); END IF;
 IF NOT (public.cq_settings_at(p_lo,p_cut)->>'enabled')::boolean THEN
   RETURN jsonb_build_object('code','CQ_DISABLED','reason','Voting was switched off. Review the current settings before putting this change forward again.');
 END IF;
 local_act:=public.cq4_local_version(p_lo,target.id,p_cut);
 IF spec->>'predecessor' IS DISTINCT FROM local_act.act_uuid::text THEN
   RETURN jsonb_build_object('code','CQ_SETTINGS_STALE','reason','The Circle reporting version changed. Put forward a proposal against the current version.');
 END IF;
 SELECT lo.organism_id INTO STRICT owner_uuid FROM public.core_livingorganism lo WHERE lo.id=p_lo;
 IF choice<>'INHERIT' AND NOT public.s015_entitlement_effective(owner_uuid,'CQ_TOTALS_ONLY',p_cut) THEN
   RETURN jsonb_build_object('code','CQ_SUBSCRIBER','reason','Subscriber access is required to select this CQ option. An option already chosen stays in force.');
 END IF;
 law:=public.cq4_reporting_law(p_lo,target.id,NULL,p_cut);
 IF choice<>'INHERIT' AND law IS NOT NULL AND law->>'value'<>choice THEN
   RETURN jsonb_build_object('code','CQ_REPORTING_LAW','reason','The cited constitutional provision fixes reporting for this Circle. Follow the provision or change the law first.');
 END IF;
 IF choice=coalesce(local_act.facts->'domain_plan'->>'choice','INHERIT') THEN
   RETURN jsonb_build_object('code','CQ_SETTINGS_UNCHANGED','reason','These CQ settings are already in force.');
 END IF;
 old_rule:=public.cq4_reporting(p_lo,target.id,NULL,p_cut);
 RETURN jsonb_build_object('domain','CIRCLE_REPORTING','route','CQ_CIRCLE','circle',target.circle_uuid,'choice',choice,
    'predecessor',local_act.act_uuid,'previous_choice',coalesce(local_act.facts->'domain_plan'->>'choice','INHERIT'),
    'previous_rule',old_rule,'value',coalesce(law->>'value',nullif(choice,'INHERIT'),public.cq_reporting_at(p_lo,p_cut)->>'value'));
END $function$
"""
CQ4_CIRCLE_PLAN_NEW = replace_once(
    CQ4_CIRCLE_PLAN_OLD,
    """ BEGIN
   IF (p_att->>'text')::jsonb IS DISTINCT FROM spec""",
    """ IF public.circle_effective_state(target.id,p_cut,p_cut) IS DISTINCT FROM 'ACTIVE' THEN
   RETURN jsonb_build_object('code','CQ_BODY','reason','This board cannot decide this act. Put it forward in the body that owns it.');
 END IF;
 BEGIN
   IF (p_att->>'text')::jsonb IS DISTINCT FROM spec""",
)

CQ3_GI_PLAN_OLD = """CREATE OR REPLACE FUNCTION public.cq3_gi_plan(p_lo core_livingorganism, p_board uuid, p_person bigint, p_att jsonb, p_cut timestamp with time zone)
 RETURNS jsonb
 LANGUAGE plpgsql
AS $function$
DECLARE s jsonb:=p_att->'spec'; op text:=s->>'operation'; owner_circle public.core_circle;
 board public.core_discboard; anchor public.core_guidingintention; latest public.core_giversion;
 cites jsonb:=s->'citations'; sorted_cites jsonb; route text; payload jsonb;
BEGIN
 IF NOT(s ?& ARRAY['organism','circle','gi','operation','predecessor','title','citations'])
   OR s-ARRAY['organism','circle','gi','operation','predecessor','title','citations']<>'{}'::jsonb
   OR op IS NULL OR op NOT IN ('CREATE','REVISE','RETIRE') OR (s->>'gi')::uuid IS NULL
   OR s->>'gi' IS DISTINCT FROM (s->>'gi')::uuid::text
   OR s->>'circle' IS DISTINCT FROM (s->>'circle')::uuid::text
   OR s->>'predecessor' IS DISTINCT FROM (s->>'predecessor')::uuid::text
   OR jsonb_typeof(cites) IS DISTINCT FROM 'array' THEN PERFORM public.cq_refuse('CQ_INPUT'); END IF;
 SELECT * INTO STRICT board FROM public.core_discboard WHERE board_uuid=p_board AND organism_id=p_lo.id;
 SELECT * INTO owner_circle FROM public.core_circle WHERE circle_uuid=(s->>'circle')::uuid AND parent_organism_id=p_lo.id;
 IF s->>'circle' IS NOT NULL AND owner_circle.id IS NULL THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 IF owner_circle.id IS NOT NULL AND board.circle_id IS DISTINCT FROM owner_circle.id AND
    NOT public.disc_audience(p_lo.id,owner_circle.id,p_person,'MEMBERS',p_cut)
 THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 SELECT * INTO anchor FROM public.core_guidingintention WHERE gi_uuid=(s->>'gi')::uuid;
 IF anchor.gi_uuid IS NOT NULL AND (anchor.organism_id<>p_lo.id OR anchor.circle_id IS DISTINCT FROM owner_circle.id)
 THEN RETURN jsonb_build_object('reason','This GI is unavailable.','code','DISC_UNAVAILABLE'); END IF;
 IF op<>'CREATE' AND anchor.gi_uuid IS NULL THEN RETURN jsonb_build_object('reason','This GI is unavailable.','code','DISC_UNAVAILABLE'); END IF;
 IF board.circle_id IS DISTINCT FROM owner_circle.id THEN
   RETURN jsonb_build_object('reason','This GI belongs to a different body.','code','CQ_BODY'); END IF;
 IF public.s015_fold_effective_state('core_livingorganism',p_lo.id,p_cut,p_cut) IS DISTINCT FROM 'ACTIVE'
   OR public.c2_basis_in_force(p_lo.founding_authority_basis_id,p_cut,p_cut) IS NOT TRUE
   OR (board.circle_id IS NOT NULL AND public.s015_fold_effective_state('core_circle',board.circle_id,p_cut,p_cut) IS DISTINCT FROM 'ACTIVE')
   OR (p_lo.platform_root AND board.circle_id IS NOT NULL AND public.r_circle_root_qualified(p_lo.id,p_cut,p_cut) IS NOT TRUE)
 THEN RETURN jsonb_build_object('reason','The GI owner no longer has authority to make this decision.','code','CQ_BODY'); END IF;
 SELECT * INTO latest FROM public.core_giversion v WHERE v.gi_uuid=anchor.gi_uuid ORDER BY v.version_number DESC LIMIT 1;
 IF (op='CREATE' AND (anchor.gi_uuid IS NOT NULL OR s->>'predecessor' IS NOT NULL))
  OR (op<>'CREATE' AND latest.version_uuid IS DISTINCT FROM (s->>'predecessor')::uuid)
  OR EXISTS(SELECT FROM public.core_giact a WHERE a.gi_uuid=anchor.gi_uuid AND a.operation='RETIRE')
 THEN RETURN jsonb_build_object('reason','The GI predecessor is no longer current or the GI has retired.','code','CQ_GI_VERSION'); END IF;
 IF op='RETIRE' THEN
   IF s->>'title' IS NOT NULL OR cites<>'[]'::jsonb OR latest.content IS DISTINCT FROM p_att->>'text'
   THEN PERFORM public.cq_refuse('CQ_INPUT'); END IF;
 ELSE
   IF jsonb_typeof(s->'title') IS DISTINCT FROM 'string' OR nullif(btrim(s->>'title'),'') IS NULL
      OR nullif(btrim(p_att->>'text'),'') IS NULL THEN PERFORM public.cq_refuse('CQ_INPUT'); END IF;
   PERFORM r.id FROM public.core_libraryresource r WHERE r.id IN (
     SELECT b.resource_id FROM jsonb_array_elements(cites) x JOIN public.core_organismlawbinding b
       ON b.binding_uuid=(x->>'binding')::uuid) ORDER BY r.id FOR SHARE;
   IF public.gi_laws_ok(p_lo.organism_id,board.circle_id,cites,p_cut,p_cut) IS NOT TRUE
      OR EXISTS(SELECT FROM jsonb_array_elements(cites) x WHERE jsonb_typeof(x->'version') IS DISTINCT FROM 'number'
         OR (x->>'binding') IS DISTINCT FROM (x->>'binding')::uuid::text)
   THEN RETURN jsonb_build_object('reason','A GI needs its exact applicable local law in force. Review its citations.','code','CQ_GI_LAW'); END IF;
 END IF;
 SELECT coalesce(jsonb_agg(x ORDER BY x->>'binding'),'[]'::jsonb) INTO sorted_cites FROM jsonb_array_elements(cites) x;
 route:=CASE WHEN board.circle_id IS NULL THEN 'CQ_MEMBERSHIP' ELSE 'CQ_CIRCLE' END;
 payload:=s||jsonb_build_object('content',CASE WHEN op<>'RETIRE' THEN p_att->>'text' END,
      'citations',sorted_cites,'route',route,'request',gen_random_uuid());
 RETURN jsonb_build_object('domain','GI','route',route,'payload',payload);
END $function$
"""
CQ3_GI_PLAN_NEW = replace_once(
    CQ3_GI_PLAN_OLD,
    """public.s015_fold_effective_state('core_circle',board.circle_id,p_cut,p_cut)""",
    """public.circle_effective_state(board.circle_id,p_cut,p_cut)""",
)

CQ3_GI_SOURCE_OLD = """CREATE OR REPLACE FUNCTION public.cq3_gi_source(p_lo bigint, p_circle bigint, p_person bigint, p_operation text, p_payload jsonb, p_cut timestamp with time zone, p_known timestamp with time zone)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE
AS $function$
DECLARE src jsonb; route text:=p_payload->>'route'; board_circle bigint;
BEGIN
 IF route IN ('CQ_MEMBERSHIP','CQ_CIRCLE') THEN
   SELECT i.facts->'result_authority',b.circle_id INTO src,board_circle FROM public.core_cqact i
    JOIN public.core_discboard b ON b.board_uuid=i.board_uuid
    WHERE i.operation='IMPLEMENT' AND i.execution_xid=pg_current_xact_id() AND i.organism_id=p_lo
      AND i.actor_id=p_person AND i.effective_at<=p_cut AND i.recorded_at<=p_known
      AND i.facts->'domain_plan'->'payload'=p_payload AND p_payload->>'operation'=p_operation
      AND public.cq3_implement_source(i)=i.authority_source;
   IF src IS NULL OR board_circle IS DISTINCT FROM p_circle
      OR NOT EXISTS(SELECT FROM public.core_livingorganism lo WHERE lo.id=p_lo
        AND public.c2_basis_in_force(lo.founding_authority_basis_id,p_cut,p_known)
        AND public.s015_fold_effective_state('core_livingorganism',p_lo,p_cut,p_known)='ACTIVE'
        AND (p_circle IS NULL OR (public.s015_fold_effective_state('core_circle',p_circle,p_cut,p_known)='ACTIVE'
             AND (NOT lo.platform_root OR public.r_circle_root_qualified(p_lo,p_cut,p_known)))))
   THEN RETURN NULL; END IF;
   RETURN src;
 END IF;
 src:=public.gi_source(p_lo,p_circle,p_person,p_operation,route,p_cut,p_known);
 IF src IS NOT NULL AND public.cq_settings_at(p_lo,p_cut)->>'enabled'='true' THEN
   IF p_circle IS NULL THEN RAISE EXCEPTION 'GI_CQ_LO' USING ERRCODE='23514'; END IF;
   RAISE EXCEPTION 'GI_CQ_CIRCLE' USING ERRCODE='23514';
 END IF;
 RETURN src;
END $function$
"""
CQ3_GI_SOURCE_NEW = replace_once(
    CQ3_GI_SOURCE_OLD,
    """public.s015_fold_effective_state('core_circle',p_circle,p_cut,p_known)""",
    """public.circle_effective_state(p_circle,p_cut,p_known)""",
)

GI_SOURCE_OLD = """CREATE OR REPLACE FUNCTION public.gi_source(p_lo bigint, p_circle bigint, p_person bigint, p_operation text, p_route text, p_cut timestamp with time zone, p_known timestamp with time zone)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE
AS $function$
DECLARE v_lo public.core_livingorganism; v_office uuid; v_circle public.core_circle; v_source jsonb;
BEGIN
 SELECT * INTO v_lo FROM public.core_livingorganism WHERE id=p_lo;
 IF v_lo.id IS NULL OR p_operation NOT IN ('CREATE','REVISE','RETIRE')
   OR coalesce(public.s015_fold_effective_state('core_livingorganism',p_lo,p_cut,p_known) IN ('ACTIVE','FOUNDING_PENDING'),false) IS NOT TRUE
   OR public.c2_basis_in_force(v_lo.founding_authority_basis_id,p_cut,p_known) IS NOT TRUE
   OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
     WHERE i.id=p_person AND i.access_state='active' AND u.is_active) THEN RETURN NULL; END IF;
 IF p_circle IS NULL AND p_route IN ('COORDINATOR_ROUTE','GOVERNANCE_INTERIM') THEN
   IF p_route='GOVERNANCE_INTERIM' AND p_operation<>'CREATE' THEN RETURN NULL; END IF;
   v_source:=public.lib_policy_source(p_lo,p_person,p_route,p_cut,p_known);
   RETURN v_source;
 END IF;
 IF p_circle IS NULL OR p_route IS DISTINCT FROM 'CIRCLE_COORDINATOR' THEN RETURN NULL; END IF;
 SELECT * INTO v_circle FROM public.core_circle WHERE id=p_circle AND parent_organism_id=p_lo;
 IF v_circle.id IS NULL OR public.s015_fold_effective_state('core_circle',p_circle,p_cut,p_known) IS DISTINCT FROM 'ACTIVE'
   OR (v_lo.platform_root AND public.r_circle_root_qualified(p_lo,p_cut,p_known) IS NOT TRUE) THEN RETURN NULL; END IF;
 SELECT a.assignment_uuid INTO v_office FROM public.circle_holders(p_circle,p_cut,p_known) h
 JOIN public.core_organismmembership m ON m.identity_id=h.person AND m.living_organism_id=p_lo
 JOIN public.core_contextualroleassignment a ON a.membership_id=m.id AND a.circle_id=p_circle
 JOIN public.c2_seat_facts(p_lo,p_cut,p_known) f ON f.assignment_id=a.id
 WHERE h.person=p_person AND f.role_scope='CIRCLE' AND f.role_code='COORDINATOR' AND f.standing='QUALIFIED'
 ORDER BY a.id LIMIT 1;
 IF v_office IS NULL THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('kind','OFFICE','reference',v_office,'route',p_route,
   'organism',v_lo.organism_id,'circle',v_circle.circle_uuid);
END $function$
"""
GI_SOURCE_NEW = replace_once(
    GI_SOURCE_OLD,
    """public.s015_fold_effective_state('core_circle',p_circle,p_cut,p_known)""",
    """public.circle_effective_state(p_circle,p_cut,p_known)""",
)

ELECT_POLICY_TRANSFER_OLD = """CREATE OR REPLACE FUNCTION public.elect_policy_transfer(p_lo bigint, p_board uuid, p_att jsonb, p_cut timestamp with time zone)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE
AS $function$
DECLARE body_ jsonb:=public.elect_document(p_att->>'text'); grant_ jsonb:=body_->'authority_transfer';
 scope_ jsonb:=grant_->'scope'; citation jsonb:=grant_->'citation'; clause jsonb; doc jsonb; law_result uuid;
 electoral jsonb; base_ text; prior public.elect_transfers; from_ text; target_ text:=grant_->>'to';
 circle_ public.core_circle; governing bigint:=public.cq3_board(p_lo,p_cut,p_cut);
BEGIN
 IF grant_ IS NULL THEN RETURN '{}'::jsonb; END IF;
 IF governing IS NULL OR NOT EXISTS(SELECT FROM public.core_discboard b WHERE b.organism_id=p_lo
    AND b.board_uuid=p_board AND b.circle_id=governing) THEN
   RETURN jsonb_build_object('code','ELECT_GRANT_BODY','reason','Only the competent Board can adopt this transfer Policy.'); END IF;
 IF jsonb_typeof(grant_) IS DISTINCT FROM 'object' OR NOT(grant_ ?& ARRAY['scope','citation','from','to','predecessor'])
    OR grant_-ARRAY['scope','citation','from','to','predecessor']<>'{}' OR jsonb_typeof(scope_) IS DISTINCT FROM 'object'
    OR jsonb_typeof(citation) IS DISTINCT FROM 'object' OR NOT(citation ?& ARRAY['binding','version','provision'])
    OR citation-ARRAY['binding','version','provision']<>'{}' THEN
   RETURN jsonb_build_object('code','ELECT_GRANT_SCOPE','reason','Identify one exact power, its holder, recipient and constitutional provision.'); END IF;
 SELECT public.elect_document(v.content),(i.request->>'result')::uuid INTO doc,law_result
 FROM public.core_livingorganism lo CROSS JOIN LATERAL public.l_laws_at(lo.organism_id,p_cut,p_cut) l
 JOIN public.core_libraryresourceversion v ON v.id=l.version
 JOIN public.core_organismlawbinding binding_ ON binding_.binding_uuid=l.binding
 JOIN public.core_livingorganismevent e ON e.id=binding_.adoption_event_id
 LEFT JOIN public.core_cqact i ON i.act_uuid=e.event_uuid AND i.organism_id=lo.id AND i.operation='IMPLEMENT'
   AND i.authority_source->>'route'='CQ_MEMBERSHIP' AND i.facts->'domain_plan'->>'domain'='LAW'
 WHERE lo.id=p_lo AND NOT l.inherited AND l.class_name IN ('ARTICLE','BYLAW')
   AND l.binding::text=citation->>'binding' AND l.version::text=citation->>'version';
 clause:=doc->'authority_transfers'->(citation->>'provision');
 IF scope_='{"power":"VOTING_SETTINGS"}' THEN base_:='LO_MEMBERSHIP';
 ELSIF scope_->>'power'='RUN_ELECTION' AND scope_=public.elect_run_scope(scope_)
   AND scope_->'binding'=citation->'binding' AND scope_->'version'=citation->'version' THEN
   electoral:=doc->'elections'->(scope_->>'provision'); base_:=electoral->>'body';
   IF electoral->>'place' IS DISTINCT FROM scope_->>'place' OR coalesce(electoral->>'type','') NOT IN ('SEAT','OFFICE')
    OR (electoral->>'type'='SEAT' AND base_ IS DISTINCT FROM 'LO_MEMBERSHIP') THEN base_:=NULL; END IF;
 ELSE RETURN jsonb_build_object('code','ELECT_GRANT_SCOPE','reason','Identify one exact power, its holder, recipient and constitutional provision.'); END IF;
 IF base_ IS NULL OR base_ NOT IN ('LO_MEMBERSHIP','GOVERNANCE') OR jsonb_typeof(clause) IS DISTINCT FROM 'object'
   OR clause->>'grantor' IS DISTINCT FROM 'GOVERNANCE' OR clause->>'power' IS DISTINCT FROM scope_->>'power'
   OR clause->>'election' IS DISTINCT FROM scope_->>'provision' OR jsonb_typeof(clause->'recipients') IS DISTINCT FROM 'array'
   OR target_ IS NULL OR NOT ((clause->'recipients') @> jsonb_build_array(target_))
   OR (base_='LO_MEMBERSHIP' AND law_result IS NULL)
 THEN RETURN jsonb_build_object('code','ELECT_GRANT_LAW','reason','Cite a current provision authorising this transfer, including a membership decision for a reserved power.'); END IF;
 prior:=public.elect_last_transfer(p_lo,scope_,p_cut);
 from_:=coalesce(prior.transfer->>'to',base_);
 IF grant_->>'from' IS DISTINCT FROM from_ OR grant_->>'predecessor' IS DISTINCT FROM prior.act_uuid::text THEN
   RETURN jsonb_build_object('code','ELECT_GRANT_STALE','reason','The power has a different holder or predecessor. Review its latest transfer.'); END IF;
 IF target_=from_ THEN RETURN jsonb_build_object('code','ELECT_GRANT_SAME','reason','This body already holds the named power.'); END IF;
 IF target_<>base_ THEN
   SELECT * INTO circle_ FROM public.core_circle c WHERE c.parent_organism_id=p_lo AND c.circle_uuid::text=target_ AND c.kind='GENERIC'
     AND public.s015_fold_effective_state('core_circle',c.id,p_cut,p_cut) ='ACTIVE';
   IF circle_.id IS NULL THEN RETURN jsonb_build_object('code','ELECT_GRANT_RECIPIENT','reason','Choose an existing eligible Circle of this organisation or return the power to its constitutional holder.'); END IF;
 END IF;
 IF EXISTS(SELECT FROM public.cmt_mandates m WHERE m.circle_id=circle_.id AND m.organism_id=p_lo)
 AND NOT EXISTS(SELECT FROM public.cmt_current(circle_.id,p_cut,p_cut) m WHERE public.cmt_live(m.binding,p_cut,p_cut))
 THEN RETURN jsonb_build_object('code','CMT_ENDED','reason',public.cmt_message('CMT_ENDED')); END IF;
 IF body_ ? 'election_powers' AND (jsonb_typeof(body_->'election_powers') IS DISTINCT FROM 'array' OR
 EXISTS(SELECT FROM jsonb_array_elements_text(body_->'election_powers') power WHERE power NOT IN
 ('CALL_NOMINATIONS','PREPARE_SLATE','RUN_ELECTION','CALL_AHEAD'))) THEN PERFORM public.cq_refuse('CMT_POWER'); END IF;
 RETURN jsonb_build_object('transfer',grant_||jsonb_strip_nulls(jsonb_build_object('committee_binding',CASE
 WHEN circle_.circle_uuid::text=body_->'committee'->>'circle' THEN p_att->'spec'->>'proposal'
 ELSE (public.cmt_current(circle_.id,p_cut,p_cut)).binding::text END))||jsonb_build_object('baseline',base_,'holder_circle',
     CASE WHEN target_=base_ AND base_='GOVERNANCE' THEN governing ELSE circle_.id END,
     'policy_binding',p_att->'spec'->'proposal','policy_version',p_att->'spec'->'version',
     'constitutional_decision',law_result));
END $function$
"""
ELECT_POLICY_TRANSFER_NEW = replace_once(
    ELECT_POLICY_TRANSFER_OLD,
    """public.s015_fold_effective_state('core_circle',c.id,p_cut,p_cut)""",
    """public.circle_effective_state(c.id,p_cut,p_cut)""",
)

ELECT_RECOGNISER_OLD = """CREATE OR REPLACE FUNCTION public.elect_recogniser(a core_cqact, p_claim jsonb, p_rule jsonb, p_cut timestamp with time zone)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE
AS $function$
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
END $function$
"""
ELECT_RECOGNISER_NEW = replace_once(
    ELECT_RECOGNISER_OLD,
    """public.s015_fold_effective_state('core_circle',c.id,p_cut,p_cut)""",
    """public.circle_effective_state(c.id,p_cut,p_cut)""",
)

REPLACEMENTS = (
    ("public.cmt_live(uuid,timestamptz,timestamptz)", LIVE_OLD, LIVE_NEW),
    ("public.cmt_empty(bigint,timestamptz,timestamptz)", EMPTY_OLD, EMPTY_NEW),
    ("public.cmt_appointment(bigint,bigint,bigint,uuid,timestamptz,timestamptz)", APPOINTMENT_OLD, APPOINTMENT_NEW),
    ("public.cmt_work_plan(bigint,uuid,jsonb,timestamptz)", WORK_PLAN_OLD, WORK_PLAN_NEW),
    ("public.cmt_form_plan(bigint,uuid,jsonb,timestamptz)", FORM_PLAN_OLD, FORM_PLAN_NEW),
    ("public.cmt_cuts(bigint,timestamptz)", CUTS_OLD, CUTS_NEW),
    ("public.cmt_observed_end(uuid,timestamptz,timestamptz)", OBSERVED_END_OLD, OBSERVED_END_NEW),
    ("public.cmt_changed()", CHANGED_OLD, CHANGED_NEW),
    ("public.disc_office(bigint,bigint,bigint,timestamptz)", DISC_OFFICE_OLD, DISC_OFFICE_NEW),
    ("public.cq4_circle_plan(bigint,uuid,bigint,jsonb,timestamptz)", CQ4_CIRCLE_PLAN_OLD, CQ4_CIRCLE_PLAN_NEW),
    ("public.cq3_gi_plan(core_livingorganism,uuid,bigint,jsonb,timestamptz)", CQ3_GI_PLAN_OLD, CQ3_GI_PLAN_NEW),
    ("public.cq3_gi_source(bigint,bigint,bigint,text,jsonb,timestamptz,timestamptz)", CQ3_GI_SOURCE_OLD, CQ3_GI_SOURCE_NEW),
    ("public.gi_source(bigint,bigint,bigint,text,text,timestamptz,timestamptz)", GI_SOURCE_OLD, GI_SOURCE_NEW),
    ("public.elect_policy_transfer(bigint,uuid,jsonb,timestamptz)", ELECT_POLICY_TRANSFER_OLD, ELECT_POLICY_TRANSFER_NEW),
    ("public.elect_recogniser(core_cqact,jsonb,jsonb,timestamptz)", ELECT_RECOGNISER_OLD, ELECT_RECOGNISER_NEW),
)

def expect(cursor, installed):
    for signature, before, after in REPLACEMENTS:
        if definition(cursor, signature) != (after if installed else before):
            raise RuntimeError("0107 installed definition differs: " + signature)
    if definition(cursor, APART) != (APART_SQL if installed else None):
        raise RuntimeError("0107 installed definition differs: " + APART)
    if records.trigger(cursor, "zz_cmt_change") != records.REFRESH_TRIGGER_NEW:
        raise RuntimeError("0107 requires the 0106 committee refresh trigger")


def install(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        expect(cursor, False)
        cursor.execute(APART_SQL)
        for signature, before, after in REPLACEMENTS:
            cursor.execute(after)
        expect(cursor, True)


def uninstall(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        expect(cursor, True)
        for signature, before, after in reversed(REPLACEMENTS):
            cursor.execute(before)
        cursor.execute("DROP FUNCTION " + APART)
        expect(cursor, False)


class Migration(migrations.Migration):
    dependencies = (("core", "0106_circle_vacancy_records"),)
    operations = [migrations.RunPython(install, uninstall)]
