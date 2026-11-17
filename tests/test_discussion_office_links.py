"""Storage witnesses for the office definition selected when a Circle Topic opens."""

import json
from importlib import import_module
from uuid import UUID, uuid4

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from core import models
from src.intevia.services import circle_commands, discussion, discussion_posts
from src.intevia.services import organism_role_definition_service as definitions
from tests import committee_support, cq_authority_support, discussion_support, election_support
from tests import test_law_acts as laws
from tests.cq_authority_support import authority
from tests.s015_0021_support import database_now, migrate_to_target_with_restore


MARKDOWN = "Who will keep the Treasurer’s accounts?"
ABSENT = (False,) * 6


def office_basis(owner, author):
    document = laws.Document(author, content="[#1] The Treasurer keeps the accounts.")
    proposal = laws.adoption(owner, document)
    adopted = laws.law.OrganismLawService().adopt_law(laws.command(author, proposal))
    if adopted.status != "ADOPT":
        raise AssertionError("The office fixture requires an adopted Treasurer provision.")
    return definitions.DefinitionContent(
        code="TREASURER",
        provisions=(definitions.DefinitionProvision("OFFICE_BASIS", f"law-ref:{document.name}#1",
                                                  str(document.first.version_number)),),
    )


def record_office(owner, author, content, *, prior=None):
    at = database_now()
    key = uuid4().hex
    service = definitions.OrganismRoleDefinitionService(authority=authority(), clock=database_now)
    command = definitions.RecordRoleDefinitionCommand(
        credential=author.credential, living_organism_uuid=owner.organism_id,
        event_action="AMEND" if prior else "DEFINE", request_reference="office-link:" + key,
        idempotency_key=key, occurred_at=at, effective_at=at, content=content, role_definition_uuid=prior,
    )
    return service.record_role_definition(command).role_definition_uuid


def discussion_category(owner, author, circle=None):
    args = dict(credential=author.credential, organism_uuid=owner.organism_id, circle_uuid=circle)
    discussion.ensure_board(**args, request_uuid=uuid4())
    listing = discussion.board_categories(**args)
    category = next(row for row in listing["categories"] if row["functional_code"] == "DISCUSSION")
    return {"board": str(listing["board"]), "organism": str(owner.organism_id),
            "circle": str(circle) if circle else None, "category": str(category["category_uuid"])}


def start(category, topic_type="TOPIC", **fields):
    return {**category, "operation": "START", "request": str(uuid4()), "thread": str(uuid4()),
            "post": str(uuid4()), "type": topic_type, "title": "Treasurer duties", "quote": None,
            "body_fingerprint": discussion_posts.body_fingerprint(MARKDOWN), **fields}


def execute(author, payload, *, received=None):
    """Return the database verdict after exercising the deferred checks, even inside a savepoint."""
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT public.discp_execute(%s::jsonb,%s,%s,%s,%s)",
                           [json.dumps(payload), MARKDOWN, author.pk, author.access_epoch,
                            received if received is not None else database_now()])
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        return "ACCEPTED"
    except IntegrityError as exc:
        return exc.__cause__.diag.message_primary


def recorded(payload):
    """Observe only the six records belonging to this opening request."""
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT EXISTS(SELECT FROM public.core_discpostact WHERE act_uuid=%(request)s),
                   EXISTS(SELECT FROM public.core_livingorganismevent WHERE event_uuid=%(request)s),
                   EXISTS(SELECT FROM public.core_discthread WHERE thread_uuid=%(thread)s),
                   EXISTS(SELECT FROM public.core_discpost WHERE post_uuid=%(post)s),
                   EXISTS(SELECT FROM public.core_discpostversion WHERE version_uuid=%(request)s),
                   EXISTS(SELECT FROM public.core_discbody WHERE version_uuid=%(request)s)
        """, payload)
        return cursor.fetchone()


class DiscussionOfficeLinkTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.owner, self.author = laws.ordinary_birth()
        circle = circle_commands.form(
            credential=self.author.credential, organism_uuid=self.owner.organism_id,
            circle_uuid=uuid4(), kind="GENERIC", purpose="Discuss office duties", request_uuid=uuid4(),
        )
        self.category = discussion_category(self.owner, self.author, circle.circle_uuid)
        self.before_office = database_now()
        self.content = office_basis(self.owner, self.author)
        self.office = record_office(self.owner, self.author, self.content)

    def test_link_requires_eligible_target(self):
        """A Circle Topic can select only a current Board office of its owner that existed at receipt."""
        other, other_author = laws.ordinary_birth()
        foreign = record_office(other, other_author, office_basis(other, other_author))
        successor = record_office(self.owner, self.author, self.content, prior=self.office)
        coordinators = models.OrganismRoleDefinition.objects.filter(
            living_organism=self.owner, code="COORDINATOR", active=True,
        )
        for label, target, received in (
            ("foreign same-code office", foreign, None),
            ("unknown office", uuid4(), None),
            ("superseded office", self.office, None),
            ("wrong kind", coordinators.get(scope="LIVING_ORGANISM").role_uuid, None),
            ("Circle scope", coordinators.get(scope="CIRCLE").role_uuid, None),
            ("not yet known at receipt", successor, self.before_office),
        ):
            with self.subTest(target=label):
                payload = start(self.category, office_definition_uuid=str(target))
                self.assertEqual((execute(self.author, payload, received=received), recorded(payload)),
                                 ("DISC_OFFICE_SELECTION", ABSENT),
                                 f"{label} must refuse without recording an opening or its children.")

        payload = start(self.category, office_definition_uuid=str(successor))
        self.assertEqual(execute(self.author, payload), "ACCEPTED", "The current local office must qualify.")
        self.assertEqual(recorded(payload), (True,) * 6)
        with connection.cursor() as cursor:
            cursor.execute("SELECT office_definition_uuid FROM public.core_discthread WHERE thread_uuid=%s",
                           [payload["thread"]])
            self.assertEqual(str(cursor.fetchone()[0]), str(successor), "Keep the exact selected version.")

    def test_only_circle_topic_start_carries_reference(self):
        """Only an ordinary Circle Topic START may carry a canonical office UUID."""
        target = str(self.office)
        organisation = discussion_category(self.owner, self.author)
        for label, category, subtype in (
            ("organisation board", organisation, "TOPIC"),
            ("question", self.category, "QUESTION"),
            ("suggestion", self.category, "SUGGESTION"),
        ):
            with self.subTest(case=label):
                payload = start(category, subtype)
                self.assertEqual(execute(self.author, payload | {"office_definition_uuid": target}), "DISC_POST_INPUT")
                self.assertEqual(recorded(payload), ABSENT)
                self.assertEqual(execute(self.author, payload), "ACCEPTED", "Omitting the key must admit this start.")

        for value in (None, [target], target.replace("-", "")):
            with self.subTest(malformed=value):
                payload = start(self.category, office_definition_uuid=value)
                self.assertEqual(execute(self.author, payload), "DISC_POST_INPUT")
                self.assertEqual(recorded(payload), ABSENT)

        opening = start(self.category, office_definition_uuid=target)
        self.assertEqual(execute(self.author, opening), "ACCEPTED")
        edit = {**self.category, "operation": "EDIT", "request": str(uuid4()), "thread": opening["thread"],
                "post": opening["post"], "predecessor": opening["request"],
                "body_fingerprint": discussion_posts.body_fingerprint(MARKDOWN)}
        self.assertEqual(execute(self.author, edit | {"office_definition_uuid": target}), "DISC_POST_INPUT",
                         "An accepted EDIT would record a later choice of office.")
        self.assertEqual(execute(self.author, edit), "ACCEPTED", "The same author's unlinked edit must still work.")

    def test_child_must_match_start(self):
        """An opening whose thread omits the selected office is refused with none of its six records left."""
        payload = start(self.category, office_definition_uuid=str(self.office))
        control = start(self.category, office_definition_uuid=str(self.office))
        self.assertEqual(execute(self.author, control), "ACCEPTED",
                         "An unaltered linked start through the same guards must work.")
        with transaction.atomic(), connection.cursor() as cursor:
            # This test-local trigger sorts before discp_input. Only this request's
            # proposed column is changed and all production guards remain enabled.
            cursor.execute("SELECT set_config('intevia_test.office_link_request', %s, true)", [payload["request"]])
            cursor.execute("""
                CREATE FUNCTION public.a21_test_omit_selected_office() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.creation_act::text=current_setting('intevia_test.office_link_request') THEN
                        NEW.office_definition_uuid := NULL;
                    END IF;
                    RETURN NEW;
                END $$;
                CREATE TRIGGER a_omit_selected_office BEFORE INSERT ON public.core_discthread
                    FOR EACH ROW EXECUTE FUNCTION public.a21_test_omit_selected_office();
            """)
            verdict = execute(self.author, payload)
            footprint = recorded(payload)
            transaction.set_rollback(True)
        self.assertEqual((verdict, footprint), ("DISC_RECORD", ABSENT),
                         "A divergent header must roll back its act, event, thread, post, version and body.")

    test_child_must_match_start.ISOLATION_FULL_CAPTURE = True


def public_start_args(owner, actor, circle, category):
    """Build one public opening with stable retry identities in the supplied category."""
    return dict(credential=actor.credential, organism_uuid=owner.organism_id, circle_uuid=circle.circle_uuid,
                category_uuid=UUID(category["category"]), thread_uuid=uuid4(), post_uuid=uuid4(),
                request_uuid=uuid4(), title="Treasurer duties", markdown=MARKDOWN)


def start_verdict(args, **changes):
    """Capture success or the public refusal so a wrong outcome fails the assertion."""
    try:
        return discussion_posts.start_topic(**(args | changes))
    except (discussion.DiscussionRefused, discussion.DiscussionUnavailable) as exc:
        return type(exc), getattr(exc, "code", None), str(exc)
    except IntegrityError as exc:
        return type(exc), exc.__cause__.diag.message_primary


class DiscussionOfficeStartTests(TransactionTestCase):
    def test_start_records_exact_office(self):
        """A trustee's START binds the exact office to the opening and discloses it with the title."""
        owner, scope, people, circle = cq_authority_support.ordinary(3, board=True)
        founder, moderator, author = people
        office = record_office(owner, founder, office_basis(owner, founder))
        version = models.OrganismRoleDefinition.objects.get(role_uuid=office).definition_version
        election_support.enable_voting(scope)
        circle_commands.activate(**scope, circle_uuid=circle.circle_uuid, request_uuid=uuid4())
        cq_authority_support.appoint_board_coordinator(owner, circle, founder, moderator)
        category = discussion_category(owner, author, circle.circle_uuid)
        args = public_start_args(owner, author, circle, category)
        receipt = discussion_posts.start_topic(**args, office_definition_uuid=office)
        self.assertEqual(receipt, dict(act=args["request_uuid"], version=args["request_uuid"],
                                       thread=args["thread_uuid"], post=args["post_uuid"]))

        def legacy_topic(thread):
            """Read exactly the previously exposed projection for this thread."""
            return discussion._rows("SELECT * FROM public.discp_threads WHERE thread_uuid=%s", [thread])[0]

        def detail(reader, thread):
            """Read the same board and thread as the specified member."""
            return discussion_posts.read_thread(credential=reader.credential, organism_uuid=owner.organism_id,
                                                circle_uuid=circle.circle_uuid, thread_uuid=thread)

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT h.office_definition_uuid, a.payload->>'office_definition_uuid',
                       a.fingerprint, public.disc_fingerprint(a.payload), e.payload_fingerprint,
                       e.enterer_identity_id, a.actor_id
                FROM public.core_discthread h
                JOIN public.core_discpostact a ON a.act_uuid=h.creation_act
                JOIN public.core_livingorganismevent e ON e.event_uuid=a.act_uuid
                WHERE h.thread_uuid=%s AND a.act_uuid=%s
            """, [receipt["thread"], receipt["act"]])
            row = cursor.fetchone()
        self.assertIsNotNone(row, "The opening must have a thread, attributed START and event.")
        header, submitted, act_hash, computed_hash, event_hash, enterer, actor = row
        self.assertEqual((header, submitted), (office, str(office)), "The header and START must name the chosen Treasurer.")
        self.assertEqual((act_hash, event_hash), (computed_hash, computed_hash))
        self.assertEqual((enterer, actor), (author.pk, author.pk))

        prior_shape = legacy_topic(receipt["thread"])
        visible = prior_shape | {"office_definition_uuid": office, "office_definition_version": version}
        first = detail(author, receipt["thread"])
        self.assertEqual(first["topic"], visible)
        self.assertEqual((first["posts"][0]["post_uuid"], first["posts"][0]["version_uuid"]),
                         (receipt["post"], receipt["version"]))
        unlinked = discussion_posts.start_topic(**public_start_args(owner, author, circle, category))
        plain = detail(author, unlinked["thread"])
        self.assertEqual(set(plain), {"topic", "posts", "category_retired"})
        self.assertEqual(plain["topic"], legacy_topic(unlinked["thread"]))
        self.assertTrue({"office_definition_uuid", "office_definition_version"}.isdisjoint(plain["posts"][0]))

        def hide(reader, hidden, as_coordinator):
            """Exercise the existing hide or restore route on the opening post."""
            discussion_posts.set_post_hidden(
                credential=reader.credential, organism_uuid=owner.organism_id, circle_uuid=circle.circle_uuid,
                category_uuid=args["category_uuid"], thread_uuid=receipt["thread"], post_uuid=receipt["post"],
                request_uuid=uuid4(), hidden=hidden, as_coordinator=as_coordinator,
            )

        hide(author, True, False)
        author_hidden = detail(author, receipt["thread"])
        self.assertIsNone(author_hidden["posts"][0]["markdown"])
        self.assertEqual(author_hidden["topic"], visible, "An author hiding the body leaves the title visible.")
        hide(moderator, True, True)
        concealed = detail(author, receipt["thread"])
        self.assertEqual(set(concealed), set(plain))
        self.assertEqual(set(concealed["posts"][0]), set(plain["posts"][0]))
        self.assertEqual(concealed["topic"], prior_shape | {"title": None},
                         "A hidden title must reveal neither an office reference nor a placeholder.")
        self.assertEqual(detail(moderator, receipt["thread"])["topic"], visible)
        hide(moderator, False, True)
        self.assertEqual(detail(author, receipt["thread"])["topic"], visible)

    def test_retry_preserves_original_selection(self):
        """Exact retries keep their receipt and original version while added, removed or changed pins conflict."""
        owner, author = laws.ordinary_birth()
        circle = circle_commands.form(credential=author.credential, organism_uuid=owner.organism_id,
                                      circle_uuid=uuid4(), kind="GENERIC", purpose="Office retries", request_uuid=uuid4())
        category = discussion_category(owner, author, circle.circle_uuid)
        content = office_basis(owner, author)
        office = record_office(owner, author, content)
        args = public_start_args(owner, author, circle, category)
        linked = discussion_posts.start_topic(**args, office_definition_uuid=office)
        self.assertEqual(start_verdict(args, office_definition_uuid=office), linked)

        # Seed exactly the old public START wire format, independently of today's adapter.
        old = start(category)
        with transaction.atomic():
            raw_receipt = discussion._rows(
                "SELECT public.discp_execute(%s::jsonb,%s,%s,%s,%s) AS receipt",
                [json.dumps(old), MARKDOWN, author.pk, author.access_epoch, database_now()],
            )[0]["receipt"]
        legacy = {key: UUID(value) for key, value in raw_receipt.items()}
        legacy_args = args | {"request_uuid": UUID(old["request"]), "thread_uuid": UUID(old["thread"]),
                              "post_uuid": UUID(old["post"]), "title": old["title"]}
        successor = record_office(owner, author, content, prior=office)
        openings = models.LivingOrganismEvent.objects.filter(living_organism=owner, action="RECORD_DISCUSSION_POST")
        before = list(openings.order_by("sequence").values_list("event_uuid", "payload_fingerprint"))
        self.assertEqual({key for key, _ in before}, {linked["act"], legacy["act"]})
        self.assertEqual(start_verdict(args, office_definition_uuid=office), linked,
                         "A later office amendment must leave the original request replayable.")
        self.assertEqual(start_verdict(legacy_args), legacy)
        self.assertEqual(start_verdict(legacy_args, office_definition_uuid=None), legacy)
        conflict = (discussion.DiscussionRefused, "DISC_REQUEST_CONFLICT", discussion.MESSAGES["DISC_REQUEST_CONFLICT"])
        for label, submitted, changes in (
            ("removed", args, {}), ("changed", args, {"office_definition_uuid": successor}),
            ("added", legacy_args, {"office_definition_uuid": successor}),
        ):
            with self.subTest(selection=label):
                self.assertEqual(start_verdict(submitted, **changes), conflict,
                                 "Reusing a request for a different office selection must conflict.")
        self.assertEqual(before, list(openings.order_by("sequence").values_list("event_uuid", "payload_fingerprint")))
        pinned_version = models.OrganismRoleDefinition.objects.get(role_uuid=office).definition_version
        topic = discussion_posts.read_thread(credential=author.credential, organism_uuid=owner.organism_id,
                                             circle_uuid=circle.circle_uuid, thread_uuid=linked["thread"])["topic"]
        self.assertEqual((topic.get("office_definition_uuid"), topic.get("office_definition_version")),
                         (office, pinned_version), "Detail preserves the definition originally selected.")

    def test_no_office_authority_bypass(self):
        """Office selection discloses target eligibility only to an author with posting standing in this Circle."""
        owner, outside = laws.ordinary_birth(board_seats=2, board_minimum=None)
        governance = models.Circle.objects.get(parent_organism=owner, kind="GOVERNANCE")
        inside = cq_authority_support.seat_trustee(owner, outside, governance, 1)
        office = record_office(owner, outside, office_basis(owner, outside))
        circle, moderator = discussion_support.independent_circle(owner, outside)
        committee_support.join({"credential": moderator.credential, "organism_uuid": owner.organism_id}, circle, inside)
        category = discussion_category(owner, inside, circle.circle_uuid)
        args = public_start_args(owner, outside, circle, category)
        missing = uuid4()
        unavailable = (discussion.DiscussionRefused, "DISC_UNAVAILABLE", discussion.UNAVAILABLE)
        for label, target in (("current", office), ("unknown", missing)):
            with self.subTest(outsider_target=label):
                self.assertEqual(start_verdict(args, office_definition_uuid=target), unavailable,
                                 "The Organisation Coordinator outside this Circle cannot probe the office.")
        args["credential"] = inside.credential
        self.assertEqual(start_verdict(args, office_definition_uuid=missing),
                         (discussion.DiscussionRefused, "DISC_OFFICE_SELECTION",
                          "Choose a current Board office in this organisation."))
        self.assertEqual(start_verdict(args, office_definition_uuid=str(office)),
                         (discussion.DiscussionRefused, "DISC_INPUT", discussion.MESSAGES["DISC_INPUT"]))
        self.assertFalse(models.LivingOrganismEvent.objects.filter(event_uuid=args["request_uuid"]).exists())
        self.assertEqual(discussion_posts.start_topic(**args, office_definition_uuid=office)["act"],
                         args["request_uuid"], "The admitted author can open this Topic about the local office.")


class DiscussionOfficeSearchTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.owner, self.coordinator = laws.ordinary_birth()
        self.scope = {"credential": self.coordinator.credential, "organism_uuid": self.owner.organism_id}
        self.circle = circle_commands.form(**self.scope, circle_uuid=uuid4(), kind="GENERIC",
                                          purpose="Discuss the Board offices", request_uuid=uuid4())
        self.board = discussion_category(self.owner, self.coordinator, self.circle.circle_uuid)
        self.member = laws.add_member(self.owner)
        self.place = committee_support.join(self.scope, self.circle, self.member)
        document = laws.Document(self.coordinator, content="[#1] The Treasurer keeps the accounts and the Secretary keeps the minutes.")
        adopted = laws.law.OrganismLawService().adopt_law(
            laws.command(self.coordinator, laws.adoption(self.owner, document))
        )
        self.assertEqual(adopted.status, "ADOPT", "Both fixture offices need their recorded basis.")
        provision = definitions.DefinitionProvision("OFFICE_BASIS", f"law-ref:{document.name}#1",
                                                   str(document.first.version_number))
        self.content = definitions.DefinitionContent(code="TREASURER", provisions=(provision,))
        self.office = record_office(self.owner, self.coordinator, self.content)

    def secretary(self):
        content = definitions.DefinitionContent(code="SECRETARY", provisions=self.content.provisions)
        return record_office(self.owner, self.coordinator, content)

    def open_topic(self, title, *, board=None, category=None, office=None, author=None):
        """Open through START and construct the expected public summary from its receipt and fixed coordinates."""
        board = self.board if board is None else board
        author = self.coordinator if author is None else author
        office = self.office if office is None else office
        circle = UUID(board["circle"])
        category = UUID(board["category"]) if category is None else category
        receipt = discussion_posts.start_topic(
            credential=author.credential, organism_uuid=self.owner.organism_id, circle_uuid=circle,
            category_uuid=category, thread_uuid=uuid4(), post_uuid=uuid4(), request_uuid=uuid4(),
            title=title, markdown=MARKDOWN, office_definition_uuid=office,
        )
        event = models.LivingOrganismEvent.objects.get(event_uuid=receipt["act"])
        pin = models.OrganismRoleDefinition.objects.get(role_uuid=office)
        summary = dict(thread_uuid=receipt["thread"], board_uuid=UUID(board["board"]), circle_uuid=circle,
                       category_uuid=category, title=title, created_at=event.effective_at,
                       office_definition_uuid=office, office_definition_version=pin.definition_version)
        return receipt, summary

    def search(self, office, *, reader=None):
        reader = self.member if reader is None else reader
        return discussion_posts.topics_about_office(credential=reader.credential, organism_uuid=self.owner.organism_id,
                                                    office_definition_uuid=office)

    def test_current_office_finds_earlier_version(self):
        """Either Treasurer version finds both openings in creation order with their original pins."""
        _, first = self.open_topic("Before the amendment")
        successor = record_office(self.owner, self.coordinator, self.content, prior=self.office)
        _, second = self.open_topic("After the amendment", office=successor)
        secretary = self.secretary()
        _, other = self.open_topic("Keeping minutes", office=secretary)

        self.assertEqual(self.search(successor), [second, first],
                         "The current definition must also find the Topic pinned before amendment.")
        self.assertEqual(self.search(self.office), [second, first])
        self.assertEqual(self.search(secretary), [other])

    def test_search_discloses_no_unreadable_board(self):
        """A member sees only matches on their current Circle boards, with identical empty responses elsewhere."""
        private = circle_commands.form(**self.scope, circle_uuid=uuid4(), kind="GENERIC",
                                       purpose="Another Circle discusses the same office", request_uuid=uuid4())
        other_board = discussion_category(self.owner, self.coordinator, private.circle_uuid)
        _, private_topic = self.open_topic("Private Circle discussion", board=other_board)
        empty = self.search(self.secretary())
        self.assertEqual(empty, [])
        self.assertEqual(self.search(self.office), empty,
                         "Standing on another Circle must reveal nothing about the private opening.")
        self.assertEqual(self.search(self.office, reader=self.coordinator), [private_topic])

        foreign, other_author = laws.ordinary_birth()
        foreign_office = record_office(foreign, other_author, office_basis(foreign, other_author))
        _, visible = self.open_topic("Shared with this Circle")
        self.assertEqual(self.search(self.office), [visible])
        self.assertEqual(self.search(foreign_office), empty)
        self.assertEqual(self.search(uuid4()), empty)
        self.assertEqual(self.search(self.office, reader=other_author), empty)
        self.assertEqual(discussion_posts.topics_about_office(
            credential=self.member.credential, organism_uuid=uuid4(), office_definition_uuid=self.office), empty)
        with self.assertRaises(discussion.DiscussionUnavailable):
            discussion_posts.topics_about_office(credential=None, organism_uuid=None, office_definition_uuid=None)

        # The member still passes the organisation-board preflight after leaving the Circle.
        discussion_category(self.owner, self.coordinator)
        circle_commands.leave(credential=self.member.credential, organism_uuid=self.owner.organism_id,
                              circle_uuid=self.circle.circle_uuid, participation_uuid=self.place.participation_uuid,
                              request_uuid=uuid4(), confirmed=True)
        self.assertEqual(self.search(self.office), empty, "Losing the Circle place removes the formerly visible match.")

    def test_search_uses_exact_category(self):
        """Each visible match must satisfy its own category's current audience on the same board."""
        local = self.scope | {"circle_uuid": self.circle.circle_uuid}
        rows = discussion.board_categories(**local)["categories"]
        coordination = next(row["category_uuid"] for row in rows if row["functional_code"] == "COORDINATION")
        _, restricted = self.open_topic("Coordination business", category=coordination)
        _, shared = self.open_topic("Member discussion")
        self.assertEqual(self.search(self.office), [shared],
                         "The same board's Coordination opening must be absent for this member.")
        self.assertEqual(self.search(self.office, reader=self.coordinator), [shared, restricted])

        custom = uuid4()
        discussion.create_category(**local, category_uuid=custom, request_uuid=uuid4(), name="Office work")
        _, changing = self.open_topic("Category audience changes", category=custom)
        self.assertEqual(self.search(self.office), [changing, shared])
        previous = discussion.category_history(**local, category_uuid=custom)[-1]["version_uuid"]
        discussion.revise_category(**local, category_uuid=custom, request_uuid=uuid4(), predecessor=previous,
                                   name="Office work", description="", order=0, access="COORDINATORS")
        self.assertEqual(self.search(self.office), [shared])
        self.assertEqual(self.search(self.office, reader=self.coordinator), [changing, shared, restricted])

    def test_link_visibility_follows_opening_title(self):
        """Opening-title visibility governs the office list and forward link through hide, restore and retirement."""
        local = self.scope | {"circle_uuid": self.circle.circle_uuid}
        category = uuid4()
        discussion.create_category(**local, category_uuid=category, request_uuid=uuid4(), name="Office history")
        receipt, summary = self.open_topic("Treasurer history", category=category, author=self.member)
        topic_ref = local | {"category_uuid": category, "thread_uuid": receipt["thread"]}

        def hide(reader, hidden, *, coordinator=False):
            discussion_posts.set_post_hidden(**(topic_ref | {"credential": reader.credential}),
                                             post_uuid=receipt["post"], request_uuid=uuid4(), hidden=hidden,
                                             as_coordinator=coordinator)

        def observe(reader):
            result = discussion_posts.read_thread(credential=reader.credential, organism_uuid=self.owner.organism_id,
                                                  circle_uuid=self.circle.circle_uuid, thread_uuid=receipt["thread"])
            header = result["topic"]
            link = {key: header[key] for key in ("office_definition_uuid", "office_definition_version") if key in header}
            return self.search(self.office, reader=reader), header["title"], link

        expected = ([summary], summary["title"],
                    {"office_definition_uuid": self.office, "office_definition_version": summary["office_definition_version"]})
        self.assertEqual(observe(self.member), expected)
        hide(self.member, True)
        self.assertEqual(observe(self.member), expected, "Author-only hiding preserves the title and both links.")
        hide(self.coordinator, True, coordinator=True)
        self.assertEqual(observe(self.member), ([], None, {}),
                         "A Coordinator-hidden opening must leave no summary, title or forward key for its author.")
        self.assertEqual(observe(self.coordinator), expected)
        hide(self.coordinator, False, coordinator=True)
        self.assertEqual(observe(self.member), expected)
        discussion_posts.moderate_thread(**topic_ref, operation="CLOSE", request_uuid=uuid4())
        previous = discussion.category_history(**local, category_uuid=category)[-1]["version_uuid"]
        discussion.retire_category(**local, category_uuid=category, predecessor=previous, request_uuid=uuid4())
        self.assertEqual(observe(self.member), expected, "Closed and retired history retains the permitted links.")


class DiscussionOfficeMigrationTests(TransactionTestCase):
    def test_migration_preserves_link_history(self):
        """0110 round-trips unlinked history and refuses to discard a recorded office link."""
        migration = import_module("core.migrations.0110_discussion_office_link")
        parent, = migration.Migration.dependencies
        head = ("core", "0110_discussion_office_link")

        def footprint():
            """Observe the installed schema and each thread pin, including after an unexpected descent."""
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT attnum, atttypid::regtype::text, attnotnull
                    FROM pg_catalog.pg_attribute
                    WHERE attrelid='public.core_discthread'::regclass
                      AND attname='office_definition_uuid' AND NOT attisdropped
                """)
                column = cursor.fetchone()
                cursor.execute("SELECT pg_get_indexdef(to_regclass('public.discp_thread_office_idx'))")
                index, = cursor.fetchone()
                bodies = []
                for signature in (migration.EXECUTE, migration.INPUT):
                    cursor.execute("SELECT prosrc FROM pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)", [signature])
                    bodies.append(cursor.fetchone()[0])
                pins = None
                if column is not None:
                    cursor.execute("SELECT thread_uuid, office_definition_uuid FROM public.core_discthread ORDER BY thread_uuid")
                    pins = dict(cursor.fetchall())
                return column, index, bodies, pins

        owner, author = laws.ordinary_birth()
        circle = circle_commands.form(credential=author.credential, organism_uuid=owner.organism_id,
                                      circle_uuid=uuid4(), kind="GENERIC", purpose="Office link history", request_uuid=uuid4())
        category = discussion_category(owner, author, circle.circle_uuid)
        office = record_office(owner, author, office_basis(owner, author))
        first = discussion_posts.start_topic(**public_start_args(owner, author, circle, category))
        self.assertIsNone(footprint()[3][first["thread"]])

        # This committed descent has real unlinked data. Register restoration of the received heads first.
        migrate_to_target_with_restore(self, parent)
        column, index, bodies, _ = footprint()
        self.assertEqual((column, index, bodies), (None, None, [prior for _, prior, _ in migration.REPLACEMENTS]))
        # Submit the old wire format to the installed 0109 writer and retain its actual answer.
        old = start(category)
        result = discussion._rows("SELECT public.discp_execute(%s::jsonb,%s,%s,%s,%s) AS receipt",
                                  [json.dumps(old), MARKDOWN, author.pk, author.access_epoch, database_now()])[0]["receipt"]
        receipt = {key: UUID(value) for key, value in result.items()}
        MigrationExecutor(connection).migrate([head])
        column, index, bodies, pins = footprint()
        self.assertIsNotNone(column)
        self.assertEqual(column[1:], ("uuid", False))
        self.assertIsNotNone(index)
        self.assertEqual(bodies, [after for _, _, after in migration.REPLACEMENTS])
        self.assertEqual((pins[first["thread"]], pins[receipt["thread"]]), (None, None))
        retry = public_start_args(owner, author, circle, category)
        retry.update(thread_uuid=UUID(old["thread"]), post_uuid=UUID(old["post"]),
                     request_uuid=UUID(old["request"]), title=old["title"])
        self.assertEqual(discussion_posts.start_topic(**retry), receipt)

        linked = discussion_posts.start_topic(**public_start_args(owner, author, circle, category),
                                              office_definition_uuid=office)
        before = footprint()
        self.assertEqual(before[3][linked["thread"]], office)
        ddl = []

        def observe_ddl(execute, sql, params, many, context):
            # A later atomic rollback must not mask a destructive step taken before refusal.
            verb = str(sql).split(None, 1)[0].upper()
            if verb in {"CREATE", "ALTER", "DROP", "UPDATE", "DELETE", "TRUNCATE"}:
                ddl.append(verb)
            return execute(sql, params, many, context)

        with transaction.atomic():
            verdict = "reversed"
            try:
                with connection.execute_wrapper(observe_ddl):
                    MigrationExecutor(connection).migrate([parent])
            except RuntimeError as exc:
                verdict = str(exc)
            after = footprint()
            # Roll back even a successful broken downgrade, including its lost pins and migration ledger row.
            transaction.set_rollback(True)

        self.assertEqual((verdict, ddl, after == before),
                         ("0110 keeps the Board office chosen for a recorded Topic. Leave 0110 applied to preserve that link.",
                          [], True),
                         "A recorded link must stop reversal before any schema or data change and retain the whole footprint.")

    test_migration_preserves_link_history.ISOLATION_FULL_CAPTURE = True
