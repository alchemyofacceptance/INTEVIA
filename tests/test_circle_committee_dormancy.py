"""C1-33. The committee's authority and custody at its own or its parent's deadline."""

from datetime import timedelta
from uuid import uuid4

from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase

from core import models
from intevia.test_postgresql_backend import clock
from src.intevia.services import circle_commands as circles, committees, cq, discussion as disc
from src.intevia.services import service_activity_service as activities
from tests import committee_support as c, s015_0021_support as support
from tests import test_election_mandates as mandates


def one(sql, parameters):
    with connection.cursor() as cursor:
        cursor.execute(sql, parameters)
        return cursor.fetchone()


def live(binding, cut):
    return one("SELECT public.cmt_live(%s,%s,%s),public.cmt_live_apart(%s,%s,%s)",
               [binding, cut, cut, binding, cut, cut])


class CircleCommitteeDormancyTests(TransactionTestCase):
    topic = mandates.ElectionMandateTests.topic
    activate_board = mandates.ElectionMandateTests.activate_board
    committee = mandates.ElectionMandateTests.committee

    def setUp(self):
        super().setUp()
        self.lo, self.general, self.board, self.voters = c.formation_board(self, offering="COMPLIMENTARY_BETA")
        self.founder, self.member, _ = self.voters

    def form_mandate(self, *, parent=None):
        circle = self.committee(self.general)
        for voter in self.voters[1:]:
            c.join(self.general, circle, voter)
        terms = {"committee_work": {"return_to": "CHAIR"}}
        where = self.board
        fields = {}
        if parent is None:
            terms["form_children"] = True
        else:
            where = self.general | {"circle_uuid": parent.circle_uuid}
            fields = {"kind": "SUB_COMMITTEE", "parent": str(parent.circle_uuid)}
        forming, _ = c.mandate(self.lo, self.founder, circle, "2" if parent is None else "3",
                               terms=terms, publisher=c.first_version, **fields)
        receipt = c.decide_instrument(self, where, forming, self.voters)
        if parent is not None:
            self.assertIsNotNone(receipt["plan"]["committee"]["delegating_parent"])
        return circle, forming["spec"]["proposal"]

    def lose_chair(self, circle):
        place = models.CircleParticipation.objects.get(circle=circle, membership__identity=self.founder)
        circles.leave(**self.general, circle_uuid=circle.circle_uuid, participation_uuid=place.participation_uuid,
                      request_uuid=uuid4(), confirmed=True)
        return models.ContextualRoleAssignmentStateEvent.objects.get(
            assignment__circle=circle, assignment__membership=place.membership, action="END")

    def move_past(self, loss):
        due = loss.effective_at + timedelta(hours=720)
        clock.advance_to(due + timedelta(microseconds=1))
        self.assertGreater(support.database_now(), due)
        return due

    def record_dormancy(self, loss):
        for occurrence in ("OPENED", "DORMANT"):
            self.assertEqual(one("SELECT public.circle_record_vacancy(%s,%s,%s,%s)",
                                 [self.lo.pk, loss.event_uuid, occurrence, self.member.pk]), (True,))

    def mandate_boundary(self, parent_expires):
        parent, parent_binding = self.form_mandate()
        child, child_binding = self.form_mandate(parent=parent)
        target = parent if parent_expires else child
        issuer = self.general | {"circle_uuid": child.circle_uuid, "credential": self.member.credential}
        report = c.step(issuer, child_binding, "REPORT", area="reserves", finding="Reviewed.")
        # Prepare the suggestions as the same continuing participant before any loss.
        attempts = [self.topic(issuer, markdown=report["text"]) for _ in range(3)]
        def propose(index):
            journey, _, post = attempts[index]
            return cq.put_forward(**journey, request_uuid=uuid4(), version_uuid=post["version"], attachment=report)

        loss = self.lose_chair(target)
        self.assertEqual(live(child_binding, support.database_now()), (True, True))
        propose(0)
        self.move_past(loss)
        for index in (1, 2):
            if index == 2:
                self.record_dormancy(loss)
            at = support.database_now()
            self.assertEqual(live(child_binding, at), (False, not parent_expires),
                             "Only the child's own vacancy may be put aside for its chair.")
            self.assertEqual(live(parent_binding, at), (not parent_expires, True))
            with self.assertRaises(cq.CQRefused) as refused:
                propose(index)
            self.assertEqual(refused.exception.code, "CMT_ENDED")
            self.assertEqual(one("SELECT count(*) FROM public.core_cqact WHERE thread_uuid=%s AND operation='INITIATE'",
                                 [attempts[index][0]["thread_uuid"]]), (0,))

    def test_child_expiry_stops_only_its_own_mandate(self):
        """W7 child. Its parent remains active while the child loses its power to report."""
        self.mandate_boundary(False)

    def test_parent_expiry_vetoes_an_active_child(self):
        """W7 ancestor. Both ordinary work and chair authority lose the delegating parent."""
        self.mandate_boundary(True)

    def renew_board_issuer(self):
        from src.intevia.services.organism_coverage import office_scope
        from tests.root_genesis_support import external_finding

        # Renew the Board holder's expired finding for the same appointment.
        # The vacant committee receives neither a finding nor an appointment.
        board = models.Circle.objects.get(circle_uuid=self.board["circle_uuid"])
        appointment = models.ContextualRoleAssignmentStateEvent.objects.get(
            assignment__circle=board, assignment__membership__identity=self.member,
            assignment__role_definition__code="COORDINATOR", action="ASSIGN")
        external_finding(office_scope(self.lo, appointment.assignment, appointment.authority_basis))
        late = support.database_now()
        self.assertEqual(one("SELECT count(*) FROM public.circle_holders(%s,%s,%s) WHERE person=%s",
                             [board.pk, late, late, self.member.pk]), (1,))

    def appoint_board_nominee(self, circle, binding, key, decision, loss):
        from src.intevia.services.organism_office_consent_service import OfficeAppointmentConsentService
        from src.intevia.services.organism_role_service import OrganismRoleService
        from tests import cq_authority_support as support_board, test_sr_office_actions as offices

        roles = OrganismRoleService(authority=support_board.authority(), clock=support.database_now)
        place = models.CircleParticipation.objects.get(circle=circle, membership__identity=self.member)
        scope = self.general | {"circle_uuid": circle.circle_uuid}
        roles.nominate_circle_coordinator(**scope, participation_uuid=place.participation_uuid,
                                          assignment_uuid=key, request_uuid=uuid4())
        nomination = models.ContextualRoleAssignment.objects.get(assignment_uuid=key)
        OfficeAppointmentConsentService(authority=support_board.authority(), clock=support.database_now).record_office_consent(
            offices.SROfficeActionTests()._consent_command(place.membership, nomination))
        history = models.CircleStateEvent.objects.filter(circle=circle, action="RESEAT")
        self.assertFalse(history.exists())
        cut = support.database_now()
        self.assertEqual(live(binding, cut), (False, True))
        request = uuid4()
        roles.appoint_circle_coordinator(**scope, assignment_uuid=key, request_uuid=request)
        assigned = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=nomination, action="ASSIGN")
        self.assertEqual(assigned.evidence_reference.split("|")[1], str(decision["implementation"]))
        self.assertTrue(assigned.evidence_reference.endswith("|CMT"))
        self.assertEqual(list(history.values_list("event_uuid", "evidence_reference", "effective_at")),
                         [(request, f"vacancy:{loss.event_uuid}|assign:{request}", assigned.effective_at)],
                         "A vote alone leaves the Circle dormant; its named ASSIGN brings it to ACTIVE once.")
        self.assertEqual(roles.appoint_circle_coordinator(**scope, assignment_uuid=key, request_uuid=request), request)
        self.assertEqual(history.count(), 1)
        circle.refresh_from_db()
        cut = support.database_now()
        self.assertEqual((circle.state, live(binding, cut)), ("ACTIVE", (True, True)))
        self.assertEqual(one("SELECT count(*) FROM public.circle_holders(%s,%s,%s) WHERE person=%s",
                             [circle.pk, cut, cut, self.member.pk]), (1,))
        return assigned

    def test_board_can_fill_the_reserved_chair_while_the_mandate_has_no_power(self):
        """W8. The Board's precise FILL_CHAIR decision supplies authority before ASSIGN."""
        circle, binding = self.form_mandate()
        loss = self.lose_chair(circle)
        self.move_past(loss)
        self.record_dormancy(loss)
        self.renew_board_issuer()
        late = support.database_now()
        self.assertEqual(live(binding, late), (False, True))
        # W3's reserved-chair control uses the genuine parent LO office and the
        # former holder's missing place. Only the Board may supply this appointment.
        from tests.test_circle_vacancy_episode import recovery_place
        parent_office = models.ContextualRoleAssignment.objects.get(
            membership__identity=self.founder, membership__living_organism=self.lo,
            circle__isnull=True, role_definition__code="COORDINATOR", state="ACTIVE")
        def parent_authority():
            cut = support.database_now()
            return one("SELECT public.circle_lo_office(%s,%s,%s,%s)", [self.lo.pk, self.founder.pk, cut, cut])[0]
        if parent_authority() is None:
            from src.intevia.services.organism_coverage import office_scope
            from tests.root_genesis_support import external_finding
            appoint = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=parent_office, action="ASSIGN")
            external_finding(office_scope(self.lo, parent_office, appoint.authority_basis))
        self.assertEqual(parent_authority(), parent_office.assignment_uuid)
        scope = self.general | {"circle_uuid": circle.circle_uuid}
        with self.assertRaisesMessage(circles.CircleAccessRefused, cq.MESSAGES["CMT_CHAIR_GAP"]):
            circles.recover_circle(**scope, request_uuid=uuid4())
        with self.assertRaisesMessage(DatabaseError, "CIRCLE_RESEAT_PLACE_SOURCE"), transaction.atomic():
            recovery_place(self, circle, parent_office.membership, parent_office)
            transaction.set_rollback(True)
        future = uuid4()
        attachment = c.step(self.board, binding, "FILL_CHAIR", person=str(self.member.identity_id),
                            assignment=str(future))
        try:
            decision = c.decide_instrument(self, self.board, attachment, self.voters)
        except cq.CQRefused as refusal:
            if refusal.code != "CMT_ENDED":
                raise
            self.fail("The Board must be able to fill the reserved chair of this otherwise-live mandate.")
        cut = support.database_now()
        self.assertEqual(one("SELECT public.cmt_appointment(%s,%s,%s,%s,%s,%s),"
                             "public.cmt_appointment(%s,%s,%s,%s,%s,%s)",
                             [circle.pk, self.founder.pk, self.member.pk, future, cut, cut,
                              circle.pk, self.founder.pk, self.member.pk, uuid4(), cut, cut]),
                         (decision["implementation"], None))
        self.assertFalse(models.ContextualRoleAssignment.objects.filter(assignment_uuid=future).exists())
        self.assertFalse(models.CircleStateEvent.objects.filter(circle=circle, action="RESEAT").exists())
        self.assertEqual(live(binding, cut), (False, True))
        self.appoint_board_nominee(circle, binding, future, decision, loss)

    def custody_after_expiry(self, in_progress):
        parent, parent_binding = self.form_mandate()
        child, binding = self.form_mandate(parent=parent)
        local = self.general | {"circle_uuid": child.circle_uuid}
        identifier, service = c.assigned_activity(self.founder, child, binding, with_service=True)
        decision = c.decide_instrument(self, local, c.step(local, binding, "BIND_WORK", activity=str(identifier)),
                                       self.voters)
        activity = models.ServiceActivity.objects.get(activity_id=identifier)
        assignment = models.ServiceActivityAssignment.objects.get(activity=activity)

        def command():
            return dict(credential=self.founder.credential, request_reference=uuid4().hex,
                        idempotency_key=uuid4().hex, occurred_at=support.database_now(), activity_id=identifier)

        if in_progress:
            service.accept_service_assignment(activities.AcceptServiceAssignmentCommand(**command()))
        loss = self.lose_chair(parent)
        before = support.database_now()
        self.assertEqual(committees.work_holder(activity, assignment, "default", before), self.founder.pk)
        due = self.move_past(loss)
        self.assertIsNone(committees.work_holder(activity, assignment, "default"))
        self.assertEqual(one("SELECT count(*) FROM public.core_cmtcustody WHERE predecessor=%s",
                             [decision["implementation"]]), (0,))
        self.record_dormancy(loss)
        for mandate in (parent_binding, binding):
            self.assertEqual(disc._rows("SELECT effective_at FROM public.core_cmtcomposition "
                                        "WHERE mandate=%s AND requirement='MANDATE' AND unmet", [mandate]),
                             [{"effective_at": due}], "The mandate gap must be recorded at the vacancy deadline.")
        self.assertEqual(disc._rows("SELECT effective_at,holder,office FROM public.core_cmtcustody "
                                    "WHERE activity=%s AND predecessor=%s", [activity.pk, decision["implementation"]]),
                         [{"effective_at": due, "holder": None, "office": None}])
        self.assertEqual(one("SELECT count(*) FROM public.cmt_refresh_batch", []), (0,))
        self.assertEqual(committees.work_holder(activity, assignment, "default", before), self.founder.pk)
        if in_progress:
            commands = [(service.submit_service_work, activities.SubmitServiceWorkCommand(
                **command(), submission_reference="review", submission_support_references=()))]
        else:
            commands = [(service.accept_service_assignment, activities.AcceptServiceAssignmentCommand(**command())),
                        (service.decline_service_assignment, activities.DeclineServiceAssignmentCommand(
                            **command(), decline_basis_reference="review"))]
        for operation, value in commands:
            with self.subTest(operation=operation.__name__), self.assertRaisesMessage(
                    activities.ServiceActivityActorError, cq.MESSAGES["CMT_WORK"]):
                operation(value)

        if not in_progress:
            # The historical custody hand-over was to an empty office. Restoring
            # the mandate clears its gap, but cannot confer custody without a new act.
            self.renew_board_issuer()
            key = uuid4()
            fill = c.decide_instrument(self, self.board, c.step(
                self.board, parent_binding, "FILL_CHAIR", person=str(self.member.identity_id),
                assignment=str(key)), self.voters)
            others = models.Circle.objects.filter(parent_organism=self.lo).exclude(pk__in=(parent.pk, child.pk))
            unaffected = list(others.order_by("pk").values_list("pk", "head_event_id"))
            self.assertTrue(unaffected)
            handed = one("SELECT receipt FROM public.core_cmtcustody WHERE predecessor=%s",
                         [decision["implementation"]])[0]
            assigned = self.appoint_board_nominee(parent, parent_binding, key, fill, loss)
            for mandate in (parent_binding, binding):
                self.assertEqual(disc._rows("""SELECT effective_at,unmet FROM public.core_cmtcomposition
                    WHERE mandate=%s AND requirement='MANDATE' AND effective_at>=%s ORDER BY effective_at""",
                                            [mandate, due]),
                                 [{"effective_at": due, "unmet": True},
                                  {"effective_at": assigned.effective_at, "unmet": False}],
                                 "The parent's reseat must close the child's mandate gap at the actual ASSIGN.")
            self.assertEqual(one("SELECT count(*) FROM public.core_cmtcustody WHERE predecessor=%s", [handed]), (0,))
            self.assertIsNone(committees.work_holder(activity, assignment, "default"))
            self.assertEqual(committees.work_holder(activity, assignment, "default", before), self.founder.pk)
            self.assertEqual(one("SELECT count(*) FROM public.cmt_refresh_batch", []), (0,))
            self.assertEqual(list(others.order_by("pk").values_list("pk", "head_event_id")), unaffected)

    def test_deadline_returns_assigned_work_and_refuses_its_former_worker(self):
        """W9 assigned. A surviving child office loses custody when its parent becomes dormant."""
        self.custody_after_expiry(False)

    def test_deadline_returns_work_in_progress_and_refuses_submission(self):
        """W9 in progress. A genuine accepted assignment cannot be submitted by the former holder."""
        self.custody_after_expiry(True)
