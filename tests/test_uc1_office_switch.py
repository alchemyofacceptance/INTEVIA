"""The organisation switch at the real form, session, command and middleware boundaries."""
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from django.db import OperationalError
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import include, path, resolve, reverse

from core import office_switch, organisation_pages, ui_views
from core.cache import appearance_privacy
from tests.test_f0_ui import Markup
from tests.test_uc1_office_page import assert_private, care_state_text, signed_in


def plain_page(request):
    return HttpResponse("Public control")


# The same callbacks, reached under names the application has never seen.
urlpatterns = [
    path("probe/you/", ui_views.you, name="probe-account"),
    path("probe/<uuid:org>/<uuid:office>/", ui_views.office_page, name="probe-read"),
    path("probe/<uuid:org>/settings/", organisation_pages.organisation_settings, name="probe-settings"),
    path("probe/<uuid:org>/act/", office_switch.change_assistance, name="probe-change"),
    path("probe/public/", plain_page, name="probe-public"),
    path("", include("intevia.urls")),
]


@override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=["testserver"])
class PrivacyMarkerTests(SimpleTestCase):
    def test_unsafe_settings_alias_gets_the_read_method_refusal(self):
        client = Client(enforce_csrf_checks=True)
        address = reverse("probe-settings", args=(uuid4(),))
        user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Mira")
        identity = SimpleNamespace(access_state="active", display_name="Mira")
        with patch("django.contrib.auth.middleware.get_user", return_value=user), \
             patch("core.session.IdentityAccessMiddleware._valid_identity", return_value=identity):
            response = client.post(address)
        self.assertEqual((response.status_code, response.get("Allow")), (405, "GET, HEAD"),
                         "The same settings callback must give its method refusal under an alias.")
        self.assertContains(response, "This request could not be used.", status_code=405)
        assert_private(self, response)

    def test_privacy_follows_the_resolved_view_on_early_refusal(self):
        client = Client(enforce_csrf_checks=True)
        args = [uuid4(), uuid4()]
        for name, route_args in (("probe-account", []), ("probe-read", args), ("probe-change", args[:1])):
            with self.subTest(view=name):
                url = reverse(name, args=route_args)
                normal = client.get(url)
                self.assertEqual(normal.status_code, 302, "The wrapped personal view must direct the visitor to sign in.")
                assert_private(self, normal)
                early = client.post(url, {})
                self.assertEqual(early.status_code, 403, "Missing CSRF must reach the rejection before the personal view.")
                assert_private(self, early)
                # An inner middleware can stop the request before Django supplies resolver_match.
                pending = RequestFactory().get(url)
                self.assertIsNone(pending.resolver_match, "The early redirect control must precede URL resolution.")
                short = appearance_privacy(lambda request: HttpResponse(status=302))(pending)
                assert_private(self, short)
        routed = resolve(reverse("organisation_assistance_change", args=args[:1]))
        self.assertIs(routed.func, office_switch.change_assistance,
                      "The new product route must retain the callback used by the form-security hook.")
        public = client.get(reverse("probe-public"))
        self.assertEqual(public.status_code, 200, "The unmarked neighbouring view must be reachable.")
        self.assertNotIn("private", public.get("Cache-Control", ""), "An unmarked public view must keep its cache policy.")


@override_settings(ALLOWED_HOSTS=["testserver"])
class SwitchRefusalTests(SimpleTestCase):
    def test_refusal_titles_precede_the_red_card(self):
        cases = (
            ("office_switch_refusal", {"title": "Your choice was refused."}, "Your choice was refused."),
            ("organisation_unavailable", {}, "Page unavailable"),
            ("office_unavailable", {}, "Office record unavailable"),
        )
        request = RequestFactory().get("/")
        request.user = SimpleNamespace(is_authenticated=False)
        for template, context, title in cases:
            with self.subTest(template=template):
                page = Markup(render_to_string(f"core/{template}.html", context, request=request))
                main, = page.select("main")
                headings = page.select("h1", within=main)
                self.assertEqual([(h["text"], h["attrs"].get("class")) for h in headings],
                                 [(title, "record-heading page-title")], "Use one shared teal record heading.")
                card, = [n for n in page.nodes if n["attrs"].get("role") == "alert" and main in n["parents"]]
                self.assertIn("refusal", card["attrs"]["class"].split())
                self.assertIs(headings[0]["parents"][-1], main, "The title must stand outside the refusal card.")
                self.assertLess(page.nodes.index(headings[0]), page.nodes.index(card), "The title introduces the alert.")

    def test_switch_refusals_explain_the_next_action(self):
        from src.intevia.services import care_assistance as care

        org = uuid4()
        url = reverse("organisation_assistance_change", args=(org,))
        request = RequestFactory().post(url, {"enabled": "off", "predecessor": "", "request_uuid": str(uuid4())})
        request.resolver_match = resolve(url)
        request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "A Member")
        cases = [
            ("CARE_SETTING_PREDECESSOR", 409, "Reload the current choice and decide again."),
            ("CARE_SETTING_REPLAY", 409, "Reopen the form before trying again."),
            ("CARE_SETTING_INPUT", 400, "Reopen organisation settings and choose On or Off."),
            ("CARE_SETTING_AUTHORITY", 403, "Return to organisation settings to see the current choice."),
            (None, 503, "Reopen organisation settings and check the current choice before submitting again."),
        ]
        with patch.object(care, "read_organisation_assistance", return_value={"can_change": True}):
            for code, status, action in cases:
                with self.subTest(code=code):
                    error = care.SettingRefused(code) if code else OperationalError("fixture interruption")
                    with patch.object(care, "set_assistance", side_effect=error):
                        response = request.resolver_match.func(request, org)
                    page = Markup(response.content.decode())
                    self.assertEqual(response.status_code, status, "The refusal must report the appropriate HTTP outcome.")
                    self.assertIn(action, [n["text"].strip() for n in page.select("a")],
                                  "The refusal must give the practical next action for this outcome.")
                    alert = page.select("aside", role="alert")
                    self.assertEqual(len(alert), 1, "The refusal body must have one alert.")
                    parts = [n for n in page.nodes if n["parents"] and n["parents"][-1] is alert[0]]
                    self.assertEqual([p["tag"] for p in parts if p["text"].strip()], ["p", "p", "p", "p"],
                                     "The alert must give the cause, effect, next action and contact in order.")
                    selected = page.select("a", classes=("nav-item",), **{"aria-current": "page"})
                    self.assertEqual([a["text"].strip() for a in selected], ["INTEVIA"],
                                     "The command refusal must keep INTEVIA selected.")
                    self.assertEqual(len(page.select("nav", **{"aria-label": "Domains"})), 1,
                                     "Keep the organisation domain tabs on the command’s refusal page.")
                    links = page.select("a", within=alert[0])
                    self.assertEqual([a["attrs"]["href"] for a in links], [reverse("organisation_settings", args=(org,))],
                                     "The refusal’s next action must return to this organisation’s settings.")
                    if code in {"CARE_SETTING_INPUT", "CARE_SETTING_REPLAY", "CARE_SETTING_PREDECESSOR"}:
                        self.assertContains(response, "Your submitted choice: Off", status_code=status,
                                            msg_prefix="A still-authorised reader may see the intent from their refused form.")
                    if not code:
                        self.assertContains(response, "The current choice must be checked.", status_code=503,
                                            msg_prefix="An uncertain outcome must not claim that the setting was unchanged.")
                    assert_private(self, response)

            # Membership can disappear between the admitted read and the command.
            with patch.object(care, "set_assistance", side_effect=care.SettingRefused("CARE_SETTING_UNAVAILABLE")):
                unseen = request.resolver_match.func(request, org)
            self.assertEqual((unseen.status_code, unseen.content),
                             (404, render_to_string("core/organisation_unavailable.html", request=request).encode()),
                             "Loss of membership during the command must discard any retained choice.")
        with patch.object(care, "read_organisation_assistance", side_effect=OperationalError("fixture interruption")), \
             patch.object(care, "set_assistance") as command:
            failed = request.resolver_match.func(request, org)
        self.assertContains(failed, "The setting could not be read.", status_code=503,
                            msg_prefix="A pre-command read failure must use the failed-read register.")
        assert_private(self, failed)
        page = Markup(failed.content.decode())
        alert, = page.select("aside", role="alert")
        self.assertEqual([a["attrs"]["href"] for a in page.select("a", within=alert, classes=("button", "secondary"))],
                         [reverse("organisation_settings", args=(org,))], "Retry must open the read page after a failed POST.")
        self.assertFalse(command.called, "A failed admission read must never reach the writer.")


def bound_client(person):
    client = Client(enforce_csrf_checks=True)
    client.cookies.update(signed_in(person).cookies)
    return client


@override_settings(ALLOWED_HOSTS=["testserver"])
class OfficeSwitchTests(TransactionTestCase):
    def setUp(self):
        from tests.test_law_acts import ordinary_birth

        self.owner, self.coordinator = ordinary_birth(board_seats=3)
        self.client = bound_client(self.coordinator)
        self.read = reverse("organisation_settings", args=(self.owner.organism_id,))
        self.change = reverse("organisation_assistance_change", args=(self.owner.organism_id,))

    def snapshot(self):
        from tests.office_record_support import footprint
        return footprint(self.owner)

    def forms(self, client):
        response = client.get(self.read, headers={"Purpose": "prefetch"})
        self.assertEqual(response.status_code, 200, "The member must be able to open the organisation settings page.")
        assert_private(self, response)
        page = Markup(response.content.decode())
        forms = {}
        for form in page.select("form", action=self.change, method="post"):
            fields = {n["attrs"]["name"]: n["attrs"]["value"] for n in page.select("input", within=form)}
            kind = "secondary" if fields["enabled"] == "off" else "primary"
            buttons = page.select("button", within=form, classes=(kind,), type="submit")
            self.assertEqual(len(buttons), 1, "Each switch form must use the button variant for its submitted choice.")
            forms[fields["enabled"]] = fields
        return response, forms

    def assert_redirect(self, response):
        self.assertEqual((response.status_code, response.get("Location")), (302, self.read),
                         "A recorded choice must redirect to this organisation’s settings.")
        assert_private(self, response)

    @override_settings(ROOT_URLCONF=__name__)
    def test_only_the_coordinator_can_post_the_switch(self):
        from core.models import Circle
        from src.intevia.services import cq
        from tests.cq_authority_support import seat_trustee

        circle = Circle.objects.get(parent_organism=self.owner, kind="GOVERNANCE")
        trustee = seat_trustee(self.owner, self.coordinator, circle, 1)
        seat_trustee(self.owner, self.coordinator, circle, 2)
        scope = dict(credential=self.coordinator.credential, organism_uuid=self.owner.organism_id)
        current = cq.settings(**scope)
        cq.change_settings(**scope, predecessor=current["version"], changes={"enabled": True}, request_uuid=uuid4())
        self.assertTrue(cq.settings(**scope)["settings"]["enabled"], "Voting must be on before either person submits.")
        operator_page, forms = self.forms(self.client)
        self.assertContains(operator_page, "On by default", msg_prefix="An unchosen On setting must identify the default.")
        ordinary = bound_client(trustee)
        reader_page, reader_forms = self.forms(ordinary)
        self.assertFalse(reader_forms, "A trustee without the organisation office must have no switch form.")
        self.assertContains(reader_page, "On by default", msg_prefix="The trustee still sees the current setting.")
        you = ordinary.get(reverse("probe-account"))
        self.assertEqual(you.status_code, 200, "The bound trustee must receive the You page through its alternate URL.")
        assert_private(self, you)
        own_token = Markup(you.content.decode()).select("input", name="csrfmiddlewaretoken")[0]["attrs"]["value"]
        before = self.snapshot()
        # A transaction regression must fail at the HTTP status assertion, including a server error.
        ordinary.raise_request_exception = False
        denied = ordinary.post(self.change, forms["off"] | {"csrfmiddlewaretoken": own_token})
        self.assertEqual(self.snapshot(), before, "Submitting as the ordinary trustee must leave the setting act absent.")
        self.assertEqual(denied.status_code, 403, "The organisation command must refuse a trustee without the Organisation Coordinator office.")
        assert_private(self, denied)
        page = Markup(denied.content.decode())
        alert = page.select("aside", role="alert")
        self.assertEqual(len(alert), 1, "A refused setting choice must have one visible alert.")
        self.assertEqual([n["text"] for n in page.select("h1", classes=("record-heading", "page-title"))], ["Your choice was refused."],
                         "The authority refusal's title must stand above the card as the teal page title.")
        self.assertEqual([n["text"].strip() for n in page.nodes if n["parents"] and n["parents"][-1] is alert[0]], [
            "Only the Organisation Coordinator can change this setting.",
            "This request left the setting unchanged.", "Return to organisation settings to see the current choice.",
            "Ask the Organisation Coordinator if a change is needed.",
        ], "The authority refusal must explain what happened, why, its effect, the next action and who can help.")

        alias_args = (self.owner.organism_id,)
        alias_read = self.client.get(reverse("probe-settings", args=alias_args), headers={"Purpose": "prefetch"})
        self.assertContains(alias_read, "On by default", msg_prefix="The bound Coordinator must receive the alternate settings URL.")
        assert_private(self, alias_read)
        self.assert_redirect(self.client.post(reverse("probe-change", args=alias_args), forms["off"]))
        chosen, forms_on = self.forms(self.client)
        self.assertIn("Off · Organisation choice", care_state_text(chosen.content.decode()), "Switching off must publish a chosen Off setting.")
        self.assertEqual(forms_on["on"]["predecessor"], forms["off"]["request_uuid"],
                         "The On form must identify the Off act the Coordinator saw.")
        self.assert_redirect(self.client.post(self.change, forms_on["on"]))
        restored, _ = self.forms(ordinary)
        self.assertIn("On · Organisation choice", care_state_text(restored.content.decode()), "Switching back on must remain an explicit choice.")
        expected = [UUID(forms["off"]["request_uuid"]), UUID(forms_on["on"]["request_uuid"])]
        self.assertEqual(self.snapshot()[1:], (expected, expected), "Two real submissions must create exactly their two ILC pairs.")

    def test_switch_uses_the_submitted_predecessor(self):
        _, first = self.forms(self.client)
        _, stale = self.forms(self.client)
        off = first["off"]
        self.assert_redirect(self.client.post(self.change, off))
        before = self.snapshot()
        response = self.client.post(self.change, stale["off"])
        self.assertEqual((response.status_code, self.snapshot()), (409, before),
                         "The stale form must preserve the first committed choice.")
        self.assertContains(response, "Reload the current choice and decide again.", status_code=409,
                            msg_prefix="A stale choice must tell the Coordinator to reload and decide again.")
        self.assert_redirect(self.client.post(self.change, off))
        self.assertEqual(self.snapshot(), before, "An exact replay must reuse its first act and ILC entry.")
        conflict = self.client.post(self.change, off | {"enabled": "on"})
        self.assertContains(conflict, "already been used for a different action", status_code=409,
                            msg_prefix="Reusing the reference with another intent must give the replay refusal.")
        self.assertEqual(self.snapshot(), before, "A conflicting replay must leave the head and both histories intact.")

    def test_explicit_default_and_both_already_notices(self):
        _, forms = self.forms(self.client)
        self.assertEqual(set(forms), {"off"}, "Default On must offer only the command to turn assistance off.")
        # Exercise the retained POST contract directly with the rendered CSRF token and predecessor.
        self.assert_redirect(self.client.post(self.change, forms["off"] | {"enabled": "on"}))
        for value, other in (("on", "off"), ("off", "on")):
            with self.subTest(value=value):
                response, actual = self.forms(self.client)
                self.assertIn(value.title() + " · Organisation choice", care_state_text(response.content.decode()),
                              "A recorded choice must be distinguished from the default.")
                before = self.snapshot()
                self.assert_redirect(self.client.post(self.change, actual[other] | {"enabled": value}))
                page, _ = self.forms(self.client)
                notices = Markup(page.content.decode()).select("aside", role="status")
                expected = "Assistance is already " + value + "."
                self.assertEqual([s["text"].strip() for s in notices], [expected],
                                 "An already-current response must show exactly one plain notice for the returned state.")
                self.assertEqual(self.snapshot(), before, "An already-current submission must add no setting or ILC entry.")
                if value == "on":
                    self.assert_redirect(self.client.post(self.change, actual["off"]))

    def test_switch_rejects_unsafe_form_submissions(self):
        _, forms = self.forms(self.client)
        form = forms["off"]
        before = self.snapshot()
        missing = dict(form)
        del missing["csrfmiddlewaretoken"]
        response = self.client.post(self.change, missing)
        self.assertEqual(response.status_code, 403, "A forged switch form must be refused before command dispatch.")
        assert_private(self, response)
        self.assertContains(response, "Its security check did not pass.", status_code=403,
                            msg_prefix="A missing CSRF field must give the form-security refusal.")
        for key, value in (("enabled", "other"), ("enabled", ["off", "on"]), ("predecessor", "invalid"),
                           ("request_uuid", ""), ("request_uuid", ["", str(uuid4())])):
            with self.subTest(key=key, value=value):
                invalid = self.client.post(self.change, form | {key: value})
                self.assertEqual(invalid.status_code, 400, "A malformed or repeated command field must be refused.")
        self.assertEqual(self.client.get(self.change).status_code, 405, "A command URL must refuse a GET.")
        for header in ("Sec-Purpose", "Purpose"):
            refused = self.client.post(self.change, form, headers={header: "Prefetch;prerender"})
            self.assertEqual(refused.status_code, 400, "A speculative command request must not submit a choice.")
        foreign = self.client.post(reverse("organisation_assistance_change", args=(uuid4(),)), form)
        self.assertEqual(foreign.status_code, 404, "The command must admit the addressed organisation before processing the form.")
        self.assertEqual(self.snapshot(), before, "None of the refused submissions may change the head, setting or history.")
        self.assert_redirect(self.client.post(self.change, form))
        self.assertEqual(self.snapshot()[1], [UUID(form["request_uuid"])], "The valid form must still record exactly its own choice.")
