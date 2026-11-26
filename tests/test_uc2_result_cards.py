"""The posts box, part PB-6: the member's own answer card, the result's time word and the danger card.

Rulings of 24 Nov 2026: capture AH (Q15: a result with no decision reads "Result recorded"; Q18:
the member's own answer on a card in the answer's colour), capture AO (AO1: Abstain is amber),
captures AP and AQ (AP2: the cards as drawn; AQ2: while voting is open INTEVIA's confirmation is
merged into the answer card).

Part PB-6a: PB6A-01 to PB6A-04, with no database. Part PB-6b (captures AP1, AQ1, AQ3, AQ4, AR1, AR2):
PB6B-01 to PB6B-05 with no database, and PB6B-06 on the real pages. PB6B-06's fixture is built through
the real services, rows per test, with no owner or superuser power.
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import resolve, reverse

from core import discussion_pages, discussion_vote
from core.ui import danger_card, own_answer_card
from src.intevia.services import cq_reads
from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import signed_in
from tests.test_uc2_closed_vote_ring import closed_board_vote
from tests.test_uc2_pages import FIXTURE_HASHERS

WHEN = datetime(2026, 11, 24, 19, 1, 20, tzinfo=timezone.utc)
ANSWERS = (("QUALIFIED", "For", "success"), ("NOT_QUALIFIED", "Against", "refusal"), ("ABSTAIN", "Abstain", "warning"))
TEAL_LINE = "Your answer is recorded."


def vote_section(**row):
    """The Topic's vote section as the page builds it from the vote read, with no database."""
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = mock.Mock(session_key="reader")
    with mock.patch.object(discussion_vote.cq, "board_topic_vote", return_value=row):
        vote = discussion_vote.panel(request, uuid4(), uuid4(), uuid4())
    page = Markup(render_to_string("core/components/board_vote.html", {"vote": vote}))
    section, = page.select("section", id="vote")
    return page, section


def own_cards(page, section):
    return [(card["text"].strip(), card["attrs"]["class"].split(), card["attrs"].get("role"))
            for card in page.select("div", within=section, id="own-answer")]


def teal_lines(page, section):
    return [p["text"].strip() for notice in page.select("div", within=section, classes=("notice", "vote-feedback"))
            for p in page.select("p", within=notice)]


class OwnAnswerCardTests(SimpleTestCase):
    """PB6A-01 and PB6A-02: the member's own answer card, while voting is open and after it closes."""

    def open_vote(self, **ballot):
        return vote_section(phase="VOTING", read_at=WHEN, deadline=WHEN + timedelta(days=3), ballot_uuid=uuid4(),
                            ballot={"ballot_uuid": uuid4(), "answered": 2, "eligible": 4, "options": ["QUALIFIED", "NOT_QUALIFIED"],
                                    "may_answer": False, "answer_refusal": None} | ballot)

    def test_pb6a_01_while_voting_is_open_one_card_confirms_and_states_the_answer_in_its_colour(self):
        """Captures AH Q18 and AQ2. One card per answer: green, red or amber; INTEVIA's teal line is merged into it."""
        for choice, label, kind in ANSWERS:
            with self.subTest(choice=choice):
                page, section = self.open_vote(own_answer=choice)
                self.assertEqual(own_cards(page, section),
                                 [(f"Your answer is recorded. You answered {label}.", ["notice", kind], "status")],
                                 "While voting is open the member must read one card, in the colour of their answer.")
                self.assertEqual(teal_lines(page, section), [],
                                 "INTEVIA's separate confirmation must be gone: the answer card carries it.")
        page, section = self.open_vote(own_answer=None, answer_refusal="CQ_SAT_OUT")
        self.assertEqual(own_cards(page, section), [], "A member who has not answered has no answer card.")
        self.assertEqual(teal_lines(page, section),
                         ["You did not confirm that you were clear on this wording, so you sit out this vote."],
                         "The vote section's other feedback is unchanged.")

    def test_pb6a_02_after_the_vote_closes_the_card_states_the_answer_alone(self):
        """Captures AH Q18 and AQ2. After the result the card reads "You answered For." in its colour, with no teal line."""
        for choice, label, kind in ANSWERS:
            with self.subTest(choice=choice):
                page, section = vote_section(phase="RESULT", outcome="QUALIFIED", ballot_uuid=uuid4(), closed_at=WHEN,
                                             effective_at=WHEN, decided_own_answer=choice)
                self.assertEqual(own_cards(page, section), [(f"You answered {label}.", ["notice", kind], "status")],
                                 "After the vote closes the card must state the answer alone, in its colour.")
                self.assertEqual(teal_lines(page, section), [], "After the vote closes no confirmation line is shown.")
        page, section = vote_section(phase="RESULT", outcome="QUALIFIED", ballot_uuid=uuid4(), closed_at=WHEN, effective_at=WHEN)
        self.assertEqual(own_cards(page, section), [], "A member who did not answer has no answer card after the result.")
        self.assertIsNone(own_answer_card("SOMETHING_ELSE", voting_open=True), "An answer the ballot does not offer has no card.")


class AnswerCardAtTheDeadlineTests(SimpleTestCase):
    """PB9-04 (HG capture BK of 25 Nov 2026; GPT Astra's posts-box part 2 review, P2-2): the card's wording follows the deadline."""

    def at(self, seconds, own, **row):
        """The vote section read ``seconds`` from the deadline, by a member whose own answer is ``own``."""
        deadline = WHEN + timedelta(days=3)
        ballot = {"ballot_uuid": uuid4(), "answered": 3, "eligible": 4, "options": ["QUALIFIED", "NOT_QUALIFIED"],
                  "may_answer": False, "answer_refusal": None, "own_answer": own}
        return vote_section(phase="VOTING", read_at=deadline + timedelta(seconds=seconds), deadline=deadline, ballot_uuid=uuid4(),
                            ballot=ballot, **row)

    def test_pb9_04_the_answer_card_is_open_worded_before_the_deadline_and_closed_worded_from_it(self):
        """PB9-04. One second before: "Your answer is recorded. You answered For." At the deadline and after, result still due: "You answered For." """
        page, section = self.at(-1, "QUALIFIED")
        self.assertEqual(own_cards(page, section), [("Your answer is recorded. You answered For.", ["notice", "success"], "status")],
                         "One second before the deadline voting is open, so the card must carry INTEVIA's confirmation.")
        for seconds in (0, 1, 3600):
            with self.subTest(seconds_after_the_deadline=seconds):
                page, section = self.at(seconds, "QUALIFIED", result_due=True)
                self.assertEqual(own_cards(page, section), [("You answered For.", ["notice", "success"], "status")],
                                 "From the deadline on, while the result is still due, the card must state the answer alone.")
                self.assertEqual(page.select("div", within=section, classes=("participation-summary",)), [],
                                 "From the deadline on the live ring is gone, so the card must not still speak as if voting were open.")
        for seconds in (-1, 0, 1):
            with self.subTest(never_answered_at=seconds):
                page, section = self.at(seconds, None, result_due=seconds >= 0)
                self.assertEqual(own_cards(page, section), [], "A member who never answered must have no answer card.")

    def test_pb9_04b_the_due_result_can_still_be_recorded_by_a_member_who_answered(self):
        """PB9-04. The closed wording must not take away the way to record a due result."""
        request = RequestFactory().get("/")
        request.user, request.session = AnonymousUser(), mock.Mock(session_key="reader")
        deadline = WHEN + timedelta(days=3)
        row = {"phase": "VOTING", "read_at": deadline, "deadline": deadline, "ballot_uuid": uuid4(), "result_due": True,
               "ballot": {"ballot_uuid": uuid4(), "answered": 3, "eligible": 4, "options": ["QUALIFIED", "NOT_QUALIFIED"],
                          "may_answer": False, "answer_refusal": None, "own_answer": "QUALIFIED"}}
        org, circle, thread = uuid4(), uuid4(), uuid4()
        with mock.patch.object(discussion_vote.cq, "board_topic_vote", return_value=row):
            vote = discussion_vote.panel(request, org, circle, thread)
        self.assertEqual(vote["own_answer_card"]["text"], "You answered For.", "At the deadline the card must state the answer alone.")
        self.assertEqual(vote.get("due_url"), reverse("discussion_record_result", args=(org, circle, thread)),
                         "A member who answered must still be led to record the due result.")


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS, INTEVIA_TIMED_EVENTS_WAKE=False)
class AnswerCardAtTheDeadlineRealPageTests(TransactionTestCase):
    """PB9-06: the same on the real Topic, with a real ballot and the INTEVIA Clock moved to its deadline. Rows per test."""

    def test_pb9_06_a_member_who_answered_reads_the_closed_wording_once_the_deadline_passes(self):
        """PB9-06. Before the deadline the card confirms; at and after it, result still due, it states the answer alone."""
        from intevia.test_postgresql_backend import clock
        from src.intevia.services import cq
        from tests.test_uc2_ballot_reads import open_board_vote
        case = open_board_vote()
        answered, silent = case.people[0], case.people[2]
        ballot = cq_reads.vote_status(**case.scope)["ballot"]
        cq.answer(**(case.scope | {"credential": answered.credential}), ballot_uuid=ballot, choice="QUALIFIED", request_uuid=uuid4())
        deadline = cq.board_topic_vote(**(case.scope | {"credential": answered.credential}))["deadline"]

        def cards(reader):
            # A session lasts eight hours on the INTEVIA Clock (core/session.py) and the ballot is open for days,
            # so each reader signs in after the clock has moved, as the closed-vote witnesses do (PB9-R1).
            response = signed_in(reader).get(case.url)
            self.assertEqual(response.status_code, 200, "A signed-in Board member must be shown the Topic, not sent to sign in.")
            page = Markup(response.content.decode())
            section, = page.select("section", id="vote")
            return [words for words, _, _ in own_cards(page, section)]

        clock.advance_to(deadline - timedelta(minutes=1))
        self.assertEqual(cards(answered), ["Your answer is recorded. You answered For."],
                         "A minute before the deadline voting is open, so the card must carry INTEVIA's confirmation.")
        for onward in (timedelta(minutes=1), timedelta(seconds=1)):  # to the deadline, then a second past it
            clock.advance(onward)  # by a duration, always forward: the clock also runs while pages load (PC1 R3)
            self.assertEqual(cards(answered), ["You answered For."],
                             "From the deadline on, with the result still due, the card must state the answer alone.")
            self.assertEqual(cards(silent), [], "A member who never answered must have no answer card.")
        self.assertEqual(cq.phase(**case.scope)["phase"], "VOTING", "The fixture's result must still be due: nothing recorded it.")


class ResultTimeWordTests(SimpleTestCase):
    """PB6A-03 and PB6A-04: a result with no decision reads "Result recorded" (capture AH, Q15)."""

    NO_DECISION = ("NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION", "NO_DECISION_FEWER_THAN_THREE", "NO_DECISION_COORDINATOR_CLOSED",
                   "NO_DECISION_CLARITY_THRESHOLD", "NO_DECISION_BOARD_ACTIVATED")
    DECIDED = ("QUALIFIED", "NOT_QUALIFIED_VOTE", "NOT_QUALIFIED_TIE")

    def result_page_rows(self, kind, **changes):
        org, circle, thread, result = (uuid4() for _ in range(4))
        address = reverse("discussion_result", args=(org, circle, thread, result))
        request = RequestFactory().get(address)
        request.user = AnonymousUser()
        request.resolver_match = resolve(address)
        record = {"post_uuid": result, "ballot": str(uuid4()), "count": {"kind": kind}, "markdown": "The outcome.",
                  "effective_at": WHEN, "recorded_at": WHEN + timedelta(minutes=4), "closed_at": "2026-11-24T09:00:00+00:00",
                  "deciding_body": {"full_name": "Board of Trustees", "short_label": "Board"}, "wording": "Approve one payment.",
                  "wording_version_number": 1, "carried_no_attached_act": False} | changes
        context = {"result": record, **discussion_pages._result_context(record)}
        page = Markup(render_to_string("core/discussion_result.html", context, request=request))
        rows, = [node for node in page.select("dl", classes=("office-facts",))
                 if "vote-names" not in node["attrs"]["class"].split()]
        return [node["text"].strip() for node in page.select("dt", within=rows)]

    def test_pb6a_03_the_result_page_names_a_no_decision_time_result_recorded_once(self):
        """A decided result keeps "Took effect" and its separate "Result recorded"; a no-decision result has one row, "Result recorded"."""
        for kind in self.DECIDED:
            with self.subTest(kind=kind):
                self.assertEqual(self.result_page_rows(kind),
                                 ["Deciding body", "Outcome", "Took effect", "Voting closed", "Result recorded"],
                                 "A decision that carried or was defeated keeps its rows as built.")
        for kind in self.NO_DECISION:
            with self.subTest(kind=kind):
                self.assertEqual(self.result_page_rows(kind), ["Deciding body", "Outcome", "Result recorded", "Voting closed"],
                                 "A result with no decision must name its time \"Result recorded\", once.")
                self.assertEqual(discussion_pages.result_time_word(kind), "Result recorded")
        record = {"count": {"kind": "NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION"}, "ballot": str(uuid4()), "effective_at": WHEN,
                  "recorded_at": WHEN + timedelta(minutes=4), "closed_at": "2026-11-24T09:00:00+00:00"}
        self.assertEqual(discussion_pages._result_context(record)["times"]["took_effect"], WHEN + timedelta(minutes=4),
                         "A no-decision result's \"Result recorded\" row must carry the time the result was recorded.")
        self.assertEqual(discussion_pages._result_context(record | {"count": {"kind": "QUALIFIED"}})["times"]["took_effect"], WHEN,
                         "A decided result's \"Took effect\" row keeps the result's effective time.")

    def test_pb6a_04_the_topics_result_post_takes_the_same_word(self):
        """The Topic's vote-result post reads the word the page's read gave it, and "Took effect" where it gave none."""
        org, circle, thread, category = (uuid4() for _ in range(4))
        opening = {"post_number": 1, "markdown": "Opening"}

        def result(kind, number):
            return {"post_number": number, "post_uuid": uuid4(), "post_type": "GROUP_GAZE", "hidden": False,
                    "markdown": "The outcome.", "count": {"kind": kind}, "effective_at": WHEN}

        low, carried = result("NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION", 2), result("QUALIFIED", 3)
        read = {"topic": {"thread_uuid": thread, "title": "Repair", "category_uuid": category}, "posts": [opening, low, carried]}
        unavailable = discussion_pages.discussion.DiscussionUnavailable
        listing = {"categories": [{"category_uuid": category}], "reads_without_board_place": False}
        with mock.patch.object(discussion_pages.discussion, "board_categories", return_value=listing), \
             mock.patch.object(discussion_pages.discussion_posts, "read_thread", return_value=read), \
             mock.patch.object(discussion_pages.discussion_posts, "edit_standing", return_value={"read_at": None, "own": set()}), \
             mock.patch.object(discussion_pages.member_navigation, "member_navigation", side_effect=discussion_pages.member_navigation.NavigationUnavailable), \
             mock.patch.object(discussion_pages.cq_reads, "result_post", side_effect=unavailable):
            discussion_pages._read_page.__wrapped__(AnonymousUser(), org, circle, thread)
        self.assertEqual((low.get("time_word"), carried.get("time_word")), ("Result recorded", "Took effect"),
                         "The page's read must give each result post the word for its outcome, released destination or not.")

        def line(post):
            page = Markup("<main>" + render_to_string("core/components/vote_result.html", {"result": post}) + "</main>")
            return page.select("p", classes=("meta",))[0]["text"].strip()

        self.assertEqual((line(low), line(carried)),
                         ("Result recorded 24 November 2026, 19:01 UTC", "Took effect 24 November 2026, 19:01 UTC"),
                         "The vote-result post must name a no-decision result's time \"Result recorded\".")
        self.assertEqual(line({"markdown": "Carried.", "effective_at": WHEN}), "Took effect 24 November 2026, 19:01 UTC",
                         "A result post with no word given keeps \"Took effect\".")

    def test_pc1_02_result_recorded_shows_the_recorded_time_on_the_topic_and_the_result_page(self):
        """PC1-02 (capture BW, BW1). A no-decision result recorded four minutes after its effective time reads 19:05 on both pages."""
        org, circle, thread, category = (uuid4() for _ in range(4))
        recorded = WHEN + timedelta(minutes=4)

        def result(kind, number):
            return {"post_number": number, "post_uuid": uuid4(), "post_type": "GROUP_GAZE", "hidden": False, "ballot": str(uuid4()),
                    "markdown": "The outcome.", "count": {"kind": kind}, "effective_at": WHEN, "recorded_at": recorded}

        low, carried = result("NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION", 2), result("QUALIFIED", 3)
        read = {"topic": {"thread_uuid": thread, "title": "Repair", "category_uuid": category},
                "posts": [{"post_number": 1, "markdown": "Opening"}, low, carried]}
        listing = {"categories": [{"category_uuid": category}], "reads_without_board_place": False}
        with mock.patch.object(discussion_pages.discussion, "board_categories", return_value=listing), \
             mock.patch.object(discussion_pages.discussion_posts, "read_thread", return_value=read), \
             mock.patch.object(discussion_pages.discussion_posts, "edit_standing", return_value={"read_at": None, "own": set()}), \
             mock.patch.object(discussion_pages.member_navigation, "member_navigation", side_effect=discussion_pages.member_navigation.NavigationUnavailable), \
             mock.patch.object(discussion_pages.cq_reads, "result_post", side_effect=discussion_pages.discussion.DiscussionUnavailable):
            discussion_pages._read_page.__wrapped__(AnonymousUser(), org, circle, thread)

        def topic_line(post):
            page = Markup("<main>" + render_to_string("core/components/vote_result.html", {"result": post}) + "</main>")
            stamp, = page.select("time")
            return page.select("p", classes=("meta",))[0]["text"].strip(), stamp["attrs"]["datetime"][:16]

        self.assertEqual(topic_line(low), ("Result recorded 24 November 2026, 19:05 UTC", "2026-11-24T19:05"),
                         "On the Topic, \"Result recorded\" must show when the result was recorded, not its effective time.")
        self.assertEqual(topic_line(carried), ("Took effect 24 November 2026, 19:01 UTC", "2026-11-24T19:01"),
                         "On the Topic, \"Took effect\" keeps the result's effective time.")
        on_result_page = discussion_pages._result_context(low | {"closed_at": "2026-11-24T09:00:00+00:00"})
        self.assertEqual((on_result_page["time_word"], on_result_page["times"]["took_effect"]), ("Result recorded", recorded),
                         "The result page must show the same recorded time under the same label.")
        self.assertIn("a.recorded_at", cq_reads._RESULT.split(" FROM ")[0],
                      "Every released result row must carry its recorded time for the Topic to show.")


LOW = "NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION"
LOW_COUNT = {"kind": LOW, "eligible": 4, "participated": 1, "required": 3, "qualified": 1, "not_qualified": 0, "abstained": 0}
LOW_TALLY = {"eligible": 4, "for": 1, "against": 0, "abstain": 0, "no_answer": 3, "abstain_allowed": False,
             "names": {"QUALIFIED": ["Mira Alder"], "NOT_QUALIFIED": [], "ABSTAIN": [], "NO_ANSWER": ["Rowan Fell", "Sol Reed", "Kit Vale"]}}
DANGER_LINES = ["Insufficient participation. No decision was reached.", "1 of 4 eligible Board members took part. 3 were required."]


def classes(node):
    return node["attrs"].get("class", "").split()


def children(page, parent):
    """A node's own child elements, in order."""
    return [node for node in page.nodes if node["parents"] and node["parents"][-1] is parent]


def closed_section(outcome, count, tally, **row):
    """The Topic's vote section after a result, as the page builds it: the vote read, then the result post's ring."""
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = mock.Mock(session_key="reader")
    ballot = uuid4()
    with mock.patch.object(discussion_vote.cq, "board_topic_vote",
                           return_value=dict(phase="RESULT", outcome=outcome, ballot_uuid=ballot, closed_at=WHEN, effective_at=WHEN,
                                             markdown="Approve one payment.", version_number=1) | row):
        vote = discussion_vote.panel(request, uuid4(), uuid4(), uuid4())
    context = {"vote": vote, "posts": [{"post_number": 1, "markdown": "Opening"},
                                       {"post_type": "GROUP_GAZE", "hidden": False, "ballot": str(ballot), "post_uuid": uuid4(),
                                        "count": count, "tally": tally}]}
    discussion_pages._closed_vote_ring(context)
    page = Markup(render_to_string("core/components/board_vote.html", {"vote": vote}))
    section, = page.select("section", id="vote")
    return page, section


def result_page(kind, count, **changes):
    org, circle, thread, result = (uuid4() for _ in range(4))
    address = reverse("discussion_result", args=(org, circle, thread, result))
    request = RequestFactory().get(address)
    request.user = AnonymousUser()
    request.resolver_match = resolve(address)
    record = {"post_uuid": result, "ballot": str(uuid4()), "event_kind": "DECISION", "count": count | {"kind": kind},
              "markdown": "The stored outcome sentence.", "effective_at": WHEN, "recorded_at": WHEN,
              "closed_at": "2026-11-24T19:01:00+00:00", "deciding_body": {"full_name": "Board of Trustees", "short_label": "Board"},
              "wording": "Approve one payment.", "wording_version_number": 1, "carried_no_attached_act": False,
              "tally": LOW_TALLY} | changes
    context = {"result": record, **discussion_pages._result_context(record)}
    return Markup(render_to_string("core/discussion_result.html", context, request=request))


class VoteSectionLayoutTests(SimpleTestCase):
    """PB6B-01 to PB6B-03: the Topic's "Board vote" section as layout C, and the red card's place."""

    def test_pb6b_01_insufficient_participation_leads_with_the_red_card_then_one_vote_card_then_the_wording(self):
        """Captures AH Q19, AQ1 and AR1. The red card first; one card with the line, ring, names and own answer; the wording's own card."""
        page, section = closed_section(LOW, LOW_COUNT, LOW_TALLY, decided_own_answer="QUALIFIED")
        parts = children(page, section)
        self.assertEqual([(node["tag"], node["attrs"].get("id") or " ".join(classes(node))) for node in parts],
                         [("h2", "vote-heading"), ("div", "vote-danger"), ("div", "card vote-card"), ("article", "card")],
                         "Under the heading: the red card, then the vote card, then the wording's card, and nothing else.")
        danger, card, wording = parts[1:]
        self.assertEqual((classes(danger), [p["text"] for p in page.select("p", within=danger)]),
                         (["notice", "refusal"], DANGER_LINES),
                         "The red card must say the outcome and then this result's own counts.")
        self.assertEqual([strong["text"] for strong in page.select("strong", within=danger)], ["Insufficient participation."],
                         "The outcome's name leads the red card in bold.")
        inside = children(page, card)
        self.assertEqual([node["attrs"].get("id") or " ".join(classes(node)) for node in inside],
                         ["meta", "participation-summary", "office-facts vote-names", "own-answer"],
                         "The vote card holds, in order: the status line, the ring beside its counts, the names, the member's own answer.")
        self.assertEqual(inside[0]["text"], "Vote ended · Voting closed 24 November 2026, 19:01 UTC",
                         "The status line names the times and does not repeat the outcome.")
        self.assertEqual((inside[3]["text"].strip(), classes(inside[3])), ("You answered For.", ["notice", "success"]),
                         "The member's own answer sits at the foot of the vote card, in its colour.")
        heading, = page.select("h3", within=wording)
        self.assertEqual((heading["attrs"].get("id"), heading["text"]), ("vote-wording-heading", "Wording voted on"),
                         "The wording's heading sits inside the wording's own card.")
        self.assertEqual(len(page.select("div", within=wording, classes=("motion-wording",))), 1)

    def test_pb6b_02_no_other_result_carries_the_red_card(self):
        """Capture AQ4. A carried, defeated or other no-decision result has the same shape with no red card."""
        for outcome, line in (("QUALIFIED", "Decided · Carried · Voting closed 24 November 2026, 19:01 UTC"),
                              ("NOT_QUALIFIED_VOTE", "Decided · Not carried · Voting closed 24 November 2026, 19:01 UTC"),
                              ("NO_DECISION_FEWER_THAN_THREE",
                               "Vote ended · Fewer than three eligible members remained. No decision was reached."
                               " · Voting closed 24 November 2026, 19:01 UTC")):
            with self.subTest(outcome=outcome):
                page, section = closed_section(outcome, LOW_COUNT | {"kind": outcome}, LOW_TALLY)
                parts = children(page, section)
                self.assertEqual([node["attrs"].get("id") or " ".join(classes(node)) for node in parts],
                                 ["vote-heading", "card vote-card", "card"], "The same shape, with no red card.")
                self.assertEqual(page.select("div", classes=("notice", "refusal")), [], "No red card for this result.")
                self.assertEqual(children(page, parts[1])[0]["text"], line, "The status line keeps this result's outcome.")
        self.assertEqual(danger_card({"participated": 0, "eligible": 3, "required": 1})["counts"],
                         "0 of 3 eligible Board members took part. 1 was required.", "One required member reads in the singular.")
        self.assertIsNone(danger_card()["counts"], "With no counts at hand the red card keeps to its first sentence.")

    def test_pb6b_03_while_voting_is_open_the_merged_card_sits_at_the_foot_of_the_vote_card(self):
        """Captures AQ1 and AQ2. Voting open: no red card; the vote card ends with the merged answer card; the wording has its card."""
        page, section = vote_section(phase="VOTING", read_at=WHEN, deadline=WHEN + timedelta(days=3), ballot_uuid=uuid4(),
                                     markdown="Approve one payment.", version_number=1,
                                     ballot={"ballot_uuid": uuid4(), "answered": 2, "eligible": 4, "own_answer": "QUALIFIED",
                                             "options": ["QUALIFIED", "NOT_QUALIFIED"], "may_answer": False, "answer_refusal": None})
        parts = children(page, section)
        self.assertEqual([node["attrs"].get("id") or " ".join(classes(node)) for node in parts],
                         ["vote-heading", "card vote-card", "card"], "While voting is open there is no red card.")
        inside = children(page, parts[1])
        self.assertEqual([node["attrs"].get("id") or " ".join(classes(node)) for node in inside],
                         ["meta", "participation-summary", "own-answer"],
                         "The vote card holds the status line, the ring beside its counts and the member's merged answer card.")
        self.assertEqual(inside[2]["text"].strip(), "Your answer is recorded. You answered For.")
        self.assertEqual(page.select("h3", within=parts[2])[0]["text"], "Wording being voted on")


class ResultPageDangerAndAnswerTests(SimpleTestCase):
    """PB6B-04 and PB6B-05: the result page's red card, "No decision" and the member's own answer; the read behind it."""

    def test_pb6b_04_result_page_red_card_no_decision_and_own_answer_at_the_cards_foot(self):
        """Captures AP1, AQ4 and AR2. Red card above the Decision card; Outcome "No decision"; own answer only for an answerer."""
        page = result_page(LOW, LOW_COUNT, own_answer="QUALIFIED")
        main, = page.select("main")
        danger, = page.select("div", within=main, id="vote-danger")
        decision = next(card for card in page.select("article", within=main, classes=("card",))
                        if page.select("h2", within=card)[0]["text"] == "Decision")
        self.assertEqual((classes(danger), [p["text"] for p in page.select("p", within=danger)]), (["notice", "refusal"], DANGER_LINES),
                         "The red card must say the outcome and this result's own counts.")
        self.assertLess(page.nodes.index(danger), page.nodes.index(decision), "The red card sits above the Decision card.")
        self.assertEqual(danger["parents"][-1], decision["parents"][-1], "The red card is beside the Decision card, not inside it.")
        rows, = [node for node in page.select("dl", within=decision) if "vote-names" not in classes(node)]
        facts = dict(zip([dt["text"].strip() for dt in page.select("dt", within=rows)],
                         [dd["text"].strip() for dd in page.select("dd", within=rows)], strict=True))
        self.assertEqual(facts["Outcome"], "No decision", "Under the red card the Outcome row reads \"No decision\".")
        own = children(page, decision)[-1]
        self.assertEqual((own["attrs"].get("id"), own["text"].strip(), classes(own)), ("own-answer", "You answered For.", ["notice", "success"]),
                         "A member who answered sees their own answer at the foot of the Decision card, in its colour.")
        silent = result_page(LOW, LOW_COUNT, own_answer=None)
        self.assertEqual(silent.select("div", id="own-answer"), [], "A reader who did not answer has no answer card.")
        carried = result_page("QUALIFIED", LOW_COUNT, own_answer="NOT_QUALIFIED")
        self.assertEqual(carried.select("div", id="vote-danger"), [], "A carried result has no red card.")
        self.assertIn("Carried", [dd["text"].strip() for dd in carried.select("dd")],
                      "A carried result's Outcome row reads the outcome word (capture AT2; part PB-6c).")
        self.assertEqual([(card["text"].strip(), classes(card)) for card in carried.select("div", id="own-answer")],
                         [("You answered Against.", ["notice", "refusal"])], "An Against answer sits on the red answer card.")

    def test_pb6b_05_the_own_answer_read_is_the_readers_own_and_never_an_elections(self):
        """Capture AR2. The read asks for this reader's answer on this ballot only; an election or a result with no ballot is not asked."""
        actor, board = mock.Mock(pk=41), {"board_uuid": uuid4(), "organism_id": 7}
        thread, ballot = uuid4(), str(uuid4())
        record = {"event_kind": "DECISION", "ballot": ballot}
        with mock.patch.object(cq_reads.disc, "_rows", return_value=[{"choice": "ABSTAIN"}]) as rows:
            self.assertEqual(cq_reads._own_answer(board, thread, actor, record), "ABSTAIN")
        self.assertEqual(rows.call_args.args[1], [board["board_uuid"], thread, ballot, 41, 7, 41],
                         "The read must be bound to this board, Topic, ballot and reader.")
        self.assertIn("a.actor_id=%s", rows.call_args.args[0], "Only the reader's own answer may be read.")
        with mock.patch.object(cq_reads.disc, "_rows", return_value=[]):
            self.assertIsNone(cq_reads._own_answer(board, thread, actor, record), "A reader who did not answer has none.")
        for other in ({"event_kind": "ELECTION", "ballot": ballot}, {"event_kind": "DECISION", "ballot": None}):
            with mock.patch.object(cq_reads.disc, "_rows") as rows:
                self.assertIsNone(cq_reads._own_answer(board, thread, actor, other))
            rows.assert_not_called()


class VoteSummaryStyleRuleTests(SimpleTestCase):
    """PB6B-07 (capture AQ3), read from the stylesheet's source: inside a card the ring stays beside its counts.

    ``tools/f0_styles.py check`` holds the served stylesheet to this source, and the renders measure the pages.
    """

    def test_pb6b_07_one_shared_rule_narrows_the_summary_inside_a_card_only(self):
        source = (Path(__file__).resolve().parents[1] / "core/styles/theme.css").read_text(encoding="utf-8")
        rules = dict(re.findall(r"(\.card \.participation-summary[^{]*)\{([^}]*)\}", source))
        self.assertEqual(rules, {".card .participation-summary": "gap:var(--space-md)",
                                 ".card .participation-summary .participation-ring": "inline-size:7rem",
                                 ".card .participation-summary .counts": "min-inline-size:8.5rem"},
                         "One shared rule, scoped to a summary inside a card: a closer gap, a smaller ring, narrower count rows.")
        self.assertIn(".participation-summary .participation-ring{inline-size:8.75rem;margin:0}", source,
                      "Outside a card the summary keeps its landed sizes.")


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS, INTEVIA_TIMED_EVENTS_WAKE=False)
class InsufficientParticipationPageTests(TransactionTestCase):
    """PB6B-06: the real Topic and result pages after a Board vote that too few answered."""

    def test_pb6b_06_one_answer_of_four_shows_the_red_card_on_both_pages_and_the_answer_only_to_its_member(self):
        """Captures AH Q19, AP1, AQ4 and AR2. One of four answers: the red card leads both pages; only the answerer sees an answer card."""
        case = closed_board_vote(answers=((0, "QUALIFIED"),))
        result = cq_reads.result_post(**case.scope, result_uuid=case.status["result_uuid"])
        count = result["count"]
        self.assertEqual((case.status["phase"], count["kind"], count["participated"], count["eligible"]), ("RESULT", LOW, 1, 4),
                         "The fixture must have recorded a result that one of four answered and that reached no decision.")
        lines = ["Insufficient participation. No decision was reached.",
                 f"1 of 4 eligible Board members took part. {count['required']} {'was' if count['required'] == 1 else 'were'} required."]
        for person, answer in ((case.people[0], [("You answered For.", ["notice", "success"])]), (case.people[1], [])):
            with self.subTest(answered=bool(answer)):
                reader = signed_in(person)
                topic = Markup(reader.get(case.url).content.decode())
                section, = topic.select("section", id="vote")
                parts = children(topic, section)
                self.assertEqual([node["attrs"].get("id") or " ".join(classes(node)) for node in parts],
                                 ["vote-heading", "vote-danger", "card vote-card", "card"],
                                 "On the Topic the red card leads, then the vote card, then the wording's card.")
                self.assertEqual([p["text"] for p in topic.select("p", within=parts[1])], lines,
                                 "The Topic's red card must carry this result's own counts.")
                self.assertTrue(children(topic, parts[2])[0]["text"].startswith("Vote ended · Voting closed "),
                                "The status line names the close of voting and does not repeat the outcome.")
                self.assertEqual([(card["text"].strip(), classes(card)) for card in topic.select("div", within=parts[2], id="own-answer")],
                                 answer, "Only the member who answered sees an answer card on the Topic.")
                page = Markup(reader.get(case.result_url).content.decode())
                main, = page.select("main")
                self.assertEqual([p["text"] for p in page.select("p", within=page.select("div", within=main, id="vote-danger")[0])], lines,
                                 "The result page's red card must carry the same words.")
                self.assertIn("No decision", [dd["text"].strip() for dd in page.select("dd", within=main)],
                              "The result page's Outcome row reads \"No decision\".")
                self.assertEqual([(card["text"].strip(), classes(card)) for card in page.select("div", within=main, id="own-answer")],
                                 answer, "Only the member who answered sees an answer card on the result page.")
