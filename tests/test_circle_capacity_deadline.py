"""C1-33. Committed capacity at a Circle's own deadline, with and without the engine record."""
from datetime import timedelta
import json
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.test import TransactionTestCase

from core import models
from intevia.test_postgresql_backend import clock
from src.intevia.services import cq, elections
from tests import committee_support as c, election_support as fx, s015_0021_support as support
from tests import test_circle_committee_dormancy as committee_witness
from tests import test_election_mandates as mandates, test_guiding_intentions as intentions, test_law_acts as law

one = committee_witness.one


class CircleCapacityDeadlineTests(TransactionTestCase):
    topic = mandates.ElectionMandateTests.topic
    committee = mandates.ElectionMandateTests.committee
    activate_board = mandates.ElectionMandateTests.activate_board
    constitution = mandates.ElectionMandateTests.constitution
    decision = mandates.ElectionMandateTests.decision
    lose_chair = committee_witness.CircleCommitteeDormancyTests.lose_chair
    move_past = committee_witness.CircleCommitteeDormancyTests.move_past
    record_dormancy = committee_witness.CircleCommitteeDormancyTests.record_dormancy

    def ordinary_circle(self, *, other_members=3, with_law=False):
        self.topic_category_ids = {}
        self.lo, self.founder = law.ordinary_birth(offering_choice="COMPLIMENTARY_BETA")
        self.general = {"organism_uuid": self.lo.organism_id, "credential": self.founder.credential}
        circle = self.committee(self.general)
        citation = intentions.local_law(self.lo, self.founder)[0] if with_law else None
        fx.enable_voting(self.general)
        others = [law.add_member(self.lo) for _ in range(other_members)]
        for voter in others:
            c.join(self.general, circle, voter)
        self.member = others[0]
        return circle, others, citation

    def seated_board(self):
        self.lo, self.general, board, voters = c.formation_board(self, offering="COMPLIMENTARY_BETA")
        self.founder, self.member, _ = voters
        return board, voters

    def member_board(self, circle):
        return self.general | {"credential": self.member.credential, "circle_uuid": circle.circle_uuid}

    def attempts(self, scope, attachment):
        return [self.topic(scope, markdown=attachment["text"]) for _ in range(3)]

    def initiate(self, attempt, attachment):
        journey, _, post = attempt
        return cq.put_forward(**journey, request_uuid=uuid4(), version_uuid=post["version"], attachment=attachment)

    def boundaries(self, circle, attachment, attempts, expected_code, check):
        loss = self.lose_chair(circle)
        self.initiate(attempts[0], attachment)
        check(False)
        self.move_past(loss)
        for index in (1, 2):
            if index == 2:
                self.record_dormancy(loss)
            with self.assertRaises(cq.CQRefused) as refused:
                self.initiate(attempts[index], attachment)
            self.assertEqual(refused.exception.code, expected_code)
            check(True)
            self.assertEqual(one("""SELECT count(*) FROM public.core_cqact
                WHERE thread_uuid=%s AND operation='INITIATE'""", [attempts[index][0]["thread_uuid"]]), (0,))

    def test_circle_reporting_authority_at_its_deadline(self):
        """W18. A continuing member can propose the reporting choice only during recovery."""
        circle, _, _ = self.ordinary_circle()
        scope = self.member_board(circle)
        att = cq.circle_reporting_attachment(organism_uuid=self.lo.organism_id, circle_uuid=circle.circle_uuid,
                                             reporting="TOTALS_ONLY", predecessor=None)
        attempts = self.attempts(scope, att)

        def check(expired):
            self.assertEqual(one("""SELECT coalesce(p->>'code',p->>'domain') FROM public.core_discboard b
                CROSS JOIN LATERAL public.cq4_circle_plan(b.organism_id,b.board_uuid,%s,%s::jsonb,%s) p
                WHERE b.circle_id=%s""", [self.member.pk, json.dumps(att), support.database_now(), circle.pk]),
                ("CQ_BODY" if expired else "CIRCLE_REPORTING",))
            self.assertEqual(one("SELECT public.s015_entitlement_effective(%s,'CQ_TOTALS_ONLY',%s)",
                                 [self.lo.organism_id, support.database_now()]), (True,))
            self.assertEqual(cq.settings(**scope)["reporting_origin"], "PLATFORM")

        self.boundaries(circle, att, attempts, "CQ_BODY", check)

    def gi_attachment(self, circle, citation):
        spec = {"organism": str(self.lo.organism_id), "circle": str(circle.circle_uuid), "gi": str(uuid4()),
                "operation": "CREATE", "predecessor": None, "title": "Welcome", "citations": [
                    {"binding": str(citation["binding"]), "version": citation["version"]}]}
        return {"kind": "GI", "spec": spec, "text": "Welcome each family."}

    def pending_gi(self, circle, voters, citation):
        attachment = self.gi_attachment(circle, citation)
        journey = c.start(self, self.general | {"circle_uuid": circle.circle_uuid}, attachment)
        ballot = fx.open_election(journey)
        for voter in voters[:3]:
            fx.cast(journey, ballot, voter)
        self.assertEqual(one("""SELECT count(*),count(*) FILTER(WHERE choice='QUALIFIED')
            FROM public.cq_ballot_entries(%s,%s)""", [ballot, support.database_now()]), (5, 3))
        self.assertEqual(one("SELECT count(*) FROM public.core_cqact WHERE thread_uuid=%s AND operation='SETTLE'",
                             [journey["thread_uuid"]]), (0,))
        deadline, = one("SELECT (facts->>'deadline')::timestamptz FROM public.core_cqact WHERE act_uuid=%s", [ballot])
        return attachment, journey["thread_uuid"], ballot, deadline

    def check_applied_gi(self, decision, gi_id):
        # A formally applied result already has its IMPLEMENT in the same statement.
        with self.assertRaises(IntegrityError) as refused, transaction.atomic():
            one("""INSERT INTO public.core_cqact
                (act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
                SELECT %s,organism_id,board_uuid,thread_uuid,'IMPLEMENT',actor_id,actor_epoch,
                       public.intevia_clock_statement(),jsonb_build_object('result',act_uuid)
                  FROM public.core_cqact WHERE act_uuid=%s RETURNING act_uuid""", [uuid4(), decision["result"]])
        self.assertEqual(refused.exception.__cause__.diag.message_primary, "CQ_COMPLETE")
        self.assertEqual(one("""SELECT count(*),bool_and(act_uuid=%s) FROM public.core_cqact WHERE operation='IMPLEMENT'
                AND request->>'result'=%s""", [decision["implementation"], str(decision["result"])]), (1, True))
        self.assertEqual(one("SELECT count(*) FROM public.core_giversion WHERE gi_uuid=%s", [gi_id]), (1,))

    def test_gi_owner_authority_at_its_deadline(self):
        """W19. Loss of owner capacity prevents a new GI and prevents applying a pre-existing majority."""
        circle, voters, citation = self.ordinary_circle(other_members=4, with_law=True)
        local = self.general | {"circle_uuid": circle.circle_uuid}
        approved = self.gi_attachment(circle, citation)
        decision = c.decide_instrument(self, local, approved, [self.founder, *voters])
        pending, thread, ballot, ballot_end = self.pending_gi(circle, voters, citation)
        new = self.gi_attachment(circle, citation)
        attempts = self.attempts(self.member_board(circle), new)

        def plan():
            return one("""SELECT coalesce(p->>'code',p->>'domain') FROM public.core_livingorganism lo
                JOIN public.core_discboard b ON b.organism_id=lo.id
                CROSS JOIN LATERAL public.cq3_gi_plan(lo,b.board_uuid,%s,%s::jsonb,%s) p
                WHERE b.circle_id=%s""", [self.member.pk, json.dumps(new), support.database_now(), circle.pk])

        loss = self.lose_chair(circle)
        self.assertEqual(plan(), ("GI",))
        self.initiate(attempts[0], new)
        clock.advance_to(max(loss.effective_at + timedelta(hours=720), ballot_end) + timedelta(microseconds=1))
        for index in (1, 2):
            if index == 2:
                self.record_dormancy(loss)
            self.assertEqual(plan(), ("CQ_BODY",), "This GI plan must lose its owner's authority at the deadline.")
            with self.assertRaises(cq.CQRefused) as refused:
                self.initiate(attempts[index], new)
            self.assertEqual(refused.exception.code, "CQ_BODY")
            self.assertEqual(one("""SELECT count(*) FROM public.core_cqact WHERE thread_uuid=%s
                AND operation='INITIATE'""", [attempts[index][0]["thread_uuid"]]), (0,))
            self.assertEqual(one("SELECT count(*) FROM public.core_giversion WHERE gi_uuid=%s", [new["spec"]["gi"]]), (0,))
            if index == 1:
                cq.catch_up(credential=self.member.credential)
                self.assertEqual(one("""SELECT facts#>>'{count,kind}',facts->>'disposition',facts->>'reason'
                    FROM public.core_cqact WHERE thread_uuid=%s AND operation='SETTLE' AND request->>'ballot'=%s""",
                    [thread, str(ballot)]), ("QUALIFIED", "NO_EFFECT",
                                             "The GI owner no longer has authority to make this decision."))
                self.assertEqual(one("SELECT count(*) FROM public.core_cqact WHERE thread_uuid=%s AND operation='IMPLEMENT'",
                                     [thread]), (0,))
                self.assertEqual(one("SELECT count(*) FROM public.core_giversion WHERE gi_uuid=%s",
                                     [pending["spec"]["gi"]]), (0,))
                self.check_applied_gi(decision, approved["spec"]["gi"])

    def test_committee_cannot_exercise_its_law_grant_after_expiry(self):
        """W20. The reserved area and the terms stay in force while the committee loses power."""
        board, voters = self.seated_board()
        circle = self.committee(self.general)
        for voter in voters[1:]:
            c.join(self.general, circle, voter)
        formed, _ = c.mandate(self.lo, self.founder, circle, "1", publisher=c.first_version)
        c.decide_instrument(self, board, formed, voters)
        text = {"committee_adoption": {"mandate": formed["spec"]["proposal"], "area": "reserves"}, "text": "Hold reserves."}
        doc, version = c.first_version(self.founder, text)
        att = c.instrument(self.lo, doc, version)
        attempts = self.attempts(self.member_board(circle), att)

        def check(expired):
            at = support.database_now()
            self.assertEqual(one("""SELECT coalesce(p->>'code',p->>'route') FROM public.core_discboard b
                CROSS JOIN LATERAL public.cmt_law_grant(b.organism_id,b.board_uuid,%s,%s,'POLICY',%s,%s) p
                WHERE b.circle_id=%s""", [version.resource_id, version.content, at, at, circle.pk]),
                ("CMT_ENDED" if expired else "CQ_CIRCLE",))
            self.assertEqual(one("SELECT count(*) FROM public.core_organismlawbinding WHERE version_id=%s",
                                 [version.pk]), (0,))

        self.boundaries(circle, att, attempts, "CMT_ENDED", check)

    def test_board_cannot_transfer_election_to_expired_recipient(self):
        """W21. A named independent Circle becomes an ineligible recipient before its recorded dormancy."""
        board, voters = self.seated_board()
        circle = self.committee(self.general)
        c.join(self.general, circle, self.member)
        recipient = str(circle.circle_uuid)
        citation, _ = self.constitution(self.lo, self.founder, self.general, voters, {
            "committees": {"chair_board_member": False}, "elections": {"Trustee": fx.seat(place="trustee-3")},
            "authority_transfers": {"Delegation": {"grantor": "GOVERNANCE", "power": "RUN_ELECTION",
                                                   "election": "Trustee", "recipients": [recipient, "LO_MEMBERSHIP"]}}})
        power = citation | {"power": "RUN_ELECTION", "provision": "Trustee", "place": "trustee-3"}
        content = elections.transfer_policy(scope=power, citation=citation | {"provision": "Delegation"},
                    from_body="LO_MEMBERSHIP", to_body=recipient, predecessor=None, purpose="Run the trustee election.")
        doc, version = c.first_version(self.founder, json.loads(content))
        att = c.instrument(self.lo, doc, version)
        attempts = self.attempts(board, att)

        def check(expired):
            self.assertEqual(elections.power_history(**self.general, scope=power), [])
            self.assertEqual(one("""SELECT coalesce(p->>'code',p#>>'{transfer,to}') FROM public.core_discboard b
                CROSS JOIN LATERAL public.elect_policy_transfer(b.organism_id,b.board_uuid,%s::jsonb,%s) p
                WHERE b.circle_id=(SELECT id FROM public.core_circle WHERE circle_uuid=%s)""",
                [json.dumps(att), support.database_now(), board["circle_uuid"]]), ("ELECT_GRANT_RECIPIENT" if expired else recipient,))

        self.boundaries(circle, att, attempts, "ELECT_GRANT_RECIPIENT", check)
