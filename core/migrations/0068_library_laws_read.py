"""LIB P1. Exact-version law projection and authorised GRP-13 refusal wording."""

from django.db import migrations

# These complete function definitions are frozen here for reproducible reversal.
# Their sole delta is the two authorised refusal messages.
FORWARD = r"""
CREATE OR REPLACE FUNCTION public.l_assess(s jsonb, person bigint, cut timestamptz, known timestamptz, own_event bigint DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE lo public.core_livingorganism; doc public.core_libraryresource; v public.core_libraryresourceversion;
 b public.core_organismlawbinding; previous public.core_organismlawbinding; last_end public.core_organismlawending;
 c public.core_circle; scope text:=s->>'scope'; cls text:=s->>'law_class'; owner text; elector jsonb; auth jsonb;
 operation text:=s->>'operation'; intent text:=s->>'intent'; proposal uuid:=(s->>'proposal')::uuid;
 reference text; affected jsonb; affected_ref text; open_prior boolean; effect jsonb;
 pin text; agreement bigint; route text; previous_owner text;
BEGIN
 SELECT * INTO lo FROM public.core_livingorganism WHERE organism_id=(s->>'organism')::uuid;
 SELECT * INTO doc FROM public.core_libraryresource WHERE resource_id=s->>'resource';
 IF NOT EXISTS (SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
           WHERE i.id=person AND i.access_state='active' AND u.is_active) THEN
   RETURN jsonb_build_object('reason','This act needs your active identity.');
 END IF;
 IF lo.id IS NULL OR doc.id IS NULL THEN RETURN jsonb_build_object('reason','Choose an existing organisation and LIBRARY instrument.'); END IF;
 pin:=public.l_charter_pin(doc.resource_id);
 IF pin IS NOT NULL AND (s->>'feature'='END_CHARTER_PROTECTION' OR (intent='RETIRE' AND scope='PLATFORM')) THEN
   RETURN jsonb_build_object('reason','Ending this charter''s inherited constitutional protection requires an explicit ruling made outside this route, under GRP-13.');
 END IF;
 IF operation IN ('RESOLVE','PROCEED_AND_CLOSE') OR s->>'resolution'='PROCEED_AND_CLOSE' THEN
   RETURN jsonb_build_object('reason','Closing or resolving nominations is not yet available. Defer the change instead.');
 END IF;
 IF s->>'feature' IS NOT NULL THEN
   RETURN jsonb_build_object('reason',CASE s->>'feature'
     WHEN 'CIRCLE_DELEGATION' THEN 'Circle delegation of law is not yet available.'
     WHEN 'ISSUE_CIRCLE_TERMS' THEN 'Board-issued Circle terms of reference are not yet available.'
     WHEN 'RESOLUTION_LABEL' THEN 'Changing the display name for members’ resolutions is not yet available.'
     ELSE 'This law change is not yet available.' END);
 END IF;
 IF operation NOT IN ('ADOPT','RETIRE','DEFER') OR intent NOT IN ('ADOPT','RETIRE')
    OR (operation<>'DEFER' AND intent<>operation) THEN
   RETURN jsonb_build_object('reason','Choose adoption, retirement or a deferral of one of those acts.');
 END IF;
 IF scope='CIRCLE' THEN
   SELECT * INTO c FROM public.core_circle WHERE parent_organism_id=lo.id AND circle_uuid=(s->>'circle')::uuid;
   IF c.id IS NULL OR cls<>'TERMS_OF_REFERENCE' THEN RETURN jsonb_build_object('reason','Choose the exact Circle governed by these terms.'); END IF;
   reference:='CIRCLE:'||c.circle_uuid;
 ELSIF scope='PLATFORM' THEN
   IF NOT lo.platform_root THEN RETURN jsonb_build_object('reason','Only INTEVIA can adopt or retire inherited platform law.'); END IF;
   reference:='PLATFORM';
 ELSIF scope='LIVING_ORGANISM' THEN reference:='LO:'||lo.organism_id;
 ELSE RETURN jsonb_build_object('reason','Choose an organisation, platform or Circle scope.'); END IF;
 IF (cls='TERMS_OF_REFERENCE')<>(scope='CIRCLE') OR
    (scope<>'CIRCLE' AND s->>'circle' IS NOT NULL) OR
    (public.l_law_rank(cls) IS NULL AND cls<>'TERMS_OF_REFERENCE') THEN
   RETURN jsonb_build_object('reason','The specified class cannot govern this scope.');
 END IF;
 IF EXISTS (SELECT FROM public.core_lawact WHERE core_lawact.proposal=(s->>'proposal')::uuid
            AND kind<>'AGREE' AND event_id IS DISTINCT FROM own_event) THEN
   RETURN jsonb_build_object('reason','This proposal already has a completed act. Submit a fresh proposal.');
 END IF;
 SELECT * INTO previous FROM public.core_organismlawbinding p
 WHERE p.resource_id=doc.id AND p.scope_key=reference AND p.adoption_event_id IS DISTINCT FROM own_event
 AND p.in_force_from<=cut ORDER BY p.in_force_from DESC LIMIT 1;
 IF previous.binding_uuid IS DISTINCT FROM (s->>'predecessor')::uuid THEN
   RETURN jsonb_build_object('reason','The named predecessor differs from this instrument’s latest binding. Review the current version.');
 END IF;
 SELECT * INTO last_end FROM public.core_organismlawending x WHERE x.binding_id=previous.binding_uuid
   AND x.end_event_id IS DISTINCT FROM own_event;
 open_prior:=previous.binding_uuid IS NOT NULL AND last_end.binding_id IS NULL;
 IF intent='RETIRE' AND (NOT open_prior OR previous.law_class<>cls) THEN
   RETURN jsonb_build_object('reason','Retirement must name the open binding at its recorded class.');
 END IF;
 IF pin IS NOT NULL AND (NOT lo.platform_root OR cls<>'ARTICLE' OR scope='CIRCLE'
    OR (intent='RETIRE' AND (scope='PLATFORM' OR NOT EXISTS (SELECT FROM public.core_organismlawbinding platform_law
        WHERE platform_law.resource_id=doc.id AND platform_law.scope_kind='PLATFORM'
          AND public.l_visible_window(platform_law,cut,known))))) THEN
   RETURN jsonb_build_object('reason','Removing charter protection requires an explicit ruling made outside this route, under GRP-13.');
 END IF;
 IF intent='ADOPT' THEN
   SELECT * INTO v FROM public.core_libraryresourceversion WHERE resource_id=doc.id
       AND version_number=(s->>'version')::integer;
   IF v.id IS NULL OR doc.state='archived' OR NOT EXISTS
      (SELECT FROM public.core_libraryresourcetransition t WHERE t.version_id=v.id AND t.resource_id=doc.id
         AND t.to_state='published' AND t.occurred_at<=cut) THEN
     RETURN jsonb_build_object('reason','Adoption needs the exact published version of an unarchived LIBRARY instrument.');
   END IF;
   IF EXISTS(SELECT FROM public.core_organismlawbinding other WHERE other.resource_id=doc.id
      AND other.adoption_event_id IS DISTINCT FROM own_event AND public.l_visible_window(other,cut,known)
      AND ((scope='PLATFORM' AND other.scope_kind<>'PLATFORM' AND
           (other.shadow_side IS NOT NULL OR other.version_id<>v.id OR other.law_class<>cls))
        OR (scope<>'PLATFORM' AND other.scope_kind='PLATFORM' AND
           (NOT lo.platform_root OR scope='CIRCLE' OR other.version_id<>v.id OR other.law_class<>cls)))) THEN
      RETURN jsonb_build_object('reason','This version would shadow inherited platform law or disagree with the root copy.');
   END IF;
   IF pin IS NOT NULL AND encode(sha256(convert_to(v.content,'UTF8')),'hex')<>pin THEN
     RETURN jsonb_build_object('reason','A different charter text requires separately authorised GRP-13 amendment evidence.');
   END IF;
   IF EXISTS (SELECT FROM public.l_clauses(v.content) g GROUP BY g.locator HAVING count(*)>1 OR length(g.locator)>64) THEN
     RETURN jsonb_build_object('reason','An adopted version needs unique clause markers of at most 64 characters.');
   END IF;
   IF open_prior AND public.l_law_rank(cls)>public.l_law_rank(previous.law_class) THEN
     RETURN jsonb_build_object('reason','A lower class cannot displace higher law.');
   END IF;
   IF (s->>'resolution_effect' IS NOT DISTINCT FROM 'STANDING')<>(cls='MEMBERS_RESOLUTION')
      OR (s->>'resolution_effect' IS NOT NULL AND s->>'resolution_effect' NOT IN ('STANDING','AMENDS_ARTICLES'))
      OR ((s->>'resolution_effect' IS NULL)<>(s->>'resolution_reference' IS NULL))
      OR (s->>'resolution_effect'='AMENDS_ARTICLES' AND cls<>'ARTICLE')
      OR (coalesce((s->>'reserves_bylaws')::boolean,false) AND cls<>'ARTICLE') THEN
     RETURN jsonb_build_object('reason','Use a standing resolution for lasting law, or cite an amending resolution on its Article.');
   END IF;
 ELSE
   v.id:=previous.version_id;
   IF s->>'version' IS NOT NULL AND NOT EXISTS(SELECT FROM public.core_libraryresourceversion ver
     WHERE ver.id=v.id AND ver.version_number=(s->>'version')::integer) THEN
     RETURN jsonb_build_object('reason','The retirement version differs from its binding.');
   END IF;
 END IF;
 IF scope='CIRCLE' AND s->>'requested_route' IN ('GOVERNANCE_INTERIM','CQ_GOVERNANCE_CIRCLE') THEN
   RETURN jsonb_build_object('reason','Board-issued Circle terms of reference are not yet available.');
 END IF;
 owner:=public.l_class_owner(cls,public.l_reservation(lo.id,cut,known) IS NOT NULL,NULL);
 IF open_prior THEN
   SELECT o.kind INTO previous_owner FROM public.l_owner_at(previous,cut,known) o;
 END IF;
 elector:=public.l_electorate(lo.id,cut,known);
 auth:=public.l_route(lo,person,cls,coalesce(previous_owner,owner),scope,pin IS NOT NULL,s->>'requested_route',elector,cut,known);
 IF auth ? 'reason' THEN RETURN auth; END IF;
 route:=auth->>'route';
 affected:=public.l_pending(lo.id,scope='PLATFORM',CASE WHEN open_prior THEN previous.version_id END,
               CASE WHEN intent='ADOPT' THEN v.id END,previous.law_class,cls,
               previous.consequence_class,s->>'consequence_class',cut,known,previous.binding_uuid);
 affected_ref:='sha256:'||encode(sha256(convert_to('INTEVIA:LAW:AFFECTED:v1:'||
    public.l_canonical(affected),'UTF8')),'hex');
 IF jsonb_array_length(affected->'nominations')>0 AND operation<>'DEFER' THEN
   RETURN jsonb_build_object('reason','Pending nominations may be affected. Defer this change; existing law continues.','affected_reference',affected_ref,'affected',affected,'affected_count',jsonb_array_length(affected->'nominations'));
 END IF;
 IF (s->>'affected_reference' IS NOT NULL AND s->>'affected_reference'<>affected_ref)
    OR (operation='DEFER' AND (jsonb_array_length(affected->'nominations')=0 OR s->>'affected_reference' IS DISTINCT FROM affected_ref)) THEN
   RETURN jsonb_build_object('reason','Deferral needs the current affected nominations and their reference. Refresh the preview.','affected_reference',affected_ref,'affected',affected,'affected_count',jsonb_array_length(affected->'nominations'));
 END IF;
 IF intent='ADOPT' THEN
   b.binding_uuid:=proposal; b.resource_id:=doc.id; b.version_id:=v.id; b.organism_id:=lo.id;
   b.scope_kind:=scope; b.scope_circle_id:=c.id; b.law_class:=cls; b.owner_kind:=owner;
   b.decision_route:=route; b.delegated_areas:='{}'; b.reserves_bylaws:=(s->>'reserves_bylaws')::boolean;
   b.consequence_class:=s->>'consequence_class'; b.predecessor_id:=previous.binding_uuid;
   b.resolution_effect:=s->>'resolution_effect'; b.resolution_reference:=s->>'resolution_reference';
   IF b.consequence_class NOT IN ('BINDING','DECLARATORY') OR (b.consequence_class='DECLARATORY' AND
      (scope='PLATFORM' OR cls IN ('ARTICLE','BYLAW'))) THEN RETURN jsonb_build_object('reason','Choose a lawful consequence for this class.'); END IF;
   effect:=public.l_adoption_facts(b);
 ELSE effect:=jsonb_build_object('binding',previous.binding_uuid,'route',route); END IF;
 IF route='UNANIMOUS_PAIR' THEN
   SELECT a.event_id INTO agreement FROM public.core_lawact a JOIN public.core_authoritydecision d ON d.id=a.decision_id
   JOIN public.core_livingorganismevent e ON e.id=a.event_id
   WHERE a.kind='AGREE' AND a.proposal=(s->>'proposal')::uuid AND d.actor_id<>person
     AND d.target->'spec'=s AND a.plan->'electorate'=elector AND a.plan->'effect'=effect
     AND a.plan->'affected'=affected AND a.plan->>'route'=route AND d.actor_access_epoch=(elector->d.actor_id::text->>2)::bigint
     AND a.event_id IS DISTINCT FROM own_event
     AND e.recorded_at<=known AND e.effective_at<=cut ORDER BY e.sequence DESC LIMIT 1;
   IF agreement IS NULL THEN
     IF EXISTS (SELECT FROM public.core_lawact a JOIN public.core_authoritydecision d ON d.id=a.decision_id
        WHERE a.kind='AGREE' AND a.proposal=(s->>'proposal')::uuid AND d.actor_id=person
          AND a.plan->'electorate'=elector AND a.event_id IS DISTINCT FROM own_event) THEN
       RETURN jsonb_build_object('reason','Your agreement is already recorded. The other eligible member must decide independently. If the proposal has changed, start a fresh proposal.');
     END IF;
     operation:='AGREE';
   END IF;
 END IF;
 RETURN auth||jsonb_build_object('kind',operation,'effect',effect,'scope_key',reference,'open_prior',open_prior,
   'electorate',CASE WHEN route IN ('UNANIMOUS_PAIR','SOLE_MEMBER_COORDINATOR') THEN elector ELSE '{}'::jsonb END,
   'agreement',agreement,'affected_reference',affected_ref,'affected',affected,'affected_count',jsonb_array_length(affected->'nominations'));
END $$;

CREATE FUNCTION public.lib_laws_at(subject uuid, cut timestamptz, known timestamptz)
RETURNS TABLE(
 binding uuid, resource bigint, version bigint, version_number integer, class_name text,
 scope text, inherited boolean, owner text, owner_circle bigint, owner_organism bigint,
 owner_cause uuid, decision_route text, act uuid, predecessor uuid,
 effective_from timestamptz, effective_until timestamptz, proposing_circle bigint,
 terms_binding uuid, display_class text,
 library_reference text, scope_kind text, scope_circle uuid, owner_circle_uuid uuid,
 owner_organism_uuid uuid, consequence_class text, adoption_received_at timestamptz,
 adoption_recorded_at timestamptz, authority_decision_reference text, issuer uuid,
 content text)
LANGUAGE sql STABLE STRICT AS $$
 SELECT l.*, r.resource_id::text, b.scope_kind::text, scoped.circle_uuid,
        owning.circle_uuid, parent.organism_id, b.consequence_class::text,
        e.received_at, e.recorded_at, e.authority_decision_reference::text,
        issuing.organism_id, v.content
 FROM public.l_laws_at(subject,cut,known) l
 JOIN public.core_organismlawbinding b ON b.binding_uuid=l.binding
 JOIN public.core_libraryresource r ON r.id=l.resource
 JOIN public.core_libraryresourceversion v ON v.id=l.version
 JOIN public.core_livingorganismevent e ON e.id=b.adoption_event_id
 JOIN public.core_livingorganism issuing ON issuing.id=b.organism_id
 JOIN public.core_livingorganism parent ON parent.id=l.owner_organism
 LEFT JOIN public.core_circle scoped ON scoped.id=b.scope_circle_id
 LEFT JOIN public.core_circle owning ON owning.id=l.owner_circle
$$;
"""
REVERSE = r"""
DROP FUNCTION public.lib_laws_at(uuid,timestamptz,timestamptz);
CREATE OR REPLACE FUNCTION public.l_assess(s jsonb, person bigint, cut timestamptz, known timestamptz, own_event bigint DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE lo public.core_livingorganism; doc public.core_libraryresource; v public.core_libraryresourceversion;
 b public.core_organismlawbinding; previous public.core_organismlawbinding; last_end public.core_organismlawending;
 c public.core_circle; scope text:=s->>'scope'; cls text:=s->>'law_class'; owner text; elector jsonb; auth jsonb;
 operation text:=s->>'operation'; intent text:=s->>'intent'; proposal uuid:=(s->>'proposal')::uuid;
 reference text; affected jsonb; affected_ref text; open_prior boolean; effect jsonb;
 pin text; agreement bigint; route text; previous_owner text;
BEGIN
 SELECT * INTO lo FROM public.core_livingorganism WHERE organism_id=(s->>'organism')::uuid;
 SELECT * INTO doc FROM public.core_libraryresource WHERE resource_id=s->>'resource';
 IF NOT EXISTS (SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
           WHERE i.id=person AND i.access_state='active' AND u.is_active) THEN
   RETURN jsonb_build_object('reason','This act needs your active identity.');
 END IF;
 IF lo.id IS NULL OR doc.id IS NULL THEN RETURN jsonb_build_object('reason','Choose an existing organisation and LIBRARY instrument.'); END IF;
 pin:=public.l_charter_pin(doc.resource_id);
 IF pin IS NOT NULL AND (s->>'feature'='END_CHARTER_PROTECTION' OR (intent='RETIRE' AND scope='PLATFORM')) THEN
   RETURN jsonb_build_object('reason','Ending this charter’s inherited constitutional protection requires an explicit ruling from the INTEVIA Founder & Steward.');
 END IF;
 IF operation IN ('RESOLVE','PROCEED_AND_CLOSE') OR s->>'resolution'='PROCEED_AND_CLOSE' THEN
   RETURN jsonb_build_object('reason','Closing or resolving nominations is not yet available. Defer the change instead.');
 END IF;
 IF s->>'feature' IS NOT NULL THEN
   RETURN jsonb_build_object('reason',CASE s->>'feature'
     WHEN 'CIRCLE_DELEGATION' THEN 'Circle delegation of law is not yet available.'
     WHEN 'ISSUE_CIRCLE_TERMS' THEN 'Board-issued Circle terms of reference are not yet available.'
     WHEN 'RESOLUTION_LABEL' THEN 'Changing the display name for members’ resolutions is not yet available.'
     ELSE 'This law change is not yet available.' END);
 END IF;
 IF operation NOT IN ('ADOPT','RETIRE','DEFER') OR intent NOT IN ('ADOPT','RETIRE')
    OR (operation<>'DEFER' AND intent<>operation) THEN
   RETURN jsonb_build_object('reason','Choose adoption, retirement or a deferral of one of those acts.');
 END IF;
 IF scope='CIRCLE' THEN
   SELECT * INTO c FROM public.core_circle WHERE parent_organism_id=lo.id AND circle_uuid=(s->>'circle')::uuid;
   IF c.id IS NULL OR cls<>'TERMS_OF_REFERENCE' THEN RETURN jsonb_build_object('reason','Choose the exact Circle governed by these terms.'); END IF;
   reference:='CIRCLE:'||c.circle_uuid;
 ELSIF scope='PLATFORM' THEN
   IF NOT lo.platform_root THEN RETURN jsonb_build_object('reason','Only INTEVIA can adopt or retire inherited platform law.'); END IF;
   reference:='PLATFORM';
 ELSIF scope='LIVING_ORGANISM' THEN reference:='LO:'||lo.organism_id;
 ELSE RETURN jsonb_build_object('reason','Choose an organisation, platform or Circle scope.'); END IF;
 IF (cls='TERMS_OF_REFERENCE')<>(scope='CIRCLE') OR
    (scope<>'CIRCLE' AND s->>'circle' IS NOT NULL) OR
    (public.l_law_rank(cls) IS NULL AND cls<>'TERMS_OF_REFERENCE') THEN
   RETURN jsonb_build_object('reason','The specified class cannot govern this scope.');
 END IF;
 IF EXISTS (SELECT FROM public.core_lawact WHERE core_lawact.proposal=(s->>'proposal')::uuid
            AND kind<>'AGREE' AND event_id IS DISTINCT FROM own_event) THEN
   RETURN jsonb_build_object('reason','This proposal already has a completed act. Submit a fresh proposal.');
 END IF;
 SELECT * INTO previous FROM public.core_organismlawbinding p
 WHERE p.resource_id=doc.id AND p.scope_key=reference AND p.adoption_event_id IS DISTINCT FROM own_event
 AND p.in_force_from<=cut ORDER BY p.in_force_from DESC LIMIT 1;
 IF previous.binding_uuid IS DISTINCT FROM (s->>'predecessor')::uuid THEN
   RETURN jsonb_build_object('reason','The named predecessor differs from this instrument’s latest binding. Review the current version.');
 END IF;
 SELECT * INTO last_end FROM public.core_organismlawending x WHERE x.binding_id=previous.binding_uuid
   AND x.end_event_id IS DISTINCT FROM own_event;
 open_prior:=previous.binding_uuid IS NOT NULL AND last_end.binding_id IS NULL;
 IF intent='RETIRE' AND (NOT open_prior OR previous.law_class<>cls) THEN
   RETURN jsonb_build_object('reason','Retirement must name the open binding at its recorded class.');
 END IF;
 IF pin IS NOT NULL AND (NOT lo.platform_root OR cls<>'ARTICLE' OR scope='CIRCLE'
    OR (intent='RETIRE' AND (scope='PLATFORM' OR NOT EXISTS (SELECT FROM public.core_organismlawbinding platform_law
        WHERE platform_law.resource_id=doc.id AND platform_law.scope_kind='PLATFORM'
          AND public.l_visible_window(platform_law,cut,known))))) THEN
   RETURN jsonb_build_object('reason','Removing charter protection requires an explicit ruling from the INTEVIA Founder & Steward.');
 END IF;
 IF intent='ADOPT' THEN
   SELECT * INTO v FROM public.core_libraryresourceversion WHERE resource_id=doc.id
       AND version_number=(s->>'version')::integer;
   IF v.id IS NULL OR doc.state='archived' OR NOT EXISTS
      (SELECT FROM public.core_libraryresourcetransition t WHERE t.version_id=v.id AND t.resource_id=doc.id
         AND t.to_state='published' AND t.occurred_at<=cut) THEN
     RETURN jsonb_build_object('reason','Adoption needs the exact published version of an unarchived LIBRARY instrument.');
   END IF;
   IF EXISTS(SELECT FROM public.core_organismlawbinding other WHERE other.resource_id=doc.id
      AND other.adoption_event_id IS DISTINCT FROM own_event AND public.l_visible_window(other,cut,known)
      AND ((scope='PLATFORM' AND other.scope_kind<>'PLATFORM' AND
           (other.shadow_side IS NOT NULL OR other.version_id<>v.id OR other.law_class<>cls))
        OR (scope<>'PLATFORM' AND other.scope_kind='PLATFORM' AND
           (NOT lo.platform_root OR scope='CIRCLE' OR other.version_id<>v.id OR other.law_class<>cls)))) THEN
      RETURN jsonb_build_object('reason','This version would shadow inherited platform law or disagree with the root copy.');
   END IF;
   IF pin IS NOT NULL AND encode(sha256(convert_to(v.content,'UTF8')),'hex')<>pin THEN
     RETURN jsonb_build_object('reason','A different charter text requires separately authorised GRP-13 amendment evidence.');
   END IF;
   IF EXISTS (SELECT FROM public.l_clauses(v.content) g GROUP BY g.locator HAVING count(*)>1 OR length(g.locator)>64) THEN
     RETURN jsonb_build_object('reason','An adopted version needs unique clause markers of at most 64 characters.');
   END IF;
   IF open_prior AND public.l_law_rank(cls)>public.l_law_rank(previous.law_class) THEN
     RETURN jsonb_build_object('reason','A lower class cannot displace higher law.');
   END IF;
   IF (s->>'resolution_effect' IS NOT DISTINCT FROM 'STANDING')<>(cls='MEMBERS_RESOLUTION')
      OR (s->>'resolution_effect' IS NOT NULL AND s->>'resolution_effect' NOT IN ('STANDING','AMENDS_ARTICLES'))
      OR ((s->>'resolution_effect' IS NULL)<>(s->>'resolution_reference' IS NULL))
      OR (s->>'resolution_effect'='AMENDS_ARTICLES' AND cls<>'ARTICLE')
      OR (coalesce((s->>'reserves_bylaws')::boolean,false) AND cls<>'ARTICLE') THEN
     RETURN jsonb_build_object('reason','Use a standing resolution for lasting law, or cite an amending resolution on its Article.');
   END IF;
 ELSE
   v.id:=previous.version_id;
   IF s->>'version' IS NOT NULL AND NOT EXISTS(SELECT FROM public.core_libraryresourceversion ver
     WHERE ver.id=v.id AND ver.version_number=(s->>'version')::integer) THEN
     RETURN jsonb_build_object('reason','The retirement version differs from its binding.');
   END IF;
 END IF;
 IF scope='CIRCLE' AND s->>'requested_route' IN ('GOVERNANCE_INTERIM','CQ_GOVERNANCE_CIRCLE') THEN
   RETURN jsonb_build_object('reason','Board-issued Circle terms of reference are not yet available.');
 END IF;
 owner:=public.l_class_owner(cls,public.l_reservation(lo.id,cut,known) IS NOT NULL,NULL);
 IF open_prior THEN
   SELECT o.kind INTO previous_owner FROM public.l_owner_at(previous,cut,known) o;
 END IF;
 elector:=public.l_electorate(lo.id,cut,known);
 auth:=public.l_route(lo,person,cls,coalesce(previous_owner,owner),scope,pin IS NOT NULL,s->>'requested_route',elector,cut,known);
 IF auth ? 'reason' THEN RETURN auth; END IF;
 route:=auth->>'route';
 affected:=public.l_pending(lo.id,scope='PLATFORM',CASE WHEN open_prior THEN previous.version_id END,
               CASE WHEN intent='ADOPT' THEN v.id END,previous.law_class,cls,
               previous.consequence_class,s->>'consequence_class',cut,known,previous.binding_uuid);
 affected_ref:='sha256:'||encode(sha256(convert_to('INTEVIA:LAW:AFFECTED:v1:'||
    public.l_canonical(affected),'UTF8')),'hex');
 IF jsonb_array_length(affected->'nominations')>0 AND operation<>'DEFER' THEN
   RETURN jsonb_build_object('reason','Pending nominations may be affected. Defer this change; existing law continues.','affected_reference',affected_ref,'affected',affected,'affected_count',jsonb_array_length(affected->'nominations'));
 END IF;
 IF (s->>'affected_reference' IS NOT NULL AND s->>'affected_reference'<>affected_ref)
    OR (operation='DEFER' AND (jsonb_array_length(affected->'nominations')=0 OR s->>'affected_reference' IS DISTINCT FROM affected_ref)) THEN
   RETURN jsonb_build_object('reason','Deferral needs the current affected nominations and their reference. Refresh the preview.','affected_reference',affected_ref,'affected',affected,'affected_count',jsonb_array_length(affected->'nominations'));
 END IF;
 IF intent='ADOPT' THEN
   b.binding_uuid:=proposal; b.resource_id:=doc.id; b.version_id:=v.id; b.organism_id:=lo.id;
   b.scope_kind:=scope; b.scope_circle_id:=c.id; b.law_class:=cls; b.owner_kind:=owner;
   b.decision_route:=route; b.delegated_areas:='{}'; b.reserves_bylaws:=(s->>'reserves_bylaws')::boolean;
   b.consequence_class:=s->>'consequence_class'; b.predecessor_id:=previous.binding_uuid;
   b.resolution_effect:=s->>'resolution_effect'; b.resolution_reference:=s->>'resolution_reference';
   IF b.consequence_class NOT IN ('BINDING','DECLARATORY') OR (b.consequence_class='DECLARATORY' AND
      (scope='PLATFORM' OR cls IN ('ARTICLE','BYLAW'))) THEN RETURN jsonb_build_object('reason','Choose a lawful consequence for this class.'); END IF;
   effect:=public.l_adoption_facts(b);
 ELSE effect:=jsonb_build_object('binding',previous.binding_uuid,'route',route); END IF;
 IF route='UNANIMOUS_PAIR' THEN
   SELECT a.event_id INTO agreement FROM public.core_lawact a JOIN public.core_authoritydecision d ON d.id=a.decision_id
   JOIN public.core_livingorganismevent e ON e.id=a.event_id
   WHERE a.kind='AGREE' AND a.proposal=(s->>'proposal')::uuid AND d.actor_id<>person
     AND d.target->'spec'=s AND a.plan->'electorate'=elector AND a.plan->'effect'=effect
     AND a.plan->'affected'=affected AND a.plan->>'route'=route AND d.actor_access_epoch=(elector->d.actor_id::text->>2)::bigint
     AND a.event_id IS DISTINCT FROM own_event
     AND e.recorded_at<=known AND e.effective_at<=cut ORDER BY e.sequence DESC LIMIT 1;
   IF agreement IS NULL THEN
     IF EXISTS (SELECT FROM public.core_lawact a JOIN public.core_authoritydecision d ON d.id=a.decision_id
        WHERE a.kind='AGREE' AND a.proposal=(s->>'proposal')::uuid AND d.actor_id=person
          AND a.plan->'electorate'=elector AND a.event_id IS DISTINCT FROM own_event) THEN
       RETURN jsonb_build_object('reason','Your agreement is already recorded. The other eligible member must decide independently. If the proposal has changed, start a fresh proposal.');
     END IF;
     operation:='AGREE';
   END IF;
 END IF;
 RETURN auth||jsonb_build_object('kind',operation,'effect',effect,'scope_key',reference,'open_prior',open_prior,
   'electorate',CASE WHEN route IN ('UNANIMOUS_PAIR','SOLE_MEMBER_COORDINATOR') THEN elector ELSE '{}'::jsonb END,
   'agreement',agreement,'affected_reference',affected_ref,'affected',affected,'affected_count',jsonb_array_length(affected->'nominations'));
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0067_law_acts")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
