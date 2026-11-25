"""The posts box, part PB-1: who and when on every post, the office line, underlined links.

Rulings of 24 Nov 2026: captures AC (Q10), AH (Q16, Q17), AJ (AJ3, the author's name on every post)
and AM (AM1: posts follow the office record's name read, so a name is dropped once the person's
identity link is severed).
"""
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import resolve, reverse

from src.intevia.services import discussion, discussion_posts
from tests import test_discussion_posts as post_support
from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import signed_in
from tests.test_uc2_pages import FIXTURE_HASHERS, path, seated_board


def shown(when):
    """A time as the page prints it."""
    when = when.astimezone(timezone.utc)
    return f"{when.day} {when:%B %Y, %H:%M} UTC"


def bylines(page, card):
    return [node["text"].strip() for node in page.select("p", within=card, classes=("post-byline",))]


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS)
class PostBylinePageTests(TransactionTestCase):
    """PB1-01, through the real services and the Topic page."""

    def test_pb1_01_each_post_names_its_author_and_time(self):
        """Capture AC, Q10. The opening post and each reply show who posted and when; an edited post says so."""
        owner, board, founder, coordinator, trustee = seated_board()
        support = post_support.DiscussionPostTests()
        hers, his = support.board(owner, coordinator, board), support.board(owner, trustee, board)
        topic = support.start(hers, title="Door repair", markdown="Should we repair the door?")
        reply = support.reply(his, topic, markdown="Two quotes first.")
        discussion_posts.edit_post(**his, thread_uuid=topic["thread"], post_uuid=reply["post"],
                                   predecessor=reply["version"], request_uuid=uuid4(), markdown="Two quotes first, please.")
        times = {row["post_number"]: row for row in discussion._rows(
            """SELECT p.post_number, p.effective_at AS posted, a.effective_at AS saved
                 FROM public.discp_posts p JOIN public.core_discpostversion v ON v.version_uuid=p.version_uuid
                 JOIN public.core_discpostact a ON a.act_uuid=v.act_uuid WHERE p.thread_uuid=%s""", [topic["thread"]])}
        names = {person.pk: person.display_name or "Member" for person in (coordinator, trustee)}
        self.assertNotEqual(names[coordinator.pk], names[trustee.pk], "The fixture needs two authors with different names.")
        page = Markup(signed_in(founder).get(path(owner, board, topic["thread"])).content.decode())
        cards = {page.select(tag, within=card)[0]["text"].strip(): card
                 for card in page.select("article", classes=("card",))
                 for tag in ("h2", "h3") if page.select(tag, within=card)}
        self.assertEqual(bylines(page, cards["Opening post"]), [f"{names[coordinator.pk]} · {shown(times[1]['posted'])}"],
                         "The opening post must name its author and the time it was posted.")
        self.assertEqual(bylines(page, cards["Post 2"]),
                         [f"{names[trustee.pk]} · Version 2 · Edited {shown(times[2]['saved'])}"],
                         "An edited reply must name its author, its version and the time that version was saved.")


    def test_pb1_06_a_severed_author_is_no_longer_named(self):
        """Capture AM1. Once an author's identity link is severed their posts say "Member"; other authors keep their names."""
        owner, board, founder, coordinator, trustee = seated_board()
        support = post_support.DiscussionPostTests()
        hers, his = support.board(owner, coordinator, board), support.board(owner, trustee, board)
        topic = support.start(hers, title="Door repair", markdown="Should we repair the door?")
        support.reply(his, topic, markdown="Two quotes first.")
        reader = signed_in(founder)

        def authors():
            page = Markup(reader.get(path(owner, board, topic["thread"])).content.decode())
            return [bylines(page, card)[0].split(" · ")[0] for card in page.select("article", classes=("card",))]

        self.assertTrue(trustee.display_name.strip() and trustee.display_name != "Member",
                        "The fixture's author needs a real name for the control.")
        self.assertEqual(authors(), [coordinator.display_name, trustee.display_name],
                         "The positive control: both authors are named before the severance.")
        # Owner power, named (item 121): no service severs an identity link yet, so the test removes the author's
        # resolution row directly, as the landed office-record witnesses do (tests/test_uc1_office_chooser.py).
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM public.core_identityresolution WHERE identity_id=%s", [trustee.pk])
            cursor.execute("SELECT EXISTS (SELECT FROM public.core_identityresolutionsevered WHERE identity_id=%s)", [trustee.pk])
            self.assertTrue(cursor.fetchone()[0], "The database must witness the severance before the final read.")
        self.assertEqual(authors(), [coordinator.display_name, "Member"],
                         "A severed author's posts must stop showing the name; another author's name must stay.")


class PostBylineProjectionTests(SimpleTestCase):
    """PB1-02, PB1-03 and PB1-05: the post card, the office line and the read's naming, with no database."""

    POSTED = datetime(2026, 11, 24, 18, 40, 10, tzinfo=timezone.utc)
    SAVED = datetime(2026, 11, 24, 18, 52, 30, tzinfo=timezone.utc)

    def topic_page(self, posts, **context):
        org, circle, thread = uuid4(), uuid4(), uuid4()
        self.org = org
        address = reverse("discussion_topic", args=(org, circle, thread))
        request = RequestFactory().get(address)
        request.user = AnonymousUser()
        request.resolver_match = resolve(address)
        base = {"board_org": org, "board_circle": circle, "posts": posts,
                "topic": {"thread_uuid": thread, "title": "Door repair", "topic_type": "TOPIC"},
                "category": {"name": "Discussion", "can_reply": True}}
        html = render_to_string("core/discussion_topic.html", base | context, request=request)
        return html, Markup(html)

    def test_pb1_02_byline_words_for_new_edited_and_withheld_posts(self):
        """The line under a post's heading: name and time; name, version and edit time; nothing for a withheld post."""
        posts = [
            {"post_number": 1, "markdown": "Opening words", "version_number": 1, "author_name": "Mira <Alder>",
             "effective_at": self.POSTED, "version_effective_at": self.POSTED},
            {"post_number": 2, "markdown": "Reply words", "version_number": 2, "author_name": "Rowan Fell",
             "effective_at": self.POSTED, "version_effective_at": self.SAVED},
            {"post_number": 3, "markdown": None, "version_number": 2, "author_name": None,
             "effective_at": self.POSTED, "version_effective_at": self.SAVED},
        ]
        html, page = self.topic_page(posts)
        opening, second, third = page.select("article", classes=("card",))
        self.assertEqual(bylines(page, opening), ["Mira <Alder> · 24 November 2026, 18:40 UTC"],
                         "A post never edited shows its author and when it was posted, and no version label.")
        self.assertEqual(bylines(page, second), ["Rowan Fell · Version 2 · Edited 24 November 2026, 18:52 UTC"],
                         "An edited post shows its author, its version and when that version was saved.")
        self.assertEqual((bylines(page, third), [p["text"] for p in page.select("p", within=third)]), ([], ["Post unavailable."]),
                         "A withheld post names no one and gives no time.")
        self.assertNotIn("<Alder>", html, "A name must reach the page as text, never as markup.")
        heading, = page.select("h2", within=opening)
        byline, = page.select("p", within=opening, classes=("post-byline",))
        text, = page.select("div", within=opening, classes=("post-text",))
        self.assertLess(page.nodes.index(heading), page.nodes.index(byline), "The line sits under the post's heading.")
        self.assertLess(page.nodes.index(byline), page.nodes.index(text), "The line sits above the post's words.")

    def test_pb1_03_office_topic_opening_reads_office_then_its_link(self):
        """Capture AH, Q16. "Office: <office>", the office a link in the sentence, not a row of its own."""
        office = {"office_definition_uuid": uuid4(), "label": "Treasurer"}
        posts = [{"post_number": 1, "markdown": "Opening words", "version_number": 1, "author_name": "Mira Alder",
                  "effective_at": self.POSTED, "version_effective_at": self.POSTED}]
        html, page = self.topic_page(posts, office=office)
        opening, = page.select("article", classes=("card",))
        line, = page.select("p", within=opening, classes=("discussion-office",))
        self.assertEqual(line["text"].strip(), "Office: Treasurer", "The office must be introduced by its label.")
        link, = page.select("a", within=line)
        self.assertEqual((link["text"], link["attrs"]["href"]),
                         ("Treasurer", reverse("office_page", args=(self.org, office["office_definition_uuid"]))),
                         "The office's name must be the link to that office's page.")
        self.assertNotIn("link-row", line["attrs"]["class"].split(),
                         "The link sits in the sentence, so the shared rule underlines it and the card's foot is not padded.")
        self.assertIs(page.select("p", within=opening)[-1], line, "The office line is the card's last line.")

    def test_pb1_05_read_names_only_readable_posts(self):
        """Capture AJ3. The read looks up the authors of readable posts only, and gives a withheld post no name."""
        posts = [{"author_id": 7, "markdown": "Words"}, {"author_id": 7, "markdown": None}, {"author_id": 9, "markdown": "More"},
                 {"author_id": 9, "markdown": "Again"}, {"author_id": 4, "markdown": None}]
        found = [{"id": 7, "name": "Mira Alder"}, {"id": 9, "name": "Rowan Fell"}]
        with mock.patch.object(discussion_posts.disc, "_rows", return_value=found) as rows:
            discussion_posts._name_authors(posts)
        self.assertEqual([post["author_name"] for post in posts], ["Mira Alder", None, "Rowan Fell", "Rowan Fell", None],
                         "Readable posts carry their author's name; a withheld post carries none, even beside the same author's readable post.")
        self.assertEqual(rows.call_args.args[1], [[7, 9]], "Only the authors of readable posts are looked up, once each.")
        with mock.patch.object(discussion_posts.disc, "_rows") as rows:
            discussion_posts._name_authors([{"author_id": 9, "markdown": None}])
        rows.assert_not_called()


class ContentLinkRuleTests(SimpleTestCase):
    """PB1-04 (capture AH, Q17, a standing rule), read from the stylesheet's source.

    ``tools/f0_styles.py check`` holds the served stylesheet to this source, and the Vision Chamber's
    renders measure the pages. This witness pins the rule's reach: which links it leaves alone.
    """

    def test_pb1_04_content_links_are_underlined_except_rows_breadcrumbs_and_buttons(self):
        source = (Path(__file__).resolve().parents[1] / "core/styles/theme.css").read_text(encoding="utf-8")
        rules = [" ".join(rule.split()) for rule in re.findall(r"main a:not\(\.button\)[^{]*\{[^}]*\}", source)]
        standing = [rule for rule in rules if rule.endswith("{text-decoration-line:underline}")]
        self.assertEqual(standing, [
            "main a:not(.button):not(.nav-item):not(.domain-item):not(:where(.circle-list > li > a, .version-list a, nav a))"
            "{text-decoration-line:underline}"],
            "One shared rule must underline every content link, leaving out only buttons, the shell's navigation, "
            "rows with the chevron, version-list links and breadcrumbs.")
