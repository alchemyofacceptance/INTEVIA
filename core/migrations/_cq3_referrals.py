"""An eligibility-loss result can refer the exact matter, never its authority."""

SQL = r"""
CREATE FUNCTION public.cq3_sole_officer(p_lo bigint,p_cut timestamptz,p_known timestamptz)
RETURNS bigint LANGUAGE sql STABLE STRICT AS $$
 SELECT min(m.identity_id) FROM public.core_organismmembership m WHERE m.living_organism_id=p_lo
 AND public.circle_lo_office(p_lo,m.identity_id,p_cut,p_known) IS NOT NULL
 HAVING count(DISTINCT m.identity_id)=1
$$;

CREATE FUNCTION public.cq3_result_visible(p_key uuid,p_lo bigint,p_actor bigint,p_cut timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 SELECT EXISTS(SELECT FROM public.core_cqact a
 JOIN public.core_discboard b ON b.board_uuid=a.board_uuid
 JOIN public.discp_threads t ON t.thread_uuid=a.thread_uuid
 JOIN public.disc_categories cat ON cat.category_uuid=t.category_uuid AND cat.board_uuid=b.board_uuid
 JOIN public.discp_posts first ON first.thread_uuid=t.thread_uuid AND first.post_number=1
 LEFT JOIN public.core_circle c ON c.id=b.circle_id
 WHERE a.act_uuid=p_key AND a.organism_id=p_lo AND a.operation IN ('SETTLE','CLOSE','NO_BALLOT')
 AND (public.disc_office(p_lo,b.circle_id,p_actor,p_cut) IS NOT NULL OR
      (NOT first.coordinator_hidden AND (NOT first.author_hidden OR EXISTS(
          SELECT FROM public.core_cqact o WHERE o.thread_uuid=a.thread_uuid AND o.operation IN ('PROCEED','OPEN_VOTE','CLOSE','NO_BALLOT')))))
 AND (public.disc_audience(p_lo,b.circle_id,p_actor,cat.access_level,p_cut) OR
      (c.kind='GOVERNANCE' AND cat.access_level<>'COORDINATORS' AND public.disc_audience(p_lo,NULL,p_actor,'MEMBERS',p_cut))))
$$;

CREATE FUNCTION public.cq3_referral(a public.core_cqact,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE start_act public.core_cqact; origin public.core_discboard; gov bigint; gov_uuid uuid;
 lo_uuid uuid; origin_circle uuid; origin_key text; dest text:='NONE'; dest_key text;
 att jsonb; spec jsonb; cls text; owner_kind text; path jsonb; excluded jsonb; sole bigint;
BEGIN
 SELECT * INTO STRICT start_act FROM public.core_cqact WHERE thread_uuid=a.thread_uuid AND operation='INITIATE';
 SELECT * INTO STRICT origin FROM public.core_discboard WHERE board_uuid=a.board_uuid;
 SELECT organism_id INTO STRICT lo_uuid FROM public.core_livingorganism WHERE id=a.organism_id;
 SELECT circle_uuid INTO origin_circle FROM public.core_circle WHERE id=origin.circle_id;
 gov:=public.cq3_board(a.organism_id,p_cut,p_cut);
 SELECT circle_uuid INTO gov_uuid FROM public.core_circle WHERE id=gov;
 att:=start_act.request->'attachment'; spec:=att->'spec'; cls:=spec->>'law_class';
 origin_key:=coalesce('CIRCLE:'||origin_circle,'LO:'||lo_uuid);
 path:=coalesce(start_act.facts->'referred_path','[]'::jsonb)||to_jsonb(origin_key);
 SELECT coalesce(jsonb_agg(x ORDER BY x),'[]'::jsonb) INTO excluded FROM (
    SELECT value AS x FROM jsonb_array_elements(coalesce(start_act.facts->'referred_conflicts','[]'::jsonb))
    UNION SELECT to_jsonb(c.actor_id) FROM public.core_cqact c WHERE c.thread_uuid=a.thread_uuid
          AND c.operation='CONFLICT' AND c.effective_at<=p_cut AND c.recorded_at<=p_cut) people;
 IF att->>'kind'='LAW' THEN
   owner_kind:=public.l_class_owner(cls,public.l_reservation(a.organism_id,p_cut,p_cut) IS NOT NULL,NULL);
 END IF;
 -- Typed GI authority stays with its owner. A higher body can deliberate on
 -- an advisory matter, but cannot acquire competence over a bound GI.
 IF (att IS NULL OR att->>'kind' IN ('LAW','RELEASE_POLICY','PUBLISH_POLICY')) THEN
   IF gov IS NOT NULL AND gov IS DISTINCT FROM origin.circle_id AND NOT path ? ('CIRCLE:'||gov_uuid)
      AND (att IS NULL OR att->>'kind' IN ('RELEASE_POLICY','PUBLISH_POLICY') OR
          (owner_kind IN ('GOVERNANCE','LO_COORDINATOR') AND cls IN ('BYLAW','POLICY','PROCEDURE')))
   THEN dest:='GOVERNANCE'; dest_key:='CIRCLE:'||gov_uuid;
   ELSIF origin.circle_id IS NOT NULL AND NOT path ? ('LO:'||lo_uuid)
      AND (att IS NULL OR owner_kind='MEMBERS' OR (cls='BYLAW' AND gov IS NULL))
   THEN dest:='MEMBERSHIP'; dest_key:='LO:'||lo_uuid;
   END IF;
 END IF;
 IF dest='NONE' AND att->>'kind'='LAW' AND cls IN ('POLICY','PROCEDURE','TERMS_OF_REFERENCE')
    AND (cls<>'POLICY' OR gov IS NULL) AND spec->>'scope'<>'PLATFORM'
    AND public.l_charter_pin(spec->>'resource') IS NULL
    AND NOT EXISTS(SELECT FROM public.core_livingorganism root WHERE root.id=a.organism_id AND root.platform_root)
 THEN
   sole:=public.cq3_sole_officer(a.organism_id,p_cut,p_cut);
   IF sole IS NOT NULL AND NOT excluded @> jsonb_build_array(sole)
      AND NOT EXISTS(SELECT FROM public.core_organismlawbinding prior WHERE prior.binding_uuid=(spec->>'predecessor')::uuid
          AND (prior.law_class IN ('ARTICLE','BYLAW','MEMBERS_RESOLUTION') OR prior.owner_kind IN ('MEMBERS','CIRCLE')
            OR (prior.law_class='POLICY' AND gov IS NOT NULL)))
      AND NOT EXISTS(SELECT FROM public.core_organismlawbinding b
        JOIN public.core_libraryresource r ON r.id=b.resource_id WHERE b.organism_id=a.organism_id
          AND r.resource_id=spec->>'resource' AND b.decision_route='CQ_GOVERNANCE_CIRCLE' AND b.law_class='POLICY')
   THEN dest:='LO_COORDINATOR'; END IF;
 END IF;
 RETURN jsonb_build_object('origin_result',a.act_uuid,'wording_version',a.facts->'version',
    'wording_fingerprint',a.facts->'wording_fingerprint','attachment_fingerprint',start_act.facts->'attachment_fingerprint',
    'destination',dest,'destination_owner',dest_key,'visited',path,'conflicts',excluded,
    'officeholder',CASE WHEN dest='LO_COORDINATOR' THEN sole END);
END $$;

CREATE FUNCTION public.cq3_accept_referral(a public.core_cqact,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE source_act public.core_cqact; initial public.core_cqact; receiving public.core_cqact; ref jsonb; target_owner text;
BEGIN
 SELECT * INTO source_act FROM public.core_cqact WHERE act_uuid::text=a.request->>'referral_result'
   AND organism_id=a.organism_id;
 IF NOT coalesce(public.cq3_result_visible(source_act.act_uuid,a.organism_id,a.actor_id,p_cut),false)
 THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 ref:=source_act.facts->'referral';
 SELECT coalesce('CIRCLE:'||c.circle_uuid,'LO:'||lo.organism_id) INTO target_owner FROM public.core_discboard b
 JOIN public.core_livingorganism lo ON lo.id=b.organism_id LEFT JOIN public.core_circle c ON c.id=b.circle_id
 WHERE b.board_uuid=a.board_uuid AND b.organism_id=a.organism_id
 AND (b.circle_id IS NULL OR (c.kind='GOVERNANCE' AND public.cq3_board(lo.id,p_cut,p_cut)=c.id));
 IF ref IS NULL OR ref->>'destination' NOT IN ('MEMBERSHIP','GOVERNANCE') OR target_owner IS NULL
    OR ref->>'destination_owner' IS DISTINCT FROM target_owner OR (ref->'visited') ? target_owner
 THEN PERFORM public.cq_refuse('CQ_REFERRAL_BODY'); END IF;
 SELECT * INTO STRICT initial FROM public.core_cqact WHERE thread_uuid=source_act.thread_uuid AND operation='INITIATE';
 IF initial.request->'attachment' IS DISTINCT FROM a.request->'attachment'
   OR ref->>'wording_fingerprint' IS DISTINCT FROM a.facts->>'wording_fingerprint'
   OR a.request->>'previous_thread' IS NOT NULL THEN PERFORM public.cq_refuse('CQ_REFERRAL_MATTER'); END IF;
 SELECT * INTO receiving FROM public.core_cqact i WHERE i.operation='INITIATE' AND i.request->>'referral_result'=source_act.act_uuid::text;
 IF receiving.act_uuid IS NOT NULL THEN
   IF NOT EXISTS(SELECT FROM public.discp_threads t JOIN public.disc_categories cat USING(category_uuid)
      JOIN public.core_discboard b ON b.board_uuid=t.board_uuid
      JOIN public.discp_posts first ON first.thread_uuid=t.thread_uuid AND first.post_number=1
      WHERE t.thread_uuid=receiving.thread_uuid AND public.disc_audience(b.organism_id,b.circle_id,a.actor_id,cat.access_level,p_cut)
        AND (public.disc_office(b.organism_id,b.circle_id,a.actor_id,p_cut) IS NOT NULL OR
             (NOT first.coordinator_hidden AND NOT first.author_hidden)))
   THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
   PERFORM public.cq_refuse('CQ_REFERRAL_USED');
 END IF;
 RETURN jsonb_build_object('referred_path',ref->'visited','referred_conflicts',ref->'conflicts',
                            'referral_result',source_act.act_uuid);
END $$;

CREATE FUNCTION public.cq3_carried_conflict(p_thread uuid,p_person bigint,p_cut timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 SELECT EXISTS(SELECT FROM public.core_cqact i WHERE i.thread_uuid=p_thread AND i.operation='INITIATE'
    AND i.recorded_at<=p_cut AND i.effective_at<=p_cut AND (i.facts->'referred_conflicts') @> jsonb_build_array(p_person))
$$;

CREATE FUNCTION public.cq3_office_referral(s jsonb,p_lo bigint,p_person bigint,p_cut timestamptz,p_known timestamptz)
RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE r public.core_cqact; initial public.core_cqact; ref jsonb;
BEGIN
 SELECT * INTO r FROM public.core_cqact WHERE act_uuid::text=s->>'referral_result' AND organism_id=p_lo
   AND recorded_at<=p_known AND effective_at<=p_cut;
 IF NOT coalesce(public.cq3_result_visible(r.act_uuid,p_lo,p_person,p_known),false) THEN
   RETURN 'This discussion is unavailable to you.'; END IF;
 ref:=r.facts->'referral';
 IF ref->>'destination' IS DISTINCT FROM 'LO_COORDINATOR'
   OR (ref->>'officeholder')::bigint IS DISTINCT FROM p_person
   OR public.cq3_sole_officer(p_lo,p_cut,p_known) IS DISTINCT FROM p_person
   OR (ref->'conflicts') @> jsonb_build_array(p_person)
 THEN RETURN 'This referral needs its competent body or its sole unrecused LO Coordinator.'; END IF;
 SELECT * INTO STRICT initial FROM public.core_cqact WHERE thread_uuid=r.thread_uuid AND operation='INITIATE';
 IF initial.request->'attachment'->>'kind' IS DISTINCT FROM 'LAW'
    OR initial.request->'attachment'->'spec' IS DISTINCT FROM s-'referral_result'
    OR s->>'law_class' NOT IN ('POLICY','PROCEDURE','TERMS_OF_REFERENCE')
 THEN RETURN 'The referral covers only the exact unprotected proposal that went up.'; END IF;
 RETURN NULL;
END $$;

CREATE INDEX cq_law_attachment_proposal ON public.core_cqact ((request->'attachment'->'spec'->>'proposal'))
 WHERE operation='INITIATE' AND request->'attachment'->>'kind'='LAW';
CREATE UNIQUE INDEX cq_one_referral_journey ON public.core_cqact ((request->>'referral_result'))
 WHERE operation='INITIATE' AND request ? 'referral_result';
"""


def edits(clarity, ballots, function, one):
    old = function(clarity.FORWARD, "cq_roster")
    yield (
        old,
        one(
            old,
            " AND NOT EXISTS(SELECT FROM public.core_cqact a WHERE a.thread_uuid=p_thread",
            """
 AND NOT public.cq3_carried_conflict(p_thread,m.identity_id,p_cut)
 AND NOT EXISTS(SELECT FROM public.core_cqact a WHERE a.thread_uuid=p_thread""",
        ),
    )
    old = function(ballots.FORWARD, "cq_answer_eligible")
    yield (
        old,
        one(
            old,
            " IF EXISTS(SELECT FROM public.core_cqact c WHERE c.operation='CONFLICT'",
            """
 IF public.cq3_carried_conflict(p_thread,p_person,v_cut) THEN PERFORM public.cq_refuse('CQ_CONFLICTED'); END IF;
 IF EXISTS(SELECT FROM public.core_cqact c WHERE c.operation='CONFLICT'""",
        ),
    )
