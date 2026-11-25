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
            # HG capture AA of 24 Nov 2026 (Q5): the Board's office chooser is removed. A member now reaches the
            # office-pinned form from the office page, at the address discussion_forms.office_start_url builds.
            picked = client.get(start, {"category": str(category), "office": str(office_id)})
            action, fixed = command_form(picked)
            self.assertEqual((action, fixed["office"], fixed["type"]), (board_action, str(office_id), "TOPIC"),
                             "Starting from the Treasurer's office page must open its fixed-type form.")
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


class VoteTransportTests(SimpleTestCase):
    def test_only_an_intentional_vote_post_reaches_the_command(self):
        from core import discussion_vote
        with projected_forms() as (client, org, circle, _, thread):
            page = client.get(reverse("discussion_topic", args=(org, circle, thread)))
            _, fields = command_form(page)
            for name, op in (("discussion_put_forward", "INITIATE"), ("discussion_prepare", "PREPARED"),
                             ("discussion_confirm_clarity", "CONFIRM"), ("discussion_clarity_question", "QUESTION"),
                             ("discussion_clarity_conflict", "CONFLICT"), ("discussion_clarity_proceed", "PROCEED"),
                             ("discussion_clarity_extend", "EXTEND"), ("discussion_clarity_close", "CLOSE"),
                             ("discussion_clarity_withdraw", "WITHDRAW"), ("discussion_answer", "ANSWER")):
                url = reverse(name, args=(org, circle, thread))
                # Include the window and confirmation required by the five Clarity commands.
                confirmation = op if op in ("CLOSE", "WITHDRAW") else None
                token = discussion_forms.issue_form(page.wsgi_request, op, org, circle, thread=thread,
                    version=uuid4(), window=uuid4(), confirmation=confirmation,
                    ballot=uuid4() if op == "ANSWER" else None)
                data = {"csrfmiddlewaretoken": fields["csrfmiddlewaretoken"], "vote_command": token}
                if op == "ANSWER":
                    # A valid choice, so that malformed input cannot mask a broken gate.
                    data["choice"] = "QUALIFIED"
                with self.subTest(operation=op), mock.patch.object(discussion_vote.cq, "_lock") as lock:
                    self.assertEqual(client.get(url).status_code, 405, "A link cannot submit a vote action.")
                    self.assertEqual(client.head(url).status_code, 405, "HEAD cannot submit a vote action.")
                    self.assertEqual(client.post(url, data, HTTP_SEC_PURPOSE="prefetch").status_code, 400,
                                     "A speculative POST cannot start a vote action.")
                    self.assertEqual(client.post(url, data, HTTP_PURPOSE="prerender").status_code, 400,
                                     "Prerendering cannot submit a vote action.")
                    self.assertEqual(client.post(url, {"vote_command": token}).status_code, 403,
                                     "The signed command still needs a CSRF token.")
                    self.assertEqual(client.post(url, data | {"vote_command": fields["command"]}).status_code, 400,
                                     "A genuine reply form cannot authorise a vote.")
                    if op == "PREPARED":
                        self.assertEqual(client.post(url, data).status_code, 400,
                                         "A signed preparation form from before confirmation must be refused.")
                    lock.assert_not_called()

    def test_translated_unavailable_uses_the_private_response(self):
        from django.db import IntegrityError
        from core import discussion_vote

        with projected_forms() as (client, org, circle, _, thread), mock.patch.object(discussion_forms, "_back_organisation", return_value=None):
            page = client.get(reverse("discussion_topic", args=(org, circle, thread)))
            _, fields = command_form(page)
            token = discussion_forms.issue_form(page.wsgi_request, "INITIATE", org, circle,
                                                thread=thread, version=uuid4())
            url = reverse("discussion_put_forward", args=(org, circle, thread))
            data = {"csrfmiddlewaretoken": fields["csrfmiddlewaretoken"], "vote_command": token}
            refusal = discussion_vote.discussion.DiscussionRefused(code="DISC_UNAVAILABLE")
            with mock.patch.object(discussion_forms, "_envelope", side_effect=refusal):
                direct = client.post(url, data)
            with mock.patch.object(discussion_forms, "_envelope", side_effect=IntegrityError), \
                 mock.patch.object(discussion_vote.cq, "translate_refusal", side_effect=refusal):
                translated = client.post(url, data)
            self.assertEqual(direct.status_code, 404, "An unavailable Topic must have a private response.")
            self.assertEqual(translated.status_code, direct.status_code, "Translated unavailability must also be private.")
            self.assertEqual(Markup(translated.content.decode()).select("h1")[0]["text"],
                             Markup(direct.content.decode()).select("h1")[0]["text"],
                             "Both refusal paths must give the same heading.")


class ResultCardProjectionTests(SimpleTestCase):
    def test_hidden_result_keeps_its_number_and_has_no_link(self):
        org, circle, thread, category, post = (uuid4() for _ in range(5))
        request = RequestFactory().get(reverse("discussion_topic", args=(org, circle, thread)))
        request.user = AnonymousUser()
        request.resolver_match = resolve(request.path)
        opening = {"post_number": 1, "markdown": "Opening"}
        result = {"post_number": 2, "markdown": None, "post_type": "GROUP_GAZE", "hidden": True, "post_uuid": post}
        reply = {"post_number": 3, "markdown": "Reply"}
        read = {"topic": {"thread_uuid": thread, "title": "Repair", "category_uuid": category}, "posts": [opening, result, reply]}
        with mock.patch.object(discussion_pages.discussion, "board_categories", return_value={"categories": [{"category_uuid": category}]}), \
             mock.patch.object(discussion_pages.discussion_posts, "read_thread", return_value=read), \
             mock.patch.object(discussion_pages.discussion_posts, "edit_standing", return_value={"read_at": None, "own": set()}), \
             mock.patch.object(discussion_pages.member_navigation, "member_navigation", side_effect=discussion_pages.member_navigation.NavigationUnavailable), \
             mock.patch.object(discussion_pages.cq_reads, "result_post", side_effect=discussion_pages.discussion.DiscussionUnavailable) as destination:
            # The page's read also takes the reader's edit standing (the posts box, part PB-4); this reader owns no post.
            template, context = discussion_pages._read_page.__wrapped__(AnonymousUser(), org, circle, thread)
            page = Markup(render_to_string(template, context, request=request))
            self.assertEqual([n["text"] for n in page.select("h3")], ["Post 2", "Post 3"], "A withheld result must keep its place.")
            self.assertIn("Post unavailable.", [n["text"] for n in page.select("p")])
            self.assertNotIn(str(post), render_to_string(template, context, request=request), "No withheld result identity may appear in the page.")
            destination.assert_not_called()
            # A released summary still requires a separate grant for its destination.
            result.update(hidden=False, markdown="Carried.")
            template, context = discussion_pages._read_page.__wrapped__(AnonymousUser(), org, circle, thread)
            html = render_to_string(template, context, request=request)
            self.assertIn("Carried.", html)
            self.assertNotIn("View vote result", html, "A denied result destination must have no link.")
            destination.assert_called_once()


class DiscussionNavigationTests(SimpleTestCase):
    def test_board_back_is_the_same_secondary_button(self):
        with projected_forms() as (client, org, circle, _, thread):
            topic_url = reverse("discussion_topic", args=(org, circle, thread))
            board_url = reverse("board_discussion", args=(org, circle))
            detail = client.get(topic_url)
            endpoint, fields = command_form(detail)
            refusal = client.post(endpoint, fields | {"markdown": "", "quote": ""})
            for response in (detail, refusal):
                links = Markup(response.content.decode()).select("a", href=board_url, classes=("button", "secondary"))
                self.assertEqual([a["text"] for a in links], ["Back to Board discussion"],
                                 "Topic and refusal must have the same single secondary Board destination.")

    def test_reply_refusal_returns_to_its_topic(self):
        with projected_forms() as (client, org, circle, _, thread):
            url = reverse("discussion_topic", args=(org, circle, thread))
            action, fields = command_form(client.get(url))
            response = client.post(action, fields | {"markdown": "", "quote": ""})
            links = Markup(response.content.decode()).select("a", href=url)
            self.assertEqual([a["text"] for a in links], ["Back to Topic"], "The refused reply must lead back to its own Topic.")

    def test_unknown_start_returns_to_board_to_look_for_topic(self):
        with projected_forms() as (client, org, circle, category, _):
            form = client.get(reverse("discussion_start_form", args=(org, circle)), {"category": str(category)})
            action, fields = command_form(form)
            with mock.patch.object(discussion_forms.discussion_posts, "start_topic", side_effect=DatabaseError):
                response = client.post(action, fields | {"title": "One payment", "type": "SUGGESTION",
                                                         "markdown": "Please review.", "category": str(category)})
            self.assertContains(response, "Check the Board discussion for your Topic before trying the same submission again.", status_code=503)
            self.assertNotContains(response, "Check the Topic before", status_code=503)
            self.assertNotContains(response, "Back to Topic", status_code=503)

    def test_correctable_start_has_one_board_destination(self):
        with projected_forms() as (client, org, circle, category, _):
            form = client.get(reverse("discussion_start_form", args=(org, circle)), {"category": str(category)})
            action, fields = command_form(form)
            response = client.post(action, fields | {"title": "One payment", "type": "SUGGESTION",
                                                     "markdown": "", "category": str(category)})
            self.assertEqual(response.status_code, 400)
            board = reverse("board_discussion", args=(org, circle))
            links = Markup(response.content.decode()).select("a", href=board)
            self.assertEqual([a["text"] for a in links], ["Back to Board discussion"], "A correctable start must offer one way back to this Board.")

    def test_index_start_uses_shared_button_and_selected_category(self):
        org, circle, category = uuid4(), uuid4(), uuid4()
        request = RequestFactory().get(reverse("board_discussion", args=(org, circle)))
        request.user = AnonymousUser()
        request.resolver_match = resolve(request.path)
        ctx = dict(board_org=org, board_circle=circle,
                   categories=[{"category_uuid": category, "name": "Discussion", "can_start_topic": True}])
        from django.test.signals import template_rendered
        used = []

        def collect(sender, template, context, **kwargs):
            if template.name == "core/components/button.html":
                used.append(context.flatten())

        template_rendered.connect(collect, weak=False)
        self.addCleanup(template_rendered.disconnect, collect)
        page = Markup(render_to_string("core/board_discussion.html", ctx, request=request))
        self.assertEqual([row["label"] for row in used if row.get("label") == "Start a Topic"], ["Start a Topic"],
                         "The permitted category must render Start a Topic through the shared button component.")
        address = reverse("discussion_start_form", args=(org, circle)) + "?category=" + str(category)
        self.assertEqual([n["text"] for n in page.select("a", href=address, classes=("button", "primary"))], ["Start a Topic"])

    def test_vote_refusals_explain_the_actual_cause(self):
        org, circle, thread = uuid4(), uuid4(), uuid4()
        request = RequestFactory().post(reverse("discussion_prepare", args=(org, circle, thread)))
        request.user = AnonymousUser()
        request.resolver_match = resolve(request.path)
        context = {"board_org": org, "board_circle": circle, "governance_board": True, "circle": {"short_label": "Board"}}
        for code, expected in (
            ("CQ_INIT_VERSION", "The opening post has changed since you opened this form."),
            ("CQ_PREPARE_SUGGESTION", "Only the author or the current Board Chair can mark this Suggestion as prepared."),
            ("CQ_PREPARE_OFFICE", "Only the officer or administrator authorised for this vote can mark it as prepared."),
            ("CQ_PREPARATION", "The vote is no longer within its preparation period."),
            ("CQ_FULL_AUDIENCE", "The Suggestion must be visible to everyone eligible for this Board vote."),
            ("CQ_REQUEST", "This form already recorded a different vote action."),
        ):
            with self.subTest(reason=code):
                page = Markup(discussion_forms.refusal(request, code, context).content.decode())
                self.assertEqual([h["text"] for h in page.select("h1")], ["Your vote action could not be recorded."])
                parts = [p["text"] for p in page.select("p", within=page.select("div", role="alert")[0])]
                self.assertEqual(parts, [expected, "This request recorded no new vote action.",
                                         "Reopen the page and check the current wording and available actions.",
                                         "If you need help, contact a Board Coordinator."],
                                 "Each vote refusal must identify its own cause, effect, next action and helper.")


    def test_preparation_refusal_outside_board_names_only_the_author(self):
        org, circle, thread = uuid4(), uuid4(), uuid4()
        request = RequestFactory().post(reverse("discussion_prepare", args=(org, circle, thread)))
        request.user = AnonymousUser()
        request.resolver_match = resolve(request.path)
        response = discussion_forms.refusal(request, "CQ_PREPARE_SUGGESTION")
        self.assertContains(response, "Only the author can mark this Suggestion as prepared.", status_code=409)
        self.assertNotContains(response, "Board Coordinator", status_code=409)


class VotePanelPresentationTests(SimpleTestCase):
    def setUp(self):
        from datetime import datetime, timedelta, timezone

        self.when = datetime(2026, 11, 22, 15, tzinfo=timezone(timedelta(hours=2)))
        self.later = self.when + timedelta(days=1)
        self.request = RequestFactory().get("/")
        self.request.user = AnonymousUser()

    def vote_markup(self, **row):
        from core import discussion_vote

        with mock.patch.object(discussion_vote.cq, "board_topic_vote", return_value=row):
            vote = discussion_vote.panel(self.request, uuid4(), uuid4(), uuid4())
        return Markup(render_to_string("core/components/board_vote.html", {"vote": vote}))

    def test_preparation_help_describes_the_readers_context(self):
        expected = ("Discuss the Suggestion here first. Its author can mark it prepared for Clarity or voting; "
                    "if the preparation deadline passes first, it moves on as unprepared.")
        markup = self.vote_markup(phase="PREPARATION", read_at=self.when, deadline=self.later)
        notice, = markup.select("div", classes=("notice", "vote-feedback"))
        self.assertEqual([p["text"] for p in markup.select("p", within=notice)],
                         [expected, "The wording can still change until voting opens."],
                         "Outside the Board only the author should be named in the preparation help.")
        for row in ({"phase": "PREPARATION", "specialist_preparation": True}, {"phase": "VOTING"}):
            with self.subTest(row=row):
                self.assertNotIn(expected, self.vote_markup(**row).select("section")[0]["text"],
                                 "The author instruction belongs only to ordinary Suggestion preparation.")

    def test_clarity_wording_uses_the_deciding_body(self):
        for body, election, noun, office in (
            ("BOARD", False, "Board members", "Board Chair"),
            ("CIRCLE", False, "Circle members", "Circle Coordinator"),
            ("COMMITTEE", False, "committee members", "Circle Coordinator"),
            ("BOARD", True, "voters", "Board Chair"),
        ):
            with self.subTest(body=body, election=election):
                data = dict(body_kind=body, election=election, read_at=self.when, deadline=self.later)
                opened = self.vote_markup(phase="VOTING", opening_count=5, **data)
                self.assertEqual(opened.select("p")[1]["text"],
                                 f"All 5 eligible {noun} were clear, so voting opened on this wording.")
                waiting = self.vote_markup(phase="CLARITY", count={"eligible": 3, "confirmed": 3},
                                           stepped_out=True, can_proceed=False, **data)
                self.assertEqual(waiting.select("p")[1]["text"], "3 of 3 are clear.")
                self.assertEqual(waiting.select("p")[2]["text"],
                                 f"A member has stepped out of this decision, and all 3 remaining {noun} are clear. "
                                 f"Voting opens automatically after Clarity ends, or sooner if the {office} opens it.")

    def test_vote_feedback_groups_each_sections_messages_by_speaker(self):
        confirmed = "You said you're clear on this wording."
        question = " If something becomes unclear, you can still ask a question."
        conflict = "You have declared a conflict and stepped out of this decision."
        opening = "All 3 eligible Board members were clear, so voting opened on this wording."
        step = ("A member has stepped out of this decision, and all 3 remaining Board members are clear. "
                "Voting opens automatically after Clarity ends, or ")
        early = {"read_at": self.when, "deadline": self.later}
        count = {"count": {"eligible": 3, "confirmed": 3}}
        states = [
            ("unconfirmed trustee", "CLARITY", early, [], True),
            ("author clear by authoring", "CLARITY", early | {"is_author": True}, [], True),
            ("read-only trustee", "CLARITY", early, [], True),
            ("confirmed trustee", "CLARITY", early | {"own_clear": True}, [confirmed + question], True),
            ("at the deadline", "CLARITY", {"own_clear": True, "read_at": self.later, "deadline": self.later}, [confirmed], False),
            ("response period", "CLARITY", {"own_clear": True, "read_at": self.later, "deadline": self.when}, [confirmed], False),
            ("declarer", "CLARITY", early | {"own_conflict": True}, [conflict], True),
            ("Chair", "CLARITY", early | count | {"stepped_out": True, "can_proceed": True, "own_clear": True},
             [step + "you can open it now."], True),
            ("declarer sees both facts", "CLARITY", early | count | {"stepped_out": True, "own_conflict": True},
             [step + "sooner if the Board Chair opens it.", conflict], True),
            ("opened by rule", "VOTING", early | {"opening_count": 3}, [opening], False),
            ("ordinary opening", "VOTING", early, [], False),
            ("awaiting result", "VOTING", {"read_at": self.later, "deadline": self.when}, [], False),
            ("specialist preparation", "PREPARATION", early | {"specialist_preparation": True}, [], True),
        ]
        for reader, phase, facts, expected, editable in states:
            with self.subTest(reader=reader):
                page = self.vote_markup(phase=phase, markdown="Member's words.", version_number=2, **facts)
                if editable:
                    expected = [*expected, "The wording can still change until voting opens."]
                feedback = page.select("div", classes=("notice", "vote-feedback"))
                self.assertEqual(len(feedback), int(bool(expected)), "Use one feedback bubble when the stage has something to say.")
                if feedback:
                    self.assertEqual([p["text"] for p in page.select("p", within=feedback[0])], expected,
                                     "Put the reader's applicable messages together, in their own paragraphs.")
                wording, = page.select("div", classes=("motion-wording", "card"))
                self.assertEqual(wording["text"].strip(), "Member's words.", "Only the human wording belongs in its glass.")
                notices = [n for n in page.nodes if "notice" in n["attrs"].get("class", "").split()]
                self.assertEqual(notices, feedback, "The edit note must not create a second notice.")
                section, = page.select("article", **{"aria-labelledby": "vote-wording-heading"})
                self.assertIs(wording["parents"][-1], section, "The human wording belongs to its labelled section.")
                if feedback:
                    self.assertLess(page.nodes.index(feedback[0]), page.nodes.index(section),
                                    "INTEVIA speaks before the wording heading.")
                # Layout C (HG captures AQ1 and AR1 of 24 Nov 2026; the posts box, part PB-6b) replaces the earlier
                # rule that phase, date and count sit outside the glass: they now lead the one vote card, and the
                # wording's heading sits inside the wording's own card. The section's heading stays outside both.
                glass = lambda node: [n for n in (*node["parents"], node)
                                      if {"card", "notice"} & set(n["attrs"].get("class", "").split())]
                for heading in page.select("h2"):
                    self.assertEqual(glass(heading), [], "The section's heading sits outside the glass.")
                card, = page.select("div", classes=("card", "vote-card"))
                paragraphs = page.select("p")
                status = paragraphs[:2] if "count" in facts else paragraphs[:1]
                self.assertEqual(status[0]["attrs"].get("class"), "meta", "The phase and date lead the vote card as its quiet first line.")
                for placed in status:
                    self.assertEqual(glass(placed), [card], "Phase, date and count sit in the vote card, and in no other glass.")
                title, = page.select("h3", id="vote-wording-heading")
                self.assertEqual(glass(title), [section], "The wording's heading sits inside the wording's own card.")

    def test_response_row_orders_only_the_available_controls(self):
        context = {"board_org": uuid4(), "board_circle": uuid4(), "topic": {"thread_uuid": uuid4()}}
        for confirm, question, reply, expected in (
            (True, True, True, ["I'm clear", "Ask a question"]),
            (False, True, True, ["Ask a question"]),
            (True, True, False, ["I'm clear"]),
            (False, True, False, []),
            (False, False, True, []),
        ):
            with self.subTest(confirm=confirm, question=question, reply=reply):
                vote = {"confirm_command": confirm, "question_url": "/?clarity=question" if question else None}
                page = Markup(render_to_string("core/components/board_vote.html", context | {"vote": vote, "reply_command": reply}, request=self.request))
                rows = page.select("div", classes=("clarity-responses",))
                self.assertEqual(len(rows), int(bool(expected)), "Render the row only for actionable responses.")
                if rows:
                    children = [node for node in page.nodes if node["parents"] and node["parents"][-1] is rows[0]]
                    labels = []
                    for child in children:
                        if child["tag"] == "form":
                            action, = page.select("button", within=child, classes=("primary",), type="submit")
                        else:
                            self.assertEqual(child["tag"], "a", "The question opens a separate form on the Topic.")
                            self.assertIn("secondary", child["attrs"]["class"].split())
                            action = child
                        labels.append(action["text"].strip())
                    self.assertEqual(labels, expected, "Put the primary confirmation first, followed by the secondary question.")

    def test_focus_and_stop_colours_follow_the_selected_vote_form(self):
        from core import discussion_vote

        ids = (uuid4(), uuid4(), uuid4())
        topic = reverse("discussion_topic", args=ids)
        version = uuid4()
        base = {"phase": "CLARITY", "confirm_version": version, "question_version": version,
                "clarity_actions": ["CONFLICT", "PROCEED", "EXTEND", "CLOSE", "WITHDRAW"],
                "clarity_window": uuid4(), "clarity_version": version}

        def render(query, *, can_reply=True):
            request = RequestFactory().get(topic, query)
            request.user = AnonymousUser()
            request.session = SimpleNamespace(session_key="fixture")
            with mock.patch.object(discussion_vote.cq, "board_topic_vote", return_value=base.copy()):
                vote = discussion_vote.panel(request, *ids)
            return Markup(render_to_string("core/components/board_vote.html", {
                "vote": vote, "board_org": ids[0], "board_circle": ids[1],
                "topic": {"thread_uuid": ids[2]}, "reply_command": can_reply,
            }, request=request))

        choices = render({})
        red = choices.select("a", classes=("button", "danger"))
        self.assertEqual([a["text"] for a in red], ["Declare a conflict", "Close without a vote", "Withdraw Suggestion"],
                         "Each stop opens its own form with a red button.")
        self.assertFalse(choices.select("textarea"), "The choice of commands must show no entry field.")
        for slug, submit, variant, field in (
            ("question", "Submit question", "primary", "markdown"),
            ("conflict", "Declare conflict", "primary", "basis"),
            ("close", "Close without a vote", "danger", None),
            ("withdraw", "Withdraw Suggestion", "danger", None),
        ):
            with self.subTest(form=slug):
                anchor = "clarity-input" if field else "clarity-confirmation"
                self.assertEqual(len(choices.select("a", href=f"{topic}?clarity={slug}#{anchor}")), 1)
                page = render({"clarity": slug})
                buttons = page.select("button")
                self.assertEqual([b["text"] for b in buttons], [submit], "Show only the selected vote submit.")
                self.assertIn(variant, buttons[0]["attrs"]["class"].split(), "Use the ruled submit colour.")
                links = page.select("a")
                self.assertEqual([(a["text"], a["attrs"]["href"]) for a in links], [("Cancel", topic + "#vote")],
                                 "Cancel alone returns to the choices on this Topic.")
                self.assertIn("secondary", links[0]["attrs"]["class"].split())
                form, = page.select("form")
                self.assertEqual((form["attrs"]["method"], form["attrs"]["action"]),
                                 ("post", reverse("discussion_clarity_" + slug, args=ids)))
                if field:
                    box, = page.select("textarea", within=form, name=field)
                    self.assertIn("required", box["attrs"], "A question or conflict needs the member's own words.")
                else:
                    self.assertEqual(len(page.select("div", classes=("card", "notice", "refusal"))), 1)
        withheld = render({"clarity": "question"}, can_reply=False)
        self.assertFalse(withheld.select("textarea"), "A direct preview cannot bypass the ordinary reply gate.")
        self.assertTrue(withheld.select("form", action=reverse("discussion_confirm_clarity", args=ids)),
                        "An unavailable question must not hide an admitted confirmation.")

    def test_private_reason_sits_between_system_feedback_and_wording(self):
        reason = "My business quoted <b>for this work</b>."
        for phase in ("CLARITY", "VOTING"):
            with self.subTest(phase=phase):
                page = self.vote_markup(phase=phase, own_conflict=True, own_conflict_reason=reason)
                feedback, = page.select("div", classes=("notice", "vote-feedback"))
                own, = page.select("div", classes=("card", "vote-reason"))
                title, = page.select("h3", id="vote-wording-heading")
                self.assertEqual(own["text"].strip(), reason, "The declarer's words must be escaped as text.")
                self.assertFalse(page.select("b", within=own))
                self.assertLess(page.nodes.index(feedback), page.nodes.index(own), "INTEVIA speaks first.")
                self.assertLess(page.nodes.index(own), page.nodes.index(title), "The private reason precedes the wording.")
                self.assertFalse(self.vote_markup(phase=phase).select("div", classes=("vote-reason",)),
                                 "An empty or withheld reason creates no bubble.")

    def test_initial_command_and_stop_previews_keep_their_own_sections(self):
        context = {"board_org": uuid4(), "board_circle": uuid4(), "topic": {"thread_uuid": uuid4()}}
        template = "core/components/board_vote.html"
        initial = {"command": "test-command", "label": "Put to a Board vote", "route": "discussion_put_forward"}
        page = Markup(render_to_string(template, context | {"vote": initial}, request=self.request))
        self.assertEqual([n["text"].strip() for n in page.select("button")], ["Put to a Board vote"])
        self.assertFalse(page.select("article") or page.select("div", classes=("card",)) or page.select("div", classes=("notice",)),
                         "Before initiation there is no stage message or wording card.")
        for heading, effect in (
            ("Close without a vote?", "Clarity will end. No ballot will open on this Suggestion."),
            ("Withdraw this Suggestion?", "Your Suggestion will be recorded as withdrawn. No ballot will open."),
        ):
            with self.subTest(heading=heading):
                preview = {"heading": heading, "effect": effect, "token": "test-command", "cancel": "#vote"}
                vote = {"phase": "CLARITY", "line": "Clarity period ended", "clarity_prompt": preview}
                page = Markup(render_to_string(template, context | {"vote": vote}, request=self.request))
                article, = page.select("article", id="clarity-confirmation")
                bubble, = page.select("div", within=article, classes=("card", "notice", "refusal"))
                self.assertEqual([p["text"] for p in page.select("p", within=bubble)], [effect])
                self.assertEqual([h["text"] for h in page.select("h3", within=article)], [heading])
                self.assertFalse(page.select("h3", within=bubble), "The prompt title sits outside the glass.")
                self.assertEqual(len(page.select("form", within=bubble, method="post")), 1,
                                 "The confirmation keeps its own action row with its consequence.")

    def test_vote_line_names_each_state_in_plain_words(self):
        stamp = "22 November 2026, 13:00 UTC"
        rows = [
            ("PREPARATION", "Preparation · Deadline " + stamp),
            ("PREPARATION_DUE", "Preparation deadline passed · " + stamp),
            ("CLARITY", "Clarity · Confirm by " + stamp),
            ("CLARITY_GATE", "Clarity confirmations required"),
            ("VOTING", "Voting open · Closes " + stamp),
            ("RESPONSE_DUE", "Clarity response period ended"),
            ("VOTES_CAST", "Votes cast"),
            ("RESULT", "Decided · Carried · Voting closed " + stamp),
            ("CLOSED", "Suggestion closed · " + stamp),
            ("WITHDRAWN", "Suggestion withdrawn · " + stamp),
            ("NO_BALLOT", "No vote opened · " + stamp),
            ("AWAITING_SETTLEMENT", "Voting closed · Awaiting result"),
            ("UNRECOGNISED", "Vote status unavailable"),
        ]
        for phase, expected in rows:
            with self.subTest(phase=phase):
                # A recorded result of a held vote has a ballot and a closing time (capture Z, Q2).
                held = {"ballot_uuid": uuid4(), "closed_at": self.when} if phase == "RESULT" else {}
                markup = self.vote_markup(phase=phase, deadline=self.when, effective_at=self.when,
                                          outcome="QUALIFIED" if phase == "RESULT" else None, **held)
                self.assertEqual(markup.select("p")[0]["text"], expected)
                notices = markup.select("div", classes=("notice", "vote-feedback"))
                self.assertEqual([n["text"].strip() for n in notices],
                                 ["The wording can still change until voting opens."] if phase == "PREPARATION" else [],
                                 "The plain stage line alone never needs a notice.")

    def test_vote_line_uses_read_cut_and_terminal_act(self):
        common = dict(deadline=self.when, response_deadline=self.later)
        for phase, cut, line in [
            ("PREPARATION", self.when, "Preparation deadline passed · 22 November 2026, 13:00 UTC"),
            ("VOTING", self.when, "Voting closed · Closed 22 November 2026, 13:00 UTC"),
            ("CLARITY", self.when, "Clarity period ended · Respond by 23 November 2026, 13:00 UTC"),
            ("CLARITY", self.later, "Clarity response period ended"),
        ]:
            with self.subTest(phase=phase, cut=cut):
                self.assertEqual(self.vote_markup(phase=phase, read_at=cut, **common).select("p")[0]["text"], line)
        settled = self.vote_markup(phase="RESULT", outcome="QUALIFIED", effective_at=self.when, deadline=self.later,
                                   ballot_uuid=uuid4(), closed_at=self.when)
        self.assertEqual(settled.select("p")[0]["text"], "Decided · Carried · Voting closed 22 November 2026, 13:00 UTC")
        refused = self.vote_markup(phase="RESULT", head_operation="NO_BALLOT", outcome="NO_DECISION_PROVISION_ENDED")
        self.assertEqual(refused.select("p")[0]["text"], "No vote opened · A cited constitutional provision was no longer in force")

    def test_decisions_and_no_decision_results(self):
        rows = [
            ("RESULT", "QUALIFIED", "Decided", "Carried"),
            ("RESULT", "NOT_QUALIFIED_VOTE", "Decided", "Not carried"),
            ("RESULT", "NOT_QUALIFIED_TIE", "Decided", "Not carried. The vote was tied."),
            ("RESULT", "NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION", "Vote ended", "Insufficient participation. No decision was reached."),
            ("RESULT", "NO_DECISION_FEWER_THAN_THREE", "Vote ended", "Fewer than three eligible members remained. No decision was reached."),
            ("RESULT", "NO_DECISION_SLATE_CHANGED", "Vote ended", "A candidate withdrew. A fresh election is required."),
            ("CLOSED", "NO_DECISION_BOARD_ACTIVATED", "Vote ended", "The Board became active. No decision was reached."),
        ]
        for phase, kind, status, outcome in rows:
            with self.subTest(kind=kind):
                if kind == "NO_DECISION_BOARD_ACTIVATED":
                    # No ballot was held, so the line keeps its one time.
                    page = self.vote_markup(phase=phase, outcome=kind, effective_at=self.when)
                    expected = f"{status} · 22 November 2026, 13:00 UTC · {outcome}"
                else:
                    # A held vote names when voting closed (capture Z, Q2; C1-403).
                    page = self.vote_markup(phase=phase, outcome=kind, effective_at=self.when,
                                            ballot_uuid=uuid4(), closed_at=self.when)
                    expected = f"{status} · {outcome} · Voting closed 22 November 2026, 13:00 UTC"
                if kind == "NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION":
                    # Captures AH (Q19) and AQ1 (the posts box, part PB-6b): the red card says the outcome first,
                    # and the status line no longer repeats it.
                    danger, = page.select("div", classes=("notice", "refusal"), id="vote-danger")
                    self.assertEqual([p["text"] for p in page.select("p", within=danger)], [outcome],
                                     "Insufficient participation is said on the red card, above the vote card.")
                    self.assertEqual(page.select("p", classes=("meta",))[0]["text"],
                                     f"{status} · Voting closed 22 November 2026, 13:00 UTC")
                    continue
                self.assertEqual(page.select("div", id="vote-danger"), [], "No other result carries the red card.")
                self.assertEqual(page.select("p")[0]["text"], expected)
        self.assertEqual(self.vote_markup(phase="PREPARATION_DUE").select("p")[0]["text"], "Preparation deadline passed")

    def test_topic_opening_and_reply_version_labels(self):
        org, circle, thread = uuid4(), uuid4(), uuid4()
        request = RequestFactory().get(reverse("discussion_topic", args=(org, circle, thread)))
        request.user = AnonymousUser()
        request.resolver_match = resolve(request.path)
        for opening, reply in [(1, 2), (2, 1)]:
            with self.subTest(opening=opening):
                context = {"board_org": org, "board_circle": circle, "topic": {"thread_uuid": thread},
                           "organisation": {"organism_uuid": org}, "circle": {"circle_uuid": circle},
                           "posts": [{"post_number": 1, "markdown": "Opening words", "version_number": opening},
                                     {"post_number": 2, "markdown": "Reply words", "version_number": reply}]}
                page = Markup(render_to_string("core/discussion_topic.html", context, request=request))
                for card, version in zip(page.select("article", classes=("card",)), (opening, reply), strict=True):
                    labels = [p["text"] for p in page.select("p", within=card, classes=("meta",))]
                    self.assertEqual(labels, [] if version == 1 else ["Version 2"])

    def test_wording_heading_version_and_edit_hint(self):
        for phase, ballot, version, title, editable in (
            ("PREPARATION", None, 1, "Wording to be decided", True),
            ("CLARITY", None, 2, "Wording to be decided · Version 2", True),
            ("VOTING", uuid4(), 2, "Wording being voted on · Version 2", False),
            ("RESULT", uuid4(), 2, "Wording decided · Version 2", False),
            ("WITHDRAWN", None, 1, "Wording at closure", False),
        ):
            with self.subTest(phase=phase):
                markup = self.vote_markup(phase=phase, ballot_uuid=ballot, version_number=version,
                                          read_at=self.when, deadline=self.later,
                                          outcome="QUALIFIED" if phase == "RESULT" else None)
                self.assertEqual(markup.select("h3")[0]["text"], title)
                self.assertEqual([p["text"] for p in markup.select("p", classes=("vote-edit-note",))],
                                 ["The wording can still change until voting opens."] if editable else [])
                if editable:
                    wording, = markup.select("div", classes=("motion-wording",))
                    hint, = markup.select("p", classes=("vote-edit-note",))
                    self.assertLess(markup.nodes.index(hint), markup.nodes.index(wording))

    def test_edit_note_tracks_clarity_deadline(self):
        for phase, cut, expected in (
            ("PREPARATION", self.later, True),
            ("CLARITY", self.when, True),
            ("CLARITY", self.later, False),
            ("CLARITY", self.later + (self.later - self.when), False),
        ):
            with self.subTest(phase=phase, cut=cut):
                page = self.vote_markup(phase=phase, read_at=cut, deadline=self.later)
                self.assertEqual(page.select("h3")[0]["text"], "Wording to be decided")
                notes = [node["text"] for node in page.select("p", classes=("vote-edit-note",))]
                self.assertEqual(notes, ["The wording can still change until voting opens."] if expected else [],
                                 "The edit note must follow the edit window at the read time.")

    def test_ballot_heading_tracks_status(self):
        for phase, outcome, heading in (
            ("VOTING", None, "Wording voted on"),
            ("RESULT", "NOT_QUALIFIED_INSUFFICIENT_PARTICIPATION", "Wording voted on"),
            ("CLOSED", "NO_DECISION_BOARD_ACTIVATED", "Wording voted on"),
            ("RESULT", "QUALIFIED", "Wording decided"),
            ("RESULT", "NOT_QUALIFIED_VOTE", "Wording decided"),
            ("RESULT", "NOT_QUALIFIED_TIE", "Wording decided"),
        ):
            with self.subTest(phase=phase, outcome=outcome):
                page = self.vote_markup(phase=phase, outcome=outcome, ballot_uuid=uuid4(),
                                        deadline=self.when, read_at=self.when)
                self.assertEqual(page.select("h3")[0]["text"], heading,
                                 "Only a substantive ballot result can label the wording decided.")

    def test_unlabelled_terminal_reason_checked_once(self):
        from core import discussion_vote

        with mock.patch.object(discussion_vote.cq_reads, "NO_BALLOT_LABELS", wraps={}) as labels:
            labels.get.side_effect = {}.get
            discussion_vote.vote_line({"phase": "NO_BALLOT", "outcome": "UNKNOWN"})
            self.assertEqual(labels.get.call_count, 1, "Read an unknown terminal reason only once.")

    def test_database_failure_withholds_panel_and_command(self):
        from django.db import IntegrityError
        from core import discussion_vote

        for error in (DatabaseError(), IntegrityError()):
            with self.subTest(error=type(error).__name__), mock.patch.object(
                discussion_vote.cq, "board_topic_vote", side_effect=error
            ), mock.patch.object(discussion_forms, "issue_form") as issue:
                self.assertIsNone(discussion_vote.panel(self.request, uuid4(), uuid4(), uuid4()))
                issue.assert_not_called()


class PreparationPromptTests(SimpleTestCase):
    def render_prompt(self, destination, days=None):
        from core import discussion_vote

        org, circle, thread = uuid4(), uuid4(), uuid4()
        request = RequestFactory().get("/", {"prepare": "1"})
        request.user = AnonymousUser()
        request.session = SimpleNamespace(session_key="fixture")
        facts = {"destination": destination, "days": days, "version": str(uuid4())}
        row = {"operation": "PREPARED", "phase": "PREPARATION", "confirmation": facts}
        with mock.patch.object(discussion_vote.cq, "board_topic_vote", return_value=row) as read:
            vote = discussion_vote.panel(request, org, circle, thread)
        self.assertTrue(read.call_args.kwargs["preview_prepared"])
        page = render_to_string("core/components/board_vote.html", {
            "vote": vote, "board_org": org, "board_circle": circle, "topic": {"thread_uuid": thread},
        }, request=request)
        markup = Markup(page)
        self.assertEqual(len(markup.select("form")), 1, "Only the Prepare confirmation form may be offered.")
        self.assertEqual(len(markup.select("button")), 1, "Only the selected Proceed submit may be offered.")
        self.assertEqual([a["text"] for a in markup.select("a")], ["Cancel"])
        return markup

    def test_clarity_and_direct_vote_explain_the_next_step(self):
        for destination, days, label, paragraphs in (
            ("CLARITY", 3, "Proceed to Clarity", [
                "Board members will be asked to confirm they are clear on this wording. They have 3 days.",
                "The author can still edit the wording during Clarity. Each edit asks them to confirm again.",
                "Voting opens when everyone is clear, or when Clarity ends.",
            ]),
            ("VOTING", None, "Proceed to vote", [
                "Voting opens now, on this wording.", "After this, the wording can no longer be edited.",
            ]),
        ):
            with self.subTest(destination=destination):
                page = self.render_prompt(destination, days)
                prompt, = page.select("article", id="prepare")
                bubble, = page.select("div", within=prompt, classes=("card", "prompt", "notice"))
                self.assertFalse(page.select("h3", within=bubble), "The prompt heading belongs outside the glass.")
                self.assertEqual([p["text"] for p in page.select("p", within=bubble)], paragraphs)
                self.assertEqual(page.select("h3", within=prompt)[0]["text"], "Mark this Suggestion as prepared?")
                form, = page.select("form", within=bubble, method="post", classes=("discussion-actions",))
                self.assertEqual([n["text"] for n in page.select("button", within=form, type="submit", classes=("primary",))], [label])
                cancels = page.select("a", within=form, classes=("button", "secondary"))
                self.assertEqual([link["text"] for link in cancels], ["Cancel"])
                self.assertTrue(cancels[0]["attrs"]["href"].endswith("/#vote"))

    def test_no_ballot_prompt_names_the_actual_reason(self):
        reasons = {
            "UNLISTED_REASON": "The reason is unavailable.",
            "NO_DECISION_PROVISION_ENDED": "A cited constitutional provision was no longer in force.",
            "NO_DECISION_FEWER_THAN_THREE": "Fewer than three eligible voters remained.",
            "NO_DECISION_MOTION_UNAVAILABLE": "The motion was unavailable to its full electorate.",
        }
        for kind, why in reasons.items():
            with self.subTest(kind=kind):
                page = self.render_prompt(kind)
                prompt, = page.select("article", id="prepare")
                bubble, = page.select("div", within=prompt, classes=("card", "prompt", "notice"))
                self.assertFalse(page.select("h3", within=bubble), "The prompt heading belongs outside the glass.")
                self.assertEqual([p["text"] for p in page.select("p", within=bubble)],
                                 ["Marking this Suggestion as prepared will record that no vote opened.", why],
                                 "Each no-vote prompt must show its reason or the plain fallback.")
                self.assertEqual(page.select("button", within=prompt)[0]["text"], "Proceed without a vote")


class QuotedPostPresentationTests(SimpleTestCase):
    def quote_page(self, text, *, version=2, hidden=False):
        row = {"markdown": "My own reply.", "quoted_markdown": text,
               "quoted_version_number": version, "quote_hidden": hidden}
        return Markup(render_to_string("core/components/discussion_post.html", {"post": row}))

    def test_quote_picker_labels_and_exact_version_values(self):
        version_ids = [uuid4() for _ in range(3)]
        for opening_version, reply_version in [(1, 2), (3, 1)]:
            with self.subTest(opening_version=opening_version):
                posts = [
                    {"post_number": 1, "version_number": opening_version, "version_uuid": version_ids[0], "markdown": "Opening"},
                    {"post_number": 2, "version_number": reply_version, "version_uuid": version_ids[1], "markdown": "Reply"},
                    {"post_number": 3, "version_number": 2, "version_uuid": version_ids[2], "markdown": None},
                ]
                page = Markup(render_to_string("core/components/reply_form.html", {
                    "board_org": uuid4(), "board_circle": uuid4(), "topic": {"thread_uuid": uuid4()},
                    "posts": posts, "entered": {"quote": str(version_ids[1])},
                }, request=RequestFactory().get("/")))
                label, = [label for label in page.select("label") if page.select("select", within=label, name="quote")]
                self.assertEqual(label["text"].strip().splitlines()[0], "Quote an earlier post (optional)")
                choices = [(n["attrs"]["value"], n["text"]) for n in page.select("option", within=label)]
                self.assertEqual(choices, [("", "No quotation"),
                                           (str(version_ids[0]), "Opening post" if opening_version == 1 else "Opening post · Version 3"),
                                           (str(version_ids[1]), "Post 2" if reply_version == 1 else "Post 2 · Version 2")])
                self.assertEqual([n["attrs"]["value"] for n in page.select("option") if "selected" in n["attrs"]],
                                 [str(version_ids[1])])

    def test_quote_precedes_reply_and_identifies_its_fixed_version(self):
        for version, label in [(1, "Quoted text"), (2, "Quoted text (Version 2)")]:
            with self.subTest(version=version):
                page = self.quote_page("An earlier sentence.", version=version)
                quote, = page.select("blockquote", classes=("post-quote",))
                reply, = page.select("div", classes=("post-text",))
                self.assertLess(page.nodes.index(quote), page.nodes.index(reply))
                self.assertEqual(page.select("p", within=quote, classes=("meta",))[0]["text"], label)
                self.assertEqual(reply["text"], "My own reply.")
        hidden = self.quote_page(None, hidden=True)
        self.assertFalse(hidden.select("blockquote"))
        self.assertIn("Quoted text unavailable.", [p["text"] for p in hidden.select("p")])
        permitted = self.quote_page("Visible to moderator", hidden=True)
        self.assertEqual(permitted.select("p", classes=("meta",))[0]["text"], "Quoted text (Version 2) · Hidden")

    def test_long_quote_folds_at_word_and_escapes_both_copies(self):
        prefix = "<b>" + ("é " * 87).rstrip()
        for text, preview in [(prefix + " elephant", prefix + "…"),
                              ("a" * 179 + " b", "a" * 179 + "…"),
                              ("b" * 180 + " c", "b" * 180 + "…"),
                              ("é" * 181, "é" * 180 + "…")]:
            with self.subTest(length=len(text)):
                page = self.quote_page(text)
                quote, = page.select("blockquote")
                details, = page.select("details", within=quote)
                self.assertNotIn("open", details["attrs"])
                self.assertEqual(page.select("div", within=quote, classes=("quote-preview",))[0]["text"], preview)
                summary, = page.select("summary", within=details)
                self.assertIs(summary["parents"][-1], details)
                self.assertEqual([n["text"] for n in page.select("span", within=summary)],
                                 ["Show the whole quote", "Show less"])
                self.assertEqual([p["text"] for p in page.select("p", within=details)], [text])
                self.assertFalse(page.select("b"))

    def test_short_quote_including_180_is_shown_whole(self):
        for size in (1, 179, 180):
            with self.subTest(size=size):
                text = "é" * size
                page = self.quote_page(text)
                quote, = page.select("blockquote")
                self.assertFalse(page.select("details", within=quote))
                self.assertEqual([p["text"] for p in page.select("p", within=quote)], ["Quoted text (Version 2)", text])

    def test_quote_preview_drops_punctuation_only_at_cut(self):
        for end in (",", ".", ":", ";", ",;"):
            with self.subTest(end=end):
                short = "The community door" + end
                text = short + " " + "w" * 180
                page = self.quote_page(text)
                self.assertEqual(page.select("div", classes=("quote-preview",))[0]["text"], "The community door…",
                                 "Cut punctuation must not sit in front of the ellipsis.")
                self.assertEqual(page.select("details")[0]["text"].strip().endswith(text), True,
                                 "The expanded quote must retain its original punctuation.")
                whole = self.quote_page(short)
                self.assertEqual(whole.select("p", within=whole.select("blockquote")[0])[-1]["text"], short,
                                 "A short quote must keep its final punctuation.")

    def test_full_quote_has_one_container(self):
        paragraphs = ["The board met. " * 12 + "The door closed.", "Second paragraph."]
        page = self.quote_page("\n\n".join(paragraphs))
        details, = page.select("details")
        children = [n for n in page.nodes if n["parents"] and n["parents"][-1] is details]
        self.assertEqual([n["tag"] for n in children], ["summary", "div"],
                         "A folded quote needs one container for all of its paragraphs.")
        self.assertEqual([p["text"] for p in page.select("p", within=children[1])], paragraphs,
                         "Both paragraphs must come before the closing control on screen.")


class TopicClarityFocusTests(SimpleTestCase):
    """Render the full Topic GET with supplied reader facts and real form links."""

    def setUp(self):
        self.ids = (uuid4(), uuid4(), uuid4())
        self.topic_url = reverse("discussion_topic", args=self.ids)
        self.reply_url = reverse("discussion_reply", args=self.ids)
        self.version = uuid4()
        self.window = uuid4()

    def open_topic(self, address, *, actions=(), question=False, can_reply=True):
        from core import discussion_vote

        org, circle, thread = self.ids
        context = {
            "board_org": org, "board_circle": circle,
            "organisation": {"organism_uuid": org}, "circle": {"circle_uuid": circle},
            "category": {"can_reply": can_reply, "retired": False},
            "topic": {"thread_uuid": thread, "title": "Ordinary payments", "topic_type": "SUGGESTION", "closed": False},
            "posts": [{"post_number": 1, "markdown": "Approve ordinary payments."},
                      {"post_number": 2, "markdown": "Please record the review date."}],
        }
        facts = {"phase": "CLARITY", "clarity_actions": actions, "clarity_version": self.version,
                 "clarity_window": self.window, "question_version": self.version if question else None}
        request = RequestFactory().get(address)
        request.resolver_match = resolve(self.topic_url)
        request.user = AnonymousUser()
        request.session = SimpleNamespace(session_key="fixture")
        with mock.patch.object(discussion_pages, "_read_page", return_value=("core/discussion_topic.html", context)), \
             mock.patch.object(discussion_vote.cq, "board_topic_vote", return_value=facts):
            response = discussion_pages._page(request, *self.ids)
        return Markup(response.content.decode())

    def test_reply_is_default_and_cancel_restores_it_for_each_clarity_form(self):
        for state, opener, operation in (
            ("question", "Ask a question", None),
            ("conflict", "Declare a conflict", "CONFLICT"),
            ("withdraw", "Withdraw Suggestion", "WITHDRAW"),
            ("close", "Close without a vote", "CLOSE"),
        ):
            with self.subTest(state=state):
                facts = {"actions": (operation,) if operation else (), "question": state == "question"}
                default = self.open_topic(self.topic_url, **facts)
                self.assertEqual(len(default.select("form", method="post", action=self.reply_url)), 1)
                link, = [a for a in default.select("a") if a["text"] == opener]
                selected = self.open_topic(link["attrs"]["href"], **facts)
                action = reverse("discussion_clarity_" + state, args=self.ids)
                self.assertEqual(len(selected.select("form", method="post", action=action)), 1)
                self.assertFalse(selected.select("form", action=self.reply_url), "The open state owns the composer.")
                self.assertFalse(selected.select("h2", id="reply-heading"))
                history, = selected.select("section", **{"aria-labelledby": "replies-heading"})
                self.assertIn("Please record the review date.", history["text"])
                cancel, = [a for a in selected.select("a") if a["text"] == "Cancel"]
                self.assertEqual(cancel["attrs"]["href"], self.topic_url + "#vote")
                restored = self.open_topic(cancel["attrs"]["href"], **facts)
                reply, = restored.select("form", action=self.reply_url)
                self.assertEqual([b["text"] for b in restored.select("button", within=reply)], ["Post reply"])
                self.assertFalse(restored.select("form", action=action))

    def test_unavailable_state_keeps_the_ordinary_reply_permission(self):
        for can_reply in (True, False):
            with self.subTest(can_reply=can_reply):
                page = self.open_topic(self.topic_url + "?clarity=close", can_reply=can_reply)
                self.assertEqual(len(page.select("form", action=self.reply_url)), int(can_reply))
                self.assertFalse(page.select("article", id="clarity-confirmation"))

    def test_chairs_admitted_buttons_follow_the_ruled_order_and_variants(self):
        for actions, expected in (
            (("CONFLICT", "PROCEED", "EXTEND", "CLOSE"),
             ["Proceed to vote", "Extend Clarity", "Declare a conflict", "Close without a vote"]),
            (("CLOSE", "EXTEND"), ["Extend Clarity", "Close without a vote"]),
        ):
            with self.subTest(actions=actions):
                page = self.open_topic(self.topic_url, actions=actions)
                panel, = page.select("section", id="vote")
                buttons = [n for n in page.nodes if any(p is panel for p in n["parents"])
                           and n["tag"] in ("a", "button")]
                self.assertEqual([b["text"] for b in buttons], expected)
                variants = {"Proceed to vote": "primary", "Extend Clarity": "secondary",
                            "Declare a conflict": "danger", "Close without a vote": "danger"}
                for button in buttons:
                    with self.subTest(label=button["text"]):
                        classes = set(button["attrs"]["class"].split())
                        self.assertEqual(classes & {"primary", "secondary", "success", "danger"},
                                         {variants[button["text"]]})
