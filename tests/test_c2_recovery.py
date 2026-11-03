"""C2 services on the owned clone. No mail, scheduler sleep or network."""
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from core import models
from src.intevia.services import organism_coverage as coverage, organism_recovery as recovery
from src.intevia.services.organism_authority import S015AuthorityRefusalCode
from tests import s015_0021_support as fixtures
from tests import test_c1b_departure as departures
from tests.test_c1_rejoin import _attempt


class RecoveryTests(TransactionTestCase):
    def setUp(self):
        self.departure = departures.DepartureTests()
        self.departure.setUp()
        self.fx = self.departure.fx
        self.lo = self.fx.organism
        self.holder = self.fx.founder_member
        self.remains = self.fx._member()

    def depart(self):
        return self.departure.service.leave_organism(self.departure.command(self.holder))

    def history(self):
        return models.LivingOrganismEvent.objects.filter(living_organism=self.lo).order_by("sequence")

    def test_departure_and_recovery_roll_back_together(self):
        cmd = self.departure.command(self.holder)
        def do_leave():
            return self.departure.service.leave_organism(cmd)
        before = list(self.history().values_list("event_uuid", flat=True))
        assessments = models.CoverageAssessment.objects.filter(case__living_organism=self.lo)
        old_count = assessments.count()
        start = recovery.start

        def interrupt_after_window(*args):
            start(*args)
            raise ValueError("C2 witness interrupted after the window opened")

        with patch.object(recovery, "start", interrupt_after_window):
            err, _ = _attempt(do_leave)
        self.assertEqual(str(err), "C2 witness interrupted after the window opened",
                         "The rollback witness must reach the recorded start of this organisation's countdown.")
        self.holder.refresh_from_db()
        self.assertEqual((self.holder.state, assessments.count(), list(self.history().values_list("event_uuid", flat=True))),
                         ("ACTIVE", old_count, before),
                         "A late failure must roll back the membership ending, assessment and countdown together.")
        err, _ = _attempt(do_leave)
        self.assertIsNone(err, f"Retrying the same admitted departure should open one window. Got {type(err).__name__}.")
        self.assertEqual(self.history().filter(action="WITHOUT_COORDINATOR").count(), 1,
                         "The retried departure must leave exactly one governed countdown.")

    def test_seating_coordinator_restores_active_with_governance_office_still_vacant(self):
        _, definition = self.fx._define()
        board_member, first_holder = self.fx._appoint(definition)
        models.EssentialCoverageRequirement.objects.create(
            living_organism=self.lo, roster_version=1, responsibility_domain_code="TEST_SECOND_DOMAIN",
            qualifying_role_code=definition.code, scope=definition.scope, minimum_active_occupants=1,
            effective_from=fixtures.database_now())
        self.fx._run(lambda: self.fx.roles.transition_assignment(
            self.fx._transition_command(first_holder, "END", actor=board_member.identity)), "Resigning the Board office")
        ended = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=first_holder, action="END")
        board_assessment = models.CoverageAssessment.objects.get(case__living_organism=self.lo,
                                                                evidence_reference=f"office:{ended.event_uuid}")
        now = fixtures.database_now()
        board_facts = coverage.assess("default", self.lo, now, now)
        self.lo.refresh_from_db()
        self.assertEqual((self.lo.state, board_assessment.result, board_facts.deficits, board_facts.coordinator_result),
                         ("ACTIVE", "UNSATISFIED", ((definition.scope, definition.code),), "SATISFIED"),
                         "A Board resignation must record the vacancy and leave the LO active.")
        seated, _ = self.fx._run(self.fx._seat_founder, "Creating and seating the GOVERNANCE Circle during the vacancy")
        self.assertTrue(models.CircleParticipation.objects.filter(participation_uuid=seated.participation_uuid).exists(),
                        "The governance seat must take effect while the Board office is vacant.")

        _, held = self.fx._nominate(definition, self.holder)
        self.fx.roles.transition_assignment(self.fx._transition_command(held, "ASSIGN"))
        self.fx._run(self.depart, "Leaving with two essential offices")
        held.refresh_from_db()
        self.holder.refresh_from_db()
        now = fixtures.database_now()
        facts = coverage.assess("default", self.lo, now, now)
        assessment = models.CoverageAssessment.objects.filter(case__living_organism=self.lo).latest("sequence")
        restricted = models.RestrictedContinuityEvent.objects.get(triggering_assessment=assessment)
        self.assertEqual((self.holder.state, held.state, facts.result, set(facts.deficits),
                          assessment.result, restricted.resulting_state, self.history().latest("sequence").action),
                         ("ENDED", "ENDED", "UNSATISFIED", {("LIVING_ORGANISM", "COORDINATOR"),
                          (definition.scope, definition.code)}, "UNSATISFIED", "RESTRICTED", "WITHOUT_COORDINATOR"),
                         "The leave must record both vacancies, restricted measures and the Coordinator countdown.")
        role = models.OrganismRoleDefinition.objects.get(living_organism=self.lo, code="COORDINATOR", active=True)
        appointment, _ = self.self_appoint(role)
        assigned = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=appointment, action="ASSIGN")
        fresh = models.CoverageAssessment.objects.get(case__living_organism=self.lo, evidence_reference=f"office:{assigned.event_uuid}")
        fresh_facts = coverage.assess("default", self.lo, fresh.evaluated_state_at, fresh.evaluated_known_at)
        measures = models.RestrictedContinuityEvent.objects.get(triggering_assessment=fresh)
        restored = self.history().latest("sequence")
        self.lo.refresh_from_db()
        with connection.cursor() as cursor:
            cursor.execute("SELECT effective_until FROM public.core_livingorganismevent WHERE id = %s", [restored.pk])
            (restored_deadline,) = cursor.fetchone()
        self.assertEqual((restored.action, restored.resulting_state, self.lo.state, restored_deadline,
                          restored.evidence_reference, fresh.result, fresh_facts.deficits,
                          fresh_facts.coordinator_result, measures.resulting_state),
                         ("K2_RESTORED", "ACTIVE", "ACTIVE", None, f"coverage-assessment:{fresh.event_uuid}",
                          "UNSATISFIED", ((definition.scope, definition.code),), "SATISFIED", "NOT_RESTRICTED"),
                         "Seating the Coordinator must restore the LO immediately and preserve the unfilled Board office.")

    def self_appoint(self, role):
        member = self.remains
        proposed = self.fx._run(lambda: self.fx.roles.propose_assignment(
            self.fx._proposal_command(role, member, actor=member.identity)), "Self-nominating for the vacant office")
        assignment = models.ContextualRoleAssignment.objects.get(assignment_uuid=proposed.assignment_uuid)
        result = self.fx._run(lambda: self.fx.roles.transition_assignment(
            self.fx._transition_command(assignment, "ASSIGN", actor=member.identity)), "Taking the vacant office")
        return assignment, result

    def test_volunteer_consent_then_fresh_assessment_restores(self):
        _, board_role = self.fx._define()
        _, held = self.fx._appoint(board_role, self.remains)
        self.fx._appoint(board_role)
        models.EssentialCoverageRequirement.objects.create(
            living_organism=self.lo, roster_version=1, responsibility_domain_code="SECOND",
            qualifying_role_code=board_role.code, scope=board_role.scope,
            minimum_active_occupants=1, effective_from=fixtures.database_now())
        self.depart()
        self.fx._run(lambda: self.fx.roles.transition_assignment(
            self.fx._transition_command(held, "END", actor=self.remains.identity)), "Resigning an essential office during K-2")
        held.refresh_from_db()
        self.remains.refresh_from_db()
        ended = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=held, action="END")
        self.assertEqual((held.state, self.remains.state,
                          models.CoverageAssessment.objects.get(case__living_organism=self.lo,
                              evidence_reference=f"office:{ended.event_uuid}").result),
                         ("ENDED", "ACTIVE", "UNSATISFIED"),
                         "Resigning ends only this holder's appointment and records the outstanding Coordinator vacancy.")
        origin = self.history().get(action="WITHOUT_COORDINATOR")
        role = models.OrganismRoleDefinition.objects.get(living_organism=self.lo, code="COORDINATOR", active=True)

        def volunteer():
            cmd = self.fx._proposal_command(role, self.remains, actor=self.remains.identity)
            result = self.fx.roles.propose_assignment(cmd)
            return models.ContextualRoleAssignment.objects.get(assignment_uuid=result.assignment_uuid)

        def appoint(assignment):
            return self.fx.roles.transition_assignment(self.fx._transition_command(assignment, "ASSIGN", actor=self.remains.identity))

        first = self.fx._run(volunteer, "Volunteering in the current recovery window")
        self.fx.consent.record_office_consent(self.fx._consent_command(self.remains, first, "WITHDRAW"))
        err, _ = _attempt(lambda: appoint(first))
        self.assertEqual(getattr(err, "refusal_code", None), S015AuthorityRefusalCode.C_OFFICE_CONSENT_ABSENT_OR_WITHDRAWN,
                         "An open recovery window must still refuse the volunteer's withdrawn nomination.")
        second = self.fx._run(volunteer, "Volunteering again with a fresh nomination")
        result = self.fx._run(lambda: appoint(second), "Restoring coverage through the fresh appointment")
        appointment = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=second, action="ASSIGN")
        assessment = models.CoverageAssessment.objects.get(case__living_organism=self.lo,
                                                          evidence_reference=f"office:{appointment.event_uuid}")
        restored = self.history().filter(action="K2_RESTORED").first()
        self.assertIsNotNone(restored, "The accepted volunteer appointment must leave a distinct restoration event.")
        decision = models.AuthorityDecision.objects.get(decision_reference=result.authority_decision_reference)
        self.assertEqual((restored.predecessor_id, restored.evidence_reference, assessment.result, restored.resulting_state,
                          decision.target.get("k2_episode")),
                         (origin.pk, f"coverage-assessment:{assessment.event_uuid}", "SATISFIED", "ACTIVE", str(origin.event_uuid)),
                         "The authorised appointment must restore this same episode through its own fresh, satisfied assessment.")

    def test_second_window_volunteer_reactivates_before_exclusive_end(self):
        # Expire a genuine fixed-term Coordinator appointment 35 days ago.
        # Its first window has elapsed, and its second still has time to run.
        start = fixtures.database_now() - timedelta(days=40)
        with transaction.atomic(), connection.cursor() as cursor:
            ids = fixtures.found_nonroot(cursor, uuid4().hex, fixture_at=start, historical_terms=True,
                        founding_assignment_overrides={"effective_until_state": "FIXED",
                                                      "effective_until": start + timedelta(days=5)})
        self.lo = self.fx.organism = models.LivingOrganism.objects.get(pk=ids["lo"])
        self.fx.ids = ids
        self.remains = self.fx._member(effective_at=start + timedelta(days=1))
        role = models.OrganismRoleDefinition.objects.get(pk=ids["roles"]["COORDINATOR"])
        # The office request catches up the missed first deadline before its real receipt.
        cmd = self.fx._proposal_command(role, self.remains, actor=self.remains.identity)
        first = self.fx._run(lambda: self.fx.roles.propose_assignment(cmd), "Volunteering during the second window")
        parent = self.history().get(action="K2_DEACTIVATE")
        now = fixtures.database_now()
        phase = recovery.head_at("default", self.lo, now, now)
        self.assertEqual((phase.pk, phase.resulting_state, phase.deadline - phase.effective_at),
                         (parent.pk, "DORMANT", timedelta(hours=720)),
                         "A late worker must leave the original second deadline fixed.")
        appointment = models.ContextualRoleAssignment.objects.get(assignment_uuid=first.assignment_uuid)
        with patch("src.intevia.services.clock.database_now", return_value=phase.deadline), patch.object(
                recovery, "database_now", return_value=phase.deadline):
            err, _ = _attempt(lambda: self.fx.roles.transition_assignment(
                self.fx._transition_command(appointment, "ASSIGN", actor=self.remains.identity)))
        appointment.refresh_from_db()
        self.assertEqual(getattr(err, "refusal_code", None), S015AuthorityRefusalCode.C_LO_CLOSED,
                      "Reaching the second deadline must refuse an appointment even before the worker records dissolution.")
        self.assertEqual(appointment.state, "PROPOSED", "The refused appointment must leave the nomination pending.")
        result = self.fx._run(lambda: self.fx.roles.transition_assignment(
            self.fx._transition_command(appointment, "ASSIGN", actor=self.remains.identity)), "Rescuing a dormant LO")
        restored = self.history().get(action="K2_RESTORED")
        assigned = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=appointment, action="ASSIGN")
        assessed = models.CoverageAssessment.objects.get(case__living_organism=self.lo, evidence_reference=f"office:{assigned.event_uuid}")
        decision = models.AuthorityDecision.objects.get(decision_reference=result.authority_decision_reference)
        self.assertEqual((restored.predecessor_id, restored.resulting_state, restored.evidence_reference,
                          assessed.result, decision.target["k2_episode"], recovery.advance("default", self.lo.pk)),
                         (parent.pk, "ACTIVE", f"coverage-assessment:{assessed.event_uuid}", "SATISFIED",
                          str(parent.predecessor.event_uuid), 0),
                         "The second-window volunteer must reactivate this episode through a fresh assessment and cancel its countdown.")

    def test_existing_roster_cannot_certify_a_false_assessment(self):
        old = models.CoverageAssessment.objects.filter(case__living_organism=self.lo).latest("sequence")
        now = fixtures.database_now()
        unseen = coverage.assess("default", self.lo, now, old.case.living_organism.created_at-timedelta(seconds=1))
        self.assertEqual(unseen.result, "UNKNOWN", "An organisation's roster cannot prove coverage before its records were known.")
        source = self.history().first()
        # Real founded case and chain predecessor. Only the claimed result is false.
        with self.assertRaisesRegex(IntegrityError, "disagrees", msg="The commit guard must refuse a false shortage on a covered roster."):
            with transaction.atomic(), connection.cursor() as cursor:
                fixtures.append_event(cursor, "core_coverageassessment", old.case_id, old.sequence+1, "ASSESS",
                                      old.result, "UNSATISFIED", self.holder.identity_id, source.authority_basis_id, uuid4().hex,
                                      pred_id=old.pk, eff=now, occ=now, rec=now, extra={
                                          "roster_version": old.roster_version, "evaluated_state_at": now,
                                          "evaluated_known_at": now, "determiner_capacity_reference": source.authority_decision_reference})
        self.assertEqual(models.CoverageAssessment.objects.filter(case=old.case).latest("sequence").pk, old.pk,
                         "A rejected assessment must preserve the last governed coverage record.")

class TimedWorkerTests(TransactionTestCase):
    def test_worker_replays_both_boundaries_once(self):
        # Make the *effective* founding and office term historical. Received and
        # recorded evidence stays on the real owned DB; no product clock is replaced.
        start = fixtures.database_now() - timedelta(days=75)
        expiry = start + timedelta(days=5)
        with transaction.atomic(), connection.cursor() as cursor:
            ids = fixtures.found_nonroot(cursor, uuid4().hex, fixture_at=start,
                                       founding_assignment_overrides={"effective_until_state": "FIXED", "effective_until": expiry})
        lo = models.LivingOrganism.objects.get(pk=ids["lo"])
        err, count = _attempt(lambda: recovery.advance("default", lo.pk))
        self.assertIsNone(err, f"The late worker should commit both elapsed recovery windows. Got {type(err).__name__}.")
        events = list(models.LivingOrganismEvent.objects.filter(living_organism=lo,
                      action__in=["WITHOUT_COORDINATOR", "K2_DEACTIVATE", "K2_DISSOLVE"])
                      .order_by("sequence").values_list("action", "effective_at", "resulting_state"))
        self.assertEqual(events, [("WITHOUT_COORDINATOR", expiry, "RESTRICTED_CONTINUITY"),
                                  ("K2_DEACTIVATE", expiry+timedelta(days=30), "DORMANT"),
                                  ("K2_DISSOLVE", expiry+timedelta(days=60), "CLOSED")],
                         "A missed run must use the expiry and two snapshotted deadlines, even when both windows elapsed.")
        self.assertEqual((count, recovery.advance("default", lo.pk)), (2, 0),
                         "Repeating the timed worker must append neither a second deactivation nor a second dissolution.")
