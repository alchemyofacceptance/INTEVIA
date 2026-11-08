"""ELECT P1. Five distinct claims against the real governed writers."""

from datetime import timedelta
from uuid import uuid4

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from core import models
from src.intevia.services import circle_commands, cq, cq_reads, elections
from src.intevia.services import discussion as disc
from src.intevia.services import discussion_posts as posts
from tests import cq_authority_support as board
from tests import election_support as fx
from tests import s015_0021_support as support
from tests import test_cq_clarity as clarity
from tests import test_law_acts as law


class ElectionTests(TransactionTestCase):
    topic = clarity.CQClarityTests.topic

    def test_member_election_seats_trustee_before_activation(self):
        """ELECT-01. A cited member vote fills an elected seat while the Board is dormant. Uncited and unsupported calls refuse."""
        term_end = support.database_now() + timedelta(days=90)
        rules = {
            fx.ARTICLE: fx.seat(term={"end": term_end.isoformat()}),
            "Joint": fx.seat(counting={"method": "PREFERENTIAL"}),
            "Undated": fx.seat(term={"end": "next term"}),
        }
        lo, founder, scope, circle, citation = fx.owner(rules=rules)
        trustee, third = law.add_member(lo), law.add_member(lo)
        for provision, expected in [
            ("Missing", cq.MESSAGES["ELECT_CITATION"]),
            ("Joint", "This counting method is unsupported. Hold the election before opening."),
            ("Undated", "Review the term. Supply an end and any start with a time zone, with the end after the start or current time."),
        ]:
            with self.subTest(provision=provision), self.assertRaises(cq.CQRefused) as refused:
                fx.call(self, scope, citation, provision=provision)
            self.assertEqual(str(refused.exception), expected)
        election = fx.call(self, scope, citation)
        nomination = fx.nominate(election, trustee)
        ballot = fx.open_election(election)
        for person in [founder, trustee, third]:
            fx.cast(election, ballot, person)
        result = cq_reads.vote_status(**election)["result"]
        self.assertEqual((result["count"]["kind"], result["count"]["winner"]), ("QUALIFIED", str(trustee.identity_id)))
        effect = cq_reads.implementation_record(**election, result_uuid=result["post_uuid"])["effect"]
        self.assertEqual(effect["election"]["consent"], str(nomination))
        place = models.CircleParticipation.objects.select_related("decision").get(circle=circle, seat_id="trustee-1")
        self.assertEqual((place.membership.identity_id, place.decision.decision_uuid, place.decision.deciding_identity_id),
                         (trustee.pk, effect["implementation"], None))
        self.assertEqual(place.decision.deciding_party, "body:LO_MEMBERSHIP")
        ending = models.CircleParticipationEnd.objects.get(participation=place)
        self.assertEqual((ending.effective_at, ending.composed_from_event_uuid), (term_end, effect["implementation"]))
        self.assertEqual(ending.composing_rule_reference, "elect-term:" + str(result["post_uuid"]))
        circle.refresh_from_db()
        self.assertIn(circle.state, ("DORMANT", "ELIGIBLE"))

    def test_office_election_follows_constitutional_body_and_consent(self):
        """ELECT-02. Direct-member and Board elections use their own electorate and exact consenting office proposal."""
        rules = {"Treasurer": {"type": "OFFICE", "place": "TREASURER", "body": "LO_MEMBERSHIP"},
                 "Chair": {"type": "OFFICE", "place": "CHAIR", "body": "GOVERNANCE"}}
        lo, founder, scope, circle, citation = fx.owner(seats=3, elected=(), rules=rules)
        second = board.seat_trustee(lo, founder, circle, 1)
        third = board.seat_trustee(lo, founder, circle, 2)
        member = law.add_member(lo)
        circle_commands.activate(**scope, circle_uuid=circle.circle_uuid, request_uuid=uuid4())
        board.appoint_board_coordinator(lo, circle, founder, second)
        for section, voters, circle_scope in [("Treasurer", [founder, second, third, member], {}),
                                               ("Chair", [founder, second, third], {"circle_uuid": circle.circle_uuid, "credential": second.credential})]:
            with self.subTest(section=section):
                rule = rules[section]
                candidate = third if circle_scope else second
                assignment = fx.office_proposal(lo, founder, candidate, rule["place"], rule["body"], section, citation,
                                                board_members=bool(circle_scope), self_proposed=bool(circle_scope))
                election = fx.call(self, scope | circle_scope, citation, provision=section, place=rule["place"])
                nom = fx.nominate(election, candidate, by=None if circle_scope else founder, assignment=assignment.assignment_uuid)
                if not circle_scope:
                    with self.assertRaises(cq.CQRefused) as refused:
                        fx.open_election(election)
                    self.assertEqual(str(refused.exception), cq.MESSAGES["ELECT_EMPTY"])
                    elections.consent(**(election | {"credential": second.credential}), request_uuid=uuid4(), nomination_uuid=nom)
                ballot = fx.open_election(election)
                self.assertEqual(cq_reads.vote_status(**election)["eligible"], len(voters))
                for person in voters:
                    fx.cast(election, ballot, person)
                post = cq_reads.vote_status(**election)["result"]
                effect = cq_reads.implementation_record(**election, result_uuid=post["post_uuid"])["effect"]
                event = models.ContextualRoleAssignmentStateEvent.objects.get(assignment=assignment, action="ASSIGN")
                self.assertEqual(event.evidence_reference, "elect-result:" + str(post["post_uuid"]))
                self.assertEqual((event.event_uuid, event.resulting_state), (effect["implementation"], "ACTIVE"))
                self.assertEqual(effect["election"]["body"], rule["body"])

    def test_count_requires_turnout_then_a_unique_qualified_candidate(self):
        """ELECT-03. Default floor, constitutional denominator, abstentions, ties, three-way split and the below-three stop."""
        people = [str(uuid4()) for _ in range(3)]
        a, b, c = people
        slate = [{"person": p} for p in people]
        rules = {"quorum_form": "MAJORITY", "threshold": "MAJORITY", "threshold_base": "VOTES_CAST"}
        cases = [
            (7, {a: 3}, {}, "NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION", None),
            (2, {a: 2}, {}, "NO_DECISION_FEWER_THAN_THREE", None),
            (7, {a: 2, b: 2, c: 1}, {}, "NOT_QUALIFIED_TIE", None),
            (7, {a: 3, b: 2, c: 1}, {}, "NOT_QUALIFIED_VOTE", None),
            (7, {a: 3, b: 1, "ABSTAIN": 2}, {}, "QUALIFIED", a),
            (7, {a: 3, b: 1, "ABSTAIN": 2}, {"threshold_base": "PARTICIPANTS"}, "NOT_QUALIFIED_VOTE", None),
        ]
        import json
        for eligible, totals, override, kind, winner in cases:
            with self.subTest(eligible=eligible, totals=totals, basis=override):
                count = disc._rows("SELECT public.elect_tally(%s::jsonb,%s::jsonb,%s::jsonb,%s) AS tally",
                    [json.dumps(rules | override), json.dumps(slate), json.dumps(totals), eligible])[0]["tally"]
                self.assertEqual((count["kind"], count["winner"], count["participated"]), (kind, winner, sum(totals.values())))

        # Start with the platform's own rule. Vary one constitutional instruction per case.
        for values, expected in [
            ({"abstention": "yes"}, "The electoral abstention rule must be true or false."),
            ({"quorum_form": "PERCENT", "quorum_value": 101},
             "Review the electoral participation rule, its value and any minimum waiver in the cited provision."),
            ({"threshold": "FRACTION", "numerator": 3, "denominator": 2},
             "Review the qualification threshold and any fraction in the cited electoral provision."),
        ]:
            with self.subTest(instruction=values), self.assertRaises(cq.CQRefused) as refused:
                try:
                    with transaction.atomic(), connection.cursor() as cur:
                        cur.execute(
                            "SELECT public.elect_check_count(settings || %s::jsonb, %s::jsonb, statement_timestamp()) "
                            "FROM public.cq_platform_defaults",
                            [json.dumps(values), json.dumps(values)],
                        )
                except IntegrityError as exc:
                    cq.translate_refusal(exc)
            self.assertEqual(str(refused.exception), expected)

    def test_secret_receipts_and_withdrawal_across_reads(self):
        """ELECT-04. Secret per-voter receipts and aggregate reads survive settled withdrawal. No appointment or automatic tie rerun."""
        lo, founder, scope, circle, citation = fx.owner()
        second, third = law.add_member(lo), law.add_member(lo)
        journey = fx.call(self, scope, citation)
        nom = fx.nominate(journey, second)
        fx.nominate(journey, third)
        ballot = fx.open_election(journey)
        receipt = fx.cast(journey, ballot, founder, second.identity_id)
        self.assertEqual(fx.cast(journey, ballot, founder, second.identity_id, receipt), receipt)
        self.assertEqual(cq_reads.vote_status(**journey)["own_receipt"], receipt)
        viewer = journey | {"credential": third.credential}
        outsider = support.register_test_learner(username="elect-outsider-" + uuid4().hex)
        with self.assertRaisesMessage(disc.DiscussionUnavailable, disc.UNAVAILABLE):
            elections.slate(**(journey | {"credential": outsider.credential}))
        status = cq_reads.vote_status(**viewer)
        self.assertEqual((status["participated"], status["own_receipt"]), (1, None))
        self.assertNotIn("participation", status)
        facts = disc._rows("SELECT request,facts FROM public.core_cqact WHERE act_uuid=%s", [receipt])[0]
        self.assertNotIn("choice", facts["request"])
        self.assertNotIn(str(second.identity_id), str(facts))
        elections.withdraw(**(journey | {"credential": second.credential}), nomination_uuid=nom, request_uuid=uuid4())
        post = cq_reads.vote_status(**viewer)["result"]
        self.assertEqual((post["count"]["kind"], post["count"]["winner"], post["restarted"]), ("NO_DECISION_SLATE_CHANGED", None, False))
        result = cq_reads.result_post(**viewer, result_uuid=post["post_uuid"])
        self.assertNotIn("participants", result)
        self.assertEqual(result["event_kind"], "ELECTION")
        self.assertEqual(cq_reads.reporting_decision(**viewer, result_uuid=post["post_uuid"])["origin"], "ELECTION")
        self.assertIsNone(cq_reads.implementation_record(**viewer, result_uuid=post["post_uuid"])["effect"])
        shared_post = next(p for p in posts.read_thread(**viewer)["posts"] if p.get("post_uuid") == result["post_uuid"])
        self.assertEqual(shared_post["count"], result["count"])
        self.assertNotIn("participants", shared_post)
        fresh = fx.call(self, scope, citation)
        fx.nominate(fresh, third)
        fresh_ballot = fx.open_election(fresh)
        for person in (founder, second, third):
            fx.cast(fresh, fresh_ballot, person, "NOT_QUALIFIED")
        defeated = cq_reads.vote_status(**fresh)["result"]
        self.assertEqual((defeated["count"]["kind"], defeated["count"]["winner"]), ("NOT_QUALIFIED_VOTE", None))
        self.assertIsNone(cq_reads.implementation_record(**fresh, result_uuid=defeated["post_uuid"])["effect"])
        self.assertFalse(models.CircleParticipation.objects.filter(circle=circle, seat_id="trustee-1").exists())

    def test_result_authorises_exact_person_and_place(self):
        """ELECT-05. An actual result binds winner, place and term. Refuse substitutions and reuse the same receipt/effect."""
        lo, founder, scope, circle, citation = fx.owner()
        winner, other = law.add_member(lo), law.add_member(lo)
        journey = fx.call(self, scope, citation)
        fx.nominate(journey, winner)
        ballot = fx.open_election(journey)
        for person in (founder, winner):
            fx.cast(journey, ballot, person)
        receipt = fx.cast(journey, ballot, other)
        result = cq_reads.vote_status(**journey)["result_uuid"]
        effect = cq_reads.implementation_record(**journey, result_uuid=result)["effect"]
        start = models.CircleParticipation.objects.get(participation_uuid=effect["implementation"]).effective_at
        def authorised(person, place, at):
            with connection.cursor() as cur:
                cur.execute("SELECT public.elect_authorises(%s,%s,%s,%s,%s,NULL)", [result, lo.pk, person.identity_id, place, at])
                return cur.fetchone()[0]
        self.assertTrue(authorised(winner, "trustee-1", start))
        self.assertFalse(authorised(other, "trustee-1", start), "The result must authorise the exact elected person and place.")
        self.assertFalse(authorised(winner, "trustee-0", start))
        self.assertFalse(authorised(winner, "trustee-1", start + timedelta(seconds=1)))
        self.assertEqual(fx.cast(journey, ballot, other, request=receipt), receipt)
        self.assertEqual(cq_reads.implementation_record(**journey, result_uuid=result)["effect"], effect)
        self.assertEqual(models.CircleParticipation.objects.filter(circle=circle, seat_id="trustee-1").count(), 1)
        with self.assertRaises(cq.CQRefused) as forged, transaction.atomic():
            try:
                with connection.cursor() as cur:
                    cur.execute("""INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,
                        actor_epoch,received_at,request) SELECT gen_random_uuid(),organism_id,board_uuid,thread_uuid,
                        'IMPLEMENT',actor_id,actor_epoch,statement_timestamp(),jsonb_build_object('result',act_uuid)
                        FROM public.core_cqact WHERE act_uuid=%s""", [result])
            except IntegrityError as exc:
                cq.translate_refusal(exc)
        self.assertEqual(str(forged.exception), cq.MESSAGES["CQ_COMPLETE"])
