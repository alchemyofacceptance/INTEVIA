"""Item 38. Dated vacancy with real custody and dated loss of the LO's last Coordinator."""
from dataclasses import replace
from datetime import timedelta
import threading
from uuid import uuid4

from django.db import connection, connections, transaction
from django.test import TransactionTestCase

from core import models
from intevia.test_postgresql_backend import clock
from src.intevia.services import committees, elections, timed_events as te
from src.intevia.services import organism_role_definition_service as definitions
from src.intevia.services.organism_role_service import OrganismRoleService
from tests import cq_authority_support as offices, test_sr_office_actions as office_witness
from tests import committee_support as c, election_support as fx, s015_0021_support as support
from tests import test_c2_recovery as coverage, test_circle_committee_dormancy as committee_witness
from tests import test_election_mandates as mandates, test_law_acts as law, test_timed_events as timed

one = committee_witness.one


class DatedCustodyAndCoverageTests(TransactionTestCase):
    topic = mandates.ElectionMandateTests.topic
    activate_board = mandates.ElectionMandateTests.activate_board

    def test_elect_vacancy_commits_the_seats_work_return(self):
        """W16, item 38. A dated seat vacancy returns work in actual custody to the Board without a Chair."""
        article = {}
        def keep_article(owner, issuer, binding, version):
            article.update(binding_uuid=binding, version_id=version)

        # Fixture date as in C-04, chosen well beyond the set-up commits.
        due = support.database_now() + timedelta(days=1)
        seat_name = "trustee-2"
        provision = "Trustee notice"
        vacancy_rule = "Wait for the next members' meeting"
        lo, general, board, people = c.formation_board(
            self, offering="COMPLIMENTARY_BETA", after_article=keep_article,
            charter_fields={
                "resignations": {provision: {"place": seat_name, "effect": "DATED", "effective_at": due.isoformat()}},
                "vacancies": {vacancy_rule: {"type": "SEAT", "place": seat_name, "route": "WAIT",
                                             "until": "next members' meeting"}},
            })
        founder, _, occupant = people
        doc, version = c.first_version(founder, {"committee_work": {"return_to": "CHAIR"}})
        policy = c.instrument(lo, doc, version)
        c.decide_instrument(self, board, policy, people)
        circle = models.Circle.objects.get(circle_uuid=board["circle_uuid"])
        seat = models.CircleParticipation.objects.get(circle=circle, membership__identity=occupant)
        self.assertEqual(seat.seat_id, seat_name)
        work_id = c.assigned_activity(founder, circle, policy["spec"]["proposal"], worker=occupant, board_place=seat)
        work = models.ServiceActivity.objects.get(activity_id=work_id)
        target = {"seat": seat_name}
        def work_decision(act, **fields):
            return c.decide_instrument(self, board, committees.attachment(organism_uuid=lo.organism_id,
                        act=act, target=target, activity=str(work_id), **fields), people)["implementation"]
        bound = work_decision("BIND_BOARD_WORK")
        seated = work_decision("REASSIGN_BOARD_WORK", predecessor=str(bound), assignee_target=target,
                               occupancy=str(seat.participation_uuid))

        def custody():
            with connection.cursor() as cursor:
                cursor.execute("""SELECT receipt,predecessor,holder,office,place,effective_at FROM public.core_cmtcustody
                    WHERE activity=%s ORDER BY effective_at,recorded_at""", [work.pk])
                return cursor.fetchall()

        prior = custody()
        self.assertEqual([(row[0], row[1], row[2], row[3], row[4]) for row in prior],
                         [(bound, None, None, None, None), (seated, bound, occupant.pk, None, seat.pk)])
        notice = elections.resign_board_place(**(general | {"credential": occupant.credential}), **article,
                         participation_uuid=seat.participation_uuid, provision=provision, request_uuid=uuid4())
        ending = models.CircleParticipationEnd.objects.get(participation=seat)
        self.assertLess(ending.received_at, due)
        self.assertEqual((ending.effective_at, ending.composed_from_event_uuid), (due, notice["act"]))
        self.assertEqual(custody(), prior)
        self.assertEqual(elections.vacancies(**general, ending_uuid=seat.participation_uuid), [])

        clock.advance_to(due + timedelta(microseconds=1))
        fx.idle_until_turn_of(lo, founder, "ELECT")
        wake = timed._wake(lo, founder)
        first = te.run(wake, alias="default")
        self.assertEqual((first.result, first.purpose, first.source_ref), ("OPENED", "RESIGNATION", str(notice["act"])))
        assessment = models.CoverageAssessment.objects.get(case__living_organism=lo,
                                                         evidence_reference="elect-resignation:" + str(notice["act"]))
        self.assertEqual(assessment.effective_at, due)
        self.assertEqual(custody(), prior)
        fx.idle_until_turn_of(lo, founder, "ELECT")

        def state():
            return (one("SELECT count(*) FROM public.core_electvacancy WHERE ending=%s", [seat.participation_uuid]),
                    one("SELECT count(*) FROM public.core_tereceipt WHERE organism_id=%s", [lo.pk]),
                    one("SELECT probes,opened,failed FROM public.core_tepace WHERE organism_id=%s AND consumer='ELECT'",
                        [lo.pk]), custody())
        before = state()
        self.assertEqual(before[0], (0,))
        held, release = threading.Event(), threading.Event()
        def hold_activity():
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute("SELECT id FROM public.core_serviceactivity WHERE id=%s FOR UPDATE", [work.pk])
                    if cursor.fetchone() == (work.pk,):
                        held.set()
                    release.wait(timed.WAIT)
            finally:
                connections.close_all()

        peer = threading.Thread(target=hold_activity)
        peer.start()
        try:
            self.assertTrue(held.wait(timed.WAIT), "The peer must hold the actual work row before the engine tries.")
            busy = te.run(wake, alias="default")
            self.assertTrue(peer.is_alive(), "The engine must return before the peer releases the work row.")
            self.assertEqual((busy.result, busy.consumer), ("BUSY_INNER", None))
            self.assertEqual(state(), before)
        finally:
            release.set()
            peer.join(timed.WAIT)
        self.assertFalse(peer.is_alive())
        opened = te.run(wake, alias="default")
        self.assertEqual((opened.result, opened.purpose, opened.source_ref),
                         ("OPENED", "VACANCY", str(seat.participation_uuid)))
        vacancy, = elections.vacancies(**general, ending_uuid=seat.participation_uuid)
        instruction = vacancy["instruction"]
        self.assertEqual((instruction["assessment"], instruction["route"], instruction["provision"]),
                         (str(assessment.event_uuid), "WAIT", vacancy_rule))
        after = custody()
        self.assertEqual(after[:-1], prior)
        self.assertEqual(after[-1][1:], (seated, None, None, None, due),
                         "The vacancy's own commit must add exactly the held-for-Board successor at the seat end.")
        self.assertEqual(one("""SELECT count(*) FROM public.core_tereceipt WHERE organism_id=%s AND purpose='VACANCY'
            AND source_ref=%s AND due_at=%s AND discovered_at>due_at""", [lo.pk, str(seat.participation_uuid), due]), (1,))
        fx.idle_until_turn_of(lo, founder, "ELECT")
        self.assertEqual(te.run(wake, alias="default").result, "IDLE")
        self.assertEqual(custody(), after)

    def test_last_lo_coordinator_term_opens_real_coverage(self):
        """W17. The waking member carries a missed fixed term; founding authority supplies the actual notice."""
        start = support.database_now()
        due = start + timedelta(days=5)  # C2-06's fixed-term fixture interval.
        lo, founder, member = coverage._owing(start, due)
        source, = one("SELECT source FROM public.c2_timed_input WHERE lo_id=%s AND due=%s AND source LIKE 'office-term:%%'",
                      [lo.pk, due])
        assignment, = one("""SELECT a.id FROM public.core_contextualroleassignmentstateevent e
            JOIN public.core_contextualroleassignment a ON a.id=e.assignment_id WHERE 'office-term:'||e.event_uuid=%s""", [source])
        def holders(at, known):
            return one("""SELECT count(*) FROM public.c2_occupants(%s,%s,%s)
                WHERE role_scope='LIVING_ORGANISM' AND role_code='COORDINATOR'""", [lo.pk, at, known])[0]
        now = support.database_now()
        self.assertEqual(holders(now, now), 1)
        self.assertEqual(one("SELECT count(*) FROM public.te_c2_notice_due(%s,%s) WHERE source_ref=%s",
                             [lo.pk, now, source]), (0,))
        clock.advance_to(due + timedelta(microseconds=1))
        now = support.database_now()
        self.assertEqual((holders(due - timedelta(microseconds=1), now), holders(due, now)), (1, 0))
        self.assertEqual(one("SELECT count(*) FROM public.elect_due_office(%s,%s)", [assignment, now]), (0,))
        self.assertEqual(one("SELECT source_ref,due_at FROM public.te_c2_notice_due(%s,%s)", [lo.pk, now]), (source, due))
        fx.idle_until_turn_of(lo, member, "C2")
        opened = te.run(timed._wake(lo, member), alias="default")
        self.assertEqual((opened.result, opened.consumer, opened.purpose, opened.source_ref), ("OPENED", "C2", "NOTICE", source))
        evidence = "c2-boundary:" + source
        finding = models.CoverageAssessment.objects.get(case__living_organism=lo, evidence_reference=evidence)
        losses = models.LivingOrganismEvent.objects.filter(living_organism=lo, action="WITHOUT_COORDINATOR",
                                                         evidence_reference=evidence)
        self.assertEqual(losses.count(), 1, "The physical last term must actually start restricted continuity.")
        transition = losses.get()
        restricted = models.RestrictedContinuityEvent.objects.get(triggering_assessment=finding)
        self.assertEqual((restricted.resulting_state, restricted.effective_at, restricted.case.living_organism_id),
                         ("RESTRICTED", due, lo.pk))
        founding = models.LivingOrganismEvent.objects.get(living_organism=lo, sequence=1)
        self.assertEqual((finding.result, finding.effective_at, finding.actor_id, finding.authority_basis_id),
                         ("UNSATISFIED", due, None, founding.authority_basis_id))
        self.assertEqual((transition.resulting_state, transition.effective_at, transition.actor_id),
                         ("RESTRICTED_CONTINUITY", due, None))
        # The C2 writer copies the founding enterer. These storage columns are
        # distinct from the nullable actor exposed by the chain-event models.
        self.assertEqual(one("""SELECT f.enterer_identity_id, a.enterer_identity_id, e.enterer_identity_id
            FROM public.core_livingorganismevent f, public.core_coverageassessment a, public.core_livingorganismevent e
            WHERE f.id=%s AND a.id=%s AND e.id=%s""", [founding.pk, finding.pk, transition.pk]),
            (founder, founder, founder))
        self.assertGreater(transition.recorded_at, due)
        self.assertNotEqual(member.pk, founder)
        self.assertEqual(one("""SELECT due_at,effect_at,enterer_id,effect_ref,discovered_at>due_at
            FROM public.core_tereceipt WHERE organism_id=%s AND purpose='NOTICE' AND source_ref=%s""", [lo.pk, source]),
            (due, finding.recorded_at, founder, str(finding.event_uuid), True))
        self.assertEqual(one("SELECT count(*) FROM public.te_c2_notice_due(%s,%s) WHERE source_ref=%s",
                             [lo.pk, support.database_now(), source]), (0,))

    def test_roster_change_reforecasts_the_later_lo_term_before_elect_ends_the_treasury(self):
        """W17 forecast. The roster change revises the future cut. ELECT ends the Treasurer and C2 notices each term."""
        rules = {fx.ARTICLE: fx.seat(), "Treasurer": {"type": "OFFICE", "place": "TREASURER", "body": "LO_MEMBERSHIP"}}
        lo, founder, _, _, citation = fx.owner(rules=rules, voting=False)
        treasurer, second_coordinator = law.add_member(lo), law.add_member(lo)
        helper = office_witness.SROfficeActionTests()
        helper.actor, helper.organism = founder, lo
        authority = offices.authority()
        definer = definitions.OrganismRoleDefinitionService(authority=authority, clock=support.database_now)
        defined = definer.record_role_definition(helper._command(content=definitions.DefinitionContent(
            code="TREASURER", provisions=(definitions.DefinitionProvision(
                "OFFICE_BASIS", "Treasurer", str(citation["version_id"])),))))
        treasury = models.OrganismRoleDefinition.objects.get(role_uuid=defined.role_definition_uuid)
        coordinator = models.ContextualRoleAssignment.objects.get(membership__identity=founder,
            membership__living_organism=lo, role_definition__code="COORDINATOR", circle__isnull=True).role_definition
        roles = OrganismRoleService(authority=authority, clock=support.database_now)
        starts = support.database_now()
        term_due, later = starts + timedelta(days=1), starts + timedelta(days=5)
        def assign(role, member, until):
            proposal = fx.office_proposal(lo, founder, member, role, self_proposed=False)
            command = helper._transition_command(proposal, "ASSIGN")
            roles.transition_assignment(replace(command, effective_until_state="FIXED", effective_until=until))
            return models.ContextualRoleAssignmentStateEvent.objects.get(assignment=proposal, action="ASSIGN")

        first = assign(treasury, treasurer, term_due)
        second = assign(coordinator, second_coordinator, later)
        source = "office-term:" + str(second.event_uuid)
        forecasts = models.CoverageAssessment.objects.filter(case__living_organism=lo, effective_at=later,
                    evidence_reference="c2-forecast:" + source).order_by("sequence")
        # The founder remains in office beyond the second Coordinator's term. Under the original roster
        # the first forecast at that future cut is correct. The real roster row added next changes it.
        self.assertEqual(list(forecasts.values_list("result", flat=True)), ["SATISFIED"])
        models.EssentialCoverageRequirement.objects.create(living_organism=lo, roster_version=1,
            responsibility_domain_code="TREASURY", qualifying_role_code="TREASURER", scope=treasury.scope,
            minimum_active_occupants=1, effective_from=support.database_now())
        self.assertEqual(one("SELECT public.c2_result(%s,1,%s,%s)", [lo.pk, later, support.database_now()]),
                         ("UNSATISFIED",))
        self.assertEqual(list(forecasts.values_list("result", flat=True)), ["SATISFIED", "UNSATISFIED"],
                         "The roster write must revise the same future cut before any wake or clock move.")
        forecast_ids = list(forecasts.values_list("pk", flat=True))
        updated = forecasts.get(pk=forecast_ids[-1])
        founding = models.LivingOrganismEvent.objects.get(living_organism=lo, sequence=1)
        self.assertEqual(one("""SELECT f.enterer_identity_id, a.enterer_identity_id FROM public.core_livingorganismevent f,
            public.core_coverageassessment a WHERE f.id=%s AND a.id=%s""", [founding.pk, updated.pk]),
            (founder.pk, founder.pk))
        self.assertEqual((updated.authority_basis_id, updated.authority_decision_reference),
                         (founding.authority_basis_id, founding.authority_decision_reference))
        self.assertLess(updated.recorded_at, term_due)
        fx.idle_until_turn_of(lo, founder, "ELECT")
        clock.advance_to(term_due + timedelta(microseconds=1))
        wake = timed._wake(lo, founder)
        ended = te.run(wake, alias="default")
        self.assertEqual((ended.result, ended.purpose), ("OPENED", "TERM_END"))
        self.assertTrue(models.ContextualRoleAssignmentStateEvent.objects.filter(assignment_id=first.assignment_id,
                                 action="END", effective_at=term_due).exists())
        self.assertEqual(list(forecasts.values_list("pk", flat=True)), forecast_ids,
                         "The END must preserve the roster's forecast without publishing it twice.")
        notices = models.CoverageAssessment.objects.filter(case__living_organism=lo, evidence_reference="c2-boundary:" + source)
        self.assertFalse(notices.exists())
        # Consume the Treasurer's due C2 notice first, one item per wake.
        fx.idle_until_turn_of(lo, founder, "C2")
        earlier = te.run(wake, alias="default")
        self.assertEqual((earlier.result, earlier.purpose, earlier.source_ref),
                         ("OPENED", "NOTICE", "office-term:" + str(first.event_uuid)))
        fx.idle_until_turn_of(lo, founder, "C2")
        clock.advance_to(later + timedelta(microseconds=1))
        actual = te.run(wake, alias="default")
        self.assertEqual((actual.result, actual.purpose, actual.source_ref), ("OPENED", "NOTICE", source))
        self.assertEqual(list(notices.values_list("effective_at", "result")), [(later, "UNSATISFIED")])
