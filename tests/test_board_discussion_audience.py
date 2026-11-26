"""C1-467 A. Board discussion visibility and the independent database posting boundary."""

import json
from importlib import import_module
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from core.models import Circle, CircleParticipation, OrganismMembership
from src.intevia.services import circle_commands, cq, cq_reads, discussion, discussion_posts, elections
from src.intevia.services import organism_membership_service as memberships
from src.intevia.services.organism_authority import S015Authority
from tests import cq_authority_support as seats
from tests import election_support
from tests import test_c1_rejoin as rejoin
from tests import test_c1b_departure as departure
from tests.s015_0021_support import database_now, register_test_learner
from tests.test_law_acts import FIXTURE_BOARD_MINIMUM, add_member, ordinary_birth
from tests.test_office_record_services import fixture_membership_state
from tests.test_uc2_pages import activate_trustees, seated_board


LEVELS = ("MEMBERS", "PROBATIONARY", "COORDINATORS")
SOURCE = "public.discp_source(jsonb,bigint,timestamptz)"
AUDIENCE = "public.disc_audience(bigint,bigint,bigint,text,timestamptz)"


def one(sql, args=()):
    """Read one database row; SQL NULL is a meaningful result in these tests."""
    with connection.cursor() as cursor:
        cursor.execute(sql, args)
        row, = cursor.fetchall()
        return row


def audiences(owner, circle, reader):
    """Compare the two functions for each stored category level at one statement cut."""
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT level,
                   public.disc_read_audience(%s,%s,%s,level,public.intevia_clock_statement()),
                   public.disc_audience(%s,%s,%s,level,public.intevia_clock_statement())
            FROM unnest(%s::text[]) AS levels(level)
        """, [owner.pk, circle, reader.pk, owner.pk, circle, reader.pk, list(LEVELS)])
        return {level: (read, old) for level, read, old in cursor.fetchall()}


def scope(owner, person, circle):
    return {"organism_uuid": owner.organism_id, "credential": person.credential,
            "circle_uuid": circle.circle_uuid}


def topic_at(owner, circle, author, code="DISCUSSION", kind="TOPIC"):
    """Start a real thread so the direct REPLY probe clears the parent/category scope check."""
    args = scope(owner, author, circle)
    listing = discussion.board_categories(**args)
    category, = (r for r in listing["categories"] if r["functional_code"] == code)
    topic = discussion_posts.start_topic(**args, category_uuid=category["category_uuid"],
        thread_uuid=uuid4(), post_uuid=uuid4(), request_uuid=uuid4(), topic_type=kind,
        title="Community room", markdown="Discuss the repair.")
    return listing["board"], category["category_uuid"], topic


def post_source(person, site, operation):
    board_uuid, category_uuid, topic = site
    payload = {"operation": operation, "request": str(uuid4()), "board": str(board_uuid),
               "category": str(category_uuid), "post": str(uuid4()),
               "thread": str(topic["thread"] if operation == "REPLY" else uuid4())}
    # Ask the authority function itself. The command gateway has an earlier category check.
    return one("""SELECT source IS NULL, source->>'kind'
        FROM (SELECT public.discp_source(%s::jsonb,%s,public.intevia_clock_statement()) AS source) AS decision""",
        [json.dumps(payload), person.pk])


class BoardDiscussionAudienceTests(TransactionTestCase):
    def assert_levels(self, owner, circle, reader, *, member, welcome, coordinator=False):
        observed = audiences(owner, circle.pk, reader)
        for read, old in audiences(owner, None, reader).values():
            self.assertEqual(read, old, "The organisation-wide board must retain its audience for this member.")
        self.assertEqual(observed["COORDINATORS"], (coordinator, coordinator),
                         "Only the existing local office may read a Coordination category.")
        self.assertEqual(observed["MEMBERS"][0], member, "The ordinary Board category has the wrong audience.")
        self.assertEqual(observed["PROBATIONARY"][0], welcome, "The Board Welcome level has the wrong audience.")
        return observed

    def test_unseated_member_reads_but_has_no_posting_source(self):
        """Remy and a trustee see the Board; only the trustee has a posting source."""
        owner, board, founder, coordinator, trustee = seated_board()
        remy = add_member(owner)
        self.assertFalse(CircleParticipation.objects.filter(circle=board, membership__identity=remy).exists(),
                         "The reader must belong to the organisation without ever holding this Board place.")
        observed = self.assert_levels(owner, board, remy, member=True, welcome=True)
        for level in LEVELS[:2]:
            self.assertFalse(observed[level][1], "Reading the Board must leave the original audience closed to Remy.")
        self.assert_levels(owner, board, coordinator, member=True, welcome=True, coordinator=True)
        trustee_levels = self.assert_levels(owner, board, trustee, member=True, welcome=True)
        self.assertTrue(all(trustee_levels[level][1] for level in LEVELS[:2]),
                        "A seated trustee must retain both original audience levels.")
        site = topic_at(owner, board, founder)
        for op in ("START", "REPLY"):
            with self.subTest(operation=op):
                self.assertEqual(post_source(trustee, site, op), (False, "MEMBER"),
                                 "The seated trustee must obtain an ordinary member source for this command.")
                self.assertEqual(post_source(remy, site, op), (True, None), "Reading does not give Remy a Board posting source.")

    def test_restricted_and_suspended_members_read_without_a_source(self):
        owner, board, founder, _, _ = seated_board()
        site = topic_at(owner, board, founder)
        for state in (OrganismMembership.State.RESTRICTED, OrganismMembership.State.SUSPENDED):
            with self.subTest(state=state):
                reader = add_member(owner)
                fixture_membership_state(owner, founder, reader, state)
                self.assertEqual(one("SELECT public.disc_member_state(%s,%s,public.intevia_clock_statement())",
                                     [owner.pk, reader.pk]), (state,), "The member must really be in the named state.")
                self.assert_levels(owner, board, reader, member=True, welcome=True)
                for op in ("START", "REPLY"):
                    self.assertEqual(post_source(reader, site, op), (True, None), "A limited member has no Board posting source.")

    def test_board_welcome_read_needs_both_switch_and_local_opening(self):
        owner, board, founder, coordinator, _ = seated_board()
        reader = add_member(owner)
        fixture_membership_state(owner, founder, reader, OrganismMembership.State.PROBATIONARY)
        lo = {"credential": founder.credential, "organism_uuid": owner.organism_id}
        self.assert_levels(owner, board, reader, member=False, welcome=False)
        discussion.set_probation_access(**lo, enabled=True, request_uuid=uuid4())
        self.assert_levels(owner, board, reader, member=False, welcome=False)
        discussion.open_circle_welcome(**scope(owner, coordinator, board), request_uuid=uuid4())
        self.assert_levels(owner, board, reader, member=False, welcome=True)
        discussion.set_probation_access(**lo, enabled=False, request_uuid=uuid4())
        self.assert_levels(owner, board, reader, member=False, welcome=False)

    def test_probationary_welcome_read_has_no_board_posting_source(self):
        """PLACE. The old category gate admits the reader, so only the Board place gate refuses."""
        owner, board, founder, coordinator, _ = seated_board()
        reader = add_member(owner)
        fixture_membership_state(owner, founder, reader, OrganismMembership.State.PROBATIONARY)
        discussion.set_probation_access(credential=founder.credential, organism_uuid=owner.organism_id,
                                        enabled=True, request_uuid=uuid4())
        discussion.open_circle_welcome(**scope(owner, coordinator, board), request_uuid=uuid4())
        site = topic_at(owner, board, coordinator, code="WELCOME")
        self.assertEqual(audiences(owner, board.pk, reader)["PROBATIONARY"], (True, True),
                         "The real Welcome category must admit the probationary reader under both audience rules.")
        self.assertEqual(one("SELECT public.disc_board_place_ok(%s,%s,%s,public.intevia_clock_statement())",
                             [owner.pk, board.pk, reader.pk]), (False,), "The reader must lack a continuing Board place.")
        for op in ("START", "REPLY"):
            with self.subTest(operation=op):
                self.assertEqual(post_source(coordinator, site, op), (False, "MEMBER"), "The Welcome command must have a valid target.")
                self.assertEqual(post_source(reader, site, op), (True, None), "A probationary Welcome reader needs a Board place to post.")

    def test_ordinary_circle_and_organisation_keep_their_original_audience(self):
        owner, founder = ordinary_birth()
        lo = {"organism_uuid": owner.organism_id, "credential": founder.credential}
        circle = circle_commands.form(**lo, circle_uuid=uuid4(), kind="GENERIC", purpose="Welcome", request_uuid=uuid4())
        discussion.ensure_board(**scope(owner, founder, circle), request_uuid=uuid4())
        discussion.set_probation_access(**lo, enabled=True, request_uuid=uuid4())
        discussion.open_circle_welcome(**scope(owner, founder, circle), request_uuid=uuid4())
        site = topic_at(owner, circle, founder, code="WELCOME")
        regular = add_member(owner)
        probation = add_member(owner)
        fixture_membership_state(owner, founder, probation, OrganismMembership.State.PROBATIONARY)
        limited = []
        for state in (OrganismMembership.State.RESTRICTED, OrganismMembership.State.SUSPENDED):
            reader = add_member(owner)
            fixture_membership_state(owner, founder, reader, state)
            limited.append(reader)
        outside = register_test_learner(username="ordinary-outsider-" + uuid4().hex)
        for circle_id in (None, circle.pk):
            for person in (founder, regular, probation, *limited, outside):
                with self.subTest(circle=circle_id, reader=person.pk):
                    for read, old in audiences(owner, circle_id, person).values():
                        self.assertEqual(read, old, "A non-Board scope must retain its audience at every stored level.")
        self.assertFalse(CircleParticipation.objects.filter(circle=circle, membership__identity=probation).exists(),
                         "The probationary Welcome control must have no ordinary Circle place either.")
        for op in ("START", "REPLY"):
            self.assertEqual(post_source(probation, site, op), (False, "MEMBER"),
                             "The ordinary Circle's existing Welcome posting permission must survive.")

    def test_foreign_ended_proposed_and_inactive_are_denied(self):
        owner, board, founder, _, _ = seated_board()
        foreign, other_founder = ordinary_birth()
        self.assert_levels(owner, board, other_founder, member=False, welcome=False)
        self.assert_levels(foreign, board, other_founder, member=False, welcome=False)

        reader = add_member(owner)
        membership = OrganismMembership.objects.get(living_organism=owner, identity=reader)
        service = memberships.OrganismMembershipService(authority=seats.authority(), clock=database_now)
        service.leave_organism(departure.DepartureTests().command(membership))
        self.assertEqual(one("SELECT public.disc_member_state(%s,%s,public.intevia_clock_statement())",
                             [owner.pk, reader.pk]), ("ENDED",), "The former member's period must have ended.")
        self.assert_levels(owner, board, reader, member=False, welcome=False)

        # The existing C1 fixture provider authorises opening a real PROPOSED chain.
        applicant = register_test_learner(username="pending-board-reader-" + uuid4().hex)
        authority = S015Authority(provider=rejoin._C1FixtureAuthority())
        proposed = memberships.OrganismMembershipService(authority=authority, clock=database_now)
        at = database_now()
        key = uuid4().hex
        proposed.open_membership(memberships.OpenMembershipCommand(
            credential=founder.credential, request_reference="propose:" + key, idempotency_key=key,
            occurred_at=at, effective_at=at, living_organism_id=owner.organism_id,
            member_identity_id=applicant.identity_id, reason_class="APPLICATION"))
        self.assertEqual(one("SELECT public.disc_member_state(%s,%s,public.intevia_clock_statement())",
                             [owner.pk, applicant.pk]), ("PROPOSED",), "The applicant must have an actual proposed membership.")
        self.assert_levels(owner, board, applicant, member=False, welcome=False)

        member = add_member(owner)
        self.assert_levels(owner, board, member, member=True, welcome=True)
        get_user_model().objects.filter(pk=member.credential_id).update(is_active=False)
        self.assert_levels(owner, board, member, member=False, welcome=False)

    def test_former_trustee_reads_on_membership_alone(self):
        owner, founder = ordinary_birth(offering_choice="FREE", board_seats=4, board_minimum=None)
        notice = election_support.provision(founder, owner, {}, board_minimum=FIXTURE_BOARD_MINIMUM,
            resignations={"Leaving office": {"place": "trustee-3", "effect": "ON_NOTICE"}})
        board = Circle.objects.get(parent_organism=owner, kind="GOVERNANCE")
        trustees = [seats.seat_trustee(owner, founder, board, i) for i in range(1, 4)]
        activate_trustees({"organism_uuid": owner.organism_id, "credential": founder.credential}, board)
        leaving = trustees[-1]
        remy = add_member(owner)
        place = CircleParticipation.objects.get(circle=board, membership__identity=leaving)
        before = database_now()
        self.assertEqual(audiences(owner, board.pk, leaving)["MEMBERS"], (True, True),
                         "The trustee must first hold a readable Board place.")
        elections.resign_board_place(organism_uuid=owner.organism_id, credential=leaving.credential,
                                     participation_uuid=place.participation_uuid,
                                     provision="Leaving office", request_uuid=uuid4(), **notice)
        self.assertEqual(audiences(owner, board.pk, leaving), audiences(owner, board.pk, remy),
                         "Once their place ends the trustee must read on the same terms as Remy.")
        self.assertEqual(audiences(owner, board.pk, leaving)["MEMBERS"], (True, False),
                         "Membership alone must still open the Board after a resignation.")
        self.assertEqual(one("""SELECT public.disc_board_place_ok(%s,%s,%s,%s),
                                    public.disc_board_place_ok(%s,%s,%s,public.intevia_clock_statement())""",
                             [owner.pk, board.pk, leaving.pk, before, owner.pk, board.pk, leaving.pk]),
                         (True, False), "The old cut keeps the place and the current cut sees its recorded end.")

    def test_reading_does_not_enrol_remy_or_authorise_a_ballot_answer(self):
        owner, board, founder, _, _ = seated_board()
        remy = add_member(owner)
        board_uuid, _, topic = topic_at(owner, board, founder, kind="SUGGESTION")
        member_scope = scope(owner, founder, board) | {"thread_uuid": topic["thread"]}
        cq.put_forward(**member_scope, version_uuid=topic["version"], request_uuid=uuid4())
        cq.mark_prepared(**member_scope, request_uuid=uuid4())
        ballot = cq_reads.vote_status(**member_scope)["ballot"]
        self.assertIsNotNone(ballot, "The trustee's Suggestion must have a real open ballot.")
        self.assert_levels(owner, board, remy, member=True, welcome=True)
        with connection.cursor() as cursor:
            cursor.execute("""SELECT (entry->>'person')::bigint
                FROM jsonb_array_elements(public.cq_roster(%s,%s,public.intevia_clock_statement())) AS entry""",
                [board_uuid, topic["thread"]])
            roster = {person for person, in cursor.fetchall()}
        self.assertIn(founder.pk, roster, "The seated author must be present in the electorate.")
        self.assertNotIn(remy.pk, roster, "Opening the Board's discussion cannot add Remy to its electorate.")
        key = uuid4()
        with self.assertRaises(IntegrityError, msg="The database must refuse Remy's ballot answer.") as refused:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("""SELECT public.cq_execute(%s,%s,%s,'ANSWER',%s,%s::jsonb,%s,%s,
                                                          public.intevia_clock_statement())""",
                    [owner.organism_id, board_uuid, topic["thread"], key,
                     json.dumps({"ballot": str(ballot), "choice": "QUALIFIED"}), remy.pk, remy.access_epoch])
        self.assertEqual(refused.exception.__cause__.diag.message_primary, "DISC_UNAVAILABLE",
                         "A nonparticipant must be refused at the Board's existing command audience.")
        self.assertEqual(one("SELECT count(*) FROM public.core_cqact WHERE act_uuid=%s", [key]), (0,),
                         "The refused answer must leave no vote act.")

    def test_whole_family_keeps_the_read_predicate_out_of_database_functions(self):
        """WIDEN. Search raw bodies, including every overload, without matching a function's own header."""
        with connection.cursor() as cursor:
            cursor.execute("""SELECT n.nspname, p.proname
                FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                WHERE strpos(lower(concat_ws(' ',p.prosrc,pg_catalog.pg_get_function_sqlbody(p.oid))), %s) > 0 ORDER BY n.nspname, p.proname""", ["disc_read_audience"])
            readers = cursor.fetchall()
        self.assertEqual(readers, [], "No database function may use the wider discussion-read predicate.")

        # Let this same PostgreSQL server render the historic definition. Only
        # its temporary namespace differs; compare every byte after restoring it.
        boards = import_module("core.migrations.0072_discussion_boards")
        extract = import_module("core.migrations.0076_cq_authority").function
        historic = extract(boards.FORWARD, "disc_audience")
        original = "FUNCTION public.disc_audience("
        self.assertEqual(historic.count(original), 1, "Locate one header for the unchanged 0118 audience.")
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(historic.replace(original, "FUNCTION pg_temp.disc_audience(", 1))
            cursor.execute("""SELECT pg_get_functiondef(%s::regprocedure),
                                     pg_get_functiondef(%s::regprocedure),
                                     (SELECT nspname FROM pg_namespace WHERE oid=pg_my_temp_schema())""",
                           [AUDIENCE, AUDIENCE.replace("public.", "pg_temp.", 1)])
            installed, reference, temporary_schema = cursor.fetchone()
            expected = reference.replace("FUNCTION pg_temp.disc_audience(", original, 1)
            self.assertEqual(installed, expected,
                             "The complete installed disc_audience definition must equal its 0118 definition.")
            transaction.set_rollback(True)

    def test_0119_round_trip_restores_the_exact_posting_guard(self):
        """Exercise the real migration operations in a rolled-back schema transaction."""
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        edge = executor.loader.get_migration("core", "0119_board_discussion_read_audience")
        parent = executor.loader.project_state(edge.dependencies)
        helpers = ["public.disc_read_audience(bigint,bigint,bigint,text,timestamptz)",
                   "public.disc_board_place_ok(bigint,bigint,bigint,timestamptz)"]

        def catalogue():
            return one("""SELECT pg_get_functiondef(%s::regprocedure), pg_get_functiondef(%s::regprocedure),
                                 pg_get_functiondef(to_regprocedure(%s)), pg_get_functiondef(to_regprocedure(%s))""",
                       [SOURCE, AUDIENCE, *helpers])

        posts = import_module("core.migrations.0073_discussion_posts")
        extract = import_module("core.migrations.0076_cq_authority").function
        body = import_module("core.migrations.0081_hard_voting_authority").body
        old_body = body(extract(posts.FORWARD, "discp_source"))
        head = catalogue()
        self.assertTrue(all(head), "Both Board helpers and the two original audience gates must exist at head.")
        with transaction.atomic():
            executor.unapply_migration(parent, edge)
            old_source, old_audience, read, place = catalogue()
            self.assertEqual((read, place), (None, None), "The reverse must remove both new helpers.")
            self.assertEqual(one("SELECT prosrc FROM pg_proc WHERE oid=%s::regprocedure", [SOURCE]),
                             (old_body,), "Reversing must restore the exact posting source received from 0073.")
            self.assertNotEqual(head[0], old_source, "The forward migration must change the posting source.")
            self.assertEqual(head[1], old_audience, "The original audience must stay byte-identical across 0119.")
            executor.apply_migration(parent, edge)
            self.assertEqual(catalogue(), head, "Forward again must restore all four complete definitions.")
            transaction.set_rollback(True)
        self.assertEqual(catalogue(), head, "The round-trip test must leave the received head intact.")

    test_0119_round_trip_restores_the_exact_posting_guard.ISOLATION_FULL_CAPTURE = True
