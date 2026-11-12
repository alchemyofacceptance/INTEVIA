"""C2 services on the owned clone. No mail, scheduler sleep or network."""
import re
import threading
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from core import models
from src.intevia.services import organism_coverage as coverage, organism_recovery as recovery
from src.intevia.services import timed_events as te
from src.intevia.services.organism_authority import S015AuthorityRefusalCode
from tests import s015_0021_support as fixtures
from tests import election_support
from tests import test_c1b_departure as departures
from tests import test_law_acts as law
from tests.test_c1_rejoin import _attempt


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_STEPS = ("WITHOUT_COORDINATOR", "K2_DEACTIVATE", "K2_DISSOLVE")


def _owing(start, expiry):
    """The C2-06 fixture with one more member: an LO that owes one notice and two phase steps."""
    with transaction.atomic(), connection.cursor() as cursor:
        ids = fixtures.found_nonroot(cursor, uuid4().hex, fixture_at=start,
                                     founding_assignment_overrides={"effective_until_state": "FIXED", "effective_until": expiry})
    lo = models.LivingOrganism.objects.get(pk=ids["lo"])
    return lo, ids["identity"], law.add_member(lo)


def _graph(lo, founder, fixture_began):
    """Every LO event and coverage assessment of one LO, in order, in a form two histories can share.

    Generated ids are replaced by where the record sits in its own history. The enterer,
    actor and authority are given as "the founding's own, or not". Recording and receipt
    times are left out: two equivalent histories may differ in them. A time at or after
    ``fixture_began`` belongs to the fixture's own setup (the further member joins "now")
    and is compared only as that; every time C2 dates a record at is earlier, and is
    compared exactly. The keys a record cites are compared for the records C2 itself makes.
    """
    def rows(sql):
        with connection.cursor() as cursor:
            cursor.execute(sql, [lo.pk])
            names = [column[0] for column in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]

    shared = """x.sequence,x.predecessor_sequence,x.action,x.prior_state,x.effective_at,x.occurred_at,x.effective_until,
        x.effective_until_state,x.entry_mode,x.composing_rule_state,x.composing_rule_reference,x.actor_state,
        x.actor_capacity,x.evidence_reference,x.request_reference,x.idempotency_key,x.event_uuid,
        x.enterer_identity_id,x.actor_id,x.authority_basis_id,x.authority_decision_reference"""
    events = rows("SELECT x.resulting_state AS outcome," + shared + """ FROM public.core_livingorganismevent x
        WHERE x.living_organism_id=%s ORDER BY x.sequence""")
    assessments = rows("SELECT x.result AS outcome,x.roster_version," + shared + """ FROM public.core_coverageassessment x
        JOIN public.core_essentialcoveragecase c ON c.id=x.case_id WHERE c.living_organism_id=%s ORDER BY x.sequence""")
    offices = rows("""SELECT e.event_uuid FROM public.core_contextualroleassignmentstateevent e
        JOIN public.core_contextualroleassignment a ON a.id=e.assignment_id
        JOIN public.core_organismmembership m ON m.id=a.membership_id WHERE m.living_organism_id=%s ORDER BY e.id""")
    own = {str(lo.organism_id): "lo"}
    own |= {str(row["event_uuid"]): f"lo-event-{row['sequence']}" for row in events}
    own |= {str(row["event_uuid"]): f"assessment-{row['sequence']}" for row in assessments}
    own |= {str(row["event_uuid"]): f"office-event-{index}" for index, row in enumerate(offices)}
    founding = events[0]

    def shape(row):
        made_by_c2 = (row["evidence_reference"] or "").startswith(("c2-boundary:", "k2:"))
        keys = tuple(_UUID.sub(lambda found: own.get(found.group(0), found.group(0)), row[name] or "")
                     for name in ("evidence_reference", "request_reference", "idempotency_key")) if made_by_c2 else None
        return ({name: ("DURING THE FIXTURE" if name in ("effective_at", "occurred_at", "effective_until")
                        and value is not None and value >= fixture_began else value)
                 for name, value in row.items() if name not in (
            "evidence_reference", "request_reference", "idempotency_key", "event_uuid", "enterer_identity_id",
            "actor_id", "authority_basis_id", "authority_decision_reference")}
            | {"keys": keys, "enterer_is_founder": row["enterer_identity_id"] == founder,
               "actor_is_founder": row["actor_id"] == founder,
               "founding_authority": (row["authority_basis_id"] == founding["authority_basis_id"],
                                      row["authority_decision_reference"] == founding["authority_decision_reference"])})

    lo.refresh_from_db()
    return {"state": lo.state, "events": [shape(row) for row in events], "assessments": [shape(row) for row in assessments]}


class RecoveryTests(TransactionTestCase):
    def setUp(self):
        # Ordinary registration establishes current terms before the dated founding.
        # The second-window volunteer registers at the current receipt time.
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
        """A second-window volunteer can reactivate before its exclusive end."""
        from src.intevia.services import organism_role_service
        from src.intevia.services.organism_application_authority import ProductionApplicationAuthorityProvider
        from src.intevia.services.organism_authority import S015Authority
        from tests.test_sr_office_actions import SROfficeActionTests

        self.fx = SROfficeActionTests()
        self.fx.roles = organism_role_service.OrganismRoleService(
            authority=S015Authority(provider=ProductionApplicationAuthorityProvider()), clock=fixtures.database_now
        )
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

        # TIMED EVENTS (P0a-4 A): the same three steps, taken one per attempt by the engine,
        # leave the records the landed command leaves. Two more LOs owe what the one above owed.
        # Each has one further member, who is not the founder: that member carries the wakes.
        began = start + timedelta(days=75)
        commanded, founder_c, _member_c = _owing(start, expiry)
        woken, founder_w, carrier = _owing(start, expiry)
        def both():
            return _graph(commanded, founder_c, began), _graph(woken, founder_w, began)

        before = both()
        self.assertEqual(before[0], before[1], "C2-06 witness: the two LOs must start from equivalent histories.")
        self.assertEqual(recovery.advance("default", commanded.pk), 2, "C2-06 witness: the command takes both phase steps.")
        wake = te.Wake(str(woken.organism_id), carrier.pk, carrier.credential_id, carrier.access_epoch)
        made = models.LivingOrganismEvent.objects.filter(living_organism=woken, action__in=_STEPS).order_by("sequence")
        for step, purpose in enumerate(("NOTICE", "PHASE", "PHASE"), start=1):
            election_support.idle_until_turn_of(woken, carrier, "C2")
            if step == 2:
                # The notice left this LO a restricted-continuity case. A coverage publication appends
                # to that case, so the coverage claim tries it. Held elsewhere, it must give a prompt
                # skip with nothing made; the retry below is then the one success.
                held, let_go = threading.Event(), threading.Event()

                def hold_case():
                    try:
                        with transaction.atomic(), connection.cursor() as cursor:
                            cursor.execute("""SELECT id FROM public.core_restrictedcontinuitycase
                                WHERE living_organism_id=%s FOR UPDATE""", [woken.pk])
                            if cursor.fetchone() is not None:
                                held.set()
                            let_go.wait(30)
                    finally:
                        connection.close()

                holder = threading.Thread(target=hold_case)
                holder.start()
                try:
                    self.assertTrue(held.wait(30), "C2-06 held case: the notice must have left a restricted-continuity case to hold.")
                    skipped = te.run(wake, alias="default")
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute("""SELECT public.te_coverage_claim(%s), public.te_c2_claim(%s,'NOTICE','','','UNDER_LO'),
                            public.te_elect_claim(%s,'RESIGNATION','','COVERAGE','UNDER_LO'),
                            (SELECT count(*) FROM public.core_tereceipt WHERE organism_id=%s),
                            (SELECT busy_inner FROM public.core_temeter WHERE organism_id=%s)""", [woken.pk] * 5)
                        tried = cursor.fetchone()
                    self.assertTrue(holder.is_alive(), "C2-06 held case: the engine and the claims must return while the row is held.")
                    self.assertEqual(((skipped.result, skipped.consumer), list(made.values_list("action", flat=True)), tried),
                                     (("BUSY_INNER", None), list(_STEPS[:1]), (False, False, False, 1, 1)),
                                     "C2-06 held case: a held restricted-continuity case must give a prompt busy skip, by "
                                     "C2's claim and ELECT's alike, with no step made and no receipt added.")
                finally:
                    let_go.set()
                    holder.join(30)
                self.assertFalse(holder.is_alive(), "C2-06 held case: the other connection did not finish.")
            turn = te.run(wake, alias="default")
            self.assertEqual((turn.result, turn.consumer, turn.purpose), ("OPENED", "C2", purpose),
                             f"C2-06 witness: wake {step} must open one C2 step.")
            self.assertEqual(list(made.values_list("action", flat=True)), list(_STEPS[:step]),
                             f"C2-06 witness: wake {step} must take one step and no more.")
        after = both()
        self.assertEqual(after[1], after[0],
                         "C2-06 witness: three wakes must leave the records the command leaves, the enterers included.")
        self.assertEqual((after[0]["state"], len(after[0]["events"]) - len(before[0]["events"]),
                          len(after[0]["assessments"]) - len(before[0]["assessments"])), ("CLOSED", 3, 1),
                         "C2-06 witness: one notice (an assessment and the vacancy event) and two phase steps.")
        with connection.cursor() as cursor:
            cursor.execute("""SELECT r.purpose,r.outcome,r.rule_ref,r.enterer_id,r.due_at,
                    (SELECT x.enterer_identity_id FROM public.core_coverageassessment x WHERE x.event_uuid::text=r.effect_ref
                     UNION ALL SELECT x.enterer_identity_id FROM public.core_livingorganismevent x WHERE x.event_uuid::text=r.effect_ref)
                FROM public.core_tereceipt r WHERE r.organism_id=%s ORDER BY r.due_at""", [woken.pk])
            receipts = cursor.fetchall()
        self.assertNotEqual(carrier.pk, founder_w, "C2-06 witness: the carrier must not be the founder.")
        self.assertEqual(receipts, [
            ("NOTICE", "OPENED", "C0:G2:C2", founder_w, expiry, founder_w),
            ("PHASE", "OPENED", "HG:RC-1:K-2/H-4", founder_w, expiry+timedelta(days=30), founder_w),
            ("PHASE", "OPENED", "HG:RC-1:K-2/H-4", founder_w, expiry+timedelta(days=60), founder_w)],
            "C2-06 witness: each receipt names its effect's own enterer, as landed, and never the carrier.")
        # A replay of either route adds nothing to either history.
        election_support.idle_until_turn_of(woken, carrier, "C2")
        again = te.run(wake, alias="default")
        self.assertEqual((recovery.advance("default", commanded.pk), again.result, again.consumer), (0, "IDLE", "C2"),
                         "C2-06 witness: a replay must find nothing owed.")
        self.assertEqual(both(), after,
                         "C2-06 witness: a replay must leave both histories as they were.")
