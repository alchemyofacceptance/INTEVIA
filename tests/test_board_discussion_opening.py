"""Part 0, part A. A Board's discussion opens when the Board is activated (HG capture CX, 23 Nov 2026).

Fixtures use the real service routes: an organisation born with Board seats, three
trustees seated (the Board size its governing law states), voting switched on,
then the Board activated. Rows are created per test.
"""
from io import StringIO
from unittest.mock import patch
from uuid import uuid4, uuid5

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from core import models
from src.intevia.services import circle_commands, cq, discussion
from tests.test_law_acts import add_member
from tests.test_uc2_pages import read_counts
from tests import cq_authority_support as boards
from tests.test_uc1_office_page import signed_in


def seated(size=3):
    """An organisation whose Board has its three trustees seated and is still dormant."""
    owner, scope, trustees, board = boards.ordinary(size, board=True)
    cq.change_settings(**scope, predecessor=None, changes={"enabled": True, "clarity_stage": False},
                       request_uuid=uuid4())
    return owner, scope, trustees, board


def activate(scope, board, key=None):
    key = key or uuid4()
    circle_commands.activate(**scope, circle_uuid=board.circle_uuid, request_uuid=key)
    return key


def activate_as_before_part_a(scope, board):
    """Stand in for a Board activated before activation opened its discussion.

    Replaces only circle_commands._open_board_discussion, the one step part A added.
    """
    with patch.object(circle_commands, "_open_board_discussion"):
        return activate(scope, board)


def board_rows(board):
    return discussion._rows("""SELECT b.board_uuid, b.creation_act, a.operation, a.actor_id, a.authority_source
        FROM public.core_discboard b JOIN public.core_discact a ON a.act_uuid=b.creation_act
        WHERE b.circle_id=%s""", [board.pk])


def act_count(board):
    return discussion._rows("""SELECT count(*) AS n FROM public.core_discact a
        JOIN public.core_discboard b USING(board_uuid) WHERE b.circle_id=%s""", [board.pk])[0]["n"]


def reader_args(owner, person, board):
    return {"credential": person.credential, "organism_uuid": owner.organism_id, "circle_uuid": board.circle_uuid}


class BoardDiscussionOpeningTests(TransactionTestCase):
    def test_bd_01_activation_opens_discussion_and_announcements(self):
        """BD-01. Activating a Board opens its discussion, with nothing called by hand."""
        owner, scope, trustees, board = seated()
        activate(scope, board)
        try:
            listing = discussion.board_categories(**reader_args(owner, trustees[1], board), governance_only=True)
        except discussion.DiscussionUnavailable:
            self.fail("A seated trustee could not read the Board's discussion straight after activation. "
                      "Activation must open it; no other step may be needed.")
        codes = {row["functional_code"] for row in listing["categories"]}
        self.assertLessEqual({"DISCUSSION", "ANNOUNCEMENTS"}, codes,
                             "A newly activated Board must have its Discussion and Announcements categories.")

    def test_bd_02_the_opening_is_recorded_once_as_the_activations_own_act(self):
        """BD-02. The opening is one record, keyed from the activation and made by the activator; a replay adds none."""
        owner, scope, trustees, board = seated()
        key = activate(scope, board)
        first = board_rows(board)
        self.assertEqual(len(first), 1, "Activation must record exactly one discussion for the Board.")
        self.assertEqual((first[0]["creation_act"], first[0]["operation"], first[0]["actor_id"]),
                         (uuid5(key, "board"), "PROVISION", trustees[0].pk),
                         "The opening must be the activator's PROVISION act under the activation's derived key.")
        self.assertEqual(activate(scope, board, key), key, "A replayed activation must return its first receipt.")
        discussion.ensure_board(**reader_args(owner, trustees[1], board), request_uuid=uuid4())
        self.assertEqual((board_rows(board), act_count(board)), (first, 1),
                         "A replayed activation and a later request to open must leave the one original record.")

    def test_bd_03_a_dormant_board_has_no_discussion(self):
        """BD-03. Until it is activated, a seated Board has no discussion."""
        owner, scope, trustees, board = seated()
        self.assertEqual(board_rows(board), [], "A dormant Board must have no discussion record.")
        with self.assertRaises(discussion.DiscussionUnavailable,
                               msg="A dormant Board's discussion must be unavailable to its trustees."):
            discussion.board_categories(**reader_args(owner, trustees[1], board), governance_only=True)

    def test_bd_04_an_ordinary_circle_is_unchanged(self):
        """BD-04. Birthing and activating an ordinary Circle opens no discussion for it."""
        owner, scope, _, _ = boards.ordinary(1)
        circle = circle_commands.form(**scope, circle_uuid=uuid4(), kind="GENERIC",
                                      purpose="Part A ordinary Circle witness", request_uuid=uuid4())
        self.assertEqual(board_rows(circle), [],
                         "Activating an ordinary Circle must not open a discussion for it; only a Board's activation does.")

    @override_settings(ALLOWED_HOSTS=["testserver"], INTEVIA_TIMED_EVENTS_WAKE=True)
    def test_bd_05_the_board_page_links_to_its_discussion_only_once_it_exists(self):
        """BD-05. The Board page shows "Board discussion" only once the discussion exists."""
        owner, scope, trustees, board = seated()
        reader = signed_in(trustees[1])
        member = signed_in(add_member(owner))
        kwargs = {"org": owner.organism_id, "circle": board.circle_uuid}
        page, index = reverse("circle_page", kwargs=kwargs), reverse("board_discussion", kwargs=kwargs)
        with patch("src.intevia.services.timed_events.run") as wake:
            before = read_counts()
            self.assertNotContains(member.get(page), index, msg_prefix="Before activation the member has no Board discussion link.")
            self.assertNotContains(reader.get(page), index,
                                   msg_prefix="A dormant Board has no discussion, so its page must not link to one.")
            self.assertEqual(reader.get(index).status_code, 404,
                             "A dormant Board's discussion address must give the unavailable page.")
            self.assertEqual(read_counts(), before,
                             "Reading a dormant Board's page and discussion address must leave every counted record unchanged.")
        self.assertFalse(wake.called, "Reading a dormant Board's page and discussion address must never wake due decisions.")
        activate(scope, board)
        self.assertContains(reader.get(page), index,
                            msg_prefix="Once the Board is activated its page must link to its discussion.")
        self.assertContains(member.get(page), index, msg_prefix="An unseated organisation member must receive the Board discussion link.")
        self.assertEqual(reader.get(index).status_code, 200,
                         "The link on an activated Board's page must open its discussion.")


    def test_bd_10_a_refused_opening_rolls_the_activation_back(self):
        """BD-10. If the discussion cannot open with the activation, the activation is refused in five parts and nothing is recorded."""
        owner, scope, trustees, board = seated()
        with patch.object(discussion, "ensure_board", side_effect=discussion.DiscussionRefused()):
            with self.assertRaises(circle_commands.CircleAccessRefused) as refused:
                activate(scope, board)
        self.assertEqual(str(refused.exception), circle_commands._BOARD_DISCUSSION_REFUSAL,
                         "A refused opening must give the five-part activation refusal.")
        self.assertEqual((models.CircleStateEvent.objects.filter(circle=board, action="ACTIVATE").count(), board_rows(board)),
                         (0, []), "A refused opening must leave neither an activation nor a discussion recorded.")


class ExistingBoardDiscussionStepTests(TransactionTestCase):
    def run_step(self):
        out = StringIO()
        call_command("open_board_discussions", stdout=out)
        return out.getvalue()

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_bd_06_the_step_opens_an_existing_active_boards_discussion_as_its_activator(self):
        """BD-06. The one-off step opens an active Board's discussion as the person who activated it; the page links to it only then."""
        owner, scope, trustees, board = seated()
        key = activate_as_before_part_a(scope, board)
        self.assertEqual(board_rows(board), [], "The fixture must be an active Board with no discussion.")
        reader = signed_in(trustees[1])
        kwargs = {"org": owner.organism_id, "circle": board.circle_uuid}
        page, index = reverse("circle_page", kwargs=kwargs), reverse("board_discussion", kwargs=kwargs)
        self.assertNotContains(reader.get(page), index,
                               msg_prefix="An active Board with no discussion must not link to one; the link follows the discussion, not the Board's state.")
        report = self.run_step()
        self.assertContains(reader.get(page), index,
                            msg_prefix="Once the step opens the discussion, the Board page must link to it.")
        row, = board_rows(board)
        self.assertEqual((row["creation_act"], row["actor_id"]), (uuid5(key, "board"), trustees[0].pk),
                         "The step must record the activator, under the key the activation itself would have used.")
        self.assertIn(f"OPENED organisation {owner.organism_id} Board {board.circle_uuid}.", report)
        self.assertIn("Active Boards without a discussion: 1. Opened: 1. Skipped: 0.", report)

    def test_bd_07_the_step_skips_and_reports_a_board_whose_activator_cannot_sign_in(self):
        """BD-07. A Board whose activator's sign-in is not active is skipped and reported as it is; nothing is written."""
        owner, scope, trustees, board = seated()
        activate_as_before_part_a(scope, board)
        # Django's own switch for a sign-in that is no longer active; no INTEVIA route sets it yet.
        get_user_model().objects.filter(pk=trustees[0].credential.pk).update(is_active=False)
        report = self.run_step()
        self.assertEqual(board_rows(board), [], "A skipped Board must have no discussion written for it.")
        self.assertIn(f"SKIPPED organisation {owner.organism_id} Board {board.circle_uuid}: "
                      "the discussion could not be opened as the person who activated this Board. Nothing was written. "
                      "Ask the Organisation Coordinator to check that person's sign-in and Board seat before running this again.",
                      report, "The skip report must state only what the command knows, not a cause it did not check.")
        self.assertIn("Active Boards without a discussion: 1. Opened: 0. Skipped: 1.", report)

    def test_bd_09_the_step_skips_a_board_whose_activator_left_their_board_place(self):
        """BD-09. An activator who left their Board place through the governed route, sign-in still working, is skipped; nothing is written."""
        owner, scope, trustees, board = seated()
        activate_as_before_part_a(scope, board)
        place = models.CircleParticipation.objects.get(circle=board, membership__identity=trustees[0])
        circle_commands.leave(**scope, circle_uuid=board.circle_uuid, participation_uuid=place.participation_uuid,
                              request_uuid=uuid4(), confirmed=True)
        self.assertTrue(get_user_model().objects.get(pk=trustees[0].credential.pk).is_active,
                        "The fixture must keep the former trustee's sign-in working.")
        before = read_counts()
        report = self.run_step()
        self.assertEqual((board_rows(board), read_counts()), ([], before),
                         "A Board whose activator no longer holds a place on it must have nothing written for it.")
        self.assertIn(f"SKIPPED organisation {owner.organism_id} Board {board.circle_uuid}:", report)
        self.assertIn("Active Boards without a discussion: 1. Opened: 0. Skipped: 1.", report)

    def test_bd_08_running_the_step_again_changes_nothing(self):
        """BD-08. A second run finds nothing to open and writes nothing."""
        owner, scope, trustees, board = seated()
        activate_as_before_part_a(scope, board)
        self.run_step()
        first = board_rows(board)
        report = self.run_step()
        self.assertEqual((board_rows(board), act_count(board)), (first, 1),
                         "A second run must leave the one original record.")
        self.assertIn("Active Boards without a discussion: 0. Opened: 0. Skipped: 0.", report)
