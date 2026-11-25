"""Plan box 8a: the Topic's vote read gives one reader their own ballot state and the count only.

Each test seats its own Board of three through the real routes and opens one
ordinary Suggestion's vote. Nothing here renders new page content: box 8b does.
"""
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from django.test import SimpleTestCase, TransactionTestCase, override_settings

from core.models import OrganismMembership
from src.intevia.services import circle_commands, cq, cq_reads, discussion
from tests import cq_authority_support as boards
from tests import test_cq_clarity as clarity_support
from tests import test_discussion_posts as post_support
from tests.test_f0_ui import Markup
from tests.test_office_record_services import fixture_membership_state
from tests.test_uc1_office_page import signed_in
from tests.test_uc2_pages import FIXTURE_HASHERS, path

# Part PB-6d: every reader of the vote section also receives the ballot's recorded rule and the engine's required count.
COUNT_KEYS = {"ballot_uuid", "attempt", "options", "eligible", "answered", "rule", "required"}
OWN_KEYS = {"own_answer", "may_answer", "answer_refusal"}
FOR_AGAINST = ["QUALIFIED", "NOT_QUALIFIED"]


def open_board_vote(*, changes=None, overdue=False):
    """Seat a Board of three and open one Suggestion's vote with the Clarity stage off.

    The settings change is the founder's own, made before the Board is activated,
    exactly as ``tests.test_uc2_pages.activate_trustees`` makes it.
    """
    owner, scope, people, board = boards.ordinary(3, board=True)
    founder, coordinator, _ = people
    cq.change_settings(**scope, predecessor=None, request_uuid=uuid4(),
                       changes={"enabled": True, "clarity_stage": False, **(changes or {})})
    circle_commands.activate(**scope, circle_uuid=board.circle_uuid, request_uuid=uuid4())
    boards.appoint_board_coordinator(owner, board, founder, coordinator)
    support = post_support.DiscussionPostTests()
    args = support.board(owner, coordinator, board)
    topic = support.start(args, topic_type="SUGGESTION", markdown="Approve one payment.")
    scope = support.read_args(args) | {"thread_uuid": topic["thread"]}
    cq.put_forward(**scope, request_uuid=uuid4(), version_uuid=topic["version"])
    if overdue:
        # The ballot's computed deadline is its own opening time, so it is due at once.
        with clarity_support.due_at_phase_start():
            cq.mark_prepared(**scope, request_uuid=uuid4())
    else:
        cq.mark_prepared(**scope, request_uuid=uuid4())
    return SimpleNamespace(owner=owner, board=board, people=people, scope=scope,
                           thread=topic["thread"], url=path(owner, board, topic["thread"]))


def recorded_acts(thread):
    """Every vote act on record, and this Topic's current head."""
    acts = discussion._rows("SELECT count(*) AS acts, max(ordinal) AS last FROM public.core_cqact")[0]
    head = discussion._rows("SELECT act_uuid, phase FROM public.cq_phases WHERE thread_uuid=%s", [thread])
    return acts, head


class OpenBallotGuardTests(SimpleTestCase):
    """B8A-04 (Advisor finding 2). The own-state guard in ``_open_ballot``, reached with no database."""

    BALLOT = uuid4()

    def project(self, clarity_reader):
        row = {"read_at": 1, "ballot_uuid": self.BALLOT, "clarity_reader": clarity_reader}
        counted = [{"attempt": 1, "abstention": False, "eligible": 3, "answered": 2, "own_answer": None,
                    "rule": {"threshold": "MAJORITY"}, "required": 3}]
        with mock.patch.object(cq.disc, "_rows", return_value=counted), \
                mock.patch.object(cq, "_answer_refusal", return_value=None) as probe:
            ballot = cq._open_ballot({"board_uuid": uuid4()}, uuid4(), SimpleNamespace(pk=7), row)
        return ballot, probe.call_count

    def test_b8a_04_only_an_active_or_probationary_reader_receives_own_ballot_state(self):
        hidden, probes = self.project(False)
        self.assertEqual((hidden["ballot_uuid"], hidden["eligible"], hidden["answered"]), (self.BALLOT, 3, 2),
                         "A read-only reader still receives this ballot's count.")
        self.assertEqual(set(hidden), COUNT_KEYS, "A read-only reader receives the count and nothing of its own.")
        self.assertEqual(probes, 0, "A read-only reader's answer permission must not even be probed.")
        own, probes = self.project(True)
        self.assertEqual(set(own), COUNT_KEYS | OWN_KEYS, "An active or probationary reader receives their own state.")
        self.assertEqual(probes, 1, "Their answer permission is probed once.")


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS)
class BallotReadTests(TransactionTestCase):
    """B8A-01 to B8A-03 (plan rows D09, D14 and S13, for the read)."""

    def as_reader(self, case, person):
        return case.scope | {"credential": person.credential}

    def panel(self, case, person):
        return cq.board_topic_vote(**self.as_reader(case, person))

    def ballot(self, case, person):
        panel = self.panel(case, person)
        self.assertEqual(panel["phase"], "VOTING", "The fixture must leave this Topic with a ballot as its head.")
        self.assertIn("ballot", panel, "A Board member reading a Topic with an open ballot must receive its state.")
        return panel["ballot"]

    def answer(self, case, person, ballot, choice):
        cq.answer(**self.as_reader(case, person), ballot_uuid=ballot, choice=choice, request_uuid=uuid4())

    def test_b8a_01_ballot_projects_only_own_answer_and_captured_options(self):
        """D09. Each member sees their own answer alone; a fresh attempt starts unanswered."""
        case = open_board_vote(changes={"abstention": True, "low_turnout": "NEW_CQ",
                                        "quorum_form": "ABSOLUTE", "quorum_value": 5})
        founder, coordinator, trustee = case.people
        first = self.ballot(case, founder)
        self.assertEqual(first["options"], FOR_AGAINST + ["ABSTAIN"],
                         "A ballot opened under rules that allow abstention must offer Abstain after For and Against.")
        self.assertEqual((first["attempt"], first["eligible"], first["answered"]), (1, 3, 0),
                         "Before anyone answers, the first attempt must count 0 of 3.")
        self.assertEqual((first["own_answer"], first["may_answer"], first["answer_refusal"]), (None, True, None),
                         "An eligible trustee who has not answered must be told they may answer.")
        self.answer(case, founder, first["ballot_uuid"], "QUALIFIED")
        self.answer(case, coordinator, first["ballot_uuid"], "ABSTAIN")
        expected = {founder: ("QUALIFIED", False, "CQ_DUPLICATE"),
                    coordinator: ("ABSTAIN", False, "CQ_DUPLICATE"),
                    trustee: (None, True, None)}
        for person, own in expected.items():
            seen = self.ballot(case, person)
            self.assertEqual((seen["own_answer"], seen["may_answer"], seen["answer_refusal"]), own,
                             "Each reader must receive their own answer only, and may not answer twice.")
            self.assertEqual((seen["ballot_uuid"], seen["eligible"], seen["answered"]),
                             (first["ballot_uuid"], 3, 2), "Every reader must see the same count: 2 of 3 answered.")
        # Three answers cannot reach this ballot's floor of five, so the rule opens the second attempt.
        self.answer(case, trustee, first["ballot_uuid"], "NOT_QUALIFIED")
        for person in case.people:
            fresh = self.ballot(case, person)
            self.assertNotEqual(fresh["ballot_uuid"], first["ballot_uuid"],
                                "After the rule's re-run the read must name the new ballot, not the settled one.")
            self.assertEqual((fresh["attempt"], fresh["eligible"], fresh["answered"]), (2, 3, 0),
                             "The second attempt must start at 0 of 3.")
            self.assertEqual((fresh["own_answer"], fresh["may_answer"], fresh["answer_refusal"]), (None, True, None),
                             "An answer belongs to its own ballot: on the fresh attempt nobody has answered yet.")
            self.assertEqual(fresh["options"], FOR_AGAINST + ["ABSTAIN"],
                             "The re-run keeps the options its first ballot captured.")

    def test_b8a_02_count_only_and_no_participant_name_while_voting_is_open(self):
        """D14. Under NAMED reporting the read and the page still carry the count and no name."""
        case = open_board_vote()
        founder, coordinator, trustee = case.people
        names = [person.display_name for person in case.people]
        self.assertTrue(all(names), "Each fixture trustee needs a display name for this witness to mean anything.")
        self.answer(case, founder, self.ballot(case, founder)["ballot_uuid"], "QUALIFIED")
        landed = cq_reads.vote_status(**case.scope)
        self.assertEqual(landed["reporting_rule"]["value"], "NAMED",
                         "This witness needs the platform default, which reports by name.")
        self.assertEqual(sorted(entry["name"] for entry in landed["participation"]), sorted(names),
                         "The existing status read must hold the names, so that withholding them here is a real choice.")
        withheld = names + [str(person.identity_id) for person in case.people]
        for person in case.people:
            panel = self.panel(case, person)
            found = [value for value in withheld if value in repr(panel)]
            self.assertEqual(found, [], "While voting is open the Topic's vote read must not carry any participant's "
                                        "name or identity, whatever the reporting rule.")
            self.assertEqual(set(panel["ballot"]), COUNT_KEYS | OWN_KEYS,
                             "The open ballot's read holds the count and the reader's own state, and nothing else.")
            self.assertEqual((panel["ballot"]["eligible"], panel["ballot"]["answered"]), (3, 1),
                             "The count must read 1 of 3 answered.")
            self.assertEqual(panel["ballot"]["options"], FOR_AGAINST,
                             "A ballot opened under the default rules offers For and Against only.")
        for reader in (founder, trustee):
            page = Markup(signed_in(reader).get(case.url).content.decode())
            section, = page.select("section", id="vote")
            self.assertEqual([name for name in names if name in section["text"]], [],
                             "The Topic's vote section must not name who has or has not answered while voting is open.")
        # A read-only membership is witnessed in B8A-03, where the ballot still heads the Topic,
        # and without a database in B8A-04 (Advisor finding 2).

    @override_settings(INTEVIA_TIMED_EVENTS_WAKE=False)
    def test_b8a_03_vote_reads_record_nothing_when_a_result_is_due(self):
        """S13. With the wake off, reading an overdue vote leaves it exactly as it was."""
        case = open_board_vote(overdue=True)
        founder, coordinator, trustee = case.people
        self.assertEqual(cq_reads.vote_status(**case.scope)["phase"], "AWAITING_SETTLEMENT",
                         "The fixture must leave a vote past its deadline with no result recorded.")
        before = recorded_acts(case.thread)
        for person in case.people:
            ballot = self.ballot(case, person)
            self.assertEqual((ballot["may_answer"], ballot["answer_refusal"]), (False, "CQ_VOTE_CLOSED"),
                             "After the deadline nobody may answer, and the reason is that the vote is closed.")
            self.assertEqual((ballot["eligible"], ballot["answered"]), (3, 0),
                             "The count at the deadline must still be readable: 0 of 3 answered.")
            self.assertEqual(recorded_acts(case.thread), before,
                             "Reading the Topic's vote must record nothing when a result is due.")
        cq.phase(**case.scope)
        client = signed_in(trustee)
        for url in (case.url, path(case.owner, case.board)):
            self.assertEqual(client.get(url).status_code, 200, "The Topic and the Board's discussion list must open.")
        self.assertEqual(recorded_acts(case.thread), before,
                         "Opening the pages of a vote whose result is due must record nothing and leave its head unchanged.")
        self.assertTrue(self.panel(case, trustee).get("result_due"),
                        "An active Board member is offered the press that records the due result.")
        # Advisor finding 2. The landed fixture for a read-only membership (a guarded, test-only
        # transition), read while the overdue ballot still heads the Topic.
        fixture_membership_state(case.owner, founder, trustee, OrganismMembership.State.SUSPENDED)
        restricted = self.panel(case, trustee)
        self.assertIn("ballot", restricted or {}, "A suspended Board member must still be shown the ballot that heads the Topic.")
        self.assertEqual(set(restricted["ballot"]), COUNT_KEYS,
                         "A read-only membership receives the count and none of its own ballot state.")
        # Advisor box 8 part 2 (8c gate): a suspended reader is not offered the press.
        self.assertNotIn("result_due", restricted, "Only an active member is offered the press that records a due result.")
        shown = signed_in(trustee).get(case.url)
        self.assertEqual(shown.status_code, 200, "A suspended Board member can still open the Topic.")
        self.assertEqual([b for b in Markup(shown.content.decode()).select("button") if b["text"].strip() == "Record the result"],
                         [], "A suspended Board member must not be offered the button.")
        self.assertEqual(cq.catch_up(credential=founder.credential), "Due work recorded.",
                         "The result really was due: the explicit call must record it.")
        self.assertNotEqual(recorded_acts(case.thread), before, "The explicit call, and only it, moves the vote on.")
        self.assertNotIn("ballot", self.panel(case, trustee),
                         "Once the result is recorded there is no open ballot to project.")
