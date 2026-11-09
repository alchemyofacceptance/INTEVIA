"""DISC2-09 through DISC2-19. Real decision journeys and the result readers."""

import json
from importlib import import_module
from types import SimpleNamespace
from uuid import UUID, uuid4

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from core import models
from src.intevia.services import (
    circle_commands,
    cq,
    cq_reads,
    organism_membership_service,
)
from src.intevia.services import (
    discussion as disc,
)
from src.intevia.services import (
    discussion_posts as posts,
)
from src.intevia.services.organism_application_authority import (
    ProductionApplicationAuthorityProvider,
)
from src.intevia.services.organism_authority import S015Authority
from tests import cq_authority_support as board_support
from tests import (
    cq_settings_support,
    discussion_support,
)
from tests import s015_0021_support as support
from tests import (
    test_c1b_departure as departure,
)
from tests import (
    test_cq_clarity as clarity,
)
from tests import test_law_acts as fixtures


class CQDecisionTests(TransactionTestCase):
    owner = clarity.CQClarityTests.owner
    topic = clarity.CQClarityTests.topic
    configure = clarity.CQClarityTests.configure
    facts = clarity.CQClarityTests.facts
    refused = clarity.CQClarityTests.refused
    contended_commands = clarity.CQClarityTests.contended_commands

    def setup_board(self, size, *, beta=False, changes=None):
        lo, owner, scope = self.owner(beta=beta)
        people = [owner] + [fixtures.add_member(lo) for _ in range(size - 1)]
        version = self.configure(scope, {"enabled": True, **(changes or {})})
        return lo, scope, people, version

    def confirmed(self, scope, people, *, category="DISCUSSION"):
        journey, category_id, topic = self.topic(scope, category=category)
        cq.put_forward(**journey, request_uuid=uuid4(), version_uuid=topic["version"])
        cq.mark_prepared(**journey, request_uuid=uuid4())
        for person in people:
            cq.confirm_clarity(
                **(journey | {"credential": person.credential}),
                request_uuid=uuid4(),
                version_uuid=topic["version"],
            )
        return journey, category_id

    def leave(self, lo, person):
        membership = models.OrganismMembership.objects.get(
            living_organism=lo, identity=person
        )
        at = support.database_now()
        membership_service = organism_membership_service.OrganismMembershipService(
            authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),
            clock=support.database_now,
        )
        membership_service.leave_organism(
            organism_membership_service.LeaveOrganismCommand(
                credential=person.credential,
                membership_uuid=membership.membership_uuid,
                confirmed=True,
                reason_class="VOLUNTARY",
                occurred_at=at,
                effective_at=at,
                idempotency_key=uuid4().hex,
                request_reference="disc2:ballot-departure:" + uuid4().hex,
            )
        )

    def cast(self, journey, ballot, member, choice="QUALIFIED", key=None):
        return cq.answer(
            **(journey | {"credential": member.credential}),
            ballot_uuid=ballot,
            choice=choice,
            request_uuid=key or uuid4(),
        )

    def closed(self, journey, ballot):
        status = cq_reads.vote_status(**journey, ballot_uuid=ballot)
        self.assertEqual(status["phase"], "RESULT")
        return cq_reads.result_post(**journey, result_uuid=status["result_uuid"])

    def counted(self, rules, size, yes, no, abstain):
        return disc._rows(
            "SELECT public.cq_count_decision(%s::jsonb,%s,%s,%s,%s) AS tally",
            [json.dumps(rules), size, yes, no, abstain],
        )[0]["tally"]

    def test_riverside_and_default_quorum_boundaries(self):
        """DISC2-09. Seven understand, six remain eligible, four carry at full turnout."""
        _, scope, people, _ = self.setup_board(7)
        journey, _ = self.confirmed(scope, people)
        conflicted = journey | {"credential": people[-1].credential}
        recusal = cq.declare_conflict(
            **conflicted, basis="I supply this service.", request_uuid=uuid4()
        )
        self.assertEqual(
            self.facts(recusal)["facts"]["basis"], "I supply this service."
        )
        self.refused(
            "CQ_OPEN_OFFICE",
            "Only this board's effective Coordinator may open this vote.",
            cq.open_vote,
            **(journey | {"credential": people[1].credential}),
            request_uuid=uuid4(),
        )
        ballot = cq.finish_clarity(**journey, request_uuid=uuid4())
        for index, member in enumerate(people[:-1]):
            self.cast(
                journey, ballot, member, "QUALIFIED" if index < 4 else "NOT_QUALIFIED"
            )
        result = self.closed(journey, ballot)
        count = result["count"]
        self.assertEqual(
            [
                count[k]
                for k in (
                    "kind",
                    "eligible",
                    "required",
                    "participated",
                    "qualified",
                    "not_qualified",
                )
            ],
            ["QUALIFIED", 6, 4, 6, 4, 2],
        )
        self.assertLess(result["closed_at"], result["deadline"])
        self.assertEqual(
            {p["identity"] for p in result["participants"]},
            {str(p.identity_id) for p in people[:-1]},
        )
        rules = cq.settings(**scope)["settings"]
        self.assertEqual(
            [self.counted(rules, n, 0, 0, 0)["required"] for n in (3, 4, 5, 7, 12, 51)],
            [3, 3, 3, 4, 7, 26],
        )

    def test_ballot_conflict_duplicate_abstention_and_eligible_period(self):
        """DISC2-10. One answer, own conflict, late membership and exact refusals."""
        lo, scope, people, _ = self.setup_board(4)
        journey, _ = self.confirmed(scope, people)

        def unknown_ballot(scope, member):
            with self.assertRaisesMessage(disc.DiscussionUnavailable, disc.UNAVAILABLE):
                self.cast(scope, uuid4(), member, key=key)

        key = uuid4()
        unknown_ballot(journey, people[0])
        ballot = cq.finish_clarity(**journey, request_uuid=uuid4())
        unknown_ballot(journey, people[0])
        self.assertEqual(self.cast(journey, ballot, people[1], key=key), key)
        self.assertEqual(self.cast(journey, ballot, people[1], key=key), key)
        self.refused(
            "CQ_DUPLICATE",
            "You have already answered this vote.",
            self.cast,
            journey=journey,
            ballot=ballot,
            member=people[1],
            choice="NOT_QUALIFIED",
        )
        self.refused(
            "CQ_ABSTAIN_OFF",
            "Abstention is not available for this vote.",
            self.cast,
            journey=journey,
            ballot=ballot,
            member=people[0],
            choice="ABSTAIN",
        )
        self.refused(
            "CQ_CHOICE",
            "Choose Qualified, Not Qualified or Abstain.",
            self.cast,
            journey=journey,
            ballot=ballot,
            member=people[0],
            choice="MAYBE",
        )
        late = fixtures.add_member(lo)
        self.refused(
            "CQ_ELECTORATE",
            "You are not in this vote's eligible electorate.",
            self.cast,
            journey=journey,
            ballot=ballot,
            member=late,
        )
        self.refused(
            "CQ_ELECTORATE",
            "You are not in this vote's eligible electorate.",
            self.cast,
            journey=journey,
            ballot=ballot,
            member=late,
            key=key,
        )
        cq.declare_conflict(
            **(journey | {"credential": people[1].credential}),
            basis="I tendered.",
            request_uuid=uuid4(),
        )
        self.refused(
            "CQ_CONFLICTED",
            "You declared a conflict for this proposal and cannot vote on it.",
            self.cast,
            journey=journey,
            ballot=ballot,
            member=people[1],
        )
        unknown_ballot(journey, people[1])
        status = cq_reads.vote_status(**journey)
        self.assertEqual((status["eligible"], status["participated"]), (3, 0))
        for member in (people[0], people[2], people[3]):
            self.cast(journey, ballot, member, "NOT_QUALIFIED")
        result = self.closed(journey, ballot)
        self.assertEqual(
            (result["count"]["qualified"], result["count"]["kind"]),
            (0, "NOT_QUALIFIED_VOTE"),
        )
        self.assertTrue(
            models.LivingOrganismEvent.objects.filter(event_uuid=key).exists()
        )

        unknown_ballot(journey, people[0])

        # The last non-voter leaves on the production membership route. A different
        # ballot's earlier deadline must retain its five-person historical cut.
        pool = [*people, late]
        live, _ = self.confirmed(scope, pool)
        elapsed, _ = self.confirmed(scope, pool)
        live_id = cq.finish_clarity(**live, request_uuid=uuid4())
        with clarity.due_at_phase_start():
            elapsed_id = cq.finish_clarity(**elapsed, request_uuid=uuid4())
        for member in people:
            self.cast(live, live_id, member)
        self.leave(lo, late)
        early = self.closed(live, live_id)
        self.assertEqual(
            (early["count"]["eligible"], early["count"]["participated"]), (4, 4)
        )
        rule = self.facts(early["post_uuid"])
        self.assertEqual(
            (rule["authority_source"]["kind"], rule["actor_id"]), ("RULE", late.pk)
        )
        self.assertEqual(rule["request"]["cause"]["kind"], "MEMBERSHIP")
        self.assertEqual(
            cq.catch_up(credential=people[0].credential), "Due work recorded."
        )
        past = self.closed(elapsed, elapsed_id)
        self.assertEqual(
            (past["count"]["eligible"], past["count"]["participated"]), (5, 0)
        )
        self.assertEqual(past["closed_at"], past["deadline"])

    test_ballot_conflict_duplicate_abstention_and_eligible_period.ISOLATION_FULL_CAPTURE = True

    def test_default_tie_and_exact_threshold_bases(self):
        """DISC2-11. The registered fault must change this specific tie assertion."""
        _, scope, people, version = self.setup_board(4, changes={"clarity_stage": False})
        journey, ballot = cq_settings_support.prepared_ballot(self, scope)
        for index, member in enumerate(people):
            self.cast(
                journey, ballot, member, "QUALIFIED" if index < 2 else "NOT_QUALIFIED"
            )
        result = self.closed(journey, ballot)
        self.assertEqual(
            result["count"]["kind"],
            "NOT_QUALIFIED_TIE",
            "A quorate default tie must be Not Qualified.",
        )
        self.configure(
            scope,
            {
                "threshold": "FRACTION",
                "numerator": 2,
                "denominator": 3,
                "abstention": True,
            },
            version,
        )
        other, second = cq_settings_support.prepared_ballot(self, scope)
        for member, choice in zip(
            people, ("QUALIFIED", "QUALIFIED", "NOT_QUALIFIED", "ABSTAIN"), strict=True
        ):
            self.cast(other, second, member, choice)
        tally = self.closed(other, second)["count"]
        self.assertEqual(
            [
                tally[k]
                for k in (
                    "kind",
                    "participated",
                    "abstained",
                    "qualification_denominator",
                )
            ],
            ["QUALIFIED", 4, 1, 3],
        )
        rules = cq.settings(**scope)["settings"]
        for base, denominator in (("PARTICIPANTS", 4), ("ELIGIBLE", 5)):
            tally = self.counted(rules | {"threshold_base": base}, 5, 2, 1, 1)
            self.assertEqual(
                (tally["kind"], tally["qualification_denominator"]),
                ("NOT_QUALIFIED_VOTE", denominator),
            )

    def test_low_turnout_and_distinct_no_decision_results(self):
        """DISC2-12. Deadline, below-floor closure and loss of minimum are distinct."""
        lo, scope, people, _ = self.setup_board(3)
        journey, _ = self.confirmed(scope, people)
        with clarity.due_at_phase_start():
            ballot = cq.finish_clarity(**journey, request_uuid=uuid4())
        self.assertEqual(
            cq_reads.vote_status(**journey)["phase"], "AWAITING_SETTLEMENT"
        )
        self.refused(
            "CQ_VOTE_CLOSED",
            "This vote is closed.",
            self.cast,
            journey=journey,
            ballot=ballot,
            member=people[0],
        )
        self.assertEqual(
            cq.catch_up(credential=people[1].credential), "Due work recorded."
        )
        result = self.closed(journey, ballot)
        self.assertEqual(
            (
                result["count"]["kind"],
                result["count"]["cqt_applied"],
                result["restarted"],
            ),
            ("NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION", False, False),
        )
        self.assertEqual(
            self.facts(result["post_uuid"])["facts"]["participation_record"],
            [
                {"identity": str(person.identity_id), "status": "NO_ANSWER_BY_CLOSE"}
                for person in sorted(people, key=lambda person: person.pk)
            ],
        )
        self.assertEqual(result["closed_at"], result["deadline"])
        self.assertEqual(
            cq.catch_up(credential=people[1].credential), "Nothing is due."
        )
        other, _ = self.confirmed(scope, people)
        ballot = cq.finish_clarity(**other, request_uuid=uuid4())
        self.cast(other, ballot, people[0])
        cq.declare_conflict(
            **(other | {"credential": people[2].credential}),
            basis="Related party",
            request_uuid=uuid4(),
        )
        count = self.closed(other, ballot)["count"]
        self.assertEqual(
            (
                count["eligible"],
                count["participated"],
                count["kind"],
                count["cqt_applied"],
            ),
            (2, 1, "NO_DECISION_FEWER_THAN_THREE", False),
        )
        pending, _ = self.confirmed(scope, people)
        cq.declare_conflict(**pending, basis="Financial interest", request_uuid=uuid4())
        self.refused(
            "CQ_THREE",
            "Fewer than three eligible members remain. A CQ vote cannot open.",
            cq.finish_clarity,
            **pending,
            request_uuid=uuid4(),
        )
        rules = cq.settings(**scope)["settings"]
        self.assertEqual(
            self.counted(
                rules | {"quorum_form": "NO_FLOOR", "waive_minimum": True}, 3, 1, 0, 0
            )["kind"],
            "QUALIFIED",
        )

        for waived, required in ((False, 3), (True, 2)):
            tally = self.counted(
                rules | {"quorum_form": "MAJORITY", "waive_minimum": waived}, 3, 2, 0, 0
            )
            self.assertEqual(
                (tally["required"], tally["cqt_applied"]), (required, waived)
            )

        for percent, waived, required in (
            (51, False, 3),
            (40, False, 3),
            (40, True, 2),
        ):
            tally = self.counted(
                rules
                | {
                    "quorum_form": "PERCENT",
                    "quorum_value": percent,
                    "waive_minimum": waived,
                },
                3,
                2,
                0,
                0,
            )
            self.assertEqual(
                (tally["required"], tally["cqt_applied"]), (required, waived)
            )
        # Enough at the actual deadline for a retry request, too few at catch-up to restart.
        self.configure(
            scope, {"low_turnout": "NEW_CQ"}, cq.settings(**scope)["version"]
        )
        elapsed, _ = self.confirmed(scope, people)
        with clarity.due_at_phase_start():
            expired = cq.finish_clarity(**elapsed, request_uuid=uuid4())
        self.leave(lo, people[2])
        self.assertEqual(
            cq.catch_up(credential=people[0].credential), "Due work recorded."
        )
        no_retry = self.closed(elapsed, expired)
        self.assertEqual(
            (
                no_retry["count"]["eligible"],
                no_retry["count"]["kind"],
                no_retry["retry_requested"],
                no_retry["restarted"],
            ),
            (3, "NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION", True, False),
        )
        self.assertEqual(cq_reads.vote_status(**elapsed)["ballot"], expired)

    test_low_turnout_and_distinct_no_decision_results.ISOLATION_FULL_CAPTURE = True

    def test_concurrent_settings_and_opening_snapshot(self):
        """DISC2-13. A waiting opener sees both parts of a committed settings change."""
        _, scope, people, current = self.setup_board(3, beta=True)
        journey, _ = self.confirmed(scope, people)
        proposal, wording = cq_settings_support.propose(
            self, scope, {"reporting": "TOTALS_ONLY", "abstention": True}, current
        )
        setting_ballot = cq_settings_support.open_motion(proposal, wording, people)
        for person in people[:-1]:
            self.cast(proposal, setting_ballot, person)

        def decide_settings():
            self.cast(proposal, setting_ballot, people[-1])
            return cq.settings(**scope)["version"]

        version, waiting = self.contended_commands(
            decide_settings, lambda: cq.finish_clarity(**journey, request_uuid=uuid4())
        )
        ballot = waiting.result(timeout=5)
        self.assertEqual(
            self.facts(ballot)["facts"]["reporting_rule"]["version"], str(version)
        )
        self.assertEqual(self.facts(ballot)["facts"]["settings_version"], str(version))
        for member, choice in zip(
            people, ("ABSTAIN", "QUALIFIED", "QUALIFIED"), strict=True
        ):
            self.cast(journey, ballot, member, choice)
        result = self.closed(journey, ballot)
        self.assertEqual(
            (result["count"]["abstained"], result["reporting_rule"]["value"]),
            (1, "TOTALS_ONLY"),
        )
        self.assertNotIn("participants", result)

    test_concurrent_settings_and_opening_snapshot.ISOLATION_FULL_CAPTURE = True

    def test_open_read_embargo_and_early_release(self):
        """DISC2-14. Participation before closure, named answers after, ordered forum post."""
        _, scope, people, _ = self.setup_board(3)
        journey, category, topic = self.topic(scope)
        reader = journey | {"credential": people[1].credential}
        cq.put_forward(**journey, request_uuid=uuid4(), version_uuid=topic["version"])
        cq.mark_prepared(**journey, request_uuid=uuid4())

        def hide(hidden, coordinator=False):
            posts.set_post_hidden(
                **journey,
                category_uuid=category,
                post_uuid=topic["post"],
                hidden=hidden,
                as_coordinator=coordinator,
                request_uuid=uuid4(),
            )

        for coordinator in (False, True):
            hide(True, coordinator)
            with self.assertRaisesMessage(disc.DiscussionUnavailable, disc.UNAVAILABLE):
                cq.phase(**reader)
            with self.assertRaisesMessage(disc.DiscussionUnavailable, disc.UNAVAILABLE):
                cq.confirm_clarity(
                    **reader, version_uuid=topic["version"], request_uuid=uuid4()
                )
            hide(False, coordinator)
        for member in people:
            cq.confirm_clarity(
                **(journey | {"credential": member.credential}),
                version_uuid=topic["version"],
                request_uuid=uuid4(),
            )
        for coordinator in (False, True):
            hide(True, coordinator)
            self.refused(
                "CQ_FULL_AUDIENCE",
                cq.MESSAGES["CQ_FULL_AUDIENCE"],
                cq.finish_clarity,
                **journey,
                request_uuid=uuid4(),
            )
            hide(False, coordinator)
        ballot = cq.finish_clarity(**journey, request_uuid=uuid4())
        self.cast(journey, ballot, people[1], "NOT_QUALIFIED")
        status = cq_reads.vote_status(**journey)
        self.assertEqual(
            (status["eligible"], status["participated"], status["event_kind"]),
            (3, 1, "DECISION"),
        )
        self.assertNotIn("result", status)
        self.assertEqual(
            {str(p["identity"]) for p in status["participation"] if p["answered"]},
            {str(people[1].identity_id)},
        )
        self.assertTrue(
            all(
                set(p) == {"identity", "name", "answered"}
                for p in status["participation"]
            )
        )

        dates = cq.phase(**journey)["phase_dates"]
        self.assertEqual(
            [row["phase"] for row in dates],
            ["PREPARATION", "CLARITY", "VOTING"],
        )
        self.assertEqual(dates[-1]["deadline"], status["deadline"])

        def ordinary_reply():
            posts.reply(
                **journey,
                category_uuid=category,
                post_uuid=uuid4(),
                request_uuid=uuid4(),
                markdown="Minute this.",
            )

        ordinary_reply()
        self.cast(journey, ballot, people[0])
        self.cast(journey, ballot, people[2])
        ordinary_reply()
        result = self.closed(journey, ballot)
        self.assertEqual(
            {p["identity"]: p["answer"] for p in result["participants"]},
            {
                str(p.identity_id): ("NOT_QUALIFIED" if p == people[1] else "QUALIFIED")
                for p in people
            },
        )
        thread = posts.read_thread(**journey)["posts"]
        self.assertEqual([p["post_number"] for p in thread], [1, 2, 3, 4])
        self.assertEqual(
            (thread[2]["post_type"], thread[2]["post_uuid"]),
            ("GROUP_GAZE", result["post_uuid"]),
        )
        trace = cq_reads.reporting_decision(**journey, result_uuid=result["post_uuid"])
        self.assertEqual(
            (trace["origin"], trace["version"], trace["value"]),
            ("PLATFORM", result["reporting_rule"]["version"], "NAMED"),
        )
        self.assertTrue(trace["ruling_basis"])

        hide(True)
        self.assertEqual(
            cq_reads.result_post(**reader, result_uuid=result["post_uuid"]), result
        )
        self.assertTrue(posts.read_thread(**reader)["posts"][0]["hidden"])
        hide(False)
        hide(True, True)
        for read, args in (
            (cq_reads.vote_status, {"ballot_uuid": ballot}),
            (cq_reads.result_post, {"result_uuid": result["post_uuid"]}),
            (cq_reads.reporting_decision, {"result_uuid": result["post_uuid"]}),
        ):
            with self.assertRaisesMessage(disc.DiscussionUnavailable, disc.UNAVAILABLE):
                read(**reader, **args)
        hide(False, True)
        self.assertEqual(self.closed(reader, ballot), result)

    def test_one_fresh_retry_exhausts_automation(self):
        """DISC2-15. A fixed absolute floor above available voters exercises both failures without a clock."""
        lo, scope, people, current = self.setup_board(
            4,
            beta=True,
            changes={
                "low_turnout": "NEW_CQ",
                "quorum_form": "ABSOLUTE",
                "quorum_value": 5,
            },
        )
        journey, _ = self.confirmed(scope, people)
        ballot = cq.finish_clarity(**journey, request_uuid=uuid4())
        # The four original voters cannot change an absolute floor of five.
        # The fifth member can join the settings motion; the first roster is frozen.
        fixtures.add_member(lo)
        reporting = self.configure(
            scope,
            {
                "quorum_form": "MAJORITY",
                "quorum_value": None,
                "reporting": "TOTALS_ONLY",
            },
            current,
        )
        for member in people[:3]:
            self.cast(journey, ballot, member)
        self.leave(lo, people[3])
        first = self.closed(journey, ballot)
        cause = self.facts(first["post_uuid"])["request"]["cause"]
        self.assertEqual((cause["kind"], cause["ballot"]), ("MEMBERSHIP", str(ballot)))
        self.assertTrue(first["restarted"])
        status = cq_reads.vote_status(**journey)
        second = status["ballot"]
        self.assertNotEqual(second, ballot)
        self.assertEqual(self.facts(second)["request"]["cause"], cause)
        self.assertEqual(first["reporting_rule"]["value"], "NAMED")
        self.assertEqual(status["reporting_rule"]["version"], str(reporting))
        self.assertEqual(
            (status["attempt"], status["eligible"], status["participated"]), (2, 3, 0)
        )
        self.assertEqual(
            self.facts(second)["facts"]["previous_result"], str(first["post_uuid"])
        )
        self.refused(
            "CQ_VOTE_CLOSED",
            "This vote is closed.",
            self.cast,
            journey=journey,
            ballot=ballot,
            member=people[0],
        )
        for member in people[:3]:
            self.cast(journey, second, member, "NOT_QUALIFIED")
        last = self.closed(journey, second)
        self.assertNotIn("participants", last)
        self.assertEqual(
            (
                last["restarted"],
                last["count"]["kind"],
                last["count"]["required"],
                last["count"]["qualified"],
            ),
            (False, "NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION", 5, 0),
        )
        self.assertEqual(
            [p["post_number"] for p in posts.read_thread(**journey)["posts"]], [1, 2, 3]
        )
        self.assertEqual(
            cq.catch_up(credential=people[0].credential), "Nothing is due."
        )
        successor, _, topic = self.topic(scope)
        cq.put_forward(
            **successor,
            version_uuid=topic["version"],
            previous_thread=journey["thread_uuid"],
            request_uuid=uuid4(),
        )
        self.assertEqual(cq.phase(**successor)["phase"], "PREPARATION")
        with self.assertRaises(disc.DiscussionUnavailable) as missing:
            cq_reads.vote_status(**successor)
        self.assertEqual(
            str(missing.exception), "This discussion is unavailable to you."
        )

    def test_july_reporting_rule_and_decision_trace(self):
        """DISC2-16. Any result reader follows its exact change and ILC basis, without answer data."""
        lo, scope, people, enabled = self.setup_board(3, beta=True, changes={"clarity_stage": False})
        changed = self.configure(scope, {"reporting": "TOTALS_ONLY"}, enabled)
        journey, ballot = cq_settings_support.prepared_ballot(self, scope)
        for member in people:
            self.cast(journey, ballot, member)
        view = journey | {"credential": people[2].credential}
        result = self.closed(view, ballot)
        self.assertEqual(result["reporting_rule"]["version"], str(changed))
        self.assertEqual(
            result["reporting_link_label"], "Names not shown · View reporting decision"
        )
        trace = cq_reads.reporting_decision(**view, result_uuid=result["post_uuid"])
        fact = self.facts(changed)
        self.assertEqual(
            (
                trace["origin"],
                trace["organism_uuid"],
                trace["decision"],
                trace["version"],
            ),
            ("LO", lo.organism_id, UUID(fact["request"]["result"]), str(changed)),
        )
        self.assertEqual(
            (trace["previous_value"], trace["value"], trace["scope"]),
            ("NAMED", "TOTALS_ONLY", "ENFORCED"),
        )
        self.assertEqual(
            (
                trace["actor_identity_uuid"],
                trace["enterer_identity_uuid"],
                trace["authority_source"]["reference"],
            ),
            (people[0].identity_id, people[0].identity_id, fact["request"]["result"]),
        )
        self.assertEqual(trace["authority_source"]["kind"], "CQ_RESULT")
        self.assertEqual(trace["authority_source"]["result_post"], fact["request"]["result"])
        self.assertEqual(trace["effective_from"], fact["effective_at"])
        self.assertEqual(
            trace["evidence_reference"], "cq-settings-result:" + fact["request"]["result"]
        )
        self.assertLessEqual(trace["received_at"], trace["recorded_at"])
        self.assertEqual(
            (trace["previous_fingerprint"], trace["value_fingerprint"]),
            (
                fact["facts"]["reporting_rule"]["old_fingerprint"],
                fact["facts"]["reporting_rule"]["new_fingerprint"],
            ),
        )
        self.assertNotEqual(trace["previous_fingerprint"], trace["value_fingerprint"])
        self.assertEqual(
            set(trace),
            {
                "origin",
                "organism_uuid",
                "version",
                "predecessor",
                "previous_value",
                "value",
                "previous_scope",
                "scope",
                "previous_fingerprint",
                "value_fingerprint",
                "actor_identity_uuid",
                "enterer_identity_uuid",
                "authority_source",
                "received_at",
                "occurred_at",
                "act_effective_at",
                "effective_from",
                "recorded_at",
                "decision",
                "authority_decision_reference",
                "evidence_reference",
            },
        )
        self.assertNotIn(str(people[2].identity_id), json.dumps(trace, default=str))
        # The link accepts a result in this thread, never a stand-alone settings ID.
        with self.assertRaises(disc.DiscussionUnavailable) as invalid:
            cq_reads.reporting_decision(**view, result_uuid=changed)
        self.assertEqual(
            str(invalid.exception), "This discussion is unavailable to you."
        )

    def test_reporting_switch_mid_vote_and_permanent_totals_only(self):
        """DISC2-17. Two simultaneous ballots flank the change and preserve independent reporting."""
        _, scope, people, setting = self.setup_board(3, beta=True)
        march, _ = self.confirmed(scope, people)
        july, _ = self.confirmed(scope, people)
        first = cq.finish_clarity(**march, request_uuid=uuid4())
        changed = self.configure(scope, {"reporting": "TOTALS_ONLY"}, setting)
        second = cq.finish_clarity(**july, request_uuid=uuid4())
        self.cast(july, second, people[0], "NOT_QUALIFIED")
        status = cq_reads.vote_status(**july)
        self.assertEqual((status["participated"], status["eligible"]), (1, 3))
        self.assertNotIn("participation", status)
        self.assertNotIn("result", status)
        for member in people:
            self.cast(march, first, member)
        for member in people[1:]:
            self.cast(july, second, member)
        named = self.closed(march, first)
        before = self.closed(july, second)
        self.assertEqual(
            (named["reporting_rule"]["value"], len(named["participants"])), ("NAMED", 3)
        )
        self.assertEqual(
            (before["reporting_rule"]["version"], before["count"]["not_qualified"]),
            (str(changed), 1),
        )
        self.assertNotIn("participants", before)
        restored = self.configure(scope, {"reporting": "NAMED"}, changed)
        record = cq.settings_history(**scope)[-1]
        self.assertEqual((record["act_uuid"], record["authority_source"]["counting_rule"]["reporting"]),
                         (restored, "TOTALS_ONLY"))
        self.assertNotIn("actor_id", record)
        after = self.closed(july, second)
        self.assertEqual(after, before)
        self.assertEqual(posts.read_thread(**july)["posts"][-1], after)
        self.assertEqual(
            cq_reads.reporting_decision(**july, result_uuid=after["post_uuid"])[
                "value"
            ],
            "TOTALS_ONLY",
        )
        for member in people:
            self.assertNotIn(str(member.identity_id), json.dumps(after, default=str))

    def test_circle_probation_access_and_reference_audience(self):
        """DISC2-18. Forum-only probationer may discuss and read an authorised result and trace."""
        lo, owner, scope = self.owner()
        self.configure(scope, {"enabled": True})
        disc.set_probation_access(**scope, enabled=True, request_uuid=uuid4())
        circle, leader = discussion_support.independent_circle(lo, owner)
        local = scope | {
            "circle_uuid": circle.circle_uuid,
            "credential": leader.credential,
        }
        people = [leader]
        for _ in range(2):
            member = fixtures.add_member(lo)
            row = models.OrganismMembership.objects.get(
                identity=member, living_organism=lo
            )
            join = circle_commands.request_place(
                **(local | {"credential": member.credential}),
                membership_uuid=row.membership_uuid,
                request_uuid=uuid4(),
                consent="Discuss Circle business",
            )
            circle_commands.admit(**local, request_uuid=join.request_uuid)
            people.append(member)
        probation = fixtures.add_member(lo)
        row = models.OrganismMembership.objects.get(
            identity=probation, living_organism=lo
        )
        self.fx = SimpleNamespace(
            actor=owner, ids={"basis": lo.founding_authority_basis_id}
        )
        departure.DepartureTests._state(
            self,
            row,
            "core_organismmembership",
            "core_organismmembershiptransition",
            "PROBATIONARY",
        )
        disc.ensure_board(**local, request_uuid=uuid4())
        disc.open_circle_welcome(**local, request_uuid=uuid4())
        journey, category = self.confirmed(local, people, category="WELCOME")
        ballot = cq.finish_clarity(**journey, request_uuid=uuid4())
        reader = journey | {"credential": probation.credential}
        posts.reply(
            **reader,
            category_uuid=category,
            post_uuid=uuid4(),
            request_uuid=uuid4(),
            markdown="Thank you for clarifying.",
        )
        self.assertEqual(cq_reads.vote_status(**reader)["eligible"], 3)
        self.refused(
            "CQ_PROBATION_VOTE",
            "Probationary members may discuss but cannot vote.",
            self.cast,
            journey=journey,
            ballot=ballot,
            member=probation,
        )
        for member in people:
            self.cast(journey, ballot, member)
        post = self.closed(reader, ballot)
        self.assertEqual(
            cq_reads.reporting_decision(**reader, result_uuid=post["post_uuid"])[
                "origin"
            ],
            "PLATFORM",
        )
        # The founder retains the LO office, and has no current place in this Circle.
        outsider = support.register_test_learner(
            username="cq-result-outsider-" + uuid4().hex
        )
        for target in (
            journey | {"credential": outsider.credential},
            journey | {"credential": owner.credential},
            journey | {"thread_uuid": uuid4()},
        ):
            with self.assertRaises(disc.DiscussionUnavailable) as denied:
                cq_reads.reporting_decision(**target, result_uuid=post["post_uuid"])
            self.assertEqual(
                str(denied.exception), "This discussion is unavailable to you."
            )

        # Ending a Circle place can also settle the vote. The person remains an
        # active member of the LO but is no longer counted on the Circle ballot.
        live, _ = self.confirmed(local, people, category="WELCOME")
        second = cq.finish_clarity(**live, request_uuid=uuid4())
        self.cast(live, second, people[0])
        leaving = people[2]
        departed_answer = self.cast(live, second, leaving, "NOT_QUALIFIED")
        place = models.CircleParticipation.objects.get(
            circle=circle, membership__identity=leaving
        )
        circle_commands.leave(
            **(local | {"credential": leaving.credential}),
            participation_uuid=place.participation_uuid,
            request_uuid=uuid4(),
            confirmed=True,
        )
        loss = self.closed(live, second)
        self.assertEqual(
            (
                loss["count"]["kind"],
                loss["count"]["eligible"],
                loss["count"]["participated"],
            ),
            ("NO_DECISION_FEWER_THAN_THREE", 2, 1),
        )
        self.assertEqual(loss["count"]["not_qualified"], 0)
        self.assertTrue(
            models.LivingOrganismEvent.objects.filter(
                event_uuid=departed_answer
            ).exists()
        )
        self.assertEqual(
            self.facts(loss["post_uuid"])["request"]["cause"]["kind"], "CIRCLE_PLACE"
        )

    def test_atomic_result_integrity_and_populated_reversal(self):
        """DISC2-19. A populated forum/CQ journey keeps its committed history through rollback and reversal.

        Reversal cases from test_history_retirement_guards_rollback_and_reversal (0072),
        test_guards_rollback_request_scope_and_populated_reversal (0073) and
        test_access_refusal_rollback_and_populated_reversal (0074) join 0075.
        """
        _, scope, people, settings_version = self.setup_board(3)
        journey, category_id = self.confirmed(scope, people)
        ballot = cq.finish_clarity(**journey, request_uuid=uuid4())
        for member in people[:2]:
            self.cast(journey, ballot, member)
        key = uuid4()
        with transaction.atomic():
            self.cast(journey, ballot, people[2], key=key)
            result = self.closed(journey, ballot)["post_uuid"]
            self.assertEqual(
                models.LivingOrganismEvent.objects.filter(
                    event_uuid__in=[result, key]
                ).count(),
                2,
            )
            transaction.set_rollback(True)
        self.assertFalse(
            models.LivingOrganismEvent.objects.filter(
                event_uuid__in=[key, result]
            ).exists()
        )
        self.assertEqual(cq_reads.vote_status(**journey)["participated"], 2)
        self.cast(journey, ballot, people[2], key=key)
        result = self.closed(journey, ballot)["post_uuid"]
        self.assertEqual(self.facts(result)["authority_source"]["kind"], "RULE")
        with (
            self.assertRaises(IntegrityError) as forged,
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """INSERT INTO public.core_cqact(act_uuid,organism_id,board_uuid,thread_uuid,operation,
                  actor_id,actor_epoch,received_at,request,facts) SELECT %s,organism_id,board_uuid,thread_uuid,
                  operation,actor_id,actor_epoch,statement_timestamp(),request,facts FROM public.core_cqact WHERE act_uuid=%s""",
                [uuid4(), result],
            )
        self.assertEqual(forged.exception.__cause__.diag.message_primary, "CQ_COMPLETE")
        def category_versions():
            return [row["version_uuid"] for row in disc.category_history(**scope, category_uuid=category_id)]

        def opening_body():
            return posts.read_thread(**journey)["posts"][0]["markdown"]

        versions = category_versions()
        self.assertTrue(versions, "The functional category needs its recorded provisioning version.")
        checks = (
            ("0072_discussion_boards", "DISC1 reversal refused while discussion history exists.",
             category_versions, versions),
            ("0073_discussion_posts", "DISC1 P2 reversal refused while post history exists.",
             opening_body, "Open on Saturdays"),
            ("0074_cq_clarity", "DISC2 reversal refused while CQ history exists.",
             lambda: cq.settings(**scope)["version"], settings_version),
            ("0075_cq_decisions", "DISC2 vote reversal refused while ballot history exists.",
             lambda: self.closed(journey, ballot)["post_uuid"], result),
        )
        for module, refusal, read_back, expected in checks:
            with self.subTest(reversal=module):
                migration = import_module("core.migrations." + module)
                with connection.schema_editor(atomic=True) as editor, self.assertRaises(RuntimeError) as stopped:
                    migration.reverse(None, editor)
                self.assertEqual(str(stopped.exception), refusal)
                self.assertEqual(read_back(), expected)

        hard = import_module("core.migrations.0081_hard_voting_authority")
        with connection.schema_editor(atomic=True) as editor, connection.cursor() as cursor:
            hard.uninstall(None, editor)
            self.assertIsNone(hard.installed(cursor, hard.SELECTOR))
            for signature, previous, _ in hard.edits():
                self.assertEqual(hard.installed(cursor, signature), hard.body(previous), signature)
            self.assertEqual(self.closed(journey, ballot)["post_uuid"], result)
            hard.install(None, editor)
            for signature, _, replacement in hard.edits():
                self.assertEqual(hard.installed(cursor, signature), hard.body(replacement), signature)
            self.assertEqual(hard.installed(cursor, hard.SELECTOR), hard.body(hard.SELECTOR_SQL))
        self.assertEqual(self.closed(journey, ballot)["post_uuid"], result)

    test_atomic_result_integrity_and_populated_reversal.ISOLATION_FULL_CAPTURE = True

    def test_governance_result_audience_for_unseated_member(self):
        """DISC2-20. An LO member without a seat can follow a GOVERNANCE result and its rule."""
        lo, scope, people, circle = board_support.ordinary(3, board=True)
        viewer = fixtures.add_member(lo)
        self.configure(scope, {"enabled": True})
        board = scope | {"circle_uuid": circle.circle_uuid}
        circle_commands.activate(**board, request_uuid=uuid4())
        officer = people[1]
        board_support.appoint_board_coordinator(lo, circle, people[0], officer)
        board["credential"] = officer.credential
        outsider = support.register_test_learner(
            username="disc2-external-" + uuid4().hex
        )

        category_id = None

        def begin():
            nonlocal category_id
            journey, category_id, topic = self.topic(board, category_id=category_id)
            cq.put_forward(
                **journey, version_uuid=topic["version"], request_uuid=uuid4()
            )
            window = cq.mark_prepared(**journey, request_uuid=uuid4())
            return (
                journey,
                journey | {"credential": viewer.credential},
                topic["version"],
                window,
            )

        journey, reader, wording, _ = begin()
        for read in (posts.read_thread, cq_reads.vote_status):
            with self.assertRaisesMessage(disc.DiscussionUnavailable, disc.UNAVAILABLE):
                read(**reader)
        for trustee in people:
            cq.confirm_clarity(
                **(journey | {"credential": trustee.credential}),
                version_uuid=wording,
                request_uuid=uuid4(),
            )
        ballot = cq.finish_clarity(**journey, request_uuid=uuid4())
        self.assertEqual(cq_reads.vote_status(**reader)["phase"], "VOTING")
        for trustee, choice in zip(
            people, ("QUALIFIED", "QUALIFIED", "NOT_QUALIFIED"), strict=True
        ):
            self.cast(journey, ballot, trustee, choice)
        result = self.closed(reader, ballot)
        self.assertEqual(
            (result["count"]["qualified"], result["count"]["not_qualified"]), (2, 1)
        )
        self.assertEqual(
            {p["identity"]: p["answer"] for p in result["participants"]},
            {
                str(p.identity_id): ("NOT_QUALIFIED" if p == people[2] else "QUALIFIED")
                for p in people
            },
        )
        rule = cq_reads.reporting_decision(**reader, result_uuid=result["post_uuid"])
        self.assertEqual(str(rule["version"]), result["reporting_rule"]["version"])
        for denied in (
            reader | {"credential": outsider.credential},
            reader | {"thread_uuid": uuid4()},
        ):
            with self.assertRaisesMessage(disc.DiscussionUnavailable, disc.UNAVAILABLE):
                cq_reads.result_post(**denied, result_uuid=result["post_uuid"])

        private, _, _ = self.topic(board, category="COORDINATION")
        with self.assertRaisesMessage(disc.DiscussionUnavailable, disc.UNAVAILABLE):
            cq_reads.result_post(
                **(private | {"credential": viewer.credential}),
                result_uuid=result["post_uuid"],
            )

        for operation, phase, kind in (
            ("NO_BALLOT", "RESULT", "NO_DECISION_FEWER_THAN_THREE"),
            ("CLOSE", "CLOSED", "NO_DECISION_COORDINATOR_CLOSED"),
        ):
            motion, observer, version, window = begin()
            if operation == "NO_BALLOT":
                cq.declare_conflict(
                    **(motion | {"credential": people[2].credential}),
                    basis="Related party",
                    request_uuid=uuid4(),
                )
                for trustee in people[:2]:
                    cq.confirm_clarity(
                        **(motion | {"credential": trustee.credential}),
                        version_uuid=version,
                        request_uuid=uuid4(),
                    )
                with clarity.elapsed_clarity(window, response_due=True):
                    cq.catch_up(credential=officer.credential)
            else:
                with clarity.elapsed_clarity(window, response_due=False):
                    cq.close_without_vote(**motion, request_uuid=uuid4())
            history = cq.phase(**observer)
            self.assertEqual(
                (history["phase"], [e["operation"] for e in history["phase_dates"]]),
                (phase, [operation]),
            )
            status = cq_reads.vote_status(**observer)
            outcome = status["result_uuid"]
            self.assertEqual(
                (status["ballot"], status["result"]["count"]["kind"]), (None, kind)
            )
            self.assertEqual(
                cq_reads.reporting_decision(**observer, result_uuid=outcome)["version"],
                rule["version"],
            )
            self.assertTrue(
                models.LivingOrganismEvent.objects.filter(event_uuid=outcome).exists()
            )
            with self.assertRaisesMessage(disc.DiscussionUnavailable, disc.UNAVAILABLE):
                cq_reads.result_post(
                    **(observer | {"credential": outsider.credential}),
                    result_uuid=outcome,
                )

    test_governance_result_audience_for_unseated_member.ISOLATION_FULL_CAPTURE = True
