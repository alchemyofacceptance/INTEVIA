"""The member navigation read, through the same credential and owner used by the page."""

from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from core import models
from src.intevia.services import care_assistance, circle_participation, discussion, discussion_posts
from src.intevia.services import member_navigation as navigation
from src.intevia.services import organism_membership_service, organism_role_service
from src.intevia.services.office_record import office_record
from tests import election_support, test_c1b_departure, test_sr_office_actions
from tests.cq_authority_support import authority
from tests.office_record_support import approved_office
from tests.s015_0021_support import database_now
from tests.test_discussion_office_links import discussion_category, office_basis, public_start_args, record_office
from tests.test_law_acts import add_member, ordinary_birth
from tests.test_office_record_services import fixture_membership_state
from tests.test_uc1_record_names import form_generic, name_sources


def scope(owner, reader):
    return {"organism_uuid": owner.organism_id, "credential": reader.credential}


class MemberNavigationTests(TransactionTestCase):
    def test_navigation_lists_all_and_only_this_organisations_circles(self):
        owner, founder = ordinary_birth(board_seats=1)
        foreign, other = ordinary_birth()
        river = form_generic(owner, founder, "River stewards")
        elsewhere = form_generic(foreign, other, "Other river")
        elsewhere_office = record_office(foreign, other, office_basis(foreign, other))
        board = models.Circle.objects.get(parent_organism=owner, kind="GOVERNANCE")
        reader = add_member(owner)
        self.assertFalse(circle_participation.my_places(**scope(owner, reader)),
                         "The ordinary member must have no Circle place before using the directory.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public.core_discboard WHERE organism_id=%s", [owner.pk])
            self.assertEqual(cursor.fetchone()[0], 0, "The fixture must allow navigation before discussion is provisioned.")
        with CaptureQueriesContext(connection) as queries:
            page = navigation.member_navigation(**scope(owner, reader))
        circles = {(c["circle_uuid"], c["full_name"], c["short_label"], c["kind"]) for c in page["circles"]}
        self.assertEqual(circles, {(board.circle_uuid, "Board of Trustees", "Board", "GOVERNANCE"),
                                   (river.circle_uuid, "River stewards", "River stewards", "GENERIC")},
                         "The non-participating member must find the Board and the other Circle by name.")
        org_name = models.LOFoundingContentVersion.objects.get(living_organism=owner, version=1).name
        self.assertEqual(page["organisation"], {"organism_uuid": owner.organism_id, "full_name": org_name,
                                                "short_label": org_name}, "Both labels must name this organisation.")
        self.assertNotIn(elsewhere.circle_uuid, {row[0] for row in circles}, "Another owner's Circle must stay outside the list.")
        self.assertNotIn(elsewhere_office, [row["office_definition_uuid"] for row in page["board_offices"]],
                         "A definition belonging to another organisation must be absent from this Board.")
        self.assertEqual(set(page), {"evaluated_at", "organisation", "circles", "board_offices"},
                         "Navigation must deliver only the cut, organisation, Circle destinations and office handles.")
        self.assertTrue(all(set(c) == {"circle_uuid", "full_name", "short_label", "kind"} for c in page["circles"]),
                        "Circle destinations must expose only their reference, names and kind.")
        self.assertFalse(any("core_disc" in q["sql"].lower() for q in queries),
                         "The ordinary directory must work without reading or provisioning discussions.")
        for target in (foreign.organism_id, uuid4()):
            with self.subTest(target=target), CaptureQueriesContext(connection) as refused:
                with self.assertRaises(navigation.NavigationUnavailable, msg="A foreign or absent owner must refuse.") as denial:
                    navigation.member_navigation(credential=reader.credential, organism_uuid=target)
                self.assertEqual(str(denial.exception), "This organisation is unavailable to you.",
                                 "An unknown owner and a foreign owner must receive the same refusal.")
            self.assertEqual(name_sources(refused), [], "A refused owner must cause no display-name lookup.")

    def test_probationary_navigation_preserves_reach(self):
        owner, founder, pinned, definition, _, _ = approved_office()
        hidden = record_office(owner, founder, replace(definition, code="SECRETARY"))
        reader = add_member(owner)
        person = scope(owner, reader)
        author = scope(owner, founder)
        circle = form_generic(owner, founder, "Welcome group")
        category = discussion_category(owner, founder, circle.circle_uuid)
        local = author | {"circle_uuid": circle.circle_uuid}
        fixture_membership_state(owner, founder, reader, "PROBATIONARY")
        discussion.set_probation_access(**author, enabled=True, request_uuid=uuid4())
        discussion.open_circle_welcome(**local, request_uuid=uuid4())
        welcome = next(row for row in discussion.board_categories(**local)["categories"] if row["functional_code"] == "WELCOME")
        closed_args = public_start_args(owner, founder, circle, category)
        discussion_posts.start_topic(**closed_args, office_definition_uuid=hidden)
        public_args = public_start_args(owner, founder, circle, category) | {"category_uuid": welcome["category_uuid"]}
        discussion_posts.start_topic(**public_args, office_definition_uuid=pinned)
        newer = record_office(owner, founder, definition, prior=pinned)
        # Baseline reach from the office and discussion services, independent of the new directory.
        self.assertEqual(office_record(**person, office_definition_uuid=pinned)["office"]["requested_definition"], pinned,
                         "The visible opening must independently admit its exact office page.")
        for denied in (hidden, newer):
            with self.assertRaises(care_assistance.SettingRefused, msg="An unreadable pin and an unpinned successor must refuse."):
                office_record(**person, office_definition_uuid=denied)
        with CaptureQueriesContext(connection) as queries:
            page = navigation.member_navigation(**person)
        self.assertNotIn(hidden, [row["office_definition_uuid"] for row in page["board_offices"]],
                         "The second, unreachable office must be absent from the probationary list.")
        self.assertEqual(page["board_offices"], [{"office_definition_uuid": pinned, "label": "Office record"}],
                         "Only the exact admitted pin may appear, without a family label or vacancy.")
        self.assertFalse(any("with recursive family" in q["sql"].lower() or "elect_office_effective" in q["sql"].lower()
                             for q in queries), "A probationary read must issue neither family nor tenure queries.")
        reachable = {row["circle_uuid"] for row in discussion_posts.visible_boards(credential=reader.credential)
                     if row["organism_uuid"] == owner.organism_id and row["circle_uuid"] is not None}
        self.assertEqual({row["circle_uuid"] for row in page["circles"]}, reachable,
                         "The probationary Circle list must agree with independently reachable discussion boards.")
        discussion_posts.set_post_hidden(**local, category_uuid=welcome["category_uuid"],
                                         thread_uuid=public_args["thread_uuid"], post_uuid=public_args["post_uuid"],
                                         request_uuid=uuid4(), hidden=True, as_coordinator=True)
        with self.assertRaises(care_assistance.SettingRefused, msg="Hiding the only visible opening must close its office page."):
            office_record(**person, office_definition_uuid=pinned)
        self.assertEqual(navigation.member_navigation(**person)["board_offices"], [],
                         "When the opening is hidden, navigation must withdraw its last office link.")

    def test_board_vacancy_matches_effective_tenure(self):
        owner, founder, office, definition, _, _ = approved_office()
        read = scope(owner, add_member(owner))
        fixture = test_sr_office_actions.SROfficeActionTests()
        fixture.actor, fixture.organism = founder, owner
        fixture.ids = {"basis": owner.founding_authority_basis_id}
        member = fixture._member()
        role = models.OrganismRoleDefinition.objects.get(role_uuid=office)
        appointment = election_support.office_proposal(owner, founder, member.identity, role, self_proposed=False)
        self.assertEqual(list(models.ContextualRoleAssignmentStateEvent.objects.filter(assignment=appointment)
                              .values_list("action", flat=True)), ["PROPOSE"],
                         "The proposed and consented appointment must still lack its award.")

        def treasurer(known):
            links = navigation.member_navigation(**read)["board_offices"]
            return [row for row in links if row["office_definition_uuid"] == known]

        def vacant(known):
            return any(row["state"] == "vacant" for row in office_record(**read, office_definition_uuid=known)["tenure"])

        self.assertEqual(treasurer(office), [{"office_definition_uuid": office, "label": "Treasurer", "vacant": True}],
                         "A pending nominee must leave the Treasurer shown Vacant on the Board.")
        self.assertTrue(vacant(office), "The existing office read must independently witness that pending vacancy.")
        service = organism_role_service.OrganismRoleService(authority=authority(), clock=database_now)
        service.transition_assignment(fixture._transition_command(appointment, "ASSIGN"))
        current = record_office(owner, founder, definition, prior=office)
        self.assertEqual(treasurer(office), [], "Only the current definition may be listed as the ordinary destination.")
        self.assertEqual(treasurer(current), [{"office_definition_uuid": current, "label": "Treasurer", "vacant": False,
                                               "holder_name": member.identity.display_name}],
                         "The amended office must show the member awarded the earlier definition.")
        self.assertFalse(vacant(office), "The old office link must still resolve the ongoing tenure across the amendment.")
        membership_service = organism_membership_service.OrganismMembershipService(authority=authority(), clock=database_now)
        membership_service.leave_organism(test_c1b_departure.DepartureTests().command(member))
        self.assertTrue(treasurer(current)[0]["vacant"] and vacant(current),
                        "After the holder leaves, both reads must show an evidenced vacancy.")
        self.assertNotIn("holder_name", treasurer(current)[0],
                         "After the holder leaves, the Board list must no longer name them.")

    def test_board_uses_the_admitted_office_label(self):
        owner, founder, office, _, _, _ = approved_office()
        reader = scope(owner, founder)
        rows = navigation.member_navigation(**reader)["board_offices"]
        rendered = [row["label"] for row in rows if row["office_definition_uuid"] == office]
        self.assertEqual(rendered, ["Treasurer"], "The Board list must display the helper's Treasurer label.")
        with patch.object(navigation._record_names, "office_label", return_value="Label from the helper") as labels:
            rows = navigation.member_navigation(**reader)["board_offices"]
        self.assertIn((("TREASURER",), {}), labels.call_args_list,
                      "The admitted code must be passed to the shared office-label helper.")
        self.assertEqual([row["label"] for row in rows if row["office_definition_uuid"] == office],
                         ["Label from the helper"], "The Board must publish the label returned by the helper.")

    def test_navigation_uses_one_cut_after_the_owner_lock(self):
        owner, founder, office, _, _, _ = approved_office()
        reader = add_member(owner)
        before_birth = database_now()
        later = form_generic(owner, founder, "Later group")
        birth = models.CircleStateEvent.objects.get(circle=later, sequence=1)
        self.assertLess(before_birth, max(birth.effective_at, birth.recorded_at, birth.received_at),
                        "The later Circle's first event must lie beyond the controlled cut.")
        calls = []
        lock = navigation._lock_registered_organism

        def take_lock(alias, organisation):
            lock(alias, organisation)
            calls.append("owner locked")

        def supply_cut():
            calls.append("cut taken")
            return before_birth

        with patch.object(navigation, "_lock_registered_organism", side_effect=take_lock), \
             patch.object(navigation, "database_now", side_effect=supply_cut), \
             patch.object(navigation, "office_family", wraps=navigation.office_family) as families, \
             patch.object(navigation._record_names, "full_name_and_short_label",
                          wraps=navigation._record_names.full_name_and_short_label) as names:
            page = navigation.member_navigation(**scope(owner, reader))
        self.assertEqual(calls, ["owner locked", "cut taken"],
                         "The navigation read must take exactly one cut after obtaining the owner lock.")
        self.assertEqual(page["evaluated_at"], before_birth, "The returned cut must identify the read's actual sample.")
        self.assertNotIn(later.circle_uuid, [row["circle_uuid"] for row in page["circles"]],
                         "A Circle whose birth lies beyond the cut must remain absent.")
        self.assertIn(office, [row["office_definition_uuid"] for row in page["board_offices"]],
                      "The same cut must still admit the earlier Treasurer definition.")
        self.assertEqual(len(names.call_args_list), len(page["circles"]) + 1,
                         "Each visible Circle and the selected organisation must use the name helper.")
        self.assertTrue(all(call.kwargs["cut"] == before_birth and call.kwargs["owner_id"] == owner.pk
                            for call in names.call_args_list),
                        "Every name must be bound to the selected owner and the shared cut.")
        self.assertTrue(families.called and all(call.kwargs["at"] == before_birth and call.kwargs["owner_id"] == owner.pk
                                                for call in families.call_args_list),
                        "The Board's vacancy calculation must use the same owner and cut as its names.")

    def test_navigation_distinguishes_read_only_membership_from_departure(self):
        owner, founder, office, _, _, _ = approved_office()
        for state in ("SUSPENDED", "RESTRICTED"):
            with self.subTest(state=state):
                reader = add_member(owner)
                fixture_membership_state(owner, founder, reader, state)
                page = navigation.member_navigation(**scope(owner, reader))
                self.assertIn({"office_definition_uuid": office, "label": "Treasurer", "vacant": True},
                              page["board_offices"], "A read-only member must retain the ordinary Board and office facts.")
        leaver = add_member(owner)
        membership = models.OrganismMembership.objects.get(living_organism=owner, identity=leaver)
        service = organism_membership_service.OrganismMembershipService(authority=authority(), clock=database_now)
        service.leave_organism(test_c1b_departure.DepartureTests().command(membership))
        with CaptureQueriesContext(connection) as refused:
            with self.assertRaises(navigation.NavigationUnavailable, msg="A departed person's old membership must grant no directory."):
                navigation.member_navigation(**scope(owner, leaver))
        self.assertEqual(name_sources(refused), [], "A departed person's read must end before either name source.")
