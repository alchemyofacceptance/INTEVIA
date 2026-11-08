"""DISC3 GI plans reuse the landed GI command and its exact-law guards."""

SQL = r"""
CREATE FUNCTION public.cq3_gi_plan(p_lo public.core_livingorganism,p_board uuid,p_person bigint,p_att jsonb,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $$
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
END $$;

CREATE FUNCTION public.cq3_gi_source(p_lo bigint,p_circle bigint,p_person bigint,p_operation text,
 p_payload jsonb,p_cut timestamptz,p_known timestamptz) RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
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
END $$;
"""


def edits(gi, function, one):
    """Retain office qualification, payload, attribution, version and citation checks."""
    old = function(gi.FORWARD, "gi_decision_reference")
    yield (
        old,
        one(
            old,
            "'GI-OFFICE:1:'",
            "(CASE WHEN p_source->>'kind'='CQ_RESULT' THEN 'GI-CQ:1:' ELSE 'GI-OFFICE:1:' END)",
        ),
    )
    for name, source, payload in (
        ("gi_execute", "p_payload", "p_payload"),
        ("gi_input", "NEW.authority_source", "NEW.payload"),
        ("gi_complete", "v_act.authority_source", "v_act.payload"),
    ):
        old = function(gi.FORWARD, name)
        route_argument = source + "->>'route',"
        if route_argument not in old:
            raise ValueError("Missing GI route argument in " + name)
        new = old.replace("public.gi_source(", "public.cq3_gi_source(")
        yield old, new.replace(route_argument, payload + ",")
