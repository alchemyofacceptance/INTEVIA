"""The posts box, part PB-6d: the rule shown on the Topic while voting is open.

Rulings of 24 Nov 2026: capture AX (AX6: show the rule on the Topic before voting closes) and
capture AY (AY3, option B: "How this vote will be decided" with a "Needed" row each for
participation and the majority, then the meta line saying how many have answered and how many more
are needed for the vote to count).

The rule is the one the open ballot fixed when voting opened, and the number required is the
engine's own count (``public.cq_count_decision``) for the present electorate; nothing reads a
setting. The line counts answers only and never shows how anyone answered. PB6D-01 to PB6D-05 need
no database. PB6D-06 reads the real page; its fixture is built through the real services, rows per
test, with no owner or superuser power.

Part PB-9 (HG capture BK of 25 Nov 2026; GPT Astra's posts-box part 2 review, P2-4): PB9-03 pins the
advance sentence for a fraction of one half or lower, which adds "A tie is not carried." No database.
"""
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from django.test import SimpleTestCase, TransactionTestCase, override_settings

from core.vote_tests import progress_line, result_tests, vote_rule_rows
from src.intevia.services import cq, cq_reads, discussion as disc
from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import signed_in
from tests.test_uc2_ballot_reads import open_board_vote
from tests.test_uc2_pages import FIXTURE_HASHERS
from tests.test_uc2_result_cards import WHEN, children, classes, closed_section, vote_section, LOW, LOW_COUNT, LOW_TALLY
from tests.test_uc2_result_tests import DEFAULT_RULE, TWO_THIRDS_OF_ELIGIBLE, count

PARTICIPATION = "Participation: at least 3 of the 4 eligible Board members take part."
MAJORITY = "Majority: more than half of the For and Against answers are For."
CHOICE_WORDS = ("For.", "Against", "Abstain.")


def open_section(answered=2, eligible=4, required=3, rule=DEFAULT_RULE, own="QUALIFIED", **row):
    """The Topic's vote section while a ballot is open, as the page builds it from the vote read."""
    ballot = {"ballot_uuid": uuid4(), "answered": answered, "eligible": eligible, "own_answer": own, "rule": rule, "required": required,
              "options": ["QUALIFIED", "NOT_QUALIFIED"], "may_answer": False, "answer_refusal": None}
    return vote_section(**({"phase": "VOTING", "read_at": WHEN, "deadline": WHEN + timedelta(days=3), "ballot_uuid": uuid4(),
                            "markdown": "Approve one payment.", "version_number": 1, "ballot": ballot} | row))


def rule_block(page, section):
    """The heading, each row's chip classes, chip words and sentence, and the meta line; or None where the block is absent."""
    headings = page.select("h3", within=section, id="vote-rule-heading")
    if not headings:
        return None
    rows = [(classes(page.select("span", within=row)[0]), page.select("span", within=row)[0]["text"].strip(), page.select("p", within=row)[0]["text"])
            for row in page.select("li", within=page.select("ul", within=section, classes=("result-tests",))[0])]
    line, = page.select("p", within=section, id="vote-progress")
    return headings[0]["text"], rows, (line["text"], classes(line))


class VoteRuleSentenceTests(SimpleTestCase):
    """PB6D-01 and PB6D-02: the rule's rows and the line of how many more are needed."""

    def test_pb6d_01_the_rows_state_the_ballots_own_rule_and_abstentions_only_where_allowed(self):
        """Capture AY3. Worded like the result's tests; the abstention sentence only where the ballot allows abstaining."""
        texts = lambda rows: [(row["label"], row["key"], row["text"]) for row in rows]
        self.assertEqual(texts(vote_rule_rows(4, 3, DEFAULT_RULE)),
                         [("Needed", "pending", PARTICIPATION + " Abstentions count towards taking part."), ("Needed", "pending", MAJORITY)])
        self.assertEqual(texts(vote_rule_rows(4, 3, DEFAULT_RULE | {"abstention": False}))[0][2], PARTICIPATION,
                         "Where abstaining is not allowed nothing is said of abstentions.")
        self.assertEqual([row["text"] for row in vote_rule_rows(6, 4, TWO_THIRDS_OF_ELIGIBLE | {"abstention": False})],
                         ["Participation: at least 4 of the 6 eligible Board members take part.",
                          "At least two thirds of eligible members are For."], "Another rule is stated as the ballot recorded it.")
        self.assertEqual(vote_rule_rows(5, 1, DEFAULT_RULE | {"abstention": False}, "Circle members")[0]["text"],
                         "Participation: at least 1 of the 5 eligible Circle members takes part.")
        for eligible, required, rule in ((4, 3, None), (4, None, DEFAULT_RULE), (2, 3, DEFAULT_RULE)):
            self.assertIsNone(vote_rule_rows(eligible, required, rule), "No rule is promised without the record, or below three eligible.")

    def test_pb9_03_a_fraction_a_tie_could_meet_says_before_the_vote_that_a_tie_is_not_carried(self):
        """PB9-03. One half, four eligible, two For and two Against: the rule read beforehand agrees with the result read after."""
        half = DEFAULT_RULE | {"threshold": "FRACTION", "numerator": 1, "denominator": 2, "abstention": False}
        before = [row["text"] for row in vote_rule_rows(4, 3, half)]
        self.assertEqual(before, [PARTICIPATION, "At least half of the For and Against answers are For. A tie is not carried."],
                         "A share that an even split would meet must say in advance that a tie is not carried.")
        after = [(test["label"], test["text"]) for test in result_tests(count("NOT_QUALIFIED_TIE", 4, 2, 2), half)]
        self.assertEqual(after, [("Met", "Participation: 4 of 4 eligible Board members took part. 3 were required."),
                                 ("Not met", "The vote was tied, 2 to 2. A tie is not carried.")],
                         "The result of the 2 to 2 vote must give the same tie rule the member read beforehand.")
        third_of_eligible = half | {"numerator": 1, "denominator": 3, "threshold_base": "ELIGIBLE"}
        self.assertEqual(vote_rule_rows(6, 4, third_of_eligible)[1]["text"],
                         "At least one third of eligible members are For. A tie is not carried.",
                         "A share lower than one half is also one a tie could meet.")
        unchanged = {MAJORITY: DEFAULT_RULE, "At least two thirds of eligible members are For.": TWO_THIRDS_OF_ELIGIBLE,
                     "At least three fifths of the For and Against answers are For.": half | {"numerator": 3, "denominator": 5}}
        for sentence, rule in unchanged.items():
            self.assertEqual(vote_rule_rows(6, 4, rule)[1]["text"], sentence,
                             "A majority, and a share above one half, cannot be met by a tie and must read as accepted.")

    def test_pb6d_02_the_line_counts_answers_and_reads_right_in_singular_and_plural(self):
        """Capture AY3. The three forms, and the form before anyone answers."""
        self.assertEqual([progress_line(answered, 3) for answered in (0, 1, 2, 3, 4)],
                         ["So far nobody has answered. 3 are needed for the vote to count.",
                          "So far 1 has answered. 2 more are needed for the vote to count.",
                          "So far 2 have answered. 1 more is needed for the vote to count.",
                          "So far 3 have answered. Enough have taken part for the vote to count.",
                          "So far 4 have answered. Enough have taken part for the vote to count."])

    def test_pc1_04_with_no_minimum_and_nobody_answered_the_line_says_only_that(self):
        """PC1-04 (capture BW, BW4). No minimum and no answer: "So far nobody has answered." Every other case as before."""
        self.assertEqual(progress_line(0, 0), "So far nobody has answered.",
                         "With no minimum and nobody answered, the line must not say that enough have taken part.")
        self.assertEqual([progress_line(answered, 0) for answered in (1, 2)],
                         ["So far 1 has answered. Enough have taken part for the vote to count.",
                          "So far 2 have answered. Enough have taken part for the vote to count."],
                         "Once someone has answered, a vote with no minimum says that enough have taken part.")
        self.assertEqual(progress_line(0, 1), "So far nobody has answered. 1 is needed for the vote to count.",
                         "With a minimum, the line before anyone answers is unchanged.")


class OpenVoteRulePageTests(SimpleTestCase):
    """PB6D-03 to PB6D-05: the block's place in the vote card, its absence after the close, and the read behind it."""

    def test_pb6d_03_the_rule_sits_under_the_ring_and_before_the_members_own_answer(self):
        """Capture AY3. In the vote card: the status line, the ring and counts, the heading, the rows, the meta line, the answer card."""
        page, section = open_section()
        card = children(page, section)[1]
        self.assertEqual([node["attrs"].get("id") or " ".join(classes(node)) for node in children(page, card)],
                         ["meta", "participation-summary", "vote-rule-heading", "result-tests", "vote-progress", "own-answer"])
        heading, rows, line = rule_block(page, section)
        self.assertEqual(heading, "How this vote will be decided")
        self.assertEqual(rows, [(["answer", "pending"], "Needed", PARTICIPATION + " Abstentions count towards taking part."),
                                (["answer", "pending"], "Needed", MAJORITY)])
        self.assertEqual(line, ("So far 2 have answered. 1 more is needed for the vote to count.", ["meta"]))
        self.assertEqual(rule_block(*open_section(answered=3))[2][0], "So far 3 have answered. Enough have taken part for the vote to count.")
        for words in CHOICE_WORDS:
            self.assertNotIn(words, line[0], "The line counts answers and never shows how anyone answered.")
        self.assertEqual(rule_block(*open_section(own=None))[1], rows, "A reader who has not answered, or may not, sees the same rule.")

    def test_pb6d_04_the_rule_is_not_shown_after_the_vote_closes_or_without_the_ballots_record(self):
        """Capture AY3. Past the deadline, after the result, or with no recorded rule at hand: no block."""
        self.assertIsNone(rule_block(*open_section(read_at=WHEN + timedelta(days=4))), "Past its deadline the vote is no longer open.")
        self.assertIsNone(rule_block(*closed_section(LOW, LOW_COUNT, LOW_TALLY)), "After the result, the result's summary takes over.")
        self.assertIsNone(rule_block(*open_section(rule=None)), "Without the ballot's recorded rule no rule is made up.")
        self.assertIsNone(rule_block(*open_section(eligible=2, answered=1)), "Below three eligible no rule is promised.")

    def test_pb6d_05_the_read_takes_the_rule_from_the_ballot_and_the_number_from_the_engine(self):
        """The direction's rule on sources. Every reader of the section receives both; neither comes from a setting."""
        row = {"read_at": 1, "ballot_uuid": uuid4(), "clarity_reader": False}
        counted = [{"attempt": 1, "abstention": True, "eligible": 4, "answered": 2, "own_answer": None, "rule": DEFAULT_RULE, "required": 3}]
        with mock.patch.object(cq.disc, "_rows", return_value=counted) as read:
            ballot = cq._open_ballot({"board_uuid": uuid4()}, uuid4(), SimpleNamespace(pk=7), row)
        statement = read.call_args.args[0]
        self.assertEqual((ballot["rule"], ballot["required"]), (DEFAULT_RULE, 3), "A read-only reader receives the rule and the number required.")
        self.assertNotIn("own_answer", ballot, "The rule adds nothing personal to a read-only reader's ballot.")
        self.assertIn("(o.facts->'rules')-'provisions'-'applied_provisions' AS rule", statement, "The rule is the open ballot's own.")
        self.assertIn("public.cq_count_decision(o.facts->'rules',c.eligible,0,0,0)->>'required'", statement,
                      "The number required is counted by the engine's own function from the ballot's rule.")
        self.assertNotIn("cq_settings", statement, "Today's settings are never read for an open ballot's rule.")


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS, INTEVIA_TIMED_EVENTS_WAKE=False)
class OpenVoteRuleRealPageTests(TransactionTestCase):
    """PB6D-06: the real Topic while a Board of three votes, before and after enough have answered."""

    def test_pb6d_06_the_open_votes_page_states_the_recorded_rule_and_counts_towards_it(self):
        """Capture AY3. The rows are the builders' words for the ballot's own rule; the line moves as answers arrive."""
        case = open_board_vote()
        status = cq_reads.vote_status(**case.scope)
        stored = disc._rows("SELECT (facts->'rules')-'provisions'-'applied_provisions' AS rule FROM public.core_cqact WHERE act_uuid=%s",
                            [status["ballot"]])[0]["rule"]
        expected = vote_rule_rows(3, 3, stored)
        reader = signed_in(case.people[2])
        page = Markup(reader.get(case.url).content.decode())
        heading, rows, line = rule_block(page, page.select("section", id="vote")[0])
        self.assertEqual((heading, [text for _, _, text in rows]), ("How this vote will be decided", [row["text"] for row in expected]))
        self.assertEqual(line[0], "So far nobody has answered. 3 are needed for the vote to count.",
                         "A Board of three needs all three by the default rule's floor of three.")
        for person in case.people[:2]:
            cq.answer(**(case.scope | {"credential": person.credential}), ballot_uuid=status["ballot"], choice="QUALIFIED", request_uuid=uuid4())
        page = Markup(reader.get(case.url).content.decode())
        line = rule_block(page, page.select("section", id="vote")[0])[2][0]
        self.assertEqual(line, "So far 2 have answered. 1 more is needed for the vote to count.")
        for words in CHOICE_WORDS:
            self.assertNotIn(words, line)
