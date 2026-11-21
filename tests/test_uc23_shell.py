"""Scene 4. Output escaping at the HTML boundary and object-independent refusals."""
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import resolve, reverse

from tests import test_discussion_posts as post_support
from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import assert_private, signed_in
from tests.test_uc2_pages import command_form, path, seated_board
from core import discussion_forms, discussion_pages


@override_settings(ALLOWED_HOSTS=["testserver"])
class ScopeAndShellTests(TransactionTestCase):
    def test_entered_bodies_stay_text(self):
        org, circle, thread = uuid4(), uuid4(), uuid4()
        address = reverse("discussion_topic", args=(org, circle, thread))
        request = RequestFactory().get(address)
        request.user = AnonymousUser()
        request.resolver_match = resolve(address)
        title = 'Vote on <title-sample> "A & B"'
        opening = "Edited <edited-sample> & wording"
        reply = '<reply-sample data-entered="yes">Type & read</reply-sample>'
        quotation = '<a href="/typed-destination">Quoted & unchanged</a>'
        second_line = "A second paragraph."
        hidden_excerpt = "<hidden-sample>Moderator's excerpt</hidden-sample>"
        category_name = "Discussion <category-sample>"
        context = {
            "request": request,
            "board_org": org, "board_circle": circle,
            "organisation": {"organism_uuid": org, "short_label": "Example", "full_name": "Example Trust"},
            "circle": {"circle_uuid": circle, "short_label": "Board", "full_name": "Board of Trustees"},
            "category": {"name": category_name, "can_reply": True},
            "topic": {"title": title, "topic_type": "TOPIC"},
            "posts": [
                {"markdown": opening + "\n\n" + second_line, "version_number": 2, "post_number": 1},
                {"markdown": reply, "post_number": 2, "quoted_markdown": quotation},
                {"markdown": hidden_excerpt, "post_number": 3, "hidden": True},
                {"markdown": None, "post_number": 4, "hidden": True},
            ],
        }
        page = Markup(render_to_string("core/discussion_topic.html", context, request=request))
        self.assertFalse(page.select("reply-sample"), "A reply's literal element must remain text when the Topic is rendered.")
        self.assertEqual([n["text"] for n in page.select("h1")], [title], "The rendered title must retain its exact punctuation.")
        bodies = page.select("div", classes=("post-text",))
        paragraphs = [[p["text"] for p in page.select("p", within=b)] for b in bodies]
        self.assertEqual(paragraphs, [[opening, second_line], [reply], [hidden_excerpt]],
                         "The edited opening, reply and released hidden excerpt must retain their independent paragraph text.")
        self.assertEqual([p["text"] for q in page.select("blockquote") for p in page.select("p", within=q)],
                         ["Quoted text", quotation], "The quotation must show the literal link text at the quoted version.")
        self.assertFalse(any(node["tag"].endswith("-sample") or "data-entered" in node["attrs"]
                             or node["attrs"].get("href") == "/typed-destination" for node in page.nodes),
                         "Entered markup must create no new element, attribute or destination.")
        self.assertIn("Post unavailable.", [n["text"] for n in page.select("p")],
                      "A withheld body must render only its neutral post label.")
        self.assertFalse(page.select("form"), "A page without a released command must contain no submission form.")
        parent_url = reverse("circle_page", args=(org, circle))
        phone = page.select("span", classes=("phone-path",))[0]
        self.assertEqual([a["attrs"]["href"] for a in page.select("a", within=phone)], [parent_url],
                         "The phone back link for a released Board must lead to the Board itself.")
        plain = Markup(render_to_string("core/discussion_topic.html", context | {"circle": None}, request=request))
        phone = plain.select("span", classes=("phone-path",))[0]
        self.assertTrue("‹ Board" in phone["text"] and not plain.select("a", within=phone),
                        "When the parent is withheld the phone breadcrumb must keep Board as plain text.")
        context["categories"] = [{"name": category_name, "topic_count": 1, "topics": [{"title": title, "thread_uuid": thread}]}]
        index = Markup(render_to_string("core/board_discussion.html", context, request=request))
        self.assertEqual([n["text"] for n in index.select("h3")], [category_name], "The index must show the literal category name.")
        self.assertEqual([n["text"] for n in index.select("a", href=address)], [title], "The index must show the literal opening title.")
        self.assertFalse(index.select("title-sample") or index.select("category-sample"),
                         "The index must create no element from the category or Topic label.")
        index_phone = index.select("span", classes=("phone-path",))[0]
        self.assertEqual([a["attrs"]["href"] for a in index.select("a", within=index_phone)], [parent_url],
                         "On a phone the Board discussion page must lead back to its parent, the Board (V1).")

    def test_vote_results_stay_off_the_topic_page_until_box_6(self):
        """Interim guard (item 201; capture AH): a vote-result row in the thread read is not shown as a post."""
        org, circle, thread, category = uuid4(), uuid4(), uuid4(), uuid4()
        opening = {"post_number": 1, "markdown": "Opening"}
        result = {"post_number": 2, "markdown": None, "post_type": "GROUP_GAZE", "hidden": True}
        reply = {"post_number": 3, "markdown": "Reply"}
        read = {"topic": {"title": "Repair", "category_uuid": category}, "posts": [opening, result, reply]}
        with mock.patch.object(discussion_pages.discussion, "board_categories", return_value={"categories": [{"category_uuid": category}]}), \
             mock.patch.object(discussion_pages.discussion_posts, "read_thread", return_value=read), \
             mock.patch.object(discussion_pages.member_navigation, "member_navigation",
                               side_effect=discussion_pages.member_navigation.NavigationUnavailable):
            _, context = discussion_pages._read_page(AnonymousUser(), org, circle, thread)
        self.assertEqual([post["post_number"] for post in context["posts"]], [1, 3],
                         "Reading a Topic that holds a vote result must show the opening and the reply, and leave the result row off the page until plan box 6.")

    def test_private_responses_are_indistinguishable(self):
        owner, board, _, coordinator, trustee = seated_board()
        support = post_support.DiscussionPostTests()
        args = support.board(owner, coordinator, board)
        reader = signed_in(trustee)
        known_title = "Private repair proposal"
        hidden = support.start(args, title=known_title)
        hidden_url = path(owner, board, hidden["thread"])
        self.assertContains(reader.get(hidden_url), known_title, msg_prefix="The same trustee must see the opening before it is hidden.")
        support.hide(args, hidden, True, as_coordinator=True)
        permitted = signed_in(coordinator).get(hidden_url)
        self.assertContains(permitted, known_title, msg_prefix="The authorised Coordinator must retain the concealed opening.")
        other_org, other_board, _, other_coordinator, _ = seated_board()
        other_scope = support.board(other_org, other_coordinator, other_board)
        foreign = support.start(other_scope, title="Other organisation's repair")
        other_url = path(other_org, other_board, foreign["thread"])
        foreign_page = signed_in(other_coordinator).get(other_url)
        self.assertEqual([node["text"] for node in Markup(foreign_page.content.decode()).select("h1")],
                         ["Other organisation's repair"], "The foreign control must open on its own Board for its reader.")
        missing_url = path(owner, board, uuid4())
        missing = reader.get(missing_url)
        self.assertEqual(missing.status_code, 404, "An unknown Topic must return the private page with status 404.")
        assert_private(self, missing)
        own_home = reverse("organisation_home", args=(owner.organism_id,))
        other_home = reverse("organisation_home", args=(other_org.organism_id,))
        self.assertIn(f'href="{own_home}"'.encode(), missing.content,
                      "A reader who belongs to the organisation in the address must be offered the way back to it (capture AI).")
        other_missing = reader.get(path(other_org, other_board, uuid4()))
        self.assertEqual(other_missing.status_code, 404, "An unknown Topic in another organisation must return the private page with status 404.")
        self.assertIn(b"Your organisations in INTEVIA", other_missing.content,
                      "A reader outside the organisation in the address must be offered only their own organisations.")
        self.assertNotIn(f'href="{other_home}"'.encode(), other_missing.content,
                         "The private page must not name or link an organisation the reader does not belong to.")
        cases = ((hidden_url, missing), (path(owner, board, foreign["thread"]), missing),
                 (other_url, other_missing), (path(other_org, board, hidden["thread"]), other_missing))
        for url, control in cases:
            with self.subTest(url=url):
                refused = reader.get(url)
                self.assertEqual((refused.status_code, refused.content), (404, control.content),
                                 "Within one organisation's address, hidden, cross-board and missing Topics must give byte-identical private pages.")
                self.assertEqual((refused.get("Cache-Control"), refused.get("Vary")),
                                 (control.get("Cache-Control"), control.get("Vary")),
                                 "All Topic refusals must have the missing control's private cache headers.")
                for fragment in (known_title, str(hidden["thread"]), str(foreign["thread"]), "Other organisation's repair"):
                    self.assertNotIn(fragment, refused.content.decode(), "A private refusal must omit each protected name and reference.")
        head = reader.head(hidden_url)
        self.assertEqual((head.status_code, head.content), (404, b""), "HEAD must preserve private status and return no body.")
        assert_private(self, head)
        self.assertEqual(reader.post(hidden_url).status_code, 405, "The read route must refuse a POST without dispatching a command.")

    def test_http_commands_require_genuine_post(self):
        with projected_forms() as (client, org, circle, category, thread):
            for route, service, fields in (
                (reverse("discussion_start_form", args=(org, circle)), "start_topic",
                 {"category": str(category), "type": "QUESTION", "title": "Payment authority"}),
                (reverse("discussion_topic", args=(org, circle, thread)), "reply", {"quote": ""}),
            ):
                with self.subTest(service=service):
                    action, hidden = command_form(client.get(route))
                    data = hidden | fields | {"markdown": "An intentional contribution."}
                    def recorded(**args):
                        return {"thread": args["thread_uuid"], "post": args["post_uuid"]}
                    with mock.patch.object(discussion_forms.discussion_posts, service, side_effect=recorded) as boundary:
                        for method in ("get", "head"):
                            answer = getattr(client, method)(action, data)
                            self.assertFalse(boundary.called, "A read request must stop before the discussion command.")
                            self.assertEqual(answer.status_code, 405, "The command address must require POST.")
                            self.assertEqual(answer["Allow"], "POST", "The refusal must identify the accepted method.")
                        for header in ("HTTP_PURPOSE", "HTTP_SEC_PURPOSE"):
                            answer = client.post(action, data, **{header: "prefetch"})
                            self.assertFalse(boundary.called, "A speculative submission must never call the service.")
                            self.assertEqual(answer.status_code, 400, "A preview must receive its own refusal.")
                        answer = client.post(action, {k: v for k, v in data.items() if k != "csrfmiddlewaretoken"})
                        self.assertFalse(boundary.called, "A submission without its CSRF token must stop at middleware.")
                        self.assertEqual(answer.status_code, 403, "The security check must refuse the missing token.")
                        self.assertEqual(client.post(action, data | {"command": data["command"] + "x"}).status_code,
                                         400, "A changed envelope must be refused.")
                        self.assertFalse(boundary.called, "An invalid envelope must never reach the service.")
                        kwargs = resolve(action).kwargs
                        for name in kwargs:
                            other = reverse(resolve(action).url_name, kwargs=kwargs | {name: uuid4()})
                            answer = client.post(other, data)
                            self.assertEqual(answer.status_code, 400, "A signed form cannot move to a different scoped address.")
                        self.assertFalse(boundary.called, "A changed target must never reach the command.")
                        alternate = (reverse("discussion_topic", args=(org, circle, thread)) if service == "start_topic"
                                     else reverse("discussion_start_form", args=(org, circle)))
                        _, other_form = command_form(client.get(alternate))
                        self.assertEqual(client.post(action, data | {"command": other_form["command"]}).status_code, 400,
                                         "A valid form for the other operation cannot authorise this command.")
                        original_session = client.cookies[settings.SESSION_COOKIE_NAME].value
                        session = client.session
                        session["browser"] = str(uuid4())
                        session.save()
                        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
                        self.assertEqual(client.post(action, data).status_code, 400,
                                         "Even the same actor must use a form issued to this browser session.")
                        client.cookies[settings.SESSION_COOKIE_NAME] = original_session
                        self.assertFalse(boundary.called, "A different operation or session must stop before the service.")
                        answer = client.post(action, data)
                        self.assertEqual(boundary.call_count, 1, "One genuine submission must reach its own service exactly once.")
                        self.assertEqual(answer.status_code, 303, "A successful form must redirect to a fresh read.")
                        assert_private(self, answer)

    def test_five_part_refusals(self):
        with projected_forms() as (client, org, circle, _, thread):
            url = reverse("discussion_topic", args=(org, circle, thread))
            action, fields = command_form(client.get(url))
            entered = '<submitted-example title="&">Earlier wording</submitted-example>'
            data = fields | {"markdown": entered, "quote": ""}
            help_text, open_help = "If you need help, contact a Board Coordinator.", "If you need help, contact an Organisation Coordinator."
            with mock.patch.object(discussion_forms.discussion_posts, "reply",
                                   side_effect=discussion_forms.discussion.DiscussionRefused(code="DISC_QUOTE")):
                known = client.post(action, data)
            page = Markup(known.content.decode())
            self.assertEqual([p["text"] for p in page.select("p", within=page.select("div", role="alert")[0])],
                             ["Choose a readable earlier post version from this Topic.", "This request recorded no new contribution.",
                              "Correct the form below and submit it again.", help_text],
                             "A known refusal must give the service's reason, effect, next step and named helper.")
            self.assertContains(known, "Your reply could not be recorded.", status_code=400,
                                msg_prefix="The heading must say what happened.")
            self.assertFalse(page.select("submitted-example"), "The submitted text must not become an HTML element.")
            self.assertIn(entered, [p["text"] for p in page.select("textarea")], "The reader must be able to recover their literal text.")
            with mock.patch.object(discussion_forms.discussion_posts, "reply", side_effect=DatabaseError):
                unknown = client.post(action, data)
            for part in ("Your change could not be confirmed.", "The cause is unknown.", "A contribution may already be recorded.",
                         "Check the Topic before trying the same submission again.", help_text):
                self.assertContains(unknown, part, status_code=503, msg_prefix="An uncertain result must explain all five parts.")
            for answer, code, reason in (
                (client.get(action), 405, "This address accepts a submitted form only."),
                (client.post(action, data, HTTP_SEC_PURPOSE="prefetch"), 400, "A preview cannot submit a discussion form."),
                (client.post(action, {"markdown": entered}), 403, "The form&#x27;s security check did not pass."),
            ):
                for part in ("The form could not be submitted.", reason, "This request recorded no new contribution.",
                             "Reopen the page and check the current wording and available actions.", open_help):
                    self.assertContains(answer, part, status_code=code, msg_prefix="The transport refusal must explain all five parts.")
                assert_private(self, answer)
            with mock.patch.object(discussion_forms, "_read_page", side_effect=discussion_forms.discussion.DiscussionUnavailable), \
                 mock.patch.object(discussion_forms, "_back_organisation", return_value=None):
                private = client.post(action, data)
            for part in ("You tried to open a discussion", "not authorised to view, or that does not exist",
                         "Nothing was changed.", "If you think you should have access", "contact an Organisation Coordinator."):
                self.assertContains(private, part, status_code=404, msg_prefix="An unavailable discussion must use the approved private refusal.")
            self.assertNotContains(private, "Earlier wording", status_code=404, msg_prefix="The private page must disclose no form or Topic text.")
            assert_private(self, private)

    def test_topic_type_labels(self):
        with projected_forms() as (client, org, circle, _, thread):
            start = client.get(reverse("discussion_start_form", args=(org, circle)))
            page = Markup(start.content.decode())
            selector, = page.select("select", name="type")
            pairs = [(n["attrs"]["value"], n["text"]) for n in page.select("option", within=selector)]
            self.assertEqual(pairs, [("TOPIC", "Topic"), ("QUESTION", "Question"), ("SUGGESTION", "Suggestion")],
                             "The form must offer the three stored types with their public labels.")
            request = start.wsgi_request
            _, detail = discussion_pages._read_page(request.user, org, circle, thread)
            for stored, expected in (("TOPIC", "Topic"), ("QUESTION", "Question"), ("SUGGESTION", "Suggestion"), ("OTHER", "")):
                context = detail | {"topic": detail["topic"] | {"topic_type": stored}}
                html = render_to_string("core/discussion_topic.html", context, request=request)
                meta = Markup(html).select("p", classes=("meta",))[0]["text"]
                self.assertEqual(meta.strip(), f"{expected} · Discussion".strip(),
                                 "The Topic detail must use only the registered human-readable type label.")


@contextmanager
def projected_forms(*, office_id=None):
    """One signed browser session and released read projections; no database access or created rows."""
    org, circle, category, thread = (uuid4() for _ in range(4))
    row = dict(category_uuid=category, name="Discussion", can_start_topic=True, can_reply=True, retired=False)
    context = dict(board_org=org, board_circle=circle, circle={"short_label": "Board", "circle_uuid": circle},
                   organisation={"short_label": "Example", "organism_uuid": org}, category=row)
    offices = [{"office_definition_uuid": office_id, "label": "Treasurer"}] if office_id else []
    directory = {"circles": [context["circle"]], "organisation": context["organisation"], "board_offices": offices}
    topic = context | {"topic": {"title": "Authority", "topic_type": "TOPIC", "thread_uuid": thread, "closed": False},
                       "posts": [{"post_number": 1, "post_uuid": uuid4(), "version_uuid": uuid4(),
                                  "version_number": 1, "markdown": "Opening."}]}
    user = SimpleNamespace(is_authenticated=True, pk=17, get_full_name=lambda: "Reader")
    identity = SimpleNamespace(display_name="Reader", access_state="active", colour_mode="light")
    with override_settings(SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies", ALLOWED_HOSTS=["testserver"]), \
         mock.patch.object(discussion_forms, "atomic", nullcontext), \
         mock.patch("django.contrib.auth.middleware.get_user", return_value=user), \
         mock.patch("core.session.IdentityAccessMiddleware._valid_identity", return_value=identity), \
         mock.patch.object(discussion_forms, "_categories", return_value=[row]), \
         mock.patch.object(discussion_forms.member_navigation, "member_navigation", return_value=directory), \
         mock.patch.object(discussion_pages, "_read_page", side_effect=lambda *args: ("core/discussion_topic.html", dict(topic))), \
         mock.patch.object(discussion_forms, "_read_page", side_effect=lambda *args: ("core/discussion_topic.html", dict(topic))):
        client = Client(enforce_csrf_checks=True)
        session = client.session
        session["browser"] = str(uuid4())
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        yield client, org, circle, category, thread


class OfficeTypeFormTests(SimpleTestCase):
    def test_office_type_refusal_and_correction(self):
        office_id = uuid4()
        with projected_forms(office_id=office_id) as (client, org, circle, category, _):
            start = reverse("discussion_start_form", args=(org, circle))
            board_page = client.get(start, {"category": str(category)})
            board_action, board_fields = command_form(board_page)
            board = Markup(board_page.content.decode())
            chooser, = board.select("form", method="get")
            choice, = board.select("select", within=chooser, name="office")
            treasurer, = [n for n in board.select("option", within=choice) if n["text"] == "Treasurer"]
            get_fields = {n["attrs"]["name"]: n["attrs"]["value"] for n in board.select("input", within=chooser)}
            get_fields["office"] = treasurer["attrs"]["value"]
            picked = client.get(chooser["attrs"]["action"], get_fields)
            action, fixed = command_form(picked)
            self.assertEqual((action, fixed["office"], fixed["type"]), (board_action, str(office_id), "TOPIC"),
                             "Choosing the Treasurer from the Board must open its fixed-type form.")
            self.assertFalse(Markup(picked.content.decode()).select("select", name="type"),
                             "An office start must not offer alternative types.")
            draft = {"category": str(category), "title": "Who approves payments?", "markdown": "Keep my <draft>."}

            def recorded(**args):
                return {"thread": args["thread_uuid"], "post": args["post_uuid"]}

            with mock.patch.object(discussion_forms.discussion_posts, "start_topic", side_effect=recorded) as writer:
                for kind in ("QUESTION", "SUGGESTION"):
                    for fields in (fixed, board_fields | {"office": str(office_id)}):
                        with self.subTest(kind=kind, pinned_on_entry=fields["command"] == fixed["command"]):
                            denied = client.post(action, fields | draft | {"type": kind})
                            self.assertFalse(writer.called, "An office with another type must stop before START.")
                            self.assertEqual(denied.status_code, 400, "This combination must receive a correctable refusal.")
                            assert_private(self, denied)
                            page = Markup(denied.content.decode())
                            self.assertEqual([n["text"] for n in page.select("h1")], ["Your Topic could not be recorded."], "An office with another type must give the Topic refusal heading.")
                            alert, = page.select("div", role="alert")
                            self.assertEqual([n["text"] for n in page.select("p", within=alert)], [
                                "Only the Topic type can be linked to an office.", "This request recorded no new contribution.",
                                "Submit the Topic below to keep the office link.", "If you need help, contact a Board Coordinator.",
                            ], "The refusal must explain the office rule and how to correct this very submission.")
                            self.assertContains(denied, "About this office · Treasurer", status_code=400,
                                                msg_prefix="The trustee must see which office the corrected draft will cite.")
                            retry_action, retry = command_form(denied)
                            title, = page.select("input", name="title")
                            body, = page.select("textarea", name="markdown")
                            self.assertEqual((retry_action, retry["command"], retry["office"], retry["type"],
                                              title["attrs"]["value"], body["text"]),
                                             (action, fields["command"], str(office_id), "TOPIC", draft["title"], draft["markdown"]),
                                             "Correction must keep the office, command, title and text and fix the type.")
                accepted = client.post(retry_action, retry | draft)
                self.assertEqual(accepted.status_code, 303, "The corrected office Topic must reach START.")
                self.assertEqual((writer.call_args.kwargs["topic_type"], writer.call_args.kwargs["office_definition_uuid"]),
                                 ("TOPIC", office_id), "The service must receive the selected office and fixed type.")
                for kind in ("QUESTION", "SUGGESTION"):
                    _, fresh = command_form(client.get(start))
                    accepted = client.post(board_action, fresh | draft | {"type": kind})
                    self.assertEqual(accepted.status_code, 303, "Both non-Topic types remain available without an office.")
                    self.assertEqual((writer.call_args.kwargs["topic_type"], writer.call_args.kwargs["office_definition_uuid"]),
                                     (kind, None), "The unlinked form must pass the chosen type without an office pin.")


class RefusalFixWitnessTests(SimpleTestCase):
    """VC fix v1.0 witnesses (Gemini step 2, O1): the help line, the plain default, kept text and prerender."""

    def test_help_line_names_the_nearest_coordinator(self):
        cases = (({"circle": {"short_label": "Applications"}}, "If you need help, contact an Applications Coordinator."),
                 ({"circle": {"short_label": "Board"}}, "If you need help, contact a Board Coordinator."),
                 ({"circle": None}, "If you need help, contact an Organisation Coordinator."),
                 ({}, "If you need help, contact an Organisation Coordinator."))
        for context, line in cases:
            with self.subTest(context=context):
                self.assertEqual(discussion_forms.help_line(context), line,
                                 "The help line must name the nearest coordinator with the right article.")

    def test_refusals_keep_text_and_never_show_service_messages(self):
        with projected_forms() as (client, org, circle, category, thread):
            url = reverse("discussion_topic", args=(org, circle, thread))
            action, fields = command_form(client.get(url))
            data = fields | {"markdown": "Reply text.", "quote": ""}
            internal = discussion_forms.discussion.MESSAGES["CQ_LOCKED"]
            with mock.patch.object(discussion_forms.discussion_posts, "reply",
                                   side_effect=discussion_forms.discussion.DiscussionRefused(code="CQ_LOCKED")):
                unmapped = client.post(action, data)
            self.assertContains(unmapped, "The discussion service refused this contribution.", status_code=409,
                                msg_prefix="An unmapped service refusal must give the plain sentence.")
            self.assertNotContains(unmapped, internal, status_code=409,
                                   msg_prefix="An unmapped service refusal must never show the service's own message.")
            prerender = client.post(action, data, HTTP_SEC_PURPOSE="prerender")
            self.assertContains(prerender, "A preview cannot submit a discussion form.", status_code=400,
                                msg_prefix="A prerendered request must stop before the service.")
            start = reverse("discussion_start_form", args=(org, circle))
            start_action, start_fields = command_form(client.get(start, {"category": str(category)}))
            typed = {"title": "<b>Kept title</b>", "markdown": "<i>Kept & typed</i>", "type": "TOPIC", "category": str(category)}
            stale = client.post(start_action, start_fields | typed | {"command": "no-longer-valid"})
            self.assertContains(stale, "The form could not be submitted.", status_code=400,
                                msg_prefix="A form that no longer matches must give the form refusal.")
            self.assertContains(stale, "If you need help, contact an Organisation Coordinator.", status_code=400,
                                msg_prefix="A refusal without a known Circle must name an Organisation Coordinator.")
            page = Markup(stale.content.decode())
            self.assertIn("Your submitted text", [n["text"] for n in page.select("h2")],
                          "A refusal without Board context must still give the reader their typed text.")
            self.assertContains(stale, "&lt;i&gt;Kept &amp; typed&lt;/i&gt;", status_code=400,
                                msg_prefix="The kept text must be shown as text, not markup.")
            self.assertNotContains(stale, "<i>Kept", status_code=400, msg_prefix="Typed markup must not become an HTML element.")
