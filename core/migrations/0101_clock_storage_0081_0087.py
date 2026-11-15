"""The INTEVIA Clock, storage 5 of 7: the installed functions of migrations 0081 to 0087 (CK-2d).

12 installed functions read the time through PostgreSQL's own calls. This migration replaces
each under G2-4 with a successor that differs from it only where a time call becomes the
INTEVIA Clock's face with the same meaning (22 calls: 10 statement start, 12 instant,
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

# public.elect_vacancy_guard()
# Its landed text: 0083_elect_board_continuity.py (assembled by that migration). Time calls moved: 1 statement.
ELECT_VACANCY_GUARD_LANDED = r"""
DECLARE notice_ uuid; assessment_ uuid; s public.elect_vacancy_sources; cut timestamptz:=statement_timestamp();
BEGIN
 SELECT * INTO s FROM public.elect_vacancy_sources WHERE ending=NEW.ending AND organism_id=NEW.organism_id;
 IF s.ending IS NULL OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=NEW.enterer AND i.access_state='active' AND u.is_active) OR
    (NEW.enterer IS DISTINCT FROM s.source_enterer AND public.disc_member_state(s.organism_id,NEW.enterer,cut) IS DISTINCT FROM 'ACTIVE')
    OR public.elect_already_replaced(s,cut) OR greatest(s.recorded_at,s.effective_at)>cut OR
    (public.cq_settings_at(s.organism_id,greatest(s.recorded_at,s.effective_at))->>'enabled')::boolean IS NOT TRUE
 THEN PERFORM public.cq_refuse('ELECT_VACANCY_SOURCE'); END IF;
 NEW.route_act:=gen_random_uuid(); NEW.kind:=s.kind; NEW.place:=s.place; NEW.effective_at:=s.effective_at; NEW.recorded_at:=cut;

 IF s.kind='SEAT' THEN
   SELECT first_end.composed_from_event_uuid INTO notice_
   FROM public.core_circleparticipation p CROSS JOIN LATERAL (
     SELECT e.composed_from_event_uuid,e.composing_rule_reference
     FROM public.core_circleparticipationend e WHERE e.participation_id=p.id
     ORDER BY e.effective_at,e.recorded_at,e.id LIMIT 1
   ) first_end
   WHERE p.participation_uuid=s.occupancy
     AND first_end.composing_rule_reference='elect-resign:'||first_end.composed_from_event_uuid;
 END IF;
 IF notice_ IS NOT NULL THEN
   PERFORM public.elect_resignation_coverage(s.organism_id,notice_,NEW.enterer);
   SELECT x.event_uuid INTO STRICT assessment_ FROM public.core_coverageassessment x
   JOIN public.core_essentialcoveragecase c ON c.id=x.case_id
   WHERE c.living_organism_id=s.organism_id AND x.effective_at=s.effective_at
     AND x.evidence_reference='elect-resignation:'||notice_;
 END IF;
 NEW.instruction:=public.elect_vacancy_instruction(s);
 IF notice_ IS NOT NULL THEN
   NEW.instruction:=jsonb_set(NEW.instruction,'{assessment}',to_jsonb(assessment_));
 END IF;
 RETURN NEW;
END """
ELECT_VACANCY_GUARD_CLOCK = r"""
DECLARE notice_ uuid; assessment_ uuid; s public.elect_vacancy_sources; cut timestamptz:=public.intevia_clock_statement();
BEGIN
 SELECT * INTO s FROM public.elect_vacancy_sources WHERE ending=NEW.ending AND organism_id=NEW.organism_id;
 IF s.ending IS NULL OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
    WHERE i.id=NEW.enterer AND i.access_state='active' AND u.is_active) OR
    (NEW.enterer IS DISTINCT FROM s.source_enterer AND public.disc_member_state(s.organism_id,NEW.enterer,cut) IS DISTINCT FROM 'ACTIVE')
    OR public.elect_already_replaced(s,cut) OR greatest(s.recorded_at,s.effective_at)>cut OR
    (public.cq_settings_at(s.organism_id,greatest(s.recorded_at,s.effective_at))->>'enabled')::boolean IS NOT TRUE
 THEN PERFORM public.cq_refuse('ELECT_VACANCY_SOURCE'); END IF;
 NEW.route_act:=gen_random_uuid(); NEW.kind:=s.kind; NEW.place:=s.place; NEW.effective_at:=s.effective_at; NEW.recorded_at:=cut;

 IF s.kind='SEAT' THEN
   SELECT first_end.composed_from_event_uuid INTO notice_
   FROM public.core_circleparticipation p CROSS JOIN LATERAL (
     SELECT e.composed_from_event_uuid,e.composing_rule_reference
     FROM public.core_circleparticipationend e WHERE e.participation_id=p.id
     ORDER BY e.effective_at,e.recorded_at,e.id LIMIT 1
   ) first_end
   WHERE p.participation_uuid=s.occupancy
     AND first_end.composing_rule_reference='elect-resign:'||first_end.composed_from_event_uuid;
 END IF;
 IF notice_ IS NOT NULL THEN
   PERFORM public.elect_resignation_coverage(s.organism_id,notice_,NEW.enterer);
   SELECT x.event_uuid INTO STRICT assessment_ FROM public.core_coverageassessment x
   JOIN public.core_essentialcoveragecase c ON c.id=x.case_id
   WHERE c.living_organism_id=s.organism_id AND x.effective_at=s.effective_at
     AND x.evidence_reference='elect-resignation:'||notice_;
 END IF;
 NEW.instruction:=public.elect_vacancy_instruction(s);
 IF notice_ IS NOT NULL THEN
   NEW.instruction:=jsonb_set(NEW.instruction,'{assessment}',to_jsonb(assessment_));
 END IF;
 RETURN NEW;
END """

# public.sbg_guard_circleadmissiondecision()
# Its landed text: 0083_elect_board_continuity.py (assembled by that migration). Time calls moved: 5 wall.
SBG_GUARD_CIRCLEADMISSIONDECISION_LANDED = r"""
DECLARE
    v_request public.core_circlejoinrequest%ROWTYPE;
    v_circle public.core_circle%ROWTYPE;
    v_membership public.core_organismmembership%ROWTYPE;
    v_rule public.core_governedrule%ROWTYPE;
    v_expected_settings_basis text;
BEGIN

    IF NEW.constitutional_route='RECOGNITION' THEN
       IF NOT EXISTS(SELECT FROM public.core_cqact r JOIN public.core_circlejoinrequest q ON q.id=NEW.request_id
         WHERE r.act_uuid=NEW.decision_uuid AND r.operation='RECOGNISE' AND r.execution_xid=pg_current_xact_id()
         AND r.facts->>'type'='SEAT' AND q.request_uuid::text=r.facts->>'consent'
         AND NEW.route='GOVERNANCE_CONSTITUTIONAL' AND NEW.deciding_party=r.authority_source->>'route'
         AND NEW.deciding_identity_id IS NULL AND NOT NEW.self_decision AND NEW.outcome='ACCEPT'
         AND NEW.authority_decision_reference=public.disc_decision(r.authority_source,r.fingerprint)
         AND NEW.seat_id=q.seat_id AND NEW.seat_rule_uuid=q.seat_rule_uuid
         AND NEW.settings_basis='rule:'||q.seat_rule_uuid||'@'||(r.facts->>'seat_version')||'#'||q.seat_id
         AND NEW.enterer_identity_id=r.actor_id AND NEW.occurred_at=r.received_at AND NEW.effective_at=r.effective_at
         AND NEW.received_at=r.received_at) THEN PERFORM public.cq_refuse('ELECT_RECOGNISER'); END IF;
       RETURN NEW;
    END IF;

    IF NEW.constitutional_route='ELECTION' OR
      (NEW.constitutional_route='COOPTION' AND NEW.deciding_identity_id IS NULL) THEN
      IF NOT EXISTS(SELECT FROM public.core_cqact i JOIN public.core_circlejoinrequest q ON q.id=NEW.request_id
       JOIN public.core_organismmembership m ON m.id=q.membership_id JOIN public.core_identity p ON p.id=m.identity_id
       CROSS JOIN LATERAL (SELECT i.facts->'domain_plan' AS plan) dom
       WHERE i.operation='IMPLEMENT' AND i.act_uuid=NEW.decision_uuid AND i.organism_id=m.living_organism_id
       AND i.execution_xid=pg_current_xact_id() AND i.authority_source=public.cq3_implement_source(i)
       AND plan->>'type'='SEAT' AND NEW.constitutional_route=coalesce(plan->>'admission_route','ELECTION')
       AND NEW.deciding_party='body:'||(plan->>'body') AND q.circle_id=(plan->>'circle')::bigint AND q.request_uuid::text=plan->'winner'->>'consent'
       AND q.seat_id=NEW.seat_id AND q.seat_rule_uuid=NEW.seat_rule_uuid AND NEW.seat_rule_uuid::text=plan->>'seat_rule'
       AND NEW.settings_basis='rule:'||NEW.seat_rule_uuid||'@'||(plan->>'seat_version')||'#'||NEW.seat_id
       AND NEW.authority_decision_reference=public.disc_decision(i.authority_source,i.fingerprint)
       AND NEW.effective_at=(plan->>'start')::timestamptz AND NEW.received_at=i.received_at AND NEW.occurred_at=i.effective_at
       AND NEW.enterer_identity_id=i.actor_id AND NEW.deciding_identity_id IS NULL AND NOT NEW.self_decision
       AND NEW.outcome='ACCEPT' AND public.elect_authorises((i.request->>'result')::uuid,i.organism_id,
           p.identity_id,NEW.seat_id,NEW.effective_at,(plan->>'end')::timestamptz))
       THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
      RETURN NEW;
    END IF;
    IF NEW.deciding_identity_id IS NULL THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;

    IF NEW.route='APPLICATION_MODE' THEN
        SELECT * INTO STRICT v_request FROM public.core_circlejoinrequest WHERE id=NEW.request_id;
        SELECT * INTO STRICT v_circle FROM public.core_circle WHERE id=v_request.circle_id;
        SELECT * INTO STRICT v_membership FROM public.core_organismmembership WHERE id=v_request.membership_id;
        PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=v_circle.parent_organism_id)]);
        IF v_circle.kind<>'GENERIC' OR v_request.initiated_by<>'CIRCLE_MEMBER'
           OR NEW.settings_basis<>'mode:APPLICATION' OR NEW.enterer_identity_id<>NEW.deciding_identity_id
           OR NEW.self_decision IS DISTINCT FROM (NEW.enterer_identity_id=v_membership.identity_id)
           OR NEW.seat_id<>v_request.seat_id OR NEW.seat_rule_uuid<>v_request.seat_rule_uuid
           OR NEW.effective_at<>NEW.received_at OR NEW.occurred_at<>NEW.effective_at
           OR NEW.effective_at<v_request.effective_at OR NEW.received_at>NEW.recorded_at
           OR v_request.recorded_at>NEW.recorded_at THEN
           RAISE EXCEPTION 'CIRCLE_APPLICATION_SHAPE' USING ERRCODE='23514';
        END IF;
        IF NEW.outcome='WITHDRAWN' THEN
           IF NEW.actor_state<>'PARTY' OR NEW.actor_id IS DISTINCT FROM v_membership.identity_id
              OR NEW.enterer_identity_id<>v_membership.identity_id OR NOT EXISTS(
                 SELECT FROM public.core_identity i WHERE i.id=NEW.actor_id AND i.access_state='active'
                 AND i.access_epoch=NEW.actor_access_epoch)
              OR NEW.authority_decision_reference IS DISTINCT FROM public.circle_office_decision(
                 v_request.request_uuid,v_circle.circle_uuid,'WITHDRAW',NEW.actor_id,NEW.actor_id) THEN
              RAISE EXCEPTION 'CIRCLE_APPLICATION_WITHDRAWER' USING ERRCODE='23514';
           END IF;
        ELSE
           IF NEW.actor_state<>'LIVING_ORGANISM' OR NEW.outcome NOT IN ('ACCEPT','DECLINE')
              OR EXISTS(SELECT FROM (SELECT NEW.effective_at,NEW.recorded_at UNION ALL SELECT t,t FROM clock_timestamp() t) cuts(t,k)
                 WHERE public.circle_office_decision(public.circle_current_office(v_circle.id,NEW.enterer_identity_id,t,k),
                       v_request.request_uuid,NEW.outcome,NEW.enterer_identity_id,v_membership.identity_id)
                       IS DISTINCT FROM NEW.authority_decision_reference) THEN
              RAISE EXCEPTION 'CIRCLE_ADMISSION_SOURCE' USING ERRCODE='23514';
           END IF;
           IF NEW.outcome='ACCEPT' AND (
              NOT public.circle_eligible(v_circle.id,v_membership.id,NEW.effective_at,NEW.recorded_at)
              OR NOT public.circle_eligible(v_circle.id,v_membership.id,clock_timestamp(),clock_timestamp())
              OR public.c1b_period_at(v_membership.id,v_request.effective_at,v_request.recorded_at) IS DISTINCT FROM
                 public.c1b_period_at(v_membership.id,clock_timestamp(),clock_timestamp())) THEN
              RAISE EXCEPTION 'CIRCLE_APPLICATION_MEMBERSHIP' USING ERRCODE='23514';
           END IF;
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.route='ORDINARY' THEN
        SELECT * INTO STRICT v_request FROM public.core_circlejoinrequest WHERE id=NEW.request_id;
        SELECT * INTO STRICT v_circle FROM public.core_circle WHERE id=v_request.circle_id;
        SELECT * INTO STRICT v_membership FROM public.core_organismmembership WHERE id=v_request.membership_id;
        PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=v_circle.parent_organism_id)]);
        IF v_request.initiated_by<>'CIRCLE_MEMBER' OR v_circle.kind IN ('GOVERNANCE','GENERIC')
           OR NEW.outcome<>'ACCEPT' OR NEW.seat_id<>v_request.seat_id OR NEW.seat_rule_uuid<>v_request.seat_rule_uuid
           OR NEW.effective_at<>NEW.received_at OR NEW.effective_at<>NEW.occurred_at OR NEW.effective_at<v_request.effective_at
           OR NEW.received_at>NEW.recorded_at OR v_request.recorded_at>NEW.recorded_at
           OR NOT public.circle_eligible(v_circle.id,v_membership.id,NEW.recorded_at,NEW.recorded_at)
           OR NEW.enterer_identity_id<>NEW.deciding_identity_id
           OR NEW.self_decision IS DISTINCT FROM (NEW.deciding_identity_id=v_membership.identity_id)
           OR NOT public.circle_eligible(v_circle.id,v_membership.id,NEW.effective_at,NEW.recorded_at)
           OR public.c1b_period_at(v_membership.id,v_request.effective_at,v_request.recorded_at) IS DISTINCT FROM
              public.c1b_period_at(v_membership.id,NEW.effective_at,NEW.recorded_at)
           OR NOT EXISTS(SELECT FROM public.core_governeddetermination d WHERE 'finding:'||d.determination_uuid=NEW.settings_basis
              AND NEW.authority_decision_reference=public.circle_decision(v_circle.parent_organism_id,d.determination_uuid,NEW.effective_at,NEW.recorded_at)
              AND NEW.authority_decision_reference=public.circle_decision(v_circle.parent_organism_id,d.determination_uuid,NEW.recorded_at,NEW.recorded_at)
              AND d.determination_uuid=public.circle_source(v_circle.parent_organism_id,v_circle.circle_uuid,
                  'ADMIT:'||v_request.request_uuid,NEW.deciding_identity_id,v_membership.identity_id,NEW.recorded_at,NEW.recorded_at)
              AND d.determination_uuid=public.circle_source(v_circle.parent_organism_id,v_circle.circle_uuid,
                  'ADMIT:'||v_request.request_uuid,NEW.deciding_identity_id,v_membership.identity_id,NEW.effective_at,NEW.recorded_at)) THEN
           RAISE EXCEPTION 'CIRCLE_ADMISSION_SOURCE' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF EXISTS(SELECT FROM public.core_circlejoinrequest WHERE id=NEW.request_id AND initiated_by='CIRCLE_MEMBER') THEN
       RAISE EXCEPTION 'CIRCLE_ADMISSION_ROUTE' USING ERRCODE='23514';
    END IF;
    SELECT q.* INTO v_request
      FROM public.core_circlejoinrequest q
     WHERE q.id = NEW.request_id;
    IF NOT FOUND
       OR NEW.seat_rule_uuid IS DISTINCT FROM v_request.seat_rule_uuid
       OR NEW.seat_id IS DISTINCT FROM v_request.seat_id THEN
        RAISE EXCEPTION 'SBG_CIRCLEDECISION_REQUEST_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.route NOT IN ('CIRCLE_FOUNDING', 'GOVERNANCE_CONSTITUTIONAL')
       OR (NEW.route = 'CIRCLE_FOUNDING'
           AND (NEW.constitutional_route <> 'NOT_APPLICABLE'
                OR NEW.deciding_party <> 'FOUNDING_BODY'))
       OR (NEW.route = 'GOVERNANCE_CONSTITUTIONAL'
           AND (NEW.constitutional_route <> 'APPOINTMENT'
                OR NEW.deciding_party <> 'office:LO_COORDINATOR'))
       OR NEW.outcome NOT IN ('ACCEPT', 'DECLINE')
       OR NEW.authority_decision_reference !~ '^s015d1:[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'SBG_CIRCLEDECISION_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT c.* INTO v_circle
      FROM public.core_circle c
     WHERE c.id = v_request.circle_id;
    SELECT m.* INTO v_membership
      FROM public.core_organismmembership m
     WHERE m.id = v_request.membership_id;
    IF NOT FOUND OR v_circle.parent_organism_id IS DISTINCT FROM
                         v_membership.living_organism_id
       OR NEW.self_decision IS DISTINCT FROM
                         (NEW.deciding_identity_id = v_membership.identity_id) THEN
        RAISE EXCEPTION 'SBG_CIRCLEDECISION_IDENTITY_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT r.* INTO v_rule
      FROM public.core_governedrule r
     WHERE r.scope_kind = 'LIVING_ORGANISM'
       AND r.scope_organism_id = v_circle.parent_organism_id
       AND r.code = 'GOVERNANCE_CONSTITUTION'
       AND r.rule_uuid = NEW.seat_rule_uuid
       AND r.effective_from <= NEW.effective_at
       AND r.recorded_at <= NEW.recorded_at
     ORDER BY r.effective_from DESC, r.recorded_at DESC, r.version DESC
     LIMIT 1;
    IF NOT FOUND OR NOT EXISTS (
        SELECT 1
          FROM jsonb_array_elements(v_rule.body->'seats') AS s(value)
         WHERE s.value->>'seat_id' = NEW.seat_id
           AND (s.value->>'effective_from')::timestamptz <= NEW.effective_at
    ) THEN
        RAISE EXCEPTION 'SBG_SEAT_ABSENT'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF (NEW.route='CIRCLE_FOUNDING') = EXISTS (
      SELECT FROM public.core_livingorganismevent b
      WHERE b.living_organism_id=v_circle.parent_organism_id AND b.action='BIRTHING'
        AND b.effective_at<=NEW.effective_at AND b.received_at<=NEW.received_at
        AND b.recorded_at<=NEW.received_at
    ) THEN
      RAISE EXCEPTION 'SBG_SEAT_ROUTE_UNAVAILABLE' USING ERRCODE='integrity_constraint_violation';
    END IF;
    IF NEW.route='GOVERNANCE_CONSTITUTIONAL' AND NOT EXISTS (
      SELECT FROM jsonb_array_elements(v_rule.body->'seats') AS seat(value)
      WHERE seat.value->>'seat_id'=NEW.seat_id
        AND (seat.value->>'effective_from')::timestamptz<=NEW.effective_at
        AND seat.value->>'admission_route'='APPOINTMENT'
        AND seat.value->>'deciding_body'='office:LO_COORDINATOR'
    ) THEN
      RAISE EXCEPTION 'SBG_SEAT_ROUTE_UNAVAILABLE' USING ERRCODE='integrity_constraint_violation';
    END IF;
    v_expected_settings_basis := 'rule:' || v_rule.rule_uuid::text
        || '@' || v_rule.version::text || '#' || NEW.seat_id;
    IF NEW.settings_basis IS DISTINCT FROM v_expected_settings_basis THEN
        RAISE EXCEPTION 'SBG_CIRCLEDECISION_SETTINGS_BASIS_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM'
       OR NEW.actor_id IS NOT NULL
       OR NEW.actor_access_epoch IS NOT NULL
       OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
       OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
       OR NEW.enterer_identity_id IS DISTINCT FROM NEW.deciding_identity_id
       OR NEW.composing_rule_state IS DISTINCT FROM 'NA_TRANSPORT'
       OR NEW.composing_rule_reference IS NOT NULL
       OR NEW.composer_state IS DISTINCT FROM 'NA_TRANSPORT'
       OR NEW.composer_identity_id IS NOT NULL THEN
        RAISE EXCEPTION 'SBG_CIRCLEDECISION_TRANSPORT_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
"""
SBG_GUARD_CIRCLEADMISSIONDECISION_CLOCK = r"""
DECLARE
    v_request public.core_circlejoinrequest%ROWTYPE;
    v_circle public.core_circle%ROWTYPE;
    v_membership public.core_organismmembership%ROWTYPE;
    v_rule public.core_governedrule%ROWTYPE;
    v_expected_settings_basis text;
BEGIN

    IF NEW.constitutional_route='RECOGNITION' THEN
       IF NOT EXISTS(SELECT FROM public.core_cqact r JOIN public.core_circlejoinrequest q ON q.id=NEW.request_id
         WHERE r.act_uuid=NEW.decision_uuid AND r.operation='RECOGNISE' AND r.execution_xid=pg_current_xact_id()
         AND r.facts->>'type'='SEAT' AND q.request_uuid::text=r.facts->>'consent'
         AND NEW.route='GOVERNANCE_CONSTITUTIONAL' AND NEW.deciding_party=r.authority_source->>'route'
         AND NEW.deciding_identity_id IS NULL AND NOT NEW.self_decision AND NEW.outcome='ACCEPT'
         AND NEW.authority_decision_reference=public.disc_decision(r.authority_source,r.fingerprint)
         AND NEW.seat_id=q.seat_id AND NEW.seat_rule_uuid=q.seat_rule_uuid
         AND NEW.settings_basis='rule:'||q.seat_rule_uuid||'@'||(r.facts->>'seat_version')||'#'||q.seat_id
         AND NEW.enterer_identity_id=r.actor_id AND NEW.occurred_at=r.received_at AND NEW.effective_at=r.effective_at
         AND NEW.received_at=r.received_at) THEN PERFORM public.cq_refuse('ELECT_RECOGNISER'); END IF;
       RETURN NEW;
    END IF;

    IF NEW.constitutional_route='ELECTION' OR
      (NEW.constitutional_route='COOPTION' AND NEW.deciding_identity_id IS NULL) THEN
      IF NOT EXISTS(SELECT FROM public.core_cqact i JOIN public.core_circlejoinrequest q ON q.id=NEW.request_id
       JOIN public.core_organismmembership m ON m.id=q.membership_id JOIN public.core_identity p ON p.id=m.identity_id
       CROSS JOIN LATERAL (SELECT i.facts->'domain_plan' AS plan) dom
       WHERE i.operation='IMPLEMENT' AND i.act_uuid=NEW.decision_uuid AND i.organism_id=m.living_organism_id
       AND i.execution_xid=pg_current_xact_id() AND i.authority_source=public.cq3_implement_source(i)
       AND plan->>'type'='SEAT' AND NEW.constitutional_route=coalesce(plan->>'admission_route','ELECTION')
       AND NEW.deciding_party='body:'||(plan->>'body') AND q.circle_id=(plan->>'circle')::bigint AND q.request_uuid::text=plan->'winner'->>'consent'
       AND q.seat_id=NEW.seat_id AND q.seat_rule_uuid=NEW.seat_rule_uuid AND NEW.seat_rule_uuid::text=plan->>'seat_rule'
       AND NEW.settings_basis='rule:'||NEW.seat_rule_uuid||'@'||(plan->>'seat_version')||'#'||NEW.seat_id
       AND NEW.authority_decision_reference=public.disc_decision(i.authority_source,i.fingerprint)
       AND NEW.effective_at=(plan->>'start')::timestamptz AND NEW.received_at=i.received_at AND NEW.occurred_at=i.effective_at
       AND NEW.enterer_identity_id=i.actor_id AND NEW.deciding_identity_id IS NULL AND NOT NEW.self_decision
       AND NEW.outcome='ACCEPT' AND public.elect_authorises((i.request->>'result')::uuid,i.organism_id,
           p.identity_id,NEW.seat_id,NEW.effective_at,(plan->>'end')::timestamptz))
       THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
      RETURN NEW;
    END IF;
    IF NEW.deciding_identity_id IS NULL THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;

    IF NEW.route='APPLICATION_MODE' THEN
        SELECT * INTO STRICT v_request FROM public.core_circlejoinrequest WHERE id=NEW.request_id;
        SELECT * INTO STRICT v_circle FROM public.core_circle WHERE id=v_request.circle_id;
        SELECT * INTO STRICT v_membership FROM public.core_organismmembership WHERE id=v_request.membership_id;
        PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=v_circle.parent_organism_id)]);
        IF v_circle.kind<>'GENERIC' OR v_request.initiated_by<>'CIRCLE_MEMBER'
           OR NEW.settings_basis<>'mode:APPLICATION' OR NEW.enterer_identity_id<>NEW.deciding_identity_id
           OR NEW.self_decision IS DISTINCT FROM (NEW.enterer_identity_id=v_membership.identity_id)
           OR NEW.seat_id<>v_request.seat_id OR NEW.seat_rule_uuid<>v_request.seat_rule_uuid
           OR NEW.effective_at<>NEW.received_at OR NEW.occurred_at<>NEW.effective_at
           OR NEW.effective_at<v_request.effective_at OR NEW.received_at>NEW.recorded_at
           OR v_request.recorded_at>NEW.recorded_at THEN
           RAISE EXCEPTION 'CIRCLE_APPLICATION_SHAPE' USING ERRCODE='23514';
        END IF;
        IF NEW.outcome='WITHDRAWN' THEN
           IF NEW.actor_state<>'PARTY' OR NEW.actor_id IS DISTINCT FROM v_membership.identity_id
              OR NEW.enterer_identity_id<>v_membership.identity_id OR NOT EXISTS(
                 SELECT FROM public.core_identity i WHERE i.id=NEW.actor_id AND i.access_state='active'
                 AND i.access_epoch=NEW.actor_access_epoch)
              OR NEW.authority_decision_reference IS DISTINCT FROM public.circle_office_decision(
                 v_request.request_uuid,v_circle.circle_uuid,'WITHDRAW',NEW.actor_id,NEW.actor_id) THEN
              RAISE EXCEPTION 'CIRCLE_APPLICATION_WITHDRAWER' USING ERRCODE='23514';
           END IF;
        ELSE
           IF NEW.actor_state<>'LIVING_ORGANISM' OR NEW.outcome NOT IN ('ACCEPT','DECLINE')
              OR EXISTS(SELECT FROM (SELECT NEW.effective_at,NEW.recorded_at UNION ALL SELECT t,t FROM public.intevia_clock_instant() t) cuts(t,k)
                 WHERE public.circle_office_decision(public.circle_current_office(v_circle.id,NEW.enterer_identity_id,t,k),
                       v_request.request_uuid,NEW.outcome,NEW.enterer_identity_id,v_membership.identity_id)
                       IS DISTINCT FROM NEW.authority_decision_reference) THEN
              RAISE EXCEPTION 'CIRCLE_ADMISSION_SOURCE' USING ERRCODE='23514';
           END IF;
           IF NEW.outcome='ACCEPT' AND (
              NOT public.circle_eligible(v_circle.id,v_membership.id,NEW.effective_at,NEW.recorded_at)
              OR NOT public.circle_eligible(v_circle.id,v_membership.id,public.intevia_clock_instant(),public.intevia_clock_instant())
              OR public.c1b_period_at(v_membership.id,v_request.effective_at,v_request.recorded_at) IS DISTINCT FROM
                 public.c1b_period_at(v_membership.id,public.intevia_clock_instant(),public.intevia_clock_instant())) THEN
              RAISE EXCEPTION 'CIRCLE_APPLICATION_MEMBERSHIP' USING ERRCODE='23514';
           END IF;
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.route='ORDINARY' THEN
        SELECT * INTO STRICT v_request FROM public.core_circlejoinrequest WHERE id=NEW.request_id;
        SELECT * INTO STRICT v_circle FROM public.core_circle WHERE id=v_request.circle_id;
        SELECT * INTO STRICT v_membership FROM public.core_organismmembership WHERE id=v_request.membership_id;
        PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=v_circle.parent_organism_id)]);
        IF v_request.initiated_by<>'CIRCLE_MEMBER' OR v_circle.kind IN ('GOVERNANCE','GENERIC')
           OR NEW.outcome<>'ACCEPT' OR NEW.seat_id<>v_request.seat_id OR NEW.seat_rule_uuid<>v_request.seat_rule_uuid
           OR NEW.effective_at<>NEW.received_at OR NEW.effective_at<>NEW.occurred_at OR NEW.effective_at<v_request.effective_at
           OR NEW.received_at>NEW.recorded_at OR v_request.recorded_at>NEW.recorded_at
           OR NOT public.circle_eligible(v_circle.id,v_membership.id,NEW.recorded_at,NEW.recorded_at)
           OR NEW.enterer_identity_id<>NEW.deciding_identity_id
           OR NEW.self_decision IS DISTINCT FROM (NEW.deciding_identity_id=v_membership.identity_id)
           OR NOT public.circle_eligible(v_circle.id,v_membership.id,NEW.effective_at,NEW.recorded_at)
           OR public.c1b_period_at(v_membership.id,v_request.effective_at,v_request.recorded_at) IS DISTINCT FROM
              public.c1b_period_at(v_membership.id,NEW.effective_at,NEW.recorded_at)
           OR NOT EXISTS(SELECT FROM public.core_governeddetermination d WHERE 'finding:'||d.determination_uuid=NEW.settings_basis
              AND NEW.authority_decision_reference=public.circle_decision(v_circle.parent_organism_id,d.determination_uuid,NEW.effective_at,NEW.recorded_at)
              AND NEW.authority_decision_reference=public.circle_decision(v_circle.parent_organism_id,d.determination_uuid,NEW.recorded_at,NEW.recorded_at)
              AND d.determination_uuid=public.circle_source(v_circle.parent_organism_id,v_circle.circle_uuid,
                  'ADMIT:'||v_request.request_uuid,NEW.deciding_identity_id,v_membership.identity_id,NEW.recorded_at,NEW.recorded_at)
              AND d.determination_uuid=public.circle_source(v_circle.parent_organism_id,v_circle.circle_uuid,
                  'ADMIT:'||v_request.request_uuid,NEW.deciding_identity_id,v_membership.identity_id,NEW.effective_at,NEW.recorded_at)) THEN
           RAISE EXCEPTION 'CIRCLE_ADMISSION_SOURCE' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF EXISTS(SELECT FROM public.core_circlejoinrequest WHERE id=NEW.request_id AND initiated_by='CIRCLE_MEMBER') THEN
       RAISE EXCEPTION 'CIRCLE_ADMISSION_ROUTE' USING ERRCODE='23514';
    END IF;
    SELECT q.* INTO v_request
      FROM public.core_circlejoinrequest q
     WHERE q.id = NEW.request_id;
    IF NOT FOUND
       OR NEW.seat_rule_uuid IS DISTINCT FROM v_request.seat_rule_uuid
       OR NEW.seat_id IS DISTINCT FROM v_request.seat_id THEN
        RAISE EXCEPTION 'SBG_CIRCLEDECISION_REQUEST_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.route NOT IN ('CIRCLE_FOUNDING', 'GOVERNANCE_CONSTITUTIONAL')
       OR (NEW.route = 'CIRCLE_FOUNDING'
           AND (NEW.constitutional_route <> 'NOT_APPLICABLE'
                OR NEW.deciding_party <> 'FOUNDING_BODY'))
       OR (NEW.route = 'GOVERNANCE_CONSTITUTIONAL'
           AND (NEW.constitutional_route <> 'APPOINTMENT'
                OR NEW.deciding_party <> 'office:LO_COORDINATOR'))
       OR NEW.outcome NOT IN ('ACCEPT', 'DECLINE')
       OR NEW.authority_decision_reference !~ '^s015d1:[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'SBG_CIRCLEDECISION_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT c.* INTO v_circle
      FROM public.core_circle c
     WHERE c.id = v_request.circle_id;
    SELECT m.* INTO v_membership
      FROM public.core_organismmembership m
     WHERE m.id = v_request.membership_id;
    IF NOT FOUND OR v_circle.parent_organism_id IS DISTINCT FROM
                         v_membership.living_organism_id
       OR NEW.self_decision IS DISTINCT FROM
                         (NEW.deciding_identity_id = v_membership.identity_id) THEN
        RAISE EXCEPTION 'SBG_CIRCLEDECISION_IDENTITY_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT r.* INTO v_rule
      FROM public.core_governedrule r
     WHERE r.scope_kind = 'LIVING_ORGANISM'
       AND r.scope_organism_id = v_circle.parent_organism_id
       AND r.code = 'GOVERNANCE_CONSTITUTION'
       AND r.rule_uuid = NEW.seat_rule_uuid
       AND r.effective_from <= NEW.effective_at
       AND r.recorded_at <= NEW.recorded_at
     ORDER BY r.effective_from DESC, r.recorded_at DESC, r.version DESC
     LIMIT 1;
    IF NOT FOUND OR NOT EXISTS (
        SELECT 1
          FROM jsonb_array_elements(v_rule.body->'seats') AS s(value)
         WHERE s.value->>'seat_id' = NEW.seat_id
           AND (s.value->>'effective_from')::timestamptz <= NEW.effective_at
    ) THEN
        RAISE EXCEPTION 'SBG_SEAT_ABSENT'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF (NEW.route='CIRCLE_FOUNDING') = EXISTS (
      SELECT FROM public.core_livingorganismevent b
      WHERE b.living_organism_id=v_circle.parent_organism_id AND b.action='BIRTHING'
        AND b.effective_at<=NEW.effective_at AND b.received_at<=NEW.received_at
        AND b.recorded_at<=NEW.received_at
    ) THEN
      RAISE EXCEPTION 'SBG_SEAT_ROUTE_UNAVAILABLE' USING ERRCODE='integrity_constraint_violation';
    END IF;
    IF NEW.route='GOVERNANCE_CONSTITUTIONAL' AND NOT EXISTS (
      SELECT FROM jsonb_array_elements(v_rule.body->'seats') AS seat(value)
      WHERE seat.value->>'seat_id'=NEW.seat_id
        AND (seat.value->>'effective_from')::timestamptz<=NEW.effective_at
        AND seat.value->>'admission_route'='APPOINTMENT'
        AND seat.value->>'deciding_body'='office:LO_COORDINATOR'
    ) THEN
      RAISE EXCEPTION 'SBG_SEAT_ROUTE_UNAVAILABLE' USING ERRCODE='integrity_constraint_violation';
    END IF;
    v_expected_settings_basis := 'rule:' || v_rule.rule_uuid::text
        || '@' || v_rule.version::text || '#' || NEW.seat_id;
    IF NEW.settings_basis IS DISTINCT FROM v_expected_settings_basis THEN
        RAISE EXCEPTION 'SBG_CIRCLEDECISION_SETTINGS_BASIS_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM'
       OR NEW.actor_id IS NOT NULL
       OR NEW.actor_access_epoch IS NOT NULL
       OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
       OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
       OR NEW.enterer_identity_id IS DISTINCT FROM NEW.deciding_identity_id
       OR NEW.composing_rule_state IS DISTINCT FROM 'NA_TRANSPORT'
       OR NEW.composing_rule_reference IS NOT NULL
       OR NEW.composer_state IS DISTINCT FROM 'NA_TRANSPORT'
       OR NEW.composer_identity_id IS NOT NULL THEN
        RAISE EXCEPTION 'SBG_CIRCLEDECISION_TRANSPORT_INVALID'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
"""

# public.sbg_guard_circleparticipation_occupancy()
# Its landed text: 0083_elect_board_continuity.py (assembled by that migration). Time calls moved: 1 wall.
SBG_GUARD_CIRCLEPARTICIPATION_OCCUPANCY_LANDED = r"""
DECLARE
    v_circle public.core_circle%ROWTYPE;
    v_membership public.core_organismmembership%ROWTYPE;
    v_rule public.core_governedrule%ROWTYPE;
    v_seat_occupied boolean;
    v_person_seated boolean;
    v_known_at timestamptz;
BEGIN

    IF NEW.recovery_office_id IS NOT NULL AND NOT EXISTS (
       SELECT FROM public.core_contextualroleassignment a JOIN public.core_contextualroleassignmentstateevent proposal
         ON proposal.assignment_id=a.id AND proposal.action='PROPOSE'
       JOIN public.core_contextualroleassignmentstateevent assigned ON assigned.predecessor_id=proposal.id
         AND assigned.assignment_id=a.id AND assigned.action='ASSIGN'
       JOIN public.core_contextualroleassignment issuer ON issuer.id=NEW.recovery_office_id
       WHERE a.membership_id=NEW.membership_id AND a.circle_id=NEW.circle_id
         AND proposal.evidence_reference='place:'||NEW.participation_uuid
         AND assigned.enterer_identity_id=NEW.enterer_identity_id
         AND split_part(assigned.evidence_reference,'|',2)=issuer.assignment_uuid::text
         AND split_part(assigned.evidence_reference,'|',7)='CB1') THEN
        RAISE EXCEPTION 'CIRCLE_RESEAT_INCOMPLETE' USING ERRCODE='23514';
    END IF;
    IF NEW.recovery_office_id IS NOT NULL OR NEW.birth_event_id IS NOT NULL OR EXISTS(SELECT FROM public.core_circleadmissiondecision WHERE id=NEW.decision_id AND route IN ('ORDINARY','APPLICATION_MODE')) THEN
        SELECT * INTO STRICT v_circle FROM public.core_circle WHERE id=NEW.circle_id;
        PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=v_circle.parent_organism_id)]);
        IF NOT public.circle_eligible(NEW.circle_id,NEW.membership_id,NEW.effective_at,NEW.recorded_at)
           OR EXISTS(SELECT FROM public.core_circleparticipation p
              JOIN public.core_organismmembership m ON m.id=p.membership_id
              WHERE p.circle_id=NEW.circle_id AND p.id<>NEW.id
                AND m.identity_id=(SELECT identity_id FROM public.core_organismmembership WHERE id=NEW.membership_id)
                AND NOT EXISTS(SELECT FROM public.core_circleparticipationend e WHERE e.participation_id=p.id
                    AND e.effective_at<=NEW.effective_at)) THEN
          RAISE EXCEPTION 'CIRCLE_PLACE_OCCUPIED_OR_INELIGIBLE' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    END IF;
    PERFORM pg_advisory_xact_lock(
        1396852529,
        hashtext(NEW.seat_rule_uuid::text || '#' || NEW.seat_id)
    );
    SELECT c.* INTO v_circle
      FROM public.core_circle c
     WHERE c.id = NEW.circle_id;
    SELECT m.* INTO v_membership
      FROM public.core_organismmembership m
     WHERE m.id = NEW.membership_id;
    IF NOT FOUND
       OR v_circle.parent_organism_id IS DISTINCT FROM
            v_membership.living_organism_id THEN
        RAISE EXCEPTION 'SBG_MEMBER_NOT_ELIGIBLE'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    PERFORM pg_advisory_xact_lock(
        1396852529,
        hashtext(
            'person#' || NEW.circle_id::text || '#' || v_membership.identity_id::text
        )
    );
    v_known_at := clock_timestamp();
    IF public.s015_fold_effective_state(
            'core_organismmembership', NEW.membership_id,
            NEW.effective_at, NEW.recorded_at
       ) IS DISTINCT FROM 'ACTIVE' THEN
        RAISE EXCEPTION 'SBG_MEMBER_NOT_ELIGIBLE'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT r.* INTO v_rule
      FROM public.core_governedrule r
     WHERE r.scope_kind = 'LIVING_ORGANISM'
       AND r.scope_organism_id = v_circle.parent_organism_id
       AND r.code = 'GOVERNANCE_CONSTITUTION'
       AND r.rule_uuid = NEW.seat_rule_uuid
       AND r.effective_from <= NEW.effective_at
       AND r.recorded_at <= NEW.recorded_at
     ORDER BY r.effective_from DESC, r.recorded_at DESC, r.version DESC
     LIMIT 1;
    IF NOT FOUND OR NOT EXISTS (
        SELECT 1
          FROM jsonb_array_elements(v_rule.body->'seats') AS s(value)
         WHERE s.value->>'seat_id' = NEW.seat_id
           AND (s.value->>'effective_from')::timestamptz <= NEW.effective_at
    ) THEN
        RAISE EXCEPTION 'SBG_SEAT_ABSENT'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM public.core_governedrule future_rule
         WHERE future_rule.scope_kind = 'LIVING_ORGANISM'
           AND future_rule.scope_organism_id = v_circle.parent_organism_id
           AND future_rule.code = 'GOVERNANCE_CONSTITUTION'
           AND future_rule.rule_uuid = NEW.seat_rule_uuid
           AND future_rule.effective_from > NEW.effective_at
           AND future_rule.recorded_at <= NEW.recorded_at
           AND NOT EXISTS (
                SELECT 1
                  FROM jsonb_array_elements(future_rule.body->'seats') AS s(value)
                 WHERE s.value->>'seat_id' = NEW.seat_id
           )
    ) THEN
        RAISE EXCEPTION 'SBG_SEAT_ABSENT'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT EXISTS (
        SELECT 1
          FROM public.core_circleparticipation p
         WHERE p.circle_id = NEW.circle_id
           AND p.id <> NEW.id
           AND p.seat_rule_uuid = NEW.seat_rule_uuid
           AND p.seat_id = NEW.seat_id
           AND p.recorded_at <= v_known_at

           AND (public.elect_place_end(p.id,v_known_at) IS NULL
                OR public.elect_place_end(p.id,v_known_at)>NEW.effective_at)
           AND (public.elect_place_end(NEW.id,v_known_at) IS NULL
                OR p.effective_at<public.elect_place_end(NEW.id,v_known_at))
           AND public.elect_place_end(p.id,v_known_at) IS DISTINCT FROM p.effective_at
    ) INTO v_seat_occupied;
    IF v_seat_occupied THEN
        RAISE EXCEPTION 'SBG_SEAT_OCCUPIED'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT EXISTS (
        SELECT 1
          FROM public.core_circleparticipation p
          JOIN public.core_organismmembership m ON m.id = p.membership_id
         WHERE p.circle_id = NEW.circle_id
           AND p.id <> NEW.id
           AND m.identity_id = v_membership.identity_id
           AND p.recorded_at <= v_known_at

           AND (public.elect_place_end(p.id,v_known_at) IS NULL
                OR public.elect_place_end(p.id,v_known_at)>NEW.effective_at)
           AND (public.elect_place_end(NEW.id,v_known_at) IS NULL
                OR p.effective_at<public.elect_place_end(NEW.id,v_known_at))
           AND public.elect_place_end(p.id,v_known_at) IS DISTINCT FROM p.effective_at
    ) INTO v_person_seated;
    IF v_person_seated THEN
        RAISE EXCEPTION 'SBG_ALREADY_SEATED'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
"""
SBG_GUARD_CIRCLEPARTICIPATION_OCCUPANCY_CLOCK = r"""
DECLARE
    v_circle public.core_circle%ROWTYPE;
    v_membership public.core_organismmembership%ROWTYPE;
    v_rule public.core_governedrule%ROWTYPE;
    v_seat_occupied boolean;
    v_person_seated boolean;
    v_known_at timestamptz;
BEGIN

    IF NEW.recovery_office_id IS NOT NULL AND NOT EXISTS (
       SELECT FROM public.core_contextualroleassignment a JOIN public.core_contextualroleassignmentstateevent proposal
         ON proposal.assignment_id=a.id AND proposal.action='PROPOSE'
       JOIN public.core_contextualroleassignmentstateevent assigned ON assigned.predecessor_id=proposal.id
         AND assigned.assignment_id=a.id AND assigned.action='ASSIGN'
       JOIN public.core_contextualroleassignment issuer ON issuer.id=NEW.recovery_office_id
       WHERE a.membership_id=NEW.membership_id AND a.circle_id=NEW.circle_id
         AND proposal.evidence_reference='place:'||NEW.participation_uuid
         AND assigned.enterer_identity_id=NEW.enterer_identity_id
         AND split_part(assigned.evidence_reference,'|',2)=issuer.assignment_uuid::text
         AND split_part(assigned.evidence_reference,'|',7)='CB1') THEN
        RAISE EXCEPTION 'CIRCLE_RESEAT_INCOMPLETE' USING ERRCODE='23514';
    END IF;
    IF NEW.recovery_office_id IS NOT NULL OR NEW.birth_event_id IS NOT NULL OR EXISTS(SELECT FROM public.core_circleadmissiondecision WHERE id=NEW.decision_id AND route IN ('ORDINARY','APPLICATION_MODE')) THEN
        SELECT * INTO STRICT v_circle FROM public.core_circle WHERE id=NEW.circle_id;
        PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=v_circle.parent_organism_id)]);
        IF NOT public.circle_eligible(NEW.circle_id,NEW.membership_id,NEW.effective_at,NEW.recorded_at)
           OR EXISTS(SELECT FROM public.core_circleparticipation p
              JOIN public.core_organismmembership m ON m.id=p.membership_id
              WHERE p.circle_id=NEW.circle_id AND p.id<>NEW.id
                AND m.identity_id=(SELECT identity_id FROM public.core_organismmembership WHERE id=NEW.membership_id)
                AND NOT EXISTS(SELECT FROM public.core_circleparticipationend e WHERE e.participation_id=p.id
                    AND e.effective_at<=NEW.effective_at)) THEN
          RAISE EXCEPTION 'CIRCLE_PLACE_OCCUPIED_OR_INELIGIBLE' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    END IF;
    PERFORM pg_advisory_xact_lock(
        1396852529,
        hashtext(NEW.seat_rule_uuid::text || '#' || NEW.seat_id)
    );
    SELECT c.* INTO v_circle
      FROM public.core_circle c
     WHERE c.id = NEW.circle_id;
    SELECT m.* INTO v_membership
      FROM public.core_organismmembership m
     WHERE m.id = NEW.membership_id;
    IF NOT FOUND
       OR v_circle.parent_organism_id IS DISTINCT FROM
            v_membership.living_organism_id THEN
        RAISE EXCEPTION 'SBG_MEMBER_NOT_ELIGIBLE'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    PERFORM pg_advisory_xact_lock(
        1396852529,
        hashtext(
            'person#' || NEW.circle_id::text || '#' || v_membership.identity_id::text
        )
    );
    v_known_at := public.intevia_clock_instant();
    IF public.s015_fold_effective_state(
            'core_organismmembership', NEW.membership_id,
            NEW.effective_at, NEW.recorded_at
       ) IS DISTINCT FROM 'ACTIVE' THEN
        RAISE EXCEPTION 'SBG_MEMBER_NOT_ELIGIBLE'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT r.* INTO v_rule
      FROM public.core_governedrule r
     WHERE r.scope_kind = 'LIVING_ORGANISM'
       AND r.scope_organism_id = v_circle.parent_organism_id
       AND r.code = 'GOVERNANCE_CONSTITUTION'
       AND r.rule_uuid = NEW.seat_rule_uuid
       AND r.effective_from <= NEW.effective_at
       AND r.recorded_at <= NEW.recorded_at
     ORDER BY r.effective_from DESC, r.recorded_at DESC, r.version DESC
     LIMIT 1;
    IF NOT FOUND OR NOT EXISTS (
        SELECT 1
          FROM jsonb_array_elements(v_rule.body->'seats') AS s(value)
         WHERE s.value->>'seat_id' = NEW.seat_id
           AND (s.value->>'effective_from')::timestamptz <= NEW.effective_at
    ) THEN
        RAISE EXCEPTION 'SBG_SEAT_ABSENT'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM public.core_governedrule future_rule
         WHERE future_rule.scope_kind = 'LIVING_ORGANISM'
           AND future_rule.scope_organism_id = v_circle.parent_organism_id
           AND future_rule.code = 'GOVERNANCE_CONSTITUTION'
           AND future_rule.rule_uuid = NEW.seat_rule_uuid
           AND future_rule.effective_from > NEW.effective_at
           AND future_rule.recorded_at <= NEW.recorded_at
           AND NOT EXISTS (
                SELECT 1
                  FROM jsonb_array_elements(future_rule.body->'seats') AS s(value)
                 WHERE s.value->>'seat_id' = NEW.seat_id
           )
    ) THEN
        RAISE EXCEPTION 'SBG_SEAT_ABSENT'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT EXISTS (
        SELECT 1
          FROM public.core_circleparticipation p
         WHERE p.circle_id = NEW.circle_id
           AND p.id <> NEW.id
           AND p.seat_rule_uuid = NEW.seat_rule_uuid
           AND p.seat_id = NEW.seat_id
           AND p.recorded_at <= v_known_at

           AND (public.elect_place_end(p.id,v_known_at) IS NULL
                OR public.elect_place_end(p.id,v_known_at)>NEW.effective_at)
           AND (public.elect_place_end(NEW.id,v_known_at) IS NULL
                OR p.effective_at<public.elect_place_end(NEW.id,v_known_at))
           AND public.elect_place_end(p.id,v_known_at) IS DISTINCT FROM p.effective_at
    ) INTO v_seat_occupied;
    IF v_seat_occupied THEN
        RAISE EXCEPTION 'SBG_SEAT_OCCUPIED'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT EXISTS (
        SELECT 1
          FROM public.core_circleparticipation p
          JOIN public.core_organismmembership m ON m.id = p.membership_id
         WHERE p.circle_id = NEW.circle_id
           AND p.id <> NEW.id
           AND m.identity_id = v_membership.identity_id
           AND p.recorded_at <= v_known_at

           AND (public.elect_place_end(p.id,v_known_at) IS NULL
                OR public.elect_place_end(p.id,v_known_at)>NEW.effective_at)
           AND (public.elect_place_end(NEW.id,v_known_at) IS NULL
                OR p.effective_at<public.elect_place_end(NEW.id,v_known_at))
           AND public.elect_place_end(p.id,v_known_at) IS DISTINCT FROM p.effective_at
    ) INTO v_person_seated;
    IF v_person_seated THEN
        RAISE EXCEPTION 'SBG_ALREADY_SEATED'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
"""

# public.sbg_guard_membership_end_completeness()
# Its landed text: 0083_elect_board_continuity.py (assembled by that migration). Time calls moved: 1 wall.
SBG_GUARD_MEMBERSHIP_END_COMPLETENESS_LANDED = r"""
DECLARE
    v_incomplete boolean;
    v_future_seat_id text;
    v_future_start text;
BEGIN
    IF NEW.resulting_state IS DISTINCT FROM 'ENDED' THEN
        RETURN NEW;
    END IF;
    SELECT EXISTS (
        SELECT 1
          FROM public.core_circleparticipation p
         WHERE p.membership_id = NEW.membership_id
           AND (
                (p.effective_at > NEW.effective_at AND NOT public.elect_pending_successor(p.participation_uuid,NEW.membership_id,'SEAT',NEW.effective_at,NEW.received_at))
                OR NOT EXISTS (
                    SELECT 1
                      FROM public.core_circleparticipationend e
                     WHERE e.participation_id = p.id
                       AND (e.effective_at <= NEW.effective_at OR (public.elect_pending_successor(p.participation_uuid,NEW.membership_id,'SEAT',NEW.effective_at,NEW.received_at)
                       AND e.effective_at=p.effective_at AND e.composed_from_event_uuid=NEW.event_uuid))
                )
           )
    ) INTO v_incomplete;
    IF v_incomplete THEN
        SELECT p.seat_id,
               to_char(
                   p.effective_at AT TIME ZONE 'UTC',
                   'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
               )
          INTO v_future_seat_id, v_future_start
          FROM public.core_circleparticipation p
         WHERE p.membership_id = NEW.membership_id
           AND p.effective_at > NEW.effective_at
           AND NOT EXISTS (
                SELECT 1
                  FROM public.core_circleparticipationend e
                 WHERE e.participation_id = p.id
                   AND (e.effective_at <= NEW.effective_at OR (public.elect_pending_successor(p.participation_uuid,NEW.membership_id,'SEAT',NEW.effective_at,NEW.received_at)
                       AND e.effective_at=p.effective_at AND e.composed_from_event_uuid=NEW.event_uuid))
           )
         ORDER BY p.effective_at, p.recorded_at, p.id
         LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'SBG_DEPARTURE_PRECEDES_PARTICIPATION'
                USING ERRCODE = 'integrity_constraint_violation',
                      DETAIL = json_build_object(
                          'seat_id', v_future_seat_id,
                          'started_at', v_future_start
                      )::text;
        END IF;
        RAISE EXCEPTION 'SBG_MEMBERSHIP_END_PARTICIPATION_INCOMPLETE'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NEW.actor_state='PARTY' AND NEW.prior_state IN ('ACTIVE','PROBATIONARY') THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.core_authoritydecision d
            JOIN public.core_organismmembership m ON m.id=NEW.membership_id
            JOIN public.core_commandintake i ON i.actor_id=d.actor_id AND i.action=d.action
                                             AND i.idempotency_key=d.idempotency_key
            WHERE d.decision_reference=NEW.authority_decision_reference AND d.outcome='QUALIFIED'
              AND NEW.action='TRANSITION_MEMBERSHIP' AND d.action=NEW.action AND d.command_subtype=NEW.action
              AND NEW.actor_id=m.identity_id AND d.actor_id=NEW.actor_id AND i.entry=NEW.action
              AND i.request_reference=NEW.request_reference AND i.received_at=NEW.received_at
              AND i.request_fingerprint=d.target->>'intake_fingerprint'
              AND i.actor_access_epoch=NEW.actor_access_epoch AND d.actor_access_epoch=NEW.actor_access_epoch
              AND d.request_reference=NEW.request_reference AND d.idempotency_key=NEW.idempotency_key
              AND d.effective_at=NEW.effective_at AND d.evaluated_at=NEW.received_at
              AND d.target->>'membership_uuid'=m.membership_uuid::text AND d.target->>'resulting_state'='ENDED'
              AND d.target->'confirmed'='true'::jsonb AND d.authority_reference=NEW.evidence_reference
        ) THEN
            RAISE EXCEPTION 'C1B_DEPARTURE_CONFIRMATION_REQUIRED' USING ERRCODE='integrity_constraint_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM public.core_contextualroleassignment a
                   JOIN public.core_contextualroleassignmentstateevent h ON h.id=a.head_state_event_id
                   WHERE a.membership_id=NEW.membership_id AND
                       (h.resulting_state NOT IN ('PROPOSED','ENDED') OR h.effective_at>NEW.effective_at)
                     AND NOT (h.resulting_state='ENDED' AND h.composing_rule_reference IS NOT DISTINCT FROM 'ELECT:SUCCESSOR_LEFT'
                       AND h.evidence_reference LIKE 'elect-departure:'||NEW.event_uuid||':%')) THEN
            RAISE EXCEPTION 'C1B_DEPARTURE_OFFICE_INCOMPLETE' USING ERRCODE='integrity_constraint_violation';
        END IF;
        IF EXISTS (
            SELECT 1 FROM public.core_membershipcondition c
            JOIN public.c1b_condition_period b ON b.condition_id=c.id
            JOIN public.core_membershipconditionstateevent h ON h.id=c.head_state_event_id
            WHERE (c.subject_membership_id=NEW.membership_id
                   AND b.subject_start=public.c1b_period_at(NEW.membership_id,NEW.effective_at,NEW.received_at)
                   AND h.resulting_state NOT IN ('ENDED','SUPERSEDED','DECLINED','OFFER_CLOSED'))
               OR (c.mentor_membership_id=NEW.membership_id
                   AND b.mentor_start=public.c1b_period_at(NEW.membership_id,NEW.effective_at,NEW.received_at)
                   AND (h.resulting_state NOT IN ('ENDED','SUPERSEDED','DECLINED','OFFER_CLOSED','MENTOR_UNRESOLVED')
                        OR (h.prior_state='OFFERED' AND h.resulting_state='MENTOR_UNRESOLVED')))
        ) THEN
            RAISE EXCEPTION 'C1B_DEPARTURE_CONDITION_INCOMPLETE' USING ERRCODE='integrity_constraint_violation';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.core_organismmembership other JOIN public.core_organismmembership m
              ON m.living_organism_id=other.living_organism_id WHERE m.id=NEW.membership_id AND other.id<>m.id
              AND public.s015_fold_effective_state('core_organismmembership',other.id,NEW.effective_at,
                                                    clock_timestamp()) IN ('ACTIVE','PROBATIONARY')
        ) AND NOT EXISTS (
            SELECT 1 FROM public.core_livingorganismevent e JOIN public.core_organismmembership m
              ON m.living_organism_id=e.living_organism_id WHERE m.id=NEW.membership_id
              AND e.resulting_state='CLOSED' AND e.effective_at=NEW.effective_at
              AND e.evidence_reference='membership-ending:'||NEW.event_uuid::text
        ) THEN
            RAISE EXCEPTION 'C1B_DEPARTURE_DISSOLUTION_INCOMPLETE' USING ERRCODE='integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END
"""
SBG_GUARD_MEMBERSHIP_END_COMPLETENESS_CLOCK = r"""
DECLARE
    v_incomplete boolean;
    v_future_seat_id text;
    v_future_start text;
BEGIN
    IF NEW.resulting_state IS DISTINCT FROM 'ENDED' THEN
        RETURN NEW;
    END IF;
    SELECT EXISTS (
        SELECT 1
          FROM public.core_circleparticipation p
         WHERE p.membership_id = NEW.membership_id
           AND (
                (p.effective_at > NEW.effective_at AND NOT public.elect_pending_successor(p.participation_uuid,NEW.membership_id,'SEAT',NEW.effective_at,NEW.received_at))
                OR NOT EXISTS (
                    SELECT 1
                      FROM public.core_circleparticipationend e
                     WHERE e.participation_id = p.id
                       AND (e.effective_at <= NEW.effective_at OR (public.elect_pending_successor(p.participation_uuid,NEW.membership_id,'SEAT',NEW.effective_at,NEW.received_at)
                       AND e.effective_at=p.effective_at AND e.composed_from_event_uuid=NEW.event_uuid))
                )
           )
    ) INTO v_incomplete;
    IF v_incomplete THEN
        SELECT p.seat_id,
               to_char(
                   p.effective_at AT TIME ZONE 'UTC',
                   'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
               )
          INTO v_future_seat_id, v_future_start
          FROM public.core_circleparticipation p
         WHERE p.membership_id = NEW.membership_id
           AND p.effective_at > NEW.effective_at
           AND NOT EXISTS (
                SELECT 1
                  FROM public.core_circleparticipationend e
                 WHERE e.participation_id = p.id
                   AND (e.effective_at <= NEW.effective_at OR (public.elect_pending_successor(p.participation_uuid,NEW.membership_id,'SEAT',NEW.effective_at,NEW.received_at)
                       AND e.effective_at=p.effective_at AND e.composed_from_event_uuid=NEW.event_uuid))
           )
         ORDER BY p.effective_at, p.recorded_at, p.id
         LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'SBG_DEPARTURE_PRECEDES_PARTICIPATION'
                USING ERRCODE = 'integrity_constraint_violation',
                      DETAIL = json_build_object(
                          'seat_id', v_future_seat_id,
                          'started_at', v_future_start
                      )::text;
        END IF;
        RAISE EXCEPTION 'SBG_MEMBERSHIP_END_PARTICIPATION_INCOMPLETE'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NEW.actor_state='PARTY' AND NEW.prior_state IN ('ACTIVE','PROBATIONARY') THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.core_authoritydecision d
            JOIN public.core_organismmembership m ON m.id=NEW.membership_id
            JOIN public.core_commandintake i ON i.actor_id=d.actor_id AND i.action=d.action
                                             AND i.idempotency_key=d.idempotency_key
            WHERE d.decision_reference=NEW.authority_decision_reference AND d.outcome='QUALIFIED'
              AND NEW.action='TRANSITION_MEMBERSHIP' AND d.action=NEW.action AND d.command_subtype=NEW.action
              AND NEW.actor_id=m.identity_id AND d.actor_id=NEW.actor_id AND i.entry=NEW.action
              AND i.request_reference=NEW.request_reference AND i.received_at=NEW.received_at
              AND i.request_fingerprint=d.target->>'intake_fingerprint'
              AND i.actor_access_epoch=NEW.actor_access_epoch AND d.actor_access_epoch=NEW.actor_access_epoch
              AND d.request_reference=NEW.request_reference AND d.idempotency_key=NEW.idempotency_key
              AND d.effective_at=NEW.effective_at AND d.evaluated_at=NEW.received_at
              AND d.target->>'membership_uuid'=m.membership_uuid::text AND d.target->>'resulting_state'='ENDED'
              AND d.target->'confirmed'='true'::jsonb AND d.authority_reference=NEW.evidence_reference
        ) THEN
            RAISE EXCEPTION 'C1B_DEPARTURE_CONFIRMATION_REQUIRED' USING ERRCODE='integrity_constraint_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM public.core_contextualroleassignment a
                   JOIN public.core_contextualroleassignmentstateevent h ON h.id=a.head_state_event_id
                   WHERE a.membership_id=NEW.membership_id AND
                       (h.resulting_state NOT IN ('PROPOSED','ENDED') OR h.effective_at>NEW.effective_at)
                     AND NOT (h.resulting_state='ENDED' AND h.composing_rule_reference IS NOT DISTINCT FROM 'ELECT:SUCCESSOR_LEFT'
                       AND h.evidence_reference LIKE 'elect-departure:'||NEW.event_uuid||':%')) THEN
            RAISE EXCEPTION 'C1B_DEPARTURE_OFFICE_INCOMPLETE' USING ERRCODE='integrity_constraint_violation';
        END IF;
        IF EXISTS (
            SELECT 1 FROM public.core_membershipcondition c
            JOIN public.c1b_condition_period b ON b.condition_id=c.id
            JOIN public.core_membershipconditionstateevent h ON h.id=c.head_state_event_id
            WHERE (c.subject_membership_id=NEW.membership_id
                   AND b.subject_start=public.c1b_period_at(NEW.membership_id,NEW.effective_at,NEW.received_at)
                   AND h.resulting_state NOT IN ('ENDED','SUPERSEDED','DECLINED','OFFER_CLOSED'))
               OR (c.mentor_membership_id=NEW.membership_id
                   AND b.mentor_start=public.c1b_period_at(NEW.membership_id,NEW.effective_at,NEW.received_at)
                   AND (h.resulting_state NOT IN ('ENDED','SUPERSEDED','DECLINED','OFFER_CLOSED','MENTOR_UNRESOLVED')
                        OR (h.prior_state='OFFERED' AND h.resulting_state='MENTOR_UNRESOLVED')))
        ) THEN
            RAISE EXCEPTION 'C1B_DEPARTURE_CONDITION_INCOMPLETE' USING ERRCODE='integrity_constraint_violation';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.core_organismmembership other JOIN public.core_organismmembership m
              ON m.living_organism_id=other.living_organism_id WHERE m.id=NEW.membership_id AND other.id<>m.id
              AND public.s015_fold_effective_state('core_organismmembership',other.id,NEW.effective_at,
                                                    public.intevia_clock_instant()) IN ('ACTIVE','PROBATIONARY')
        ) AND NOT EXISTS (
            SELECT 1 FROM public.core_livingorganismevent e JOIN public.core_organismmembership m
              ON m.living_organism_id=e.living_organism_id WHERE m.id=NEW.membership_id
              AND e.resulting_state='CLOSED' AND e.effective_at=NEW.effective_at
              AND e.evidence_reference='membership-ending:'||NEW.event_uuid::text
        ) THEN
            RAISE EXCEPTION 'C1B_DEPARTURE_DISSOLUTION_INCOMPLETE' USING ERRCODE='integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END
"""

# public.cq4_checked(bigint, uuid, jsonb, timestamp with time zone)
# Its landed text: 0085_board_voting.py (assembled by that migration). Time calls moved: 1 wall.
CQ4_CHECKED_LANDED = r"""
DECLARE checked jsonb; owner_uuid uuid; executed timestamptz;
BEGIN
 IF p_patch->'changes' ? 'reporting_scope'
    AND p_patch->'changes'->'reporting_scope' IS DISTINCT FROM coalesce(
      (SELECT a.facts->'settings'->'reporting_scope' FROM public.core_cqact a
       WHERE a.organism_id=p_lo AND a.operation='SETTINGS' ORDER BY a.ordinal DESC LIMIT 1),
      public.cq_settings_at(p_lo,p_cut)->'reporting_scope') THEN
   PERFORM public.cq_refuse('CQ_REPORT_SCOPE'); END IF;
 checked:=public.cq_checked_settings(p_lo,p_key,p_patch,p_cut);
 IF (p_patch->'changes') ? 'provisions' THEN
   SELECT lo.organism_id INTO STRICT owner_uuid FROM public.core_livingorganism lo WHERE lo.id=p_lo;
   IF EXISTS(SELECT FROM jsonb_array_elements(checked->'previous'->'provisions') old_law
       WHERE public.cq_law_current(owner_uuid,old_law,p_cut)
       AND NOT EXISTS(SELECT FROM jsonb_array_elements(checked->'settings'->'provisions') new_law
         WHERE new_law-ARRAY['decision','body']=old_law-ARRAY['decision','body']
           AND new_law->>'decision' IN (old_law->>'decision','ALL_DECISIONS')
           AND new_law->>'body' IN (old_law->>'body','ALL_BOARDS'))) THEN
     PERFORM public.cq_refuse('CQ_SETTINGS_PROVISION'); END IF;
 END IF;

 IF (checked->'settings'->>'enabled')::boolean IS FALSE THEN
   executed:=clock_timestamp();
   IF EXISTS(SELECT FROM public.core_livingorganism lo WHERE lo.id=p_lo AND NOT lo.platform_root)
      AND (public.cq3_board(p_lo,p_cut,p_cut) IS NOT NULL OR
           public.cq3_board(p_lo,executed,executed) IS NOT NULL) THEN
     PERFORM public.cq_refuse('CQ_BOARD_ACTIVE');
   END IF;
 END IF;
 RETURN checked;
END """
CQ4_CHECKED_CLOCK = r"""
DECLARE checked jsonb; owner_uuid uuid; executed timestamptz;
BEGIN
 IF p_patch->'changes' ? 'reporting_scope'
    AND p_patch->'changes'->'reporting_scope' IS DISTINCT FROM coalesce(
      (SELECT a.facts->'settings'->'reporting_scope' FROM public.core_cqact a
       WHERE a.organism_id=p_lo AND a.operation='SETTINGS' ORDER BY a.ordinal DESC LIMIT 1),
      public.cq_settings_at(p_lo,p_cut)->'reporting_scope') THEN
   PERFORM public.cq_refuse('CQ_REPORT_SCOPE'); END IF;
 checked:=public.cq_checked_settings(p_lo,p_key,p_patch,p_cut);
 IF (p_patch->'changes') ? 'provisions' THEN
   SELECT lo.organism_id INTO STRICT owner_uuid FROM public.core_livingorganism lo WHERE lo.id=p_lo;
   IF EXISTS(SELECT FROM jsonb_array_elements(checked->'previous'->'provisions') old_law
       WHERE public.cq_law_current(owner_uuid,old_law,p_cut)
       AND NOT EXISTS(SELECT FROM jsonb_array_elements(checked->'settings'->'provisions') new_law
         WHERE new_law-ARRAY['decision','body']=old_law-ARRAY['decision','body']
           AND new_law->>'decision' IN (old_law->>'decision','ALL_DECISIONS')
           AND new_law->>'body' IN (old_law->>'body','ALL_BOARDS'))) THEN
     PERFORM public.cq_refuse('CQ_SETTINGS_PROVISION'); END IF;
 END IF;

 IF (checked->'settings'->>'enabled')::boolean IS FALSE THEN
   executed:=public.intevia_clock_instant();
   IF EXISTS(SELECT FROM public.core_livingorganism lo WHERE lo.id=p_lo AND NOT lo.platform_root)
      AND (public.cq3_board(p_lo,p_cut,p_cut) IS NOT NULL OR
           public.cq3_board(p_lo,executed,executed) IS NOT NULL) THEN
     PERFORM public.cq_refuse('CQ_BOARD_ACTIVE');
   END IF;
 END IF;
 RETURN checked;
END """

# public.cq_execute(uuid, uuid, uuid, text, uuid, jsonb, bigint, bigint, timestamp with time zone)
# Its landed text: 0085_board_voting.py (assembled by that migration). Time calls moved: 2 statement.
CQ_EXECUTE_LANDED = r"""
DECLARE v_lo bigint; v_old public.core_cqact; v_existing_board uuid; written text;
 v_constraint text; v_table text; v_schema text;
BEGIN
 SELECT id INTO v_lo FROM public.core_livingorganism WHERE organism_id=p_owner;
 IF v_lo IS NULL THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 PERFORM public.cq_source(v_lo,p_board,p_thread,p_op,p_actor,statement_timestamp());
 IF p_op='ANSWER' THEN PERFORM public.cq_answer_eligible(p_board,p_thread,p_args->>'ballot',p_actor,statement_timestamp()); END IF;
 SELECT * INTO v_old FROM public.core_cqact WHERE act_uuid=p_key;
 IF FOUND THEN
   IF v_old.operation='REFUSE_VOTING_OFF' THEN
     IF v_old.organism_id<>v_lo OR v_old.actor_id<>p_actor
        OR v_old.request->>'board' IS DISTINCT FROM p_board::text THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
     IF v_old.actor_epoch<>p_epoch OR v_old.request IS DISTINCT FROM jsonb_build_object('operation',p_op,
       'board',p_board,'thread',p_thread,'request_fingerprint',public.disc_fingerprint(p_args))
       THEN PERFORM public.cq_refuse('CQ_REQUEST'); END IF;
     RETURN NULL;
   END IF;
   IF v_old.organism_id<>v_lo OR v_old.board_uuid IS DISTINCT FROM p_board THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
   IF p_op IN ('RECOGNITION_CONSENT','RECOGNISE','BOARD_RESIGN') THEN
     IF v_old.actor_id IS DISTINCT FROM p_actor THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
     IF v_old.operation<>p_op OR v_old.request IS DISTINCT FROM p_args OR v_old.actor_epoch<>p_epoch
       THEN PERFORM public.cq_refuse('ELECT_CONT_REQUEST'); END IF;
   END IF;
   IF v_old.operation='ANSWER' AND v_old.actor_id<>p_actor AND EXISTS(
      SELECT FROM public.core_electchoice ec WHERE ec.receipt=v_old.act_uuid) THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
   IF v_old.thread_uuid IS DISTINCT FROM p_thread OR v_old.operation<>p_op OR v_old.actor_id<>p_actor
      OR v_old.actor_epoch<>p_epoch OR (CASE WHEN p_op='ANSWER' AND EXISTS(
       SELECT FROM public.core_electchoice ec WHERE ec.receipt=v_old.act_uuid)
       THEN v_old.request||jsonb_build_object('choice',(SELECT ec.choice FROM public.core_electchoice ec WHERE ec.receipt=v_old.act_uuid))
       ELSE v_old.request END) IS DISTINCT FROM p_args THEN PERFORM public.cq_refuse('CQ_REQUEST'); END IF;
   RETURN p_key;
 END IF;
 SELECT b.board_uuid INTO v_existing_board FROM (
   SELECT act_uuid,board_uuid FROM public.core_discact UNION ALL SELECT act_uuid,board_uuid FROM public.core_discpostact
 ) b WHERE b.act_uuid=p_key;
 IF v_existing_board IS NOT NULL AND v_existing_board=p_board THEN PERFORM public.cq_refuse('CQ_REQUEST'); END IF;
 IF EXISTS(SELECT FROM public.core_livingorganismevent WHERE event_uuid=p_key) THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
 VALUES(p_key,v_lo,p_board,p_thread,p_op,p_actor,p_epoch,p_received,p_args) RETURNING operation INTO written;
 IF written IS NULL OR written='REFUSE_VOTING_OFF' THEN RETURN NULL; END IF;
 RETURN p_key;
EXCEPTION WHEN unique_violation THEN
 GET STACKED DIAGNOSTICS v_constraint=CONSTRAINT_NAME,v_table=TABLE_NAME,v_schema=SCHEMA_NAME;
 IF v_schema='public' AND v_table='core_livingorganismevent' AND EXISTS(SELECT FROM pg_constraint c
   WHERE c.conrelid='public.core_livingorganismevent'::regclass AND c.conname=v_constraint AND c.contype='u'
     AND c.conkey=ARRAY[(SELECT a.attnum FROM pg_attribute a WHERE a.attrelid=c.conrelid AND a.attname='event_uuid')])
 THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 RAISE;
END """
CQ_EXECUTE_CLOCK = r"""
DECLARE v_lo bigint; v_old public.core_cqact; v_existing_board uuid; written text;
 v_constraint text; v_table text; v_schema text;
BEGIN
 SELECT id INTO v_lo FROM public.core_livingorganism WHERE organism_id=p_owner;
 IF v_lo IS NULL THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 PERFORM public.cq_source(v_lo,p_board,p_thread,p_op,p_actor,public.intevia_clock_statement());
 IF p_op='ANSWER' THEN PERFORM public.cq_answer_eligible(p_board,p_thread,p_args->>'ballot',p_actor,public.intevia_clock_statement()); END IF;
 SELECT * INTO v_old FROM public.core_cqact WHERE act_uuid=p_key;
 IF FOUND THEN
   IF v_old.operation='REFUSE_VOTING_OFF' THEN
     IF v_old.organism_id<>v_lo OR v_old.actor_id<>p_actor
        OR v_old.request->>'board' IS DISTINCT FROM p_board::text THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
     IF v_old.actor_epoch<>p_epoch OR v_old.request IS DISTINCT FROM jsonb_build_object('operation',p_op,
       'board',p_board,'thread',p_thread,'request_fingerprint',public.disc_fingerprint(p_args))
       THEN PERFORM public.cq_refuse('CQ_REQUEST'); END IF;
     RETURN NULL;
   END IF;
   IF v_old.organism_id<>v_lo OR v_old.board_uuid IS DISTINCT FROM p_board THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
   IF p_op IN ('RECOGNITION_CONSENT','RECOGNISE','BOARD_RESIGN') THEN
     IF v_old.actor_id IS DISTINCT FROM p_actor THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
     IF v_old.operation<>p_op OR v_old.request IS DISTINCT FROM p_args OR v_old.actor_epoch<>p_epoch
       THEN PERFORM public.cq_refuse('ELECT_CONT_REQUEST'); END IF;
   END IF;
   IF v_old.operation='ANSWER' AND v_old.actor_id<>p_actor AND EXISTS(
      SELECT FROM public.core_electchoice ec WHERE ec.receipt=v_old.act_uuid) THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
   IF v_old.thread_uuid IS DISTINCT FROM p_thread OR v_old.operation<>p_op OR v_old.actor_id<>p_actor
      OR v_old.actor_epoch<>p_epoch OR (CASE WHEN p_op='ANSWER' AND EXISTS(
       SELECT FROM public.core_electchoice ec WHERE ec.receipt=v_old.act_uuid)
       THEN v_old.request||jsonb_build_object('choice',(SELECT ec.choice FROM public.core_electchoice ec WHERE ec.receipt=v_old.act_uuid))
       ELSE v_old.request END) IS DISTINCT FROM p_args THEN PERFORM public.cq_refuse('CQ_REQUEST'); END IF;
   RETURN p_key;
 END IF;
 SELECT b.board_uuid INTO v_existing_board FROM (
   SELECT act_uuid,board_uuid FROM public.core_discact UNION ALL SELECT act_uuid,board_uuid FROM public.core_discpostact
 ) b WHERE b.act_uuid=p_key;
 IF v_existing_board IS NOT NULL AND v_existing_board=p_board THEN PERFORM public.cq_refuse('CQ_REQUEST'); END IF;
 IF EXISTS(SELECT FROM public.core_livingorganismevent WHERE event_uuid=p_key) THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
 VALUES(p_key,v_lo,p_board,p_thread,p_op,p_actor,p_epoch,p_received,p_args) RETURNING operation INTO written;
 IF written IS NULL OR written='REFUSE_VOTING_OFF' THEN RETURN NULL; END IF;
 RETURN p_key;
EXCEPTION WHEN unique_violation THEN
 GET STACKED DIAGNOSTICS v_constraint=CONSTRAINT_NAME,v_table=TABLE_NAME,v_schema=SCHEMA_NAME;
 IF v_schema='public' AND v_table='core_livingorganismevent' AND EXISTS(SELECT FROM pg_constraint c
   WHERE c.conrelid='public.core_livingorganismevent'::regclass AND c.conname=v_constraint AND c.contype='u'
     AND c.conkey=ARRAY[(SELECT a.attnum FROM pg_attribute a WHERE a.attrelid=c.conrelid AND a.attname='event_uuid')])
 THEN PERFORM public.cq_refuse('DISC_UNAVAILABLE'); END IF;
 RAISE;
END """

# public.c108_check_stand_down()
# Its landed text: 0086_board_continuity.py (found there verbatim). Time calls moved: 1 statement.
C108_CHECK_STAND_DOWN_LANDED = r"""
DECLARE result_ uuid;
BEGIN
 result_:=public.c108_executing_result(NEW.evidence_reference);
 IF NEW.evidence_reference IS DISTINCT FROM 'c108-stand-down:'||result_ OR NEW.prior_state IS DISTINCT FROM 'ACTIVE' OR NEW.resulting_state IS DISTINCT FROM 'DORMANT'
   OR NOT public.c108_stand_down_authorised(result_,NEW.circle_id,
        NEW.effective_at,statement_timestamp()) THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN NEW;
END """
C108_CHECK_STAND_DOWN_CLOCK = r"""
DECLARE result_ uuid;
BEGIN
 result_:=public.c108_executing_result(NEW.evidence_reference);
 IF NEW.evidence_reference IS DISTINCT FROM 'c108-stand-down:'||result_ OR NEW.prior_state IS DISTINCT FROM 'ACTIVE' OR NEW.resulting_state IS DISTINCT FROM 'DORMANT'
   OR NOT public.c108_stand_down_authorised(result_,NEW.circle_id,
        NEW.effective_at,public.intevia_clock_statement()) THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN NEW;
END """

# public.c108_close_for_activation()
# Its landed text: 0086_board_continuity.py (found there verbatim). Time calls moved: 1 statement.
C108_CLOSE_FOR_ACTIVATION_LANDED = r"""
DECLARE owner_ public.core_livingorganism; epoch_ bigint; motion record;
BEGIN
 SELECT lo.* INTO owner_ FROM public.core_livingorganism lo JOIN public.core_circle c ON c.parent_organism_id=lo.id
 WHERE c.id=NEW.circle_id AND c.kind='GOVERNANCE' AND NOT lo.platform_root;
 IF NOT FOUND THEN RETURN NULL; END IF;
 PERFORM public.s015_lock_organisms(ARRAY[owner_.organism_id]);
 SELECT access_epoch INTO STRICT epoch_ FROM public.core_identity WHERE id=NEW.enterer_identity_id;
 FOR motion IN SELECT * FROM public.c108_open_off(owner_.id) ORDER BY thread_uuid LOOP
   INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,
       actor_id,actor_epoch,received_at,request)
   VALUES(gen_random_uuid(),owner_.id,motion.board_uuid,motion.thread_uuid,'CLOSE',
       NEW.enterer_identity_id,epoch_,statement_timestamp(),
       jsonb_build_object('activation',NEW.event_uuid,'previous_phase',motion.phase_act));
 END LOOP;
 RETURN NULL;
END """
C108_CLOSE_FOR_ACTIVATION_CLOCK = r"""
DECLARE owner_ public.core_livingorganism; epoch_ bigint; motion record;
BEGIN
 SELECT lo.* INTO owner_ FROM public.core_livingorganism lo JOIN public.core_circle c ON c.parent_organism_id=lo.id
 WHERE c.id=NEW.circle_id AND c.kind='GOVERNANCE' AND NOT lo.platform_root;
 IF NOT FOUND THEN RETURN NULL; END IF;
 PERFORM public.s015_lock_organisms(ARRAY[owner_.organism_id]);
 SELECT access_epoch INTO STRICT epoch_ FROM public.core_identity WHERE id=NEW.enterer_identity_id;
 FOR motion IN SELECT * FROM public.c108_open_off(owner_.id) ORDER BY thread_uuid LOOP
   INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,
       actor_id,actor_epoch,received_at,request)
   VALUES(gen_random_uuid(),owner_.id,motion.board_uuid,motion.thread_uuid,'CLOSE',
       NEW.enterer_identity_id,epoch_,public.intevia_clock_statement(),
       jsonb_build_object('activation',NEW.event_uuid,'previous_phase',motion.phase_act));
 END LOOP;
 RETURN NULL;
END """

# public.cq_complete()
# Its landed text: 0086_board_continuity.py (assembled by that migration). Time calls moved: 3 statement.
CQ_COMPLETE_LANDED = r"""
DECLARE v_key uuid; v_act public.core_cqact; v_source jsonb;
BEGIN
 v_key:=NEW.act_uuid;
 SELECT * INTO v_act FROM public.core_cqact WHERE act_uuid=v_key;
 IF NOT FOUND OR NOT EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.event_uuid=v_key AND public.cq_event_matches(e))
 OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
   WHERE i.id=v_act.actor_id AND i.access_epoch=v_act.actor_epoch AND i.access_state='active' AND u.is_active)
 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;


 IF v_act.operation='CLOSE' AND v_act.request ? 'activation' THEN
   IF v_act.authority_source IS DISTINCT FROM public.c108_close_source(v_act,v_act.recorded_at)
      OR v_act.facts->'activation' IS DISTINCT FROM v_act.request->'activation'
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN NULL;
 END IF;
 IF v_act.operation='REFUSE_VOTING_OFF' THEN
   -- The input trigger alone derives this shape, after admission. The common
   -- checks above still require the exact four-time ILC pair and active enterer.
   RETURN NULL;
 END IF;
 IF v_act.operation IN ('RECOGNITION_CONSENT','RECOGNISE','BOARD_RESIGN') THEN
   IF v_act.authority_source IS DISTINCT FROM v_act.facts->'source'
      OR (v_act.operation='RECOGNISE' AND v_act.facts->>'type'='SEAT' AND NOT EXISTS(
        SELECT FROM public.core_circleparticipation p JOIN public.core_circleadmissiondecision d ON d.id=p.decision_id
        WHERE p.participation_uuid=v_act.act_uuid AND d.decision_uuid=v_act.act_uuid AND d.constitutional_route='RECOGNITION'
        AND (v_act.facts->>'end' IS NULL OR EXISTS(SELECT FROM public.core_circleparticipationend x WHERE x.participation_id=p.id
          AND x.composed_from_event_uuid=v_act.act_uuid AND x.effective_at=(v_act.facts->>'end')::timestamptz))))
      OR (v_act.operation='RECOGNISE' AND v_act.facts->>'type'='OFFICE' AND NOT EXISTS(
        SELECT FROM public.core_contextualroleassignmentstateevent e WHERE e.event_uuid=v_act.act_uuid
        AND e.assignment_id=(v_act.facts->>'assignment_id')::bigint AND e.effective_at=v_act.effective_at
        AND e.evidence_reference='elect-recognised:'||v_act.act_uuid))
      OR (v_act.operation='BOARD_RESIGN' AND v_act.facts->>'end' IS NOT NULL AND NOT EXISTS(
        SELECT FROM public.core_circleparticipationend x WHERE x.participation_id=(v_act.facts->>'participation_id')::bigint
        AND x.composed_from_event_uuid=v_act.act_uuid AND x.effective_at=(v_act.facts->>'end')::timestamptz))
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN NULL;
 END IF;
 IF v_act.operation='ELECT_VACANCY' THEN
   IF NOT EXISTS(SELECT FROM public.core_electvacancy v WHERE v.route_act=v_act.act_uuid AND v.enterer=v_act.actor_id
      AND v.organism_id=v_act.organism_id AND v.ending::text=v_act.request->>'ending'
      AND v_act.facts->'instruction'=v.instruction AND v_act.authority_source=jsonb_build_object('kind','RULE','route','ELECT_VACANCY','reference',v.ending))
      THEN PERFORM public.cq_refuse('ELECT_VACANCY_SOURCE'); END IF;
   RETURN NULL;
 END IF;
 IF v_act.operation='SETTINGS' AND v_act.request ? 'result' THEN
   IF public.cq4_voted_source(v_act) IS DISTINCT FROM v_act.authority_source THEN
     PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN NULL;
 END IF;
 IF v_act.operation='IMPLEMENT' THEN
   IF public.cq3_implement_source(v_act) IS DISTINCT FROM v_act.authority_source THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   PERFORM public.cq3_check_committed(v_act);
   RETURN NULL;
 ELSIF v_act.operation='SETTLE' AND v_act.facts->>'disposition'='APPLIED' AND NOT EXISTS(
    SELECT FROM public.core_cqact i WHERE i.operation='IMPLEMENT' AND i.request->>'result'=v_act.act_uuid::text) THEN
   PERFORM public.cq_refuse('CQ_COMPLETE');
 END IF;
 -- A later own conflict removes an answer from the roster; it does not invalidate
 -- the historical authority for an earlier, lawful confirmation in this transaction.
 IF v_act.operation IN ('CONFIRM','QUESTION') AND EXISTS(SELECT FROM public.core_cqact c WHERE c.thread_uuid=v_act.thread_uuid
   AND c.actor_id=v_act.actor_id AND c.operation='CONFLICT' AND c.ordinal>v_act.ordinal) THEN
   IF public.disc_member_state(v_act.organism_id,v_act.actor_id,statement_timestamp()) IS DISTINCT FROM 'ACTIVE'
     OR NOT EXISTS(SELECT FROM public.core_discboard b WHERE b.board_uuid=v_act.board_uuid AND
         public.disc_audience(b.organism_id,b.circle_id,v_act.actor_id,'MEMBERS',statement_timestamp()))
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN NULL;
 END IF;
 IF v_act.operation IN ('NO_BALLOT','OPEN_VOTE') AND v_act.request ? 'trigger_act' THEN
   v_source:=public.cq_open_source(v_act.organism_id,v_act.board_uuid,v_act.thread_uuid,v_act.actor_id,v_act.actor_epoch,v_act.request);
 ELSIF v_act.operation IN ('SETTLE','REOPEN_VOTE') AND v_act.request ? 'cause' THEN
   v_source:=public.cq_loss_source(v_act.organism_id,v_act.board_uuid,v_act.thread_uuid,v_act.actor_id,v_act.request);
 ELSE
 v_source:=public.cq_source(v_act.organism_id,v_act.board_uuid,v_act.thread_uuid,v_act.operation,v_act.actor_id,statement_timestamp());
 END IF;
 IF v_act.operation IN ('INITIATE','ELECT_NOMINATE') THEN
   v_source:=coalesce(public.elect_actor_basis(v_act,v_act.effective_at),v_source);
 END IF;
 IF v_act.operation='ANSWER' AND public.elect_attachment(v_act.thread_uuid) IS NOT NULL AND NOT public.elect_board_confirmation(public.elect_attachment(v_act.thread_uuid)) AND
      (v_act.request ? 'choice' OR NOT EXISTS(SELECT FROM public.core_electchoice ec WHERE ec.receipt=v_act.act_uuid))
    THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF v_source IS DISTINCT FROM v_act.authority_source THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN NULL;
END """
CQ_COMPLETE_CLOCK = r"""
DECLARE v_key uuid; v_act public.core_cqact; v_source jsonb;
BEGIN
 v_key:=NEW.act_uuid;
 SELECT * INTO v_act FROM public.core_cqact WHERE act_uuid=v_key;
 IF NOT FOUND OR NOT EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.event_uuid=v_key AND public.cq_event_matches(e))
 OR NOT EXISTS(SELECT FROM public.core_identity i JOIN public.auth_user u ON u.id=i.credential_id
   WHERE i.id=v_act.actor_id AND i.access_epoch=v_act.actor_epoch AND i.access_state='active' AND u.is_active)
 THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;


 IF v_act.operation='CLOSE' AND v_act.request ? 'activation' THEN
   IF v_act.authority_source IS DISTINCT FROM public.c108_close_source(v_act,v_act.recorded_at)
      OR v_act.facts->'activation' IS DISTINCT FROM v_act.request->'activation'
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN NULL;
 END IF;
 IF v_act.operation='REFUSE_VOTING_OFF' THEN
   -- The input trigger alone derives this shape, after admission. The common
   -- checks above still require the exact four-time ILC pair and active enterer.
   RETURN NULL;
 END IF;
 IF v_act.operation IN ('RECOGNITION_CONSENT','RECOGNISE','BOARD_RESIGN') THEN
   IF v_act.authority_source IS DISTINCT FROM v_act.facts->'source'
      OR (v_act.operation='RECOGNISE' AND v_act.facts->>'type'='SEAT' AND NOT EXISTS(
        SELECT FROM public.core_circleparticipation p JOIN public.core_circleadmissiondecision d ON d.id=p.decision_id
        WHERE p.participation_uuid=v_act.act_uuid AND d.decision_uuid=v_act.act_uuid AND d.constitutional_route='RECOGNITION'
        AND (v_act.facts->>'end' IS NULL OR EXISTS(SELECT FROM public.core_circleparticipationend x WHERE x.participation_id=p.id
          AND x.composed_from_event_uuid=v_act.act_uuid AND x.effective_at=(v_act.facts->>'end')::timestamptz))))
      OR (v_act.operation='RECOGNISE' AND v_act.facts->>'type'='OFFICE' AND NOT EXISTS(
        SELECT FROM public.core_contextualroleassignmentstateevent e WHERE e.event_uuid=v_act.act_uuid
        AND e.assignment_id=(v_act.facts->>'assignment_id')::bigint AND e.effective_at=v_act.effective_at
        AND e.evidence_reference='elect-recognised:'||v_act.act_uuid))
      OR (v_act.operation='BOARD_RESIGN' AND v_act.facts->>'end' IS NOT NULL AND NOT EXISTS(
        SELECT FROM public.core_circleparticipationend x WHERE x.participation_id=(v_act.facts->>'participation_id')::bigint
        AND x.composed_from_event_uuid=v_act.act_uuid AND x.effective_at=(v_act.facts->>'end')::timestamptz))
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN NULL;
 END IF;
 IF v_act.operation='ELECT_VACANCY' THEN
   IF NOT EXISTS(SELECT FROM public.core_electvacancy v WHERE v.route_act=v_act.act_uuid AND v.enterer=v_act.actor_id
      AND v.organism_id=v_act.organism_id AND v.ending::text=v_act.request->>'ending'
      AND v_act.facts->'instruction'=v.instruction AND v_act.authority_source=jsonb_build_object('kind','RULE','route','ELECT_VACANCY','reference',v.ending))
      THEN PERFORM public.cq_refuse('ELECT_VACANCY_SOURCE'); END IF;
   RETURN NULL;
 END IF;
 IF v_act.operation='SETTINGS' AND v_act.request ? 'result' THEN
   IF public.cq4_voted_source(v_act) IS DISTINCT FROM v_act.authority_source THEN
     PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN NULL;
 END IF;
 IF v_act.operation='IMPLEMENT' THEN
   IF public.cq3_implement_source(v_act) IS DISTINCT FROM v_act.authority_source THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   PERFORM public.cq3_check_committed(v_act);
   RETURN NULL;
 ELSIF v_act.operation='SETTLE' AND v_act.facts->>'disposition'='APPLIED' AND NOT EXISTS(
    SELECT FROM public.core_cqact i WHERE i.operation='IMPLEMENT' AND i.request->>'result'=v_act.act_uuid::text) THEN
   PERFORM public.cq_refuse('CQ_COMPLETE');
 END IF;
 -- A later own conflict removes an answer from the roster; it does not invalidate
 -- the historical authority for an earlier, lawful confirmation in this transaction.
 IF v_act.operation IN ('CONFIRM','QUESTION') AND EXISTS(SELECT FROM public.core_cqact c WHERE c.thread_uuid=v_act.thread_uuid
   AND c.actor_id=v_act.actor_id AND c.operation='CONFLICT' AND c.ordinal>v_act.ordinal) THEN
   IF public.disc_member_state(v_act.organism_id,v_act.actor_id,public.intevia_clock_statement()) IS DISTINCT FROM 'ACTIVE'
     OR NOT EXISTS(SELECT FROM public.core_discboard b WHERE b.board_uuid=v_act.board_uuid AND
         public.disc_audience(b.organism_id,b.circle_id,v_act.actor_id,'MEMBERS',public.intevia_clock_statement()))
   THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
   RETURN NULL;
 END IF;
 IF v_act.operation IN ('NO_BALLOT','OPEN_VOTE') AND v_act.request ? 'trigger_act' THEN
   v_source:=public.cq_open_source(v_act.organism_id,v_act.board_uuid,v_act.thread_uuid,v_act.actor_id,v_act.actor_epoch,v_act.request);
 ELSIF v_act.operation IN ('SETTLE','REOPEN_VOTE') AND v_act.request ? 'cause' THEN
   v_source:=public.cq_loss_source(v_act.organism_id,v_act.board_uuid,v_act.thread_uuid,v_act.actor_id,v_act.request);
 ELSE
 v_source:=public.cq_source(v_act.organism_id,v_act.board_uuid,v_act.thread_uuid,v_act.operation,v_act.actor_id,public.intevia_clock_statement());
 END IF;
 IF v_act.operation IN ('INITIATE','ELECT_NOMINATE') THEN
   v_source:=coalesce(public.elect_actor_basis(v_act,v_act.effective_at),v_source);
 END IF;
 IF v_act.operation='ANSWER' AND public.elect_attachment(v_act.thread_uuid) IS NOT NULL AND NOT public.elect_board_confirmation(public.elect_attachment(v_act.thread_uuid)) AND
      (v_act.request ? 'choice' OR NOT EXISTS(SELECT FROM public.core_electchoice ec WHERE ec.receipt=v_act.act_uuid))
    THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 IF v_source IS DISTINCT FROM v_act.authority_source THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
 RETURN NULL;
END """

# public.circle_coordinator_event()
# Its landed text: 0087_members_stand_down.py (assembled by that migration). Time calls moved: 1 wall.
CIRCLE_COORDINATOR_EVENT_LANDED = r"""
DECLARE office record; proposal public.core_contextualroleassignmentstateevent; cut timestamptz;
        act text; place_ref text; decision text; consent uuid; standing uuid; known timestamptz; approving_office uuid; expected_ref text; marker text;
BEGIN
 SELECT a.id,a.assignment_uuid,a.membership_id,a.circle_id,c.circle_uuid,c.parent_organism_id AS lo_id,
        c.kind,d.active,m.identity_id,lo.platform_root,lo.founding_authority_basis_id AS basis
 INTO office FROM public.core_contextualroleassignment a
 JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id AND d.code='COORDINATOR' AND d.scope='CIRCLE'
 JOIN public.core_circle c ON c.id=a.circle_id JOIN public.core_organismmembership m ON m.id=a.membership_id
 JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id WHERE a.id=NEW.assignment_id;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF office.kind='GENERIC' THEN
    PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=office.lo_id)]);
 END IF;
 IF NEW.action='END' AND public.c108_result_child(NEW.evidence_reference,'offices',NEW.assignment_id)
 AND NEW.composing_rule_reference=NEW.evidence_reference THEN RETURN NULL; END IF;
 IF NEW.action='END' AND NEW.composing_rule_reference='ELECT:TERM_END'
 AND public.cmt_chair_end(NEW.assignment_id,NEW.recorded_at)=NEW.effective_at THEN RETURN NULL; END IF;
 IF NEW.action='END' AND NEW.composing_rule_reference IN ('CIRCLES:G2:U3','CIRCLES:G2:SELF_LEAVE') THEN
    RETURN NULL; -- 0059 checks the causal event and atomic endings at commit.
 END IF;
 IF NOT office.active OR NEW.authority_basis_id IS DISTINCT FROM office.basis
    OR NEW.occurred_at IS DISTINCT FROM NEW.effective_at OR NEW.received_at IS DISTINCT FROM NEW.effective_at
    OR NEW.effective_at>NEW.recorded_at OR NEW.entry_mode<>'TRANSPORT' OR NEW.actor_capacity<>'STANDING'
    OR NOT EXISTS(SELECT FROM public.core_identity WHERE id=NEW.enterer_identity_id AND access_state='active') THEN
    RAISE EXCEPTION 'CIRCLE_COORDINATOR_CONTEXT' USING ERRCODE='23514';
 END IF;
 IF NEW.action='PROPOSE' AND NEW.sequence=1 AND NEW.resulting_state='PROPOSED' AND NEW.prior_state IS NULL
    AND NEW.actor_state='PARTY' AND NEW.actor_id=NEW.enterer_identity_id AND NEW.effective_until_state='NOT_ESTABLISHED'
    AND NEW.actor_access_epoch=(SELECT access_epoch FROM public.core_identity WHERE id=NEW.enterer_identity_id) THEN
    place_ref:=NEW.evidence_reference;
    act:='NOMINATE:'||office.assignment_uuid||':'||place_ref;
 ELSIF NEW.action='ASSIGN' AND NEW.sequence=2 AND NEW.prior_state='PROPOSED' AND NEW.resulting_state='ACTIVE'
    AND NEW.actor_state='LIVING_ORGANISM' AND NEW.effective_until_state IN ('FIXED','NONE')
    AND (NEW.effective_until IS NULL OR NEW.effective_until>NEW.recorded_at) THEN
    IF office.platform_root AND (office.kind<>'GENERIC' OR public.r_circle_root_qualified(office.lo_id,NEW.effective_at,NEW.recorded_at) IS NOT TRUE) THEN
       RAISE EXCEPTION 'CIRCLE_ROOT_APPOINTMENT_SOURCE_REQUIRED' USING ERRCODE='23514';
    END IF;
    SELECT * INTO STRICT proposal FROM public.core_contextualroleassignmentstateevent
      WHERE id=NEW.predecessor_id AND assignment_id=office.id AND action='PROPOSE' AND sequence=1;
    place_ref:=proposal.evidence_reference;
    act:='APPOINT:'||office.assignment_uuid||':'||coalesce(public.s015_canonical_ts(NEW.effective_until),'NONE');
 ELSE
    RAISE EXCEPTION 'CIRCLE_COORDINATOR_EDGE' USING ERRCODE='23514';
 END IF;
 FOR cut,known IN SELECT NEW.effective_at,NEW.recorded_at
                  UNION ALL SELECT t,t FROM (SELECT CASE WHEN office.kind='GENERIC' THEN clock_timestamp()
                                                    ELSE NEW.recorded_at END AS t) execution LOOP
    IF public.circle_office_place(office.circle_id,office.membership_id,place_ref,cut,known) IS NOT TRUE
       OR NOT EXISTS(SELECT FROM public.core_organismmembership m WHERE m.living_organism_id=office.lo_id
          AND m.identity_id=NEW.enterer_identity_id
          AND public.s015_fold_effective_state('core_organismmembership',m.id,cut,known)='ACTIVE') THEN
       RAISE EXCEPTION 'CIRCLE_COORDINATOR_PLACE' USING ERRCODE='23514';
    END IF;
    IF office.kind='GENERIC' THEN
       IF NEW.action='ASSIGN' AND (NEW.effective_until_state<>'NONE' OR NEW.effective_until IS NOT NULL) THEN
          RAISE EXCEPTION 'CIRCLE_COORDINATOR_NO_TERM' USING ERRCODE='23514';
       END IF;
       approving_office:=public.circle_appointing_office(
          office.circle_id,NEW.enterer_identity_id,office.identity_id,office.assignment_uuid,cut,known);
       decision:=public.circle_office_decision(approving_office,
          office.circle_uuid,act,NEW.enterer_identity_id,office.identity_id);
    ELSE
       decision:=public.circle_decision(office.lo_id,public.circle_source(office.lo_id,office.circle_uuid,
       act,NEW.enterer_identity_id,office.identity_id,cut,known),cut,known);
    END IF;
    IF decision IS NULL OR NEW.authority_decision_reference IS DISTINCT FROM decision THEN
       RAISE EXCEPTION 'CIRCLE_COORDINATOR_MANDATE' USING ERRCODE='23514';
    END IF;
    IF NEW.action='ASSIGN' THEN
       consent:=public.circle_office_consent(office.id,cut,known);
       IF office.kind='GENERIC' THEN
          -- A later approver must already hold the office before this ASSIGN statement.
          -- Otherwise two direct writes in one statement could try to qualify one another.
          marker:=public.circle_appointment_marker(office.circle_id,office.assignment_uuid,NEW.enterer_identity_id,
                       office.identity_id,approving_office,place_ref,cut,known);
          IF marker='|RC1' AND (proposal.actor_id IS DISTINCT FROM office.identity_id
             OR NEW.enterer_identity_id IS DISTINCT FROM office.identity_id) THEN
             RAISE EXCEPTION 'CIRCLE_VOLUNTEER_SELF_REQUIRED' USING ERRCODE='23514';
          END IF;
          IF (marker NOT IN ('|RC1','|CMT') OR marker IS NULL)
             AND NOT EXISTS(SELECT FROM public.core_contextualroleassignment issuer
               JOIN public.core_contextualroleassignmentstateevent seated ON seated.assignment_id=issuer.id AND seated.action='ASSIGN'
               WHERE issuer.assignment_uuid=approving_office AND seated.recorded_at<NEW.recorded_at) THEN
             RAISE EXCEPTION 'CIRCLE_APPROVER_PRIOR_OFFICE' USING ERRCODE='23514';
          END IF;
          IF NOT EXISTS(SELECT FROM public.core_livingorganism lo JOIN public.core_authoritybasis b ON b.id=office.basis
               WHERE lo.id=office.lo_id AND public.c2_office_standing(lo.organism_id,office.assignment_uuid,
                     office.identity_id,b.basis_uuid,cut,known)='QUALIFIED') THEN
             RAISE EXCEPTION 'CIRCLE_COORDINATOR_CONSENT_OR_STANDING' USING ERRCODE='23514';
          END IF;
          expected_ref:=public.circle_standing_text(approving_office,place_ref,consent,
               public.c1b_period_at(office.membership_id,cut,known),NEW.event_uuid)
                || marker;
       ELSE
          standing:=public.circle_source(office.lo_id,office.assignment_uuid,'COUNT_OFFICE',
               office.identity_id,office.identity_id,cut,known);
          expected_ref:='consent:'||consent||'|standing:'||standing;
       END IF;
       IF expected_ref IS NULL OR NEW.evidence_reference IS DISTINCT FROM expected_ref THEN
          RAISE EXCEPTION 'CIRCLE_COORDINATOR_CONSENT_OR_STANDING' USING ERRCODE='23514';
       END IF;
    END IF;
 END LOOP;
 RETURN NULL;
END """
CIRCLE_COORDINATOR_EVENT_CLOCK = r"""
DECLARE office record; proposal public.core_contextualroleassignmentstateevent; cut timestamptz;
        act text; place_ref text; decision text; consent uuid; standing uuid; known timestamptz; approving_office uuid; expected_ref text; marker text;
BEGIN
 SELECT a.id,a.assignment_uuid,a.membership_id,a.circle_id,c.circle_uuid,c.parent_organism_id AS lo_id,
        c.kind,d.active,m.identity_id,lo.platform_root,lo.founding_authority_basis_id AS basis
 INTO office FROM public.core_contextualroleassignment a
 JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id AND d.code='COORDINATOR' AND d.scope='CIRCLE'
 JOIN public.core_circle c ON c.id=a.circle_id JOIN public.core_organismmembership m ON m.id=a.membership_id
 JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id WHERE a.id=NEW.assignment_id;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF office.kind='GENERIC' THEN
    PERFORM public.s015_lock_organisms(ARRAY[(SELECT organism_id FROM public.core_livingorganism WHERE id=office.lo_id)]);
 END IF;
 IF NEW.action='END' AND public.c108_result_child(NEW.evidence_reference,'offices',NEW.assignment_id)
 AND NEW.composing_rule_reference=NEW.evidence_reference THEN RETURN NULL; END IF;
 IF NEW.action='END' AND NEW.composing_rule_reference='ELECT:TERM_END'
 AND public.cmt_chair_end(NEW.assignment_id,NEW.recorded_at)=NEW.effective_at THEN RETURN NULL; END IF;
 IF NEW.action='END' AND NEW.composing_rule_reference IN ('CIRCLES:G2:U3','CIRCLES:G2:SELF_LEAVE') THEN
    RETURN NULL; -- 0059 checks the causal event and atomic endings at commit.
 END IF;
 IF NOT office.active OR NEW.authority_basis_id IS DISTINCT FROM office.basis
    OR NEW.occurred_at IS DISTINCT FROM NEW.effective_at OR NEW.received_at IS DISTINCT FROM NEW.effective_at
    OR NEW.effective_at>NEW.recorded_at OR NEW.entry_mode<>'TRANSPORT' OR NEW.actor_capacity<>'STANDING'
    OR NOT EXISTS(SELECT FROM public.core_identity WHERE id=NEW.enterer_identity_id AND access_state='active') THEN
    RAISE EXCEPTION 'CIRCLE_COORDINATOR_CONTEXT' USING ERRCODE='23514';
 END IF;
 IF NEW.action='PROPOSE' AND NEW.sequence=1 AND NEW.resulting_state='PROPOSED' AND NEW.prior_state IS NULL
    AND NEW.actor_state='PARTY' AND NEW.actor_id=NEW.enterer_identity_id AND NEW.effective_until_state='NOT_ESTABLISHED'
    AND NEW.actor_access_epoch=(SELECT access_epoch FROM public.core_identity WHERE id=NEW.enterer_identity_id) THEN
    place_ref:=NEW.evidence_reference;
    act:='NOMINATE:'||office.assignment_uuid||':'||place_ref;
 ELSIF NEW.action='ASSIGN' AND NEW.sequence=2 AND NEW.prior_state='PROPOSED' AND NEW.resulting_state='ACTIVE'
    AND NEW.actor_state='LIVING_ORGANISM' AND NEW.effective_until_state IN ('FIXED','NONE')
    AND (NEW.effective_until IS NULL OR NEW.effective_until>NEW.recorded_at) THEN
    IF office.platform_root AND (office.kind<>'GENERIC' OR public.r_circle_root_qualified(office.lo_id,NEW.effective_at,NEW.recorded_at) IS NOT TRUE) THEN
       RAISE EXCEPTION 'CIRCLE_ROOT_APPOINTMENT_SOURCE_REQUIRED' USING ERRCODE='23514';
    END IF;
    SELECT * INTO STRICT proposal FROM public.core_contextualroleassignmentstateevent
      WHERE id=NEW.predecessor_id AND assignment_id=office.id AND action='PROPOSE' AND sequence=1;
    place_ref:=proposal.evidence_reference;
    act:='APPOINT:'||office.assignment_uuid||':'||coalesce(public.s015_canonical_ts(NEW.effective_until),'NONE');
 ELSE
    RAISE EXCEPTION 'CIRCLE_COORDINATOR_EDGE' USING ERRCODE='23514';
 END IF;
 FOR cut,known IN SELECT NEW.effective_at,NEW.recorded_at
                  UNION ALL SELECT t,t FROM (SELECT CASE WHEN office.kind='GENERIC' THEN public.intevia_clock_instant()
                                                    ELSE NEW.recorded_at END AS t) execution LOOP
    IF public.circle_office_place(office.circle_id,office.membership_id,place_ref,cut,known) IS NOT TRUE
       OR NOT EXISTS(SELECT FROM public.core_organismmembership m WHERE m.living_organism_id=office.lo_id
          AND m.identity_id=NEW.enterer_identity_id
          AND public.s015_fold_effective_state('core_organismmembership',m.id,cut,known)='ACTIVE') THEN
       RAISE EXCEPTION 'CIRCLE_COORDINATOR_PLACE' USING ERRCODE='23514';
    END IF;
    IF office.kind='GENERIC' THEN
       IF NEW.action='ASSIGN' AND (NEW.effective_until_state<>'NONE' OR NEW.effective_until IS NOT NULL) THEN
          RAISE EXCEPTION 'CIRCLE_COORDINATOR_NO_TERM' USING ERRCODE='23514';
       END IF;
       approving_office:=public.circle_appointing_office(
          office.circle_id,NEW.enterer_identity_id,office.identity_id,office.assignment_uuid,cut,known);
       decision:=public.circle_office_decision(approving_office,
          office.circle_uuid,act,NEW.enterer_identity_id,office.identity_id);
    ELSE
       decision:=public.circle_decision(office.lo_id,public.circle_source(office.lo_id,office.circle_uuid,
       act,NEW.enterer_identity_id,office.identity_id,cut,known),cut,known);
    END IF;
    IF decision IS NULL OR NEW.authority_decision_reference IS DISTINCT FROM decision THEN
       RAISE EXCEPTION 'CIRCLE_COORDINATOR_MANDATE' USING ERRCODE='23514';
    END IF;
    IF NEW.action='ASSIGN' THEN
       consent:=public.circle_office_consent(office.id,cut,known);
       IF office.kind='GENERIC' THEN
          -- A later approver must already hold the office before this ASSIGN statement.
          -- Otherwise two direct writes in one statement could try to qualify one another.
          marker:=public.circle_appointment_marker(office.circle_id,office.assignment_uuid,NEW.enterer_identity_id,
                       office.identity_id,approving_office,place_ref,cut,known);
          IF marker='|RC1' AND (proposal.actor_id IS DISTINCT FROM office.identity_id
             OR NEW.enterer_identity_id IS DISTINCT FROM office.identity_id) THEN
             RAISE EXCEPTION 'CIRCLE_VOLUNTEER_SELF_REQUIRED' USING ERRCODE='23514';
          END IF;
          IF (marker NOT IN ('|RC1','|CMT') OR marker IS NULL)
             AND NOT EXISTS(SELECT FROM public.core_contextualroleassignment issuer
               JOIN public.core_contextualroleassignmentstateevent seated ON seated.assignment_id=issuer.id AND seated.action='ASSIGN'
               WHERE issuer.assignment_uuid=approving_office AND seated.recorded_at<NEW.recorded_at) THEN
             RAISE EXCEPTION 'CIRCLE_APPROVER_PRIOR_OFFICE' USING ERRCODE='23514';
          END IF;
          IF NOT EXISTS(SELECT FROM public.core_livingorganism lo JOIN public.core_authoritybasis b ON b.id=office.basis
               WHERE lo.id=office.lo_id AND public.c2_office_standing(lo.organism_id,office.assignment_uuid,
                     office.identity_id,b.basis_uuid,cut,known)='QUALIFIED') THEN
             RAISE EXCEPTION 'CIRCLE_COORDINATOR_CONSENT_OR_STANDING' USING ERRCODE='23514';
          END IF;
          expected_ref:=public.circle_standing_text(approving_office,place_ref,consent,
               public.c1b_period_at(office.membership_id,cut,known),NEW.event_uuid)
                || marker;
       ELSE
          standing:=public.circle_source(office.lo_id,office.assignment_uuid,'COUNT_OFFICE',
               office.identity_id,office.identity_id,cut,known);
          expected_ref:='consent:'||consent||'|standing:'||standing;
       END IF;
       IF expected_ref IS NULL OR NEW.evidence_reference IS DISTINCT FROM expected_ref THEN
          RAISE EXCEPTION 'CIRCLE_COORDINATOR_CONSENT_OR_STANDING' USING ERRCODE='23514';
       END IF;
    END IF;
 END LOOP;
 RETURN NULL;
END """

# public.circle_guard_active_event()
# Its landed text: 0087_members_stand_down.py (assembled by that migration). Time calls moved: 3 wall.
CIRCLE_GUARD_ACTIVE_EVENT_LANDED = r"""
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
    SELECT * INTO last_exit FROM public.circle_last_ending(NEW.circle_id,clock_timestamp());
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
       NEW.authority_decision_reference,NEW.evidence_reference,NEW.effective_at,NEW.recorded_at,clock_timestamp());
    IF problem IS NOT NULL THEN RAISE EXCEPTION '%',problem USING ERRCODE='23514'; END IF;
 ELSIF NEW.prior_state='ACTIVE' OR NEW.resulting_state='ACTIVE' THEN
    SELECT * INTO previous FROM public.core_circlestateevent WHERE id=NEW.predecessor_id AND circle_id=NEW.circle_id;
    IF NEW.prior_state IS DISTINCT FROM 'ACTIVE' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'
       OR previous.resulting_state IS DISTINCT FROM 'ACTIVE'
       OR NEW.action NOT IN ('OPEN_WORK','LEAVE_CIRCLE','CLAIM_WORK') THEN
       RAISE EXCEPTION 'CIRCLE_G4_ACTIVE_EDGE' USING ERRCODE='23514'; END IF;
    IF NEW.action='CLAIM_WORK' AND EXISTS(SELECT FROM public.core_circle c WHERE c.id=NEW.circle_id AND c.kind='GENERIC') THEN
       executed:=clock_timestamp();
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
END """
CIRCLE_GUARD_ACTIVE_EVENT_CLOCK = r"""
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
END """

# public.s2b_guard_office_attribution()
# Its landed text: 0087_members_stand_down.py (assembled by that migration). Time calls moved: 2 statement.
S2B_GUARD_OFFICE_ATTRIBUTION_LANDED = r"""
DECLARE v_lo_id bigint; v_lo_uuid uuid; v_founding_xid xid8; v_membership_uuid uuid;
                v_membership_identity_id bigint; v_role_uuid uuid; v_role_code text; v_role_scope text; v_decision_row record;
                v_founding_event_uuid uuid; v_founding_authority_decision_reference text;
                v_role_kind text; v_role_active boolean; v_membership_id bigint;
BEGIN

    IF starts_with(NEW.evidence_reference,'c108-stand-down:') THEN
      IF NOT public.c108_result_child(NEW.evidence_reference,'offices',NEW.assignment_id) OR
        NEW.composing_rule_reference IS DISTINCT FROM NEW.evidence_reference OR NEW.action IS DISTINCT FROM 'END'
        OR NEW.prior_state NOT IN ('ACTIVE','SUSPENDED') OR NEW.prior_state IS NULL OR NEW.resulting_state IS DISTINCT FROM 'ENDED'
        OR NEW.actor_state IS DISTINCT FROM 'NONE' OR NEW.entry_mode IS DISTINCT FROM 'COMPOSED' OR NEW.actor_id IS NOT NULL
        OR NEW.actor_access_epoch IS NOT NULL OR NEW.actor_capacity IS DISTINCT FROM 'NA_NO_PARTY'
        OR NEW.effective_until IS NOT NULL OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
        OR NOT EXISTS(SELECT FROM public.core_circlestateevent e WHERE e.evidence_reference=NEW.evidence_reference
          AND e.action='STAND_DOWN' AND (e.authority_basis_id,e.authority_decision_reference,e.enterer_identity_id,
            e.occurred_at,e.received_at,e.payload_fingerprint)=(NEW.authority_basis_id,NEW.authority_decision_reference,
            NEW.enterer_identity_id,NEW.occurred_at,NEW.received_at,NEW.payload_fingerprint)
          AND NEW.request_reference=e.evidence_reference||':office:'||NEW.assignment_id AND NEW.idempotency_key=NEW.request_reference)
      THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
      RETURN NEW;
    END IF;
    IF NEW.composing_rule_reference='ELECT:TERM_END' THEN
      IF NEW.action IS DISTINCT FROM 'END' OR NEW.resulting_state IS DISTINCT FROM 'ENDED'
       OR NEW.prior_state IS NULL OR NEW.prior_state NOT IN ('ACTIVE','SUSPENDED') OR NEW.actor_state IS DISTINCT FROM 'NONE'
       OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
       OR NEW.actor_capacity IS DISTINCT FROM 'NA_NO_PARTY' OR NEW.entry_mode IS DISTINCT FROM 'COMPOSED'
       OR NEW.composing_rule_state IS DISTINCT FROM 'REFERENCE' OR NEW.composer_state IS DISTINCT FROM 'REFERENCE'
       OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
       OR NEW.effective_until IS NOT NULL OR NEW.effective_until_state IS DISTINCT FROM 'NONE'
       OR NEW.received_at IS DISTINCT FROM statement_timestamp()
       OR NEW.occurred_at IS DISTINCT FROM NEW.effective_at OR NOT EXISTS(
         SELECT FROM public.elect_due_office(NEW.assignment_id,NEW.received_at) q
         JOIN public.core_contextualroleassignmentstateevent e ON e.id=q.source_id
         JOIN public.core_contextualroleassignment a ON a.id=e.assignment_id
         JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
         JOIN public.core_identity i ON i.id=NEW.enterer_identity_id JOIN public.auth_user u ON u.id=i.credential_id
         WHERE i.access_state='active' AND u.is_active
         AND (public.disc_member_state(d.living_organism_id,i.id,NEW.received_at)='ACTIVE' OR
             public.elect_notice_enterer(d.living_organism_id,NEW.assignment_id,i.id,NEW.received_at))
         AND NEW.effective_at=q.due AND NEW.authority_basis_id=e.authority_basis_id
         AND NEW.authority_decision_reference=e.authority_decision_reference
         AND NEW.evidence_reference='elect-expiry:'||e.event_uuid
         AND NEW.request_reference=NEW.evidence_reference AND NEW.idempotency_key=NEW.evidence_reference
         AND NEW.payload_fingerprint=public.c2_target(NEW.evidence_reference))
      THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
      RETURN NEW;
    END IF;

    IF NEW.action='ASSIGN' THEN
      PERFORM 1 FROM public.core_livingorganism lo JOIN public.core_organismroledefinition d
        ON d.living_organism_id=lo.id JOIN public.core_contextualroleassignment a ON a.role_definition_id=d.id
        WHERE a.id=NEW.assignment_id AND d.kind='BOARD_OFFICE' FOR UPDATE OF lo;
    END IF;
    IF NEW.action='ASSIGN' AND EXISTS(
       SELECT FROM public.core_contextualroleassignment a
       JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
       WHERE a.id=NEW.assignment_id AND d.kind='BOARD_OFFICE'
       AND EXISTS(SELECT FROM public.elect_term_slots(NULL,d.id,d.code,NULL,statement_timestamp()) s
         JOIN public.core_contextualroleassignment other ON other.assignment_uuid=s.occupancy
         JOIN public.core_contextualroleassignmentstateevent assigned ON assigned.assignment_id=other.id AND assigned.action='ASSIGN'
         WHERE EXISTS(SELECT FROM public.core_cqact i WHERE i.act_uuid IN (NEW.event_uuid,assigned.event_uuid)
           AND (i.operation='RECOGNISE' OR (i.operation='IMPLEMENT' AND i.facts->'domain_plan' ? 'incumbent')))
         AND other.id<>a.id AND (s.ends IS NULL OR s.ends>s.starts)
         AND (s.ends IS NULL OR s.ends>NEW.effective_at)
         AND (NEW.effective_until IS NULL OR s.starts<NEW.effective_until)))
    THEN PERFORM public.cq_refuse('ELECT_OCCUPIED'); END IF;

    IF NEW.composing_rule_reference='ELECT:SUCCESSOR_LEFT' THEN
       IF NEW.action IS DISTINCT FROM 'END' OR NEW.prior_state IS DISTINCT FROM 'ACTIVE'
          OR NEW.resulting_state IS DISTINCT FROM 'ENDED' OR NEW.actor_state IS DISTINCT FROM 'NONE'
          OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL OR NEW.actor_capacity IS DISTINCT FROM 'NA_NO_PARTY'
          OR NEW.entry_mode IS DISTINCT FROM 'COMPOSED' OR NEW.composing_rule_state IS DISTINCT FROM 'REFERENCE'
          OR NEW.composer_state IS DISTINCT FROM 'REFERENCE' OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
          OR NEW.effective_until IS NOT NULL OR NEW.effective_until_state IS DISTINCT FROM 'NONE'
          OR NOT EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent h
            JOIN public.core_contextualroleassignment a ON a.id=h.assignment_id
            JOIN public.core_organismmembershiptransition t ON t.membership_id=a.membership_id
            WHERE h.id=NEW.predecessor_id AND h.assignment_id=NEW.assignment_id AND h.action='ASSIGN' AND t.resulting_state='ENDED'
            AND public.elect_pending_successor(h.event_uuid,t.membership_id,'OFFICE',t.effective_at,t.received_at)
            AND NEW.evidence_reference='elect-departure:'||t.event_uuid||':'||h.event_uuid
            AND NEW.enterer_identity_id=t.enterer_identity_id AND NEW.authority_basis_id=t.authority_basis_id
            AND NEW.authority_decision_reference=t.authority_decision_reference AND NEW.payload_fingerprint=t.payload_fingerprint
            AND NEW.request_reference=NEW.evidence_reference AND NEW.idempotency_key=NEW.evidence_reference
            AND (NEW.effective_at,NEW.occurred_at,NEW.received_at)=(h.effective_at,t.received_at,t.received_at))
       THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
       RETURN NEW;
    END IF;

    IF NEW.action='ASSIGN' AND starts_with(NEW.evidence_reference,'elect-recognised:') THEN
      IF NEW.prior_state IS DISTINCT FROM 'PROPOSED' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'
         OR NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM' OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
         OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT' OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
         OR NOT public.elect_recognition_office(NEW.assignment_id,NEW.event_uuid,NEW.enterer_identity_id,
             NEW.authority_decision_reference,NEW.effective_at,NEW.effective_until)
         OR NOT EXISTS(SELECT FROM public.core_cqact a JOIN public.core_livingorganism lo ON a.organism_id=lo.id
           WHERE a.act_uuid=NEW.event_uuid AND NEW.evidence_reference='elect-recognised:'||a.act_uuid
           AND NEW.request_reference='recognise:'||a.act_uuid AND NEW.idempotency_key=NEW.request_reference
           AND NEW.authority_basis_id=lo.founding_authority_basis_id AND a.fingerprint=NEW.payload_fingerprint
           AND NEW.occurred_at=a.received_at AND NEW.received_at=a.received_at)
      THEN PERFORM public.cq_refuse('ELECT_RECOGNISER'); END IF;
      RETURN NEW;
    END IF;
    IF NEW.action='ASSIGN' AND NEW.evidence_reference LIKE 'elect-result:%' THEN
      IF NEW.resulting_state IS DISTINCT FROM 'ACTIVE' OR NEW.prior_state IS DISTINCT FROM 'PROPOSED'
         OR NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM' OR NEW.actor_id IS NOT NULL
         OR NEW.actor_access_epoch IS NOT NULL OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
         OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
         OR NOT public.elect_office_source(NEW.assignment_id,NEW.event_uuid,NEW.enterer_identity_id,
           NEW.authority_decision_reference,NEW.effective_at,NEW.effective_until)
         OR NOT EXISTS(SELECT FROM public.core_cqact i JOIN public.core_livingorganism lo ON lo.id=i.organism_id
           WHERE i.act_uuid=NEW.event_uuid AND i.received_at=NEW.received_at AND i.effective_at=NEW.occurred_at
             AND i.fingerprint=NEW.payload_fingerprint AND NEW.authority_basis_id=lo.founding_authority_basis_id
             AND NEW.evidence_reference='elect-result:'||(i.request->>'result'))
         THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
      RETURN NEW;
    END IF;
        SELECT lo.id, lo.organism_id, lo.founding_insert_xid, m.membership_uuid,
                     m.identity_id, d.role_uuid, d.code, d.scope,
                     d.kind, d.active, m.id
            INTO v_lo_id, v_lo_uuid, v_founding_xid, v_membership_uuid,
                     v_membership_identity_id, v_role_uuid, v_role_code, v_role_scope,
                     v_role_kind, v_role_active, v_membership_id
      FROM public.core_contextualroleassignment a
      JOIN public.core_organismmembership m ON m.id = a.membership_id
      JOIN public.core_livingorganism lo ON lo.id = m.living_organism_id
      JOIN public.core_organismroledefinition d ON d.id = a.role_definition_id
     WHERE a.id = NEW.assignment_id;
    IF v_lo_uuid IS NULL THEN
        RAISE EXCEPTION 'S2B office attribution refused: assignment context missing'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF v_role_scope IS DISTINCT FROM 'LIVING_ORGANISM' THEN
        RETURN NEW;
    END IF;

    IF starts_with(v_role_code,'INTEVIA_') AND public.r_genesis_context(
         v_lo_id,NEW.authority_decision_reference,NEW.actor_id,NEW.effective_at) THEN
        IF v_role_code NOT IN ('INTEVIA_FOUNDER_STEWARD','INTEVIA_PRIVACY_COORDINATOR')
           OR v_membership_identity_id IS DISTINCT FROM NEW.actor_id OR NEW.sequence<>1 OR NEW.prior_state IS NOT NULL
           OR NEW.action IS DISTINCT FROM 'ASSIGN' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'
           OR NEW.actor_state IS DISTINCT FROM 'PARTY' OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
           OR NEW.enterer_identity_id IS DISTINCT FROM NEW.actor_id OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
           OR NEW.effective_until_state IS DISTINCT FROM 'NONE' OR NEW.effective_until IS NOT NULL
           OR NEW.received_at IS DISTINCT FROM NEW.effective_at OR NEW.occurred_at IS DISTINCT FROM NEW.effective_at
           OR v_role_active IS DISTINCT FROM true OR NOT EXISTS(
              SELECT FROM public.core_rootgenesis g JOIN public.core_livingorganismevent f ON f.living_organism_id=v_lo_id AND f.sequence=1
              WHERE g.organism_uuid=v_lo_uuid AND NEW.authority_basis_id=g.basis_id
              AND NEW.actor_access_epoch=(SELECT access_epoch FROM public.core_identity WHERE id=g.founder_id)
              AND NEW.evidence_reference='founding-batch:'||f.event_uuid
              AND NEW.payload_fingerprint=g.request_fingerprint) THEN
            RAISE EXCEPTION 'R_GENESIS_OFFICE_SOURCE' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF starts_with(v_role_code, 'INTEVIA_') THEN
        RAISE EXCEPTION 'S2B office attribution refused: reserved root office'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action = 'PROPOSE' THEN
        IF NEW.prior_state IS NOT NULL OR NEW.resulting_state IS DISTINCT FROM 'PROPOSED'
           OR NEW.actor_state IS DISTINCT FROM 'PARTY'
           OR NEW.actor_id IS DISTINCT FROM NEW.enterer_identity_id
           OR NEW.actor_access_epoch IS NULL THEN
            RAISE EXCEPTION 'S2B office attribution refused: PROPOSE must be the proposers act'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF v_role_kind = 'CUSTOM'
           AND NOT COALESCE(public.s015_entitlement_effective(v_lo_uuid, 'CUSTOM_ROLES', NEW.received_at), FALSE) THEN
            RAISE EXCEPTION 'S2B office attribution refused: custom role entitlement absent at receipt'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF v_role_active IS DISTINCT FROM TRUE THEN
            RAISE EXCEPTION 'S2B office attribution refused: superseded definition'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NEW.action IN ('ASSIGN', 'END', 'REASSIGN') THEN
        IF NEW.action = 'ASSIGN'
           AND NEW.sequence = 1
           AND NEW.prior_state IS NULL
           AND NEW.resulting_state = 'ACTIVE'
           AND v_role_code = 'COORDINATOR'
           AND NEW.actor_state = 'PARTY'
           AND NEW.actor_capacity = 'STANDING'
           AND NEW.actor_id = v_membership_identity_id
           AND NEW.actor_id = NEW.enterer_identity_id
           AND NEW.actor_access_epoch IS NOT NULL
           AND NEW.entry_mode = 'TRANSPORT'
           AND NEW.composing_rule_state = 'NA_TRANSPORT'
           AND NEW.composer_state = 'NA_TRANSPORT'
           AND v_founding_xid = pg_catalog.pg_current_xact_id()
           AND EXISTS (
               SELECT 1 FROM public.core_livingorganismevent founding
                WHERE founding.living_organism_id = v_lo_id
                  AND founding.sequence = 1
                  AND founding.xmin = v_founding_xid::xid
                  AND founding.actor_id = NEW.actor_id
                  AND founding.actor_access_epoch = NEW.actor_access_epoch
                  AND founding.authority_basis_id = NEW.authority_basis_id
                  AND founding.actor_state = 'PARTY'
           ) THEN
            SELECT founding.event_uuid, founding.authority_decision_reference
              INTO v_founding_event_uuid, v_founding_authority_decision_reference
              FROM public.core_livingorganismevent founding
             WHERE founding.living_organism_id = v_lo_id
               AND founding.sequence = 1
               AND founding.action = 'FOUND'
               AND founding.xmin = v_founding_xid::xid
               AND founding.actor_id = NEW.actor_id
               AND founding.actor_access_epoch = NEW.actor_access_epoch
               AND founding.authority_basis_id = NEW.authority_basis_id
               AND founding.actor_state = 'PARTY'
             LIMIT 1;
            IF NEW.evidence_reference IS DISTINCT FROM
                ('founding-batch:' || v_founding_event_uuid::text) THEN
                RAISE EXCEPTION 'S2B office attribution refused: the founding appointment must name this LO''s founding act'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NEW.authority_decision_reference IS DISTINCT FROM
                v_founding_authority_decision_reference THEN
                RAISE EXCEPTION 'S2B office attribution refused: the founding appointment must carry that founding act''s authority decision'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM'
           OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
           OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
           OR NEW.enterer_identity_id IS NULL
           OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
           OR NEW.composing_rule_state IS DISTINCT FROM 'NA_TRANSPORT'
           OR NEW.composer_state IS DISTINCT FROM 'NA_TRANSPORT'
           OR (NEW.action = 'ASSIGN' AND (NEW.prior_state IS DISTINCT FROM 'PROPOSED' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'))
           OR (NEW.action IN ('END', 'REASSIGN') AND (NEW.prior_state IS DISTINCT FROM 'ACTIVE' OR NEW.resulting_state IS DISTINCT FROM 'ENDED')) THEN
            RAISE EXCEPTION 'S2B office attribution refused: LO decision attribution required'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.action = 'ASSIGN' THEN
            -- Hold the named version against a concurrent direct deactivation.
            SELECT d.active INTO v_role_active
              FROM public.core_organismroledefinition d
              JOIN public.core_contextualroleassignment a ON a.role_definition_id=d.id
             WHERE a.id=NEW.assignment_id FOR SHARE OF d;
        END IF;
        IF NEW.action = 'ASSIGN' AND v_role_active IS DISTINCT FROM TRUE THEN
            RAISE EXCEPTION 'S2B office attribution refused: superseded definition'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.action = 'ASSIGN' AND EXISTS (
            SELECT 1
              FROM public.core_contextualroleassignment held
              JOIN public.core_organismroledefinition held_definition
                ON held_definition.id = held.role_definition_id
             WHERE held.membership_id = v_membership_id
               AND held.id <> NEW.assignment_id
               AND (held.state = 'ACTIVE' OR public.s015_fold_effective_state(
                   'core_contextualroleassignment', held.id,
                   NEW.effective_at, NEW.recorded_at
               ) = 'ACTIVE')
               AND held_definition.living_organism_id = v_lo_id
               AND held_definition.code = v_role_code
               AND (held_definition.kind IS DISTINCT FROM 'BOARD_OFFICE' OR NOT EXISTS(
                 SELECT FROM public.core_contextualroleassignmentstateevent started
                 WHERE started.assignment_id=held.id AND started.action='ASSIGN'
                 AND public.elect_office_deadline(started.id,NEW.recorded_at)<=NEW.effective_at))
               AND held_definition.scope = 'LIVING_ORGANISM'
        ) THEN
            RAISE EXCEPTION 'S2B office attribution refused: office already held'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSE
        RAISE EXCEPTION 'S2B office attribution refused: unmapped office act'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action IN ('END', 'REASSIGN')
       AND v_role_code = 'COORDINATOR'
       AND v_role_scope = 'LIVING_ORGANISM'
       AND EXISTS (
            SELECT 1
              FROM public.core_livingorganism organism
             WHERE organism.id = v_lo_id
               AND organism.founder_identity_id IS NOT NULL
               AND organism.founder_identity_id = v_membership_identity_id
               AND NEW.enterer_identity_id IS DISTINCT FROM organism.founder_identity_id
       )
       AND NOT EXISTS (
            SELECT 1
              FROM public.core_livingorganismevent birthing
             WHERE birthing.living_organism_id = v_lo_id
               AND birthing.action = 'BIRTHING'
               AND birthing.effective_at <= NEW.effective_at
               AND birthing.recorded_at <= NEW.recorded_at
       )
       AND EXISTS (
            SELECT 1
              FROM public.core_contextualroleassignmentstateevent original
              JOIN public.core_livingorganismevent founding
                ON founding.living_organism_id = v_lo_id
               AND founding.sequence = 1
               AND founding.action = 'FOUND'
             WHERE original.assignment_id = NEW.assignment_id
               AND original.sequence = 1
               AND original.action = 'ASSIGN'
               AND original.prior_state IS NULL
               AND original.resulting_state = 'ACTIVE'
               AND original.actor_state = 'PARTY'
               AND original.actor_id = v_membership_identity_id
               AND original.enterer_identity_id = v_membership_identity_id
               AND (
                    original.xmin = founding.xmin
                    OR (
                        original.authority_basis_id = founding.authority_basis_id
                        AND original.evidence_reference = 'founding-batch:' || founding.event_uuid::text
                        AND original.authority_decision_reference = founding.authority_decision_reference
                    )
               )
       ) THEN
        RAISE EXCEPTION 'C_FOUNDER_PROTECTED'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT decision.* INTO v_decision_row
      FROM public.core_authoritydecision decision
     WHERE decision.decision_reference = NEW.authority_decision_reference;
    IF v_decision_row.decision_reference IS NULL
       OR v_decision_row.outcome <> 'QUALIFIED'
       OR v_decision_row.action <> 'TRANSITION_CONTEXTUAL_ROLE'
    OR v_decision_row.command_subtype <> 'TRANSITION_CONTEXTUAL_ROLE'
       OR v_decision_row.actor_id <> NEW.enterer_identity_id
       OR v_decision_row.authority_reference IS DISTINCT FROM NEW.evidence_reference
       OR v_decision_row.target->>'living_organism_id' IS DISTINCT FROM v_lo_uuid::text
       OR v_decision_row.target->>'event_action' IS DISTINCT FROM NEW.action
       OR (NEW.action = 'PROPOSE' AND (
            v_decision_row.target->>'membership_uuid' IS DISTINCT FROM v_membership_uuid::text
            OR v_decision_row.target->>'role_definition_uuid' IS DISTINCT FROM v_role_uuid::text
       ))
       OR (NEW.action <> 'PROPOSE' AND
            v_decision_row.target->>'assignment_uuid' IS DISTINCT FROM (
                SELECT target_assignment.assignment_uuid::text FROM public.core_contextualroleassignment target_assignment WHERE target_assignment.id = NEW.assignment_id
            )) THEN
        RAISE EXCEPTION 'S2B office attribution refused: governed decision does not match event'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """
S2B_GUARD_OFFICE_ATTRIBUTION_CLOCK = r"""
DECLARE v_lo_id bigint; v_lo_uuid uuid; v_founding_xid xid8; v_membership_uuid uuid;
                v_membership_identity_id bigint; v_role_uuid uuid; v_role_code text; v_role_scope text; v_decision_row record;
                v_founding_event_uuid uuid; v_founding_authority_decision_reference text;
                v_role_kind text; v_role_active boolean; v_membership_id bigint;
BEGIN

    IF starts_with(NEW.evidence_reference,'c108-stand-down:') THEN
      IF NOT public.c108_result_child(NEW.evidence_reference,'offices',NEW.assignment_id) OR
        NEW.composing_rule_reference IS DISTINCT FROM NEW.evidence_reference OR NEW.action IS DISTINCT FROM 'END'
        OR NEW.prior_state NOT IN ('ACTIVE','SUSPENDED') OR NEW.prior_state IS NULL OR NEW.resulting_state IS DISTINCT FROM 'ENDED'
        OR NEW.actor_state IS DISTINCT FROM 'NONE' OR NEW.entry_mode IS DISTINCT FROM 'COMPOSED' OR NEW.actor_id IS NOT NULL
        OR NEW.actor_access_epoch IS NOT NULL OR NEW.actor_capacity IS DISTINCT FROM 'NA_NO_PARTY'
        OR NEW.effective_until IS NOT NULL OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
        OR NOT EXISTS(SELECT FROM public.core_circlestateevent e WHERE e.evidence_reference=NEW.evidence_reference
          AND e.action='STAND_DOWN' AND (e.authority_basis_id,e.authority_decision_reference,e.enterer_identity_id,
            e.occurred_at,e.received_at,e.payload_fingerprint)=(NEW.authority_basis_id,NEW.authority_decision_reference,
            NEW.enterer_identity_id,NEW.occurred_at,NEW.received_at,NEW.payload_fingerprint)
          AND NEW.request_reference=e.evidence_reference||':office:'||NEW.assignment_id AND NEW.idempotency_key=NEW.request_reference)
      THEN PERFORM public.cq_refuse('CQ_COMPLETE'); END IF;
      RETURN NEW;
    END IF;
    IF NEW.composing_rule_reference='ELECT:TERM_END' THEN
      IF NEW.action IS DISTINCT FROM 'END' OR NEW.resulting_state IS DISTINCT FROM 'ENDED'
       OR NEW.prior_state IS NULL OR NEW.prior_state NOT IN ('ACTIVE','SUSPENDED') OR NEW.actor_state IS DISTINCT FROM 'NONE'
       OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
       OR NEW.actor_capacity IS DISTINCT FROM 'NA_NO_PARTY' OR NEW.entry_mode IS DISTINCT FROM 'COMPOSED'
       OR NEW.composing_rule_state IS DISTINCT FROM 'REFERENCE' OR NEW.composer_state IS DISTINCT FROM 'REFERENCE'
       OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
       OR NEW.effective_until IS NOT NULL OR NEW.effective_until_state IS DISTINCT FROM 'NONE'
       OR NEW.received_at IS DISTINCT FROM public.intevia_clock_statement()
       OR NEW.occurred_at IS DISTINCT FROM NEW.effective_at OR NOT EXISTS(
         SELECT FROM public.elect_due_office(NEW.assignment_id,NEW.received_at) q
         JOIN public.core_contextualroleassignmentstateevent e ON e.id=q.source_id
         JOIN public.core_contextualroleassignment a ON a.id=e.assignment_id
         JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
         JOIN public.core_identity i ON i.id=NEW.enterer_identity_id JOIN public.auth_user u ON u.id=i.credential_id
         WHERE i.access_state='active' AND u.is_active
         AND (public.disc_member_state(d.living_organism_id,i.id,NEW.received_at)='ACTIVE' OR
             public.elect_notice_enterer(d.living_organism_id,NEW.assignment_id,i.id,NEW.received_at))
         AND NEW.effective_at=q.due AND NEW.authority_basis_id=e.authority_basis_id
         AND NEW.authority_decision_reference=e.authority_decision_reference
         AND NEW.evidence_reference='elect-expiry:'||e.event_uuid
         AND NEW.request_reference=NEW.evidence_reference AND NEW.idempotency_key=NEW.evidence_reference
         AND NEW.payload_fingerprint=public.c2_target(NEW.evidence_reference))
      THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
      RETURN NEW;
    END IF;

    IF NEW.action='ASSIGN' THEN
      PERFORM 1 FROM public.core_livingorganism lo JOIN public.core_organismroledefinition d
        ON d.living_organism_id=lo.id JOIN public.core_contextualroleassignment a ON a.role_definition_id=d.id
        WHERE a.id=NEW.assignment_id AND d.kind='BOARD_OFFICE' FOR UPDATE OF lo;
    END IF;
    IF NEW.action='ASSIGN' AND EXISTS(
       SELECT FROM public.core_contextualroleassignment a
       JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
       WHERE a.id=NEW.assignment_id AND d.kind='BOARD_OFFICE'
       AND EXISTS(SELECT FROM public.elect_term_slots(NULL,d.id,d.code,NULL,public.intevia_clock_statement()) s
         JOIN public.core_contextualroleassignment other ON other.assignment_uuid=s.occupancy
         JOIN public.core_contextualroleassignmentstateevent assigned ON assigned.assignment_id=other.id AND assigned.action='ASSIGN'
         WHERE EXISTS(SELECT FROM public.core_cqact i WHERE i.act_uuid IN (NEW.event_uuid,assigned.event_uuid)
           AND (i.operation='RECOGNISE' OR (i.operation='IMPLEMENT' AND i.facts->'domain_plan' ? 'incumbent')))
         AND other.id<>a.id AND (s.ends IS NULL OR s.ends>s.starts)
         AND (s.ends IS NULL OR s.ends>NEW.effective_at)
         AND (NEW.effective_until IS NULL OR s.starts<NEW.effective_until)))
    THEN PERFORM public.cq_refuse('ELECT_OCCUPIED'); END IF;

    IF NEW.composing_rule_reference='ELECT:SUCCESSOR_LEFT' THEN
       IF NEW.action IS DISTINCT FROM 'END' OR NEW.prior_state IS DISTINCT FROM 'ACTIVE'
          OR NEW.resulting_state IS DISTINCT FROM 'ENDED' OR NEW.actor_state IS DISTINCT FROM 'NONE'
          OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL OR NEW.actor_capacity IS DISTINCT FROM 'NA_NO_PARTY'
          OR NEW.entry_mode IS DISTINCT FROM 'COMPOSED' OR NEW.composing_rule_state IS DISTINCT FROM 'REFERENCE'
          OR NEW.composer_state IS DISTINCT FROM 'REFERENCE' OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
          OR NEW.effective_until IS NOT NULL OR NEW.effective_until_state IS DISTINCT FROM 'NONE'
          OR NOT EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent h
            JOIN public.core_contextualroleassignment a ON a.id=h.assignment_id
            JOIN public.core_organismmembershiptransition t ON t.membership_id=a.membership_id
            WHERE h.id=NEW.predecessor_id AND h.assignment_id=NEW.assignment_id AND h.action='ASSIGN' AND t.resulting_state='ENDED'
            AND public.elect_pending_successor(h.event_uuid,t.membership_id,'OFFICE',t.effective_at,t.received_at)
            AND NEW.evidence_reference='elect-departure:'||t.event_uuid||':'||h.event_uuid
            AND NEW.enterer_identity_id=t.enterer_identity_id AND NEW.authority_basis_id=t.authority_basis_id
            AND NEW.authority_decision_reference=t.authority_decision_reference AND NEW.payload_fingerprint=t.payload_fingerprint
            AND NEW.request_reference=NEW.evidence_reference AND NEW.idempotency_key=NEW.evidence_reference
            AND (NEW.effective_at,NEW.occurred_at,NEW.received_at)=(h.effective_at,t.received_at,t.received_at))
       THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
       RETURN NEW;
    END IF;

    IF NEW.action='ASSIGN' AND starts_with(NEW.evidence_reference,'elect-recognised:') THEN
      IF NEW.prior_state IS DISTINCT FROM 'PROPOSED' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'
         OR NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM' OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
         OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT' OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
         OR NOT public.elect_recognition_office(NEW.assignment_id,NEW.event_uuid,NEW.enterer_identity_id,
             NEW.authority_decision_reference,NEW.effective_at,NEW.effective_until)
         OR NOT EXISTS(SELECT FROM public.core_cqact a JOIN public.core_livingorganism lo ON a.organism_id=lo.id
           WHERE a.act_uuid=NEW.event_uuid AND NEW.evidence_reference='elect-recognised:'||a.act_uuid
           AND NEW.request_reference='recognise:'||a.act_uuid AND NEW.idempotency_key=NEW.request_reference
           AND NEW.authority_basis_id=lo.founding_authority_basis_id AND a.fingerprint=NEW.payload_fingerprint
           AND NEW.occurred_at=a.received_at AND NEW.received_at=a.received_at)
      THEN PERFORM public.cq_refuse('ELECT_RECOGNISER'); END IF;
      RETURN NEW;
    END IF;
    IF NEW.action='ASSIGN' AND NEW.evidence_reference LIKE 'elect-result:%' THEN
      IF NEW.resulting_state IS DISTINCT FROM 'ACTIVE' OR NEW.prior_state IS DISTINCT FROM 'PROPOSED'
         OR NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM' OR NEW.actor_id IS NOT NULL
         OR NEW.actor_access_epoch IS NOT NULL OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
         OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
         OR NOT public.elect_office_source(NEW.assignment_id,NEW.event_uuid,NEW.enterer_identity_id,
           NEW.authority_decision_reference,NEW.effective_at,NEW.effective_until)
         OR NOT EXISTS(SELECT FROM public.core_cqact i JOIN public.core_livingorganism lo ON lo.id=i.organism_id
           WHERE i.act_uuid=NEW.event_uuid AND i.received_at=NEW.received_at AND i.effective_at=NEW.occurred_at
             AND i.fingerprint=NEW.payload_fingerprint AND NEW.authority_basis_id=lo.founding_authority_basis_id
             AND NEW.evidence_reference='elect-result:'||(i.request->>'result'))
         THEN PERFORM public.cq_refuse('ELECT_RESULT'); END IF;
      RETURN NEW;
    END IF;
        SELECT lo.id, lo.organism_id, lo.founding_insert_xid, m.membership_uuid,
                     m.identity_id, d.role_uuid, d.code, d.scope,
                     d.kind, d.active, m.id
            INTO v_lo_id, v_lo_uuid, v_founding_xid, v_membership_uuid,
                     v_membership_identity_id, v_role_uuid, v_role_code, v_role_scope,
                     v_role_kind, v_role_active, v_membership_id
      FROM public.core_contextualroleassignment a
      JOIN public.core_organismmembership m ON m.id = a.membership_id
      JOIN public.core_livingorganism lo ON lo.id = m.living_organism_id
      JOIN public.core_organismroledefinition d ON d.id = a.role_definition_id
     WHERE a.id = NEW.assignment_id;
    IF v_lo_uuid IS NULL THEN
        RAISE EXCEPTION 'S2B office attribution refused: assignment context missing'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF v_role_scope IS DISTINCT FROM 'LIVING_ORGANISM' THEN
        RETURN NEW;
    END IF;

    IF starts_with(v_role_code,'INTEVIA_') AND public.r_genesis_context(
         v_lo_id,NEW.authority_decision_reference,NEW.actor_id,NEW.effective_at) THEN
        IF v_role_code NOT IN ('INTEVIA_FOUNDER_STEWARD','INTEVIA_PRIVACY_COORDINATOR')
           OR v_membership_identity_id IS DISTINCT FROM NEW.actor_id OR NEW.sequence<>1 OR NEW.prior_state IS NOT NULL
           OR NEW.action IS DISTINCT FROM 'ASSIGN' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'
           OR NEW.actor_state IS DISTINCT FROM 'PARTY' OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
           OR NEW.enterer_identity_id IS DISTINCT FROM NEW.actor_id OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
           OR NEW.effective_until_state IS DISTINCT FROM 'NONE' OR NEW.effective_until IS NOT NULL
           OR NEW.received_at IS DISTINCT FROM NEW.effective_at OR NEW.occurred_at IS DISTINCT FROM NEW.effective_at
           OR v_role_active IS DISTINCT FROM true OR NOT EXISTS(
              SELECT FROM public.core_rootgenesis g JOIN public.core_livingorganismevent f ON f.living_organism_id=v_lo_id AND f.sequence=1
              WHERE g.organism_uuid=v_lo_uuid AND NEW.authority_basis_id=g.basis_id
              AND NEW.actor_access_epoch=(SELECT access_epoch FROM public.core_identity WHERE id=g.founder_id)
              AND NEW.evidence_reference='founding-batch:'||f.event_uuid
              AND NEW.payload_fingerprint=g.request_fingerprint) THEN
            RAISE EXCEPTION 'R_GENESIS_OFFICE_SOURCE' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF starts_with(v_role_code, 'INTEVIA_') THEN
        RAISE EXCEPTION 'S2B office attribution refused: reserved root office'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action = 'PROPOSE' THEN
        IF NEW.prior_state IS NOT NULL OR NEW.resulting_state IS DISTINCT FROM 'PROPOSED'
           OR NEW.actor_state IS DISTINCT FROM 'PARTY'
           OR NEW.actor_id IS DISTINCT FROM NEW.enterer_identity_id
           OR NEW.actor_access_epoch IS NULL THEN
            RAISE EXCEPTION 'S2B office attribution refused: PROPOSE must be the proposers act'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF v_role_kind = 'CUSTOM'
           AND NOT COALESCE(public.s015_entitlement_effective(v_lo_uuid, 'CUSTOM_ROLES', NEW.received_at), FALSE) THEN
            RAISE EXCEPTION 'S2B office attribution refused: custom role entitlement absent at receipt'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF v_role_active IS DISTINCT FROM TRUE THEN
            RAISE EXCEPTION 'S2B office attribution refused: superseded definition'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF NEW.action IN ('ASSIGN', 'END', 'REASSIGN') THEN
        IF NEW.action = 'ASSIGN'
           AND NEW.sequence = 1
           AND NEW.prior_state IS NULL
           AND NEW.resulting_state = 'ACTIVE'
           AND v_role_code = 'COORDINATOR'
           AND NEW.actor_state = 'PARTY'
           AND NEW.actor_capacity = 'STANDING'
           AND NEW.actor_id = v_membership_identity_id
           AND NEW.actor_id = NEW.enterer_identity_id
           AND NEW.actor_access_epoch IS NOT NULL
           AND NEW.entry_mode = 'TRANSPORT'
           AND NEW.composing_rule_state = 'NA_TRANSPORT'
           AND NEW.composer_state = 'NA_TRANSPORT'
           AND v_founding_xid = pg_catalog.pg_current_xact_id()
           AND EXISTS (
               SELECT 1 FROM public.core_livingorganismevent founding
                WHERE founding.living_organism_id = v_lo_id
                  AND founding.sequence = 1
                  AND founding.xmin = v_founding_xid::xid
                  AND founding.actor_id = NEW.actor_id
                  AND founding.actor_access_epoch = NEW.actor_access_epoch
                  AND founding.authority_basis_id = NEW.authority_basis_id
                  AND founding.actor_state = 'PARTY'
           ) THEN
            SELECT founding.event_uuid, founding.authority_decision_reference
              INTO v_founding_event_uuid, v_founding_authority_decision_reference
              FROM public.core_livingorganismevent founding
             WHERE founding.living_organism_id = v_lo_id
               AND founding.sequence = 1
               AND founding.action = 'FOUND'
               AND founding.xmin = v_founding_xid::xid
               AND founding.actor_id = NEW.actor_id
               AND founding.actor_access_epoch = NEW.actor_access_epoch
               AND founding.authority_basis_id = NEW.authority_basis_id
               AND founding.actor_state = 'PARTY'
             LIMIT 1;
            IF NEW.evidence_reference IS DISTINCT FROM
                ('founding-batch:' || v_founding_event_uuid::text) THEN
                RAISE EXCEPTION 'S2B office attribution refused: the founding appointment must name this LO''s founding act'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NEW.authority_decision_reference IS DISTINCT FROM
                v_founding_authority_decision_reference THEN
                RAISE EXCEPTION 'S2B office attribution refused: the founding appointment must carry that founding act''s authority decision'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END IF;
        IF NEW.actor_state IS DISTINCT FROM 'LIVING_ORGANISM'
           OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
           OR NEW.actor_capacity IS DISTINCT FROM 'STANDING'
           OR NEW.enterer_identity_id IS NULL
           OR NEW.entry_mode IS DISTINCT FROM 'TRANSPORT'
           OR NEW.composing_rule_state IS DISTINCT FROM 'NA_TRANSPORT'
           OR NEW.composer_state IS DISTINCT FROM 'NA_TRANSPORT'
           OR (NEW.action = 'ASSIGN' AND (NEW.prior_state IS DISTINCT FROM 'PROPOSED' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'))
           OR (NEW.action IN ('END', 'REASSIGN') AND (NEW.prior_state IS DISTINCT FROM 'ACTIVE' OR NEW.resulting_state IS DISTINCT FROM 'ENDED')) THEN
            RAISE EXCEPTION 'S2B office attribution refused: LO decision attribution required'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.action = 'ASSIGN' THEN
            -- Hold the named version against a concurrent direct deactivation.
            SELECT d.active INTO v_role_active
              FROM public.core_organismroledefinition d
              JOIN public.core_contextualroleassignment a ON a.role_definition_id=d.id
             WHERE a.id=NEW.assignment_id FOR SHARE OF d;
        END IF;
        IF NEW.action = 'ASSIGN' AND v_role_active IS DISTINCT FROM TRUE THEN
            RAISE EXCEPTION 'S2B office attribution refused: superseded definition'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.action = 'ASSIGN' AND EXISTS (
            SELECT 1
              FROM public.core_contextualroleassignment held
              JOIN public.core_organismroledefinition held_definition
                ON held_definition.id = held.role_definition_id
             WHERE held.membership_id = v_membership_id
               AND held.id <> NEW.assignment_id
               AND (held.state = 'ACTIVE' OR public.s015_fold_effective_state(
                   'core_contextualroleassignment', held.id,
                   NEW.effective_at, NEW.recorded_at
               ) = 'ACTIVE')
               AND held_definition.living_organism_id = v_lo_id
               AND held_definition.code = v_role_code
               AND (held_definition.kind IS DISTINCT FROM 'BOARD_OFFICE' OR NOT EXISTS(
                 SELECT FROM public.core_contextualroleassignmentstateevent started
                 WHERE started.assignment_id=held.id AND started.action='ASSIGN'
                 AND public.elect_office_deadline(started.id,NEW.recorded_at)<=NEW.effective_at))
               AND held_definition.scope = 'LIVING_ORGANISM'
        ) THEN
            RAISE EXCEPTION 'S2B office attribution refused: office already held'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSE
        RAISE EXCEPTION 'S2B office attribution refused: unmapped office act'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.action IN ('END', 'REASSIGN')
       AND v_role_code = 'COORDINATOR'
       AND v_role_scope = 'LIVING_ORGANISM'
       AND EXISTS (
            SELECT 1
              FROM public.core_livingorganism organism
             WHERE organism.id = v_lo_id
               AND organism.founder_identity_id IS NOT NULL
               AND organism.founder_identity_id = v_membership_identity_id
               AND NEW.enterer_identity_id IS DISTINCT FROM organism.founder_identity_id
       )
       AND NOT EXISTS (
            SELECT 1
              FROM public.core_livingorganismevent birthing
             WHERE birthing.living_organism_id = v_lo_id
               AND birthing.action = 'BIRTHING'
               AND birthing.effective_at <= NEW.effective_at
               AND birthing.recorded_at <= NEW.recorded_at
       )
       AND EXISTS (
            SELECT 1
              FROM public.core_contextualroleassignmentstateevent original
              JOIN public.core_livingorganismevent founding
                ON founding.living_organism_id = v_lo_id
               AND founding.sequence = 1
               AND founding.action = 'FOUND'
             WHERE original.assignment_id = NEW.assignment_id
               AND original.sequence = 1
               AND original.action = 'ASSIGN'
               AND original.prior_state IS NULL
               AND original.resulting_state = 'ACTIVE'
               AND original.actor_state = 'PARTY'
               AND original.actor_id = v_membership_identity_id
               AND original.enterer_identity_id = v_membership_identity_id
               AND (
                    original.xmin = founding.xmin
                    OR (
                        original.authority_basis_id = founding.authority_basis_id
                        AND original.evidence_reference = 'founding-batch:' || founding.event_uuid::text
                        AND original.authority_decision_reference = founding.authority_decision_reference
                    )
               )
       ) THEN
        RAISE EXCEPTION 'C_FOUNDER_PROTECTED'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT decision.* INTO v_decision_row
      FROM public.core_authoritydecision decision
     WHERE decision.decision_reference = NEW.authority_decision_reference;
    IF v_decision_row.decision_reference IS NULL
       OR v_decision_row.outcome <> 'QUALIFIED'
       OR v_decision_row.action <> 'TRANSITION_CONTEXTUAL_ROLE'
    OR v_decision_row.command_subtype <> 'TRANSITION_CONTEXTUAL_ROLE'
       OR v_decision_row.actor_id <> NEW.enterer_identity_id
       OR v_decision_row.authority_reference IS DISTINCT FROM NEW.evidence_reference
       OR v_decision_row.target->>'living_organism_id' IS DISTINCT FROM v_lo_uuid::text
       OR v_decision_row.target->>'event_action' IS DISTINCT FROM NEW.action
       OR (NEW.action = 'PROPOSE' AND (
            v_decision_row.target->>'membership_uuid' IS DISTINCT FROM v_membership_uuid::text
            OR v_decision_row.target->>'role_definition_uuid' IS DISTINCT FROM v_role_uuid::text
       ))
       OR (NEW.action <> 'PROPOSE' AND
            v_decision_row.target->>'assignment_uuid' IS DISTINCT FROM (
                SELECT target_assignment.assignment_uuid::text FROM public.core_contextualroleassignment target_assignment WHERE target_assignment.id = NEW.assignment_id
            )) THEN
        RAISE EXCEPTION 'S2B office attribution refused: governed decision does not match event'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END """

FUNCTIONS = (
    ("public.elect_vacancy_guard()", ELECT_VACANCY_GUARD_LANDED, ELECT_VACANCY_GUARD_CLOCK),
    ("public.sbg_guard_circleadmissiondecision()", SBG_GUARD_CIRCLEADMISSIONDECISION_LANDED, SBG_GUARD_CIRCLEADMISSIONDECISION_CLOCK),
    ("public.sbg_guard_circleparticipation_occupancy()", SBG_GUARD_CIRCLEPARTICIPATION_OCCUPANCY_LANDED, SBG_GUARD_CIRCLEPARTICIPATION_OCCUPANCY_CLOCK),
    ("public.sbg_guard_membership_end_completeness()", SBG_GUARD_MEMBERSHIP_END_COMPLETENESS_LANDED, SBG_GUARD_MEMBERSHIP_END_COMPLETENESS_CLOCK),
    ("public.cq4_checked(bigint, uuid, jsonb, timestamp with time zone)", CQ4_CHECKED_LANDED, CQ4_CHECKED_CLOCK),
    ("public.cq_execute(uuid, uuid, uuid, text, uuid, jsonb, bigint, bigint, timestamp with time zone)", CQ_EXECUTE_LANDED, CQ_EXECUTE_CLOCK),
    ("public.c108_check_stand_down()", C108_CHECK_STAND_DOWN_LANDED, C108_CHECK_STAND_DOWN_CLOCK),
    ("public.c108_close_for_activation()", C108_CLOSE_FOR_ACTIVATION_LANDED, C108_CLOSE_FOR_ACTIVATION_CLOCK),
    ("public.cq_complete()", CQ_COMPLETE_LANDED, CQ_COMPLETE_CLOCK),
    ("public.circle_coordinator_event()", CIRCLE_COORDINATOR_EVENT_LANDED, CIRCLE_COORDINATOR_EVENT_CLOCK),
    ("public.circle_guard_active_event()", CIRCLE_GUARD_ACTIVE_EVENT_LANDED, CIRCLE_GUARD_ACTIVE_EVENT_CLOCK),
    ("public.s2b_guard_office_attribution()", S2B_GUARD_OFFICE_ATTRIBUTION_LANDED, S2B_GUARD_OFFICE_ATTRIBUTION_CLOCK),
)


def install(apps, schema_editor):
    """Move these functions onto the INTEVIA Clock."""
    clock.convert(schema_editor, FUNCTIONS)


def uninstall(apps, schema_editor):
    """Put the landed functions back."""
    clock.restore(schema_editor, FUNCTIONS)


class Migration(migrations.Migration):
    dependencies = (("core", "0100_clock_storage_0073_0080"),)
    operations = (migrations.RunPython(install, uninstall),)
