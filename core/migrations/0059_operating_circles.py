"""Ordinary Circle admissions and voluntary departures. The original 0045 branches remain intact."""

from importlib import import_module

from django.db import migrations, models

sbg = import_module("core.migrations.0045_sbg_governance_formation")
creation = import_module("core.migrations.0058_circle_creation_writer")

SQL = r"""
CREATE FUNCTION public.circle_source(lo bigint, target uuid, act text, actor bigint, beneficiary bigint,
                                    at_time timestamptz, known timestamptz)
RETURNS uuid LANGUAGE sql STABLE STRICT AS $$
 WITH context AS (
    SELECT l.organism_id,b.basis_uuid,public.c2_target(format(
      '{"act":%s,"basis":"%s","beneficiary":%s,"kind":"EXECUTION_ELIGIBILITY_QUALIFICATION","organism":"%s","schema":"S:scope:1","target":"%s","tested":%s}',
      to_json(act),b.basis_uuid,beneficiary,l.organism_id,target,actor)) AS scope
    FROM public.core_livingorganism l JOIN public.core_authoritybasis b ON b.id=l.founding_authority_basis_id
    WHERE l.id=lo AND (public.s015_fold_effective_state('core_livingorganism',lo,at_time,known)='ACTIVE'
      OR (starts_with(act,'CIRCLE_SERVICE:') AND EXISTS(SELECT FROM public.core_circle c
          WHERE c.circle_uuid=target AND c.parent_organism_id=lo AND c.kind='APPLICATION')
          AND public.s015_fold_effective_state('core_livingorganism',lo,at_time,known)='RESTRICTED_CONTINUITY'))
 ), latest AS (
    SELECT d,ctx.basis_uuid FROM context ctx JOIN public.core_governeddetermination d ON d.scope_fingerprint=ctx.scope
    WHERE d.determination_type='EXECUTION_ELIGIBILITY_QUALIFICATION' AND d.tested_actor_id=actor
      AND d.safeguard_beneficiary_id=beneficiary AND d.effective_at<=at_time
      AND d.received_at<=known AND d.recorded_at<=known
    ORDER BY d.effective_at DESC,d.occurred_at DESC,d.received_at DESC,d.recorded_at DESC,d.id DESC LIMIT 1
 ) SELECT (d).determination_uuid FROM latest WHERE (d).result='QUALIFIED'
      AND public.c2_finding_problem(d,basis_uuid,at_time,known) IS NULL
$$;
CREATE FUNCTION public.circle_mandate(lo_id bigint, finding uuid, at_time timestamptz, known timestamptz)
RETURNS uuid LANGUAGE plpgsql STABLE STRICT AS $$
DECLARE grant_row public.core_governeddetermination; expiry timestamptz;
BEGIN
 SELECT g.* INTO grant_row FROM public.core_governeddetermination f
 JOIN public.core_livingorganism lo ON lo.id=lo_id
 JOIN public.core_authoritybasis root_basis ON root_basis.id=lo.founding_authority_basis_id
 JOIN public.core_governeddetermination g ON g.scope_fingerprint=f.scope_fingerprint
 JOIN public.core_authoritybasis b ON b.basis_uuid::text=split_part(g.method_reference,'|',2)
     AND b.scope_fingerprint=f.scope_fingerprint
 WHERE f.determination_uuid=finding AND g.determination_type='CONSTITUTIONAL_AUTHORITY_QUALIFICATION'
 AND g.tested_actor_id=f.tested_actor_id AND g.safeguard_beneficiary_id=f.safeguard_beneficiary_id
 AND g.relied_upon_authority_principal_id=b.authority_principal_id AND g.evidence_reference=b.instrument_reference
 AND g.determiner_id=b.issuer_id AND starts_with(g.method_reference,'CIRCLES:authority:1|')
 AND EXISTS(SELECT FROM public.c2_lineage(b.id,known) chain WHERE chain.id=root_basis.authority_principal_id)
 AND public.c2_basis_in_force(root_basis.id,at_time,known)
 AND g.effective_at<=at_time AND g.received_at<=known AND g.recorded_at<=known
 AND public.c2_basis_in_force(b.id,at_time,known)
 ORDER BY g.effective_at DESC,g.occurred_at DESC,g.received_at DESC,g.recorded_at DESC,g.id DESC LIMIT 1;
 IF grant_row.id IS NULL OR grant_row.result<>'QUALIFIED' OR public.c2_contested(grant_row.id,at_time,known) THEN RETURN NULL; END IF;
 IF split_part(grant_row.method_reference,'|',3) !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$' THEN RETURN NULL; END IF;
 expiry:=split_part(grant_row.method_reference,'|',3)::timestamptz;
 IF expiry<=at_time THEN RETURN NULL; END IF;
 RETURN grant_row.determination_uuid;
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RETURN NULL;
END $$;
CREATE FUNCTION public.circle_decision(lo_id bigint,finding uuid,at_time timestamptz,known timestamptz)
RETURNS text LANGUAGE sql STABLE STRICT AS $$
 SELECT 's015d1:'||public.c2_target(finding::text||':'||public.circle_mandate(lo_id,finding,at_time,known)::text)
$$;
-- The post-founding provider uses these same cut-qualified C2 seat facts.
CREATE FUNCTION public.circle_lo_office(lo bigint, person bigint, at_time timestamptz, known timestamptz)
RETURNS uuid LANGUAGE sql STABLE STRICT AS $$
 SELECT a.assignment_uuid FROM public.c2_seat_facts(lo,at_time,known) f
 JOIN public.core_contextualroleassignment a ON a.id=f.assignment_id
 JOIN public.core_organismmembership m ON m.id=f.membership_id
 JOIN public.core_livingorganism l ON l.id=m.living_organism_id
 WHERE f.role_scope='LIVING_ORGANISM' AND f.role_code='COORDINATOR' AND f.standing='QUALIFIED'
 AND m.identity_id=person AND a.circle_id IS NULL AND NOT l.platform_root
 AND public.s015_fold_effective_state('core_livingorganism',lo,at_time,known)='ACTIVE'
 AND public.c2_basis_in_force(l.founding_authority_basis_id,at_time,known)
 ORDER BY a.id LIMIT 1
$$;
CREATE FUNCTION public.circle_office_decision(source uuid, target uuid, act text, person bigint, subject bigint)
RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT 's015d1:'||public.c2_target(jsonb_build_array('circle-office-v1',source,target,act,person,subject)::text)
$$;
CREATE FUNCTION public.circle_eligible(c bigint,m bigint,at_time timestamptz,known timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 SELECT EXISTS(SELECT FROM public.core_circle x JOIN public.core_organismmembership p
    ON p.living_organism_id=x.parent_organism_id WHERE x.id=c AND p.id=m
    AND public.s015_fold_effective_state('core_organismmembership',m,at_time,known)='ACTIVE'
    AND public.s015_fold_effective_state('core_circle',c,at_time,known) IN ('DORMANT','ELIGIBLE','ACTIVE')
    AND (x.kind='GOVERNANCE' OR EXISTS(SELECT FROM public.core_circlestateevent formed
       WHERE formed.circle_id=x.id AND formed.sequence=1 AND formed.action='CREATE_DORMANT'
       AND formed.actor_state='LIVING_ORGANISM' AND formed.resulting_state='DORMANT'
       AND starts_with(formed.evidence_reference,'circle-purpose:') AND formed.effective_at<=at_time
       AND formed.received_at<=known AND formed.recorded_at<=known AND formed.authority_decision_reference=
       CASE WHEN x.kind='GENERIC' THEN public.circle_office_decision(
          public.circle_lo_office(x.parent_organism_id,formed.enterer_identity_id,formed.effective_at,formed.recorded_at),
          x.circle_uuid,'FORM:GENERIC:'||encode(sha256(convert_to(substr(formed.evidence_reference,16),'UTF8')),'hex'),
          formed.enterer_identity_id,formed.enterer_identity_id)
       ELSE public.circle_decision(x.parent_organism_id,public.circle_source(x.parent_organism_id,x.circle_uuid,
          'FORM:'||x.kind||':'||encode(sha256(convert_to(substr(formed.evidence_reference,16),'UTF8')),'hex'),
          formed.enterer_identity_id,formed.enterer_identity_id,formed.effective_at,formed.recorded_at),
          formed.effective_at,formed.recorded_at) END)))

$$;
CREATE FUNCTION public.circle_place_at(c bigint,m bigint,at_time timestamptz,known timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 SELECT public.circle_eligible(c,m,at_time,known) AND EXISTS(SELECT FROM public.core_circleparticipation p
    WHERE p.membership_id=m AND p.circle_id=c AND p.effective_at<=at_time AND p.received_at<=known AND p.recorded_at<=known
    AND NOT EXISTS(SELECT FROM public.core_circleparticipationend e WHERE e.participation_id=p.id
          AND e.effective_at<=at_time AND e.received_at<=known AND e.recorded_at<=known))
$$;
"""

# Each discriminator is new except PARTY, which the legacy end shape explicitly
# rejected. The old branch body (and its error order) is retained byte-for-byte.
BRANCHES = {
    "sbg_guard_circlejoinrequest": (
        sbg._CIRCLEJOINREQUEST_GUARD_BODY,
        """
    IF NEW.initiated_by='CIRCLE_MEMBER' THEN
        SELECT c.parent_organism_id,m.identity_id INTO v_organism_id,v_identity_id
          FROM public.core_circle c JOIN public.core_organismmembership m ON m.living_organism_id=c.parent_organism_id
          JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id
          JOIN public.core_authoritybasis b ON b.id=lo.founding_authority_basis_id
          WHERE c.id=NEW.circle_id AND m.id=NEW.membership_id AND c.kind<>'GOVERNANCE'
            AND NEW.seat_rule_uuid=b.basis_uuid AND NEW.seat_id=m.membership_uuid::text;
        IF NOT FOUND OR v_identity_id IS DISTINCT FROM NEW.enterer_identity_id OR btrim(NEW.consent_reference)=''
           OR NEW.effective_at<>NEW.received_at OR NEW.occurred_at<>NEW.effective_at OR NEW.received_at>NEW.recorded_at
           OR NOT public.circle_eligible(NEW.circle_id,NEW.membership_id,NEW.effective_at,NEW.recorded_at) THEN
          RAISE EXCEPTION 'CIRCLE_REQUEST_SCOPE' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
""",
    ),
    "sbg_guard_circleadmissiondecision": (
        sbg._CIRCLEADMISSIONDECISION_GUARD_BODY,
        """
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
""",
    ),
    "sbg_guard_circleparticipation": (
        sbg._CIRCLEPARTICIPATION_GUARD_BODY,
        """
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
""",
    ),
    "sbg_guard_circleparticipation_occupancy": (
        sbg._CIRCLEPARTICIPATION_OCCUPANCY_BODY,
        """
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
""",
    ),
    "sbg_guard_circleparticipationend": (
        sbg._CIRCLEPARTICIPATIONEND_GUARD_BODY,
        """
    IF NEW.actor_state='PARTY' THEN
        IF NOT EXISTS(SELECT FROM public.core_circleparticipation p
          JOIN public.core_organismmembership m ON m.id=p.membership_id
          JOIN public.core_circlestateevent e ON e.circle_id=p.circle_id
          WHERE p.id=NEW.participation_id AND p.effective_at<=NEW.effective_at AND p.recorded_at<=NEW.recorded_at
            AND e.event_uuid=NEW.composed_from_event_uuid AND e.action='LEAVE_CIRCLE'
            AND e.evidence_reference='place:'||p.participation_uuid AND e.enterer_identity_id=m.identity_id
            AND e.actor_state='PARTY' AND e.actor_id=m.identity_id AND NEW.actor_id=m.identity_id
            AND NEW.enterer_identity_id=m.identity_id AND NEW.actor_access_epoch=e.actor_access_epoch
            AND (e.effective_at,e.occurred_at,e.received_at)=(NEW.effective_at,NEW.occurred_at,NEW.received_at)) THEN
          RAISE EXCEPTION 'CIRCLE_LEAVE_SOURCE' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
""",
    ),
}

# A decision and its accepted place form one committed act. Conversely the
# original 0045 participation validator still checks the decision in all cases.
SQL += r"""
CREATE FUNCTION public.circle_admit_complete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.route IN ('ORDINARY','APPLICATION_MODE') AND NEW.outcome='ACCEPT' AND NOT EXISTS(SELECT FROM public.core_circleparticipation WHERE decision_id=NEW.id) THEN
   RAISE EXCEPTION 'CIRCLE_ADMISSION_INCOMPLETE' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER circle_admit_complete AFTER INSERT ON public.core_circleadmissiondecision
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.circle_admit_complete();

CREATE FUNCTION public.circle_leave_complete() RETURNS trigger LANGUAGE plpgsql AS $$
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
END $$;
CREATE CONSTRAINT TRIGGER circle_leave_complete AFTER INSERT ON public.core_circlestateevent
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.circle_leave_complete();
"""


SQL += r"""
CREATE FUNCTION public.circle_office_consent(a bigint, at_time timestamptz, known timestamptz)
RETURNS uuid LANGUAGE sql STABLE STRICT AS $$
 SELECT CASE WHEN p.actor_id=m.identity_id AND last.action IS NULL THEN p.event_uuid
             WHEN p.actor_id<>m.identity_id AND last.action='CONSENT' THEN last.consent_uuid END
 FROM public.core_contextualroleassignment office
 JOIN public.core_organismmembership m ON m.id=office.membership_id
 JOIN public.core_contextualroleassignmentstateevent p ON p.assignment_id=office.id AND p.sequence=1
 LEFT JOIN LATERAL (SELECT c.action,c.consent_uuid FROM public.core_officeappointmentconsent c
     WHERE c.assignment_id=office.id AND c.proposal_event_id=p.id AND c.nominee_membership_id=m.id
     AND c.effective_at<=at_time AND c.received_at<=known AND c.recorded_at<=known
     ORDER BY c.ordinal DESC LIMIT 1) last ON true
 WHERE office.id=a AND p.action='PROPOSE' AND p.resulting_state='PROPOSED'
 AND p.effective_at<=at_time AND p.received_at<=known AND p.recorded_at<=known
$$;

-- A GENERIC ASSIGN is itself the immutable standing record. Its insert guard
-- verifies all references independently at both cuts before the event can commit.
CREATE FUNCTION public.circle_standing_text(approver uuid, place_ref text, consent uuid, period uuid, event_id uuid)
RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
 SELECT 'ord-standing:1|'||approver||'|'||place_ref||'|'||consent||'|'||period||'|'||event_id
$$;
CREATE FUNCTION public.circle_generic_offices(c_id bigint, at_time timestamptz, known timestamptz)
RETURNS TABLE(office_uuid uuid,member uuid,person bigint) LANGUAGE sql STABLE STRICT AS $$
 SELECT a.assignment_uuid,m.membership_uuid,m.identity_id
 FROM public.core_circle c
 JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id
 CROSS JOIN LATERAL public.c2_seat_facts(lo.id,at_time,known) f
 JOIN public.core_contextualroleassignment a ON a.id=f.assignment_id AND a.circle_id=c.id
 JOIN public.core_organismmembership m ON m.id=f.membership_id
 JOIN public.core_contextualroleassignmentstateevent e ON e.assignment_id=a.id AND e.sequence=2 AND e.action='ASSIGN'
 JOIN public.core_contextualroleassignmentstateevent proposal ON proposal.id=e.predecessor_id AND proposal.assignment_id=a.id
 JOIN public.core_circleparticipation place ON place.circle_id=c.id AND place.membership_id=m.id
      AND proposal.evidence_reference='place:'||place.participation_uuid
 LEFT JOIN public.core_contextualroleassignment issuer ON issuer.assignment_uuid::text=split_part(e.evidence_reference,'|',2)
      AND split_part(e.evidence_reference,'|',7)<>'RC1'
 LEFT JOIN public.core_organismmembership signer ON signer.id=issuer.membership_id AND signer.living_organism_id=lo.id
 LEFT JOIN public.core_organismroledefinition role ON role.id=issuer.role_definition_id AND role.code='COORDINATOR'
 WHERE c.id=c_id AND c.kind='GENERIC' AND NOT lo.platform_root AND a.id IS DISTINCT FROM issuer.id
 AND f.role_scope='CIRCLE' AND f.role_code='COORDINATOR' AND f.standing='QUALIFIED'
 AND ((signer.identity_id=e.enterer_identity_id AND role.living_organism_id=lo.id AND (
         (role.scope='CIRCLE' AND issuer.circle_id=c_id AND split_part(e.evidence_reference,'|',7)='')
         OR (role.scope='LIVING_ORGANISM' AND issuer.circle_id IS NULL AND (
            split_part(e.evidence_reference,'|',7)='CB1' OR (split_part(e.evidence_reference,'|',7)=''
            AND place.birth_event_id IS NOT NULL AND signer.id=m.id AND proposal.actor_id=m.identity_id)))))
      OR (split_part(e.evidence_reference,'|',7)='RC1' AND proposal.actor_id=m.identity_id
          AND e.enterer_identity_id=m.identity_id AND split_part(e.evidence_reference,'|',2)=place.participation_uuid::text))
 AND e.resulting_state='ACTIVE' AND e.effective_until_state='NONE' AND e.effective_until IS NULL
 AND e.effective_at<=at_time AND e.recorded_at<=known AND e.received_at<=known
 AND public.circle_place_at(c.id,m.id,at_time,known)
 AND NOT EXISTS (SELECT FROM public.core_circleparticipationend ended WHERE ended.participation_id=place.id
      AND ended.effective_at<=at_time AND ended.received_at<=known AND ended.recorded_at<=known)
 AND e.authority_decision_reference=public.circle_office_decision(coalesce(issuer.assignment_uuid,place.participation_uuid),c.circle_uuid,
      'APPOINT:'||a.assignment_uuid||':NONE',e.enterer_identity_id,m.identity_id)
 AND e.evidence_reference=public.circle_standing_text(coalesce(issuer.assignment_uuid,place.participation_uuid),proposal.evidence_reference,
      public.circle_office_consent(a.id,e.effective_at,e.recorded_at),public.c1b_period_at(m.id,at_time,known),e.event_uuid)
      || CASE split_part(e.evidence_reference,'|',7) WHEN 'RC1' THEN '|RC1' WHEN 'CB1' THEN '|CB1' ELSE '' END
$$;
CREATE FUNCTION public.circle_holders(c_id bigint, at_time timestamptz, known timestamptz)
RETURNS TABLE(member uuid,person bigint) LANGUAGE sql STABLE STRICT AS $$
 SELECT DISTINCT m.membership_uuid,m.identity_id
 FROM public.core_circle c
 JOIN public.core_livingorganism lo ON lo.id=c.parent_organism_id
 CROSS JOIN LATERAL public.c2_seat_facts(lo.id,at_time,known) f
 JOIN public.core_contextualroleassignment a ON a.id=f.assignment_id AND a.circle_id=c.id
 JOIN public.core_organismmembership m ON m.id=f.membership_id
 CROSS JOIN LATERAL (SELECT b.basis_uuid FROM public.core_contextualroleassignmentstateevent e
     JOIN public.core_authoritybasis b ON b.id=e.authority_basis_id
     WHERE e.assignment_id=a.id AND e.action='ASSIGN' AND e.effective_at<=at_time AND e.recorded_at<=known AND e.received_at<=known
     ORDER BY e.effective_at DESC,e.occurred_at DESC,e.received_at DESC,e.recorded_at DESC,e.sequence DESC LIMIT 1) term
 WHERE c.id=c_id AND c.kind<>'GENERIC' AND f.role_scope='CIRCLE' AND f.role_code='COORDINATOR' AND f.standing='QUALIFIED'
   AND public.circle_place_at(c.id,m.id,at_time,known)
   AND EXISTS(SELECT FROM public.core_governeddetermination d
      WHERE d.scope_fingerprint=public.c2_office_scope(lo.organism_id,a.assignment_uuid,m.identity_id,term.basis_uuid)
        AND d.determination_type='EXECUTION_ELIGIBILITY_QUALIFICATION' AND d.tested_actor_id=m.identity_id
        AND d.safeguard_beneficiary_id=m.identity_id AND d.effective_at<=at_time AND d.received_at<=known AND d.recorded_at<=known)
 UNION
 SELECT g.member,g.person FROM public.circle_generic_offices(c_id,at_time,known) g
$$;
"""

SQL += r"""
CREATE FUNCTION public.circle_current_office(c bigint, person_id bigint, at_time timestamptz, known timestamptz, excluding uuid DEFAULT NULL)
RETURNS uuid LANGUAGE sql STABLE AS $$
 SELECT g.office_uuid FROM public.circle_generic_offices(c,at_time,known) g
 JOIN public.core_circle x ON x.id=c
 WHERE g.person=person_id AND g.office_uuid IS DISTINCT FROM excluding
 AND public.s015_fold_effective_state('core_livingorganism',x.parent_organism_id,at_time,known)='ACTIVE'
 ORDER BY g.office_uuid LIMIT 1
$$;
"""
SQL += r"""
CREATE FUNCTION public.circle_vacant(c bigint, incoming uuid, at_time timestamptz, known timestamptz)
RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT FROM public.core_circle x JOIN public.core_livingorganism lo ON lo.id=x.parent_organism_id
    WHERE x.id=c AND x.kind='GENERIC' AND NOT lo.platform_root
    AND public.s015_fold_effective_state('core_livingorganism',lo.id,at_time,known)='ACTIVE'
    AND public.s015_fold_effective_state('core_circle',c,at_time,known)='ACTIVE'
    AND EXISTS(SELECT FROM public.core_circleparticipation p WHERE p.circle_id=c
               AND public.circle_place_at(c,p.membership_id,at_time,known))
    AND NOT EXISTS(SELECT FROM public.core_contextualroleassignment a JOIN public.core_organismroledefinition d
       ON d.id=a.role_definition_id AND d.scope='CIRCLE' AND d.code='COORDINATOR'
       WHERE a.circle_id=c AND a.assignment_uuid IS DISTINCT FROM incoming
       AND public.s015_fold_effective_state('core_contextualroleassignment',a.id,at_time,known)='ACTIVE'))
$$;
-- CQ's vote-result consumer is not operative on this base. If the effective setting
-- requires CQ or inherits an unresolved setting, never take the direct volunteer branch.
CREATE FUNCTION public.circle_direct_volunteer(lo bigint, at_time timestamptz, known timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 WITH current_choice AS (
   SELECT e.evidence_reference FROM public.core_livingorganismevent e WHERE e.living_organism_id=lo
    AND e.action='EFFECT_GOVERNANCE_CONFIGURATION' AND e.effective_at<=at_time AND e.received_at<=known AND e.recorded_at<=known
    ORDER BY e.effective_at DESC,e.occurred_at DESC,e.received_at DESC,e.recorded_at DESC,e.sequence DESC,e.event_uuid DESC LIMIT 1)
 SELECT NOT EXISTS(SELECT FROM current_choice)
    OR EXISTS(SELECT FROM current_choice e JOIN public.core_logovernanceconfigurationversion v
       ON e.evidence_reference='governance-configuration-version:'||v.version_uuid
       WHERE v.living_organism_id=lo AND v.cq_active IS FALSE AND v.inherit_portal_cq_settings IS FALSE)
$$;
"""
SQL += r"""
-- A historical claim stays in the ledger. It occupies work today only while
-- an office counted for that claimant at the claim still counts, with SERVICE.
CREATE FUNCTION public.circle_claim_current(c bigint, claim_id bigint, at_time timestamptz, known timestamptz)
RETURNS boolean LANGUAGE sql STABLE STRICT AS $$
 SELECT EXISTS(SELECT FROM public.core_circlestateevent claimed
 JOIN public.core_circle x ON x.id=claimed.circle_id AND x.kind='GENERIC'
 JOIN public.core_circlestateevent task ON task.circle_id=x.id AND task.action='OPEN_WORK'
    AND 'work:'||task.event_uuid=claimed.evidence_reference
 CROSS JOIN LATERAL public.circle_generic_offices(c,claimed.effective_at,claimed.recorded_at) original
 JOIN public.circle_generic_offices(c,at_time,known) current_office ON current_office.office_uuid=original.office_uuid
 WHERE claimed.id=claim_id AND claimed.circle_id=c AND claimed.action='CLAIM_WORK' AND claimed.actor_id=original.person
   AND claimed.effective_at<=at_time AND claimed.recorded_at<=known AND claimed.received_at<=known
   AND public.circle_source(x.parent_organism_id,x.circle_uuid,'CIRCLE_SERVICE:'||(task.evidence_reference::jsonb->>1),
            claimed.actor_id,claimed.actor_id,at_time,known) IS NOT NULL)
$$;
"""
# Check newly commissioned acts at storage. Historical Board events use the
# original route. The task's opening UUID is both its durable ID and its owner link.
SQL += r"""
CREATE FUNCTION public.circle_command_valid() RETURNS trigger LANGUAGE plpgsql AS $$
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
END $$;
CREATE CONSTRAINT TRIGGER circle_command_valid AFTER INSERT ON public.core_circlestateevent
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.circle_command_valid();
"""


SQL += r"""
CREATE FUNCTION public.circle_office_end_source() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a public.core_contextualroleassignment;
BEGIN
 IF NEW.composing_rule_reference IS NULL OR NEW.composing_rule_reference NOT IN ('CIRCLES:G2:U3','CIRCLES:G2:SELF_LEAVE') THEN
   RETURN NULL;
 END IF;
 SELECT * INTO STRICT a FROM public.core_contextualroleassignment WHERE id=NEW.assignment_id;
 IF a.circle_id IS NULL OR NEW.action<>'END' OR NEW.resulting_state<>'ENDED' OR NEW.actor_state<>'NONE'
    OR NEW.entry_mode<>'COMPOSED' OR NEW.composer_identity_id IS DISTINCT FROM NEW.enterer_identity_id
    OR NOT EXISTS(SELECT FROM public.core_organismmembership WHERE id=a.membership_id AND identity_id=NEW.enterer_identity_id) THEN
    RAISE EXCEPTION 'CIRCLE_OFFICE_END_SHAPE' USING ERRCODE='23514';
 END IF;
 IF NEW.composing_rule_reference='CIRCLES:G2:U3' THEN
    IF NOT EXISTS(SELECT FROM public.core_organismmembershiptransition t WHERE t.membership_id=a.membership_id
        AND t.resulting_state='ENDED' AND NEW.evidence_reference='lo-leave:'||t.event_uuid
        AND t.authority_decision_reference=NEW.authority_decision_reference AND t.enterer_identity_id=NEW.enterer_identity_id
        AND (t.effective_at,t.occurred_at,t.received_at)=(NEW.effective_at,NEW.occurred_at,NEW.received_at)) THEN
        RAISE EXCEPTION 'CIRCLE_OFFICE_LO_LEAVE_SOURCE' USING ERRCODE='23514';
    END IF;
 ELSIF NOT EXISTS(SELECT FROM public.core_circleparticipation p JOIN public.core_circleparticipationend e ON e.participation_id=p.id
        JOIN public.core_circlestateevent t ON t.event_uuid=e.composed_from_event_uuid
        WHERE p.membership_id=a.membership_id AND p.circle_id=a.circle_id AND t.action='LEAVE_CIRCLE'
          AND NEW.evidence_reference='circle-leave:'||t.event_uuid AND t.authority_decision_reference=NEW.authority_decision_reference
          AND (t.effective_at,t.occurred_at,t.received_at)=(NEW.effective_at,NEW.occurred_at,NEW.received_at)) THEN
    RAISE EXCEPTION 'CIRCLE_OFFICE_PLACE_LEAVE_SOURCE' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER circle_office_end_source AFTER INSERT ON public.core_contextualroleassignmentstateevent
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.circle_office_end_source();
"""


def writer_body():
    body = creation.new_body()
    body = body.replace(
        "'core_circle','core_livingorganism'",
        "'core_circle','core_contextualroleassignment','core_livingorganism'",
        1,
    )
    # Institutional formation shares its legacy envelope. Other Circle commands
    # are attributable acts of a party; 0057 constructs their normal envelope.
    body = body.replace(
        "IF token = 'core_circle' THEN",
        "IF token = 'core_circle' AND action_name='CREATE_DORMANT' THEN",
    )
    clauses = """
 IF token='core_contextualroleassignment' AND (
     action_name IS DISTINCT FROM 'END' OR outcome IS DISTINCT FROM 'ENDED' OR command_rule IS NULL
     OR command_rule NOT IN ('CIRCLES:G2:U3','CIRCLES:G2:SELF_LEAVE')) THEN
    RAISE EXCEPTION 'Unsupported scoped office envelope';
 END IF;
 IF token='core_circle' AND action_name NOT IN ('CREATE_DORMANT','LEAVE_CIRCLE','OPEN_WORK','CLAIM_WORK') THEN
    RAISE EXCEPTION 'Unsupported Circle envelope';
 END IF;
"""
    body = body.replace(
        " values_:=jsonb_build_object", clauses + " values_:=jsonb_build_object", 1
    )
    return body.replace("c.id=anchor AND c.kind='GOVERNANCE'", "c.id=anchor")


def guard_bodies():
    specs = [
        (
            creation.SIGNATURE,
            creation.new_body(),
            writer_body(),
            creation.HEADER.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"),
        )
    ]
    for name, (old, branch) in BRANCHES.items():
        marker = "\nBEGIN\n"
        if old.count(marker) != 1:
            raise RuntimeError(f"Ambiguous body for {name}")
        new = old.replace(marker, marker + branch, 1)
        specs.append(
            (
                f"public.{name}()",
                old,
                new,
                f"CREATE OR REPLACE FUNCTION public.{name}() RETURNS trigger LANGUAGE plpgsql",
            )
        )
    return specs


BODIES = guard_bodies()
SQL += "\n".join(header + " AS $$" + new + "$$;" for _, _, new, header in BODIES)


def verify_bodies(editor, reverse):
    with editor.connection.cursor() as cursor:
        for sig, old, new, _ in BODIES:
            cursor.execute(
                "SELECT prosrc FROM pg_proc WHERE oid=%s::regprocedure", [sig]
            )
            if cursor.fetchone() != (new if reverse else old,):
                raise RuntimeError(f"Installed body differs for {sig}.")


def install(apps, editor):
    verify_bodies(editor, False)
    editor.execute(SQL, params=None)


def reverse(apps, editor):
    verify_bodies(editor, True)
    for _, old, _, header in BODIES:
        editor.execute(header + " AS $$" + old + "$$;", params=None)
    editor.execute(
        """
      DROP TRIGGER circle_office_end_source ON public.core_contextualroleassignmentstateevent;
      DROP FUNCTION public.circle_office_end_source();
      DROP TRIGGER circle_command_valid ON public.core_circlestateevent;
      DROP FUNCTION public.circle_command_valid();
      DROP FUNCTION public.circle_vacant(bigint,uuid,timestamptz,timestamptz);
      DROP FUNCTION public.circle_direct_volunteer(bigint,timestamptz,timestamptz);
      DROP FUNCTION public.circle_current_office(bigint,bigint,timestamptz,timestamptz,uuid);
      DROP FUNCTION public.circle_holders(bigint,timestamptz,timestamptz);
      DROP FUNCTION public.circle_claim_current(bigint,bigint,timestamptz,timestamptz);
      DROP FUNCTION public.circle_generic_offices(bigint,timestamptz,timestamptz);
      DROP FUNCTION public.circle_standing_text(uuid,text,uuid,uuid,uuid);
      DROP FUNCTION public.circle_office_consent(bigint,timestamptz,timestamptz);
      DROP TRIGGER circle_leave_complete ON public.core_circlestateevent;
      DROP FUNCTION public.circle_leave_complete();
      DROP TRIGGER circle_admit_complete ON public.core_circleadmissiondecision;
      DROP FUNCTION public.circle_admit_complete();
      DROP FUNCTION public.circle_place_at(bigint,bigint,timestamptz,timestamptz);
      DROP FUNCTION public.circle_eligible(bigint,bigint,timestamptz,timestamptz);
      DROP FUNCTION public.circle_lo_office(bigint,bigint,timestamptz,timestamptz);
      DROP FUNCTION public.circle_office_decision(uuid,uuid,text,bigint,bigint);
      DROP FUNCTION public.circle_decision(bigint,uuid,timestamptz,timestamptz);
      DROP FUNCTION public.circle_mandate(bigint,uuid,timestamptz,timestamptz);
      DROP FUNCTION public.circle_source(bigint,uuid,text,bigint,bigint,timestamptz,timestamptz);
    """,
        params=None,
    )


def reverse_population(apps, editor):
    with editor.connection.cursor() as cursor:
        cursor.execute("""SELECT EXISTS(SELECT FROM core_circlejoinrequest WHERE initiated_by='CIRCLE_MEMBER')
               OR EXISTS(SELECT FROM core_circleparticipationend WHERE actor_state='PARTY')
               OR EXISTS(SELECT FROM core_circlestateevent e JOIN core_circle c ON c.id=e.circle_id
                   WHERE e.action IN ('ACTIVATE','LEAVE_CIRCLE','OPEN_WORK','CLAIM_WORK')
                      OR (c.kind<>'GOVERNANCE' AND e.evidence_reference LIKE 'circle-purpose:%'))""")
        if cursor.fetchone()[0]:
            raise RuntimeError(
                "Operating Circle records exist. Keep their governing migration installed."
            )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0058_circle_creation_writer"),
    ]

    operations = [
        migrations.AddField(model_name="circleparticipation", name="recovery_office", field=models.ForeignKey(
            to="core.contextualroleassignment", on_delete=models.PROTECT, null=True, blank=True, related_name="+")),
        migrations.AddField(model_name="circleparticipation", name="birth_event", field=models.OneToOneField(
            to="core.circlestateevent", on_delete=models.PROTECT, null=True, blank=True, related_name="+")),
        migrations.AlterField(model_name="circleparticipation", name="decision", field=models.OneToOneField(
            to="core.circleadmissiondecision", on_delete=models.PROTECT, null=True, blank=True, related_name="+")),
        migrations.AddConstraint(
            model_name="circleparticipation",
            constraint=models.CheckConstraint(condition=(models.Q(decision__isnull=True, birth_event__isnull=False, recovery_office__isnull=True)
                                               | models.Q(decision__isnull=False, birth_event__isnull=True, recovery_office__isnull=True)
                                               | models.Q(decision__isnull=True, birth_event__isnull=True, recovery_office__isnull=False)),
                                   name="circle_place_one_source"),
        ),
        migrations.RunPython(install, reverse),
        migrations.RemoveConstraint(
            model_name="circleadmissiondecision",
            name="sbg_circledecision_route_ck",
        ),
        migrations.RemoveConstraint(
            model_name="circleadmissiondecision",
            name="sbg_circledecision_route_shape_ck",
        ),
        migrations.RemoveConstraint(
            model_name="circlejoinrequest",
            name="sbg_circlejoinrequest_initiator_ck",
        ),
        migrations.RemoveConstraint(
            model_name="circleparticipationend",
            name="sbg_circleparticipationend_composed_ck",
        ),
        migrations.AddConstraint(
            model_name="circlejoinrequest",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("initiated_by__in", ("APPLICANT", "DECIDER", "CIRCLE_MEMBER"))
                ),
                name="sbg_circlejoinrequest_initiator_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="circleparticipationend",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("actor__isnull", True),
                        ("actor_access_epoch__isnull", True),
                        ("actor_capacity", "NA_NO_PARTY"),
                        ("actor_state", "NONE"),
                        ("composed_from_event_uuid__isnull", False),
                        ("composer_identity__isnull", False),
                        ("composer_state", "REFERENCE"),
                        ("composing_rule__isnull", False),
                        ("composing_rule_reference__isnull", False),
                        ("composing_rule_state", "REFERENCE"),
                        ("entry_mode", "COMPOSED"),
                    ),
                    models.Q(
                        ("actor__isnull", False),
                        ("actor_access_epoch__isnull", False),
                        ("actor_capacity", "STANDING"),
                        ("actor_state", "PARTY"),
                        ("composed_from_event_uuid__isnull", False),
                        ("composer_identity__isnull", True),
                        ("composer_state", "NA_TRANSPORT"),
                        ("composing_rule__isnull", True),
                        ("composing_rule_reference__isnull", True),
                        ("composing_rule_state", "NA_TRANSPORT"),
                        ("entry_mode", "TRANSPORT"),
                    ),
                    _connector="OR",
                ),
                name="sbg_circleparticipationend_composed_ck",
            ),
        ),
        migrations.RemoveConstraint(model_name="circleadmissiondecision", name="sbg_circledecision_outcome_ck"),
        migrations.RemoveConstraint(model_name="circleadmissiondecision", name="sbg_circledecision_transport_ck"),
        migrations.AddConstraint(
            model_name="circleadmissiondecision",
            constraint=models.CheckConstraint(
                condition=models.Q(route__in=("CIRCLE_FOUNDING", "GOVERNANCE_CONSTITUTIONAL", "ORDINARY", "APPLICATION_MODE")),
                name="sbg_circledecision_route_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="circleadmissiondecision",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        route="CIRCLE_FOUNDING",
                        constitutional_route="NOT_APPLICABLE",
                        deciding_party="FOUNDING_BODY",
                    )
                    | models.Q(
                        route="GOVERNANCE_CONSTITUTIONAL",
                        constitutional_route="APPOINTMENT",
                        deciding_party="office:LO_COORDINATOR",
                    )
                    | models.Q(route="ORDINARY", constitutional_route="INSTRUMENT", deciding_party="INSTRUMENT")
                    | models.Q(route="APPLICATION_MODE", constitutional_route="APPLICATION",
                               deciding_party="office:CIRCLE_COORDINATOR", outcome__in=("ACCEPT", "DECLINE"))
                    | models.Q(route="APPLICATION_MODE", constitutional_route="APPLICATION",
                               deciding_party="APPLICANT", outcome="WITHDRAWN")
                ),
                name="sbg_circledecision_route_shape_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="circleadmissiondecision",
            constraint=models.CheckConstraint(
                condition=models.Q(outcome__in=("ACCEPT", "DECLINE")) | models.Q(route="APPLICATION_MODE", outcome="WITHDRAWN"),
                name="sbg_circledecision_outcome_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="circleadmissiondecision",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    actor_state="LIVING_ORGANISM",
                    actor__isnull=True,
                    actor_access_epoch__isnull=True,
                    actor_capacity="STANDING",
                    entry_mode="TRANSPORT",
                    composing_rule_state="NA_TRANSPORT",
                    composing_rule_reference__isnull=True,
                    composer_state="NA_TRANSPORT",
                    composer_identity__isnull=True,
                ) | models.Q(route="APPLICATION_MODE", outcome="WITHDRAWN", actor_state="PARTY",
                             actor__isnull=False, actor_access_epoch__isnull=False, actor_capacity="STANDING",
                             entry_mode="TRANSPORT", composing_rule_state="NA_TRANSPORT",
                             composing_rule_reference__isnull=True, composer_state="NA_TRANSPORT", composer_identity__isnull=True),
                name="sbg_circledecision_transport_ck",
            ),
        ),
        migrations.RunPython(migrations.RunPython.noop, reverse_population),
    ]
