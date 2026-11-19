"""The selected Circle's introduction on the owner-bound navigation read."""
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from core import models
from src.intevia.services import circle_commands, discussion, member_navigation as navigation
from tests.test_discussion_office_links import discussion_category
from tests.test_law_acts import add_member, ordinary_birth
from tests.test_office_record_services import fixture_membership_state
from tests.test_uc1_record_names import form_generic, name_sources


class CircleDescriptionTests(TransactionTestCase):
    def test_nonparticipant_reads_only_the_selected_birth_purpose(self):
        owner, founder = ordinary_birth()
        reader = add_member(owner)
        scope = {"credential": reader.credential, "organism_uuid": owner.organism_id}
        sibling = form_generic(owner, founder, "Welcome circle")
        purpose = ("We survey the river and publish the results for members. " * 3).strip()
        circle = circle_commands.form(credential=founder.credential, organism_uuid=owner.organism_id,
                                      circle_uuid=uuid4(), kind="GENERIC", purpose=purpose,
                                      name="River circle", request_uuid=uuid4())
        self.assertFalse(models.CircleParticipation.objects.filter(circle=circle, membership__identity=reader).exists(),
                         "This reader must have no place in the selected Circle.")

        def head():
            return models.LivingOrganism.objects.values_list("head_event_id", flat=True).get(pk=owner.pk)

        before = head()
        page = navigation.member_navigation(**scope, circle_uuid=circle.circle_uuid)
        self.assertEqual(page["selected_circle"], {
            "circle_uuid": circle.circle_uuid, "kind": "GENERIC", "full_name": "River circle",
            "short_label": "River circle", "description": {"source": "birth_purpose", "text": purpose}},
            "The selected Circle must return its complete stored purpose and source without birth attribution.")
        self.assertIn(sibling.circle_uuid, {row["circle_uuid"] for row in page["circles"]},
                      "The read must exercise a second visible Circle with a different purpose.")
        self.assertTrue(all("description" not in row for row in page["circles"]),
                        "The directory must keep every Circle's Description off its summary row.")
        self.assertNotIn("selected_circle", navigation.member_navigation(**scope),
                         "The unselected organisation read must preserve its directory-only shape.")
        self.assertEqual(head(), before, "Reading a Circle Description must leave the organisation history head unchanged.")

    def test_board_copy_uses_the_full_name(self):
        owner, founder = ordinary_birth(board_seats=1)
        board = models.Circle.objects.get(parent_organism=owner, kind="GOVERNANCE")
        scope = {"credential": founder.credential, "organism_uuid": owner.organism_id, "circle_uuid": board.circle_uuid}
        resolve = navigation._record_names.full_name_and_short_label

        def names(**args):
            if args.get("circle") is not None and args["circle"].kind == "GOVERNANCE":
                return "Trustee Council", "Council"
            return resolve(**args)

        def expected(full):
            return {"text": f"The {full} is the organisation's Governance Circle. "
                            "Here you can find its offices and the records you can read.", "source": "board_interface"}

        with patch.object(navigation._record_names, "full_name_and_short_label", side_effect=names):
            changed = navigation.member_navigation(**scope)["selected_circle"]
        self.assertEqual(changed["description"], expected("Trustee Council"),
                         "The Board introduction must use the resolved full name, including when the name changes.")
        actual = navigation.member_navigation(**scope)["selected_circle"]
        self.assertEqual(actual["description"], expected("Board of Trustees"),
                         "The installed Board introduction must reproduce the governed sentence and source.")

    def test_missing_and_later_birth_descriptions_are_empty(self):
        owner, founder = ordinary_birth()
        circle = form_generic(owner, founder)
        scope = {"credential": founder.credential, "organism_uuid": owner.organism_id, "circle_uuid": circle.circle_uuid}

        def description():
            return navigation.member_navigation(**scope)["selected_circle"]["description"]

        self.assertIsNone(description(), "A Circle with no stored birth description must have an empty Description.")
        named = form_generic(owner, founder, "River group")
        scope["circle_uuid"] = named.circle_uuid
        row = models.CircleBirthDescription.objects.get(circle=named)
        cut = row.effective_at
        purpose = "Care for the local river"
        self.assertGreater(row.recorded_at, cut, "The source must already be effective but still unrecorded at the earlier cut.")
        with patch.object(navigation, "database_now", return_value=cut):
            self.assertIsNone(description(), "The introduction must wait for its own recording time.")
        with patch.object(navigation, "database_now", return_value=row.recorded_at):
            self.assertEqual(description(), {"source": "birth_purpose", "text": purpose},
                             "The stored introduction must become available at its exact recording time.")

    def test_unreachable_selections_refuse_before_name_or_purpose_lookup(self):
        owner, founder = ordinary_birth()
        other, other_founder = ordinary_birth()
        local = form_generic(owner, founder, "Local group")
        foreign = form_generic(other, other_founder, "Foreign group")
        reader = add_member(owner)
        scope = {"credential": reader.credential, "organism_uuid": owner.organism_id}
        self.assertEqual(navigation.member_navigation(**scope, circle_uuid=local.circle_uuid)["selected_circle"]["circle_uuid"],
                         local.circle_uuid, "The reader must be able to select the local Circle before probation.")

        def refused(target):
            with CaptureQueriesContext(connection) as queries:
                with self.assertRaises(navigation.NavigationUnavailable, msg="An unreachable selection must refuse the entire read.") as denied:
                    navigation.member_navigation(**scope, circle_uuid=target)
                self.assertEqual(str(denied.exception), "This organisation is unavailable to you.",
                                 "An inaccessible, foreign or absent Circle must have the same neutral refusal.")
            self.assertEqual(name_sources(queries), [], "A refused selection must query neither names nor stored birth purposes.")

        refused(foreign.circle_uuid)
        refused(uuid4())
        fixture_membership_state(owner, founder, reader, "PROBATIONARY")
        refused(local.circle_uuid)
        author = {"credential": founder.credential, "organism_uuid": owner.organism_id}
        discussion_category(owner, founder, local.circle_uuid)
        discussion.set_probation_access(**author, enabled=True, request_uuid=uuid4())
        discussion.open_circle_welcome(**author, circle_uuid=local.circle_uuid, request_uuid=uuid4())
        self.assertEqual(navigation.member_navigation(**scope, circle_uuid=local.circle_uuid)["selected_circle"]["description"],
                         {"source": "birth_purpose", "text": "Care for the local river"},
                         "A newly reachable Circle must give the probationary reader its admitted introduction.")


class FunctionalCircleDescriptionTests(SimpleTestCase):
    def test_functional_kinds_have_no_introduction(self):
        for kind in ("APPLICATION", "FINANCE", "OPERATIONS"):
            with self.subTest(kind=kind):
                birth = SimpleNamespace(circle=SimpleNamespace(kind=kind))
                self.assertIsNone(navigation._circle_description(birth, kind.title(), None),
                                  "A functional Circle must have an empty Description without a source lookup.")
