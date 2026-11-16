"""C1-33. A qualified office appointment reseats its own vacancy-dormant Circle."""
from importlib import import_module

from django.db import migrations

records = import_module("core.migrations.0106_circle_vacancy_records")
committee = import_module("core.migrations.0107_committee_circle_dormancy")
definition = records.episode.definition
replace_once = committee.replace_once

CIRCLE_APPOINTING_OFFICE_OLD = """CREATE OR REPLACE FUNCTION public.circle_appointing_office(c bigint, actor bigint, subject bigint, nominee_office uuid, at_time timestamp with time zone, known timestamp with time zone)
 RETURNS uuid
 LANGUAGE sql
 STABLE STRICT
AS $function$
 SELECT CASE WHEN public.cmt_appointment(c,actor,subject,nominee_office,at_time,known) IS NOT NULL
 THEN public.cmt_appointment(c,actor,subject,nominee_office,at_time,known)
 WHEN public.cmt_empty(c,at_time,known) THEN public.cmt_no_recovery()
 WHEN actor=subject AND EXISTS(
    SELECT FROM public.core_circleparticipation p JOIN public.core_circlestateevent e ON e.id=p.birth_event_id
    WHERE p.circle_id=c AND e.enterer_identity_id=actor AND e.circle_id=c
    AND public.circle_place_at(c,p.membership_id,at_time,known)
    AND e.authority_decision_reference=public.circle_office_decision(
        public.circle_lo_office(x.parent_organism_id,actor,at_time,known),x.circle_uuid,
        'FORM:GENERIC:'||encode(sha256(convert_to(substr(e.evidence_reference,16),'UTF8')),'hex'),actor,actor)
    AND public.s015_fold_effective_state('core_circle',c,at_time,known)='DORMANT')
 THEN public.circle_lo_office(x.parent_organism_id,actor,at_time,known)
 WHEN public.circle_vacant(c,nominee_office,at_time,known) THEN
    coalesce(public.circle_lo_office(x.parent_organism_id,actor,at_time,known),
      (SELECT p.participation_uuid FROM public.core_circleparticipation p JOIN public.core_organismmembership m
        ON m.id=p.membership_id AND m.identity_id=actor
       WHERE p.circle_id=c AND actor=subject AND public.circle_direct_volunteer(x.parent_organism_id,at_time,known)
         AND public.circle_office_place(c,m.id,'place:'||p.participation_uuid,at_time,known)))
 ELSE public.circle_current_office(c,actor,at_time,known,nominee_office) END
 FROM public.core_circle x WHERE x.id=c AND x.kind='GENERIC'
$function$
"""

CIRCLE_APPOINTMENT_MARKER_OLD = """CREATE OR REPLACE FUNCTION public.circle_appointment_marker(c bigint, incoming uuid, actor bigint, subject bigint, source uuid, place_ref text, at_time timestamp with time zone, known timestamp with time zone)
 RETURNS text
 LANGUAGE sql
 STABLE STRICT
AS $function$
 SELECT CASE WHEN
 source=public.cmt_appointment(c,actor,subject,incoming,at_time,known) THEN '|CMT'
 WHEN NOT public.circle_vacant(c,incoming,at_time,known) THEN ''
   WHEN source=public.circle_lo_office(x.parent_organism_id,actor,at_time,known) THEN '|CB1'
   WHEN actor=subject AND place_ref='place:'||source AND public.circle_direct_volunteer(x.parent_organism_id,at_time,known)
      THEN '|RC1' END
 FROM public.core_circle x WHERE x.id=c AND x.kind='GENERIC'
$function$
"""

SBG_GUARD_CIRCLEPARTICIPATION_OLD = """CREATE OR REPLACE FUNCTION public.sbg_guard_circleparticipation()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
$function$
"""

CIRCLE_GUARD_OTHER_OFFICE_OLD = """CREATE OR REPLACE FUNCTION public.circle_guard_other_office()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
 IF EXISTS (SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d
      ON d.id=a.role_definition_id WHERE a.id=NEW.assignment_id AND a.circle_id IS NOT NULL
      AND d.scope='CIRCLE' AND d.code<>'COORDINATOR') THEN
    RAISE EXCEPTION 'CIRCLE_G4_OFFICE_SOURCE' USING ERRCODE='23514';
 END IF;
 -- 0060 qualifies each Circle Coordinator appointment; 0021 owns structural mismatches.
 RETURN NEW;
END $function$
"""

CIRCLE_COORDINATOR_EVENT_OLD = """CREATE OR REPLACE FUNCTION public.circle_coordinator_event()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
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
END $function$
"""

CIRCLE_CLOSE_ON_FINAL_EXIT_OLD = ("CREATE OR REPLACE FUNCTION public.circle_close_on_final_exit()\n RETURNS trigger\n LANGUAGE plpgsql\nAS $function$"
    + __import__("importlib").import_module("core.migrations.0099_clock_storage_0059_0072").CIRCLE_CLOSE_ON_FINAL_EXIT_CLOCK + "$function$\n")

# Evaluate the mandate before the incoming appointment. An AFTER INSERT check
# sees its own ASSIGN; the ordinary current-state reader intentionally includes it.
APART_BEFORE = "public.cmt_live_apart_before_assignment(uuid,timestamptz,timestamptz,uuid)"
APART_BEFORE_SQL = replace_once(
    committee.APART_SQL, "public.cmt_live_apart(", "public.cmt_live_apart_before_assignment(",
).replace("p_known timestamp with time zone)", "p_known timestamp with time zone, incoming uuid)").replace(
    "public.circle_episode(a.circle_id,p_at,p_known)",
    "public.circle_episode_before_assignment(a.circle_id,incoming,p_at,p_known)",
)
# This reader can be called without an incoming office.
assert " STRICT" not in APART_BEFORE_SQL
APART_NEW = committee.APART_SQL.split("AS $function$")[0] + """AS $function$
 SELECT public.cmt_live_apart_before_assignment(p_binding,p_at,p_known,NULL)
$function$
"""
APPOINTMENT_NEW = replace_once(
    committee.APPOINTMENT_NEW, "public.cmt_live_apart(m.binding,p_cut,p_known)",
    "public.cmt_live_apart_before_assignment(m.binding,p_cut,p_known,p_assignment)",
)

RESEATABLE = "public.circle_reseatable(bigint,uuid,timestamptz,timestamptz)"
RESEATABLE_SQL = """CREATE OR REPLACE FUNCTION public.circle_reseatable(c bigint, incoming uuid, at_time timestamp with time zone, known timestamp with time zone)
 RETURNS boolean
 LANGUAGE sql
 STABLE
AS $function$
 SELECT EXISTS (SELECT FROM public.core_circle x
 JOIN public.core_livingorganism lo ON lo.id=x.parent_organism_id
 CROSS JOIN LATERAL public.circle_episode_before_assignment(c,incoming,at_time,known) ep
 WHERE x.id=c AND ep.status='EXPIRED'
 AND (NOT lo.platform_root OR public.r_circle_root_qualified(lo.id,at_time,known))
 AND public.s015_fold_effective_state('core_livingorganism',lo.id,at_time,known)='ACTIVE'
 AND EXISTS (SELECT FROM public.core_circlestateevent activated WHERE activated.circle_id=c
     AND activated.action='ACTIVATE' AND activated.effective_at<=ep.loss AND activated.recorded_at<=known)
 AND NOT EXISTS (SELECT FROM public.cmt_current(c,at_time,known) m
     WHERE public.cmt_live_apart_before_assignment(m.binding,at_time,known,incoming)))
$function$
"""

APPOINTING_NEW = replace_once(CIRCLE_APPOINTING_OFFICE_OLD,
    " WHEN actor=subject AND EXISTS(", """ WHEN public.circle_reseatable(c,nominee_office,at_time,known)
 THEN public.circle_lo_office(x.parent_organism_id,actor,at_time,known)
 WHEN actor=subject AND NOT EXISTS (SELECT FROM public.core_circlestateevent previous
    WHERE previous.circle_id=c AND previous.action='ACTIVATE'
    AND previous.effective_at<=at_time AND previous.received_at<=known AND previous.recorded_at<=known)
 AND EXISTS(""")
MARKER_NEW = replace_once(CIRCLE_APPOINTMENT_MARKER_OLD,
    " WHEN NOT public.circle_vacant", """ WHEN public.circle_reseatable(c,incoming,at_time,known)
   AND source=public.circle_lo_office(x.parent_organism_id,actor,at_time,known) THEN '|CB1'
 WHEN NOT public.circle_vacant""")
PLACE_NEW = replace_once(SBG_GUARD_CIRCLEPARTICIPATION_OLD,
    "WHERE NOT public.circle_vacant(c.id,NULL,t,k)", """WHERE public.cmt_empty(c.id,t,k)
                   OR NOT (public.circle_vacant(c.id,NULL,t,k) OR public.circle_reseatable(c.id,NULL,t,k))""")

# Reuse the existing BEFORE INSERT trigger. Settle before ASSIGN can hide the
# episode. A subsequent refusal rolls both records back with the office write.
BEFORE_OFFICE_NEW = replace_once(CIRCLE_GUARD_OTHER_OFFICE_OLD, "BEGIN", """DECLARE target record; ep record; effect_cause uuid; execution timestamptz;
BEGIN""")
BEFORE_OFFICE_NEW = replace_once(BEFORE_OFFICE_NEW, " RETURN NEW;", """ IF NEW.action='ASSIGN' THEN
   SELECT a.assignment_uuid,c.id AS circle_id,c.parent_organism_id AS lo_id,lo.organism_id AS lo_uuid
   INTO target FROM public.core_contextualroleassignment a
   JOIN public.core_circle c ON c.id=a.circle_id AND c.kind='GENERIC'
   JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id
   JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id AND d.scope='CIRCLE' AND d.code='COORDINATOR'
   WHERE a.id=NEW.assignment_id;
   IF FOUND THEN
     PERFORM public.s015_lock_organisms(ARRAY[target.lo_uuid]);
     execution:=public.intevia_clock_instant();
     SELECT * INTO ep FROM public.circle_episode(target.circle_id,execution,execution);
     IF FOUND THEN
       IF ep.status='EXPIRED' THEN
         SELECT cause INTO effect_cause FROM public.circle_episode(target.circle_id,NEW.effective_at,execution)
           WHERE status='EXPIRED';
         IF effect_cause IS DISTINCT FROM ep.cause THEN
           RAISE EXCEPTION 'CIRCLE_RESEAT_CUT' USING ERRCODE='23514'; END IF;
       END IF;
       PERFORM public.circle_record_vacancy(target.lo_id,ep.cause,'OPENED',NEW.enterer_identity_id);
       IF ep.status='EXPIRED' THEN
         PERFORM public.circle_record_vacancy(target.lo_id,ep.cause,'DORMANT',NEW.enterer_identity_id);
       END IF;
     END IF;
   END IF;
 END IF;
 RETURN NEW;""")

# The existing origin trigger owns the transaction id, including across savepoints.
ORIGIN_NEW = replace_once(records.TE_ORIGIN_APPLIES_NEW,
    "THEN p_row->>'action'='END' AND p_row->>'evidence_reference' LIKE 'elect-expiry:%'",
    """THEN (p_row->>'action'='END' AND p_row->>'evidence_reference' LIKE 'elect-expiry:%')
     OR (p_row->>'action'='ASSIGN' AND split_part(p_row->>'evidence_reference','|',7) IN ('CB1','CMT'))""",
)
COORDINATOR_NEW = replace_once(CIRCLE_COORDINATOR_EVENT_OLD,
    "expected_ref text; marker text;", """expected_ref text; marker text;
        ep record; head public.core_circlestateevent; key_ text; ref_ text; execution_ timestamptz;""")
COORDINATOR_NEW = replace_once(COORDINATOR_NEW, " END LOOP;\n RETURN NULL;", """ END LOOP;
 IF office.kind='GENERIC' AND NEW.action='ASSIGN' THEN
   SELECT * INTO ep FROM public.circle_episode_before_assignment(office.circle_id,office.assignment_uuid,
       NEW.effective_at,NEW.recorded_at) WHERE status='EXPIRED';
   execution_:=public.intevia_clock_instant();
   IF (SELECT check_ep.cause FROM public.circle_episode_before_assignment(office.circle_id,
       office.assignment_uuid,execution_,execution_) check_ep WHERE check_ep.status='EXPIRED')
       IS DISTINCT FROM ep.cause THEN
     RAISE EXCEPTION 'CIRCLE_RESEAT_CUT' USING ERRCODE='23514';
   END IF;
   IF ep.cause IS NOT NULL THEN
     SELECT * INTO STRICT head FROM public.core_circlestateevent WHERE circle_id=office.circle_id ORDER BY sequence DESC LIMIT 1;
     IF marker IS NULL OR marker NOT IN ('|CB1','|CMT') OR NOT EXISTS (
         SELECT FROM public.core_circlestateevent dorm WHERE dorm.circle_id=office.circle_id
         AND dorm.action='BECOME_DORMANT' AND dorm.evidence_reference='vacancy:'||ep.cause
         AND dorm.effective_at=ep.deadline AND dorm.recorded_at<=NEW.recorded_at) THEN
       RAISE EXCEPTION 'CIRCLE_RESEAT_SOURCE' USING ERRCODE='23514'; END IF;
     ref_:='vacancy:'||ep.cause||'|assign:'||NEW.event_uuid;
     key_:='circle-vacancy:'||ep.cause||':RESEAT';
     PERFORM public.s015_append_event('core_circle',office.circle_id,'RESEAT','ACTIVE',NEW.effective_at,
       NEW.occurred_at,NEW.received_at,NEW.enterer_identity_id,NEW.authority_basis_id,NEW.authority_decision_reference,
       ref_,key_,public.c2_target(ref_),'{}',NULL,NEW.event_uuid,head.id,head.sequence,NULL,'CIRCLES:VACANCY',key_);
     PERFORM public.s015_refresh_governed_cache('core_circle',office.circle_id,NEW.effective_at,public.intevia_clock_instant());
   END IF;
 END IF;
 RETURN NULL;""")

APPEND_NEW = replace_once(records.S015_APPEND_EVENT_NEW,
    "'OPEN_VACANCY','BECOME_DORMANT') THEN", "'OPEN_VACANCY','BECOME_DORMANT','RESEAT') THEN")
# Reuse the triggering ASSIGN's UUID on the other chain. Its checked proof
# fixes the appointment source, place, subject and consent independently.
GUARD_NEW = replace_once(records.CIRCLE_GUARD_ACTIVE_EVENT_NEW,
    """   -- This build has no human reseat writer. CB-4 supplies its positive ASSIGN proof.
   RAISE EXCEPTION 'CIRCLE_RESEAT_SOURCE' USING ERRCODE='23514';""",
    """   IF NEW.prior_state IS DISTINCT FROM 'DORMANT' OR NEW.resulting_state IS DISTINCT FROM 'ACTIVE'
     OR NEW.actor_state IS DISTINCT FROM 'NONE' OR NEW.actor_id IS NOT NULL OR NEW.actor_access_epoch IS NOT NULL
     OR NEW.actor_capacity IS DISTINCT FROM 'NA_NO_PARTY' OR NEW.entry_mode IS DISTINCT FROM 'COMPOSED'
     OR NEW.composing_rule_state IS DISTINCT FROM 'REFERENCE' OR NEW.composer_state IS DISTINCT FROM 'REFERENCE'
     OR NEW.composing_rule_reference IS DISTINCT FROM 'CIRCLES:VACANCY'
     OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
     OR NEW.effective_until_state IS DISTINCT FROM 'NONE' OR NEW.effective_until IS NOT NULL
     OR NEW.temporal_basis_kind IS NOT NULL OR NEW.temporal_basis_reference IS NOT NULL
     OR NEW.payload_fingerprint IS DISTINCT FROM public.c2_target(NEW.evidence_reference)
     OR NEW.idempotency_key IS DISTINCT FROM NEW.request_reference
     OR NOT public.te_made_here('core_contextualroleassignmentstateevent',NEW.event_uuid::text)
     OR NOT EXISTS(SELECT FROM public.core_contextualroleassignmentstateevent seated
        JOIN public.core_contextualroleassignment a ON a.id=seated.assignment_id AND a.circle_id=NEW.circle_id
        JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id AND d.scope='CIRCLE' AND d.code='COORDINATOR'
        CROSS JOIN LATERAL public.circle_episode_before_assignment(a.circle_id,a.assignment_uuid,NEW.effective_at,NEW.recorded_at) ep
        JOIN public.core_circlestateevent dorm ON dorm.circle_id=NEW.circle_id AND dorm.action='BECOME_DORMANT'
          AND dorm.evidence_reference='vacancy:'||ep.cause AND dorm.effective_at=ep.deadline AND dorm.recorded_at<=NEW.recorded_at
        JOIN public.core_circlestateevent head ON head.id=NEW.predecessor_id AND head.circle_id=NEW.circle_id
          AND head.sequence=NEW.sequence-1 AND head.sequence=NEW.predecessor_sequence AND head.sequence>=dorm.sequence
        WHERE seated.action='ASSIGN' AND seated.event_uuid=NEW.event_uuid AND ep.status='EXPIRED'
          AND seated.resulting_state='ACTIVE' AND split_part(seated.evidence_reference,'|',7) IN ('CB1','CMT')
          AND (NEW.enterer_identity_id,NEW.authority_basis_id,NEW.authority_decision_reference,NEW.effective_at,NEW.occurred_at,NEW.received_at)
            IS NOT DISTINCT FROM (seated.enterer_identity_id,seated.authority_basis_id,seated.authority_decision_reference,
                                   seated.effective_at,seated.occurred_at,seated.received_at)
          AND NEW.request_reference='circle-vacancy:'||ep.cause||':RESEAT'
          AND NEW.evidence_reference='vacancy:'||ep.cause||'|assign:'||seated.event_uuid
          AND NOT EXISTS(SELECT FROM public.core_circlestateevent r WHERE r.circle_id=NEW.circle_id
            AND (r.sequence>head.sequence OR r.request_reference=NEW.request_reference))) THEN
     RAISE EXCEPTION 'CIRCLE_RESEAT_SOURCE' USING ERRCODE='23514';
   END IF;
   RETURN NEW;""")

CLOSED_FROM_VACANCY = "public.circle_vacancy_final_exit(bigint,uuid,timestamptz,timestamptz)"
CLOSED_FROM_VACANCY_SQL = """CREATE OR REPLACE FUNCTION public.circle_vacancy_final_exit(c bigint, place uuid, at_time timestamp with time zone, known timestamp with time zone)
 RETURNS boolean
 LANGUAGE sql
 STABLE STRICT
AS $function$
 SELECT EXISTS (SELECT FROM public.circle_episode_at_exit(c,NULL,at_time,known,place) ep
 JOIN public.core_circlestateevent d ON d.circle_id=c AND d.action='BECOME_DORMANT'
 AND d.evidence_reference='vacancy:'||ep.cause AND d.effective_at=ep.deadline AND d.recorded_at<=known
 WHERE ep.status='EXPIRED')
$function$
"""
GUARD_NEW = replace_once(GUARD_NEW,
    "OR NEW.prior_state IS DISTINCT FROM 'ACTIVE'\n       OR NEW.actor_state",
    """OR (NEW.prior_state IS DISTINCT FROM 'ACTIVE' AND
           (NEW.prior_state IS DISTINCT FROM 'DORMANT' OR NOT public.circle_vacancy_final_exit(
             NEW.circle_id,last_exit.place,last_exit.at_time,NEW.recorded_at)))
       OR NEW.actor_state""")
GUARD_NEW = replace_once(GUARD_NEW, "AND h.resulting_state='ACTIVE' AND h.effective_at<=NEW.effective_at",
    """AND h.resulting_state IN ('ACTIVE','DORMANT') AND h.effective_at<=NEW.effective_at""")
GUARD_NEW = replace_once(GUARD_NEW, " IF NEW.action='ACTIVATE' THEN",
    """ IF NEW.action='ACTIVATE' AND EXISTS(SELECT FROM public.core_circle c WHERE c.id=NEW.circle_id AND c.kind='GENERIC')
    AND EXISTS(SELECT FROM public.core_circlestateevent h WHERE h.circle_id=NEW.circle_id AND h.action='ACTIVATE') THEN
   RAISE EXCEPTION 'CIRCLE_RESEAT_SOURCE' USING ERRCODE='23514';
 END IF;
 IF NEW.action='ACTIVATE' THEN""")
CLOSE_NEW = replace_once(CIRCLE_CLOSE_ON_FINAL_EXIT_OLD, "IF head.resulting_state<>'ACTIVE' OR",
    """IF (head.resulting_state<>'ACTIVE' AND (head.resulting_state<>'DORMANT'
     OR NOT public.circle_vacancy_final_exit(x.id,last_exit.place,last_exit.at_time,now_cut))) OR""")

# The authenticated U3 intake predates in-transaction settlement. At an exact
# tie the genuine final place end therefore has an earlier received_at than
# the dormancy immediately preceding its closure. Keep both intake times.
CLOSE_NEW = replace_once(CLOSE_NEW,
    "(head.effective_at,head.occurred_at,head.received_at)>\n       (last_exit.at_time,last_exit.occurred,last_exit.received)",
    """((head.effective_at,head.occurred_at,head.received_at)>
       (last_exit.at_time,last_exit.occurred,last_exit.received) AND NOT
       (head.action='BECOME_DORMANT' AND head.resulting_state='DORMANT'
        AND (head.effective_at,head.occurred_at)=(last_exit.at_time,last_exit.occurred)))""")
APPEND_NEW = replace_once(APPEND_NEW,
    """    FROM public.core_circlestateevent e WHERE e.circle_id=anchor AND e.sequence<=supplied_sequence
      AND (e.effective_at,e.occurred_at,e.received_at)<=(at_time,occurred,received);""",
    """    FROM public.core_circlestateevent e WHERE e.circle_id=anchor AND e.sequence<=supplied_sequence
      AND ((e.effective_at,e.occurred_at,e.received_at)<=(at_time,occurred,received)
        OR (action_name='DISSOLVE_CIRCLE' AND command_rule='CIRCLES:L5:LAST_MEMBER'
          AND e.id=supplied_head AND e.action='BECOME_DORMANT'
          AND (e.effective_at,e.occurred_at)=(at_time,occurred)));""")

# The guarded DISSOLVE insert independently proves the place end, cause and
# precise predecessor. Replaying a set containing both guarded immutable facts
# must preserve closure even when sorting by receipt places dormancy last.
REDUCE_NEW = replace_once(records.episode.REDUCE_0105,
    "    IF p_token='core_circle' AND v_state='ACTIVE'",
    """    IF p_token='core_circle' AND v_state='DORMANT' AND EXISTS(
        SELECT FROM public.core_circlestateevent d JOIN public.core_circlestateevent x
          ON x.predecessor_id=d.id AND x.circle_id=d.circle_id AND x.predecessor_sequence=d.sequence
        WHERE d.id=p_event_ids[cardinality(p_event_ids)] AND x.id=ANY(p_event_ids)
          AND d.action='BECOME_DORMANT' AND d.resulting_state='DORMANT' AND x.action='DISSOLVE_CIRCLE'
          AND x.prior_state='DORMANT' AND x.resulting_state='CLOSED'
          AND x.composing_rule_reference='CIRCLES:L5:LAST_MEMBER'
          AND (x.effective_at,x.occurred_at)=(d.effective_at,d.occurred_at)
          AND x.received_at<d.received_at) THEN RETURN 'CLOSED'; END IF;
    IF p_token='core_circle' AND v_state='ACTIVE'""")

CHAIN_OLD = """CREATE OR REPLACE FUNCTION public.s015_guard_aggregate_chain()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public', 'pg_temp'
AS $function$
DECLARE m record; v_anchor bigint; v_is_event boolean; v_count bigint; v_max integer; v_head bigint; v_head_seq integer;
        v_p_ids bigint[]; v_expected text; v_prior text; v_ok boolean;
BEGIN
    SELECT * INTO m FROM public.s015_anchor_map() WHERE event_table = TG_TABLE_NAME;
    IF m IS NOT NULL THEN
        v_is_event := TRUE; v_anchor := (to_jsonb(NEW) ->> m.fk_col)::bigint;
    ELSE
        SELECT * INTO m FROM public.s015_anchor_map() WHERE anchor_token = TG_TABLE_NAME;
        v_is_event := FALSE; v_anchor := NEW.id;
    END IF;
    EXECUTE format('SELECT count(*), max(sequence) FROM public.%I WHERE %I = $1', m.event_table, m.fk_col)
        INTO v_count, v_max USING v_anchor;
    -- 1. non-empty chain (basis excepted)
    IF v_count = 0 AND NOT m.empty_chain_permitted THEN
        RAISE EXCEPTION 'S015 chain: % id % has no sequence-1 event at commit', m.anchor_token, v_anchor
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    -- 2. consecutive
    IF v_count > 0 AND v_max <> v_count THEN
        RAISE EXCEPTION 'S015 chain: % id % max sequence % <> count %', m.anchor_token, v_anchor, v_max, v_count
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    -- 3. head
    EXECUTE format('SELECT %I FROM public.%I WHERE id = $1', m.head_col, m.anchor_token) INTO v_head USING v_anchor;
    IF (v_head IS NULL) <> (v_count = 0) THEN
        RAISE EXCEPTION 'S015 chain: % id % head % inconsistent with % events', m.anchor_token, v_anchor, v_head, v_count
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF v_head IS NOT NULL THEN
        EXECUTE format('SELECT sequence FROM public.%I WHERE id = $1 AND %I = $2', m.event_table, m.fk_col)
            INTO v_head_seq USING v_head, v_anchor;
        IF v_head_seq IS DISTINCT FROM v_max THEN
            RAISE EXCEPTION 'S015 chain: % id % head sequence % <> max %', m.anchor_token, v_anchor, v_head_seq, v_max
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    -- 4. cache = fold at stored coordinates (cache anchors only)
    IF m.has_cache THEN
        v_ok := public.s015_requalify_governed_cache(m.anchor_token, v_anchor);
        IF v_ok IS DISTINCT FROM TRUE THEN
            RAISE EXCEPTION 'S015 chain: % id % cache does not equal the fold at its stored coordinates', m.anchor_token, v_anchor
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    -- 5. prior_state = reducer over the record-time effective-order predecessor set (U21g-13)
    IF v_is_event THEN
      IF NEW.sequence > 1 THEN
        EXECUTE format(
            'SELECT coalesce(array_agg(id ORDER BY effective_at, occurred_at, received_at, recorded_at, sequence, event_uuid), ''{}''::bigint[])
               FROM public.%I WHERE %I = $1 AND sequence < $2
                AND (effective_at, occurred_at, received_at, recorded_at, sequence, event_uuid) < ($3, $4, $5, $6, $2, $7)',
            m.event_table, m.fk_col) INTO v_p_ids
            USING v_anchor, NEW.sequence, NEW.effective_at, NEW.occurred_at, NEW.received_at, NEW.recorded_at, NEW.event_uuid;
        v_expected := public.s015_reduce_projection(m.anchor_token, v_p_ids);
        v_prior := NEW.prior_state;
        IF v_prior IS DISTINCT FROM v_expected THEN
            RAISE EXCEPTION 'S015 chain: % event % prior_state % should be % (record-time effective-order predecessor)',
                m.event_table, NEW.id, coalesce(v_prior, 'NULL'), coalesce(v_expected, 'NULL')
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
      END IF;
    END IF;
    RETURN NULL;
END $function$
"""
CHAIN_NEW = replace_once(CHAIN_OLD,
    "        v_expected := public.s015_reduce_projection(m.anchor_token, v_p_ids);",
    """        IF m.anchor_token='core_circle' THEN
          IF NEW.action='DISSOLVE_CIRCLE' AND NEW.composing_rule_reference='CIRCLES:L5:LAST_MEMBER'
             AND NOT (NEW.predecessor_id=ANY(v_p_ids)) AND EXISTS(
               SELECT FROM public.core_circlestateevent d WHERE d.id=NEW.predecessor_id
               AND d.circle_id=v_anchor AND d.sequence=NEW.sequence-1 AND d.action='BECOME_DORMANT'
               AND d.resulting_state='DORMANT' AND (d.effective_at,d.occurred_at)=(NEW.effective_at,NEW.occurred_at)
               AND d.received_at>NEW.received_at AND d.recorded_at<=NEW.recorded_at) THEN
            v_p_ids:=array_append(v_p_ids,NEW.predecessor_id);
          END IF;
        END IF;
        v_expected := public.s015_reduce_projection(m.anchor_token, v_p_ids);""")

ADDITIONS = ((APART_BEFORE, APART_BEFORE_SQL), (RESEATABLE, RESEATABLE_SQL), (CLOSED_FROM_VACANCY, CLOSED_FROM_VACANCY_SQL))
REPLACEMENTS = (
    (committee.APART, committee.APART_SQL, APART_NEW),
    ("public.cmt_appointment(bigint,bigint,bigint,uuid,timestamptz,timestamptz)", committee.APPOINTMENT_NEW, APPOINTMENT_NEW),
    ("public.circle_appointing_office(bigint,bigint,bigint,uuid,timestamptz,timestamptz)", CIRCLE_APPOINTING_OFFICE_OLD, APPOINTING_NEW),
    ("public.circle_appointment_marker(bigint,uuid,bigint,bigint,uuid,text,timestamptz,timestamptz)", CIRCLE_APPOINTMENT_MARKER_OLD, MARKER_NEW),
    ("public.sbg_guard_circleparticipation()", SBG_GUARD_CIRCLEPARTICIPATION_OLD, PLACE_NEW),
    ("public.circle_guard_other_office()", CIRCLE_GUARD_OTHER_OFFICE_OLD, BEFORE_OFFICE_NEW),
    ("public.te_origin_applies(text,jsonb)", records.TE_ORIGIN_APPLIES_NEW, ORIGIN_NEW),
    (records.REPLACEMENTS[2][0], records.S015_APPEND_EVENT_NEW, APPEND_NEW),
    ("public.circle_guard_active_event()", records.CIRCLE_GUARD_ACTIVE_EVENT_NEW, GUARD_NEW),
    ("public.circle_coordinator_event()", CIRCLE_COORDINATOR_EVENT_OLD, COORDINATOR_NEW),
    ("public.circle_close_on_final_exit()", CIRCLE_CLOSE_ON_FINAL_EXIT_OLD, CLOSE_NEW),
    (records.episode.REDUCE, records.episode.REDUCE_0105, REDUCE_NEW),
    ("public.s015_guard_aggregate_chain()", CHAIN_OLD, CHAIN_NEW),
)


def expect(cursor, installed):
    for signature, before, after in REPLACEMENTS:
        if definition(cursor, signature) != (after if installed else before):
            raise RuntimeError("0108 installed definition differs: " + signature)
    for signature, body in ADDITIONS:
        if definition(cursor, signature) != (body if installed else None):
            raise RuntimeError("0108 installed definition differs: " + signature)


def change(schema_editor, forward):
    with schema_editor.connection.cursor() as cursor:
        expect(cursor, not forward)
        if forward:
            for _, body in ADDITIONS:
                cursor.execute(body)
            for _, _, after in REPLACEMENTS:
                cursor.execute(after)
        else:
            cursor.execute("SELECT 1 FROM public.core_circlestateevent WHERE action='RESEAT' LIMIT 1")
            if cursor.fetchone():
                raise RuntimeError("0108 cannot reverse a recorded Circle reseat.")
            cursor.execute("""SELECT 1 FROM public.core_circlestateevent x
                JOIN public.core_circlestateevent d ON d.id=x.predecessor_id AND d.circle_id=x.circle_id
                WHERE x.action='DISSOLVE_CIRCLE' AND d.action='BECOME_DORMANT'
                AND (x.effective_at,x.occurred_at)=(d.effective_at,d.occurred_at)
                AND x.received_at<d.received_at LIMIT 1""")
            if cursor.fetchone():
                raise RuntimeError("0108 cannot reverse a recorded co-effective Circle closure.")
            for _, before, _ in reversed(REPLACEMENTS):
                cursor.execute(before)
            for signature, _ in reversed(ADDITIONS):
                cursor.execute("DROP FUNCTION " + signature)
        expect(cursor, forward)


def install(apps, schema_editor):
    change(schema_editor, True)


def uninstall(apps, schema_editor):
    change(schema_editor, False)


class Migration(migrations.Migration):
    dependencies = (("core", "0107_committee_circle_dormancy"),)
    operations = [migrations.RunPython(install, uninstall)]
