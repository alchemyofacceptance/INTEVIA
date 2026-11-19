"""Member journey from the home through the Board to its permitted office record."""
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import connection
from django.test import RequestFactory, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import resolve, reverse

from core import models, organisation_pages as pages
from tests.test_uc1_office_page import assert_private, office_path, signed_in
from tests.test_uc1_organisation_pages import markup, member_url


def board_path(owner):
    board = models.Circle.objects.get(parent_organism=owner, kind="GOVERNANCE")
    return member_url(owner.organism_id, board.circle_uuid)


def labelled_link(test, page, heading_id, label):
    sections = page.select("section", **{"aria-labelledby": heading_id})
    test.assertEqual(len(sections), 1, "The page must contain one section for this directory.")
    links = [link for link in page.select("a", within=sections[0])
             if any(n["text"] == label for n in page.select("strong", within=link))]
    test.assertEqual(len(links), 1, f"The directory must have exactly one link labelled {label}.")
    return links[0]


def discussion_board_count(owner):
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM public.core_discboard WHERE organism_id=%s", [owner.pk])
        return cursor.fetchone()[0]


@override_settings(ALLOWED_HOSTS=["testserver"])
class BoardOfficeJourneyTests(TransactionTestCase):
    def test_member_follows_board_and_treasurer_without_discussion(self):
        from tests.office_record_support import approved_office, observation_footprint
        from tests.test_discussion_office_links import record_office
        from tests.test_law_acts import add_member

        owner, founder, treasurer, definition, _, _ = approved_office()
        secretary = record_office(owner, founder, replace(definition, code="SECRETARY"))
        client = signed_in(add_member(owner))
        self.assertEqual(discussion_board_count(owner), 0, "The journey must start without a discussion board.")
        before = observation_footprint(owner)
        entry = client.get(reverse("f0-organisation"), follow=True)
        self.assertEqual(entry.status_code, 200, "The single-organisation member must reach their home.")
        board_link = labelled_link(self, markup(entry), "circle-list-heading", "Board")
        board = client.get(board_link["attrs"]["href"])
        self.assertEqual(board.status_code, 200, "The home's Board link must open the governance Circle.")
        assert_private(self, board)
        directory = markup(board)
        link = labelled_link(self, directory, "board-offices-heading", "Treasurer")
        self.assertEqual(observation_footprint(owner), before,
                         "The home and Board directory must leave the office's first display for the office page.")
        office = client.get(link["attrs"]["href"])
        self.assertEqual(office.status_code, 200, "The Treasurer link on the Board must open a readable office page.")
        self.assertEqual(office.request["PATH_INFO"], office_path(owner, treasurer),
                         "The clicked URL must carry this organisation and the Treasurer definition.")
        self.assertEqual([n["text"] for n in markup(office).select("h1")], ["Treasurer"],
                         "The click must land on the Treasurer office record.")
        self.assertContains(office, "<p>Vacant</p>", html=True, msg_prefix="The office record must confirm the vacancy.")
        self.assertEqual(labelled_link(self, directory, "board-offices-heading", "Secretary")["attrs"]["href"],
                         office_path(owner, secretary), "The second office must have its own destination.")
        self.assertEqual(discussion_board_count(owner), 0, "Following the two links must not provision a discussion board.")
        assert_private(self, office)

    def test_pending_nomination_award_and_departure_match_office_tenure(self):
        from src.intevia.services import organism_membership_service, organism_role_service
        from src.intevia.services.office_record import office_record
        from tests import election_support, test_c1b_departure, test_sr_office_actions
        from tests.cq_authority_support import authority
        from tests.office_record_support import approved_office
        from tests.s015_0021_support import database_now
        from tests.test_law_acts import add_member

        owner, founder, treasurer, _, _, _ = approved_office()
        reader = add_member(owner)
        client = signed_in(reader)
        path = board_path(owner)
        fixture = test_sr_office_actions.SROfficeActionTests()
        fixture.actor, fixture.organism = founder, owner
        fixture.ids = {"basis": owner.founding_authority_basis_id}
        nominee = fixture._member()
        role = models.OrganismRoleDefinition.objects.get(role_uuid=treasurer)
        proposed = election_support.office_proposal(owner, founder, nominee.identity, role, self_proposed=False)
        self.assertEqual(list(models.ContextualRoleAssignmentStateEvent.objects.filter(assignment=proposed)
                              .values_list("action", flat=True)), ["PROPOSE"],
                         "The candidate must be nominated and consented with no effective award.")

        def board_state():
            page = markup(client.get(path))
            link = labelled_link(self, page, "board-offices-heading", "Treasurer")
            return [n["text"] for n in page.select("span", within=link, classes=("board-office-state",))]

        def has_vacancy():
            tenure = office_record(credential=reader.credential, organism_uuid=owner.organism_id,
                                   office_definition_uuid=treasurer)["tenure"]
            return any(term["state"] == "vacant" for term in tenure)

        self.assertTrue(has_vacancy(), "The office read must independently confirm the pending vacancy.")
        self.assertEqual(board_state(), ["Vacant"], "A pending nominee must leave the unfilled Treasurer shown Vacant.")
        service = organism_role_service.OrganismRoleService(authority=authority(), clock=database_now)
        service.transition_assignment(fixture._transition_command(proposed, "ASSIGN"))
        self.assertFalse(has_vacancy(), "The office read must recognise the effective award.")
        self.assertEqual(board_state(), ["Held"], "An effective Treasurer must be shown Held.")
        membership = organism_membership_service.OrganismMembershipService(authority=authority(), clock=database_now)
        membership.leave_organism(test_c1b_departure.DepartureTests().command(nominee))
        self.assertTrue(has_vacancy(), "The office read must confirm the vacancy after departure.")
        self.assertEqual(board_state(), ["Vacant"], "The Board must show Vacant again after the holder departs.")

    def test_office_link_with_a_different_member_organisation_is_unavailable(self):
        from tests.office_record_support import approved_office
        from tests.test_law_acts import add_member

        left, _, first, _, _, _ = approved_office()
        right, _, second, _, _, _ = approved_office()
        reader = add_member(left)
        with patch("tests.test_sr_office_guards.seed_identity", return_value=reader.pk):
            add_member(right)
        client = signed_in(reader)
        for owner, local, foreign in ((left, first, second), (right, second, first)):
            with self.subTest(owner=owner.organism_id):
                page = markup(client.get(board_path(owner)))
                link = labelled_link(self, page, "board-offices-heading", "Treasurer")
                self.assertEqual(link["attrs"]["href"], office_path(owner, local),
                                 "Each Board must offer the Treasurer definition belonging to its own organisation.")
                self.assertFalse(page.select("a", href=office_path(owner, foreign)),
                                 "A Board must omit an office belonging to the reader's other organisation.")
                wrong = client.get(office_path(owner, foreign))
                absent = client.get(office_path(owner, uuid4()))
                self.assertEqual(wrong.status_code, 404, "A real office under the other owner must give the private 404.")
                self.assertEqual((absent.status_code, absent.content), (404, wrong.content),
                                 "The crossed owner and office must be indistinguishable from an unknown office.")
                assert_private(self, wrong)


class BoardOfficeProjectionTests(SimpleTestCase):
    def test_only_the_board_shows_links_and_withheld_tenure_has_no_status(self):
        owner, circle, pinned = uuid4(), uuid4(), uuid4()
        name = {"organism_uuid": owner, "full_name": "River trust", "short_label": "River"}
        pin = {"office_definition_uuid": pinned, "label": "Office record"}
        route = member_url(owner, circle)

        def read(kind, offices):
            request = RequestFactory().get(route)
            request.user = SimpleNamespace(is_authenticated=True, get_full_name=lambda: "Reader")
            request.intevia_identity = SimpleNamespace(display_name="Reader", access_state="active", colour_mode="light")
            request.resolver_match = match = resolve(route)
            selected = {"kind": kind, "circle_uuid": circle, "full_name": "Selected circle",
                        "short_label": "Circle", "description": None}
            data = {"organisation": name, "selected_circle": selected, "board_offices": offices}
            with patch.object(pages, "_memberships", return_value=[owner]), \
                 patch.object(pages.transaction, "atomic", return_value=nullcontext()), \
                 patch.object(pages.navigation, "member_navigation", return_value=data), \
                 patch.object(pages, "_status", return_value=None):
                return markup(match.func(request, **match.kwargs))

        page = read("GOVERNANCE", [pin])
        link = labelled_link(self, page, "board-offices-heading", "Office record")
        self.assertEqual(link["attrs"]["href"], reverse("office_page", args=(owner, pinned)),
                         "A label-only row must link to its exact permitted definition.")
        self.assertFalse(page.select("span", within=link, classes=("board-office-state",)),
                         "A withheld tenure must produce neither a Vacant nor a Held marker.")
        self.assertNotIn("aria-describedby", link["attrs"], "A withheld status must have no accessible status reference.")
        empty = read("GOVERNANCE", [])
        self.assertTrue(any(n["text"] == "No offices to show here." for n in empty.select("p")),
                        "An empty admitted Board list must describe only what can be shown.")
        other = read("GENERIC", [pin])
        self.assertFalse(other.select("section", **{"aria-labelledby": "board-offices-heading"}),
                         "An ordinary Circle must not display the organisation's Board office list.")

        ordinary = read("GOVERNANCE", [
            {"office_definition_uuid": pinned, "label": "Treasurer", "vacant": True},
            {"office_definition_uuid": uuid4(), "label": "Secretary", "vacant": False},
        ])
        for name, status in (("Treasurer", "Vacant"), ("Secretary", "Held")):
            link = labelled_link(self, ordinary, "board-offices-heading", name)
            label_id = link["attrs"].get("aria-labelledby")
            status_id = link["attrs"].get("aria-describedby")
            self.assertEqual([n["text"] for n in ordinary.select("strong", id=label_id)], [name],
                             "Each link's accessible name must identify only its own office.")
            self.assertEqual([n["text"] for n in ordinary.select("span", id=status_id)], [status],
                             "Each office link must describe its own effective status separately.")
