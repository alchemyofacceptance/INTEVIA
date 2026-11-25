"""The posts box, part PB-4: the edit page for a member's own post.

Rulings of 24 Nov 2026: captures AC (Q8), AI (AI4), AJ (AJ1, AJ2) and AM (AM2, the 15 minutes held
by the edit route as an interim until the December plan's database guard).

Part PB-8 (HG capture BJ of 25 Nov 2026, BJ3; GPT Astra's posts-box part 1 review, finding 1 and its
ordinary-use gap): a held form cannot change withdrawn wording (PB8-01, PB8-03), and an ordinary
Topic's opening post is edited within its original 15 minutes (PB8-02).

Every fixture is built through the real services and every edit goes through the real pages. Rows
are created per test. No test here uses owner or superuser powers. PB8-03 alone reads no database:
it gives the save's check the services' answers directly.
"""
from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.urls import reverse

from core import discussion_edit
from core.discussion_edit import CLARITY_LINE
from intevia.test_postgresql_backend import clock
from src.intevia.services import cq, discussion, discussion_posts
from tests import test_discussion_posts as post_support
from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import assert_private, signed_in
from tests.test_uc2_pages import FIXTURE_HASHERS, BoardClarityFixture, path, seated_board

HELP_START = "If you need help, contact "


class EditPageSupport:
    """Shared steps: find the control, open the page, save, and read what the record holds."""

    def edit_address(self, name, post):
        return reverse(name, args=(self.owner.organism_id, self.board.circle_uuid, self.thread, post))

    def controls(self, client):
        """The edit controls the Topic page offers this reader, as (label, address) pairs."""
        page = Markup(client.get(self.url).content.decode())
        return [(a["text"].strip(), a["attrs"].get("href")) for a in page.select("a", classes=("button",))
                if a["text"].strip() in ("Edit", "Edit wording")]

    def form_fields(self, response, post):
        page = Markup(response.content.decode())
        form, = page.select("form", method="post", action=self.edit_address("discussion_edit", post))
        return {node["attrs"]["name"]: node["attrs"]["value"] for node in page.select("input", within=form, type="hidden")}

    def notice_lines(self, response):
        page = Markup(response.content.decode())
        notice, = page.select("aside", classes=("notice",))
        return [p["text"].strip() for p in page.select("p", within=notice)]

    def save(self, client, post, fields, text):
        response = client.post(self.edit_address("discussion_edit", post), fields | {"markdown": text})
        self.assertEqual((response.status_code, response.get("Location")), (303, f"{self.url}#post-{post}"),
                         "A saved edit must return the author to the Topic at the edited post.")
        assert_private(self, response)
        return response

    def versions(self, post):
        return [row["markdown"] for row in discussion._rows(
            """SELECT body.markdown FROM public.core_discpostversion v
                 JOIN public.core_discbody body ON body.version_uuid=v.version_uuid
                WHERE v.post_uuid=%s ORDER BY v.version_number""", [post])]

    def assert_refused(self, response, status, why, action):
        """The five parts (item 164): what happened, why, what was recorded, what to do next, where to get help."""
        self.assertEqual(response.status_code, status, "The refusal must carry its own status.")
        page = Markup(response.content.decode())
        heading, = page.select("h1", classes=("page-title",))
        notice, = page.select("div", classes=("notice", "refusal"))
        lines = [p["text"].strip() for p in page.select("p", within=notice)]
        self.assertEqual((heading["text"].strip(), lines[:3]),
                         ("Your edit could not be saved.", [why, "This request recorded no new version.", action]),
                         "The refusal must say what happened, why, that nothing was recorded and what to do next.")
        self.assertTrue(len(lines) == 4 and lines[3].startswith(HELP_START), "The refusal must end with where to get help.")

    def byline(self, client, post):
        page = Markup(client.get(self.url).content.decode())
        card, = page.select("article", id=f"post-{post}")
        line, = page.select("p", within=card, classes=("post-byline",))
        return line["text"].strip()


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS)
class SuggestionWordingEditTests(EditPageSupport, BoardClarityFixture, TransactionTestCase):
    """PB4-01 to PB4-04: a Suggestion's opening post, its author, and the service's lock."""

    def author_page(self):
        """The author is the fixture's third trustee; the post is the Suggestion's opening post."""
        author = signed_in(self.people[2])
        post = self.topic["post"]
        self.assertEqual(self.controls(author), [("Edit wording", self.edit_address("discussion_edit_form", post))],
                         "The author must be offered exactly one control, 'Edit wording', on the opening post.")
        return author, post, author.get(self.edit_address("discussion_edit_form", post))

    def test_pb4_01_author_edits_the_wording_in_preparation(self):
        """Capture AC, Q8. In Preparation the author opens the page, saves, and the record holds Version 2."""
        self.journey(prepare=False)
        author, post, opened = self.author_page()
        self.assertEqual(self.notice_lines(opened),
                         ["You can change the wording until voting opens. Saving records Version 2."],
                         "In Preparation the page must give the ruled words and no Clarity line.")
        self.save(author, post, self.form_fields(opened, post), "Approve one payment of 84 pounds.")
        self.assertEqual(self.versions(post), ["Approve one payment.", "Approve one payment of 84 pounds."],
                         "Saving must record a second version and keep the first.")
        self.assertIn(" · Version 2 · Edited ", self.byline(author, post),
                      "Back on the Topic, the post must show its new version and when it was edited.")

    def test_pb4_02_author_edits_in_clarity_and_a_clear_member_confirms_again(self):
        """Capture AC, Q8. In Clarity the page says so, and a member who was clear is asked again on the new wording."""
        self.journey()
        founder = signed_in(self.people[0])
        self.assertContains(self.submit_clear(founder), "You said you're clear on this wording.", count=1,
                            msg_prefix="The positive control: the member is clear on Version 1 before the edit.")
        author, post, opened = self.author_page()
        self.assertEqual(self.notice_lines(opened),
                         ["You can change the wording until voting opens. Saving records Version 2.", CLARITY_LINE],
                         "In Clarity the page must add that clear members will be asked to confirm again.")
        self.save(author, post, self.form_fields(opened, post), "Approve one payment of 84 pounds.")
        self.assertEqual(cq.phase(**self.scope)["phase"], "CLARITY", "An edit in Clarity must leave the Suggestion in Clarity.")
        again = founder.get(self.url)
        self.assertNotContains(again, "You said you're clear on this wording.",
                               msg_prefix="The member was clear on the old wording only.")
        self.assertTrue(self.fields(again, "discussion_confirm_clarity"),
                        "The member must be offered the confirmation again, on the new wording.")

    def test_pb4_03_no_edit_wording_once_voting_opens_and_the_route_refuses(self):
        """Capture AI, AI4. Once voting opens the control is gone, and both the page and a held form are refused."""
        self.journey()
        author, post, opened = self.author_page()
        held = self.form_fields(opened, post)
        for person in self.people[:2]:
            self.submit_clear(signed_in(person))
        self.assertEqual(cq.phase(**self.scope)["phase"], "VOTING", "The fixture must have opened voting.")
        self.assertEqual(self.controls(author), [], "Once voting opens the author must see no edit control.")
        why, action = "This wording is locked because voting has opened.", "To change the wording, start a new Suggestion."
        self.assert_refused(author.get(self.edit_address("discussion_edit_form", post)), 409, why, action)
        late = author.post(self.edit_address("discussion_edit", post), held | {"markdown": "A late change."})
        self.assert_refused(late, 409, why, action)
        self.assertEqual(self.versions(post), ["Approve one payment."], "A refused edit must record no version.")

    def test_pb4_04_another_member_sees_no_control_and_is_refused(self):
        """Capture AC, Q8. Only the author: another Board member has no control, no page and no save."""
        self.journey(prepare=False)
        author, post, opened = self.author_page()
        other = signed_in(self.people[0])
        self.assertEqual(self.controls(other), [], "Another member must see no edit control on the author's post.")
        self.assert_refused(other.get(self.edit_address("discussion_edit_form", post)), 403,
                            "Only a post's author can edit it, while their membership allows contributions.",
                            "Go back to the Topic to see the post as it stands.")
        borrowed = other.post(self.edit_address("discussion_edit", post),
                              self.form_fields(opened, post) | {"markdown": "Not the author's words."})
        self.assertEqual(borrowed.status_code, 400, "The author's form must not work in another member's session.")
        self.assertContains(borrowed, "The form no longer matches this session or discussion.", status_code=400)
        self.assertEqual(self.versions(post), ["Approve one payment."], "A refused edit must record no version.")

    def test_pb8_01_a_form_held_through_a_withdrawal_cannot_change_the_wording(self):
        """PB8-01 (capture BJ3). An edit saved before withdrawal stands; a form still open at withdrawal is refused."""
        self.journey()
        author, post, first = self.author_page()
        used = self.form_fields(first, post)
        self.save(author, post, used, "Approve one payment of 84 pounds.")
        saved = ["Approve one payment.", "Approve one payment of 84 pounds."]
        self.assertEqual(self.versions(post), saved, "The control: the author's edit before withdrawal must be recorded.")
        held = self.form_fields(author.get(self.edit_address("discussion_edit_form", post)), post)
        link, = [a["attrs"]["href"] for a in Markup(author.get(self.url).content.decode()).select("a")
                 if a["text"].strip() == "Withdraw Suggestion"]
        route = "discussion_clarity_withdraw"
        withdrawn = author.post(self.endpoint(route), self.fields(author.get(link), route))
        self.assertEqual((withdrawn.status_code, cq.phase(**self.scope)["phase"]), (303, "WITHDRAWN"),
                         "The fixture must have withdrawn the Suggestion through its own page.")
        self.assertEqual(self.controls(author), [], "After withdrawal the author must see no edit control.")
        late = author.post(self.edit_address("discussion_edit", post), held | {"markdown": "Approve two payments."})
        self.assert_refused(late, 409, "The time for changing this wording has ended.",
                            "To change the wording, start a new Suggestion.")
        self.assertEqual(self.versions(post), saved,
                         "A form held through the withdrawal must record no version: the opening post keeps its wording.")
        record = cq.board_topic_vote(**self.scope)
        self.assertEqual((record["phase"], record["version_number"], record["markdown"]), ("WITHDRAWN", 2, saved[-1]),
                         "The withdrawal record and the opening post must give the same wording and version.")
        self.save(author, post, used, "Approve one payment of 84 pounds.")
        self.assertEqual(self.versions(post), saved,
                         "Repeating the save completed before withdrawal must give its receipt and record nothing new.")
        different = author.post(self.edit_address("discussion_edit", post), used | {"markdown": "Something else."})
        self.assert_refused(different, 409, "This form already saved a different version.",
                            "Go back to the Topic to read the saved version, then open the edit page again.")
        self.assertEqual(self.versions(post), saved, "A used form carrying different words must record nothing.")


class WithdrawnWordingCheckTests(SimpleTestCase):
    """PB8-03: the save's own check, given the services' answers. No database."""

    def check(self, phase, recorded, request_uuid):
        calls = []
        vote = None if phase is None else {"phase": phase}
        with patch.object(discussion_edit, "_lock_registered_organism", side_effect=lambda *a: calls.append("lock")), \
             patch.object(discussion_edit.cq, "board_topic_vote", side_effect=lambda **k: calls.append("phase") or vote), \
             patch.object(discussion_edit.discussion_posts, "post_history",
                          side_effect=lambda **k: calls.append("history") or [{"version_uuid": u} for u in recorded]):
            try:
                discussion_edit._refuse_withdrawn_wording(SimpleNamespace(user=object()), uuid4(), uuid4(), uuid4(), uuid4(),
                                                          request_uuid)
                return None, calls
            except discussion.DiscussionRefused as refused:
                return refused.code, calls

    def test_pb8_03_only_a_fresh_edit_of_withdrawn_wording_is_refused_and_the_lock_comes_first(self):
        """PB8-03. Withdrawn and fresh: refused. Withdrawn and already saved by this form: left to the service. Not withdrawn: left alone."""
        fresh, earlier = uuid4(), uuid4()
        code, calls = self.check("WITHDRAWN", [earlier], fresh)
        self.assertEqual(code, "edit_wording_ended", "A fresh edit of withdrawn wording must get the ended-wording refusal.")
        self.assertEqual(calls, ["lock", "phase", "history"],
                         "The organisation's lock must be taken before the phase is read, so a withdrawal cannot slip between.")
        self.assertEqual(self.check("WITHDRAWN", [earlier, fresh], fresh)[0], None,
                         "A save this form completed earlier must be left to the edit service's receipt.")
        for phase in ("PREPARATION", "CLARITY", "VOTING", "RESULT", "CLOSED", None):
            with self.subTest(phase=phase):
                code, calls = self.check(phase, [], fresh)
                self.assertEqual((code, calls), (None, ["lock", "phase"]),
                                 "Any other phase must be left to the service's own lock, which names its reason.")


class RepeatedSaveAfterTheWindowTests(SimpleTestCase):
    """PC1-06 (Gemini D2): the save's own decision after the 15 minutes, given the services' answers. No database."""

    def save(self, *, minutes, recorded, own=True):
        """Send one reply's edit form ``minutes`` after posting. Returns (refusal code or None, what was called)."""
        from datetime import datetime

        org, circle, thread, post, version, sent = (uuid4() for _ in range(6))
        posted = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
        target = {"post_uuid": post, "post_number": 2, "hidden": False, "effective_at": posted, "version_uuid": version}
        state = {"own": {post} if own else set(), "read_at": posted + timedelta(minutes=minutes)}
        released = {"topic": {"topic_type": "TOPIC"}, "category": {"category_uuid": uuid4()}}
        calls = []

        def read(request, org, circle, thread, post, context):
            context.update(released)
            return target, state

        with patch.object(discussion_edit, "_target", side_effect=read), \
             patch.object(discussion_edit.forms, "_envelope", return_value={"version": str(version), "request": str(sent)}), \
             patch.object(discussion_edit.forms, "_one", side_effect=lambda data, name: data[name]), \
             patch.object(discussion_edit.discussion_posts, "post_history",
                          side_effect=lambda **k: calls.append("history") or [{"version_uuid": u} for u in ([sent] if recorded else [uuid4()])]), \
             patch.object(discussion_edit.discussion_posts, "edit_post",
                          side_effect=lambda **k: calls.append(("edit_post", k["request_uuid"], k["predecessor"]))):
            try:
                discussion_edit._save(SimpleNamespace(user=object()), org, circle, thread, post,
                                      {"command": "signed", "markdown": "Two quotes first, please."}, {})
                return None, calls, (sent, version)
            except discussion.DiscussionRefused as refused:
                return refused.code, calls, (sent, version)

    def test_pc1_06_a_repeat_of_a_recorded_save_is_left_to_the_service_after_the_15_minutes(self):
        """PC1-06. After the window: a request that already recorded goes to the edit service; a new request is refused."""
        code, calls, (sent, version) = self.save(minutes=16, recorded=True)
        self.assertEqual((code, calls), (None, ["history", ("edit_post", sent, version)]),
                         "A repeat of a save that already recorded must reach the edit service, with its own request "
                         "and version identities, so that the service can answer with its receipt.")
        code, calls, _ = self.save(minutes=16, recorded=False)
        self.assertEqual((code, calls), ("edit_window", ["history"]),
                         "A new request after the 15 minutes must still be refused, and must record nothing.")
        code, calls, (sent, version) = self.save(minutes=14, recorded=False)
        self.assertEqual((code, calls), (None, [("edit_post", sent, version)]),
                         "Inside the 15 minutes the save goes straight to the edit service, with no extra read.")
        code, calls, _ = self.save(minutes=16, recorded=True, own=False)
        self.assertEqual((code, calls), ("DISC_POST_AUTHORITY", []),
                         "Only the 15-minute refusal is lifted for a repeat; a reader who is not the author is still refused.")


class NoPanelNoEditWordingTests(SimpleTestCase):
    """PC1-07 (Gemini D3): with no vote panel for a Suggestion, no "Edit wording" and no form. No database."""

    def page(self, vote_read):
        """The author's Topic controls and edit-form response, with the vote read answering as given."""
        from contextlib import nullcontext

        from django.test import RequestFactory
        from django.urls import resolve

        org, circle, thread, post = (uuid4() for _ in range(4))
        address = reverse("discussion_edit_form", args=(org, circle, thread, post))
        request = RequestFactory().get(address)
        request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Author")
        request.intevia_identity = SimpleNamespace(colour_mode="light", display_name="Author")
        request.session = SimpleNamespace(session_key="author")
        request.resolver_match = resolve(address)

        def released():
            return {"topic": {"topic_type": "SUGGESTION", "title": "Repair", "thread_uuid": thread},
                    "category": {"category_uuid": uuid4()}, "board_org": org, "board_circle": circle,
                    "organisation": {"organism_uuid": org, "short_label": "Alder Beck"},
                    "circle": {"circle_uuid": circle, "short_label": "Board"},
                    "posts": [{"post_uuid": post, "post_number": 1, "hidden": False, "markdown": "Approve one payment.",
                               "version_uuid": uuid4(), "version_number": 1}],
                    "edit_state": {"own": {post}, "read_at": None}}

        from core import discussion_vote
        with patch.object(discussion_vote.cq, "board_topic_vote", **vote_read) as read, \
             patch.object(discussion_edit.forms, "_read_page", side_effect=lambda *a: ("core/discussion_topic.html", released())), \
             patch.object(discussion_edit.forms, "issue_form", return_value="signed"), \
             patch.object(discussion_edit, "atomic", return_value=nullcontext()):
            on_topic = released()
            on_topic["vote"] = discussion_vote.panel(request, org, circle, thread)
            discussion_edit.offer(request, on_topic, org, circle, thread)
            response = discussion_edit.edit_form(request, org, circle, thread, post)
        self.assertEqual(read.call_count, 2, "Both the Topic's offer and the form must have asked the vote read.")
        opened = Markup(response.content.decode())
        # The refusal's own lines, where the form was refused (correction PC1 R2).
        self.refusal = [p["text"].strip() for notice in opened.select("div", classes=("notice", "refusal"))
                        for p in opened.select("p", within=notice)]
        return ([row["edit"]["label"] for row in on_topic["posts"] if row.get("edit")], response.status_code,
                len(opened.select("form", method="post")), [n["text"].strip() for n in opened.select("h1")])

    def test_pc1_07_a_failed_vote_read_offers_no_edit_wording_and_refuses_the_form(self):
        """PC1-07. The vote read raising DatabaseError: no control, and the form is refused. The control: a Suggestion not yet started."""
        from django.db import DatabaseError

        self.assertEqual(self.page({"return_value": {"markdown": "Approve one payment."}}),
                         (["Edit wording"], 200, 1, ["Edit wording"]),
                         "The control: before the Suggestion is put to a vote, its author is offered the form.")
        self.assertEqual(self.page({"side_effect": DatabaseError("the read failed")}),
                         ([], 409, 0, ["Your edit could not be saved."]),
                         "With no panel the page cannot tell whether the wording is locked: no control, and the form refuses.")
        self.assertEqual(self.page({"return_value": None}), ([], 409, 0, ["Your edit could not be saved."]),
                         "A withheld vote read gives no panel either, and is treated the same way.")
        # Correction PC1 R2 (HG capture BX of 25 Nov 2026, BX1): the refusal says why in a sentence of its own.
        for read in ({"side_effect": DatabaseError("the read failed")}, {"return_value": None}):
            with self.subTest(read=sorted(read)):
                self.page(read)
                self.assertEqual(self.refusal[:3],
                                 ["The state of this Suggestion's vote could not be read just now, so its wording cannot be edited.",
                                  "This request recorded no new version.",
                                  "Go back to the Topic and try again in a moment."],
                                 "The refusal must say that the vote's state could not be read, that nothing was recorded, "
                                 "and to try again in a moment.")
                self.assertNotIn("The discussion service refused this contribution.", self.refusal,
                                 "The default sentence must no longer stand in for this refusal.")


class EditPageShellTests(SimpleTestCase):
    """PC1-01 (Gemini I1): the edit form and the edit refusal keep the INTEVIA tab and the domain row. No database."""

    def test_pc1_01_the_edit_form_and_its_refusal_keep_the_intevia_tab_and_the_domain_row(self):
        """PC1-01. On both edit routes the served page has INTEVIA as its one current tab, and the domain row."""
        from django.template.loader import render_to_string
        from django.test import RequestFactory
        from django.urls import resolve

        from core import discussion_forms

        org, circle, thread, post = (uuid4() for _ in range(4))
        board = {"organisation": {"organism_uuid": org, "short_label": "Alder Beck"},
                 "circle": {"circle_uuid": circle, "short_label": "Board"}, "topic": {"title": "Repair"}}
        form = {"heading": "Edit your reply", "place": "Post 2", "field_label": "Your reply", "terms": "Terms.",
                "clarity": None, "markdown": "Words", "save_url": "/save/", "cancel_url": "/topic/", "command": "signed"}
        for name in ("discussion_topic", "discussion_edit_form", "discussion_edit"):
            address = reverse(name, args=(org, circle, thread) if name == "discussion_topic" else (org, circle, thread, post))
            request = RequestFactory().get(address)
            request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Test reader")
            request.intevia_identity = SimpleNamespace(colour_mode="light", display_name="Test reader")
            request.resolver_match = resolve(address)
            pages = {"refusal": discussion_forms.refusal(request, "edit_window", dict(board)).content.decode()}
            if name == "discussion_edit_form":
                pages["form"] = render_to_string("core/discussion_edit.html", board | {"edit": form}, request=request)
            for kind, served in pages.items():
                with self.subTest(route=name, page=kind):
                    page = Markup(served)
                    tabs, = page.select("nav", **{"aria-label": "Primary navigation"})
                    current = page.select("a", within=tabs, **{"aria-current": "page"})
                    self.assertEqual([(a["text"].strip(), a["attrs"]["href"]) for a in current],
                                     [("INTEVIA", reverse("f0-organisation"))],
                                     "The page must keep INTEVIA as its one current tab, as the Topic page does.")
                    self.assertEqual(len(page.select("nav", **{"aria-label": "Domains"})), 1,
                                     "The page must keep the domain row, as the Topic page does.")


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS)
class OpeningPostEditWindowTests(EditPageSupport, TransactionTestCase):
    """PB8-02: an ordinary Topic's opening post, its author, and the original 15 minutes (capture AJ2)."""

    def test_pb8_02_an_ordinary_opening_post_is_edited_within_its_original_15_minutes(self):
        """PB8-02. The new wording shows, the title and the earlier version stay, and a later edit does not restart the window."""
        self.owner, self.board, founder, coordinator, trustee = seated_board()
        support = post_support.DiscussionPostTests()
        topic = support.start(support.board(self.owner, trustee, self.board), title="Door repair",
                              markdown="Should we repair the door?")
        self.thread, post = topic["thread"], topic["post"]
        self.url = path(self.owner, self.board, self.thread)
        author, reader = signed_in(trustee), signed_in(founder)
        posted = discussion._rows("SELECT effective_at FROM public.discp_posts WHERE post_uuid=%s", [post])[0]["effective_at"]
        until = (posted + discussion_posts.EDIT_WINDOW).astimezone(timezone.utc)
        terms = (f"You can edit your opening post until {until:%H:%M} UTC, 15 minutes after you posted it. "
                 "Saving records Version {}. Earlier versions stay on the record.")
        self.assertEqual(self.controls(author), [("Edit", self.edit_address("discussion_edit_form", post))],
                         "The Topic's author must be offered 'Edit' on the opening post.")
        self.assertEqual(self.controls(reader), [], "Another member must see no edit control on the opening post.")
        clock.advance_to(posted + timedelta(minutes=5))
        opened = author.get(self.edit_address("discussion_edit_form", post))
        self.assertEqual(self.notice_lines(opened), [terms.format(2)],
                         "The page must give the ruled words with the opening post's own last minute.")
        self.save(author, post, self.form_fields(opened, post), "Should we repair or replace the door?")
        self.assertEqual(self.versions(post), ["Should we repair the door?", "Should we repair or replace the door?"],
                         "Saving must record a second version and keep the first.")
        page = reader.get(self.url)
        shown = Markup(page.content.decode())
        title, = shown.select("h1", classes=("discussion-title",))
        self.assertEqual(title["text"].strip(), "Door repair", "Editing the opening post must leave the Topic's title as it was.")
        self.assertContains(page, "Should we repair or replace the door?", msg_prefix="Every reader must see the new wording.")
        self.assertNotContains(page, "Should we repair the door?", msg_prefix="The Topic page shows the current version only.")
        self.assertIn(" · Version 2 · Edited ", self.byline(reader, post),
                      "The opening post must show its new version and when it was edited.")
        again = author.get(self.edit_address("discussion_edit_form", post))
        self.assertEqual(self.notice_lines(again), [terms.format(3)],
                         "After an edit the page must still give the original last minute: an edit does not restart the 15 minutes.")
        held = self.form_fields(again, post)
        clock.advance_to(posted + discussion_posts.EDIT_WINDOW)
        self.assertEqual(self.controls(author), [],
                         "Fifteen minutes after posting the control must be gone, although the edit was made ten minutes ago.")
        why = "The 15 minutes for editing this post have ended."
        action = "Go back to the Topic. You can post a reply to add to what you wrote."
        self.assert_refused(author.get(self.edit_address("discussion_edit_form", post)), 409, why, action)
        late = author.post(self.edit_address("discussion_edit", post), held | {"markdown": "A late change."})
        self.assert_refused(late, 409, why, action)
        self.assertEqual(len(self.versions(post)), 2, "A refused edit must record no version.")


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS)
class ReplyEditWindowTests(EditPageSupport, TransactionTestCase):
    """PB4-05 to PB4-07: a reply, its author, the 15 minutes and a replayed save."""

    def replied(self):
        self.owner, self.board, founder, coordinator, trustee = seated_board()
        support = post_support.DiscussionPostTests()
        hers, his = support.board(self.owner, coordinator, self.board), support.board(self.owner, trustee, self.board)
        topic = support.start(hers, title="Door repair", markdown="Should we repair the door?")
        self.thread = topic["thread"]
        self.url = path(self.owner, self.board, self.thread)
        reply = support.reply(his, topic, markdown="Two quotes first.")
        return signed_in(trustee), reply["post"], signed_in(founder)

    def posted_at(self, post):
        return discussion._rows("SELECT effective_at FROM public.discp_posts WHERE post_uuid=%s", [post])[0]["effective_at"]

    def test_pb4_05_author_edits_a_reply_inside_the_window(self):
        """Capture AI, AI4 and AJ1. Within 15 minutes the author is offered "Edit", sees the ruled words, and saves Version 2."""
        author, post, reader = self.replied()
        self.assertEqual(self.controls(author), [("Edit", self.edit_address("discussion_edit_form", post))],
                         "The reply's author must be offered 'Edit' on the reply, and on no one else's post.")
        self.assertEqual(self.controls(reader), [], "A member who wrote neither post must see no edit control.")
        opened = author.get(self.edit_address("discussion_edit_form", post))
        until = (self.posted_at(post) + timedelta(minutes=15)).astimezone(timezone.utc)
        self.assertEqual(self.notice_lines(opened),
                         [f"You can edit your reply until {until:%H:%M} UTC, 15 minutes after you posted it. "
                          "Saving records Version 2. Earlier versions stay on the record."],
                         "The page must give the ruled words with the reply's own last minute.")
        self.save(author, post, self.form_fields(opened, post), "Two quotes first, please.")
        self.assertEqual(self.versions(post), ["Two quotes first.", "Two quotes first, please."],
                         "Saving must record a second version and keep the first.")
        self.assertIn(" · Version 2 · Edited ", self.byline(reader, post),
                      "Every reader of the Topic must see the reply's new version and when it was edited.")

    def test_pb4_06_the_route_refuses_after_15_minutes(self):
        """Capture AM2 (interim). After 15 minutes on the INTEVIA Clock the control is gone and the route refuses."""
        author, post, _ = self.replied()
        held = self.form_fields(author.get(self.edit_address("discussion_edit_form", post)), post)
        before = clock.offset()
        clock.advance_to(self.posted_at(post) + discussion_posts.EDIT_WINDOW)
        self.assertGreater(clock.offset(), before, "The owned INTEVIA Clock must have moved to the window's end.")
        self.assertEqual(self.controls(author), [], "After 15 minutes the author must see no edit control.")
        why = "The 15 minutes for editing this post have ended."
        action = "Go back to the Topic. You can post a reply to add to what you wrote."
        self.assert_refused(author.get(self.edit_address("discussion_edit_form", post)), 409, why, action)
        late = author.post(self.edit_address("discussion_edit", post), held | {"markdown": "A late change."})
        self.assert_refused(late, 409, why, action)
        self.assertEqual(self.versions(post), ["Two quotes first."], "A refused edit must record no version.")

    def test_pb4_07_a_replayed_save_records_once(self):
        """The discussion forms' replay rule. The same form submitted twice records one new version."""
        author, post, _ = self.replied()
        fields = self.form_fields(author.get(self.edit_address("discussion_edit_form", post)), post)
        self.save(author, post, fields, "Two quotes first, please.")
        self.save(author, post, fields, "Two quotes first, please.")
        self.assertEqual(self.versions(post), ["Two quotes first.", "Two quotes first, please."],
                         "A replayed save must leave exactly one new version.")
        different = author.post(self.edit_address("discussion_edit", post), fields | {"markdown": "Something else."})
        self.assert_refused(different, 409, "This form already saved a different version.",
                            "Go back to the Topic to read the saved version, then open the edit page again.")
        self.assertEqual(len(self.versions(post)), 2, "A used form carrying different words must record nothing.")

    def test_pc1_06_a_save_repeated_after_the_15_minutes_gets_its_receipt(self):
        """PC1-06 (Gemini D2). A save recorded inside the window and sent again after it returns to the Topic; a new request is refused."""
        author, post, _ = self.replied()
        fields = self.form_fields(author.get(self.edit_address("discussion_edit_form", post)), post)
        unsent = self.form_fields(author.get(self.edit_address("discussion_edit_form", post)), post)
        self.save(author, post, fields, "Two quotes first, please.")
        clock.advance_to(self.posted_at(post) + discussion_posts.EDIT_WINDOW + timedelta(minutes=1))
        self.assertEqual(self.controls(author), [], "The fixture must be past the 15 minutes: no edit control.")
        self.save(author, post, fields, "Two quotes first, please.")
        self.assertEqual(self.versions(post), ["Two quotes first.", "Two quotes first, please."],
                         "The repeat must record nothing more.")
        late = author.post(self.edit_address("discussion_edit", post), unsent | {"markdown": "A late change."})
        self.assert_refused(late, 409, "The 15 minutes for editing this post have ended.",
                            "Go back to the Topic. You can post a reply to add to what you wrote.")
        self.assertEqual(len(self.versions(post)), 2, "A new request after the 15 minutes must record nothing.")
