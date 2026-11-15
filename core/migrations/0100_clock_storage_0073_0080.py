"""The INTEVIA Clock, storage 4 of 7: the installed functions of migrations 0073 to 0080 (CK-2c).

20 installed functions read the time through PostgreSQL's own calls. This migration replaces
each under G2-4 with a successor that differs from it only where a time call becomes the
INTEVIA Clock's face with the same meaning (30 calls: 30 statement start, 0 instant,
0 transaction start):

    statement_timestamp()    public.intevia_clock_statement()
    clock_timestamp()        public.intevia_clock_instant()
    transaction_timestamp()  public.intevia_clock_transaction()

In production each face is exactly its built-in (migration 0096), so nothing changes there.
In an owned test database the faces are the ones a test may move.

Each function is held here twice, as landed and as it reads the clock. Forward: the installed
text must equal the landed text; it is replaced; the function's every other attribute must be
unchanged. Reverse: the same, the other way. ``core.migrations._clock_conversion`` has the step.
The two texts of a function are compared with the database character for character, so
neither is ever edited.
"""

from importlib import import_module

from django.db import migrations

clock = import_module("core.migrations._clock_conversion")

# public.discp_complete()
# Its landed text: 0073_discussion_posts.py (found there verbatim). Time calls moved: 1 statement.
DISCP_COMPLETE_LANDED = r"""
DECLARE v_key uuid; v_act public.core_discpostact; v_expected integer; v_actual integer;
BEGIN
 IF TG_TABLE_NAME IN ('core_discthread','core_discpost') THEN v_key:=NEW.creation_act;
 ELSIF TG_TABLE_NAME='core_livingorganismevent' THEN v_key:=NEW.event_uuid;
 ELSIF TG_TABLE_NAME='core_discbody' THEN v_key:=NEW.version_uuid;
 ELSE v_key:=NEW.act_uuid; END IF;
 SELECT * INTO v_act FROM public.core_discpostact WHERE act_uuid=v_key;
 v_expected:=CASE WHEN v_act.operation IN ('START','REPLY','EDIT') THEN 1 ELSE 0 END;
 SELECT count(*) INTO v_actual FROM public.core_discpostversion v JOIN public.core_discbody b USING(version_uuid) WHERE v.act_uuid=v_key;
 IF v_act.act_uuid IS NULL OR v_actual<>v_expected
   OR (SELECT count(*) FROM public.core_discpost p WHERE p.creation_act=v_key)<>(CASE WHEN v_act.operation IN ('START','REPLY') THEN 1 ELSE 0 END)
   OR (SELECT count(*) FROM public.core_discthread t WHERE t.creation_act=v_key)<>(CASE WHEN v_act.operation='START' THEN 1 ELSE 0 END)
   OR NOT EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.event_uuid=v_key AND public.discp_event_matches(e))
   OR public.discp_source(v_act.payload,v_act.actor_id,statement_timestamp()) IS DISTINCT FROM v_act.authority_source
   OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
     WHERE i.id=v_act.actor_id AND i.access_epoch=v_act.actor_epoch AND i.access_state='active' AND u.is_active) THEN
   RAISE EXCEPTION 'DISC_COMPLETE' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END """
DISCP_COMPLETE_CLOCK = r"""
DECLARE v_key uuid; v_act public.core_discpostact; v_expected integer; v_actual integer;
BEGIN
 IF TG_TABLE_NAME IN ('core_discthread','core_discpost') THEN v_key:=NEW.creation_act;
 ELSIF TG_TABLE_NAME='core_livingorganismevent' THEN v_key:=NEW.event_uuid;
 ELSIF TG_TABLE_NAME='core_discbody' THEN v_key:=NEW.version_uuid;
 ELSE v_key:=NEW.act_uuid; END IF;
 SELECT * INTO v_act FROM public.core_discpostact WHERE act_uuid=v_key;
 v_expected:=CASE WHEN v_act.operation IN ('START','REPLY','EDIT') THEN 1 ELSE 0 END;
 SELECT count(*) INTO v_actual FROM public.core_discpostversion v JOIN public.core_discbody b USING(version_uuid) WHERE v.act_uuid=v_key;
 IF v_act.act_uuid IS NULL OR v_actual<>v_expected
   OR (SELECT count(*) FROM public.core_discpost p WHERE p.creation_act=v_key)<>(CASE WHEN v_act.operation IN ('START','REPLY') THEN 1 ELSE 0 END)
   OR (SELECT count(*) FROM public.core_discthread t WHERE t.creation_act=v_key)<>(CASE WHEN v_act.operation='START' THEN 1 ELSE 0 END)
   OR NOT EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.event_uuid=v_key AND public.discp_event_matches(e))
   OR public.discp_source(v_act.payload,v_act.actor_id,public.intevia_clock_statement()) IS DISTINCT FROM v_act.authority_source
   OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
     WHERE i.id=v_act.actor_id AND i.access_epoch=v_act.actor_epoch AND i.access_state='active' AND u.is_active) THEN
   RAISE EXCEPTION 'DISC_COMPLETE' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END """

# public.cq_edit_lock()
# Its landed text: 0074_cq_clarity.py (found there verbatim). Time calls moved: 2 statement.
CQ_EDIT_LOCK_LANDED = r"""
DECLARE h public.cq_phases;
BEGIN
 SELECT phase.* INTO h FROM public.core_discpost p JOIN public.cq_phases phase USING(thread_uuid)
 WHERE p.post_uuid::text=NEW.payload->>'post' AND p.post_number=1;
 IF h.phase='CLARITY' AND statement_timestamp()>=h.response_deadline THEN
   PERFORM public.cq_refuse('CQ_EDIT_DUE');
 ELSIF h.phase='CLARITY' AND statement_timestamp()>=h.deadline THEN
   PERFORM public.cq_refuse('CQ_WINDOW_LOCKED');
 ELSIF h.phase='CLOSED' OR EXISTS(SELECT FROM public.core_cqact a WHERE a.thread_uuid=h.thread_uuid AND a.operation='NO_BALLOT') THEN
   PERFORM public.cq_refuse('CQ_CLOSED_LOCKED');
 ELSIF EXISTS(SELECT FROM public.core_cqact a WHERE a.thread_uuid=h.thread_uuid AND a.operation='PROCEED') THEN
   PERFORM public.cq_refuse('CQ_LOCKED');
 ELSIF EXISTS(SELECT FROM public.core_cqact a WHERE a.thread_uuid=h.thread_uuid AND a.operation='OPEN_VOTE') THEN
   PERFORM public.cq_refuse('CQ_VOTE_LOCKED');
 END IF;
 RETURN NEW;
END """
CQ_EDIT_LOCK_CLOCK = r"""
DECLARE h public.cq_phases;
BEGIN
 SELECT phase.* INTO h FROM public.core_discpost p JOIN public.cq_phases phase USING(thread_uuid)
 WHERE p.post_uuid::text=NEW.payload->>'post' AND p.post_number=1;
 IF h.phase='CLARITY' AND public.intevia_clock_statement()>=h.response_deadline THEN
   PERFORM public.cq_refuse('CQ_EDIT_DUE');
 ELSIF h.phase='CLARITY' AND public.intevia_clock_statement()>=h.deadline THEN
   PERFORM public.cq_refuse('CQ_WINDOW_LOCKED');
 ELSIF h.phase='CLOSED' OR EXISTS(SELECT FROM public.core_cqact a WHERE a.thread_uuid=h.thread_uuid AND a.operation='NO_BALLOT') THEN
   PERFORM public.cq_refuse('CQ_CLOSED_LOCKED');
 ELSIF EXISTS(SELECT FROM public.core_cqact a WHERE a.thread_uuid=h.thread_uuid AND a.operation='PROCEED') THEN
   PERFORM public.cq_refuse('CQ_LOCKED');
 ELSIF EXISTS(SELECT FROM public.core_cqact a WHERE a.thread_uuid=h.thread_uuid AND a.operation='OPEN_VOTE') THEN
   PERFORM public.cq_refuse('CQ_VOTE_LOCKED');
 END IF;
 RETURN NEW;
END """

# public.cq_settings(bigint)
# Its landed text: 0074_cq_clarity.py (found there verbatim). Time calls moved: 1 statement.
CQ_SETTINGS_LANDED = r"""
 SELECT public.cq_settings_at(p_lo,statement_timestamp())
"""
CQ_SETTINGS_CLOCK = r"""
 SELECT public.cq_settings_at(p_lo,public.intevia_clock_statement())
"""

# public.s3fu_guard_gateway_effect()
# Its landed text: 0074_cq_clarity.py (assembled by that migration). Time calls moved: 1 statement.
S3FU_GUARD_GATEWAY_EFFECT_LANDED = r"""
DECLARE version_row jsonb; version_id bigint; previous_id bigint; version_table text; prefix text;
        options jsonb; option_count integer;
        v_readiness_code text; v_previous_governance_enabled boolean;
BEGIN

    IF NEW.action='RECORD_CQ' THEN
      IF public.cq_event_matches(NEW) IS NOT TRUE THEN
        RAISE EXCEPTION 'CQ_COMPLETE' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END IF;

    IF NEW.action='RECORD_DISCUSSION_POST' THEN
      IF public.discp_event_matches(NEW) IS NOT TRUE THEN
        RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END IF;

    IF NEW.action='RECORD_DISCUSSION' THEN
      IF public.disc_event_matches(NEW) IS NOT TRUE THEN
        RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END IF;

    IF NEW.action='RECORD_GUIDING_INTENTION' THEN
      IF public.gi_event_matches(NEW) IS NOT TRUE THEN
        RAISE EXCEPTION 'GI_EVENT' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END IF;

    IF NEW.action='ADOPT_LAW' THEN
      IF NEW.prior_state IS DISTINCT FROM NEW.resulting_state OR NEW.sequence<=1 OR NEW.entry_mode<>'TRANSPORT'
        OR NEW.actor_state<>'LIVING_ORGANISM' OR NEW.actor_capacity<>'STANDING' OR NEW.actor_id IS NOT NULL
        OR NEW.actor_access_epoch IS NOT NULL OR NEW.effective_at IS DISTINCT FROM statement_timestamp()
        OR NEW.composing_rule_state<>'NA_TRANSPORT' OR NEW.composer_state<>'NA_TRANSPORT'
        OR NOT EXISTS(SELECT FROM public.core_authoritydecision d JOIN public.core_commandintake i
          ON i.id=(d.target->>'intake')::bigint JOIN public.core_livingorganism lo ON lo.id=NEW.living_organism_id
          WHERE d.decision_reference=NEW.authority_decision_reference AND public.l_intake_matches(d,i)
            AND d.outcome='QUALIFIED' AND d.target->>'living_organism_id'=lo.organism_id::text
            AND d.actor_id=NEW.enterer_identity_id AND NEW.evidence_reference=public.l_evidence(d.target->'plan')
            AND NEW.authority_basis_id=lo.founding_authority_basis_id AND NEW.received_at=i.received_at
            AND NEW.payload_fingerprint=i.request_fingerprint) THEN
        RAISE EXCEPTION 'LAW transport does not match its qualified decision' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END IF;
    IF NEW.action = 'BIRTHING' THEN RETURN NEW; END IF;
    IF NEW.action NOT IN ('EFFECT_APPLICATION_SETTINGS','EFFECT_APPLICATION_QUESTION','EFFECT_GOVERNANCE_CONFIGURATION','EFFECT_ENTITLEMENT_ELECTION','PHASE_ADVANCE','ADOPT_GOVERNING_INSTRUMENT') THEN
        IF NEW.prior_state IS NOT DISTINCT FROM NEW.resulting_state THEN
            RAISE EXCEPTION 'S3FU gateway effect: same-state event without effect action' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.action = 'PHASE_ADVANCE' THEN
        RETURN NEW;
    END IF;
    IF NEW.prior_state IS DISTINCT FROM NEW.resulting_state OR NEW.sequence = 1
       OR NEW.actor_state <> 'LIVING_ORGANISM' OR NEW.actor_id IS NOT NULL
       OR NEW.actor_access_epoch IS NOT NULL OR NEW.actor_capacity <> 'STANDING'
       OR NEW.entry_mode <> 'TRANSPORT' OR NEW.enterer_identity_id IS NULL
       OR NEW.composing_rule_state <> 'NA_TRANSPORT' OR NEW.composer_state <> 'NA_TRANSPORT' THEN
        RAISE EXCEPTION 'S3FU gateway effect: same-state LO transport required' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action = 'ADOPT_GOVERNING_INSTRUMENT' THEN
            IF NEW.prior_state IS DISTINCT FROM NEW.resulting_state
               OR NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM'
               OR NEW.actor_id IS NOT NULL
               OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
               OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
               OR NEW.evidence_reference IS NULL
               OR NOT EXISTS (
                    SELECT 1 FROM public.core_governedrule r
                     WHERE r.authority_reference = 'lo-event:' || NEW.event_uuid::text
                       AND r.scope_organism_id = NEW.living_organism_id
                       AND r.code = 'GOVERNANCE_CONSTITUTION'
                       AND NEW.evidence_reference =
                           'rule:' || r.rule_uuid::text || '@' || r.version::text
               ) THEN
                RAISE EXCEPTION 'SBG_GOVERNEDRULE_EVENT_INVALID'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END IF;
        version_table := CASE NEW.action
            WHEN 'EFFECT_ENTITLEMENT_ELECTION' THEN 'core_loapplicationsettingsversion'
        WHEN 'EFFECT_APPLICATION_SETTINGS' THEN 'core_loapplicationsettingsversion'
        WHEN 'EFFECT_APPLICATION_QUESTION' THEN 'core_loapplicationquestionversion'
        ELSE 'core_logovernanceconfigurationversion' END;
    prefix := CASE NEW.action
            WHEN 'EFFECT_ENTITLEMENT_ELECTION' THEN 'application-settings-version:'
        WHEN 'EFFECT_APPLICATION_SETTINGS' THEN 'application-settings-version:'
        WHEN 'EFFECT_APPLICATION_QUESTION' THEN 'application-question-version:'
        ELSE 'governance-configuration-version:' END;
    IF NEW.evidence_reference !~ ('^' || prefix || '[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$') THEN
        RAISE EXCEPTION 'S3FU gateway effect: evidence reference malformed' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    EXECUTE format('SELECT id, to_jsonb(v) FROM public.%I v WHERE version_uuid = $1::uuid', version_table)
        INTO version_id, version_row USING substring(NEW.evidence_reference from length(prefix) + 1);
    IF version_id IS NULL THEN
        RAISE EXCEPTION 'S3FU gateway effect: version not found' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (version_row->>'living_organism_id')::bigint IS DISTINCT FROM NEW.living_organism_id THEN
        RAISE EXCEPTION 'S3FU gateway effect: version of another organism' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
        -- SE-GATE-01 fixed under HG_DECISION_SE_GATE_01_FIX_IN_SE_20261012: settings and election payloads remain disjoint; shared gateway fields are unchanged.
IF NEW.action = 'EFFECT_ENTITLEMENT_ELECTION' AND (COALESCE(version_row->>'entitlement_choice', '') NOT IN ('FREE','COMPLIMENTARY_BETA') OR COALESCE(version_row->>'entitlement_election_route', '') NOT IN ('BIRTHING','POST_BIRTHING') OR COALESCE((version_row->>'entitlement_policy_version')::integer, 0) <= 0 OR COALESCE(version_row->>'entitlement_policy_text_sha256', '') !~ '^[0-9a-f]{64}$' OR nullif(btrim(version_row->>'entitlement_authority_source'), '') IS NULL OR nullif(btrim(version_row->>'entitlement_authority_version'), '') IS NULL) THEN
        RAISE EXCEPTION 'SE_ENTITLEMENT_ELECTION_INVALID' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action = 'EFFECT_APPLICATION_SETTINGS' AND (version_row->>'entitlement_choice' IS NOT NULL OR version_row->>'entitlement_election_route' IS NOT NULL OR version_row->>'entitlement_policy_version' IS NOT NULL OR version_row->>'entitlement_policy_text_sha256' IS NOT NULL OR version_row->>'entitlement_authority_source' IS NOT NULL OR version_row->>'entitlement_authority_version' IS NOT NULL) THEN
        RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action = 'EFFECT_ENTITLEMENT_ELECTION' AND (version_row->>'applications_open' IS NOT NULL OR version_row->>'automatic_acceptance' IS NOT NULL OR version_row->>'processing_responsibility' IS NOT NULL) THEN
        RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action = 'EFFECT_APPLICATION_SETTINGS' THEN
        IF version_row->>'applications_open' IS NULL OR version_row->>'automatic_acceptance' IS NULL
           OR version_row->>'processing_responsibility' IS NULL
           OR version_row->>'processing_responsibility' NOT IN ('LO','APPLICATION_CIRCLE') THEN
            RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NEW.action = 'EFFECT_APPLICATION_QUESTION' THEN
        options := version_row->'options';
        option_count := CASE WHEN jsonb_typeof(options) = 'array' THEN jsonb_array_length(options) ELSE 0 END;
        IF (version_row->>'question_text') LIKE '%[LO name]%' THEN
            RAISE EXCEPTION 'S3FU gateway effect: unresolved LO name placeholder' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
          IF nullif(btrim(version_row->>'question_text'), '') IS NULL
              OR version_row->>'answer_format' IS NULL
           OR version_row->>'answer_format' NOT IN ('SHORT_ANSWER','LONG_ANSWER','YES_NO','MULTIPLE_CHOICE','SCALE_COMPLEX')
           OR (version_row->>'answer_format' IN ('MULTIPLE_CHOICE','SCALE_COMPLEX') AND (option_count < 2 OR option_count > 7))
           OR (version_row->>'answer_format' NOT IN ('MULTIPLE_CHOICE','SCALE_COMPLEX') AND option_count <> 0) THEN
            RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NEW.action = 'EFFECT_GOVERNANCE_CONFIGURATION' AND (
          version_row->>'cq_active' IS NULL
          OR version_row->>'inherit_portal_cq_settings' IS NULL
          OR version_row->>'require_cq_to_change_lo_settings' IS NULL
          OR version_row->>'require_cq_to_change_application_question' IS NULL) THEN
        RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    EXECUTE format('SELECT 1 FROM public.core_livingorganismevent e JOIN public.%I v ON e.evidence_reference = $1 || v.version_uuid::text WHERE v.id = $2 LIMIT 1', version_table)
        INTO previous_id USING prefix, version_id;
    IF previous_id IS NOT NULL THEN
        RAISE EXCEPTION 'S3FU gateway effect: already effective' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    previous_id := NULL;
    EXECUTE format('SELECT v.id FROM public.core_livingorganismevent e JOIN public.%I v ON e.evidence_reference = $1 || v.version_uuid::text WHERE e.living_organism_id = $2 AND e.action = $3 AND e.effective_at <= $4 AND e.received_at <= $5 AND e.recorded_at <= $5 ORDER BY e.effective_at DESC, e.occurred_at DESC, e.received_at DESC, e.recorded_at DESC, e.sequence DESC, e.event_uuid DESC LIMIT 1', version_table)
        INTO previous_id USING prefix, NEW.living_organism_id, NEW.action, NEW.effective_at, NEW.recorded_at;
    IF (version_row->>'supersedes_id')::bigint IS DISTINCT FROM previous_id THEN
        RAISE EXCEPTION 'S3FU gateway effect: continuity' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action = 'EFFECT_GOVERNANCE_CONFIGURATION'
       AND COALESCE((version_row->>'governance_enabled')::boolean, false)
       AND EXISTS (
            SELECT 1 FROM public.core_livingorganismevent birth
             WHERE birth.living_organism_id = NEW.living_organism_id
               AND birth.action = 'BIRTHING'
               AND birth.effective_at <= NEW.effective_at
               AND birth.received_at <= NEW.recorded_at
               AND birth.recorded_at <= NEW.recorded_at
       ) THEN
        SELECT prior.governance_enabled INTO v_previous_governance_enabled
          FROM public.core_logovernanceconfigurationversion prior
         WHERE prior.id = previous_id;
        IF v_previous_governance_enabled IS DISTINCT FROM true THEN
            v_readiness_code := public.sbg_governance_readiness(
                NEW.living_organism_id, NEW.effective_at, NEW.recorded_at, 'ENABLING'
            );
            IF v_readiness_code IS NOT NULL THEN
                RAISE EXCEPTION '%', v_readiness_code
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END """
S3FU_GUARD_GATEWAY_EFFECT_CLOCK = r"""
DECLARE version_row jsonb; version_id bigint; previous_id bigint; version_table text; prefix text;
        options jsonb; option_count integer;
        v_readiness_code text; v_previous_governance_enabled boolean;
BEGIN

    IF NEW.action='RECORD_CQ' THEN
      IF public.cq_event_matches(NEW) IS NOT TRUE THEN
        RAISE EXCEPTION 'CQ_COMPLETE' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END IF;

    IF NEW.action='RECORD_DISCUSSION_POST' THEN
      IF public.discp_event_matches(NEW) IS NOT TRUE THEN
        RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END IF;

    IF NEW.action='RECORD_DISCUSSION' THEN
      IF public.disc_event_matches(NEW) IS NOT TRUE THEN
        RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END IF;

    IF NEW.action='RECORD_GUIDING_INTENTION' THEN
      IF public.gi_event_matches(NEW) IS NOT TRUE THEN
        RAISE EXCEPTION 'GI_EVENT' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END IF;

    IF NEW.action='ADOPT_LAW' THEN
      IF NEW.prior_state IS DISTINCT FROM NEW.resulting_state OR NEW.sequence<=1 OR NEW.entry_mode<>'TRANSPORT'
        OR NEW.actor_state<>'LIVING_ORGANISM' OR NEW.actor_capacity<>'STANDING' OR NEW.actor_id IS NOT NULL
        OR NEW.actor_access_epoch IS NOT NULL OR NEW.effective_at IS DISTINCT FROM public.intevia_clock_statement()
        OR NEW.composing_rule_state<>'NA_TRANSPORT' OR NEW.composer_state<>'NA_TRANSPORT'
        OR NOT EXISTS(SELECT FROM public.core_authoritydecision d JOIN public.core_commandintake i
          ON i.id=(d.target->>'intake')::bigint JOIN public.core_livingorganism lo ON lo.id=NEW.living_organism_id
          WHERE d.decision_reference=NEW.authority_decision_reference AND public.l_intake_matches(d,i)
            AND d.outcome='QUALIFIED' AND d.target->>'living_organism_id'=lo.organism_id::text
            AND d.actor_id=NEW.enterer_identity_id AND NEW.evidence_reference=public.l_evidence(d.target->'plan')
            AND NEW.authority_basis_id=lo.founding_authority_basis_id AND NEW.received_at=i.received_at
            AND NEW.payload_fingerprint=i.request_fingerprint) THEN
        RAISE EXCEPTION 'LAW transport does not match its qualified decision' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END IF;
    IF NEW.action = 'BIRTHING' THEN RETURN NEW; END IF;
    IF NEW.action NOT IN ('EFFECT_APPLICATION_SETTINGS','EFFECT_APPLICATION_QUESTION','EFFECT_GOVERNANCE_CONFIGURATION','EFFECT_ENTITLEMENT_ELECTION','PHASE_ADVANCE','ADOPT_GOVERNING_INSTRUMENT') THEN
        IF NEW.prior_state IS NOT DISTINCT FROM NEW.resulting_state THEN
            RAISE EXCEPTION 'S3FU gateway effect: same-state event without effect action' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.action = 'PHASE_ADVANCE' THEN
        RETURN NEW;
    END IF;
    IF NEW.prior_state IS DISTINCT FROM NEW.resulting_state OR NEW.sequence = 1
       OR NEW.actor_state <> 'LIVING_ORGANISM' OR NEW.actor_id IS NOT NULL
       OR NEW.actor_access_epoch IS NOT NULL OR NEW.actor_capacity <> 'STANDING'
       OR NEW.entry_mode <> 'TRANSPORT' OR NEW.enterer_identity_id IS NULL
       OR NEW.composing_rule_state <> 'NA_TRANSPORT' OR NEW.composer_state <> 'NA_TRANSPORT' THEN
        RAISE EXCEPTION 'S3FU gateway effect: same-state LO transport required' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action = 'ADOPT_GOVERNING_INSTRUMENT' THEN
            IF NEW.prior_state IS DISTINCT FROM NEW.resulting_state
               OR NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM'
               OR NEW.actor_id IS NOT NULL
               OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
               OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
               OR NEW.evidence_reference IS NULL
               OR NOT EXISTS (
                    SELECT 1 FROM public.core_governedrule r
                     WHERE r.authority_reference = 'lo-event:' || NEW.event_uuid::text
                       AND r.scope_organism_id = NEW.living_organism_id
                       AND r.code = 'GOVERNANCE_CONSTITUTION'
                       AND NEW.evidence_reference =
                           'rule:' || r.rule_uuid::text || '@' || r.version::text
               ) THEN
                RAISE EXCEPTION 'SBG_GOVERNEDRULE_EVENT_INVALID'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END IF;
        version_table := CASE NEW.action
            WHEN 'EFFECT_ENTITLEMENT_ELECTION' THEN 'core_loapplicationsettingsversion'
        WHEN 'EFFECT_APPLICATION_SETTINGS' THEN 'core_loapplicationsettingsversion'
        WHEN 'EFFECT_APPLICATION_QUESTION' THEN 'core_loapplicationquestionversion'
        ELSE 'core_logovernanceconfigurationversion' END;
    prefix := CASE NEW.action
            WHEN 'EFFECT_ENTITLEMENT_ELECTION' THEN 'application-settings-version:'
        WHEN 'EFFECT_APPLICATION_SETTINGS' THEN 'application-settings-version:'
        WHEN 'EFFECT_APPLICATION_QUESTION' THEN 'application-question-version:'
        ELSE 'governance-configuration-version:' END;
    IF NEW.evidence_reference !~ ('^' || prefix || '[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$') THEN
        RAISE EXCEPTION 'S3FU gateway effect: evidence reference malformed' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    EXECUTE format('SELECT id, to_jsonb(v) FROM public.%I v WHERE version_uuid = $1::uuid', version_table)
        INTO version_id, version_row USING substring(NEW.evidence_reference from length(prefix) + 1);
    IF version_id IS NULL THEN
        RAISE EXCEPTION 'S3FU gateway effect: version not found' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (version_row->>'living_organism_id')::bigint IS DISTINCT FROM NEW.living_organism_id THEN
        RAISE EXCEPTION 'S3FU gateway effect: version of another organism' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
        -- SE-GATE-01 fixed under HG_DECISION_SE_GATE_01_FIX_IN_SE_20261012: settings and election payloads remain disjoint; shared gateway fields are unchanged.
IF NEW.action = 'EFFECT_ENTITLEMENT_ELECTION' AND (COALESCE(version_row->>'entitlement_choice', '') NOT IN ('FREE','COMPLIMENTARY_BETA') OR COALESCE(version_row->>'entitlement_election_route', '') NOT IN ('BIRTHING','POST_BIRTHING') OR COALESCE((version_row->>'entitlement_policy_version')::integer, 0) <= 0 OR COALESCE(version_row->>'entitlement_policy_text_sha256', '') !~ '^[0-9a-f]{64}$' OR nullif(btrim(version_row->>'entitlement_authority_source'), '') IS NULL OR nullif(btrim(version_row->>'entitlement_authority_version'), '') IS NULL) THEN
        RAISE EXCEPTION 'SE_ENTITLEMENT_ELECTION_INVALID' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action = 'EFFECT_APPLICATION_SETTINGS' AND (version_row->>'entitlement_choice' IS NOT NULL OR version_row->>'entitlement_election_route' IS NOT NULL OR version_row->>'entitlement_policy_version' IS NOT NULL OR version_row->>'entitlement_policy_text_sha256' IS NOT NULL OR version_row->>'entitlement_authority_source' IS NOT NULL OR version_row->>'entitlement_authority_version' IS NOT NULL) THEN
        RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action = 'EFFECT_ENTITLEMENT_ELECTION' AND (version_row->>'applications_open' IS NOT NULL OR version_row->>'automatic_acceptance' IS NOT NULL OR version_row->>'processing_responsibility' IS NOT NULL) THEN
        RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action = 'EFFECT_APPLICATION_SETTINGS' THEN
        IF version_row->>'applications_open' IS NULL OR version_row->>'automatic_acceptance' IS NULL
           OR version_row->>'processing_responsibility' IS NULL
           OR version_row->>'processing_responsibility' NOT IN ('LO','APPLICATION_CIRCLE') THEN
            RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NEW.action = 'EFFECT_APPLICATION_QUESTION' THEN
        options := version_row->'options';
        option_count := CASE WHEN jsonb_typeof(options) = 'array' THEN jsonb_array_length(options) ELSE 0 END;
        IF (version_row->>'question_text') LIKE '%[LO name]%' THEN
            RAISE EXCEPTION 'S3FU gateway effect: unresolved LO name placeholder' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
          IF nullif(btrim(version_row->>'question_text'), '') IS NULL
              OR version_row->>'answer_format' IS NULL
           OR version_row->>'answer_format' NOT IN ('SHORT_ANSWER','LONG_ANSWER','YES_NO','MULTIPLE_CHOICE','SCALE_COMPLEX')
           OR (version_row->>'answer_format' IN ('MULTIPLE_CHOICE','SCALE_COMPLEX') AND (option_count < 2 OR option_count > 7))
           OR (version_row->>'answer_format' NOT IN ('MULTIPLE_CHOICE','SCALE_COMPLEX') AND option_count <> 0) THEN
            RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NEW.action = 'EFFECT_GOVERNANCE_CONFIGURATION' AND (
          version_row->>'cq_active' IS NULL
          OR version_row->>'inherit_portal_cq_settings' IS NULL
          OR version_row->>'require_cq_to_change_lo_settings' IS NULL
          OR version_row->>'require_cq_to_change_application_question' IS NULL) THEN
        RAISE EXCEPTION 'S3FU gateway effect: version incomplete' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    EXECUTE format('SELECT 1 FROM public.core_livingorganismevent e JOIN public.%I v ON e.evidence_reference = $1 || v.version_uuid::text WHERE v.id = $2 LIMIT 1', version_table)
        INTO previous_id USING prefix, version_id;
    IF previous_id IS NOT NULL THEN
        RAISE EXCEPTION 'S3FU gateway effect: already effective' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    previous_id := NULL;
    EXECUTE format('SELECT v.id FROM public.core_livingorganismevent e JOIN public.%I v ON e.evidence_reference = $1 || v.version_uuid::text WHERE e.living_organism_id = $2 AND e.action = $3 AND e.effective_at <= $4 AND e.received_at <= $5 AND e.recorded_at <= $5 ORDER BY e.effective_at DESC, e.occurred_at DESC, e.received_at DESC, e.recorded_at DESC, e.sequence DESC, e.event_uuid DESC LIMIT 1', version_table)
        INTO previous_id USING prefix, NEW.living_organism_id, NEW.action, NEW.effective_at, NEW.recorded_at;
    IF (version_row->>'supersedes_id')::bigint IS DISTINCT FROM previous_id THEN
        RAISE EXCEPTION 'S3FU gateway effect: continuity' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action = 'EFFECT_GOVERNANCE_CONFIGURATION'
       AND COALESCE((version_row->>'governance_enabled')::boolean, false)
       AND EXISTS (
            SELECT 1 FROM public.core_livingorganismevent birth
             WHERE birth.living_organism_id = NEW.living_organism_id
               AND birth.action = 'BIRTHING'
               AND birth.effective_at <= NEW.effective_at
               AND birth.received_at <= NEW.recorded_at
               AND birth.recorded_at <= NEW.recorded_at
       ) THEN
        SELECT prior.governance_enabled INTO v_previous_governance_enabled
          FROM public.core_logovernanceconfigurationversion prior
         WHERE prior.id = previous_id;
        IF v_previous_governance_enabled IS DISTINCT FROM true THEN
            v_readiness_code := public.sbg_governance_readiness(
                NEW.living_organism_id, NEW.effective_at, NEW.recorded_at, 'ENABLING'
            );
            IF v_readiness_code IS NOT NULL THEN
                RAISE EXCEPTION '%', v_readiness_code
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END """

# public.cq_after_departure()
# Its landed text: 0075_cq_decisions.py (found there verbatim). Time calls moved: 4 statement.
CQ_AFTER_DEPARTURE_LANDED = r"""
DECLARE v_lo bigint; v_circle bigint; v_member bigint; v_epoch bigint; v_kind text; v_vote record;
BEGIN
 IF NEW.effective_at>statement_timestamp() THEN RETURN NULL; END IF;
 IF TG_TABLE_NAME='core_organismmembershiptransition' THEN
   SELECT living_organism_id,identity_id INTO v_lo,v_member FROM public.core_organismmembership WHERE id=NEW.membership_id;
   v_kind:='MEMBERSHIP';
 ELSE
   SELECT m.living_organism_id,m.identity_id,cp.circle_id INTO v_lo,v_member,v_circle
    FROM public.core_circleparticipation cp JOIN public.core_organismmembership m ON m.id=cp.membership_id
    WHERE cp.id=NEW.participation_id;
   v_kind:='CIRCLE_PLACE';
 END IF;
 FOR v_vote IN SELECT o.act_uuid,o.thread_uuid,o.board_uuid FROM public.cq_phases h
   JOIN public.core_cqact o ON o.act_uuid=h.act_uuid JOIN public.core_discboard b ON b.board_uuid=o.board_uuid
   WHERE h.organism_id=v_lo AND h.phase='VOTING' AND h.deadline>statement_timestamp()
     AND o.recorded_at<=NEW.recorded_at AND (v_circle IS NULL OR v_circle=b.circle_id)
     AND EXISTS(SELECT FROM jsonb_array_elements(o.facts->'roster') x WHERE x->>'person'=v_member::text)
     AND public.cq_can_settle(o.act_uuid,statement_timestamp()) ORDER BY o.ordinal
 LOOP
   SELECT i.access_epoch INTO v_epoch FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=NEW.enterer_identity_id AND i.access_state='active' AND u.is_active;
   IF v_epoch IS NULL THEN CONTINUE; END IF;
   INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
   VALUES(gen_random_uuid(),v_lo,v_vote.board_uuid,v_vote.thread_uuid,'SETTLE',NEW.enterer_identity_id,v_epoch,
     statement_timestamp(),jsonb_build_object('ballot',v_vote.act_uuid,'cause',
       jsonb_build_object('kind',v_kind,'id',NEW.id,'ballot',v_vote.act_uuid)));
 END LOOP;
 RETURN NULL;
END """
CQ_AFTER_DEPARTURE_CLOCK = r"""
DECLARE v_lo bigint; v_circle bigint; v_member bigint; v_epoch bigint; v_kind text; v_vote record;
BEGIN
 IF NEW.effective_at>public.intevia_clock_statement() THEN RETURN NULL; END IF;
 IF TG_TABLE_NAME='core_organismmembershiptransition' THEN
   SELECT living_organism_id,identity_id INTO v_lo,v_member FROM public.core_organismmembership WHERE id=NEW.membership_id;
   v_kind:='MEMBERSHIP';
 ELSE
   SELECT m.living_organism_id,m.identity_id,cp.circle_id INTO v_lo,v_member,v_circle
    FROM public.core_circleparticipation cp JOIN public.core_organismmembership m ON m.id=cp.membership_id
    WHERE cp.id=NEW.participation_id;
   v_kind:='CIRCLE_PLACE';
 END IF;
 FOR v_vote IN SELECT o.act_uuid,o.thread_uuid,o.board_uuid FROM public.cq_phases h
   JOIN public.core_cqact o ON o.act_uuid=h.act_uuid JOIN public.core_discboard b ON b.board_uuid=o.board_uuid
   WHERE h.organism_id=v_lo AND h.phase='VOTING' AND h.deadline>public.intevia_clock_statement()
     AND o.recorded_at<=NEW.recorded_at AND (v_circle IS NULL OR v_circle=b.circle_id)
     AND EXISTS(SELECT FROM jsonb_array_elements(o.facts->'roster') x WHERE x->>'person'=v_member::text)
     AND public.cq_can_settle(o.act_uuid,public.intevia_clock_statement()) ORDER BY o.ordinal
 LOOP
   SELECT i.access_epoch INTO v_epoch FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=NEW.enterer_identity_id AND i.access_state='active' AND u.is_active;
   IF v_epoch IS NULL THEN CONTINUE; END IF;
   INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
   VALUES(gen_random_uuid(),v_lo,v_vote.board_uuid,v_vote.thread_uuid,'SETTLE',NEW.enterer_identity_id,v_epoch,
     public.intevia_clock_statement(),jsonb_build_object('ballot',v_vote.act_uuid,'cause',
       jsonb_build_object('kind',v_kind,'id',NEW.id,'ballot',v_vote.act_uuid)));
 END LOOP;
 RETURN NULL;
END """

# public.cq_loss_source(bigint, uuid, uuid, bigint, jsonb)
# Its landed text: 0075_cq_decisions.py (found there verbatim). Time calls moved: 2 statement.
CQ_LOSS_SOURCE_LANDED = r"""
DECLARE v_cause jsonb:=p_args->'cause'; v_open public.core_cqact; v_member bigint;
BEGIN
 SELECT * INTO v_open FROM public.core_cqact WHERE act_uuid::text=v_cause->>'ballot'
    AND organism_id=p_lo AND board_uuid=p_board AND thread_uuid=p_thread AND operation IN ('PROCEED','OPEN_VOTE','REOPEN_VOTE');
 IF v_open.act_uuid IS NULL THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF p_args ? 'ballot' AND p_args->>'ballot' IS DISTINCT FROM v_open.act_uuid::text
 OR (p_args ? 'result' AND NOT EXISTS(SELECT FROM public.core_cqact r WHERE r.act_uuid::text=p_args->>'result'
   AND r.operation='SETTLE' AND r.request->'cause'=v_cause AND r.execution_xid=pg_current_xact_id()))
 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF v_cause->>'kind'='MEMBERSHIP' THEN
   SELECT m.identity_id INTO v_member FROM public.core_organismmembershiptransition t
    JOIN public.core_organismmembership m ON m.id=t.membership_id WHERE t.id::text=v_cause->>'id'
    AND m.living_organism_id=p_lo AND t.enterer_identity_id=p_actor AND t.prior_state='ACTIVE' AND t.resulting_state<>'ACTIVE'
    AND t.effective_at<=statement_timestamp() AND t.recorded_at>=v_open.recorded_at;
 ELSIF v_cause->>'kind'='CIRCLE_PLACE' THEN
   SELECT m.identity_id INTO v_member FROM public.core_circleparticipationend e
    JOIN public.core_circleparticipation cp ON cp.id=e.participation_id JOIN public.core_organismmembership m ON m.id=cp.membership_id
    JOIN public.core_discboard b ON b.circle_id=cp.circle_id AND b.organism_id=m.living_organism_id
    WHERE b.board_uuid=p_board AND e.id::text=v_cause->>'id' AND e.enterer_identity_id=p_actor
    AND e.effective_at<=statement_timestamp() AND e.recorded_at>=v_open.recorded_at;
 END IF;
 IF v_member IS NULL OR NOT EXISTS(SELECT FROM jsonb_array_elements(v_open.facts->'roster') x
   WHERE x->>'person'=v_member::text) THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN jsonb_build_object('kind','RULE','reference','HG:DISC2:D3/D5','cause',v_cause);
END """
CQ_LOSS_SOURCE_CLOCK = r"""
DECLARE v_cause jsonb:=p_args->'cause'; v_open public.core_cqact; v_member bigint;
BEGIN
 SELECT * INTO v_open FROM public.core_cqact WHERE act_uuid::text=v_cause->>'ballot'
    AND organism_id=p_lo AND board_uuid=p_board AND thread_uuid=p_thread AND operation IN ('PROCEED','OPEN_VOTE','REOPEN_VOTE');
 IF v_open.act_uuid IS NULL THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF p_args ? 'ballot' AND p_args->>'ballot' IS DISTINCT FROM v_open.act_uuid::text
 OR (p_args ? 'result' AND NOT EXISTS(SELECT FROM public.core_cqact r WHERE r.act_uuid::text=p_args->>'result'
   AND r.operation='SETTLE' AND r.request->'cause'=v_cause AND r.execution_xid=pg_current_xact_id()))
 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF v_cause->>'kind'='MEMBERSHIP' THEN
   SELECT m.identity_id INTO v_member FROM public.core_organismmembershiptransition t
    JOIN public.core_organismmembership m ON m.id=t.membership_id WHERE t.id::text=v_cause->>'id'
    AND m.living_organism_id=p_lo AND t.enterer_identity_id=p_actor AND t.prior_state='ACTIVE' AND t.resulting_state<>'ACTIVE'
    AND t.effective_at<=public.intevia_clock_statement() AND t.recorded_at>=v_open.recorded_at;
 ELSIF v_cause->>'kind'='CIRCLE_PLACE' THEN
   SELECT m.identity_id INTO v_member FROM public.core_circleparticipationend e
    JOIN public.core_circleparticipation cp ON cp.id=e.participation_id JOIN public.core_organismmembership m ON m.id=cp.membership_id
    JOIN public.core_discboard b ON b.circle_id=cp.circle_id AND b.organism_id=m.living_organism_id
    WHERE b.board_uuid=p_board AND e.id::text=v_cause->>'id' AND e.enterer_identity_id=p_actor
    AND e.effective_at<=public.intevia_clock_statement() AND e.recorded_at>=v_open.recorded_at;
 END IF;
 IF v_member IS NULL OR NOT EXISTS(SELECT FROM jsonb_array_elements(v_open.facts->'roster') x
   WHERE x->>'person'=v_member::text) THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN jsonb_build_object('kind','RULE','reference','HG:DISC2:D3/D5','cause',v_cause);
END """

# public.cq_vote_followup()
# Its landed text: 0075_cq_decisions.py (found there verbatim). Time calls moved: 2 statement.
CQ_VOTE_FOLLOWUP_LANDED = r"""
DECLARE v_open uuid; v_op text; v_args jsonb;
BEGIN
 IF NEW.operation IN ('PREPARED','PREPARATION_DUE','RESPONSE_DUE') THEN
   IF NEW.facts->>'rule_open' IS DISTINCT FROM 'true' THEN RETURN NULL; END IF;
   v_op:='RULE_OPEN'; v_args:=jsonb_build_object('trigger_act',NEW.act_uuid);
 ELSIF NEW.operation IN ('ANSWER','CONFLICT') THEN
   SELECT h.act_uuid INTO v_open FROM public.cq_phases h
    WHERE h.thread_uuid=NEW.thread_uuid AND h.phase='VOTING';
   IF v_open IS NULL OR NOT public.cq_can_settle(v_open,statement_timestamp()) THEN RETURN NULL; END IF;
   v_op:='SETTLE'; v_args:=jsonb_build_object('ballot',v_open);
 ELSIF NEW.operation='SETTLE' AND (NEW.facts->>'restart')::boolean THEN
   v_op:='REOPEN_VOTE'; v_args:=jsonb_build_object('result',NEW.act_uuid);
   IF NEW.request ? 'cause' THEN v_args:=v_args||jsonb_build_object('cause',NEW.request->'cause'); END IF;
 ELSE RETURN NULL; END IF;
 INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
 VALUES(gen_random_uuid(),NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,v_op,NEW.actor_id,NEW.actor_epoch,statement_timestamp(),v_args);
 RETURN NULL;
END """
CQ_VOTE_FOLLOWUP_CLOCK = r"""
DECLARE v_open uuid; v_op text; v_args jsonb;
BEGIN
 IF NEW.operation IN ('PREPARED','PREPARATION_DUE','RESPONSE_DUE') THEN
   IF NEW.facts->>'rule_open' IS DISTINCT FROM 'true' THEN RETURN NULL; END IF;
   v_op:='RULE_OPEN'; v_args:=jsonb_build_object('trigger_act',NEW.act_uuid);
 ELSIF NEW.operation IN ('ANSWER','CONFLICT') THEN
   SELECT h.act_uuid INTO v_open FROM public.cq_phases h
    WHERE h.thread_uuid=NEW.thread_uuid AND h.phase='VOTING';
   IF v_open IS NULL OR NOT public.cq_can_settle(v_open,public.intevia_clock_statement()) THEN RETURN NULL; END IF;
   v_op:='SETTLE'; v_args:=jsonb_build_object('ballot',v_open);
 ELSIF NEW.operation='SETTLE' AND (NEW.facts->>'restart')::boolean THEN
   v_op:='REOPEN_VOTE'; v_args:=jsonb_build_object('result',NEW.act_uuid);
   IF NEW.request ? 'cause' THEN v_args:=v_args||jsonb_build_object('cause',NEW.request->'cause'); END IF;
 ELSE RETURN NULL; END IF;
 INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
 VALUES(gen_random_uuid(),NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,v_op,NEW.actor_id,NEW.actor_epoch,public.intevia_clock_statement(),v_args);
 RETURN NULL;
END """

# public.discp_execute(jsonb, text, bigint, bigint, timestamp with time zone)
# Its landed text: 0075_cq_decisions.py (assembled by that migration). Time calls moved: 1 statement.
DISCP_EXECUTE_LANDED = r"""
DECLARE v_board public.core_discboard; v_lo public.core_livingorganism; v_head public.core_livingorganismevent;
 v_old public.core_discpostact; v_source jsonb; v_fp text; v_stamp timestamptz:=statement_timestamp();
 v_key uuid:=(p_payload->>'request')::uuid; v_thread uuid:=(p_payload->>'thread')::uuid;
 v_post uuid:=(p_payload->>'post')::uuid; v_op text:=p_payload->>'operation'; v_version public.discp_posts;
 v_count integer; v_state text; v_other_board uuid; v_quote uuid;
 v_conflict_name text; v_conflict_table text; v_conflict_schema text;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('DISC1:request:'||v_key::text,0));
 SELECT b.* INTO v_board FROM public.core_discboard b JOIN public.core_livingorganism lo ON lo.id=b.organism_id
   LEFT JOIN public.core_circle c ON c.id=b.circle_id
   WHERE b.board_uuid=(p_payload->>'board')::uuid AND lo.organism_id=(p_payload->>'organism')::uuid
     AND c.circle_uuid IS NOT DISTINCT FROM (p_payload->>'circle')::uuid;
 IF v_board.board_uuid IS NULL THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 SELECT * INTO v_lo FROM public.core_livingorganism WHERE id=v_board.organism_id;
 PERFORM public.s015_lock_organisms(ARRAY[v_lo.organism_id]);
 IF NOT EXISTS(SELECT FROM public.disc_categories c WHERE c.category_uuid=(p_payload->>'category')::uuid
   AND c.board_uuid=v_board.board_uuid AND public.disc_audience(v_board.organism_id,v_board.circle_id,p_actor,c.access_level,v_stamp)) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 -- Resolve omitted quote from the immutable predecessor, making replay stable.
 IF v_op='EDIT' AND NOT (p_payload ? 'quote') THEN
   SELECT quote_version INTO v_quote FROM public.core_discpostversion
     WHERE version_uuid=(p_payload->>'predecessor')::uuid AND post_uuid=v_post;
   p_payload:=p_payload||jsonb_build_object('quote',v_quote);
 END IF;
 v_source:=public.discp_source(p_payload,p_actor,v_stamp);
 IF v_source IS NULL THEN
   -- Same refusal for new-ID probes whether those IDs are used or unused.
   IF v_op IN ('START','REPLY') THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   RAISE EXCEPTION 'DISC_POST_AUTHORITY' USING ERRCODE='23514'; END IF;
 SELECT board_uuid INTO v_other_board FROM public.core_discact WHERE act_uuid=v_key;
 IF v_other_board IS NOT NULL THEN
   IF v_other_board<>v_board.board_uuid THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   RAISE EXCEPTION 'DISC_REQUEST_CONFLICT' USING ERRCODE='23514';
 END IF;
 SELECT * INTO v_old FROM public.core_discpostact WHERE act_uuid=v_key;
 IF v_old.act_uuid IS NOT NULL THEN
   IF v_old.board_uuid<>v_board.board_uuid THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   IF v_old.actor_id<>p_actor OR v_old.actor_epoch<>p_epoch OR v_old.payload IS DISTINCT FROM p_payload THEN
     RAISE EXCEPTION 'DISC_REQUEST_CONFLICT' USING ERRCODE='23514'; END IF;
   RETURN jsonb_build_object('act',v_key,'thread',v_thread,'post',v_post,'version',CASE WHEN v_op IN ('START','REPLY','EDIT') THEN v_key END);
 END IF;
 IF EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.event_uuid=v_key) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 IF v_op IN ('START','REPLY','EDIT') AND (p_body IS NULL OR public.discp_body_fingerprint(p_body) IS DISTINCT FROM p_payload->>'body_fingerprint') THEN
   RAISE EXCEPTION 'DISC_POST_INPUT' USING ERRCODE='23514'; END IF;
 v_fp:=public.disc_fingerprint(p_payload);
 INSERT INTO public.core_discpostact(act_uuid,board_uuid,operation,actor_id,actor_epoch,authority_source,payload,fingerprint,received_at)
 VALUES(v_key,v_board.board_uuid,v_op,p_actor,p_epoch,v_source,p_payload,v_fp,p_received);
 IF v_op='START' THEN
   INSERT INTO public.core_discthread VALUES(v_thread,(p_payload->>'category')::uuid,v_key,p_payload->>'type',p_payload->>'title','ORDINARY');
 END IF;
 IF v_op IN ('START','REPLY') THEN
   SELECT count(*)+(SELECT count(*) FROM public.core_cqact a WHERE a.thread_uuid=v_thread AND a.operation IN ('SETTLE','CLOSE','NO_BALLOT')) INTO v_count FROM public.core_discpost p WHERE p.thread_uuid=v_thread;
   INSERT INTO public.core_discpost VALUES(v_post,v_thread,v_key,p_actor,v_count+1);
 END IF;
 IF v_op IN ('START','REPLY','EDIT') THEN
   SELECT * INTO v_version FROM public.discp_posts WHERE post_uuid=v_post;
   INSERT INTO public.core_discpostversion VALUES(v_key,v_post,v_key,coalesce(v_version.version_number,0)+1,
     v_version.version_uuid,p_payload->>'body_fingerprint',(p_payload->>'quote')::uuid);
   INSERT INTO public.core_discbody VALUES(v_key,p_body);
 END IF;
 SELECT * INTO STRICT v_lo FROM public.core_livingorganism WHERE id=v_board.organism_id FOR UPDATE;
 SELECT * INTO STRICT v_head FROM public.core_livingorganismevent WHERE id=v_lo.head_event_id;
 v_state:=public.s015_fold_effective_state('core_livingorganism',v_lo.id,v_stamp,v_stamp);
 PERFORM public.s015_append_event('core_livingorganism',v_lo.id,'RECORD_DISCUSSION_POST',v_state,
   v_stamp,p_received,p_received,p_actor,v_lo.founding_authority_basis_id,
   public.disc_decision(v_source,v_fp),'disc-post-act:'||v_key,'disc-post:'||v_key,v_fp,'{}'::jsonb,
   NULL,v_key,v_head.id,v_head.sequence::integer,NULL::bigint,NULL::text,'disc-post:'||v_key);
 RETURN jsonb_build_object('act',v_key,'thread',v_thread,'post',v_post,'version',CASE WHEN v_op IN ('START','REPLY','EDIT') THEN v_key END);
EXCEPTION WHEN unique_violation THEN
 -- Another domain may race our preflight without the DISC1 request lock.
 -- Translate only the shared event UUID constraint; preserve unrelated faults.
 GET STACKED DIAGNOSTICS v_conflict_name=CONSTRAINT_NAME,
   v_conflict_table=TABLE_NAME,v_conflict_schema=SCHEMA_NAME;
 IF v_conflict_schema='public' AND v_conflict_table='core_livingorganismevent'
   AND EXISTS(SELECT FROM pg_constraint c WHERE c.conrelid='public.core_livingorganismevent'::regclass
     AND c.conname=v_conflict_name AND c.contype='u'
     AND c.conkey=ARRAY[(SELECT a.attnum FROM pg_attribute a
       WHERE a.attrelid=c.conrelid AND a.attname='event_uuid')]) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 RAISE;
END """
DISCP_EXECUTE_CLOCK = r"""
DECLARE v_board public.core_discboard; v_lo public.core_livingorganism; v_head public.core_livingorganismevent;
 v_old public.core_discpostact; v_source jsonb; v_fp text; v_stamp timestamptz:=public.intevia_clock_statement();
 v_key uuid:=(p_payload->>'request')::uuid; v_thread uuid:=(p_payload->>'thread')::uuid;
 v_post uuid:=(p_payload->>'post')::uuid; v_op text:=p_payload->>'operation'; v_version public.discp_posts;
 v_count integer; v_state text; v_other_board uuid; v_quote uuid;
 v_conflict_name text; v_conflict_table text; v_conflict_schema text;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('DISC1:request:'||v_key::text,0));
 SELECT b.* INTO v_board FROM public.core_discboard b JOIN public.core_livingorganism lo ON lo.id=b.organism_id
   LEFT JOIN public.core_circle c ON c.id=b.circle_id
   WHERE b.board_uuid=(p_payload->>'board')::uuid AND lo.organism_id=(p_payload->>'organism')::uuid
     AND c.circle_uuid IS NOT DISTINCT FROM (p_payload->>'circle')::uuid;
 IF v_board.board_uuid IS NULL THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 SELECT * INTO v_lo FROM public.core_livingorganism WHERE id=v_board.organism_id;
 PERFORM public.s015_lock_organisms(ARRAY[v_lo.organism_id]);
 IF NOT EXISTS(SELECT FROM public.disc_categories c WHERE c.category_uuid=(p_payload->>'category')::uuid
   AND c.board_uuid=v_board.board_uuid AND public.disc_audience(v_board.organism_id,v_board.circle_id,p_actor,c.access_level,v_stamp)) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 -- Resolve omitted quote from the immutable predecessor, making replay stable.
 IF v_op='EDIT' AND NOT (p_payload ? 'quote') THEN
   SELECT quote_version INTO v_quote FROM public.core_discpostversion
     WHERE version_uuid=(p_payload->>'predecessor')::uuid AND post_uuid=v_post;
   p_payload:=p_payload||jsonb_build_object('quote',v_quote);
 END IF;
 v_source:=public.discp_source(p_payload,p_actor,v_stamp);
 IF v_source IS NULL THEN
   -- Same refusal for new-ID probes whether those IDs are used or unused.
   IF v_op IN ('START','REPLY') THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   RAISE EXCEPTION 'DISC_POST_AUTHORITY' USING ERRCODE='23514'; END IF;
 SELECT board_uuid INTO v_other_board FROM public.core_discact WHERE act_uuid=v_key;
 IF v_other_board IS NOT NULL THEN
   IF v_other_board<>v_board.board_uuid THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   RAISE EXCEPTION 'DISC_REQUEST_CONFLICT' USING ERRCODE='23514';
 END IF;
 SELECT * INTO v_old FROM public.core_discpostact WHERE act_uuid=v_key;
 IF v_old.act_uuid IS NOT NULL THEN
   IF v_old.board_uuid<>v_board.board_uuid THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   IF v_old.actor_id<>p_actor OR v_old.actor_epoch<>p_epoch OR v_old.payload IS DISTINCT FROM p_payload THEN
     RAISE EXCEPTION 'DISC_REQUEST_CONFLICT' USING ERRCODE='23514'; END IF;
   RETURN jsonb_build_object('act',v_key,'thread',v_thread,'post',v_post,'version',CASE WHEN v_op IN ('START','REPLY','EDIT') THEN v_key END);
 END IF;
 IF EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.event_uuid=v_key) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 IF v_op IN ('START','REPLY','EDIT') AND (p_body IS NULL OR public.discp_body_fingerprint(p_body) IS DISTINCT FROM p_payload->>'body_fingerprint') THEN
   RAISE EXCEPTION 'DISC_POST_INPUT' USING ERRCODE='23514'; END IF;
 v_fp:=public.disc_fingerprint(p_payload);
 INSERT INTO public.core_discpostact(act_uuid,board_uuid,operation,actor_id,actor_epoch,authority_source,payload,fingerprint,received_at)
 VALUES(v_key,v_board.board_uuid,v_op,p_actor,p_epoch,v_source,p_payload,v_fp,p_received);
 IF v_op='START' THEN
   INSERT INTO public.core_discthread VALUES(v_thread,(p_payload->>'category')::uuid,v_key,p_payload->>'type',p_payload->>'title','ORDINARY');
 END IF;
 IF v_op IN ('START','REPLY') THEN
   SELECT count(*)+(SELECT count(*) FROM public.core_cqact a WHERE a.thread_uuid=v_thread AND a.operation IN ('SETTLE','CLOSE','NO_BALLOT')) INTO v_count FROM public.core_discpost p WHERE p.thread_uuid=v_thread;
   INSERT INTO public.core_discpost VALUES(v_post,v_thread,v_key,p_actor,v_count+1);
 END IF;
 IF v_op IN ('START','REPLY','EDIT') THEN
   SELECT * INTO v_version FROM public.discp_posts WHERE post_uuid=v_post;
   INSERT INTO public.core_discpostversion VALUES(v_key,v_post,v_key,coalesce(v_version.version_number,0)+1,
     v_version.version_uuid,p_payload->>'body_fingerprint',(p_payload->>'quote')::uuid);
   INSERT INTO public.core_discbody VALUES(v_key,p_body);
 END IF;
 SELECT * INTO STRICT v_lo FROM public.core_livingorganism WHERE id=v_board.organism_id FOR UPDATE;
 SELECT * INTO STRICT v_head FROM public.core_livingorganismevent WHERE id=v_lo.head_event_id;
 v_state:=public.s015_fold_effective_state('core_livingorganism',v_lo.id,v_stamp,v_stamp);
 PERFORM public.s015_append_event('core_livingorganism',v_lo.id,'RECORD_DISCUSSION_POST',v_state,
   v_stamp,p_received,p_received,p_actor,v_lo.founding_authority_basis_id,
   public.disc_decision(v_source,v_fp),'disc-post-act:'||v_key,'disc-post:'||v_key,v_fp,'{}'::jsonb,
   NULL,v_key,v_head.id,v_head.sequence::integer,NULL::bigint,NULL::text,'disc-post:'||v_key);
 RETURN jsonb_build_object('act',v_key,'thread',v_thread,'post',v_post,'version',CASE WHEN v_op IN ('START','REPLY','EDIT') THEN v_key END);
EXCEPTION WHEN unique_violation THEN
 -- Another domain may race our preflight without the DISC1 request lock.
 -- Translate only the shared event UUID constraint; preserve unrelated faults.
 GET STACKED DIAGNOSTICS v_conflict_name=CONSTRAINT_NAME,
   v_conflict_table=TABLE_NAME,v_conflict_schema=SCHEMA_NAME;
 IF v_conflict_schema='public' AND v_conflict_table='core_livingorganismevent'
   AND EXISTS(SELECT FROM pg_constraint c WHERE c.conrelid='public.core_livingorganismevent'::regclass
     AND c.conname=v_conflict_name AND c.contype='u'
     AND c.conkey=ARRAY[(SELECT a.attnum FROM pg_attribute a
       WHERE a.attrelid=c.conrelid AND a.attname='event_uuid')]) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 RAISE;
END """

# public.discp_input()
# Its landed text: 0075_cq_decisions.py (assembled by that migration). Time calls moved: 1 statement.
DISCP_INPUT_LANDED = r"""
DECLARE v_act public.core_discpostact; v_board public.core_discboard; v_lo uuid; v_circle uuid;
 v_source jsonb; v_stamp timestamptz:=statement_timestamp(); v_post public.discp_posts;
 v_thread public.discp_threads; v_count integer; v_version public.core_discpostversion;
BEGIN
 IF TG_TABLE_NAME='core_discpostact' THEN
   SELECT * INTO v_board FROM public.core_discboard WHERE board_uuid=NEW.board_uuid;
   SELECT organism_id INTO v_lo FROM public.core_livingorganism WHERE id=v_board.organism_id;
   PERFORM public.s015_lock_organisms(ARRAY[v_lo]);
   v_source:=public.discp_source(NEW.payload,NEW.actor_id,v_stamp);
   IF v_source IS NULL AND NEW.operation IN ('START','REPLY') THEN
     RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   IF v_source IS NULL OR v_source IS DISTINCT FROM NEW.authority_source OR
     public.discp_source(NEW.payload,NEW.actor_id,NEW.received_at) IS DISTINCT FROM v_source THEN
     RAISE EXCEPTION 'DISC_POST_AUTHORITY' USING ERRCODE='23514'; END IF;
   SELECT circle_uuid INTO v_circle FROM public.core_circle WHERE id=v_board.circle_id;
   IF NEW.execution_xid IS NOT NULL OR NEW.received_at>v_stamp
     OR NEW.payload->>'organism' IS DISTINCT FROM v_lo::text OR NEW.payload->>'circle' IS DISTINCT FROM v_circle::text
     OR NEW.payload->>'board' IS DISTINCT FROM NEW.board_uuid::text OR NEW.payload->>'request' IS DISTINCT FROM NEW.act_uuid::text
     OR NEW.payload->>'operation' IS DISTINCT FROM NEW.operation
     OR NEW.fingerprint IS DISTINCT FROM public.disc_fingerprint(NEW.payload)
     OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch) THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   PERFORM public.discp_validate(NEW.payload,NEW.actor_id,v_stamp);
   NEW.execution_xid:=pg_current_xact_id(); NEW.effective_at:=v_stamp; NEW.recorded_at:=v_stamp;
   NEW.author_hidden:=NULL; NEW.coordinator_hidden:=NULL; NEW.pinned:=NULL; NEW.closed:=NULL;
   IF NEW.operation IN ('HIDE','RESTORE') THEN
     SELECT * INTO v_post FROM public.discp_posts WHERE post_uuid=(NEW.payload->>'post')::uuid;
     NEW.author_hidden:=v_post.author_hidden; NEW.coordinator_hidden:=v_post.coordinator_hidden;
     IF NEW.payload->>'as_coordinator'='true' THEN
       NEW.coordinator_hidden:=NEW.operation='HIDE';
       IF NEW.operation='RESTORE' THEN NEW.author_hidden:=false; END IF;
     ELSE NEW.author_hidden:=NEW.operation='HIDE'; END IF;
   ELSIF NEW.operation IN ('PIN','UNPIN','CLOSE','REOPEN') THEN
     SELECT * INTO v_thread FROM public.discp_threads WHERE thread_uuid=(NEW.payload->>'thread')::uuid;
     NEW.pinned:=v_thread.pinned; NEW.closed:=v_thread.closed;
     IF NEW.operation IN ('PIN','UNPIN') THEN NEW.pinned:=NEW.operation='PIN'; ELSE NEW.closed:=NEW.operation='CLOSE'; END IF;
   END IF;
   RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='core_discthread' OR TG_TABLE_NAME='core_discpost' THEN
   SELECT * INTO v_act FROM public.core_discpostact WHERE act_uuid=NEW.creation_act;
 ELSIF TG_TABLE_NAME='core_discpostversion' THEN
   SELECT * INTO v_act FROM public.core_discpostact WHERE act_uuid=NEW.act_uuid;
 ELSE
   SELECT * INTO v_version FROM public.core_discpostversion WHERE version_uuid=NEW.version_uuid;
   SELECT * INTO v_act FROM public.core_discpostact WHERE act_uuid=v_version.act_uuid;
 END IF;
 IF v_act.act_uuid IS NULL OR v_act.execution_xid<>pg_current_xact_id() THEN
   RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='core_discthread' THEN
   IF v_act.operation<>'START' OR NEW.thread_uuid::text IS DISTINCT FROM v_act.payload->>'thread'
     OR NEW.category_uuid::text IS DISTINCT FROM v_act.payload->>'category' OR NEW.title IS DISTINCT FROM v_act.payload->>'title'
     OR NEW.topic_type IS DISTINCT FROM v_act.payload->>'type' OR NEW.record_kind<>'ORDINARY' THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='core_discpost' THEN
   SELECT count(*)+(SELECT count(*) FROM public.core_cqact a WHERE a.thread_uuid=NEW.thread_uuid AND a.operation IN ('SETTLE','CLOSE','NO_BALLOT')) INTO v_count FROM public.core_discpost p WHERE p.thread_uuid=NEW.thread_uuid;
   IF v_act.operation NOT IN ('START','REPLY') OR NEW.post_uuid::text IS DISTINCT FROM v_act.payload->>'post'
     OR NEW.thread_uuid::text IS DISTINCT FROM v_act.payload->>'thread' OR NEW.author_id<>v_act.actor_id
     OR NEW.post_number<>v_count+1 THEN RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='core_discpostversion' THEN
   SELECT * INTO v_post FROM public.discp_posts WHERE post_uuid=NEW.post_uuid;
   IF v_act.operation NOT IN ('START','REPLY','EDIT') OR NEW.version_uuid<>v_act.act_uuid
     OR NEW.post_uuid::text IS DISTINCT FROM v_act.payload->>'post'
     OR NEW.version_number<>coalesce(v_post.version_number,0)+1 OR NEW.predecessor IS DISTINCT FROM v_post.version_uuid
     OR NEW.quote_version::text IS DISTINCT FROM v_act.payload->>'quote'
     OR NEW.body_fingerprint IS DISTINCT FROM v_act.payload->>'body_fingerprint' THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
 ELSE
   IF v_version.body_fingerprint IS DISTINCT FROM public.discp_body_fingerprint(NEW.markdown) THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END """
DISCP_INPUT_CLOCK = r"""
DECLARE v_act public.core_discpostact; v_board public.core_discboard; v_lo uuid; v_circle uuid;
 v_source jsonb; v_stamp timestamptz:=public.intevia_clock_statement(); v_post public.discp_posts;
 v_thread public.discp_threads; v_count integer; v_version public.core_discpostversion;
BEGIN
 IF TG_TABLE_NAME='core_discpostact' THEN
   SELECT * INTO v_board FROM public.core_discboard WHERE board_uuid=NEW.board_uuid;
   SELECT organism_id INTO v_lo FROM public.core_livingorganism WHERE id=v_board.organism_id;
   PERFORM public.s015_lock_organisms(ARRAY[v_lo]);
   v_source:=public.discp_source(NEW.payload,NEW.actor_id,v_stamp);
   IF v_source IS NULL AND NEW.operation IN ('START','REPLY') THEN
     RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   IF v_source IS NULL OR v_source IS DISTINCT FROM NEW.authority_source OR
     public.discp_source(NEW.payload,NEW.actor_id,NEW.received_at) IS DISTINCT FROM v_source THEN
     RAISE EXCEPTION 'DISC_POST_AUTHORITY' USING ERRCODE='23514'; END IF;
   SELECT circle_uuid INTO v_circle FROM public.core_circle WHERE id=v_board.circle_id;
   IF NEW.execution_xid IS NOT NULL OR NEW.received_at>v_stamp
     OR NEW.payload->>'organism' IS DISTINCT FROM v_lo::text OR NEW.payload->>'circle' IS DISTINCT FROM v_circle::text
     OR NEW.payload->>'board' IS DISTINCT FROM NEW.board_uuid::text OR NEW.payload->>'request' IS DISTINCT FROM NEW.act_uuid::text
     OR NEW.payload->>'operation' IS DISTINCT FROM NEW.operation
     OR NEW.fingerprint IS DISTINCT FROM public.disc_fingerprint(NEW.payload)
     OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch) THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   PERFORM public.discp_validate(NEW.payload,NEW.actor_id,v_stamp);
   NEW.execution_xid:=pg_current_xact_id(); NEW.effective_at:=v_stamp; NEW.recorded_at:=v_stamp;
   NEW.author_hidden:=NULL; NEW.coordinator_hidden:=NULL; NEW.pinned:=NULL; NEW.closed:=NULL;
   IF NEW.operation IN ('HIDE','RESTORE') THEN
     SELECT * INTO v_post FROM public.discp_posts WHERE post_uuid=(NEW.payload->>'post')::uuid;
     NEW.author_hidden:=v_post.author_hidden; NEW.coordinator_hidden:=v_post.coordinator_hidden;
     IF NEW.payload->>'as_coordinator'='true' THEN
       NEW.coordinator_hidden:=NEW.operation='HIDE';
       IF NEW.operation='RESTORE' THEN NEW.author_hidden:=false; END IF;
     ELSE NEW.author_hidden:=NEW.operation='HIDE'; END IF;
   ELSIF NEW.operation IN ('PIN','UNPIN','CLOSE','REOPEN') THEN
     SELECT * INTO v_thread FROM public.discp_threads WHERE thread_uuid=(NEW.payload->>'thread')::uuid;
     NEW.pinned:=v_thread.pinned; NEW.closed:=v_thread.closed;
     IF NEW.operation IN ('PIN','UNPIN') THEN NEW.pinned:=NEW.operation='PIN'; ELSE NEW.closed:=NEW.operation='CLOSE'; END IF;
   END IF;
   RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='core_discthread' OR TG_TABLE_NAME='core_discpost' THEN
   SELECT * INTO v_act FROM public.core_discpostact WHERE act_uuid=NEW.creation_act;
 ELSIF TG_TABLE_NAME='core_discpostversion' THEN
   SELECT * INTO v_act FROM public.core_discpostact WHERE act_uuid=NEW.act_uuid;
 ELSE
   SELECT * INTO v_version FROM public.core_discpostversion WHERE version_uuid=NEW.version_uuid;
   SELECT * INTO v_act FROM public.core_discpostact WHERE act_uuid=v_version.act_uuid;
 END IF;
 IF v_act.act_uuid IS NULL OR v_act.execution_xid<>pg_current_xact_id() THEN
   RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='core_discthread' THEN
   IF v_act.operation<>'START' OR NEW.thread_uuid::text IS DISTINCT FROM v_act.payload->>'thread'
     OR NEW.category_uuid::text IS DISTINCT FROM v_act.payload->>'category' OR NEW.title IS DISTINCT FROM v_act.payload->>'title'
     OR NEW.topic_type IS DISTINCT FROM v_act.payload->>'type' OR NEW.record_kind<>'ORDINARY' THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='core_discpost' THEN
   SELECT count(*)+(SELECT count(*) FROM public.core_cqact a WHERE a.thread_uuid=NEW.thread_uuid AND a.operation IN ('SETTLE','CLOSE','NO_BALLOT')) INTO v_count FROM public.core_discpost p WHERE p.thread_uuid=NEW.thread_uuid;
   IF v_act.operation NOT IN ('START','REPLY') OR NEW.post_uuid::text IS DISTINCT FROM v_act.payload->>'post'
     OR NEW.thread_uuid::text IS DISTINCT FROM v_act.payload->>'thread' OR NEW.author_id<>v_act.actor_id
     OR NEW.post_number<>v_count+1 THEN RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='core_discpostversion' THEN
   SELECT * INTO v_post FROM public.discp_posts WHERE post_uuid=NEW.post_uuid;
   IF v_act.operation NOT IN ('START','REPLY','EDIT') OR NEW.version_uuid<>v_act.act_uuid
     OR NEW.post_uuid::text IS DISTINCT FROM v_act.payload->>'post'
     OR NEW.version_number<>coalesce(v_post.version_number,0)+1 OR NEW.predecessor IS DISTINCT FROM v_post.version_uuid
     OR NEW.quote_version::text IS DISTINCT FROM v_act.payload->>'quote'
     OR NEW.body_fingerprint IS DISTINCT FROM v_act.payload->>'body_fingerprint' THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
 ELSE
   IF v_version.body_fingerprint IS DISTINCT FROM public.discp_body_fingerprint(NEW.markdown) THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END """

# public.gi_complete()
# Its landed text: 0076_cq_authority.py (assembled by that migration). Time calls moved: 1 statement.
GI_COMPLETE_LANDED = r"""
DECLARE v_key uuid; v_act public.core_giact; v_gi public.core_guidingintention; v_org uuid;
 v_version public.core_giversion; v_cites jsonb; v_stamp timestamptz:=statement_timestamp();
BEGIN
 IF TG_TABLE_NAME='core_livingorganismevent' THEN v_key:=NEW.event_uuid;
 ELSIF TG_TABLE_NAME='core_guidingintention' THEN v_key:=NEW.creation_act;
 ELSIF TG_TABLE_NAME='core_gicitation' THEN
   SELECT act_uuid INTO v_key FROM public.core_giversion WHERE version_uuid=NEW.version_uuid;
 ELSE v_key:=NEW.act_uuid; END IF;
 SELECT * INTO STRICT v_act FROM public.core_giact WHERE act_uuid=v_key;
 SELECT * INTO STRICT v_gi FROM public.core_guidingintention WHERE gi_uuid=v_act.gi_uuid;
 SELECT organism_id INTO v_org FROM public.core_livingorganism WHERE id=v_gi.organism_id;
 SELECT * INTO STRICT v_version FROM public.core_giversion WHERE version_uuid=v_act.version_uuid;
 IF TG_TABLE_NAME='core_guidingintention' THEN
   IF v_act.operation<>'CREATE' OR v_act.gi_uuid<>NEW.gi_uuid THEN
     RAISE EXCEPTION 'GI_COMPLETE' USING ERRCODE='23514'; END IF;
 END IF;
 IF v_version.gi_uuid<>v_gi.gi_uuid OR NOT EXISTS(SELECT FROM public.core_livingorganismevent e
     WHERE e.event_uuid=v_key AND public.gi_event_matches(e))
   OR public.cq3_gi_source(v_gi.organism_id,v_gi.circle_id,v_act.actor_id,v_act.operation,
      v_act.payload,v_stamp,v_stamp) IS DISTINCT FROM v_act.authority_source
   OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
      WHERE i.id=v_act.actor_id AND i.access_epoch=v_act.actor_epoch AND i.access_state='active' AND u.is_active) THEN
   RAISE EXCEPTION 'GI_COMPLETE' USING ERRCODE='23514'; END IF;
 IF v_act.operation<>'RETIRE' THEN
   SELECT coalesce(jsonb_agg(jsonb_build_object('binding',c.binding_uuid,'version',c.law_version_id)
     ORDER BY c.binding_uuid),'[]'::jsonb) INTO v_cites FROM public.core_gicitation c WHERE c.version_uuid=v_version.version_uuid;
   IF v_version.act_uuid<>v_act.act_uuid OR v_cites IS DISTINCT FROM v_act.payload->'citations'
     OR public.gi_laws_ok(v_org,v_gi.circle_id,v_cites,v_stamp,v_stamp) IS NOT TRUE THEN
     RAISE EXCEPTION 'GI_CITATIONS_COMPLETE' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NULL;
END """
GI_COMPLETE_CLOCK = r"""
DECLARE v_key uuid; v_act public.core_giact; v_gi public.core_guidingintention; v_org uuid;
 v_version public.core_giversion; v_cites jsonb; v_stamp timestamptz:=public.intevia_clock_statement();
BEGIN
 IF TG_TABLE_NAME='core_livingorganismevent' THEN v_key:=NEW.event_uuid;
 ELSIF TG_TABLE_NAME='core_guidingintention' THEN v_key:=NEW.creation_act;
 ELSIF TG_TABLE_NAME='core_gicitation' THEN
   SELECT act_uuid INTO v_key FROM public.core_giversion WHERE version_uuid=NEW.version_uuid;
 ELSE v_key:=NEW.act_uuid; END IF;
 SELECT * INTO STRICT v_act FROM public.core_giact WHERE act_uuid=v_key;
 SELECT * INTO STRICT v_gi FROM public.core_guidingintention WHERE gi_uuid=v_act.gi_uuid;
 SELECT organism_id INTO v_org FROM public.core_livingorganism WHERE id=v_gi.organism_id;
 SELECT * INTO STRICT v_version FROM public.core_giversion WHERE version_uuid=v_act.version_uuid;
 IF TG_TABLE_NAME='core_guidingintention' THEN
   IF v_act.operation<>'CREATE' OR v_act.gi_uuid<>NEW.gi_uuid THEN
     RAISE EXCEPTION 'GI_COMPLETE' USING ERRCODE='23514'; END IF;
 END IF;
 IF v_version.gi_uuid<>v_gi.gi_uuid OR NOT EXISTS(SELECT FROM public.core_livingorganismevent e
     WHERE e.event_uuid=v_key AND public.gi_event_matches(e))
   OR public.cq3_gi_source(v_gi.organism_id,v_gi.circle_id,v_act.actor_id,v_act.operation,
      v_act.payload,v_stamp,v_stamp) IS DISTINCT FROM v_act.authority_source
   OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
      WHERE i.id=v_act.actor_id AND i.access_epoch=v_act.actor_epoch AND i.access_state='active' AND u.is_active) THEN
   RAISE EXCEPTION 'GI_COMPLETE' USING ERRCODE='23514'; END IF;
 IF v_act.operation<>'RETIRE' THEN
   SELECT coalesce(jsonb_agg(jsonb_build_object('binding',c.binding_uuid,'version',c.law_version_id)
     ORDER BY c.binding_uuid),'[]'::jsonb) INTO v_cites FROM public.core_gicitation c WHERE c.version_uuid=v_version.version_uuid;
   IF v_version.act_uuid<>v_act.act_uuid OR v_cites IS DISTINCT FROM v_act.payload->'citations'
     OR public.gi_laws_ok(v_org,v_gi.circle_id,v_cites,v_stamp,v_stamp) IS NOT TRUE THEN
     RAISE EXCEPTION 'GI_CITATIONS_COMPLETE' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NULL;
END """

# public.gi_execute(jsonb, bigint, bigint, timestamp with time zone)
# Its landed text: 0076_cq_authority.py (assembled by that migration). Time calls moved: 1 statement.
GI_EXECUTE_LANDED = r"""
DECLARE v_lo public.core_livingorganism; v_circle bigint; v_gi public.core_guidingintention;
 v_old public.core_giact; v_source jsonb; v_key uuid:=(p_payload->>'request')::uuid;
 v_id uuid:=(p_payload->>'gi')::uuid; v_operation text:=p_payload->>'operation';
 v_stamp timestamptz:=statement_timestamp(); v_version uuid; v_number integer;
 v_head public.core_livingorganismevent; v_state text; v_fp text; v_event bigint;
BEGIN
 SELECT * INTO v_lo FROM public.core_livingorganism WHERE organism_id=(p_payload->>'organism')::uuid;
 IF v_lo.id IS NULL THEN RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514'; END IF;
 PERFORM public.s015_lock_organisms(ARRAY[v_lo.organism_id]);
 IF p_payload->>'circle' IS NOT NULL THEN
   SELECT id INTO v_circle FROM public.core_circle WHERE circle_uuid=(p_payload->>'circle')::uuid AND parent_organism_id=v_lo.id;
   IF v_circle IS NULL THEN RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514'; END IF;
 END IF;
 v_source:=public.cq3_gi_source(v_lo.id,v_circle,p_actor,v_operation,p_payload,v_stamp,v_stamp);
 IF v_source IS NULL OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=p_actor AND i.access_epoch=p_epoch) THEN
   RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514'; END IF;
 SELECT * INTO v_old FROM public.core_giact WHERE act_uuid=v_key;
 IF v_old.act_uuid IS NOT NULL THEN
   IF NOT EXISTS(SELECT FROM public.core_guidingintention previous_owner
       WHERE previous_owner.gi_uuid=v_old.gi_uuid AND previous_owner.organism_id=v_lo.id
         AND previous_owner.circle_id IS NOT DISTINCT FROM v_circle) THEN
     RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514'; END IF;
   IF v_old.payload IS DISTINCT FROM p_payload OR v_old.actor_id<>p_actor OR v_old.actor_epoch<>p_epoch THEN
     RAISE EXCEPTION 'GI_REQUEST_CONFLICT' USING ERRCODE='23514'; END IF;
   RETURN jsonb_build_object('gi',v_old.gi_uuid,'act',v_key,'version',v_old.version_uuid,'operation',v_old.operation);
 END IF;
 SELECT * INTO v_gi FROM public.core_guidingintention WHERE gi_uuid=v_id;
 IF v_operation='CREATE' THEN
   IF v_gi.gi_uuid IS NOT NULL THEN RAISE EXCEPTION 'GI_PREDECESSOR' USING ERRCODE='23514'; END IF;
   INSERT INTO public.core_guidingintention VALUES(v_id,v_lo.id,v_circle,v_key);
 ELSIF v_gi.gi_uuid IS NULL OR v_gi.organism_id<>v_lo.id OR v_gi.circle_id IS DISTINCT FROM v_circle THEN
   RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514';
 END IF;
 v_version:=CASE WHEN v_operation='RETIRE' THEN (p_payload->>'predecessor')::uuid ELSE gen_random_uuid() END;
 v_fp:=public.gi_payload_fingerprint(p_payload);
 INSERT INTO public.core_giact(act_uuid,gi_uuid,operation,version_uuid,actor_id,actor_epoch,authority_source,
   payload,fingerprint,received_at) VALUES(v_key,v_id,v_operation,v_version,p_actor,p_epoch,v_source,p_payload,v_fp,p_received);
 IF v_operation<>'RETIRE' THEN
   SELECT coalesce(max(version_number),0)+1 INTO v_number FROM public.core_giversion WHERE gi_uuid=v_id;
   INSERT INTO public.core_giversion VALUES(v_version,v_id,v_number,(p_payload->>'predecessor')::uuid,v_key,
     p_payload->>'title',p_payload->>'content');
   INSERT INTO public.core_gicitation SELECT v_version,(x->>'binding')::uuid,(x->>'version')::bigint
     FROM jsonb_array_elements(p_payload->'citations') x;
 END IF;
 SELECT * INTO STRICT v_lo FROM public.core_livingorganism WHERE id=v_lo.id FOR UPDATE;
 SELECT * INTO STRICT v_head FROM public.core_livingorganismevent WHERE id=v_lo.head_event_id;
 v_state:=public.s015_fold_effective_state('core_livingorganism',v_lo.id,v_stamp,v_stamp);
 v_event:=public.s015_append_event('core_livingorganism',v_lo.id,'RECORD_GUIDING_INTENTION',v_state,
   v_stamp,p_received,p_received,p_actor,v_lo.founding_authority_basis_id,
   public.gi_decision_reference(v_source,v_fp),'gi-act:'||v_key,'gi:'||v_key,v_fp,'{}'::jsonb,
   NULL,v_key,v_head.id,v_head.sequence::integer,NULL::bigint,NULL::text,'gi:'||v_key);
 RETURN jsonb_build_object('gi',v_id,'act',v_key,'version',v_version,'operation',v_operation);
END """
GI_EXECUTE_CLOCK = r"""
DECLARE v_lo public.core_livingorganism; v_circle bigint; v_gi public.core_guidingintention;
 v_old public.core_giact; v_source jsonb; v_key uuid:=(p_payload->>'request')::uuid;
 v_id uuid:=(p_payload->>'gi')::uuid; v_operation text:=p_payload->>'operation';
 v_stamp timestamptz:=public.intevia_clock_statement(); v_version uuid; v_number integer;
 v_head public.core_livingorganismevent; v_state text; v_fp text; v_event bigint;
BEGIN
 SELECT * INTO v_lo FROM public.core_livingorganism WHERE organism_id=(p_payload->>'organism')::uuid;
 IF v_lo.id IS NULL THEN RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514'; END IF;
 PERFORM public.s015_lock_organisms(ARRAY[v_lo.organism_id]);
 IF p_payload->>'circle' IS NOT NULL THEN
   SELECT id INTO v_circle FROM public.core_circle WHERE circle_uuid=(p_payload->>'circle')::uuid AND parent_organism_id=v_lo.id;
   IF v_circle IS NULL THEN RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514'; END IF;
 END IF;
 v_source:=public.cq3_gi_source(v_lo.id,v_circle,p_actor,v_operation,p_payload,v_stamp,v_stamp);
 IF v_source IS NULL OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=p_actor AND i.access_epoch=p_epoch) THEN
   RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514'; END IF;
 SELECT * INTO v_old FROM public.core_giact WHERE act_uuid=v_key;
 IF v_old.act_uuid IS NOT NULL THEN
   IF NOT EXISTS(SELECT FROM public.core_guidingintention previous_owner
       WHERE previous_owner.gi_uuid=v_old.gi_uuid AND previous_owner.organism_id=v_lo.id
         AND previous_owner.circle_id IS NOT DISTINCT FROM v_circle) THEN
     RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514'; END IF;
   IF v_old.payload IS DISTINCT FROM p_payload OR v_old.actor_id<>p_actor OR v_old.actor_epoch<>p_epoch THEN
     RAISE EXCEPTION 'GI_REQUEST_CONFLICT' USING ERRCODE='23514'; END IF;
   RETURN jsonb_build_object('gi',v_old.gi_uuid,'act',v_key,'version',v_old.version_uuid,'operation',v_old.operation);
 END IF;
 SELECT * INTO v_gi FROM public.core_guidingintention WHERE gi_uuid=v_id;
 IF v_operation='CREATE' THEN
   IF v_gi.gi_uuid IS NOT NULL THEN RAISE EXCEPTION 'GI_PREDECESSOR' USING ERRCODE='23514'; END IF;
   INSERT INTO public.core_guidingintention VALUES(v_id,v_lo.id,v_circle,v_key);
 ELSIF v_gi.gi_uuid IS NULL OR v_gi.organism_id<>v_lo.id OR v_gi.circle_id IS DISTINCT FROM v_circle THEN
   RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514';
 END IF;
 v_version:=CASE WHEN v_operation='RETIRE' THEN (p_payload->>'predecessor')::uuid ELSE gen_random_uuid() END;
 v_fp:=public.gi_payload_fingerprint(p_payload);
 INSERT INTO public.core_giact(act_uuid,gi_uuid,operation,version_uuid,actor_id,actor_epoch,authority_source,
   payload,fingerprint,received_at) VALUES(v_key,v_id,v_operation,v_version,p_actor,p_epoch,v_source,p_payload,v_fp,p_received);
 IF v_operation<>'RETIRE' THEN
   SELECT coalesce(max(version_number),0)+1 INTO v_number FROM public.core_giversion WHERE gi_uuid=v_id;
   INSERT INTO public.core_giversion VALUES(v_version,v_id,v_number,(p_payload->>'predecessor')::uuid,v_key,
     p_payload->>'title',p_payload->>'content');
   INSERT INTO public.core_gicitation SELECT v_version,(x->>'binding')::uuid,(x->>'version')::bigint
     FROM jsonb_array_elements(p_payload->'citations') x;
 END IF;
 SELECT * INTO STRICT v_lo FROM public.core_livingorganism WHERE id=v_lo.id FOR UPDATE;
 SELECT * INTO STRICT v_head FROM public.core_livingorganismevent WHERE id=v_lo.head_event_id;
 v_state:=public.s015_fold_effective_state('core_livingorganism',v_lo.id,v_stamp,v_stamp);
 v_event:=public.s015_append_event('core_livingorganism',v_lo.id,'RECORD_GUIDING_INTENTION',v_state,
   v_stamp,p_received,p_received,p_actor,v_lo.founding_authority_basis_id,
   public.gi_decision_reference(v_source,v_fp),'gi-act:'||v_key,'gi:'||v_key,v_fp,'{}'::jsonb,
   NULL,v_key,v_head.id,v_head.sequence::integer,NULL::bigint,NULL::text,'gi:'||v_key);
 RETURN jsonb_build_object('gi',v_id,'act',v_key,'version',v_version,'operation',v_operation);
END """

# public.gi_input()
# Its landed text: 0076_cq_authority.py (assembled by that migration). Time calls moved: 1 statement.
GI_INPUT_LANDED = r"""
DECLARE v_gi public.core_guidingintention; v_act public.core_giact; v_prior public.core_giversion;
 v_org uuid; v_count integer; v_circle uuid; v_stamp timestamptz:=statement_timestamp(); v_source jsonb;
BEGIN
 IF TG_TABLE_NAME='core_guidingintention' THEN
   SELECT organism_id INTO v_org FROM public.core_livingorganism WHERE id=NEW.organism_id;
   PERFORM public.s015_lock_organisms(ARRAY[v_org]);
   IF NEW.circle_id IS NOT NULL AND NOT EXISTS(SELECT FROM public.core_circle c
     WHERE c.id=NEW.circle_id AND c.parent_organism_id=NEW.organism_id) THEN
     RAISE EXCEPTION 'GI_OWNER' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 ELSIF TG_TABLE_NAME='core_giact' THEN
   SELECT * INTO STRICT v_gi FROM public.core_guidingintention WHERE gi_uuid=NEW.gi_uuid;
   SELECT organism_id INTO v_org FROM public.core_livingorganism WHERE id=v_gi.organism_id;
   -- The authenticated service holds the actor first. Direct inserts independently
   -- serialize on the owner; no new actor lock is acquired after this lock.
   PERFORM public.s015_lock_organisms(ARRAY[v_org]);
   IF NEW.execution_xid IS NOT NULL THEN RAISE EXCEPTION 'GI_XID' USING ERRCODE='23514'; END IF;
   NEW.execution_xid:=pg_current_xact_id(); NEW.effective_at:=v_stamp; NEW.recorded_at:=v_stamp;
   SELECT circle_uuid INTO v_circle FROM public.core_circle WHERE id=v_gi.circle_id;
   IF NEW.received_at>v_stamp OR NEW.payload->>'organism' IS DISTINCT FROM v_org::text
     OR NEW.payload->>'circle' IS DISTINCT FROM v_circle::text
     OR NEW.payload->>'gi' IS DISTINCT FROM NEW.gi_uuid::text
     OR NEW.payload->>'operation' IS DISTINCT FROM NEW.operation
     OR NEW.payload->>'request' IS DISTINCT FROM NEW.act_uuid::text
     OR NEW.payload->>'route' IS DISTINCT FROM NEW.authority_source->>'route'
     OR NEW.fingerprint IS DISTINCT FROM public.gi_payload_fingerprint(NEW.payload)
     OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
        WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch AND i.access_state='active' AND u.is_active) THEN
     RAISE EXCEPTION 'GI_PAYLOAD' USING ERRCODE='23514'; END IF;
   v_source:=public.cq3_gi_source(v_gi.organism_id,v_gi.circle_id,NEW.actor_id,NEW.operation,
     NEW.payload,NEW.received_at,NEW.received_at);
   IF v_source IS NULL OR v_source IS DISTINCT FROM NEW.authority_source
     OR public.cq3_gi_source(v_gi.organism_id,v_gi.circle_id,NEW.actor_id,NEW.operation,
       NEW.payload,v_stamp,v_stamp) IS DISTINCT FROM v_source THEN
     RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514'; END IF;
   IF EXISTS(SELECT FROM public.core_giact a WHERE a.gi_uuid=NEW.gi_uuid AND a.operation='RETIRE') THEN
     RAISE EXCEPTION 'GI_RETIRED' USING ERRCODE='23514'; END IF;
   SELECT * INTO v_prior FROM public.core_giversion v WHERE v.gi_uuid=NEW.gi_uuid ORDER BY version_number DESC LIMIT 1;
   IF (NEW.operation='CREATE' AND (v_prior.version_uuid IS NOT NULL OR NEW.payload->>'predecessor' IS NOT NULL
         OR v_gi.creation_act<>NEW.act_uuid))
     OR (NEW.operation<>'CREATE' AND (v_prior.version_uuid IS NULL OR
       NEW.payload->>'predecessor' IS DISTINCT FROM v_prior.version_uuid::text)) THEN
     RAISE EXCEPTION 'GI_PREDECESSOR' USING ERRCODE='23514'; END IF;
   IF NEW.operation='RETIRE' THEN
     IF NEW.version_uuid IS DISTINCT FROM v_prior.version_uuid OR NEW.payload->>'title' IS NOT NULL
       OR NEW.payload->>'content' IS NOT NULL OR NEW.payload->'citations' IS DISTINCT FROM '[]'::jsonb THEN
       RAISE EXCEPTION 'GI_RETIRE_SHAPE' USING ERRCODE='23514'; END IF;
   ELSE
     PERFORM r.id FROM public.core_libraryresource r WHERE r.id IN (
       SELECT b.resource_id FROM jsonb_array_elements(NEW.payload->'citations') x
       JOIN public.core_organismlawbinding b ON b.binding_uuid=(x->>'binding')::uuid
     ) ORDER BY r.id FOR SHARE;
     IF public.gi_laws_ok(v_org,v_gi.circle_id,NEW.payload->'citations',NEW.received_at,NEW.received_at) IS NOT TRUE
       OR public.gi_laws_ok(v_org,v_gi.circle_id,NEW.payload->'citations',v_stamp,v_stamp) IS NOT TRUE THEN
       RAISE EXCEPTION 'GI_LAW_REQUIRED' USING ERRCODE='23514'; END IF;
   END IF;
   RETURN NEW;
 ELSIF TG_TABLE_NAME='core_giversion' THEN
   SELECT * INTO STRICT v_act FROM public.core_giact WHERE act_uuid=NEW.act_uuid;
   SELECT count(*) INTO v_count FROM public.core_giversion WHERE gi_uuid=NEW.gi_uuid;
   IF v_act.execution_xid IS DISTINCT FROM pg_current_xact_id() OR v_act.operation NOT IN ('CREATE','REVISE')
     OR v_act.version_uuid<>NEW.version_uuid OR v_act.gi_uuid<>NEW.gi_uuid OR NEW.version_number<>v_count+1
     OR NEW.predecessor::text IS DISTINCT FROM v_act.payload->>'predecessor'
     OR NEW.title IS DISTINCT FROM v_act.payload->>'title' OR NEW.content IS DISTINCT FROM v_act.payload->>'content' THEN
     RAISE EXCEPTION 'GI_VERSION' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 ELSE
   SELECT a.* INTO STRICT v_act FROM public.core_giversion v JOIN public.core_giact a ON a.act_uuid=v.act_uuid
     WHERE v.version_uuid=NEW.version_uuid;
   IF v_act.execution_xid IS DISTINCT FROM pg_current_xact_id() OR NOT EXISTS(
     SELECT FROM jsonb_array_elements(v_act.payload->'citations') x
     JOIN public.core_organismlawbinding b ON b.binding_uuid=(x->>'binding')::uuid
     WHERE b.binding_uuid=NEW.binding_uuid AND b.version_id=NEW.law_version_id
       AND (x->>'version')::bigint=NEW.law_version_id) THEN
     RAISE EXCEPTION 'GI_CITATION' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 END IF;
END """
GI_INPUT_CLOCK = r"""
DECLARE v_gi public.core_guidingintention; v_act public.core_giact; v_prior public.core_giversion;
 v_org uuid; v_count integer; v_circle uuid; v_stamp timestamptz:=public.intevia_clock_statement(); v_source jsonb;
BEGIN
 IF TG_TABLE_NAME='core_guidingintention' THEN
   SELECT organism_id INTO v_org FROM public.core_livingorganism WHERE id=NEW.organism_id;
   PERFORM public.s015_lock_organisms(ARRAY[v_org]);
   IF NEW.circle_id IS NOT NULL AND NOT EXISTS(SELECT FROM public.core_circle c
     WHERE c.id=NEW.circle_id AND c.parent_organism_id=NEW.organism_id) THEN
     RAISE EXCEPTION 'GI_OWNER' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 ELSIF TG_TABLE_NAME='core_giact' THEN
   SELECT * INTO STRICT v_gi FROM public.core_guidingintention WHERE gi_uuid=NEW.gi_uuid;
   SELECT organism_id INTO v_org FROM public.core_livingorganism WHERE id=v_gi.organism_id;
   -- The authenticated service holds the actor first. Direct inserts independently
   -- serialize on the owner; no new actor lock is acquired after this lock.
   PERFORM public.s015_lock_organisms(ARRAY[v_org]);
   IF NEW.execution_xid IS NOT NULL THEN RAISE EXCEPTION 'GI_XID' USING ERRCODE='23514'; END IF;
   NEW.execution_xid:=pg_current_xact_id(); NEW.effective_at:=v_stamp; NEW.recorded_at:=v_stamp;
   SELECT circle_uuid INTO v_circle FROM public.core_circle WHERE id=v_gi.circle_id;
   IF NEW.received_at>v_stamp OR NEW.payload->>'organism' IS DISTINCT FROM v_org::text
     OR NEW.payload->>'circle' IS DISTINCT FROM v_circle::text
     OR NEW.payload->>'gi' IS DISTINCT FROM NEW.gi_uuid::text
     OR NEW.payload->>'operation' IS DISTINCT FROM NEW.operation
     OR NEW.payload->>'request' IS DISTINCT FROM NEW.act_uuid::text
     OR NEW.payload->>'route' IS DISTINCT FROM NEW.authority_source->>'route'
     OR NEW.fingerprint IS DISTINCT FROM public.gi_payload_fingerprint(NEW.payload)
     OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
        WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch AND i.access_state='active' AND u.is_active) THEN
     RAISE EXCEPTION 'GI_PAYLOAD' USING ERRCODE='23514'; END IF;
   v_source:=public.cq3_gi_source(v_gi.organism_id,v_gi.circle_id,NEW.actor_id,NEW.operation,
     NEW.payload,NEW.received_at,NEW.received_at);
   IF v_source IS NULL OR v_source IS DISTINCT FROM NEW.authority_source
     OR public.cq3_gi_source(v_gi.organism_id,v_gi.circle_id,NEW.actor_id,NEW.operation,
       NEW.payload,v_stamp,v_stamp) IS DISTINCT FROM v_source THEN
     RAISE EXCEPTION 'GI_AUTHORITY' USING ERRCODE='23514'; END IF;
   IF EXISTS(SELECT FROM public.core_giact a WHERE a.gi_uuid=NEW.gi_uuid AND a.operation='RETIRE') THEN
     RAISE EXCEPTION 'GI_RETIRED' USING ERRCODE='23514'; END IF;
   SELECT * INTO v_prior FROM public.core_giversion v WHERE v.gi_uuid=NEW.gi_uuid ORDER BY version_number DESC LIMIT 1;
   IF (NEW.operation='CREATE' AND (v_prior.version_uuid IS NOT NULL OR NEW.payload->>'predecessor' IS NOT NULL
         OR v_gi.creation_act<>NEW.act_uuid))
     OR (NEW.operation<>'CREATE' AND (v_prior.version_uuid IS NULL OR
       NEW.payload->>'predecessor' IS DISTINCT FROM v_prior.version_uuid::text)) THEN
     RAISE EXCEPTION 'GI_PREDECESSOR' USING ERRCODE='23514'; END IF;
   IF NEW.operation='RETIRE' THEN
     IF NEW.version_uuid IS DISTINCT FROM v_prior.version_uuid OR NEW.payload->>'title' IS NOT NULL
       OR NEW.payload->>'content' IS NOT NULL OR NEW.payload->'citations' IS DISTINCT FROM '[]'::jsonb THEN
       RAISE EXCEPTION 'GI_RETIRE_SHAPE' USING ERRCODE='23514'; END IF;
   ELSE
     PERFORM r.id FROM public.core_libraryresource r WHERE r.id IN (
       SELECT b.resource_id FROM jsonb_array_elements(NEW.payload->'citations') x
       JOIN public.core_organismlawbinding b ON b.binding_uuid=(x->>'binding')::uuid
     ) ORDER BY r.id FOR SHARE;
     IF public.gi_laws_ok(v_org,v_gi.circle_id,NEW.payload->'citations',NEW.received_at,NEW.received_at) IS NOT TRUE
       OR public.gi_laws_ok(v_org,v_gi.circle_id,NEW.payload->'citations',v_stamp,v_stamp) IS NOT TRUE THEN
       RAISE EXCEPTION 'GI_LAW_REQUIRED' USING ERRCODE='23514'; END IF;
   END IF;
   RETURN NEW;
 ELSIF TG_TABLE_NAME='core_giversion' THEN
   SELECT * INTO STRICT v_act FROM public.core_giact WHERE act_uuid=NEW.act_uuid;
   SELECT count(*) INTO v_count FROM public.core_giversion WHERE gi_uuid=NEW.gi_uuid;
   IF v_act.execution_xid IS DISTINCT FROM pg_current_xact_id() OR v_act.operation NOT IN ('CREATE','REVISE')
     OR v_act.version_uuid<>NEW.version_uuid OR v_act.gi_uuid<>NEW.gi_uuid OR NEW.version_number<>v_count+1
     OR NEW.predecessor::text IS DISTINCT FROM v_act.payload->>'predecessor'
     OR NEW.title IS DISTINCT FROM v_act.payload->>'title' OR NEW.content IS DISTINCT FROM v_act.payload->>'content' THEN
     RAISE EXCEPTION 'GI_VERSION' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 ELSE
   SELECT a.* INTO STRICT v_act FROM public.core_giversion v JOIN public.core_giact a ON a.act_uuid=v.act_uuid
     WHERE v.version_uuid=NEW.version_uuid;
   IF v_act.execution_xid IS DISTINCT FROM pg_current_xact_id() OR NOT EXISTS(
     SELECT FROM jsonb_array_elements(v_act.payload->'citations') x
     JOIN public.core_organismlawbinding b ON b.binding_uuid=(x->>'binding')::uuid
     WHERE b.binding_uuid=NEW.binding_uuid AND b.version_id=NEW.law_version_id
       AND (x->>'version')::bigint=NEW.law_version_id) THEN
     RAISE EXCEPTION 'GI_CITATION' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 END IF;
END """

# public.lib_policy_commit()
# Its landed text: 0076_cq_authority.py (assembled by that migration). Time calls moved: 1 statement.
LIB_POLICY_COMMIT_LANDED = r"""
DECLARE version_ bigint; meta public.core_libpolicyversion; fact public.core_libpublicationfact; stamp timestamptz:=statement_timestamp();
BEGIN
 version_:=NEW.version_id;
 SELECT * INTO STRICT meta FROM public.core_libpolicyversion WHERE version_id=version_;
 IF TG_TABLE_NAME='core_libpolicyversion' THEN
  IF public.lib_policy_source(meta.organism_id,meta.actor_id,meta.authority_source->>'route',stamp,stamp) IS DISTINCT FROM meta.authority_source THEN
   RAISE EXCEPTION 'LIB: Policy authority changed before commit.' USING ERRCODE='23514'; END IF;
  IF meta.declared_self AND NOT EXISTS(SELECT FROM public.core_libauthorclaim WHERE version_id=version_
     AND author_id=meta.actor_id AND actor_id=meta.actor_id AND inherited_from IS NULL) THEN
   RAISE EXCEPTION 'LIB: the declared own-authorship act must be recorded with this version.' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_TABLE_NAME='core_libreleasepermission' THEN
  IF public.cq3_release_source(meta.organism_id,NEW.actor_id,NEW.version_id,NEW.authority_source,stamp,stamp) IS DISTINCT FROM NEW.authority_source THEN
   RAISE EXCEPTION 'LIB: release authority changed before commit.' USING ERRCODE='23514'; END IF;
 END IF;
 IF NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
   WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch AND i.access_state='active' AND u.is_active) THEN
   RAISE EXCEPTION 'LIB: the act''s signed-in actor changed before commit.' USING ERRCODE='23514'; END IF;
 FOR fact IN SELECT * FROM public.core_libpublicationfact WHERE version_id=version_ LOOP
  IF public.cq3_release_source(fact.organism_id,fact.actor_id,fact.version_id,fact.authority_source,stamp,stamp) IS DISTINCT FROM fact.authority_source THEN
   RAISE EXCEPTION 'LIB: publication authority changed before commit.' USING ERRCODE='23514'; END IF;
  IF fact.kind='ACCEPTANCE' AND NOT EXISTS(SELECT FROM public.core_libpublicationfact pub WHERE pub.operation_uuid=fact.operation_uuid
    AND pub.kind='PUBLICATION' AND pub.version_id=fact.version_id AND pub.actor_id=fact.actor_id AND pub.authority_source=fact.authority_source
    AND pub.declaration_id=fact.declaration_id AND pub.permission_uuid=fact.permission_uuid) THEN
   RAISE EXCEPTION 'LIB: acceptance and publication must commit together.' USING ERRCODE='23514'; END IF;
  IF fact.kind='PUBLICATION' AND (NOT EXISTS(SELECT FROM public.core_libraryresourcetransition t
      WHERE t.id=fact.transition_id AND t.version_id=fact.version_id AND t.actor_id=fact.actor_id
      AND t.command='publish_library_resource' AND t.to_state='published' AND t.authority_reference='lib-policy:'||fact.authority_source::text)
    OR (fact.declaration_id IS NOT NULL AND NOT EXISTS(SELECT FROM public.core_libpublicationfact acc
      WHERE acc.operation_uuid=fact.operation_uuid AND acc.kind='ACCEPTANCE' AND acc.version_id=fact.version_id
      AND acc.actor_id=fact.actor_id AND acc.authority_source=fact.authority_source AND acc.declaration_id=fact.declaration_id
      AND acc.permission_uuid=fact.permission_uuid))) THEN
   RAISE EXCEPTION 'LIB: publication must cite its exact transition and required acceptance.' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NULL;
END """
LIB_POLICY_COMMIT_CLOCK = r"""
DECLARE version_ bigint; meta public.core_libpolicyversion; fact public.core_libpublicationfact; stamp timestamptz:=public.intevia_clock_statement();
BEGIN
 version_:=NEW.version_id;
 SELECT * INTO STRICT meta FROM public.core_libpolicyversion WHERE version_id=version_;
 IF TG_TABLE_NAME='core_libpolicyversion' THEN
  IF public.lib_policy_source(meta.organism_id,meta.actor_id,meta.authority_source->>'route',stamp,stamp) IS DISTINCT FROM meta.authority_source THEN
   RAISE EXCEPTION 'LIB: Policy authority changed before commit.' USING ERRCODE='23514'; END IF;
  IF meta.declared_self AND NOT EXISTS(SELECT FROM public.core_libauthorclaim WHERE version_id=version_
     AND author_id=meta.actor_id AND actor_id=meta.actor_id AND inherited_from IS NULL) THEN
   RAISE EXCEPTION 'LIB: the declared own-authorship act must be recorded with this version.' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_TABLE_NAME='core_libreleasepermission' THEN
  IF public.cq3_release_source(meta.organism_id,NEW.actor_id,NEW.version_id,NEW.authority_source,stamp,stamp) IS DISTINCT FROM NEW.authority_source THEN
   RAISE EXCEPTION 'LIB: release authority changed before commit.' USING ERRCODE='23514'; END IF;
 END IF;
 IF NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
   WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch AND i.access_state='active' AND u.is_active) THEN
   RAISE EXCEPTION 'LIB: the act''s signed-in actor changed before commit.' USING ERRCODE='23514'; END IF;
 FOR fact IN SELECT * FROM public.core_libpublicationfact WHERE version_id=version_ LOOP
  IF public.cq3_release_source(fact.organism_id,fact.actor_id,fact.version_id,fact.authority_source,stamp,stamp) IS DISTINCT FROM fact.authority_source THEN
   RAISE EXCEPTION 'LIB: publication authority changed before commit.' USING ERRCODE='23514'; END IF;
  IF fact.kind='ACCEPTANCE' AND NOT EXISTS(SELECT FROM public.core_libpublicationfact pub WHERE pub.operation_uuid=fact.operation_uuid
    AND pub.kind='PUBLICATION' AND pub.version_id=fact.version_id AND pub.actor_id=fact.actor_id AND pub.authority_source=fact.authority_source
    AND pub.declaration_id=fact.declaration_id AND pub.permission_uuid=fact.permission_uuid) THEN
   RAISE EXCEPTION 'LIB: acceptance and publication must commit together.' USING ERRCODE='23514'; END IF;
  IF fact.kind='PUBLICATION' AND (NOT EXISTS(SELECT FROM public.core_libraryresourcetransition t
      WHERE t.id=fact.transition_id AND t.version_id=fact.version_id AND t.actor_id=fact.actor_id
      AND t.command='publish_library_resource' AND t.to_state='published' AND t.authority_reference='lib-policy:'||fact.authority_source::text)
    OR (fact.declaration_id IS NOT NULL AND NOT EXISTS(SELECT FROM public.core_libpublicationfact acc
      WHERE acc.operation_uuid=fact.operation_uuid AND acc.kind='ACCEPTANCE' AND acc.version_id=fact.version_id
      AND acc.actor_id=fact.actor_id AND acc.authority_source=fact.authority_source AND acc.declaration_id=fact.declaration_id
      AND acc.permission_uuid=fact.permission_uuid))) THEN
   RAISE EXCEPTION 'LIB: publication must cite its exact transition and required acceptance.' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NULL;
END """

# public.lib_policy_input()
# Its landed text: 0076_cq_authority.py (assembled by that migration). Time calls moved: 1 statement.
LIB_POLICY_INPUT_LANDED = r"""
DECLARE v public.core_libraryresourceversion; old public.core_libpolicyversion; stamp timestamptz:=statement_timestamp();
BEGIN
 NEW.recorded_at:=stamp; NEW.effective_at:=stamp;
 IF NEW.received_at>stamp OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch AND i.access_state='active' AND u.is_active) THEN
  RAISE EXCEPTION 'LIB: the signed-in actor or act time is unavailable.' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='core_libpolicyversion' THEN
  SELECT * INTO STRICT v FROM public.core_libraryresourceversion WHERE id=NEW.version_id;
  PERFORM 1 FROM public.core_libraryresource WHERE id=v.resource_id FOR UPDATE;
  SELECT * INTO old FROM public.core_libpolicyversion WHERE version_id=v.predecessor_id;
  IF (NEW.declared_self AND NOT NEW.actor_id=ANY(NEW.author_ids))
    OR NEW.author_ids IS DISTINCT FROM ARRAY(SELECT DISTINCT a FROM unnest(NEW.author_ids) a ORDER BY a)
    OR NEW.content_sha256 IS DISTINCT FROM encode(sha256(convert_to(v.content,'UTF8')),'hex')
    OR v.created_by_id<>NEW.actor_id OR v.created_at<>NEW.received_at
    OR NOT EXISTS(SELECT FROM public.core_libraryresource r WHERE r.id=v.resource_id AND r.current_version_id=v.id AND r.state='draft')
    OR (v.predecessor_id IS NOT NULL AND (old.version_id IS NULL OR old.organism_id<>NEW.organism_id OR NOT old.author_ids<@NEW.author_ids))
    OR NOT EXISTS(SELECT FROM public.core_libraryresourcetransition t WHERE t.version_id=v.id AND t.actor_id=NEW.actor_id
      AND t.command IN ('create_library_resource','create_library_resource_version')
      AND t.authority_reference='lib-policy:'||NEW.authority_source::text) THEN
    RAISE EXCEPTION 'LIB: Policy provenance must name the exact new version and retain prior authors.' USING ERRCODE='23514'; END IF;
  IF public.lib_policy_source(NEW.organism_id,NEW.actor_id,NEW.authority_source->>'route',NEW.received_at,NEW.received_at) IS DISTINCT FROM NEW.authority_source
    OR public.lib_policy_source(NEW.organism_id,NEW.actor_id,NEW.authority_source->>'route',stamp,stamp) IS DISTINCT FROM NEW.authority_source THEN
    RAISE EXCEPTION 'LIB: this office does not authorise the Policy act.' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='core_libreleasepermission' THEN
  SELECT x.* INTO STRICT v FROM public.core_libraryresourceversion x WHERE x.id=NEW.version_id;
  PERFORM 1 FROM public.core_libraryresource WHERE id=v.resource_id FOR UPDATE;
  SELECT * INTO STRICT old FROM public.core_libpolicyversion WHERE version_id=v.id;
  IF public.cq3_release_source(old.organism_id,NEW.actor_id,NEW.version_id,NEW.authority_source,NEW.received_at,NEW.received_at) IS DISTINCT FROM NEW.authority_source
    OR public.cq3_release_source(old.organism_id,NEW.actor_id,NEW.version_id,NEW.authority_source,stamp,stamp) IS DISTINCT FROM NEW.authority_source THEN
    RAISE EXCEPTION 'LIB: release permission requires this organisation''s effective authority for the exact version.' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END """
LIB_POLICY_INPUT_CLOCK = r"""
DECLARE v public.core_libraryresourceversion; old public.core_libpolicyversion; stamp timestamptz:=public.intevia_clock_statement();
BEGIN
 NEW.recorded_at:=stamp; NEW.effective_at:=stamp;
 IF NEW.received_at>stamp OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch AND i.access_state='active' AND u.is_active) THEN
  RAISE EXCEPTION 'LIB: the signed-in actor or act time is unavailable.' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='core_libpolicyversion' THEN
  SELECT * INTO STRICT v FROM public.core_libraryresourceversion WHERE id=NEW.version_id;
  PERFORM 1 FROM public.core_libraryresource WHERE id=v.resource_id FOR UPDATE;
  SELECT * INTO old FROM public.core_libpolicyversion WHERE version_id=v.predecessor_id;
  IF (NEW.declared_self AND NOT NEW.actor_id=ANY(NEW.author_ids))
    OR NEW.author_ids IS DISTINCT FROM ARRAY(SELECT DISTINCT a FROM unnest(NEW.author_ids) a ORDER BY a)
    OR NEW.content_sha256 IS DISTINCT FROM encode(sha256(convert_to(v.content,'UTF8')),'hex')
    OR v.created_by_id<>NEW.actor_id OR v.created_at<>NEW.received_at
    OR NOT EXISTS(SELECT FROM public.core_libraryresource r WHERE r.id=v.resource_id AND r.current_version_id=v.id AND r.state='draft')
    OR (v.predecessor_id IS NOT NULL AND (old.version_id IS NULL OR old.organism_id<>NEW.organism_id OR NOT old.author_ids<@NEW.author_ids))
    OR NOT EXISTS(SELECT FROM public.core_libraryresourcetransition t WHERE t.version_id=v.id AND t.actor_id=NEW.actor_id
      AND t.command IN ('create_library_resource','create_library_resource_version')
      AND t.authority_reference='lib-policy:'||NEW.authority_source::text) THEN
    RAISE EXCEPTION 'LIB: Policy provenance must name the exact new version and retain prior authors.' USING ERRCODE='23514'; END IF;
  IF public.lib_policy_source(NEW.organism_id,NEW.actor_id,NEW.authority_source->>'route',NEW.received_at,NEW.received_at) IS DISTINCT FROM NEW.authority_source
    OR public.lib_policy_source(NEW.organism_id,NEW.actor_id,NEW.authority_source->>'route',stamp,stamp) IS DISTINCT FROM NEW.authority_source THEN
    RAISE EXCEPTION 'LIB: this office does not authorise the Policy act.' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='core_libreleasepermission' THEN
  SELECT x.* INTO STRICT v FROM public.core_libraryresourceversion x WHERE x.id=NEW.version_id;
  PERFORM 1 FROM public.core_libraryresource WHERE id=v.resource_id FOR UPDATE;
  SELECT * INTO STRICT old FROM public.core_libpolicyversion WHERE version_id=v.id;
  IF public.cq3_release_source(old.organism_id,NEW.actor_id,NEW.version_id,NEW.authority_source,NEW.received_at,NEW.received_at) IS DISTINCT FROM NEW.authority_source
    OR public.cq3_release_source(old.organism_id,NEW.actor_id,NEW.version_id,NEW.authority_source,stamp,stamp) IS DISTINCT FROM NEW.authority_source THEN
    RAISE EXCEPTION 'LIB: release permission requires this organisation''s effective authority for the exact version.' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END """

# public.lib_publication_input()
# Its landed text: 0076_cq_authority.py (assembled by that migration). Time calls moved: 1 statement.
LIB_PUBLICATION_INPUT_LANDED = r"""
DECLARE stamp timestamptz:=statement_timestamp(); meta public.core_libpolicyversion; v public.core_libraryresourceversion;
 permission public.core_libreleasepermission; decl public.core_rootpublicationdeclaration;
BEGIN
 NEW.recorded_at:=stamp; NEW.effective_at:=stamp;
 SELECT * INTO STRICT meta FROM public.core_libpolicyversion WHERE version_id=NEW.version_id;
 SELECT * INTO STRICT v FROM public.core_libraryresourceversion WHERE id=NEW.version_id;
 PERFORM 1 FROM public.core_libraryresource WHERE id=v.resource_id FOR UPDATE;
 SELECT * INTO STRICT permission FROM public.core_libreleasepermission WHERE permission_uuid=NEW.permission_uuid;

 IF NEW.authority_source->>'kind'='CQ_RESULT' AND NOT EXISTS(
   SELECT FROM public.core_cqact a WHERE a.act_uuid=NEW.operation_uuid AND a.execution_xid=pg_current_xact_id()
    AND a.facts->'domain_plan'->>'domain'='PUBLISH_POLICY' AND a.facts->'result_authority'=NEW.authority_source)
 THEN RAISE EXCEPTION 'LIB: publication needs the exact Board decision.' USING ERRCODE='23514'; END IF;
 IF NEW.received_at>stamp OR permission.version_id<>v.id OR permission.recorded_at>stamp
   OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
      WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch AND i.access_state='active' AND u.is_active)
   OR EXISTS(SELECT FROM unnest(meta.author_ids) a WHERE NOT EXISTS(SELECT FROM public.core_libauthorclaim c WHERE c.version_id=v.id AND c.author_id=a))
   OR public.cq3_release_source(NEW.organism_id,NEW.actor_id,NEW.version_id,NEW.authority_source,NEW.received_at,NEW.received_at) IS DISTINCT FROM NEW.authority_source
   OR public.cq3_release_source(NEW.organism_id,NEW.actor_id,NEW.version_id,NEW.authority_source,stamp,stamp) IS DISTINCT FROM NEW.authority_source THEN
   RAISE EXCEPTION 'LIB: publication needs complete authorship, exact release permission and effective authority.' USING ERRCODE='23514'; END IF;
 IF NEW.authority_source->>'route'='ROOT_EXCEPTION' THEN
  SELECT * INTO decl FROM public.core_rootpublicationdeclaration WHERE id=NEW.declaration_id;
  IF decl.id IS NULL OR decl.living_organism_id<>NEW.organism_id OR decl.effective_at>NEW.received_at OR decl.recorded_at>NEW.received_at
     OR meta.author_ids IS DISTINCT FROM ARRAY[NEW.actor_id] OR NOT NEW.self_accepted THEN
    RAISE EXCEPTION 'LIB: the root exception needs its declaration and the publisher as sole recorded author.' USING ERRCODE='23514'; END IF;
 ELSE
  IF NEW.organism_id<>meta.organism_id OR NEW.declaration_id IS NOT NULL OR NEW.self_accepted OR NEW.kind<>'PUBLICATION' THEN
    RAISE EXCEPTION 'LIB: ordinary release cannot use the root''s self-acceptance exception.' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END """
LIB_PUBLICATION_INPUT_CLOCK = r"""
DECLARE stamp timestamptz:=public.intevia_clock_statement(); meta public.core_libpolicyversion; v public.core_libraryresourceversion;
 permission public.core_libreleasepermission; decl public.core_rootpublicationdeclaration;
BEGIN
 NEW.recorded_at:=stamp; NEW.effective_at:=stamp;
 SELECT * INTO STRICT meta FROM public.core_libpolicyversion WHERE version_id=NEW.version_id;
 SELECT * INTO STRICT v FROM public.core_libraryresourceversion WHERE id=NEW.version_id;
 PERFORM 1 FROM public.core_libraryresource WHERE id=v.resource_id FOR UPDATE;
 SELECT * INTO STRICT permission FROM public.core_libreleasepermission WHERE permission_uuid=NEW.permission_uuid;

 IF NEW.authority_source->>'kind'='CQ_RESULT' AND NOT EXISTS(
   SELECT FROM public.core_cqact a WHERE a.act_uuid=NEW.operation_uuid AND a.execution_xid=pg_current_xact_id()
    AND a.facts->'domain_plan'->>'domain'='PUBLISH_POLICY' AND a.facts->'result_authority'=NEW.authority_source)
 THEN RAISE EXCEPTION 'LIB: publication needs the exact Board decision.' USING ERRCODE='23514'; END IF;
 IF NEW.received_at>stamp OR permission.version_id<>v.id OR permission.recorded_at>stamp
   OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
      WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch AND i.access_state='active' AND u.is_active)
   OR EXISTS(SELECT FROM unnest(meta.author_ids) a WHERE NOT EXISTS(SELECT FROM public.core_libauthorclaim c WHERE c.version_id=v.id AND c.author_id=a))
   OR public.cq3_release_source(NEW.organism_id,NEW.actor_id,NEW.version_id,NEW.authority_source,NEW.received_at,NEW.received_at) IS DISTINCT FROM NEW.authority_source
   OR public.cq3_release_source(NEW.organism_id,NEW.actor_id,NEW.version_id,NEW.authority_source,stamp,stamp) IS DISTINCT FROM NEW.authority_source THEN
   RAISE EXCEPTION 'LIB: publication needs complete authorship, exact release permission and effective authority.' USING ERRCODE='23514'; END IF;
 IF NEW.authority_source->>'route'='ROOT_EXCEPTION' THEN
  SELECT * INTO decl FROM public.core_rootpublicationdeclaration WHERE id=NEW.declaration_id;
  IF decl.id IS NULL OR decl.living_organism_id<>NEW.organism_id OR decl.effective_at>NEW.received_at OR decl.recorded_at>NEW.received_at
     OR meta.author_ids IS DISTINCT FROM ARRAY[NEW.actor_id] OR NOT NEW.self_accepted THEN
    RAISE EXCEPTION 'LIB: the root exception needs its declaration and the publisher as sole recorded author.' USING ERRCODE='23514'; END IF;
 ELSE
  IF NEW.organism_id<>meta.organism_id OR NEW.declaration_id IS NOT NULL OR NEW.self_accepted OR NEW.kind<>'PUBLICATION' THEN
    RAISE EXCEPTION 'LIB: ordinary release cannot use the root''s self-acceptance exception.' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END """

# public.elect_nomination_followup()
# Its landed text: 0079_elect_and_seat.py (found there verbatim). Time calls moved: 1 statement.
ELECT_NOMINATION_FOLLOWUP_LANDED = r"""
DECLARE plan jsonb; nom public.core_cqact; o public.core_cqact;
BEGIN
 IF NEW.operation='ELECT_WITHDRAW' THEN
   SELECT a.* INTO o FROM public.cq_phases h JOIN public.core_cqact a USING(act_uuid)
     WHERE h.thread_uuid=NEW.thread_uuid AND h.phase='VOTING';
   IF o.act_uuid IS NOT NULL THEN
    INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
    VALUES(gen_random_uuid(),NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,'SETTLE',NEW.actor_id,NEW.actor_epoch,
       statement_timestamp(),jsonb_build_object('ballot',o.act_uuid)); END IF;
   RETURN NULL;
 END IF;
 IF NEW.operation='ELECT_CONSENT' THEN
   SELECT * INTO STRICT nom FROM public.core_cqact WHERE act_uuid=(NEW.request->>'nomination')::uuid;
 ELSIF (NEW.facts->>'self_consent')::boolean THEN nom:=NEW;
 ELSE RETURN NULL; END IF;
 plan:=public.elect_plan(NEW.organism_id,NEW.board_uuid,public.elect_attachment(NEW.thread_uuid),NEW.effective_at);
 IF plan->>'type'='SEAT' THEN
   INSERT INTO public.core_circlejoinrequest(request_uuid,circle_id,membership_id,seat_rule_uuid,seat_id,initiated_by,
    consent_reference,enterer_identity_id,occurred_at,effective_at,received_at)
   VALUES(NEW.act_uuid,(plan->>'circle')::bigint,(nom.facts->>'member')::bigint,(plan->>'seat_rule')::uuid,
     plan->>'place','APPLICANT','elect-consent:'||NEW.act_uuid,NEW.actor_id,NEW.effective_at,NEW.effective_at,NEW.received_at);
 END IF;
 RETURN NULL;
END """
ELECT_NOMINATION_FOLLOWUP_CLOCK = r"""
DECLARE plan jsonb; nom public.core_cqact; o public.core_cqact;
BEGIN
 IF NEW.operation='ELECT_WITHDRAW' THEN
   SELECT a.* INTO o FROM public.cq_phases h JOIN public.core_cqact a USING(act_uuid)
     WHERE h.thread_uuid=NEW.thread_uuid AND h.phase='VOTING';
   IF o.act_uuid IS NOT NULL THEN
    INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
    VALUES(gen_random_uuid(),NEW.organism_id,NEW.board_uuid,NEW.thread_uuid,'SETTLE',NEW.actor_id,NEW.actor_epoch,
       public.intevia_clock_statement(),jsonb_build_object('ballot',o.act_uuid)); END IF;
   RETURN NULL;
 END IF;
 IF NEW.operation='ELECT_CONSENT' THEN
   SELECT * INTO STRICT nom FROM public.core_cqact WHERE act_uuid=(NEW.request->>'nomination')::uuid;
 ELSIF (NEW.facts->>'self_consent')::boolean THEN nom:=NEW;
 ELSE RETURN NULL; END IF;
 plan:=public.elect_plan(NEW.organism_id,NEW.board_uuid,public.elect_attachment(NEW.thread_uuid),NEW.effective_at);
 IF plan->>'type'='SEAT' THEN
   INSERT INTO public.core_circlejoinrequest(request_uuid,circle_id,membership_id,seat_rule_uuid,seat_id,initiated_by,
    consent_reference,enterer_identity_id,occurred_at,effective_at,received_at)
   VALUES(NEW.act_uuid,(plan->>'circle')::bigint,(nom.facts->>'member')::bigint,(plan->>'seat_rule')::uuid,
     plan->>'place','APPLICANT','elect-consent:'||NEW.act_uuid,NEW.actor_id,NEW.effective_at,NEW.effective_at,NEW.received_at);
 END IF;
 RETURN NULL;
END """

# public.elect_office_withdrawn()
# Its landed text: 0079_elect_and_seat.py (found there verbatim). Time calls moved: 3 statement.
ELECT_OFFICE_WITHDRAWN_LANDED = r"""
DECLARE item record; epoch_ bigint;
BEGIN
 IF greatest(NEW.effective_at,NEW.received_at)>statement_timestamp() THEN RETURN NULL; END IF;
 SELECT access_epoch INTO epoch_ FROM public.core_identity WHERE id=NEW.actor_id;
 FOR item IN SELECT o.* FROM public.cq_phases h JOIN public.core_cqact o USING(act_uuid)
 JOIN public.core_contextualroleassignment a ON a.id=NEW.assignment_id
 WHERE h.phase='VOTING' AND h.deadline>statement_timestamp() AND EXISTS(
   SELECT FROM jsonb_array_elements(o.facts->'slate') s WHERE s->>'assignment'=a.assignment_uuid::text)
 ORDER BY o.organism_id,o.ordinal LOOP
    INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
    VALUES(gen_random_uuid(),item.organism_id,item.board_uuid,item.thread_uuid,'SETTLE',NEW.actor_id,epoch_,
       statement_timestamp(),jsonb_build_object('ballot',item.act_uuid));
 END LOOP;
 RETURN NULL;
END """
ELECT_OFFICE_WITHDRAWN_CLOCK = r"""
DECLARE item record; epoch_ bigint;
BEGIN
 IF greatest(NEW.effective_at,NEW.received_at)>public.intevia_clock_statement() THEN RETURN NULL; END IF;
 SELECT access_epoch INTO epoch_ FROM public.core_identity WHERE id=NEW.actor_id;
 FOR item IN SELECT o.* FROM public.cq_phases h JOIN public.core_cqact o USING(act_uuid)
 JOIN public.core_contextualroleassignment a ON a.id=NEW.assignment_id
 WHERE h.phase='VOTING' AND h.deadline>public.intevia_clock_statement() AND EXISTS(
   SELECT FROM jsonb_array_elements(o.facts->'slate') s WHERE s->>'assignment'=a.assignment_uuid::text)
 ORDER BY o.organism_id,o.ordinal LOOP
    INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
    VALUES(gen_random_uuid(),item.organism_id,item.board_uuid,item.thread_uuid,'SETTLE',NEW.actor_id,epoch_,
       public.intevia_clock_statement(),jsonb_build_object('ballot',item.act_uuid));
 END LOOP;
 RETURN NULL;
END """

# public.elect_link_fill()
# Its landed text: 0080_elect_authority_and_vacancy.py (found there verbatim). Time calls moved: 2 statement.
ELECT_LINK_FILL_LANDED = r"""
DECLARE lo bigint; place_ text; kind_ text; event_ uuid; receipt_ uuid; actor_ bigint; role_ bigint; decision_ text;
 v public.core_electvacancy; mandate jsonb; source_ uuid; at_ timestamptz:=NEW.effective_at;
BEGIN
 IF TG_TABLE_NAME='core_circleparticipation' THEN
   SELECT c.parent_organism_id INTO lo FROM public.core_circle c WHERE c.id=NEW.circle_id AND c.kind='GOVERNANCE';
   kind_:='SEAT'; place_:=NEW.seat_id; event_:=NEW.participation_uuid; actor_:=NEW.enterer_identity_id;
   SELECT d.authority_decision_reference INTO decision_ FROM public.core_circleadmissiondecision d WHERE d.id=NEW.decision_id;
 ELSE
   SELECT d.living_organism_id,d.code,d.id INTO lo,place_,role_ FROM public.core_contextualroleassignment a
   JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
   WHERE a.id=NEW.assignment_id AND d.kind='BOARD_OFFICE' AND NOT starts_with(d.code,'INTEVIA_');
   kind_:='OFFICE'; event_:=NEW.event_uuid; actor_:=NEW.enterer_identity_id; decision_:=NEW.authority_decision_reference;
 END IF;
 IF lo IS NULL THEN RETURN NULL; END IF;
 -- An ending and its replacement can share one transaction; start the order before consuming it.
 SELECT s.ending INTO source_ FROM public.elect_vacancy_sources s WHERE s.organism_id=lo AND s.kind=kind_
   AND s.place=place_ AND s.effective_at<=at_ AND s.recorded_at<=statement_timestamp()
   ORDER BY s.effective_at DESC,s.recorded_at DESC LIMIT 1;
 IF source_ IS NULL THEN RETURN NULL; END IF;
 PERFORM public.elect_start_vacancy(lo,source_,actor_);
 v:=public.elect_pending_vacancy(lo,kind_,place_,at_);
 IF v.ending IS NULL THEN RETURN NULL; END IF;
 IF v.instruction->>'route'='HOLD_HG' THEN PERFORM public.cq_refuse('ELECT_VACANCY_HOLD'); END IF;
 IF v.instruction->>'route'='APPOINTMENT' THEN
   IF kind_='OFFICE' THEN
     IF public.elect_named_appointment(lo,role_,actor_,at_,statement_timestamp()) IS DISTINCT FROM v.ending
        OR NOT EXISTS(SELECT FROM public.core_authoritydecision a WHERE a.decision_reference=decision_ AND a.outcome='QUALIFIED'
           AND a.actor_id=actor_ AND a.request_reference='elect-vacancy:'||v.ending) THEN
        PERFORM public.cq_refuse('ELECT_VACANCY_APPOINTER'); END IF;
   ELSIF NOT EXISTS(SELECT FROM public.core_circleadmissiondecision d WHERE d.id=NEW.decision_id AND d.outcome='ACCEPT'
     AND d.constitutional_route='APPOINTMENT' AND d.deciding_party=v.instruction->>'authority'
     AND d.deciding_identity_id=actor_) THEN PERFORM public.cq_refuse('ELECT_VACANCY_APPOINTER'); END IF;
 ELSE
   SELECT (i.request->>'result')::uuid,i.facts->'domain_plan' INTO receipt_,mandate FROM public.core_cqact i
    WHERE i.act_uuid=event_ AND i.organism_id=lo AND i.operation='IMPLEMENT' AND i.execution_xid=pg_current_xact_id()
      AND i.authority_source=public.cq3_implement_source(i);
   IF receipt_ IS NULL OR mandate->>'vacancy' IS DISTINCT FROM v.ending::text OR mandate->>'place' IS DISTINCT FROM place_
      OR mandate->>'type' IS DISTINCT FROM kind_ OR v.instruction->>'route' NOT IN ('BY_ELECTION','BOARD_CQ','COOPTION')
     THEN PERFORM public.cq_refuse('ELECT_VACANCY_ROUTE'); END IF;
 END IF;
 INSERT INTO public.core_electvacancyfill VALUES (v.ending,event_,receipt_,decision_); RETURN NULL;
END """
ELECT_LINK_FILL_CLOCK = r"""
DECLARE lo bigint; place_ text; kind_ text; event_ uuid; receipt_ uuid; actor_ bigint; role_ bigint; decision_ text;
 v public.core_electvacancy; mandate jsonb; source_ uuid; at_ timestamptz:=NEW.effective_at;
BEGIN
 IF TG_TABLE_NAME='core_circleparticipation' THEN
   SELECT c.parent_organism_id INTO lo FROM public.core_circle c WHERE c.id=NEW.circle_id AND c.kind='GOVERNANCE';
   kind_:='SEAT'; place_:=NEW.seat_id; event_:=NEW.participation_uuid; actor_:=NEW.enterer_identity_id;
   SELECT d.authority_decision_reference INTO decision_ FROM public.core_circleadmissiondecision d WHERE d.id=NEW.decision_id;
 ELSE
   SELECT d.living_organism_id,d.code,d.id INTO lo,place_,role_ FROM public.core_contextualroleassignment a
   JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
   WHERE a.id=NEW.assignment_id AND d.kind='BOARD_OFFICE' AND NOT starts_with(d.code,'INTEVIA_');
   kind_:='OFFICE'; event_:=NEW.event_uuid; actor_:=NEW.enterer_identity_id; decision_:=NEW.authority_decision_reference;
 END IF;
 IF lo IS NULL THEN RETURN NULL; END IF;
 -- An ending and its replacement can share one transaction; start the order before consuming it.
 SELECT s.ending INTO source_ FROM public.elect_vacancy_sources s WHERE s.organism_id=lo AND s.kind=kind_
   AND s.place=place_ AND s.effective_at<=at_ AND s.recorded_at<=public.intevia_clock_statement()
   ORDER BY s.effective_at DESC,s.recorded_at DESC LIMIT 1;
 IF source_ IS NULL THEN RETURN NULL; END IF;
 PERFORM public.elect_start_vacancy(lo,source_,actor_);
 v:=public.elect_pending_vacancy(lo,kind_,place_,at_);
 IF v.ending IS NULL THEN RETURN NULL; END IF;
 IF v.instruction->>'route'='HOLD_HG' THEN PERFORM public.cq_refuse('ELECT_VACANCY_HOLD'); END IF;
 IF v.instruction->>'route'='APPOINTMENT' THEN
   IF kind_='OFFICE' THEN
     IF public.elect_named_appointment(lo,role_,actor_,at_,public.intevia_clock_statement()) IS DISTINCT FROM v.ending
        OR NOT EXISTS(SELECT FROM public.core_authoritydecision a WHERE a.decision_reference=decision_ AND a.outcome='QUALIFIED'
           AND a.actor_id=actor_ AND a.request_reference='elect-vacancy:'||v.ending) THEN
        PERFORM public.cq_refuse('ELECT_VACANCY_APPOINTER'); END IF;
   ELSIF NOT EXISTS(SELECT FROM public.core_circleadmissiondecision d WHERE d.id=NEW.decision_id AND d.outcome='ACCEPT'
     AND d.constitutional_route='APPOINTMENT' AND d.deciding_party=v.instruction->>'authority'
     AND d.deciding_identity_id=actor_) THEN PERFORM public.cq_refuse('ELECT_VACANCY_APPOINTER'); END IF;
 ELSE
   SELECT (i.request->>'result')::uuid,i.facts->'domain_plan' INTO receipt_,mandate FROM public.core_cqact i
    WHERE i.act_uuid=event_ AND i.organism_id=lo AND i.operation='IMPLEMENT' AND i.execution_xid=pg_current_xact_id()
      AND i.authority_source=public.cq3_implement_source(i);
   IF receipt_ IS NULL OR mandate->>'vacancy' IS DISTINCT FROM v.ending::text OR mandate->>'place' IS DISTINCT FROM place_
      OR mandate->>'type' IS DISTINCT FROM kind_ OR v.instruction->>'route' NOT IN ('BY_ELECTION','BOARD_CQ','COOPTION')
     THEN PERFORM public.cq_refuse('ELECT_VACANCY_ROUTE'); END IF;
 END IF;
 INSERT INTO public.core_electvacancyfill VALUES (v.ending,event_,receipt_,decision_); RETURN NULL;
END """

# public.elect_start_vacancy(bigint, uuid, bigint)
# Its landed text: 0080_elect_authority_and_vacancy.py (found there verbatim). Time calls moved: 2 statement.
ELECT_START_VACANCY_LANDED = r"""
DECLARE writes integer;
BEGIN
 PERFORM 1 FROM public.core_livingorganism WHERE id=p_lo FOR UPDATE;
 INSERT INTO public.core_electvacancy(ending,organism_id,enterer)
 SELECT s.ending,s.organism_id,p_enterer FROM public.elect_vacancy_sources s WHERE s.organism_id=p_lo AND s.ending=p_ending
 AND NOT public.elect_already_replaced(s,statement_timestamp())
 AND greatest(s.effective_at,s.recorded_at)<=statement_timestamp()
 AND EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
   WHERE i.id=p_enterer AND i.access_state='active' AND u.is_active)
 AND EXISTS(SELECT FROM public.core_circle c WHERE c.parent_organism_id=p_lo AND c.kind='GOVERNANCE')
 AND (public.cq_settings_at(p_lo,greatest(s.recorded_at,s.effective_at))->>'enabled')::boolean
 AND NOT EXISTS(SELECT FROM public.core_electvacancy v WHERE v.ending=s.ending)
 ON CONFLICT(ending) DO NOTHING;
 GET DIAGNOSTICS writes=ROW_COUNT; RETURN writes=1;
END """
ELECT_START_VACANCY_CLOCK = r"""
DECLARE writes integer;
BEGIN
 PERFORM 1 FROM public.core_livingorganism WHERE id=p_lo FOR UPDATE;
 INSERT INTO public.core_electvacancy(ending,organism_id,enterer)
 SELECT s.ending,s.organism_id,p_enterer FROM public.elect_vacancy_sources s WHERE s.organism_id=p_lo AND s.ending=p_ending
 AND NOT public.elect_already_replaced(s,public.intevia_clock_statement())
 AND greatest(s.effective_at,s.recorded_at)<=public.intevia_clock_statement()
 AND EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
   WHERE i.id=p_enterer AND i.access_state='active' AND u.is_active)
 AND EXISTS(SELECT FROM public.core_circle c WHERE c.parent_organism_id=p_lo AND c.kind='GOVERNANCE')
 AND (public.cq_settings_at(p_lo,greatest(s.recorded_at,s.effective_at))->>'enabled')::boolean
 AND NOT EXISTS(SELECT FROM public.core_electvacancy v WHERE v.ending=s.ending)
 ON CONFLICT(ending) DO NOTHING;
 GET DIAGNOSTICS writes=ROW_COUNT; RETURN writes=1;
END """

# public.elect_vacancy_receipt()
# Its landed text: 0080_elect_authority_and_vacancy.py (found there verbatim). Time calls moved: 1 statement.
ELECT_VACANCY_RECEIPT_LANDED = r"""
BEGIN
 INSERT INTO public.core_cqact(act_uuid,organism_id,operation,actor_id,actor_epoch,received_at,request)
 SELECT NEW.route_act,NEW.organism_id,'ELECT_VACANCY',i.id,i.access_epoch,statement_timestamp(),jsonb_build_object('ending',NEW.ending)
 FROM public.core_identity i WHERE i.id=NEW.enterer;
 RETURN NULL;
END """
ELECT_VACANCY_RECEIPT_CLOCK = r"""
BEGIN
 INSERT INTO public.core_cqact(act_uuid,organism_id,operation,actor_id,actor_epoch,received_at,request)
 SELECT NEW.route_act,NEW.organism_id,'ELECT_VACANCY',i.id,i.access_epoch,public.intevia_clock_statement(),jsonb_build_object('ending',NEW.ending)
 FROM public.core_identity i WHERE i.id=NEW.enterer;
 RETURN NULL;
END """

FUNCTIONS = (
    ("public.discp_complete()", DISCP_COMPLETE_LANDED, DISCP_COMPLETE_CLOCK),
    ("public.cq_edit_lock()", CQ_EDIT_LOCK_LANDED, CQ_EDIT_LOCK_CLOCK),
    ("public.cq_settings(bigint)", CQ_SETTINGS_LANDED, CQ_SETTINGS_CLOCK),
    ("public.s3fu_guard_gateway_effect()", S3FU_GUARD_GATEWAY_EFFECT_LANDED, S3FU_GUARD_GATEWAY_EFFECT_CLOCK),
    ("public.cq_after_departure()", CQ_AFTER_DEPARTURE_LANDED, CQ_AFTER_DEPARTURE_CLOCK),
    ("public.cq_loss_source(bigint, uuid, uuid, bigint, jsonb)", CQ_LOSS_SOURCE_LANDED, CQ_LOSS_SOURCE_CLOCK),
    ("public.cq_vote_followup()", CQ_VOTE_FOLLOWUP_LANDED, CQ_VOTE_FOLLOWUP_CLOCK),
    ("public.discp_execute(jsonb, text, bigint, bigint, timestamp with time zone)", DISCP_EXECUTE_LANDED, DISCP_EXECUTE_CLOCK),
    ("public.discp_input()", DISCP_INPUT_LANDED, DISCP_INPUT_CLOCK),
    ("public.gi_complete()", GI_COMPLETE_LANDED, GI_COMPLETE_CLOCK),
    ("public.gi_execute(jsonb, bigint, bigint, timestamp with time zone)", GI_EXECUTE_LANDED, GI_EXECUTE_CLOCK),
    ("public.gi_input()", GI_INPUT_LANDED, GI_INPUT_CLOCK),
    ("public.lib_policy_commit()", LIB_POLICY_COMMIT_LANDED, LIB_POLICY_COMMIT_CLOCK),
    ("public.lib_policy_input()", LIB_POLICY_INPUT_LANDED, LIB_POLICY_INPUT_CLOCK),
    ("public.lib_publication_input()", LIB_PUBLICATION_INPUT_LANDED, LIB_PUBLICATION_INPUT_CLOCK),
    ("public.elect_nomination_followup()", ELECT_NOMINATION_FOLLOWUP_LANDED, ELECT_NOMINATION_FOLLOWUP_CLOCK),
    ("public.elect_office_withdrawn()", ELECT_OFFICE_WITHDRAWN_LANDED, ELECT_OFFICE_WITHDRAWN_CLOCK),
    ("public.elect_link_fill()", ELECT_LINK_FILL_LANDED, ELECT_LINK_FILL_CLOCK),
    ("public.elect_start_vacancy(bigint, uuid, bigint)", ELECT_START_VACANCY_LANDED, ELECT_START_VACANCY_CLOCK),
    ("public.elect_vacancy_receipt()", ELECT_VACANCY_RECEIPT_LANDED, ELECT_VACANCY_RECEIPT_CLOCK),
)


def install(apps, schema_editor):
    """Move these functions onto the INTEVIA Clock."""
    clock.convert(schema_editor, FUNCTIONS)


def uninstall(apps, schema_editor):
    """Put the landed functions back."""
    clock.restore(schema_editor, FUNCTIONS)


class Migration(migrations.Migration):
    dependencies = (("core", "0099_clock_storage_0059_0072"),)
    operations = (migrations.RunPython(install, uninstall),)
