"""Scene 4. Board and Topic reads with the reader's real, current standing."""
import re
from dataclasses import replace
from importlib import import_module
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from core import discussion_forms
from core.migrations._clock_conversion import ClockConversionRefused, swap
from core.models import Circle, CircleStateEvent, ContextualRoleAssignment, LivingOrganismEvent, OrganismMembership
from src.intevia.services import circle_commands, cq, cq_reads, discussion, discussion_posts, elections
from tests import committee_support, election_support
from tests import test_cq_clarity as clarity_support
from tests import cq_authority_support as boards
from tests import test_discussion_posts as post_support
from tests import test_election_mandates as election_mandates
from tests import test_law_acts as laws
from tests.s015_0021_support import database_now
from tests.test_discussion_office_links import office_basis, record_office
from tests.test_f0_ui import Markup
from tests.test_office_record_services import fixture_membership_state
from tests.test_uc1_office_page import assert_private, signed_in
from verification.isolation.state import NON_CORE_TRIGGER_DELTAS


# These four HTTP witnesses use a bound session. Password work factors are outside their claims.
FIXTURE_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def activate_trustees(scope, board, *, clarity=False):
    cq.change_settings(**scope, predecessor=None, changes={"enabled": True, "clarity_stage": clarity}, request_uuid=uuid4())
    circle_commands.activate(**scope, circle_uuid=board.circle_uuid, request_uuid=uuid4())


def seated_board(*, clarity=False, size=3, chair_indices=(), coordinator_index=1):
    owner, scope, trustees, board = boards.ordinary(size, board=True)
    founder, coordinator = trustees[0], trustees[coordinator_index]
    boards.appoint_board_chairs(owner, founder, *(trustees[i] for i in chair_indices))
    activate_trustees(scope, board, clarity=clarity)
    boards.appoint_board_coordinator(owner, board, founder, coordinator)
    return owner, board, *trustees


def path(owner, board, thread=None):
    kwargs = {"org": owner.organism_id, "circle": board.circle_uuid}
    if thread is None:
        return reverse("board_discussion", kwargs=kwargs)
    return reverse("discussion_topic", kwargs={**kwargs, "thread": thread})


def preparation_receipts(thread):
    return discussion._rows("""SELECT actor_id, authority_source FROM public.core_cqact
        WHERE thread_uuid=%s AND operation='PREPARED' ORDER BY ordinal""", [thread])


def read_counts():
    return discussion._rows("""SELECT (SELECT count(*) FROM public.core_discboard) AS boards,
        (SELECT count(*) FROM public.core_disccategory) AS categories,
        (SELECT count(*) FROM public.core_discthread) AS threads,
        (SELECT count(*) FROM public.core_discpost) AS posts,
        (SELECT count(*) FROM public.core_livingorganismevent) AS events""")[0]


@override_settings(ALLOWED_HOSTS=["testserver"], INTEVIA_TIMED_EVENTS_WAKE=True)
class BoardVotePageTests(TransactionTestCase):
    @override_settings(PASSWORD_HASHERS=FIXTURE_HASHERS)
    def test_command_list_is_the_current_readers(self):
        owner, board, _, chair_holder, trustee = seated_board(chair_indices=(1,), coordinator_index=2)
        support = post_support.DiscussionPostTests()
        args = support.board(owner, trustee, board) | {"credential": trustee.credential}
        topic = support.start(args, topic_type="SUGGESTION", markdown="Approve this payment.")
        url = path(owner, board, topic["thread"])
        author, chair = signed_in(trustee), signed_in(chair_holder)
        page = author.get(url)
        self.assertContains(page, "Put to a Board vote", count=1)
        forward, submitted = command_form(page, "vote_command")
        for attempt in range(2):
            response = author.post(forward, submitted)
            self.assertEqual((response.status_code, response.get("Location")), (303, url + "#vote"),
                             "Putting forward the same signed form must return to its vote.")
        ordinary = author.get(url)
        self.assertContains(ordinary, "Preparation")
        self.assertContains(ordinary, "Deadline")
        self.assertContains(ordinary, "Mark as prepared", count=1, msg_prefix="The opening author must see the preparation command.")
        self.assertContains(ordinary, "Discuss the Suggestion here first. Its author or the Board Chair can mark it prepared "
                            "for Clarity or voting; if the preparation deadline passes first, it moves on as unprepared.", count=1)
        eligible = chair.get(url)
        self.assertContains(eligible, "Mark as prepared", count=1)
        eligible_markup = Markup(eligible.content.decode())
        self.assertEqual(eligible_markup.select("input", name="vote_command"), [],
                         "The initial page must not issue a PREPARED submission.")
        link, = [a["attrs"]["href"] for a in eligible_markup.select("a") if a["text"] == "Mark as prepared"]
        self.assertContains(author.get(link), "Proceed to vote", count=1, msg_prefix="The author can open the same confirmation.")
        preview = chair.get(link, HTTP_SEC_PURPOSE="prefetch")
        self.assertContains(preview, "Voting opens now, on this wording.")
        self.assertContains(preview, "After this, the wording can no longer be edited.")
        self.assertContains(preview, "Proceed to vote", count=1)
        cancel, = [a["attrs"]["href"] for a in Markup(preview.content.decode()).select("a") if a["text"] == "Cancel"]
        self.assertEqual(cancel, url + "#vote")
        self.assertNotContains(chair.get(cancel), "Proceed to vote")
        seen = discussion._rows("SELECT operation FROM public.core_cqact WHERE thread_uuid=%s ORDER BY ordinal",
                                [topic["thread"]])
        self.assertEqual(seen, [{"operation": "INITIATE"}], "Displaying or cancelling the prompt must add no act.")
        prepare, prepared = command_form(chair.get(link), "vote_command")
        for attempt in range(2):
            self.assertEqual(chair.post(prepare, prepared).status_code, 303,
                             "The Chair can submit or replay the preparation form.")
        acts = discussion._rows("""SELECT operation FROM public.core_cqact WHERE thread_uuid=%s
            AND operation IN ('INITIATE','PREPARED') ORDER BY ordinal""", [topic["thread"]])
        self.assertEqual([row["operation"] for row in acts], ["INITIATE", "PREPARED"],
                         "Each genuine command and its replay must produce one act.")
        assignment = ContextualRoleAssignment.objects.get(role_definition__living_organism=owner,
                                                          membership__identity=chair_holder, role_definition__code="CHAIR")
        authority = preparation_receipts(topic["thread"])
        self.assertEqual(authority, [{"actor_id": chair_holder.pk, "authority_source": {
            "kind": "OFFICE", "reference": str(assignment.assignment_uuid), "route": "BOARD_CHAIR",
            "organism": str(owner.organism_id), "circle": str(board.circle_uuid)}}],
            "The other trustee prepared in their actual Board office, with one receipt after replay.")
        direct_vote = chair.get(url)
        self.assertNotContains(direct_vote, "Mark as prepared", msg_prefix="Preparation has ended.")
        self.assertContains(direct_vote, "Voting open · Closes ", count=1, msg_prefix="This direct ballot is open.")
        self.assertNotContains(direct_vote, "so voting opened on this wording", msg_prefix="A direct vote never passed through Clarity.")

    @override_settings(PASSWORD_HASHERS=FIXTURE_HASHERS)
    def test_clarity_preview_matches_recorded_transition(self):
        owner, board, _, coordinator, trustee = seated_board(clarity=True)
        support = post_support.DiscussionPostTests()
        args = support.board(owner, coordinator, board) | {"credential": trustee.credential}
        topic = support.start(args, topic_type="SUGGESTION", markdown="Review this payment.")
        scope = support.read_args(args) | {"thread_uuid": topic["thread"]}
        cq.put_forward(**scope, version_uuid=topic["version"], request_uuid=uuid4())
        url = path(owner, board, topic["thread"])
        author = signed_in(trustee)
        initial = author.get(url + "?prepare=1")
        self.assertContains(initial, "Board members will be asked to confirm they are clear on this wording. They have 7 days.")
        self.assertContains(initial, "Proceed to Clarity", count=1)
        endpoint, fields = command_form(initial, "vote_command")
        discussion_posts.edit_post(**args, thread_uuid=topic["thread"], post_uuid=topic["post"],
                                   predecessor=topic["version"], request_uuid=uuid4(), markdown="Review the revised terms.")
        stale = author.post(endpoint, fields)
        self.assertEqual(stale.status_code, 409, "New wording must require a new confirmation.")
        self.assertContains(stale, "The wording or the next step has changed", status_code=409)
        self.assertEqual(cq.phase(**scope)["phase"], "PREPARATION", "A stale confirmation must roll back its act.")
        endpoint, current = command_form(author.get(url + "?prepare=1"), "vote_command")
        self.assertEqual(author.post(endpoint, current).status_code, 303)
        self.assertEqual(cq.phase(**scope)["phase"], "CLARITY", "Proceed must actually begin the stage the reader confirmed.")
        self.assertEqual(preparation_receipts(topic["thread"]), [{"actor_id": trustee.pk, "authority_source": {
            "kind": "MEMBER", "reference": trustee.pk, "route": "PREPARED"}}],
            "The author alone must own the one preparation receipt through membership.")

    @override_settings(PASSWORD_HASHERS=FIXTURE_HASHERS)
    def test_changed_electorate_requires_an_updated_prompt(self):
        owner, board, founder, coordinator, trustee = seated_board(chair_indices=(1,))
        support = post_support.DiscussionPostTests()
        args = support.board(owner, coordinator, board) | {"credential": trustee.credential}
        topic = support.start(args, topic_type="SUGGESTION", markdown="Approve the payment.")
        scope = support.read_args(args) | {"thread_uuid": topic["thread"]}
        cq.put_forward(**scope, version_uuid=topic["version"], request_uuid=uuid4())
        url = path(owner, board, topic["thread"]) + "?prepare=1"
        chair = signed_in(coordinator)
        before = chair.get(url)
        self.assertContains(before, "Proceed to vote")
        endpoint, earlier = command_form(before, "vote_command")
        cq.declare_conflict(**(scope | {"credential": founder.credential}), request_uuid=uuid4(),
                            basis="I supplied the estimate.")
        self.assertEqual(chair.post(endpoint, earlier).status_code, 409)
        self.assertEqual(cq.phase(**scope)["phase"], "PREPARATION", "An obsolete promise to open voting must record no act.")
        after = chair.get(url)
        self.assertContains(after, "Fewer than three eligible voters remained.")
        self.assertContains(after, "Proceed without a vote", count=1)
        endpoint, current = command_form(after, "vote_command")
        self.assertEqual(chair.post(endpoint, current).status_code, 303)
        status = cq_reads.vote_status(**scope)
        self.assertIsNone(status["ballot"])
        self.assertEqual(status["result"]["count"]["kind"], "NO_DECISION_FEWER_THAN_THREE")

    @override_settings(PASSWORD_HASHERS=FIXTURE_HASHERS)
    def test_other_trustee_cannot_preview_or_submit_preparation(self):
        owner, board, author, chair, other = seated_board(clarity=True, chair_indices=(1,), coordinator_index=2)
        support = post_support.DiscussionPostTests()
        args = support.board(owner, other, board) | {"credential": author.credential}
        topic = support.start(args, topic_type="SUGGESTION", markdown="Review this purchase.")
        thread = topic["thread"]
        scope = support.read_args(args) | {"thread_uuid": thread}
        cq.put_forward(**scope, version_uuid=topic["version"], request_uuid=uuid4())
        url = path(owner, board, thread)
        client = signed_in(other)
        page = client.get(url)
        self.assertContains(page, "Wording to be decided", msg_prefix="A refused command must leave the readable vote on screen.")
        preview = cq.board_topic_vote(**scope, preview_prepared=True)["confirmation"]
        # Sign for the other trustee's own real session. A token copied from the
        # author would test session binding, and would never reach cq_source.
        token = discussion_forms.issue_form(page.wsgi_request, "PREPARED", owner.organism_id, board.circle_uuid,
                                            thread=thread, confirmation=preview)
        endpoint = reverse("discussion_prepare", args=(owner.organism_id, board.circle_uuid, thread))
        csrf = client.cookies["csrftoken"].value
        client.handler.enforce_csrf_checks = True
        response = client.post(endpoint, {"vote_command": token, "csrfmiddlewaretoken": csrf})
        self.assertContains(response, "Only the author or the current Board Chair can mark this Suggestion as prepared.",
                            status_code=409, msg_prefix="The real writer must refuse another trustee for lack of authority.")
        self.assertContains(response, "This request recorded no new vote action.", status_code=409)
        self.assertNotContains(page, "Mark as prepared")
        self.assertNotContains(client.get(url + "?prepare=1"), "Proceed to Clarity")
        self.assertEqual(discussion._rows("SELECT operation FROM public.core_cqact WHERE thread_uuid=%s ORDER BY ordinal",
                                         [thread]), [{"operation": "INITIATE"}], "The refused POST must leave the vote untouched.")

    def test_organisation_coordinator_cannot_prepare_another_authors_suggestion(self):
        lo, coordinator = laws.ordinary_birth()
        person = laws.add_member(lo)
        cq.change_settings(organism_uuid=lo.organism_id, credential=coordinator.credential, predecessor=None,
                           changes={"enabled": True, "clarity_stage": True}, request_uuid=uuid4())
        support = post_support.DiscussionPostTests()
        args = support.board(lo, coordinator) | {"credential": person.credential}
        topic = support.start(args, topic_type="SUGGESTION")
        scope = support.read_args(args) | {"thread_uuid": topic["thread"]}
        cq.put_forward(**scope, version_uuid=topic["version"], request_uuid=uuid4())
        with self.assertRaises(cq.CQRefused, msg="The organisation-wide board has no Coordinator fallback.") as refused:
            cq.mark_prepared(**(scope | {"credential": coordinator.credential}), request_uuid=uuid4())
        self.assertEqual(refused.exception.code, "CQ_PREPARE_SUGGESTION")
        self.assertEqual(preparation_receipts(topic["thread"]), [], "Refusal must leave the author free to prepare.")
        cq.mark_prepared(**scope, request_uuid=uuid4())
        self.assertEqual(preparation_receipts(topic["thread"]), [{"actor_id": person.pk, "authority_source": {
            "kind": "MEMBER", "reference": person.pk, "route": "PREPARED"}}], "The original author may prepare here.")

    def test_election_author_needs_the_responsible_office(self):
        lo, coordinator = laws.ordinary_birth(offering_choice="COMPLIMENTARY_BETA", board_seats=2,
                                             elected=(1,), board_minimum=None)
        article = election_support.ARTICLE
        citation = election_support.provision(coordinator, lo, {article: election_support.seat()},
                                              board_minimum=laws.FIXTURE_BOARD_MINIMUM)
        local = {"credential": coordinator.credential, "organism_uuid": lo.organism_id}
        cq.change_settings(**local, predecessor=None, changes={"enabled": True, "clarity_stage": False,
                                                             "coordinator_initiation": True}, request_uuid=uuid4())
        author = laws.add_member(lo)
        nominee = laws.add_member(lo)
        support = post_support.DiscussionPostTests()
        args = support.board(lo, coordinator) | {"credential": author.credential}
        terms = dict(**citation, provision=article, place="trustee-1")
        proposal = elections.attachment(organism_uuid=lo.organism_id, **terms)
        topic = support.start(args, topic_type="SUGGESTION", markdown=proposal["text"])
        author_scope = support.read_args(args) | {"thread_uuid": topic["thread"]}
        office_scope = author_scope | {"credential": coordinator.credential}
        elections.call(**office_scope, **terms, version_uuid=topic["version"], request_uuid=uuid4())
        election_support.nominate(author_scope, nominee)
        with self.assertRaises(cq.CQRefused, msg="Authorship alone must never prepare this election slate.") as refused:
            cq.mark_prepared(**author_scope, request_uuid=uuid4())
        self.assertEqual(refused.exception.code, "CQ_PREPARE_OFFICE")
        self.assertEqual(preparation_receipts(topic["thread"]), [], "The refused election preparation must leave no receipt.")
        cq.mark_prepared(**office_scope, request_uuid=uuid4())
        receipt, = preparation_receipts(topic["thread"])
        self.assertEqual((receipt["actor_id"], receipt["authority_source"]["kind"]), (coordinator.pk, "OFFICE"),
                         "The responsible election officer must be able to prepare the same slate.")

    def test_0115_preparation_body_and_authority_round_trip(self):
        owner, board, _, chair, author = seated_board(clarity=True)
        support = post_support.DiscussionPostTests()
        args = support.board(owner, chair, board) | {"credential": author.credential}
        topic = support.start(args, topic_type="SUGGESTION")
        scope = support.read_args(args) | {"thread_uuid": topic["thread"]}
        cq.put_forward(**scope, version_uuid=topic["version"], request_uuid=uuid4())

        def installed():
            row, = discussion._rows("""SELECT octet_length(convert_to(prosrc,'UTF8')) AS bytes,
                encode(sha256(convert_to(prosrc,'UTF8')),'hex') AS digest FROM pg_proc
                WHERE oid=to_regprocedure('public.cq_source(bigint,uuid,uuid,text,bigint,timestamptz)')""")
            return row["bytes"], row["digest"], cq.board_topic_vote(**scope).get("operation")

        original = (4582, "f92eef4fe708e9f97984aee177b13f0cb576a3432d85b1e6a971fe8acf3cfe5b", None)
        revised = (5381, "2dcb881609dd82fe12e3efcbb0de86e0232f410e247ef18b1a11e4c0b9e9e4bc", "PREPARED")
        executor = MigrationExecutor(connection)
        edge = executor.loader.get_migration("core", "0115_suggestion_preparer")
        before = executor.loader.project_state(edge.dependencies)
        chair_head = (7015, "3dcef46dadf792068cafdd409f237e16dd74d4c1ca764c5a0bfdd944ff33f84f", "PREPARED")
        chair_edge = executor.loader.get_migration("core", "0117_board_chair_commands")
        chair_base = executor.loader.project_state(chair_edge.dependencies)
        author_edge = executor.loader.get_migration("core", "0118_author_clarity_and_conflict")
        author_base = executor.loader.project_state(author_edge.dependencies)
        received = installed()
        with transaction.atomic():
            executor.unapply_migration(author_base, author_edge)
            self.assertEqual(installed(), chair_head, "At 0117 the Chair rule must still admit the author.")
            executor.unapply_migration(chair_base, chair_edge)
            self.assertEqual(installed(), revised, "Reversing 0117 must restore the complete 0115 function.")
            executor.unapply_migration(before, edge)
            self.assertEqual(installed(), original, "Reversing must restore the complete office-only predecessor.")
            executor.apply_migration(before, edge)
            self.assertEqual(installed(), revised, "Forward must install the complete new body and admit the author.")
            executor.unapply_migration(before, edge)
            self.assertEqual(installed(), original, "A second reversal must restore the exact old function.")
            executor.apply_migration(before, edge)
            self.assertEqual(installed(), revised, "Forward again must restore the identical author rule.")
            executor.apply_migration(chair_base, chair_edge)
            self.assertEqual(installed(), chair_head, "Forward again must restore every byte of the Chair rule.")
            transaction.set_rollback(True)
        self.assertEqual(installed(), received, "Rollback must leave the received 0118 head intact.")

    test_0115_preparation_body_and_authority_round_trip.ISOLATION_FULL_CAPTURE = True

    def assert_no_vote_receipt(self, chair, preview, scope, expected):
        self.assertContains(preview, "Proceed without a vote", count=1,
                            msg_prefix="The confirmation must offer the no-vote action.")
        endpoint, fields = command_form(preview, "vote_command")
        self.assertEqual(chair.post(endpoint, fields).status_code, 303,
                         "Proceed must accept the signed preview and persist its promised outcome.")
        acts = discussion._rows(
            """SELECT operation, facts#>>'{count,kind}' AS reason,
                      request->>'trigger_act' AS parent, act_uuid::text AS key
               FROM public.core_cqact WHERE thread_uuid=%s ORDER BY ordinal""", [scope["thread_uuid"]])
        self.assertEqual([act["operation"] for act in acts], ["INITIATE", "PREPARED", "NO_BALLOT"],
                         "Proceed must record one preparation and one no-ballot act.")
        self.assertEqual((acts[-1]["parent"], acts[-1]["reason"]), (acts[1]["key"], expected),
                         "The preparation receipt must own the no-ballot act with the reason shown.")

    @override_settings(PASSWORD_HASHERS=FIXTURE_HASHERS)
    def test_restricted_motion_preview_matches_no_vote_act(self):
        owner, board, _, coordinator, trustee = seated_board(chair_indices=(1,))
        support = post_support.DiscussionPostTests()
        board_scope = support.read_args(support.board(owner, coordinator, board))
        category = uuid4()
        discussion.create_category(**board_scope, category_uuid=category, name="Payments", request_uuid=uuid4())
        author = board_scope | {"credential": trustee.credential, "category_uuid": category}
        topic = support.start(author, topic_type="SUGGESTION", markdown="Approve this invoice.")
        scope = support.read_args(author) | {"thread_uuid": topic["thread"]}
        cq.put_forward(**scope, version_uuid=topic["version"], request_uuid=uuid4())
        chair = signed_in(coordinator)
        url = path(owner, board, topic["thread"]) + "?prepare=1"
        self.assertContains(chair.get(url), "Proceed to vote", count=1,
                            msg_prefix="Members can read the proposed wording before the category changes.")
        category_scope = board_scope | {"category_uuid": category}
        current = discussion.category_history(**category_scope)[-1]["version_uuid"]
        discussion.revise_category(**category_scope, predecessor=current, name="Payments", description="",
                                   order=0, access="COORDINATORS", request_uuid=uuid4())
        preview = chair.get(url)
        self.assertContains(preview, "The motion was unavailable to its full electorate.", count=1,
                            msg_prefix="Restricting the category must explain why the vote cannot open.")
        self.assert_no_vote_receipt(chair, preview, scope, "NO_DECISION_MOTION_UNAVAILABLE")

    @override_settings(PASSWORD_HASHERS=FIXTURE_HASHERS)
    def test_retired_vote_provision_preview_matches_no_vote_act(self):
        # Adopt, cite and retire while the founder still holds sole-member LAW authority.
        # The later Board's first preparation must still check its VOTE-stage citation.
        owner, founder = laws.ordinary_birth(offering_choice="FREE", board_seats=3)
        board = Circle.objects.get(parent_organism=owner, kind="GOVERNANCE")
        document = laws.Document(founder, content="[#1] Board motions require three participants.")
        article = laws.adoption(owner, document, "ARTICLE")
        service = laws.law.OrganismLawService()
        self.assertEqual(service.adopt_law(laws.command(founder, article)).status, "ADOPT",
                         "The voting provision must first become law.")
        citation = {"binding": str(article.proposal), "version": document.first.pk, "provision": "1",
                    "body": str(board.circle_uuid), "decision": "ALL_DECISIONS", "stage": "VOTE",
                    "denominator": "Eligible Board trustees", "setting": "CQPT",
                    "value": {"quorum_form": "ABSOLUTE", "quorum_value": 3, "waive_minimum": False}}
        local = {"credential": founder.credential, "organism_uuid": owner.organism_id}
        cq.change_settings(**local, predecessor=None, request_uuid=uuid4(),
                           changes={"enabled": True, "clarity_stage": False, "provisions": [citation]})
        retirement = replace(article, proposal=uuid4(), predecessor=article.proposal, operation="RETIRE", intent="RETIRE")
        self.assertEqual(service.adopt_law(laws.command(founder, retirement)).status, "RETIRE",
                         "The cited voting provision must genuinely leave force.")
        coordinator = boards.seat_trustee(owner, founder, board, 1)
        trustee = boards.seat_trustee(owner, founder, board, 2)
        circle_commands.activate(**local, circle_uuid=board.circle_uuid, request_uuid=uuid4())
        boards.appoint_board_coordinator(owner, board, founder, coordinator)
        support = post_support.DiscussionPostTests()
        author = support.board(owner, coordinator, board) | {"credential": trustee.credential}
        topic = support.start(author, topic_type="SUGGESTION", markdown="Approve this invoice.")
        scope = support.read_args(author) | {"thread_uuid": topic["thread"]}
        cq.put_forward(**scope, version_uuid=topic["version"], request_uuid=uuid4())
        chair = signed_in(trustee)
        preview = chair.get(path(owner, board, topic["thread"]) + "?prepare=1")
        self.assertContains(preview, "A cited constitutional provision was no longer in force.", count=1,
                            msg_prefix="The prompt must name the ended provision before the Board proceeds.")
        self.assert_no_vote_receipt(chair, preview, scope, "NO_DECISION_PROVISION_ENDED")

    @override_settings(PASSWORD_HASHERS=FIXTURE_HASHERS)
    def test_motion_text_is_scoped_to_version_and_reader(self):
        owner, board, _, coordinator, trustee = seated_board()
        support = post_support.DiscussionPostTests()
        args = support.board(owner, coordinator, board) | {"credential": trustee.credential}
        topic = support.start(args, topic_type="SUGGESTION", markdown="Approve one invoice.")
        scope = support.read_args(args) | {"thread_uuid": topic["thread"]}
        cq.put_forward(**scope, version_uuid=topic["version"], request_uuid=uuid4())
        discussion_posts.edit_post(**args, thread_uuid=topic["thread"], post_uuid=topic["post"],
                                   predecessor=topic["version"], request_uuid=uuid4(), markdown="Revised opening.")
        url = path(owner, board, topic["thread"])
        page = Markup(signed_in(trustee).get(url).content.decode())
        self.assertEqual([node["text"] for node in page.select("div", classes=("motion-wording",))], ["Revised opening."],
                         "The vote must follow an edit during Preparation.")
        self.assertIn("Wording to be decided · Version 2", [n["text"] for n in page.select("h3")])
        self.assertIn("Revised opening.", [n["text"] for n in page.select("div", classes=("post-text",))])
        cq.mark_prepared(**scope, request_uuid=uuid4())
        outsider = laws.add_member(owner)
        other_scope = scope | {"credential": outsider.credential}
        self.assertEqual(cq.phase(**other_scope)["phase"], "VOTING", "This member may read the released vote phase.")
        self.assertIsNone(cq.board_topic_vote(**other_scope), "A result-only member must receive no motion text or command.")
        self.assertEqual(signed_in(outsider).get(url).status_code, 404, "Result access must never open the private Topic.")

    @override_settings(PASSWORD_HASHERS=FIXTURE_HASHERS)
    def test_board_result_occupies_its_recorded_post_number(self):
        owner, board, founder, coordinator, trustee = seated_board()
        support = post_support.DiscussionPostTests()
        args = support.board(owner, coordinator, board)
        topic = support.start(args, topic_type="SUGGESTION", title="Private opening title", markdown="One payment.")
        scope = support.read_args(args) | {"thread_uuid": topic["thread"]}
        cq.put_forward(**scope, request_uuid=uuid4(), version_uuid=topic["version"])
        discussion_posts.edit_post(**args, thread_uuid=topic["thread"], post_uuid=topic["post"],
                                   predecessor=topic["version"], request_uuid=uuid4(), markdown="Revised payment terms.")
        cq.mark_prepared(**scope, request_uuid=uuid4())
        ballot = cq_reads.vote_status(**scope)["ballot"]
        for voter in (founder, coordinator, trustee):
            cq.answer(**(scope | {"credential": voter.credential}), ballot_uuid=ballot, choice="QUALIFIED", request_uuid=uuid4())
        receipt = cq_reads.vote_status(**scope, ballot_uuid=ballot)["result_uuid"]
        support.reply(args, topic, markdown="Thank you.")
        client = signed_in(trustee)
        url = path(owner, board, topic["thread"])
        result_url = reverse("discussion_result", args=(owner.organism_id, board.circle_uuid, topic["thread"], receipt))
        page = Markup(client.get(url).content.decode())
        self.assertEqual([n["text"] for n in page.select("div", classes=("motion-wording",))], ["Revised payment terms."],
                         "The result must display the version fixed by the ballot, including the pre-ballot edit.")
        self.assertIn("Wording decided · Version 2", [n["text"] for n in page.select("h3")])
        entries = [a for a in page.select("article") if page.select("h3", within=a) and page.select("h3", within=a)[0]["text"].startswith("Post ")]
        self.assertEqual([page.select("h3", within=a)[0]["text"] for a in entries], ["Post 2 · Vote result", "Post 3"],
                         "The result must occupy post 2 and the following reply must remain post 3.")
        card = entries[0]
        self.assertIn("Board of Trustees", card["text"])
        self.assertIn("Carried.", card["text"])
        stored = discussion._rows("""SELECT effective_at, (facts->>'closed_at')::timestamptz AS closed_at
            FROM public.core_cqact WHERE act_uuid=%s""", [receipt])[0]
        effective = stored["effective_at"]
        from django.utils.dateparse import parse_datetime
        from datetime import timezone
        decided = effective.astimezone(timezone.utc)
        vote_section, = page.select("section", id="vote")
        # Capture Z, Q2 (C1-403): the line names when voting closed, and the recording only in another minute.
        closed = stored["closed_at"].astimezone(timezone.utc)
        line = f"Decided · Carried · Voting closed {closed.day} {closed:%B %Y, %H:%M} UTC"
        if decided.replace(second=0, microsecond=0) != closed.replace(second=0, microsecond=0):
            line += f" · Result recorded {decided.day} {decided:%B %Y, %H:%M} UTC"
        self.assertEqual(page.select("p", within=vote_section)[0]["text"], line)
        self.assertIn("Took effect", card["text"], "The Topic's result card names its time as the result page does (capture AC, Q7).")
        self.assertNotIn("Effective", card["text"], "The card's old label must be gone.")
        self.assertEqual(parse_datetime(page.select("time", within=card)[0]["attrs"]["datetime"]), effective,
                         "The result time must equal the stored act’s effective time.")
        self.assertEqual([a["text"] for a in page.select("a", within=card, href=result_url)], ["View vote result"])
        self.assertFalse(any("Post 2" in option["text"] for option in page.select("option")), "A result cannot be quoted as an editable post version.")
        observer = signed_in(laws.add_member(owner))
        permitted = observer.get(result_url)
        # Capture AT2 (the posts box, part PB-6c): the result page's Outcome row reads the outcome word only.
        self.assertContains(permitted, "<dd>Carried</dd>", msg_prefix="A result-only member must be able to open the result itself.")
        self.assertNotContains(permitted, "result-topic", msg_prefix="A member who may not open the Topic is not shown its title (capture AW1).")
        self.assertContains(permitted, "Revised payment terms.",
                            msg_prefix="A result-only member must see the wording that was decided (HG capture Z, Q1).")
        assert_private(self, permitted)
        for withheld in ("Private opening title", "One payment.", "Back to Topic", "Thank you."):
            self.assertNotContains(permitted, withheld,
                                   msg_prefix="The result page must withhold the Topic's title, its earlier wording and the way back.")
        self.assertEqual(observer.get(url).status_code, 404)
        self.assertEqual(observer.get(reverse("discussion_result", args=(owner.organism_id, board.circle_uuid, uuid4(), receipt))).status_code, 404)

    def test_board_lists_only_visible_topics(self):
        owner, board, _, coordinator, trustee = seated_board()
        reader, steward = signed_in(trustee), signed_in(coordinator)
        index = path(owner, board)
        parent = reverse("circle_page", kwargs={"org": owner.organism_id, "circle": board.circle_uuid})
        support = post_support.DiscussionPostTests()
        with patch("src.intevia.services.timed_events.run") as wake:
            before = read_counts()
            empty = reader.get(index)
            self.assertContains(empty, "No Topics to show here.", msg_prefix="An activated Board's discussion must open with an empty category.")
            self.assertContains(reader.get(parent), index, msg_prefix="A seated reader must be able to enter the activated Board's discussion.")
            self.assertEqual(read_counts(), before, "Reading the activated Board's discussion must leave every counted record unchanged.")
            args = support.board(owner, coordinator, board)
            first = support.start(args, title="Pinned public question")
            second = support.start(args, title="Ordinary public question")
            concealed = support.start(args, title="Withheld opening")
            support.moderate(args, first, "PIN")
            support.hide(args, concealed, True, as_coordinator=True)
            categories = discussion.board_categories(**support.read_args(args))["categories"]
            private = next(cat for cat in categories if cat["functional_code"] == "COORDINATION")
            private_topic = support.start(args | {"category_uuid": private["category_uuid"]}, title="Private coordination")
            before = read_counts()
            response = reader.get(index)
            assert_private(self, response)
            page = Markup(response.content.decode())
            card = next(c for c in page.select("article") if page.select("h3", within=c)[0]["text"] == "Discussion")
            self.assertEqual([n["text"] for n in page.select("p", within=card, classes=("meta",))], ["2 Topics"],
                             "The Discussion count must include the two released openings only.")
            topic_list, = page.select("ul", within=card, classes=("discussion-topics",))
            self.assertEqual([(a["text"], a["attrs"]["href"]) for a in page.select("a", within=topic_list)],
                             [("Pinned public question", path(owner, board, first["thread"])),
                              ("Ordinary public question", path(owner, board, second["thread"]))],
                             "The pinned Topic must precede the newer opening, each at its own URL.")
            for withheld in ("Coordination", "Withheld opening", "Private coordination", str(concealed["thread"]), str(private_topic["thread"])):
                self.assertNotContains(response, withheld, msg_prefix="The trustee's page must omit every withheld Topic fragment.")
            permitted = steward.get(index)
            self.assertContains(permitted, "Withheld opening", msg_prefix="The moderating Coordinator must see the hidden opening.")
            self.assertContains(permitted, "Private coordination", msg_prefix="The Coordinator must see the private category's Topic.")
            self.assertEqual(read_counts(), before, "Repeated reads with different grants must create no forum or lineage rows.")
            self.assertFalse(wake.called, "The Board index and its parent page must never wake due decisions.")

    def test_topic_permission_and_unavailable_reason(self):
        owner, board, founder, coordinator, trustee = seated_board()
        reader = signed_in(trustee)
        support = post_support.DiscussionPostTests()
        args = support.board(owner, coordinator, board)
        topic = support.start(args, title="Repair authority", markdown="First wording.")
        url = path(owner, board, topic["thread"])
        reply = support.reply(args, topic, markdown="Read this quotation.", quote_version=topic["version"])
        discussion_posts.edit_post(**args, thread_uuid=topic["thread"], post_uuid=topic["post"],
                                   predecessor=topic["version"], request_uuid=uuid4(), markdown="Second wording.")
        before = read_counts()
        with patch("src.intevia.services.timed_events.run") as wake:
            response = reader.get(url)
            self.assertEqual(response.status_code, 200, "The seated trustee must be able to read the open Topic.")
            assert_private(self, response)
            page = Markup(response.content.decode())
            self.assertEqual([n["text"] for n in page.select("div", classes=("post-text",))],
                             ["Second wording.", "Read this quotation."], "The opening and reply must show their current versions.")
            self.assertEqual([p["text"] for q in page.select("blockquote") for p in page.select("p", within=q)][-1:],
                             ["First wording."], "Editing the opening must leave the reply's quotation at its original version.")
            self.assertEqual(read_counts(), before, "Reading a Topic and its quote must create no forum or lineage rows.")
            self.assertFalse(wake.called, "Reading the Topic must never wake due decisions.")
        support.hide(args, reply, True, as_coordinator=True)
        concealed_post = reader.get(url)
        self.assertContains(concealed_post, "Post unavailable.", msg_prefix="The readable thread must keep a neutral concealed reply.")
        self.assertNotContains(concealed_post, "Read this quotation.", msg_prefix="A concealed reply's text must disappear.")
        self.assertNotContains(concealed_post, "First wording.", msg_prefix="A concealed reply must release no quotation.")
        category = uuid4()
        discussion.create_category(**support.read_args(args), category_uuid=category, name="Archived work", request_uuid=uuid4())
        custom = args | {"category_uuid": category}
        archived = support.start(custom, title="Archived but readable")
        prior = discussion.category_history(**custom)[-1]["version_uuid"]
        discussion.retire_category(**custom, predecessor=prior, request_uuid=uuid4())
        self.assertContains(reader.get(path(owner, board, archived["thread"])), "This category is retired.",
                            msg_prefix="A retired category keeps readable history and explains the retirement.")
        fixture_membership_state(owner, founder, trustee, OrganismMembership.State.SUSPENDED)
        self.assertContains(reader.get(url), "This category is open to you for reading.",
                            msg_prefix="A suspended trustee may read and must see the read-only explanation.")
        support.moderate(args, topic, "CLOSE")
        self.assertContains(reader.get(url), "This Topic is closed.", msg_prefix="A readable closed Topic must explain its closure.")
        support.hide(args, topic, True, as_coordinator=True)
        withheld = reader.get(url)
        self.assertEqual(withheld.status_code, 404, "A concealed opening must now give the same private status as missing.")
        self.assertNotIn(b"This Topic is closed.", withheld.content, "The private refusal must never disclose the hidden Topic's closure.")
        self.assertIn(b"You tried to open a discussion that you are not authorised to view, or that does not exist.",
                      withheld.content, "Concealment must give the neutral refusal (capture AI).")

    @override_settings(PASSWORD_HASHERS=FIXTURE_HASHERS)
    def test_start_links_original_office_version(self):
        owner, scope, trustees, board = boards.ordinary(3, board=True)
        founder, _, trustee = trustees
        definition = office_basis(owner, founder)
        first_version = record_office(owner, founder, definition)
        pin = record_office(owner, founder, definition, prior=first_version)
        activate_trustees(scope, board)
        args = post_support.DiscussionPostTests().board(owner, trustee, board)
        office_url = reverse("office_page", args=(owner.organism_id, pin))
        reader = signed_in(trustee)
        office = Markup(reader.get(office_url).content.decode())
        links = [a["attrs"]["href"] for a in office.select("a") if a["text"].strip() == "Start a Topic about this office"]
        self.assertEqual(len(links), 1, "An admitted trustee must have one start link from this office.")
        action, data = command_form(reader.get(links[0]))
        self.assertEqual(data["type"], "TOPIC", "An office start must submit the fixed Topic type.")
        data.update(category=str(args["category_uuid"]), title="Who can approve a payment while the Treasurer's seat is vacant?",
                    markdown="The Treasurer’s seat is vacant.")
        first = reader.post(action, data)
        self.assertEqual(first.status_code, 303, "Starting the office Topic must redirect privately to its opening post.")
        assert_private(self, first)
        replay = reader.post(action, data)
        self.assertEqual((replay.status_code, replay.get("Location")), (303, first["Location"]),
                         "Identical starts must return the same Topic and opening post.")
        topic_url, anchor = first["Location"].split("#")
        current_page = Markup(reader.get(office_url).content.decode())
        links = current_page.select("a", href=topic_url)
        self.assertEqual(len(links), 1, "The Topic started at the office must have one link to its own page.")
        self.assertEqual([n["text"] for n in current_page.select("strong", within=links[0])], [data["title"]],
                         "The linked glass row must show the original Topic title.")
        saved = reader.get(topic_url)
        self.assertContains(saved, "Office definition · Version 2", msg_prefix="The original office version must remain visible.")
        self.assertContains(saved, f'id="{anchor}"', msg_prefix="The redirect must name the opening post on the page.")
        self.assertEqual(discussion._rows("SELECT count(*) AS n FROM public.core_discpostact WHERE payload->>'thread'=%s",
                                          [topic_url.rstrip("/").split("/")[-1]])[0]["n"], 1,
                         "Replaying the opening must record just one post act.")
        fixture_membership_state(owner, founder, trustee, OrganismMembership.State.SUSPENDED)
        self.assertNotContains(reader.get(office_url), "Start a Topic about this office",
                               msg_prefix="A reader without a posting grant must receive no start command.")

    @override_settings(PASSWORD_HASHERS=FIXTURE_HASHERS)
    def test_reply_replay_creates_one_post(self):
        owner, scope, trustees, board = boards.ordinary(3, board=True)
        _, opener, trustee = trustees
        activate_trustees(scope, board)
        support = post_support.DiscussionPostTests()
        args = support.board(owner, opener, board)
        opening = support.start(args, markdown="Original payment question.")
        url = path(owner, board, opening["thread"])
        reader = signed_in(trustee)
        displayed = reader.get(url)
        action, data = command_form(displayed)
        quotes = Markup(displayed.content.decode()).select("option", value=str(opening["version"]))
        self.assertEqual(len(quotes), 1, "The form must offer the exact visible version as a quotation.")
        discussion_posts.edit_post(**args, thread_uuid=opening["thread"], post_uuid=opening["post"],
                                   predecessor=opening["version"], request_uuid=uuid4(), markdown="Revised question.")
        data.update(markdown="I have read the provision.", quote=str(opening["version"]))
        first = reader.post(action, data)
        self.assertEqual(first.status_code, 303, "The second trustee's reply must lead to the recorded post.")
        assert_private(self, first)
        duplicate = reader.post(action, data)
        self.assertEqual((duplicate.status_code, duplicate.get("Location")), (303, first["Location"]),
                         "An identical reply must return the same post and its original anchor.")
        posts = discussion_posts.read_thread(**support.read_args(args), thread_uuid=opening["thread"])["posts"]
        self.assertEqual([(row["post_number"], row["markdown"]) for row in posts],
                         [(1, "Revised question."), (2, data["markdown"])], "Only one reply may follow the edited opening.")
        self.assertEqual((posts[1]["author_id"], posts[1]["quoted_markdown"]), (trustee.pk, "Original payment question."),
                         "The reply must name the second trustee and quote the version shown before the edit.")
        self.assertEqual(first["Location"], f'{url}#post-{posts[1]["post_uuid"]}', "The redirect must identify that reply.")
        count = discussion._rows("SELECT count(*) AS n FROM public.core_discpostact WHERE payload->>'thread'=%s AND operation='REPLY'",
                                 [str(opening["thread"])])[0]["n"]
        self.assertEqual(count, 1, "Identical submissions must leave one reply receipt.")
        denied = signed_in(opener).post(action, data)
        self.assertEqual(denied.status_code, 400, "Another trustee cannot use the first reader's signed form.")
        _, fresh = command_form(reader.get(url))
        new = reader.post(action, fresh | {"markdown": "A separate reply.", "quote": ""})
        self.assertEqual(new.status_code, 303, "A fresh form must allow a deliberate second contribution.")
        self.assertNotEqual(new["Location"], first["Location"], "A new contribution must have its own post identity.")

    @override_settings(PASSWORD_HASHERS=FIXTURE_HASHERS)
    def test_revised_office_link_keeps_original_version(self):
        from src.intevia.services.organism_role_definition_service import DefinitionContent, DefinitionProvision

        # Main's C1-108-R route. The same three people can take separately elected seats on restoration.
        owner, founder = laws.ordinary_birth(offering_choice="FREE", board_seats=6, elected=(3, 4, 5), board_minimum=None)
        board_id = discussion._board_id(owner.organism_id, None)
        now = database_now()
        elections = {f"Seat {n}": election_support.seat(place=f"trustee-{n}", term={"end": (now + timedelta(days=n)).isoformat()})
                     for n in (3, 4, 5)}
        charter, version = committee_support.first_version(founder, {
            "board_makeup": {"minimum": 3}, "board_restoration": {"Restore": {"body": str(board_id)}},
            "committees": {"chair_board_member": False}, "elections": elections,
            "offices": {"Treasurer": "The Treasurer approves ordinary payments."},
        })
        proposal = laws.adoption(owner, charter, "ARTICLE")
        self.assertEqual(laws.law.OrganismLawService().adopt_law(laws.command(founder, proposal)).status, "ADOPT",
                         "The Board’s restoration and office provisions must be adopted before the first term.")
        content = DefinitionContent(code="TREASURER", provisions=(DefinitionProvision("OFFICE_BASIS", "Treasurer", str(version.pk)),))
        original = record_office(owner, founder, content)
        journey = election_mandates.ElectionMandateTests()
        journey.topic_category_ids = {}
        _, general, governed, trustees = journey.activate_board(owner, founder)
        board = Circle.objects.get(circle_uuid=governed["circle_uuid"])
        first = CircleStateEvent.objects.get(circle=board, action="ACTIVATE")
        args = post_support.DiscussionPostTests().board(owner, trustees[1], board)
        reader = signed_in(trustees[2])
        start = reverse("discussion_start_form", args=(owner.organism_id, board.circle_uuid))
        action, fields = command_form(reader.get(start, {"office": str(original)}))
        fields.update(category=str(args["category_uuid"]), title="Treasurer duties", markdown="First wording.")
        opening = reader.post(action, fields)
        self.assertEqual(opening.status_code, 303, "The first Board term must admit an opening pinned to office version 1.")

        stand_down = cq.stand_down_attachment(organism_uuid=owner.organism_id, circle_uuid=board.circle_uuid,
                                              lifecycle_uuid=first.event_uuid, board_uuid=board_id)
        down = committee_support.decide_instrument(journey, general, stand_down, trustees)["result"]
        board.refresh_from_db()
        self.assertNotEqual(board.state, "ACTIVE", "The Board must stand down before the members can turn voting off.")
        off = cq.settings_attachment(organism_uuid=owner.organism_id, predecessor=cq.settings(**general)["version"],
                                     changes={"enabled": False})
        committee_support.decide_instrument(journey, general, off, trustees)
        successor = record_office(owner, founder, content, prior=original)
        self.assertNotEqual(successor, original, "The amendment must create a successor definition.")
        cq.change_settings(**general, predecessor=cq.settings(**general)["version"], changes={"enabled": True}, request_uuid=uuid4())
        citation = {"binding_uuid": proposal.proposal, "version_id": version.pk}
        restore = cq.restoration_attachment(organism_uuid=owner.organism_id, circle_uuid=board.circle_uuid,
                                            stand_down_uuid=down, board_uuid=board_id,
                                            citation={"binding": str(proposal.proposal), "version": version.pk, "provision": "Restore"})
        restored = committee_support.decide_instrument(journey, general, restore, trustees)["result"]
        for n, trustee in zip((3, 4, 5), trustees, strict=True):
            seat = election_support.call(journey, general, citation, provision=f"Seat {n}", place=f"trustee-{n}")
            election_support.nominate(seat, trustee)
            ballot = election_support.open_election(seat)
            for voter in trustees:
                election_support.cast(seat, ballot, voter)
        circle_commands.activate(**general, circle_uuid=board.circle_uuid, restoration_result_uuid=restored, request_uuid=uuid4())
        board.refresh_from_db()
        self.assertEqual(board.state, "ACTIVE", "The original Board must be active on its elected seats.")
        response = reader.get(opening["Location"].split("#")[0])
        page = Markup(response.content.decode())
        destination = reverse("office_page", args=(owner.organism_id, successor))
        self.assertEqual([a["text"] for a in page.select("a", href=destination)], ["Treasurer"],
                         "A Topic pinned before amendment must link to the current definition of that same office.")
        self.assertContains(response, "Office definition · Version 1", msg_prefix="The link must preserve the original version label.")
        self.assertEqual(reader.get(destination).status_code, 200, "The released family link must open for the same reader.")
        replay = reader.post(action, fields)
        self.assertEqual((replay.status_code, replay.get("Location")), (303, opening["Location"]),
                         "A still-admitted retry after amendment must recognise the original receipt and retain its v1 pin.")

    @override_settings(PASSWORD_HASHERS=FIXTURE_HASHERS)
    def test_revoked_category_refused_on_form_and_submit(self):
        owner, board, _, coordinator, trustee = seated_board()
        support = post_support.DiscussionPostTests()
        args = support.read_args(support.board(owner, coordinator, board))
        category = uuid4()
        discussion.create_category(**args, category_uuid=category, name="Payments", request_uuid=uuid4())
        start = reverse("discussion_start_form", args=(owner.organism_id, board.circle_uuid))
        reader = signed_in(trustee)
        url = f"{start}?category={category}"
        action, fields = command_form(reader.get(url))
        fields.update(category=str(category), type="TOPIC", title="Payments question", markdown="My retained text.")
        scoped = args | {"category_uuid": category}
        predecessor = discussion.category_history(**scoped)[-1]["version_uuid"]
        discussion.retire_category(**scoped, predecessor=predecessor, request_uuid=uuid4())
        with patch.object(discussion_posts, "start_topic", wraps=discussion_posts.start_topic) as start_service:
            denied = reader.post(action, fields)
            self.assertFalse(start_service.called, "The submitted form must recheck the revoked category before calling START.")
            self.assertContains(denied, "This category has been retired", status_code=403,
                                msg_prefix="The reader must learn why the earlier form cannot submit.")
            fresh = reader.get(url)
            self.assertContains(fresh, "This category has been retired", status_code=403,
                                msg_prefix="A new form must also refuse the retired category.")
            self.assertNotContains(fresh, 'name="command"', status_code=403, msg_prefix="The denied category must release no form.")
        missing = reader.get(f"{start}?category={uuid4()}")
        self.assertEqual(missing.status_code, 404, "An unknown category must receive the private page.")
        hidden = uuid4()
        discussion.create_category(**args, category_uuid=hidden, name="Private review", request_uuid=uuid4())
        address = f"{start}?category={hidden}"
        hidden_action, hidden_fields = command_form(reader.get(address))
        scoped = args | {"category_uuid": hidden}
        prior = discussion.category_history(**scoped)[-1]["version_uuid"]
        discussion.revise_category(**scoped, predecessor=prior, name="Private review", description="",
                                   order=0, access="COORDINATORS", request_uuid=uuid4())
        with patch.object(discussion_posts, "start_topic") as start_service:
            sent = reader.post(hidden_action, hidden_fields | dict(category=str(hidden), type="TOPIC",
                                                                    title="Withdrawn access", markdown="Private draft."))
            for denied in (sent, reader.get(address)):
                self.assertEqual((denied.status_code, denied.content), (404, missing.content),
                                 "Loss of category visibility must use the same private page as a missing category.")
            self.assertFalse(start_service.called, "A concealed category must stop both form paths before the service.")


def command_form(response, field="command"):
    """Find the actual discussion form and carry its own hidden fields."""
    page = Markup(response.content.decode())
    command, = page.select("input", name=field)
    form = next(parent for parent in command["parents"] if parent["tag"] == "form")
    return form["attrs"]["action"], {node["attrs"]["name"]: node["attrs"]["value"]
                                    for node in page.select("input", within=form, type="hidden")}


BR2_CATALOGUE_PINS = (
    ("public.cq_phase_facts(bigint,uuid,uuid,text,jsonb,bigint,timestamptz)",
     (10269, 'bcaa90b76ebc0b5e50da385331de48e40a0342130b45897da73c2bafc59e054d'),
     (10796, '4f2afac2d23f6f5ffc82fb1292325c392d384e60734c5f5ece0cc499524bdddc')),
    ("public.cq_open_source(bigint,uuid,uuid,bigint,bigint,jsonb)",
     (594, 'a3ad0352ca13431fadc104f6b17f817b271b8bf4082558615859b7f7eca7a235'),
     (776, '0bba7e8d526aa151f3fe2a7c5fc0fc4bd4731b2b9a2a75fe6f43a337d6aa2e9a')),
    ("public.cq_vote_followup()",
     (1208, '1162aa9b2763a2281d58ae7fc3556fad6ff8918352da86f53388441a72d7a801'),
     (1218, '48bf4c6444e05daaff92a1a370dc88f4a6193d58ccdde5522ec2103d1d69d43b')),
    ("public.cq_complete()",
     (5851, '409c1d8f4160b959095488647fdb0408305aa75f545d691e8f2a5a45857fe529'),
     (6506, '2ef23724983d44ef5af47f8897b4051ee0d3f76c0c6b8ee82da48edba8f2bfe6')),
)


class ClarityRuleOpeningTests(TransactionTestCase):
    """BR2 at the governed write, including the two independently attributable acts."""

    def ordinary(self, size=3):
        lo, founder = laws.ordinary_birth()
        people = [founder] + [laws.add_member(lo) for _ in range(size - 1)]
        scope = {"credential": founder.credential, "organism_uuid": lo.organism_id}
        cq.change_settings(**scope, predecessor=None, changes={"enabled": True, "clarity_stage": True},
                           request_uuid=uuid4())
        journey, wording, window = self.start(scope)
        return journey, wording, window, people

    def start(self, scope):
        journey, _, opening = clarity_support.CQClarityTests().topic(scope)
        cq.put_forward(**journey, version_uuid=opening["version"], request_uuid=uuid4())
        window = cq.mark_prepared(**journey, request_uuid=uuid4())
        return journey, opening["version"], window

    def confirm(self, scope, wording, person, key=None):
        return cq.confirm_clarity(**(scope | {"credential": person.credential}),
                                  version_uuid=wording, request_uuid=key or uuid4())

    def responses(self, scope):
        return discussion._rows("""SELECT a.act_uuid, a.operation, a.authority_source, a.actor_id,
                   a.execution_xid::text AS xid, a.request->>'trigger_act' AS cause,
                   a.facts->>'rule_open' AS marker, e.actor_state, e.actor_id AS event_actor,
                   e.entry_mode, e.enterer_identity_id AS enterer
              FROM public.core_cqact a LEFT JOIN public.core_livingorganismevent e ON e.event_uuid=a.act_uuid
             WHERE a.thread_uuid=%s AND a.operation IN ('CONFIRM','OPEN_VOTE','NO_BALLOT','PROCEED')
             ORDER BY a.ordinal""", [scope["thread_uuid"]])

    def test_three_trustees_open_once_on_the_last_confirmation(self):
        owner, board, founder, chair, third = seated_board(clarity=True)
        scope = {"organism_uuid": owner.organism_id, "circle_uuid": board.circle_uuid, "credential": chair.credential}
        journey, wording, _ = self.start(scope)
        self.confirm(journey, wording, founder)
        self.assertEqual([r["operation"] for r in self.responses(journey)], ["CONFIRM"],
                         "The founder and the author cannot open a three-member vote alone.")
        key = uuid4()
        for _ in range(2):
            self.assertEqual(self.confirm(journey, wording, third, key), key,
                             "Replaying the last confirmation must return its own receipt.")
        rows = self.responses(journey)
        self.assertEqual([r["operation"] for r in rows], ["CONFIRM"] * 2 + ["OPEN_VOTE"],
                         "The last non-author response must produce exactly one opening.")
        parent, child = rows[-2:]
        self.assertEqual((parent["marker"], child["cause"], child["xid"]),
                         ("true", str(key), parent["xid"]), "The opening belongs to this response and transaction.")
        panel = cq.board_topic_vote(**journey)
        self.assertEqual((panel["phase"], panel["head_operation"], panel["ballot_uuid"]),
                         ("VOTING", "OPEN_VOTE", child["act_uuid"]), "The Board must already see this rule's ballot.")
        self.assertTrue({"actor_id", "enterer_identity_id", "opener", "opened_by"}.isdisjoint(panel),
                        "The reader must never present the last confirmer as the ballot opener.")

    def test_two_contending_confirmations_open_one_ballot(self):
        scope, wording, _, people = self.ordinary()
        first, waiting = clarity_support.CQClarityTests().contended_commands(
            lambda: self.confirm(scope, wording, people[1]),
            lambda: self.confirm(scope, wording, people[2]),
        )
        last = waiting.result(timeout=5)
        rows = self.responses(scope)
        self.assertEqual([r["operation"] for r in rows], ["CONFIRM"] * 2 + ["OPEN_VOTE"],
                         "Two overlapping confirmations must open a single ballot.")
        self.assertEqual((rows[0]["act_uuid"], rows[0]["marker"], rows[1]["act_uuid"], rows[2]["cause"]),
                         (first, None, last, str(last)), "Only the last committed responder may cause the opening.")

    def test_opening_authority_and_transport(self):
        scope, wording, _, people = self.ordinary()
        _, second, last = people
        self.confirm(scope, wording, second)
        with transaction.atomic():
            key = self.confirm(scope, wording, last)
            parent, child = self.responses(scope)[-2:]
            self.assertEqual(parent["authority_source"], {"kind": "MEMBER", "reference": last.pk, "route": "CONFIRM"},
                             "The last member must retain their own confirmation.")
            self.assertEqual(child["authority_source"], {"kind": "RULE", "reference": "HG:BR2",
                             "route": "CLARITY_FULL_PARTICIPATION", "cause": str(key)},
                             "The opening belongs to the rule and cites the member's confirmation.")
            self.assertEqual((child["actor_state"], child["event_actor"], child["entry_mode"], child["enterer"]),
                             ("LIVING_ORGANISM", None, "TRANSPORT", last.pk),
                             "The member carries the opening into the record; the organisation is its standing actor.")
        status = cq_reads.vote_status(**scope)
        self.assertEqual(status["ballot"], child["act_uuid"], "The reader must use the rule-owned ballot.")

    def test_child_refusal_rolls_back_the_last_confirmation(self):
        scope, wording, _, people = self.ordinary()
        for person in people[1:-1]:
            self.confirm(scope, wording, person)
        before = self.responses(scope)
        key = uuid4()
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("""ALTER TABLE public.core_cqact ADD CONSTRAINT b7b1_refuse_opening
                              CHECK (operation <> 'OPEN_VOTE') NOT VALID""")
            with self.assertRaises(IntegrityError, msg="This controlled refusal must reach the child insert.") as error:
                self.confirm(scope, wording, people[-1], key)
            self.assertEqual(error.exception.__cause__.diag.constraint_name, "b7b1_refuse_opening",
                             "Only the child-insert control may cause this failure.")
            self.assertEqual(self.responses(scope), before, "A refused child must also undo its parent's act and event.")
            self.assertFalse(LivingOrganismEvent.objects.filter(event_uuid=key).exists(),
                             "The refused confirmation must leave no independent ILC event.")
            cursor.execute("ALTER TABLE public.core_cqact DROP CONSTRAINT b7b1_refuse_opening")
        self.confirm(scope, wording, people[-1], key)
        self.assertEqual(self.responses(scope)[-1]["operation"], "OPEN_VOTE", "Restoring the child must admit the same request.")

    def test_marked_parent_requires_its_child(self):
        scope, wording, _, people = self.ordinary()
        for person in people[1:-1]:
            self.confirm(scope, wording, person)
        before = self.responses(scope)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("ALTER TABLE public.core_cqact DISABLE TRIGGER cq_br2_confirm")
            with self.assertRaises(IntegrityError, msg="A marked confirmation must reject a missing child.") as error:
                with transaction.atomic():
                    self.confirm(scope, wording, people[-1])
                    cursor.execute("SET CONSTRAINTS cq_complete IMMEDIATE")
            self.assertEqual(error.exception.__cause__.diag.message_primary, "CQ_COMPLETE",
                             "The parent completeness check must refuse the missing originating link.")
            self.assertEqual(self.responses(scope), before, "The refused parent must roll back.")
            cursor.execute("ALTER TABLE public.core_cqact ENABLE TRIGGER cq_br2_confirm")
            cursor.execute("SET CONSTRAINTS cq_complete IMMEDIATE")
            self.confirm(scope, wording, people[-1])
        self.assertEqual(self.responses(scope)[-1]["operation"], "OPEN_VOTE",
                         "With constraints immediate, a genuine last response must commit its opening.")

    def test_conflict_roster_and_three_person_floor(self):
        scope, wording, _, people = self.ordinary(4)
        cq.declare_conflict(**(scope | {"credential": people[-1].credential}), basis="Supplier", request_uuid=uuid4())
        for person in people[1:-1]:
            self.confirm(scope, wording, person)
        self.assertEqual([r["operation"] for r in self.responses(scope)], ["CONFIRM"] * 2 + ["OPEN_VOTE"],
                         "Three clear members after a prior conflict must open the vote.")
        next_scope, next_wording, _ = self.start({k: v for k, v in scope.items() if k != "thread_uuid"})
        for person in people[2:]:
            cq.declare_conflict(**(next_scope | {"credential": person.credential}), basis="Supplier", request_uuid=uuid4())
        self.confirm(next_scope, next_wording, people[1])
        self.assertEqual([r["operation"] for r in self.responses(next_scope)], ["CONFIRM"],
                         "Two out of two must stay in Clarity.")

    def test_deadline_blocks_the_last_confirmation(self):
        scope, wording, window, people = self.ordinary()
        for person in people[1:-1]:
            self.confirm(scope, wording, person)
        with clarity_support.elapsed_clarity(window, response_due=False):
            with self.assertRaises(cq.CQRefused, msg="A confirmation at or past the deadline must be refused.") as error:
                self.confirm(scope, wording, people[-1])
            self.assertEqual(error.exception.code, "CQ_CONFIRM_PERIOD", "The governed deadline must be the refusal.")
        self.assertEqual([r["operation"] for r in self.responses(scope)], ["CONFIRM"],
                         "A late response must leave neither a third confirmation nor a ballot.")

    def test_trigger_filters_partial_responses(self):
        row, = discussion._rows("""SELECT pg_get_triggerdef(t.oid) AS ddl, t.tgenabled AS enabled
            FROM pg_trigger t WHERE t.tgrelid='public.core_cqact'::regclass AND t.tgname='cq_br2_confirm'""")
        predicate = row["ddl"].partition(" WHEN (")[2].partition(" EXECUTE ")[0].lower()
        predicate = re.sub(r"::(?:character varying|text)|[()\s]", "", predicate)
        self.assertEqual(predicate, "new.operation='confirm'andnew.facts->>'rule_open'='true'",
                         "The trigger must select only a confirmation marked to open by rule.")
        self.assertEqual(row["enabled"], "O", "Ordinary inserts must use the Clarity follow-up.")

    def test_0116_reverses_and_refuses_installed_drift(self):
        migration = import_module("core.migrations.0116_clarity_full_participation")
        executor = MigrationExecutor(connection)
        edge = executor.loader.get_migration("core", "0116_clarity_full_participation")
        state = executor.loader.project_state(edge.dependencies)
        # Independent known-answer pairs from P0 Appendix A, in installation order.
        known = BR2_CATALOGUE_PINS

        def catalogue():
            with connection.cursor() as cursor:
                pins = []
                for signature, _, _ in known:
                    cursor.execute("""SELECT octet_length(convert_to(prosrc,'UTF8')),
                        encode(sha256(convert_to(prosrc,'UTF8')),'hex') FROM pg_proc
                        WHERE oid=to_regprocedure(%s)""", [signature])
                    pins.append(cursor.fetchone())
                cursor.execute("""SELECT count(*) FILTER (WHERE NOT t.tgisinternal),
                    count(*) FILTER (WHERE c.relname='core_cqact' AND t.tgname='cq_br2_confirm')
                    FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                    WHERE c.relnamespace=current_schema()::regnamespace""")
                count, br2 = cursor.fetchone()
                cursor.execute("SELECT app,name FROM django_migrations")
                applied = set(cursor.fetchall())
            non_core = sum(n for key, n in NON_CORE_TRIGGER_DELTAS.items() if key in applied)
            return pins, count - non_core, br2

        parent = ([old for _, old, _ in known], 738, 0)
        revised = ([new for _, _, new in known], 739, 1)
        author_edge = executor.loader.get_migration("core", "0118_author_clarity_and_conflict")
        author_base = executor.loader.project_state(author_edge.dependencies)
        received = catalogue()
        with transaction.atomic():
            executor.unapply_migration(author_base, author_edge)
            self.assertEqual(catalogue(), revised, "The 0117 edge retains the four exact 0116 bodies and its trigger.")
            executor.unapply_migration(state, edge)
            self.assertEqual(catalogue(), parent, "Reverse must restore all four 0115 bodies and remove the new trigger.")
            # Drift the last routine so a refused forward also tests rollback of the first three swaps.
            signature, before, _ = migration.qualified_bodies()[-1]
            with connection.cursor() as cursor:
                swap(cursor, signature, before, before + "\n")
            drifted = catalogue()
            with self.assertRaises(ClockConversionRefused, msg="Forward must refuse drift in the last installed body."):
                with transaction.atomic():
                    executor.apply_migration(state, edge)
            self.assertEqual(catalogue(), drifted, "A refused forward must roll back all preceding swaps.")
            with connection.cursor() as cursor:
                swap(cursor, signature, before + "\n", before)
            executor.apply_migration(state, edge)
            self.assertEqual(catalogue(), revised, "A clean forward must install the exact approved catalogue.")
            executor.unapply_migration(state, edge)
            self.assertEqual(catalogue(), parent, "Reversing the fresh installation must reproduce the same parent.")
            executor.apply_migration(state, edge)
            self.assertEqual(catalogue(), revised, "Forward again must reproduce the identical catalogue.")
            transaction.set_rollback(True)
        self.assertEqual(catalogue(), received, "The round trip must leave the received 0118 head intact.")

    test_0116_reverses_and_refuses_installed_drift.ISOLATION_FULL_CAPTURE = True

    def test_changed_election_slate_refuses_the_last_response(self):
        lo, owner, local, _, citation = election_support.owner(voting=False)
        people = [owner, laws.add_member(lo), laws.add_member(lo)]
        cq.change_settings(**local, changes={"enabled": True, "clarity_stage": True}, predecessor=None,
                           request_uuid=uuid4())
        scope = election_support.call(clarity_support.CQClarityTests(), local, citation)
        election_support.nominate(scope, people[1])
        withdrawn = election_support.nominate(scope, people[2])
        cq.mark_prepared(**scope, request_uuid=uuid4())
        wording = cq.phase(**scope)["version"]
        for person in people[1:-1]:
            self.confirm(scope, wording, person)
        elections.withdraw(**(scope | {"credential": people[2].credential}), nomination_uuid=withdrawn,
                           request_uuid=uuid4())
        before = self.responses(scope)
        key = uuid4()
        with self.assertRaises(cq.CQRefused, msg="The changed, non-empty slate must refuse its rule opening.") as error:
            self.confirm(scope, wording, people[-1], key)
        self.assertEqual(error.exception.code, "ELECT_CHANGED", "The specialist slate comparison must be the refusal.")
        self.assertEqual(self.responses(scope), before, "A refused election must lose the triggering confirmation as well.")
        self.assertFalse(LivingOrganismEvent.objects.filter(event_uuid=key).exists(),
                         "An election refusal must leave no confirmation event.")
        self.assertEqual(cq.phase(**scope)["phase"], "CLARITY", "The invalidated opening must leave Clarity in place.")

    def test_ordinary_circle_coordinator_still_closes_clarity(self):
        lo, founder = laws.ordinary_birth(offering_choice="FREE")
        general = {"organism_uuid": lo.organism_id, "credential": founder.credential}
        circle = election_mandates.ElectionMandateTests().committee(general)
        local = general | {"circle_uuid": circle.circle_uuid}
        for _ in range(2):
            committee_support.join(general, circle, laws.add_member(lo))
        cq.change_settings(**general, predecessor=None, changes={"enabled": True, "clarity_stage": True},
                           request_uuid=uuid4())
        scope, _, window = self.start(local)
        with clarity_support.elapsed_clarity(window, response_due=False):
            cq.close_without_vote(**scope, request_uuid=uuid4())
        receipt, = discussion._rows("""SELECT actor_id, authority_source FROM public.core_cqact
            WHERE thread_uuid=%s AND operation='CLOSE'""", [scope["thread_uuid"]])
        self.assertEqual((receipt["actor_id"], receipt["authority_source"]["route"]),
                         (founder.pk, "CIRCLE_COORDINATOR"), "The ordinary Circle keeps its effective Coordinator.")

    def test_committee_opens_under_its_own_mandate(self):
        # The fixture normally chooses Clarity off at birth. Use the real settings command to opt in.
        case = election_mandates.ElectionMandateTests()

        def enable_clarity(scope):
            cq.change_settings(**scope, predecessor=None, changes={"enabled": True, "clarity_stage": True},
                               request_uuid=uuid4())

        with patch.object(election_support, "enable_voting", enable_clarity):
            lo, general, board, voters = committee_support.formation_board(case, offering="FREE")
        circle = case.committee(general)
        local = general | {"circle_uuid": circle.circle_uuid}
        for person in voters[1:]:
            committee_support.join(general, circle, person)
        forming, _ = committee_support.mandate(lo, voters[0], circle, "1", publisher=committee_support.first_version)
        founding_motion = committee_support.start(case, board, forming)
        cq.mark_prepared(**founding_motion, request_uuid=uuid4())
        wording = cq.phase(**founding_motion)["version"]
        for trustee in voters:
            if trustee.credential != founding_motion["credential"]:
                self.confirm(founding_motion, wording, trustee)
        ballot = cq_reads.vote_status(**founding_motion)["ballot"]
        for trustee in voters:
            election_support.cast(founding_motion, ballot, trustee)
        mandate = forming["spec"]["proposal"]
        attachment = committee_support.step(local, mandate, "INVESTIGATE", area="reserves", finding="Check reserves.")
        scope = committee_support.start(case, local, attachment)
        cq.mark_prepared(**scope, request_uuid=uuid4())
        local_office, = preparation_receipts(scope["thread_uuid"])
        self.assertEqual(local_office["authority_source"]["route"], "CIRCLE_COORDINATOR",
                         "The committee chair still prepares its mandate motion through its own Circle office.")
        wording = cq.phase(**scope)["version"]
        for person in voters:
            if person.credential != scope["credential"]:
                last = self.confirm(scope, wording, person)
        child = self.responses(scope)[-1]
        self.assertEqual((child["operation"], child["cause"]), ("OPEN_VOTE", str(last)),
                         "The committee's last confirmation must open its own ballot.")
        receipt, = discussion._rows("""SELECT start.request#>>'{attachment,spec,mandate}' AS mandate,
                       start.request#>>'{attachment,kind}' AS kind, board.circle_id,
                       (public.cmt_current(board.circle_id,child.effective_at,child.recorded_at)).binding::text AS governing,
                       child.facts->>'attachment_fingerprint'=public.disc_fingerprint(start.request->'attachment') AS bound
            FROM public.core_cqact child JOIN public.core_discboard board USING(board_uuid)
            JOIN public.core_cqact start ON start.thread_uuid=child.thread_uuid
             AND start.board_uuid=child.board_uuid AND start.operation='INITIATE'
            WHERE child.act_uuid=%s""", [child["act_uuid"]])
        self.assertEqual(receipt, {"mandate": str(mandate), "kind": "COMMITTEE", "circle_id": circle.pk,
                                   "governing": str(mandate), "bound": True},
                         "The opened motion must belong to the committee and cite its adopted mandate.")


class BoardClarityFixture:
    """Build one ordinary Board and Suggestion for the calling test."""

    def journey(self, *, prepare=True, resignation=False, size=3, chair_indices=(), coordinator_index=1):
        if resignation:
            # Adopt the seat's notice rule while the founder alone may do so.
            owner, founder = laws.ordinary_birth(offering_choice="FREE", board_seats=4, board_minimum=None)
            self.notice = election_support.provision(founder, owner, {}, board_minimum=laws.FIXTURE_BOARD_MINIMUM,
                resignations={"Leaving office": {"place": "trustee-3", "effect": "ON_NOTICE"}})
            board = Circle.objects.get(parent_organism=owner, kind="GOVERNANCE")
            people = [founder] + [boards.seat_trustee(owner, founder, board, i) for i in range(1, 4)]
            boards.appoint_board_chairs(owner, founder, *(people[i] for i in chair_indices))
            activate_trustees({"organism_uuid": owner.organism_id, "credential": founder.credential}, board, clarity=True)
            boards.appoint_board_coordinator(owner, board, founder, people[coordinator_index])
        else:
            owner, board, *people = seated_board(clarity=True, size=size, chair_indices=chair_indices,
                                                  coordinator_index=coordinator_index)
        self.owner, self.board, self.people = owner, board, people
        support = post_support.DiscussionPostTests()
        self.args = support.board(owner, people[coordinator_index], board) | {"credential": people[2].credential}
        self.topic = support.start(self.args, topic_type="SUGGESTION", markdown="Approve one payment.")
        self.thread = self.topic["thread"]
        self.scope = support.read_args(self.args) | {"thread_uuid": self.thread}
        self.url = path(owner, board, self.thread)
        self.start_act = cq.put_forward(**self.scope, version_uuid=self.topic["version"], request_uuid=uuid4())
        if prepare:
            self.window = cq.mark_prepared(**self.scope, request_uuid=uuid4())

    def endpoint(self, name):
        return reverse(name, args=(self.owner.organism_id, self.board.circle_uuid, self.thread))

    def open_vote_form(self, response, label):
        page = Markup(response.content.decode())
        vote, = page.select("section", id="vote")
        link, = [a for a in page.select("a", within=vote) if a["text"].strip() == label]
        opened = response.client.get(link["attrs"]["href"])
        self.assertEqual(opened.status_code, 200, "The offered form must open for this same reader.")
        return opened

    def fields(self, response, name):
        page = Markup(response.content.decode())
        form, = page.select("form", method="post", action=self.endpoint(name))
        return {node["attrs"]["name"]: node["attrs"]["value"]
                for node in page.select("input", within=form, type="hidden")}

    def submit_clear(self, client):
        name = "discussion_confirm_clarity"
        fields = self.fields(client.get(self.url), name)
        response = client.post(self.endpoint(name), fields)
        self.assertEqual((response.status_code, response.get("Location")), (303, self.url + "#vote"),
                         "A successful response must take the member back to this Topic's vote.")
        assert_private(self, response)
        return client.get(response["Location"])

    def confirm_as(self, person):
        cq.confirm_clarity(**(self.scope | {"credential": person.credential}),
                           version_uuid=self.topic["version"], request_uuid=uuid4())

    def stored_responses(self):
        return discussion._rows("""SELECT operation,act_uuid,execution_xid::text AS xid,facts->>'version' AS version
            FROM public.core_cqact WHERE thread_uuid=%s AND operation IN ('CONFIRM','QUESTION')
            ORDER BY ordinal""", [self.thread])

    def replies(self):
        return discussion._rows("""SELECT a.act_uuid,a.execution_xid::text AS xid,p.post_uuid,body.markdown
            FROM public.discp_posts p JOIN public.core_discpostact a ON a.act_uuid=p.creation_act
            JOIN public.core_discbody body ON body.version_uuid=p.version_uuid
            WHERE p.thread_uuid=%s AND a.operation='REPLY' ORDER BY p.post_number""", [self.thread])


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS)
class BoardClarityResponseTests(BoardClarityFixture, TransactionTestCase):
    """A member responds on the released Topic. The database owns the phase transition."""

    own_status = "You said you're clear on this wording. If something becomes unclear, you can still ask a question."

    def test_01_last_clear_opens_on_the_displayed_wording(self):
        self.journey()
        founder, chair, _ = self.people
        second = self.submit_clear(signed_in(founder))
        self.assertContains(second, "2 of 3 are clear.", count=1)
        self.assertEqual(cq.phase(**self.scope)["phase"], "CLARITY", "Two members cannot open the three-member vote.")
        opened = self.submit_clear(signed_in(chair))
        self.assertEqual(cq.phase(**self.scope)["phase"], "VOTING", "The last confirmation must open voting.")
        page = Markup(opened.content.decode())
        card, = page.select("section", id="vote")
        self.assertTrue(all(person.display_name not in card["text"] for person in self.people),
                        "The vote card must not name any member as the opener.")
        lines = [p["text"] for p in page.select("p", within=card)]
        self.assertTrue(lines[0].startswith("Voting open · Closes "), "The active ballot deadline comes first.")
        self.assertEqual(lines[1], "All 3 eligible Board members were clear, so voting opened on this wording.",
                         "Explain the rule and its opening electorate immediately below the status.")
        self.assertFalse(page.select("form", within=card), "The Clarity forms must disappear once voting opens.")
        self.assertContains(signed_in(founder).get(self.url), lines[1], count=1,
                            msg_prefix="An earlier confirmer sees the same explanation after returning.")

    def test_02_stale_wording_is_refused_and_current_wording_counts(self):
        self.journey()
        client = signed_in(self.people[0])
        old = client.get(self.url)
        names = ("discussion_confirm_clarity", "discussion_clarity_question")
        question_page = self.open_vote_form(old, "Ask a question")
        old_fields = [self.fields(old, names[0]), self.fields(question_page, names[1])]
        current = discussion_posts.edit_post(**self.args, thread_uuid=self.thread, post_uuid=self.topic["post"],
            predecessor=self.topic["version"], request_uuid=uuid4(), markdown="Approve the revised payment.")
        for name, fields in zip(names, old_fields):
            refused = client.post(self.endpoint(name), fields | {"markdown": "Which figure?"})
            self.assertEqual(refused.status_code, 409, "An old wording version must be refused at submission.")
            self.assertContains(refused, "The wording has changed", status_code=409)
            link, = [n for n in Markup(refused.content.decode()).select("a") if n["text"] == "Review current wording"]
            self.assertEqual(link["attrs"]["href"], self.url + "#vote", "Offer the current wording on this Topic.")
            self.assertContains(client.get(link["attrs"]["href"]), "Approve the revised payment.")
        self.assertEqual((self.stored_responses(), self.replies()), ([], []), "Stale forms must leave both stores empty.")
        page = self.submit_clear(client)
        self.assertContains(page, "2 of 3 are clear.", count=1)
        receipt, = self.stored_responses()
        self.assertEqual(receipt["version"], str(current["version"]), "Only the current version may acquire assent.")

    def test_03_question_and_reply_share_one_commit_and_replay(self):
        self.journey()
        client = signed_in(self.people[0])
        name = "discussion_clarity_question"
        response = client.get(self.url)
        choices = Markup(response.content.decode())
        clear, = choices.select("form", action=self.endpoint("discussion_confirm_clarity"))
        self.assertEqual([b["text"] for b in choices.select("button", within=clear, type="submit", classes=("primary",))],
                         ["I'm clear"], "Assent uses its own primary submit.")
        opened = self.open_vote_form(response, "Ask a question")
        page = Markup(opened.content.decode())
        form, = page.select("form", action=self.endpoint(name))
        self.assertEqual([b["text"] for b in page.select("button", within=form, type="submit", classes=("primary",))],
                         ["Submit question"], "The question form has one primary submit.")
        fields = self.fields(opened, name)
        # NUL is non-blank. The ordinary reply service refuses it while fingerprinting
        # the body, after the vote service has successfully recorded the marker.
        invalid = client.post(self.endpoint(name), fields | {"markdown": "Which\x00invoice?"})
        self.assertContains(invalid, "Enter your question as plain text.", status_code=400)
        self.assertEqual((self.stored_responses(), self.replies()), ([], []),
                         "The reply's failure must roll back its earlier question marker.")
        body = "Which invoice is covered?"
        submitted = fields | {"markdown": body}
        accepted = client.post(self.endpoint(name), submitted)
        self.assertEqual((accepted.status_code, accepted.get("Location")), (303, self.url + "#vote"))
        assert_private(self, accepted)
        marker, = self.stored_responses()
        reply, = self.replies()
        self.assertEqual((marker["operation"], marker["version"], reply["markdown"], marker["xid"]),
                         ("QUESTION", str(self.topic["version"]), body, reply["xid"]),
                         "The marker for this wording and the ordinary reply must share the database transaction.")
        self.assertNotEqual(marker["act_uuid"], reply["act_uuid"], "The two writes require distinct receipt keys.")
        self.assertContains(client.get(self.url), "1 of 3 are clear.", count=1, msg_prefix="A question gives no assent.")
        for trustee in self.people[:2]:
            self.confirm_as(trustee)
        self.assertEqual(cq.phase(**self.scope)["phase"], "VOTING")
        self.assertEqual(client.post(self.endpoint(name), submitted).status_code, 303,
                         "The same signed form must recognise both receipts even after voting opens.")
        self.assertEqual(([r for r in self.stored_responses() if r["operation"] == "QUESTION"], self.replies()),
                         ([marker], [reply]), "A replay must preserve both original receipts without adding a copy.")
        self.assertContains(client.get(self.url), body, count=1, msg_prefix="The question remains one ordinary reply.")

    def test_04_count_is_private_and_never_lists_confirmers(self):
        self.journey()
        founder, _, reader = self.people
        self.confirm_as(founder)
        client = signed_in(reader)
        visible = client.get(self.url)
        self.assertContains(visible, "2 of 3 are clear.", count=1)
        card, = Markup(visible.content.decode()).select("section", id="vote")
        self.assertTrue(all(p.display_name not in card["text"] for p in self.people),
                        "The count must not list the members who responded.")
        reason = "I prepared the estimate."
        cq.declare_conflict(**(self.scope | {"credential": reader.credential}), basis=reason, request_uuid=uuid4())
        self.assertContains(client.get(self.url), reason, count=1, msg_prefix="The active declarer sees their own reason.")
        fixture_membership_state(self.owner, founder, reader, OrganismMembership.State.SUSPENDED)
        restricted = client.get(self.url)
        self.assertNotContains(restricted, reason, msg_prefix="A read-only membership cannot fetch the private declaration.")
        self.assertContains(restricted, "Approve one payment.", msg_prefix="The suspended trustee can still read the wording.")
        self.assertNotContains(restricted, "are clear.", msg_prefix="A read-only trustee must receive no Clarity count.")
        self.assertFalse(Markup(restricted.content.decode()).select("input", name="vote_command"),
                         "A suspended reader receives no Clarity form.")

    def test_05_board_departure_explains_the_two_views(self):
        self.journey(resignation=True, chair_indices=(1,), coordinator_index=2)
        founder, chair, other, leaving = self.people
        for trustee in (founder, chair):
            self.confirm_as(trustee)
        chair_client = signed_in(chair)
        self.assertContains(chair_client.get(self.url), "3 of 4 are clear.", count=1)
        # A second, already-open ballot in the very same Board saw all four members.
        support = post_support.DiscussionPostTests()
        prior = support.start(self.args, topic_type="SUGGESTION", markdown="Publish the minutes.")
        prior_scope = self.scope | {"thread_uuid": prior["thread"]}
        cq.put_forward(**prior_scope, version_uuid=prior["version"], request_uuid=uuid4())
        cq.mark_prepared(**prior_scope, request_uuid=uuid4())
        for person in (founder, chair, leaving):
            cq.confirm_clarity(**(prior_scope | {"credential": person.credential}),
                               version_uuid=prior["version"], request_uuid=uuid4())
        from core.models import CircleParticipation, CircleParticipationEnd
        place = CircleParticipation.objects.get(circle=self.board, membership__identity=leaving)
        elections.resign_board_place(organism_uuid=self.owner.organism_id, credential=leaving.credential,
                                     participation_uuid=place.participation_uuid, **self.notice,
                                     provision="Leaving office", request_uuid=uuid4())
        self.assertTrue(CircleParticipationEnd.objects.filter(participation=place).exists(),
                        "The fourth trustee must actually leave the Board.")
        self.assertEqual(cq.phase(**self.scope)["phase"], "CLARITY", "Departure alone keeps Clarity open in this part.")
        first = "A member has stepped out of this decision, and all 3 remaining Board members are clear. "
        for client, ending in ((chair_client, "or you can open it now."),
                               (signed_in(other), "or sooner if the Board Chair opens it.")):
            page = Markup(client.get(self.url).content.decode())
            card, = page.select("section", id="vote")
            lines = [p["text"] for p in page.select("p", within=card)]
            self.assertEqual(lines[1:3], ["3 of 3 are clear.", first + "Voting opens automatically after Clarity ends, " + ending],
                             "Explain the standing of the remaining members immediately below the count.")
            self.assertNotIn(self.own_status, card["text"], "The stepped-out explanation replaces personal status.")
            self.assertEqual(bool(page.select("form", within=card, action=self.endpoint("discussion_clarity_proceed"))),
                             client is chair_client, "Only the Board Chair can open this stepped-out vote.")
        prior_page = chair_client.get(path(self.owner, self.board, prior["thread"]))
        self.assertContains(prior_page, "All 4 eligible Board members were clear, so voting opened on this wording.",
                            msg_prefix="Departure cannot revise the electorate recorded when the earlier ballot opened.")

    def test_06_preparation_help_ends_at_the_deadline(self):
        self.journey(prepare=False, chair_indices=(1,))
        client = signed_in(self.people[2])
        help_text = ("Discuss the Suggestion here first. Its author or the Board Chair can mark it prepared "
                     "for Clarity or voting; if the preparation deadline passes first, it moves on as unprepared.")
        self.assertContains(client.get(self.url), help_text, count=1)
        prompt = self.url + "?prepare=1"
        self.assertContains(client.get(prompt), "You can still edit the wording during Clarity.")
        self.assertContains(signed_in(self.people[1]).get(prompt), "The author can still edit the wording during Clarity.")
        with clarity_support.elapsed_clarity(self.start_act, response_due=False):
            page = client.get(self.url)
            self.assertContains(page, "Preparation deadline passed", msg_prefix="The stored preparation head is still visible.")
            self.assertNotContains(page, help_text, msg_prefix="After the deadline the preparation advice must disappear.")

    def test_07_outsider_and_ineligible_member_cannot_respond(self):
        from tests.s015_0021_support import register_test_learner

        self.journey()
        founder = self.people[0]
        cq.declare_conflict(**(self.scope | {"credential": founder.credential}),
                            basis="I supplied the estimate.", request_uuid=uuid4())
        outsider = register_test_learner(username="clarity-outsider-" + uuid4().hex)
        for person, read_status, write_status in ((outsider, 404, 404), (founder, 200, 409)):
            client = signed_in(person)
            page = client.get(self.url)
            self.assertEqual(page.status_code, read_status, "Use this reader's real visibility at the target.")
            self.assertFalse(Markup(page.content.decode()).select("input", name="vote_command"),
                             "Neither reader may be offered a response form.")
            for name, operation in (("discussion_confirm_clarity", "CONFIRM"), ("discussion_clarity_question", "QUESTION")):
                token = discussion_forms.issue_form(page.wsgi_request, operation, self.owner.organism_id,
                    self.board.circle_uuid, thread=self.thread, version=self.topic["version"])
                refused = client.post(self.endpoint(name), {"vote_command": token, "markdown": "Why now?"})
                self.assertEqual(refused.status_code, write_status,
                                 "Even a correctly signed request must recheck the member's current standing.")
                assert_private(self, refused)
        self.assertEqual((self.stored_responses(), self.replies()), ([], []), "Forced forms must add no vote or reply.")

    def test_08_only_the_confirmed_reader_sees_the_current_wording_status(self):
        self.journey()
        reader = signed_in(self.people[0])
        confirmed = self.submit_clear(reader)
        page = Markup(confirmed.content.decode())
        vote, = page.select("section", id="vote")
        paragraphs = [node["text"] for node in page.select("p", within=vote)]
        self.assertEqual(paragraphs[1:3], ["2 of 3 are clear.", self.own_status],
                         "Personal status belongs directly below the count on the returning trustee's page.")
        peer = signed_in(self.people[1]).get(self.url)
        self.assertNotContains(peer, self.own_status, msg_prefix="One trustee cannot inherit another trustee's status.")
        self.assertContains(peer, "2 of 3 are clear.", msg_prefix="The other trustee can still read the count.")
        discussion_posts.edit_post(**self.args, thread_uuid=self.thread, post_uuid=self.topic["post"],
            predecessor=self.topic["version"], request_uuid=uuid4(), markdown="Approve the revised payment.")
        revised = reader.get(self.url)
        self.assertContains(revised, "1 of 3 are clear.", msg_prefix="An edit requires fresh confirmation.")
        self.assertNotContains(revised, self.own_status, msg_prefix="Earlier wording gives no personal confirmation.")

    def test_09_a_question_after_confirmation_withdraws_personal_clarity(self):
        self.journey()
        reader = signed_in(self.people[0])
        confirmed = self.submit_clear(reader)
        self.assertContains(confirmed, self.own_status, msg_prefix="The reader begins with current confirmation.")
        page = Markup(confirmed.content.decode())
        vote, = page.select("section", id="vote")
        self.assertFalse(page.select("textarea", within=vote), "The confirmed trustee starts at the choices.")
        opened = self.open_vote_form(confirmed, "Ask a question")
        question = "Does this cover both invoices?"
        route = "discussion_clarity_question"
        fields = self.fields(opened, route)
        sent = reader.post(self.endpoint(route), fields | {"markdown": question})
        self.assertEqual((sent.status_code, sent.get("Location")), (303, self.url + "#vote"),
                         "A confirmed trustee can still send a question and return to this vote.")
        current = reader.get(sent["Location"])
        self.assertNotContains(current, self.own_status, msg_prefix="The latest question withdraws the earlier clear.")
        self.assertContains(current, "1 of 3 are clear.", msg_prefix="The withdrawn clear also leaves the count.")
        self.assertEqual([row["operation"] for row in self.stored_responses()], ["CONFIRM", "QUESTION"],
                         "Keep both responses in order.")
        self.assertEqual([row["markdown"] for row in self.replies()], [question], "Record the question once as a reply.")

    def test_10_the_question_opens_its_own_signed_form(self):
        self.journey()
        reader = signed_in(self.people[0])
        response = reader.get(self.url)
        route = "discussion_clarity_question"
        self.assertNotContains(response, self.endpoint(route), msg_prefix="The choice must not issue a question POST.")
        opened = self.open_vote_form(response, "Ask a question")
        page = Markup(opened.content.decode())
        vote, = page.select("section", id="vote")
        form, = page.select("form", within=vote)
        self.assertEqual((form["attrs"]["method"], form["attrs"]["action"]), ("post", self.endpoint(route)))
        label, = page.select("label", within=form)
        self.assertEqual(label["text"].strip(), "Your question")
        box, = page.select("textarea", within=label, name="markdown")
        self.assertIn("required", box["attrs"])
        cancel, = page.select("a", within=vote, classes=("secondary",))
        choices = reader.get(cancel["attrs"]["href"])
        self.assertContains(choices, "Ask a question", msg_prefix="Cancel must restore the reader's choices.")
        self.assertEqual((self.stored_responses(), self.replies()), ([], []), "Opening and cancelling must record nothing.")
        body = "Which payment date applies?"
        fields = self.fields(opened, route)
        reply = reader.post(self.endpoint(route), fields | {"markdown": body})
        self.assertEqual((reply.status_code, reply.get("Location")), (303, self.url + "#vote"),
                         "Submit the expanded form back to its own Topic.")
        self.assertEqual([row["operation"] for row in self.stored_responses()], ["QUESTION"],
                         "The focused form must record precisely one question marker.")
        self.assertEqual([row["markdown"] for row in self.replies()], [body], "The member's reply must appear once.")


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS)
class BoardClarityCommandTests(BoardClarityFixture, TransactionTestCase):
    """Real signed Board forms, independent write authority and the stored consequence."""

    def actions(self):
        return discussion._rows("""SELECT act_uuid, operation, actor_id, request, facts, authority_source
            FROM public.core_cqact WHERE thread_uuid=%s ORDER BY ordinal""", [self.thread])

    def forced(self, page, operation, *, confirmed=False):
        """An in-session signed envelope to exercise a write that the page did not offer."""
        token = discussion_forms.issue_form(
            page.wsgi_request, operation, self.owner.organism_id, self.board.circle_uuid,
            thread=self.thread, version=self.topic["version"], window=self.window,
            confirmation=operation if confirmed else None,
        )
        return {"vote_command": token}

    def posted(self, client, operation, payload):
        response = client.post(self.endpoint("discussion_clarity_" + operation), payload)
        self.assertEqual((response.status_code, response.get("Location")), (303, self.url + "#vote"),
                         "The command must return to the same Topic's vote.")
        assert_private(self, response)
        return response

    def assert_no_single_chair(self, chair_indices):
        self.journey(prepare=False, size=4, chair_indices=chair_indices)
        facts, = discussion._rows("""
            SELECT count(effective.id) AS holders, public.cmt_board_chair(%s, t.at, t.at) AS sole
              FROM (SELECT public.intevia_clock_statement() AS at) t
              LEFT JOIN LATERAL (
                  SELECT a.id FROM public.core_contextualroleassignment a
                    JOIN public.core_organismroledefinition d ON d.id=a.role_definition_id
                   WHERE d.living_organism_id=%s AND d.kind='BOARD_OFFICE' AND d.code='CHAIR'
                     AND d.scope='LIVING_ORGANISM' AND public.elect_office_effective(a.id,t.at,t.at)
              ) effective ON true GROUP BY t.at
        """, [self.owner.pk, self.owner.pk])
        self.assertEqual(facts, {"holders": len(chair_indices), "sole": None},
                         "The real office history must contain exactly the intended number of effective Chairs.")
        # Include both putative holders and the Circle Coordinator; the author keeps a separate right.
        readers = [signed_in(self.people[index]) for index in sorted({1, *chair_indices})]
        for client in readers:
            page = client.get(self.url)
            self.assertNotContains(page, "Mark as prepared", msg_prefix="No individual holds the Board Chair authority.")
        self.window = cq.mark_prepared(**self.scope, request_uuid=uuid4())
        for person in self.people[:2]:
            self.confirm_as(person)
        with clarity_support.elapsed_clarity(self.window, response_due=False):
            for client in readers:
                page = client.get(self.url)
                self.assertNotContains(page, self.endpoint("discussion_clarity_proceed"))
                self.assertNotContains(page, self.endpoint("discussion_clarity_extend"))
                self.assertNotContains(page, "?clarity=close")
        self.confirm_as(self.people[3])
        openings = [a for a in self.actions() if a["operation"] in ("PROCEED", "OPEN_VOTE")]
        self.assertEqual([(a["operation"], a["authority_source"]["kind"]) for a in openings],
                         [("OPEN_VOTE", "RULE")], "All four clear members must open exactly one ballot by rule.")
        self.assertEqual(cq.phase(**self.scope)["phase"], "VOTING")

    def test_no_chair_reserves_opening_to_the_rule(self):
        self.assert_no_single_chair(())

    def test_two_effective_chairs_reserve_opening_to_the_rule(self):
        self.assert_no_single_chair((1, 3))

    def test_01_member_declares_a_conflict_with_their_own_basis(self):
        self.journey(chair_indices=(1,), coordinator_index=2)
        member = signed_in(self.people[0])
        route = "discussion_clarity_conflict"
        initial = member.get(self.url)
        self.assertContains(initial, "1 of 3 are clear.", count=1, msg_prefix="The count starts with all three seated trustees.")
        self.assertNotContains(initial, self.endpoint(route), msg_prefix="The red choice opens a form without submitting.")
        opened = self.open_vote_form(initial, "Declare a conflict")
        fields = self.fields(opened, route)
        markup = Markup(opened.content.decode())
        form, = markup.select("form", action=self.endpoint(route))
        basis, = markup.select("textarea", within=form, name="basis")
        self.assertIn("required", basis["attrs"], "The page must require the member's own reason.")
        before = self.actions()
        for blank in ("", "  \t "):
            denied = member.post(self.endpoint(route), fields | {"basis": blank})
            self.assertContains(denied, "Give the reason for your conflict of interest in your own words.", status_code=400)
            self.assertEqual(self.actions(), before, "A blank reason cannot leave an act.")
        reason = "My firm supplied the quote."
        self.posted(member, "conflict", fields | {"basis": reason})
        self.posted(member, "conflict", fields | {"basis": reason})
        new = self.actions()[len(before):]
        self.assertEqual([(a["operation"], a["actor_id"], a["facts"]["basis"]) for a in new],
                         [("CONFLICT", self.people[0].pk, reason)], "The member's conflict and reason must occur once.")
        own = member.get(self.url)
        line = "You have declared a conflict and stepped out of this decision."
        self.assertContains(own, "1 of 2 are clear.", count=1)
        self.assertContains(own, line, count=1)
        self.assertContains(own, reason, count=1, msg_prefix="The declarer must see their own reason in Clarity.")
        self.assertNotContains(own, "?clarity=conflict", msg_prefix="The declarer cannot open a second declaration.")
        for other in self.people[1:]:
            peer = signed_in(other).get(self.url)
            self.assertContains(peer, "1 of 2 are clear.", count=1)
            self.assertNotContains(peer, reason, msg_prefix="Neither the Chair nor another trustee may receive the reason.")
            self.assertNotContains(peer, line, msg_prefix="A peer must not inherit the declarer's status.")
            self.assertFalse(Markup(peer.content.decode()).select("div", classes=("vote-reason",)),
                             "The Chair and peer must receive no private declaration bubble.")

    def test_02_chair_opens_the_stepped_out_vote(self):
        self.journey(resignation=True, chair_indices=(1,), coordinator_index=2)
        founder, chair, author, leaving = self.people
        for person in (founder, chair):
            self.confirm_as(person)
        from core.models import CircleParticipation
        seat = CircleParticipation.objects.get(circle=self.board, membership__identity=leaving)
        elections.resign_board_place(organism_uuid=self.owner.organism_id, credential=leaving.credential,
                                     participation_uuid=seat.participation_uuid, **self.notice,
                                     provision="Leaving office", request_uuid=uuid4())
        ordinary = signed_in(author)  # The Board Circle Coordinator, with no Chair assignment.
        peer = ordinary.get(self.url)
        url = self.endpoint("discussion_clarity_proceed")
        before_refusal = self.actions()
        denied = ordinary.post(url, self.forced(peer, "PROCEED"))
        self.assertEqual(denied.status_code, 409, "A peer cannot open this vote with a signed form.")
        self.assertEqual(self.actions(), before_refusal, "A peer’s forced request cannot open a ballot.")
        self.assertContains(peer, "or sooner if the Board Chair opens it.")
        self.assertNotContains(peer, url, msg_prefix="The Circle Coordinator may read D4 but has no Proceed command.")
        client = signed_in(chair)
        page = client.get(self.url)
        self.assertContains(page, "3 of 3 are clear.", count=1)
        self.assertContains(page, "or you can open it now.")
        self.assertContains(page, "Proceed to vote", count=1)
        before = self.actions()
        self.posted(client, "proceed", self.fields(page, "discussion_clarity_proceed"))
        new = self.actions()[len(before):]
        self.assertEqual([(a["operation"], a["actor_id"], a["authority_source"]["route"]) for a in new],
                         [("PROCEED", chair.pk, "BOARD_CHAIR")], "The Chair opens one ballot in the appointed office.")
        self.assertEqual(cq.phase(**self.scope)["phase"], "VOTING")

    def test_03_partial_clarity_waits_for_the_deadline(self):
        self.journey(size=4, chair_indices=(1,), coordinator_index=2)
        for person in self.people[:2]:
            self.confirm_as(person)
        client = signed_in(self.people[1])
        page = client.get(self.url)
        route = "discussion_clarity_proceed"
        self.assertContains(page, "3 of 4 are clear.", count=1)
        self.assertNotContains(page, self.endpoint(route), msg_prefix="A partial count cannot offer an early Proceed.")
        before = self.actions()
        forced = client.post(self.endpoint(route), self.forced(page, "PROCEED"))
        self.assertEqual(forced.status_code, 409, "The same early command must be refused at the writer.")
        self.assertEqual(self.actions(), before, "An early attempt must leave the Clarity window intact.")
        with clarity_support.elapsed_clarity(self.window, response_due=False):
            ready = client.get(self.url)
            self.assertContains(ready, "Proceed to vote", count=1, msg_prefix="Three of four suffice after the deadline.")
            self.posted(client, "proceed", self.fields(ready, route))
            self.assertEqual(cq.phase(**self.scope)["phase"], "VOTING")

    def test_04_only_the_chair_can_extend_and_confirm_closure(self):
        self.journey(chair_indices=(1,), coordinator_index=2)
        chair = signed_in(self.people[1])
        member = signed_in(self.people[2])  # Circle Coordinator, distinct from the Chair.
        close = "discussion_clarity_close"
        extend = "discussion_clarity_extend"
        early = chair.get(self.url)
        self.assertNotContains(early, self.endpoint(extend))
        self.assertNotContains(early, "?clarity=close")
        with clarity_support.elapsed_clarity(self.window, response_due=False):
            before = self.actions()
            peer = member.get(self.url + "?clarity=close")
            for op in ("CLOSE", "EXTEND"):
                endpoint = self.endpoint("discussion_clarity_" + op.lower())
                self.assertNotContains(peer, endpoint, msg_prefix="The Circle Coordinator has no Chair command.")
                denied = member.post(endpoint, self.forced(peer, op, confirmed=op == "CLOSE"))
                self.assertEqual(denied.status_code, 409, "A signed form must recheck the effective Board office.")
            self.assertEqual(self.actions(), before, "The forced commands must leave both the head and ledger untouched.")
            initial = chair.get(self.url)
            self.assertNotContains(initial, self.endpoint(close), msg_prefix="The initial page must not issue a close POST.")
            close_link, = [n["attrs"]["href"] for n in Markup(initial.content.decode()).select("a")
                           if n["text"].strip() == "Close without a vote"]
            preview = chair.get(close_link)
            self.assertContains(preview, "Clarity will end. No ballot will open on this Suggestion.")
            cancel, = [n["attrs"]["href"] for n in Markup(preview.content.decode()).select("a") if n["text"].strip() == "Cancel"]
            self.assertEqual(cancel, self.url + "#vote")
            chair.get(cancel)
            unconfirmed = chair.post(self.endpoint(close), self.forced(initial, "CLOSE"))
            self.assertEqual(unconfirmed.status_code, 400, "The close route requires the signed confirmation.")
            self.assertEqual(self.actions(), before, "Reading, cancelling or bypassing the prompt must record nothing.")
            previous = cq.phase(**self.scope)["deadline"]
            self.posted(chair, "extend", self.fields(initial, extend))
            extended = self.actions()[-1]
            self.assertEqual((extended["operation"], extended["authority_source"]["route"]), ("EXTEND", "BOARD_CHAIR"))
            self.assertGreater(cq.phase(**self.scope)["deadline"], previous, "Extend must move the deadline forward.")
            after_extend = self.actions()
            stale = chair.post(self.endpoint(close), self.fields(preview, close))
            self.assertContains(stale, "The Clarity window or the wording has changed", status_code=409,
                                msg_prefix="Extending Clarity must expire the earlier close confirmation.")
            self.assertEqual(self.actions(), after_extend, "The expired confirmation must leave the extended window open.")
        with clarity_support.elapsed_clarity(extended["act_uuid"], response_due=False):
            current = chair.get(self.url + "?clarity=close")
            self.posted(chair, "close", self.fields(current, close))
            events = self.actions()
            self.assertEqual((events[-1]["operation"], events[-1]["facts"]["phase"], events[-1]["facts"]["ballot"]),
                             ("CLOSE", "CLOSED", None), "The confirmed close must end the Suggestion without a ballot.")
            self.assertEqual(events[-1]["authority_source"]["route"], "BOARD_CHAIR")
            self.assertFalse(any(a["operation"] in ("OPEN_VOTE", "PROCEED") for a in events))

    def test_05_only_the_author_can_confirm_withdrawal(self):
        self.journey()
        chair = signed_in(self.people[1])
        author = signed_in(self.people[2])
        route = "discussion_clarity_withdraw"
        before = self.actions()
        ordinary = chair.get(self.url)
        self.assertNotContains(ordinary, "Withdraw Suggestion", msg_prefix="The Board office alone may not withdraw the author's motion.")
        preview = chair.get(self.url + "?clarity=withdraw")
        self.assertNotContains(preview, self.endpoint(route), msg_prefix="Even the direct preview may not issue a token.")
        denied = chair.post(self.endpoint(route), self.forced(preview, "WITHDRAW", confirmed=True))
        self.assertContains(denied, "Only the author can withdraw this Suggestion.", status_code=409)
        initial = author.get(self.url)
        self.assertNotContains(initial, self.endpoint(route), msg_prefix="The author must see a prompt before submitting.")
        link, = [n["attrs"]["href"] for n in Markup(initial.content.decode()).select("a") if n["text"].strip() == "Withdraw Suggestion"]
        prompt = author.get(link)
        self.assertContains(prompt, "Your Suggestion will be recorded as withdrawn. No ballot will open.")
        cancel, = [n["attrs"]["href"] for n in Markup(prompt.content.decode()).select("a") if n["text"].strip() == "Cancel"]
        self.assertEqual(cancel, self.url + "#vote")
        author.get(cancel)
        unconfirmed = author.post(self.endpoint(route), self.forced(initial, "WITHDRAW"))
        self.assertEqual(unconfirmed.status_code, 400, "Posting without the signed withdrawal confirmation must fail.")
        self.assertEqual(self.actions(), before, "The preview, cancellation and forced requests must record nothing.")
        self.posted(author, "withdraw", self.fields(prompt, route))
        final = self.actions()
        self.assertEqual([(a["operation"], a["actor_id"]) for a in final[len(before):]],
                         [("WITHDRAW", self.people[2].pk)], "The author must record exactly one withdrawal.")
        self.assertEqual(cq.phase(**self.scope)["phase"], "WITHDRAWN")

    def test_06_stale_clarity_conflict_cannot_become_a_ballot_conflict(self):
        self.journey()
        client = signed_in(self.people[0])
        route = "discussion_clarity_conflict"
        opened = self.open_vote_form(client.get(self.url), "Declare a conflict")
        fields = self.fields(opened, route)
        for person in self.people[:2]:
            self.confirm_as(person)
        self.assertEqual(cq.phase(**self.scope)["phase"], "VOTING")
        before = self.actions()
        denied = client.post(self.endpoint(route), fields | {"basis": "I supplied a quote."})
        self.assertEqual(denied.status_code, 409, "A Clarity declaration cannot silently become a voting declaration.")
        page = Markup(denied.content.decode())
        self.assertEqual([h["text"] for h in page.select("h1")], ["Your vote action could not be recorded."])
        alert, = page.select("div", role="alert")
        self.assertEqual([p["text"] for p in page.select("p", within=alert)], [
            "The Clarity window or the wording has changed since you opened this form.",
            "This request recorded no new vote action.",
            "Reopen the page and check the current wording and available actions.",
            "If you need help, contact a Board Coordinator.",
        ], "The refusal must explain the reason, effect, next step and source of help in ordinary words.")
        self.assertEqual(self.actions(), before, "The stale request must leave every recorded act as it was.")
        assert_private(self, denied)


@override_settings(ALLOWED_HOSTS=["testserver"], PASSWORD_HASHERS=FIXTURE_HASHERS)
class AuthorClarityAndConflictTests(BoardClarityFixture, TransactionTestCase):
    """A real Board, its current wording, and both ways participation can open its vote."""

    def conflict(self, person, *, key=None):
        return cq.declare_conflict(**(self.scope | {"credential": person.credential}),
                                   basis="My firm supplied the estimate.", request_uuid=key or uuid4())

    def records(self):
        return discussion._rows("""
            SELECT a.act_uuid,a.operation,a.actor_id,a.facts,a.request,a.authority_source,
                   a.execution_xid::text AS xid,e.actor_state,e.actor_id AS event_actor,
                   e.entry_mode,e.enterer_identity_id AS enterer
              FROM public.core_cqact a
              JOIN public.core_livingorganismevent e ON e.event_uuid=a.act_uuid
             WHERE a.thread_uuid=%s ORDER BY a.ordinal
        """, [self.thread])

    def test_author_count_and_controls_at_start(self):
        self.journey()
        client = signed_in(self.people[2])
        page = client.get(self.url)
        self.assertContains(page, "1 of 3 are clear.", count=1, msg_prefix="The eligible author is already clear.")
        self.assertEqual(self.stored_responses(), [], "The first display must not create a response.")
        self.assertNotContains(page, self.endpoint("discussion_confirm_clarity"))
        self.assertNotContains(page, "?clarity=question", msg_prefix="Authoring supplies clarity.")
        preview = client.get(self.url + "?clarity=question")
        self.assertNotContains(preview, self.endpoint("discussion_clarity_question"),
                               msg_prefix="Even a direct visit cannot give the author a question form.")
        peer = signed_in(self.people[0]).get(self.url)
        self.assertContains(peer, self.endpoint("discussion_confirm_clarity"))
        question = self.open_vote_form(peer, "Ask a question")
        self.assertContains(question, self.endpoint("discussion_clarity_question"))

    def test_author_posts_are_refused_without_a_response_or_reply(self):
        self.journey()
        client = signed_in(self.people[2])
        page = client.get(self.url)
        before = self.records()
        for operation, route in (("CONFIRM", "discussion_confirm_clarity"), ("QUESTION", "discussion_clarity_question")):
            with self.subTest(operation=operation):
                token = discussion_forms.issue_form(page.wsgi_request, operation, self.owner.organism_id,
                    self.board.circle_uuid, thread=self.thread, version=self.topic["version"])
                response = client.post(self.endpoint(route), {"vote_command": token, "markdown": "Which invoice?"})
                self.assertContains(response, "You are already clear as the author. You can edit the wording or reply to the discussion.",
                                    status_code=409, msg_prefix="A valid author token must reach the writer's own refusal.")
                self.assertEqual((self.records(), self.replies()), (before, []),
                                 "Neither a vote receipt, an event nor an ordinary reply may survive the refusal.")

    def test_reword_keeps_only_the_author_clear_and_last_peer_opens(self):
        self.journey()
        first, last, author = self.people
        self.confirm_as(first)
        updated = discussion_posts.edit_post(**self.args, thread_uuid=self.thread, post_uuid=self.topic["post"],
            predecessor=self.topic["version"], request_uuid=uuid4(), markdown="Approve only the second invoice.")
        version = updated["version"]
        page = signed_in(first).get(self.url)
        self.assertContains(page, "1 of 3 are clear.", count=1, msg_prefix="Only the author remains clear on an edit.")
        self.assertEqual([r["version"] for r in self.stored_responses()], [str(self.topic["version"])],
                         "The new wording must have no explicit confirmations.")
        for person in (last, first):
            receipt = cq.confirm_clarity(**(self.scope | {"credential": person.credential}),
                                         version_uuid=version, request_uuid=uuid4())
        self.assertEqual(cq.phase(**self.scope)["phase"], "VOTING", "Two peers plus the eligible author must open voting.")
        confirmations = [r for r in self.records() if r["operation"] == "CONFIRM"]
        self.assertEqual([r["actor_id"] for r in confirmations], [first.pk, last.pk, first.pk],
                         "The author must never acquire a fabricated confirmation.")
        opening, = [r for r in self.records() if r["operation"] == "OPEN_VOTE"]
        self.assertEqual((opening["facts"]["version"], opening["authority_source"]["cause"]), (str(version), str(receipt)))
        statuses = {r["member"]["person"]: r["response"] for r in confirmations[-1]["facts"]["responses"]}
        self.assertEqual(statuses, {author.pk: "AUTHOR_CLEAR", first.pk: "CONFIRMED", last.pk: "CONFIRMED"},
                         "The opening snapshot must distinguish authorship from the two actual responses.")

    def assert_conflict_opening(self, declarer, remaining, key, previous):
        basis = "My firm supplied the estimate."
        with transaction.atomic():
            self.assertEqual(self.conflict(declarer, key=key), key, "The conflict and its rule child must commit together.")
            created = self.records()[len(previous):]
            self.assertEqual([act["operation"] for act in created], ["CONFLICT", "OPEN_VOTE"],
                             "The declaration must cause exactly one immediate rule opening.")
            parent, child = created
            self.assertEqual((parent["act_uuid"], parent["actor_id"], parent["facts"]["basis"]),
                             (key, declarer.pk, basis), "The declaration must retain its member and their reason.")
            self.assertEqual(parent["authority_source"], {"kind": "MEMBER", "reference": declarer.pk, "route": "CONFLICT"})
            self.assertEqual(child["authority_source"], {"kind": "RULE", "reference": "HG:BR2",
                             "route": "CLARITY_FULL_PARTICIPATION", "cause": str(key)}, "Only the rule opens this ballot.")
            self.assertEqual((child["request"]["trigger_act"], child["xid"]), (str(key), parent["xid"]))
            self.assertEqual((child["actor_state"], child["event_actor"], child["entry_mode"], child["enterer"]),
                             ("LIVING_ORGANISM", None, "TRANSPORT", declarer.pk), "The declarer transports the rule's event.")
            self.assertEqual({entry["person"] for entry in child["facts"]["roster"]}, {person.pk for person in remaining})
            self.assertNotIn("basis", child["facts"], "The private declaration field must stay off the ballot.")
            self.assertNotIn(basis, str(child["facts"]), "Nested ballot facts must also omit the declaration text.")
            self.assertEqual(cq.phase(**self.scope)["phase"], "VOTING")
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS cq_complete IMMEDIATE")
                cursor.execute("SET CONSTRAINTS cq_complete DEFERRED")
        self.assertEqual(self.conflict(declarer, key=key), key, "An exact replay must return the first receipt.")
        self.assertEqual(self.records(), previous + created, "A replay must produce no further declaration, event or opening.")

    def test_last_conflict_opens_with_the_author_and_replays(self):
        self.journey(size=4, chair_indices=(1,), coordinator_index=2)
        first, second, author, declarer = self.people
        self.confirm_as(first)
        self.confirm_as(second)
        self.assertEqual(cq.phase(**self.scope)["phase"], "CLARITY")
        self.assert_conflict_opening(declarer, (first, second, author), uuid4(), self.records())
        opening = "All 3 eligible Board members were clear, so voting opened on this wording."
        personal = "You have declared a conflict and stepped out of this decision."
        basis = "My firm supplied the estimate."
        own = signed_in(declarer).get(self.url)
        self.assertContains(own, opening, count=1)
        self.assertContains(own, basis, count=1, msg_prefix="The declarer's reason survives into the open ballot.")
        self.assertContains(own, personal, count=1)
        for other in (first, second):  # An ordinary trustee and the seated Chair.
            peer = signed_in(other).get(self.url)
            self.assertContains(peer, opening, count=1)
            self.assertNotContains(peer, basis, msg_prefix="The private reason must never enter another reader's page.")
            self.assertNotContains(peer, personal)

    def test_recused_author_leaves_the_count_and_last_conflict_can_open(self):
        self.journey(size=5)
        first, second, author, third, declarer = self.people
        self.conflict(author)
        page = signed_in(first).get(self.url)
        self.assertContains(page, "0 of 4 are clear.", count=1, msg_prefix="A recused author contributes no implicit clarity.")
        for person in (first, second, third):
            self.confirm_as(person)
        self.assert_conflict_opening(declarer, (first, second, third), uuid4(), self.records())

    def test_last_conflict_leaving_two_records_only_the_declaration(self):
        self.journey()
        first, declarer, author = self.people
        self.confirm_as(first)
        before = self.records()
        self.conflict(declarer)
        self.assertEqual([r["operation"] for r in self.records()[len(before):]], ["CONFLICT"],
                         "Two clear remaining members cannot cause an opening or a no-ballot result.")
        self.assertEqual(cq.phase(**self.scope)["phase"], "CLARITY")
        self.assertContains(signed_in(author).get(self.url), "2 of 2 are clear.", count=1)

    def test_conflict_with_an_outstanding_question_stays_in_clarity(self):
        self.journey(size=4)
        first, questioner, _, declarer = self.people
        self.confirm_as(first)
        self.confirm_as(questioner)
        cq.raise_clarity_question(**(self.scope | {"credential": questioner.credential}),
                                  version_uuid=self.topic["version"], request_uuid=uuid4())
        before = self.records()
        self.conflict(declarer)
        self.assertEqual([r["operation"] for r in self.records()[len(before):]], ["CONFLICT"],
                         "A remaining trustee's latest question prevents the rule from opening.")
        self.assertEqual(cq.phase(**self.scope)["phase"], "CLARITY")
        self.assertContains(signed_in(first).get(self.url), "2 of 3 are clear.", count=1)

    def test_conflict_with_a_silent_remaining_member_stays_in_clarity(self):
        self.journey(size=4)
        first, _, _, declarer = self.people
        self.confirm_as(first)
        before = self.records()
        self.conflict(declarer)
        self.assertEqual([r["operation"] for r in self.records()[len(before):]], ["CONFLICT"],
                         "A conflict with a silent eligible peer must leave the decision in Clarity.")
        self.assertEqual(cq.phase(**self.scope)["phase"], "CLARITY")

    def test_declarer_cannot_turn_an_open_question_into_a_rule_opening(self):
        self.journey(size=4)
        first, second, _, declarer = self.people
        cq.raise_clarity_question(**(self.scope | {"credential": declarer.credential}),
                                  version_uuid=self.topic["version"], request_uuid=uuid4())
        self.confirm_as(first)
        self.confirm_as(second)
        before = self.records()
        self.conflict(declarer)
        self.assertEqual([r["operation"] for r in self.records()[len(before):]], ["CONFLICT"],
                         "An outstanding question on this wording cannot trigger the conflict opening.")
        self.assertEqual(cq.phase(**self.scope)["phase"], "CLARITY")

    def test_writer_refuses_both_author_responses(self):
        self.journey()
        author = self.people[2]
        for op in ("CONFIRM", "QUESTION"):
            key = uuid4()
            with self.subTest(operation=op):
                with self.assertRaises(IntegrityError, msg="The storage writer must itself refuse the author.") as error:
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute("""INSERT INTO public.core_cqact
                            (act_uuid,organism_id,board_uuid,thread_uuid,operation,actor_id,actor_epoch,received_at,request)
                            SELECT %s,organism_id,board_uuid,thread_uuid,%s,%s,%s,public.intevia_clock_statement(),
                                   jsonb_build_object('version',%s::uuid)
                            FROM public.core_cqact WHERE act_uuid=%s""",
                            [key, op, author.pk, author.access_epoch, self.topic["version"], self.start_act])
                self.assertEqual(error.exception.__cause__.diag.message_primary, "CQ_AUTHOR_CLEAR",
                                 "A refused author response must reach the specific authorship check.")
                self.assertFalse(LivingOrganismEvent.objects.filter(event_uuid=key).exists())
        self.assertEqual(self.stored_responses(), [], "Direct writes cannot manufacture an author response.")

    def test_marked_conflict_requires_a_matching_child(self):
        self.journey(size=4)
        first, second, _, declarer = self.people
        self.confirm_as(first)
        self.confirm_as(second)
        before = self.records()
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("ALTER TABLE public.core_cqact DISABLE TRIGGER cq_vote_followup")
            with self.assertRaises(IntegrityError, msg="A marked declaration must have a complete child.") as error:
                with transaction.atomic():
                    self.conflict(declarer)
                    cursor.execute("SET CONSTRAINTS cq_complete IMMEDIATE")
            self.assertEqual(error.exception.__cause__.diag.message_primary, "CQ_COMPLETE")
            self.assertEqual(self.records(), before, "The missing child must also undo the declaration.")
            cursor.execute("ALTER TABLE public.core_cqact ENABLE TRIGGER cq_vote_followup")
        self.assert_conflict_opening(declarer, (first, second, self.people[2]), uuid4(), before)

    def test_0118_reverses_to_the_five_exact_0117_bodies(self):
        known = (
            ("public.cq_phase_facts(bigint,uuid,uuid,text,jsonb,bigint,timestamptz)",
             (10796, "4f2afac2d23f6f5ffc82fb1292325c392d384e60734c5f5ece0cc499524bdddc"),
             (11780, "e01827c071a370ff03d8438aeed5ebea52e44c5b975ae09909f84c68e90c403c")),
            ("public.cq_open_source(bigint,uuid,uuid,bigint,bigint,jsonb)",
             (776, "0bba7e8d526aa151f3fe2a7c5fc0fc4bd4731b2b9a2a75fe6f43a337d6aa2e9a"),
             (803, "f2971d236ed135cc6a43a0198acdd6728eb1cfd8763611bdcfb01107159c68c6")),
            ("public.cq_vote_followup()",
             (1218, "48bf4c6444e05daaff92a1a370dc88f4a6193d58ccdde5522ec2103d1d69d43b"),
             (1287, "d83877525c46c36ffb02281a900e56029b832f8ff586138b273ecef93356d3e3")),
            ("public.cq_complete()",
             (6506, "2ef23724983d44ef5af47f8897b4051ee0d3f76c0c6b8ee82da48edba8f2bfe6"),
             (6522, "b1d35a0ba1be2445dd7513dcaebf65c90d6ee037386aa837ca16bce45d07ccc1")),
            ("public.cq_source(bigint,uuid,uuid,text,bigint,timestamptz)",
             (7015, "3dcef46dadf792068cafdd409f237e16dd74d4c1ca764c5a0bfdd944ff33f84f"),
             (7143, "29eb48e8d128ac4ae5693444082ffa0dde0f76b1403cc98ca31da6935eb553e1")),
        )

        def catalogue():
            with connection.cursor() as cursor:
                result = []
                for signature, _, _ in known:
                    cursor.execute("""SELECT octet_length(convert_to(prosrc,'UTF8')),
                                      encode(sha256(convert_to(prosrc,'UTF8')),'hex') FROM pg_proc
                                      WHERE oid=to_regprocedure(%s)""", [signature])
                    result.append(cursor.fetchone())
                return result

        previous, current = [old for _, old, _ in known], [new for _, _, new in known]
        executor = MigrationExecutor(connection)
        edge = executor.loader.get_migration("core", "0118_author_clarity_and_conflict")
        state = executor.loader.project_state(edge.dependencies)
        self.assertEqual(catalogue(), current, "Head must contain the five independently pinned 0118 bodies.")
        with transaction.atomic():
            executor.unapply_migration(state, edge)
            self.assertEqual(catalogue(), previous, "Reversal must restore every byte present at 0117.")
            executor.apply_migration(state, edge)
            self.assertEqual(catalogue(), current, "Forward must reinstall the five identical 0118 bodies.")
            transaction.set_rollback(True)
        self.assertEqual(catalogue(), current, "The witness must leave head intact.")

    test_0118_reverses_to_the_five_exact_0117_bodies.ISOLATION_FULL_CAPTURE = True
