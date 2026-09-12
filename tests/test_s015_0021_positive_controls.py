"""S015 migration 0021 — positive controls (U21g Phase 2).

For every negative class in tests/test_s015_0021_negative_direct_sql.py, the lawful route that must still work. The suite
is built around the complete founding population on the lawful route (all twelve aggregates anchored, caches
database-computed). Node identity: tests/test_s015_0021_positive_controls.py::<Class>::<test>.
"""
import datetime as dt
import hashlib
import json
import uuid
from unittest import skipUnless

from django.db import connection, transaction
from django.test import TransactionTestCase

from s015_0021_support import (
    FIVE_RESERVED, SIX_CODES, T0, add_remaining_aggregates, append_event, append_second_event, drop_unset_check,
    empty_fp, found_nonroot, found_root, fresh, refresh, restore_unset_check, seed_authority, seed_identity,
)

POSTGRESQL_ONLY = skipUnless(connection.vendor == "postgresql", "S015 0021 guardians are PostgreSQL triggers")
TOKENS = [("core_livingorganism", "lo"), ("core_circle", "circle"), ("core_organismmembership", "membership"),
          ("core_contextualroleassignment", None), ("core_membershipcondition", "condition"), ("core_authoritybasis", "basis"),
          ("core_determinationcontestcase", "contest_case"), ("core_essentialcoveragecase", "coverage_case"),
          ("core_restrictedcontinuitycase", "continuity_case"), ("core_governedvisibilitygrant", "grant"),
          ("core_governedobligationcase", "obligation_case"), ("core_planningclassificationcase", "planning_case")]
UNSCAFFOLDED_FIXED_VECTOR_TESTS = frozenset({
    "test_pos_control_installed_sql_reproduces_all_eight_vectors_seven_rederived_plus_one_additional",
    "test_pos_control_installed_inventories_equal_published_for_all_twelve_event_tables",
    "test_pos_control_record_refuses_missing_column_unknown_key_and_unruled_type",
})


@POSTGRESQL_ONLY
class _S015PositiveBase(TransactionTestCase):
    NO_PLATFORM_ROOT_SCAFFOLD = frozenset()

    def setUp(self):
        super().setUp()
        self._uses_platform_root_scaffold = (
            self._testMethodName not in self.NO_PLATFORM_ROOT_SCAFFOLD
            and self._testMethodName not in UNSCAFFOLDED_FIXED_VECTOR_TESTS
        )
        with connection.cursor() as cur:
            if self._uses_platform_root_scaffold:
                drop_unset_check(cur)
            else:
                cur.execute("ALTER TABLE public.core_livingorganism VALIDATE CONSTRAINT s015_platform_root_unset_ck")

    def tearDown(self):
        if self._uses_platform_root_scaffold:
            with connection.cursor() as cur:
                cur.execute(
                    "ALTER TABLE public.core_livingorganism ADD CONSTRAINT "
                    "s015_platform_root_unset_ck CHECK (NOT platform_root) NOT VALID"
                )
        super().tearDown()

    def commits(self, fn):
        with transaction.atomic():
            with connection.cursor() as cur:
                return fn(cur)

    def route(self, token, anchor_id, state_at=T0, known_at=None):
        with connection.cursor() as cur:
            cur.execute("SELECT projection, eligible_count, served_from, fingerprint FROM public.s015_effective_state(%s, %s, %s, %s)",
                        (token, anchor_id, state_at, known_at or dt.datetime.now(dt.timezone.utc)))
            return cur.fetchone()

    def head(self, table, col, anchor_id):
        with connection.cursor() as cur:
            cur.execute(f"SELECT {col} FROM {table} WHERE id = %s", (anchor_id,)); return cur.fetchone()[0]


class AppendOnly(_S015PositiveBase):
    def test_pos_control_lawful_next_event_commits(self):
        ids = self.commits(lambda cur: found_root(cur))
        e2 = self.commits(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "RESTRICT", "ACTIVE", "RESTRICTED_CONTINUITY", ids["identity"], ids["basis"], fresh(), pred_id=ids["lo_event"]))[0]
        self.assertEqual(self.head("core_livingorganism", "head_event_id", ids["lo"]), e2)

    def test_pos_control_new_requirement_version_commits(self):
        ids = self.commits(lambda cur: found_root(cur))
        self.commits(lambda cur: cur.execute("INSERT INTO core_essentialcoveragerequirement (requirement_uuid, roster_version, responsibility_domain_code, qualifying_role_code, minimum_active_occupants, scope, effective_from, living_organism_id) VALUES (%s, 2, 'GOVERNANCE', 'COORDINATOR', 2, 'LIVING_ORGANISM', %s, %s)",
                                             (str(uuid.uuid4()), T0, ids["lo"])))

    def test_pos_control_one_lawful_insert_on_each_event_table(self):
        ids = self.commits(lambda cur: add_remaining_aggregates(cur, found_root(cur), fresh()))
        self.assertEqual(len(ids["events"]), 12)
        for event_table, event_id in ids["events"].items():
            with self.subTest(event_table=event_table):
                self.assertIsInstance(event_id, int)


class ChainClosure(_S015PositiveBase):
    def test_pos_control_complete_founding_all_twelve_aggregates(self):
        ids = self.commits(lambda cur: add_remaining_aggregates(cur, found_root(cur), fresh()))
        for token, key in TOKENS:
            anchor = ids["assignments"]["INTEVIA_FOUNDER_STEWARD"] if key is None else ids[key]
            projection, n, served, fp = self.route(token, anchor)
            with self.subTest(token=token):
                self.assertEqual(n, 1); self.assertIsNotNone(projection); self.assertTrue(fp.startswith("s015f3:"))

    def test_pos_control_consecutive_appends_advance_head(self):
        ids = self.commits(lambda cur: found_root(cur))
        e2 = self.commits(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "A", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(), pred_id=ids["lo_event"]))[0]
        e3 = self.commits(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 3, "B", "DORMANT", "ACTIVE", ids["identity"], ids["basis"], fresh(), pred_id=e2))[0]
        self.assertEqual(self.head("core_livingorganism", "head_event_id", ids["lo"]), e3)

    def test_pos_control_basis_commits_with_empty_invalidation_chain(self):
        ids = self.commits(lambda cur: found_root(cur))
        self.assertIsNone(self.head("core_authoritybasis", "head_invalidation_event_id", ids["basis"]))
        self.assertEqual(self.route("core_authoritybasis", ids["basis"])[0], None)


class FourTimeGrammar(_S015PositiveBase):
    def test_pos_control_recorded_at_database_assigned(self):
        ids = self.commits(lambda cur: found_root(cur))
        _, recorded = self.commits(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "A", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(), pred_id=ids["lo_event"]))
        self.assertIsNotNone(recorded); self.assertGreaterEqual(recorded, T0)

    def test_pos_control_retrospective_with_basis_reference(self):
        ids = self.commits(lambda cur: found_root(cur))
        self.commits(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "A", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(), pred_id=ids["lo_event"],
                                              eff=T0 + dt.timedelta(minutes=30), occ=T0 + dt.timedelta(minutes=30), rec=T0 + dt.timedelta(hours=1)))  # retrospective by 30 min


class VocabulariesAndShapes(_S015PositiveBase):
    def test_pos_control_each_lawful_circle_token(self):
        ids = self.commits(lambda cur: add_remaining_aggregates(cur, found_root(cur), fresh()))
        prev, prior = self.head("core_circle", "head_event_id", ids["circle"]), "DORMANT"
        for seq, token in [(2, "ELIGIBLE"), (3, "SUSPENDED"), (4, "CLOSED")]:
            prev = self.commits(lambda cur: append_event(cur, "core_circlestateevent", ids["circle"], seq, "T", prior, token, ids["identity"], ids["basis"], fresh(), pred_id=prev))[0]
            prior = token

    def test_pos_control_each_of_six_assignment_tokens_and_actions(self):
        ids = self.commits(lambda cur: found_root(cur))
        a = ids["assignments"]["INTEVIA_FOUNDER_STEWARD"]
        prev, prior = self.head("core_contextualroleassignment", "head_state_event_id", a), "ACTIVE"
        for seq, (action, token) in enumerate([("SUSPEND", "SUSPENDED"), ("ASSIGN", "PROBATIONARY"), ("REASSIGN", "ENDED"), ("PRIVACY_TRANSFORM", "PRIVACY_TRANSFORMED")], start=2):
            prev = self.commits(lambda cur: append_event(cur, "core_contextualroleassignmentstateevent", a, seq, action, prior, token, ids["identity"], ids["basis"], fresh(), pred_id=prev))[0]
            prior = token

    def test_pos_control_role_scope_circle_token(self):
        ids = self.commits(lambda cur: found_root(cur))
        self.commits(lambda cur: cur.execute("INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, created_at, living_organism_id) VALUES (%s, 'TREASURER', 'CIRCLE', 1, true, now(), %s)", (str(uuid.uuid4()), ids["lo"])))


class CacheBoundary(_S015PositiveBase):
    def test_pos_control_refresh_moves_coordinates_and_route_requalifies(self):
        ids = self.commits(lambda cur: found_root(cur))
        known = dt.datetime.now(dt.timezone.utc)
        fp = self.commits(lambda cur: (cur.execute("SELECT public.s015_refresh_governed_cache('core_livingorganism', %s, %s, %s)", (ids["lo"], T0, known)), cur.fetchone()[0])[1])
        projection, n, served, route_fp = self.route("core_livingorganism", ids["lo"], T0, known)
        self.assertEqual((projection, n, served, route_fp), ("ACTIVE", 1, "CACHE_REQUALIFIED", fp))

    def test_pos_control_obligation_five_tuple_requalifies(self):
        ids = self.commits(lambda cur: add_remaining_aggregates(cur, found_root(cur), fresh()))
        known = dt.datetime.now(dt.timezone.utc)
        self.commits(lambda cur: cur.execute("SELECT public.s015_refresh_governed_cache('core_governedobligationcase', %s, %s, %s)", (ids["obligation_case"], T0, known)))
        with connection.cursor() as cur:
            cur.execute("SELECT projection, legal_deadline_status, served_from FROM public.s015_effective_state('core_governedobligationcase', %s, %s, %s)", (ids["obligation_case"], T0, known))
            self.assertEqual(cur.fetchone(), ("OBLIGATION_BLOCKED_PENDING_AUTHORITY", "UNKNOWN", "CACHE_REQUALIFIED"))

    def test_pos_control_fold_excludes_future_effective_and_later_recorded(self):
        ids = self.commits(lambda cur: found_root(cur))
        self.commits(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "A", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(), pred_id=ids["lo_event"],
                                              eff=T0 + dt.timedelta(days=30), occ=T0 + dt.timedelta(hours=2), rec=T0 + dt.timedelta(hours=2)))
        self.assertEqual(self.route("core_livingorganism", ids["lo"], T0 + dt.timedelta(days=1))[0:2], ("ACTIVE", 1))   # future-effective not yet current
        self.assertEqual(self.route("core_livingorganism", ids["lo"], T0 + dt.timedelta(days=31))[0:2], ("DORMANT", 2))
        self.assertEqual(self.route("core_livingorganism", ids["lo"], T0 + dt.timedelta(days=31), T0)[1], 0)         # known_at before either record → nothing eligible

    def test_pos_control_cache_miss_returns_live_fold_not_stale_cache_or_null(self):
        ids = self.commits(lambda cur: found_root(cur))
        self.commits(
            lambda cur: append_event(
                cur,
                "core_livingorganismevent",
                ids["lo"],
                2,
                "RESTRICT",
                "ACTIVE",
                "DORMANT",
                ids["identity"],
                ids["basis"],
                fresh(),
                pred_id=ids["lo_event"],
                eff=T0 + dt.timedelta(hours=1),
                occ=T0 + dt.timedelta(hours=1),
                rec=T0 + dt.timedelta(hours=1),
            )
        )
        projection, eligible_count, served_from, _fingerprint = self.route(
            "core_livingorganism", ids["lo"], T0 + dt.timedelta(hours=2)
        )
        self.assertEqual((projection, eligible_count, served_from), ("DORMANT", 2, "FOLD"))

    def test_pos_control_obligation_reducer_takes_all_five_values_from_last_event(self):
        ids = self.commits(lambda cur: add_remaining_aggregates(cur, found_root(cur), fresh()))
        first_event = ids["events"]["core_obligationstateevent"]
        qualified_at = T0 + dt.timedelta(days=10)
        effective_deadline = T0 + dt.timedelta(days=11)
        second_event = self.commits(
            lambda cur: append_event(
                cur,
                "core_obligationstateevent",
                ids["obligation_case"],
                2,
                "QUALIFY",
                "OBLIGATION_BLOCKED_PENDING_AUTHORITY",
                "OBLIGATION_ACTIVE",
                ids["identity"],
                ids["basis"],
                fresh(),
                pred_id=first_event,
                eff=T0 + dt.timedelta(hours=1),
                occ=T0 + dt.timedelta(hours=1),
                rec=T0 + dt.timedelta(hours=1),
                extra={
                    "legal_deadline_status": "QUALIFIED",
                    "qualified_legal_deadline": qualified_at,
                    "effective_deadline": effective_deadline,
                    "legal_basis_determination_id": ids["determination"],
                },
            )
        )[0]
        with connection.cursor() as cur:
            cur.execute(
                "SELECT (p).* FROM public.s015_reduce_obligation(ARRAY[%s, %s]::bigint[]) p",
                (first_event, second_event),
            )
            self.assertEqual(
                cur.fetchone(),
                ("OBLIGATION_ACTIVE", "QUALIFIED", qualified_at, effective_deadline, ids["determination"]),
            )


class PriorState(_S015PositiveBase):
    def test_pos_control_prior_state_equals_effective_order_predecessor(self):
        ids = self.commits(lambda cur: found_root(cur))
        self.commits(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "A", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(), pred_id=ids["lo_event"]))

    def test_pos_control_retrospective_e3_commits_and_e2_is_not_rechecked(self):
        # U21g-13: E1 (T0) ACTIVE; E2 effective T0+2h prior ACTIVE → DORMANT; E3 retrospective effective T0+1h prior ACTIVE → CLOSED commits
        ids = self.commits(lambda cur: found_root(cur))
        e2 = self.commits(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "A", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(), pred_id=ids["lo_event"],
                                                   eff=T0 + dt.timedelta(hours=2), occ=T0 + dt.timedelta(hours=2), rec=T0 + dt.timedelta(hours=2)))[0]
        self.commits(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 3, "B", "ACTIVE", "CLOSED", ids["identity"], ids["basis"], fresh(), pred_id=e2,
                                              eff=T0 + dt.timedelta(hours=1), occ=T0 + dt.timedelta(hours=1), rec=T0 + dt.timedelta(hours=3)))
        with connection.cursor() as cur:
            cur.execute("SELECT sequence, prior_state, resulting_state FROM core_livingorganismevent WHERE living_organism_id = %s ORDER BY sequence", (ids["lo"],))
            self.assertEqual(cur.fetchall(), [(1, None, "ACTIVE"), (2, "ACTIVE", "DORMANT"), (3, "ACTIVE", "CLOSED")])
        self.assertEqual(self.route("core_livingorganism", ids["lo"], T0 + dt.timedelta(days=1))[0], "DORMANT")  # fold order: E3 (1h) then E2 (2h)

    def test_pos_control_empty_predecessor_set_commits_with_null(self):
        ids = self.commits(lambda cur: found_root(cur))
        self.commits(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "A", None, "DORMANT", ids["identity"], ids["basis"], fresh(), pred_id=ids["lo_event"],
                                              eff=T0 - dt.timedelta(days=1), occ=T0 - dt.timedelta(days=1), rec=T0))


class Structure(_S015PositiveBase):
    def test_pos_control_circle_scoped_assignment(self):
        ids = self.commits(lambda cur: add_remaining_aggregates(cur, found_root(cur), fresh()))
        def lawful(cur):
            cur.execute("INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, created_at, living_organism_id) VALUES (%s, 'CIRCLE_LEAD', 'CIRCLE', 1, true, now(), %s) RETURNING id", (str(uuid.uuid4()), ids["lo"]))
            role = cur.fetchone()[0]; au = str(uuid.uuid4())
            cur.execute("INSERT INTO core_contextualroleassignment (assignment_uuid, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, membership_id, role_definition_id, circle_id) VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s, %s) RETURNING id",
                        (au, T0, T0, empty_fp(cur, "core_contextualroleassignment", au), ids["membership"], role, ids["circle"]))
            a = cur.fetchone()[0]
            append_event(cur, "core_contextualroleassignmentstateevent", a, 1, "ASSIGN", None, "ACTIVE", ids["identity"], ids["basis"], fresh()); refresh(cur, "core_contextualroleassignment", a)
        self.commits(lawful)

    def test_pos_control_mentorship_with_same_organism_mentor(self):
        ids = self.commits(lambda cur: found_root(cur))
        def lawful(cur):
            other = seed_identity(cur, f"mentor-{fresh()}"); mu = str(uuid.uuid4())
            cur.execute("INSERT INTO core_organismmembership (membership_uuid, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, identity_id, living_organism_id) VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) RETURNING id",
                        (mu, T0, T0, empty_fp(cur, "core_organismmembership", mu), other, ids["lo"]))
            m = cur.fetchone()[0]
            append_event(cur, "core_organismmembershiptransition", m, 1, "JOIN", None, "ACTIVE", ids["identity"], ids["basis"], fresh(), extra={"reason_class": "X"}); refresh(cur, "core_organismmembership", m)
            cur.execute("INSERT INTO core_membershipcondition (condition_uuid, condition_kind, subject_membership_id, mentor_membership_id) VALUES (%s, 'MENTORSHIP', %s, %s) RETURNING id", (str(uuid.uuid4()), m, ids["membership"]))
            append_event(cur, "core_membershipconditionstateevent", cur.fetchone()[0], 1, "OPEN", None, "ACTIVE", ids["identity"], ids["basis"], fresh())
        self.commits(lawful)


class FoundingCompleteness(_S015PositiveBase):
    NO_PLATFORM_ROOT_SCAFFOLD = frozenset({"test_pos_control_complete_non_root_founding"})

    def test_pos_control_complete_root_founding(self):
        ids = self.commits(lambda cur: found_root(cur))
        with connection.cursor() as cur:
            cur.execute("SELECT state, platform_root, founder_identity_id IS NOT NULL FROM core_livingorganism WHERE id = %s", (ids["lo"],))
            self.assertEqual(cur.fetchone(), ("ACTIVE", True, True))

    def test_pos_control_both_root_offices_active_at_founding_coordinates(self):
        ids = self.commits(
            lambda cur: found_root(
                cur,
                assignment_states={"INTEVIA_FOUNDER_STEWARD": "ACTIVE", "INTEVIA_PRIVACY_COORDINATOR": "ACTIVE"},
            )
        )
        self.assertEqual(
            {code: self.route("core_contextualroleassignment", anchor)[0] for code, anchor in ids["assignments"].items()},
            {"INTEVIA_FOUNDER_STEWARD": "ACTIVE", "INTEVIA_PRIVACY_COORDINATOR": "ACTIVE"},
        )

    def test_pos_control_complete_non_root_founding(self):
        ids = self.commits(lambda cur: found_nonroot(cur))
        self.assertEqual(self.route("core_livingorganism", ids["lo"])[0], "ACTIVE")

    def test_pos_control_superseded_version_inactive_beside_current_active(self):
        ids = self.commits(lambda cur: found_root(cur))
        def supersede(cur):
            cur.execute("UPDATE core_organismroledefinition SET active = FALSE WHERE id = %s", (ids["roles"]["COORDINATOR"],))
            cur.execute("INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, created_at, living_organism_id) VALUES (%s, 'COORDINATOR', 'LIVING_ORGANISM', 2, TRUE, now(), %s)", (str(uuid.uuid4()), ids["lo"]))
        self.commits(supersede)


class ReservedRoleCodes(_S015PositiveBase):
    def test_pos_control_root_defines_each_reserved_name_and_non_root_defines_custom_names(self):
        r = self.commits(lambda cur: found_root(cur))
        n = self.commits(lambda cur: found_nonroot(cur))
        for code in FIVE_RESERVED:
            with self.subTest(code=code, organism="root"):
                self.commits(lambda cur: cur.execute("INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, created_at, living_organism_id) VALUES (%s, %s, 'LIVING_ORGANISM', 7, false, now(), %s)", (str(uuid.uuid4()), code, r["lo"])))
        for code in ["COORDINATOR", "TREASURER", "LEAD_STEWARD", "COORDINATOR_FINANCE"]:
            with self.subTest(code=code, organism="non-root"):
                self.commits(lambda cur: cur.execute("INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, created_at, living_organism_id) VALUES (%s, %s, 'LIVING_ORGANISM', 7, false, now(), %s)", (str(uuid.uuid4()), code, n["lo"])))


class PlatformRoot(_S015PositiveBase):
    def test_pos_control_root_founding_with_unset_check_dropped(self):
        ids = self.commits(lambda cur: found_root(cur))
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM core_livingorganism WHERE platform_root"); self.assertEqual(cur.fetchone()[0], 1)


class RoleDefinitionImmutability(_S015PositiveBase):
    def test_pos_control_fresh_version_row_and_deactivation(self):
        ids = self.commits(lambda cur: found_root(cur))
        # documented: 0021 does not refuse UPDATE of `active` (FND-2 is PKT-B's)
        self.commits(lambda cur: cur.execute("UPDATE core_organismroledefinition SET active = false WHERE id = %s", (ids["roles"]["COORDINATOR"],)))
        self.commits(lambda cur: cur.execute("INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, created_at, living_organism_id) VALUES (%s, 'COORDINATOR', 'LIVING_ORGANISM', 2, true, now(), %s)", (str(uuid.uuid4()), ids["lo"])))


class LockOrder(_S015PositiveBase):
    NO_PLATFORM_ROOT_SCAFFOLD = frozenset({"test_pos_control_each_owning_organism_lock_attachment_registers"})

    def test_pos_control_canonical_order_and_helper_with_reversed_array(self):
        a = self.commits(lambda cur: found_root(cur))
        b = self.commits(lambda cur: found_nonroot(cur))
        with connection.cursor() as cur:
            cur.execute("SELECT id, organism_id FROM core_livingorganism WHERE id IN (%s, %s) ORDER BY organism_id", (a["lo"], b["lo"]))
            (lower_id, lower_uuid), (greater_id, greater_uuid) = cur.fetchall()
        def lawful(cur):
            cur.execute("SELECT public.s015_lock_organisms(ARRAY[%s::uuid, %s::uuid])", (str(greater_uuid), str(lower_uuid)))  # helper sorts
            for lo in (lower_id, greater_id):
                cur.execute("INSERT INTO core_essentialcoveragerequirement (requirement_uuid, roster_version, responsibility_domain_code, qualifying_role_code, minimum_active_occupants, scope, effective_from, living_organism_id) VALUES (%s, 2, 'X', 'COORDINATOR', 1, 'LIVING_ORGANISM', %s, %s)",
                            (str(uuid.uuid4()), T0, lo))
        self.commits(lawful)

    def test_pos_control_each_owning_organism_lock_attachment_registers(self):
        event_tables = (
            "core_organismmembershiptransition",
            "core_membershipconditionstateevent",
            "core_contextualroleassignmentstateevent",
            "core_circlestateevent",
            "core_coverageassessment",
            "core_restrictedcontinuityevent",
        )
        for event_table in event_tables:
            with self.subTest(table=event_table):
                ids = self.commits(lambda cur: add_remaining_aggregates(cur, found_nonroot(cur), fresh()))
                def append_and_check(cur):
                    append_second_event(cur, ids, event_table, fresh())
                    cur.execute(
                        "SELECT count(*) FROM public.s015_transaction_register "
                        "WHERE xid = pg_current_xact_id() AND kind = 'ORGANISM_LOCK'",
                    )
                    self.assertEqual(cur.fetchone()[0], 1)
                self.commits(append_and_check)

        ids = self.commits(lambda cur: found_nonroot(cur))
        def insert_requirement_and_check(cur):
            cur.execute(
                "INSERT INTO core_essentialcoveragerequirement "
                "(requirement_uuid, roster_version, responsibility_domain_code, qualifying_role_code, "
                "minimum_active_occupants, scope, effective_from, living_organism_id) "
                "VALUES (%s, 2, 'GOVERNANCE', 'COORDINATOR', 1, 'LIVING_ORGANISM', %s, %s)",
                (str(uuid.uuid4()), T0, ids["lo"]),
            )
            cur.execute(
                "SELECT count(*) FROM public.s015_transaction_register "
                "WHERE xid = pg_current_xact_id() AND kind = 'ORGANISM_LOCK'",
            )
            self.assertEqual(cur.fetchone()[0], 1)
        self.commits(insert_requirement_and_check)


class FixedVectors(_S015PositiveBase):
    """The eight published s015f3 fixed vectors — seven re-derived from the Phase 2 inputs (V1, V2, V3a, V3b, V3c, V4, V5)
    plus one additional obligation vector (V6) — in vectors/s015f3_fixed_vectors.json (repository-relative only).

    The oracle is the installed SQL: every published event row is carried as jsonb through s015_canonical_event_record,
    the records through s015_canonical_envelope and the envelope through s015_digest_envelope; each stage must equal the
    published preimage and digest. The installed catalogue column list of every one of the twelve event tables must equal
    the published canonical column list, so a migration that adds, drops or renames an event column fails here until the
    form is re-versioned. The set-level functions (eligibility, fold order, s015_event_set_preimage,
    s015_fingerprint_event_set) are exercised on live rows: Python applies eligibility and fold order to to_jsonb(row)
    of every row of the anchor and assembles the envelope from per-row installed records; SQL must agree."""

    FORM = "s015f3"
    FOLD = "effective_at, occurred_at, received_at, recorded_at, sequence, event_uuid"

    @staticmethod
    def corpus():
        import pathlib
        path = pathlib.Path(__file__).resolve().parents[1] / "vectors" / "s015f3_fixed_vectors.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _sql_record(self, cur, table, row):
        cur.execute("SELECT public.s015_canonical_event_record(%s::regclass, %s::jsonb)", (f"public.{table}", json.dumps(row, ensure_ascii=False)))
        return cur.fetchone()[0]

    def test_pos_control_installed_sql_reproduces_all_eight_vectors_seven_rederived_plus_one_additional(self):
        corpus = self.corpus()
        self.assertEqual(corpus["form_version"], self.FORM)
        vectors = corpus["vectors"]
        self.assertEqual(len(vectors), 8)
        self.assertEqual(sum(v["kind"] == "re-derived" for v in vectors), 7)
        self.assertEqual(sum(v["kind"] == "additional" for v in vectors), 1)
        with connection.cursor() as cur:
            for v in vectors:
                with self.subTest(vector=v["id"], kind=v["kind"]):
                    cur.execute("SELECT public.s015_canonical_columns(%s::regclass)", (f"public.{v['event_table']}",))
                    self.assertEqual(cur.fetchone()[0], [c["column"] for c in v["canonical_columns"]])
                    fold = sorted(v["events"], key=lambda e: (e["effective_at"], e["occurred_at"], e["received_at"], e["recorded_at"], e["sequence"], e["event_uuid"]))
                    records = [self._sql_record(cur, v["event_table"], e) for e in fold]
                    cur.execute("SELECT public.s015_canonical_envelope(%s, %s::uuid, %s::text[])", (v["anchor_token"], v["anchor_identity"], records))
                    preimage = cur.fetchone()[0]
                    cur.execute("SELECT public.s015_digest_envelope(%s), length(public.s015_digest_envelope(%s))", (preimage, preimage))
                    digest, width = cur.fetchone()
                    self.assertEqual(preimage, v["preimage"])
                    self.assertEqual(len(preimage.encode("utf-8")), v["preimage_bytes"])
                    self.assertEqual(digest, v["digest"]); self.assertEqual(width, 71)
                    self.assertEqual("s015f3:" + hashlib.sha256(preimage.encode("utf-8")).hexdigest(), v["digest"])

    def test_pos_control_installed_inventories_equal_published_for_all_twelve_event_tables(self):
        corpus = self.corpus()
        published = corpus["inventories"]
        self.assertEqual(len(published), 12)
        self.assertEqual(sum(t["column_count"] for t in published.values()), corpus["inventory_total_columns"])
        with connection.cursor() as cur:
            cur.execute("SELECT event_table FROM public.s015_anchor_map() ORDER BY 1")
            tables = [r[0] for r in cur.fetchall()]
            self.assertEqual(sorted(tables), sorted(published))
            for table in tables:
                with self.subTest(table=table):
                    cur.execute("SELECT public.s015_canonical_columns(%s::regclass)", (f"public.{table}",))
                    installed = cur.fetchone()[0]
                    self.assertEqual(installed, sorted(installed, key=lambda s: s.encode("utf-8")))
                    self.assertEqual(installed, [c["column"] for c in published[table]["canonical_columns"]])
                    self.assertEqual(len(installed), published[table]["column_count"])

    def test_pos_control_event_set_preimage_matches_row_reserialisation_on_live_rows(self):
        ids = self.commits(lambda cur: found_root(cur))
        e2 = self.commits(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "RESTRICT", "ACTIVE", "RESTRICTED_CONTINUITY", ids["identity"], ids["basis"], fresh(), pred_id=ids["lo_event"], eff=T0 + dt.timedelta(days=30), occ=T0, rec=T0))[0]
        far = T0 + dt.timedelta(days=60)
        with connection.cursor() as cur:
            cur.execute("SELECT organism_id FROM core_livingorganism WHERE id = %s", (ids["lo"],)); identity = str(cur.fetchone()[0])
            cur.execute("SELECT to_jsonb(t)::text, recorded_at FROM core_livingorganismevent t WHERE living_organism_id = %s ORDER BY id", (ids["lo"],))
            rows = [(json.loads(r[0]), r[1]) for r in cur.fetchall()]
            self.assertEqual(len(rows), 2)
            e2_recorded = [rec for row, rec in rows if row["id"] == e2][0]
            for state_at, known_at, expect_n in ((T0, far, 1), (far, far, 2), (far, e2_recorded - dt.timedelta(microseconds=1), 1)):
                with self.subTest(state_at=state_at, known_at=known_at):
                    def elig(row):
                        eff, rec, recd = (dt.datetime.fromisoformat(row[k]) for k in ("effective_at", "received_at", "recorded_at"))
                        return eff <= state_at and rec <= known_at and recd <= known_at
                    chosen = sorted((row for row, _ in rows if elig(row)),
                                    key=lambda r: (dt.datetime.fromisoformat(r["effective_at"]), dt.datetime.fromisoformat(r["occurred_at"]), dt.datetime.fromisoformat(r["received_at"]), dt.datetime.fromisoformat(r["recorded_at"]), r["sequence"], r["event_uuid"]))
                    self.assertEqual(len(chosen), expect_n)
                    expected = f'["{self.FORM}","core_livingorganism","{identity}",[' + ",".join(self._sql_record(cur, "core_livingorganismevent", r) for r in chosen) + "]]"
                    cur.execute("SELECT public.s015_event_set_preimage('core_livingorganism', %s, %s, %s), public.s015_fingerprint_event_set('core_livingorganism', %s, %s, %s)",
                                (ids["lo"], state_at, known_at, ids["lo"], state_at, known_at))
                    preimage, fp = cur.fetchone()
                    self.assertEqual(preimage, expected)
                    self.assertEqual(fp, "s015f3:" + hashlib.sha256(expected.encode("utf-8")).hexdigest())
                    for r in chosen:
                        self.assertIn(f'"evidence_reference":{json.dumps(r["evidence_reference"], ensure_ascii=False)}', preimage)

    def test_pos_control_record_refuses_missing_column_unknown_key_and_unruled_type(self):
        with connection.cursor() as cur:
            cur.execute("SELECT public.s015_canonical_columns('public.core_livingorganismevent'::regclass)"); cols = cur.fetchone()[0]
            full = {c: None for c in cols}
            self.assertEqual(self._sql_record(cur, "core_livingorganismevent", full), "{" + ",".join(f'"{c}":null' for c in cols) + "}")
        for bad, needle in (({c: None for c in cols if c != "evidence_reference"}, "lacks column"), ({**full, "bogus": 1}, "unknown key")):
            with self.subTest(needle=needle), connection.cursor() as cur, self.assertRaises(Exception) as ctx:
                self._sql_record(cur, "core_livingorganismevent", bad)
            self.assertIn(needle, str(ctx.exception))
        for typ in ("boolean", "jsonb"):
            with self.subTest(type=typ), connection.cursor() as cur, self.assertRaises(Exception) as ctx:
                cur.execute("SELECT public.s015_canonical_value(%s::regtype, 'true'::jsonb)", (typ,))
            self.assertIn("no rule for type", str(ctx.exception))
