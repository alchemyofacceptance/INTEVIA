"""Install the two rule-composed Circle vacancy events and their storage proofs."""

from importlib import import_module

from django.db import migrations

episode = import_module("core.migrations.0105_circle_vacancy_episode")

S015_APPEND_EVENT_OLD = """CREATE OR REPLACE FUNCTION public.s015_append_event(token text, anchor bigint, action_name text, outcome text, at_time timestamp with time zone, occurred timestamp with time zone, received timestamp with time zone, actor bigint, basis bigint, decision text, evidence text, request_key text, payload text, metadata jsonb, deadline timestamp with time zone DEFAULT NULL::timestamp with time zone, command_event uuid DEFAULT NULL::uuid, supplied_head bigint DEFAULT NULL::bigint, supplied_sequence integer DEFAULT NULL::integer, access_epoch bigint DEFAULT NULL::bigint, command_rule text DEFAULT NULL::text, command_key text DEFAULT NULL::text)
 RETURNS bigint
 LANGUAGE plpgsql
AS $function$
DECLARE
 is_command boolean := command_event IS NOT NULL;
 event uuid := coalesce(command_event,gen_random_uuid());
 table_name text; fk text; col text; previous_id bigint; previous_seq integer; previous_l1 text; prior text;
 rule_ref text; lineage text; values_ jsonb; cols text; id_ bigint;
BEGIN
 SELECT m.event_table,m.fk_col,m.state_col INTO STRICT table_name,fk,col
 FROM public.s015_anchor_map() m WHERE m.anchor_token=token AND (
    (is_command AND token IN ('core_organismmembership','core_essentialcoveragecase','core_circle','core_contextualroleassignment','core_livingorganism','core_membershipcondition','core_authoritybasis','core_determinationcontestcase'))
 OR (NOT is_command AND token IN ('core_livingorganism','core_essentialcoveragecase','core_restrictedcontinuitycase')));

 IF is_command THEN
    -- Keep the caller's checked predecessor, even for a stale/empty head.
    -- The installed chain guards still decide whether that continuation is lawful.
    previous_id:=supplied_head; previous_seq:=supplied_sequence;
    IF supplied_head IS NOT NULL THEN
       EXECUTE format('SELECT l1_commitment FROM public.%I WHERE id=$1',table_name)
       INTO STRICT previous_l1 USING supplied_head;
    END IF;
    rule_ref:=command_rule;
    lineage:=public.c2_target(format('{"event":"%s","request":%s}',event,to_json(payload)));
    IF EXISTS (SELECT FROM jsonb_object_keys(metadata) k
               WHERE NOT (
       (token='core_authoritybasis' AND k='kind') OR
       (token='core_organismmembership' AND k='reason_class') OR
       (token='core_essentialcoveragecase' AND k IN ('roster_version','evaluated_state_at','evaluated_known_at','determiner_capacity_reference')))) THEN
       RAISE EXCEPTION 'Unexpected command chain metadata'; END IF;
 ELSE
    EXECUTE format('SELECT id,sequence,l1_commitment FROM public.%I WHERE %I=$1 ORDER BY sequence DESC LIMIT 1',table_name,fk)
    INTO previous_id,previous_seq,previous_l1 USING anchor;
    rule_ref:='C0:G2:C2';
    lineage:=encode(sha256(convert_to(event::text||':'||payload,'UTF8')),'hex');
    IF EXISTS(SELECT FROM jsonb_object_keys(metadata) k WHERE k NOT IN
       ('roster_version','evaluated_state_at','evaluated_known_at','determiner_capacity_reference',
        'permitted_measures_reference','prohibited_effects_reference','triggering_assessment_id','accountable_actor_id')) THEN
       RAISE EXCEPTION 'C2 chain metadata is invalid'; END IF;
 END IF;
 IF NOT is_command OR supplied_head IS NOT NULL THEN
    EXECUTE format('SELECT %I FROM public.%I WHERE %I=$1 AND (effective_at,occurred_at,received_at)<=($2,$3,$4)
        ORDER BY effective_at DESC,occurred_at DESC,received_at DESC,recorded_at DESC,sequence DESC,event_uuid DESC LIMIT 1',
        col,table_name,fk) INTO prior USING anchor,at_time,occurred,received;
 END IF;


 IF token = 'core_circle' AND action_name='CREATE_DORMANT' THEN
    IF action_name IS DISTINCT FROM 'CREATE_DORMANT' OR outcome IS DISTINCT FROM 'DORMANT'
       OR supplied_head IS NOT NULL OR supplied_sequence IS NOT NULL OR command_rule IS NOT NULL
       OR access_epoch IS NOT NULL OR deadline IS NOT NULL OR actor IS NULL THEN
       RAISE EXCEPTION 'Invalid institutional Circle creation envelope';
    END IF;
    SELECT encode(sha256(convert_to(format(
      '{"action":"CREATE_DORMANT_CIRCLE","event_uuid":"%s","fingerprint":"%s","subject_uuid":"%s"}',
      event,payload,c.circle_uuid),'UTF8')),'hex') INTO STRICT lineage
    FROM public.core_circle c WHERE c.id=anchor;
 END IF;


 IF is_command AND token IN ('core_organismmembership','core_essentialcoveragecase') AND (
    command_rule IS NOT NULL OR NOT EXISTS(SELECT FROM public.core_rootgenesis g JOIN public.core_livingorganism lo
     ON lo.organism_id=g.organism_uuid WHERE g.insert_xid=pg_current_xact_id() AND g.decision_reference=decision
     AND g.founder_id=actor AND g.basis_id=basis AND g.request_fingerprint=payload AND g.effective_at=at_time
     AND ((token='core_organismmembership' AND EXISTS(SELECT FROM public.core_organismmembership m
            WHERE m.id=anchor AND m.living_organism_id=lo.id AND m.identity_id=actor)
            AND action_name IN ('OPEN','ACTIVATE') AND metadata->>'reason_class'='FOUNDING')
       OR (token='core_essentialcoveragecase' AND EXISTS(SELECT FROM public.core_essentialcoveragecase c
            WHERE c.id=anchor AND c.living_organism_id=lo.id) AND action_name='ASSESS' AND outcome='SATISFIED')))) THEN
    RAISE EXCEPTION 'R_GENESIS_WRITER_SOURCE'; END IF;

 IF token='core_contextualroleassignment' AND action_name='ASSIGN'
    AND public.elect_recognition_office(anchor,command_event,actor,decision,at_time,deadline) THEN
    IF command_rule IS NOT NULL OR outcome IS DISTINCT FROM 'ACTIVE' OR evidence IS DISTINCT FROM 'elect-recognised:'||event
      THEN PERFORM public.cq_refuse('ELECT_RECOGNISER'); END IF;
 ELSIF token='core_contextualroleassignment' AND action_name='ASSIGN'
    AND public.elect_office_source(anchor,command_event,actor,decision,at_time,deadline) THEN
    IF command_rule IS NOT NULL OR outcome<>'ACTIVE' OR evidence NOT LIKE 'elect-result:%' THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
 ELSIF token='core_contextualroleassignment' AND action_name='ASSIGN' AND EXISTS(
    SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
    WHERE a.id=anchor AND a.circle_id IS NULL AND d.scope='LIVING_ORGANISM'
    AND public.r_genesis_context(d.living_organism_id,decision,actor,at_time)) THEN
    IF command_rule IS NOT NULL OR access_epoch IS NULL OR deadline IS NOT NULL THEN
       RAISE EXCEPTION 'R_GENESIS_WRITER_OFFICE'; END IF;
 ELSIF token='core_contextualroleassignment' AND action_name IN ('PROPOSE','ASSIGN') THEN
    IF command_rule IS NOT NULL OR NOT EXISTS (
       SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d
       ON d.id=a.role_definition_id WHERE a.id=anchor AND a.circle_id IS NOT NULL
       AND d.scope='CIRCLE' AND d.code='COORDINATOR') THEN
       RAISE EXCEPTION 'Invalid Circle Coordinator envelope';
    END IF;
 ELSIF token='core_contextualroleassignment' AND starts_with(command_rule,'c108-stand-down:') THEN
    IF NOT is_command OR action_name IS DISTINCT FROM 'END' OR outcome IS DISTINCT FROM 'ENDED'
    OR evidence IS DISTINCT FROM command_rule OR NOT public.c108_result_child(evidence,'offices',anchor)
    THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;

    IF EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent h
      WHERE h.id=supplied_head AND h.assignment_id=anchor AND h.effective_at>at_time AND h.resulting_state='ENDED') THEN
      SELECT public.s015_reduce_projection(token,coalesce(array_agg(e.id ORDER BY e.effective_at,e.occurred_at,
        e.received_at,e.recorded_at,e.sequence,e.event_uuid),'{}'::bigint[])) INTO prior
      FROM public.core_contextualroleassignmentstateevent e
      WHERE e.assignment_id=anchor AND e.sequence<=supplied_sequence
        AND (e.effective_at,e.occurred_at,e.received_at)<=(at_time,occurred,received);
    END IF;
 ELSIF token='core_contextualroleassignment' AND command_rule='ELECT:SUCCESSOR_LEFT' THEN
    -- The assignment input guard independently binds the complete row to the
    -- exact departure and predecessor. Other uses of this envelope refuse.
    IF NOT is_command OR action_name IS DISTINCT FROM 'END' OR outcome IS DISTINCT FROM 'ENDED'
      THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
 ELSIF token='core_contextualroleassignment' AND command_rule='ELECT:TERM_END' THEN
    IF NOT is_command OR action_name IS DISTINCT FROM 'END' OR outcome IS DISTINCT FROM 'ENDED'
       OR NOT EXISTS(SELECT FROM public.elect_due_office(anchor,public.intevia_clock_statement()) q WHERE q.due=at_time)
    THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
 ELSIF token='core_contextualroleassignment' AND (
     action_name IS DISTINCT FROM 'END' OR outcome IS DISTINCT FROM 'ENDED' OR command_rule IS NULL
     OR command_rule NOT IN ('CIRCLES:G2:U3','CIRCLES:G2:SELF_LEAVE')) THEN
    RAISE EXCEPTION 'Unsupported scoped office envelope';
 END IF;
 IF token='core_circle' AND action_name NOT IN ('CREATE_DORMANT','LEAVE_CIRCLE','OPEN_WORK','CLAIM_WORK','ACTIVATE','DISSOLVE_CIRCLE','STAND_DOWN') THEN
    RAISE EXCEPTION 'Unsupported Circle envelope';
 END IF;
 values_:=jsonb_build_object(fk,anchor,'event_uuid',event,'sequence',coalesce(previous_seq,0)+1,
    'predecessor_id',previous_id,'predecessor_sequence',previous_seq,'action',action_name,'prior_state',prior,col,outcome,
    'actor_state',CASE WHEN rule_ref IS NULL THEN 'PARTY' ELSE 'NONE' END,
    'actor_id',CASE WHEN rule_ref IS NULL THEN actor END,
    'actor_access_epoch',CASE WHEN rule_ref IS NULL THEN access_epoch END,
    'actor_capacity',CASE WHEN rule_ref IS NULL THEN 'STANDING' ELSE 'NA_NO_PARTY' END,
    'enterer_identity_id',actor,'entry_mode',CASE WHEN rule_ref IS NULL THEN 'TRANSPORT' ELSE 'COMPOSED' END,
    'composing_rule_state',CASE WHEN rule_ref IS NULL THEN 'NA_TRANSPORT' ELSE 'REFERENCE' END,
    'composing_rule_reference',rule_ref,'composer_state',CASE WHEN rule_ref IS NULL THEN 'NA_TRANSPORT' ELSE 'REFERENCE' END,
    'composer_identity_id',CASE WHEN rule_ref IS NOT NULL THEN actor END,
    'effective_until_state',CASE WHEN deadline IS NULL THEN 'NONE' ELSE 'FIXED' END,'effective_until',deadline,
    'authority_basis_id',basis,'authority_decision_reference',decision,'evidence_reference',evidence,
    'request_reference',request_key,'idempotency_key',CASE WHEN is_command THEN command_key ELSE request_key END,
    'payload_fingerprint',payload,'lineage_reference','s015l1:'||lineage,
    'temporal_basis_kind',CASE WHEN at_time<received THEN 'RETROSPECTIVE' WHEN at_time>received THEN 'PROSPECTIVE' END,
    'temporal_basis_reference',CASE WHEN at_time<>received THEN 's015tb1:'||payload END,
    'effective_at',at_time,'occurred_at',occurred,'received_at',received,'predecessor_l1_commitment',previous_l1,
    'parts_commitment',public.s015_0022_digest('s015p1',public.s015_0022_parts_preimage(table_name,event)))||metadata;

 IF token = 'core_circle' AND action_name='CREATE_DORMANT' THEN
    values_ := values_ || jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL);
 END IF;
 IF token='core_contextualroleassignment' THEN
    IF action_name='ASSIGN' AND EXISTS(SELECT FROM public.core_contextualroleassignment a WHERE a.id=anchor AND a.circle_id IS NOT NULL) THEN
       values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
    ELSIF action_name='PROPOSE' THEN
       values_:=values_||jsonb_build_object('effective_until_state','NOT_ESTABLISHED');
    END IF;
 END IF;

 IF token='core_circle' AND action_name='ACTIVATE' THEN
    IF command_rule IS NOT NULL OR access_epoch IS NOT NULL OR deadline IS NOT NULL THEN
       RAISE EXCEPTION 'Invalid institutional activation envelope'; END IF;
    values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF is_command AND token='core_livingorganism' AND action_name='ADOPT_LAW' THEN
   IF command_rule IS NOT NULL OR deadline IS NOT NULL OR at_time IS DISTINCT FROM public.intevia_clock_statement()
     OR basis IS NULL OR actor IS NULL OR prior IS DISTINCT FROM outcome THEN
     RAISE EXCEPTION 'LAW requires same-state institutional transport at execution' USING ERRCODE='23514';
   END IF;
   values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF is_command AND token='core_livingorganism' AND action_name='RECORD_GUIDING_INTENTION' THEN
   IF command_rule IS NOT NULL OR deadline IS NOT NULL OR at_time IS DISTINCT FROM public.intevia_clock_statement()
     OR basis IS NULL OR actor IS NULL OR prior IS DISTINCT FROM outcome THEN
     RAISE EXCEPTION 'GI requires same-state institutional transport at execution' USING ERRCODE='23514';
   END IF;
   values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF is_command AND token='core_livingorganism' AND action_name='RECORD_DISCUSSION' THEN
   IF command_rule IS NOT NULL OR deadline IS NOT NULL OR at_time IS DISTINCT FROM public.intevia_clock_statement()
     OR basis IS NULL OR actor IS NULL OR prior IS DISTINCT FROM outcome THEN
     RAISE EXCEPTION 'DISC requires same-state institutional transport at execution' USING ERRCODE='23514';
   END IF;
   values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF is_command AND token='core_livingorganism' AND action_name='RECORD_DISCUSSION_POST' THEN
   IF command_rule IS NOT NULL OR deadline IS NOT NULL OR at_time IS DISTINCT FROM public.intevia_clock_statement()
     OR basis IS NULL OR actor IS NULL OR prior IS DISTINCT FROM outcome THEN
     RAISE EXCEPTION 'DISC requires same-state institutional transport at execution' USING ERRCODE='23514';
   END IF;
   values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF is_command AND token='core_livingorganism' AND action_name='RECORD_CQ' THEN
   IF command_rule IS NOT NULL OR deadline IS NOT NULL OR at_time IS DISTINCT FROM public.intevia_clock_statement()
     OR basis IS NULL OR actor IS NULL OR prior IS DISTINCT FROM outcome THEN
     RAISE EXCEPTION 'DISC requires same-state institutional transport at execution' USING ERRCODE='23514';
   END IF;
   values_:=values_||jsonb_build_object('actor_state','LIVING_ORGANISM','actor_id',NULL,'actor_access_epoch',NULL);
 END IF;

 IF token='core_contextualroleassignment' AND action_name='ASSIGN' AND (public.elect_office_source(anchor,command_event,actor,decision,at_time,deadline)
   OR public.elect_recognition_office(anchor,command_event,actor,decision,at_time,deadline)) THEN
   values_:=values_||'{"actor_state":"LIVING_ORGANISM","actor_id":null,"actor_access_epoch":null}'::jsonb;
 END IF;
 SELECT string_agg(quote_ident(k),',' ORDER BY k) INTO cols FROM jsonb_object_keys(values_) AS k;
 EXECUTE format('INSERT INTO public.%I (%s) SELECT %s FROM jsonb_populate_record(NULL::public.%I,$1) RETURNING id',
                table_name,cols,cols,table_name) INTO id_ USING values_;
 IF token='core_livingorganism' THEN
    PERFORM public.s015_refresh_governed_cache(token,anchor,greatest(
        (SELECT current_state_effective_at FROM public.core_livingorganism WHERE id=anchor),
        least(at_time,public.intevia_clock_instant())),public.intevia_clock_instant());
 END IF;
 RETURN id_;
END $function$
"""

S015_APPEND_EVENT_NEW = S015_APPEND_EVENT_OLD.replace(
    ' IF NOT is_command OR supplied_head IS NOT NULL THEN',
    """ IF token='core_circle' AND supplied_head IS NOT NULL AND EXISTS (
       SELECT FROM public.core_circlestateevent d WHERE d.circle_id=anchor
       AND d.action='BECOME_DORMANT' AND d.effective_at<=at_time AND d.sequence<=supplied_sequence) THEN
    SELECT public.s015_reduce_projection(token,coalesce(array_agg(e.id ORDER BY e.effective_at,e.occurred_at,
           e.received_at,e.recorded_at,e.sequence,e.event_uuid),'{}'::bigint[])) INTO prior
    FROM public.core_circlestateevent e WHERE e.circle_id=anchor AND e.sequence<=supplied_sequence
      AND (e.effective_at,e.occurred_at,e.received_at)<=(at_time,occurred,received);
 ELSIF NOT is_command OR supplied_head IS NOT NULL THEN""",
).replace(
    "'DISSOLVE_CIRCLE','STAND_DOWN') THEN",
    """'DISSOLVE_CIRCLE','STAND_DOWN','OPEN_VACANCY','BECOME_DORMANT') THEN""",
)
CIRCLE_GUARD_ACTIVE_EVENT_OLD = """CREATE OR REPLACE FUNCTION public.circle_guard_active_event()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE problem text; previous public.core_circlestateevent; owner_basis bigint; last_exit record; executed timestamptz;
BEGIN
 IF NEW.action='STAND_DOWN' THEN
   IF NEW.prior_state IS DISTINCT FROM 'ACTIVE' OR NEW.resulting_state IS DISTINCT FROM 'DORMANT'
     OR NEW.event_uuid IS DISTINCT FROM public.c108_executing_result(NEW.evidence_reference)
     OR NEW.evidence_reference IS DISTINCT FROM 'c108-stand-down:'||NEW.event_uuid
     OR NEW.composing_rule_reference IS DISTINCT FROM NEW.evidence_reference
     OR NEW.actor_state IS DISTINCT FROM 'NONE' OR NEW.entry_mode IS DISTINCT FROM 'COMPOSED'
     OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
     OR NOT EXISTS(SELECT FROM public.core_cqact i JOIN public.core_livingorganism lo ON lo.id=i.organism_id
       WHERE i.operation='IMPLEMENT' AND i.execution_xid=pg_current_xact_id() AND i.request->>'result'=NEW.event_uuid::text
       AND i.facts#>>'{domain_plan,circle_id}'=NEW.circle_id::text
       AND i.actor_id=NEW.enterer_identity_id AND i.fingerprint=NEW.payload_fingerprint
       AND NEW.authority_decision_reference=public.disc_decision(i.authority_source,i.fingerprint)
       AND NEW.authority_basis_id=lo.founding_authority_basis_id AND NEW.effective_until IS NULL
       AND (NEW.effective_at,NEW.occurred_at,NEW.received_at)=(i.effective_at,i.received_at,i.received_at)
       AND NEW.request_reference=NEW.evidence_reference AND NEW.idempotency_key=NEW.evidence_reference)
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN NEW;
 END IF;
 IF (NEW.action='DISSOLVE_CIRCLE' OR NEW.prior_state='CLOSED' OR NEW.resulting_state='CLOSED')
    AND EXISTS(SELECT FROM public.core_circle c WHERE c.id=NEW.circle_id AND c.kind='GENERIC'
       AND EXISTS(SELECT FROM public.core_circlestateevent born WHERE born.circle_id=c.id AND born.action='ACTIVATE')) THEN
    SELECT * INTO last_exit FROM public.circle_last_ending(NEW.circle_id,public.intevia_clock_instant());
    IF NOT FOUND OR NEW.action<>'DISSOLVE_CIRCLE' OR NEW.resulting_state<>'CLOSED' OR NEW.prior_state IS DISTINCT FROM 'ACTIVE'
       OR NEW.actor_state<>'NONE' OR NEW.entry_mode<>'COMPOSED' OR NEW.composing_rule_reference IS DISTINCT FROM 'CIRCLES:L5:LAST_MEMBER'
       OR NEW.composer_identity_id IS DISTINCT FROM last_exit.enterer OR NEW.enterer_identity_id IS DISTINCT FROM last_exit.enterer
       OR (NEW.effective_at,NEW.occurred_at,NEW.received_at) IS DISTINCT FROM (last_exit.at_time,last_exit.occurred,last_exit.received)
       OR NEW.authority_basis_id IS DISTINCT FROM last_exit.basis OR NEW.effective_until_state<>'NONE'
       OR NEW.evidence_reference IS DISTINCT FROM 'last-place:'||last_exit.place||'|cause:'||last_exit.cause
       OR NEW.authority_decision_reference IS DISTINCT FROM 's015d1:'||public.c2_target(NEW.evidence_reference)
       OR NOT EXISTS(SELECT FROM public.core_circlestateevent h WHERE h.id=NEW.predecessor_id AND h.circle_id=NEW.circle_id
            AND h.resulting_state='ACTIVE' AND h.effective_at<=NEW.effective_at) THEN
       RAISE EXCEPTION 'CIRCLE_DISSOLUTION_SOURCE' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
 ELSIF NEW.action='DISSOLVE_CIRCLE' THEN
    RAISE EXCEPTION 'CIRCLE_DISSOLUTION_SCOPE' USING ERRCODE='23514';
 END IF;
 IF NEW.action='ACTIVATE' THEN
    IF NEW.resulting_state<>'ACTIVE' OR NEW.prior_state IS NULL OR NEW.prior_state NOT IN ('DORMANT','ELIGIBLE')
       OR NEW.sequence<=1 OR NEW.actor_state<>'LIVING_ORGANISM' OR NEW.actor_id IS NOT NULL
       OR NEW.actor_access_epoch IS NOT NULL OR NEW.actor_capacity<>'STANDING' OR NEW.entry_mode<>'TRANSPORT'
       OR NEW.effective_until_state<>'NONE' OR NEW.occurred_at<>NEW.effective_at
       OR NEW.received_at<>NEW.effective_at OR NEW.recorded_at IS NULL OR NEW.effective_at>NEW.recorded_at
       OR NOT EXISTS (SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
                      WHERE i.id=NEW.enterer_identity_id AND i.access_state='active' AND u.is_active)
       THEN RAISE EXCEPTION 'CIRCLE_G4_ENVELOPE' USING ERRCODE='23514'; END IF;
    SELECT lo.founding_authority_basis_id INTO STRICT owner_basis FROM public.core_circle c
      JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id WHERE c.id=NEW.circle_id;
    IF NEW.authority_basis_id IS DISTINCT FROM owner_basis THEN
       RAISE EXCEPTION 'CIRCLE_G4_AUTHORITY' USING ERRCODE='23514'; END IF;
    problem:=public.circle_activation_problem(NEW.circle_id,NEW.enterer_identity_id,NEW.event_uuid,
       NEW.authority_decision_reference,NEW.evidence_reference,NEW.effective_at,NEW.recorded_at,public.intevia_clock_instant());
    IF problem IS NOT NULL THEN RAISE EXCEPTION '%',problem USING ERRCODE='23514'; END IF;
 ELSIF NEW.prior_state='ACTIVE' OR NEW.resulting_state='ACTIVE' THEN
    SELECT * INTO previous FROM public.core_circlestateevent WHERE id=NEW.predecessor_id AND circle_id=NEW.circle_id;
    IF NEW.prior_state IS DISTINCT FROM 'ACTIVE' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'
       OR previous.resulting_state IS DISTINCT FROM 'ACTIVE'
       OR NEW.action NOT IN ('OPEN_WORK','LEAVE_CIRCLE','CLAIM_WORK') THEN
       RAISE EXCEPTION 'CIRCLE_G4_ACTIVE_EDGE' USING ERRCODE='23514'; END IF;
    IF NEW.action='CLAIM_WORK' AND EXISTS(SELECT FROM public.core_circle c WHERE c.id=NEW.circle_id AND c.kind='GENERIC') THEN
       executed:=public.intevia_clock_instant();
       IF NOT EXISTS(SELECT FROM public.core_circle c JOIN public.core_circlestateevent task ON task.circle_id=c.id
          WHERE c.id=NEW.circle_id AND task.action='OPEN_WORK' AND 'work:'||task.event_uuid=NEW.evidence_reference
          AND EXISTS(SELECT FROM public.circle_holders(c.id,executed,executed) holder WHERE holder.person=NEW.actor_id)
          AND public.circle_source(c.parent_organism_id,c.circle_uuid,'CIRCLE_SERVICE:'||(task.evidence_reference::jsonb->>1),
                                  NEW.actor_id,NEW.actor_id,executed,executed) IS NOT NULL) THEN
          RAISE EXCEPTION 'CIRCLE_CLAIM_EXECUTION_QUALIFICATION' USING ERRCODE='23514';
       END IF;
    END IF;
    -- Same-state work and departure require their existing specific 0059 sources.
 END IF;
 RETURN NEW;
END $function$
"""

CIRCLE_GUARD_ACTIVE_EVENT_NEW = CIRCLE_GUARD_ACTIVE_EVENT_OLD.replace(
    'DECLARE problem text;',
    """DECLARE episode_ record; problem text;""",
).replace(
    " IF NEW.action='STAND_DOWN' THEN",
    """ IF NEW.action IN ('OPEN_VACANCY','BECOME_DORMANT') THEN
   -- TIMED EVENTS: the two rule-composed records of a vacancy episode.
   SELECT * INTO episode_ FROM public.circle_episode(NEW.circle_id,NEW.recorded_at,NEW.recorded_at);
   IF episode_.cause IS NULL OR NEW.prior_state IS DISTINCT FROM 'ACTIVE'
     OR NEW.actor_state IS DISTINCT FROM 'NONE' OR NEW.entry_mode IS DISTINCT FROM 'COMPOSED'
     OR NEW.composing_rule_reference IS DISTINCT FROM 'CIRCLES:VACANCY'
     OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL OR NEW.actor_capacity IS DISTINCT FROM 'NA_NO_PARTY'
     OR NEW.composer_state IS DISTINCT FROM 'REFERENCE' OR NEW.composing_rule_state IS DISTINCT FROM 'REFERENCE'
     OR NEW.payload_fingerprint IS DISTINCT FROM public.c2_target(NEW.evidence_reference)
     OR NEW.temporal_basis_kind IS DISTINCT FROM (CASE WHEN NEW.effective_at<NEW.received_at THEN 'RETROSPECTIVE'::text END)
     OR NEW.temporal_basis_reference IS DISTINCT FROM (CASE WHEN NEW.effective_at<NEW.received_at THEN 's015tb1:'||NEW.payload_fingerprint END)
     OR NEW.effective_until_state IS DISTINCT FROM (CASE NEW.action WHEN 'OPEN_VACANCY' THEN 'FIXED'::text ELSE 'NONE'::text END)
     OR NEW.received_at IS DISTINCT FROM public.intevia_clock_statement()
     OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
     OR NEW.evidence_reference IS DISTINCT FROM 'vacancy:'||episode_.cause
     OR NEW.authority_decision_reference IS DISTINCT FROM 's015d1:'||public.c2_target(NEW.evidence_reference)
     OR NEW.request_reference IS DISTINCT FROM 'circle-vacancy:'||episode_.cause||':'||NEW.action
     OR NEW.idempotency_key IS DISTINCT FROM NEW.request_reference
     OR NEW.occurred_at IS DISTINCT FROM NEW.effective_at
     OR NEW.authority_basis_id IS DISTINCT FROM (SELECT lo.founding_authority_basis_id FROM public.core_circle c
          JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id WHERE c.id=NEW.circle_id)
     OR NOT EXISTS(SELECT FROM public.core_circlestateevent h WHERE h.id=NEW.predecessor_id
          AND h.circle_id=NEW.circle_id AND h.sequence=NEW.sequence-1 AND h.sequence=NEW.predecessor_sequence
          AND NOT EXISTS(SELECT FROM public.core_circlestateevent next WHERE next.circle_id=h.circle_id AND next.sequence>h.sequence))
     OR NOT EXISTS(SELECT FROM public.core_circle c WHERE c.id=NEW.circle_id AND
          public.disc_member_state(c.parent_organism_id,NEW.enterer_identity_id,NEW.received_at)='ACTIVE')
     OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
          WHERE i.id=NEW.enterer_identity_id AND i.access_state='active' AND u.is_active)
     OR EXISTS(SELECT FROM public.core_circlestateevent r WHERE r.circle_id=NEW.circle_id
          AND r.action=NEW.action AND r.evidence_reference=NEW.evidence_reference)
     OR (NEW.action='OPEN_VACANCY' AND (NEW.resulting_state IS DISTINCT FROM 'ACTIVE'
          OR NEW.effective_at IS DISTINCT FROM episode_.loss
          OR NEW.effective_until IS DISTINCT FROM episode_.deadline))
     OR (NEW.action='BECOME_DORMANT' AND (NEW.resulting_state IS DISTINCT FROM 'DORMANT'
          OR episode_.status IS DISTINCT FROM 'EXPIRED'
          OR NEW.effective_at IS DISTINCT FROM episode_.deadline OR NEW.effective_until IS NOT NULL
          OR NOT EXISTS(SELECT FROM public.core_circlestateevent r WHERE r.circle_id=NEW.circle_id
               AND r.action='OPEN_VACANCY' AND r.evidence_reference=NEW.evidence_reference)))
   THEN RAISE EXCEPTION 'CIRCLE_VACANCY_SOURCE' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 END IF;
 IF NEW.action='RESEAT' THEN
   -- This build has no human reseat writer. CB-4 supplies its positive ASSIGN proof.
   RAISE EXCEPTION 'CIRCLE_RESEAT_SOURCE' USING ERRCODE='23514';
 END IF;
 IF NEW.prior_state=NEW.resulting_state OR NEW.resulting_state='CLOSED' THEN
   IF NEW.resulting_state='CLOSED' THEN
     SELECT * INTO last_exit FROM public.circle_last_ending(NEW.circle_id,NEW.recorded_at);
     SELECT * INTO episode_ FROM public.circle_episode_at_exit(
         NEW.circle_id,NULL,coalesce(last_exit.at_time,NEW.recorded_at),NEW.recorded_at,last_exit.place);
   ELSE
     SELECT * INTO episode_ FROM public.circle_episode(NEW.circle_id,NEW.recorded_at,NEW.recorded_at);
   END IF;
   IF episode_.status='EXPIRED' AND NOT EXISTS (
       SELECT FROM public.core_circlestateevent r WHERE r.circle_id=NEW.circle_id
       AND r.action='BECOME_DORMANT' AND r.evidence_reference='vacancy:'||episode_.cause) THEN
     RAISE EXCEPTION 'CIRCLE_DORMANCY_OWED' USING ERRCODE='23514';
   END IF;
 END IF;
 IF NEW.action='STAND_DOWN' THEN""",
)
TE_ORIGIN_KEY_OLD = """CREATE OR REPLACE FUNCTION public.te_origin_key(p_table text)
 RETURNS text
 LANGUAGE sql
 IMMUTABLE
AS $function$
 SELECT CASE p_table
  WHEN 'core_contextualroleassignmentstateevent' THEN 'event_uuid'
  WHEN 'core_coverageassessment' THEN 'event_uuid'
  WHEN 'core_livingorganismevent' THEN 'event_uuid'
  WHEN 'core_electvacancy' THEN 'ending'
  WHEN 'core_cqact' THEN 'act_uuid'
  WHEN 'core_cmtmandateend' THEN 'mandate' END
$function$
"""

TE_ORIGIN_KEY_NEW = TE_ORIGIN_KEY_OLD.replace(
    "WHEN 'core_coverageassessment' THEN 'event_uuid'",
    """WHEN 'core_circlestateevent' THEN 'event_uuid'
  WHEN 'core_coverageassessment' THEN 'event_uuid'""",
)
TE_ORIGIN_APPLIES_OLD = """CREATE OR REPLACE FUNCTION public.te_origin_applies(p_table text, p_row jsonb)
 RETURNS boolean
 LANGUAGE sql
 IMMUTABLE
AS $function$
 SELECT COALESCE(CASE p_table
  WHEN 'core_contextualroleassignmentstateevent'
   THEN p_row->>'action'='END' AND p_row->>'evidence_reference' LIKE 'elect-expiry:%'
  WHEN 'core_cqact' THEN p_row->'authority_source'->>'kind'='RULE'
  WHEN 'core_livingorganismevent' THEN p_row->>'action' IN ('K2_DEACTIVATE','K2_DISSOLVE')
  WHEN 'core_coverageassessment' THEN true
  WHEN 'core_electvacancy' THEN true
  WHEN 'core_cmtmandateend' THEN true END,false)
$function$
"""

TE_ORIGIN_APPLIES_NEW = TE_ORIGIN_APPLIES_OLD.replace(
    "WHEN 'core_coverageassessment' THEN true",
    """WHEN 'core_circlestateevent' THEN p_row->>'action' IN ('OPEN_VACANCY','BECOME_DORMANT')
  WHEN 'core_coverageassessment' THEN true""",
)
GUARD_TRIGGER = "CREATE TRIGGER zz_circle_qualified_activation BEFORE INSERT ON core_circlestateevent FOR EACH ROW WHEN ((new.action::text = ANY (ARRAY['ACTIVATE'::character varying, 'DISSOLVE_CIRCLE'::character varying]::text[])) OR (new.resulting_state::text = ANY (ARRAY['ACTIVE'::character varying, 'CLOSED'::character varying]::text[])) OR (new.prior_state::text = ANY (ARRAY['ACTIVE'::character varying, 'CLOSED'::character varying]::text[]))) EXECUTE FUNCTION circle_guard_active_event()"
GUARD_TRIGGER_NEW = "CREATE TRIGGER zz_circle_qualified_activation BEFORE INSERT ON core_circlestateevent FOR EACH ROW WHEN ((new.action::text = ANY (ARRAY['ACTIVATE'::character varying, 'DISSOLVE_CIRCLE'::character varying, 'OPEN_VACANCY'::character varying, 'BECOME_DORMANT'::character varying, 'RESEAT'::character varying]::text[])) OR (new.resulting_state::text = ANY (ARRAY['ACTIVE'::character varying, 'CLOSED'::character varying]::text[])) OR (new.prior_state::text = ANY (ARRAY['ACTIVE'::character varying, 'CLOSED'::character varying]::text[])) OR new.prior_state::text = new.resulting_state::text) EXECUTE FUNCTION circle_guard_active_event()"
REFRESH_TRIGGER = "CREATE CONSTRAINT TRIGGER zz_cmt_change AFTER INSERT ON core_circlestateevent DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (new.resulting_state::text = 'CLOSED'::text) EXECUTE FUNCTION cmt_changed()"
REFRESH_TRIGGER_NEW = "CREATE CONSTRAINT TRIGGER zz_cmt_change AFTER INSERT ON core_circlestateevent DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN ((new.action::text = ANY (ARRAY['BECOME_DORMANT'::character varying, 'RESEAT'::character varying]::text[])) OR new.resulting_state::text = 'CLOSED'::text) EXECUTE FUNCTION cmt_changed()"
ORIGIN_TRIGGER = ("CREATE TRIGGER zz_te_origin BEFORE INSERT ON core_circlestateevent "
                  "FOR EACH ROW EXECUTE FUNCTION te_record_origin()")

# Only the closing guard passes the positively identified last place. All normal
# episode readers continue to require an eligible place at the requested cut.
EXIT_EPISODE = "public.circle_episode_at_exit(bigint,uuid,timestamptz,timestamptz,uuid)"
EXIT_DEFINITION = episode.CAUSE_DEFINITION.replace(
    "circle_episode_before_assignment(", "circle_episode_at_exit(",
).replace("known timestamp with time zone)", "known timestamp with time zone, exiting uuid)").replace(
    "AND public.circle_place_at(c,place.membership_id,at_time,known))",
    """AND (public.circle_place_at(c,place.membership_id,at_time,known)
                    OR (place.participation_uuid=exiting AND EXISTS (
                         SELECT FROM public.circle_last_ending(c,known) last_place
                         WHERE last_place.place=exiting AND last_place.at_time=at_time))))""",
)
BEFORE_ASSIGN_NEW = episode.CAUSE_DEFINITION.split("AS $function$")[0] + """AS $function$
 SELECT * FROM public.circle_episode_at_exit(c,incoming,at_time,known,NULL)
$function$
"""

OWED_SQL = """CREATE OR REPLACE FUNCTION public.circle_vacancy_owed(p_lo bigint, p_known timestamp with time zone)
 RETURNS TABLE(circle_id bigint, cause uuid, occurrence text, action_ text, due_at timestamp with time zone, deadline timestamp with time zone)
 LANGUAGE sql
 STABLE
AS $function$
 SELECT c.id,e.cause,o.occurrence,o.action_,o.due_at,e.deadline
 FROM public.core_circle c CROSS JOIN LATERAL public.circle_episode(c.id,p_known,p_known) e
 CROSS JOIN LATERAL (VALUES ('OPENED','OPEN_VACANCY',e.loss),('DORMANT','BECOME_DORMANT',e.deadline)) o(occurrence,action_,due_at)
 WHERE c.parent_organism_id=p_lo AND c.kind='GENERIC'
 AND NOT EXISTS(SELECT FROM public.core_circlestateevent r WHERE r.circle_id=c.id AND r.action=o.action_
     AND r.evidence_reference='vacancy:'||e.cause AND r.recorded_at<=p_known)
 AND (o.occurrence='OPENED' OR (e.status='EXPIRED' AND EXISTS(SELECT FROM public.core_circlestateevent r
     WHERE r.circle_id=c.id AND r.action='OPEN_VACANCY' AND r.evidence_reference='vacancy:'||e.cause AND r.recorded_at<=p_known)))
$function$
"""
WRITE_SQL = """CREATE OR REPLACE FUNCTION public.circle_record_vacancy(p_lo bigint, p_cause uuid, p_occurrence text, p_enterer bigint)
 RETURNS boolean
 LANGUAGE plpgsql
AS $function$
DECLARE cut_ timestamptz:=public.intevia_clock_statement(); owed record; head public.core_circlestateevent;
        basis bigint; ref text; key text; fp text;
BEGIN
 SELECT founding_authority_basis_id INTO STRICT basis FROM public.core_livingorganism WHERE id=p_lo FOR UPDATE;
 IF public.disc_member_state(p_lo,p_enterer,cut_) IS DISTINCT FROM 'ACTIVE' OR NOT EXISTS(
    SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=p_enterer AND i.access_state='active' AND u.is_active)
 THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 SELECT * INTO owed FROM public.circle_vacancy_owed(p_lo,cut_) o WHERE o.cause=p_cause AND o.occurrence=p_occurrence;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO STRICT head FROM public.core_circlestateevent WHERE circle_id=owed.circle_id ORDER BY sequence DESC LIMIT 1;
 ref:='vacancy:'||p_cause; key:='circle-vacancy:'||p_cause||':'||owed.action_; fp:=public.c2_target(ref);
 PERFORM public.s015_append_event('core_circle',owed.circle_id,owed.action_,
   CASE owed.occurrence WHEN 'OPENED' THEN 'ACTIVE' ELSE 'DORMANT' END,owed.due_at,owed.due_at,cut_,
   p_enterer,basis,'s015d1:'||fp,ref,key,fp,'{}',CASE owed.occurrence WHEN 'OPENED' THEN owed.deadline END,
   gen_random_uuid(),head.id,head.sequence,NULL,'CIRCLES:VACANCY',key);
 PERFORM public.s015_refresh_governed_cache('core_circle',owed.circle_id,cut_,public.intevia_clock_instant());
 RETURN true;
END $function$
"""
WRITER = "public.circle_record_vacancy(bigint,uuid,text,bigint)"
REGISTRY_OLD = """CREATE OR REPLACE FUNCTION public.te_consumers()
 RETURNS TABLE(consumer text, purpose text, ordinal integer, rule text, selector text, validator text, claim text)
 LANGUAGE sql
 IMMUTABLE
AS $function$
 SELECT v.consumer,v.purpose,v.ordinal,v.rule,v.selector,v.validator,v.claim FROM (VALUES
 ('ELECT','TERM_END',1,'ELECT:TERM_END','te_elect_term_due','te_elect_term_proof','te_elect_claim'),
 ('ELECT','RESIGNATION',2,'ELECT:RESIGNATION_COVERAGE','te_elect_resignation_due','te_elect_resignation_proof','te_elect_claim'),
 ('ELECT','VACANCY',3,'ELECT:VACANCY','te_elect_vacancy_due','te_elect_vacancy_proof','te_elect_claim'),
 ('CQ','PREPARATION_DUE',10,'HG:DISC2:G1:D4','te_cq_preparation_due','te_cq_proof','te_cq_claim'),
 ('CQ','RESPONSE_DUE',11,'HG:DISC2:D7','te_cq_response_due','te_cq_proof','te_cq_claim'),
 ('CMT','MANDATE_END',20,'CMT:MANDATE_END','te_cmt_end_due','te_cmt_end_proof','te_cmt_end_claim'),
 ('C2','NOTICE',30,'C0:G2:C2','te_c2_notice_due','te_c2_proof','te_c2_claim'),
 ('C2','PHASE',31,'HG:RC-1:K-2/H-4','te_c2_phase_due','te_c2_proof','te_c2_claim')
 ) v(consumer,purpose,ordinal,rule,selector,validator,claim) WHERE v.consumer IS NOT NULL
$function$
"""
REGISTRY_NEW = REGISTRY_OLD.replace(
    " ) v(consumer,purpose,ordinal,rule,selector,validator,claim)",
    """,('CIRCLE','VACANCY',32,'CIRCLES:VACANCY','te_circle_due','te_circle_proof','te_circle_claim')
 ) v(consumer,purpose,ordinal,rule,selector,validator,claim)""",
)
DUE_SQL = """CREATE OR REPLACE FUNCTION public.te_circle_due(p_lo bigint, p_known timestamp with time zone)
 RETURNS TABLE(source_ref text, occurrence text, due_at timestamp with time zone)
 LANGUAGE sql
 STABLE
AS $function$
 SELECT owed.cause::text,owed.occurrence,owed.due_at
 FROM public.circle_vacancy_owed(p_lo,p_known) owed
$function$
"""
PROOF_SQL = """CREATE OR REPLACE FUNCTION public.te_circle_proof(p_lo bigint, p_purpose text, p_source text, p_occurrence text, p_effect text)
 RETURNS TABLE(due_at timestamp with time zone, effect_at timestamp with time zone, enterer_id bigint, made_here boolean)
 LANGUAGE sql
 STABLE
AS $function$
 SELECT r.effective_at,r.recorded_at,r.enterer_identity_id,
        public.te_made_here('core_circlestateevent',r.event_uuid::text)
 FROM public.core_circlestateevent r
 JOIN public.core_circle c ON c.id=r.circle_id AND c.parent_organism_id=p_lo
 JOIN public.core_circlestateevent opening ON opening.circle_id=r.circle_id
    AND opening.action='OPEN_VACANCY' AND opening.evidence_reference=r.evidence_reference
 JOIN public.core_contextualroleassignmentstateevent ending ON ending.event_uuid::text=p_source AND ending.action='END'
 JOIN public.core_contextualroleassignment a ON a.id=ending.assignment_id AND a.circle_id=c.id
 WHERE p_purpose='VACANCY' AND r.event_uuid::text=p_effect AND r.evidence_reference='vacancy:'||p_source
    AND r.composing_rule_reference='CIRCLES:VACANCY' AND opening.effective_at=ending.effective_at
    AND ((p_occurrence='OPENED' AND r.id=opening.id)
      OR (p_occurrence='DORMANT' AND r.action='BECOME_DORMANT' AND r.effective_at=opening.effective_until))
$function$
"""
CLAIM_SQL = """CREATE OR REPLACE FUNCTION public.te_circle_claim(p_lo bigint, p_purpose text, p_source text, p_occurrence text, p_phase text)
 RETURNS boolean
 LANGUAGE plpgsql
AS $function$
DECLARE target bigint; predecessor bigint; founder bigint; basis bigint;
BEGIN
 IF p_purpose IS DISTINCT FROM 'VACANCY' OR p_occurrence IS NULL OR p_occurrence NOT IN ('OPENED','DORMANT')
 THEN RETURN false; END IF;
 IF p_phase='BEFORE_LO' THEN RETURN true; END IF;
 IF p_phase IS DISTINCT FROM 'UNDER_LO' THEN RETURN false; END IF;

 -- The selector has just run again under this LO. Try its one specific anchor,
 -- then the recorded head that the new event will reference as its predecessor.
 SELECT c.id,c.head_event_id INTO target,predecessor
 FROM public.core_contextualroleassignmentstateevent e
 JOIN public.core_contextualroleassignment a ON a.id=e.assignment_id
 JOIN public.core_circle c ON c.id=a.circle_id
 WHERE e.event_uuid::text=p_source AND e.action='END' AND c.parent_organism_id=p_lo
 FOR UPDATE OF c SKIP LOCKED;
 IF NOT FOUND OR predecessor IS NULL THEN RETURN false; END IF;
 PERFORM FROM public.core_circlestateevent e WHERE e.id=predecessor AND e.circle_id=target
 FOR KEY SHARE OF e SKIP LOCKED;
 IF NOT FOUND THEN RETURN false; END IF;

 SELECT r.identity,r.basis INTO STRICT founder,basis FROM public.te_lo_references(p_lo) r;
 IF NOT public.te_reference_claim(ARRAY[founder],ARRAY[basis]) THEN RETURN false; END IF;
 -- The carrier's identity and credential, and the LO itself, are already held.
 IF p_occurrence='DORMANT' THEN RETURN public.te_refresh_claim(p_lo); END IF;
 RETURN true;
END $function$
"""
ADDITIONS = ((EXIT_EPISODE, EXIT_DEFINITION), ("public.circle_vacancy_owed(bigint,timestamptz)", OWED_SQL),
             (WRITER, WRITE_SQL), ("public.te_circle_due(bigint,timestamptz)", DUE_SQL),
             ("public.te_circle_proof(bigint,text,text,text,text)", PROOF_SQL),
             ("public.te_circle_claim(bigint,text,text,text,text)", CLAIM_SQL))
REPLACEMENTS = (
    ("public.te_consumers()", REGISTRY_OLD, REGISTRY_NEW),
    (episode.BEFORE_ASSIGN, episode.CAUSE_DEFINITION, BEFORE_ASSIGN_NEW),
    ("public.s015_append_event(text,bigint,text,text,timestamp with time zone,timestamp with time zone,timestamp with time zone,bigint,bigint,text,text,text,text,jsonb,timestamp with time zone,uuid,bigint,integer,bigint,text,text)", S015_APPEND_EVENT_OLD, S015_APPEND_EVENT_NEW),
    ("public.circle_guard_active_event()", CIRCLE_GUARD_ACTIVE_EVENT_OLD, CIRCLE_GUARD_ACTIVE_EVENT_NEW),
    ("public.te_origin_key(text)", TE_ORIGIN_KEY_OLD, TE_ORIGIN_KEY_NEW),
    ("public.te_origin_applies(text,jsonb)", TE_ORIGIN_APPLIES_OLD, TE_ORIGIN_APPLIES_NEW),
)
TABLE = "public.core_circlestateevent"


# DDL to execute. PG17 deparses these IN lists to the exact pins checked by expect().
GUARD_SOURCE = (
    "CREATE TRIGGER zz_circle_qualified_activation BEFORE INSERT ON public.core_circlestateevent "
    "FOR EACH ROW WHEN (NEW.action IN ('ACTIVATE','DISSOLVE_CIRCLE') "
    "OR NEW.resulting_state IN ('ACTIVE','CLOSED') "
    "OR NEW.prior_state IN ('ACTIVE','CLOSED')) "
    "EXECUTE FUNCTION public.circle_guard_active_event()"
)
GUARD_SOURCE_NEW = (
    "CREATE TRIGGER zz_circle_qualified_activation BEFORE INSERT ON public.core_circlestateevent "
    "FOR EACH ROW WHEN (NEW.action IN ('ACTIVATE','DISSOLVE_CIRCLE','OPEN_VACANCY','BECOME_DORMANT','RESEAT') "
    "OR NEW.resulting_state IN ('ACTIVE','CLOSED') "
    "OR NEW.prior_state IN ('ACTIVE','CLOSED') "
    "OR NEW.prior_state = NEW.resulting_state) "
    "EXECUTE FUNCTION public.circle_guard_active_event()"
)
REFRESH_SOURCE_NEW = (
    "CREATE CONSTRAINT TRIGGER zz_cmt_change AFTER INSERT ON public.core_circlestateevent DEFERRABLE INITIALLY DEFERRED "
    "FOR EACH ROW WHEN (NEW.action IN ('BECOME_DORMANT','RESEAT') "
    "OR NEW.resulting_state = 'CLOSED') "
    "EXECUTE FUNCTION public.cmt_changed()"
)


def trigger(cursor, name):
    cursor.execute("SELECT pg_catalog.pg_get_triggerdef(oid,true) FROM pg_catalog.pg_trigger "
                   "WHERE tgrelid=%s::regclass AND tgname=%s", [TABLE, name])
    row = cursor.fetchone()
    return row[0] if row else None


def expect(cursor, installed):
    for signature, before, after in REPLACEMENTS:
        if episode.definition(cursor, signature) != (after if installed else before):
            raise RuntimeError("0106 installed definition differs: " + signature)
    for signature, sql in ADDITIONS:
        if episode.definition(cursor, signature) != (sql if installed else None):
            raise RuntimeError("0106 installed definition differs: " + signature)
    for name, wanted in (("zz_circle_qualified_activation", GUARD_TRIGGER_NEW if installed else GUARD_TRIGGER),
                         ("zz_cmt_change", REFRESH_TRIGGER_NEW if installed else REFRESH_TRIGGER),
                         ("zz_te_origin", ORIGIN_TRIGGER if installed else None)):
        if trigger(cursor, name) != wanted:
            raise RuntimeError("0106 installed trigger differs: " + name)


def change(schema_editor, forward):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("LOCK TABLE public.core_circlestateevent,public.core_tereceipt,public.core_tepace IN SHARE ROW EXCLUSIVE MODE")
        expect(cursor, not forward)
        if forward:
            for _, sql in ADDITIONS:
                cursor.execute(sql)
        else:
            cursor.execute("SELECT EXISTS(SELECT FROM public.core_circlestateevent "
                           "WHERE action IN ('OPEN_VACANCY','BECOME_DORMANT'))")
            if cursor.fetchone()[0]:
                raise RuntimeError("0106 retains recorded Circle vacancy events.")
            cursor.execute("SELECT EXISTS(SELECT FROM public.core_tereceipt WHERE consumer='CIRCLE')")
            if cursor.fetchone()[0]:
                raise RuntimeError("0106 retains recorded CIRCLE receipts.")
            cursor.execute("DELETE FROM public.core_tepace WHERE consumer='CIRCLE'")
            cursor.execute("DROP TRIGGER zz_te_origin ON " + TABLE)
        for _, before, after in REPLACEMENTS:
            cursor.execute(after if forward else before)
        cursor.execute("DROP TRIGGER zz_circle_qualified_activation ON " + TABLE)
        cursor.execute(GUARD_SOURCE_NEW if forward else GUARD_SOURCE)
        cursor.execute("DROP TRIGGER zz_cmt_change ON " + TABLE)
        cursor.execute(REFRESH_SOURCE_NEW if forward else REFRESH_TRIGGER)
        if forward:
            cursor.execute(ORIGIN_TRIGGER)
        else:
            for signature, _ in reversed(ADDITIONS):
                cursor.execute("DROP FUNCTION " + signature)
        expect(cursor, forward)


def install(apps, schema_editor):
    change(schema_editor, True)


def uninstall(apps, schema_editor):
    change(schema_editor, False)


class Migration(migrations.Migration):
    dependencies = (("core", "0105_circle_vacancy_episode"),)
    operations = (migrations.RunPython(install, uninstall),)
