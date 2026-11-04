"""C1-16 equivalence against the two pre-0057 record contracts on the run-owned clone."""
from datetime import timedelta
from hashlib import sha256

from django.db import connection
from django.test import TransactionTestCase

from core import models
from src.intevia.services.organism_authority import authority_target_fingerprint
from tests import test_c1b_conditions as conditions
from tests import test_c1b_departure as departures


class SharedAppendTests(TransactionTestCase):
    def assert_record(self, event, *, parent, party, request, key, payload, rule, actor,
                      evidence, decision, basis, occurred, effective, received, deadline,
                      action, prior, state):
        """Use raw SQL for envelope fields not mapped by the ORM, and the installed commitment preimages."""
        table = event._meta.db_table
        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT e.*, p.l1_commitment AS parent_l1,
                    public.s015_0022_digest('s015p1',public.s015_0022_parts_preimage(%s,e.event_uuid)) AS correct_parts,
                    public.s015_0022_digest('s015r1',public.s015_0022_l1_preimage(%s::regclass,to_jsonb(e))) AS correct_l1
                FROM public.{table} e LEFT JOIN public.{table} p ON p.id=%s WHERE e.id=%s
            """, [table, 'public.' + table, parent.pk if parent else None, event.pk])
            row = dict(zip((col[0] for col in cursor.description), cursor.fetchone()))
        command = rule != "C0:G2:C2"
        lineage = (authority_target_fingerprint({"event": str(event.event_uuid), "request": payload}) if command
                   else sha256(f"{event.event_uuid}:{payload}".encode()).hexdigest())
        temporal = "RETROSPECTIVE" if effective < received else "PROSPECTIVE" if effective > received else None
        expected = dict(sequence=parent.sequence+1 if parent else 1,
                        predecessor_id=parent.pk if parent else None, predecessor_sequence=parent.sequence if parent else None,
                        prior_state=prior, resulting_state=state, action=action,
                        actor_state="PARTY" if party else "NONE", actor_id=actor.pk if party else None,
                        actor_access_epoch=actor.access_epoch if party else None,
                        actor_capacity="STANDING" if party else "NA_NO_PARTY", enterer_identity_id=actor.pk,
                        entry_mode="TRANSPORT" if party else "COMPOSED", composing_rule_reference=rule,
                        composing_rule_state="NA_TRANSPORT" if party else "REFERENCE",
                        composer_state="NA_TRANSPORT" if party else "REFERENCE", composer_identity_id=None if party else actor.pk,
                        effective_until_state="FIXED" if deadline else "NONE", effective_until=deadline,
                        authority_basis_id=basis, authority_decision_reference=decision, evidence_reference=evidence,
                        request_reference=request, idempotency_key=key, payload_fingerprint=payload,
                        lineage_reference="s015l1:"+lineage, temporal_basis_kind=temporal,
                        temporal_basis_reference="s015tb1:"+payload if temporal else None,
                        effective_at=effective, occurred_at=occurred, received_at=received,
                        parts_commitment=row['correct_parts'], l1_commitment=row['correct_l1'],
                        predecessor_l1_commitment=row['parent_l1'], l2_commitment=None)
        anchor_col = "living_organism_id" if isinstance(event, models.LivingOrganismEvent) else "condition_id"
        self.assertEqual(set(row) - {"parent_l1", "correct_parts", "correct_l1"},
                         set(expected) | {"id", "event_uuid", "recorded_at", anchor_col},
                         "The stored row must have the complete original event column set.")
        self.assertEqual({k: row[k] for k in expected}, expected,
                         f"The {action} record must retain its pre-consolidation values and verifiable commitments.")
        self.assertGreaterEqual(row['recorded_at'], received, "The database stamp must cover the admitted receipt.")

    def test_trigger_then_departure_continue_one_chain(self):
        fx = departures.DepartureTests()
        fx.setUp()
        lo = fx.fx.organism
        actor = fx.fx.actor
        member = fx.fx.founder_member
        office = models.ContextualRoleAssignment.objects.get(membership=member, role_definition__code="COORDINATOR")
        history = models.LivingOrganismEvent.objects.filter(living_organism=lo)
        parent = history.latest("sequence")
        fx.fx._run(lambda: fx.fx.roles.transition_assignment(fx.fx._transition_command(office,"END",actor)),
                   "Ending the sole Coordinator office while its holder remains a member")
        ended = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=office, action="END")
        window = history.get(action="WITHOUT_COORDINATOR")
        trigger_key = "c2:" + str(ended.event_uuid)
        self.assert_record(window, parent=parent, party=False, actor=actor, rule="C0:G2:C2",
                           request=trigger_key, key=trigger_key, payload=ended.payload_fingerprint,
                           evidence=f"office:{ended.event_uuid}", decision=ended.authority_decision_reference,
                           basis=ended.authority_basis_id, occurred=ended.occurred_at, effective=ended.effective_at,
                           received=ended.received_at, deadline=ended.effective_at+timedelta(hours=720),
                           action="WITHOUT_COORDINATOR", prior="ACTIVE", state="RESTRICTED_CONTINUITY")
        # Validate the C2-only metadata on the sibling assessment and restriction.
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT a.result,a.roster_version,a.evaluated_state_at,
                       a.evaluated_known_at=a.recorded_at,a.determiner_capacity_reference,
                       rc.resulting_state,rc.permitted_measures_reference,rc.prohibited_effects_reference,
                       rc.accountable_actor_id
                FROM public.core_essentialcoveragecase c JOIN public.core_coverageassessment a ON a.case_id=c.id
                JOIN public.core_restrictedcontinuityevent rc ON rc.triggering_assessment_id=a.id
                WHERE c.living_organism_id=%s AND a.evidence_reference=%s
            """, [lo.pk, f"office:{ended.event_uuid}"])
            self.assertEqual(cursor.fetchall(), [
                ("UNSATISFIED", 1, ended.effective_at, True, ended.authority_decision_reference,
                 "RESTRICTED", "C2:preserve-leave-and-qualified-recovery", "C2:no-fallback-or-ordinary-execution", actor.pk)
            ], "The same coverage change must retain its roster, assessment cut, linked restriction and accountability.")
        cmd = fx.command(member)
        fx.fx._run(lambda: fx.service.leave_organism(cmd), "Leaving the last membership during its recovery window")
        left = models.OrganismMembershipTransition.objects.get(membership=member, resulting_state="ENDED")
        closed = history.get(action="DISSOLVE")
        self.assert_record(closed, parent=window, party=False, actor=actor, rule="C0:O-16:v1.2",
                           request=cmd.request_reference, key=cmd.idempotency_key, payload=left.payload_fingerprint,
                           evidence=f"membership-ending:{left.event_uuid}", decision=left.authority_decision_reference,
                           basis=left.authority_basis_id, occurred=cmd.occurred_at, effective=cmd.effective_at,
                           received=left.received_at, deadline=None, action="DISSOLVE", prior="RESTRICTED_CONTINUITY",
                           state="CLOSED")
        lo.refresh_from_db()
        self.assertEqual((lo.state,lo.head_event_id), ("CLOSED",closed.pk),
                         "The cache and recorded head must follow the command which continued the trigger's chain.")

    def test_personal_offer_retains_attribution_and_distinct_keys(self):
        fx = conditions.ConditionTests()
        fx.setUp()
        cmd = fx.command()
        saved = fx.fx._run(lambda: fx.service.record(cmd), "Recording a Coordinator's mentorship offer")
        event = models.MembershipConditionStateEvent.objects.get(event_uuid=saved.event_uuid)
        intake = models.CommandIntake.objects.get(actor=fx.fx.actor, idempotency_key=cmd.idempotency_key)
        self.assertEqual(str(event.condition.condition_uuid), str(saved.condition_uuid),
                         "The returned event must belong to the newly recorded condition.")
        self.assert_record(event, parent=None, party=True, actor=fx.fx.actor, rule=None,
                           request=cmd.request_reference, key=cmd.idempotency_key, payload=intake.request_fingerprint,
                           evidence=cmd.evidence_reference, decision=saved.authority_decision_reference,
                           basis=fx.fx.organism.founding_authority_basis_id, occurred=cmd.occurred_at,
                           effective=cmd.effective_at, received=intake.received_at, deadline=None,
                           action="OFFER", prior=None, state="OFFERED")
