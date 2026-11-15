"""The INTEVIA Clock, storage 3 of 7: the installed functions of migrations 0059 to 0072 (CK-2b).

27 installed functions read the time through PostgreSQL's own calls. This migration replaces
each under G2-4 with a successor that differs from it only where a time call becomes the
INTEVIA Clock's face with the same meaning (35 calls: 24 statement start, 7 instant,
4 transaction start):

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

# public.circle_command_valid()
# Its landed text: 0059_operating_circles.py (found there verbatim). Time calls moved: 2 wall.
CIRCLE_COMMAND_VALID_LANDED = r"""
DECLARE c public.core_circle; lo public.core_livingorganism; f public.core_governeddetermination;
        task public.core_circlestateevent; act text; data jsonb; former bigint; former_event bigint;
BEGIN
 SELECT * INTO STRICT c FROM public.core_circle WHERE id=NEW.circle_id;
 SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE id=c.parent_organism_id;
 IF NEW.action='CREATE_DORMANT' AND c.kind='GENERIC' AND (
       NEW.evidence_reference IS NULL OR NOT starts_with(NEW.evidence_reference,'circle-purpose:')
       OR btrim(substr(NEW.evidence_reference,16))='') THEN
    RAISE EXCEPTION 'CIRCLE_BIRTH_PURPOSE' USING ERRCODE='23514';
 END IF;
 IF NEW.action='CREATE_DORMANT' AND c.kind<>'GOVERNANCE' AND starts_with(NEW.evidence_reference,'circle-purpose:') THEN
    act:='FORM:'||c.kind||':'||encode(sha256(convert_to(substr(NEW.evidence_reference,16),'UTF8')),'hex');
    IF NEW.sequence<>1 OR NEW.resulting_state<>'DORMANT' OR NEW.actor_state<>'LIVING_ORGANISM'
       OR c.founding_reference<>NEW.authority_decision_reference THEN
       RAISE EXCEPTION 'CIRCLE_FORMATION_SHAPE' USING ERRCODE='23514';
    END IF;
 ELSIF NEW.action IN ('LEAVE_CIRCLE','OPEN_WORK','CLAIM_WORK') THEN
    IF NEW.prior_state IS DISTINCT FROM NEW.resulting_state OR NEW.actor_state<>'PARTY'
       OR NEW.actor_id IS DISTINCT FROM NEW.enterer_identity_id
       OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_access_epoch
                        AND i.access_state='active') THEN
       RAISE EXCEPTION 'CIRCLE_COMMAND_PARTY' USING ERRCODE='23514';
    END IF;
    IF NEW.action='OPEN_WORK' THEN
       data:=NEW.evidence_reference::jsonb;
       IF jsonb_typeof(data)<>'array' OR jsonb_array_length(data)<>2 OR jsonb_typeof(data->0)<>'string'
          OR jsonb_typeof(data->1)<>'string' OR btrim(data->>0)='' OR btrim(data->>1)='' THEN
          RAISE EXCEPTION 'CIRCLE_WORK_DESCRIPTION' USING ERRCODE='23514';
       END IF;
       act:='OPEN_WORK:'||NEW.event_uuid||':'||encode(sha256(convert_to(NEW.evidence_reference,'UTF8')),'hex');
    ELSIF NEW.action='CLAIM_WORK' THEN
       SELECT * INTO task FROM public.core_circlestateevent WHERE circle_id=c.id AND action='OPEN_WORK'
           AND 'work:'||event_uuid=NEW.evidence_reference AND sequence<NEW.sequence;
       IF task.id IS NULL OR NEW.prior_state<>'ACTIVE' OR NOT EXISTS(
           SELECT FROM public.circle_holders(c.id,NEW.effective_at,NEW.recorded_at) h WHERE h.person=NEW.actor_id) THEN
           RAISE EXCEPTION 'CIRCLE_WORK_COVERAGE' USING ERRCODE='23514';
       END IF;
       SELECT actor_id,id INTO former,former_event FROM public.core_circlestateevent WHERE circle_id=c.id AND action='CLAIM_WORK'
           AND evidence_reference=NEW.evidence_reference AND sequence<NEW.sequence ORDER BY sequence DESC LIMIT 1;
       IF (c.kind='GENERIC' AND public.circle_claim_current(c.id,former_event,NEW.effective_at,NEW.recorded_at))
          OR (c.kind<>'GENERIC' AND EXISTS(SELECT FROM public.circle_holders(c.id,NEW.effective_at,NEW.recorded_at) h WHERE h.person=former)) THEN
           RAISE EXCEPTION 'CIRCLE_WORK_HELD' USING ERRCODE='23514';
       END IF;
       act:='CIRCLE_SERVICE:'||(task.evidence_reference::jsonb->>1);
    END IF;
 ELSE RETURN NULL;
 END IF;
 IF NEW.effective_at<>NEW.received_at OR NEW.occurred_at<>NEW.effective_at OR NEW.received_at>NEW.recorded_at THEN
    RAISE EXCEPTION 'CIRCLE_COMMAND_TIME' USING ERRCODE='23514';
 END IF;
 IF c.kind='GENERIC' AND NEW.action='CREATE_DORMANT' THEN
    IF NEW.authority_basis_id IS DISTINCT FROM lo.founding_authority_basis_id
       OR NEW.authority_decision_reference IS DISTINCT FROM public.circle_office_decision(
          public.circle_lo_office(lo.id,NEW.enterer_identity_id,NEW.effective_at,NEW.recorded_at),
          c.circle_uuid,act,NEW.enterer_identity_id,NEW.enterer_identity_id)
       OR NEW.authority_decision_reference IS DISTINCT FROM public.circle_office_decision(
          public.circle_lo_office(lo.id,NEW.enterer_identity_id,clock_timestamp(),clock_timestamp()),
          c.circle_uuid,act,NEW.enterer_identity_id,NEW.enterer_identity_id)
       OR NOT EXISTS(SELECT FROM public.core_circleparticipation p JOIN public.core_organismmembership m ON m.id=p.membership_id
          JOIN public.core_contextualroleassignment a ON a.circle_id=c.id AND a.membership_id=m.id
          JOIN public.core_contextualroleassignmentstateevent assigned ON assigned.assignment_id=a.id AND assigned.action='ASSIGN'
          JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id AND d.code='COORDINATOR' AND d.scope='CIRCLE'
          JOIN public.core_circlestateevent activated ON activated.circle_id=c.id AND activated.action='ACTIVATE'
          WHERE p.birth_event_id=NEW.id AND m.identity_id=NEW.enterer_identity_id
          AND activated.enterer_identity_id=m.identity_id AND assigned.effective_at<=activated.effective_at) THEN
       RAISE EXCEPTION 'CIRCLE_BIRTH_INCOMPLETE' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
 END IF;
 IF act IS NOT NULL THEN
    SELECT * INTO f FROM public.core_governeddetermination WHERE determination_uuid=
      public.circle_source(lo.id,c.circle_uuid,act,NEW.enterer_identity_id,NEW.enterer_identity_id,NEW.effective_at,NEW.recorded_at);
    IF f.id IS NULL OR f.determination_uuid IS DISTINCT FROM public.circle_source(lo.id,c.circle_uuid,act,NEW.enterer_identity_id,NEW.enterer_identity_id,NEW.recorded_at,NEW.recorded_at)
       OR (NEW.action<>'CLAIM_WORK' AND NEW.authority_decision_reference IS DISTINCT FROM public.circle_decision(lo.id,f.determination_uuid,NEW.recorded_at,NEW.recorded_at))
       OR NEW.authority_decision_reference IS DISTINCT FROM (CASE WHEN NEW.action='CLAIM_WORK' THEN 's015d1:'||f.scope_fingerprint ELSE public.circle_decision(lo.id,f.determination_uuid,NEW.effective_at,NEW.recorded_at) END)
       OR NEW.authority_basis_id<>lo.founding_authority_basis_id THEN
       RAISE EXCEPTION 'CIRCLE_COMMAND_SOURCE' USING ERRCODE='23514';
    END IF;
 END IF;
 RETURN NULL;
END """
CIRCLE_COMMAND_VALID_CLOCK = r"""
DECLARE c public.core_circle; lo public.core_livingorganism; f public.core_governeddetermination;
        task public.core_circlestateevent; act text; data jsonb; former bigint; former_event bigint;
BEGIN
 SELECT * INTO STRICT c FROM public.core_circle WHERE id=NEW.circle_id;
 SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE id=c.parent_organism_id;
 IF NEW.action='CREATE_DORMANT' AND c.kind='GENERIC' AND (
       NEW.evidence_reference IS NULL OR NOT starts_with(NEW.evidence_reference,'circle-purpose:')
       OR btrim(substr(NEW.evidence_reference,16))='') THEN
    RAISE EXCEPTION 'CIRCLE_BIRTH_PURPOSE' USING ERRCODE='23514';
 END IF;
 IF NEW.action='CREATE_DORMANT' AND c.kind<>'GOVERNANCE' AND starts_with(NEW.evidence_reference,'circle-purpose:') THEN
    act:='FORM:'||c.kind||':'||encode(sha256(convert_to(substr(NEW.evidence_reference,16),'UTF8')),'hex');
    IF NEW.sequence<>1 OR NEW.resulting_state<>'DORMANT' OR NEW.actor_state<>'LIVING_ORGANISM'
       OR c.founding_reference<>NEW.authority_decision_reference THEN
       RAISE EXCEPTION 'CIRCLE_FORMATION_SHAPE' USING ERRCODE='23514';
    END IF;
 ELSIF NEW.action IN ('LEAVE_CIRCLE','OPEN_WORK','CLAIM_WORK') THEN
    IF NEW.prior_state IS DISTINCT FROM NEW.resulting_state OR NEW.actor_state<>'PARTY'
       OR NEW.actor_id IS DISTINCT FROM NEW.enterer_identity_id
       OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_access_epoch
                        AND i.access_state='active') THEN
       RAISE EXCEPTION 'CIRCLE_COMMAND_PARTY' USING ERRCODE='23514';
    END IF;
    IF NEW.action='OPEN_WORK' THEN
       data:=NEW.evidence_reference::jsonb;
       IF jsonb_typeof(data)<>'array' OR jsonb_array_length(data)<>2 OR jsonb_typeof(data->0)<>'string'
          OR jsonb_typeof(data->1)<>'string' OR btrim(data->>0)='' OR btrim(data->>1)='' THEN
          RAISE EXCEPTION 'CIRCLE_WORK_DESCRIPTION' USING ERRCODE='23514';
       END IF;
       act:='OPEN_WORK:'||NEW.event_uuid||':'||encode(sha256(convert_to(NEW.evidence_reference,'UTF8')),'hex');
    ELSIF NEW.action='CLAIM_WORK' THEN
       SELECT * INTO task FROM public.core_circlestateevent WHERE circle_id=c.id AND action='OPEN_WORK'
           AND 'work:'||event_uuid=NEW.evidence_reference AND sequence<NEW.sequence;
       IF task.id IS NULL OR NEW.prior_state<>'ACTIVE' OR NOT EXISTS(
           SELECT FROM public.circle_holders(c.id,NEW.effective_at,NEW.recorded_at) h WHERE h.person=NEW.actor_id) THEN
           RAISE EXCEPTION 'CIRCLE_WORK_COVERAGE' USING ERRCODE='23514';
       END IF;
       SELECT actor_id,id INTO former,former_event FROM public.core_circlestateevent WHERE circle_id=c.id AND action='CLAIM_WORK'
           AND evidence_reference=NEW.evidence_reference AND sequence<NEW.sequence ORDER BY sequence DESC LIMIT 1;
       IF (c.kind='GENERIC' AND public.circle_claim_current(c.id,former_event,NEW.effective_at,NEW.recorded_at))
          OR (c.kind<>'GENERIC' AND EXISTS(SELECT FROM public.circle_holders(c.id,NEW.effective_at,NEW.recorded_at) h WHERE h.person=former)) THEN
           RAISE EXCEPTION 'CIRCLE_WORK_HELD' USING ERRCODE='23514';
       END IF;
       act:='CIRCLE_SERVICE:'||(task.evidence_reference::jsonb->>1);
    END IF;
 ELSE RETURN NULL;
 END IF;
 IF NEW.effective_at<>NEW.received_at OR NEW.occurred_at<>NEW.effective_at OR NEW.received_at>NEW.recorded_at THEN
    RAISE EXCEPTION 'CIRCLE_COMMAND_TIME' USING ERRCODE='23514';
 END IF;
 IF c.kind='GENERIC' AND NEW.action='CREATE_DORMANT' THEN
    IF NEW.authority_basis_id IS DISTINCT FROM lo.founding_authority_basis_id
       OR NEW.authority_decision_reference IS DISTINCT FROM public.circle_office_decision(
          public.circle_lo_office(lo.id,NEW.enterer_identity_id,NEW.effective_at,NEW.recorded_at),
          c.circle_uuid,act,NEW.enterer_identity_id,NEW.enterer_identity_id)
       OR NEW.authority_decision_reference IS DISTINCT FROM public.circle_office_decision(
          public.circle_lo_office(lo.id,NEW.enterer_identity_id,public.intevia_clock_instant(),public.intevia_clock_instant()),
          c.circle_uuid,act,NEW.enterer_identity_id,NEW.enterer_identity_id)
       OR NOT EXISTS(SELECT FROM public.core_circleparticipation p JOIN public.core_organismmembership m ON m.id=p.membership_id
          JOIN public.core_contextualroleassignment a ON a.circle_id=c.id AND a.membership_id=m.id
          JOIN public.core_contextualroleassignmentstateevent assigned ON assigned.assignment_id=a.id AND assigned.action='ASSIGN'
          JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id AND d.code='COORDINATOR' AND d.scope='CIRCLE'
          JOIN public.core_circlestateevent activated ON activated.circle_id=c.id AND activated.action='ACTIVATE'
          WHERE p.birth_event_id=NEW.id AND m.identity_id=NEW.enterer_identity_id
          AND activated.enterer_identity_id=m.identity_id AND assigned.effective_at<=activated.effective_at) THEN
       RAISE EXCEPTION 'CIRCLE_BIRTH_INCOMPLETE' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
 END IF;
 IF act IS NOT NULL THEN
    SELECT * INTO f FROM public.core_governeddetermination WHERE determination_uuid=
      public.circle_source(lo.id,c.circle_uuid,act,NEW.enterer_identity_id,NEW.enterer_identity_id,NEW.effective_at,NEW.recorded_at);
    IF f.id IS NULL OR f.determination_uuid IS DISTINCT FROM public.circle_source(lo.id,c.circle_uuid,act,NEW.enterer_identity_id,NEW.enterer_identity_id,NEW.recorded_at,NEW.recorded_at)
       OR (NEW.action<>'CLAIM_WORK' AND NEW.authority_decision_reference IS DISTINCT FROM public.circle_decision(lo.id,f.determination_uuid,NEW.recorded_at,NEW.recorded_at))
       OR NEW.authority_decision_reference IS DISTINCT FROM (CASE WHEN NEW.action='CLAIM_WORK' THEN 's015d1:'||f.scope_fingerprint ELSE public.circle_decision(lo.id,f.determination_uuid,NEW.effective_at,NEW.recorded_at) END)
       OR NEW.authority_basis_id<>lo.founding_authority_basis_id THEN
       RAISE EXCEPTION 'CIRCLE_COMMAND_SOURCE' USING ERRCODE='23514';
    END IF;
 END IF;
 RETURN NULL;
END """

# public.circle_leave_complete()
# Its landed text: 0059_operating_circles.py (found there verbatim). Time calls moved: 1 statement.
CIRCLE_LEAVE_COMPLETE_LANDED = r"""
DECLARE place public.core_circleparticipation;
BEGIN
 IF NEW.action<>'LEAVE_CIRCLE' THEN RETURN NULL; END IF;
 SELECT p.* INTO place FROM public.core_circleparticipation p
 JOIN public.core_circleparticipationend e ON e.participation_id=p.id
 WHERE p.circle_id=NEW.circle_id AND e.composed_from_event_uuid=NEW.event_uuid AND e.actor_state='PARTY';
 IF place.id IS NULL OR EXISTS(SELECT FROM public.core_contextualroleassignment a
     WHERE a.circle_id=NEW.circle_id AND a.membership_id=place.membership_id
     AND public.s015_fold_effective_state('core_contextualroleassignment',a.id,NEW.effective_at,statement_timestamp())
         IN ('ACTIVE','PROBATIONARY','PROPOSED','SUSPENDED')) THEN
   RAISE EXCEPTION 'CIRCLE_LEAVE_INCOMPLETE' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END """
CIRCLE_LEAVE_COMPLETE_CLOCK = r"""
DECLARE place public.core_circleparticipation;
BEGIN
 IF NEW.action<>'LEAVE_CIRCLE' THEN RETURN NULL; END IF;
 SELECT p.* INTO place FROM public.core_circleparticipation p
 JOIN public.core_circleparticipationend e ON e.participation_id=p.id
 WHERE p.circle_id=NEW.circle_id AND e.composed_from_event_uuid=NEW.event_uuid AND e.actor_state='PARTY';
 IF place.id IS NULL OR EXISTS(SELECT FROM public.core_contextualroleassignment a
     WHERE a.circle_id=NEW.circle_id AND a.membership_id=place.membership_id
     AND public.s015_fold_effective_state('core_contextualroleassignment',a.id,NEW.effective_at,public.intevia_clock_statement())
         IN ('ACTIVE','PROBATIONARY','PROPOSED','SUSPENDED')) THEN
   RAISE EXCEPTION 'CIRCLE_LEAVE_INCOMPLETE' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END """

# public.sbg_guard_circleparticipation()
# Its landed text: 0059_operating_circles.py (assembled by that migration). Time calls moved: 1 wall.
SBG_GUARD_CIRCLEPARTICIPATION_LANDED = r"""
DECLARE
    v_decision public.core_circleadmissiondecision%ROWTYPE;
BEGIN

    IF NEW.recovery_office_id IS NOT NULL THEN
       PERFORM public.s015_lock_organisms(ARRAY[(SELECT lo.organism_id FROM public.core_circle c
          JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id WHERE c.id=NEW.circle_id)]);
       IF NEW.occurred_at<>NEW.effective_at OR NEW.received_at<>NEW.effective_at OR NEW.received_at>NEW.recorded_at
          OR NOT EXISTS(SELECT FROM public.core_circle c JOIN public.core_organismmembership m
             ON m.id=NEW.membership_id AND m.living_organism_id=c.parent_organism_id
             JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id
             JOIN public.core_authoritybasis b ON b.id=lo.founding_authority_basis_id
             JOIN public.core_contextualroleassignment a ON a.id=NEW.recovery_office_id AND a.membership_id=m.id
             WHERE c.id=NEW.circle_id AND m.identity_id=NEW.enterer_identity_id AND NEW.seat_id=m.membership_uuid::text
             AND NEW.seat_rule_uuid=b.basis_uuid AND NOT EXISTS(
                SELECT FROM (SELECT NEW.effective_at,NEW.recorded_at UNION ALL SELECT t,t FROM clock_timestamp() t) cuts(t,k)
                WHERE NOT public.circle_vacant(c.id,NULL,t,k)
                   OR a.assignment_uuid IS DISTINCT FROM public.circle_lo_office(lo.id,m.identity_id,t,k)
                   OR public.circle_place_at(c.id,m.id,t,k))) THEN
          RAISE EXCEPTION 'CIRCLE_RESEAT_PLACE_SOURCE' USING ERRCODE='23514';
       END IF;
       RETURN NEW;
    END IF;
    IF NEW.birth_event_id IS NOT NULL THEN
        IF NEW.decision_id IS NOT NULL OR NOT EXISTS (
           SELECT FROM public.core_circlestateevent e JOIN public.core_circle c ON c.id=e.circle_id
           JOIN public.core_organismmembership m ON m.id=NEW.membership_id AND m.living_organism_id=c.parent_organism_id
           JOIN public.core_livingorganism l ON l.id=c.parent_organism_id
           JOIN public.core_authoritybasis b ON b.id=l.founding_authority_basis_id
           WHERE e.id=NEW.birth_event_id AND e.sequence=1 AND e.action='CREATE_DORMANT' AND c.kind='GENERIC'
           AND e.circle_id=NEW.circle_id AND e.enterer_identity_id=m.identity_id AND NEW.enterer_identity_id=m.identity_id
           AND NEW.seat_id=m.membership_uuid::text AND NEW.seat_rule_uuid=b.basis_uuid
           AND e.recorded_at<=NEW.recorded_at AND NEW.occurred_at=NEW.effective_at AND NEW.received_at=NEW.effective_at
           AND e.effective_at<=NEW.effective_at AND NEW.effective_at<=NEW.recorded_at
           AND public.circle_eligible(c.id,m.id,NEW.effective_at,NEW.recorded_at)) THEN
          RAISE EXCEPTION 'CIRCLE_FOUNDER_PLACE_SOURCE' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    SELECT d.* INTO v_decision
      FROM public.core_circleadmissiondecision d
     WHERE d.id = NEW.decision_id;
    IF NOT FOUND
       OR v_decision.outcome IS DISTINCT FROM 'ACCEPT'
       OR v_decision.request_id IS NULL
       OR (SELECT q.circle_id FROM public.core_circlejoinrequest q
            WHERE q.id = v_decision.request_id) IS DISTINCT FROM NEW.circle_id
       OR (SELECT q.membership_id FROM public.core_circlejoinrequest q
            WHERE q.id = v_decision.request_id) IS DISTINCT FROM NEW.membership_id
       OR v_decision.seat_rule_uuid IS DISTINCT FROM NEW.seat_rule_uuid
       OR v_decision.seat_id IS DISTINCT FROM NEW.seat_id
       OR NEW.effective_at IS DISTINCT FROM v_decision.effective_at
       OR NEW.effective_at < v_decision.occurred_at THEN
        RAISE EXCEPTION 'SBG_CIRCLEPARTICIPATION_DECISION_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM'
       OR NEW.actor_id IS NOT NULL
       OR NEW.actor_access_epoch IS NOT NULL
       OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
       OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
       OR NEW.enterer_identity_id IS DISTINCT FROM v_decision.enterer_identity_id
       OR NEW.composing_rule_state IS DISTINCT FROM 'NA_TRANSPORT'
       OR NEW.composing_rule_reference IS NOT NULL
       OR NEW.composer_state IS DISTINCT FROM 'NA_TRANSPORT'
       OR NEW.composer_identity_id IS NOT NULL THEN
        RAISE EXCEPTION 'SBG_CIRCLEPARTICIPATION_TRANSPORT_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
"""
SBG_GUARD_CIRCLEPARTICIPATION_CLOCK = r"""
DECLARE
    v_decision public.core_circleadmissiondecision%ROWTYPE;
BEGIN

    IF NEW.recovery_office_id IS NOT NULL THEN
       PERFORM public.s015_lock_organisms(ARRAY[(SELECT lo.organism_id FROM public.core_circle c
          JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id WHERE c.id=NEW.circle_id)]);
       IF NEW.occurred_at<>NEW.effective_at OR NEW.received_at<>NEW.effective_at OR NEW.received_at>NEW.recorded_at
          OR NOT EXISTS(SELECT FROM public.core_circle c JOIN public.core_organismmembership m
             ON m.id=NEW.membership_id AND m.living_organism_id=c.parent_organism_id
             JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id
             JOIN public.core_authoritybasis b ON b.id=lo.founding_authority_basis_id
             JOIN public.core_contextualroleassignment a ON a.id=NEW.recovery_office_id AND a.membership_id=m.id
             WHERE c.id=NEW.circle_id AND m.identity_id=NEW.enterer_identity_id AND NEW.seat_id=m.membership_uuid::text
             AND NEW.seat_rule_uuid=b.basis_uuid AND NOT EXISTS(
                SELECT FROM (SELECT NEW.effective_at,NEW.recorded_at UNION ALL SELECT t,t FROM public.intevia_clock_instant() t) cuts(t,k)
                WHERE NOT public.circle_vacant(c.id,NULL,t,k)
                   OR a.assignment_uuid IS DISTINCT FROM public.circle_lo_office(lo.id,m.identity_id,t,k)
                   OR public.circle_place_at(c.id,m.id,t,k))) THEN
          RAISE EXCEPTION 'CIRCLE_RESEAT_PLACE_SOURCE' USING ERRCODE='23514';
       END IF;
       RETURN NEW;
    END IF;
    IF NEW.birth_event_id IS NOT NULL THEN
        IF NEW.decision_id IS NOT NULL OR NOT EXISTS (
           SELECT FROM public.core_circlestateevent e JOIN public.core_circle c ON c.id=e.circle_id
           JOIN public.core_organismmembership m ON m.id=NEW.membership_id AND m.living_organism_id=c.parent_organism_id
           JOIN public.core_livingorganism l ON l.id=c.parent_organism_id
           JOIN public.core_authoritybasis b ON b.id=l.founding_authority_basis_id
           WHERE e.id=NEW.birth_event_id AND e.sequence=1 AND e.action='CREATE_DORMANT' AND c.kind='GENERIC'
           AND e.circle_id=NEW.circle_id AND e.enterer_identity_id=m.identity_id AND NEW.enterer_identity_id=m.identity_id
           AND NEW.seat_id=m.membership_uuid::text AND NEW.seat_rule_uuid=b.basis_uuid
           AND e.recorded_at<=NEW.recorded_at AND NEW.occurred_at=NEW.effective_at AND NEW.received_at=NEW.effective_at
           AND e.effective_at<=NEW.effective_at AND NEW.effective_at<=NEW.recorded_at
           AND public.circle_eligible(c.id,m.id,NEW.effective_at,NEW.recorded_at)) THEN
          RAISE EXCEPTION 'CIRCLE_FOUNDER_PLACE_SOURCE' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    SELECT d.* INTO v_decision
      FROM public.core_circleadmissiondecision d
     WHERE d.id = NEW.decision_id;
    IF NOT FOUND
       OR v_decision.outcome IS DISTINCT FROM 'ACCEPT'
       OR v_decision.request_id IS NULL
       OR (SELECT q.circle_id FROM public.core_circlejoinrequest q
            WHERE q.id = v_decision.request_id) IS DISTINCT FROM NEW.circle_id
       OR (SELECT q.membership_id FROM public.core_circlejoinrequest q
            WHERE q.id = v_decision.request_id) IS DISTINCT FROM NEW.membership_id
       OR v_decision.seat_rule_uuid IS DISTINCT FROM NEW.seat_rule_uuid
       OR v_decision.seat_id IS DISTINCT FROM NEW.seat_id
       OR NEW.effective_at IS DISTINCT FROM v_decision.effective_at
       OR NEW.effective_at < v_decision.occurred_at THEN
        RAISE EXCEPTION 'SBG_CIRCLEPARTICIPATION_DECISION_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM'
       OR NEW.actor_id IS NOT NULL
       OR NEW.actor_access_epoch IS NOT NULL
       OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
       OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
       OR NEW.enterer_identity_id IS DISTINCT FROM v_decision.enterer_identity_id
       OR NEW.composing_rule_state IS DISTINCT FROM 'NA_TRANSPORT'
       OR NEW.composing_rule_reference IS NOT NULL
       OR NEW.composer_state IS DISTINCT FROM 'NA_TRANSPORT'
       OR NEW.composer_identity_id IS NOT NULL THEN
        RAISE EXCEPTION 'SBG_CIRCLEPARTICIPATION_TRANSPORT_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
"""

# public.circle_coordinator_definition()
# Its landed text: 0060_circle_coordinators.py (found there verbatim). Time calls moved: 2 statement.
CIRCLE_COORDINATOR_DEFINITION_LANDED = r"""
BEGIN
 IF TG_OP='UPDATE' THEN
    IF OLD.code='COORDINATOR' AND OLD.scope='CIRCLE' THEN
       RAISE EXCEPTION 'CIRCLE_COORDINATOR_DEFINITION_FIXED' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
 END IF;
 IF NEW.code<>'COORDINATOR' OR NEW.scope<>'CIRCLE' THEN RETURN NEW; END IF;
 IF NEW.kind IS DISTINCT FROM 'COORDINATOR' OR NEW.definition_version<>1 OR NOT NEW.active
    OR NOT EXISTS (SELECT FROM public.core_organismroledefinition lo_role
       WHERE lo_role.living_organism_id=NEW.living_organism_id AND lo_role.code='COORDINATOR'
       AND lo_role.scope='LIVING_ORGANISM' AND lo_role.active)
    OR NEW.supersedes_id IS NOT NULL OR NEW.participation_required IS DISTINCT FROM true
    OR NEW.recorded_at IS NOT NULL OR NEW.effective_at IS NULL
    OR NEW.occurred_at IS DISTINCT FROM NEW.effective_at OR NEW.received_at IS DISTINCT FROM NEW.effective_at
    OR NEW.received_at>statement_timestamp()
    OR NEW.authority_decision_reference IS NULL OR NOT EXISTS (
       SELECT FROM public.core_identity i WHERE i.id=NEW.enterer_id AND i.access_state='active'
       AND i.access_epoch=NEW.enterer_access_epoch)
    OR num_nonnulls(NEW.appointing_authority,NEW.appointing_body_reference,NEW.alternative_route_reference,
       NEW.nomination_basis,NEW.nomination_policy,NEW.nomination_dependency,NEW.ex_officio_seat_reference,
       NEW.retired_by_decision_reference,NEW.retired_effective_at,NEW.retired_received_at,
       NEW.retired_recorded_at,NEW.retirement_reason)<>0 THEN
    RAISE EXCEPTION 'CIRCLE_COORDINATOR_DEFINITION_SHAPE' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=statement_timestamp();
 RETURN NEW;
END """
CIRCLE_COORDINATOR_DEFINITION_CLOCK = r"""
BEGIN
 IF TG_OP='UPDATE' THEN
    IF OLD.code='COORDINATOR' AND OLD.scope='CIRCLE' THEN
       RAISE EXCEPTION 'CIRCLE_COORDINATOR_DEFINITION_FIXED' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
 END IF;
 IF NEW.code<>'COORDINATOR' OR NEW.scope<>'CIRCLE' THEN RETURN NEW; END IF;
 IF NEW.kind IS DISTINCT FROM 'COORDINATOR' OR NEW.definition_version<>1 OR NOT NEW.active
    OR NOT EXISTS (SELECT FROM public.core_organismroledefinition lo_role
       WHERE lo_role.living_organism_id=NEW.living_organism_id AND lo_role.code='COORDINATOR'
       AND lo_role.scope='LIVING_ORGANISM' AND lo_role.active)
    OR NEW.supersedes_id IS NOT NULL OR NEW.participation_required IS DISTINCT FROM true
    OR NEW.recorded_at IS NOT NULL OR NEW.effective_at IS NULL
    OR NEW.occurred_at IS DISTINCT FROM NEW.effective_at OR NEW.received_at IS DISTINCT FROM NEW.effective_at
    OR NEW.received_at>public.intevia_clock_statement()
    OR NEW.authority_decision_reference IS NULL OR NOT EXISTS (
       SELECT FROM public.core_identity i WHERE i.id=NEW.enterer_id AND i.access_state='active'
       AND i.access_epoch=NEW.enterer_access_epoch)
    OR num_nonnulls(NEW.appointing_authority,NEW.appointing_body_reference,NEW.alternative_route_reference,
       NEW.nomination_basis,NEW.nomination_policy,NEW.nomination_dependency,NEW.ex_officio_seat_reference,
       NEW.retired_by_decision_reference,NEW.retired_effective_at,NEW.retired_received_at,
       NEW.retired_recorded_at,NEW.retirement_reason)<>0 THEN
    RAISE EXCEPTION 'CIRCLE_COORDINATOR_DEFINITION_SHAPE' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=public.intevia_clock_statement();
 RETURN NEW;
END """

# public.circle_close_on_final_exit()
# Its landed text: 0061_circle_qualified_activation.py (found there verbatim). Time calls moved: 2 wall.
CIRCLE_CLOSE_ON_FINAL_EXIT_LANDED = r"""
DECLARE x public.core_circle; last_exit record; head public.core_circlestateevent;
        event_id uuid:=gen_random_uuid(); key text; ref text; fp text; now_cut timestamptz;
BEGIN
 SELECT c.* INTO STRICT x FROM public.core_circle c JOIN public.core_circleparticipation p ON p.circle_id=c.id
    WHERE p.id=NEW.participation_id;
 IF x.kind<>'GENERIC' THEN RETURN NULL; END IF;
 PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=x.parent_organism_id)]);
 now_cut:=clock_timestamp();
 SELECT * INTO last_exit FROM public.circle_last_ending(x.id,now_cut);
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT * INTO STRICT head FROM public.core_circlestateevent WHERE circle_id=x.id ORDER BY sequence DESC LIMIT 1;
 IF head.resulting_state='CLOSED' AND head.action='DISSOLVE_CIRCLE' THEN RETURN NULL; END IF;
 IF head.resulting_state<>'ACTIVE' OR (head.effective_at,head.occurred_at,head.received_at)>
       (last_exit.at_time,last_exit.occurred,last_exit.received) THEN
    RAISE EXCEPTION 'CIRCLE_DISSOLUTION_HISTORY' USING ERRCODE='23514';
 END IF;
 ref:='last-place:'||last_exit.place||'|cause:'||last_exit.cause;
 key:='circle-close:'||last_exit.place;
 fp:=public.c2_target(ref);
 PERFORM public.s015_append_event('core_circle',x.id,'DISSOLVE_CIRCLE','CLOSED',last_exit.at_time,last_exit.occurred,
   last_exit.received,last_exit.enterer,last_exit.basis,'s015d1:'||fp,ref,key,fp,'{}',NULL,event_id,
   head.id,head.sequence,NULL,'CIRCLES:L5:LAST_MEMBER',key);
 PERFORM public.s015_refresh_governed_cache('core_circle',x.id,last_exit.at_time,clock_timestamp());
 RETURN NULL;
END """
CIRCLE_CLOSE_ON_FINAL_EXIT_CLOCK = r"""
DECLARE x public.core_circle; last_exit record; head public.core_circlestateevent;
        event_id uuid:=gen_random_uuid(); key text; ref text; fp text; now_cut timestamptz;
BEGIN
 SELECT c.* INTO STRICT x FROM public.core_circle c JOIN public.core_circleparticipation p ON p.circle_id=c.id
    WHERE p.id=NEW.participation_id;
 IF x.kind<>'GENERIC' THEN RETURN NULL; END IF;
 PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=x.parent_organism_id)]);
 now_cut:=public.intevia_clock_instant();
 SELECT * INTO last_exit FROM public.circle_last_ending(x.id,now_cut);
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT * INTO STRICT head FROM public.core_circlestateevent WHERE circle_id=x.id ORDER BY sequence DESC LIMIT 1;
 IF head.resulting_state='CLOSED' AND head.action='DISSOLVE_CIRCLE' THEN RETURN NULL; END IF;
 IF head.resulting_state<>'ACTIVE' OR (head.effective_at,head.occurred_at,head.received_at)>
       (last_exit.at_time,last_exit.occurred,last_exit.received) THEN
    RAISE EXCEPTION 'CIRCLE_DISSOLUTION_HISTORY' USING ERRCODE='23514';
 END IF;
 ref:='last-place:'||last_exit.place||'|cause:'||last_exit.cause;
 key:='circle-close:'||last_exit.place;
 fp:=public.c2_target(ref);
 PERFORM public.s015_append_event('core_circle',x.id,'DISSOLVE_CIRCLE','CLOSED',last_exit.at_time,last_exit.occurred,
   last_exit.received,last_exit.enterer,last_exit.basis,'s015d1:'||fp,ref,key,fp,'{}',NULL,event_id,
   head.id,head.sequence,NULL,'CIRCLES:L5:LAST_MEMBER',key);
 PERFORM public.s015_refresh_governed_cache('core_circle',x.id,last_exit.at_time,public.intevia_clock_instant());
 RETURN NULL;
END """

# public.r_genesis_complete()
# Its landed text: 0062_root_genesis.py (found there verbatim). Time calls moved: 1 statement.
R_GENESIS_COMPLETE_LANDED = r"""
DECLARE lo public.core_livingorganism; required_code text;
BEGIN
 SELECT * INTO lo FROM public.core_livingorganism WHERE organism_id=NEW.organism_uuid AND platform_root;
 IF lo.id IS NULL OR lo.founder_identity_id IS DISTINCT FROM NEW.founder_id
    OR lo.founding_authority_basis_id IS DISTINCT FROM NEW.basis_id
    OR public.r_genesis_context(lo.id,NEW.decision_reference,NEW.founder_id,NEW.effective_at) IS NOT TRUE THEN
    RAISE EXCEPTION 'R_GENESIS_INCOMPLETE' USING ERRCODE='23514'; END IF;
 FOREACH required_code IN ARRAY ARRAY['INTEVIA_FOUNDER_STEWARD','INTEVIA_PRIVACY_COORDINATOR','COORDINATOR'] LOOP
    IF (SELECT count(*) FROM public.core_contextualroleassignment a
       JOIN public.core_organismmembership m ON m.id=a.membership_id
       JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
       WHERE m.identity_id=NEW.founder_id AND m.living_organism_id=lo.id AND d.code=required_code AND d.scope='LIVING_ORGANISM'
       AND a.circle_id IS NULL AND public.s015_fold_effective_state('core_contextualroleassignment',a.id,NEW.effective_at,statement_timestamp())='ACTIVE')<>1 THEN
       RAISE EXCEPTION 'R_GENESIS_FOUNDER_OFFICE_MISSING: %',required_code USING ERRCODE='23514'; END IF;
 END LOOP;
 IF (SELECT count(*) FROM public.core_rootsuccessornomination WHERE genesis_id=NEW.id)<>2
    OR EXISTS(SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismmembership m ON m.id=a.membership_id
       JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
       WHERE d.living_organism_id=lo.id AND starts_with(d.code,'INTEVIA_') AND m.identity_id<>NEW.founder_id) THEN
    RAISE EXCEPTION 'R_GENESIS_NOMINATION_NOT_ACTIVATION' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END """
R_GENESIS_COMPLETE_CLOCK = r"""
DECLARE lo public.core_livingorganism; required_code text;
BEGIN
 SELECT * INTO lo FROM public.core_livingorganism WHERE organism_id=NEW.organism_uuid AND platform_root;
 IF lo.id IS NULL OR lo.founder_identity_id IS DISTINCT FROM NEW.founder_id
    OR lo.founding_authority_basis_id IS DISTINCT FROM NEW.basis_id
    OR public.r_genesis_context(lo.id,NEW.decision_reference,NEW.founder_id,NEW.effective_at) IS NOT TRUE THEN
    RAISE EXCEPTION 'R_GENESIS_INCOMPLETE' USING ERRCODE='23514'; END IF;
 FOREACH required_code IN ARRAY ARRAY['INTEVIA_FOUNDER_STEWARD','INTEVIA_PRIVACY_COORDINATOR','COORDINATOR'] LOOP
    IF (SELECT count(*) FROM public.core_contextualroleassignment a
       JOIN public.core_organismmembership m ON m.id=a.membership_id
       JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
       WHERE m.identity_id=NEW.founder_id AND m.living_organism_id=lo.id AND d.code=required_code AND d.scope='LIVING_ORGANISM'
       AND a.circle_id IS NULL AND public.s015_fold_effective_state('core_contextualroleassignment',a.id,NEW.effective_at,public.intevia_clock_statement())='ACTIVE')<>1 THEN
       RAISE EXCEPTION 'R_GENESIS_FOUNDER_OFFICE_MISSING: %',required_code USING ERRCODE='23514'; END IF;
 END LOOP;
 IF (SELECT count(*) FROM public.core_rootsuccessornomination WHERE genesis_id=NEW.id)<>2
    OR EXISTS(SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismmembership m ON m.id=a.membership_id
       JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
       WHERE d.living_organism_id=lo.id AND starts_with(d.code,'INTEVIA_') AND m.identity_id<>NEW.founder_id) THEN
    RAISE EXCEPTION 'R_GENESIS_NOMINATION_NOT_ACTIVATION' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END """

# public.r_genesis_input()
# Its landed text: 0062_root_genesis.py (found there verbatim). Time calls moved: 1 statement.
R_GENESIS_INPUT_LANDED = r"""
DECLARE p public.core_authorityprincipal; b public.core_authoritybasis; request jsonb;
BEGIN
 IF NEW.recorded_at IS NOT NULL OR NEW.insert_xid IS NOT NULL THEN
    RAISE EXCEPTION 'R_GENESIS_DATABASE_TIME' USING ERRCODE='23514'; END IF;
 NEW.recorded_at:=statement_timestamp(); NEW.insert_xid:=pg_current_xact_id();
 IF NEW.canonical_reference IS DISTINCT FROM 'intevia-root-lo'
    OR NEW.scope IS DISTINCT FROM 'DISPOSABLE_ALPHA'
    OR encode(sha256(NEW.principal_invocation),'hex') IS DISTINCT FROM '57a247b376dfff11834ba8521ed66589a02b2cbf3c45e04bfc6f3738ea7367d7'
    OR encode(sha256(NEW.designation_invocation),'hex') IS DISTINCT FROM 'd1fbe0010f4d52e7fa562ad70000fc2076cb33b67336812ff718fb930d3ee831'
    OR encode(sha256(NEW.adoption_capture),'hex') IS DISTINCT FROM '3544c14ac5f2ec8083b6bad2666afb062a907f73e578aaf84ada37e929f3beb9'
    OR NEW.founder_id=NEW.nominee_id OR NEW.effective_at IS DISTINCT FROM NEW.received_at
    OR NEW.received_at>NEW.recorded_at
    OR encode(sha256(NEW.request_bytes),'hex') IS DISTINCT FROM NEW.request_fingerprint THEN
    RAISE EXCEPTION 'R_GENESIS_INPUT_BINDING' USING ERRCODE='23514'; END IF;
 request:=convert_from(NEW.request_bytes,'UTF8')::jsonb;
 IF request->>'schema' IS DISTINCT FROM 'R:GENESIS:1'
    OR request->>'organism_uuid' IS DISTINCT FROM NEW.organism_uuid::text
    OR request->>'canonical_reference' IS DISTINCT FROM NEW.canonical_reference
    OR request->>'scope' IS DISTINCT FROM NEW.scope
    OR request->>'request_reference' IS DISTINCT FROM NEW.request_reference
    OR request->>'idempotency_key' IS DISTINCT FROM NEW.idempotency_key
    OR request->>'principal_sha256' IS DISTINCT FROM '57a247b376dfff11834ba8521ed66589a02b2cbf3c45e04bfc6f3738ea7367d7'
    OR request->>'designation_sha256' IS DISTINCT FROM 'd1fbe0010f4d52e7fa562ad70000fc2076cb33b67336812ff718fb930d3ee831'
    OR request->>'adoption_sha256' IS DISTINCT FROM '3544c14ac5f2ec8083b6bad2666afb062a907f73e578aaf84ada37e929f3beb9'
    OR request->>'founder' IS DISTINCT FROM (SELECT identity_id::text FROM public.core_identity WHERE id=NEW.founder_id)
    OR request->>'nominee' IS DISTINCT FROM (SELECT identity_id::text FROM public.core_identity WHERE id=NEW.nominee_id)
    OR request->>'founder_username' IS DISTINCT FROM (SELECT canonical_username FROM public.core_identity WHERE id=NEW.founder_id)
    OR request->>'nominee_username' IS DISTINCT FROM (SELECT canonical_username FROM public.core_identity WHERE id=NEW.nominee_id)
    OR (SELECT count(*) FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
        WHERE i.id IN (NEW.founder_id,NEW.nominee_id) AND i.access_state='active' AND u.is_active)<>2
    OR NOT EXISTS(SELECT FROM public.core_learnerregistration r JOIN public.core_platformtermsacceptance a ON r.evidence_reference='platform-terms-acceptance:'||a.acceptance_uuid AND a.acceptance_kind='REGISTRATION'
       JOIN public.core_platformtermsversion v ON v.id=a.terms_version_id
       JOIN public.core_registrationreceipt receipt ON receipt.id=r.registration_receipt_id
       WHERE r.identity_id=NEW.founder_id AND v.is_fixture AND v.terms_version_uuid::text=request->>'terms_version_uuid' AND receipt.transaction_id=txid_current())
    OR NOT EXISTS(SELECT FROM public.core_learnerregistration r JOIN public.core_platformtermsacceptance a ON r.evidence_reference='platform-terms-acceptance:'||a.acceptance_uuid AND a.acceptance_kind='REGISTRATION'
       JOIN public.core_platformtermsversion v ON v.id=a.terms_version_id
       JOIN public.core_registrationreceipt receipt ON receipt.id=r.registration_receipt_id
       WHERE r.identity_id=NEW.nominee_id AND v.is_fixture AND v.terms_version_uuid::text=request->>'terms_version_uuid' AND receipt.transaction_id=txid_current()) THEN
    RAISE EXCEPTION 'R_GENESIS_ACCOUNT_BINDING' USING ERRCODE='23514'; END IF;
 SELECT * INTO STRICT b FROM public.core_authoritybasis WHERE id=NEW.basis_id;
 SELECT * INTO STRICT p FROM public.core_authorityprincipal WHERE id=b.authority_principal_id;
 IF p.governed_source_namespace IS DISTINCT FROM 'INTEVIA_RESERVED_GENESIS_AUTHORITY_V1'
    OR p.canonical_governed_source_id IS DISTINCT FROM 'urn:intevia:governed-source:reserved-genesis:human-governor:carmian-owen:intevia-v1.0'
    OR p.bootstrap_invocation_fingerprint IS DISTINCT FROM '57a247b376dfff11834ba8521ed66589a02b2cbf3c45e04bfc6f3738ea7367d7'
    OR b.derivation_root_principal_id IS DISTINCT FROM p.id OR b.issuer_id IS DISTINCT FROM NEW.founder_id
    OR b.authority_class IS DISTINCT FROM 'GENESIS' OR b.scope_fingerprint IS DISTINCT FROM NEW.request_fingerprint
    OR b.instrument_reference IS DISTINCT FROM 'sha256:d1fbe0010f4d52e7fa562ad70000fc2076cb33b67336812ff718fb930d3ee831'
    OR b.evidence_reference IS DISTINCT FROM 'sha256:3544c14ac5f2ec8083b6bad2666afb062a907f73e578aaf84ada37e929f3beb9'
    OR b.effective_at IS DISTINCT FROM NEW.effective_at THEN
    RAISE EXCEPTION 'R_GENESIS_PRINCIPAL_BINDING' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT FROM public.core_livingorganism WHERE platform_root) THEN
    RAISE EXCEPTION 'R_GENESIS_ALREADY_EXISTS' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END """
R_GENESIS_INPUT_CLOCK = r"""
DECLARE p public.core_authorityprincipal; b public.core_authoritybasis; request jsonb;
BEGIN
 IF NEW.recorded_at IS NOT NULL OR NEW.insert_xid IS NOT NULL THEN
    RAISE EXCEPTION 'R_GENESIS_DATABASE_TIME' USING ERRCODE='23514'; END IF;
 NEW.recorded_at:=public.intevia_clock_statement(); NEW.insert_xid:=pg_current_xact_id();
 IF NEW.canonical_reference IS DISTINCT FROM 'intevia-root-lo'
    OR NEW.scope IS DISTINCT FROM 'DISPOSABLE_ALPHA'
    OR encode(sha256(NEW.principal_invocation),'hex') IS DISTINCT FROM '57a247b376dfff11834ba8521ed66589a02b2cbf3c45e04bfc6f3738ea7367d7'
    OR encode(sha256(NEW.designation_invocation),'hex') IS DISTINCT FROM 'd1fbe0010f4d52e7fa562ad70000fc2076cb33b67336812ff718fb930d3ee831'
    OR encode(sha256(NEW.adoption_capture),'hex') IS DISTINCT FROM '3544c14ac5f2ec8083b6bad2666afb062a907f73e578aaf84ada37e929f3beb9'
    OR NEW.founder_id=NEW.nominee_id OR NEW.effective_at IS DISTINCT FROM NEW.received_at
    OR NEW.received_at>NEW.recorded_at
    OR encode(sha256(NEW.request_bytes),'hex') IS DISTINCT FROM NEW.request_fingerprint THEN
    RAISE EXCEPTION 'R_GENESIS_INPUT_BINDING' USING ERRCODE='23514'; END IF;
 request:=convert_from(NEW.request_bytes,'UTF8')::jsonb;
 IF request->>'schema' IS DISTINCT FROM 'R:GENESIS:1'
    OR request->>'organism_uuid' IS DISTINCT FROM NEW.organism_uuid::text
    OR request->>'canonical_reference' IS DISTINCT FROM NEW.canonical_reference
    OR request->>'scope' IS DISTINCT FROM NEW.scope
    OR request->>'request_reference' IS DISTINCT FROM NEW.request_reference
    OR request->>'idempotency_key' IS DISTINCT FROM NEW.idempotency_key
    OR request->>'principal_sha256' IS DISTINCT FROM '57a247b376dfff11834ba8521ed66589a02b2cbf3c45e04bfc6f3738ea7367d7'
    OR request->>'designation_sha256' IS DISTINCT FROM 'd1fbe0010f4d52e7fa562ad70000fc2076cb33b67336812ff718fb930d3ee831'
    OR request->>'adoption_sha256' IS DISTINCT FROM '3544c14ac5f2ec8083b6bad2666afb062a907f73e578aaf84ada37e929f3beb9'
    OR request->>'founder' IS DISTINCT FROM (SELECT identity_id::text FROM public.core_identity WHERE id=NEW.founder_id)
    OR request->>'nominee' IS DISTINCT FROM (SELECT identity_id::text FROM public.core_identity WHERE id=NEW.nominee_id)
    OR request->>'founder_username' IS DISTINCT FROM (SELECT canonical_username FROM public.core_identity WHERE id=NEW.founder_id)
    OR request->>'nominee_username' IS DISTINCT FROM (SELECT canonical_username FROM public.core_identity WHERE id=NEW.nominee_id)
    OR (SELECT count(*) FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
        WHERE i.id IN (NEW.founder_id,NEW.nominee_id) AND i.access_state='active' AND u.is_active)<>2
    OR NOT EXISTS(SELECT FROM public.core_learnerregistration r JOIN public.core_platformtermsacceptance a ON r.evidence_reference='platform-terms-acceptance:'||a.acceptance_uuid AND a.acceptance_kind='REGISTRATION'
       JOIN public.core_platformtermsversion v ON v.id=a.terms_version_id
       JOIN public.core_registrationreceipt receipt ON receipt.id=r.registration_receipt_id
       WHERE r.identity_id=NEW.founder_id AND v.is_fixture AND v.terms_version_uuid::text=request->>'terms_version_uuid' AND receipt.transaction_id=txid_current())
    OR NOT EXISTS(SELECT FROM public.core_learnerregistration r JOIN public.core_platformtermsacceptance a ON r.evidence_reference='platform-terms-acceptance:'||a.acceptance_uuid AND a.acceptance_kind='REGISTRATION'
       JOIN public.core_platformtermsversion v ON v.id=a.terms_version_id
       JOIN public.core_registrationreceipt receipt ON receipt.id=r.registration_receipt_id
       WHERE r.identity_id=NEW.nominee_id AND v.is_fixture AND v.terms_version_uuid::text=request->>'terms_version_uuid' AND receipt.transaction_id=txid_current()) THEN
    RAISE EXCEPTION 'R_GENESIS_ACCOUNT_BINDING' USING ERRCODE='23514'; END IF;
 SELECT * INTO STRICT b FROM public.core_authoritybasis WHERE id=NEW.basis_id;
 SELECT * INTO STRICT p FROM public.core_authorityprincipal WHERE id=b.authority_principal_id;
 IF p.governed_source_namespace IS DISTINCT FROM 'INTEVIA_RESERVED_GENESIS_AUTHORITY_V1'
    OR p.canonical_governed_source_id IS DISTINCT FROM 'urn:intevia:governed-source:reserved-genesis:human-governor:carmian-owen:intevia-v1.0'
    OR p.bootstrap_invocation_fingerprint IS DISTINCT FROM '57a247b376dfff11834ba8521ed66589a02b2cbf3c45e04bfc6f3738ea7367d7'
    OR b.derivation_root_principal_id IS DISTINCT FROM p.id OR b.issuer_id IS DISTINCT FROM NEW.founder_id
    OR b.authority_class IS DISTINCT FROM 'GENESIS' OR b.scope_fingerprint IS DISTINCT FROM NEW.request_fingerprint
    OR b.instrument_reference IS DISTINCT FROM 'sha256:d1fbe0010f4d52e7fa562ad70000fc2076cb33b67336812ff718fb930d3ee831'
    OR b.evidence_reference IS DISTINCT FROM 'sha256:3544c14ac5f2ec8083b6bad2666afb062a907f73e578aaf84ada37e929f3beb9'
    OR b.effective_at IS DISTINCT FROM NEW.effective_at THEN
    RAISE EXCEPTION 'R_GENESIS_PRINCIPAL_BINDING' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT FROM public.core_livingorganism WHERE platform_root) THEN
    RAISE EXCEPTION 'R_GENESIS_ALREADY_EXISTS' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END """

# public.r_genesis_nomination()
# Its landed text: 0062_root_genesis.py (found there verbatim). Time calls moved: 1 statement.
R_GENESIS_NOMINATION_LANDED = r"""
BEGIN
 IF NEW.recorded_at IS NOT NULL THEN RAISE EXCEPTION 'R_NOMINATION_DATABASE_TIME' USING ERRCODE='23514'; END IF;
 NEW.recorded_at:=statement_timestamp();
 IF NOT EXISTS(SELECT FROM public.core_rootgenesis g
   JOIN public.core_livingorganism lo ON lo.organism_id=g.organism_uuid AND lo.platform_root
   JOIN public.core_organismroledefinition d ON d.living_organism_id=lo.id AND d.id=NEW.definition_id
   WHERE g.id=NEW.genesis_id AND g.insert_xid=pg_current_xact_id() AND NEW.nominee_id=g.nominee_id
   AND NEW.entered_by_id=g.founder_id AND NEW.effective_at=g.effective_at
   AND d.code IN ('INTEVIA_GUIDING_STEWARD','INTEVIA_EMERGENCY_STEWARD') AND d.scope='LIVING_ORGANISM' AND d.active
   AND public.r_genesis_context(lo.id,g.decision_reference,g.founder_id,g.effective_at)) THEN
    RAISE EXCEPTION 'R_NOMINATION_SOURCE' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END """
R_GENESIS_NOMINATION_CLOCK = r"""
BEGIN
 IF NEW.recorded_at IS NOT NULL THEN RAISE EXCEPTION 'R_NOMINATION_DATABASE_TIME' USING ERRCODE='23514'; END IF;
 NEW.recorded_at:=public.intevia_clock_statement();
 IF NOT EXISTS(SELECT FROM public.core_rootgenesis g
   JOIN public.core_livingorganism lo ON lo.organism_id=g.organism_uuid AND lo.platform_root
   JOIN public.core_organismroledefinition d ON d.living_organism_id=lo.id AND d.id=NEW.definition_id
   WHERE g.id=NEW.genesis_id AND g.insert_xid=pg_current_xact_id() AND NEW.nominee_id=g.nominee_id
   AND NEW.entered_by_id=g.founder_id AND NEW.effective_at=g.effective_at
   AND d.code IN ('INTEVIA_GUIDING_STEWARD','INTEVIA_EMERGENCY_STEWARD') AND d.scope='LIVING_ORGANISM' AND d.active
   AND public.r_genesis_context(lo.id,g.decision_reference,g.founder_id,g.effective_at)) THEN
    RAISE EXCEPTION 'R_NOMINATION_SOURCE' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END """

# public.sr_definition_insert_guard()
# Its landed text: 0062_root_genesis.py (assembled by that migration). Time calls moved: 1 statement.
SR_DEFINITION_INSERT_GUARD_LANDED = r"""
DECLARE v_lo_uuid uuid; v_max_version integer; v_decision_row record; v_superseded record;
BEGIN
    IF NEW.scope IS DISTINCT FROM 'LIVING_ORGANISM' THEN
        RETURN NEW;
    END IF;
    IF NEW.recorded_at IS NOT NULL THEN
        RAISE EXCEPTION 'SR definition refused: recorded_at is database-assigned'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.recorded_at := statement_timestamp();
    IF NEW.kind IS NULL OR NEW.appointing_authority IS NULL OR NEW.nomination_basis IS NULL
       OR NEW.nomination_policy IS NULL OR NEW.participation_required IS NULL
       OR NEW.enterer_id IS NULL OR NEW.enterer_access_epoch IS NULL
       OR NEW.authority_decision_reference IS NULL
       OR NEW.occurred_at IS NULL OR NEW.effective_at IS NULL OR NEW.received_at IS NULL THEN
        RAISE EXCEPTION 'SR definition refused: content or attribution incomplete'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.kind NOT IN ('COORDINATOR', 'BOARD_OFFICE', 'CUSTOM')
       OR NEW.appointing_authority NOT IN ('LO_COORDINATOR_BODY', 'NAMED_BODY')
       OR NEW.nomination_basis NOT IN ('CONSTITUTION', 'SILENT', 'CUSTOM')
       OR NEW.nomination_policy NOT IN ('A', 'B', 'C', 'UNSUPPORTED') THEN
        RAISE EXCEPTION 'SR definition refused: value outside its domain'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.appointing_authority = 'NAMED_BODY') <> (NEW.appointing_body_reference IS NOT NULL) THEN
        RAISE EXCEPTION 'SR definition refused: appointing body reference required iff NAMED_BODY'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.nomination_policy = 'UNSUPPORTED') <> (NEW.nomination_dependency IS NOT NULL)
       OR (NEW.nomination_policy = 'UNSUPPORTED' AND NEW.nomination_basis <> 'CONSTITUTION') THEN
        RAISE EXCEPTION 'SR definition refused: UNSUPPORTED only with a constitutional basis and its dependency'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.kind = 'CUSTOM') <> (NEW.nomination_basis = 'CUSTOM') THEN
        RAISE EXCEPTION 'SR definition refused: nomination basis CUSTOM iff kind CUSTOM'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.code = 'COORDINATOR') <> (NEW.kind = 'COORDINATOR') THEN
        RAISE EXCEPTION 'SR definition refused: kind COORDINATOR iff code COORDINATOR'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.occurred_at > NEW.received_at OR NEW.received_at > NEW.recorded_at THEN
        RAISE EXCEPTION 'SR definition refused: time order'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT NEW.active THEN
        RAISE EXCEPTION 'SR definition refused: a new version is recorded active'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.retired_by_decision_reference IS NOT NULL OR NEW.retired_effective_at IS NOT NULL
       OR NEW.retired_received_at IS NOT NULL OR NEW.retired_recorded_at IS NOT NULL
       OR NEW.retirement_reason IS NOT NULL THEN
        RAISE EXCEPTION 'SR definition refused: retirement columns are set only by retirement'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT lo.organism_id INTO v_lo_uuid FROM public.core_livingorganism lo WHERE lo.id = NEW.living_organism_id;
    IF v_lo_uuid IS NULL THEN
        RAISE EXCEPTION 'SR definition refused: organism missing'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT COALESCE(MAX(d.definition_version), 0) INTO v_max_version
      FROM public.core_organismroledefinition d
     WHERE d.living_organism_id = NEW.living_organism_id AND d.code = NEW.code;
    IF NEW.definition_version <> v_max_version + 1 THEN
        RAISE EXCEPTION 'SR definition refused: version must be one above the latest for this code'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.kind = 'CUSTOM'
       AND NOT COALESCE(public.s015_entitlement_effective(v_lo_uuid, 'CUSTOM_ROLES', NEW.received_at), FALSE) THEN
        RAISE EXCEPTION 'SR definition refused: custom role entitlement absent at receipt'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.supersedes_id IS NOT NULL THEN
        SELECT d.* INTO v_superseded FROM public.core_organismroledefinition d WHERE d.id = NEW.supersedes_id;
        IF v_superseded.id IS NOT NULL AND NEW.kind IS DISTINCT FROM v_superseded.kind THEN
            RAISE EXCEPTION 'SR definition refused: an office must keep its original kind'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF v_superseded.id IS NULL
           OR v_superseded.living_organism_id <> NEW.living_organism_id
           OR v_superseded.code <> NEW.code
           OR v_superseded.scope IS DISTINCT FROM 'LIVING_ORGANISM'
           OR v_superseded.active
           OR v_superseded.retired_by_decision_reference IS NOT NULL
           OR v_superseded.retired_effective_at IS NOT NULL
           OR v_superseded.retired_received_at IS NOT NULL
           OR v_superseded.retired_recorded_at IS NOT NULL
           OR v_superseded.retirement_reason IS NOT NULL THEN
            RAISE EXCEPTION 'SR definition refused: supersedes must name the deactivated, unretired prior version of this code'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    IF v_max_version > 0 AND NEW.code <> 'COORDINATOR' THEN
        IF NEW.supersedes_id IS NULL THEN
            RAISE EXCEPTION 'SR definition refused: a later version must supersede the latest version of this office'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF v_superseded.definition_version <> v_max_version THEN
            RAISE EXCEPTION 'SR definition refused: a later version must supersede the latest version of this office'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    SELECT decision.* INTO v_decision_row
      FROM public.core_authoritydecision decision
     WHERE decision.decision_reference = NEW.authority_decision_reference;
    IF v_decision_row.decision_reference IS NULL
       OR v_decision_row.outcome <> 'QUALIFIED'
       OR v_decision_row.actor_id <> NEW.enterer_id
       OR v_decision_row.evaluated_at <> NEW.received_at THEN
        RAISE EXCEPTION 'SR definition refused: governed decision does not match definition'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF v_decision_row.action = 'GENESIS' THEN
        IF public.r_genesis_context(NEW.living_organism_id,NEW.authority_decision_reference,NEW.enterer_id,NEW.effective_at) IS NOT TRUE
           OR NOT (NEW.code=ANY(public.s015_reserved_role_codes()) OR NEW.code='COORDINATOR')
           OR NEW.scope<>'LIVING_ORGANISM' OR NEW.definition_version<>1 OR NEW.supersedes_id IS NOT NULL
           OR NEW.occurred_at IS DISTINCT FROM NEW.effective_at OR NEW.received_at IS DISTINCT FROM NEW.effective_at
           OR NEW.enterer_access_epoch IS DISTINCT FROM v_decision_row.actor_access_epoch THEN
            RAISE EXCEPTION 'R_GENESIS_DEFINITION_SOURCE' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF v_decision_row.action = 'FOUND_LIVING_ORGANISM' THEN
        -- (F) the founding branch (Advisor A-3): the founding decision attributes version 1 of
        -- the LO's own COORDINATOR definition, and nothing else; no decision is invented at founding.
        IF NEW.code <> 'COORDINATOR' OR NEW.kind <> 'COORDINATOR' OR NEW.definition_version <> 1
           OR NEW.supersedes_id IS NOT NULL
           OR v_decision_row.command_subtype <> 'FOUND_LIVING_ORGANISM'
           OR v_decision_row.target->>'action' IS DISTINCT FROM 'FOUND_LIVING_ORGANISM'
           OR v_decision_row.target->>'proposed_organism_uuid' IS DISTINCT FROM v_lo_uuid::text THEN
            RAISE EXCEPTION 'SR definition refused: founding decision does not match definition'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF v_decision_row.action = 'RECORD_ROLE_DEFINITION' THEN
        -- (D) the definition act: the decision names this very version (the 0036/0044 pattern).
        IF NEW.code = 'COORDINATOR' THEN
            RAISE EXCEPTION 'SR definition refused: the Coordinator office is established at founding and cannot be changed here'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF starts_with(NEW.code, 'INTEVIA_') THEN
            RAISE EXCEPTION 'SR definition refused: reserved root office'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF v_decision_row.command_subtype <> 'RECORD_ROLE_DEFINITION'
           OR v_decision_row.target->>'action' IS DISTINCT FROM 'RECORD_ROLE_DEFINITION'
           OR v_decision_row.target->>'living_organism_id' IS DISTINCT FROM v_lo_uuid::text
           OR v_decision_row.target->>'code' IS DISTINCT FROM NEW.code
           OR v_decision_row.target->>'definition_version' IS DISTINCT FROM NEW.definition_version::text
           OR v_decision_row.target->>'kind' IS DISTINCT FROM NEW.kind
           OR v_decision_row.target->>'nomination_policy' IS DISTINCT FROM NEW.nomination_policy
           OR v_decision_row.target->>'appointing_authority' IS DISTINCT FROM NEW.appointing_authority THEN
            RAISE EXCEPTION 'SR definition refused: governed decision does not match definition'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'SR definition refused: governed decision does not match definition'
        USING ERRCODE = 'integrity_constraint_violation';
END
"""
SR_DEFINITION_INSERT_GUARD_CLOCK = r"""
DECLARE v_lo_uuid uuid; v_max_version integer; v_decision_row record; v_superseded record;
BEGIN
    IF NEW.scope IS DISTINCT FROM 'LIVING_ORGANISM' THEN
        RETURN NEW;
    END IF;
    IF NEW.recorded_at IS NOT NULL THEN
        RAISE EXCEPTION 'SR definition refused: recorded_at is database-assigned'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.recorded_at := public.intevia_clock_statement();
    IF NEW.kind IS NULL OR NEW.appointing_authority IS NULL OR NEW.nomination_basis IS NULL
       OR NEW.nomination_policy IS NULL OR NEW.participation_required IS NULL
       OR NEW.enterer_id IS NULL OR NEW.enterer_access_epoch IS NULL
       OR NEW.authority_decision_reference IS NULL
       OR NEW.occurred_at IS NULL OR NEW.effective_at IS NULL OR NEW.received_at IS NULL THEN
        RAISE EXCEPTION 'SR definition refused: content or attribution incomplete'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.kind NOT IN ('COORDINATOR', 'BOARD_OFFICE', 'CUSTOM')
       OR NEW.appointing_authority NOT IN ('LO_COORDINATOR_BODY', 'NAMED_BODY')
       OR NEW.nomination_basis NOT IN ('CONSTITUTION', 'SILENT', 'CUSTOM')
       OR NEW.nomination_policy NOT IN ('A', 'B', 'C', 'UNSUPPORTED') THEN
        RAISE EXCEPTION 'SR definition refused: value outside its domain'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.appointing_authority = 'NAMED_BODY') <> (NEW.appointing_body_reference IS NOT NULL) THEN
        RAISE EXCEPTION 'SR definition refused: appointing body reference required iff NAMED_BODY'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.nomination_policy = 'UNSUPPORTED') <> (NEW.nomination_dependency IS NOT NULL)
       OR (NEW.nomination_policy = 'UNSUPPORTED' AND NEW.nomination_basis <> 'CONSTITUTION') THEN
        RAISE EXCEPTION 'SR definition refused: UNSUPPORTED only with a constitutional basis and its dependency'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.kind = 'CUSTOM') <> (NEW.nomination_basis = 'CUSTOM') THEN
        RAISE EXCEPTION 'SR definition refused: nomination basis CUSTOM iff kind CUSTOM'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (NEW.code = 'COORDINATOR') <> (NEW.kind = 'COORDINATOR') THEN
        RAISE EXCEPTION 'SR definition refused: kind COORDINATOR iff code COORDINATOR'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.occurred_at > NEW.received_at OR NEW.received_at > NEW.recorded_at THEN
        RAISE EXCEPTION 'SR definition refused: time order'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT NEW.active THEN
        RAISE EXCEPTION 'SR definition refused: a new version is recorded active'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.retired_by_decision_reference IS NOT NULL OR NEW.retired_effective_at IS NOT NULL
       OR NEW.retired_received_at IS NOT NULL OR NEW.retired_recorded_at IS NOT NULL
       OR NEW.retirement_reason IS NOT NULL THEN
        RAISE EXCEPTION 'SR definition refused: retirement columns are set only by retirement'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT lo.organism_id INTO v_lo_uuid FROM public.core_livingorganism lo WHERE lo.id = NEW.living_organism_id;
    IF v_lo_uuid IS NULL THEN
        RAISE EXCEPTION 'SR definition refused: organism missing'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT COALESCE(MAX(d.definition_version), 0) INTO v_max_version
      FROM public.core_organismroledefinition d
     WHERE d.living_organism_id = NEW.living_organism_id AND d.code = NEW.code;
    IF NEW.definition_version <> v_max_version + 1 THEN
        RAISE EXCEPTION 'SR definition refused: version must be one above the latest for this code'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.kind = 'CUSTOM'
       AND NOT COALESCE(public.s015_entitlement_effective(v_lo_uuid, 'CUSTOM_ROLES', NEW.received_at), FALSE) THEN
        RAISE EXCEPTION 'SR definition refused: custom role entitlement absent at receipt'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.supersedes_id IS NOT NULL THEN
        SELECT d.* INTO v_superseded FROM public.core_organismroledefinition d WHERE d.id = NEW.supersedes_id;
        IF v_superseded.id IS NOT NULL AND NEW.kind IS DISTINCT FROM v_superseded.kind THEN
            RAISE EXCEPTION 'SR definition refused: an office must keep its original kind'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF v_superseded.id IS NULL
           OR v_superseded.living_organism_id <> NEW.living_organism_id
           OR v_superseded.code <> NEW.code
           OR v_superseded.scope IS DISTINCT FROM 'LIVING_ORGANISM'
           OR v_superseded.active
           OR v_superseded.retired_by_decision_reference IS NOT NULL
           OR v_superseded.retired_effective_at IS NOT NULL
           OR v_superseded.retired_received_at IS NOT NULL
           OR v_superseded.retired_recorded_at IS NOT NULL
           OR v_superseded.retirement_reason IS NOT NULL THEN
            RAISE EXCEPTION 'SR definition refused: supersedes must name the deactivated, unretired prior version of this code'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    IF v_max_version > 0 AND NEW.code <> 'COORDINATOR' THEN
        IF NEW.supersedes_id IS NULL THEN
            RAISE EXCEPTION 'SR definition refused: a later version must supersede the latest version of this office'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF v_superseded.definition_version <> v_max_version THEN
            RAISE EXCEPTION 'SR definition refused: a later version must supersede the latest version of this office'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    SELECT decision.* INTO v_decision_row
      FROM public.core_authoritydecision decision
     WHERE decision.decision_reference = NEW.authority_decision_reference;
    IF v_decision_row.decision_reference IS NULL
       OR v_decision_row.outcome <> 'QUALIFIED'
       OR v_decision_row.actor_id <> NEW.enterer_id
       OR v_decision_row.evaluated_at <> NEW.received_at THEN
        RAISE EXCEPTION 'SR definition refused: governed decision does not match definition'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF v_decision_row.action = 'GENESIS' THEN
        IF public.r_genesis_context(NEW.living_organism_id,NEW.authority_decision_reference,NEW.enterer_id,NEW.effective_at) IS NOT TRUE
           OR NOT (NEW.code=ANY(public.s015_reserved_role_codes()) OR NEW.code='COORDINATOR')
           OR NEW.scope<>'LIVING_ORGANISM' OR NEW.definition_version<>1 OR NEW.supersedes_id IS NOT NULL
           OR NEW.occurred_at IS DISTINCT FROM NEW.effective_at OR NEW.received_at IS DISTINCT FROM NEW.effective_at
           OR NEW.enterer_access_epoch IS DISTINCT FROM v_decision_row.actor_access_epoch THEN
            RAISE EXCEPTION 'R_GENESIS_DEFINITION_SOURCE' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF v_decision_row.action = 'FOUND_LIVING_ORGANISM' THEN
        -- (F) the founding branch (Advisor A-3): the founding decision attributes version 1 of
        -- the LO's own COORDINATOR definition, and nothing else; no decision is invented at founding.
        IF NEW.code <> 'COORDINATOR' OR NEW.kind <> 'COORDINATOR' OR NEW.definition_version <> 1
           OR NEW.supersedes_id IS NOT NULL
           OR v_decision_row.command_subtype <> 'FOUND_LIVING_ORGANISM'
           OR v_decision_row.target->>'action' IS DISTINCT FROM 'FOUND_LIVING_ORGANISM'
           OR v_decision_row.target->>'proposed_organism_uuid' IS DISTINCT FROM v_lo_uuid::text THEN
            RAISE EXCEPTION 'SR definition refused: founding decision does not match definition'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF v_decision_row.action = 'RECORD_ROLE_DEFINITION' THEN
        -- (D) the definition act: the decision names this very version (the 0036/0044 pattern).
        IF NEW.code = 'COORDINATOR' THEN
            RAISE EXCEPTION 'SR definition refused: the Coordinator office is established at founding and cannot be changed here'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF starts_with(NEW.code, 'INTEVIA_') THEN
            RAISE EXCEPTION 'SR definition refused: reserved root office'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF v_decision_row.command_subtype <> 'RECORD_ROLE_DEFINITION'
           OR v_decision_row.target->>'action' IS DISTINCT FROM 'RECORD_ROLE_DEFINITION'
           OR v_decision_row.target->>'living_organism_id' IS DISTINCT FROM v_lo_uuid::text
           OR v_decision_row.target->>'code' IS DISTINCT FROM NEW.code
           OR v_decision_row.target->>'definition_version' IS DISTINCT FROM NEW.definition_version::text
           OR v_decision_row.target->>'kind' IS DISTINCT FROM NEW.kind
           OR v_decision_row.target->>'nomination_policy' IS DISTINCT FROM NEW.nomination_policy
           OR v_decision_row.target->>'appointing_authority' IS DISTINCT FROM NEW.appointing_authority THEN
            RAISE EXCEPTION 'SR definition refused: governed decision does not match definition'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'SR definition refused: governed decision does not match definition'
        USING ERRCODE = 'integrity_constraint_violation';
END
"""

# public.r_circle_description_input()
# Its landed text: 0063_root_circles.py (found there verbatim). Time calls moved: 1 statement.
R_CIRCLE_DESCRIPTION_INPUT_LANDED = r"""
DECLARE c public.core_circle; opening public.core_circlestateevent;
BEGIN
 IF NEW.recorded_at IS NOT NULL THEN RAISE EXCEPTION 'R_DESCRIPTION_DATABASE_TIME' USING ERRCODE='23514'; END IF;
 NEW.recorded_at:=statement_timestamp();
 SELECT * INTO STRICT c FROM public.core_circle WHERE id=NEW.circle_id;
 SELECT * INTO opening FROM public.core_circlestateevent WHERE id=NEW.birth_event_id AND circle_id=c.id AND sequence=1;
 IF opening.id IS NULL OR opening.action<>'CREATE_DORMANT' OR c.kind<>'GENERIC'
    OR opening.evidence_reference IS DISTINCT FROM 'circle-purpose:'||NEW.purpose
    OR NEW.enterer_id IS DISTINCT FROM opening.enterer_identity_id
    OR btrim(NEW.name)='' OR NEW.effective_at>NEW.recorded_at
    OR NEW.effective_at<opening.effective_at
    OR NOT EXISTS(SELECT FROM public.circle_holders(c.id,NEW.effective_at,NEW.recorded_at) h WHERE h.person=NEW.enterer_id)
    OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=NEW.enterer_id AND i.access_epoch=NEW.enterer_access_epoch AND i.access_state='active') THEN
    RAISE EXCEPTION 'R_DESCRIPTION_BIRTH_SOURCE' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END """
R_CIRCLE_DESCRIPTION_INPUT_CLOCK = r"""
DECLARE c public.core_circle; opening public.core_circlestateevent;
BEGIN
 IF NEW.recorded_at IS NOT NULL THEN RAISE EXCEPTION 'R_DESCRIPTION_DATABASE_TIME' USING ERRCODE='23514'; END IF;
 NEW.recorded_at:=public.intevia_clock_statement();
 SELECT * INTO STRICT c FROM public.core_circle WHERE id=NEW.circle_id;
 SELECT * INTO opening FROM public.core_circlestateevent WHERE id=NEW.birth_event_id AND circle_id=c.id AND sequence=1;
 IF opening.id IS NULL OR opening.action<>'CREATE_DORMANT' OR c.kind<>'GENERIC'
    OR opening.evidence_reference IS DISTINCT FROM 'circle-purpose:'||NEW.purpose
    OR NEW.enterer_id IS DISTINCT FROM opening.enterer_identity_id
    OR btrim(NEW.name)='' OR NEW.effective_at>NEW.recorded_at
    OR NEW.effective_at<opening.effective_at
    OR NOT EXISTS(SELECT FROM public.circle_holders(c.id,NEW.effective_at,NEW.recorded_at) h WHERE h.person=NEW.enterer_id)
    OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=NEW.enterer_id AND i.access_epoch=NEW.enterer_access_epoch AND i.access_state='active') THEN
    RAISE EXCEPTION 'R_DESCRIPTION_BIRTH_SOURCE' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END """

# public.r_root_circle_batch_input()
# Its landed text: 0063_root_circles.py (found there verbatim). Time calls moved: 1 statement.
R_ROOT_CIRCLE_BATCH_INPUT_LANDED = r"""
DECLARE lo public.core_livingorganism; g public.core_rootgenesis; item jsonb;
BEGIN
 IF NEW.recorded_at IS NOT NULL THEN RAISE EXCEPTION 'R_BATCH_DATABASE_TIME' USING ERRCODE='23514'; END IF;
 NEW.recorded_at:=statement_timestamp();
 SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE id=NEW.living_organism_id;
 SELECT * INTO g FROM public.core_rootgenesis WHERE organism_uuid=lo.organism_id;
 IF g.id IS NULL OR public.r_circle_root_qualified(lo.id,NEW.effective_at,NEW.recorded_at) IS NOT TRUE OR NEW.enterer_id<>g.founder_id
    OR NEW.effective_at>NEW.recorded_at OR jsonb_typeof(NEW.circles)<>'array' OR jsonb_array_length(NEW.circles)<>10
    OR public.circle_lo_office(lo.id,NEW.enterer_id,NEW.effective_at,NEW.recorded_at) IS NULL
    OR NOT EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.living_organism_id=lo.id AND e.action='BIRTHING' AND e.effective_at<=NEW.effective_at AND e.recorded_at<=NEW.recorded_at) THEN
    RAISE EXCEPTION 'R_ROOT_CIRCLE_BATCH_SOURCE' USING ERRCODE='23514'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(NEW.circles) LOOP
    IF NOT EXISTS(SELECT FROM public.core_circle c JOIN public.core_circlebirthdescription d ON d.circle_id=c.id
        WHERE c.parent_organism_id=lo.id AND c.circle_uuid::text=item->>'circle_uuid' AND d.name=item->>'name'
        AND c.kind='GENERIC' AND d.enterer_id=g.founder_id AND d.recorded_at<=NEW.recorded_at
        AND public.s015_fold_effective_state('core_circle',c.id,NEW.effective_at,NEW.recorded_at)='ACTIVE'
        AND EXISTS(SELECT FROM public.circle_holders(c.id,NEW.effective_at,NEW.recorded_at) h WHERE h.person=g.founder_id)) THEN
       RAISE EXCEPTION 'R_ROOT_CIRCLE_BATCH_INCOMPLETE' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF (SELECT count(DISTINCT value->>'name') FROM jsonb_array_elements(NEW.circles))<>10
    OR EXISTS(SELECT value->>'name' FROM jsonb_array_elements(NEW.circles) EXCEPT SELECT unnest(ARRAY[
       'INTEVIA Education','INTEVIA Library','INTEVIA Support','INTEVIA Relationships','INTEVIA Operations',
       'INTEVIA People','INTEVIA Finance','INTEVIA Legal and Compliance','INTEVIA Technology','INTEVIA Marketing and Communications'])) THEN
    RAISE EXCEPTION 'R_ROOT_CIRCLE_BATCH_INVENTORY' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END """
R_ROOT_CIRCLE_BATCH_INPUT_CLOCK = r"""
DECLARE lo public.core_livingorganism; g public.core_rootgenesis; item jsonb;
BEGIN
 IF NEW.recorded_at IS NOT NULL THEN RAISE EXCEPTION 'R_BATCH_DATABASE_TIME' USING ERRCODE='23514'; END IF;
 NEW.recorded_at:=public.intevia_clock_statement();
 SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE id=NEW.living_organism_id;
 SELECT * INTO g FROM public.core_rootgenesis WHERE organism_uuid=lo.organism_id;
 IF g.id IS NULL OR public.r_circle_root_qualified(lo.id,NEW.effective_at,NEW.recorded_at) IS NOT TRUE OR NEW.enterer_id<>g.founder_id
    OR NEW.effective_at>NEW.recorded_at OR jsonb_typeof(NEW.circles)<>'array' OR jsonb_array_length(NEW.circles)<>10
    OR public.circle_lo_office(lo.id,NEW.enterer_id,NEW.effective_at,NEW.recorded_at) IS NULL
    OR NOT EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.living_organism_id=lo.id AND e.action='BIRTHING' AND e.effective_at<=NEW.effective_at AND e.recorded_at<=NEW.recorded_at) THEN
    RAISE EXCEPTION 'R_ROOT_CIRCLE_BATCH_SOURCE' USING ERRCODE='23514'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(NEW.circles) LOOP
    IF NOT EXISTS(SELECT FROM public.core_circle c JOIN public.core_circlebirthdescription d ON d.circle_id=c.id
        WHERE c.parent_organism_id=lo.id AND c.circle_uuid::text=item->>'circle_uuid' AND d.name=item->>'name'
        AND c.kind='GENERIC' AND d.enterer_id=g.founder_id AND d.recorded_at<=NEW.recorded_at
        AND public.s015_fold_effective_state('core_circle',c.id,NEW.effective_at,NEW.recorded_at)='ACTIVE'
        AND EXISTS(SELECT FROM public.circle_holders(c.id,NEW.effective_at,NEW.recorded_at) h WHERE h.person=g.founder_id)) THEN
       RAISE EXCEPTION 'R_ROOT_CIRCLE_BATCH_INCOMPLETE' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF (SELECT count(DISTINCT value->>'name') FROM jsonb_array_elements(NEW.circles))<>10
    OR EXISTS(SELECT value->>'name' FROM jsonb_array_elements(NEW.circles) EXCEPT SELECT unnest(ARRAY[
       'INTEVIA Education','INTEVIA Library','INTEVIA Support','INTEVIA Relationships','INTEVIA Operations',
       'INTEVIA People','INTEVIA Finance','INTEVIA Legal and Compliance','INTEVIA Technology','INTEVIA Marketing and Communications'])) THEN
    RAISE EXCEPTION 'R_ROOT_CIRCLE_BATCH_INVENTORY' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END """

# public.r_publication_declaration_input()
# Its landed text: 0064_root_publication_declaration.py (assembled by that migration). Time calls moved: 1 statement.
R_PUBLICATION_DECLARATION_INPUT_LANDED = r"""
DECLARE executed timestamptz:=statement_timestamp();
BEGIN
 IF NEW.recorded_at IS NOT NULL THEN RAISE EXCEPTION 'R_DECLARATION_DATABASE_TIME' USING ERRCODE='23514'; END IF;
 NEW.recorded_at:=executed;
 IF NEW.founder_office_id IS NULL OR NEW.record_class IS DISTINCT FROM 'DECISION'
    OR NEW.access_posture IS DISTINCT FROM 'PRIVATE_DECLARING_ACTOR'
    OR NEW.verification_reference IS DISTINCT FROM 'sha256:e9f7dfb81e9f6ae7e7e0cf4b0f1210269b1973b87ce13d3afcdd88e1f9b5836c'
    OR NOT EXISTS(SELECT FROM public.core_livingorganism lo WHERE lo.id=NEW.living_organism_id AND lo.head_event_id=NEW.prior_root_event_id)
    OR NEW.decision_kind IS DISTINCT FROM 'PUBLICATION_EXCEPTION'
    OR NEW.declared_capacity IS DISTINCT FROM 'HUMAN_GOVERNOR_AND_INTEVIA_FOUNDER_STEWARD'
    OR NEW.text_sha256 IS DISTINCT FROM 'bbce006ebf7326b848803c8fbe9ba71ac2adc9fc7c43cbc275d8dddd403b1acf'
    OR encode(sha256(convert_to(NEW.text,'UTF8')),'hex') IS DISTINCT FROM NEW.text_sha256
    OR NEW.source_hashes IS DISTINCT FROM '{"HG_RULINGS_OQ47_OQ48_20261009.md":"20766b6a84c58e6eb2370cd8761dc6cf5b13ddaa889c42693e2e936f56fc6e1e","HG_RULING_OQ46_FOUNDER_STEWARD_EXCEPTION_20261009.md":"d3de399e38330e00c226d2ddf976a56df9288598d938222ceeba15c0f0078be8","HG_RULING_OQ46_SUCCESSION_CORRECTION_20261009.md":"70d626c03d7cbcbbc62c9318ef40b392e16b4ede8f2661cab1b8e1b8b5fd5240","VC_B14_RS107_VERIFICATION_RECORD_v1_0.md":"e9f7dfb81e9f6ae7e7e0cf4b0f1210269b1973b87ce13d3afcdd88e1f9b5836c"}'::jsonb
    OR NEW.effective_at IS DISTINCT FROM NEW.received_at OR NEW.received_at>executed
    OR public.r_founder_office(NEW.living_organism_id,NEW.actor_id,NEW.effective_at,executed) IS DISTINCT FROM NEW.founder_office_id
    OR public.r_founder_office(NEW.living_organism_id,NEW.actor_id,executed,executed) IS DISTINCT FROM NEW.founder_office_id
    OR NOT EXISTS(SELECT FROM public.core_livingorganism lo WHERE lo.id=NEW.living_organism_id AND lo.founding_authority_basis_id=NEW.authority_basis_id)
    OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_access_epoch AND i.access_state='active') THEN
    RAISE EXCEPTION 'R_DECLARATION_SOURCE' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END """
R_PUBLICATION_DECLARATION_INPUT_CLOCK = r"""
DECLARE executed timestamptz:=public.intevia_clock_statement();
BEGIN
 IF NEW.recorded_at IS NOT NULL THEN RAISE EXCEPTION 'R_DECLARATION_DATABASE_TIME' USING ERRCODE='23514'; END IF;
 NEW.recorded_at:=executed;
 IF NEW.founder_office_id IS NULL OR NEW.record_class IS DISTINCT FROM 'DECISION'
    OR NEW.access_posture IS DISTINCT FROM 'PRIVATE_DECLARING_ACTOR'
    OR NEW.verification_reference IS DISTINCT FROM 'sha256:e9f7dfb81e9f6ae7e7e0cf4b0f1210269b1973b87ce13d3afcdd88e1f9b5836c'
    OR NOT EXISTS(SELECT FROM public.core_livingorganism lo WHERE lo.id=NEW.living_organism_id AND lo.head_event_id=NEW.prior_root_event_id)
    OR NEW.decision_kind IS DISTINCT FROM 'PUBLICATION_EXCEPTION'
    OR NEW.declared_capacity IS DISTINCT FROM 'HUMAN_GOVERNOR_AND_INTEVIA_FOUNDER_STEWARD'
    OR NEW.text_sha256 IS DISTINCT FROM 'bbce006ebf7326b848803c8fbe9ba71ac2adc9fc7c43cbc275d8dddd403b1acf'
    OR encode(sha256(convert_to(NEW.text,'UTF8')),'hex') IS DISTINCT FROM NEW.text_sha256
    OR NEW.source_hashes IS DISTINCT FROM '{"HG_RULINGS_OQ47_OQ48_20261009.md":"20766b6a84c58e6eb2370cd8761dc6cf5b13ddaa889c42693e2e936f56fc6e1e","HG_RULING_OQ46_FOUNDER_STEWARD_EXCEPTION_20261009.md":"d3de399e38330e00c226d2ddf976a56df9288598d938222ceeba15c0f0078be8","HG_RULING_OQ46_SUCCESSION_CORRECTION_20261009.md":"70d626c03d7cbcbbc62c9318ef40b392e16b4ede8f2661cab1b8e1b8b5fd5240","VC_B14_RS107_VERIFICATION_RECORD_v1_0.md":"e9f7dfb81e9f6ae7e7e0cf4b0f1210269b1973b87ce13d3afcdd88e1f9b5836c"}'::jsonb
    OR NEW.effective_at IS DISTINCT FROM NEW.received_at OR NEW.received_at>executed
    OR public.r_founder_office(NEW.living_organism_id,NEW.actor_id,NEW.effective_at,executed) IS DISTINCT FROM NEW.founder_office_id
    OR public.r_founder_office(NEW.living_organism_id,NEW.actor_id,executed,executed) IS DISTINCT FROM NEW.founder_office_id
    OR NOT EXISTS(SELECT FROM public.core_livingorganism lo WHERE lo.id=NEW.living_organism_id AND lo.founding_authority_basis_id=NEW.authority_basis_id)
    OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_access_epoch AND i.access_state='active') THEN
    RAISE EXCEPTION 'R_DECLARATION_SOURCE' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END """

# public.r_activation_input()
# Its landed text: 0065_root_continuity.py (found there verbatim). Time calls moved: 1 statement, 1 transaction.
R_ACTIVATION_INPUT_LANDED = r"""
DECLARE stamp timestamptz:=statement_timestamp(); loss public.core_rootlossconfirmation; lo_uuid uuid;
BEGIN
 SELECT * INTO STRICT loss FROM public.core_rootlossconfirmation WHERE id=NEW.confirmation_id;
 SELECT organism_id INTO STRICT lo_uuid FROM public.core_livingorganism WHERE id=loss.organism_id;
 PERFORM public.s015_lock_organisms(ARRAY[lo_uuid]);
 IF NEW.recorded_at IS NOT NULL OR NEW.effective_at NOT BETWEEN transaction_timestamp() AND stamp
 OR NOT EXISTS(SELECT FROM public.core_organismroledefinition d WHERE d.id=NEW.office_definition_id
      AND d.living_organism_id=loss.organism_id AND d.active AND d.scope='LIVING_ORGANISM'
      AND d.code=CASE loss.event_kind WHEN 'DEATH' THEN 'INTEVIA_GUIDING_STEWARD' ELSE 'INTEVIA_EMERGENCY_STEWARD' END)
 OR NEW.actor_id<>loss.beneficiary_id OR NOT EXISTS(SELECT FROM public.core_identity
     WHERE id=NEW.actor_id AND access_state='active' AND access_epoch=NEW.actor_epoch)
 OR NOT public.r_grant_ok(loss.id,NEW.grant_id,NEW.grant_basis_id,NEW.holder_finding_id,NEW.domains,
        NEW.bounds,NEW.hard_expiry,NEW.effective_at,stamp)
 OR NOT public.r_grant_ok(loss.id,NEW.grant_id,NEW.grant_basis_id,NEW.holder_finding_id,NEW.domains,
        NEW.bounds,NEW.hard_expiry,stamp,stamp)
 OR (loss.event_kind='DEATH' AND NEW.envelope_id IS NOT NULL)
 OR (loss.event_kind='INCAPACITY' AND NOT EXISTS(SELECT FROM public.core_emergencyauthorityenvelope env
   JOIN public.core_authoritybasis b ON b.id=NEW.grant_basis_id WHERE env.id=NEW.envelope_id
   AND env.qualifying_constitutional_grant_id=NEW.grant_id AND env.beneficiary_id=NEW.actor_id
   AND env.authority_principal_id=b.authority_principal_id AND env.derivation_root_principal_id=b.derivation_root_principal_id
   AND env.hard_expiry=NEW.hard_expiry AND env.permitted_command_set='["PUBLISH_OWN"]'::jsonb
   AND env.target_scope_set=jsonb_build_array(lo_uuid::text,NEW.domains)
   AND env.proportionality_bounds=jsonb_build_object('necessity_and_bounds',NEW.bounds))) THEN
  RAISE EXCEPTION 'R_ACTIVATION_GRANT_OR_ENVELOPE' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=stamp; RETURN NEW;
END """
R_ACTIVATION_INPUT_CLOCK = r"""
DECLARE stamp timestamptz:=public.intevia_clock_statement(); loss public.core_rootlossconfirmation; lo_uuid uuid;
BEGIN
 SELECT * INTO STRICT loss FROM public.core_rootlossconfirmation WHERE id=NEW.confirmation_id;
 SELECT organism_id INTO STRICT lo_uuid FROM public.core_livingorganism WHERE id=loss.organism_id;
 PERFORM public.s015_lock_organisms(ARRAY[lo_uuid]);
 IF NEW.recorded_at IS NOT NULL OR NEW.effective_at NOT BETWEEN public.intevia_clock_transaction() AND stamp
 OR NOT EXISTS(SELECT FROM public.core_organismroledefinition d WHERE d.id=NEW.office_definition_id
      AND d.living_organism_id=loss.organism_id AND d.active AND d.scope='LIVING_ORGANISM'
      AND d.code=CASE loss.event_kind WHEN 'DEATH' THEN 'INTEVIA_GUIDING_STEWARD' ELSE 'INTEVIA_EMERGENCY_STEWARD' END)
 OR NEW.actor_id<>loss.beneficiary_id OR NOT EXISTS(SELECT FROM public.core_identity
     WHERE id=NEW.actor_id AND access_state='active' AND access_epoch=NEW.actor_epoch)
 OR NOT public.r_grant_ok(loss.id,NEW.grant_id,NEW.grant_basis_id,NEW.holder_finding_id,NEW.domains,
        NEW.bounds,NEW.hard_expiry,NEW.effective_at,stamp)
 OR NOT public.r_grant_ok(loss.id,NEW.grant_id,NEW.grant_basis_id,NEW.holder_finding_id,NEW.domains,
        NEW.bounds,NEW.hard_expiry,stamp,stamp)
 OR (loss.event_kind='DEATH' AND NEW.envelope_id IS NOT NULL)
 OR (loss.event_kind='INCAPACITY' AND NOT EXISTS(SELECT FROM public.core_emergencyauthorityenvelope env
   JOIN public.core_authoritybasis b ON b.id=NEW.grant_basis_id WHERE env.id=NEW.envelope_id
   AND env.qualifying_constitutional_grant_id=NEW.grant_id AND env.beneficiary_id=NEW.actor_id
   AND env.authority_principal_id=b.authority_principal_id AND env.derivation_root_principal_id=b.derivation_root_principal_id
   AND env.hard_expiry=NEW.hard_expiry AND env.permitted_command_set='["PUBLISH_OWN"]'::jsonb
   AND env.target_scope_set=jsonb_build_array(lo_uuid::text,NEW.domains)
   AND env.proportionality_bounds=jsonb_build_object('necessity_and_bounds',NEW.bounds))) THEN
  RAISE EXCEPTION 'R_ACTIVATION_GRANT_OR_ENVELOPE' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=stamp; RETURN NEW;
END """

# public.r_confirm_input()
# Its landed text: 0065_root_continuity.py (found there verbatim). Time calls moved: 1 statement, 1 transaction.
R_CONFIRM_INPUT_LANDED = r"""
DECLARE stamp timestamptz:=statement_timestamp(); prior text;
BEGIN
 PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=NEW.organism_id)]);
 SELECT event_kind INTO prior FROM public.core_rootlossconfirmation
 WHERE id=public.r_latest_loss(NEW.organism_id,stamp,stamp);
 IF NEW.recorded_at IS NOT NULL OR NEW.effective_at NOT BETWEEN transaction_timestamp() AND stamp
 OR NEW.event_kind NOT IN ('DEATH','INCAPACITY') OR prior='DEATH' OR prior=NEW.event_kind
 OR EXISTS(SELECT FROM public.core_rootlossconfirmation WHERE organism_id=NEW.organism_id AND effective_at>NEW.effective_at)
 OR NOT EXISTS(SELECT FROM public.core_identity WHERE id=NEW.actor_id AND access_state='active' AND access_epoch=NEW.actor_epoch)
 OR public.r_loss_source(NEW.organism_id,NEW.confirmer_value_id,NEW.event_kind,NEW.actor_id,
       NEW.beneficiary_id,NEW.finding_id,NEW.effective_at,stamp) IS NOT TRUE
 OR public.r_loss_source(NEW.organism_id,NEW.confirmer_value_id,NEW.event_kind,NEW.actor_id,
       NEW.beneficiary_id,NEW.finding_id,stamp,stamp) IS NOT TRUE THEN
  RAISE EXCEPTION 'R_CONFIRMATION_AUTHORITY_OR_EVIDENCE' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=stamp; RETURN NEW;
END """
R_CONFIRM_INPUT_CLOCK = r"""
DECLARE stamp timestamptz:=public.intevia_clock_statement(); prior text;
BEGIN
 PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=NEW.organism_id)]);
 SELECT event_kind INTO prior FROM public.core_rootlossconfirmation
 WHERE id=public.r_latest_loss(NEW.organism_id,stamp,stamp);
 IF NEW.recorded_at IS NOT NULL OR NEW.effective_at NOT BETWEEN public.intevia_clock_transaction() AND stamp
 OR NEW.event_kind NOT IN ('DEATH','INCAPACITY') OR prior='DEATH' OR prior=NEW.event_kind
 OR EXISTS(SELECT FROM public.core_rootlossconfirmation WHERE organism_id=NEW.organism_id AND effective_at>NEW.effective_at)
 OR NOT EXISTS(SELECT FROM public.core_identity WHERE id=NEW.actor_id AND access_state='active' AND access_epoch=NEW.actor_epoch)
 OR public.r_loss_source(NEW.organism_id,NEW.confirmer_value_id,NEW.event_kind,NEW.actor_id,
       NEW.beneficiary_id,NEW.finding_id,NEW.effective_at,stamp) IS NOT TRUE
 OR public.r_loss_source(NEW.organism_id,NEW.confirmer_value_id,NEW.event_kind,NEW.actor_id,
       NEW.beneficiary_id,NEW.finding_id,stamp,stamp) IS NOT TRUE THEN
  RAISE EXCEPTION 'R_CONFIRMATION_AUTHORITY_OR_EVIDENCE' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=stamp; RETURN NEW;
END """

# public.r_constitutional_grant_input()
# Its landed text: 0065_root_continuity.py (found there verbatim). Time calls moved: 1 statement, 1 transaction.
R_CONSTITUTIONAL_GRANT_INPUT_LANDED = r"""
DECLARE parts text[]; c public.core_rootlossconfirmation; b public.core_authoritybasis; epoch bigint;
        stamp timestamptz:=statement_timestamp();
BEGIN
 parts:=string_to_array(NEW.method_reference,'|');
 IF cardinality(parts)<>6 THEN RAISE EXCEPTION 'R_GRANT_INSTRUMENT' USING ERRCODE='23514'; END IF;
 SELECT * INTO c FROM public.core_rootlossconfirmation WHERE request_uuid::text=parts[3];
 SELECT * INTO b FROM public.core_authoritybasis WHERE basis_uuid::text=parts[2];
 SELECT access_epoch INTO epoch FROM public.core_identity WHERE id=NEW.determiner_id;
 IF c.id IS NOT NULL THEN
  PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=c.organism_id)]);
 END IF;
 IF c.id IS NULL OR b.id IS NULL OR NEW.determination_type<>'CONSTITUTIONAL_AUTHORITY_QUALIFICATION'
 OR NEW.result NOT IN ('QUALIFIED','REFUSED') OR NEW.independence_state<>'INDEPENDENT'
 OR NEW.tested_actor_id IS DISTINCT FROM c.beneficiary_id OR NEW.safeguard_beneficiary_id IS DISTINCT FROM c.beneficiary_id
 OR NEW.evidence_reference IS DISTINCT FROM b.instrument_reference OR NEW.relied_upon_authority_principal_id<>b.authority_principal_id
 OR NEW.occurred_at<>NEW.effective_at OR NEW.received_at<>NEW.effective_at
 OR NEW.effective_at NOT BETWEEN transaction_timestamp() AND stamp
 OR public.r_can_issue(c.id,NEW.determiner_id,b.id,NEW.determination_uuid,NEW.scope_fingerprint,parts[4],parts[5],parts[6],
                      NEW.effective_at,stamp,epoch) IS NOT TRUE
 OR public.r_can_issue(c.id,NEW.determiner_id,b.id,NEW.determination_uuid,NEW.scope_fingerprint,parts[4],parts[5],parts[6],
                      stamp,stamp,epoch) IS NOT TRUE THEN
  RAISE EXCEPTION 'R_GRANT_INSTRUMENT' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=stamp; RETURN NEW;
END """
R_CONSTITUTIONAL_GRANT_INPUT_CLOCK = r"""
DECLARE parts text[]; c public.core_rootlossconfirmation; b public.core_authoritybasis; epoch bigint;
        stamp timestamptz:=public.intevia_clock_statement();
BEGIN
 parts:=string_to_array(NEW.method_reference,'|');
 IF cardinality(parts)<>6 THEN RAISE EXCEPTION 'R_GRANT_INSTRUMENT' USING ERRCODE='23514'; END IF;
 SELECT * INTO c FROM public.core_rootlossconfirmation WHERE request_uuid::text=parts[3];
 SELECT * INTO b FROM public.core_authoritybasis WHERE basis_uuid::text=parts[2];
 SELECT access_epoch INTO epoch FROM public.core_identity WHERE id=NEW.determiner_id;
 IF c.id IS NOT NULL THEN
  PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=c.organism_id)]);
 END IF;
 IF c.id IS NULL OR b.id IS NULL OR NEW.determination_type<>'CONSTITUTIONAL_AUTHORITY_QUALIFICATION'
 OR NEW.result NOT IN ('QUALIFIED','REFUSED') OR NEW.independence_state<>'INDEPENDENT'
 OR NEW.tested_actor_id IS DISTINCT FROM c.beneficiary_id OR NEW.safeguard_beneficiary_id IS DISTINCT FROM c.beneficiary_id
 OR NEW.evidence_reference IS DISTINCT FROM b.instrument_reference OR NEW.relied_upon_authority_principal_id<>b.authority_principal_id
 OR NEW.occurred_at<>NEW.effective_at OR NEW.received_at<>NEW.effective_at
 OR NEW.effective_at NOT BETWEEN public.intevia_clock_transaction() AND stamp
 OR public.r_can_issue(c.id,NEW.determiner_id,b.id,NEW.determination_uuid,NEW.scope_fingerprint,parts[4],parts[5],parts[6],
                      NEW.effective_at,stamp,epoch) IS NOT TRUE
 OR public.r_can_issue(c.id,NEW.determiner_id,b.id,NEW.determination_uuid,NEW.scope_fingerprint,parts[4],parts[5],parts[6],
                      stamp,stamp,epoch) IS NOT TRUE THEN
  RAISE EXCEPTION 'R_GRANT_INSTRUMENT' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=stamp; RETURN NEW;
END """

# public.r_restoration_input()
# Its landed text: 0065_root_continuity.py (found there verbatim). Time calls moved: 1 statement, 1 wall.
R_RESTORATION_INPUT_LANDED = r"""
DECLARE stamp timestamptz:=statement_timestamp(); executed timestamptz; lo_uuid uuid;
BEGIN
 SELECT lo.organism_id INTO STRICT lo_uuid FROM public.core_rootlossconfirmation c
 JOIN public.core_livingorganism lo ON lo.id=c.organism_id WHERE c.id=NEW.loss_id;
 PERFORM public.s015_lock_organisms(ARRAY[lo_uuid]);
 executed:=clock_timestamp();
 IF NEW.founder_office_id IS NULL OR NEW.recorded_at IS NOT NULL OR NOT EXISTS (SELECT FROM public.core_identity
    WHERE id=NEW.actor_id AND access_state='active' AND access_epoch=NEW.actor_epoch)
 OR public.r_restoration_office(NEW.loss_id,NEW.finding_id,NEW.actor_id,stamp,stamp)
    IS DISTINCT FROM NEW.founder_office_id
 OR public.r_restoration_office(NEW.loss_id,NEW.finding_id,NEW.actor_id,executed,executed)
    IS DISTINCT FROM NEW.founder_office_id THEN
    RAISE EXCEPTION 'R_RESTORATION_AUTHORITY_OR_EVIDENCE' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=stamp;
 RETURN NEW;
END """
R_RESTORATION_INPUT_CLOCK = r"""
DECLARE stamp timestamptz:=public.intevia_clock_statement(); executed timestamptz; lo_uuid uuid;
BEGIN
 SELECT lo.organism_id INTO STRICT lo_uuid FROM public.core_rootlossconfirmation c
 JOIN public.core_livingorganism lo ON lo.id=c.organism_id WHERE c.id=NEW.loss_id;
 PERFORM public.s015_lock_organisms(ARRAY[lo_uuid]);
 executed:=public.intevia_clock_instant();
 IF NEW.founder_office_id IS NULL OR NEW.recorded_at IS NOT NULL OR NOT EXISTS (SELECT FROM public.core_identity
    WHERE id=NEW.actor_id AND access_state='active' AND access_epoch=NEW.actor_epoch)
 OR public.r_restoration_office(NEW.loss_id,NEW.finding_id,NEW.actor_id,stamp,stamp)
    IS DISTINCT FROM NEW.founder_office_id
 OR public.r_restoration_office(NEW.loss_id,NEW.finding_id,NEW.actor_id,executed,executed)
    IS DISTINCT FROM NEW.founder_office_id THEN
    RAISE EXCEPTION 'R_RESTORATION_AUTHORITY_OR_EVIDENCE' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=stamp;
 RETURN NEW;
END """

# public.r_value_input()
# Its landed text: 0065_root_continuity.py (found there verbatim). Time calls moved: 1 statement, 1 transaction.
R_VALUE_INPUT_LANDED = r"""
DECLARE stamp timestamptz:=statement_timestamp(); previous bigint;
BEGIN
 PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=NEW.organism_id)]);
 SELECT id INTO previous FROM public.core_rootvaluedecision WHERE organism_id=NEW.organism_id AND setting=NEW.setting
 ORDER BY effective_at DESC,id DESC LIMIT 1;
 IF NEW.founder_office_id IS NULL OR NEW.recorded_at IS NOT NULL OR NEW.effective_at NOT BETWEEN transaction_timestamp() AND stamp
 OR previous IS DISTINCT FROM NEW.predecessor_id
 OR EXISTS(SELECT FROM public.core_rootvaluedecision WHERE id=previous AND effective_at>NEW.effective_at)
 OR public.r_founder_office(NEW.organism_id,NEW.actor_id,NEW.effective_at,stamp) IS DISTINCT FROM NEW.founder_office_id
 OR public.r_founder_office(NEW.organism_id,NEW.actor_id,stamp,stamp) IS DISTINCT FROM NEW.founder_office_id
 OR public.r_value_person_allowed(NEW.organism_id,NEW.setting,NEW.person_id,stamp,stamp) IS NOT TRUE
 OR NOT EXISTS(SELECT FROM public.core_identity WHERE id=NEW.actor_id AND access_state='active' AND access_epoch=NEW.actor_epoch) THEN
  RAISE EXCEPTION 'R_VALUE_AUTHORITY_OR_PREDECESSOR' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=stamp; RETURN NEW;
END """
R_VALUE_INPUT_CLOCK = r"""
DECLARE stamp timestamptz:=public.intevia_clock_statement(); previous bigint;
BEGIN
 PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=NEW.organism_id)]);
 SELECT id INTO previous FROM public.core_rootvaluedecision WHERE organism_id=NEW.organism_id AND setting=NEW.setting
 ORDER BY effective_at DESC,id DESC LIMIT 1;
 IF NEW.founder_office_id IS NULL OR NEW.recorded_at IS NOT NULL OR NEW.effective_at NOT BETWEEN public.intevia_clock_transaction() AND stamp
 OR previous IS DISTINCT FROM NEW.predecessor_id
 OR EXISTS(SELECT FROM public.core_rootvaluedecision WHERE id=previous AND effective_at>NEW.effective_at)
 OR public.r_founder_office(NEW.organism_id,NEW.actor_id,NEW.effective_at,stamp) IS DISTINCT FROM NEW.founder_office_id
 OR public.r_founder_office(NEW.organism_id,NEW.actor_id,stamp,stamp) IS DISTINCT FROM NEW.founder_office_id
 OR public.r_value_person_allowed(NEW.organism_id,NEW.setting,NEW.person_id,stamp,stamp) IS NOT TRUE
 OR NOT EXISTS(SELECT FROM public.core_identity WHERE id=NEW.actor_id AND access_state='active' AND access_epoch=NEW.actor_epoch) THEN
  RAISE EXCEPTION 'R_VALUE_AUTHORITY_OR_PREDECESSOR' USING ERRCODE='23514';
 END IF;
 NEW.recorded_at:=stamp; RETURN NEW;
END """

# public.l_check_archive()
# Its landed text: 0066_law_records.py (found there verbatim). Time calls moved: 1 statement.
L_CHECK_ARCHIVE_LANDED = r"""
DECLARE resource_pk bigint; scope_count integer; classes text;
BEGIN
 IF TG_TABLE_NAME='core_libraryresource' THEN
   resource_pk:=NEW.id;
 ELSE
   resource_pk:=NEW.resource_id;
   -- A raw archive transition also participates in the cross-scope write fence.
   UPDATE public.core_libraryresource r SET updated_at=r.updated_at WHERE r.id=resource_pk;
 END IF;
 PERFORM 1 FROM public.core_libraryresource WHERE id=resource_pk FOR UPDATE;
 SELECT count(DISTINCT scope_key),string_agg(DISTINCT law_class,', ' ORDER BY law_class)
 INTO scope_count, classes FROM public.core_organismlawbinding
 WHERE resource_id=resource_pk AND (in_force_until IS NULL OR in_force_until>statement_timestamp());
 IF scope_count>0 THEN
   RAISE EXCEPTION 'Archive refused: % in force in % scope(s)',classes,scope_count USING ERRCODE='23514',CONSTRAINT='l_archive_in_force';
 END IF;
 RETURN NEW;
END """
L_CHECK_ARCHIVE_CLOCK = r"""
DECLARE resource_pk bigint; scope_count integer; classes text;
BEGIN
 IF TG_TABLE_NAME='core_libraryresource' THEN
   resource_pk:=NEW.id;
 ELSE
   resource_pk:=NEW.resource_id;
   -- A raw archive transition also participates in the cross-scope write fence.
   UPDATE public.core_libraryresource r SET updated_at=r.updated_at WHERE r.id=resource_pk;
 END IF;
 PERFORM 1 FROM public.core_libraryresource WHERE id=resource_pk FOR UPDATE;
 SELECT count(DISTINCT scope_key),string_agg(DISTINCT law_class,', ' ORDER BY law_class)
 INTO scope_count, classes FROM public.core_organismlawbinding
 WHERE resource_id=resource_pk AND (in_force_until IS NULL OR in_force_until>public.intevia_clock_statement());
 IF scope_count>0 THEN
   RAISE EXCEPTION 'Archive refused: % in force in % scope(s)',classes,scope_count USING ERRCODE='23514',CONSTRAINT='l_archive_in_force';
 END IF;
 RETURN NEW;
END """

# public.l_act_complete()
# Its landed text: 0067_law_acts.py (found there verbatim). Time calls moved: 1 wall.
L_ACT_COMPLETE_LANDED = r"""
DECLARE a public.core_lawact; d public.core_authoritydecision; spec jsonb; checked jsonb; cutoff timestamptz;
BEGIN
 SELECT * INTO STRICT a FROM public.core_lawact WHERE event_id=NEW.id;
 SELECT * INTO STRICT d FROM public.core_authoritydecision WHERE id=a.decision_id;
 spec:=d.target->'spec';
 IF NOT public.l_qualified_operation(NEW.id) OR NOT EXISTS(SELECT FROM public.core_organismcommandreceipt r
      WHERE r.action='ADOPT_LAW' AND r.authority_decision_reference=d.decision_reference)
   OR (a.kind='ADOPT') <> EXISTS(SELECT FROM public.core_organismlawbinding b WHERE b.adoption_event_id=NEW.id)
   OR (a.kind='RETIRE' OR (a.kind='ADOPT' AND (a.plan->>'open_prior')::boolean)) <>
       EXISTS(SELECT FROM public.core_organismlawending x WHERE x.end_event_id=NEW.id) THEN
   RAISE EXCEPTION 'LAW event requires its receipt, exact qualified operation and complete effects' USING ERRCODE='23514';
 END IF;
 checked:=public.l_assess_request(spec,d.actor_id,NEW.received_at,NEW.effective_at,NEW.id);
 cutoff:=clock_timestamp();
 IF checked IS DISTINCT FROM a.plan OR public.l_pending(NEW.living_organism_id,spec->>'scope'='PLATFORM',
   CASE WHEN (a.plan->>'open_prior')::boolean THEN
    (SELECT b.version_id FROM public.core_organismlawbinding b WHERE b.binding_uuid=(spec->>'predecessor')::uuid) END,
   CASE WHEN spec->>'intent'='ADOPT' THEN (a.plan->'effect'->>'version_id')::bigint END,
   (SELECT b.law_class FROM public.core_organismlawbinding b WHERE b.binding_uuid=(spec->>'predecessor')::uuid),
   spec->>'law_class',
   (SELECT b.consequence_class FROM public.core_organismlawbinding b WHERE b.binding_uuid=(spec->>'predecessor')::uuid),
   spec->>'consequence_class',cutoff,cutoff,(spec->>'predecessor')::uuid) IS DISTINCT FROM a.plan->'affected' THEN
   RAISE EXCEPTION 'LAW authority or affected nominations differ at the final check' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END """
L_ACT_COMPLETE_CLOCK = r"""
DECLARE a public.core_lawact; d public.core_authoritydecision; spec jsonb; checked jsonb; cutoff timestamptz;
BEGIN
 SELECT * INTO STRICT a FROM public.core_lawact WHERE event_id=NEW.id;
 SELECT * INTO STRICT d FROM public.core_authoritydecision WHERE id=a.decision_id;
 spec:=d.target->'spec';
 IF NOT public.l_qualified_operation(NEW.id) OR NOT EXISTS(SELECT FROM public.core_organismcommandreceipt r
      WHERE r.action='ADOPT_LAW' AND r.authority_decision_reference=d.decision_reference)
   OR (a.kind='ADOPT') <> EXISTS(SELECT FROM public.core_organismlawbinding b WHERE b.adoption_event_id=NEW.id)
   OR (a.kind='RETIRE' OR (a.kind='ADOPT' AND (a.plan->>'open_prior')::boolean)) <>
       EXISTS(SELECT FROM public.core_organismlawending x WHERE x.end_event_id=NEW.id) THEN
   RAISE EXCEPTION 'LAW event requires its receipt, exact qualified operation and complete effects' USING ERRCODE='23514';
 END IF;
 checked:=public.l_assess_request(spec,d.actor_id,NEW.received_at,NEW.effective_at,NEW.id);
 cutoff:=public.intevia_clock_instant();
 IF checked IS DISTINCT FROM a.plan OR public.l_pending(NEW.living_organism_id,spec->>'scope'='PLATFORM',
   CASE WHEN (a.plan->>'open_prior')::boolean THEN
    (SELECT b.version_id FROM public.core_organismlawbinding b WHERE b.binding_uuid=(spec->>'predecessor')::uuid) END,
   CASE WHEN spec->>'intent'='ADOPT' THEN (a.plan->'effect'->>'version_id')::bigint END,
   (SELECT b.law_class FROM public.core_organismlawbinding b WHERE b.binding_uuid=(spec->>'predecessor')::uuid),
   spec->>'law_class',
   (SELECT b.consequence_class FROM public.core_organismlawbinding b WHERE b.binding_uuid=(spec->>'predecessor')::uuid),
   spec->>'consequence_class',cutoff,cutoff,(spec->>'predecessor')::uuid) IS DISTINCT FROM a.plan->'affected' THEN
   RAISE EXCEPTION 'LAW authority or affected nominations differ at the final check' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END """

# public.l_decision_time()
# Its landed text: 0067_law_acts.py (found there verbatim). Time calls moved: 1 statement.
L_DECISION_TIME_LANDED = r"""
DECLARE i public.core_commandintake; assessed jsonb;
BEGIN
 IF NEW.l_execution_xid IS NOT NULL THEN RAISE EXCEPTION 'LAW transaction identity is database assigned' USING ERRCODE='23514'; END IF;
 SELECT * INTO STRICT i FROM public.core_commandintake WHERE id=(NEW.target->>'intake')::bigint;
 IF public.l_intake_matches(NEW,i) IS NOT TRUE OR NEW.effective_at<i.received_at OR NEW.effective_at>statement_timestamp()
   OR NEW.provider_identity<>'INTEVIA.S015.ApplicationAuthorityProvider'
   OR NEW.decision_payload->'target' IS DISTINCT FROM NEW.target
   OR NEW.decision_payload->>'target_fingerprint' IS DISTINCT FROM NEW.target_fingerprint
   OR NEW.decision_payload->>'action' IS DISTINCT FROM NEW.action
   OR NEW.decision_payload->>'command_subtype' IS DISTINCT FROM NEW.command_subtype
   OR (NEW.decision_payload->>'actor_pk')::bigint IS DISTINCT FROM i.actor_id
   OR (NEW.decision_payload->>'actor_identity_id')::uuid IS DISTINCT FROM i.actor_identity_id
   OR (NEW.decision_payload->>'actor_access_epoch')::bigint IS DISTINCT FROM i.actor_access_epoch
   OR NEW.decision_payload->>'request_reference' IS DISTINCT FROM i.request_reference
   OR NEW.decision_payload->>'idempotency_key' IS DISTINCT FROM i.idempotency_key
   OR (NEW.decision_payload->>'effective_at')::timestamptz IS DISTINCT FROM NEW.effective_at
   OR (NEW.decision_payload->>'evaluated_at')::timestamptz IS DISTINCT FROM NEW.evaluated_at
   OR NEW.decision_payload->>'authority_reference' IS DISTINCT FROM NEW.authority_reference
   OR NEW.decision_payload->>'refusal_code' IS DISTINCT FROM NEW.refusal_code THEN
   RAISE EXCEPTION 'LAW decision differs from the signed request and its intake' USING ERRCODE='23514';
 END IF;
 assessed:=public.l_assess_request(NEW.target->'spec',i.actor_id,i.received_at,NEW.effective_at);
 IF assessed IS DISTINCT FROM NEW.target->'plan' OR (NEW.outcome='REFUSED')<>(assessed ? 'reason') OR
    (NEW.outcome='QUALIFIED' AND NEW.authority_reference IS DISTINCT FROM
      's015a1:'||public.l_canonical(jsonb_build_object('kind','LAW_VERSION','route',assessed->>'route'))) THEN
    RAISE EXCEPTION 'LAW decision differs from the independently checked facts' USING ERRCODE='23514';
 END IF;
 NEW.l_execution_xid:=pg_current_xact_id();
 RETURN NEW;
END """
L_DECISION_TIME_CLOCK = r"""
DECLARE i public.core_commandintake; assessed jsonb;
BEGIN
 IF NEW.l_execution_xid IS NOT NULL THEN RAISE EXCEPTION 'LAW transaction identity is database assigned' USING ERRCODE='23514'; END IF;
 SELECT * INTO STRICT i FROM public.core_commandintake WHERE id=(NEW.target->>'intake')::bigint;
 IF public.l_intake_matches(NEW,i) IS NOT TRUE OR NEW.effective_at<i.received_at OR NEW.effective_at>public.intevia_clock_statement()
   OR NEW.provider_identity<>'INTEVIA.S015.ApplicationAuthorityProvider'
   OR NEW.decision_payload->'target' IS DISTINCT FROM NEW.target
   OR NEW.decision_payload->>'target_fingerprint' IS DISTINCT FROM NEW.target_fingerprint
   OR NEW.decision_payload->>'action' IS DISTINCT FROM NEW.action
   OR NEW.decision_payload->>'command_subtype' IS DISTINCT FROM NEW.command_subtype
   OR (NEW.decision_payload->>'actor_pk')::bigint IS DISTINCT FROM i.actor_id
   OR (NEW.decision_payload->>'actor_identity_id')::uuid IS DISTINCT FROM i.actor_identity_id
   OR (NEW.decision_payload->>'actor_access_epoch')::bigint IS DISTINCT FROM i.actor_access_epoch
   OR NEW.decision_payload->>'request_reference' IS DISTINCT FROM i.request_reference
   OR NEW.decision_payload->>'idempotency_key' IS DISTINCT FROM i.idempotency_key
   OR (NEW.decision_payload->>'effective_at')::timestamptz IS DISTINCT FROM NEW.effective_at
   OR (NEW.decision_payload->>'evaluated_at')::timestamptz IS DISTINCT FROM NEW.evaluated_at
   OR NEW.decision_payload->>'authority_reference' IS DISTINCT FROM NEW.authority_reference
   OR NEW.decision_payload->>'refusal_code' IS DISTINCT FROM NEW.refusal_code THEN
   RAISE EXCEPTION 'LAW decision differs from the signed request and its intake' USING ERRCODE='23514';
 END IF;
 assessed:=public.l_assess_request(NEW.target->'spec',i.actor_id,i.received_at,NEW.effective_at);
 IF assessed IS DISTINCT FROM NEW.target->'plan' OR (NEW.outcome='REFUSED')<>(assessed ? 'reason') OR
    (NEW.outcome='QUALIFIED' AND NEW.authority_reference IS DISTINCT FROM
      's015a1:'||public.l_canonical(jsonb_build_object('kind','LAW_VERSION','route',assessed->>'route'))) THEN
    RAISE EXCEPTION 'LAW decision differs from the independently checked facts' USING ERRCODE='23514';
 END IF;
 NEW.l_execution_xid:=pg_current_xact_id();
 RETURN NEW;
END """

# public.l_execute(text)
# Its landed text: 0067_law_acts.py (found there verbatim). Time calls moved: 1 statement.
L_EXECUTE_LANDED = r"""
DECLARE d public.core_authoritydecision; i public.core_commandintake; lo public.core_livingorganism;
 head public.core_livingorganismevent; plan jsonb; spec jsonb; kind_ text; event_pk bigint;
 cut timestamptz:=statement_timestamp(); state_ text;
BEGIN
 SELECT * INTO STRICT d FROM public.core_authoritydecision WHERE decision_reference=decision_ref;
 SELECT * INTO STRICT i FROM public.core_commandintake WHERE id=(d.target->>'intake')::bigint;
 IF public.l_intake_matches(d,i) IS NOT TRUE OR d.outcome<>'QUALIFIED'
   OR d.l_execution_xid IS DISTINCT FROM pg_current_xact_id() THEN
   RAISE EXCEPTION 'LAW requires the qualified decision for this exact intake' USING ERRCODE='23514';
 END IF;
 spec:=d.target->'spec';
 SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE organism_id=(spec->>'organism')::uuid FOR UPDATE;
 PERFORM 1 FROM public.core_libraryresource WHERE resource_id=spec->>'resource' FOR UPDATE;
 plan:=public.l_assess_request(spec,i.actor_id,i.received_at,cut);
 IF plan ? 'reason' OR plan IS DISTINCT FROM d.target->'plan' THEN
   RAISE EXCEPTION 'LAW authority or affected nominations changed before execution; refresh the proposal' USING ERRCODE='40001';
 END IF;
 kind_:=plan->>'kind';
 SELECT * INTO STRICT head FROM public.core_livingorganismevent WHERE id=lo.head_event_id;
 state_:=public.s015_fold_effective_state('core_livingorganism',lo.id,cut,cut);
 event_pk:=public.s015_append_event('core_livingorganism',lo.id,'ADOPT_LAW',state_,cut,i.received_at,
   i.received_at,i.actor_id,lo.founding_authority_basis_id,d.decision_reference,public.l_evidence(plan),
   i.request_reference,i.request_fingerprint,'{}'::jsonb,NULL,gen_random_uuid(),head.id,
   head.sequence::integer,NULL::bigint,NULL::text,i.idempotency_key);
 INSERT INTO public.core_lawact VALUES(event_pk,i.id,d.id,(spec->>'proposal')::uuid,kind_,plan);
 IF kind_='RETIRE' OR (kind_='ADOPT' AND (plan->>'open_prior')::boolean) THEN
   INSERT INTO public.core_organismlawending(binding_id,end_event_id,decision_route,end_kind,successor_id)
   VALUES((spec->>'predecessor')::uuid,event_pk,plan->>'route',
     CASE WHEN kind_='ADOPT' THEN 'SUPERSEDED' ELSE 'RETIRED' END,
     CASE WHEN kind_='ADOPT' THEN (spec->>'proposal')::uuid END);
 END IF;
 IF kind_='ADOPT' THEN
   INSERT INTO public.core_organismlawbinding SELECT b.* FROM jsonb_populate_record(NULL::public.core_organismlawbinding,
      (plan->'effect')||jsonb_build_object('adoption_event_id',event_pk,'in_force_from',cut)) AS b;
 END IF;
 RETURN event_pk;
END """
L_EXECUTE_CLOCK = r"""
DECLARE d public.core_authoritydecision; i public.core_commandintake; lo public.core_livingorganism;
 head public.core_livingorganismevent; plan jsonb; spec jsonb; kind_ text; event_pk bigint;
 cut timestamptz:=public.intevia_clock_statement(); state_ text;
BEGIN
 SELECT * INTO STRICT d FROM public.core_authoritydecision WHERE decision_reference=decision_ref;
 SELECT * INTO STRICT i FROM public.core_commandintake WHERE id=(d.target->>'intake')::bigint;
 IF public.l_intake_matches(d,i) IS NOT TRUE OR d.outcome<>'QUALIFIED'
   OR d.l_execution_xid IS DISTINCT FROM pg_current_xact_id() THEN
   RAISE EXCEPTION 'LAW requires the qualified decision for this exact intake' USING ERRCODE='23514';
 END IF;
 spec:=d.target->'spec';
 SELECT * INTO STRICT lo FROM public.core_livingorganism WHERE organism_id=(spec->>'organism')::uuid FOR UPDATE;
 PERFORM 1 FROM public.core_libraryresource WHERE resource_id=spec->>'resource' FOR UPDATE;
 plan:=public.l_assess_request(spec,i.actor_id,i.received_at,cut);
 IF plan ? 'reason' OR plan IS DISTINCT FROM d.target->'plan' THEN
   RAISE EXCEPTION 'LAW authority or affected nominations changed before execution; refresh the proposal' USING ERRCODE='40001';
 END IF;
 kind_:=plan->>'kind';
 SELECT * INTO STRICT head FROM public.core_livingorganismevent WHERE id=lo.head_event_id;
 state_:=public.s015_fold_effective_state('core_livingorganism',lo.id,cut,cut);
 event_pk:=public.s015_append_event('core_livingorganism',lo.id,'ADOPT_LAW',state_,cut,i.received_at,
   i.received_at,i.actor_id,lo.founding_authority_basis_id,d.decision_reference,public.l_evidence(plan),
   i.request_reference,i.request_fingerprint,'{}'::jsonb,NULL,gen_random_uuid(),head.id,
   head.sequence::integer,NULL::bigint,NULL::text,i.idempotency_key);
 INSERT INTO public.core_lawact VALUES(event_pk,i.id,d.id,(spec->>'proposal')::uuid,kind_,plan);
 IF kind_='RETIRE' OR (kind_='ADOPT' AND (plan->>'open_prior')::boolean) THEN
   INSERT INTO public.core_organismlawending(binding_id,end_event_id,decision_route,end_kind,successor_id)
   VALUES((spec->>'predecessor')::uuid,event_pk,plan->>'route',
     CASE WHEN kind_='ADOPT' THEN 'SUPERSEDED' ELSE 'RETIRED' END,
     CASE WHEN kind_='ADOPT' THEN (spec->>'proposal')::uuid END);
 END IF;
 IF kind_='ADOPT' THEN
   INSERT INTO public.core_organismlawbinding SELECT b.* FROM jsonb_populate_record(NULL::public.core_organismlawbinding,
      (plan->'effect')||jsonb_build_object('adoption_event_id',event_pk,'in_force_from',cut)) AS b;
 END IF;
 RETURN event_pk;
END """

# public.l_locator_input()
# Its landed text: 0067_law_acts.py (found there verbatim). Time calls moved: 1 statement.
L_LOCATOR_INPUT_LANDED = r"""
BEGIN
 IF NOT EXISTS(SELECT FROM public.core_organismroledefinitionprovision p WHERE p.id=NEW.provision_id
               AND p.recorded_at = statement_timestamp()
               AND EXISTS (SELECT FROM public.core_libraryresourceversion v JOIN public.core_libraryresource r ON r.id=v.resource_id
                  WHERE v.id=NEW.version_id AND p.provision_reference='law-ref:'||r.resource_id||coalesce('#'||NEW.locator,'')
                  AND p.provision_version=v.version_number::text
                  AND EXISTS(SELECT FROM public.core_organismroledefinition d JOIN public.core_organismlawbinding b
                     ON b.version_id=v.id AND (b.scope_kind='PLATFORM' OR
                        (b.scope_kind='LIVING_ORGANISM' AND b.organism_id=d.living_organism_id))
                     WHERE d.id=p.definition_id AND public.l_visible_window(b,d.effective_at,d.recorded_at))
                  AND (CASE WHEN public.l_charter_pin(r.resource_id) IS NOT NULL THEN NEW.locator IS NULL
                            ELSE EXISTS(SELECT FROM public.l_clauses(v.content) c WHERE c.locator=NEW.locator) END))) THEN
   RAISE EXCEPTION 'A LAW clause link must be recorded with its original provision' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END """
L_LOCATOR_INPUT_CLOCK = r"""
BEGIN
 IF NOT EXISTS(SELECT FROM public.core_organismroledefinitionprovision p WHERE p.id=NEW.provision_id
               AND p.recorded_at = public.intevia_clock_statement()
               AND EXISTS (SELECT FROM public.core_libraryresourceversion v JOIN public.core_libraryresource r ON r.id=v.resource_id
                  WHERE v.id=NEW.version_id AND p.provision_reference='law-ref:'||r.resource_id||coalesce('#'||NEW.locator,'')
                  AND p.provision_version=v.version_number::text
                  AND EXISTS(SELECT FROM public.core_organismroledefinition d JOIN public.core_organismlawbinding b
                     ON b.version_id=v.id AND (b.scope_kind='PLATFORM' OR
                        (b.scope_kind='LIVING_ORGANISM' AND b.organism_id=d.living_organism_id))
                     WHERE d.id=p.definition_id AND public.l_visible_window(b,d.effective_at,d.recorded_at))
                  AND (CASE WHEN public.l_charter_pin(r.resource_id) IS NOT NULL THEN NEW.locator IS NULL
                            ELSE EXISTS(SELECT FROM public.l_clauses(v.content) c WHERE c.locator=NEW.locator) END))) THEN
   RAISE EXCEPTION 'A LAW clause link must be recorded with its original provision' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END """

# public.l_lock_dependencies(jsonb, bigint, bigint)
# Its landed text: 0067_law_acts.py (found there verbatim). Time calls moved: 1 statement.
L_LOCK_DEPENDENCIES_LANDED = r"""
DECLARE prior public.core_organismlawbinding; nominations uuid[]; cutoff timestamptz:=statement_timestamp();
BEGIN
 IF spec->>'predecessor' IS NULL THEN RETURN; END IF;
 SELECT b.* INTO prior FROM public.core_organismlawbinding b JOIN public.core_libraryresource r ON r.id=b.resource_id
   WHERE b.binding_uuid=(spec->>'predecessor')::uuid AND r.resource_id=spec->>'resource' AND b.organism_id=lo
   FOR UPDATE OF b;
 IF prior.binding_uuid IS NULL THEN RETURN; END IF;
 IF spec->>'scope'='PLATFORM' THEN
   IF public.r_founder_office(lo,person,cutoff,cutoff) IS NULL THEN RETURN; END IF;
   LOCK TABLE public.core_contextualroleassignmentstateevent, public.core_organismroledefinitionprovision,
     public.core_lawprovisionlocator IN SHARE MODE;
 END IF;
 SELECT ARRAY(SELECT DISTINCT a.assignment_uuid FROM public.core_contextualroleassignment a
   JOIN public.core_organismroledefinition d ON a.role_definition_id=d.id
   JOIN public.core_organismroledefinitionprovision p ON p.definition_id=d.id
   LEFT JOIN public.core_lawprovisionlocator k ON k.provision_id=p.id
   LEFT JOIN public.core_libraryresourceversion v ON v.id=k.version_id
   WHERE (d.living_organism_id=lo OR (spec->>'scope'='PLATFORM' AND k.version_id IS NOT NULL))
      AND (k.version_id IS NULL OR v.resource_id=prior.resource_id)
      AND public.s015_fold_effective_state('core_contextualroleassignment',a.id,cutoff,cutoff)='PROPOSED')
 INTO nominations;
 PERFORM a.id FROM public.core_contextualroleassignment a WHERE a.assignment_uuid=ANY(nominations)
 ORDER BY a.assignment_uuid FOR UPDATE;
 PERFORM d.id FROM public.core_organismroledefinition d WHERE EXISTS(SELECT FROM public.core_contextualroleassignment a
   WHERE a.role_definition_id=d.id AND a.assignment_uuid=ANY(nominations)) ORDER BY d.role_uuid FOR SHARE;
END """
L_LOCK_DEPENDENCIES_CLOCK = r"""
DECLARE prior public.core_organismlawbinding; nominations uuid[]; cutoff timestamptz:=public.intevia_clock_statement();
BEGIN
 IF spec->>'predecessor' IS NULL THEN RETURN; END IF;
 SELECT b.* INTO prior FROM public.core_organismlawbinding b JOIN public.core_libraryresource r ON r.id=b.resource_id
   WHERE b.binding_uuid=(spec->>'predecessor')::uuid AND r.resource_id=spec->>'resource' AND b.organism_id=lo
   FOR UPDATE OF b;
 IF prior.binding_uuid IS NULL THEN RETURN; END IF;
 IF spec->>'scope'='PLATFORM' THEN
   IF public.r_founder_office(lo,person,cutoff,cutoff) IS NULL THEN RETURN; END IF;
   LOCK TABLE public.core_contextualroleassignmentstateevent, public.core_organismroledefinitionprovision,
     public.core_lawprovisionlocator IN SHARE MODE;
 END IF;
 SELECT ARRAY(SELECT DISTINCT a.assignment_uuid FROM public.core_contextualroleassignment a
   JOIN public.core_organismroledefinition d ON a.role_definition_id=d.id
   JOIN public.core_organismroledefinitionprovision p ON p.definition_id=d.id
   LEFT JOIN public.core_lawprovisionlocator k ON k.provision_id=p.id
   LEFT JOIN public.core_libraryresourceversion v ON v.id=k.version_id
   WHERE (d.living_organism_id=lo OR (spec->>'scope'='PLATFORM' AND k.version_id IS NOT NULL))
      AND (k.version_id IS NULL OR v.resource_id=prior.resource_id)
      AND public.s015_fold_effective_state('core_contextualroleassignment',a.id,cutoff,cutoff)='PROPOSED')
 INTO nominations;
 PERFORM a.id FROM public.core_contextualroleassignment a WHERE a.assignment_uuid=ANY(nominations)
 ORDER BY a.assignment_uuid FOR UPDATE;
 PERFORM d.id FROM public.core_organismroledefinition d WHERE EXISTS(SELECT FROM public.core_contextualroleassignment a
   WHERE a.role_definition_id=d.id AND a.assignment_uuid=ANY(nominations)) ORDER BY d.role_uuid FOR SHARE;
END """

# public.lib_author_input()
# Its landed text: 0069_library_policy_publication.py (found there verbatim). Time calls moved: 1 statement.
LIB_AUTHOR_INPUT_LANDED = r"""
DECLARE meta public.core_libpolicyversion; v public.core_libraryresourceversion; prior public.core_libauthorclaim;
 stamp timestamptz:=statement_timestamp();
BEGIN
 NEW.recorded_at:=stamp; NEW.effective_at:=stamp;
 SELECT * INTO STRICT meta FROM public.core_libpolicyversion WHERE version_id=NEW.version_id;
 SELECT * INTO STRICT v FROM public.core_libraryresourceversion WHERE id=NEW.version_id;
 PERFORM 1 FROM public.core_libraryresource WHERE id=v.resource_id FOR UPDATE;
 IF NEW.received_at>stamp OR NOT NEW.author_id=ANY(meta.author_ids)
    OR EXISTS(SELECT FROM public.core_libpublicationfact WHERE version_id=v.id)
    OR EXISTS(SELECT FROM public.core_libraryresourceversion WHERE predecessor_id=v.id)
    OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
      WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch AND i.access_state='active' AND u.is_active)
    OR NEW.declaration IS DISTINCT FROM (CASE WHEN NEW.inherited_from IS NULL
      THEN 'I declare my authorship of this exact version and confirm its recorded author list is complete.'
      ELSE 'Attribution carried from the cited predecessor declaration; no new declaration by that author.' END) THEN
  RAISE EXCEPTION 'LIB: authorship needs an explicit declaration before publication or a successor.' USING ERRCODE='23514'; END IF;
 IF NEW.inherited_from IS NULL THEN
  IF NEW.actor_id<>NEW.author_id THEN RAISE EXCEPTION 'LIB: an author must declare in their own identity.' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO STRICT prior FROM public.core_libauthorclaim WHERE claim_uuid=NEW.inherited_from;
  IF prior.version_id IS DISTINCT FROM v.predecessor_id OR prior.author_id<>NEW.author_id OR NEW.actor_id<>meta.actor_id THEN
    RAISE EXCEPTION 'LIB: carried attribution must cite the predecessor author''s declaration.' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END """
LIB_AUTHOR_INPUT_CLOCK = r"""
DECLARE meta public.core_libpolicyversion; v public.core_libraryresourceversion; prior public.core_libauthorclaim;
 stamp timestamptz:=public.intevia_clock_statement();
BEGIN
 NEW.recorded_at:=stamp; NEW.effective_at:=stamp;
 SELECT * INTO STRICT meta FROM public.core_libpolicyversion WHERE version_id=NEW.version_id;
 SELECT * INTO STRICT v FROM public.core_libraryresourceversion WHERE id=NEW.version_id;
 PERFORM 1 FROM public.core_libraryresource WHERE id=v.resource_id FOR UPDATE;
 IF NEW.received_at>stamp OR NOT NEW.author_id=ANY(meta.author_ids)
    OR EXISTS(SELECT FROM public.core_libpublicationfact WHERE version_id=v.id)
    OR EXISTS(SELECT FROM public.core_libraryresourceversion WHERE predecessor_id=v.id)
    OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
      WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch AND i.access_state='active' AND u.is_active)
    OR NEW.declaration IS DISTINCT FROM (CASE WHEN NEW.inherited_from IS NULL
      THEN 'I declare my authorship of this exact version and confirm its recorded author list is complete.'
      ELSE 'Attribution carried from the cited predecessor declaration; no new declaration by that author.' END) THEN
  RAISE EXCEPTION 'LIB: authorship needs an explicit declaration before publication or a successor.' USING ERRCODE='23514'; END IF;
 IF NEW.inherited_from IS NULL THEN
  IF NEW.actor_id<>NEW.author_id THEN RAISE EXCEPTION 'LIB: an author must declare in their own identity.' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO STRICT prior FROM public.core_libauthorclaim WHERE claim_uuid=NEW.inherited_from;
  IF prior.version_id IS DISTINCT FROM v.predecessor_id OR prior.author_id<>NEW.author_id OR NEW.actor_id<>meta.actor_id THEN
    RAISE EXCEPTION 'LIB: carried attribution must cite the predecessor author''s declaration.' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END """

# public.disc_complete()
# Its landed text: 0072_discussion_boards.py (found there verbatim). Time calls moved: 1 statement.
DISC_COMPLETE_LANDED = r"""
DECLARE v_key uuid; v_act public.core_discact; v_expected integer; v_actual integer;
BEGIN
 IF TG_TABLE_NAME='core_discboard' OR TG_TABLE_NAME='core_disccategory' THEN v_key:=NEW.creation_act;
 ELSIF TG_TABLE_NAME='core_livingorganismevent' THEN v_key:=NEW.event_uuid;
 ELSE v_key:=NEW.act_uuid; END IF;
 SELECT * INTO v_act FROM public.core_discact WHERE act_uuid=v_key;
 v_expected:=CASE v_act.operation WHEN 'PROVISION' THEN 3 WHEN 'PROBATION_ACCESS' THEN 0 ELSE 1 END;
 SELECT count(*) INTO v_actual FROM public.core_disccategoryversion v WHERE v.act_uuid=v_key;
 IF v_act.act_uuid IS NULL OR v_actual<>v_expected OR NOT EXISTS(SELECT FROM public.core_livingorganismevent e
   WHERE e.event_uuid=v_key AND public.disc_event_matches(e))
   OR public.disc_source(v_act.board_uuid,v_act.actor_id,v_act.operation,statement_timestamp()) IS DISTINCT FROM v_act.authority_source
   OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
      WHERE i.id=v_act.actor_id AND i.access_epoch=v_act.actor_epoch AND i.access_state='active' AND u.is_active) THEN
   RAISE EXCEPTION 'DISC_COMPLETE' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END """
DISC_COMPLETE_CLOCK = r"""
DECLARE v_key uuid; v_act public.core_discact; v_expected integer; v_actual integer;
BEGIN
 IF TG_TABLE_NAME='core_discboard' OR TG_TABLE_NAME='core_disccategory' THEN v_key:=NEW.creation_act;
 ELSIF TG_TABLE_NAME='core_livingorganismevent' THEN v_key:=NEW.event_uuid;
 ELSE v_key:=NEW.act_uuid; END IF;
 SELECT * INTO v_act FROM public.core_discact WHERE act_uuid=v_key;
 v_expected:=CASE v_act.operation WHEN 'PROVISION' THEN 3 WHEN 'PROBATION_ACCESS' THEN 0 ELSE 1 END;
 SELECT count(*) INTO v_actual FROM public.core_disccategoryversion v WHERE v.act_uuid=v_key;
 IF v_act.act_uuid IS NULL OR v_actual<>v_expected OR NOT EXISTS(SELECT FROM public.core_livingorganismevent e
   WHERE e.event_uuid=v_key AND public.disc_event_matches(e))
   OR public.disc_source(v_act.board_uuid,v_act.actor_id,v_act.operation,public.intevia_clock_statement()) IS DISTINCT FROM v_act.authority_source
   OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
      WHERE i.id=v_act.actor_id AND i.access_epoch=v_act.actor_epoch AND i.access_state='active' AND u.is_active) THEN
   RAISE EXCEPTION 'DISC_COMPLETE' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END """

# public.disc_execute(jsonb, bigint, bigint, timestamp with time zone)
# Its landed text: 0072_discussion_boards.py (found there verbatim). Time calls moved: 1 statement.
DISC_EXECUTE_LANDED = r"""
DECLARE v_board public.core_discboard; v_lo public.core_livingorganism; v_circle bigint;
 v_old public.core_discact; v_source jsonb; v_stamp timestamptz:=statement_timestamp();
 v_key uuid:=(p_payload->>'request')::uuid; v_board_key uuid:=(p_payload->>'board')::uuid;
 v_op text:=p_payload->>'operation'; v_cat public.disc_categories; v_id uuid; v_fp text;
 v_head public.core_livingorganismevent; v_state text; v_event bigint; v_code text; v_other_board uuid;
 v_conflict_name text; v_conflict_table text; v_conflict_schema text;
BEGIN
 -- Serialize the global request identity before the owning-organism lock.
 PERFORM pg_advisory_xact_lock(hashtextextended('DISC1:request:'||v_key::text,0));
 SELECT * INTO v_lo FROM public.core_livingorganism WHERE organism_id=(p_payload->>'organism')::uuid;
 IF v_lo.id IS NULL THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 PERFORM public.s015_lock_organisms(ARRAY[v_lo.organism_id]);
 IF p_payload->>'circle' IS NOT NULL THEN
   SELECT id INTO v_circle FROM public.core_circle WHERE circle_uuid=(p_payload->>'circle')::uuid AND parent_organism_id=v_lo.id;
   IF v_circle IS NULL THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 END IF;
 IF NOT public.disc_audience(v_lo.id,v_circle,p_actor,'MEMBERS',v_stamp)
   AND NOT public.disc_audience(v_lo.id,v_circle,p_actor,'PROBATIONARY',v_stamp) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 SELECT * INTO v_board FROM public.core_discboard WHERE organism_id=v_lo.id AND circle_id IS NOT DISTINCT FROM v_circle;
 IF (v_board.board_uuid IS NULL AND v_op<>'PROVISION')
   OR (v_board.board_uuid IS NOT NULL AND v_board.board_uuid<>v_board_key) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 IF v_board.board_uuid IS NULL THEN
   -- Resolve D1 without inserting a board before the global reuse checks.
   IF public.disc_member_state(v_lo.id,p_actor,v_stamp)='ACTIVE' THEN
     v_source:=jsonb_build_object('kind','RULE','reference','HG:DISC1:D1:20261106','route','PROVISION');
   END IF;
 ELSE
   v_source:=public.disc_source(v_board_key,p_actor,v_op,v_stamp);
 END IF;
 IF v_source IS NULL THEN RAISE EXCEPTION 'DISC_AUTHORITY' USING ERRCODE='23514'; END IF;
 -- 0072 also works alone; 0073's act table exists only after that migration.
 IF to_regclass('public.core_discpostact') IS NOT NULL THEN
   EXECUTE 'SELECT board_uuid FROM public.core_discpostact WHERE act_uuid=$1' INTO v_other_board USING v_key;
   IF v_other_board IS NOT NULL THEN
     IF v_other_board<>v_board_key THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
     RAISE EXCEPTION 'DISC_REQUEST_CONFLICT' USING ERRCODE='23514';
   END IF;
 END IF;
 SELECT * INTO v_old FROM public.core_discact WHERE act_uuid=v_key;
 IF v_old.act_uuid IS NOT NULL THEN
   IF v_old.board_uuid<>v_board_key THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   IF v_old.payload IS DISTINCT FROM p_payload OR v_old.actor_id<>p_actor OR v_old.actor_epoch<>p_epoch THEN
     RAISE EXCEPTION 'DISC_REQUEST_CONFLICT' USING ERRCODE='23514'; END IF;
   RETURN jsonb_build_object('board',v_board_key,'act',v_key,'operation',v_op);
 END IF;
 -- A request belonging to any other shared-chain domain is unavailable.
 IF EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.event_uuid=v_key) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 IF v_op='PROVISION' AND v_board.board_uuid IS NOT NULL THEN
   RETURN jsonb_build_object('board',v_board.board_uuid,'act',v_board.creation_act,'operation',v_op);
 END IF;
 IF v_op='PROVISION' THEN
   INSERT INTO public.core_discboard VALUES(v_board_key,v_lo.id,v_circle,v_key);
 END IF;
 v_fp:=public.disc_fingerprint(p_payload);
 INSERT INTO public.core_discact(act_uuid,board_uuid,operation,actor_id,actor_epoch,authority_source,payload,fingerprint,received_at)
 VALUES(v_key,v_board_key,v_op,p_actor,p_epoch,v_source,p_payload,v_fp,p_received);
 IF v_op IN ('PROVISION','WELCOME') THEN
   FOREACH v_code IN ARRAY (CASE WHEN v_op='PROVISION' THEN ARRAY['COORDINATION','DISCUSSION','ANNOUNCEMENTS'] ELSE ARRAY['WELCOME'] END) LOOP
     v_id:=gen_random_uuid();
     INSERT INTO public.core_disccategory VALUES(v_id,v_board_key,v_code,v_key);
     INSERT INTO public.core_disccategoryversion VALUES(gen_random_uuid(),v_id,v_key,1,NULL,initcap(v_code),'',
       CASE v_code WHEN 'COORDINATION' THEN 1 WHEN 'DISCUSSION' THEN 2 WHEN 'ANNOUNCEMENTS' THEN 3 ELSE 4 END,
       CASE v_code WHEN 'COORDINATION' THEN 'COORDINATORS' WHEN 'WELCOME' THEN 'PROBATIONARY' ELSE 'MEMBERS' END,false);
   END LOOP;
 ELSIF v_op IN ('CREATE_CATEGORY','REVISE_CATEGORY','RETIRE_CATEGORY') THEN
   v_id:=(p_payload->>'category')::uuid;
   SELECT * INTO v_cat FROM public.disc_categories WHERE category_uuid=v_id;
   IF v_op='CREATE_CATEGORY' THEN INSERT INTO public.core_disccategory VALUES(v_id,v_board_key,NULL,v_key); END IF;
   INSERT INTO public.core_disccategoryversion VALUES(gen_random_uuid(),v_id,v_key,coalesce(v_cat.version_number,0)+1,
     v_cat.version_uuid,CASE WHEN v_op='RETIRE_CATEGORY' THEN v_cat.name ELSE p_payload->>'name' END,
     CASE WHEN v_op='RETIRE_CATEGORY' THEN v_cat.description ELSE p_payload->>'description' END,
     CASE WHEN v_op='RETIRE_CATEGORY' THEN v_cat.sort_order ELSE (p_payload->>'order')::integer END,
     CASE WHEN v_op='RETIRE_CATEGORY' THEN v_cat.access_level ELSE p_payload->>'access' END,v_op='RETIRE_CATEGORY');
 END IF;
 SELECT * INTO STRICT v_lo FROM public.core_livingorganism WHERE id=v_lo.id FOR UPDATE;
 SELECT * INTO STRICT v_head FROM public.core_livingorganismevent WHERE id=v_lo.head_event_id;
 v_state:=public.s015_fold_effective_state('core_livingorganism',v_lo.id,v_stamp,v_stamp);
 v_event:=public.s015_append_event('core_livingorganism',v_lo.id,'RECORD_DISCUSSION',v_state,
   v_stamp,p_received,p_received,p_actor,v_lo.founding_authority_basis_id,
   public.disc_decision(v_source,v_fp),'disc-act:'||v_key,'disc:'||v_key,v_fp,'{}'::jsonb,
   NULL,v_key,v_head.id,v_head.sequence::integer,NULL::bigint,NULL::text,'disc:'||v_key);
 RETURN jsonb_build_object('board',v_board_key,'act',v_key,'operation',v_op);
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
DISC_EXECUTE_CLOCK = r"""
DECLARE v_board public.core_discboard; v_lo public.core_livingorganism; v_circle bigint;
 v_old public.core_discact; v_source jsonb; v_stamp timestamptz:=public.intevia_clock_statement();
 v_key uuid:=(p_payload->>'request')::uuid; v_board_key uuid:=(p_payload->>'board')::uuid;
 v_op text:=p_payload->>'operation'; v_cat public.disc_categories; v_id uuid; v_fp text;
 v_head public.core_livingorganismevent; v_state text; v_event bigint; v_code text; v_other_board uuid;
 v_conflict_name text; v_conflict_table text; v_conflict_schema text;
BEGIN
 -- Serialize the global request identity before the owning-organism lock.
 PERFORM pg_advisory_xact_lock(hashtextextended('DISC1:request:'||v_key::text,0));
 SELECT * INTO v_lo FROM public.core_livingorganism WHERE organism_id=(p_payload->>'organism')::uuid;
 IF v_lo.id IS NULL THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 PERFORM public.s015_lock_organisms(ARRAY[v_lo.organism_id]);
 IF p_payload->>'circle' IS NOT NULL THEN
   SELECT id INTO v_circle FROM public.core_circle WHERE circle_uuid=(p_payload->>'circle')::uuid AND parent_organism_id=v_lo.id;
   IF v_circle IS NULL THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 END IF;
 IF NOT public.disc_audience(v_lo.id,v_circle,p_actor,'MEMBERS',v_stamp)
   AND NOT public.disc_audience(v_lo.id,v_circle,p_actor,'PROBATIONARY',v_stamp) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 SELECT * INTO v_board FROM public.core_discboard WHERE organism_id=v_lo.id AND circle_id IS NOT DISTINCT FROM v_circle;
 IF (v_board.board_uuid IS NULL AND v_op<>'PROVISION')
   OR (v_board.board_uuid IS NOT NULL AND v_board.board_uuid<>v_board_key) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 IF v_board.board_uuid IS NULL THEN
   -- Resolve D1 without inserting a board before the global reuse checks.
   IF public.disc_member_state(v_lo.id,p_actor,v_stamp)='ACTIVE' THEN
     v_source:=jsonb_build_object('kind','RULE','reference','HG:DISC1:D1:20261106','route','PROVISION');
   END IF;
 ELSE
   v_source:=public.disc_source(v_board_key,p_actor,v_op,v_stamp);
 END IF;
 IF v_source IS NULL THEN RAISE EXCEPTION 'DISC_AUTHORITY' USING ERRCODE='23514'; END IF;
 -- 0072 also works alone; 0073's act table exists only after that migration.
 IF to_regclass('public.core_discpostact') IS NOT NULL THEN
   EXECUTE 'SELECT board_uuid FROM public.core_discpostact WHERE act_uuid=$1' INTO v_other_board USING v_key;
   IF v_other_board IS NOT NULL THEN
     IF v_other_board<>v_board_key THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
     RAISE EXCEPTION 'DISC_REQUEST_CONFLICT' USING ERRCODE='23514';
   END IF;
 END IF;
 SELECT * INTO v_old FROM public.core_discact WHERE act_uuid=v_key;
 IF v_old.act_uuid IS NOT NULL THEN
   IF v_old.board_uuid<>v_board_key THEN RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   IF v_old.payload IS DISTINCT FROM p_payload OR v_old.actor_id<>p_actor OR v_old.actor_epoch<>p_epoch THEN
     RAISE EXCEPTION 'DISC_REQUEST_CONFLICT' USING ERRCODE='23514'; END IF;
   RETURN jsonb_build_object('board',v_board_key,'act',v_key,'operation',v_op);
 END IF;
 -- A request belonging to any other shared-chain domain is unavailable.
 IF EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.event_uuid=v_key) THEN
   RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
 IF v_op='PROVISION' AND v_board.board_uuid IS NOT NULL THEN
   RETURN jsonb_build_object('board',v_board.board_uuid,'act',v_board.creation_act,'operation',v_op);
 END IF;
 IF v_op='PROVISION' THEN
   INSERT INTO public.core_discboard VALUES(v_board_key,v_lo.id,v_circle,v_key);
 END IF;
 v_fp:=public.disc_fingerprint(p_payload);
 INSERT INTO public.core_discact(act_uuid,board_uuid,operation,actor_id,actor_epoch,authority_source,payload,fingerprint,received_at)
 VALUES(v_key,v_board_key,v_op,p_actor,p_epoch,v_source,p_payload,v_fp,p_received);
 IF v_op IN ('PROVISION','WELCOME') THEN
   FOREACH v_code IN ARRAY (CASE WHEN v_op='PROVISION' THEN ARRAY['COORDINATION','DISCUSSION','ANNOUNCEMENTS'] ELSE ARRAY['WELCOME'] END) LOOP
     v_id:=gen_random_uuid();
     INSERT INTO public.core_disccategory VALUES(v_id,v_board_key,v_code,v_key);
     INSERT INTO public.core_disccategoryversion VALUES(gen_random_uuid(),v_id,v_key,1,NULL,initcap(v_code),'',
       CASE v_code WHEN 'COORDINATION' THEN 1 WHEN 'DISCUSSION' THEN 2 WHEN 'ANNOUNCEMENTS' THEN 3 ELSE 4 END,
       CASE v_code WHEN 'COORDINATION' THEN 'COORDINATORS' WHEN 'WELCOME' THEN 'PROBATIONARY' ELSE 'MEMBERS' END,false);
   END LOOP;
 ELSIF v_op IN ('CREATE_CATEGORY','REVISE_CATEGORY','RETIRE_CATEGORY') THEN
   v_id:=(p_payload->>'category')::uuid;
   SELECT * INTO v_cat FROM public.disc_categories WHERE category_uuid=v_id;
   IF v_op='CREATE_CATEGORY' THEN INSERT INTO public.core_disccategory VALUES(v_id,v_board_key,NULL,v_key); END IF;
   INSERT INTO public.core_disccategoryversion VALUES(gen_random_uuid(),v_id,v_key,coalesce(v_cat.version_number,0)+1,
     v_cat.version_uuid,CASE WHEN v_op='RETIRE_CATEGORY' THEN v_cat.name ELSE p_payload->>'name' END,
     CASE WHEN v_op='RETIRE_CATEGORY' THEN v_cat.description ELSE p_payload->>'description' END,
     CASE WHEN v_op='RETIRE_CATEGORY' THEN v_cat.sort_order ELSE (p_payload->>'order')::integer END,
     CASE WHEN v_op='RETIRE_CATEGORY' THEN v_cat.access_level ELSE p_payload->>'access' END,v_op='RETIRE_CATEGORY');
 END IF;
 SELECT * INTO STRICT v_lo FROM public.core_livingorganism WHERE id=v_lo.id FOR UPDATE;
 SELECT * INTO STRICT v_head FROM public.core_livingorganismevent WHERE id=v_lo.head_event_id;
 v_state:=public.s015_fold_effective_state('core_livingorganism',v_lo.id,v_stamp,v_stamp);
 v_event:=public.s015_append_event('core_livingorganism',v_lo.id,'RECORD_DISCUSSION',v_state,
   v_stamp,p_received,p_received,p_actor,v_lo.founding_authority_basis_id,
   public.disc_decision(v_source,v_fp),'disc-act:'||v_key,'disc:'||v_key,v_fp,'{}'::jsonb,
   NULL,v_key,v_head.id,v_head.sequence::integer,NULL::bigint,NULL::text,'disc:'||v_key);
 RETURN jsonb_build_object('board',v_board_key,'act',v_key,'operation',v_op);
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

# public.disc_input()
# Its landed text: 0072_discussion_boards.py (found there verbatim). Time calls moved: 1 statement.
DISC_INPUT_LANDED = r"""
DECLARE v_board public.core_discboard; v_act public.core_discact; v_cat public.disc_categories;
 v_lo uuid; v_circle_uuid uuid; v_stamp timestamptz:=statement_timestamp(); v_source jsonb; v_count integer;
BEGIN
 IF TG_TABLE_NAME='core_discboard' THEN
   SELECT organism_id INTO v_lo FROM public.core_livingorganism WHERE id=NEW.organism_id;
   PERFORM public.s015_lock_organisms(ARRAY[v_lo]);
   IF NEW.circle_id IS NOT NULL AND NOT EXISTS(SELECT FROM public.core_circle c
     WHERE c.id=NEW.circle_id AND c.parent_organism_id=NEW.organism_id) THEN
     RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 ELSIF TG_TABLE_NAME='core_discact' THEN
   SELECT * INTO STRICT v_board FROM public.core_discboard WHERE board_uuid=NEW.board_uuid;
   SELECT organism_id INTO v_lo FROM public.core_livingorganism WHERE id=v_board.organism_id;
   PERFORM public.s015_lock_organisms(ARRAY[v_lo]);
   IF NEW.execution_xid IS NOT NULL THEN RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   NEW.execution_xid:=pg_current_xact_id(); NEW.effective_at:=v_stamp; NEW.recorded_at:=v_stamp;
   v_source:=public.disc_source(NEW.board_uuid,NEW.actor_id,NEW.operation,v_stamp);
   IF v_source IS NULL OR v_source IS DISTINCT FROM NEW.authority_source
     OR public.disc_source(NEW.board_uuid,NEW.actor_id,NEW.operation,NEW.received_at) IS DISTINCT FROM v_source THEN
     RAISE EXCEPTION 'DISC_AUTHORITY' USING ERRCODE='23514'; END IF;
   SELECT circle_uuid INTO v_circle_uuid FROM public.core_circle WHERE id=v_board.circle_id;
   IF NEW.received_at>v_stamp OR NEW.fingerprint IS DISTINCT FROM public.disc_fingerprint(NEW.payload)
     OR NEW.payload->>'organism' IS DISTINCT FROM v_lo::text
     OR NEW.payload->>'circle' IS DISTINCT FROM v_circle_uuid::text
     OR NEW.payload->>'request' IS DISTINCT FROM NEW.act_uuid::text
     OR NEW.payload->>'board' IS DISTINCT FROM NEW.board_uuid::text
     OR NEW.payload->>'operation' IS DISTINCT FROM NEW.operation
     OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch) THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   IF NEW.operation='PROVISION' THEN
     IF v_board.creation_act<>NEW.act_uuid OR EXISTS(SELECT FROM public.core_discact a WHERE a.board_uuid=NEW.board_uuid) THEN
       RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   ELSIF NEW.operation='PROBATION_ACCESS' THEN
     IF jsonb_typeof(NEW.payload->'enabled') IS DISTINCT FROM 'boolean' THEN
       RAISE EXCEPTION 'DISC_INPUT' USING ERRCODE='23514'; END IF;
   ELSIF NEW.operation='WELCOME' THEN
     IF EXISTS(SELECT FROM public.core_disccategory c
       WHERE c.board_uuid=NEW.board_uuid AND c.functional_code='WELCOME') THEN
       RAISE EXCEPTION 'DISC_WELCOME_OPEN' USING ERRCODE='23514'; END IF;
     IF NOT public.disc_probation_open(v_board.organism_id,v_stamp) THEN
       RAISE EXCEPTION 'DISC_PROBATION' USING ERRCODE='23514'; END IF;
   ELSE
     SELECT * INTO v_cat FROM public.disc_categories WHERE category_uuid=(NEW.payload->>'category')::uuid;
     IF NEW.operation='CREATE_CATEGORY' THEN
       IF v_cat.category_uuid IS NOT NULL AND v_cat.board_uuid<>NEW.board_uuid THEN
         RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
       IF v_cat.category_uuid IS NOT NULL THEN RAISE EXCEPTION 'DISC_PREDECESSOR' USING ERRCODE='23514'; END IF;
       SELECT count(*) INTO v_count FROM public.disc_categories c WHERE c.board_uuid=NEW.board_uuid
         AND c.functional_code IS NULL AND NOT c.retired;
       IF v_count>=9 AND NOT public.s015_entitlement_effective(v_lo,'DISCUSSION_CUSTOM_CATEGORIES',v_stamp) THEN -- DISC_FREE_CAP
         RAISE EXCEPTION 'DISC_CATEGORY_LIMIT' USING ERRCODE='23514'; END IF;
     ELSE
       IF v_cat.category_uuid IS NULL OR v_cat.board_uuid<>NEW.board_uuid THEN
         RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
       IF v_cat.functional_code IS NOT NULL THEN RAISE EXCEPTION 'DISC_FUNCTIONAL' USING ERRCODE='23514'; END IF;
       IF NEW.payload->>'predecessor' IS DISTINCT FROM v_cat.version_uuid::text OR v_cat.retired THEN
         RAISE EXCEPTION 'DISC_PREDECESSOR' USING ERRCODE='23514'; END IF;
     END IF;
     IF NEW.operation<>'RETIRE_CATEGORY' THEN
       IF jsonb_typeof(NEW.payload->'name') IS DISTINCT FROM 'string' OR length(NEW.payload->>'name') NOT BETWEEN 1 AND 45
         OR btrim(NEW.payload->>'name')='' OR jsonb_typeof(NEW.payload->'description') IS DISTINCT FROM 'string'
         OR NEW.payload->>'access' NOT IN ('COORDINATORS','MEMBERS','PROBATIONARY')
         OR jsonb_typeof(NEW.payload->'order') IS DISTINCT FROM 'number'
         OR NEW.payload->>'order' !~ '^-?[0-9]+$' THEN RAISE EXCEPTION 'DISC_INPUT' USING ERRCODE='23514'; END IF;
       IF NEW.payload->>'access'='PROBATIONARY' AND NOT public.disc_probation_open(v_board.organism_id,v_stamp) THEN
         RAISE EXCEPTION 'DISC_PROBATION' USING ERRCODE='23514'; END IF;
     END IF;
   END IF;
   RETURN NEW;
 ELSIF TG_TABLE_NAME='core_disccategory' THEN
   SELECT * INTO STRICT v_act FROM public.core_discact WHERE act_uuid=NEW.creation_act;
   IF v_act.execution_xid<>pg_current_xact_id() OR v_act.board_uuid<>NEW.board_uuid
     OR NOT ((v_act.operation='PROVISION' AND NEW.functional_code IN ('COORDINATION','DISCUSSION','ANNOUNCEMENTS'))
       OR (v_act.operation='WELCOME' AND NEW.functional_code='WELCOME')
       OR (v_act.operation='CREATE_CATEGORY' AND NEW.functional_code IS NULL
           AND v_act.payload->>'category'=NEW.category_uuid::text)) THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 ELSE
   SELECT * INTO STRICT v_act FROM public.core_discact WHERE act_uuid=NEW.act_uuid;
   SELECT * INTO v_cat FROM public.disc_categories WHERE category_uuid=NEW.category_uuid;
   SELECT count(*) INTO v_count FROM public.core_disccategoryversion v WHERE v.category_uuid=NEW.category_uuid;
   IF v_act.execution_xid<>pg_current_xact_id() OR NEW.version_number<>v_count+1
     OR NEW.predecessor IS DISTINCT FROM v_cat.version_uuid OR NOT EXISTS(
       SELECT FROM public.core_disccategory c WHERE c.category_uuid=NEW.category_uuid AND c.board_uuid=v_act.board_uuid) THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   IF v_act.operation IN ('CREATE_CATEGORY','REVISE_CATEGORY') THEN
     IF NEW.name IS DISTINCT FROM v_act.payload->>'name' OR NEW.description IS DISTINCT FROM v_act.payload->>'description'
       OR NEW.access_level IS DISTINCT FROM v_act.payload->>'access' OR NEW.sort_order IS DISTINCT FROM (v_act.payload->>'order')::integer
       OR NEW.retired OR NEW.category_uuid::text IS DISTINCT FROM v_act.payload->>'category' THEN
       RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   ELSIF v_act.operation='RETIRE_CATEGORY' THEN
     IF NOT NEW.retired OR NEW.name<>v_cat.name OR NEW.description<>v_cat.description OR NEW.sort_order<>v_cat.sort_order
       OR NEW.access_level<>v_cat.access_level OR NEW.category_uuid::text IS DISTINCT FROM v_act.payload->>'category' THEN
       RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   ELSIF v_act.operation IN ('PROVISION','WELCOME') THEN
     IF NEW.predecessor IS NOT NULL OR NEW.retired OR NOT EXISTS(
       SELECT FROM public.core_disccategory c WHERE c.category_uuid=NEW.category_uuid
         AND c.creation_act=v_act.act_uuid AND
         (c.functional_code,NEW.name,NEW.access_level,NEW.sort_order) IN
           (('COORDINATION','Coordination','COORDINATORS',1),('DISCUSSION','Discussion','MEMBERS',2),
            ('ANNOUNCEMENTS','Announcements','MEMBERS',3),('WELCOME','Welcome','PROBATIONARY',4)))
       OR NEW.description<>'' THEN RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   ELSE RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 END IF;
END """
DISC_INPUT_CLOCK = r"""
DECLARE v_board public.core_discboard; v_act public.core_discact; v_cat public.disc_categories;
 v_lo uuid; v_circle_uuid uuid; v_stamp timestamptz:=public.intevia_clock_statement(); v_source jsonb; v_count integer;
BEGIN
 IF TG_TABLE_NAME='core_discboard' THEN
   SELECT organism_id INTO v_lo FROM public.core_livingorganism WHERE id=NEW.organism_id;
   PERFORM public.s015_lock_organisms(ARRAY[v_lo]);
   IF NEW.circle_id IS NOT NULL AND NOT EXISTS(SELECT FROM public.core_circle c
     WHERE c.id=NEW.circle_id AND c.parent_organism_id=NEW.organism_id) THEN
     RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 ELSIF TG_TABLE_NAME='core_discact' THEN
   SELECT * INTO STRICT v_board FROM public.core_discboard WHERE board_uuid=NEW.board_uuid;
   SELECT organism_id INTO v_lo FROM public.core_livingorganism WHERE id=v_board.organism_id;
   PERFORM public.s015_lock_organisms(ARRAY[v_lo]);
   IF NEW.execution_xid IS NOT NULL THEN RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   NEW.execution_xid:=pg_current_xact_id(); NEW.effective_at:=v_stamp; NEW.recorded_at:=v_stamp;
   v_source:=public.disc_source(NEW.board_uuid,NEW.actor_id,NEW.operation,v_stamp);
   IF v_source IS NULL OR v_source IS DISTINCT FROM NEW.authority_source
     OR public.disc_source(NEW.board_uuid,NEW.actor_id,NEW.operation,NEW.received_at) IS DISTINCT FROM v_source THEN
     RAISE EXCEPTION 'DISC_AUTHORITY' USING ERRCODE='23514'; END IF;
   SELECT circle_uuid INTO v_circle_uuid FROM public.core_circle WHERE id=v_board.circle_id;
   IF NEW.received_at>v_stamp OR NEW.fingerprint IS DISTINCT FROM public.disc_fingerprint(NEW.payload)
     OR NEW.payload->>'organism' IS DISTINCT FROM v_lo::text
     OR NEW.payload->>'circle' IS DISTINCT FROM v_circle_uuid::text
     OR NEW.payload->>'request' IS DISTINCT FROM NEW.act_uuid::text
     OR NEW.payload->>'board' IS DISTINCT FROM NEW.board_uuid::text
     OR NEW.payload->>'operation' IS DISTINCT FROM NEW.operation
     OR NOT EXISTS(SELECT FROM public.core_identity i WHERE i.id=NEW.actor_id AND i.access_epoch=NEW.actor_epoch) THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   IF NEW.operation='PROVISION' THEN
     IF v_board.creation_act<>NEW.act_uuid OR EXISTS(SELECT FROM public.core_discact a WHERE a.board_uuid=NEW.board_uuid) THEN
       RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   ELSIF NEW.operation='PROBATION_ACCESS' THEN
     IF jsonb_typeof(NEW.payload->'enabled') IS DISTINCT FROM 'boolean' THEN
       RAISE EXCEPTION 'DISC_INPUT' USING ERRCODE='23514'; END IF;
   ELSIF NEW.operation='WELCOME' THEN
     IF EXISTS(SELECT FROM public.core_disccategory c
       WHERE c.board_uuid=NEW.board_uuid AND c.functional_code='WELCOME') THEN
       RAISE EXCEPTION 'DISC_WELCOME_OPEN' USING ERRCODE='23514'; END IF;
     IF NOT public.disc_probation_open(v_board.organism_id,v_stamp) THEN
       RAISE EXCEPTION 'DISC_PROBATION' USING ERRCODE='23514'; END IF;
   ELSE
     SELECT * INTO v_cat FROM public.disc_categories WHERE category_uuid=(NEW.payload->>'category')::uuid;
     IF NEW.operation='CREATE_CATEGORY' THEN
       IF v_cat.category_uuid IS NOT NULL AND v_cat.board_uuid<>NEW.board_uuid THEN
         RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
       IF v_cat.category_uuid IS NOT NULL THEN RAISE EXCEPTION 'DISC_PREDECESSOR' USING ERRCODE='23514'; END IF;
       SELECT count(*) INTO v_count FROM public.disc_categories c WHERE c.board_uuid=NEW.board_uuid
         AND c.functional_code IS NULL AND NOT c.retired;
       IF v_count>=9 AND NOT public.s015_entitlement_effective(v_lo,'DISCUSSION_CUSTOM_CATEGORIES',v_stamp) THEN -- DISC_FREE_CAP
         RAISE EXCEPTION 'DISC_CATEGORY_LIMIT' USING ERRCODE='23514'; END IF;
     ELSE
       IF v_cat.category_uuid IS NULL OR v_cat.board_uuid<>NEW.board_uuid THEN
         RAISE EXCEPTION 'DISC_UNAVAILABLE' USING ERRCODE='23514'; END IF;
       IF v_cat.functional_code IS NOT NULL THEN RAISE EXCEPTION 'DISC_FUNCTIONAL' USING ERRCODE='23514'; END IF;
       IF NEW.payload->>'predecessor' IS DISTINCT FROM v_cat.version_uuid::text OR v_cat.retired THEN
         RAISE EXCEPTION 'DISC_PREDECESSOR' USING ERRCODE='23514'; END IF;
     END IF;
     IF NEW.operation<>'RETIRE_CATEGORY' THEN
       IF jsonb_typeof(NEW.payload->'name') IS DISTINCT FROM 'string' OR length(NEW.payload->>'name') NOT BETWEEN 1 AND 45
         OR btrim(NEW.payload->>'name')='' OR jsonb_typeof(NEW.payload->'description') IS DISTINCT FROM 'string'
         OR NEW.payload->>'access' NOT IN ('COORDINATORS','MEMBERS','PROBATIONARY')
         OR jsonb_typeof(NEW.payload->'order') IS DISTINCT FROM 'number'
         OR NEW.payload->>'order' !~ '^-?[0-9]+$' THEN RAISE EXCEPTION 'DISC_INPUT' USING ERRCODE='23514'; END IF;
       IF NEW.payload->>'access'='PROBATIONARY' AND NOT public.disc_probation_open(v_board.organism_id,v_stamp) THEN
         RAISE EXCEPTION 'DISC_PROBATION' USING ERRCODE='23514'; END IF;
     END IF;
   END IF;
   RETURN NEW;
 ELSIF TG_TABLE_NAME='core_disccategory' THEN
   SELECT * INTO STRICT v_act FROM public.core_discact WHERE act_uuid=NEW.creation_act;
   IF v_act.execution_xid<>pg_current_xact_id() OR v_act.board_uuid<>NEW.board_uuid
     OR NOT ((v_act.operation='PROVISION' AND NEW.functional_code IN ('COORDINATION','DISCUSSION','ANNOUNCEMENTS'))
       OR (v_act.operation='WELCOME' AND NEW.functional_code='WELCOME')
       OR (v_act.operation='CREATE_CATEGORY' AND NEW.functional_code IS NULL
           AND v_act.payload->>'category'=NEW.category_uuid::text)) THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 ELSE
   SELECT * INTO STRICT v_act FROM public.core_discact WHERE act_uuid=NEW.act_uuid;
   SELECT * INTO v_cat FROM public.disc_categories WHERE category_uuid=NEW.category_uuid;
   SELECT count(*) INTO v_count FROM public.core_disccategoryversion v WHERE v.category_uuid=NEW.category_uuid;
   IF v_act.execution_xid<>pg_current_xact_id() OR NEW.version_number<>v_count+1
     OR NEW.predecessor IS DISTINCT FROM v_cat.version_uuid OR NOT EXISTS(
       SELECT FROM public.core_disccategory c WHERE c.category_uuid=NEW.category_uuid AND c.board_uuid=v_act.board_uuid) THEN
     RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   IF v_act.operation IN ('CREATE_CATEGORY','REVISE_CATEGORY') THEN
     IF NEW.name IS DISTINCT FROM v_act.payload->>'name' OR NEW.description IS DISTINCT FROM v_act.payload->>'description'
       OR NEW.access_level IS DISTINCT FROM v_act.payload->>'access' OR NEW.sort_order IS DISTINCT FROM (v_act.payload->>'order')::integer
       OR NEW.retired OR NEW.category_uuid::text IS DISTINCT FROM v_act.payload->>'category' THEN
       RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   ELSIF v_act.operation='RETIRE_CATEGORY' THEN
     IF NOT NEW.retired OR NEW.name<>v_cat.name OR NEW.description<>v_cat.description OR NEW.sort_order<>v_cat.sort_order
       OR NEW.access_level<>v_cat.access_level OR NEW.category_uuid::text IS DISTINCT FROM v_act.payload->>'category' THEN
       RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   ELSIF v_act.operation IN ('PROVISION','WELCOME') THEN
     IF NEW.predecessor IS NOT NULL OR NEW.retired OR NOT EXISTS(
       SELECT FROM public.core_disccategory c WHERE c.category_uuid=NEW.category_uuid
         AND c.creation_act=v_act.act_uuid AND
         (c.functional_code,NEW.name,NEW.access_level,NEW.sort_order) IN
           (('COORDINATION','Coordination','COORDINATORS',1),('DISCUSSION','Discussion','MEMBERS',2),
            ('ANNOUNCEMENTS','Announcements','MEMBERS',3),('WELCOME','Welcome','PROBATIONARY',4)))
       OR NEW.description<>'' THEN RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   ELSE RAISE EXCEPTION 'DISC_RECORD' USING ERRCODE='23514'; END IF;
   RETURN NEW;
 END IF;
END """

FUNCTIONS = (
    ("public.circle_command_valid()", CIRCLE_COMMAND_VALID_LANDED, CIRCLE_COMMAND_VALID_CLOCK),
    ("public.circle_leave_complete()", CIRCLE_LEAVE_COMPLETE_LANDED, CIRCLE_LEAVE_COMPLETE_CLOCK),
    ("public.sbg_guard_circleparticipation()", SBG_GUARD_CIRCLEPARTICIPATION_LANDED, SBG_GUARD_CIRCLEPARTICIPATION_CLOCK),
    ("public.circle_coordinator_definition()", CIRCLE_COORDINATOR_DEFINITION_LANDED, CIRCLE_COORDINATOR_DEFINITION_CLOCK),
    ("public.circle_close_on_final_exit()", CIRCLE_CLOSE_ON_FINAL_EXIT_LANDED, CIRCLE_CLOSE_ON_FINAL_EXIT_CLOCK),
    ("public.r_genesis_complete()", R_GENESIS_COMPLETE_LANDED, R_GENESIS_COMPLETE_CLOCK),
    ("public.r_genesis_input()", R_GENESIS_INPUT_LANDED, R_GENESIS_INPUT_CLOCK),
    ("public.r_genesis_nomination()", R_GENESIS_NOMINATION_LANDED, R_GENESIS_NOMINATION_CLOCK),
    ("public.sr_definition_insert_guard()", SR_DEFINITION_INSERT_GUARD_LANDED, SR_DEFINITION_INSERT_GUARD_CLOCK),
    ("public.r_circle_description_input()", R_CIRCLE_DESCRIPTION_INPUT_LANDED, R_CIRCLE_DESCRIPTION_INPUT_CLOCK),
    ("public.r_root_circle_batch_input()", R_ROOT_CIRCLE_BATCH_INPUT_LANDED, R_ROOT_CIRCLE_BATCH_INPUT_CLOCK),
    ("public.r_publication_declaration_input()", R_PUBLICATION_DECLARATION_INPUT_LANDED, R_PUBLICATION_DECLARATION_INPUT_CLOCK),
    ("public.r_activation_input()", R_ACTIVATION_INPUT_LANDED, R_ACTIVATION_INPUT_CLOCK),
    ("public.r_confirm_input()", R_CONFIRM_INPUT_LANDED, R_CONFIRM_INPUT_CLOCK),
    ("public.r_constitutional_grant_input()", R_CONSTITUTIONAL_GRANT_INPUT_LANDED, R_CONSTITUTIONAL_GRANT_INPUT_CLOCK),
    ("public.r_restoration_input()", R_RESTORATION_INPUT_LANDED, R_RESTORATION_INPUT_CLOCK),
    ("public.r_value_input()", R_VALUE_INPUT_LANDED, R_VALUE_INPUT_CLOCK),
    ("public.l_check_archive()", L_CHECK_ARCHIVE_LANDED, L_CHECK_ARCHIVE_CLOCK),
    ("public.l_act_complete()", L_ACT_COMPLETE_LANDED, L_ACT_COMPLETE_CLOCK),
    ("public.l_decision_time()", L_DECISION_TIME_LANDED, L_DECISION_TIME_CLOCK),
    ("public.l_execute(text)", L_EXECUTE_LANDED, L_EXECUTE_CLOCK),
    ("public.l_locator_input()", L_LOCATOR_INPUT_LANDED, L_LOCATOR_INPUT_CLOCK),
    ("public.l_lock_dependencies(jsonb, bigint, bigint)", L_LOCK_DEPENDENCIES_LANDED, L_LOCK_DEPENDENCIES_CLOCK),
    ("public.lib_author_input()", LIB_AUTHOR_INPUT_LANDED, LIB_AUTHOR_INPUT_CLOCK),
    ("public.disc_complete()", DISC_COMPLETE_LANDED, DISC_COMPLETE_CLOCK),
    ("public.disc_execute(jsonb, bigint, bigint, timestamp with time zone)", DISC_EXECUTE_LANDED, DISC_EXECUTE_CLOCK),
    ("public.disc_input()", DISC_INPUT_LANDED, DISC_INPUT_CLOCK),
)


def install(apps, schema_editor):
    """Move these functions onto the INTEVIA Clock."""
    clock.convert(schema_editor, FUNCTIONS)


def uninstall(apps, schema_editor):
    """Put the landed functions back."""
    clock.restore(schema_editor, FUNCTIONS)


class Migration(migrations.Migration):
    dependencies = (("core", "0098_clock_storage_0050_0058"),)
    operations = (migrations.RunPython(install, uninstall),)
