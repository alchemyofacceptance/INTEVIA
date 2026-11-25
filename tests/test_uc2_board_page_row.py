"""Posts box PB-7. The Board page's "Board discussion" row, the glass lists and the glass organisation chip.

HG capture AG of 24 Nov 2026: AG1 (a teal "Board discussion" section with one row in the shared list
pattern and the count of Topics this reader may open), AG2 (the shared list takes the cards' glass and
purple stripe) and AG3 (every organisation chip takes the glass, with no stripe).
"""
import re
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.staticfiles import finders
from django.test import RequestFactory, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import resolve, reverse

from core import organisation_pages as pages
from tests.test_f0_ui import Markup
from tests.test_uc1_render import declarations

SHARED_GLASS = ".panel,.event-item,.card,.notice,.circle-list,.org-chip"
FIXTURE_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def by_id(page, tag, identifier):
    return [node for node in page.select(tag) if node["attrs"].get("id") == identifier]


class BoardDiscussionRowTests(SimpleTestCase):
    """The Board page as the template draws it, from a controlled navigation read. No database."""

    def setUp(self):
        self.owner = {"organism_uuid": uuid4(), "full_name": "Alder Beck Community Trust", "short_label": "Alder Beck"}
        self.board = {"circle_uuid": uuid4(), "kind": "GOVERNANCE", "full_name": "Board of Trustees", "short_label": "Board"}

    def read(self, **discussion_facts):
        org, circle = self.owner["organism_uuid"], self.board["circle_uuid"]
        path = reverse("circle_page", args=(org, circle))
        request = RequestFactory().get(path)
        request.resolver_match = match = resolve(path)
        request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Mira")
        request.intevia_identity = SimpleNamespace(display_name="Mira", access_state="active", colour_mode="light")
        projection = {"organisation": self.owner, "circles": [self.board], "board_offices": [],
                      "selected_circle": {**self.board, "description": None}, **discussion_facts}
        with patch.object(pages, "_memberships", return_value=[org]), \
             patch.object(pages.transaction, "atomic", return_value=nullcontext()), \
             patch.object(pages.navigation, "member_navigation", return_value=projection):
            return Markup(match.func(request, **match.kwargs).content.decode())

    def test_pb7_01_the_board_discussion_is_a_headed_row_with_its_topic_count(self):
        """PB7-01. A teal "Board discussion" heading over one shared-list row: label, Topic count, chevron.

        With no Topic this reader may open, the row reads "No Topics yet" (HG capture BJ of 25 Nov 2026, BJ2).
        """
        index = reverse("board_discussion", args=(self.owner["organism_uuid"], self.board["circle_uuid"]))
        for count, words in ((3, "3 Topics"), (1, "1 Topic"), (0, "No Topics yet")):
            with self.subTest(count=count):
                page = self.read(discussion_available=True, discussion_topic_count=count)
                heading, = by_id(page, "h2", "board-discussion-heading")
                self.assertEqual(heading["text"].strip(), "Board discussion",
                                 "The Board discussion must have its own heading on the Board page.")
                self.assertIn("record-heading", heading["attrs"].get("class", "").split(),
                              "The Board discussion heading must be the shared teal section heading.")
                rows, = page.select("ul", classes=("circle-list", "board-discussion-row"))
                link, = page.select("a", within=rows)
                self.assertEqual(link["attrs"]["href"], index, "The row must open this Board's discussion.")
                label, = by_id(page, "strong", "board-discussion-label")
                self.assertEqual(label["text"].strip(), "Topics and Board votes", "The row must be named for what it opens.")
                shown, = by_id(page, "small", "board-discussion-count")
                self.assertEqual(shown["text"].strip(), words, "The row must give this reader's Topic count beneath its name.")
                self.assertEqual((link["attrs"].get("aria-labelledby"), link["attrs"].get("aria-describedby")),
                                 ("board-discussion-label", "board-discussion-count"),
                                 "A screen reader must hear the row's name, then its Topic count.")
                self.assertFalse(page.select("p", classes=("board-discussion-destination",)),
                                 "The plain Board discussion link must be gone from the Board page.")
                self.assertNotIn("board-discussion-destination", rows["attrs"]["class"].split(),
                                 "The row must not take the old link's class, which sets it 32 px lower than the offices list.")

    def test_pb7_02_a_reader_without_the_discussion_gets_no_section_and_no_hint(self):
        """PB7-02. No discussion for this reader: no heading, no row, no count and no word of it."""
        for facts in ({}, {"discussion_available": False}, {"discussion_available": False, "discussion_topic_count": 3}):
            with self.subTest(facts=facts):
                page = self.read(**facts)
                self.assertFalse(by_id(page, "h2", "board-discussion-heading"),
                                 "A reader who cannot see the discussion must get no Board discussion heading.")
                self.assertFalse(page.select("ul", classes=("board-discussion-row",)),
                                 "A reader who cannot see the discussion must get no Board discussion row.")
                words = " ".join(node["text"] for node in page.select("main"))
                for hint in ("Board discussion", "Topics and Board votes", "Topic"):
                    self.assertNotIn(hint, words, "A reader who cannot see the discussion must get no hint of it: " + hint)


class SharedGlassStyleTests(SimpleTestCase):
    """The served stylesheet: one glass, shared by cards, voice boxes, lists and organisation chips. No database."""

    def sheet(self):
        return Path(finders.find("core/f0.css")).read_text(encoding="utf-8")

    def test_pb7_03_lists_and_chips_take_the_one_shared_glass(self):
        """PB7-03. The lists and the chip are in the shared glass rule; no rule gives them a glass of their own."""
        sheet = self.sheet()
        glass = declarations(sheet, SHARED_GLASS)
        self.assertIn("--glass-shine", glass.get("background-image", ""),
                      "The Circle and office lists and the organisation chip must be in the one shared glass rule.")
        self.assertIn("inset", glass.get("box-shadow", ""), "The shared glass must give lists and chips its rim and shadow.")
        painters = {match[1].strip() for match in re.finditer(r"(?:^|[{}])([^{}]+)\{([^{}]*)\}", sheet)
                    if "background-image" in match[2] and "--glass-shine" in match[2]}
        self.assertEqual(painters, {SHARED_GLASS},
                         "Only the one shared rule may paint the glass; no section, list or chip may carry a copy.")
        for selector, what in ((".circle-list", "The shared list"), (".org-chip", "The organisation chip"),
                               (".org-chip[aria-current=true]", "The current organisation's chip")):
            own = declarations(sheet, selector)
            self.assertIn("background-color", own, what + " must set its surface colour.")
            self.assertNotIn("background", own, what + " must set a colour only, or it would wipe the shared glass off.")
            self.assertNotIn("background-image", own, what + " must not paint a glass of its own.")
            self.assertNotIn("box-shadow", own, what + " must not cast a shadow of its own.")

    def test_pb7_04_a_list_has_the_purple_stripe_and_a_chip_has_none(self):
        """PB7-04. AG2: the list takes the cards' purple stripe. AG3: the chip takes the glass with no stripe."""
        sheet = self.sheet()
        card = declarations(sheet, ".panel,.event-item,.card")
        rows = declarations(sheet, ".circle-list")
        self.assertEqual(rows.get("border-left"), "var(--stripe) solid var(--color-primary)",
                         "The shared list must carry the purple stripe.")
        self.assertEqual(rows.get("border-left"), card.get("border-left"), "The list's stripe must be the cards' own stripe.")
        chip = declarations(sheet, ".org-chip")
        self.assertNotIn("border-left", chip, "An organisation chip must have no stripe.")
        self.assertEqual(chip.get("border"), "var(--stroke) solid var(--color-control)",
                         "An organisation chip must keep its even outline all round.")
        self.assertEqual(chip.get("border-radius"), "var(--radius-pill)", "An organisation chip must stay a pill.")


@override_settings(ALLOWED_HOSTS=["testserver"], INTEVIA_TIMED_EVENTS_WAKE=True, PASSWORD_HASHERS=FIXTURE_HASHERS)
class BoardTopicCountTests(TransactionTestCase):
    """The count on the Board page, read through the real pages and services with each reader's own grants."""

    def shown_count(self, client, owner, board):
        response = client.get(reverse("circle_page", kwargs={"org": owner.organism_id, "circle": board.circle_uuid}))
        page = Markup(response.content.decode())
        shown = by_id(page, "small", "board-discussion-count")
        return response, (shown[0]["text"].strip() if shown else None)

    def listed(self, client, index):
        page = Markup(client.get(index).content.decode())
        return sum(len(page.select("a", within=topics)) for topics in page.select("ul", classes=("discussion-topics",)))

    def test_pb7_05_the_count_is_the_topics_this_reader_may_open(self):
        """PB7-05. Each reader's count equals the Topics the Board discussion lists for that reader; no title leaks."""
        from tests import test_discussion_posts as post_support
        from tests.test_uc1_office_page import signed_in
        from tests.test_uc2_pages import path, read_counts, seated_board
        from src.intevia.services import discussion

        owner, board, _, coordinator, trustee = seated_board()
        reader, steward = signed_in(trustee), signed_in(coordinator)
        index = path(owner, board)
        self.assertEqual(self.shown_count(reader, owner, board)[1], "No Topics yet",
                         "An opened discussion with no Topic must say so on the Board page (capture BJ2).")
        support = post_support.DiscussionPostTests()
        args = support.board(owner, coordinator, board)
        support.start(args, title="Open question one")
        support.start(args, title="Open question two")
        concealed = support.start(args, title="Withheld opening")
        support.hide(args, concealed, True, as_coordinator=True)
        categories = discussion.board_categories(**support.read_args(args))["categories"]
        private = next(cat for cat in categories if cat["functional_code"] == "COORDINATION")
        support.start(args | {"category_uuid": private["category_uuid"]}, title="Private coordination")
        with patch("src.intevia.services.timed_events.run") as wake:
            before = read_counts()
            response, shown = self.shown_count(reader, owner, board)
            self.assertEqual(shown, "2 Topics", "A trustee's count must hold only the two Topics that trustee may open.")
            self.assertEqual(shown, f"{self.listed(reader, index)} Topics",
                             "The trustee's count must equal the Topics the Board discussion lists for that trustee.")
            for withheld in ("Open question one", "Withheld opening", "Private coordination"):
                self.assertNotContains(response, withheld,
                                       msg_prefix="The Board page gives a count only, never a Topic's or category's name.")
            _, wider = self.shown_count(steward, owner, board)
            self.assertEqual(wider, f"{self.listed(steward, index)} Topics",
                             "The Coordinator's count must equal the Topics the Board discussion lists for the Coordinator.")
            self.assertEqual(wider, "4 Topics", "The Coordinator may open the hidden and the private Topic, so counts four.")
            self.assertEqual(read_counts(), before, "Reading the Board page's count must record nothing.")
        self.assertFalse(wake.called, "Reading the Board page's count must never wake due decisions.")

    def test_pb7_06_a_dormant_board_shows_no_discussion_section(self):
        """PB7-06. Before the discussion exists the Board page has no Board discussion section, row or count."""
        from tests.test_board_discussion_opening import seated
        from tests.test_uc1_office_page import signed_in

        owner, scope, trustees, board = seated()
        response, shown = self.shown_count(signed_in(trustees[1]), owner, board)
        self.assertEqual(response.status_code, 200, "A trustee must be able to read a dormant Board's page.")
        self.assertIsNone(shown, "A dormant Board has no discussion, so its page must show no Topic count.")
        for hint in ("board-discussion-heading", "Board discussion", "Topics and Board votes"):
            self.assertNotContains(response, hint, msg_prefix="A dormant Board's page must give no hint of a discussion.")
