"""ELECT · verified constitutional scope, nominations and protected ballots."""

SQL = r"""
CREATE TABLE public.core_electchoice (
 receipt uuid PRIMARY KEY REFERENCES public.core_cqact(act_uuid) DEFERRABLE INITIALLY DEFERRED,
 choice text NOT NULL);
CREATE FUNCTION public.elect_choice_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF pg_trigger_depth()<>2 OR EXISTS(SELECT FROM public.core_cqact WHERE act_uuid=NEW.receipt)
 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER elect_choice_guard BEFORE INSERT ON public.core_electchoice
 FOR EACH ROW EXECUTE FUNCTION public.elect_choice_guard();
CREATE TRIGGER elect_choice_immutable BEFORE UPDATE OR DELETE ON public.core_electchoice
 FOR EACH ROW EXECUTE FUNCTION public.s015_refuse_row_mutation();
CREATE TRIGGER s015_elect_choice_no_truncate BEFORE TRUNCATE ON public.core_electchoice
 FOR EACH STATEMENT EXECUTE FUNCTION public.s015_refuse_row_mutation();

CREATE FUNCTION public.elect_attachment(p_thread uuid) RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT a.request->'attachment' FROM public.core_cqact a WHERE a.thread_uuid=p_thread
   AND a.operation='INITIATE' AND a.request->'attachment'->>'kind'='ELECTION'
$$;

-- A constitution can identify a place in an adopted Article or Bylaw.
-- Uncompiled prose and unfamiliar constitutional methods are held for interpretation.
CREATE FUNCTION public.elect_plan(p_lo bigint,p_board uuid,p_att jsonb,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE spec jsonb:=p_att->'spec'; law_text text; rule jsonb; counting jsonb; seat jsonb;
 board_circle bigint; gov public.core_circle; arrangement public.core_governedrule;
 role_ public.core_organismroledefinition; route_ text; policy_ text; extra jsonb;
BEGIN
 IF NOT(spec ?& ARRAY['organism','binding','version','provision','place'])
   OR spec-ARRAY['organism','binding','version','provision','place']<>'{}'::jsonb
   OR nullif(btrim(spec->>'provision'),'') IS NULL THEN
   RETURN jsonb_build_object('code','ELECT_CITATION','reason','Cite the constitutional provision and version for this place, or enter the constitution in LIBRARY.');
 END IF;
 IF (p_att->>'text')::jsonb IS DISTINCT FROM spec THEN PERFORM public.cq_refuse('CQ_SETTINGS_WORDING'); END IF;
 SELECT v.content INTO law_text FROM public.core_livingorganism lo
 CROSS JOIN LATERAL public.l_laws_at(lo.organism_id,p_cut,p_cut) l
 JOIN public.core_libraryresourceversion v ON v.id=l.version
 WHERE lo.id=p_lo AND NOT l.inherited AND l.class_name IN ('ARTICLE','BYLAW')
 AND l.binding::text=spec->>'binding' AND l.version::text=spec->>'version';
 IF law_text IS NULL THEN RETURN jsonb_build_object('code','ELECT_CITATION','reason','The cited constitutional version is unavailable for this place.'); END IF;
 BEGIN rule:=law_text::jsonb->'elections'->(spec->>'provision');
 EXCEPTION WHEN invalid_text_representation THEN rule:=NULL; END;
 IF rule IS NULL OR rule->>'place' IS DISTINCT FROM spec->>'place' OR jsonb_typeof(rule) IS DISTINCT FROM 'object'
 THEN RETURN jsonb_build_object('code','ELECT_CITATION','reason','The cited provision does not identify this electoral place in a supported form.'); END IF;
 SELECT circle_id INTO board_circle FROM public.core_discboard WHERE board_uuid=p_board AND organism_id=p_lo;
 SELECT * INTO gov FROM public.core_circle WHERE parent_organism_id=p_lo AND kind='GOVERNANCE';
 IF (SELECT v.governance_enabled FROM public.core_livingorganismevent e JOIN public.core_logovernanceconfigurationversion v
       ON e.evidence_reference='governance-configuration-version:'||v.version_uuid AND v.living_organism_id=e.living_organism_id
       WHERE e.living_organism_id=p_lo AND e.action='EFFECT_GOVERNANCE_CONFIGURATION' AND greatest(e.effective_at,e.received_at,e.recorded_at)<=p_cut
       ORDER BY e.effective_at DESC,e.occurred_at DESC,e.received_at DESC,e.recorded_at DESC,e.sequence DESC,e.event_uuid DESC LIMIT 1)
       IS DISTINCT FROM true OR NOT EXISTS(SELECT FROM public.core_livingorganismevent e
       JOIN public.core_lobirthingsnapshot snap ON snap.living_organism_id=e.living_organism_id
        AND e.evidence_reference='birthing-snapshot:'||snap.snapshot_uuid AND snap.snapshot_commitment IS NOT NULL
       JOIN public.core_logovernanceconfigurationversion cfg ON cfg.living_organism_id=p_lo AND cfg.governance_enabled
        AND snap.body->'settings'->>'governance_configuration'='governance-configuration-version:'||cfg.version_uuid
       WHERE e.living_organism_id=p_lo AND e.action='BIRTHING' AND greatest(e.effective_at,e.received_at,e.recorded_at)<=p_cut)
       OR public.s015_fold_effective_state('core_livingorganism',p_lo,p_cut,p_cut) IS DISTINCT FROM 'ACTIVE'
       OR public.s015_fold_effective_state('core_circle',gov.id,p_cut,p_cut)='CLOSED'
   THEN RETURN jsonb_build_object('code','ELECT_GOVERNANCE','reason','Check the recorded Birthing choice and effective GOVERNANCE configuration before calling this election.'); END IF;
 IF gov.id IS NULL OR rule->>'body' IS NULL OR rule->>'body' NOT IN ('LO_MEMBERSHIP','GOVERNANCE')
    OR (board_circle IS DISTINCT FROM (CASE WHEN rule->>'body'='GOVERNANCE' THEN gov.id END))
    OR (rule->>'body'='GOVERNANCE' AND public.cq3_board(p_lo,p_cut,p_cut) IS DISTINCT FROM gov.id)
    OR (rule->>'type'='SEAT' AND board_circle IS NOT NULL) THEN
   RETURN jsonb_build_object('code','CQ_BODY','reason','This board cannot decide this act. Put it forward in the body that owns it.'); END IF;
 IF rule->>'type'='SEAT' THEN
   SELECT * INTO arrangement FROM public.core_governedrule WHERE scope_organism_id=p_lo
     AND code='GOVERNANCE_CONSTITUTION' AND effective_from<=p_cut AND recorded_at<=p_cut
     ORDER BY effective_from DESC,recorded_at DESC,version DESC LIMIT 1;
   SELECT s INTO seat FROM jsonb_array_elements(arrangement.body->'seats') s WHERE s->>'seat_id'=spec->>'place'
    AND s->>'admission_route'='ELECTION' AND s->>'deciding_body'='body:LO_MEMBERSHIP'
    AND (s->>'effective_from')::timestamptz<=p_cut;
   IF seat IS NULL THEN RETURN jsonb_build_object('code','ELECT_CITATION','reason','The current seat arrangement does not authorise this election.'); END IF;
   policy_:=coalesce(rule->>'nomination','C');
   extra:=jsonb_build_object('seat_rule',arrangement.rule_uuid,'seat_version',arrangement.version);
 ELSIF rule->>'type'='OFFICE' THEN
   SELECT * INTO role_ FROM public.core_organismroledefinition d WHERE d.living_organism_id=p_lo
     AND d.code=spec->>'place' AND d.kind='BOARD_OFFICE' AND NOT starts_with(d.code,'INTEVIA_') AND d.active
     AND d.effective_at<=p_cut AND d.recorded_at<=p_cut AND NOT EXISTS(SELECT FROM public.core_organismroledefinition s
       WHERE s.supersedes_id=d.id AND s.effective_at<=p_cut AND s.recorded_at<=p_cut);
   IF role_.id IS NULL OR role_.appointing_authority<>'NAMED_BODY'
      OR role_.appointing_body_reference IS DISTINCT FROM ('body:'||(rule->>'body'))
      OR NOT EXISTS(SELECT FROM public.core_organismroledefinitionprovision v WHERE v.definition_id=role_.id
        AND v.relationship='OFFICE_BASIS' AND v.provision_reference=spec->>'provision'
        AND v.provision_version=spec->>'version') THEN
      RETURN jsonb_build_object('code','ELECT_CITATION','reason','The office definition and its cited electoral authority must agree.'); END IF;
   policy_:=role_.nomination_policy;
   -- SR's production nomination entitlement source currently supplies no evidence.
   IF role_.nomination_basis='SILENT' AND policy_ IN ('A','B') THEN policy_:='C'; END IF;
   IF rule ? 'nomination' AND rule->>'nomination' IS DISTINCT FROM policy_ THEN
      RETURN jsonb_build_object('code','ELECT_METHOD_ROUTES','reason','The office definition and the cited provision give different nomination routes. Reconcile them before calling the election.'); END IF;
   extra:=jsonb_build_object('role_id',role_.id,'participation_required',role_.participation_required);
 ELSE RETURN jsonb_build_object('code','ELECT_METHOD_PLACE','reason','The cited provision must identify a supported electoral place, either a seat or an office.'); END IF;
 counting:=coalesce(rule->'counting','{}'::jsonb);
 IF rule-ARRAY['place','body','type','nomination','counting','term']<>'{}'::jsonb THEN
   RETURN jsonb_build_object('code','ELECT_METHOD_FIELDS','reason','The cited electoral provision contains an unsupported field. Review the provision before calling the election.');
 END IF;
 IF jsonb_typeof(counting) IS DISTINCT FROM 'object' THEN
   RETURN jsonb_build_object('code','ELECT_METHOD_COUNT_FIELDS','reason','Supply the counting instructions as an object containing only supported counting fields.');
 END IF;
 IF counting-ARRAY['method','threshold_base','threshold','numerator','denominator','abstention','tie','quorum_form','quorum_value','waive_minimum']<>'{}' THEN
   RETURN jsonb_build_object('code','ELECT_METHOD_COUNT_FIELDS','reason','Supply the counting instructions as an object containing only supported counting fields.');
 END IF;
 IF coalesce(counting->>'method','SINGLE_SEAT')<>'SINGLE_SEAT' THEN
   RETURN jsonb_build_object('code','ELECT_METHOD_COUNT','reason','This counting method is unsupported. Hold the election before opening.');
 END IF;
 IF policy_ NOT IN ('A','B','C') THEN
   RETURN jsonb_build_object('code','ELECT_METHOD_NOMINATION','reason','The cited nomination route is unsupported. Review who may nominate for this place.');
 END IF;
 IF rule ? 'term' AND rule->'term'<>'null'::jsonb THEN
   IF jsonb_typeof(rule->'term')<>'object' THEN
     RETURN jsonb_build_object('code','ELECT_METHOD_TERM','reason','Review the term. Supply an end and any start with a time zone, with the end after the start or current time.');
   END IF;
   IF (rule->'term')-ARRAY['start','end']<>'{}'
      OR coalesce(rule->'term'->>'end','') !~ '^\d{4}-\d{2}-\d{2}T.*(Z|[+-]\d{2}:\d{2})$'
      OR (rule->'term' ? 'start' AND coalesce(rule->'term'->>'start','') !~ '^\d{4}-\d{2}-\d{2}T.*(Z|[+-]\d{2}:\d{2})$') THEN
     RETURN jsonb_build_object('code','ELECT_METHOD_TERM','reason','Review the term. Supply an end and any start with a time zone, with the end after the start or current time.');
   END IF;
   IF (rule->'term'->>'end')::timestamptz<=coalesce((rule->'term'->>'start')::timestamptz,p_cut) THEN
     RETURN jsonb_build_object('code','ELECT_METHOD_TERM','reason','Review the term. Supply an end and any start with a time zone, with the end after the start or current time.');
   END IF;
 END IF;
 IF (rule->'term'->>'start')::timestamptz>p_cut THEN
    RETURN jsonb_build_object('code','ELECT_TERM','reason','This prospective succession needs the term-continuity route. Hold it for that route.'); END IF;
 route_:=CASE WHEN board_circle IS NULL THEN 'CQ_LO_MEMBERSHIP' ELSE 'CQ_GOVERNANCE_CIRCLE' END;
 RETURN spec-'version'||extra||jsonb_build_object('law_version',spec->'version','domain','ELECTION','route',route_,
   'body',rule->'body','type',rule->'type','circle',gov.id,'nomination',policy_,'counting',counting,
   'term',rule->'term');
EXCEPTION WHEN invalid_text_representation OR invalid_datetime_format OR datetime_field_overflow THEN
 RETURN jsonb_build_object('code','ELECT_METHOD_VALUE','reason','A value in the cited provision cannot be read. Check the representation of the provision and its dates.');
END $$;

CREATE FUNCTION public.elect_nomination(p_act public.core_cqact,p_cut timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE att jsonb:=public.elect_attachment(p_act.thread_uuid); plan jsonb; member_ public.core_organismmembership;
 nom public.core_cqact; phase_ text; assignment_ uuid; eligible_ boolean; person_ uuid;
BEGIN
 IF att IS NULL THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 SELECT phase INTO phase_ FROM public.cq_phases WHERE thread_uuid=p_act.thread_uuid;
 IF phase_ IS DISTINCT FROM 'PREPARATION' AND NOT (p_act.operation='ELECT_WITHDRAW' AND phase_ IN ('CLARITY','VOTING')) THEN
   PERFORM public.cq_refuse('ELECT_SLATE_LOCKED'); END IF;
 IF p_act.operation='ELECT_NOMINATE' THEN
   plan:=public.elect_plan(p_act.organism_id,p_act.board_uuid,att,p_cut);
   IF plan ? 'reason' THEN PERFORM public.cq_refuse(plan->>'code'); END IF;
   SELECT identity_id INTO person_ FROM public.core_identity WHERE id=p_act.actor_id;
   IF (plan->>'nomination'='B' OR (plan->>'nomination'='C' AND p_act.request->>'person' IS DISTINCT FROM person_::text))
     AND public.disc_office(p_act.organism_id,(SELECT circle_id FROM public.core_discboard WHERE board_uuid=p_act.board_uuid),
        p_act.actor_id,p_cut) IS NULL THEN PERFORM public.cq_refuse('ELECT_NOMINATION'); END IF;
   SELECT m.* INTO member_ FROM public.core_identity i JOIN public.core_organismmembership m ON m.identity_id=i.id
     WHERE i.identity_id::text=p_act.request->>'person' AND m.living_organism_id=p_act.organism_id
      AND public.disc_member_state(p_act.organism_id,i.id,p_cut)='ACTIVE';
   IF member_.id IS NULL OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
      WHERE i.id=member_.identity_id AND i.access_state='active' AND u.is_active) OR NOT public.elect_vacant(plan,member_.id,p_cut) THEN PERFORM public.cq_refuse('ELECT_CANDIDATE'); END IF;
   IF plan->>'type'='OFFICE' THEN
      SELECT a.assignment_uuid INTO assignment_ FROM public.core_contextualroleassignment a
       JOIN public.core_contextualroleassignmentstateevent e ON e.assignment_id=a.id AND e.sequence=1 AND e.action='PROPOSE'
       WHERE a.assignment_uuid::text=p_act.request->>'assignment' AND a.membership_id=member_.id
        AND e.actor_id=p_act.actor_id AND a.role_definition_id=(plan->>'role_id')::bigint
        AND public.s015_fold_effective_state('core_contextualroleassignment',a.id,p_cut,p_cut)='PROPOSED';
      IF assignment_ IS NULL THEN PERFORM public.cq_refuse('ELECT_CANDIDATE'); END IF;
   ELSIF p_act.request->>'assignment' IS NOT NULL THEN PERFORM public.cq_refuse('ELECT_CANDIDATE'); END IF;
   IF EXISTS(SELECT FROM public.core_cqact n WHERE n.thread_uuid=p_act.thread_uuid AND n.operation='ELECT_NOMINATE'
       AND n.request->'person'=p_act.request->'person' AND NOT EXISTS(SELECT FROM public.core_cqact w
         WHERE w.operation='ELECT_WITHDRAW' AND w.request->>'nomination'=n.act_uuid::text)) THEN PERFORM public.cq_refuse('ELECT_DUPLICATE'); END IF;
   RETURN jsonb_build_object('person',p_act.request->'person','member',member_.id,'assignment',assignment_,
     'statement',p_act.request->'statement','self_consent',member_.identity_id=p_act.actor_id);
 END IF;
 SELECT * INTO nom FROM public.core_cqact n WHERE n.organism_id=p_act.organism_id AND n.thread_uuid=p_act.thread_uuid
   AND n.operation='ELECT_NOMINATE' AND n.act_uuid::text=p_act.request->>'nomination';
 IF nom.act_uuid IS NULL OR nom.facts->>'person' IS DISTINCT FROM (SELECT identity_id::text FROM public.core_identity WHERE id=p_act.actor_id)
 THEN PERFORM public.cq_refuse('ELECT_OWN'); END IF;
 IF EXISTS(SELECT FROM public.core_cqact a WHERE a.thread_uuid=p_act.thread_uuid AND a.request->>'nomination'=nom.act_uuid::text
    AND (a.operation='ELECT_WITHDRAW' OR a.operation=p_act.operation))
   OR (p_act.operation='ELECT_CONSENT' AND (nom.facts->>'self_consent')::boolean) THEN PERFORM public.cq_refuse('ELECT_DUPLICATE'); END IF;
 IF phase_='VOTING' THEN
   SELECT EXISTS(SELECT FROM public.cq_phases h JOIN public.core_cqact o USING(act_uuid)
     CROSS JOIN LATERAL jsonb_array_elements(o.facts->'slate') s WHERE h.thread_uuid=p_act.thread_uuid
       AND s->>'nomination'=nom.act_uuid::text AND h.deadline>p_cut) INTO eligible_;
   IF NOT eligible_ THEN PERFORM public.cq_refuse('ELECT_SLATE_LOCKED'); END IF;
 END IF;
 RETURN jsonb_build_object('nomination',nom.act_uuid,'person',nom.facts->'person');
END $$;

CREATE FUNCTION public.elect_slate(p_thread uuid,p_plan jsonb,p_cut timestamptz) RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT coalesce(jsonb_agg(n.facts||jsonb_build_object('nomination',n.act_uuid,'consent',
   coalesce(c.act_uuid,n.act_uuid)) ORDER BY n.ordinal),'[]'::jsonb)
 FROM public.core_cqact n JOIN public.core_organismmembership m ON m.id=(n.facts->>'member')::bigint
 LEFT JOIN public.core_cqact c ON c.operation='ELECT_CONSENT' AND c.request->>'nomination'=n.act_uuid::text
 WHERE n.thread_uuid=p_thread AND n.operation='ELECT_NOMINATE' AND (c.act_uuid IS NOT NULL OR (n.facts->>'self_consent')::boolean)
 AND EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
   WHERE i.id=m.identity_id AND u.is_active AND i.access_state='active')
 AND public.disc_member_state(n.organism_id,m.identity_id,p_cut)='ACTIVE'
 AND NOT EXISTS(SELECT FROM public.core_cqact w WHERE w.operation='ELECT_WITHDRAW' AND w.request->>'nomination'=n.act_uuid::text)
 AND (NOT coalesce((p_plan->>'participation_required')::boolean,false) OR EXISTS(
   SELECT FROM public.core_circleparticipation t WHERE t.circle_id=(p_plan->>'circle')::bigint AND t.membership_id=m.id
   AND t.effective_at<=p_cut AND NOT EXISTS(SELECT FROM public.core_circleparticipationend e WHERE e.participation_id=t.id AND e.effective_at<=p_cut)))
 AND (p_plan->>'type'<>'OFFICE' OR EXISTS(SELECT FROM public.core_contextualroleassignment a
 JOIN public.core_contextualroleassignmentstateevent proposal ON proposal.assignment_id=a.id AND proposal.sequence=1 AND proposal.action='PROPOSE'
 LEFT JOIN LATERAL (SELECT h.action FROM public.core_officeappointmentconsent h WHERE h.proposal_event_id=proposal.id
   AND greatest(h.effective_at,h.recorded_at,h.received_at)<=p_cut ORDER BY h.ordinal DESC LIMIT 1) last_ ON true
 WHERE a.assignment_uuid::text=n.facts->>'assignment' AND a.role_definition_id=(p_plan->>'role_id')::bigint
 AND public.s015_fold_effective_state('core_contextualroleassignment',a.id,p_cut,p_cut)='PROPOSED'
 AND coalesce(last_.action,(CASE WHEN proposal.actor_id=m.identity_id THEN 'CONSENT' END))='CONSENT'))
$$;

CREATE FUNCTION public.elect_tally(p_rules jsonb,p_slate jsonb,p_totals jsonb,p_size integer)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE total_ integer; abstain_ integer:=coalesce((p_totals->>'ABSTAIN')::integer,0);
 top_ integer; ties_ integer; best_ text; counted jsonb; single_ boolean:=jsonb_array_length(p_slate)=1;
BEGIN
 SELECT coalesce(sum(value::integer),0) INTO total_ FROM jsonb_each_text(p_totals);
 SELECT max(coalesce((p_totals->>(s->>'person'))::integer,0)) INTO top_ FROM jsonb_array_elements(p_slate) s;
 SELECT count(*),min(s->>'person') INTO ties_,best_ FROM jsonb_array_elements(p_slate) s
 WHERE coalesce((p_totals->>(s->>'person'))::integer,0)=top_;
 -- The CQ helper compares yes and no. Several distinct opponents are not a tied candidate.
 counted:=public.cq_count_decision(p_rules,p_size,top_,total_-abstain_-top_,abstain_);
 IF (counted->>'cqt_applied')::boolean THEN
   IF ties_>1 OR (single_ AND top_=coalesce((p_totals->>'NOT_QUALIFIED')::int,0)) THEN
      counted:=counted||'{"kind":"NOT_QUALIFIED_TIE"}'::jsonb;
   ELSIF top_=0 THEN counted:=counted||'{"kind":"NOT_QUALIFIED_VOTE"}'::jsonb;
   ELSIF p_rules->>'threshold'='FRACTION' AND top_::numeric*(p_rules->>'denominator')::numeric
       >=(counted->>'qualification_denominator')::numeric*(p_rules->>'numerator')::numeric THEN
      counted:=counted||'{"kind":"QUALIFIED"}'::jsonb;
   ELSIF NOT single_ AND counted->>'kind'='NOT_QUALIFIED_TIE' THEN
      counted:=counted||'{"kind":"NOT_QUALIFIED_VOTE"}'::jsonb;
   END IF;
 END IF;
 RETURN counted-ARRAY['qualified','not_qualified']||jsonb_build_object('totals',p_totals,'winner',
   CASE WHEN counted->>'kind'='QUALIFIED' THEN best_ END);
END $$;
"""

SQL += r"""
CREATE FUNCTION public.elect_check_count(p_rules jsonb,p_changes jsonb,p_cut timestamptz)
RETURNS void LANGUAGE plpgsql STABLE AS $$
BEGIN
 IF p_rules ? 'waive_minimum' AND jsonb_typeof(p_rules->'waive_minimum') IS DISTINCT FROM 'boolean'
 THEN PERFORM public.cq_refuse('ELECT_METHOD_WAIVER'); END IF;
 IF jsonb_typeof(p_rules->'abstention') IS DISTINCT FROM 'boolean'
 THEN PERFORM public.cq_refuse('ELECT_METHOD_ABSTENTION'); END IF;
 IF (p_rules->>'threshold_base' IN ('VOTES_CAST','PARTICIPANTS','ELIGIBLE')) IS DISTINCT FROM true
 THEN PERFORM public.cq_refuse('ELECT_METHOD_BASE'); END IF;
 IF p_rules->>'tie' IS DISTINCT FROM 'NOT_QUALIFIED'
 THEN PERFORM public.cq_refuse('ELECT_METHOD_TIE'); END IF;
 IF p_changes ? 'method' AND p_changes->>'method' IS DISTINCT FROM 'SINGLE_SEAT'
 THEN PERFORM public.cq_refuse('ELECT_METHOD_COUNT'); END IF;
 -- Keep the existing CQ validators, with a distinct refusal for each scope.
 BEGIN
   PERFORM public.cq_check_values(p_rules||'{"reporting_scope":"ENFORCED","threshold":"MAJORITY","numerator":null,"denominator":null}'::jsonb,
       p_cut,'CQPT',coalesce((p_rules->>'waive_minimum')::boolean,false));
 EXCEPTION WHEN integrity_constraint_violation OR invalid_text_representation OR numeric_value_out_of_range THEN
   PERFORM public.cq_refuse('ELECT_METHOD_QUORUM');
 END;
 BEGIN
   PERFORM public.cq_check_values(p_rules||'{"reporting_scope":"ENFORCED","quorum_form":"MAJORITY","quorum_value":null}'::jsonb,
       p_cut,'CQT',coalesce((p_rules->>'waive_minimum')::boolean,false));
 EXCEPTION WHEN integrity_constraint_violation OR invalid_text_representation OR numeric_value_out_of_range THEN
   PERFORM public.cq_refuse('ELECT_METHOD_THRESHOLD');
 END;
END $$;
"""
