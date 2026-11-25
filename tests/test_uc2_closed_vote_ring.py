"""The posts box, part PB-5: the rings.

Rulings of 24 Nov 2026: capture AE (Q11: the ring's centre shows the person icon and the total
only), capture AF (Q12: a closed Board vote shows its result ring, the counts and, under NAMED
reporting, who answered what), capture AK (AK4: those who did not answer are named too) and capture
AM (AM1: a name is dropped once the person's identity link is severed).

The database fixtures are built through the real services, rows per test. One owner power is used
and named where it is used (PB5-03).
"""
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from django.db import connection
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.urls import reverse

from core.ui import count_ring, result_ring
from intevia.test_postgresql_backend import clock
from src.intevia.services import circle_commands, cq, cq_reads
from tests import cq_authority_support as boards
from tests import test_discussion_posts as post_support
from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import signed_in
from tests.test_uc2_pages import FIXTURE_HASHERS, path


def shown(page, within):
    """What a reader is shown of one ring: its words, its arcs, the counts beside it and the name rows."""
    ring, = page.select("div", within=within, classes=("participation-ring",))
    summary, = page.select("div", within=within, classes=("participation-summary",))
    lists = page.select("dl", within=within, classes=("vote-names",))
    return {
        "words": (ring["attrs"].get("role"), ring["attrs"].get("aria-label")),
        "centre": [node["text"].strip() for node in page.select("div", within=ring, classes=("ring-centre",))],
        "arcs": [(node["attrs"]["class"], node["attrs"]["stroke-dasharray"].split()[0])
                 for node in page.select("circle", within=ring, classes=("ring-segment",))],
        "counts": [(" ".join(p["text"].split()[1:-1]), p["text"].split()[-1]) for p in page.select("p", within=summary)],
        "names": [list(zip([dt["text"].strip() for dt in page.select("dt", within=rows)],
                           [dd["text"].strip() for dd in page.select("dd", within=rows)], strict=True)) for rows in lists],
    }


def tally(**changes):
    return {"eligible": 4, "for": 2, "against": 1, "abstain": 0, "no_answer": 1, "abstain_allowed": False,
            "names": None} | changes


class RingProjectionTests(SimpleTestCase):
    """PB5-01, PB5-04 and PB5-05: the shared ring component and the tally's projection, with no database."""

    def test_pb5_01_the_open_rings_centre_is_the_icon_and_the_total_only(self):
        """Capture AE, Q11. The centre has no "Board members"; the ring's accessible words keep the full sentence."""
        markup = render_to_string("core/components/participation_ring.html", {"ring": count_ring(1, 3, "Board members")})
        page = Markup(markup)
        ring, = page.select("div", classes=("participation-ring",))
        centre, = page.select("div", within=ring, classes=("ring-centre",))
        self.assertEqual(centre["text"].strip(), "3", "The centre must show the total and no other words.")
        self.assertEqual(len(page.select("svg", within=centre)), 1, "The centre must keep the person icon.")
        self.assertNotIn("Board members", centre["text"], "The centre must not name the electorate.")
        self.assertEqual((ring["attrs"]["role"], ring["attrs"]["aria-label"]), ("img", "1 of 3 Board members have answered"),
                         "The ring's accessible words must keep the full sentence, electorate included.")

    def test_pb5_04_a_totals_only_result_shows_the_ring_and_counts_and_no_name(self):
        """Capture AF. A result that does not carry its named answers is never asked for names, and the page lists none."""
        record = {"event_kind": "DECISION", "ballot": str(uuid4()), "post_uuid": uuid4(),
                  "count": {"kind": "QUALIFIED", "eligible": 4, "participated": 3, "qualified": 2,
                            "not_qualified": 1, "abstained": 0}}
        with mock.patch.object(cq_reads.disc, "_rows", return_value=[{"abstain_allowed": False, "people": None}]) as rows:
            counted = cq_reads._tally(uuid4(), uuid4(), record)
        self.assertIs(rows.call_args.args[1][0], False, "A totals-only result must not ask the record for anyone's name.")
        self.assertEqual(counted, tally(), "The counts must be the result's own, with no names.")
        named = record | {"participants": [{"identity": "a", "name": "Recorded name", "answer": "QUALIFIED"}]}
        people = [{"answer": "QUALIFIED", "name": "Mira Alder"}, {"answer": "NOT_QUALIFIED", "name": "Rowan Fell"},
                  {"answer": "QUALIFIED", "name": "Member"}, {"answer": None, "name": "Kit Vale"}]
        with mock.patch.object(cq_reads.disc, "_rows", return_value=[{"abstain_allowed": False, "people": people}]) as rows:
            counted = cq_reads._tally(uuid4(), uuid4(), named)
        self.assertIs(rows.call_args.args[1][0], True, "The positive control: a named result asks for the names.")
        self.assertEqual(counted["names"], {"QUALIFIED": ["Member", "Mira Alder"], "NOT_QUALIFIED": ["Rowan Fell"],
                                            "ABSTAIN": [], "NO_ANSWER": ["Kit Vale"]},
                         "Each person must fall under their own answer, and under no answer where they gave none.")
        vote = {"phase": "RESULT", "line": "Decided · Carried", "wording_heading": "Wording decided", "markdown": "Words",
                "version_number": 1, "ring": result_ring(tally(), "Board members")}
        page = Markup(render_to_string("core/components/board_vote.html", {"vote": vote}))
        section, = page.select("section", id="vote")
        seen = shown(page, section)
        self.assertEqual(seen["counts"], [("For", "2"), ("Against", "1"), ("Did not answer", "1")],
                         "Under totals only the counts must still be shown.")
        self.assertEqual((seen["names"], page.select("dl", within=section)), ([], []),
                         "Under totals only the vote section must list no one.")
        for election in ({"event_kind": "ELECTION"}, {"ballot": None}):
            with mock.patch.object(cq_reads.disc, "_rows") as rows:
                self.assertIsNone(cq_reads._tally(uuid4(), uuid4(), named | election),
                                  "An election, or a result with no ballot, has no ring of answers.")
            rows.assert_not_called()

    def test_pc1_05_each_rows_names_are_alphabetical_whatever_order_the_accounts_were_made_in(self):
        """PC1-05 (capture BW, BW5). Alphabetical by display name in each row, case-insensitive; equal names keep account order."""
        record = {"event_kind": "DECISION", "ballot": str(uuid4()), "post_uuid": uuid4(), "participants": [],
                  "count": {"kind": "QUALIFIED", "eligible": 9, "participated": 7, "qualified": 5,
                            "not_qualified": 2, "abstained": 0}}
        # The read gives people in account order. These accounts were made in an order that is not alphabetical.
        in_account_order = [("QUALIFIED", "rowan Fell"), ("QUALIFIED", "Zed Marsh"), ("QUALIFIED", "Member"),
                            ("QUALIFIED", "Élan Moor"), ("QUALIFIED", "Mira Alder"),
                            ("NOT_QUALIFIED", "KIT VALE"), ("NOT_QUALIFIED", "Kit Vale"),
                            (None, "Sol Reed"), (None, "Ada Birch")]
        people = [{"answer": answer, "name": name} for answer, name in in_account_order]
        with mock.patch.object(cq_reads.disc, "_rows", return_value=[{"abstain_allowed": False, "people": people}]):
            names = cq_reads._tally(uuid4(), uuid4(), record)["names"]
        self.assertEqual(names["QUALIFIED"], ["Élan Moor", "Member", "Mira Alder", "rowan Fell", "Zed Marsh"],
                         "Names must read alphabetically whatever their case, an accented letter beside its plain "
                         "letter, with \"Member\" sorted as its own word.")
        self.assertEqual(names["NOT_QUALIFIED"], ["KIT VALE", "Kit Vale"],
                         "Names that differ only by case keep the read's own order, so the order never changes between reads.")
        self.assertEqual(names["NO_ANSWER"], ["Ada Birch", "Sol Reed"], "Those who did not answer are ordered the same way.")

    def test_pb5_05_the_result_ring_splits_by_answer_and_names_each_row(self):
        """Captures AF and AK4. For, Against, Abstain only where allowed, Did not answer; rows with no one are left out."""
        ring = result_ring(tally(names={"QUALIFIED": ["Mira <Alder>", "Rowan Fell"], "NOT_QUALIFIED": ["Sol Reed"],
                                        "ABSTAIN": [], "NO_ANSWER": ["Kit Vale"]}), "Board members")
        self.assertEqual(ring["text"], "4 Board members: 2 For, 1 Against, 1 Did not answer",
                         "The ring must say its whole count in words for a screen reader.")
        self.assertEqual([(s["key"], s["length"], s["glass"]) for s in ring["segments"]],
                         [("consented", "50.000", True), ("declined", "25.000", True), ("pending", "25.000", False)],
                         "For takes the For green, Against the Against red, and the share that did not answer is flat grey.")
        markup = render_to_string("core/components/vote_ring.html", {"ring": ring})
        seen = shown(Markup("<main>" + markup + "</main>"), None)
        self.assertEqual(seen["centre"], ["4"], "The closed ring's centre is the icon and the total, as the open ring's.")
        self.assertEqual(seen["counts"], [("For", "2"), ("Against", "1"), ("Did not answer", "1")],
                         "The counts beside the ring must read in words, as the open ring's do.")
        self.assertEqual(seen["names"], [[("For", "Mira <Alder>, Rowan Fell"), ("Against", "Sol Reed"), ("Did not answer", "Kit Vale")]],
                         "Each answer's row must list its people, those who did not answer included.")
        self.assertNotIn("<Alder>", markup, "A name must reach the page as text, never as markup.")
        with_abstain = result_ring(tally(**{"for": 1, "abstain": 1, "abstain_allowed": True}), "Board members")
        self.assertEqual([(c["key"], c["label"], c["count"]) for c in with_abstain["counts"]],
                         [("consented", "For", 1), ("declined", "Against", 1), ("abstained", "Abstain", 1), ("pending", "Did not answer", 1)],
                         "Where the vote allowed Abstain it has its own row and arc, in the shared amber (capture AO1), "
                         "between Against and Did not answer.")
        self.assertNotIn("names", with_abstain, "A tally without names must give the page no name rows.")
        none_abstained = result_ring(tally(abstain_allowed=True), "Board members")
        self.assertEqual(none_abstained["counts"][2], {"key": "abstained", "symbol": "—", "label": "Abstain", "count": 0},
                         "Where Abstain was allowed and no one chose it, its count still shows, as zero.")
        only_for = result_ring(tally(names={"QUALIFIED": ["Mira Alder"], "NOT_QUALIFIED": [], "ABSTAIN": [], "NO_ANSWER": []}),
                               "Board members")
        self.assertEqual(only_for["names"], [{"label": "For", "names": ["Mira Alder"]}], "A row with no one is left out.")

    def test_pb5_06_the_vote_section_takes_the_ring_of_the_result_its_ballot_produced(self):
        """Capture AF. Only a recorded ordinary result gives the vote section a ring, and it is that ballot's own result."""
        from core import discussion_pages

        ballot, earlier, own = uuid4(), uuid4(), uuid4()
        posts = [{"post_number": 1, "markdown": "Words"},
                 {"post_type": "GROUP_GAZE", "hidden": False, "ballot": str(uuid4()), "post_uuid": earlier,
                  "tally": tally(**{"for": 0, "against": 3})},
                 {"post_type": "GROUP_GAZE", "hidden": False, "ballot": str(ballot), "post_uuid": own, "tally": tally()}]

        def ring(**vote):
            context = {"posts": posts, "vote": {"phase": "RESULT", "ballot_uuid": ballot, "election": False} | vote}
            discussion_pages._closed_vote_ring(context)
            return context["vote"].get("ring")

        self.assertEqual(ring()["text"], "4 Board members: 2 For, 1 Against, 1 Did not answer",
                         "The vote section must draw the result this ballot produced, not an earlier ballot's.")
        for vote in ({"phase": "VOTING"}, {"election": True}, {"ballot_uuid": None}, {"ballot_uuid": uuid4()}):
            with self.subTest(vote=sorted(vote)):
                self.assertIsNone(ring(**vote), "An open vote, an election, a result with no ballot or another "
                                                "ballot's result gets no ring.")
        posts[2]["tally"] = None
        self.assertIsNone(ring(), "A result that is not released to this reader, or has no tally, gives no ring.")


CARRIED_ANSWERS = ((0, "QUALIFIED"), (2, "QUALIFIED"), (1, "NOT_QUALIFIED"))


def closed_board_vote(answers=CARRIED_ANSWERS):
    """Seat a Board of four, open one Suggestion's vote with Clarity off, and close it at its deadline.

    By default two answer For, one Against and one does not answer, so the vote is carried with a
    non-answerer. ``answers`` names other trustees and choices, by the trustee's place in ``people``.
    The fixture follows ``tests.test_uc2_ballot_reads.open_board_vote``, with a fourth trustee. The
    INTEVIA Clock is moved to the ballot's own deadline, and the due result is recorded by the rule's
    catch-up, as a member's "Record the result" does.
    """
    owner, scope, people, board = boards.ordinary(4, board=True)
    founder, coordinator = people[0], people[1]
    cq.change_settings(**scope, predecessor=None, request_uuid=uuid4(), changes={"enabled": True, "clarity_stage": False})
    circle_commands.activate(**scope, circle_uuid=board.circle_uuid, request_uuid=uuid4())
    boards.appoint_board_coordinator(owner, board, founder, coordinator)
    support = post_support.DiscussionPostTests()
    args = support.board(owner, coordinator, board)
    topic = support.start(args, topic_type="SUGGESTION", markdown="Approve one payment.")
    scope = support.read_args(args) | {"thread_uuid": topic["thread"]}
    cq.put_forward(**scope, request_uuid=uuid4(), version_uuid=topic["version"])
    cq.mark_prepared(**scope, request_uuid=uuid4())
    status = cq_reads.vote_status(**scope)
    for place, choice in answers:
        cq.answer(**(scope | {"credential": people[place].credential}), ballot_uuid=status["ballot"], choice=choice,
                  request_uuid=uuid4())
    clock.advance_to(status["deadline"])
    cq.catch_up(credential=founder.credential)
    closed = cq_reads.vote_status(**scope)
    return SimpleNamespace(owner=owner, board=board, people=people, scope=scope, status=closed,
                           url=path(owner, board, topic["thread"]),
                           result_url=reverse("discussion_result", args=(owner.organism_id, board.circle_uuid,
                                                                         topic["thread"], closed["result_uuid"]))
                           if closed.get("result_uuid") else None)


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS, INTEVIA_TIMED_EVENTS_WAKE=False)
class ClosedVoteRingPageTests(TransactionTestCase):
    """PB5-02 and PB5-03: the real Topic and result pages after a Board vote has closed, under NAMED reporting."""

    def expected(self, case, last="unchanged"):
        first, second, third, fourth = [person.display_name for person in case.people]
        return {"words": ("img", "4 Board members: 2 For, 1 Against, 1 Did not answer"), "centre": ["4"],
                "arcs": [("ring-segment consented glass", "50.000"), ("ring-segment declined glass", "25.000"),
                         ("ring-segment pending", "25.000")],
                "counts": [("For", "2"), ("Against", "1"), ("Did not answer", "1")],
                # PC1-05 (capture BW, BW5): the two For names read alphabetically, not in account order.
                "names": [[("For", ", ".join(sorted([first, third], key=str.casefold))), ("Against", second),
                           ("Did not answer", fourth if last == "unchanged" else last)]]}

    def closed(self):
        case = closed_board_vote()
        self.assertEqual((case.status["phase"], case.status["reporting_rule"]["value"]), ("RESULT", "NAMED"),
                         "The fixture must have recorded the result, under the Board's default of reporting by name.")
        names = [person.display_name for person in case.people]
        self.assertEqual(len({name for name in names if name.strip() and name != "Member"}), 4,
                         "Each fixture trustee needs their own real name for this witness to mean anything.")
        return case

    def topic_ring(self, client, case):
        page = Markup(client.get(case.url).content.decode())
        section, = page.select("section", id="vote")
        return shown(page, section)

    def result_ring(self, client, case):
        page = Markup(client.get(case.result_url).content.decode())
        main, = page.select("main")
        return shown(page, main)

    def test_pb5_02_a_closed_board_vote_shows_the_split_the_counts_and_who_answered_what(self):
        """Captures AF and AK4. The Topic's vote section: the split ring, the counts, and three name rows with the right people."""
        case = self.closed()
        seen = self.topic_ring(signed_in(case.people[3]), case)
        self.assertEqual(seen, self.expected(case),
                         "The closed vote must show For, Against and Did not answer as arcs, as counts and by name, "
                         "with the trustee who did not answer named.")

    def test_pb5_03_the_result_page_agrees_with_the_topic_and_a_severed_name_is_dropped(self):
        """Captures AF and AM1. The result page shows the same ring as the Topic; a severed member reads "Member" on both."""
        case = self.closed()
        reader = signed_in(case.people[0])
        on_topic, on_result = self.topic_ring(reader, case), self.result_ring(reader, case)
        self.assertEqual(on_result, self.expected(case), "The result page must show the split, the counts and the names.")
        self.assertEqual(on_result, on_topic, "The result page and the Topic must show the same ring.")
        # Owner power, named (item 121): no service severs an identity link yet, so the test removes the
        # non-answerer's resolution row directly, as the landed office-record witnesses do.
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM public.core_identityresolution WHERE identity_id=%s", [case.people[3].pk])
            cursor.execute("SELECT EXISTS (SELECT FROM public.core_identityresolutionsevered WHERE identity_id=%s)",
                           [case.people[3].pk])
            self.assertTrue(cursor.fetchone()[0], "The database must witness the severance before the final read.")
        self.assertEqual((self.topic_ring(reader, case), self.result_ring(reader, case)),
                         (self.expected(case, last="Member"),) * 2,
                         "Once a member's identity link is severed, both pages must stop showing that name.")
