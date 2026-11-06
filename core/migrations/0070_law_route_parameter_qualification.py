"""LIB P2e: qualify the LAW route's actor parameter; preserve landed 0067."""
from django.db import migrations

FORWARD = r"""
CREATE OR REPLACE FUNCTION public.l_route(lo public.core_livingorganism, person bigint, class_name text, owner text,
 scope text, charter boolean, requested text, elect jsonb, cut timestamptz, known timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE route text; office uuid; count_ integer; born boolean; state_ text; founder_office bigint;
BEGIN
 state_:=public.s015_fold_effective_state('core_livingorganism',lo.id,cut,known);
 IF state_ IS NULL OR state_ NOT IN ('ACTIVE','FOUNDING_PENDING')
    OR NOT public.c2_basis_in_force(lo.founding_authority_basis_id,cut,known) THEN
   RETURN jsonb_build_object('reason','This organisation needs a current founding basis and an active or founding state.');
 END IF;
 IF requested IN ('CQ_MEMBERSHIP','CQ_CIRCLE','CQ_GOVERNANCE_CIRCLE','STEWARDS_AUTHORISED','STEWARDS_PROPOSE_FOUNDER_AUTHORISES') THEN
   RETURN jsonb_build_object('reason','This collective route opens when DISCUSSION provides the governed CQ vote.');
 END IF;
 SELECT EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.living_organism_id=lo.id
   AND e.action='BIRTHING' AND e.effective_at<=cut AND e.received_at<=known AND e.recorded_at<=known) INTO born;
 IF lo.platform_root THEN
   founder_office:=public.r_founder_office(lo.id,lo.founder_identity_id,cut,known);
   IF founder_office IS NULL THEN
     RETURN jsonb_build_object('reason','Root authority requires the effective Founder and Steward or a functioning, authorised Circle of Stewards.');
   END IF;
 END IF;
 IF scope='PLATFORM' OR charter OR (lo.platform_root AND class_name IN ('ARTICLE','MEMBERS_RESOLUTION','BYLAW')) THEN
   IF NOT lo.platform_root THEN RETURN jsonb_build_object('reason','Only INTEVIA can change inherited law or its charters.'); END IF;
   IF l_route.person<>lo.founder_identity_id THEN
     RETURN jsonb_build_object('reason','Only the effective Founder and Steward may authorise this protected root act before collective governance.');
   END IF;
   route:='FOUNDER_STEWARD';
 ELSIF NOT born THEN
   IF l_route.person<>lo.founder_identity_id THEN RETURN jsonb_build_object('reason','Before Birthing only the founder may change law.'); END IF;
   route:='FOUNDER_CONSTITUTIVE';
 ELSIF NOT (elect ? l_route.person::text) THEN
   RETURN jsonb_build_object('reason','This decision requires eligible membership in this organisation.');
 ELSIF class_name IN ('ARTICLE','MEMBERS_RESOLUTION','BYLAW') THEN
   SELECT count(*) INTO count_ FROM jsonb_object_keys(elect);
   IF count_>=3 THEN RETURN jsonb_build_object('reason','This protected law needs a collective vote. Its CQ route awaits DISCUSSION.'); END IF;
   IF count_=2 THEN route:='UNANIMOUS_PAIR'; ELSE route:='SOLE_MEMBER_COORDINATOR'; END IF;
 ELSIF requested='GOVERNANCE_INTERIM' OR owner='GOVERNANCE' THEN
   SELECT a.assignment_uuid INTO office FROM public.core_circle c
      CROSS JOIN LATERAL public.circle_holders(c.id,cut,known) h
      JOIN public.core_contextualroleassignment a ON a.circle_id=c.id
      JOIN public.core_organismmembership m ON m.id=a.membership_id AND m.identity_id=h.person
      JOIN public.c2_seat_facts(lo.id,cut,known) f ON f.assignment_id=a.id AND f.role_scope='CIRCLE'
         AND f.role_code='COORDINATOR' AND f.standing='QUALIFIED'
      WHERE c.parent_organism_id=lo.id AND c.kind='GOVERNANCE' AND h.person=l_route.person
      AND public.s015_fold_effective_state('core_circle',c.id,cut,known)='ACTIVE' ORDER BY a.id LIMIT 1;
   IF office IS NULL THEN
     RETURN jsonb_build_object('reason','The interim board route requires an effective Coordinator of the GOVERNANCE Circle.');
   END IF;
   route:='GOVERNANCE_INTERIM';
 ELSIF owner='CIRCLE' OR requested='CIRCLE_INTERIM' THEN
   RETURN jsonb_build_object('reason','Circle delegation of law is not yet available.');
 ELSE route:='COORDINATOR_ROUTE';
 END IF;
 IF route IN ('SOLE_MEMBER_COORDINATOR','COORDINATOR_ROUTE') THEN
   SELECT a.assignment_uuid INTO office FROM public.c2_seat_facts(lo.id,cut,known) f
   JOIN public.core_contextualroleassignment a ON a.id=f.assignment_id
   JOIN public.core_organismmembership m ON m.id=f.membership_id
   WHERE f.role_scope='LIVING_ORGANISM' AND f.role_code='COORDINATOR' AND f.standing='QUALIFIED'
     AND a.circle_id IS NULL AND m.identity_id=l_route.person ORDER BY a.id LIMIT 1;
   IF office IS NULL THEN RETURN jsonb_build_object('reason','This act needs the effective LO Coordinator, recorded in that capacity.'); END IF;
 END IF;
 IF lo.platform_root AND l_route.person<>lo.founder_identity_id AND NOT EXISTS (
   SELECT FROM public.core_contextualroleassignment a JOIN public.core_contextualroleassignmentstateevent e
       ON e.assignment_id=a.id JOIN public.core_authoritydecision d ON d.decision_reference=e.authority_decision_reference
    WHERE a.assignment_uuid=office AND e.action='ASSIGN' AND d.outcome='QUALIFIED' AND d.actor_id=lo.founder_identity_id
      AND e.effective_at<=cut AND e.recorded_at<=known AND public.r_founder_office(lo.id,d.actor_id,e.effective_at,e.recorded_at) IS NOT NULL
      AND e.id=(SELECT latest.id FROM public.core_contextualroleassignmentstateevent latest
         WHERE latest.assignment_id=a.id AND latest.action='ASSIGN' AND latest.effective_at<=cut AND latest.recorded_at<=known
         ORDER BY latest.effective_at DESC,latest.sequence DESC LIMIT 1)) THEN
   RETURN jsonb_build_object('reason','This ordinary root office needs a recorded authority chain to the Founder and Steward.');
 END IF;
 IF requested IS NOT NULL AND requested<>route THEN
   RETURN jsonb_build_object('reason',CASE route
     WHEN 'UNANIMOUS_PAIR' THEN 'Both eligible members must record their agreement separately.'
     WHEN 'SOLE_MEMBER_COORDINATOR' THEN 'The sole eligible member must act as the recorded LO Coordinator.'
     WHEN 'FOUNDER_STEWARD' THEN 'The effective Founder and Steward must authorise this root decision.'
     WHEN 'FOUNDER_CONSTITUTIVE' THEN 'Before Birthing, the founder must make this decision.'
     WHEN 'GOVERNANCE_INTERIM' THEN 'Use the interim route of the active GOVERNANCE Circle.'
     ELSE 'Use the effective LO Coordinator’s route for this law.' END);
 END IF;
 RETURN jsonb_build_object('route',route,'office',office,'root_office',founder_office);
END $$;
"""

REVERSE = r"""
CREATE OR REPLACE FUNCTION public.l_route(lo public.core_livingorganism, person bigint, class_name text, owner text,
 scope text, charter boolean, requested text, elect jsonb, cut timestamptz, known timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE route text; office uuid; count_ integer; born boolean; state_ text; founder_office bigint;
BEGIN
 state_:=public.s015_fold_effective_state('core_livingorganism',lo.id,cut,known);
 IF state_ IS NULL OR state_ NOT IN ('ACTIVE','FOUNDING_PENDING')
    OR NOT public.c2_basis_in_force(lo.founding_authority_basis_id,cut,known) THEN
   RETURN jsonb_build_object('reason','This organisation needs a current founding basis and an active or founding state.');
 END IF;
 IF requested IN ('CQ_MEMBERSHIP','CQ_CIRCLE','CQ_GOVERNANCE_CIRCLE','STEWARDS_AUTHORISED','STEWARDS_PROPOSE_FOUNDER_AUTHORISES') THEN
   RETURN jsonb_build_object('reason','This collective route opens when DISCUSSION provides the governed CQ vote.');
 END IF;
 SELECT EXISTS(SELECT FROM public.core_livingorganismevent e WHERE e.living_organism_id=lo.id
   AND e.action='BIRTHING' AND e.effective_at<=cut AND e.received_at<=known AND e.recorded_at<=known) INTO born;
 IF lo.platform_root THEN
   founder_office:=public.r_founder_office(lo.id,lo.founder_identity_id,cut,known);
   IF founder_office IS NULL THEN
     RETURN jsonb_build_object('reason','Root authority requires the effective Founder and Steward or a functioning, authorised Circle of Stewards.');
   END IF;
 END IF;
 IF scope='PLATFORM' OR charter OR (lo.platform_root AND class_name IN ('ARTICLE','MEMBERS_RESOLUTION','BYLAW')) THEN
   IF NOT lo.platform_root THEN RETURN jsonb_build_object('reason','Only INTEVIA can change inherited law or its charters.'); END IF;
   IF person<>lo.founder_identity_id THEN
     RETURN jsonb_build_object('reason','Only the effective Founder and Steward may authorise this protected root act before collective governance.');
   END IF;
   route:='FOUNDER_STEWARD';
 ELSIF NOT born THEN
   IF person<>lo.founder_identity_id THEN RETURN jsonb_build_object('reason','Before Birthing only the founder may change law.'); END IF;
   route:='FOUNDER_CONSTITUTIVE';
 ELSIF NOT (elect ? person::text) THEN
   RETURN jsonb_build_object('reason','This decision requires eligible membership in this organisation.');
 ELSIF class_name IN ('ARTICLE','MEMBERS_RESOLUTION','BYLAW') THEN
   SELECT count(*) INTO count_ FROM jsonb_object_keys(elect);
   IF count_>=3 THEN RETURN jsonb_build_object('reason','This protected law needs a collective vote. Its CQ route awaits DISCUSSION.'); END IF;
   IF count_=2 THEN route:='UNANIMOUS_PAIR'; ELSE route:='SOLE_MEMBER_COORDINATOR'; END IF;
 ELSIF requested='GOVERNANCE_INTERIM' OR owner='GOVERNANCE' THEN
   SELECT a.assignment_uuid INTO office FROM public.core_circle c
      CROSS JOIN LATERAL public.circle_holders(c.id,cut,known) h
      JOIN public.core_contextualroleassignment a ON a.circle_id=c.id
      JOIN public.core_organismmembership m ON m.id=a.membership_id AND m.identity_id=h.person
      JOIN public.c2_seat_facts(lo.id,cut,known) f ON f.assignment_id=a.id AND f.role_scope='CIRCLE'
         AND f.role_code='COORDINATOR' AND f.standing='QUALIFIED'
      WHERE c.parent_organism_id=lo.id AND c.kind='GOVERNANCE' AND h.person=person
      AND public.s015_fold_effective_state('core_circle',c.id,cut,known)='ACTIVE' ORDER BY a.id LIMIT 1;
   IF office IS NULL THEN
     RETURN jsonb_build_object('reason','The interim board route requires an effective Coordinator of the GOVERNANCE Circle.');
   END IF;
   route:='GOVERNANCE_INTERIM';
 ELSIF owner='CIRCLE' OR requested='CIRCLE_INTERIM' THEN
   RETURN jsonb_build_object('reason','Circle delegation of law is not yet available.');
 ELSE route:='COORDINATOR_ROUTE';
 END IF;
 IF route IN ('SOLE_MEMBER_COORDINATOR','COORDINATOR_ROUTE') THEN
   SELECT a.assignment_uuid INTO office FROM public.c2_seat_facts(lo.id,cut,known) f
   JOIN public.core_contextualroleassignment a ON a.id=f.assignment_id
   JOIN public.core_organismmembership m ON m.id=f.membership_id
   WHERE f.role_scope='LIVING_ORGANISM' AND f.role_code='COORDINATOR' AND f.standing='QUALIFIED'
     AND a.circle_id IS NULL AND m.identity_id=person ORDER BY a.id LIMIT 1;
   IF office IS NULL THEN RETURN jsonb_build_object('reason','This act needs the effective LO Coordinator, recorded in that capacity.'); END IF;
 END IF;
 IF lo.platform_root AND person<>lo.founder_identity_id AND NOT EXISTS (
   SELECT FROM public.core_contextualroleassignment a JOIN public.core_contextualroleassignmentstateevent e
       ON e.assignment_id=a.id JOIN public.core_authoritydecision d ON d.decision_reference=e.authority_decision_reference
    WHERE a.assignment_uuid=office AND e.action='ASSIGN' AND d.outcome='QUALIFIED' AND d.actor_id=lo.founder_identity_id
      AND e.effective_at<=cut AND e.recorded_at<=known AND public.r_founder_office(lo.id,d.actor_id,e.effective_at,e.recorded_at) IS NOT NULL
      AND e.id=(SELECT latest.id FROM public.core_contextualroleassignmentstateevent latest
         WHERE latest.assignment_id=a.id AND latest.action='ASSIGN' AND latest.effective_at<=cut AND latest.recorded_at<=known
         ORDER BY latest.effective_at DESC,latest.sequence DESC LIMIT 1)) THEN
   RETURN jsonb_build_object('reason','This ordinary root office needs a recorded authority chain to the Founder and Steward.');
 END IF;
 IF requested IS NOT NULL AND requested<>route THEN
   RETURN jsonb_build_object('reason',CASE route
     WHEN 'UNANIMOUS_PAIR' THEN 'Both eligible members must record their agreement separately.'
     WHEN 'SOLE_MEMBER_COORDINATOR' THEN 'The sole eligible member must act as the recorded LO Coordinator.'
     WHEN 'FOUNDER_STEWARD' THEN 'The effective Founder and Steward must authorise this root decision.'
     WHEN 'FOUNDER_CONSTITUTIVE' THEN 'Before Birthing, the founder must make this decision.'
     WHEN 'GOVERNANCE_INTERIM' THEN 'Use the interim route of the active GOVERNANCE Circle.'
     ELSE 'Use the effective LO Coordinator’s route for this law.' END);
 END IF;
 RETURN jsonb_build_object('route',route,'office',office,'root_office',founder_office);
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0069_library_policy_publication")]
    operations = [migrations.RunSQL(FORWARD, reverse_sql=REVERSE)]
