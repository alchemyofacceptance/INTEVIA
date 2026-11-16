"""D-2. The child chair stays reserved until its delegating parent is reseated."""
import json
from datetime import timedelta
from uuid import uuid4, uuid5

from django.db import DatabaseError, transaction
from django.test import TransactionTestCase

from core import models
from intevia.test_postgresql_backend import clock
from src.intevia.services import circle_commands as circles, cq
from tests import committee_support as c, s015_0021_support as support
from tests import test_circle_committee_dormancy as committee
from tests import test_circle_vacancy_episode as vacancy

PARENT = ("The sub-committee chair could not be filled. "
    "This place is reserved while the committee that delegated the work is dormant. "
    "The chair is still empty. "
    "Ask the Board to check the forming law and arrange for the dormant committee to be reseated first. "
    "After reseating, the Board or the body named in the sub-committee's forming law handles the appointment.")
STATUS = ("This committee’s Circle is dormant. "
    "Its recovery period ended at {when} without a chair. "
    "It cannot currently exercise its mandate. "
    "The body named in the forming law appoints the chair, and if that body’s own Circle is dormant it must be reseated first. "
    "To tell the Board, review and send this notice: [LINK PLACEHOLDER: Send to the Board].")
CLAIM = ("You could not claim this work. "
    "The committee lost its chair and its recovery period ended at {when}. "
    "This attempt left the work assignment unchanged. "
    "The body named in the forming law appoints the chair, and if that body’s own Circle is dormant it must be reseated first. "
    "To tell the Board, review and send this notice: [LINK PLACEHOLDER: Send to the Board].")
LANDED_GAP = "The committee chair's place is empty. Ask the Board to fill it under the forming law."
LANDED_ENDED = {"code": "CMT_ENDED", "reason": "This committee’s mandate has ended. Refer new work to the Board."}
UNAVAILABLE = "This request cannot use the selected Circle or membership. Check the organisation and selection."


class CommitteeChairAuthorityTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.fx = committee.CircleCommitteeDormancyTests()
        self.fx.setUp()
        self.parent, self.parent_binding = self.fx.form_mandate()
        self.child, self.child_binding = self.fx.form_mandate(parent=self.parent)
        self.member = self.fx.general | {"circle_uuid": self.child.circle_uuid, "credential": self.fx.member.credential}
        self.issuer = self.fx.general | {"circle_uuid": self.child.circle_uuid}

    def fill(self, binding, key):
        return c.step(self.fx.board, binding, "FILL_CHAIR", person=str(self.fx.member.identity_id), assignment=str(key))

    def plan(self, board_circle, attachment):
        result, = committee.one(
            """SELECT public.cmt_work_plan(organism_id,board_uuid,%s::jsonb,%s)::text FROM public.core_discboard
               WHERE circle_id=%s AND organism_id=%s""",
            [json.dumps(attachment), support.database_now(), board_circle.pk, self.fx.lo.pk])
        return json.loads(result)

    def office(self):
        from src.intevia.services.organism_coverage import office_scope
        from tests.root_genesis_support import external_finding

        holder = self.fx.founder
        office = models.ContextualRoleAssignment.objects.get(membership__identity=holder,
            membership__living_organism=self.fx.lo, circle__isnull=True, role_definition__code="COORDINATOR", state="ACTIVE")
        def current():
            cut = support.database_now()
            return committee.one("SELECT public.circle_lo_office(%s,%s,%s,%s)", [self.fx.lo.pk, holder.pk, cut, cut])[0]
        if current() is None:
            assigned = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=office, action="ASSIGN")
            external_finding(office_scope(self.fx.lo, office, assigned.authority_basis))
        self.assertEqual(current(), office.assignment_uuid, "The founder must hold the effective LO office at this cut.")
        return office

    def counts(self):
        return (models.CircleParticipation.objects.filter(circle=self.child).count(),
                models.ContextualRoleAssignment.objects.filter(circle=self.child).count(),
                models.ContextualRoleAssignmentStateEvent.objects.filter(assignment__circle=self.child, action="ASSIGN").count(),
                models.CircleStateEvent.objects.filter(circle=self.child, action="RESEAT").count())

    def denied_recovery(self, scope, request, message):
        with self.assertRaises(circles.CircleAccessRefused, msg="The recovery request must leave the chair empty.") as result:
            circles.recover_circle(**scope, request_uuid=request)
        self.assertEqual(str(result.exception), message, "The caller must get the refusal specific to their authority.")

    def test_child_chair_is_reserved_across_both_deadlines(self):
        """D2. Neither generic recovery nor a Board FILL bypasses a dormant delegating parent."""
        board = models.Circle.objects.get(circle_uuid=self.fx.board["circle_uuid"])
        fill = self.fill(self.child_binding, uuid4())
        attempts = [self.fx.topic(self.fx.board, markdown=fill["text"]) for _ in range(2)]
        outsider = support.register_test_learner(username="outsider-" + uuid4().hex)
        parent_loss = self.fx.lose_chair(self.parent)
        clock.advance_to(parent_loss.effective_at + timedelta(hours=100))
        child_loss = self.fx.lose_chair(self.child)
        due = child_loss.effective_at + timedelta(hours=720)
        self.fx.move_past(parent_loss)
        cut = support.database_now()
        self.assertLess(cut, due, "The child must still have time to recover at the first cut.")
        self.assertEqual(committee.one("SELECT public.circle_effective_state(%s,%s,%s)", [self.parent.pk, cut, cut]),
                         ("DORMANT",), "The delegating parent must already read dormant.")
        self.assertFalse(models.CircleStateEvent.objects.filter(circle=self.parent, action="BECOME_DORMANT").exists(),
                         "The first refusal must precede the parent's recorded dormancy.")
        self.assertEqual(committee.one("SELECT status FROM public.circle_episode(%s,%s,%s)", [self.child.pk, cut, cut]),
                         ("OPEN",), "The child's separate recovery episode must still be open.")
        self.assertEqual(committee.live(self.child_binding, cut), (False, False),
                         "The dormant ancestor must block ordinary work and chair authority.")
        self.assertEqual(committee.one("SELECT count(*) FROM public.circle_holders(%s,%s,%s)", [self.child.pk, cut, cut]),
                         (0,), "The reserved chair must actually be empty.")
        member_place = models.OrganismMembership.objects.get(living_organism=self.fx.lo, identity=self.fx.member)
        self.assertEqual(committee.one("SELECT public.circle_place_at(%s,%s,%s,%s), "
                         "public.circle_lo_office(%s,%s,%s,%s) IS NULL, public.circle_direct_volunteer(%s,%s,%s)",
                         [self.child.pk, member_place.pk, cut, cut, self.fx.lo.pk, self.fx.member.pk, cut, cut,
                          self.fx.lo.pk, cut, cut]), (True, True, False),
                         "The first caller must be a continuing child member without LO office or direct-volunteer authority.")
        self.assertEqual(committee.one("SELECT mandate->>'delegating_parent' FROM public.cmt_mandates WHERE binding=%s",
                                       [self.child_binding]), (str(self.parent_binding),),
                         "The child's authority must come from the exact parent binding that lost its chair.")
        before = self.counts()
        request = uuid4()
        with self.subTest(route="member at the first cut"):
            self.denied_recovery(self.member, request, PARENT)
        with self.subTest(route="storage reservation"):
            self.assertEqual(committee.one("SELECT public.cmt_empty(%s,%s,%s)", [self.child.pk, cut, cut]), (True,),
                             "The child's empty chair must remain reserved before its own deadline.")
        with self.subTest(route="member's storage authority"):
            with self.assertRaises(DatabaseError, msg="Storage must refuse ordinary appointment to the reserved chair.") as refusal:
                with transaction.atomic():
                    committee.one("SELECT public.circle_appointing_office(%s,%s,%s,%s,%s,%s)",
                                  [self.child.pk, self.fx.member.pk, self.fx.member.pk,
                                   uuid5(request, "recover-circle-coordinator"), cut, cut])
            self.assertIn("CMT_CHAIR_GAP", str(refusal.exception), "Storage must identify the committee reservation.")
        office = self.office()
        cut = support.database_now()
        self.assertEqual(committee.one("SELECT public.circle_place_at(%s,%s,%s,%s)",
                         [self.child.pk, office.membership_id, cut, cut]), (False,),
                         "The LO Coordinator must have no current child place.")
        # The VC separately commits this same call on main. Here roll back a successful
        # call only, so the remainder can still examine the original chair on a broken tree.
        with self.subTest(route="LO Coordinator before child expiry"), transaction.atomic():
            try:
                self.denied_recovery(self.issuer, uuid4(), PARENT)
            except AssertionError:
                transaction.set_rollback(True)
                raise
        self.assertEqual(self.counts(), before, "The first-cut recovery trials must leave all child counts unchanged.")
        self.denied_recovery(self.member | {"credential": outsider.credential}, uuid4(), UNAVAILABLE)
        self.fx.renew_board_issuer()
        for index, (journey, _, post) in enumerate(attempts):
            if index:
                self.fx.record_dormancy(parent_loss)
            with self.subTest(visibility=("recorded" if index else "silent")):
                self.assertEqual(self.plan(board, fill), {"code": "CMT_PARENT_DORMANT", "reason": PARENT},
                                 "Only the competent body's refused fill receives the dormant-parent explanation.")
                self.assertEqual(self.plan(self.parent, fill), LANDED_ENDED,
                                 "Another body's board must retain the ordinary ended refusal.")
                with self.assertRaises(cq.CQRefused, msg="The public vote must refuse the child appointment.") as denied:
                    cq.put_forward(**journey, request_uuid=uuid4(), version_uuid=post["version"], attachment=fill)
                self.assertEqual((denied.exception.code, str(denied.exception)), ("CMT_PARENT_DORMANT", PARENT),
                                 "The public refusal must retain the approved five sentences.")
                self.assertEqual(committee.one("SELECT count(*) FROM public.core_cqact WHERE thread_uuid=%s AND operation='INITIATE'",
                                               [journey["thread_uuid"]]), (0,), "A refused vote must leave no initiation.")
                self.denied_recovery(self.member, uuid4(), PARENT)
        self.fx.move_past(child_loss)
        office = self.office()
        cut = support.database_now()
        # A5: this read, independently of both early public refusals, distinguishes the R2 substitution.
        with self.subTest(route="storage after child expiry"):
            self.assertEqual(committee.one("SELECT public.circle_reseatable(%s,NULL,%s,%s)", [self.child.pk, cut, cut]),
                             (False,), "A child with a reserved chair must be ineligible for ordinary LO reseating.")
        with self.subTest(route="LO Coordinator after child expiry"), transaction.atomic():
            try:
                self.denied_recovery(self.issuer, uuid4(), PARENT)
            except AssertionError:
                transaction.set_rollback(True)
                raise
        with self.subTest(route="isolated recovery place"):
            with self.assertRaises(DatabaseError, msg="Storage must refuse the exceptional recovery place.") as denied:
                with transaction.atomic():
                    vacancy.recovery_place(self.fx, self.child, office.membership, office)
                    transaction.set_rollback(True)
            self.assertIn("CIRCLE_RESEAT_PLACE_SOURCE", str(denied.exception), "The place must fail at its source guard.")
        self.assertEqual(self.counts(), before, "Refused acts must add no child place, office, assignment or reseat.")
        when = circles._deadline_text(due)
        with self.subTest(text="status"):
            self.assertEqual(circles.operating_status_detail(**self.member)["message"], STATUS.format(when=when),
                             "A participant must see the approved status with the child's actual deadline.")
        with self.subTest(text="work claim"):
            self.assertEqual(circles._expired_capacity_message(due, circles._chair_reserved("default", self.child, cut),
                             True, claim=True), CLAIM.format(when=when), "The reserved-chair claim must use the approved words.")
        self.denied_recovery(self.issuer | {"circle_uuid": self.parent.circle_uuid}, uuid4(), LANDED_GAP)
        # End the actual parent binding by the same Board retirement used in C-11.
        self.fx.renew_board_issuer()
        raw, = committee.one("""SELECT (request->'attachment')::text FROM public.core_cqact WHERE organism_id=%s
                       AND operation='INITIATE' AND request#>>'{attachment,spec,proposal}'=%s""",
                             [self.fx.lo.pk, str(self.parent_binding)])
        original = json.loads(raw)
        retired = original["spec"] | {"operation": "RETIRE", "intent": "RETIRE",
                                      "predecessor": str(self.parent_binding), "proposal": str(uuid4())}
        c.decide_instrument(self.fx, self.fx.board, original | {"spec": retired}, self.fx.voters)
        ended = support.database_now()
        self.assertEqual(committee.one("SELECT public.cmt_empty(%s,%s,%s)", [self.child.pk, ended, ended]),
                         (False,), "The retired parent instrument must cease reserving the child's chair.")
        self.assertEqual(self.plan(board, fill), LANDED_ENDED,
                         "A retired delegating instrument must lose its reservation and retain the ended refusal.")

    def test_child_chair_releases_only_after_lawful_parent_reseat(self):
        """D2-release. A parent's reseat permits a fresh child appointment but never fills the child."""
        blocked_key = uuid4()
        blocked_fill = self.fill(self.child_binding, blocked_key)
        journey, _, post = self.fx.topic(self.fx.board, markdown=blocked_fill["text"])
        parent_loss = self.fx.lose_chair(self.parent)
        child_loss = self.fx.lose_chair(self.child)
        self.fx.move_past(child_loss)
        self.fx.record_dormancy(parent_loss)
        self.fx.record_dormancy(child_loss)
        self.office()
        self.fx.renew_board_issuer()
        board = models.Circle.objects.get(circle_uuid=self.fx.board["circle_uuid"])
        plan = self.plan(board, blocked_fill)
        self.assertIn(plan.get("code"), ("CMT_ENDED", "CMT_PARENT_DORMANT"),
                      "Reservation alone must never let a Board fill a child under a dormant parent.")
        with self.assertRaises(cq.CQRefused, msg="A fresh child fill must be refused while the parent is dormant.") as denied:
            cq.put_forward(**journey, request_uuid=uuid4(), version_uuid=post["version"], attachment=blocked_fill)
        self.assertEqual(denied.exception.code, plan["code"], "The public and stored decisions must agree.")
        before = self.counts()
        parent_key = uuid4()
        parent_vote = c.decide_instrument(self.fx, self.fx.board, self.fill(self.parent_binding, parent_key), self.fx.voters)
        self.fx.appoint_board_nominee(self.parent, self.parent_binding, parent_key, parent_vote, parent_loss)
        cut = support.database_now()
        self.assertEqual(self.counts(), before, "Reseating the parent alone must add no child place or office event.")
        self.assertEqual(committee.live(self.child_binding, cut), (False, True),
                         "Only the child's own expiry may now be set aside for a governed appointment.")
        self.assertEqual(committee.one("SELECT count(*) FROM public.circle_holders(%s,%s,%s)", [self.child.pk, cut, cut]),
                         (0,), "The child must still have no holder after the parent's appointment.")
        self.assertEqual(committee.one("SELECT count(*) FROM public.core_cqact WHERE thread_uuid=%s AND operation='INITIATE'",
                                       [journey["thread_uuid"]]), (0,), "The parent's reseat must not revive the refused vote.")
        child_key = uuid4()
        child_vote = c.decide_instrument(self.fx, self.fx.board, self.fill(self.child_binding, child_key), self.fx.voters)
        assigned = self.fx.appoint_board_nominee(self.child, self.child_binding, child_key, child_vote, child_loss)
        self.assertEqual(assigned.assignment.assignment_uuid, child_key,
                         "Only the later, fresh child's appointment may fill its reserved chair.")
        self.assertFalse(models.ContextualRoleAssignment.objects.filter(assignment_uuid=blocked_key).exists(),
                         "No earlier blocked request may turn into a child appointment.")


class NamedCommitteeBodyTests(TransactionTestCase):
    """D-3. A live target cannot lend its capacity to the Circle named to decide its act."""

    landed_refusals = {
        "CMT_AUTH": "Put this act to the body named in the forming law.",
        "CMT_TASK": "Only the authority named in the terms can record completion of the stated task. Refer it there.",
    }

    def setUp(self):
        super().setUp()
        # Compose K's Board fixture without collecting K's methods or D2's delegated mandates.
        self.fx = committee.CircleCommitteeDormancyTests()
        self.fx.setUp()
        self.decider = self.fx.committee(self.fx.general)
        self.target = self.fx.committee(self.fx.general)
        for voter in self.fx.voters[1:]:
            c.join(self.fx.general, self.decider, voter)
            c.join(self.fx.general, self.target, voter)
        self.issuer = self.fx.general | {"circle_uuid": self.decider.circle_uuid,
                                        "credential": self.fx.member.credential}

    def test_named_appointing_circle_must_be_active(self):
        """D3-F. A named body can initiate the fill of a vacant chair only while effectively active."""
        assignment = uuid4()
        self.check_decision("FILL_CHAIR", {"appointing_body": str(self.decider.circle_uuid)},
                            {"person": str(self.fx.member.identity_id), "assignment": str(assignment)},
                            "CMT_AUTH", {"person_id": self.fx.member.pk})
        self.assertFalse(models.ContextualRoleAssignment.objects.filter(assignment_uuid=assignment).exists(),
                         "The active proposal and refused late proposals must leave the nominee without the proposed office.")

    def test_named_completion_circle_must_be_active(self):
        """D3-T. The named body can initiate completion of the outstanding task only while effectively active."""
        task = "reserve review"
        self.check_decision("COMPLETE_TASK", {"kind": "AD_HOC", "completion_body": str(self.decider.circle_uuid),
                                              "task": task},
                            {"task": task}, "CMT_TASK", {})

    def plan(self, board, attachment, cut):
        value, = committee.one("SELECT public.cmt_work_plan(%s,%s,%s::jsonb,%s)::text",
                               [self.fx.lo.pk, board, json.dumps(attachment), cut])
        return json.loads(value)

    def plan_refusal(self, board, attachment, cut):
        # The branch raises in storage. Preserve unrelated errors as errors, including fixture failures.
        try:
            with transaction.atomic():
                self.plan(board, attachment, cut)
        except DatabaseError as error:
            diag = getattr(error.__cause__, "diag", None)
            code = getattr(diag, "message_primary", None)
            if code not in ("CMT_AUTH", "CMT_TASK"):
                raise
            return code
        return None

    def check_target(self, binding, cut, fill, child_deadline):
        self.assertEqual(committee.one("SELECT public.circle_effective_state(%s,%s,%s)", [self.target.pk, cut, cut]),
                         ("ACTIVE",), "The target must still be active when the named body's authority is tested.")
        self.assertEqual(committee.live(binding, cut), (True, True),
                         "The target's current instrument and its delegation must still permit the act.")
        held, = committee.one("SELECT count(*) FROM public.circle_holders(%s,%s,%s)", [self.target.pk, cut, cut])
        if fill:
            self.assertLess(cut, child_deadline, "The target's vacant chair must still be inside its own recovery window.")
            self.assertEqual(held, 0, "The target must have a genuinely vacant chair to fill.")
            self.assertEqual(committee.one(
                """SELECT public.circle_place_at(%s,id,%s,%s),public.disc_member_state(%s,identity_id,%s)
                   FROM public.core_organismmembership WHERE living_organism_id=%s AND identity_id=%s""",
                [self.target.pk, cut, cut, self.fx.lo.pk, cut, self.fx.lo.pk, self.fx.member.pk]),
                (True, "ACTIVE"), "The named nominee must remain an eligible member with a target Circle place.")
        else:
            self.assertEqual(held, 1, "The target keeps its own chair throughout the completion trial.")
            self.assertEqual(committee.one(
                """SELECT count(*) FROM public.core_cqact WHERE organism_id=%s AND operation='IMPLEMENT'
                   AND facts#>>'{domain_plan,committee,completed}'=%s""", [self.fx.lo.pk, str(binding)]),
                (0,), "The target's task must remain outstanding after each attempt.")

    def check_decision(self, action, mandate_fields, act_fields, code, planned_effect):
        fill = action == "FILL_CHAIR"
        forming, _ = c.mandate(self.fx.lo, self.fx.founder, self.target, "2", publisher=c.first_version,
                               terms={"committee_work": {"return_to": "CHAIR"}}, **mandate_fields)
        c.decide_instrument(self.fx, self.fx.board, forming, self.fx.voters)
        binding = forming["spec"]["proposal"]
        stored, = committee.one("SELECT mandate::text FROM public.cmt_mandates WHERE organism_id=%s AND binding=%s",
                                [self.fx.lo.pk, binding])
        law = json.loads(stored)
        for name, value in mandate_fields.items():
            self.assertEqual(law.get(name), value, "The adopted instrument must contain the requested " + name + ".")
        self.assertIsNone(law.get("delegating_parent"), "The external decider must have no delegating-parent veto over the target.")
        attachment = c.step(self.issuer, binding, action, **act_fields)
        attempts = [self.fx.topic(self.issuer, markdown=attachment["text"]) for _ in range(3)]
        board, = committee.one("SELECT board_uuid FROM public.core_discboard WHERE organism_id=%s AND circle_id=%s",
                               [self.fx.lo.pk, self.decider.pk])
        other_board, = committee.one(
            """SELECT b.board_uuid FROM public.core_discboard b JOIN public.core_circle c ON c.id=b.circle_id
               WHERE b.organism_id=%s AND c.circle_uuid=%s""", [self.fx.lo.pk, self.fx.board["circle_uuid"]])

        loss = self.fx.lose_chair(self.decider)
        child_deadline = None
        if fill:
            clock.advance_to(loss.effective_at + timedelta(hours=100))
            target_loss = self.fx.lose_chair(self.target)
            child_deadline = target_loss.effective_at + timedelta(hours=720)

        journey, _, post = attempts[0]
        request = uuid4()
        cq.put_forward(**journey, request_uuid=request, version_uuid=post["version"], attachment=attachment)
        proof = committee.one(
            """SELECT count(*) OVER (),act_uuid,board_uuid,actor_id,request->>'version',facts->>'version',
                      (request->'attachment')::text,facts->>'attachment_fingerprint'=public.disc_fingerprint(%s::jsonb),
                      effective_at FROM public.core_cqact
               WHERE organism_id=%s AND thread_uuid=%s AND operation='INITIATE'""",
            [json.dumps(attachment), self.fx.lo.pk, journey["thread_uuid"]])
        self.assertIsNotNone(proof, "The active named body's vote must have a committed initiation.")
        count, act, actual_board, actor, requested, frozen, attached, fingerprint_ok, active_cut = proof
        self.assertEqual((count, act, actual_board, actor), (1, request, board, self.fx.member.pk),
                         "One initiation must name the actual request, the deciding Circle's board and its continuing member.")
        self.assertEqual((requested, frozen, json.loads(attached), fingerprint_ok),
                         (str(post["version"]), str(post["version"]), attachment, True),
                         "The initiation must preserve this exact suggestion version, attachment and its fingerprint.")
        self.assertEqual(committee.one("SELECT public.circle_effective_state(%s,%s,%s)", [self.decider.pk, active_cut, active_cut]),
                         ("ACTIVE",), "The deciding Circle must have capacity at the committed initiation's cut.")
        self.check_target(binding, active_cut, fill, child_deadline)
        plan = self.plan(board, attachment, active_cut)
        effect = planned_effect if fill else {"completed": str(binding)}
        self.assertEqual((plan.get("domain"), plan.get("route"), plan.get("committee")),
                         ("COMMITTEE", "CQ_CIRCLE", attachment["spec"] | effect),
                         "The stored board's active plan must name the exact committee act and its target.")
        self.assertEqual(self.plan_refusal(other_board, attachment, active_cut), code,
                         "The Board that formed the target cannot stand in for the different body named in its law.")
        if not fill:
            wrong_task = c.step(self.issuer, binding, action, task="a different task")
            self.assertEqual(self.plan_refusal(board, wrong_task, active_cut), code,
                             "Even an active named body cannot complete a different task.")

        due = self.fx.move_past(loss)
        for recorded, (journey, _, post) in zip((False, True), attempts[1:]):
            if recorded:
                self.fx.record_dormancy(loss)
            cut = support.database_now()
            self.decider.refresh_from_db()
            self.assertGreater(cut, due, "The late write must occur strictly after the deciding Circle's deadline.")
            self.assertEqual(self.decider.state, "DORMANT" if recorded else "ACTIVE",
                             "The two attempts must distinguish silent expiry from the recorded dormant head.")
            self.assertEqual(committee.one("SELECT public.circle_effective_state(%s,%s,%s)", [self.decider.pk, cut, cut]),
                             ("DORMANT",), "Expiry must take the deciding Circle's capacity even before its head is updated.")
            self.check_target(binding, cut, fill, child_deadline)
            direct = self.plan_refusal(board, attachment, cut)
            refusal = None
            try:
                cq.put_forward(**journey, request_uuid=uuid4(), version_uuid=post["version"], attachment=attachment)
            except cq.CQRefused as error:
                refusal = (error.code, str(error))
            after = support.database_now()
            direct_after = self.plan_refusal(board, attachment, after)
            counts = committee.one(
                """SELECT count(*) FILTER (WHERE operation='INITIATE'), count(*) FILTER (WHERE operation='IMPLEMENT')
                   FROM public.core_cqact WHERE organism_id=%s AND thread_uuid=%s""",
                [self.fx.lo.pk, journey["thread_uuid"]])
            with self.subTest(recorded=recorded, route="storage"):
                self.assertEqual((direct, direct_after), (code, code),
                                 "The stored plan must refuse for the named body's lack of capacity on both sides of the public attempt.")
            with self.subTest(recorded=recorded, route="public"):
                self.assertEqual(refusal, (code, self.landed_refusals[code]),
                                 "The member's late request must receive the existing refusal for this exact act.")
            with self.subTest(recorded=recorded, route="history"):
                self.assertEqual(counts, (0, 0), "The refused suggestion must leave no initiation or implementation.")
            self.check_target(binding, after, fill, child_deadline)
