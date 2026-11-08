"""Constitutional permissions and the immutable transfer projected by Policy adoption."""

SQL = r"""
CREATE FUNCTION public.elect_document(p_text text) RETURNS jsonb LANGUAGE plpgsql IMMUTABLE STRICT AS $$
BEGIN
 RETURN p_text::jsonb;
EXCEPTION WHEN invalid_text_representation THEN RETURN NULL;
END $$;

-- One scoped receipt is part of the LAW IMPLEMENT act which adopted the exact Policy.
CREATE VIEW public.elect_transfers AS
 SELECT i.organism_id,i.act_uuid,i.board_uuid,i.ordinal,i.effective_at,i.recorded_at,
   i.request->>'result' AS decision,i.facts->'domain_plan'->'transfer' AS transfer
 FROM public.core_cqact i WHERE i.operation='IMPLEMENT' AND i.facts->'domain_plan' ? 'transfer';
CREATE INDEX elect_transfer_history ON public.core_cqact
 (organism_id, (facts->'domain_plan'->'transfer'->'scope'), ordinal DESC)
 WHERE operation='IMPLEMENT' AND facts->'domain_plan' ? 'transfer';

CREATE FUNCTION public.elect_last_transfer(p_lo bigint,p_scope jsonb,p_cut timestamptz)
RETURNS public.elect_transfers LANGUAGE sql STABLE AS $$
 SELECT t.* FROM public.elect_transfers t WHERE t.organism_id=p_lo AND t.transfer->'scope'=p_scope
 AND greatest(t.effective_at,t.recorded_at)<=p_cut ORDER BY t.ordinal DESC LIMIT 1
$$;
CREATE FUNCTION public.elect_run_scope(p_spec jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
 SELECT jsonb_build_object('power','RUN_ELECTION','binding',p_spec->'binding','version',p_spec->'version',
     'provision',p_spec->'provision','place',p_spec->'place')
$$;

-- Returning to a constitutional holder is an explicit successor receipt.
CREATE FUNCTION public.elect_policy_transfer(p_lo bigint,p_board uuid,p_att jsonb,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
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
 RETURN jsonb_build_object('transfer',grant_||jsonb_build_object('baseline',base_,'holder_circle',
     CASE WHEN target_=base_ AND base_='GOVERNANCE' THEN governing ELSE circle_.id END,
     'policy_binding',p_att->'spec'->'proposal','policy_version',p_att->'spec'->'version',
     'constitutional_decision',law_result));
END $$;

CREATE FUNCTION public.elect_admin_office(p_lo bigint,p_board uuid,p_att jsonb,p_person bigint,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE grant_ public.elect_transfers; circle_ bigint; source_ jsonb;
BEGIN
 SELECT b.circle_id INTO circle_ FROM public.core_discboard b WHERE b.organism_id=p_lo AND b.board_uuid=p_board;
 IF p_att->>'kind'='ELECTION' AND NOT public.elect_board_confirmation(p_att) THEN
   grant_:=public.elect_last_transfer(p_lo,public.elect_run_scope(p_att->'spec'),p_cut);
   IF grant_.act_uuid IS NOT NULL THEN circle_:=(grant_.transfer->>'holder_circle')::bigint; END IF;
 END IF;
 source_:=public.disc_office(p_lo,circle_,p_person,p_cut);
 IF source_ IS NOT NULL AND grant_.act_uuid IS NOT NULL THEN
   source_:=source_||jsonb_build_object('transfer',grant_.act_uuid,'policy_decision',grant_.decision);
 END IF;
 RETURN source_;
END $$;
"""
