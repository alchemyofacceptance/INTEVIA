"""Shared S source checks and C2 coverage on the existing immutable chains."""
from django.db import migrations

# Match organism_authority.authority_target_fingerprint. Inputs here are exclusively
# UUIDs, integer IDs, 64-hex digests and our fixed ASCII vocabulary.
SQL = r"""
CREATE FUNCTION public.c2_target(preimage text) RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT encode(sha256(convert_to('INTEVIA:S015:ORGANISM_AUTHORITY_TARGET:v1','UTF8')
                      || decode('00','hex') || convert_to(preimage,'UTF8')),'hex')
$$;
CREATE FUNCTION public.c2_lineage(b bigint, known timestamptz) RETURNS TABLE(id bigint)
LANGUAGE sql STABLE STRICT AS $$
 WITH RECURSIVE origins(id) AS (
    SELECT unnest(ARRAY[authority_principal_id,derivation_root_principal_id])
      FROM public.core_authoritybasis WHERE core_authoritybasis.id=b
    UNION
    SELECT e.root_principal_id FROM origins o JOIN public.core_authorityderivationedge e ON e.principal_id=o.id
     WHERE e.recorded_at<=known
 ) SELECT id FROM origins
$$;
CREATE FUNCTION public.c2_basis_in_force(b bigint, at_time timestamptz, known timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 SELECT EXISTS (
    SELECT 1 FROM public.core_authoritybasis a WHERE a.id=b AND a.effective_at<=at_time AND
    a.issued_at<=known AND a.received_at<=known AND a.recorded_at<=known
    AND NOT EXISTS(SELECT FROM public.core_authoritybasis n WHERE n.superseded_basis_id=b AND
                  n.effective_at<=at_time AND n.received_at<=known AND n.recorded_at<=known)
    AND coalesce((SELECT kind='RESTORATION' FROM public.core_authorityinvalidationevent i WHERE i.basis_id=b
                  AND i.effective_at<=at_time AND i.received_at<=known AND i.recorded_at<=known
                  ORDER BY i.effective_at DESC,i.occurred_at DESC,i.received_at DESC,i.recorded_at DESC,i.sequence DESC LIMIT 1),true))
$$;
CREATE FUNCTION public.c2_contested(d bigint, at_time timestamptz, known timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 SELECT EXISTS(SELECT FROM public.core_determinationcontestcase c JOIN public.core_determinationcontestevent e
 ON e.case_id=c.id WHERE c.determination_id=d AND e.effective_at<=at_time AND e.received_at<=known AND e.recorded_at<=known)
$$;
CREATE FUNCTION public.c2_finding_problem(f public.core_governeddetermination, subject uuid,
                                          at_time timestamptz, known timestamptz)
RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE parts text[]; g public.core_governeddetermination; b public.core_authoritybasis;
        s public.core_authoritybasis; expires timestamptz; grant_end timestamptz; stamp text;
BEGIN
 IF f.independence_state<>'INDEPENDENT' OR f.tested_actor_id IS NULL OR f.safeguard_beneficiary_id IS NULL
    OR f.determiner_id IN (f.tested_actor_id,f.safeguard_beneficiary_id)
    OR f.determiner_id=f.model_author_id OR btrim(f.evidence_reference)='' THEN
    RETURN 'The person determining the case is tested by or benefits from it.'; END IF;
 parts:=string_to_array(f.method_reference,'|');
 IF cardinality(parts)<>5 OR parts[1]<>'S:finding:1' OR parts[5]::uuid IS DISTINCT FROM subject THEN
    RETURN 'The finding does not identify its exact authority sources.'; END IF;
 SELECT * INTO s FROM public.core_authoritybasis WHERE basis_uuid=subject;
 SELECT * INTO b FROM public.core_authoritybasis WHERE basis_uuid=parts[3]::uuid;
 IF NOT coalesce(public.c2_basis_in_force(s.id,at_time,known),false)
    OR NOT coalesce(public.c2_basis_in_force(b.id,at_time,known),false)
    OR b.scope_fingerprint IS DISTINCT FROM f.scope_fingerprint
    OR f.relied_upon_authority_principal_id IS DISTINCT FROM b.authority_principal_id THEN
    RETURN 'The cited basis has ended, changed or was unavailable.'; END IF;
 IF EXISTS(SELECT id FROM public.c2_lineage(s.id,known) INTERSECT SELECT id FROM public.c2_lineage(b.id,known)) THEN
    RETURN 'Both authorities derive from the same source.'; END IF;
 IF EXISTS(SELECT FROM public.core_authorityprincipalaliasreview a WHERE a.recorded_at<=known
    AND NOT EXISTS(SELECT FROM public.core_authorityprincipalaliasreview newer
      WHERE newer.candidate_namespace=a.candidate_namespace AND newer.candidate_governed_source_id=a.candidate_governed_source_id
      AND newer.recorded_at<=known AND (newer.recorded_at,newer.id)>(a.recorded_at,a.id))
    AND a.state IN ('AUTHORITY_EQUIVALENCE_UNKNOWN','BOUND_TO_EXISTING_PRINCIPAL')
    AND (a.possible_principal_id IN (SELECT id FROM public.c2_lineage(s.id,known) UNION SELECT id FROM public.c2_lineage(b.id,known))
      OR EXISTS(SELECT FROM public.core_authorityprincipal p WHERE p.canonical_governed_source_id=a.candidate_governed_source_id
         AND p.id IN (SELECT id FROM public.c2_lineage(s.id,known) UNION SELECT id FROM public.c2_lineage(b.id,known))))) THEN
    RETURN 'The two sources cannot be established as independent.'; END IF;
 SELECT * INTO g FROM public.core_governeddetermination WHERE determination_uuid=parts[2]::uuid
   AND determination_type='CONSTITUTIONAL_AUTHORITY_QUALIFICATION' AND result='QUALIFIED'
   AND independence_state='INDEPENDENT' AND tested_actor_id=f.determiner_id
   AND determiner_id NOT IN(f.determiner_id,f.tested_actor_id,f.safeguard_beneficiary_id)
   AND relied_upon_authority_principal_id=b.authority_principal_id AND evidence_reference=b.instrument_reference
   AND scope_fingerprint=public.c2_target(format('{"basis":"%s","schema":"S:mandate:1","subject":"%s","verifier":%s}',
       b.basis_uuid,f.scope_fingerprint,f.determiner_id))
   AND effective_at<=at_time AND received_at<=known AND recorded_at<=known;
 IF g.id IS NULL OR split_part(g.method_reference,'|',1)<>'S:instrument:1' THEN
    RETURN 'An independently qualified mandate for this person and act is missing.'; END IF;
 stamp:='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$';
 IF parts[4]!~stamp OR split_part(g.method_reference,'|',2)!~stamp THEN RETURN 'The expiry cannot be verified.'; END IF;
 expires:=parts[4]::timestamptz; grant_end:=split_part(g.method_reference,'|',2)::timestamptz;
 IF expires<=at_time OR expires>grant_end THEN RETURN 'The finding is outside its authorised interval.'; END IF;
 IF public.c2_contested(g.id,at_time,known) OR public.c2_contested(f.id,at_time,known) THEN
    RETURN 'A contest has been recorded against this determination.'; END IF;
 RETURN NULL;
EXCEPTION WHEN invalid_text_representation OR invalid_datetime_format OR datetime_field_overflow THEN
 RETURN 'The finding source or expiry could not be read.';
END $$;
CREATE FUNCTION public.c2_guard_finding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE reason text; source uuid;
BEGIN
 IF NEW.method_reference LIKE 'S:finding:1|%' OR NEW.method_reference LIKE 'S:instrument:1|%' THEN
    NEW.recorded_at:=statement_timestamp();
 END IF;
 IF NEW.method_reference LIKE 'S:finding:1|%' THEN
    IF NEW.result NOT IN ('QUALIFIED','REFUSED') OR NEW.effective_at>NEW.received_at OR NEW.received_at>clock_timestamp()
       OR NEW.determination_type NOT IN ('EXECUTION_ELIGIBILITY_QUALIFICATION','UNAVAILABILITY_DETERMINATION','INDEPENDENCE_DETERMINATION') THEN
       RAISE EXCEPTION 'S finding has an invalid type, result or time' USING ERRCODE='23514'; END IF;
    source:=split_part(NEW.method_reference,'|',5)::uuid;
    PERFORM id FROM public.core_authoritybasis WHERE basis_uuid IN(source,split_part(NEW.method_reference,'|',3)::uuid)
       ORDER BY id FOR UPDATE;
    reason:=coalesce(public.c2_finding_problem(NEW,source,NEW.effective_at,NEW.received_at),
                     public.c2_finding_problem(NEW,source,clock_timestamp(),clock_timestamp()));
    IF reason IS NOT NULL THEN RAISE EXCEPTION 'S finding refused: %',reason USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER c2_guard_finding BEFORE INSERT ON public.core_governeddetermination
 FOR EACH ROW EXECUTE FUNCTION public.c2_guard_finding();

ALTER TABLE public.core_essentialcoveragerequirement ADD COLUMN c2_recorded_at timestamptz;
ALTER TABLE public.core_essentialcoveragerequirement ALTER COLUMN c2_recorded_at SET DEFAULT statement_timestamp();

CREATE FUNCTION public.c2_office_scope(lo uuid, office uuid, person bigint, basis uuid)
RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT public.c2_target(format('{"act":"COUNT_OFFICE","basis":"%s","beneficiary":%s,"kind":"EXECUTION_ELIGIBILITY_QUALIFICATION","organism":"%s","schema":"S:scope:1","target":"%s","tested":%s}',basis,person,lo,office,person))
$$;
CREATE FUNCTION public.c2_office_standing(lo uuid, office uuid, person bigint, basis uuid, at_time timestamptz, known timestamptz)
RETURNS text LANGUAGE sql STABLE STRICT AS $$
 WITH finding AS (
 SELECT d FROM public.core_governeddetermination d
 WHERE d.scope_fingerprint=public.c2_office_scope(lo,office,person,basis)
 AND d.determination_type='EXECUTION_ELIGIBILITY_QUALIFICATION'
 AND d.tested_actor_id=person AND d.safeguard_beneficiary_id=person
 AND d.effective_at<=at_time AND d.received_at<=known AND d.recorded_at<=known
 ORDER BY d.effective_at DESC,d.occurred_at DESC,d.received_at DESC,d.recorded_at DESC,d.id DESC LIMIT 1)
 SELECT coalesce((SELECT CASE WHEN public.c2_finding_problem(d,basis,at_time,known) IS NOT NULL THEN 'UNKNOWN'
                            WHEN (d).result IN ('QUALIFIED','REFUSED') THEN (d).result ELSE 'UNKNOWN' END FROM finding),'QUALIFIED')
$$;
CREATE FUNCTION public.c2_seat_facts(lo_id bigint, at_time timestamptz, known timestamptz)
RETURNS TABLE(assignment_id bigint,membership_id bigint, role_scope text, role_code text, standing text)
LANGUAGE sql STABLE STRICT AS $$
 SELECT a.id,m.id,r.scope::text,r.code::text,public.c2_office_standing(
   lo.organism_id,a.assignment_uuid,m.identity_id,b.basis_uuid,at_time,known)
 FROM public.core_livingorganism lo
 JOIN public.core_organismmembership m ON m.living_organism_id=lo.id
 JOIN public.core_contextualroleassignment a ON a.membership_id=m.id
 JOIN public.core_organismroledefinition r ON r.id=a.role_definition_id AND r.living_organism_id=lo.id
 CROSS JOIN LATERAL (SELECT e.effective_until,e.effective_until_state,e.authority_basis_id FROM public.core_contextualroleassignmentstateevent e
      WHERE e.assignment_id=a.id AND e.action='ASSIGN' AND e.effective_at<=at_time AND e.received_at<=known AND e.recorded_at<=known
      ORDER BY e.effective_at DESC,e.occurred_at DESC,e.received_at DESC,e.recorded_at DESC,e.sequence DESC LIMIT 1) term
 JOIN public.core_authoritybasis b ON b.id=term.authority_basis_id
 WHERE lo.id=lo_id AND (r.code<>'COORDINATOR' OR r.active) AND (term.effective_until_state<>'FIXED' OR term.effective_until>at_time)
 AND public.s015_fold_effective_state('core_organismmembership',m.id,at_time,known)='ACTIVE'
 AND public.s015_fold_effective_state('core_contextualroleassignment',a.id,at_time,known)='ACTIVE'

$$;
CREATE FUNCTION public.c2_seats(lo_id bigint, at_time timestamptz, known timestamptz)
RETURNS TABLE(membership_id bigint,role_scope text,role_code text,standing text) LANGUAGE sql STABLE STRICT AS $$
 SELECT f.membership_id,f.role_scope,f.role_code,CASE WHEN bool_or(f.standing='QUALIFIED') THEN 'QUALIFIED'
        WHEN bool_or(f.standing='UNKNOWN') THEN 'UNKNOWN' ELSE 'REFUSED' END
 FROM public.c2_seat_facts(lo_id,at_time,known) f GROUP BY f.membership_id,f.role_scope,f.role_code
$$;
CREATE FUNCTION public.c2_occupants(lo_id bigint, at_time timestamptz, known timestamptz)
RETURNS TABLE(membership_id bigint,role_scope text,role_code text) LANGUAGE sql STABLE STRICT AS $$
 SELECT s.membership_id,s.role_scope,s.role_code FROM public.c2_seats(lo_id,at_time,known) s WHERE s.standing='QUALIFIED'
$$;
CREATE INDEX c2_scope_cuts ON public.core_governeddetermination(scope_fingerprint,effective_at DESC,recorded_at DESC);
CREATE FUNCTION public.c2_roster_version(lo_id bigint, at_time timestamptz, known timestamptz)
RETURNS integer LANGUAGE sql STABLE STRICT AS $$
 SELECT a.roster_version FROM public.core_coverageassessment a JOIN public.core_essentialcoveragecase c ON c.id=a.case_id
 WHERE c.living_organism_id=lo_id AND a.composing_rule_reference IS DISTINCT FROM 'C0:G2:C2' AND a.effective_at<=at_time AND a.received_at<=known AND a.recorded_at<=known
 ORDER BY a.effective_at DESC,a.occurred_at DESC,a.received_at DESC,a.recorded_at DESC,a.sequence DESC LIMIT 1
$$;
-- NULL timestamps pre-date 0055. The installed cut is an upper bound on when
-- those rows were known. Only the actually witnessed founding roster has an earlier bound.
CREATE FUNCTION public.c2_install_cut() RETURNS timestamptz LANGUAGE sql STABLE AS $$
 SELECT coalesce(min(applied),'infinity'::timestamptz) FROM public.django_migrations WHERE app='core' AND name='0055_c2_coverage'
$$;
CREATE FUNCTION public.c2_legacy_uncertain(lo_id bigint, version integer, at_time timestamptz, known timestamptz, coordinator_only boolean DEFAULT false)
RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT known<public.c2_install_cut() AND EXISTS(
 SELECT FROM public.core_essentialcoveragerequirement r WHERE r.living_organism_id=lo_id AND r.roster_version=version
 AND (NOT coordinator_only OR (r.scope='LIVING_ORGANISM' AND r.qualifying_role_code='COORDINATOR'))
 AND r.c2_recorded_at IS NULL AND r.effective_from<=at_time AND (r.effective_until IS NULL OR r.effective_until>at_time)
 AND NOT EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.living_organism_id=lo_id AND e.sequence=1
 AND r.xmin=e.xmin AND r.roster_version=1 AND r.responsibility_domain_code='GOVERNANCE'
 AND r.qualifying_role_code='COORDINATOR' AND r.scope='LIVING_ORGANISM' AND r.minimum_active_occupants=1
 AND e.recorded_at<=known AND e.received_at<=known))
$$;
CREATE FUNCTION public.c2_rules(lo_id bigint, version integer, at_time timestamptz, known timestamptz)
RETURNS SETOF public.core_essentialcoveragerequirement LANGUAGE sql STABLE STRICT AS $$
 SELECT r.* FROM public.core_essentialcoveragerequirement r WHERE r.living_organism_id=lo_id
 AND r.roster_version=version AND r.effective_from<=at_time AND (r.effective_until IS NULL OR r.effective_until>at_time)
 AND (r.c2_recorded_at IS NULL OR r.c2_recorded_at<=known)
$$;
CREATE FUNCTION public.c2_result(lo_id bigint, version integer, at_time timestamptz, known timestamptz,
 coordinator_only boolean DEFAULT false, excluding_member bigint DEFAULT NULL)
RETURNS text LANGUAGE sql STABLE AS $$
 WITH seats AS MATERIALIZED (SELECT * FROM public.c2_seats(lo_id,at_time,known)
    WHERE membership_id IS DISTINCT FROM excluding_member),
 facts AS (SELECT r.minimum_active_occupants AS needed,
    (SELECT count(*) FROM seats o WHERE o.role_code=r.qualifying_role_code AND o.role_scope=r.scope AND o.standing='QUALIFIED') AS held,
    (SELECT count(*) FROM seats o WHERE o.role_code=r.qualifying_role_code AND o.role_scope=r.scope AND o.standing<>'REFUSED') AS possible
    FROM public.c2_rules(lo_id,version,at_time,known) r
    WHERE NOT coordinator_only OR (r.scope='LIVING_ORGANISM' AND r.qualifying_role_code='COORDINATOR'))
 SELECT CASE WHEN public.c2_legacy_uncertain(lo_id,version,at_time,known,coordinator_only) OR count(*)=0 THEN 'UNKNOWN' WHEN bool_or(possible<needed) THEN 'UNSATISFIED'
             WHEN bool_and(held>=needed) THEN 'SATISFIED' ELSE 'UNKNOWN' END FROM facts
$$;
"""
# Durable consequences use the same ILC preimages and chain heads as the command
# writers. All dynamic identifiers come from this closed list, never event data.
SQL += r"""
CREATE FUNCTION public.c2_phase_at(lo_id bigint, at_time timestamptz, known timestamptz)
RETURNS SETOF public.core_livingorganismevent LANGUAGE sql STABLE STRICT AS $$
 SELECT h.* FROM public.core_livingorganismevent h WHERE h.living_organism_id=lo_id
 AND h.effective_at<=at_time AND h.received_at<=known AND h.recorded_at<=known
 ORDER BY h.effective_at DESC,h.occurred_at DESC,h.received_at DESC,h.recorded_at DESC,h.id DESC LIMIT 1
$$;

CREATE FUNCTION public.c2_append_event(token text, anchor bigint, action_name text, outcome text,
 at_time timestamptz, occurred timestamptz, received timestamptz, actor bigint, basis bigint,
 decision text, evidence text, request_key text, payload text, metadata jsonb, deadline timestamptz DEFAULT NULL)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE table_name text; fk text; col text; head_row record; prev text; event uuid:=gen_random_uuid();
        values_ jsonb; cols text; id_ bigint;
BEGIN
 SELECT m.event_table,m.fk_col,m.state_col INTO STRICT table_name,fk,col
 FROM public.s015_anchor_map() m WHERE m.anchor_token=token
 AND token IN ('core_essentialcoveragecase','core_restrictedcontinuitycase','core_livingorganism');
 EXECUTE format('SELECT id,sequence,l1_commitment FROM public.%I WHERE %I=$1 ORDER BY sequence DESC LIMIT 1',table_name,fk)
 INTO head_row USING anchor;
 EXECUTE format('SELECT %I FROM public.%I WHERE %I=$1 AND (effective_at,occurred_at,received_at)<=($2,$3,$4)
                 ORDER BY effective_at DESC,occurred_at DESC,received_at DESC,recorded_at DESC,sequence DESC,event_uuid DESC LIMIT 1',col,table_name,fk)
 INTO prev USING anchor,at_time,occurred,received;
 values_:=jsonb_build_object(fk,anchor,'event_uuid',event,'sequence',coalesce(head_row.sequence,0)+1,
    'predecessor_id',head_row.id,'predecessor_sequence',head_row.sequence,'action',action_name,
    'prior_state',prev,col,outcome,'actor_state','NONE','actor_capacity','NA_NO_PARTY','enterer_identity_id',actor,
    'entry_mode','COMPOSED','composing_rule_state','REFERENCE','composing_rule_reference','C0:G2:C2',
    'composer_state','REFERENCE','composer_identity_id',actor,'effective_until_state',CASE WHEN deadline IS NULL THEN 'NONE' ELSE 'FIXED' END,
    'effective_until',deadline,'authority_basis_id',basis,'authority_decision_reference',decision,'evidence_reference',evidence,
    'request_reference',request_key,'idempotency_key',request_key,'payload_fingerprint',payload,
    'lineage_reference','s015l1:'||encode(sha256(convert_to(event::text||':'||payload,'UTF8')),'hex'),
    'temporal_basis_kind',CASE WHEN at_time<received THEN 'RETROSPECTIVE' WHEN at_time>received THEN 'PROSPECTIVE' END,
    'temporal_basis_reference',CASE WHEN at_time<>received THEN 's015tb1:'||payload END,
    'effective_at',at_time,'occurred_at',occurred,'received_at',received,
    'predecessor_l1_commitment',head_row.l1_commitment,'parts_commitment',
    public.s015_0022_digest('s015p1',public.s015_0022_parts_preimage(table_name,event)));
 IF EXISTS(SELECT FROM jsonb_object_keys(metadata) k WHERE k NOT IN
 ('roster_version','evaluated_state_at','evaluated_known_at','determiner_capacity_reference',
 'permitted_measures_reference','prohibited_effects_reference','triggering_assessment_id','accountable_actor_id')) THEN
    RAISE EXCEPTION 'C2 chain metadata is invalid'; END IF;
 values_:=values_||metadata;
 SELECT string_agg(quote_ident(k),',' ORDER BY k) INTO cols FROM jsonb_object_keys(values_) AS k;
 EXECUTE format('INSERT INTO public.%I (%s) SELECT %s FROM jsonb_populate_record(NULL::public.%I,$1) RETURNING id',
                table_name,cols,cols,table_name) INTO id_ USING values_;
 IF token='core_livingorganism' THEN
    PERFORM public.s015_refresh_governed_cache(token,anchor,greatest(
        (SELECT current_state_effective_at FROM public.core_livingorganism WHERE id=anchor),
        least(at_time,clock_timestamp())),clock_timestamp());
 END IF;
 RETURN id_;
END $$;

CREATE FUNCTION public.c2_publish(lo_id bigint, at_time timestamptz, occurred timestamptz, received timestamptz,
 actor bigint, basis bigint, decision text, evidence text, request_key text, payload text)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE v integer; case_id_ bigint; cont bigint; result_ text; covered boolean; a bigint; known timestamptz:=statement_timestamp();
BEGIN
 SELECT id INTO STRICT case_id_ FROM public.core_essentialcoveragecase WHERE living_organism_id=lo_id FOR UPDATE;
 v:=public.c2_roster_version(lo_id,at_time,known);
 IF v IS NULL THEN RAISE EXCEPTION 'Coverage could not be recorded. Check the founding assessment and effective date.'
 USING ERRCODE='23514'; END IF;
 result_:=public.c2_result(lo_id,v,at_time,known);
 covered:=public.c2_result(lo_id,v,at_time,known,true)='SATISFIED';
 a:=public.c2_append_event('core_essentialcoveragecase',case_id_,'ASSESS',result_,at_time,occurred,received,actor,basis,decision,
    evidence,request_key,payload,jsonb_build_object('roster_version',v,'evaluated_state_at',at_time,'evaluated_known_at',known,
    'determiner_capacity_reference',decision));
 SELECT id INTO cont FROM public.core_restrictedcontinuitycase WHERE living_organism_id=lo_id;
 IF cont IS NULL AND NOT covered THEN
    INSERT INTO public.core_restrictedcontinuitycase(case_uuid,living_organism_id) VALUES(gen_random_uuid(),lo_id) RETURNING id INTO cont;
 END IF;
 IF cont IS NOT NULL THEN
    PERFORM public.c2_append_event('core_restrictedcontinuitycase',cont,'REASSESS',CASE WHEN covered
      THEN 'NOT_RESTRICTED' ELSE 'RESTRICTED' END,at_time,occurred,received,actor,basis,decision,evidence,request_key,payload,
      jsonb_build_object('permitted_measures_reference',CASE WHEN covered THEN 'C2:ordinary-governed-routes'
       ELSE 'C2:preserve-leave-and-qualified-recovery' END,'prohibited_effects_reference',CASE WHEN covered
       THEN 'C2:no-extra-authority' ELSE 'C2:no-fallback-or-ordinary-execution' END,
       'triggering_assessment_id',a,'accountable_actor_id',actor));
 END IF;
 RETURN a;
END $$;

CREATE FUNCTION public.c2_is_coordinator_vacancy(lo_id bigint, version integer, at_time timestamptz, known timestamptz)
RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT public.c2_result(lo_id,version,at_time,known,true)='UNSATISFIED'
 AND NOT EXISTS(SELECT FROM public.c2_seats(lo_id,at_time,known) WHERE role_scope='LIVING_ORGANISM' AND role_code='COORDINATOR')
$$;

CREATE FUNCTION public.c2_source_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE lo_id bigint; lo public.core_livingorganism; c public.core_essentialcoveragecase;
 v integer; cut timestamptz; known timestamptz:=statement_timestamp(); origin text; outcome text;
 current_lo text; assessment bigint; already boolean; vacant boolean; party bigint; office text;
BEGIN
 IF TG_TABLE_NAME='core_organismmembershiptransition' THEN
    SELECT living_organism_id INTO lo_id FROM public.core_organismmembership WHERE id=NEW.membership_id;
    -- A proposal alone cannot change the number of active members holding offices.
    IF NEW.resulting_state='PROPOSED' AND NEW.prior_state IS DISTINCT FROM 'ACTIVE' THEN RETURN NULL; END IF;
    origin:='membership-ending:'||NEW.event_uuid;
    IF NEW.resulting_state<>'ENDED' THEN origin:='membership:'||NEW.event_uuid; END IF;
 ELSE
    IF NEW.action='PROPOSE' THEN RETURN NULL; END IF;
    SELECT m.living_organism_id,r.code INTO lo_id,office FROM public.core_contextualroleassignment a
    JOIN public.core_organismmembership m ON m.id=a.membership_id JOIN public.core_organismroledefinition r
    ON r.id=a.role_definition_id WHERE a.id=NEW.assignment_id;
    origin:='office:'||NEW.event_uuid;
 END IF;
 SELECT * INTO lo FROM public.core_livingorganism WHERE id=lo_id;
 IF lo.platform_root THEN RETURN NULL; END IF;
 IF lo.founding_insert_xid=pg_current_xact_id() THEN
    IF TG_TABLE_NAME='core_contextualroleassignmentstateevent' THEN PERFORM public.c2_future_cuts(lo_id); END IF;
    RETURN NULL; END IF;
 SELECT * INTO c FROM public.core_essentialcoveragecase WHERE living_organism_id=lo_id;
 IF c.id IS NULL THEN RAISE EXCEPTION 'C2 missing founded coverage case' USING ERRCODE='23514'; END IF;
 cut:=NEW.effective_at; v:=public.c2_roster_version(lo_id,cut,known);
 IF office IS NOT NULL AND NOT EXISTS(SELECT FROM public.c2_rules(lo_id,v,cut,known) r WHERE r.qualifying_role_code=office)
 THEN RETURN NULL; END IF;
 current_lo:=public.s015_fold_effective_state('core_livingorganism',lo_id,cut,known);
 outcome:=public.c2_result(lo_id,v,cut,known);
 SELECT EXISTS(SELECT FROM public.core_coverageassessment a WHERE a.case_id=c.id AND a.xmin=pg_current_xact_id()::xid
    AND a.effective_at=cut AND a.result=outcome AND a.evaluated_known_at>=NEW.recorded_at
    AND public.c2_result(lo_id,v,cut,known,true)=public.c2_result(lo_id,a.roster_version,cut,a.evaluated_known_at,true)) INTO already;
 IF NOT already THEN
    assessment:=public.c2_publish(lo_id,cut,NEW.occurred_at,NEW.received_at,NEW.enterer_identity_id,
    NEW.authority_basis_id,NEW.authority_decision_reference,origin,'c2:'||NEW.event_uuid,NEW.payload_fingerprint);
 END IF;
 PERFORM public.c2_future_cuts(lo_id);
 IF current_lo IN ('FOUNDING_PENDING','CLOSED') OR outcome='SATISFIED' THEN RETURN NULL; END IF;
 vacant:=public.c2_is_coordinator_vacancy(lo_id,v,cut,known);
 IF cut<=known AND current_lo='ACTIVE' AND vacant AND EXISTS(SELECT FROM public.core_organismmembership m WHERE m.living_organism_id=lo_id
     AND public.s015_fold_effective_state('core_organismmembership',m.id,cut,known) IN ('ACTIVE','PROBATIONARY')) THEN
    -- Only an actual disappearance of the last incumbent starts this timer. Routine
    -- membership traffic cannot create or restart an episode on a legacy vacancy.
    IF NOT EXISTS(SELECT FROM public.c2_occupants(lo_id,cut,NEW.recorded_at-interval '1 microsecond') o
       WHERE role_scope='LIVING_ORGANISM' AND role_code='COORDINATOR') THEN RETURN NULL; END IF;
    PERFORM public.c2_append_event('core_livingorganism',lo_id,'WITHOUT_COORDINATOR','RESTRICTED_CONTINUITY',cut,
      NEW.occurred_at,NEW.received_at,NEW.enterer_identity_id,NEW.authority_basis_id,NEW.authority_decision_reference,
      origin,'c2:'||NEW.event_uuid,NEW.payload_fingerprint,'{}',cut+interval '720 hours');
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER c2_membership_coverage AFTER INSERT ON public.core_organismmembershiptransition
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.c2_source_change();
CREATE CONSTRAINT TRIGGER c2_office_coverage AFTER INSERT ON public.core_contextualroleassignmentstateevent
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.c2_source_change();
"""
SQL += r"""
CREATE VIEW public.c2_office_finding AS
 SELECT d.id AS finding_id,d.determination_uuid,d.effective_at,d.recorded_at,d.received_at,d.determiner_id,
        d.method_reference,m.living_organism_id AS lo_id,lo.organism_id
 FROM public.core_governeddetermination d JOIN public.core_organismmembership m ON m.identity_id=d.tested_actor_id
 JOIN public.core_contextualroleassignment a ON a.membership_id=m.id
 JOIN public.core_livingorganism lo ON lo.id=m.living_organism_id AND NOT lo.platform_root
 JOIN public.core_authoritybasis b ON b.basis_uuid::text=split_part(d.method_reference,'|',5)
 WHERE EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent e WHERE e.assignment_id=a.id
              AND e.action='ASSIGN' AND e.authority_basis_id=b.id) AND d.determination_type='EXECUTION_ELIGIBILITY_QUALIFICATION' AND d.method_reference LIKE 'S:finding:1|%'
 AND d.scope_fingerprint=public.c2_office_scope(lo.organism_id,a.assignment_uuid,m.identity_id,b.basis_uuid);
CREATE VIEW public.c2_timed_input AS
 SELECT m.living_organism_id AS lo_id,'office-term:'||e.event_uuid AS source,e.effective_until AS due,
        e.recorded_at AS source_known FROM public.core_contextualroleassignmentstateevent e
 JOIN public.core_contextualroleassignment a ON a.id=e.assignment_id JOIN public.core_organismmembership m ON m.id=a.membership_id
 WHERE e.action='ASSIGN' AND e.effective_until_state='FIXED'
 UNION ALL SELECT m.living_organism_id,'office-effective:'||e.event_uuid,e.effective_at,e.recorded_at
 FROM public.core_contextualroleassignmentstateevent e JOIN public.core_contextualroleassignment a ON a.id=e.assignment_id
 JOIN public.core_organismmembership m ON m.id=a.membership_id WHERE e.effective_at>e.recorded_at AND e.action<>'PROPOSE'
 UNION ALL SELECT m.living_organism_id,'membership-effective:'||e.event_uuid,e.effective_at,e.recorded_at
 FROM public.core_organismmembershiptransition e JOIN public.core_organismmembership m ON m.id=e.membership_id
 WHERE e.effective_at>e.recorded_at AND e.resulting_state<>'PROPOSED'
 UNION ALL SELECT f.lo_id,'finding-start:'||f.determination_uuid,f.effective_at,f.recorded_at FROM public.c2_office_finding f
 UNION ALL SELECT f.lo_id,'finding-end:'||f.determination_uuid,split_part(f.method_reference,'|',4)::timestamptz,f.recorded_at
 FROM public.c2_office_finding f
 UNION ALL SELECT f.lo_id,'contest:'||e.event_uuid,e.effective_at,e.recorded_at FROM public.c2_office_finding f
 JOIN public.core_governeddetermination mandate ON mandate.determination_uuid::text=split_part(f.method_reference,'|',2)
 JOIN public.core_determinationcontestcase c ON c.determination_id IN (f.finding_id,mandate.id)
 JOIN public.core_determinationcontestevent e ON e.case_id=c.id
 UNION ALL SELECT f.lo_id,'source-change:'||e.event_uuid,e.effective_at,e.recorded_at FROM public.c2_office_finding f
 JOIN public.core_authoritybasis b ON b.basis_uuid::text IN(split_part(f.method_reference,'|',3),split_part(f.method_reference,'|',5))
 JOIN public.core_authorityinvalidationevent e ON e.basis_id=b.id
 UNION ALL SELECT f.lo_id,'supersession:'||n.basis_uuid,n.effective_at,n.recorded_at FROM public.c2_office_finding f
 JOIN public.core_authoritybasis b ON b.basis_uuid::text IN(split_part(f.method_reference,'|',3),split_part(f.method_reference,'|',5))
 JOIN public.core_authoritybasis n ON n.superseded_basis_id=b.id
 UNION ALL SELECT f.lo_id,'derivation:'||e.edge_uuid,e.recorded_at,e.recorded_at FROM public.c2_office_finding f
 JOIN public.core_authoritybasis b ON b.basis_uuid::text IN(split_part(f.method_reference,'|',3),split_part(f.method_reference,'|',5))
 JOIN public.core_authorityderivationedge e ON EXISTS(SELECT FROM public.c2_lineage(b.id,e.recorded_at) chain WHERE chain.id=e.principal_id)
 UNION ALL SELECT f.lo_id,'alias:'||e.review_uuid,e.recorded_at,e.recorded_at FROM public.c2_office_finding f
 JOIN public.core_authoritybasis b ON b.basis_uuid::text IN(split_part(f.method_reference,'|',3),split_part(f.method_reference,'|',5))
 JOIN public.core_authorityprincipalaliasreview e ON EXISTS(SELECT FROM public.c2_lineage(b.id,e.recorded_at) chain
 JOIN public.core_authorityprincipal p ON p.id=chain.id WHERE p.id=e.possible_principal_id
 OR p.canonical_governed_source_id=e.candidate_governed_source_id)
 UNION ALL SELECT r.living_organism_id,'roster-start:'||r.requirement_uuid,r.effective_from,r.c2_recorded_at
 FROM public.core_essentialcoveragerequirement r WHERE r.c2_recorded_at IS NOT NULL AND NOT EXISTS(
   SELECT FROM public.core_livingorganismevent e WHERE e.living_organism_id=r.living_organism_id AND e.sequence=1
   AND e.xmin=r.xmin AND r.effective_from<=e.effective_at)
 UNION ALL SELECT r.living_organism_id,'roster-end:'||r.requirement_uuid,r.effective_until,r.c2_recorded_at
 FROM public.core_essentialcoveragerequirement r WHERE r.effective_until IS NOT NULL;

CREATE FUNCTION public.c2_future_cuts(lo_id_ bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE cut record; v integer; actual text; previous public.core_coverageassessment; origin public.core_livingorganismevent;
        known timestamptz:=statement_timestamp(); key_ text;
BEGIN
 SELECT * INTO origin FROM public.core_livingorganismevent WHERE living_organism_id=lo_id_ AND sequence=1;
 FOR cut IN SELECT i.due,min(i.source) AS first_source FROM public.c2_timed_input i
   WHERE i.lo_id=lo_id_ AND i.due>known GROUP BY i.due ORDER BY i.due LOOP
   v:=public.c2_roster_version(lo_id_,cut.due,known);
   IF v IS NULL THEN CONTINUE; END IF;
   actual:=public.c2_result(lo_id_,v,cut.due,known);
   SELECT a.* INTO previous FROM public.core_coverageassessment a JOIN public.core_essentialcoveragecase c ON c.id=a.case_id
   WHERE c.living_organism_id=lo_id_ AND a.effective_at=cut.due AND a.received_at<=known AND a.recorded_at<=known
   ORDER BY a.occurred_at DESC,a.received_at DESC,a.recorded_at DESC,a.sequence DESC LIMIT 1;
   IF previous.id IS NOT NULL AND previous.result=actual AND previous.roster_version=v
      AND public.c2_result(lo_id_,v,cut.due,previous.evaluated_known_at,true)=public.c2_result(lo_id_,v,cut.due,known,true)
   THEN CONTINUE; END IF;
   key_:='c2-forecast:'||gen_random_uuid();
   PERFORM public.c2_publish(lo_id_,cut.due,known,known,origin.enterer_identity_id,origin.authority_basis_id,
     origin.authority_decision_reference,'c2-forecast:'||cut.first_source,key_,encode(sha256(convert_to(key_,'UTF8')),'hex'));
 END LOOP;
END $$;

CREATE FUNCTION public.c2_notice_input(lo_id_ bigint, source_ text) RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE input record; lo public.core_livingorganism; founding public.core_livingorganismevent;
        v integer; key_ text; payload text; current_lo text; known timestamptz;
BEGIN
 SELECT * INTO lo FROM public.core_livingorganism WHERE id=lo_id_;
 IF lo.id IS NULL OR lo.platform_root THEN RETURN false; END IF;
 PERFORM public.s015_register_organism_lock(lo.organism_id);
 known:=statement_timestamp();
 SELECT * INTO input FROM public.c2_timed_input i WHERE i.lo_id=lo_id_ AND i.source=source_ AND i.due<=known LIMIT 1;
 IF NOT FOUND THEN RETURN false; END IF;
 key_:='c2-boundary:'||source_;
 SELECT * INTO founding FROM public.core_livingorganismevent WHERE living_organism_id=lo_id_ AND sequence=1;
 IF founding.id IS NULL OR input.due<founding.effective_at THEN RETURN false; END IF;
 IF EXISTS(SELECT FROM public.core_coverageassessment a JOIN public.core_essentialcoveragecase c ON c.id=a.case_id
   WHERE c.living_organism_id=lo_id_ AND a.evidence_reference=key_ AND a.effective_at=input.due) THEN RETURN false; END IF;
 v:=public.c2_roster_version(lo_id_,input.due,known);
 IF v IS NULL THEN RETURN false; END IF;
 payload:=encode(sha256(convert_to(lo.organism_id::text||':'||key_,'UTF8')),'hex');
 PERFORM public.c2_publish(lo_id_,input.due,input.due,known,founding.enterer_identity_id,founding.authority_basis_id,
   founding.authority_decision_reference,key_,key_,payload);
 current_lo:=public.s015_fold_effective_state('core_livingorganism',lo_id_,input.due,known);
 IF current_lo='ACTIVE' AND public.c2_is_coordinator_vacancy(lo_id_,v,input.due,known)
   AND EXISTS(SELECT FROM public.c2_occupants(lo_id_,input.due-interval '1 microsecond',known)
              WHERE role_scope='LIVING_ORGANISM' AND role_code='COORDINATOR')
   AND EXISTS(SELECT FROM public.core_organismmembership m WHERE m.living_organism_id=lo_id_ AND
        public.s015_fold_effective_state('core_organismmembership',m.id,input.due,known) IN ('ACTIVE','PROBATIONARY')) THEN
     PERFORM public.c2_append_event('core_livingorganism',lo_id_,'WITHOUT_COORDINATOR','RESTRICTED_CONTINUITY',
       input.due,input.due,known,founding.enterer_identity_id,founding.authority_basis_id,founding.authority_decision_reference,
       key_,key_,payload,'{}',input.due+interval '720 hours');
 END IF;
 RETURN true;
END $$;

CREATE FUNCTION public.c2_on_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE source_ text; target record;
BEGIN
 CASE TG_TABLE_NAME
 WHEN 'core_governeddetermination' THEN
    IF NEW.method_reference NOT LIKE 'S:finding:1|%' THEN RETURN NULL; END IF;
    source_:='finding-start:'||NEW.determination_uuid;
 WHEN 'core_determinationcontestevent' THEN source_:='contest:'||NEW.event_uuid;
 WHEN 'core_authorityinvalidationevent' THEN source_:='source-change:'||NEW.event_uuid;
 WHEN 'core_authoritybasis' THEN
    IF NEW.superseded_basis_id IS NULL THEN RETURN NULL; END IF;
    source_:='supersession:'||NEW.basis_uuid;
 WHEN 'core_authorityderivationedge' THEN source_:='derivation:'||NEW.edge_uuid;
 WHEN 'core_authorityprincipalaliasreview' THEN source_:='alias:'||NEW.review_uuid;
 WHEN 'core_essentialcoveragerequirement' THEN source_:='roster-start:'||NEW.requirement_uuid;
 END CASE;
 FOR target IN SELECT DISTINCT i.lo_id,l.organism_id FROM public.c2_timed_input i
 JOIN public.core_livingorganism l ON l.id=i.lo_id WHERE i.source=source_
 AND l.founding_insert_xid IS DISTINCT FROM pg_current_xact_id() ORDER BY l.organism_id LOOP
    PERFORM public.s015_register_organism_lock(target.organism_id);
    PERFORM public.c2_notice_input(target.lo_id,source_);
    PERFORM public.c2_future_cuts(target.lo_id);
 END LOOP;
 RETURN NULL;
END $$;
"""
INPUT_TABLES = ("governeddetermination", "determinationcontestevent", "authorityinvalidationevent",
                "authoritybasis", "essentialcoveragerequirement", "authorityderivationedge", "authorityprincipalaliasreview")
for table in INPUT_TABLES:
    SQL += f"""
CREATE CONSTRAINT TRIGGER c2_input_{table} AFTER INSERT ON public.core_{table}
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.c2_on_input();
"""

SQL += r"""
CREATE FUNCTION public.c2_guard_recovery() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent public.core_livingorganismevent; lo public.core_livingorganism; known timestamptz:=clock_timestamp();
        v integer; linked boolean:=false; a public.core_coverageassessment;
BEGIN
 SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE id=NEW.living_organism_id;
 SELECT * INTO parent FROM public.core_livingorganismevent WHERE id=NEW.predecessor_id;
 IF NEW.action='K2_COORDINATOR_SEATED' THEN
    RAISE EXCEPTION 'A Coordinator recovery appointment must restore the organisation' USING ERRCODE='23514';
 END IF;
 IF NEW.action NOT IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE','K2_DISSOLVE','K2_RESTORED') THEN
    IF parent.action IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE','K2_DISSOLVE') AND
       NOT (NEW.action='DISSOLVE' AND NEW.resulting_state='CLOSED' AND EXISTS(
         SELECT FROM public.core_organismmembershiptransition m JOIN public.core_organismmembership x ON x.id=m.membership_id
         WHERE x.living_organism_id=lo.id AND NEW.evidence_reference='membership-ending:'||m.event_uuid
         AND m.resulting_state='ENDED' AND m.effective_at=NEW.effective_at)
         AND NOT EXISTS(SELECT FROM public.core_organismmembership x WHERE x.living_organism_id=lo.id AND
          public.s015_fold_effective_state('core_organismmembership',x.id,NEW.effective_at,known) IN ('ACTIVE','PROBATIONARY'))) THEN
       RAISE EXCEPTION 'The recovery episode cannot be replaced by an ordinary organisation event' USING ERRCODE='23514'; END IF;
    RETURN NULL;
 END IF;
 IF lo.platform_root OR NEW.entry_mode<>'COMPOSED' OR NEW.actor_state<>'NONE' THEN
    RAISE EXCEPTION 'K-2 requires a composed ordinary organisation event' USING ERRCODE='23514'; END IF;
 v:=public.c2_roster_version(lo.id,NEW.effective_at,known);
 IF NEW.action='WITHOUT_COORDINATOR' THEN
    SELECT EXISTS(SELECT FROM public.core_coverageassessment x JOIN public.core_essentialcoveragecase c ON c.id=x.case_id
      JOIN public.core_restrictedcontinuityevent rc ON rc.triggering_assessment_id=x.id
      WHERE c.living_organism_id=lo.id AND x.xmin=pg_current_xact_id()::xid AND public.c2_result(lo.id,x.roster_version,x.evaluated_state_at,x.evaluated_known_at,true)='UNSATISFIED'
      AND x.effective_at=NEW.effective_at AND rc.resulting_state='RESTRICTED'
      AND (x.evidence_reference=NEW.evidence_reference OR x.authority_decision_reference=NEW.authority_decision_reference))
      INTO linked;
    linked:=linked AND NEW.prior_state='ACTIVE'
      AND NEW.resulting_state='RESTRICTED_CONTINUITY'
      AND public.c2_is_coordinator_vacancy(lo.id,v,NEW.effective_at,known)
      AND NEW.effective_until=NEW.effective_at+interval '720 hours'
      AND EXISTS(SELECT FROM public.core_organismmembership x WHERE x.living_organism_id=lo.id AND
          public.s015_fold_effective_state('core_organismmembership',x.id,NEW.effective_at,known) IN ('ACTIVE','PROBATIONARY'));
    linked:=linked AND (
      EXISTS(SELECT FROM public.core_organismmembershiptransition e JOIN public.core_organismmembership m ON m.id=e.membership_id
        JOIN public.core_authoritydecision d ON d.decision_reference=e.authority_decision_reference
        WHERE m.living_organism_id=lo.id AND NEW.evidence_reference='membership-ending:'||e.event_uuid
        AND e.effective_at=NEW.effective_at AND d.target->'confirmed'='true'::jsonb AND e.resulting_state='ENDED'
        AND d.outcome='QUALIFIED' AND e.xmin=pg_current_xact_id()::xid AND d.decision_reference=NEW.authority_decision_reference)
      OR EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent e
        JOIN public.core_contextualroleassignment ra ON ra.id=e.assignment_id
        JOIN public.core_organismmembership m ON m.id=ra.membership_id
        JOIN public.core_organismroledefinition r ON r.id=ra.role_definition_id
        WHERE m.living_organism_id=lo.id AND r.code='COORDINATOR' AND r.scope='LIVING_ORGANISM'
        AND NEW.evidence_reference='office:'||e.event_uuid AND e.effective_at=NEW.effective_at
        AND e.prior_state='ACTIVE' AND e.resulting_state<>'ACTIVE' AND e.xmin=pg_current_xact_id()::xid
        AND e.authority_decision_reference=NEW.authority_decision_reference)
      OR EXISTS(SELECT FROM public.core_organismmembershiptransition e JOIN public.core_organismmembership m ON m.id=e.membership_id
        WHERE m.living_organism_id=lo.id AND NEW.evidence_reference='membership:'||e.event_uuid
        AND e.effective_at=NEW.effective_at AND e.prior_state='ACTIVE' AND e.resulting_state<>'ACTIVE'
        AND e.xmin=pg_current_xact_id()::xid AND e.authority_decision_reference=NEW.authority_decision_reference)
      OR EXISTS(SELECT FROM public.c2_timed_input i WHERE i.lo_id=lo.id AND NEW.evidence_reference='c2-boundary:'||i.source
        AND i.due=NEW.effective_at AND EXISTS(SELECT FROM public.c2_occupants(lo.id,i.due-interval '1 microsecond',known)
            WHERE role_scope='LIVING_ORGANISM' AND role_code='COORDINATOR')));
 ELSIF NEW.action='K2_RESTORED' THEN
    SELECT x.* INTO a FROM public.core_coverageassessment x JOIN public.core_essentialcoveragecase c ON c.id=x.case_id
    WHERE c.living_organism_id=lo.id AND NEW.evidence_reference='coverage-assessment:'||x.event_uuid
      AND x.xmin=pg_current_xact_id()::xid AND x.effective_at=NEW.effective_at;
    linked:=a.id IS NOT NULL AND NEW.effective_until IS NULL
      AND a.roster_version=v AND NEW.resulting_state='ACTIVE'
      AND public.c2_result(lo.id,v,a.evaluated_state_at,a.evaluated_known_at,true)='SATISFIED'
      AND public.c2_result(lo.id,v,NEW.effective_at,known,true)='SATISFIED'
      AND public.c2_result(lo.id,public.c2_roster_version(lo.id,known,known),known,known,true)='SATISFIED'
      AND EXISTS(
       SELECT FROM public.core_contextualroleassignmentstateevent e JOIN public.core_authoritydecision d ON d.decision_reference=e.authority_decision_reference
       JOIN public.core_contextualroleassignment h ON h.id=e.assignment_id JOIN public.core_organismroledefinition r ON r.id=h.role_definition_id
       JOIN public.core_organismmembership m ON m.id=h.membership_id
       WHERE a.evidence_reference='office:'||e.event_uuid AND d.outcome='QUALIFIED'
       AND m.living_organism_id=lo.id AND e.action='ASSIGN' AND r.scope='LIVING_ORGANISM'
       AND e.xmin=pg_current_xact_id()::xid AND e.effective_at=NEW.effective_at AND e.authority_decision_reference=NEW.authority_decision_reference
       AND parent.action IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE')
         AND NEW.effective_at<parent.effective_until AND NEW.recorded_at<parent.effective_until
         AND d.target->>'k2_episode'=CASE WHEN parent.action='WITHOUT_COORDINATOR' THEN parent.event_uuid::text
               ELSE substring(parent.evidence_reference FROM 4) END
         AND r.code='COORDINATOR' AND m.identity_id=d.actor_id
         AND EXISTS(SELECT FROM public.c2_occupants(lo.id,NEW.effective_at,a.evaluated_known_at) o WHERE o.membership_id=m.id
                    AND o.role_scope='LIVING_ORGANISM' AND o.role_code='COORDINATOR')
         AND EXISTS(SELECT FROM public.c2_occupants(lo.id,known,known) o WHERE o.membership_id=m.id
                    AND o.role_scope='LIVING_ORGANISM' AND o.role_code='COORDINATOR'));
 ELSE
    linked:=NEW.evidence_reference='k2:'||parent.event_uuid AND NEW.authority_decision_reference=parent.authority_decision_reference
      AND NEW.authority_basis_id=parent.authority_basis_id AND NEW.enterer_identity_id=parent.enterer_identity_id AND (
      (NEW.action='K2_DEACTIVATE' AND NEW.resulting_state='DORMANT' AND NEW.effective_until=NEW.effective_at+interval '720 hours'
       AND parent.action='WITHOUT_COORDINATOR' AND NEW.effective_at=parent.effective_until)
      OR (NEW.action='K2_DISSOLVE' AND parent.action='K2_DEACTIVATE' AND NEW.resulting_state='CLOSED'
          AND NEW.effective_at=parent.effective_until AND NEW.effective_until IS NULL));
 END IF;
 IF linked IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'C2 recovery evidence, coverage or countdown does not match the requested transition' USING ERRCODE='23514';
 END IF; RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER c2_recovery_evidence AFTER INSERT ON public.core_livingorganismevent
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.c2_guard_recovery();
CREATE FUNCTION public.c2_guard_measures() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE assessment public.core_coverageassessment; lo_id bigint; covered boolean;
BEGIN
 IF NEW.action IN ('K2_COORDINATOR_SEATED','WITHOUT_COORDINATOR') THEN
    RAISE EXCEPTION 'K-2 belongs to the organisation state chain' USING ERRCODE='23514';
 END IF;
 SELECT c.living_organism_id INTO STRICT lo_id FROM public.core_restrictedcontinuitycase c WHERE c.id=NEW.case_id;
 SELECT a.* INTO assessment FROM public.core_coverageassessment a JOIN public.core_essentialcoveragecase c ON c.id=a.case_id
 WHERE a.id=NEW.triggering_assessment_id AND c.living_organism_id=lo_id AND a.effective_at=NEW.effective_at;
 covered:=public.c2_result(lo_id,assessment.roster_version,assessment.evaluated_state_at,assessment.evaluated_known_at,true)='SATISFIED';
 IF assessment.id IS NULL OR NEW.resulting_state IS DISTINCT FROM
    (CASE WHEN covered THEN 'NOT_RESTRICTED' ELSE 'RESTRICTED' END) THEN
    RAISE EXCEPTION 'Continuity measures must match Coordinator coverage for this assessment' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER c2_restriction_recovery AFTER INSERT ON public.core_restrictedcontinuityevent
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.action IN ('K2_COORDINATOR_SEATED','WITHOUT_COORDINATOR','REASSESS'))
 EXECUTE FUNCTION public.c2_guard_measures();
"""
SQL += r"""
CREATE FUNCTION public.c2_roster_stamp() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.c2_recorded_at:=statement_timestamp(); RETURN NEW; END $$;
CREATE TRIGGER c2_roster_knowledge BEFORE INSERT ON public.core_essentialcoveragerequirement
 FOR EACH ROW EXECUTE FUNCTION public.c2_roster_stamp();

CREATE FUNCTION public.c2_assessment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE lo_id bigint; actual text;
BEGIN
 IF NEW.sequence=1 THEN RETURN NULL; END IF;
 SELECT c.living_organism_id INTO lo_id FROM public.core_essentialcoveragecase c WHERE c.id=NEW.case_id;
 IF (SELECT platform_root FROM public.core_livingorganism WHERE id=lo_id) THEN RETURN NULL; END IF;
 -- The earlier roster existence guard still runs for every assessment. At this
 -- layer the declared result also has to follow from the facts at its two cuts.
 IF NEW.evaluated_known_at>NEW.recorded_at OR NEW.evaluated_state_at<>NEW.effective_at THEN
    RAISE EXCEPTION 'Coverage assessment coordinates differ from the governed effect' USING ERRCODE='23514'; END IF;
 actual:=public.c2_result(lo_id,NEW.roster_version,NEW.evaluated_state_at,NEW.evaluated_known_at);
 IF NEW.result<>actual THEN RAISE EXCEPTION 'Coverage assessment disagrees with the essential roster occupants'
 USING ERRCODE='23514'; END IF;
 IF public.c2_result(lo_id,NEW.roster_version,NEW.evaluated_state_at,NEW.evaluated_known_at,true)<>'SATISFIED' AND NOT EXISTS(SELECT FROM public.core_restrictedcontinuityevent rc
    JOIN public.core_restrictedcontinuitycase c ON c.id=rc.case_id
    WHERE c.living_organism_id=lo_id AND rc.triggering_assessment_id=NEW.id AND rc.resulting_state='RESTRICTED'
    AND rc.effective_at=NEW.effective_at) THEN
    RAISE EXCEPTION 'Lost or unknown Coordinator coverage requires the matching restricted measures at the same boundary' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER c2_assessment_facts AFTER INSERT ON public.core_coverageassessment
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.c2_assessment_guard();
"""

SQL += r"""
CREATE FUNCTION public.c2_guard_volunteer() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE m public.core_organismmembership; role public.core_organismroledefinition; assignment public.core_contextualroleassignment;
        original public.core_livingorganismevent; current_ public.core_livingorganismevent; now_ timestamptz:=clock_timestamp();
BEGIN
 IF NEW.action NOT IN ('PROPOSE','ASSIGN') THEN RETURN NULL; END IF;
 SELECT * INTO assignment FROM public.core_contextualroleassignment WHERE id=NEW.assignment_id;
 SELECT * INTO m FROM public.core_organismmembership WHERE id=assignment.membership_id;
 SELECT * INTO role FROM public.core_organismroledefinition WHERE id=assignment.role_definition_id;
 SELECT * INTO original FROM public.c2_phase_at(m.living_organism_id,NEW.effective_at,NEW.received_at);
 SELECT * INTO current_ FROM public.c2_phase_at(m.living_organism_id,NEW.recorded_at,NEW.recorded_at);
 IF coalesce(original.action,'') NOT IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE','K2_DISSOLVE') AND
    coalesce(current_.action,'') NOT IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE','K2_DISSOLVE') THEN RETURN NULL; END IF;
 IF original.event_uuid IS DISTINCT FROM current_.event_uuid OR coalesce(original.action,'') NOT IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE')
    OR NEW.effective_at>=original.effective_until OR now_>=original.effective_until OR
    role.code<>'COORDINATOR' OR role.scope<>'LIVING_ORGANISM' OR NEW.enterer_identity_id<>m.identity_id OR
    public.s015_fold_effective_state('core_organismmembership',m.id,NEW.effective_at,NEW.received_at) IS DISTINCT FROM 'ACTIVE'
    OR public.s015_fold_effective_state('core_organismmembership',m.id,now_,now_) IS DISTINCT FROM 'ACTIVE'
    OR NOT EXISTS(SELECT FROM public.core_authoritydecision d WHERE d.decision_reference=NEW.authority_decision_reference
       AND d.outcome='QUALIFIED' AND d.action='TRANSITION_CONTEXTUAL_ROLE' AND d.actor_id=m.identity_id
       AND d.target->>'k2_episode'=CASE WHEN original.action='WITHOUT_COORDINATOR' THEN original.event_uuid::text
           ELSE substring(original.evidence_reference FROM 4) END AND d.target->>'event_action'=NEW.action
       AND d.effective_at=NEW.effective_at AND d.evaluated_at=NEW.received_at
       AND ((NEW.action='PROPOSE' AND d.target->>'membership_uuid'=m.membership_uuid::text
             AND d.target->>'role_definition_uuid'=role.role_uuid::text)
         OR (NEW.action='ASSIGN' AND d.target->>'assignment_uuid'=assignment.assignment_uuid::text))) THEN
    RAISE EXCEPTION 'Coordinator volunteer evidence does not match this member and current recovery window' USING ERRCODE='23514'; END IF;
 IF NEW.action='ASSIGN' AND NOT EXISTS(SELECT FROM public.core_livingorganismevent e
    JOIN public.core_coverageassessment assessment ON e.evidence_reference='coverage-assessment:'||assessment.event_uuid
    WHERE assessment.evidence_reference='office:'||NEW.event_uuid AND e.living_organism_id=m.living_organism_id AND e.action='K2_RESTORED' AND e.effective_at=NEW.effective_at
    AND e.authority_decision_reference=NEW.authority_decision_reference AND e.xmin=pg_current_xact_id()::xid) THEN
    RAISE EXCEPTION 'A recovery appointment requires a fresh assessment and restoration event' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER c2_volunteer AFTER INSERT ON public.core_contextualroleassignmentstateevent
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.c2_guard_volunteer();
"""

# Preserve evidence once S or C2 has operated. Do not remove other objects by CASCADE.
REVERSE = r"""
DO $$ BEGIN
 IF EXISTS(SELECT FROM public.core_governeddetermination WHERE method_reference LIKE 'S:finding:1|%' OR method_reference LIKE 'S:instrument:1|%')
 OR EXISTS(SELECT FROM public.core_livingorganismevent WHERE action IN ('WITHOUT_COORDINATOR','K2_DEACTIVATE','K2_DISSOLVE','K2_RESTORED'))
 OR EXISTS(SELECT FROM public.core_coverageassessment WHERE composing_rule_reference='C0:G2:C2')
 OR EXISTS(SELECT FROM public.core_essentialcoveragerequirement r WHERE r.c2_recorded_at IS NOT NULL AND
    NOT EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.living_organism_id=r.living_organism_id AND e.sequence=1
        AND r.roster_version=1 AND r.responsibility_domain_code='GOVERNANCE' AND r.qualifying_role_code='COORDINATOR'
        AND r.minimum_active_occupants=1 AND r.scope='LIVING_ORGANISM' AND r.effective_until IS NULL
        AND r.xmin=e.xmin)) THEN
    RAISE EXCEPTION 'C2 reversal refused because governed coverage or qualification records exist.';
 END IF;
END $$;
DROP TRIGGER c2_volunteer ON public.core_contextualroleassignmentstateevent;
DROP FUNCTION public.c2_guard_volunteer();
DROP TRIGGER c2_assessment_facts ON public.core_coverageassessment;
DROP FUNCTION public.c2_assessment_guard();
DROP TRIGGER c2_roster_knowledge ON public.core_essentialcoveragerequirement;
DROP FUNCTION public.c2_roster_stamp();
DROP TRIGGER c2_restriction_recovery ON public.core_restrictedcontinuityevent;
DROP FUNCTION public.c2_guard_measures();
DROP TRIGGER c2_recovery_evidence ON public.core_livingorganismevent;
DROP FUNCTION public.c2_guard_recovery();
"""
for table in INPUT_TABLES:
    REVERSE += f"DROP TRIGGER c2_input_{table} ON public.core_{table};\n"
REVERSE += """
DROP FUNCTION public.c2_on_input();
DROP FUNCTION public.c2_notice_input(bigint,text);
DROP FUNCTION public.c2_future_cuts(bigint);
DROP VIEW public.c2_timed_input;
DROP VIEW public.c2_office_finding;
DROP TRIGGER c2_membership_coverage ON public.core_organismmembershiptransition;
DROP TRIGGER c2_office_coverage ON public.core_contextualroleassignmentstateevent;
DROP FUNCTION public.c2_source_change();
DROP FUNCTION public.c2_is_coordinator_vacancy(bigint,integer,timestamptz,timestamptz);
DROP FUNCTION public.c2_publish(bigint,timestamptz,timestamptz,timestamptz,bigint,bigint,text,text,text,text);
DROP FUNCTION public.c2_phase_at(bigint,timestamptz,timestamptz);
DROP FUNCTION public.c2_append_event(text,bigint,text,text,timestamptz,timestamptz,timestamptz,bigint,bigint,text,text,text,text,jsonb,timestamptz);
DROP FUNCTION public.c2_result(bigint,integer,timestamptz,timestamptz,boolean,bigint);
DROP FUNCTION public.c2_rules(bigint,integer,timestamptz,timestamptz);
DROP FUNCTION public.c2_legacy_uncertain(bigint,integer,timestamptz,timestamptz,boolean);
DROP FUNCTION public.c2_install_cut();
DROP FUNCTION public.c2_roster_version(bigint,timestamptz,timestamptz);
DROP INDEX public.c2_scope_cuts;
DROP FUNCTION public.c2_occupants(bigint,timestamptz,timestamptz);
DROP FUNCTION public.c2_seats(bigint,timestamptz,timestamptz);
DROP FUNCTION public.c2_seat_facts(bigint,timestamptz,timestamptz);
DROP FUNCTION public.c2_office_standing(uuid,uuid,bigint,uuid,timestamptz,timestamptz);
DROP FUNCTION public.c2_office_scope(uuid,uuid,bigint,uuid);
ALTER TABLE public.core_essentialcoveragerequirement DROP COLUMN c2_recorded_at;
DROP TRIGGER c2_guard_finding ON public.core_governeddetermination;
DROP FUNCTION public.c2_guard_finding();
DROP FUNCTION public.c2_finding_problem(public.core_governeddetermination,uuid,timestamptz,timestamptz);
DROP FUNCTION public.c2_contested(bigint,timestamptz,timestamptz);
DROP FUNCTION public.c2_basis_in_force(bigint,timestamptz,timestamptz);
DROP FUNCTION public.c2_lineage(bigint,timestamptz);
DROP FUNCTION public.c2_target(text);
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0054_c1b_relationships")]
    operations = [migrations.RunSQL(SQL, reverse_sql=REVERSE)]
