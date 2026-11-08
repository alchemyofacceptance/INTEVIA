"""DISC3. Bind a single domain effect to an immutable CQ settlement."""

import re
from importlib import import_module

from django.db import migrations

from . import _cq3_gi as gi_domain
from . import _cq3_referrals as referrals


def prior(name):
    return import_module("core.migrations." + name)


circles = prior("0061_circle_qualified_activation")
records = prior("0066_law_records")
acts = prior("0067_law_acts")
law = prior("0068_library_laws_read")
library = prior("0069_library_policy_publication")
gi = prior("0071_guiding_intentions")
ballots = prior("0075_cq_decisions")
one = ballots.clarity.replace_once


def function(sql, name):
    pattern = r"CREATE (?:OR REPLACE )?FUNCTION public\." + name + r"\(.*?\$\$;"
    source = re.search(pattern, sql, re.DOTALL).group()
    return source.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


HELPERS = r"""
CREATE FUNCTION public.cq3_board(p_lo bigint,p_cut timestamptz,p_known timestamptz)
RETURNS bigint LANGUAGE sql STABLE STRICT AS $$
 SELECT c.id FROM public.core_circle c WHERE c.parent_organism_id=p_lo AND c.kind='GOVERNANCE'
 AND public.s015_fold_effective_state('core_circle',c.id,p_cut,p_known)='ACTIVE'
$$;

CREATE FUNCTION public.cq3_birth_proof(p_circle bigint,p_person bigint,p_cut timestamptz,p_known timestamptz)
RETURNS text LANGUAGE sql STABLE STRICT AS $$
 SELECT 'birth:'||snap.snapshot_uuid||'|formation:'||opening.event_uuid||'|office:'||office.id||
   '|rule:'||current_rule.rule_uuid||'|seats:'||public.c2_target(string_agg(p.participation_uuid::text,',' ORDER BY p.participation_uuid))
 FROM public.core_circle c JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id
 JOIN public.core_circlestateevent opening ON opening.circle_id=c.id AND opening.sequence=1
 JOIN public.core_livingorganismevent birth ON birth.living_organism_id=lo.id AND birth.action='BIRTHING'
 JOIN public.core_lobirthingsnapshot snap ON snap.living_organism_id=lo.id
   AND birth.evidence_reference='birthing-snapshot:'||snap.snapshot_uuid
 JOIN public.core_governedrule r ON r.scope_organism_id=lo.id AND r.code='GOVERNANCE_CONSTITUTION'
   AND snap.body->>'governance_constitution'='rule:'||r.rule_uuid||'@'||r.version
 JOIN LATERAL (SELECT g.rule_uuid,g.body FROM public.core_governedrule g WHERE g.scope_organism_id=lo.id
   AND g.scope_kind='LIVING_ORGANISM' AND g.code='GOVERNANCE_CONSTITUTION'
   AND g.effective_from<=p_cut AND g.recorded_at<=p_known
   ORDER BY g.effective_from DESC,g.recorded_at DESC,g.version DESC LIMIT 1) current_rule ON true
 JOIN public.core_circleparticipation p ON p.circle_id=c.id
   AND EXISTS(SELECT FROM jsonb_array_elements(current_rule.body->'seats') s
       WHERE s->>'seat_id'=p.seat_id AND (s->>'effective_from')::timestamptz<=p_cut)
 JOIN public.core_circleadmissiondecision d ON d.id=p.decision_id AND d.outcome='ACCEPT'
   AND d.route IN ('CIRCLE_FOUNDING','GOVERNANCE_CONSTITUTIONAL') AND d.seat_id=p.seat_id AND d.seat_rule_uuid=p.seat_rule_uuid
 JOIN public.core_organismmembership m ON m.id=p.membership_id AND m.living_organism_id=lo.id
 CROSS JOIN LATERAL (SELECT public.circle_lo_office(lo.id,p_person,p_cut,p_known) AS id) office
 WHERE c.id=p_circle AND NOT lo.platform_root AND c.kind='GOVERNANCE' AND office.id IS NOT NULL
   AND public.c2_basis_in_force(lo.founding_authority_basis_id,p_cut,p_known)
   AND public.s015_fold_effective_state('core_livingorganism',lo.id,p_cut,p_known)='ACTIVE'
   AND public.s015_fold_effective_state('core_circle',c.id,p_cut,p_known) IN ('DORMANT','ELIGIBLE')
   AND NOT EXISTS(SELECT FROM public.core_circlestateevent h WHERE h.circle_id=c.id
       AND (h.resulting_state='ACTIVE' OR h.effective_at>p_cut))
   AND public.cq_settings_at(lo.id,p_cut)->>'enabled'='true'
   AND p.effective_at<=p_cut AND p.received_at<=p_known AND p.recorded_at<=p_known
   AND NOT EXISTS(SELECT FROM public.core_circleparticipationend x WHERE x.participation_id=p.id
      AND x.effective_at<=p_cut AND x.received_at<=p_known AND x.recorded_at<=p_known)
   AND EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
      WHERE i.id=m.identity_id AND i.access_state='active' AND u.is_active)
   AND public.circle_place_at(c.id,m.id,p_cut,p_known)
   AND public.disc_member_state(lo.id,m.identity_id,p_cut)='ACTIVE'
 GROUP BY snap.snapshot_uuid,opening.event_uuid,office.id,current_rule.rule_uuid
 HAVING count(DISTINCT m.identity_id)>=3 AND count(DISTINCT p.seat_id)>=3
$$;

CREATE FUNCTION public.cq3_policy_problem(p_lo bigint,p_doc bigint,p_route text,p_cut timestamptz,p_known timestamptz)
RETURNS text LANGUAGE sql STABLE AS $$
 SELECT CASE
 WHEN lo.platform_root THEN NULL
 WHEN EXISTS(SELECT FROM public.core_organismlawbinding b JOIN public.core_livingorganismevent e
   ON e.id=b.adoption_event_id WHERE b.organism_id=p_lo AND b.resource_id=p_doc AND b.law_class='POLICY'
   AND b.decision_route='CQ_GOVERNANCE_CIRCLE' AND e.recorded_at<=p_known AND b.in_force_from<=p_cut)
 THEN 'This Policy was adopted by Board vote. A change requires another Board vote.'
 WHEN public.cq3_board(p_lo,p_cut,p_known) IS NOT NULL AND
   (p_route IS DISTINCT FROM 'GOVERNANCE_INTERIM' OR public.cq_settings_at(p_lo,p_cut)->>'enabled'='true')
 THEN CASE WHEN public.cq_settings_at(p_lo,p_cut)->>'enabled'='true'
      THEN 'The active GOVERNANCE Circle holds Policy. Put this act to the Board.'
      ELSE 'Voting is off. This Policy needs the active GOVERNANCE Circle’s effective Coordinator.' END
 END FROM public.core_livingorganism lo WHERE lo.id=p_lo
$$;

CREATE FUNCTION public.cq3_body_route(p_lo public.core_livingorganism,p_board uuid,p_owner text,p_cls text,
 p_scope text,p_protected boolean,p_cut timestamptz,p_known timestamptz)
RETURNS text LANGUAGE sql STABLE AS $$
 SELECT CASE WHEN b.circle_id IS NULL AND (p_owner='MEMBERS' OR
    (p_cls='BYLAW' AND p_owner='GOVERNANCE' AND gov.id IS NULL)) THEN 'CQ_MEMBERSHIP'
 WHEN b.circle_id=gov.id AND p_owner IN ('GOVERNANCE','LO_COORDINATOR')
    AND p_cls IN ('POLICY','BYLAW','PROCEDURE') THEN 'CQ_GOVERNANCE_CIRCLE' END
 FROM public.core_discboard b CROSS JOIN LATERAL (SELECT public.cq3_board(p_lo.id,p_cut,p_known) AS id) gov
 WHERE b.board_uuid=p_board AND b.organism_id=p_lo.id AND NOT p_lo.platform_root AND NOT p_protected
 AND p_scope='LIVING_ORGANISM' AND public.c2_basis_in_force(p_lo.founding_authority_basis_id,p_cut,p_known)
 AND public.s015_fold_effective_state('core_livingorganism',p_lo.id,p_cut,p_known)='ACTIVE'
 AND EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.living_organism_id=p_lo.id AND e.action='BIRTHING'
     AND e.effective_at<=p_cut AND e.received_at<=p_known AND e.recorded_at<=p_known)
$$;

CREATE FUNCTION public.cq3_qualified(p_kind text) RETURNS boolean LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT p_kind = 'QUALIFIED'
$$;
"""

ASSESS_AUTH = """
 route:=public.cq3_body_route(lo,p_board,owner,cls,scope,pin IS NOT NULL,cut,known);
 IF route IS NULL OR (s->>'requested_route' IS NOT NULL AND s->>'requested_route'<>route)
   OR (route<>'CQ_GOVERNANCE_CIRCLE' AND EXISTS(SELECT FROM public.core_organismlawbinding voted
      WHERE voted.organism_id=lo.id AND voted.resource_id=doc.id AND voted.law_class='POLICY'
      AND voted.decision_route='CQ_GOVERNANCE_CIRCLE' AND voted.adoption_event_id IS DISTINCT FROM own_event))
   OR (open_prior AND public.cq3_body_route(lo,p_board,previous_owner,previous.law_class,
      scope,pin IS NOT NULL,cut,known) IS DISTINCT FROM route) THEN
   RETURN jsonb_build_object('code','CQ_BODY','reason','This board cannot decide this act. Put it forward in the body that owns it.');
 END IF;
 auth:=jsonb_build_object('route',route);
"""

DIRECT_POLICY = """
 IF NOT lo.platform_root AND route='GOVERNANCE_INTERIM' AND cls<>'POLICY'
   AND public.cq_settings_at(lo.id,cut)->>'enabled'='true' THEN
   RETURN jsonb_build_object('reason','CQ is active. Put this act to the GOVERNANCE Circle.');
 END IF;
 IF NOT lo.platform_root AND (cls='POLICY' OR previous.law_class='POLICY' OR EXISTS (
    SELECT FROM public.core_organismlawbinding voted WHERE voted.organism_id=lo.id AND voted.resource_id=doc.id
    AND voted.decision_route='CQ_GOVERNANCE_CIRCLE' AND voted.law_class='POLICY')) THEN
   policy_problem:=public.cq3_policy_problem(lo.id,doc.id,route,cut,known);
   IF policy_problem IS NOT NULL THEN RETURN jsonb_build_object('reason',policy_problem); END IF;
 END IF;
"""

# The request contains a complete LawProposal.content() or a Policy publication
# target. The specified text is supplied by the proposer; validate its scope
# before ever projecting it to a different reader.
ATTACHMENT = r"""
CREATE FUNCTION public.cq3_plan(p_lo bigint,p_board uuid,p_actor bigint,p_attachment jsonb,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $$
DECLARE spec jsonb:=p_attachment->'spec'; op text:=p_attachment->>'kind';
 doc public.core_libraryresource; v public.core_libraryresourceversion; meta public.core_libpolicyversion;
 lo public.core_livingorganism; plan jsonb; keys text[];
BEGIN
 SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE id=p_lo;
 IF jsonb_typeof(p_attachment) IS DISTINCT FROM 'object' OR NOT(p_attachment ?& ARRAY['spec','kind','text'])
    OR p_attachment-ARRAY['spec','kind','text']<>'{}'::jsonb OR jsonb_typeof(spec) IS DISTINCT FROM 'object'
    OR jsonb_typeof(p_attachment->'text') IS DISTINCT FROM 'string'
    OR op IS NULL OR op NOT IN ('LAW','PUBLISH_POLICY','RELEASE_POLICY','GI') THEN PERFORM public.cq_refuse('CQ_INPUT'); END IF;
 IF spec->>'organism' IS DISTINCT FROM lo.organism_id::text THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 IF op='GI' THEN RETURN public.cq3_gi_plan(lo,p_board,p_actor,p_attachment,p_cut); END IF;
 SELECT * INTO doc FROM public.core_libraryresource WHERE resource_id=spec->>'resource' FOR UPDATE;
 SELECT * INTO v FROM public.core_libraryresourceversion WHERE resource_id=doc.id AND version_number::text=spec->>'version';
 SELECT * INTO meta FROM public.core_libpolicyversion WHERE version_id=v.id;
 IF doc.id IS NULL OR v.id IS NULL OR (meta.version_id IS NOT NULL AND meta.organism_id<>lo.id)
    OR (op='LAW' AND NOT EXISTS(SELECT FROM public.core_libraryresourcetransition t WHERE t.resource_id=doc.id
          AND t.version_id=v.id AND t.to_state='published' AND t.occurred_at<=p_cut))
    OR (op<>'LAW' AND meta.organism_id IS DISTINCT FROM lo.id) THEN
    RETURN jsonb_build_object('reason','This exact instrument is unavailable.','code','DISC_UNAVAILABLE'); END IF;
 IF v.content IS DISTINCT FROM p_attachment->>'text' THEN
    RETURN jsonb_build_object('reason','The exact text differs. Review the proposed version.','code','CQ_ATTACHMENT'); END IF;
 IF op='LAW' THEN
   keys:=ARRAY['organism','resource','proposal','law_class','operation','intent','version','predecessor','scope','circle',
     'consequence_class','reserves_bylaws','resolution_effect','resolution_reference','requested_route','feature','affected_reference','resolution'];
   IF NOT(spec ?& keys) OR spec-keys<>'{}'::jsonb OR spec->>'operation' IS NULL OR spec->>'operation' NOT IN ('ADOPT','RETIRE')
     OR spec->>'operation' IS DISTINCT FROM spec->>'intent' OR spec->>'proposal' IS NULL OR spec->>'law_class' IS NULL
     OR spec->>'scope' IS DISTINCT FROM 'LIVING_ORGANISM' OR spec->>'consequence_class' IS NULL
     OR jsonb_typeof(spec->'reserves_bylaws') IS DISTINCT FROM 'boolean' THEN PERFORM public.cq_refuse('CQ_INPUT'); END IF;
   IF EXISTS(SELECT FROM public.core_organismlawbinding WHERE binding_uuid=(spec->>'proposal')::uuid)
      OR EXISTS(SELECT FROM public.core_cqact i JOIN public.core_cqact start ON start.thread_uuid=i.thread_uuid
         AND start.operation='INITIATE' WHERE i.operation='IMPLEMENT' AND i.facts->'domain_plan'->>'domain'='LAW'
         AND start.request->'attachment'->'spec'->>'proposal'=spec->>'proposal') THEN
      RETURN jsonb_build_object('reason','That proposal identifier already has a binding.','code','CQ_ATTACHMENT'); END IF;
   PERFORM public.l_lock_dependencies(spec,p_lo,p_actor);
   plan:=public.cq3_law_assess(spec,p_actor,p_board,p_cut,p_cut);
   RETURN plan||jsonb_build_object('domain',op);
 END IF;
 IF NOT(spec ?& ARRAY['organism','resource','version']) OR spec-ARRAY['organism','resource','version']<>'{}'::jsonb
 THEN PERFORM public.cq_refuse('CQ_INPUT'); END IF;
 IF public.cq3_body_route(lo,p_board,'LO_COORDINATOR','POLICY','LIVING_ORGANISM',false,p_cut,p_cut)
   IS DISTINCT FROM 'CQ_GOVERNANCE_CIRCLE' THEN
   RETURN jsonb_build_object('code','CQ_BODY','reason','This board cannot decide this act. Put it forward in the body that owns it.');
 END IF;
 IF doc.current_version_id<>v.id OR doc.state<>'draft'
   OR (op='RELEASE_POLICY' AND EXISTS (SELECT FROM public.core_libreleasepermission WHERE version_id=v.id))
   OR (op='PUBLISH_POLICY' AND EXISTS(SELECT FROM unnest(meta.author_ids) author WHERE NOT EXISTS(
       SELECT FROM public.core_libauthorclaim c WHERE c.version_id=v.id AND c.author_id=author)))
 THEN RETURN jsonb_build_object('reason','The draft, authorship or release evidence has changed. Review this exact version.');
 END IF;
 RETURN jsonb_build_object('domain',op,'version',v.id,'resource',doc.id,'route','CQ_GOVERNANCE_CIRCLE');
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
 PERFORM public.cq_refuse('CQ_INPUT'); RETURN NULL;
END $$;

CREATE FUNCTION public.cq3_attachment_facts(a public.core_cqact,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $$
DECLARE att jsonb; frozen text; ready public.core_cqact; plan jsonb; extra jsonb;
BEGIN
 IF a.operation='INITIATE' THEN
   IF NOT a.request ? 'attachment' THEN RETURN '{}'::jsonb; END IF;
   att:=a.request->'attachment';
   plan:=public.cq3_plan(a.organism_id,a.board_uuid,a.actor_id,att,p_cut);
   IF plan ? 'reason' THEN PERFORM public.cq_refuse(coalesce(plan->>'code','CQ_ATTACHMENT')); END IF;
   RETURN jsonb_build_object('attachment_fingerprint',public.disc_fingerprint(att));
 END IF;
 SELECT request->'attachment',facts->>'attachment_fingerprint' INTO att,frozen FROM public.core_cqact
 WHERE organism_id=a.organism_id AND board_uuid=a.board_uuid AND thread_uuid=a.thread_uuid AND operation='INITIATE';
 IF att IS NULL THEN RETURN '{}'::jsonb; END IF;
 extra:=jsonb_build_object('attachment_fingerprint',frozen);
 IF a.operation<>'SETTLE' THEN RETURN extra; END IF;
 SELECT * INTO STRICT ready FROM public.core_cqact WHERE act_uuid=(a.facts->>'ballot')::uuid;
 IF ready.facts->>'attachment_fingerprint' IS DISTINCT FROM frozen OR public.disc_fingerprint(att) IS DISTINCT FROM frozen
    OR ready.board_uuid<>a.board_uuid OR ready.thread_uuid<>a.thread_uuid
    OR ready.facts->'version' IS DISTINCT FROM a.facts->'version' THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF NOT public.cq3_qualified(a.facts->'count'->>'kind') THEN RETURN extra||'{"disposition":"NO_AUTHORITY"}'::jsonb; END IF;
 plan:=public.cq3_plan(a.organism_id,a.board_uuid,a.actor_id,att,p_cut);
 IF plan ? 'reason' THEN RETURN extra||jsonb_build_object('disposition','NO_EFFECT','reason',plan->>'reason'); END IF;
 RETURN extra||jsonb_build_object('disposition','APPLIED','domain_plan',plan,
   'result_authority',jsonb_build_object('kind','CQ_RESULT','route',plan->>'route','reference',a.act_uuid));
END $$;

CREATE FUNCTION public.cq3_release_source(p_lo bigint,p_person bigint,p_version bigint,p_source jsonb,p_cut timestamptz,p_known timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE resource_ bigint;
BEGIN
 IF p_source->>'kind'='CQ_RESULT' THEN
   RETURN (SELECT a.facts->'result_authority' FROM public.core_cqact a WHERE a.act_uuid::text=p_source->>'reference'
     AND a.operation='SETTLE' AND a.organism_id=p_lo AND a.actor_id=p_person AND a.facts->>'disposition'='APPLIED'
     AND public.cq3_qualified(a.facts->'count'->>'kind') AND a.facts->'domain_plan'->>'version'=p_version::text
     AND a.facts->'domain_plan'->>'domain' IN ('RELEASE_POLICY','PUBLISH_POLICY') AND a.effective_at<=p_cut AND a.recorded_at<=p_known);
 END IF;
 SELECT resource_id INTO resource_ FROM public.core_libraryresourceversion WHERE id=p_version;
 IF public.cq3_policy_problem(p_lo,resource_,p_source->>'route',p_cut,p_known) IS NOT NULL THEN RETURN NULL; END IF;
 RETURN public.lib_policy_source(p_lo,p_person,p_source->>'route',p_cut,p_known);
END $$;

CREATE FUNCTION public.cq3_implement_source(a public.core_cqact) RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT r.facts->'result_authority' FROM public.core_cqact r WHERE r.act_uuid::text=a.request->>'result'
   AND r.operation='SETTLE' AND r.execution_xid=pg_current_xact_id() AND r.organism_id=a.organism_id
   AND r.board_uuid=a.board_uuid AND r.thread_uuid=a.thread_uuid AND r.actor_id=a.actor_id AND r.actor_epoch=a.actor_epoch
   AND r.effective_at=a.received_at AND r.facts->>'disposition'='APPLIED' AND public.cq3_qualified(r.facts->'count'->>'kind')
$$;

CREATE UNIQUE INDEX cq_one_domain_effect ON public.core_cqact((request->>'result')) WHERE operation='IMPLEMENT';
CREATE FUNCTION public.cq3_apply() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE plan jsonb:=NEW.facts->'domain_plan'; spec jsonb; event_pk bigint; cut timestamptz:=NEW.effective_at;
 kind_ text:=plan->>'kind'; permission uuid; transition_pk bigint; source_ jsonb:=NEW.facts->'result_authority';
 v_id bigint:=(plan->>'version')::bigint; doc_id bigint:=(plan->>'resource')::bigint; ref text:='lib-policy:'||source_::text;
BEGIN
 IF NEW.operation='SETTLE' THEN
   INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
   VALUES(gen_random_uuid(),NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,'IMPLEMENT',NEW.actor_id,NEW.actor_epoch,
      cut,jsonb_build_object('result',NEW.act_uuid));
   RETURN NULL;
 END IF;
 SELECT e.id INTO STRICT event_pk FROM public.core_livingorganismevent e WHERE e.event_uuid=NEW.act_uuid
   AND e.living_organism_id=NEW.organism_id AND public.cq_event_matches(e);
 IF plan->>'domain'='LAW' THEN
   SELECT request->'attachment'->'spec' INTO STRICT spec FROM public.core_cqact
    WHERE thread_uuid=NEW.thread_uuid AND operation='INITIATE';
   __APPLY_LAW__
 ELSIF plan->>'domain'='GI' THEN
   PERFORM public.gi_execute(plan->'payload',NEW.actor_id,NEW.actor_epoch,cut);
 ELSE
   SELECT permission_uuid INTO permission FROM public.core_libreleasepermission WHERE version_id=v_id;
   IF permission IS NULL THEN
     permission:=gen_random_uuid();
     INSERT INTO public.core_libreleasepermission(permission_uuid,version_id,actor_id,actor_epoch,authority_source,received_at)
      VALUES(permission,v_id,NEW.actor_id,NEW.actor_epoch,source_,cut);
   END IF;
   IF plan->>'domain'='PUBLISH_POLICY' THEN
     UPDATE public.core_libraryresource SET state='published',updated_at=cut WHERE id=doc_id;
     INSERT INTO public.core_libraryresourcetransition(resource_id,version_id,from_state,to_state,command,actor_id,
        authority_reference,occurred_at,previous_transition_id,lineage_reference)
      VALUES(doc_id,v_id,'draft','published','publish_library_resource',NEW.actor_id,ref,cut,
        (SELECT t.id FROM public.core_libraryresourcetransition t WHERE t.resource_id=doc_id ORDER BY t.occurred_at DESC,t.id DESC LIMIT 1),
        'library-transition:'||gen_random_uuid()) RETURNING id INTO transition_pk;
     INSERT INTO public.core_libraryresourceevidencereference(resource_id,version_id,transition_id,reference,
        reference_type,supplied_by_id,authority_reference,occurred_at)
      VALUES(doc_id,v_id,transition_pk,'lib-policy-publish:'||(NEW.request->>'result'),'lifecycle_transition',NEW.actor_id,ref,cut);
     INSERT INTO public.core_libpublicationfact(fact_uuid,operation_uuid,kind,version_id,organism_id,actor_id,actor_epoch,
        authority_source,permission_uuid,transition_id,self_accepted,independent_review,received_at)
      VALUES(gen_random_uuid(),(NEW.request->>'result')::uuid,'PUBLICATION',v_id,NEW.organism_id,NEW.actor_id,NEW.actor_epoch,
        source_,permission,transition_pk,false,false,cut);
   END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER cq_bind_domain AFTER INSERT ON public.core_cqact FOR EACH ROW
 WHEN (NEW.operation='IMPLEMENT' OR (NEW.operation='SETTLE' AND NEW.facts->>'disposition'='APPLIED')) EXECUTE FUNCTION public.cq3_apply();
"""

BIRTH_GATE = """
       FOR cut,as_known IN SELECT * FROM (VALUES (effective,recorded),(executed,executed)) AS cuts(t,k) LOOP
         IF public.cq_settings_at(c.parent_organism_id,cut)->>'enabled' IS DISTINCT FROM 'true'
           THEN RETURN 'CIRCLE_G4_OPERATIVE_VOTING'; END IF;
         IF EXISTS(SELECT FROM public.core_circlestateevent h WHERE h.circle_id=c.id AND h.resulting_state='ACTIVE')
           THEN RETURN 'CIRCLE_G4_STATE'; END IF;
         IF EXISTS(SELECT FROM public.core_circlestateevent h WHERE h.circle_id=c.id AND h.effective_at>effective)
           THEN RETURN 'CIRCLE_G4_LATER_EVENT'; END IF;
         IF public.circle_lo_office(c.parent_organism_id,enterer,cut,as_known) IS NULL
           THEN RETURN 'CIRCLE_G4_AUTHORITY'; END IF;
         IF public.cq3_birth_proof(c.id,enterer,cut,as_known) IS NULL THEN RETURN 'CIRCLE_G4_GOVERNANCE_SEATS'; END IF;
         IF evidence IS DISTINCT FROM public.cq3_birth_proof(c.id,enterer,cut,as_known)
           OR decision IS DISTINCT FROM 's015d1:'||public.c2_target(c.circle_uuid||':'||request_id||':'||evidence)
           THEN RETURN 'CIRCLE_G4_AUTHORITY'; END IF;
       END LOOP;
       RETURN NULL;
"""

RESULT_SOURCE = """
 IF kind IN ('ADOPT','RETIRE') AND e.action='RECORD_CQ' AND e.living_organism_id=lo AND e.effective_at=cut
   AND EXISTS(SELECT FROM public.core_cqact a WHERE a.act_uuid=e.event_uuid AND a.organism_id=lo
     AND a.operation='IMPLEMENT' AND public.cq_event_matches(e) AND public.cq3_implement_source(a)=a.authority_source
     AND a.facts->>'disposition'='APPLIED' AND a.facts->'domain_plan'->>'domain'='LAW'
     AND a.facts->'domain_plan'->>'kind'=kind AND a.facts->'domain_plan'->'effect'=l_require_source.facts
     AND a.authority_source->>'reference'=a.request->>'result') THEN RETURN; END IF;
"""

COMMIT_DOMAIN = """
 IF v_act.operation='IMPLEMENT' THEN
   IF public.cq3_implement_source(v_act) IS DISTINCT FROM v_act.authority_source THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   PERFORM public.cq3_check_committed(v_act);
   RETURN NULL;
 ELSIF v_act.operation='SETTLE' AND v_act.facts->>'disposition'='APPLIED' AND NOT EXISTS(
    SELECT FROM public.core_cqact i WHERE i.operation='IMPLEMENT' AND i.request->>'result'=v_act.act_uuid::text) THEN
   PERFORM public.cq_refuse('CQ_COMPLETE');
 END IF;
"""
COMPLETE = r"""
CREATE FUNCTION public.cq3_check_committed(a public.core_cqact) RETURNS void LANGUAGE plpgsql STABLE AS $$
DECLARE plan jsonb:=a.facts->'domain_plan'; spec jsonb; e bigint; check_plan jsonb; stamp timestamptz:=statement_timestamp();
BEGIN
 SELECT id INTO STRICT e FROM public.core_livingorganismevent WHERE event_uuid=a.act_uuid;
 IF plan->>'domain'='LAW' THEN
   SELECT request->'attachment'->'spec' INTO STRICT spec FROM public.core_cqact
    WHERE thread_uuid=a.thread_uuid AND operation='INITIATE';
   check_plan:=public.cq3_law_assess(spec,a.actor_id,a.board_uuid,stamp,stamp,e);
   IF check_plan IS DISTINCT FROM plan-'domain' OR
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
      b.circle_id=public.cq3_board(a.organism_id,stamp,stamp)) OR
     NOT EXISTS(SELECT FROM public.core_libreleasepermission p WHERE p.version_id=(plan->>'version')::bigint
       AND (plan->>'domain'='PUBLISH_POLICY' OR p.authority_source=a.facts->'result_authority')) OR
     (plan->>'domain'='PUBLISH_POLICY' AND NOT EXISTS(SELECT FROM public.core_libpublicationfact f WHERE
       f.operation_uuid::text=a.request->>'result' AND f.kind='PUBLICATION' AND f.authority_source=a.facts->'result_authority'))
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 END IF;
END $$;
"""


def assess_definitions():
    old = function(law.FORWARD, "l_assess")
    call = "auth:=public.l_route(lo,person,cls,coalesce(previous_owner,owner),scope,pin IS NOT NULL,s->>'requested_route',elector,cut,known);"
    collective = one(
        old,
        "public.l_assess(s jsonb, person bigint,",
        "public.cq3_law_assess(s jsonb, person bigint, p_board uuid,",
    )
    collective = one(collective, call, ASSESS_AUTH)
    personal = one(
        old,
        " reference text;",
        " referral_key uuid; policy_problem text; reference text;",
    )
    personal = one(
        personal,
        call,
        call.replace(
            "coalesce(previous_owner,owner)",
            "CASE WHEN previous_owner='GOVERNANCE' AND previous.law_class='POLICY' AND "
            "s->>'requested_route' IS DISTINCT FROM 'GOVERNANCE_INTERIM' THEN 'LO_COORDINATOR' ELSE coalesce(previous_owner,owner) END",
        ),
    )
    personal = one(
        personal,
        " affected:=public.l_pending(",
        DIRECT_POLICY + " affected:=public.l_pending(",
    )
    personal = one(
        personal,
        " pin:=public.l_charter_pin(",
        """
 IF NOT s ? 'referral_result' THEN
   SELECT r.act_uuid INTO referral_key FROM public.core_cqact i
     JOIN public.core_cqact r ON r.thread_uuid=i.thread_uuid AND r.facts ? 'referral'
     WHERE i.operation='INITIATE' AND i.organism_id=lo.id AND i.request->'attachment'->>'kind'='LAW'
       AND i.request->'attachment'->'spec'->>'proposal'=s->>'proposal'
       AND r.effective_at<=cut AND r.recorded_at<=known ORDER BY r.ordinal DESC LIMIT 1;
   IF referral_key IS NOT NULL THEN
     IF NOT public.cq3_result_visible(referral_key,lo.id,person,known) THEN
       RETURN jsonb_build_object('reason','This discussion is unavailable to you.'); END IF;
     RETURN jsonb_build_object('reason','This proposal cannot be used as a separate personal act. Follow its recorded referral.');
   END IF;
 END IF;
 IF s ? 'referral_result' THEN
   policy_problem:=public.cq3_office_referral(s,lo.id,person,cut,known);
   IF policy_problem IS NOT NULL THEN RETURN jsonb_build_object('reason',policy_problem); END IF;
 END IF;
 pin:=public.l_charter_pin(""",
    )
    personal = one(
        personal,
        " RETURN auth||jsonb_build_object('kind',operation,",
        " IF s ? 'referral_result' THEN auth:=auth||jsonb_build_object('referral_result',s->'referral_result'); END IF;\n"
        " RETURN auth||jsonb_build_object('kind',operation,",
    )
    return old, personal, collective


def edits():
    yield from referrals.edits(ballots.clarity, ballots, function, one)
    yield from gi_domain.edits(gi, function, one)
    old = function(library.FORWARD, "lib_law_authority_source")
    yield (
        old,
        one(
            old,
            "facts->'electorate')",
            "facts->'electorate') || "
            "CASE WHEN facts ? 'referral_result' THEN jsonb_build_object('referral_result',facts->'referral_result') ELSE '{}'::jsonb END",
        ),
    )
    old = function(circles.SQL, "circle_activation_problem")
    yield old, one(old, "       RETURN 'CIRCLE_G4_OPERATIVE_VOTING';", BIRTH_GATE)
    old = function(records.FORWARD, "l_owner_at")
    new = one(
        old,
        "WHEN b.law_class='BYLAW' THEN CASE",
        "WHEN board.event_uuid IS NOT NULL THEN 'GOVERNANCE'\n             WHEN b.law_class='BYLAW' THEN CASE",
    )
    new = one(
        new,
        "CASE WHEN closing.event_uuid IS NULL THEN b.owner_circle_id END",
        "CASE WHEN board.event_uuid IS NOT NULL THEN board.circle_id WHEN closing.event_uuid IS NULL THEN b.owner_circle_id END",
    )
    new = one(
        new,
        "coalesce(closing.event_uuid, marker.event_uuid",
        "coalesce(closing.event_uuid, board.event_uuid, marker.event_uuid",
    )
    yield (
        old,
        one(
            new,
            " LEFT JOIN public.core_organismlawbinding article",
            """
 LEFT JOIN LATERAL (SELECT e.circle_id,e.event_uuid FROM public.core_circlestateevent e
     WHERE e.circle_id=public.cq3_board(b.organism_id,cut,known) AND e.action='ACTIVATE'
     AND e.effective_at<=cut AND e.recorded_at<=known ORDER BY e.sequence DESC LIMIT 1) board
     ON b.law_class='POLICY' AND b.scope_kind='LIVING_ORGANISM' AND b.owner_kind='LO_COORDINATOR'
        AND NOT EXISTS(SELECT FROM public.core_livingorganism root WHERE root.id=b.organism_id AND root.platform_root)
 LEFT JOIN public.core_organismlawbinding article""",
        ),
    )
    old, personal, _ = assess_definitions()
    yield old, personal
    old = function(records.FORWARD, "l_require_source")
    yield (
        old,
        one(
            old, " IF e.living_organism_id", RESULT_SOURCE + " IF e.living_organism_id"
        ),
    )
    for sig, _, old in ballots.definitions():
        if sig.startswith("public.cq_source("):
            yield (
                old,
                one(
                    old,
                    " IF p_op IN ('CONFIRM','QUESTION') AND EXISTS",
                    """
 IF p_op IN ('CONFIRM','QUESTION') AND public.cq3_carried_conflict(p_thread,p_person,p_cut)
   THEN PERFORM public.cq_refuse('CQ_CONFIRM_ELIGIBLE'); END IF;
 IF p_op IN ('CONFIRM','QUESTION') AND EXISTS""",
                ),
            )
        if sig == "public.cq_input()":
            new = one(
                old,
                "ARRAY['version','previous_thread']",
                "ARRAY['version','previous_thread','attachment','referral_result']",
            )
            new = one(
                new,
                " IF NEW.operation='RULE_OPEN' THEN\n   IF pg_trigger_depth()",
                """ IF NEW.operation='IMPLEMENT' THEN
   v_source:=public.cq3_implement_source(NEW);
   IF pg_trigger_depth()<2 OR NEW.received_at<>v_stamp OR v_source IS NULL THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 ELSIF NEW.operation='RULE_OPEN' THEN
   IF pg_trigger_depth()""",
            )
            new = one(
                new,
                "WHEN 'RULE_OPEN' THEN ARRAY",
                "WHEN 'IMPLEMENT' THEN ARRAY['result'] WHEN 'RULE_OPEN' THEN ARRAY",
            )
            new = one(
                new,
                " IF NEW.operation='SETTINGS' THEN",
                """ IF NEW.operation='IMPLEMENT' THEN
   SELECT jsonb_build_object('result_authority',v_source,'disposition','APPLIED','domain_plan',r.facts->'domain_plan',
       'attachment_fingerprint',r.facts->'attachment_fingerprint','wording_version',r.facts->'version') INTO STRICT NEW.facts
     FROM public.core_cqact r WHERE r.act_uuid::text=NEW.request->>'result';
 ELSIF NEW.operation='SETTINGS' THEN""",
            )
            yield (
                old,
                one(
                    new,
                    " SELECT coalesce(max(a.ordinal)",
                    """
 IF NEW.operation='INITIATE' AND NEW.request ? 'referral_result' THEN
   NEW.facts:=NEW.facts||public.cq3_accept_referral(NEW,v_stamp);
 END IF;
 IF NEW.operation IN ('INITIATE','PROCEED','OPEN_VOTE','REOPEN_VOTE','SETTLE') THEN
   NEW.facts:=NEW.facts||public.cq3_attachment_facts(NEW,v_stamp);
 END IF;
 IF NEW.operation IN ('SETTLE','NO_BALLOT') AND NEW.facts->'count'->>'kind'='NO_DECISION_FEWER_THAN_THREE' THEN
   NEW.facts:=NEW.facts||jsonb_build_object('referral',public.cq3_referral(NEW,v_stamp));
 END IF;
 SELECT coalesce(max(a.ordinal)""",
                ),
            )
        if sig == "public.cq_complete()":
            yield (
                old,
                one(
                    old,
                    " -- A later own conflict",
                    COMMIT_DOMAIN + " -- A later own conflict",
                ),
            )
    for name, replacements in (
        ("lib_policy_input", (("old.organism_id", "NEW.actor_id", "NEW"),)),
        ("lib_publication_input", (("NEW.organism_id", "NEW.actor_id", "NEW"),)),
        (
            "lib_policy_commit",
            (
                ("meta.organism_id", "NEW.actor_id", "NEW"),
                ("fact.organism_id", "fact.actor_id", "fact"),
            ),
        ),
    ):
        old = function(library.FORWARD, name)
        new = old
        for owner, actor, row in replacements:
            target = f"public.lib_policy_source({owner},{actor},{row}.authority_source->>'route',"
            revised = f"public.cq3_release_source({owner},{actor},{row}.version_id,{row}.authority_source,"
            if target not in new:
                raise RuntimeError("Missing scoped source in " + name)
            new = new.replace(target, revised)
        if name == "lib_publication_input":
            new = one(
                new,
                " IF NEW.received_at>stamp",
                """
 IF NEW.authority_source->>'kind'='CQ_RESULT' AND NOT EXISTS(
   SELECT FROM public.core_cqact a WHERE a.act_uuid=NEW.operation_uuid AND a.execution_xid=pg_current_xact_id()
    AND a.facts->'domain_plan'->>'domain'='PUBLISH_POLICY' AND a.facts->'result_authority'=NEW.authority_source)
 THEN RAISE EXCEPTION 'LIB: publication needs the exact Board decision.' USING ERRCODE='23514'; END IF;
 IF NEW.received_at>stamp""",
            )
        yield old, new
    old = function(library.FORWARD, "lib_laws_at")
    new = one(
        old,
        "v.content, act.authority_source",
        """v.content, coalesce(act.authority_source,
         vote.facts->'result_authority'||jsonb_build_object('board',vote.board_uuid,'thread',vote.thread_uuid,
           'result_post',vote.act_uuid,'circle',(SELECT c.circle_uuid FROM public.core_discboard b2 JOIN public.core_circle c ON c.id=b2.circle_id WHERE b2.board_uuid=vote.board_uuid),'ballot',opening.act_uuid,'wording_version',vote.facts->'version',
           'counting_rule',(opening.facts->'rules')-'provisions'-'applied_provisions',
           'attachment_fingerprint',vote.facts->'attachment_fingerprint'))""",
    )
    new = one(
        new,
        "LEFT JOIN public.core_circle owning ON owning.id=l.owner_circle",
        "LEFT JOIN public.core_circle owning ON owning.id=l.owner_circle\n WHERE act.event_id IS NOT NULL OR vote.act_uuid IS NOT NULL",
    )
    yield (
        old,
        one(
            new,
            " JOIN public.core_lawact act ON act.event_id=e.id",
            """
 LEFT JOIN public.core_lawact act ON act.event_id=e.id
 LEFT JOIN public.core_cqact effect ON effect.act_uuid=e.event_uuid AND effect.operation='IMPLEMENT'
 LEFT JOIN public.core_cqact vote ON vote.act_uuid::text=effect.request->>'result' AND vote.operation='SETTLE'
 LEFT JOIN public.core_cqact opening ON opening.act_uuid=(vote.facts->>'ballot')::uuid""",
        ),
    )


def install(apps, editor):
    editor.execute(
        ballots.operations_constraint(
            ballots.PRIOR_OPS + ballots.BALLOT_OPS + ("IMPLEMENT",)
        ),
        params=None,
    )
    editor.execute(HELPERS, params=None)
    editor.execute(assess_definitions()[2], params=None)
    writer = function(acts.FORWARD, "l_execute")
    projection = writer[
        writer.index(" IF kind_='RETIRE' OR") : writer.index(" RETURN event_pk;")
    ]
    editor.execute(one(ATTACHMENT, "__APPLY_LAW__", projection), params=None)
    editor.execute(gi_domain.SQL, params=None)
    editor.execute(referrals.SQL, params=None)
    editor.execute(COMPLETE, params=None)
    for _, new in edits():
        editor.execute(new, params=None)


def reverse(apps, editor):
    editor.execute(
        "LOCK TABLE public.core_cqact,public.core_circlestateevent IN ACCESS EXCLUSIVE MODE",
        params=None,
    )
    with editor.connection.cursor() as cursor:
        cursor.execute("""SELECT EXISTS(SELECT FROM public.core_cqact WHERE request ? 'attachment' OR facts ? 'referral' OR request ? 'referral_result')
          OR EXISTS(SELECT FROM public.core_circlestateevent e JOIN public.core_circle c ON c.id=e.circle_id
                WHERE c.kind='GOVERNANCE' AND e.action='ACTIVATE' AND e.evidence_reference LIKE 'birth:%')""")
        if cursor.fetchone()[0]:
            raise RuntimeError(
                "DISC3 has recorded CQ authority or Birthing activation. Preserve its history."
            )
    for old, _ in reversed(list(edits())):
        editor.execute(old, params=None)
    editor.execute("DROP TRIGGER cq_bind_domain ON public.core_cqact")
    editor.execute(
        "DROP INDEX public.cq_one_domain_effect, public.cq_one_referral_journey, public.cq_law_attachment_proposal"
    )
    editor.execute(
        ballots.operations_constraint(ballots.PRIOR_OPS + ballots.BALLOT_OPS),
        params=None,
    )
    for name in (
        "cq3_accept_referral",
        "cq3_office_referral",
        "cq3_referral",
        "cq3_result_visible",
        "cq3_sole_officer",
        "cq3_carried_conflict",
        "cq3_gi_source",
        "cq3_gi_plan",
        "cq3_implement_source",
        "cq3_apply",
        "cq3_check_committed",
        "cq3_attachment_facts",
        "cq3_plan",
        "cq3_release_source",
        "cq3_law_assess",
        "cq3_qualified",
        "cq3_body_route",
        "cq3_policy_problem",
        "cq3_birth_proof",
        "cq3_board",
    ):
        editor.execute("DROP FUNCTION public." + name)


class Migration(migrations.Migration):
    dependencies = (("core", "0075_cq_decisions"),)
    operations = (migrations.RunPython(install, reverse),)
