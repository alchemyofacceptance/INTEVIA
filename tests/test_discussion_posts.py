"""DISC1-06..10. Five P2 witnesses; no additional recurring control.

Uses real services and the P1 guarded fixture latitude. Lapse is explicitly a
shared entitlement-reader result substitution, not a fabricated expiry event.
"""
import json
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from core import models
from src.intevia.services import circle_commands
from src.intevia.services import discussion as disc
from src.intevia.services import discussion_posts as posts
from src.intevia.services import organism_membership_service as membership
from src.intevia.services.organism_application_authority import (
    ProductionApplicationAuthorityProvider,
)
from src.intevia.services.organism_authority import S015Authority
from tests import discussion_support
from tests import s015_0021_support as support
from tests import test_c1b_departure as departure
from tests import test_law_acts as fixtures


class DiscussionPostTests(TransactionTestCase):
    def refused(self, code, call):
        with self.assertRaises(disc.DiscussionRefused) as caught:
            call()
        self.assertEqual((type(caught.exception), caught.exception.code, str(caught.exception)),
                         (disc.DiscussionRefused, code, disc.MESSAGES[code]))

    def unavailable(self, call):
        with self.assertRaises(disc.DiscussionUnavailable) as caught:
            call()
        self.assertEqual((type(caught.exception), str(caught.exception)), (disc.DiscussionUnavailable, disc.UNAVAILABLE))

    def board(self, lo, actor, circle=None):
        args = {"credential": actor.credential, "organism_uuid": lo.organism_id,
                "circle_uuid": circle.circle_uuid if circle else None}
        disc.ensure_board(**args, request_uuid=uuid4())
        category = next(c for c in disc.board_categories(**args)["categories"] if c["functional_code"] == "DISCUSSION")
        return args | {"category_uuid": category["category_uuid"]}

    def start(self, args, **changes):
        return posts.start_topic(**(args | {"thread_uuid": uuid4(), "post_uuid": uuid4(), "request_uuid": uuid4(),
            "title": "A topic", "markdown": "First body"} | changes))

    def read_args(self, args):
        return {k: v for k, v in args.items() if k != "category_uuid"}

    def reply(self, args, topic, **changes):
        return posts.reply(**(args | {"thread_uuid": topic["thread"], "post_uuid": uuid4(),
            "request_uuid": uuid4(), "markdown": "Reply"} | changes))

    def moderate(self, args, topic, operation):
        return posts.moderate_thread(**args, thread_uuid=topic["thread"], operation=operation, request_uuid=uuid4())

    def hide(self, args, post, hidden, *, as_coordinator=False):
        return posts.set_post_hidden(**args, thread_uuid=post["thread"], post_uuid=post["post"],
            request_uuid=uuid4(), hidden=hidden, as_coordinator=as_coordinator)

    def test_topics_numbered_quotes_versions_order_and_closure(self):
        """DISC1-06. Typed topics, exact quotes, author versions, ordered pure reads and closure."""
        lo, owner = fixtures.ordinary_birth()
        args = self.board(lo, owner)
        member = fixtures.add_member(lo)
        member_args = args | {"credential": member.credential}
        source = "  **Typed**\r\n[External](https://example.org) [Library](library:exact-version)\n<script>literal data</script>"
        topic = self.start(member_args, markdown=source, title="T" * 90)
        question = self.start(member_args, topic_type="QUESTION")
        suggestion = self.start(member_args, topic_type="SUGGESTION")
        response = self.reply(args, topic, quote_version=topic["version"])
        view = posts.read_thread(**self.read_args(member_args), thread_uuid=topic["thread"])
        self.assertEqual([p["post_number"] for p in view["posts"]], [1, 2])
        self.assertEqual(view["posts"][0]["markdown"], source)
        self.assertEqual(view["posts"][1]["quoted_markdown"], source)
        self.assertEqual(view["posts"][0]["body_fingerprint"], posts.body_fingerprint(source.replace("\r\n", "\n")))
        edit = dict(member_args, thread_uuid=topic["thread"], post_uuid=topic["post"], predecessor=topic["version"],
                    request_uuid=uuid4(), markdown="Revised author text")
        changed = posts.edit_post(**edit)
        self.assertEqual(posts.edit_post(**edit), changed)
        self.refused("DISC_POST_PREDECESSOR", lambda: posts.edit_post(**(edit | {"request_uuid": uuid4()})))
        self.refused("DISC_REQUEST_CONFLICT", lambda: posts.edit_post(**(edit | {"markdown": "different request"})))
        self.refused("DISC_POST_AUTHORITY", lambda: posts.edit_post(**(edit | {"credential": owner.credential, "request_uuid": uuid4()})))
        history = posts.post_history(**self.read_args(member_args), post_uuid=topic["post"])
        self.assertEqual([v["version_number"] for v in history], [1, 2])
        self.assertEqual([v["markdown"] for v in history], [source, "Revised author text"])
        self.assertNotEqual(history[0]["body_fingerprint"], history[1]["body_fingerprint"])
        self.assertTrue(all(v["author_id"] == member.pk and v["version_author"] == member.pk for v in history))
        self.assertEqual(posts.read_thread(**self.read_args(args), thread_uuid=topic["thread"])["posts"][1]["quoted_markdown"], source)
        self.moderate(args, topic, "PIN")
        rows = posts.category_topics(**args)
        self.assertEqual([r["thread_uuid"] for r in rows], [topic["thread"], suggestion["thread"], question["thread"]])
        self.assertEqual({r["topic_type"] for r in rows}, {"TOPIC", "QUESTION", "SUGGESTION"})
        self.assertTrue(all(r["record_kind"] == "ORDINARY" for r in rows))
        self.moderate(args, topic, "CLOSE")
        self.refused("DISC_CLOSED", lambda: self.reply(member_args, topic))
        self.assertTrue(posts.read_thread(**self.read_args(member_args), thread_uuid=topic["thread"])["topic"]["closed"])
        self.moderate(args, topic, "REOPEN")
        third = self.reply(member_args, topic, quote_version=response["version"])
        self.assertEqual(posts.read_thread(**self.read_args(args), thread_uuid=topic["thread"])["posts"][-1]["post_number"], 3)
        self.moderate(args, topic, "UNPIN")
        self.assertEqual(posts.category_topics(**args)[0]["thread_uuid"], suggestion["thread"])
        self.refused("DISC_QUOTE", lambda: self.reply(member_args, topic, quote_version=question["version"]))
        self.assertIsNotNone(third["act"])
        before = models.LivingOrganismEvent.objects.count()
        self.assertEqual(len(posts.visible_boards(credential=member.credential)), 1)
        posts.category_topics(**member_args)
        posts.post_history(**self.read_args(member_args), post_uuid=topic["post"])
        posts.read_thread(**self.read_args(member_args), thread_uuid=topic["thread"])
        posts.moderation_history(**self.read_args(member_args), thread_uuid=topic["thread"])
        self.assertEqual(models.LivingOrganismEvent.objects.count(), before)
        record = disc._rows("SELECT actor_state,enterer_identity_id,payload_fingerprint FROM public.core_livingorganismevent WHERE event_uuid=%s", [topic["act"]])[0]
        self.assertEqual((record["actor_state"], record["enterer_identity_id"]), ("LIVING_ORGANISM", member.pk))
        act = disc._rows("SELECT payload,fingerprint,authority_source FROM public.core_discpostact WHERE act_uuid=%s", [topic["act"]])[0]
        self.assertEqual(record["payload_fingerprint"], act["fingerprint"])
        self.assertEqual(act["authority_source"]["kind"], "MEMBER")
        self.assertNotIn("markdown", act["payload"])

    def test_hide_layers_quotes_history_and_restore_prior_state(self):
        """DISC1-07. Hidden content stays hidden through topic, history, edit and quote reads.

        Includes test_p2c_r2_author_hide_preserves_topic_and_other_replies and
        test_p2c_r7_edit_preserves_quote_and_explicit_none_removes_it.
        """
        lo, owner = fixtures.ordinary_birth()
        args = self.board(lo, owner)
        author = fixtures.add_member(lo)
        member = args | {"credential": author.credential}
        topic = self.start(member)
        reply = self.reply(member, topic, markdown="Private when hidden")
        quote = self.reply(args, topic, quote_version=reply["version"])
        before = {t: disc._rows("SELECT count(*) AS n FROM public." + t)[0]["n"]
                  for t in ("core_discthread", "core_discpost", "core_discpostversion", "core_discbody")}
        self.hide(member, reply, True)
        view = posts.read_thread(**self.read_args(member), thread_uuid=topic["thread"])
        self.assertTrue(view["posts"][1]["hidden"])
        self.assertIsNone(view["posts"][1]["markdown"])
        self.assertIsNone(view["posts"][2]["quoted_markdown"])
        self.assertEqual(view["posts"][1]["author_id"], author.pk)
        self.assertIsNotNone(view["posts"][1]["received_at"])
        self.assertIsNone(posts.post_history(**self.read_args(member), post_uuid=reply["post"])[0]["markdown"])
        self.assertEqual(posts.read_thread(**self.read_args(args), thread_uuid=topic["thread"])["posts"][2]["quoted_markdown"], "Private when hidden")
        self.hide(member, reply, False)
        self.assertFalse(posts.read_thread(**self.read_args(member), thread_uuid=topic["thread"])["posts"][1]["hidden"])
        self.hide(member, reply, True)
        self.hide(args, reply, True, as_coordinator=True)
        self.refused("DISC_MODERATED", lambda: self.hide(member, reply, False))
        self.hide(args, reply, False, as_coordinator=True)
        self.assertFalse(posts.read_thread(**self.read_args(member), thread_uuid=topic["thread"])["posts"][1]["hidden"])
        self.moderate(args, topic, "CLOSE")
        self.hide(args, topic, True, as_coordinator=True)
        view = posts.read_thread(**self.read_args(member), thread_uuid=topic["thread"])
        self.assertTrue(view["topic"]["closed"])
        self.assertIsNone(view["topic"]["title"])
        self.assertTrue(all(p["hidden"] and p["markdown"] is None and p["quoted_markdown"] is None for p in view["posts"]))
        self.assertIsNone(posts.category_topics(**member)[0]["title"])
        self.assertIsNone(posts.post_history(**self.read_args(member), post_uuid=quote["post"])[0]["markdown"])
        self.hide(args, topic, False, as_coordinator=True)
        self.assertTrue(posts.read_thread(**self.read_args(member), thread_uuid=topic["thread"])["topic"]["closed"])
        self.moderate(args, topic, "REOPEN")
        self.hide(args, topic, True, as_coordinator=True)
        self.refused("DISC_HIDDEN", lambda: self.reply(member, topic))
        self.refused("DISC_HIDDEN", lambda: posts.edit_post(**member, thread_uuid=topic["thread"], post_uuid=topic["post"],
            predecessor=topic["version"], request_uuid=uuid4(), markdown="Hidden edit"))
        self.hide(args, topic, False, as_coordinator=True)
        self.assertFalse(posts.read_thread(**self.read_args(member), thread_uuid=topic["thread"])["topic"]["closed"])
        self.assertEqual(before, {t: disc._rows("SELECT count(*) AS n FROM public." + t)[0]["n"] for t in before})
        log = posts.moderation_history(**self.read_args(member), thread_uuid=topic["thread"])
        self.assertTrue(any(row["actor_id"] == owner.pk and row["authority_source"]["kind"] == "OFFICE" for row in log))
        self.assertTrue(any(row["actor_id"] == author.pk and row["operation"] == "HIDE" for row in log))

        with self.subTest(case="test_p2c_r2_author_hide_preserves_topic_and_other_replies"):
            topic = self.start(member, title="Still a named discussion")
            response = self.reply(args, topic, markdown="Another person's reply", quote_version=topic["version"])
            self.hide(member, topic, True)
            view = posts.read_thread(**self.read_args(member), thread_uuid=topic["thread"])
            self.assertEqual(view["topic"]["title"], "Still a named discussion")
            self.assertIsNone(view["posts"][0]["markdown"])
            self.assertEqual(view["posts"][1]["markdown"], "Another person's reply")
            self.assertIsNone(view["posts"][1]["quoted_markdown"])
            self.assertTrue(view["posts"][1]["quote_hidden"])
            self.assertFalse(view["posts"][1]["hidden"])

            def listed_topic():
                """Find this thread even when another discussion is listed first."""
                rows = {row["thread_uuid"]: row for row in posts.category_topics(**member)}
                self.assertIn(topic["thread"], rows, "Hiding the opening must preserve the listed thread.")
                return rows[topic["thread"]]

            listed = listed_topic()
            self.assertEqual((listed["title"], listed["hidden"]), ("Still a named discussion", False))
            self.assertIsNone(posts.post_history(**self.read_args(member), post_uuid=topic["post"])[0]["markdown"])
            self.assertEqual(posts.post_history(**self.read_args(member), post_uuid=response["post"])[0]["markdown"], "Another person's reply")
            self.reply(member, topic)
            self.moderate(args, topic, "CLOSE")
            self.refused("DISC_CLOSED", lambda: self.reply(member, topic))
            self.hide(args, topic, True, as_coordinator=True)
            view = posts.read_thread(**self.read_args(member), thread_uuid=topic["thread"])
            self.assertIsNone(view["topic"]["title"])
            self.assertTrue(all(p["hidden"] and p["markdown"] is None and p["quoted_markdown"] is None for p in view["posts"]))
            self.assertIsNone(listed_topic()["title"])
            self.assertEqual(posts.read_thread(**self.read_args(args), thread_uuid=topic["thread"])["posts"][1]["markdown"], "Another person's reply")
            self.hide(args, topic, False, as_coordinator=True)
            self.assertTrue(posts.read_thread(**self.read_args(member), thread_uuid=topic["thread"])["topic"]["closed"])

        with self.subTest(case="test_p2c_r7_edit_preserves_quote_and_explicit_none_removes_it"):
            topic = self.start(args)
            quoted = self.reply(member, topic, markdown="Source")
            quoting = self.reply(member, topic, quote_version=quoted["version"])
            self.hide(member, quoted, True)
            edit = member | {"thread_uuid": topic["thread"], "post_uuid": quoting["post"],
                            "predecessor": quoting["version"], "request_uuid": uuid4(), "markdown": "Revised reply"}
            revised = posts.edit_post(**edit)
            self.assertEqual(posts.edit_post(**edit), revised)
            view = posts.read_thread(**self.read_args(member), thread_uuid=topic["thread"])["posts"][-1]
            self.assertEqual(view["quote_version"], quoted["version"])
            self.assertIsNone(view["quoted_markdown"])
            self.assertEqual(view["markdown"], "Revised reply")
            explicit = posts.edit_post(**(edit | {"predecessor": revised["version"], "request_uuid": uuid4(),
                                                  "quote_version": quoted["version"]}))
            # Another post cannot acquire a new quote of that hidden version.
            plain = self.reply(member, topic)
            self.refused("DISC_QUOTE", lambda: posts.edit_post(**member, thread_uuid=topic["thread"], post_uuid=plain["post"],
                predecessor=plain["version"], request_uuid=uuid4(), markdown="New quote", quote_version=quoted["version"]))
            self.refused("DISC_QUOTE", lambda: self.reply(member, topic, quote_version=quoted["version"]))
            remove = edit | {"predecessor": explicit["version"], "request_uuid": uuid4(), "quote_version": None}
            removed = posts.edit_post(**remove)
            before_replay = models.LivingOrganismEvent.objects.count()
            # Current quote is None; replay must still resolve the older request's
            # omitted quote from its immutable predecessor and return that old act.
            self.assertEqual(posts.edit_post(**edit), revised)
            self.assertEqual(posts.edit_post(**remove), removed)
            self.assertEqual(models.LivingOrganismEvent.objects.count(), before_replay)
            history = posts.post_history(**self.read_args(member), post_uuid=quoting["post"])
            self.assertEqual([v["quote_version"] for v in history], [quoted["version"]]*3 + [None])
            self.assertTrue(all(v["quoted_markdown"] is None for v in history))
            self.hide(member, quoted, False)
            self.assertEqual(posts.post_history(**self.read_args(member), post_uuid=quoting["post"])[0]["quoted_markdown"], "Source")

    def test_circle_local_moderation_announcements_and_retired_category(self):
        """DISC1-08. Independent Circle office, Announcements start/reply and category retirement."""
        lo, owner = fixtures.ordinary_birth()
        lo_args = self.board(lo, owner)
        member = fixtures.add_member(lo)
        ann = next(c for c in disc.board_categories(**self.read_args(lo_args))["categories"] if c["functional_code"] == "ANNOUNCEMENTS")
        announcements = lo_args | {"category_uuid": ann["category_uuid"]}
        self.refused("DISC_UNAVAILABLE", lambda: self.start(announcements | {"credential": member.credential}))
        notice = self.start(announcements)
        self.reply(announcements | {"credential": member.credential}, notice)
        circle, coordinator = discussion_support.independent_circle(lo, owner)
        circle_args = self.board(lo, coordinator, circle)
        topic = self.start(circle_args)
        self.moderate(circle_args, topic, "PIN")
        self.moderate(circle_args, topic, "CLOSE")
        self.moderate(circle_args, topic, "REOPEN")
        self.hide(circle_args, topic, True, as_coordinator=True)
        self.hide(circle_args, topic, False, as_coordinator=True)
        self.refused("DISC_UNAVAILABLE", lambda: self.moderate(circle_args | {"credential": owner.credential}, topic, "PIN"))
        self.refused("DISC_POST_AUTHORITY", lambda: self.moderate(announcements | {"credential": coordinator.credential}, notice, "PIN"))
        category = uuid4()
        disc.create_category(**self.read_args(circle_args), category_uuid=category, request_uuid=uuid4(), name="Retire with history")
        custom = circle_args | {"category_uuid": category}
        thread = self.start(custom)
        version = disc.category_history(**custom)[-1]
        disc.revise_category(**custom, predecessor=version["version_uuid"], request_uuid=uuid4(),
            name="Renamed with thread", description="Same category identity", order=7, access="MEMBERS")
        self.assertEqual(posts.read_thread(**self.read_args(custom), thread_uuid=thread["thread"])["topic"]["category_uuid"], category)
        version = disc.category_history(**custom)[-1]
        disc.retire_category(**custom, predecessor=version["version_uuid"], request_uuid=uuid4())
        self.refused("DISC_CATEGORY_RETIRED", lambda: self.start(custom))
        self.refused("DISC_CATEGORY_RETIRED", lambda: self.reply(custom, thread))
        posts.edit_post(**custom, thread_uuid=thread["thread"], post_uuid=thread["post"], predecessor=thread["version"],
            request_uuid=uuid4(), markdown="History can be corrected")
        self.hide(custom, thread, True)
        self.hide(custom, thread, False)
        self.assertEqual(len(posts.post_history(**self.read_args(custom), post_uuid=thread["post"])), 2)
        self.assertTrue(posts.read_thread(**self.read_args(custom), thread_uuid=thread["thread"])["category_retired"])

    def test_probation_departure_and_posting_after_entitlement_loss(self):
        """DISC1-09. Actual posting under probation permission and after a shared lapse result."""
        lo, owner = fixtures.ordinary_birth()
        args = self.board(lo, owner)
        reader_args = self.read_args(args)
        keys = []
        for n in range(12):
            key = uuid4()
            disc.create_category(**reader_args, category_uuid=key, request_uuid=uuid4(), name="Custom " + str(n))
            keys.append(key)
        migration = import_module("core.migrations.0072_discussion_boards")
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("SELECT prosrc FROM pg_proc WHERE oid='public.s015_entitlement_effective(uuid,text,timestamptz)'::regprocedure")
            original = cur.fetchone()[0]
            cur.execute(migration.SE_HEADER + "SELECT p_capability<>'DISCUSSION_CUSTOM_CATEGORIES' AND (" + original.strip() + ")$$;", params=None)
            try:
                listing = disc.board_categories(**reader_args)
                self.assertFalse(listing["subscriber_categories"])
                available = {c["category_uuid"] for c in listing["categories"]
                             if c["can_start_topic"] and c["can_reply"]}
                self.assertTrue(set(keys) <= available, "All twelve existing categories must still support posts.")
                for key in (keys[0], keys[8], keys[-1]):
                    custom = args | {"category_uuid": key}
                    topic = self.start(custom)
                    self.reply(custom, topic)
                    self.assertEqual(len(posts.read_thread(**reader_args, thread_uuid=topic["thread"])["posts"]), 2)
                self.refused("DISC_CATEGORY_LIMIT", lambda: disc.create_category(**reader_args, category_uuid=uuid4(), request_uuid=uuid4(), name="Thirteenth"))
            finally:
                cur.execute(migration.SE_HEADER + original + "$$;", params=None)
        member = fixtures.add_member(lo)
        authored = self.start(args | {"credential": member.credential})
        row = models.OrganismMembership.objects.get(living_organism=lo, identity=member)
        self.fx = SimpleNamespace(actor=owner, ids={"basis": lo.founding_authority_basis_id})
        departure.DepartureTests._state(self, row, "core_organismmembership", "core_organismmembershiptransition", "PROBATIONARY")
        disc.set_probation_access(credential=owner.credential, organism_uuid=lo.organism_id, enabled=True, request_uuid=uuid4())
        welcome = next(c for c in disc.board_categories(**reader_args)["categories"] if c["functional_code"] == "WELCOME")
        probation = args | {"credential": member.credential, "category_uuid": welcome["category_uuid"]}
        topic = self.start(probation)
        self.reply(probation, topic)
        circle, coordinator = discussion_support.independent_circle(lo, owner)
        circle_args = self.board(lo, coordinator, circle)
        circle_category = uuid4()
        disc.create_category(**self.read_args(circle_args), category_uuid=circle_category,
            request_uuid=uuid4(), name="Probation forum only", access="PROBATIONARY")
        circle_probation = circle_args | {"credential": member.credential, "category_uuid": circle_category}
        self.assertFalse(models.CircleParticipation.objects.filter(circle=circle, membership=row).exists())
        circle_topic = self.start(circle_probation)
        self.reply(circle_probation, circle_topic)
        self.assertEqual(len(posts.read_thread(**self.read_args(circle_probation), thread_uuid=circle_topic["thread"])["posts"]), 2)
        self.refused("DISC_UNAVAILABLE", lambda: self.start(args | {"credential": member.credential}))
        self.unavailable(lambda: posts.read_thread(**(reader_args | {"credential": member.credential}), thread_uuid=authored["thread"]))
        at = support.database_now()
        membership.OrganismMembershipService(authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),
            clock=support.database_now).leave_organism(membership.LeaveOrganismCommand(credential=member.credential,
                membership_uuid=row.membership_uuid, confirmed=True, reason_class="VOLUNTARY", occurred_at=at,
                effective_at=at, request_reference=uuid4().hex, idempotency_key=uuid4().hex))
        outsider = support.register_test_learner(username="disc-post-outsider-" + uuid4().hex)
        for who in (member, outsider):
            self.unavailable(lambda who=who: posts.read_thread(**(reader_args | {"credential": who.credential}), thread_uuid=topic["thread"]))
            self.unavailable(lambda who=who: posts.visible_boards(credential=who.credential))
        history = posts.post_history(**reader_args, post_uuid=authored["post"])
        self.assertEqual(history[0]["author_id"], member.pk)
        self.assertEqual(history[0]["markdown"], "First body")
        self.unavailable(lambda: posts.read_thread(**(reader_args | {"organism_uuid": uuid4()}), thread_uuid=authored["thread"]))

    test_probation_departure_and_posting_after_entitlement_loss.ISOLATION_FULL_CAPTURE = True

    def test_guards_rollback_request_scope_and_populated_reversal(self):
        """DISC1-10. Immutable text/metadata, complete shared lineage and atomic rollback (reversal in DISC2-19)."""
        lo, owner = fixtures.ordinary_birth()
        args = self.board(lo, owner)
        topic = self.start(args)
        tables = ("core_discpostact", "core_discthread", "core_discpost", "core_discpostversion", "core_discbody")
        for table in tables:
            with self.assertRaises(IntegrityError) as caught, transaction.atomic(), connection.cursor() as cur:
                cur.execute("DELETE FROM public." + table)
            self.assertEqual(caught.exception.__cause__.diag.message_primary, "S015 immutable or append-only row mutation refused")
        before = {t: disc._rows("SELECT count(*) AS n FROM public." + t)[0]["n"] for t in tables}
        events = models.LivingOrganismEvent.objects.count()
        with self.assertRaisesRegex(RuntimeError, "caller rollback"), transaction.atomic():
            self.reply(args, topic)
            raise RuntimeError("caller rollback")
        self.assertEqual(before, {t: disc._rows("SELECT count(*) AS n FROM public." + t)[0]["n"] for t in tables})
        self.assertEqual(events, models.LivingOrganismEvent.objects.count())
        other, other_owner = fixtures.ordinary_birth()
        other_args = self.board(other, other_owner)
        self.refused("DISC_UNAVAILABLE", lambda: self.start(other_args, request_uuid=topic["act"]))
        with self.assertRaises(IntegrityError) as forged, transaction.atomic(), connection.cursor() as cur:
            cur.execute("""INSERT INTO public.core_discpostact(act_uuid,board_uuid,operation,actor_id,actor_epoch,
              authority_source,payload,fingerprint,received_at)
              SELECT %s,board_uuid,operation,actor_id,actor_epoch,'{"kind":"CQ"}'::jsonb,payload,fingerprint,statement_timestamp()
              FROM public.core_discpostact WHERE act_uuid=%s""", [uuid4(), topic["act"]])
        self.assertEqual(forged.exception.__cause__.diag.message_primary, "DISC_POST_AUTHORITY")
        # This deliberately incomplete raw act violates both the event FK and
        # the domain completeness guard. Check each obligation explicitly;
        # their deferred execution order is not a DISC_COMPLETE guarantee.
        def incomplete_act(*, check_complete=False):
            try:
                with transaction.atomic(), connection.cursor() as cur:
                    cur.execute("""INSERT INTO public.core_discpostact(act_uuid,board_uuid,operation,actor_id,actor_epoch,
                      authority_source,payload,fingerprint,received_at)
                      SELECT key,board_uuid,operation,actor_id,actor_epoch,authority_source,p,public.disc_fingerprint(p),statement_timestamp()
                      FROM public.core_discpostact a CROSS JOIN LATERAL (SELECT %s::uuid AS key) k
                      CROSS JOIN LATERAL (SELECT a.payload || jsonb_build_object('request',key,'thread',%s::uuid,'post',%s::uuid) AS p) x
                      WHERE a.act_uuid=%s""", [uuid4(), uuid4(), uuid4(), topic["act"]])
                    if check_complete:
                        cur.execute("SET CONSTRAINTS discp_complete IMMEDIATE")
            except IntegrityError as exc:
                disc.translate_refusal(exc)

        # At outer COMMIT the missing event FK wins. The translator must not
        # relabel an unrelated database diagnostic as a governed DISC refusal.
        with self.assertRaises(IntegrityError) as missing_event:
            incomplete_act()
        diagnostic = missing_event.exception.__cause__.diag
        self.assertEqual((diagnostic.sqlstate, diagnostic.constraint_name),
                         ("23503", "core_discpostact_act_uuid_fkey"))
        # Force only the named completeness constraints while the FK remains
        # deferred: this proves their exact refusal and its public translation.
        with self.assertRaises(disc.DiscussionRefused) as incomplete:
            incomplete_act(check_complete=True)
        self.assertEqual((incomplete.exception.code, str(incomplete.exception)), ("DISC_COMPLETE", disc.MESSAGES["DISC_COMPLETE"]))

    test_guards_rollback_request_scope_and_populated_reversal.ISOLATION_FULL_CAPTURE = True

    def test_p2c_r3_scope_before_author_and_coordinator_authority(self):
        """DISC1-13 / R3. Unknown/mismatched targets have one refusal on every existing-target route."""
        lo, owner = fixtures.ordinary_birth()
        args = self.board(lo, owner)
        author = fixtures.add_member(lo)
        member = args | {"credential": author.credential}
        topic = self.start(member)
        other_thread = self.start(member)
        custom = uuid4()
        disc.create_category(**self.read_args(args), category_uuid=custom, request_uuid=uuid4(), name="Other category")
        other_category = self.start(member | {"category_uuid": custom})
        # Same author, another board: the Circle founder is also this LO's Coordinator.
        circle = circle_commands.form(credential=owner.credential, organism_uuid=lo.organism_id,
            circle_uuid=uuid4(), kind="GENERIC", purpose="Scope witness", request_uuid=uuid4())
        circle_topic = self.start(self.board(lo, owner, circle))
        foreign, foreign_owner = fixtures.ordinary_birth()
        foreign_topic = self.start(self.board(foreign, foreign_owner))
        cases = [(uuid4(), uuid4()), (topic["thread"], uuid4()),
                 (topic["thread"], other_thread["post"]), (other_category["thread"], other_category["post"]),
                 (circle_topic["thread"], circle_topic["post"]), (foreign_topic["thread"], foreign_topic["post"])]
        for caller in (member, args):
            for thread, post in cases:
                with self.subTest(actor=caller["credential"].pk, thread=thread, post=post):
                    self.refused("DISC_UNAVAILABLE", lambda caller=caller, post=post, thread=thread: posts.edit_post(**caller, thread_uuid=thread,
                        post_uuid=post, predecessor=uuid4(), request_uuid=uuid4(), markdown="No"))
                    for hidden in (True, False):
                        for office in (False, True):
                            self.refused("DISC_UNAVAILABLE", lambda hidden=hidden, office=office, caller=caller, post=post, thread=thread: posts.set_post_hidden(
                                **caller, thread_uuid=thread, post_uuid=post, request_uuid=uuid4(), hidden=hidden, as_coordinator=office))
            for thread in (uuid4(), other_category["thread"], circle_topic["thread"], foreign_topic["thread"]):
                for operation in ("PIN", "UNPIN", "CLOSE", "REOPEN"):
                    self.refused("DISC_UNAVAILABLE", lambda operation=operation, caller=caller, thread=thread: posts.moderate_thread(
                        **caller, thread_uuid=thread, request_uuid=uuid4(), operation=operation))
                self.refused("DISC_UNAVAILABLE", lambda caller=caller, thread=thread: posts.reply(**caller, thread_uuid=thread,
                    post_uuid=uuid4(), request_uuid=uuid4(), markdown="No"))
        # The same pre-authority scope gate also protects direct act insertion.
        with self.assertRaises(IntegrityError) as raw, transaction.atomic(), connection.cursor() as cur:
            cur.execute("""INSERT INTO public.core_discpostact(act_uuid,board_uuid,operation,actor_id,actor_epoch,
                authority_source,payload,fingerprint,received_at)
                SELECT %s,board_uuid,'HIDE',actor_id,actor_epoch,authority_source,
                payload||jsonb_build_object('operation','HIDE','post',%s::uuid),fingerprint,statement_timestamp()
                FROM public.core_discpostact WHERE act_uuid=%s""", [uuid4(), uuid4(), topic["act"]])
        self.assertEqual(raw.exception.__cause__.diag.message_primary, "DISC_UNAVAILABLE")
        with self.assertRaises(disc.DiscussionRefused) as translated:
            disc.translate_refusal(raw.exception)
        self.assertEqual((translated.exception.code, str(translated.exception)), ("DISC_UNAVAILABLE", disc.UNAVAILABLE))

    def test_p2c_r5_request_identity_across_both_executors(self):
        """DISC1-15 / R5. Own-board conflict, foreign-board unavailable, original replay, unrelated event."""
        lo, owner = fixtures.ordinary_birth()
        args = self.board(lo, owner)
        board_args = self.read_args(args)
        provision = disc.ensure_board(**board_args, request_uuid=uuid4())
        start = args | {"thread_uuid": uuid4(), "post_uuid": uuid4(), "request_uuid": uuid4(), "title": "Replay", "markdown": "Body"}
        topic = posts.start_topic(**start)
        self.assertEqual(posts.start_topic(**start), topic)
        self.refused("DISC_REQUEST_CONFLICT", lambda: self.start(args, request_uuid=provision["act"]))
        self.refused("DISC_REQUEST_CONFLICT", lambda: disc.create_category(**board_args, category_uuid=uuid4(),
            request_uuid=topic["act"], name="Collision"))
        self.refused("DISC_REQUEST_CONFLICT", lambda: disc.ensure_board(**board_args, request_uuid=topic["act"]))
        key = uuid4()
        category_args = board_args | {"category_uuid": uuid4(), "request_uuid": key, "name": "Replay category"}
        category = disc.create_category(**category_args)
        self.assertEqual(disc.create_category(**category_args), category)
        self.refused("DISC_REQUEST_CONFLICT", lambda: disc.ensure_board(**board_args, request_uuid=key))
        other, other_owner = fixtures.ordinary_birth()
        other_args = self.board(other, other_owner)
        foreign_provision = disc.ensure_board(**self.read_args(other_args), request_uuid=uuid4())
        foreign_topic = self.start(other_args)
        for request in (foreign_provision["act"], foreign_topic["act"]):
            self.refused("DISC_UNAVAILABLE", lambda request=request: self.start(args, request_uuid=request))
            self.refused("DISC_UNAVAILABLE", lambda request=request: disc.create_category(**board_args, category_uuid=uuid4(), request_uuid=request, name="No"))
            self.refused("DISC_UNAVAILABLE", lambda request=request: disc.ensure_board(**board_args, request_uuid=request))
        # Reuse must also refuse before inserting a previously absent Circle board.
        circle = circle_commands.form(credential=owner.credential, organism_uuid=lo.organism_id,
            circle_uuid=uuid4(), kind="GENERIC", purpose="Request scope before provision", request_uuid=uuid4())
        circle_args = board_args | {"circle_uuid": circle.circle_uuid}
        for request in (topic["act"], provision["act"], foreign_topic["act"]):
            self.refused("DISC_UNAVAILABLE", lambda request=request: disc.ensure_board(**circle_args, request_uuid=request))
        self.assertFalse(disc._rows("SELECT board_uuid FROM public.core_discboard WHERE board_uuid=%s",
                                   [disc._board_id(lo.organism_id, circle.circle_uuid)]))
        disc.ensure_board(**circle_args, request_uuid=uuid4())
        # Non-DISC shared-chain UUIDs must never reach the writer as duplicates.
        for organism in (lo, other):
            event = models.LivingOrganismEvent.objects.filter(living_organism=organism).order_by("sequence").first()
            self.refused("DISC_UNAVAILABLE", lambda event=event: self.start(args, request_uuid=event.event_uuid))
            self.refused("DISC_UNAVAILABLE", lambda event=event: disc.create_category(**board_args, category_uuid=uuid4(),
                request_uuid=event.event_uuid, name="No"))
        # Only successful acts above exist: a conflicting request made no partial topic.
        self.assertEqual(len(posts.category_topics(**args)), 1)
        self.assertEqual(len(posts.read_thread(**board_args, thread_uuid=topic["thread"])["posts"]), 1)

    def test_p2c_r8_read_predicate_indexes_installed(self):
        """DISC1-18 / R8. Installed indexes cover the two JSON targets and category lookup."""
        rows = disc._rows("""SELECT c.relname AS name,i.indisvalid,i.indisready,
            pg_get_indexdef(i.indexrelid,1,true) AS key
            FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname IN ('discp_act_post','discp_act_thread','discp_thread_category')""")
        indexes = {r["name"]: r for r in rows}
        self.assertEqual(set(indexes), {"discp_act_post", "discp_act_thread", "discp_thread_category"})
        self.assertTrue(all(r["indisvalid"] and r["indisready"] for r in rows))
        self.assertRegex(indexes["discp_act_post"]["key"], r"payload\s*->>\s*'post'")
        self.assertRegex(indexes["discp_act_thread"]["key"], r"payload\s*->>\s*'thread'")
        self.assertEqual(indexes["discp_thread_category"]["key"], "category_uuid")

    test_p2c_r8_read_predicate_indexes_installed.ISOLATION_WRITE_NOTHING = True

    def test_p2d_s1_owner_arguments_precede_every_object_lookup(self):
        """DISC1-20 / S1. Read argument checks are independent of object existence.

        Includes test_p2d_s2_required_category_argument_is_exact_uuid.
        """
        lo, owner = fixtures.ordinary_birth()
        args = self.board(lo, owner)
        topic = self.start(args)
        outsider = support.register_test_learner(username="disc-order-"+uuid4().hex)
        for who in (owner, outsider):
            for invalid in ({"organism_uuid": "bad"}, {"circle_uuid": "bad"}):
                owner_args = self.read_args(args) | {"credential": who.credential} | invalid
                for thread, post in ((topic["thread"], topic["post"]), (uuid4(), uuid4())):
                    calls = [
                        lambda owner_args=owner_args, thread=thread: posts.read_thread(**owner_args, thread_uuid=thread),
                        lambda owner_args=owner_args, post=post: posts.post_history(**owner_args, post_uuid=post),
                        lambda owner_args=owner_args, thread=thread: posts.moderation_history(**owner_args, thread_uuid=thread),
                        lambda owner_args=owner_args: posts.category_topics(**owner_args, category_uuid=args["category_uuid"]),
                        lambda owner_args=owner_args: disc.category_history(**owner_args, category_uuid=args["category_uuid"]),
                        lambda owner_args=owner_args: disc.board_categories(**owner_args),
                    ]
                    for call in calls:
                        with self.subTest(actor=who.pk, invalid=invalid, thread=thread):  # noqa: SIM117 -- keep query observation scoped
                            # _actor uses the real ORM lock. No object _rows query
                            # may run before invalid owner inputs have been rejected.
                            with patch.object(disc, "_rows", wraps=disc._rows) as queries:
                                with self.assertRaises(Exception) as caught:
                                    call()
                                self.assertIs(type(caught.exception), ValueError)
                                self.assertEqual(str(caught.exception), "Choose an exact UUID for the discussion reference.")
                                queries.assert_not_called()
        for read, field in ((posts.read_thread, "thread_uuid"), (posts.post_history, "post_uuid"),
                            (posts.moderation_history, "thread_uuid")):
            self.unavailable(lambda read=read, field=field: read(credential=None, organism_uuid="bad", **{field: uuid4()}))

        category_scope = self.read_args(args)
        with self.subTest(case="test_p2d_s2_required_category_argument_is_exact_uuid"):
            for value in (None, "bad", 0):
                for read in (disc.category_history, posts.category_topics):
                    with self.subTest(value=value, read=read.__name__):
                        with self.assertRaises(Exception) as caught:
                            read(**category_scope, category_uuid=value)
                        self.assertIs(type(caught.exception), ValueError)
                        self.assertEqual(str(caught.exception), "Choose an exact UUID for the discussion reference.")
            self.assertEqual(len(disc.board_categories(**category_scope)["categories"]), 3)
            for read in (disc.category_history, posts.category_topics):
                self.unavailable(lambda read=read: read(credential=None, organism_uuid=lo.organism_id, category_uuid=None))

    def test_p2d_s4_new_identity_scope_and_replay_completion(self):
        """DISC1-23 / S4. Collisions are scoped before authority; denied new-ID probes have one answer."""
        lo, owner = fixtures.ordinary_birth()
        args = self.board(lo, owner)
        member = fixtures.add_member(lo)
        announcements = next(c for c in disc.board_categories(**self.read_args(args))["categories"]
                             if c["functional_code"] == "ANNOUNCEMENTS")
        ann = args | {"category_uuid": announcements["category_uuid"]}
        command = ann | {"thread_uuid": uuid4(), "post_uuid": uuid4(), "request_uuid": uuid4(),
                         "title": "Scope and replay", "markdown": "Notice"}
        topic = posts.start_topic(**command)
        other = self.start(ann)
        foreign, foreign_owner = fixtures.ordinary_birth()
        foreign_topic = self.start(self.board(foreign, foreign_owner))
        # The scope helper itself must refuse local existing IDs on a fresh request,
        # before any caller's office could affect the answer.
        payload = {"board": str(disc._board_id(lo.organism_id, None)), "category": str(ann["category_uuid"]),
                   "thread": str(topic["thread"]), "post": str(topic["post"]), "operation": "START", "request": str(uuid4())}
        with self.assertRaises(IntegrityError) as collision, transaction.atomic():
            disc._rows("SELECT public.discp_scope(%s::jsonb)", [json.dumps(payload)])
        self.assertEqual(collision.exception.__cause__.diag.message_primary, "DISC_UNAVAILABLE")
        with self.assertRaises(disc.DiscussionRefused) as mapped:
            disc.translate_refusal(collision.exception)
        self.assertEqual((mapped.exception.code, str(mapped.exception)), ("DISC_UNAVAILABLE", disc.UNAVAILABLE))
        member_ann = ann | {"credential": member.credential}
        for thread, post in ((topic["thread"], uuid4()), (topic["thread"], topic["post"]),
                             (uuid4(), topic["post"]), (foreign_topic["thread"], foreign_topic["post"]),
                             (uuid4(), uuid4())):
            self.refused("DISC_UNAVAILABLE", lambda thread=thread, post=post: self.start(member_ann, thread_uuid=thread, post_uuid=post))
        # A permitted caller also gets the scope refusal on existing anchors.
        self.refused("DISC_UNAVAILABLE", lambda: self.start(ann, thread_uuid=topic["thread"]))
        self.refused("DISC_UNAVAILABLE", lambda: self.start(ann, post_uuid=topic["post"]))
        for post in (topic["post"], other["post"], foreign_topic["post"]):
            self.refused("DISC_UNAVAILABLE", lambda post=post: self.reply(member_ann, topic, post_uuid=post))
        outsider = support.register_test_learner(username="disc-probe-"+uuid4().hex)
        for post in (topic["post"], foreign_topic["post"], uuid4()):
            self.refused("DISC_UNAVAILABLE", lambda post=post: self.reply(ann | {"credential": outsider.credential}, topic, post_uuid=post))
        # Persisted creation_act is the exemption: execution and deferred completion
        # both see these anchors as belonging to their own request on replay.
        before = models.LivingOrganismEvent.objects.count()
        self.assertEqual(posts.start_topic(**command), topic)
        self.assertEqual(models.LivingOrganismEvent.objects.count(), before)
        reply_args = member_ann | {"thread_uuid": topic["thread"], "post_uuid": uuid4(),
                                   "request_uuid": uuid4(), "markdown": "Reply"}
        with transaction.atomic():
            reply = posts.reply(**reply_args)
            self.assertEqual(posts.reply(**reply_args), reply)
        self.assertEqual(posts.reply(**reply_args), reply)
        self.assertEqual(len(posts.read_thread(**self.read_args(args), thread_uuid=topic["thread"])["posts"]), 2)
