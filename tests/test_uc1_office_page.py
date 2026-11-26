"""Member office route, private responses and admitted display fields."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import resolve, reverse

from core import models
from core.session import SESSION_ACCESS_EPOCH_KEY, bind_identity_session
from core.ui import initials
from tests.test_f0_ui import Markup


def care_state_text(markup):
    page = Markup(markup)
    return [p["text"].strip() for card in page.select("section", **{"aria-labelledby": "settings-assistance"})
            for p in page.select("p", within=card)]


def office_path(owner, office):
    return reverse("office_page", kwargs={"org": owner.organism_id, "office": office})


def signed_in(identity):
    client = Client()
    client.force_login(identity.credential)
    session = client.session
    bind_identity_session(SimpleNamespace(session=session), identity)
    session.save()
    client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
    return client


def assert_private(test, response):
    def tokens(header):
        return {word.strip().lower() for word in response.get(header, "").split(",")}
    test.assertTrue({"private", "no-store"} <= tokens("Cache-Control") and "cookie" in tokens("Vary"),
                    "The personalised office response must forbid shared storage and vary by session.")


@override_settings(ALLOWED_HOSTS=["testserver"])
class OfficePageTests(TransactionTestCase):
    def test_office_route_binds_owner_and_reader(self):
        from tests.office_record_support import approved_office
        from tests.test_discussion_office_links import office_basis, record_office
        from tests.test_law_acts import add_member, ordinary_birth

        owner, founder, old, definition, version, _ = approved_office()
        current = record_office(owner, founder, definition, prior=old)
        old_row = models.OrganismRoleDefinition.objects.get(role_uuid=old)
        current_row = models.OrganismRoleDefinition.objects.get(role_uuid=current)
        self.assertEqual(current_row.supersedes_id, old_row.pk, "The independently saved old office must be the predecessor.")
        client = signed_in(add_member(owner))
        path = office_path(owner, old)
        response = client.get(path)
        self.assertEqual(response.status_code, 200, "A member must be able to open the older office URL directly.")
        page = Markup(response.content.decode())
        self.assertEqual([n["text"] for n in page.select("h1")], ["Treasurer"], "The main heading must use the office label.")
        org_name = models.LOFoundingContentVersion.objects.get(living_organism=owner, version=1).name
        self.assertContains(response, org_name, msg_prefix="The office page must name the organisation that admitted the read.")
        adoption_fields = [pair for details in page.select("details", classes=("office-adoption",))
                           for pair in zip((n["text"] for n in page.select("dt", within=details)),
                                           (n["text"] for n in page.select("dd", within=details)), strict=True)]
        self.assertIn(("Adopting body", org_name), adoption_fields,
                      "The real provision must name its own organisation as the adopting body.")
        current_tab = page.select("a", href=reverse("f0-organisation"), **{"aria-current": "page"})
        self.assertEqual(len(current_tab), 1, "The member office must retain the organisation tab in the F0 shell.")
        self.assertFalse(page.select("hgroup"), "The repeated Board/organisation context line is removed.")
        crumb, = page.select("nav", **{"aria-label": "Breadcrumb"})
        wide, = page.select("span", within=crumb, classes=("wide-path",))
        board_link, = [a for a in page.select("a", within=wide) if a["text"] == "Board"]
        board_circle = models.Circle.objects.get(parent_organism=owner, kind="GOVERNANCE")
        self.assertEqual(board_link["attrs"]["href"], reverse("circle_page", args=(owner.organism_id, board_circle.circle_uuid)),
                         "The admitted Board's short label must lead to its own Circle.")
        self.assertContains(response, f"Office definition · Version {old_row.definition_version}",
                            msg_prefix="The independent cited office version must be visible.")
        self.assertContains(response, f"Current office definition · Version {current_row.definition_version}",
                            msg_prefix="The current office definition must be reported alongside the cited version.")
        law_section = page.select("section", **{"aria-labelledby": "office-provisions"})[0]
        self.assertEqual([node["text"] for node in page.select("h3", within=law_section)
                          if node["attrs"].get("id", "").startswith("office-law-tier-")], ["Policy"],
                         "The real adopted Policy must supply its tier heading.")
        self.assertContains(response, '<h4 class="card-heading">Establishes this office</h4>', html=True,
                            msg_prefix="An unmapped law must name its relationship as the heading.")
        self.assertIn(f"{version.resource.resource_id} · Clause “approval” · Version {version.version_number}",
                      [node["text"] for node in page.select("p", within=law_section, classes=("meta",))],
                      "The provision must display its own locator and law version together.")
        self.assertContains(response, "<p>Vacant</p>", html=True, msg_prefix="Only an evidenced vacancy may be displayed.")

        foreign, foreign_founder = ordinary_birth()
        foreign_office = record_office(foreign, foreign_founder, office_basis(foreign, foreign_founder))
        unknown = client.get(office_path(owner, uuid4()))
        self.assertEqual(unknown.status_code, 404, "An unknown office must return the unavailable page.")
        self.assertContains(unknown, f"Back to {org_name}", status_code=404,
                            msg_prefix="The admitted reader may return to the organisation in the address.")
        wrong = client.get(office_path(foreign, old))
        unknown_foreign = client.get(office_path(foreign, uuid4()))
        self.assertEqual((wrong.status_code, wrong.content), (404, unknown_foreign.content),
                         "In an unadmitted organisation a known office must be indistinguishable from a missing one.")
        self.assertNotContains(wrong, "Back to ", status_code=404,
                               msg_prefix="An unadmitted reader must receive no return destination.")
        foreign_target = client.get(office_path(owner, foreign_office))
        self.assertEqual((foreign_target.status_code, foreign_target.content), (404, unknown.content),
                         "A known foreign office must be indistinguishable from an unknown office.")
        signed_out = Client().get(path)
        self.assertEqual((signed_out.status_code, signed_out.get("Location")), (302, reverse("login") + "?next=" + path),
                         "A visitor must be sent to sign in for the original office URL.")
        assert_private(self, signed_out)

    def test_member_pages_keep_responses_private(self):
        from tests.office_record_support import approved_office
        from tests.test_law_acts import add_member

        owner, founder, office, _, _, _ = approved_office(board_seats=0)
        member = add_member(owner)
        path = office_path(owner, office)
        marks = []
        for reader in (founder, member):
            client = signed_in(reader)
            response = client.get(path)
            self.assertEqual(response.status_code, 200, "Each bound member must receive their own office page.")
            assert_private(self, response)
            profile = Markup(response.content.decode()).select("a", classes=("profile-link",))
            self.assertEqual([node["text"].strip() for node in profile], [initials(reader.display_name)],
                             "The profile mark must belong to the account that opened this URL.")
            marks.append(initials(reader.display_name))
        self.assertNotEqual(marks[0], marks[1], "The two accounts must have distinguishable profile marks.")
        unavailable = client.get(office_path(owner, uuid4()))
        self.assertEqual(unavailable.status_code, 404, "An absent office must use the private unavailable response.")
        assert_private(self, unavailable)
        refused_method = client.post(path)
        self.assertEqual(refused_method.status_code, 405, "The office read must refuse a POST.")
        assert_private(self, refused_method)

        session = client.session
        session[SESSION_ACCESS_EPOCH_KEY] += 1
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        stale = client.get(path)
        self.assertEqual((stale.status_code, stale.get("Location")), (302, reverse("login")),
                         "A stale binding must reach the existing session refusal before the office view.")
        assert_private(self, stale)

    def test_probationary_page_renders_only_its_pin(self):
        from src.intevia.services import circle_commands, discussion, discussion_posts
        from tests.office_record_support import approved_office
        from tests.test_discussion_office_links import discussion_category, public_start_args, record_office
        from tests.test_law_acts import add_member
        from tests.test_office_record_services import fixture_membership_state

        owner, founder, pinned, definition, _, _ = approved_office(board_seats=0)
        reader = add_member(owner)
        scope = dict(credential=founder.credential, organism_uuid=owner.organism_id)
        circle = circle_commands.form(**scope, circle_uuid=uuid4(), kind="GENERIC",
                                      purpose="Welcome new members", request_uuid=uuid4())
        board = discussion_category(owner, founder, circle.circle_uuid)
        local = scope | {"circle_uuid": circle.circle_uuid}
        fixture_membership_state(owner, founder, reader, "PROBATIONARY")
        discussion.set_probation_access(**scope, enabled=True, request_uuid=uuid4())
        discussion.open_circle_welcome(**local, request_uuid=uuid4())
        welcome = next(c for c in discussion.board_categories(**local)["categories"] if c["functional_code"] == "WELCOME")
        args = public_start_args(owner, founder, circle, board) | {"category_uuid": welcome["category_uuid"]}
        discussion_posts.start_topic(**args, office_definition_uuid=pinned)
        sibling = record_office(owner, founder, definition, prior=pinned)
        version = models.OrganismRoleDefinition.objects.get(role_uuid=pinned).definition_version
        client = signed_in(reader)
        permitted = client.get(office_path(owner, pinned))
        self.assertEqual(permitted.status_code, 200, "The exact visible Topic pin must admit the probationary reader.")
        self.assertNotContains(permitted, "Vacant", msg_prefix="A Topic pin must not disclose that the office is vacant.")
        self.assertNotContains(permitted, "Office tenure", msg_prefix="A pin must disclose no tenure block.")
        self.assertContains(permitted, '<h1 class="record-heading page-title">Office record</h1>', html=True,
                            msg_prefix="A pin without the office label must use the neutral heading.")
        self.assertContains(permitted, f"Office definition · Version {version}",
                            msg_prefix="The pin must identify its own definition version.")
        self.assertNotContains(permitted, "Current office definition", msg_prefix="A pin must grant no family lookup.")
        denied = client.get(office_path(owner, sibling))
        unknown = client.get(office_path(owner, uuid4()))
        self.assertEqual((denied.status_code, denied.content), (404, unknown.content),
                         "A visible predecessor pin must grant no access to its unpinned successor.")
        assert_private(self, denied)


class OfficeMarkupTests(SimpleTestCase):
    def test_unavailable_office_returns_only_to_an_admitted_organisation(self):
        from unittest.mock import patch
        from src.intevia.services import member_navigation
        from src.intevia.services.care_assistance import SettingRefused

        org, office = uuid4(), uuid4()
        request = RequestFactory().get(reverse("office_page", args=(org, office)))
        request.resolver_match = resolve(request.path)
        request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Reader")
        name = "River Trust"
        for admitted in (True, False):
            with self.subTest(admitted=admitted), patch(
                "src.intevia.services.office_record.office_record", side_effect=SettingRefused("CARE_SETTING_UNAVAILABLE")
            ), patch.object(member_navigation, "member_organisation_name", return_value={"organism_uuid": org, "full_name": name},
                            side_effect=None if admitted else member_navigation.NavigationUnavailable()) as read:
                response = request.resolver_match.func(request, **request.resolver_match.kwargs)
                self.assertEqual(response.status_code, 404)
                page = Markup(response.content.decode())
                main, = page.select("main")
                self.assertEqual([n["text"] for n in page.select("h1", within=main, classes=("record-heading", "page-title"))],
                                 ["Office record unavailable"])
                notice, = page.select("div", within=main, classes=("notice", "refusal"), role="alert")
                self.assertEqual(notice["text"].strip(), "This office record is unavailable to you.")
                links = page.select("a", within=main)
                expected = [(f"Back to {name}", reverse("organisation_home", args=(org,)))] if admitted else []
                self.assertEqual([(a["text"].strip(), a["attrs"]["href"]) for a in links], expected,
                                 "Only a reader admitted to the addressed organisation may receive its return link.")
                if admitted:
                    self.assertEqual(links, page.select("a", within=main, classes=("button", "secondary")))
                else:
                    self.assertNotIn(name, response.content.decode(), "The unavailable office must withhold the name.")
                read.assert_called_once_with(credential=request.user, organism_uuid=org)

    def test_tenure_uses_the_permitted_name_and_dates(self):
        # Distinct full/short names exercise both template slots under today's equal organisation defaults.
        office = dict(organisation_full_name="River Organisation", organisation_short_label="RO",
                      board_full_name="Board of Trustees", board_short_label="Board", label="Treasurer",
                      requested_version=1, current_version=1)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 12, 1, tzinfo=timezone.utc)
        request = RequestFactory().get(reverse("office_page", args=[uuid4(), uuid4()]))
        request.user = AnonymousUser()
        request.resolver_match = resolve(request.path)
        rows = [dict(state="ongoing", starts_at=start, ends_at=end),
                dict(state="former", holder_name="Former <Member>", starts_at=start, ends_at=end)]
        page = Markup(render_to_string("core/office_page.html", {"record": {"office": office, "tenure": rows}}, request))
        paragraphs = [p["text"].strip() for p in page.select("p")]
        self.assertTrue(any("Scheduled to finish" in text and "1 December 2026" in text for text in paragraphs),
                        "A current fixed term must retain its scheduled end.")
        self.assertIn("Current term", paragraphs, "Withheld holder identity must leave just the current term label.")
        self.assertIn("Former term · Former <Member>", paragraphs, "A permitted former name must render as text.")
        self.assertFalse(page.select("member"), "Text in a holder's name must never become an HTML element.")
        self.assertTrue(any("Ended" in text for text in paragraphs), "A former term must display its actual ending.")
        crumb = page.select("nav", **{"aria-label": "Breadcrumb"})
        self.assertTrue(len(crumb) == 1 and "RO" in crumb[0]["text"] and "River Organisation" not in crumb[0]["text"],
                        "The breadcrumb must keep the organisation short label in its own place.")
        self.assertNotIn("River Organisation · Board of Trustees", paragraphs,
                         "The organisation and Board must not be repeated below the breadcrumb.")
