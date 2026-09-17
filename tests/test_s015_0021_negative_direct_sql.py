"""S015 migration 0021 — negative direct-SQL tests (U21g Phase 2).

Every test issues raw SQL that the specification v0.8 requires the database to refuse, and asserts the refusal — at the
statement or at commit. Each class's positive control lives in tests/test_s015_0021_positive_controls.py under the same
class name. Node identity: tests/test_s015_0021_negative_direct_sql.py::<Class>::<test>.
"""
import datetime as dt
import uuid
from unittest import skipUnless

from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase

from s015_0021_support import (
    FIVE_RESERVED, SIX_CODES, T0, add_remaining_aggregates, append_event, append_second_event, contract_columns,
    drop_unset_check, empty_fp, empty_parts_commitment, first_line, found_nonroot, found_root, fresh, predecessor_l1,
    refresh, restore_unset_check, seed_authority, seed_identity, u,
)

POSTGRESQL_ONLY = skipUnless(connection.vendor == "postgresql", "S015 0021 guardians are PostgreSQL triggers")
EVENT_NAME_TOKENS = {
    "core_livingorganismevent": "loevent",
    "core_circlestateevent": "circleevent",
    "core_organismmembershiptransition": "memtransition",
    "core_contextualroleassignmentstateevent": "craevent",
    "core_membershipconditionstateevent": "mcevent",
    "core_authorityinvalidationevent": "aievent",
    "core_determinationcontestevent": "dcevent",
    "core_coverageassessment": "assessment",
    "core_restrictedcontinuityevent": "rcevent",
    "core_visibilitygrantstateevent": "vgevent",
    "core_obligationstateevent": "obevent",
    "core_planningclassificationevent": "pcevent",
}


@POSTGRESQL_ONLY
class _S015NegativeBase(TransactionTestCase):
    NO_PLATFORM_ROOT_SCAFFOLD = frozenset()

    def setUp(self):
        super().setUp()
        self._uses_platform_root_scaffold = self._testMethodName not in self.NO_PLATFORM_ROOT_SCAFFOLD
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

    def refuses(self, fn, *, contains=None):
        """Run fn(cur) inside an outermost atomic block; the block must fail (statement or commit)."""
        with self.assertRaises(DatabaseError) as ctx:
            with transaction.atomic():
                with connection.cursor() as cur:
                    fn(cur)
        message = first_line(ctx.exception)
        if contains:
            self.assertIn(contains, message, message)
        return message

    def commits(self, fn):
        with transaction.atomic():
            with connection.cursor() as cur:
                return fn(cur)

    def root(self, cur, **kw):
        ids = found_root(cur, **kw)
        return ids


# ------------------------------------------------------------------ §6.1 item 1 — append-only refusal
class AppendOnly(_S015NegativeBase):
    def test_neg_update_event_same_value(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: cur.execute("UPDATE core_livingorganismevent SET action = action WHERE id = %s", (ids["lo_event"],)),
                     contains="append-only row mutation refused")

    def test_neg_update_event_state(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: cur.execute("UPDATE core_livingorganismevent SET resulting_state = 'CLOSED' WHERE id = %s", (ids["lo_event"],)),
                     contains="append-only row mutation refused")

    def test_neg_delete_event(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: cur.execute("DELETE FROM core_livingorganismevent WHERE id = %s", (ids["lo_event"],)),
                     contains="append-only row mutation refused")

    def test_neg_truncate_event_table(self):
        self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: cur.execute("TRUNCATE core_livingorganismevent CASCADE"), contains="append-only row mutation refused")

    def test_neg_set_constraints_deferred_does_not_lift_append_only(self):
        ids = self.commits(lambda cur: self.root(cur))
        def attempt(cur):
            cur.execute("SET CONSTRAINTS ALL DEFERRED")
            cur.execute("DELETE FROM core_livingorganismevent WHERE id = %s", (ids["lo_event"],))
        self.refuses(attempt, contains="append-only row mutation refused")

    def test_neg_update_requirement_version(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: cur.execute("UPDATE core_essentialcoveragerequirement SET minimum_active_occupants = 2 WHERE id = %s", (ids["requirement"],)),
                     contains="append-only row mutation refused")

    def test_neg_each_event_table_append_only_attachment_by_operation(self):
        ids = self.commits(lambda cur: add_remaining_aggregates(cur, self.root(cur), fresh()))
        self.assertEqual(len(ids["events"]), 12)
        for event_table, event_id in ids["events"].items():
            operations = (
                ("update", f"UPDATE public.{event_table} SET action = action WHERE id = %s", (event_id,)),
                ("delete", f"DELETE FROM public.{event_table} WHERE id = %s", (event_id,)),
                ("truncate", f"TRUNCATE public.{event_table} CASCADE", ()),
            )
            for operation, sql, params in operations:
                with self.subTest(event_table=event_table, operation=operation):
                    self.refuses(
                        lambda cur, sql=sql, params=params: cur.execute(sql, params),
                        contains="S015 immutable or append-only row mutation refused",
                    )


# ------------------------------------------------------------------ §5.1 / §6.1 item 2 — the recorded-chain closure contract
class ChainClosure(_S015NegativeBase):
    def _ids(self):
        return self.commits(lambda cur: self.root(cur))

    def test_neg_orphan_sequence_two_without_predecessor(self):
        ids = self._ids()
        self.refuses(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "X", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(),
                                              pred_id=None, overrides={"predecessor_sequence": None}), contains="adjacency_ck")

    def test_neg_fork_second_successor(self):
        ids = self._ids()
        self.commits(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "X", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(), pred_id=ids["lo_event"]))
        self.refuses(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 3, "Y", "DORMANT", "ACTIVE", ids["identity"], ids["basis"], fresh(),
                                              pred_id=ids["lo_event"], overrides={"predecessor_sequence": 2}), contains="pred_uniq")

    def test_neg_skip_sequence(self):
        ids = self._ids()
        self.refuses(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 3, "X", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(),
                                              pred_id=ids["lo_event"]), contains="append order")

    def test_neg_predecessor_of_another_anchor(self):
        ids = self._ids()
        def case(cur):
            cur.execute("INSERT INTO core_planningclassificationcase (case_uuid, item_identifier) VALUES (%s, %s) RETURNING id", (str(uuid.uuid4()), f"u21g:{fresh()}"))
            c = cur.fetchone()[0]
            e = append_event(cur, "core_planningclassificationevent", c, 1, "CLASSIFY", None, "ROUTINE_ELIGIBLE", ids["identity"], ids["basis"], fresh(), extra={"criterion_reference": "c"})[0]
            return c, e
        case_a, event_a = self.commits(case)
        case_b, _event_b = self.commits(case)
        # sequence 2 on case B naming case A's event as predecessor: same-aggregate parentage is declarative (composite FK)
        self.refuses(lambda cur: append_event(cur, "core_planningclassificationevent", case_b, 2, "RECLASSIFY", "ROUTINE_ELIGIBLE", "UNKNOWN", ids["identity"], ids["basis"], fresh(),
                                              pred_id=event_a, extra={"criterion_reference": "c"}), contains="predecessor_fk")

    def test_neg_head_rewind(self):
        ids = self._ids()
        e2 = self.commits(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "X", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(), pred_id=ids["lo_event"]))[0]
        self.refuses(lambda cur: cur.execute("UPDATE core_livingorganism SET head_event_id = %s WHERE id = %s", (ids["lo_event"], ids["lo"])),
                     contains="head pointer")

    def test_neg_head_null_while_events_exist(self):
        ids = self._ids()
        self.refuses(lambda cur: cur.execute("UPDATE core_livingorganism SET head_event_id = NULL WHERE id = %s", (ids["lo"],)), contains="head pointer")

    def test_neg_anchor_without_sequence_one_at_commit(self):
        ids = self._ids()
        def attempt(cur):
            cur.execute("INSERT INTO core_planningclassificationcase (case_uuid, item_identifier) VALUES (%s, %s)", (str(uuid.uuid4()), f"u21g:{fresh()}"))
        self.refuses(attempt, contains="no sequence-1 event at commit")

    def test_neg_sequence_one_with_prior_state(self):
        ids = self._ids()
        def attempt(cur):
            cur.execute("INSERT INTO core_planningclassificationcase (case_uuid, item_identifier) VALUES (%s, %s) RETURNING id", (str(uuid.uuid4()), f"u21g:{fresh()}"))
            case = cur.fetchone()[0]
            append_event(cur, "core_planningclassificationevent", case, 1, "CLASSIFY", "UNKNOWN", "ROUTINE_ELIGIBLE", ids["identity"], ids["basis"], fresh(), extra={"criterion_reference": "c"})
        self.refuses(attempt, contains="prior_seq1_ck")


# ------------------------------------------------------------------ §5.3 — the four-time grammar
class FourTimeGrammar(_S015NegativeBase):
    def test_neg_caller_supplied_recorded_at(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "X", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(),
                                              pred_id=ids["lo_event"], recorded_at=T0), contains="recorded_at is database-assigned")

    def test_neg_occurred_after_received(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "X", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(),
                                              pred_id=ids["lo_event"], occ=T0 + dt.timedelta(seconds=1)), contains="four_time_ck")

    def test_neg_received_in_the_future(self):
        ids = self.commits(lambda cur: self.root(cur))
        future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
        self.refuses(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "X", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(),
                                              pred_id=ids["lo_event"], occ=future, rec=future, eff=future), contains="four_time_ck")

    def test_neg_timestamp_outside_domain(self):
        ids = self.commits(lambda cur: self.root(cur))
        def attempt(cur):
            event_uuid = str(uuid.uuid4())
            contract = contract_columns(ids["identity"])
            contract["parts_commitment"] = empty_parts_commitment("core_livingorganismevent", event_uuid)
            contract["predecessor_l1_commitment"] = predecessor_l1(cur, "core_livingorganismevent", {"sequence": 2, "predecessor_id": ids["lo_event"]})
            names = ", ".join(contract)
            cur.execute("INSERT INTO core_livingorganismevent (event_uuid, living_organism_id, sequence, predecessor_id, predecessor_sequence, action, prior_state, resulting_state, actor_id, actor_access_epoch, authority_basis_id, authority_decision_reference, evidence_reference, request_reference, idempotency_key, payload_fingerprint, lineage_reference, occurred_at, effective_at, received_at, " + names + ") "
                        "VALUES (%s, %s, 2, %s, 1, 'X', 'ACTIVE', 'DORMANT', %s, 0, %s, %s, 'e', 'r', %s, %s, %s, '-infinity', %s, %s, " + ", ".join(["%s"] * len(contract)) + ")",
                        (str(event_uuid), ids["lo"], ids["lo_event"], ids["identity"], ids["basis"], "s015d1:" + "a" * 64, fresh(), "b" * 64, "s015l1:" + uuid.uuid4().hex + uuid.uuid4().hex, T0, T0, *contract.values()))
        self.refuses(attempt, contains="domain_ck")

    def test_neg_temporal_basis_missing_when_retrospective(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "X", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(),
                                              pred_id=ids["lo_event"], eff=T0 - dt.timedelta(hours=1), overrides={"temporal_basis_kind": None, "temporal_basis_reference": None}),
                     contains="temporal_basis_ck")

    def test_neg_temporal_basis_polarity_mismatch(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "X", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(),
                                              pred_id=ids["lo_event"], eff=T0 - dt.timedelta(hours=1), overrides={"temporal_basis_kind": "PROSPECTIVE"}),
                     contains="temporal_basis_ck")

    def test_neg_temporal_basis_reference_blank(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "X", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(),
                                              pred_id=ids["lo_event"], eff=T0 - dt.timedelta(hours=1), overrides={"temporal_basis_reference": " padded "}),
                     contains="canonical_ck")

    def test_neg_temporal_basis_null_kind_with_reference_on_all_twelve_tables(self):
        ids = self.commits(lambda cur: add_remaining_aggregates(cur, self.root(cur), fresh()))
        self.assertEqual(len(ids["events"]), 12)
        for event_table, name_token in EVENT_NAME_TOKENS.items():
            with self.subTest(event_table=event_table):
                self.refuses(
                    lambda cur, event_table=event_table: append_second_event(
                        cur,
                        ids,
                        event_table,
                        fresh(),
                        prior=None,
                        eff=T0 - dt.timedelta(hours=1),
                        overrides={
                            "temporal_basis_kind": None,
                            "temporal_basis_reference": "u21h:retrospective-basis",
                        },
                    ),
                    contains=f"s015_0021_{name_token}_temporal_basis_ck",
                )


# ------------------------------------------------------------------ §5.5, §6.1 items 4–7 — vocabularies, exclusions and shapes
class VocabulariesAndShapes(_S015NegativeBase):
    def test_neg_circle_active_on_anchor(self):
        ids = self.commits(lambda cur: self.root(cur))
        def attempt(cur):
            c = str(uuid.uuid4())
            cur.execute("INSERT INTO core_circle (circle_uuid, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, founding_reference, parent_organism_id) VALUES (%s, 'ACTIVE', now(), %s, %s, %s, 'f', %s)",
                        (c, T0, T0, empty_fp(cur, "core_circle", c), ids["lo"]))
        self.refuses(attempt, contains="circle_state_vocab_ck")

    def test_neg_circle_active_on_event(self):
        ids = self.commits(lambda cur: add_remaining_aggregates(cur, self.root(cur), fresh()))
        head = self._circle_head(ids)
        self.refuses(lambda cur: append_event(cur, "core_circlestateevent", ids["circle"], 2, "X", "DORMANT", "ACTIVE", ids["identity"], ids["basis"], fresh(), pred_id=head),
                     contains="s015_0021_circleevent_resulting_state_vocab_ck")  # FU-B3 (Change C; A1 O-2): the circle vocabulary CHECK itself

    def _circle_head(self, ids):
        with connection.cursor() as cur:
            cur.execute("SELECT head_event_id FROM core_circle WHERE id = %s", (ids["circle"],))
            return cur.fetchone()[0]

    def test_neg_assignment_seventh_token(self):
        ids = self.commits(lambda cur: self.root(cur))
        a = ids["assignments"]["INTEVIA_FOUNDER_STEWARD"]
        with connection.cursor() as cur:
            cur.execute("SELECT head_state_event_id FROM core_contextualroleassignment WHERE id = %s", (a,)); head = cur.fetchone()[0]
        self.refuses(lambda cur: append_event(cur, "core_contextualroleassignmentstateevent", a, 2, "SUSPEND", "ACTIVE", "RESTRICTED", ids["identity"], ids["basis"], fresh(), pred_id=head),
                     contains="vocab_ck")

    def test_neg_assignment_action_outside_six(self):
        ids = self.commits(lambda cur: self.root(cur))
        a = ids["assignments"]["INTEVIA_FOUNDER_STEWARD"]
        with connection.cursor() as cur:
            cur.execute("SELECT head_state_event_id FROM core_contextualroleassignment WHERE id = %s", (a,)); head = cur.fetchone()[0]
        self.refuses(lambda cur: append_event(cur, "core_contextualroleassignmentstateevent", a, 2, "PROMOTE", "ACTIVE", "SUSPENDED", ids["identity"], ids["basis"], fresh(), pred_id=head),
                     contains="cra_action_ck")

    def test_neg_planning_result_fourth_token(self):
        ids = self.commits(lambda cur: self.root(cur))
        def attempt(cur):
            cur.execute("INSERT INTO core_planningclassificationcase (case_uuid, item_identifier) VALUES (%s, %s) RETURNING id", (str(uuid.uuid4()), f"u21g:{fresh()}"))
            append_event(cur, "core_planningclassificationevent", cur.fetchone()[0], 1, "CLASSIFY", None, "NOT_A_RESULT", ids["identity"], ids["basis"], fresh(), extra={"criterion_reference": "c"})
        self.refuses(attempt, contains="result_vocab_ck")

    def test_neg_fingerprint_old_shape_on_anchor(self):
        ids = self.commits(lambda cur: self.root(cur))
        def attempt(cur):
            cur.execute("INSERT INTO core_circle (circle_uuid, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, founding_reference, parent_organism_id) VALUES (%s, 'DORMANT', now(), %s, %s, %s, 'f', %s)",
                        (str(uuid.uuid4()), T0, T0, "0" * 64, ids["lo"]))
        self.refuses(attempt, contains="insert-time fingerprint")

    def test_neg_decision_reference_wrong_prefix(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "X", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(),
                                              pred_id=ids["lo_event"], overrides={"authority_decision_reference": "s012d1:" + "a" * 64}), contains="decision_shape_ck")

    def test_neg_reference_not_canonical(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "X", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(),
                                              pred_id=ids["lo_event"], overrides={"evidence_reference": "  "}), contains="canonical_ck")

    def test_neg_role_scope_third_token(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: cur.execute("INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, created_at, living_organism_id) VALUES (%s, 'TREASURER', 'NOT_A_SCOPE', 1, true, now(), %s)",
                                             (str(uuid.uuid4()), ids["lo"])), contains="role_scope_domain_ck")


# ------------------------------------------------------------------ §6.1 items 19, 20 — the cache boundary and requalification
class CacheBoundary(_S015NegativeBase):
    NO_PLATFORM_ROOT_SCAFFOLD = frozenset({"test_neg_non_owner_cannot_write_register"})

    def test_neg_raw_update_of_cache_value_same_value(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: cur.execute("UPDATE core_livingorganism SET state = 'ACTIVE' WHERE id = %s", (ids["lo"],)), contains="database-computed")

    def test_neg_raw_update_of_fingerprint(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: cur.execute("UPDATE core_livingorganism SET state_source_event_set_fingerprint = state_source_event_set_fingerprint WHERE id = %s", (ids["lo"],)), contains="database-computed")

    def test_neg_raw_coordinate_update_without_ticket(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: cur.execute("UPDATE core_livingorganism SET state_known_at = clock_timestamp() WHERE id = %s", (ids["lo"],)), contains="no refresh ticket")

    def test_neg_refresh_known_at_in_future(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(
            lambda cur: cur.execute("SELECT public.s015_refresh_governed_cache('core_livingorganism', %s, %s, clock_timestamp() + interval '1 day')", (ids["lo"], T0)),
            contains="coordinates must satisfy state_at <= known_at <= now",
        )

    def test_neg_insert_with_non_empty_set_fingerprint(self):
        ids = self.commits(lambda cur: self.root(cur))
        def attempt(cur):
            cur.execute("INSERT INTO core_circle (circle_uuid, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, founding_reference, parent_organism_id) VALUES (%s, 'DORMANT', now(), %s, %s, %s, 'f', %s)",
                        (str(uuid.uuid4()), T0, T0, "s015f4:" + "0" * 64, ids["lo"]))
        self.refuses(attempt, contains="insert-time fingerprint")

    def test_neg_placeholder_never_refreshed_refused_at_commit(self):
        # insert-time placeholder is never the fold; without the refresh the anchor cannot commit (predicate 4)
        self.refuses(lambda cur: found_root(cur, do_refresh=False), contains="cache does not equal the fold")

    def test_neg_obligation_raw_value_update(self):
        ids = self.commits(lambda cur: add_remaining_aggregates(cur, self.root(cur), fresh()))
        self.refuses(lambda cur: cur.execute("UPDATE core_governedobligationcase SET qualified_legal_deadline = now() WHERE id = %s", (ids["obligation_case"],)), contains="database-computed")

    def test_neg_read_route_never_serves_cache_at_other_coordinates(self):
        ids = self.commits(lambda cur: self.root(cur))
        with connection.cursor() as cur:
            cur.execute("SELECT served_from FROM public.s015_effective_state('core_livingorganism', %s, %s, %s)", (ids["lo"], T0 + dt.timedelta(days=1), dt.datetime.now(dt.timezone.utc)))
            self.assertEqual(cur.fetchone()[0], "FOLD")

    def test_neg_non_owner_cannot_write_register(self):
        with connection.cursor() as cur:
            cur.execute("SELECT has_table_privilege('public', 'public.s015_transaction_register', 'INSERT')")
            self.assertFalse(cur.fetchone()[0])


# ------------------------------------------------------------------ §5.3 / U21g-13 — prior_state at record time
class PriorState(_S015NegativeBase):
    def test_neg_prior_state_not_the_effective_order_predecessor(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "X", "CLOSED", "DORMANT", ids["identity"], ids["basis"], fresh(), pred_id=ids["lo_event"]),
                     contains="prior_state")

    def test_neg_prior_state_non_null_at_empty_predecessor_set(self):
        # a sequence-2 event effective before sequence 1 has an empty record-time predecessor set → prior_state must be NULL
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: append_event(cur, "core_livingorganismevent", ids["lo"], 2, "X", "ACTIVE", "DORMANT", ids["identity"], ids["basis"], fresh(),
                                              pred_id=ids["lo_event"], eff=T0 - dt.timedelta(days=1)), contains="should be NULL")


# ------------------------------------------------------------------ §6.1 item 8 — structural guardians
class Structure(_S015NegativeBase):
    def _two(self):
        a = self.commits(lambda cur: self.root(cur))
        b = self.commits(lambda cur: found_nonroot(cur))
        return a, b

    def _assignment(self, cur, membership, role, circle=None):
        au = str(uuid.uuid4())
        cur.execute("INSERT INTO core_contextualroleassignment (assignment_uuid, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, membership_id, role_definition_id, circle_id) VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s, %s) RETURNING id",
                    (au, T0, T0, empty_fp(cur, "core_contextualroleassignment", au), membership, role, circle))
        return cur.fetchone()[0]

    def test_neg_membership_and_role_in_different_organisms(self):
        a, b = self._two()
        def attempt(cur):
            aid = self._assignment(cur, a["membership"], b["roles"]["COORDINATOR"])
            append_event(cur, "core_contextualroleassignmentstateevent", aid, 1, "ASSIGN", None, "ACTIVE", a["identity"], a["basis"], fresh()); refresh(cur, "core_contextualroleassignment", aid)
        self.refuses(attempt, contains="assignment structure")

    def test_neg_scope_circle_mismatch(self):
        a = self.commits(lambda cur: add_remaining_aggregates(cur, self.root(cur), fresh()))
        def attempt(cur):
            aid = self._assignment(cur, a["membership"], a["roles"]["COORDINATOR"], circle=a["circle"])  # LIVING_ORGANISM scope with a circle
            append_event(cur, "core_contextualroleassignmentstateevent", aid, 1, "ASSIGN", None, "ACTIVE", a["identity"], a["basis"], fresh()); refresh(cur, "core_contextualroleassignment", aid)
        self.refuses(attempt, contains="role scope")

    def test_neg_founder_steward_for_non_founder(self):
        a = self.commits(lambda cur: self.root(cur))
        def attempt(cur):
            other = seed_identity(cur, f"other-{fresh()}")
            mu = str(uuid.uuid4())
            cur.execute("INSERT INTO core_organismmembership (membership_uuid, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, identity_id, living_organism_id) VALUES (%s, 'ACTIVE', now(), %s, %s, %s, %s, %s) RETURNING id",
                        (mu, T0, T0, empty_fp(cur, "core_organismmembership", mu), other, a["lo"]))
            m = cur.fetchone()[0]
            append_event(cur, "core_organismmembershiptransition", m, 1, "JOIN", None, "ACTIVE", a["identity"], a["basis"], fresh(), extra={"reason_class": "X"}); refresh(cur, "core_organismmembership", m)
            aid = self._assignment(cur, m, a["roles"]["INTEVIA_FOUNDER_STEWARD"])
            append_event(cur, "core_contextualroleassignmentstateevent", aid, 1, "ASSIGN", None, "ACTIVE", a["identity"], a["basis"], fresh()); refresh(cur, "core_contextualroleassignment", aid)
        self.refuses(attempt, contains="INTEVIA_FOUNDER_STEWARD anchor must be held by the founder")

    def test_neg_self_mentor(self):
        a = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: cur.execute("INSERT INTO core_membershipcondition (condition_uuid, condition_kind, subject_membership_id, mentor_membership_id) VALUES (%s, 'MENTORSHIP', %s, %s)", (str(uuid.uuid4()), a["membership"], a["membership"])),
                     contains="mentor_distinct_ck")

    def test_neg_mentorship_without_mentor(self):
        a = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: cur.execute("INSERT INTO core_membershipcondition (condition_uuid, condition_kind, subject_membership_id) VALUES (%s, 'MENTORSHIP', %s)", (str(uuid.uuid4()), a["membership"])),
                     contains="mentorship_requires_mentor_ck")

    def test_neg_mentor_in_other_organism(self):
        a, b = self._two()
        def attempt(cur):
            cur.execute("INSERT INTO core_membershipcondition (condition_uuid, condition_kind, subject_membership_id, mentor_membership_id) VALUES (%s, 'MENTORSHIP', %s, %s) RETURNING id", (str(uuid.uuid4()), a["membership"], b["membership"]))
            append_event(cur, "core_membershipconditionstateevent", cur.fetchone()[0], 1, "OPEN", None, "ACTIVE", a["identity"], a["basis"], fresh())
        self.refuses(attempt, contains="condition structure")

    def test_neg_assessment_declares_absent_roster(self):
        a = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: append_event(cur, "core_coverageassessment", a["coverage_case"], 2, "ASSESS", "SATISFIED", "SATISFIED", a["identity"], a["basis"], fresh(), pred_id=a["assessment"],
                                              extra={"roster_version": 9, "evaluated_state_at": T0, "evaluated_known_at": T0, "determiner_capacity_reference": "c"}), contains="roster version 9")


# ------------------------------------------------------------------ §6.1 item 3 — founding completeness, both branches
class FoundingCompleteness(_S015NegativeBase):
    NO_PLATFORM_ROOT_SCAFFOLD = frozenset({
        "test_neg_nonroot_limb8_no_coordinator",
        "test_neg_nonroot_limb8_inactive_coordinator",
        "test_neg_nonroot_duplicate_active_versions",
        "test_neg_nonroot_stale_active_version",
        "test_neg_nonroot_limb5_no_requirement",
    })

    def test_neg_root_limb1_no_sequence_one_event(self):
        self.refuses(
            lambda cur: found_root(cur, skip={"lo_event"}, skip_refresh={"core_livingorganism"}),
            contains="no sequence-1 event at commit",
        )

    def test_neg_root_limb2_completion_omitted(self):
        self.refuses(lambda cur: found_root(cur, skip={"completion"}), contains="founding limb 2")

    def test_neg_root_limb3_founder_membership_chain_empty(self):
        self.refuses(
            lambda cur: found_root(cur, skip={"membership_event"}, skip_refresh={"core_organismmembership"}),
            contains="founder membership with a non-empty chain required",
        )

    def test_neg_root_limb4_no_founder_steward_assignment(self):
        self.refuses(
            lambda cur: found_root(cur, assign_founder=False),
            contains="S015 founding completion update refused",
        )

    def test_neg_root_limb4_founder_steward_not_active_at_founding_coordinates(self):
        for state in ("SUSPENDED", "ENDED", "PROPOSED"):
            with self.subTest(state=state):
                self.refuses(
                    lambda cur: found_root(cur, assignment_states={"INTEVIA_FOUNDER_STEWARD": state}),
                    contains="S015 founding completion update refused",
                )

    def test_neg_root_limb5_no_requirement(self):
        self.refuses(lambda cur: found_root(cur, skip={"requirement"}), contains="founding limb 5")

    def test_neg_root_limb6_no_assessment(self):
        self.refuses(lambda cur: found_root(cur, skip={"assessment"}), contains="limb 6")

    def test_neg_root_limb7_no_privacy_coordinator_assignment(self):
        self.refuses(lambda cur: found_root(cur, skip={"pc_assignment"}), contains="limb 7")

    def test_neg_root_limb7_privacy_coordinator_not_active_at_founding_coordinates(self):
        for state in ("SUSPENDED", "ENDED", "PROPOSED"):
            with self.subTest(state=state):
                self.refuses(
                    lambda cur: found_root(cur, assignment_states={"INTEVIA_PRIVACY_COORDINATOR": state}),
                    contains="limb 7",
                )

    def test_neg_root_limb8_five_of_six_codes(self):
        self.refuses(lambda cur: found_root(cur, codes=SIX_CODES[:5]), contains="limb 8")

    def test_neg_root_limb8_inactive_code(self):
        inv = {c: [(1, True)] for c in SIX_CODES}; inv["INTEVIA_STEWARD"] = [(1, False)]
        self.refuses(lambda cur: found_root(cur, active_versions=inv), contains="exactly one active INTEVIA_STEWARD")

    def test_neg_root_duplicate_active_versions(self):
        inv = {c: [(1, True)] for c in SIX_CODES}; inv["COORDINATOR"] = [(1, True), (2, True)]
        self.refuses(lambda cur: found_root(cur, active_versions=inv), contains="s015_0021_role_active_uniq")

    def test_neg_root_stale_active_version(self):
        inv = {c: [(1, True)] for c in SIX_CODES}; inv["COORDINATOR"] = [(1, True), (2, False)]
        self.refuses(lambda cur: found_root(cur, active_versions=inv), contains="not the current version")

    def test_neg_nonroot_limb8_no_coordinator(self):
        self.refuses(lambda cur: found_nonroot(cur, codes=[]), contains="exactly one active COORDINATOR")

    def test_neg_nonroot_limb8_inactive_coordinator(self):
        self.refuses(lambda cur: found_nonroot(cur, active_versions={"COORDINATOR": [(1, False)]}), contains="exactly one active COORDINATOR")

    def test_neg_nonroot_duplicate_active_versions(self):
        self.refuses(
            lambda cur: found_nonroot(cur, active_versions={"COORDINATOR": [(1, True), (2, True)]}),
            contains="s015_0021_role_active_uniq",
        )

    def test_neg_nonroot_stale_active_version(self):
        self.refuses(
            lambda cur: found_nonroot(cur, active_versions={"COORDINATOR": [(1, True), (2, False)]}),
            contains="not the current version",
        )

    def test_neg_nonroot_limb5_no_requirement(self):
        self.refuses(lambda cur: found_nonroot(cur, skip={"requirement"}), contains="limb 5")

    def test_neg_zero_rows_after_refused_founding(self):
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM core_livingorganism"); before = cur.fetchone()[0]
        self.refuses(lambda cur: found_root(cur, skip={"assessment"}), contains="founding limb 6")
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM core_livingorganism"); self.assertEqual(cur.fetchone()[0], before)
            cur.execute("SELECT count(*) FROM core_organismroledefinition"); self.assertEqual(cur.fetchone()[0], 0)


# ------------------------------------------------------------------ §5.5.1 — the five reserved names
class ReservedRoleCodes(_S015NegativeBase):
    NO_PLATFORM_ROOT_SCAFFOLD = frozenset({
        "test_neg_each_reserved_name_on_non_root",
        "test_neg_non_root_founding_with_founder_steward_defined",
    })

    def test_neg_each_reserved_name_on_non_root(self):
        b = self.commits(lambda cur: found_nonroot(cur))
        for code in FIVE_RESERVED:
            with self.subTest(code=code):
                self.refuses(lambda cur: cur.execute("INSERT INTO core_organismroledefinition (role_uuid, code, scope, definition_version, active, created_at, living_organism_id) VALUES (%s, %s, 'LIVING_ORGANISM', 7, false, now(), %s)",
                                                     (str(uuid.uuid4()), code, b["lo"])), contains="may be defined only by the platform root")

    def test_neg_non_root_founding_with_founder_steward_defined(self):
        from s015_0021_support import found_organism
        self.refuses(
            lambda cur: found_organism(
                cur,
                f"reserved-{fresh()}",
                root=False,
                codes=["INTEVIA_FOUNDER_STEWARD", "COORDINATOR"],
                assign_founder=True,
            ),
            contains="may be defined only by the platform root",
        )
        ids = self.commits(lambda cur: found_nonroot(cur))
        with connection.cursor() as cur:
            cur.execute("SELECT state, platform_root FROM core_livingorganism WHERE id = %s", (ids["lo"],))
            self.assertEqual(cur.fetchone(), ("ACTIVE", False))


# ------------------------------------------------------------------ §4.16 — the platform_root discriminator
class PlatformRoot(_S015NegativeBase):
    def test_neg_true_row_refused_while_unset_check_present(self):
        with connection.cursor() as cur:
            restore_unset_check(cur)
        try:
            self.refuses(lambda cur: found_root(cur), contains="s015_platform_root_unset_ck")
        finally:
            with connection.cursor() as cur:
                drop_unset_check(cur)

    def test_neg_second_root_refused_by_singleton(self):
        self.commits(lambda cur: found_root(cur))
        self.refuses(lambda cur: found_root(cur), contains="s015_platform_root_singleton_uniq")

    def test_neg_update_flag_refused_either_direction(self):
        ids = self.commits(lambda cur: found_root(cur))
        self.refuses(lambda cur: cur.execute("UPDATE core_livingorganism SET platform_root = FALSE WHERE id = %s", (ids["lo"],)), contains="immutable")
        self.refuses(lambda cur: cur.execute("UPDATE core_livingorganism SET platform_root = TRUE WHERE id = %s", (ids["lo"],)), contains="immutable")

    def test_neg_bare_true_row_without_population(self):
        def attempt(cur):
            ident = str(uuid.uuid4())
            cur.execute("INSERT INTO core_livingorganism (organism_id, slug, state, created_at, current_state_effective_at, state_known_at, state_source_event_set_fingerprint, constitutional_spine_reference, platform_root) VALUES (%s, %s, 'FOUNDING_PENDING', now(), now(), now(), %s, 's', TRUE)",
                        (ident, f"bare-{fresh()}", empty_fp(cur, "core_livingorganism", ident)))
        self.refuses(attempt, contains="no sequence-1 event at commit")


# ------------------------------------------------------------------ §6.4 / U21g-5, U21g-14 — role-definition immutability
class RoleDefinitionImmutability(_S015NegativeBase):
    def test_neg_each_fixed_column(self):
        ids = self.commits(lambda cur: self.root(cur))
        rid = ids["roles"]["COORDINATOR"]
        for col, value in [("code", "'COORDINATOR'"), ("scope", "'LIVING_ORGANISM'"), ("living_organism_id", "living_organism_id"), ("definition_version", "definition_version"), ("role_uuid", "role_uuid")]:
            with self.subTest(column=col):
                self.refuses(lambda cur: cur.execute(f"UPDATE core_organismroledefinition SET {col} = {value} WHERE id = %s", (rid,)), contains="immutable")

    def test_neg_delete_and_truncate(self):
        ids = self.commits(lambda cur: self.root(cur))
        self.refuses(lambda cur: cur.execute("DELETE FROM core_organismroledefinition WHERE id = %s", (ids["roles"]["COORDINATOR"],)), contains="immutable")
        self.refuses(lambda cur: cur.execute("TRUNCATE core_organismroledefinition CASCADE"), contains="immutable")


# ------------------------------------------------------------------ §6.5 — canonical root order
class LockOrder(_S015NegativeBase):
    def test_neg_noncanonical_two_organism_write(self):
        a = self.commits(lambda cur: self.root(cur))
        b = self.commits(lambda cur: found_nonroot(cur))
        with connection.cursor() as cur:
            cur.execute("SELECT id, organism_id FROM core_livingorganism WHERE id IN (%s, %s) ORDER BY organism_id", (a["lo"], b["lo"]))
            (lower_id, _lower_uuid), (_greater_id, greater_uuid) = cur.fetchall()
        def attempt(cur):
            # Greater organism first, then a write that needs the lower organism: refused by canonical ordering.
            cur.execute("SELECT public.s015_lock_organisms(ARRAY[%s::uuid])", (str(greater_uuid),))
            cur.execute("INSERT INTO core_essentialcoveragerequirement (requirement_uuid, roster_version, responsibility_domain_code, qualifying_role_code, minimum_active_occupants, scope, effective_from, living_organism_id) VALUES (%s, 2, 'X', 'COORDINATOR', 1, 'LIVING_ORGANISM', %s, %s)",
                        (str(uuid.uuid4()), T0, lower_id))
        self.refuses(attempt, contains="lock order")
