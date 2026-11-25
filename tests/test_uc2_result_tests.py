"""The posts box, part PB-6c: "How this result was reached" on the result page.

Rulings of 24 Nov 2026: capture AT (AT1: a row per test the engine applied, Met / Not met / Not
tested; AT2: the Outcome row reads the outcome word), capture AU (AU1: the count rows' width cap),
capture AW (AW1: the result page names its Topic), capture AX (AX2: abstentions said in the
participation row and on the red card) and the VC's answer on the card heading's size.

Every sentence is built from the result's stored count and the rule its ballot fixed when voting
opened; nothing here reads a setting. PB6C-01 to PB6C-08 need no database. PB6C-09 reads the real
pages; its fixture is built through the real services, rows per test, with no owner or superuser power.

Part PB-9 (HG capture BK of 25 Nov 2026; GPT Astra's posts-box part 2 review, P2-1): PB9-01 and PB9-02
pin a failed fractional vote's chip and sentence against expectations written out here, not built.
PB9-05 pins the divided lists' shared style rules (BK1). None needs a database.
"""
import re
from pathlib import Path
from unittest import mock
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import resolve, reverse

from core import discussion_pages
from core.ui import danger_card
from core.vote_tests import outcome_word, result_tests
from src.intevia.services import cq_reads
from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import signed_in
from tests.test_uc2_closed_vote_ring import closed_board_vote
from tests.test_uc2_pages import FIXTURE_HASHERS
from tests.test_uc2_result_cards import WHEN, classes

# The rule a ballot fixes by default (migration 0074's defaults), and two others a ballot can fix.
DEFAULT_RULE = {"quorum_form": "MAJORITY", "quorum_value": None, "threshold_base": "VOTES_CAST", "threshold": "MAJORITY",
                "numerator": None, "denominator": None, "abstention": True}
TWO_THIRDS_OF_ELIGIBLE = DEFAULT_RULE | {"threshold": "FRACTION", "numerator": 2, "denominator": 3, "threshold_base": "ELIGIBLE"}
OF_ALL_ANSWERS = DEFAULT_RULE | {"threshold_base": "PARTICIPANTS"}


def count(kind, eligible, yes, no, abstained=0, required=3, base=None):
    """A stored count as ``public.cq_count_decision`` writes it."""
    applied = kind in ("QUALIFIED", "NOT_QUALIFIED_VOTE", "NOT_QUALIFIED_TIE")
    return {"kind": kind, "eligible": eligible, "participated": yes + no + abstained, "required": required, "qualified": yes,
            "not_qualified": no, "abstained": abstained, "cqt_applied": applied,
            "qualification_denominator": (yes + no if base is None else base) if applied else None}


def rows(tests):
    return [(test["label"], test["text"]) for test in tests]


def result_page(stored, rule=DEFAULT_RULE, topic=None, **changes):
    """The result page as the view builds it from a result's record, with no database."""
    org, circle, thread, result = (uuid4() for _ in range(4))
    address = reverse("discussion_result", args=(org, circle, thread, result))
    request = RequestFactory().get(address)
    request.user = AnonymousUser()
    request.resolver_match = resolve(address)
    record = {"post_uuid": result, "ballot": str(uuid4()), "event_kind": "DECISION", "count": stored, "rule": rule,
              "markdown": "The stored outcome sentence.", "effective_at": WHEN, "recorded_at": WHEN,
              "closed_at": "2026-11-24T19:01:00+00:00", "deciding_body": {"full_name": "Board of Trustees", "short_label": "Board"},
              "wording": "Approve one payment.", "wording_version_number": 1, "carried_no_attached_act": False} | changes
    context = {"result": record, **discussion_pages._result_context(record)}
    if topic:
        context.update(topic_url="/the-topic/", topic_title=topic)
    return Markup(render_to_string("core/discussion_result.html", context, request=request))


def shown_tests(page):
    """Each row of the page's test summary: its chip's classes and words, then its sentence."""
    return [(classes(page.select("span", within=row)[0]), page.select("span", within=row)[0]["text"].strip(),
             page.select("p", within=row)[0]["text"]) for row in page.select("li", within=page.select("ul", classes=("result-tests",))[0])]


def outcome(page):
    terms = [node["text"].strip() for node in page.select("dt")]
    return page.select("dd")[terms.index("Outcome")]["text"].strip()


class ResultTestSentenceTests(SimpleTestCase):
    """PB6C-01 to PB6C-04: the sentence builders, each outcome in the engine's order."""

    def test_pb6c_01_carried_and_not_carried_state_participation_then_the_majority(self):
        """Capture AT1. Carried and not carried, by the default rule, from the stored counts."""
        self.assertEqual(rows(result_tests(count("QUALIFIED", 5, 2, 1, 1), DEFAULT_RULE)),
                         [("Met", "Participation: 4 of 5 eligible Board members took part, including 1 abstention. 3 were required."),
                          ("Met", "Majority: 2 of the 3 For and Against answers were For. More than half was required. "
                                  "The abstention counted towards participation only.")])
        self.assertEqual(rows(result_tests(count("NOT_QUALIFIED_VOTE", 5, 1, 3), DEFAULT_RULE)),
                         [("Met", "Participation: 4 of 5 eligible Board members took part. 3 were required."),
                          ("Not met", "Majority: 1 of the 4 For and Against answers were For. More than half was required.")],
                         "With no abstention nothing is said of abstentions.")

    def test_pb6c_02_a_tie_and_two_abstentions(self):
        """Captures AT1 and AX2. A tie is not carried; two abstentions are counted in words that agree in number."""
        self.assertEqual(rows(result_tests(count("NOT_QUALIFIED_TIE", 6, 2, 2, 2), DEFAULT_RULE)),
                         [("Met", "Participation: 6 of 6 eligible Board members took part, including 2 abstentions. 3 were required."),
                          ("Not met", "The vote was tied, 2 to 2. A tie is not carried. The 2 abstentions counted towards participation only.")])

    def test_pb6c_03_a_failed_test_leaves_the_later_ones_not_tested(self):
        """Capture AT1. Participation not met: the majority is not tested. Fewer than three eligible: nothing after it is tested."""
        self.assertEqual(rows(result_tests(count("NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION", 4, 1, 0, 1), DEFAULT_RULE)),
                         [("Not met", "Participation: 2 of 4 eligible Board members took part, including 1 abstention. 3 were required."),
                          ("Not tested", "Majority: not tested, because participation was not met.")])
        self.assertEqual(rows(result_tests(count("NO_DECISION_FEWER_THAN_THREE", 2, 1, 0), DEFAULT_RULE)),
                         [("Not met", "At least three eligible members: 2 remained."),
                          ("Not tested", "Participation: not tested, because fewer than three eligible members remained."),
                          ("Not tested", "Majority: not tested, because fewer than three eligible members remained.")])

    def test_pb6c_04_another_rule_is_stated_as_recorded_and_no_ballot_has_no_summary(self):
        """Capture AT1. A fraction or another base is worded from the ballot's rule; without a counted ballot or its rule there is no summary."""
        self.assertEqual(rows(result_tests(count("QUALIFIED", 6, 4, 1, 1, required=4, base=6), TWO_THIRDS_OF_ELIGIBLE))[1],
                         ("Met", "At least two thirds of eligible members were For: 4 of 6."),
                         "With eligible members as the base, abstentions are in the base and are not said to be left out.")
        self.assertEqual(rows(result_tests(count("NOT_QUALIFIED_VOTE", 5, 2, 1, 2, base=5), OF_ALL_ANSWERS))[1],
                         ("Not met", "Majority: 2 of the 5 answers were For. More than half was required."))
        for kind in ("NO_DECISION_SLATE_CHANGED", "NO_BALLOT_WITHDRAWN", "ELECTED"):
            self.assertIsNone(result_tests({"kind": kind}, DEFAULT_RULE), "Only a counted ordinary ballot has a test summary.")
            self.assertIsNone(outcome_word(kind), "Only a counted ordinary ballot has an outcome word.")
        self.assertIsNone(result_tests(count("QUALIFIED", 5, 3, 1), None), "With no recorded rule at hand no sentence is made up.")
        self.assertEqual([outcome_word(kind) for kind in ("QUALIFIED", "NOT_QUALIFIED_VOTE", "NOT_QUALIFIED_TIE",
                                                          "NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION", "NO_DECISION_FEWER_THAN_THREE")],
                         ["Carried", "Not carried", "Not carried", "No decision", "No decision"])


class FailedFractionSentenceTests(SimpleTestCase):
    """PB9-01 and PB9-02: a fractional vote that was not carried is never said to have met its fraction."""

    FAILED = "3 of 5 eligible members were For. At least two thirds were required."

    def test_pb9_01_a_failed_fraction_states_the_count_then_what_was_required(self):
        """PB9-01. Astra's case: two thirds of eligible, five eligible, three For and two Against. Not met, and said so."""
        tests = result_tests(count("NOT_QUALIFIED_VOTE", 5, 3, 2, base=5), TWO_THIRDS_OF_ELIGIBLE)
        self.assertEqual([(test["label"], test["key"], test["symbol"], test["text"]) for test in tests],
                         [("Met", "consented", "✓", "Participation: 5 of 5 eligible Board members took part. 3 were required."),
                          ("Not met", "declined", "✕", self.FAILED)],
                         "Three of five is less than two thirds: the chip must read Not met and the sentence must not say the fraction was reached.")
        of_votes_cast = DEFAULT_RULE | {"threshold": "FRACTION", "numerator": 2, "denominator": 3}
        self.assertEqual(rows(result_tests(count("NOT_QUALIFIED_VOTE", 6, 3, 2, 1), of_votes_cast))[1],
                         ("Not met", "3 of 5 For and Against answers were For. At least two thirds were required. "
                                     "The abstention counted towards participation only."),
                         "With For and Against answers as the base, the ballot's own denominator and the abstention note must stay.")
        of_all_answers = of_votes_cast | {"threshold_base": "PARTICIPANTS", "numerator": 3, "denominator": 4}
        self.assertEqual(rows(result_tests(count("NOT_QUALIFIED_VOTE", 6, 3, 1, 2, base=6), of_all_answers))[1],
                         ("Not met", "3 of 6 answers were For. At least three quarters were required."),
                         "With every answer as the base, abstentions are in the base and are not said to be left out.")
        for failed in (tests[1]["text"], rows(result_tests(count("NOT_QUALIFIED_VOTE", 6, 3, 2, 1), of_votes_cast))[1][1]):
            self.assertNotIn("were For:", failed, "A failed vote must never carry the carried sentence's form.")
        self.assertEqual(rows(result_tests(count("QUALIFIED", 6, 4, 2, base=6), TWO_THIRDS_OF_ELIGIBLE))[1],
                         ("Met", "At least two thirds of eligible members were For: 4 of 6."),
                         "The accepted sentence for a carried fractional vote must be unchanged.")
        self.assertEqual(rows(result_tests(count("NOT_QUALIFIED_VOTE", 5, 1, 3), DEFAULT_RULE))[1],
                         ("Not met", "Majority: 1 of the 4 For and Against answers were For. More than half was required."),
                         "The accepted majority sentence for a vote that was not carried must be unchanged.")

    def test_pb9_02_the_result_page_shows_the_failed_fraction_as_not_met(self):
        """PB9-02. On the page: Outcome "Not carried", the red Not met chip, and the same true sentence."""
        page = result_page(count("NOT_QUALIFIED_VOTE", 5, 3, 2, base=5), TWO_THIRDS_OF_ELIGIBLE)
        self.assertEqual(outcome(page), "Not carried", "The Outcome row must read the stored outcome.")
        chip, words, sentence = shown_tests(page)[1]
        self.assertEqual((words.split()[-2:], "declined" in chip, sentence.strip()), (["Not", "met"], True, self.FAILED),
                         "The page's threshold row must pair the Not met chip with the sentence that says what was required.")


class DividedListStyleTests(SimpleTestCase):
    """PB9-05: the test rows and the count rows are each one outlined group with dividers, by shared rules only."""

    def test_pb9_05_the_divided_lists_are_shared_rules_in_the_served_stylesheet(self):
        """PB9-05 (capture BK1). One outline per group, no gap, a thin rule between rows; no row keeps its own box."""
        from django.contrib.staticfiles import finders
        from tests.test_uc1_render import declarations
        sheet = Path(finders.find("core/f0.css")).read_text(encoding="utf-8")
        line = "var(--stroke) solid var(--color-line)"
        for group, row in ((".result-tests", ".result-tests li"), (".counts", ".counts p")):
            with self.subTest(group=group):
                outer, inner, between = declarations(sheet, group), declarations(sheet, row), declarations(sheet, row + "+" + row.split()[-1])
                self.assertEqual((outer.get("border"), outer.get("border-radius"), outer.get("gap")), (line, "var(--radius-sm)", "0"),
                                 "The group must have one outline with the shared corner and no gap between its rows.")
                self.assertEqual(between.get("border-block-start"), line, "A thin rule must divide each row from the one above.")
                self.assertEqual(inner.get("padding"), "var(--space-sm) var(--space-md)", "Each row must keep its room inside the group.")
                self.assertNotIn("border", inner, "A row must not keep a box of its own.")
                self.assertNotIn("border-radius", inner, "A row must not keep corners of its own.")
        self.assertEqual(declarations(sheet, ".counts").get("grid-template-columns"), "1fr",
                         "The count rows must stack in one column, or a divider would cross a second column.")
        scoped = sorted(set(re.findall(r"[^{}]*(?:#vote|\.vote-card|\.record-section)[^{}]*(?:\.counts|\.result-tests)[^{}]*(?=\{)", sheet)))
        self.assertEqual(scoped, [], "No section may carry its own copy of the divided lists (item 278).")


class ResultPageSummaryTests(SimpleTestCase):
    """PB6C-05 to PB6C-08: the page, the red card, the read of the rule and the style rules."""

    def test_pb6c_05_the_summary_sits_in_the_decision_card_with_shared_chips_and_the_outcome_word(self):
        """Captures AT1 and AT2. Under the dates, before the ring: a heading and a chip beside each sentence; Outcome is one word."""
        page = result_page(count("QUALIFIED", 5, 2, 1, 1), tally={"eligible": 5, "for": 2, "against": 1, "abstain": 1, "no_answer": 1,
                                                                    "abstain_allowed": True, "names": None})
        decision = next(card for card in page.select("article", classes=("card",)) if page.select("h2", within=card)[0]["text"] == "Decision")
        order = [node for node in page.nodes if node["parents"] and node["parents"][-1] is decision]
        self.assertEqual([node["tag"] + "." + ".".join(classes(node)) for node in order],
                         ["h2.", "dl.office-facts", "h3.", "ul.result-tests", "div.participation-summary"],
                         "The summary follows the outcome and dates and comes before the ring.")
        self.assertEqual(order[2]["text"], "How this result was reached")
        self.assertEqual([(chip, words) for chip, words, _ in shown_tests(page)],
                         [(["answer", "consented"], "✓ Met"), (["answer", "consented"], "✓ Met")])
        self.assertEqual(outcome(page), "Carried", "The Outcome row reads the outcome word only.")
        self.assertNotIn("The stored outcome sentence.", [node["text"].strip() for node in page.select("dd")])
        failed = result_page(count("NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION", 4, 1, 0, 1))
        self.assertEqual([(chip, words) for chip, words, _ in shown_tests(failed)],
                         [(["answer", "declined"], "✕ Not met"), (["answer", "pending"], "— Not tested")])
        self.assertEqual((outcome(failed), outcome(result_page(count("NOT_QUALIFIED_TIE", 4, 2, 2)))), ("No decision", "Not carried"))

    def test_pb6c_06_no_ballot_and_an_election_keep_their_sentence_and_have_no_summary(self):
        """Capture AT1. A result with no ballot, and an election, show no test summary and keep their own Outcome sentence."""
        for changes in ({"ballot": None, "count": {"kind": "NO_BALLOT_WITHDRAWN"}},
                        {"event_kind": "ELECTION", "count": {"kind": "NO_DECISION_SLATE_CHANGED"}}):
            page = result_page(changes["count"], wording=None, **{key: value for key, value in changes.items() if key != "count"})
            self.assertEqual(page.select("ul", classes=("result-tests",)), [])
            self.assertEqual(outcome(page), "The stored outcome sentence.")
        counted = result_page(count("QUALIFIED", 5, 3, 1), event_kind="ELECTION")
        self.assertEqual((counted.select("ul", classes=("result-tests",)), outcome(counted)), ([], "The stored outcome sentence."),
                         "An election has no summary and keeps its sentence, whatever its count holds.")

    def test_pc1_03_an_election_that_misses_its_minimum_takes_no_red_card_and_keeps_its_sentence(self):
        """PC1-03 (captures AT1 and BW3). An election counted as insufficient participation: no red card on either page."""
        from tests.test_uc2_result_cards import LOW, LOW_COUNT, LOW_TALLY, closed_section

        low = count(LOW, 5, 1, 0)
        election = result_page(low, event_kind="ELECTION")
        self.assertEqual(election.select("div", id="vote-danger"), [], "An election's result page must not take the red card.")
        self.assertEqual(outcome(election), "The stored outcome sentence.",
                         "An election's Outcome row keeps its own stored sentence, never \"No decision\".")
        self.assertEqual(election.select("ul", classes=("result-tests",)), [])
        decision = result_page(low)
        self.assertEqual((len(decision.select("div", id="vote-danger")), outcome(decision)), (1, "No decision"),
                         "The control: an ordinary vote that misses its minimum keeps the red card and \"No decision\".")
        page, section = closed_section(LOW, LOW_COUNT, LOW_TALLY, election=True)
        self.assertEqual(page.select("div", within=section, id="vote-danger"), [],
                         "The Topic's vote card must not take the red card for an election.")
        card, = page.select("div", within=section, classes=("card", "vote-card"))
        self.assertEqual(page.select("p", within=card)[0]["text"].strip(),
                         "Vote ended · Insufficient participation. No decision was reached."
                         " · Voting closed 24 November 2026, 19:01 UTC",
                         "With no red card above it, an election's status line keeps its outcome.")
        page, section = closed_section(LOW, LOW_COUNT, LOW_TALLY)
        self.assertEqual(len(page.select("div", within=section, id="vote-danger")), 1,
                         "The control: an ordinary vote's Topic keeps the red card.")

    def test_pb6c_07_the_topic_is_named_and_linked_only_where_the_reader_may_open_it(self):
        """Capture AW1. "Topic: <title>" under the heading, the title linked to the Topic; absent where the page has no Topic to offer."""
        page = result_page(count("QUALIFIED", 5, 3, 1), topic="Community room door repair")
        line, = page.select("p", id="result-topic")
        heading, = page.select("h1")
        self.assertEqual((line["text"], classes(line)), ("Topic: Community room door repair", ["lede", "discussion-office"]))
        self.assertEqual([(a["text"], a["attrs"]["href"]) for a in page.select("a", within=line)], [("Community room door repair", "/the-topic/")])
        self.assertEqual(page.nodes.index(line), page.nodes.index(heading) + 1, "The Topic line follows the page's heading.")
        self.assertEqual(result_page(count("QUALIFIED", 5, 3, 1)).select("p", id="result-topic"), [],
                         "A reader who may not open the Topic is not shown its title.")

    def test_pb6c_08_the_red_card_counts_abstentions_and_the_rule_read_is_the_ballots_own(self):
        """Capture AX2 and the direction's rule on sources. The red card's words; the rule is read from the ballot act, never a setting."""
        self.assertEqual(danger_card(count("NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION", 4, 1, 0, 1))["counts"],
                         "2 of 4 eligible Board members took part, including 1 abstention. 3 were required.")
        board, thread, post = uuid4(), uuid4(), uuid4()
        record = {"event_kind": "DECISION", "ballot": str(uuid4()), "post_uuid": post}
        with mock.patch.object(cq_reads.disc, "_rows", return_value=[{"rule": DEFAULT_RULE}]) as read:
            self.assertEqual(cq_reads._rule(board, thread, record), DEFAULT_RULE)
        statement = read.call_args.args[0]
        self.assertEqual(read.call_args.args[1], [board, thread, post], "The read is bound to this board, Topic and result.")
        self.assertIn("ballot.facts->'rules'", statement, "The rule is the one the ballot fixed when voting opened.")
        self.assertNotIn("cq_settings", statement, "Today's settings are never read for a result's rule.")
        for other in ({"event_kind": "ELECTION", "ballot": record["ballot"]}, {"event_kind": "DECISION", "ballot": None}):
            with mock.patch.object(cq_reads.disc, "_rows") as read:
                self.assertIsNone(cq_reads._rule(board, thread, other | {"post_uuid": post}))
            read.assert_not_called()
        source = (Path(__file__).resolve().parents[1] / "core/styles/theme.css").read_text(encoding="utf-8")
        for rule in (".participation-summary .counts{max-inline-size:24rem}", ".card-heading{font-size:var(--text-xl)}",
                     ".result-tests li{display:flex;align-items:flex-start;gap:var(--space-sm);padding:var(--space-sm) var(--space-md)}"):
            self.assertEqual(source.count(rule), 1, f"The stylesheet's source must hold the shared rule once: {rule}")
        self.assertEqual(len(re.findall(r"\.participation-summary \.counts\{max-inline-size", source)), 1)
        from tests.test_uc2_result_cards import closed_section, LOW, LOW_COUNT, LOW_TALLY
        page, section = closed_section(LOW, LOW_COUNT, LOW_TALLY)
        heading, = page.select("h3", within=section, id="vote-wording-heading")
        self.assertEqual(classes(heading), ["card-heading"], "The Topic's wording heading keeps its level and takes the card-heading size.")


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS, INTEVIA_TIMED_EVENTS_WAKE=False)
class RecordedRulePageTests(TransactionTestCase):
    """PB6C-09: the real result page after a carried Board vote, from the recorded count and the ballot's recorded rule."""

    def test_pb6c_09_a_carried_votes_page_states_its_tests_from_the_record_and_names_its_topic(self):
        """Captures AT1, AT2 and AW1. The rows are the builders' own words for the stored count and the ballot's rule."""
        case = closed_board_vote()
        result = cq_reads.result_post(**case.scope, result_uuid=case.status["result_uuid"], detail=True)
        self.assertEqual(result["count"]["kind"], "QUALIFIED", "The fixture must have carried.")
        self.assertIn("threshold_base", result["rule"], "The result's read must carry the rule its ballot fixed.")
        expected = result_tests(result["count"], result["rule"])
        self.assertEqual([test["label"] for test in expected], ["Met", "Met"])
        page = Markup(signed_in(case.people[0]).get(case.result_url).content.decode())
        self.assertEqual([text for _, _, text in shown_tests(page)], [test["text"] for test in expected])
        self.assertEqual(outcome(page), "Carried")
        line, = page.select("p", id="result-topic")
        self.assertTrue(line["text"].startswith("Topic: "), "A Board member, who may open the Topic, sees it named.")
        self.assertEqual([a["attrs"]["href"] for a in page.select("a", within=line)], [case.url])
