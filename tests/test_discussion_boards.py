"""DISC1-01..05. P1 board, authority, audience, entitlement and lineage witnesses.

Fixture latitude: the existing guarded member helper; C1b's guarded probation
transition; a transaction-scoped SE reader substitution for lapse. No paid or
trial grant route or physical expiry is claimed. No import under a patch.
"""
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from core import models
from src.intevia.services import circle_commands, root_genesis
from src.intevia.services import discussion as disc
from src.intevia.services import organism_membership_service as membership
from src.intevia.services.organism_application_authority import (
    ProductionApplicationAuthorityProvider,
)
from src.intevia.services.organism_authority import S015Authority
from src.intevia.services.organism_entitlement_service import entitlement_effective
from tests import discussion_support, root_genesis_support
from tests import s015_0021_support as support
from tests import test_c1b_departure as departure
from tests import test_law_acts as fixtures


class DiscussionBoardTests(TransactionTestCase):
    def refused(self, code, call):
        with self.assertRaises(disc.DiscussionRefused) as caught:
            call()
        self.assertIs(type(caught.exception), disc.DiscussionRefused)
        self.assertEqual((caught.exception.code, str(caught.exception)), (code, disc.MESSAGES[code]))

    def unavailable(self, call):
        with self.assertRaises(disc.DiscussionUnavailable) as caught:
            call()
        self.assertIs(type(caught.exception), disc.DiscussionUnavailable)
        self.assertEqual(str(caught.exception), disc.UNAVAILABLE)

    def args(self, lo, actor, circle=None):
        return {"credential": actor.credential, "organism_uuid": lo.organism_id,
                "circle_uuid": circle.circle_uuid if circle else None}

    def create(self, args, name="Custom", **changes):
        key = uuid4()
        result = disc.create_category(**(dict(args, category_uuid=key, request_uuid=uuid4(), name=name) | changes))
        return key, result

    def test_defaults_rule_provisioning_and_root(self):
        """DISC1-01. Idempotent defaults, no invented office act and no read events."""
        lo, actor = fixtures.ordinary_birth()
        args = self.args(lo, actor)
        key = uuid4()
        result = disc.ensure_board(**args, request_uuid=key)
        self.assertEqual(disc.ensure_board(**args, request_uuid=key), result)
        self.assertEqual(disc.ensure_board(**args, request_uuid=uuid4()), result)
        act, = disc._rows("SELECT authority_source FROM public.core_discact WHERE act_uuid=%s", [result["act"]])
        self.assertEqual(act["authority_source"], {"kind": "RULE", "reference": "HG:DISC1:D1:20261106", "route": "PROVISION"})
        before = models.LivingOrganismEvent.objects.count()
        categories = disc.board_categories(**args)["categories"]
        self.assertEqual([(r["name"], r["access_level"]) for r in categories],
                         [("Coordination", "COORDINATORS"), ("Discussion", "MEMBERS"), ("Announcements", "MEMBERS")])
        member = fixtures.add_member(lo)
        member_read = disc.board_categories(**self.args(lo, member))["categories"]
        self.assertEqual([r["name"] for r in member_read], ["Discussion", "Announcements"])
        self.assertEqual([(r["can_start_topic"], r["can_reply"]) for r in member_read], [(True, True), (False, True)])
        self.assertEqual(models.LivingOrganismEvent.objects.count(), before)
        root = root_genesis.execute(root_genesis_support.command())
        root_args = self.args(root, root.founder_identity)
        disc.ensure_board(**root_args, request_uuid=uuid4())
        self.assertEqual(len(disc.board_categories(**root_args)["categories"]), 3)
        root_genesis_support.prepare_root_beta(root, root.founder_identity)
        root_circle = circle_commands.form(credential=root.founder_identity.credential,
            organism_uuid=root.organism_id, circle_uuid=uuid4(), kind="GENERIC",
            purpose="DISC1 root Circle board", request_uuid=uuid4())
        root_circle_args = self.args(root, root.founder_identity, root_circle)
        disc.ensure_board(**root_circle_args, request_uuid=uuid4())
        self.assertEqual(len(disc.board_categories(**root_circle_args)["categories"]), 3)
        self.assertEqual(disc._rows("SELECT count(*) AS n FROM public.core_discboard")[0]["n"], 3)

    def test_circle_coordinator_has_only_local_authority(self):
        """DISC1-02. Real Circle-only office works; LO-only office has no Circle privilege."""
        lo, actor = fixtures.ordinary_birth()
        disc.ensure_board(**self.args(lo, actor), request_uuid=uuid4())
        circle, coordinator = discussion_support.independent_circle(lo, actor)
        args = self.args(lo, coordinator, circle)
        disc.ensure_board(**args, request_uuid=uuid4())
        self.assertFalse(models.ContextualRoleAssignment.objects.filter(
            membership__identity=coordinator, role_definition__scope="LIVING_ORGANISM").exists())
        category, _ = self.create(args)
        current = disc.category_history(**args, category_uuid=category)[-1]
        self.assertEqual(current["authority_source"]["route"], "CIRCLE_COORDINATOR")
        revised = dict(args, category_uuid=category, request_uuid=uuid4(), predecessor=current["version_uuid"],
                       name="Circle work", description="Recorded", order=9, access="COORDINATORS")
        disc.revise_category(**revised)
        head = disc.category_history(**args, category_uuid=category)[-1]
        disc.retire_category(**args, category_uuid=category, request_uuid=uuid4(), predecessor=head["version_uuid"])
        self.assertTrue(disc.category_history(**args, category_uuid=category)[-1]["retired"])
        self.refused("DISC_AUTHORITY", lambda: self.create(self.args(lo, coordinator)))
        # The LO Coordinator left this Circle; write and read disclose no category.
        self.refused("DISC_UNAVAILABLE", lambda: self.create(self.args(lo, actor, circle)))
        self.unavailable(lambda: disc.board_categories(**self.args(lo, actor, circle)))
        self.refused("DISC_UNAVAILABLE", lambda: disc.create_category(**(args | {
            "circle_uuid": uuid4(), "category_uuid": uuid4(), "request_uuid": uuid4(), "name": "Absent"})))

    def test_access_probation_permission_and_departure(self):
        """DISC1-03. LO permission plus local category; no Circle admission and exact refusal parity."""
        lo, actor = fixtures.ordinary_birth()
        args = self.args(lo, actor)
        disc.ensure_board(**args, request_uuid=uuid4())
        member = fixtures.add_member(lo)
        self.refused("DISC_AUTHORITY", lambda: self.create(self.args(lo, member)))
        self.refused("DISC_PROBATION", lambda: self.create(args, access="PROBATIONARY"))
        disc.set_probation_access(credential=actor.credential, organism_uuid=lo.organism_id,
                                  enabled=True, request_uuid=uuid4())
        member_row = models.OrganismMembership.objects.get(identity=member, living_organism=lo)
        # Existing C1b fixture latitude: a guarded transition, not disabled guards.
        self.fx = SimpleNamespace(actor=actor, ids={"basis": lo.founding_authority_basis_id})
        departure.DepartureTests._state(self, member_row, "core_organismmembership", "core_organismmembershiptransition", "PROBATIONARY")
        member_args = self.args(lo, member)
        visible = disc.board_categories(**member_args)["categories"]
        self.assertEqual([r["name"] for r in visible], ["Welcome"])
        self.assertTrue(visible[0]["can_reply"])
        hidden = next(r for r in disc.board_categories(**args)["categories"] if r["name"] == "Discussion")
        self.unavailable(lambda: disc.category_history(**member_args, category_uuid=hidden["category_uuid"]))
        circle, coordinator = discussion_support.independent_circle(lo, actor)
        circle_args = self.args(lo, coordinator, circle)
        disc.ensure_board(**circle_args, request_uuid=uuid4())
        p, _ = self.create(circle_args, access="PROBATIONARY")
        self.assertFalse(models.CircleParticipation.objects.filter(circle=circle, membership=member_row).exists())
        self.assertEqual(len(disc.category_history(**self.args(lo, member, circle), category_uuid=p)), 1)
        disc.set_probation_access(credential=actor.credential, organism_uuid=lo.organism_id,
                                  enabled=False, request_uuid=uuid4())
        self.unavailable(lambda: disc.category_history(**self.args(lo, member, circle), category_uuid=p))
        outsider = support.register_test_learner(username="disc-outsider-" + uuid4().hex)
        for who, owner in ((outsider, lo.organism_id), (actor, uuid4())):
            self.unavailable(lambda who=who, owner=owner: disc.board_categories(credential=who.credential, organism_uuid=owner))
        # A real voluntary departure; no synthetic ENDED cache state.
        leaving = fixtures.add_member(lo)
        row = models.OrganismMembership.objects.get(identity=leaving, living_organism=lo)
        at = support.database_now()
        membership.OrganismMembershipService(authority=S015Authority(provider=ProductionApplicationAuthorityProvider()),
            clock=support.database_now).leave_organism(membership.LeaveOrganismCommand(
                credential=leaving.credential, membership_uuid=row.membership_uuid, confirmed=True,
                reason_class="VOLUNTARY", occurred_at=at, effective_at=at, request_reference=uuid4().hex, idempotency_key=uuid4().hex))
        self.unavailable(lambda: disc.board_categories(**self.args(lo, leaving)))

    def test_free_cap_beta_exception_and_lapse_projection(self):
        """DISC1-04. Nine free slots; beta >9; lapse-reader result keeps existing categories usable."""
        lo, actor = fixtures.ordinary_birth(offering_choice="FREE")
        args = self.args(lo, actor)
        disc.ensure_board(**args, request_uuid=uuid4())
        for number in range(9):
            self.create(args, name="Free " + str(number))
        self.assertFalse(entitlement_effective(lo.organism_id, disc.CAPABILITY))
        # Recurring mutation must fail this assertion, not a setup operation.
        self.refused("DISC_CATEGORY_LIMIT", lambda: self.create(args, name="Tenth"))
        self.assertEqual(len(disc.board_categories(**args)["categories"]), 12)
        beta, owner = fixtures.ordinary_birth()
        beta_args = self.args(beta, owner)
        disc.ensure_board(**beta_args, request_uuid=uuid4())
        self.assertTrue(entitlement_effective(beta.organism_id, disc.CAPABILITY))
        keys = [self.create(beta_args, name="Beta " + str(number))[0] for number in range(12)]
        migration = import_module("core.migrations.0072_discussion_boards")
        # No physical expiry/grant route exists on this base. Substitute only
        # the derived capability result, inside a rollback-safe transaction.
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("SELECT prosrc FROM pg_proc WHERE oid='public.s015_entitlement_effective(uuid,text,timestamptz)'::regprocedure")
            original = cur.fetchone()[0]
            limited = "SELECT p_capability<>'DISCUSSION_CUSTOM_CATEGORIES' AND (" + original.strip() + ")"
            cur.execute(migration.SE_HEADER + limited + "$$;", params=None)
            try:
                rows = disc.board_categories(**beta_args)["categories"]
                self.assertEqual(len(rows), 15)
                self.assertTrue(all(r["can_reply"] for r in rows))
                head = disc.category_history(**beta_args, category_uuid=keys[0])[-1]
                disc.revise_category(**beta_args, category_uuid=keys[0], predecessor=head["version_uuid"],
                    request_uuid=uuid4(), name="Still usable", description="After lapse projection", order=0, access="MEMBERS")
                self.refused("DISC_CATEGORY_LIMIT", lambda: self.create(beta_args, name="Thirteenth"))
            finally:
                cur.execute(migration.SE_HEADER + original + "$$;", params=None)
        self.assertTrue(entitlement_effective(beta.organism_id, disc.CAPABILITY))

    test_free_cap_beta_exception_and_lapse_projection.ISOLATION_FULL_CAPTURE = True

    def test_history_retirement_guards_rollback_and_reversal(self):
        """DISC1-05. Stable IDs, immutable versions, free-slot reuse, atomicity and populated reversal."""
        lo, actor = fixtures.ordinary_birth(offering_choice="FREE")
        args = self.args(lo, actor)
        provision = disc.ensure_board(**args, request_uuid=uuid4())
        keys = [self.create(args, name="Category " + str(n))[0] for n in range(9)]
        first = disc.category_history(**args, category_uuid=keys[0])[-1]
        self.assertEqual(first["version_number"], 1)
        revise = dict(args, category_uuid=keys[0], predecessor=first["version_uuid"], request_uuid=uuid4(),
                      name="Renamed", description="  Exact description\n", order=-1, access="MEMBERS")
        changed = disc.revise_category(**revise)
        self.assertEqual(disc.revise_category(**revise), changed)
        history = disc.category_history(**args, category_uuid=keys[0])
        self.assertEqual(history[0], first)
        self.assertEqual(history[1]["description"], revise["description"])
        self.assertEqual(history[1]["version_number"], 2)
        self.assertNotEqual(history[0]["fingerprint"], history[1]["fingerprint"])
        self.refused("DISC_PREDECESSOR", lambda: disc.revise_category(**(revise | {"request_uuid": uuid4()})))
        self.refused("DISC_REQUEST_CONFLICT", lambda: disc.revise_category(**(revise | {"name": "Changed request"})))
        disc.retire_category(**args, category_uuid=keys[0], predecessor=history[-1]["version_uuid"], request_uuid=uuid4())
        self.create(args, name="Replacement slot")
        rows = disc.board_categories(**args)["categories"]
        self.assertEqual(rows[0]["name"], "Renamed")
        self.assertTrue(rows[0]["retired"])
        self.assertFalse(rows[0]["can_reply"])
        functional = next(r for r in rows if r["functional_code"] == "DISCUSSION")
        self.refused("DISC_FUNCTIONAL", lambda: disc.retire_category(**args, category_uuid=functional["category_uuid"],
            predecessor=functional["version_uuid"], request_uuid=uuid4()))
        with self.assertRaises(IntegrityError) as immutable, transaction.atomic(), connection.cursor() as cur:
            cur.execute("DELETE FROM public.core_disccategoryversion WHERE category_uuid=%s", [keys[0]])
        self.assertEqual(immutable.exception.__cause__.diag.message_primary, "S015 immutable or append-only row mutation refused")
        # Direct forged act is refused by source verification before commit.
        with self.assertRaises(IntegrityError) as forged, transaction.atomic(), connection.cursor() as cur:
            key = uuid4()
            cur.execute("""INSERT INTO public.core_discact(act_uuid,board_uuid,operation,actor_id,actor_epoch,
                authority_source,payload,fingerprint,received_at)
                SELECT %s,board_uuid,'PROBATION_ACCESS',actor_id,actor_epoch,'{"kind":"CQ"}'::jsonb,
                       '{}'::jsonb,'bad',statement_timestamp() FROM public.core_discact WHERE act_uuid=%s""",
                [key, provision["act"]])
        self.assertEqual(forged.exception.__cause__.diag.message_primary, "DISC_AUTHORITY")
        count = disc._rows("SELECT count(*) AS n FROM public.core_discact")[0]["n"]
        event_count = models.LivingOrganismEvent.objects.count()
        # Roll back an otherwise valid act through the caller's transaction.
        with self.assertRaisesRegex(RuntimeError, "deliberate rollback"), transaction.atomic():
            disc.set_probation_access(credential=actor.credential, organism_uuid=lo.organism_id, enabled=True, request_uuid=uuid4())
            raise RuntimeError("deliberate rollback")
        self.assertEqual(disc._rows("SELECT count(*) AS n FROM public.core_discact")[0]["n"], count)
        self.assertEqual(models.LivingOrganismEvent.objects.count(), event_count)
        migration = import_module("core.migrations.0072_discussion_boards")
        with connection.schema_editor(atomic=True) as editor:  # noqa: SIM117 -- inspect inside editor
            with self.assertRaises(RuntimeError) as reversal:
                migration.reverse(None, editor)
        self.assertEqual(str(reversal.exception), "DISC1 reversal refused while discussion history exists.")
        self.assertEqual(len(disc.category_history(**args, category_uuid=keys[0])), 3)

    test_history_retirement_guards_rollback_and_reversal.ISOLATION_FULL_CAPTURE = True

    def test_p2c_r1_circle_welcome_requires_deliberate_local_act(self):
        """DISC1-11 / R1. Provisioning never opens Circle Welcome; explicit OFFICE does."""
        lo, owner = fixtures.ordinary_birth()
        circle, coordinator = discussion_support.independent_circle(lo, owner)
        args = self.args(lo, coordinator, circle)
        disc.ensure_board(**args, request_uuid=uuid4())
        disc.set_probation_access(credential=owner.credential, organism_uuid=lo.organism_id,
                                 enabled=True, request_uuid=uuid4())
        before = models.LivingOrganismEvent.objects.count()
        disc.ensure_board(**args, request_uuid=uuid4())
        self.assertEqual(models.LivingOrganismEvent.objects.count(), before)
        self.assertNotIn("WELCOME", [c["functional_code"] for c in disc.board_categories(**args)["categories"]])
        disc.set_probation_access(credential=owner.credential, organism_uuid=lo.organism_id,
                                 enabled=False, request_uuid=uuid4())
        self.refused("DISC_PROBATION", lambda: disc.open_circle_welcome(**args, request_uuid=uuid4()))
        disc.set_probation_access(credential=owner.credential, organism_uuid=lo.organism_id,
                                 enabled=True, request_uuid=uuid4())
        self.refused("DISC_UNAVAILABLE", lambda: disc.open_circle_welcome(**self.args(lo, owner, circle), request_uuid=uuid4()))
        request = uuid4()
        opened = disc.open_circle_welcome(**args, request_uuid=request)
        self.assertEqual(disc.open_circle_welcome(**args, request_uuid=request), opened)
        welcome = next(c for c in disc.board_categories(**args)["categories"] if c["functional_code"] == "WELCOME")
        history = disc.category_history(**args, category_uuid=welcome["category_uuid"])
        self.assertEqual((len(history), history[0]["actor_id"], history[0]["authority_source"]["kind"],
                          history[0]["authority_source"]["route"]), (1, coordinator.pk, "OFFICE", "CIRCLE_COORDINATOR"))
        self.refused("DISC_FUNCTIONAL", lambda: disc.retire_category(**args, category_uuid=welcome["category_uuid"],
            predecessor=welcome["version_uuid"], request_uuid=uuid4()))
        self.assertIn("WELCOME", [c["functional_code"] for c in disc.board_categories(**self.args(lo, owner))["categories"]])

    def test_p2c_r4_probation_audience_is_board_specific(self):
        """DISC1-14 / R4. Closed-to-probation Circle, unprovisioned Circle and unknown are indistinguishable."""
        lo, owner = fixtures.ordinary_birth()
        disc.set_probation_access(credential=owner.credential, organism_uuid=lo.organism_id,
                                 enabled=True, request_uuid=uuid4())
        circle, coordinator = discussion_support.independent_circle(lo, owner)
        unopened, _ = discussion_support.independent_circle(lo, owner)
        args = self.args(lo, coordinator, circle)
        disc.ensure_board(**args, request_uuid=uuid4())
        category = next(c for c in disc.board_categories(**args)["categories"] if c["functional_code"] == "DISCUSSION")
        member = fixtures.add_member(lo)
        row = models.OrganismMembership.objects.get(identity=member, living_organism=lo)
        self.fx = SimpleNamespace(actor=owner, ids={"basis": lo.founding_authority_basis_id})
        departure.DepartureTests._state(self, row, "core_organismmembership", "core_organismmembershiptransition", "PROBATIONARY")
        for circle_uuid in (circle.circle_uuid, unopened.circle_uuid, uuid4()):
            target = self.args(lo, member) | {"circle_uuid": circle_uuid}
            with self.subTest(circle=circle_uuid):
                self.unavailable(lambda target=target: disc.board_categories(**target))
                self.unavailable(lambda target=target: disc.category_history(**target, category_uuid=category["category_uuid"]))
                self.refused("DISC_UNAVAILABLE", lambda target=target: disc.ensure_board(**target, request_uuid=uuid4()))
                self.refused("DISC_UNAVAILABLE", lambda target=target: self.create(target))
        disc.open_circle_welcome(**args, request_uuid=uuid4())
        probation = self.args(lo, member, circle)
        visible = disc.board_categories(**probation)["categories"]
        self.assertEqual([c["functional_code"] for c in visible], ["WELCOME"])
        self.assertEqual(len(disc.category_history(**probation, category_uuid=visible[0]["category_uuid"])), 1)
        # Board is now visible, but probation does not confer provisioning authority.
        self.refused("DISC_AUTHORITY", lambda: disc.ensure_board(**probation, request_uuid=uuid4()))
        self.assertFalse(models.CircleParticipation.objects.filter(circle=circle, membership=row).exists())

        # S5: custom probationary categories, unlike Welcome, can retire or
        # change their audience. Use separate boards so Welcome cannot mask either case.
        retired_circle, retired_owner = discussion_support.independent_circle(lo, owner)
        retired_args = self.args(lo, retired_owner, retired_circle)
        disc.ensure_board(**retired_args, request_uuid=uuid4())
        category, _ = self.create(retired_args, access="PROBATIONARY")
        head = disc.category_history(**retired_args, category_uuid=category)[-1]
        disc.retire_category(**retired_args, category_uuid=category, predecessor=head["version_uuid"], request_uuid=uuid4())
        probation_args = self.args(lo, member, retired_circle)
        history = disc.category_history(**probation_args, category_uuid=category)
        self.assertEqual([v["retired"] for v in history], [False, True])
        visible = disc.board_categories(**probation_args)["categories"]
        self.assertEqual([v["category_uuid"] for v in visible], [category])
        self.assertFalse(visible[0]["can_reply"])
        changed_circle, changed_owner = discussion_support.independent_circle(lo, owner)
        changed_args = self.args(lo, changed_owner, changed_circle)
        disc.ensure_board(**changed_args, request_uuid=uuid4())
        changed_category, _ = self.create(changed_args, access="PROBATIONARY")
        head = disc.category_history(**changed_args, category_uuid=changed_category)[-1]
        changed_probation = self.args(lo, member, changed_circle)
        self.assertEqual(len(disc.category_history(**changed_probation, category_uuid=changed_category)), 1)
        disc.revise_category(**changed_args, category_uuid=changed_category, predecessor=head["version_uuid"],
            request_uuid=uuid4(), name="Now members", description="", order=0, access="MEMBERS")
        for target in (changed_probation, changed_probation | {"circle_uuid": uuid4()}):
            self.unavailable(lambda target=target: disc.board_categories(**target))
            self.unavailable(lambda target=target: disc.category_history(**target, category_uuid=changed_category))

    def test_p2c_r6_foreign_category_and_functional_refusal_order(self):
        """DISC1-16 / R6. Foreign category IDs disclose nothing; functional check precedes predecessor."""
        lo, owner = fixtures.ordinary_birth()
        args = self.args(lo, owner)
        disc.ensure_board(**args, request_uuid=uuid4())
        other, other_owner = fixtures.ordinary_birth()
        other_args = self.args(other, other_owner)
        disc.ensure_board(**other_args, request_uuid=uuid4())
        foreign, _ = self.create(other_args)
        self.refused("DISC_UNAVAILABLE", lambda: self.create(args, category_uuid=foreign))
        local, _ = self.create(args)
        self.refused("DISC_PREDECESSOR", lambda: self.create(args, category_uuid=local))
        functional = next(c for c in disc.board_categories(**args)["categories"] if c["functional_code"] == "DISCUSSION")
        self.refused("DISC_FUNCTIONAL", lambda: disc.retire_category(**args, category_uuid=functional["category_uuid"],
            predecessor=uuid4(), request_uuid=uuid4()))
        self.refused("DISC_FUNCTIONAL", lambda: disc.revise_category(**args, category_uuid=functional["category_uuid"],
            predecessor=uuid4(), request_uuid=uuid4(), name="No", description="", order=0, access="MEMBERS"))

    def test_p2c_r9_input_contract_and_single_actor_acquisition(self):
        """DISC1-19 / R9. Typed command refusals, read ValueError after auth, one real actor lock."""
        lo, owner = fixtures.ordinary_birth()
        args = self.args(lo, owner)
        for enabled in ("yes", 1, None):
            with self.assertRaises(ValueError) as invalid:
                disc.set_probation_access(credential=owner.credential, organism_uuid=lo.organism_id,
                                         enabled=enabled, request_uuid=uuid4())
            self.assertIs(type(invalid.exception), disc.DiscussionRefused)
            self.assertEqual((invalid.exception.code, str(invalid.exception)), ("DISC_INPUT", disc.MESSAGES["DISC_INPUT"]))
        self.refused("DISC_INPUT", lambda: disc.ensure_board(**args, request_uuid="not-a-uuid"))
        self.refused("DISC_INPUT", lambda: self.create(args, category_uuid="not-a-uuid"))
        # Wrap the real lock; no imports or captured aliases are created under this patch.
        with patch.object(disc, "_lock_actor", wraps=disc._lock_actor) as actor_lock:
            disc.set_probation_access(credential=owner.credential, organism_uuid=lo.organism_id,
                                     enabled=True, request_uuid=uuid4())
        self.assertEqual(actor_lock.call_count, 1)
        with patch.object(disc, "_lock_actor", wraps=disc._lock_actor) as actor_lock:
            disc.set_probation_access(credential=owner.credential, organism_uuid=lo.organism_id,
                                     enabled=False, request_uuid=uuid4())
        self.assertEqual(actor_lock.call_count, 1)
        with self.assertRaises(ValueError) as malformed:
            disc.board_categories(**(args | {"organism_uuid": "not-a-uuid"}))
        self.assertIs(type(malformed.exception), ValueError)
        self.assertEqual(str(malformed.exception), "Choose an exact UUID for the discussion reference.")
        self.unavailable(lambda: disc.board_categories(credential=None, organism_uuid="not-a-uuid"))

    def test_p2d_s3_welcome_new_board_replay_and_already_open_refusal(self):
        """DISC1-22 / S3. Explicit Welcome provisions once; new request refuses plainly without records."""
        lo, owner = fixtures.ordinary_birth()
        disc.set_probation_access(credential=owner.credential, organism_uuid=lo.organism_id,
                                 enabled=True, request_uuid=uuid4())
        circle, coordinator = discussion_support.independent_circle(lo, owner)
        args = self.args(lo, coordinator, circle)
        board_uuid = disc._board_id(lo.organism_id, circle.circle_uuid)
        self.assertFalse(disc._rows("SELECT board_uuid FROM public.core_discboard WHERE board_uuid=%s", [board_uuid]))
        request = uuid4()
        opened = disc.open_circle_welcome(**args, request_uuid=request)
        self.assertEqual([c["functional_code"] for c in disc.board_categories(**args)["categories"]],
                         ["COORDINATION", "DISCUSSION", "ANNOUNCEMENTS", "WELCOME"])
        before = {name: disc._rows("SELECT count(*) AS n FROM public."+name)[0]["n"] for name in
                  ("core_discboard", "core_discact", "core_disccategory", "core_disccategoryversion", "core_livingorganismevent")}
        self.assertEqual(disc.open_circle_welcome(**args, request_uuid=request), opened)
        with self.assertRaises(disc.DiscussionRefused) as caught:
            disc.open_circle_welcome(**args, request_uuid=uuid4())
        self.assertEqual((caught.exception.code, str(caught.exception)),
                         ("DISC_WELCOME_OPEN", "Welcome is already open on this board."))
        self.assertEqual(before, {name: disc._rows("SELECT count(*) AS n FROM public."+name)[0]["n"] for name in before})
        acts = disc._rows("SELECT operation,authority_source FROM public.core_discact WHERE board_uuid=%s", [board_uuid])
        self.assertEqual({(a["operation"], a["authority_source"]["kind"]) for a in acts},
                         {("PROVISION", "RULE"), ("WELCOME", "OFFICE")})
        # Without LO permission, a different unopened board still gets DISC_PROBATION,
        # and the composite command rolls its attempted provision back completely.
        unopened, second = discussion_support.independent_circle(lo, owner)
        disc.set_probation_access(credential=owner.credential, organism_uuid=lo.organism_id,
                                 enabled=False, request_uuid=uuid4())
        self.refused("DISC_PROBATION", lambda: disc.open_circle_welcome(**self.args(lo, second, unopened), request_uuid=uuid4()))
        self.assertFalse(disc._rows("SELECT board_uuid FROM public.core_discboard WHERE board_uuid=%s",
                                   [disc._board_id(lo.organism_id, unopened.circle_uuid)]))
