"""C-1a.2 · member destinations, public entry, and the selected Circle's facts."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.staticfiles import finders
from django.test import Client, RequestFactory, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import resolve, reverse

from core import models, organisation_pages as pages
from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import assert_private, signed_in
from tests.test_uc1_render import declarations


def markup(response):
    return Markup(response.content.decode())


def member_url(org, circle=None):
    if circle is None:
        return reverse("organisation_home", args=(org,))
    return reverse("circle_page", args=(org, circle))


def chips(page):
    return {n["attrs"]["href"]: (n["text"].strip(), n["attrs"].get("aria-current"))
            for n in page.select("a", classes=("org-chip",))}


@override_settings(ALLOWED_HOSTS=["testserver"])
class OrganisationRouteTests(TransactionTestCase):
    def test_entry_selects_only_own_organisations(self):
        from tests.test_law_acts import add_member, ordinary_birth
        from tests.s015_0021_support import register_test_learner

        entry = reverse("f0-organisation")
        visitor = Client()
        with patch.object(pages.OrganismMembershipReader, "list_own_memberships") as reads:
            public = visitor.get(entry)
            self.assertContains(public, "Join or found an organisation", msg_prefix="A visitor keeps the public landing.")
            self.assertFalse(reads.called, "A visitor must never ask the private membership reader.")
        empty = signed_in(register_test_learner(username="circle-reader-" + uuid4().hex))
        zero = empty.get(entry)
        self.assertContains(zero, "Join or found an organisation", msg_prefix="Zero memberships keep the public landing.")
        self.assertFalse(chips(markup(zero)), "The public landing must disclose no organisation chips.")
        assert_private(self, zero)

        owner, _ = ordinary_birth()
        member = add_member(owner)
        client = signed_in(member)
        home = member_url(owner.organism_id)
        self.assertRedirects(client.get(entry), home, fetch_redirect_response=False,
                             msg_prefix="Exactly one own membership must open its canonical home.")
        other, _ = ordinary_birth()
        second = member_url(other.organism_id)
        absent = client.get(member_url(uuid4()))
        foreign = client.get(second)
        self.assertEqual((foreign.status_code, foreign.content), (404, absent.content),
                         "A foreign selection must use the neutral refusal instead of opening another home.")
        assert_private(self, foreign)

        # Bind the second JOIN fixture to the same identity. Its membership and event are real.
        with patch("tests.test_sr_office_guards.seed_identity", return_value=member.pk):
            add_member(other)
        heads = list(models.LivingOrganism.objects.order_by("pk").values_list("head_event_id", flat=True))
        def name(org):
            return models.LOFoundingContentVersion.objects.get(living_organism=org, version=1).name
        labels = {home: name(owner), second: name(other)}
        chooser = client.get(entry)
        self.assertEqual(chooser.status_code, 200, "Several memberships must open the chooser without selecting the first.")
        self.assertEqual(chips(markup(chooser)), {url: (label, None) for url, label in labels.items()},
                         "The chooser must link exactly both own organisations and mark neither current.")
        for url in (home, second):
            selected = client.get(url)
            self.assertEqual(chips(markup(selected)), {u: (n, "true" if u == url else None) for u, n in labels.items()},
                             "The explicit URL alone must select the current organisation chip.")
            self.assertEqual([h["text"] for h in markup(selected).select("h1")], [labels[url]],
                             "The organisation's full name must head its own screen.")
            assert_private(self, selected)
            self.assertEqual(client.head(url).status_code, 200, "HEAD must be a lawful read of a member home.")
        self.assertEqual(chips(markup(client.get(entry))), chips(markup(chooser)),
                         "Visiting an organisation must leave the next chooser unselected.")
        self.assertEqual(list(models.LivingOrganism.objects.order_by("pk").values_list("head_event_id", flat=True)), heads,
                         "Navigation must leave every organisation history head in place.")

    def test_member_can_open_the_board_without_an_operating_claim(self):
        from tests.test_law_acts import add_member, ordinary_birth

        owner, _ = ordinary_birth(board_seats=1)
        board = models.Circle.objects.get(parent_organism=owner, kind="GOVERNANCE")
        member = add_member(owner)
        client = signed_in(member)
        url = member_url(owner.organism_id, board.circle_uuid)
        home = markup(client.get(member_url(owner.organism_id)))
        destination = home.select("a", href=url)
        self.assertEqual([n["text"] for link in destination for n in home.select("strong", within=link)], ["Board"],
                         "The organisation directory must link the Board by its short label.")
        self.assertEqual([n["text"] for link in destination for n in home.select("small", within=link)], ["Governance Circle"],
                         "The directory must identify the Board by kind without assigning operating status.")
        response = client.get(url)
        page = markup(response)
        self.assertEqual([n["text"] for n in page.select("h1")], ["Board of Trustees"],
                         "The Board heading must use its full name.")
        self.assertContains(response, "Governance Circle", msg_prefix="The page must identify the kind of Circle.")
        self.assertContains(response, "The Board of Trustees is the organisation", msg_prefix="The Board must have its admitted Description.")
        self.assertFalse(page.select("section", **{"aria-labelledby": "status-heading"}),
                         "A listed Board must make no claim about operating status.")
        assert_private(self, response)
        for path in (member_url(owner.organism_id), url):
            refused = Client().get(path)
            self.assertRedirects(refused, reverse("login") + "?next=" + path, fetch_redirect_response=False,
                                 msg_prefix="A private destination must redirect a visitor back through sign-in.")
            assert_private(self, refused)
            self.assertEqual(client.post(path).status_code, 405, "Neither member read may accept a command.")
        other, _ = ordinary_birth(board_seats=1)
        foreign_circle = models.Circle.objects.get(parent_organism=other, kind="GOVERNANCE")
        wrong = client.get(member_url(owner.organism_id, foreign_circle.circle_uuid))
        unknown = client.get(member_url(owner.organism_id, uuid4()))
        self.assertEqual((wrong.status_code, wrong.content), (404, unknown.content),
                         "A wrong-parent Circle and an absent Circle must have the same neutral page.")
        assert_private(self, wrong)

    def test_nonparticipant_reads_description_and_participant_reads_status(self):
        from tests.discussion_support import independent_circle
        from tests.test_law_acts import add_member, ordinary_birth

        owner, founder = ordinary_birth()
        circle, participant = independent_circle(owner, founder, name="River stewards")
        args = {"organism_uuid": owner.organism_id, "circle_uuid": circle.circle_uuid}
        url = member_url(owner.organism_id, circle.circle_uuid)
        ordinary = signed_in(add_member(owner)).get(url)
        self.assertContains(ordinary, "DISC1 independent Circle office",
                            msg_prefix="A non-participant may read the exact stored purpose.")
        self.assertNotContains(ordinary, "Operating status", msg_prefix="An outside member sees no operating block.")
        # The founder still holds the parent office after leaving this Circle.
        broader = pages.circle_commands.operating_status_detail(credential=founder.credential, **args)
        self.assertEqual(broader["state"], "ACTIVE", "The existing parent-office status read must still succeed.")
        former = signed_in(founder).get(url)
        self.assertNotContains(former, "Operating status",
                               msg_prefix="A parent Coordinator without a Circle place must see no operating block.")
        inside = signed_in(participant).get(url)
        block = markup(inside).select("section", **{"aria-labelledby": "status-heading"})
        self.assertTrue(len(block) == 1 and "Active" in block[0]["text"],
                        "A current Circle participant must see its admitted operating state.")
        self.assertNotContains(inside, "Deadline", msg_prefix="An absent deadline must produce no deadline label.")
        assert_private(self, inside)


class OrganisationPresentationTests(SimpleTestCase):
    """Controlled presentation cells for distinct labels, uncommon states and exact text boundaries."""
    def setUp(self):
        self.owner = {"organism_uuid": uuid4(), "full_name": "Alder Beck Community Trust", "short_label": "Alder Beck"}
        self.circle = {"circle_uuid": uuid4(), "kind": "GENERIC", "full_name": "River stewards", "short_label": "River"}
        self.scope = {"credential": SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Mira"),
                      "organism_uuid": self.owner["organism_uuid"]}

    def read(self, *, text=None, mode="light", detail=None, target="circle"):
        from contextlib import nullcontext
        org, circle = self.owner["organism_uuid"], self.circle["circle_uuid"]
        path = member_url(org, circle if target == "circle" else None)
        request = RequestFactory().get(path)
        request.resolver_match = match = resolve(path)
        request.user = self.scope["credential"]
        request.intevia_identity = SimpleNamespace(display_name="Mira", access_state="active", colour_mode=mode)
        selected = {**self.circle, "description": {"text": text, "source": "birth_purpose"} if text else None}
        projection = {"organisation": self.owner, "circles": [self.circle]}
        if target == "circle":
            projection["selected_circle"] = selected
        with patch.object(pages, "_memberships", return_value=[org]), \
             patch.object(pages.transaction, "atomic", return_value=nullcontext()), \
             patch.object(pages.navigation, "member_navigation", return_value=projection), \
             patch.object(pages.circle_commands, "operating_status_detail", return_value=detail,
                          side_effect=None if detail else pages.circle_participation.CircleAccessRefused()):
            return match.func(request, **match.kwargs)

    def test_chips_and_headings_share_the_column(self):
        for target in ("home", "circle"):
            for mode in ("light", "dark"):
                with self.subTest(target=target, mode=mode):
                    page = markup(self.read(target=target, mode=mode))
                    column = page.select("div", classes=("page-column",))
                    rows = page.select("nav", classes=("org-strip",))
                    self.assertTrue(len(column) == len(rows) == 1 and rows[0]["parents"][-1] is column[0],
                                    "The chip row must be a direct child of the centred page column.")
                    band = page.select("aside", classes=("capability-notice",))
                    main = page.select("main")
                    self.assertTrue(len(band) == len(main) == 1 and band[0]["parents"][-1] is column[0]
                                    and main[0]["parents"][-1] is column[0], "Band, chips and main must share one column.")
                    self.assertEqual(chips(page), {member_url(self.owner["organism_uuid"]): ("Alder Beck", "true")},
                                     "An organisation chip must use the admitted short label.")
                    heading = self.circle if target == "circle" else self.owner
                    self.assertEqual([n["text"] for n in page.select("h1")], [heading["full_name"]],
                                     "Each page heading must use the admitted full name.")
                    self.assertTrue(page.select("html", **{"data-mode": mode}), "The selected mode must reach the shell.")
                    self.assertTrue(page.select("a", href=reverse("f0-organisation"), **{"aria-current": "page"}),
                                    "The organisation tab must stay current on both screens.")

        css = Path(finders.find("core/f0.css")).read_text(encoding="utf-8")
        painted_row = declarations(css, ".org-strip")
        self.assertEqual(painted_row.get("flex-wrap"), "wrap", "Chips must wrap on the shared narrow column.")
        inset = {"padding", "padding-left", "padding-right", "padding-inline", "padding-inline-start",
                 "margin", "margin-left", "margin-right", "margin-inline", "margin-inline-start"}
        self.assertFalse(inset.intersection(painted_row), "The chip row must add no second horizontal inset.")

    def test_description_uses_native_disclosure_at_the_boundary(self):
        exact = "x" * 120
        short = markup(self.read(text=exact))
        self.assertEqual([n["text"] for n in short.select("p", classes=("description-text",))], [exact],
                         "A 120-character purpose must remain whole.")
        self.assertFalse(short.select("details"), "Exactly 120 characters must need no disclosure.")
        short_word = "x" * 118
        for text, expected in ((exact + "y", "x" * 119 + "…"),
                               (short_word + " abc", short_word + "…"),
                               ("Keep the river safe. " + "banks " * 19 + "<em>and its bridges</em>",
                                ("Keep the river safe. " + "banks " * 16).rstrip() + "…")):
            with self.subTest(text=text):
                page = markup(self.read(text=text))
                self.assertEqual([n["text"] for n in page.select("p", classes=("description-excerpt",))], [expected],
                                 "The excerpt must fit 120 characters including the ellipsis and respect a word boundary.")
                folded = page.select("details")
                self.assertTrue(len(folded) == 1 and "open" not in folded[0]["attrs"],
                                "A long purpose must start in one closed native disclosure.")
                summary = page.select("summary", within=folded[0])
                self.assertTrue(len(summary) == 1 and summary[0]["text"] == "Read moreRead less",
                                "The native toggle must carry the two named actions.")
                self.assertEqual([n["text"] for n in page.select("p", within=folded[0])], [text],
                                 "Opening the disclosure must give the complete original text.")
                self.assertFalse(page.select("em"), "Stored markup must appear as text.")
        empty = self.read()
        self.assertContains(empty, "No description is available.", msg_prefix="An empty Description must explain the gap.")
        self.assertNotContains(empty, "<details", msg_prefix="An empty Description must have no disclosure.")

    def test_status_displays_state_and_deadline_only(self):
        due = datetime(2027, 1, 23, 15, 6, tzinfo=timezone.utc)
        response = self.read(detail={"state": "WITHOUT_COORDINATOR", "deadline": due, "message": "Private status reason."})
        self.assertNotContains(response, "Private status reason.", msg_prefix="The raw status message must stay out of the page.")
        page = markup(response)
        block = page.select("section", **{"aria-labelledby": "status-heading"})
        self.assertTrue(len(block) == 1 and "Without coordinator" in block[0]["text"],
                        "The status block must retain the returned operating state in words.")
        self.assertEqual([n["attrs"]["datetime"] for n in page.select("time", within=block[0])], [due.isoformat()],
                         "The status block must give the returned deadline as an exact instant.")


@override_settings(ALLOWED_HOSTS=["testserver"])
class BoardDescriptionTests(TransactionTestCase):
    def test_board_introduction_is_visible_in_full_without_disclosure(self):
        from src.intevia.services import circle_commands
        from tests.test_law_acts import add_member, ordinary_birth

        sentence = ("The Board of Trustees is the organisation's Governance Circle. "
                    "Here you can find its offices and the records you can read.")
        owner, founder = ordinary_birth(board_seats=1)
        board = models.Circle.objects.get(parent_organism=owner, kind="GOVERNANCE")
        client = signed_in(add_member(owner))

        def description(circle):
            response = client.get(member_url(owner.organism_id, circle.circle_uuid))
            self.assertEqual(response.status_code, 200, "The member must reach the selected Circle.")
            page = markup(response)
            sections = page.select("section", **{"aria-labelledby": "description-heading"})
            self.assertEqual(len(sections), 1, "The page must have one Description section.")
            return page, sections[0]

        page, section = description(board)
        self.assertFalse(page.select("details", within=section),
                         "The Board introduction must be visible immediately without opening a disclosure.")
        self.assertEqual([p["text"] for p in page.select("p", within=section)], [sentence],
                         "The Description must show the complete approved Board sentence.")
        for label in ("Read more", "Read less"):
            self.assertNotIn(label, section["text"], "The Board introduction must have no disclosure action.")

        # Identical words entered as a birth purpose still qualify for the long-purpose disclosure.
        generic = circle_commands.form(
            credential=founder.credential, organism_uuid=owner.organism_id, circle_uuid=uuid4(),
            kind="GENERIC", name="River stewards", purpose=sentence, request_uuid=uuid4(),
        )
        page, section = description(generic)
        folded = page.select("details", within=section)
        self.assertTrue(len(folded) == 1 and "open" not in folded[0]["attrs"],
                        "The same words stored as a long birth purpose must start folded.")
        self.assertEqual([p["text"] for p in page.select("p", within=folded[0])], [sentence],
                         "Opening the stored purpose must reveal the complete original sentence.")
