"""The organisation CARE page, its destination, eligibility and private refusals."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import NoReverseMatch, Resolver404, resolve, reverse

from core import organisation_pages as pages
from core.models import OrganismMembership
from src.intevia.services import organism_membership_service, timed_events_handoff as handoff
from tests import test_c1b_departure
from tests.cq_authority_support import authority
from tests.office_record_support import approved_office
from tests.s015_0021_support import database_now, register_test_learner
from tests.test_f0_ui import Markup
from tests.test_law_acts import add_member, ordinary_birth
from tests.test_office_record_services import fixture_membership_state
from tests.test_uc1_office_page import assert_private, signed_in
from tests.test_uc4_organisation_assistance import choose, record_counts


def settings_url(org):
    return reverse("organisation_settings", args=(org,))


def render_settings(setting, owner=None):
    """Render the real settings route with just its two database reads supplied."""
    if owner is None:
        owner = {"organism_uuid": uuid4(), "short_label": "Board trust", "full_name": "Board trust"}
    path = settings_url(owner["organism_uuid"])
    request = RequestFactory().get(path)
    request.resolver_match = resolve(path)
    request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Reader")
    with patch.object(pages.transaction, "atomic", return_value=nullcontext()), \
         patch.object(pages.care_assistance, "read_organisation_assistance", return_value=setting), \
         patch.object(pages.navigation, "member_organisation_name", return_value=owner):
        return request.resolver_match.func(request, **request.resolver_match.kwargs)


@override_settings(ALLOWED_HOSTS=["testserver"])
class SettingsPageTests(TransactionTestCase):
    def test_member_without_office_reads_settings(self):
        owner, founder = ordinary_birth()
        client = signed_in(add_member(owner))
        path = settings_url(owner.organism_id)
        receipt = None
        for enabled, expected in ((None, "On · On by default"), (False, "Off · Organisation choice"),
                                  (True, "On · Organisation choice")):
            if enabled is not None:
                receipt = choose(owner, founder, enabled, predecessor=receipt)["act_uuid"]
            response = client.get(path)
            self.assertEqual(response.status_code, 200, "An ordinary member needs no office to read the setting.")
            assert_private(self, response)
            page = Markup(response.content.decode())
            cards = page.select("section", classes=("notice", "care"))
            self.assertEqual([h["text"] for h in page.select("h1", classes=("record-heading", "page-title"))],
                             ["Organisation settings"], "The page must use the shared Organisation settings title.")
            self.assertEqual([h["text"] for card in cards for h in page.select("h2", within=card)],
                             ["CARE assistance"], "One CARE assistance section must name the setting.")
            self.assertEqual([p["text"].strip() for card in cards for p in page.select("p", within=card)],
                             [expected, "Only the Organisation Coordinator can change this."], "Show the recorded state and distinguish a default from an explicit choice.")
            self.assertFalse(page.select("form") or page.select("button"), "A member without the office must see no command.")

    def test_unavailable_is_indistinguishable(self):
        owner, _ = ordinary_birth()
        foreign, foreign_founder = ordinary_birth()
        reader = register_test_learner(username="uc4b-" + uuid4().hex)
        from tests.test_uc1_office_switch import bound_client
        client = bound_client(reader)
        you = Markup(client.get(reverse("f0-you")).content.decode())
        token = you.select("input", name="csrfmiddlewaretoken")[0]["attrs"]["value"]
        form = {"enabled": "off", "predecessor": "", "request_uuid": str(uuid4()), "csrfmiddlewaretoken": token}

        def unseen(target):
            return (client.get(settings_url(target)),
                    client.post(reverse("organisation_assistance_change", args=(target,)), form))

        # Keep the same signed-in shell for each status and byte comparison.
        missing = unseen(uuid4())
        expected = render_to_string("core/organisation_unavailable.html", request=missing[0].wsgi_request).encode()
        cases = [("unknown", missing), ("non-member", unseen(owner.organism_id))]
        with patch("tests.test_sr_office_guards.seed_identity", return_value=reader.pk):
            add_member(owner)
        self.assertEqual(client.get(settings_url(owner.organism_id)).status_code, 200,
                         "The admitted member must be able to open their own settings before departure.")
        cases.append(("foreign", unseen(foreign.organism_id)))
        membership = OrganismMembership.objects.get(living_organism=owner, identity=reader)
        service = organism_membership_service.OrganismMembershipService(authority=authority(), clock=database_now)
        service.leave_organism(test_c1b_departure.DepartureTests().command(membership))
        cases.append(("departed", unseen(owner.organism_id)))
        with patch("tests.test_sr_office_guards.seed_identity", return_value=reader.pk):
            add_member(foreign)
        fixture_membership_state(foreign, foreign_founder, reader, OrganismMembership.State.PROBATIONARY)
        cases.append(("probation", unseen(foreign.organism_id)))
        for reason, (opened, posted) in cases:
            with self.subTest(reason=reason):
                self.assertEqual((opened.status_code, opened.content), (404, expected),
                                 "Every unseen target must give the same ordinary unavailable page on GET.")
                self.assertEqual((posted.status_code, posted.content), (404, expected),
                                 "An unseen target must give the identical ordinary unavailable page on POST.")
                for response in (opened, posted):
                    assert_private(self, response)
                    self.assertNotIn(b"This office record is unavailable", response.content,
                                     "A service refusal must never disclose office-specific wording.")

    def test_settings_reads_do_not_record(self):
        owner, founder, *_ = approved_office()
        client = signed_in(founder)
        path = settings_url(owner.organism_id)
        before = record_counts(owner)
        # Observe the actual middleware slip as the request finishes, before any wake could run.
        note = handoff.note
        slips = []

        def observe(request, response, slip):
            slips.append((slip.organism_uuid, slip.identity_id, slip.confirmed))
            return note(request, response, slip)

        with patch.object(handoff, "note", side_effect=observe):
            for method, headers in ((client.get, {}), (client.head, {}), (client.get, {"Purpose": "prefetch"})):
                response = method(path, headers=headers)
                self.assertEqual(response.status_code, 200, "Normal and speculative reads must complete.")
                if method == client.head:
                    self.assertEqual(response.content, b"", "HEAD must send no response body.")
                self.assertEqual(record_counts(owner), before,
                                 "Reading must preserve all choice, observation and organism-event counts.")
        self.assertEqual(slips, [(None, None, False)] * 3,
                         "GET, HEAD and prefetch must each leave the actual request slip empty.")

    def test_home_settings_row(self):
        owner, founder = ordinary_birth()
        reader = add_member(owner)
        client = signed_in(reader)
        url = reverse("organisation_home", args=(owner.organism_id,))
        target = settings_url(owner.organism_id)
        with patch.object(pages.care_assistance, "read_organisation_assistance") as read:
            page = Markup(client.get(url).content.decode())
            self.assertEqual([h["text"] for h in page.select("h2")][-2:], ["Laws", "Settings"],
                             "Settings must follow Laws on the organisation home.")
            rows = page.select("ul", classes=("circle-list",))
            links = [a for row in rows for a in page.select("a", within=row, href=target)]
            self.assertEqual([n["text"] for a in links for n in page.select("strong", within=a)],
                             ["Organisation settings"], "One divided-list row must open this organisation’s settings.")
            self.assertEqual([n["text"] for a in links for n in page.select("small", within=a)],
                             ["CARE assistance for this organisation"], "The row must say which setting is available.")
            fixture_membership_state(owner, founder, reader, OrganismMembership.State.PROBATIONARY)
            response = client.get(url)
            self.assertEqual(response.status_code, 200, "Probation must still admit the organisation home.")
            self.assertNotContains(response, target, msg_prefix="Probation must hide the destination itself.")
            self.assertNotContains(response, 'id="settings-heading"', msg_prefix="Probation must hide the whole section.")
            self.assertFalse(read.called, "Listing destinations must not read the setting.")


@override_settings(ALLOWED_HOSTS=["testserver"])
class SettingsPresentationTests(SimpleTestCase):
    def setUp(self):
        self.org = uuid4()
        self.owner = {"organism_uuid": self.org, "full_name": "Alder Beck Community Trust", "short_label": "Alder Beck"}
        self.default = {"enabled": True, "choice_source": "default", "can_change": False}

    def request(self):
        path = settings_url(self.org)
        request = RequestFactory().get(path)
        request.resolver_match = resolve(path)
        request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Mira")
        return request

    def dispatch(self, request):
        return request.resolver_match.func(request, **request.resolver_match.kwargs)

    def test_settings_navigation(self):
        response = render_settings(self.default, self.owner)
        page = Markup(response.content.decode())
        self.assertEqual([a["text"].strip() for a in page.select("a", classes=("nav-item",), **{"aria-current": "page"})],
                         ["INTEVIA"], "The organisation tab must be selected.")
        domains = page.select("nav", **{"aria-label": "Domains"})
        self.assertEqual(len(domains), 1, "Show the organisation domain tabs on settings.")
        self.assertTrue(page.select("a", within=domains[0], href=reverse("event-list")),
                        "The organisation Events tab must lead to its normal destination.")
        home = reverse("organisation_home", args=(self.org,))
        crumb, = page.select("nav", **{"aria-label": "Breadcrumb"})
        for style in ("wide-path", "phone-path"):
            span, = page.select("span", within=crumb, classes=(style,))
            links = page.select("a", within=span, href=home)
            self.assertTrue(len(links) == 1 and self.owner["short_label"] in links[0]["text"],
                            "Both breadcrumb layouts must link the admitted parent label to its home.")
        backs = page.select("a", classes=("button", "secondary"), href=home)
        self.assertEqual([a["text"] for a in backs], ["Back to organisation"], "Provide one secondary way back.")
        self.assertFalse(any(n["tag"] == "section" for n in backs[0]["parents"]),
                         "The Back button belongs outside the CARE box.")

    def test_old_route_resolves_to_nothing(self):
        org, office = uuid4(), uuid4()
        old = f"/intevia/{org}/offices/{office}/assistance/"
        with self.assertRaises(Resolver404, msg="A stale office form must reach no callback."):
            resolve(old)
        with self.assertRaises(NoReverseMatch, msg="The old route name must be gone."):
            reverse("office_switch", args=(org, office))
        response = Client(enforce_csrf_checks=True).post(old, {"enabled": "off"})
        self.assertEqual(response.status_code, 404, "An expired office form must receive a real HTTP 404.")

    def test_member_line_for_a_member_only(self):
        for coordinator in (False, True):
            with self.subTest(coordinator=coordinator):
                page = Markup(render_settings(self.default | {"can_change": coordinator}, self.owner).content.decode())
                card, = page.select("section", classes=("notice", "care"))
                lines = [p["text"] for p in page.select("p", within=card)]
                self.assertEqual("Only the Organisation Coordinator can change this." in lines, not coordinator,
                                 "The member line belongs only to an admitted reader without the Coordinator office.")
                self.assertEqual("As Organisation Coordinator, you can change this." in lines, coordinator,
                                 "Show the personal invitation only to the current Coordinator.")
                self.assertEqual(len(page.select("form", within=card, method="post")), int(coordinator),
                                 "Only the Coordinator may see the command in the CARE box.")

    def test_guest_goes_to_sign_in(self):
        path = settings_url(self.org)
        client = Client(enforce_csrf_checks=True)
        with patch.object(pages.care_assistance, "read_organisation_assistance") as read:
            for response in (client.get(path), client.post(path)):
                self.assertEqual((response.status_code, response.get("Location")), (302, reverse("login") + "?next=" + path),
                                 "A guest must be directed to sign in and return to the settings URL.")
                assert_private(self, response)
        self.assertFalse(read.called, "A guest must not reach the setting service.")

    def test_chooser_name_only_when_released(self):
        key, chooser = uuid4(), uuid4()
        source = {"enabled": False, "choice_source": "chosen", "can_change": False, "switch_receipt": key}
        for extra, expected in (({"switched_by": chooser, "switched_by_name": "Mira <Reed>"}, ["Mira <Reed>"]),
                                ({}, []), ({"switched_by": chooser}, []), ({"switched_by_name": "Withheld"}, [])):
            response = render_settings(source | extra, self.owner)
            page = Markup(response.content.decode())
            self.assertEqual([d["text"] for d in page.select("dt")], ["Chosen by"] if expected else [],
                             "Show the chooser label only with a released display name.")
            self.assertEqual([d["text"] for d in page.select("dd")], expected,
                             "A released chooser name must be displayed as escaped text.")
            self.assertFalse(page.select("reed") or page.select("form") or page.select("button"),
                             "A member sees escaped attribution without a command.")
            self.assertNotContains(response, str(chooser), msg_prefix="The chooser UUID must never appear in the page.")
            self.assertNotContains(response, str(key), msg_prefix="The switch receipt must never appear in the page.")
            self.assertNotContains(response, "As Organisation Coordinator", msg_prefix="An ordinary member must never see command guidance.")

    def test_failed_read_shows_the_five_part_503(self):
        for failing in ("setting", "label"):
            with self.subTest(failing=failing), patch.object(pages.transaction, "atomic", return_value=nullcontext()), \
                 patch.object(pages.care_assistance, "read_organisation_assistance", return_value=self.default,
                              side_effect=DatabaseError() if failing == "setting" else None), \
                 patch.object(pages.navigation, "member_organisation_name",
                              side_effect=DatabaseError() if failing == "label" else None):
                response = self.dispatch(self.request())
                self.assertEqual(response.status_code, 503, "A failed read must report temporary unavailability.")
                assert_private(self, response)
                page = Markup(response.content.decode())
                alert, = page.select("aside", role="alert")
                self.assertEqual([h["text"] for h in page.select("h1", classes=("record-heading", "page-title"))], ["Organisation settings could not be shown."],
                                 "The refusal heading must identify this settings read.")
                rows = page.select("p", within=alert, classes=("next-action", "link-row"))
                retry = [a for row in rows for a in page.select("a", within=row, classes=("button", "secondary"))]
                self.assertEqual([(a["text"].strip(), a["attrs"]["href"]) for a in retry],
                                 [("Try opening organisation settings again.", settings_url(self.org))],
                                 "The failed read must link back to the settings in the request.")
                self.assertEqual([p["text"].strip() for p in page.select("p", within=alert)],
                                 ["The setting could not be read.", "This page offers no change.",
                                  "Try opening organisation settings again.", "If this continues, contact support."],
                                 "Explain the reason, effect, next step and source of help.")
                self.assertFalse(page.select("form") or page.select("span", classes=("tag",)),
                                 "A failed read must offer no change form or default state.")

    def test_failed_read_without_an_address_keeps_the_retry_as_text(self):
        from core.office_switch import failed_settings_read

        request = self.request()
        request.resolver_match = None
        response = failed_settings_read(request)
        self.assertEqual(response.status_code, 503)
        page = Markup(response.content.decode())
        alert, = page.select("aside", role="alert")
        self.assertIn("Try opening organisation settings again.", alert["text"])
        self.assertFalse(page.select("a", within=alert), "Without an addressed organisation there is no retry target.")

    def test_other_methods_receive_the_landed_refusal(self):
        client = Client(enforce_csrf_checks=True)
        user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Mira")
        identity = SimpleNamespace(access_state="active", display_name="Mira")
        with patch("django.contrib.auth.middleware.get_user", return_value=user), \
             patch("core.session.IdentityAccessMiddleware._valid_identity", return_value=identity), \
             patch.object(pages.care_assistance, "read_organisation_assistance") as read:
            for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"):
                response = client.generic(method, settings_url(self.org))
                assert_private(self, response)
                self.assertEqual((response.status_code, response.get("Allow")), (405, "GET, HEAD"),
                                 "The read-only route must allow only GET and HEAD.")
                self.assertContains(response, "This address accepts a different kind of request.", status_code=405,
                                    msg_prefix="Use the landed method refusal before reading anything.")
            self.assertFalse(read.called, "Other methods must never reach the settings read.")
